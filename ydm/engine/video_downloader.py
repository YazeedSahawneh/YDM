"""Video grabber built on yt-dlp (supports YouTube and ~1800 other sites).

yt-dlp finds the streams; when they are plain HTTP(S) files (YouTube's video
and audio, most progressive MP4s) YDM downloads them itself with the
multi-connection engine, both streams at once, and merges them with FFmpeg.
That is several times faster than one connection per stream, and a pause keeps
every segment: on resume the links are re-read (they expire) and the
download continues where it stopped. Anything else (HLS, DASH fragments)
goes through yt-dlp's own downloader with parallel fragments.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .. import tools
from ..utils import unique_path
from . import net
from .http_downloader import HttpDownloader
from .item import DownloadItem, Segment, Status
from .limiter import RateLimiter


def has_ffmpeg() -> bool:
    return tools.ffmpeg_path() is not None


class _Cancelled(Exception):
    pass


class _SilentLogger:
    """Route yt-dlp output nowhere (a windowed .exe has no console; errors come back as exceptions)."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _base_opts(headers: dict) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "noprogress": True,
            "logger": _SilentLogger(), "remote_components": {"ejs:github"}}
    runtimes = tools.js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
    ffmpeg = tools.ffmpeg_path()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    hdrs = {k: v for k, v in (headers or {}).items() if v}
    if hdrs:
        opts["http_headers"] = hdrs
    proxy = net.current_proxy()
    if proxy:
        opts["proxy"] = proxy
    return opts


_INFO_CACHE: dict[str, tuple[float, dict]] = {}     # url -> (time, raw info) from the Add dialog
_INFO_TTL = 300                                      # stream links stay valid much longer; be safe


def _cached_info(url: str) -> dict | None:
    hit = _INFO_CACHE.pop(url, None)
    if hit and time.time() - hit[0] < _INFO_TTL:
        return hit[1]
    return None


def _size(f: dict) -> int:
    return int(f.get("filesize") or f.get("filesize_approx") or 0)


def fetch_video_info(url: str, headers: dict | None = None) -> dict:
    """Return {'title', 'thumbnail', 'duration', 'uploader',
    'options': [(label, format_spec, approx_size, convert), ...]}; convert is "" or "mp3"."""
    import yt_dlp
    with yt_dlp.YoutubeDL(_base_opts(headers or {})) as ydl:
        info = ydl.extract_info(url, download=False, process=False)
        if info.get("_type") in ("playlist", "multi_video") and info.get("entries"):
            info = next(e for e in info["entries"] if e)
            if info.get("_type") == "url":
                info = ydl.extract_info(info["url"], download=False, process=False)
    if info.get("formats"):
        _INFO_CACHE[url] = (time.time(), info)       # the download reuses it: no second page read
    formats = info.get("formats") or []
    videos = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("height")]
    audios = [f for f in formats if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")]
    best_audio = max((_size(a) for a in audios), default=0)
    merge = has_ffmpeg()
    heights = sorted({f["height"] for f in videos}, reverse=True)

    def size_for(h: int | None) -> int:
        pool = [f for f in videos if h is None or f["height"] <= h]
        if not merge:
            pool = [f for f in pool if f.get("acodec") not in (None, "none")]
        best = max((_size(f) for f in pool if f["height"] == max(p["height"] for p in pool)), default=0) if pool else 0
        return best + (best_audio if merge and best else 0)

    options = [("Best quality", "bv*+ba/b" if merge else "b", size_for(None), "")]
    for h in heights:
        spec = f"bv*[height<={h}]+ba/b[height<={h}]" if merge else f"b[height<={h}]"
        options.append((f"{h}p", spec, size_for(h), ""))
    m4a = max((_size(a) for a in audios if a.get("ext") == "m4a"), default=best_audio)
    if merge:
        options.append(("Audio · MP3", "ba/b", best_audio, "mp3"))
    options.append(("Audio · M4A", "ba[ext=m4a]/ba", m4a, ""))
    return {"title": info.get("title") or "video", "duration": info.get("duration"),
            "uploader": info.get("uploader") or info.get("channel") or "",
            "thumbnail": info.get("thumbnail"), "options": options, "merge": merge,
            "ext": "mp4" if merge else info.get("ext", "mp4")}


def browser_info(payload: dict) -> dict:
    """Quality list for the extension's on-page menu (also warms the cache, so the download
    that follows starts without reading the page again)."""
    headers = {"Referer": payload.get("referrer") or "", "User-Agent": payload.get("userAgent") or "",
               "Cookie": payload.get("cookies") or ""}
    info = fetch_video_info(str(payload["url"]), headers)
    return {"title": info["title"], "uploader": info["uploader"], "duration": info["duration"],
            "options": [{"label": label, "format": spec, "size": size, "convert": conv}
                        for label, spec, size, conv in info["options"]]}


class VideoDownloader:
    def __init__(self, item: DownloadItem, limiter: RateLimiter,
                 resolve_dir: Callable[[DownloadItem], str]):
        self.item = item
        self.limiter = limiter
        self.resolve_dir = resolve_dir
        self._stop = threading.Event()
        self._done_bytes = 0       # yt-dlp mode: bytes of already-finished streams
        self._children: list[HttpDownloader] = []
        self._fids: list[str] = []

    def stop(self) -> None:
        self._stop.set()
        for child in list(self._children):
            child.stop()

    def set_connections(self, n: int) -> None:
        self.item.connections = n
        for i, child in enumerate(list(self._children)):      # video stream gets most, audio a quarter
            child.set_connections(n if i == 0 else max(2, n // 4))

    def run(self) -> None:
        item = self.item
        try:
            import yt_dlp
        except ImportError:
            item.status, item.error = Status.ERROR, "yt-dlp is not installed"
            return
        item.status = Status.CONNECTING
        item.category = "Video"
        if not item.save_dir:
            item.save_dir = self.resolve_dir(item)
        Path(item.save_dir).mkdir(parents=True, exist_ok=True)

        opts = _base_opts(item.headers)
        opts.update({
            "format": item.video_format or ("bv*+ba/b" if has_ffmpeg() else "b"),
            "outtmpl": os.path.join(item.save_dir, "%(title).150B [%(id)s].%(ext)s"),
            "progress_hooks": [self._hook],
            "postprocessor_hooks": [self._pp_hook],
            "concurrent_fragment_downloads": max(1, min(item.connections, 16)),
            "continuedl": True,
            "retries": 10,
            "fragment_retries": 10,
            "windowsfilenames": True,
        })
        if has_ffmpeg():
            opts["merge_output_format"] = "mp4"
        if item.convert == "mp3" and has_ffmpeg():
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                                       "preferredquality": "0"}]
        if self.limiter.rate > 0:
            opts["ratelimit"] = self.limiter.rate
        self._done_bytes = 0
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                raw = _cached_info(item.url)
                if raw is not None:
                    info = ydl.process_ie_result(raw, download=False)
                else:
                    info = ydl.extract_info(item.url, download=False)
                if info.get("_type") == "playlist" and info.get("entries"):
                    info = next(e for e in info["entries"] if e)
                if self._stop.is_set():
                    raise _Cancelled
                item.title = info.get("title", item.title)
                streams = info.get("requested_formats") or [info]
                if self._fast_possible(info, streams):
                    path = self._fast_download(ydl, info, streams)
                else:
                    new_info = dict(info)
                    ydl.process_info(new_info)
                    path = new_info.get("filepath") or ydl.prepare_filename(new_info)
            if self._stop.is_set():
                raise _Cancelled
            if path and os.path.exists(path):
                item.save_dir, item.filename = os.path.split(path)
                item.total = item.downloaded = os.path.getsize(path)
            item.parts = []
            item.status = Status.COMPLETED
            item.finished = time.time()
        except Exception as exc:  # noqa: BLE001
            if self._stop.is_set():
                item.status = Status.PAUSED
            else:
                item.status = Status.ERROR
                msg = str(exc).replace("ERROR: ", "")
                item.error = msg.splitlines()[0][:300] if msg else exc.__class__.__name__
        finally:
            item.speed = 0

    # ------------------------------------------------- fast (segmented) path
    @staticmethod
    def _fast_possible(info: dict, streams: list[dict]) -> bool:
        if info.get("is_live") or not streams:
            return False
        if len(streams) > 1 and not has_ffmpeg():
            return False
        return all(s.get("url") and s.get("protocol") in ("http", "https") and not s.get("fragments")
                   for s in streams)

    def _fast_download(self, ydl, info: dict, streams: list[dict]) -> str:
        item = self.item
        stem = os.path.splitext(ydl.prepare_filename(info))[0]
        merging = len(streams) > 1
        to_mp3 = item.convert == "mp3" and not merging and has_ffmpeg()
        ext = "mp3" if to_mp3 else ("mp4" if merging else (streams[0].get("ext") or "mp4"))
        final = Path(stem + "." + ext)
        if final.exists():
            final = unique_path(final)
        item.filename = final.name
        item.status = Status.DOWNLOADING

        saved = {p.get("format_id"): p for p in item.parts}
        self._fids = [str(s.get("format_id")) for s in streams]
        parts: list[DownloadItem] = []
        for i, s in enumerate(streams):
            fid = str(s.get("format_id"))
            sub = DownloadItem(url=s["url"], save_dir=item.save_dir,
                               filename=f"{final.stem}.f{fid}.{s.get('ext') or 'bin'}",
                               headers=dict(s.get("http_headers") or {}),
                               connections=item.connections if i == 0 else max(2, item.connections // 4))
            old = saved.get(fid)
            if old and old.get("segments") and os.path.exists(os.path.join(item.save_dir, old["filename"]) + ".ydm"):
                sub.filename, sub.total, sub.resumable = old["filename"], int(old["total"]), True
                sub.segments = [Segment(**seg) for seg in old["segments"]]
                sub.downloaded = sum(seg.done for seg in sub.segments)
            parts.append(sub)

        self._children = [HttpDownloader(p, self.limiter, lambda _i: item.save_dir) for p in parts]
        threads = [threading.Thread(target=c.run, daemon=True) for c in self._children]
        for t in threads:
            t.start()
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(0.25)
            self._sync(parts)
        self._sync(parts)
        if self._stop.is_set():
            raise _Cancelled
        failed = next((p for p in parts if p.status != Status.COMPLETED), None)
        if failed:
            raise RuntimeError(failed.error or "A stream did not finish")

        if to_mp3:
            item.status = Status.PROCESSING
            self._ffmpeg(["-i", parts[0].path, "-vn", "-c:a", "libmp3lame", "-q:a", "0"], final, "Converting")
            Path(parts[0].path).unlink(missing_ok=True)
            return str(final)
        if not merging:
            os.replace(parts[0].path, final)
            return str(final)
        item.status = Status.PROCESSING
        video = next((p for p, s in zip(parts, streams) if s.get("vcodec") not in (None, "none")), parts[0])
        audio = next(p for p in parts if p is not video)
        self._ffmpeg(["-i", video.path, "-i", audio.path, "-map", "0:v:0", "-map", "1:a:0", "-c", "copy"],
                     final, "Merging")
        for p in parts:
            Path(p.path).unlink(missing_ok=True)
        return str(final)

    @staticmethod
    def _ffmpeg(args: list[str], final: Path, what: str) -> None:
        tmp = final.with_name(final.stem + ".ffmpeg" + final.suffix)
        cmd = [tools.ffmpeg_path() or "ffmpeg", "-y", "-loglevel", "error", *args, str(tmp)]
        res = subprocess.run(cmd, capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if res.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            last = (res.stderr or "").strip().splitlines()[-1:] or ["FFmpeg failed"]
            raise RuntimeError(f"{what} failed: {last[0][:200]}")
        os.replace(tmp, final)

    def _sync(self, parts: list[DownloadItem]) -> None:
        item = self.item
        item.downloaded = sum(p.downloaded for p in parts)
        item.total = sum(p.total for p in parts) if all(p.total > 0 for p in parts) else -1
        item.parts = [{"format_id": fid, "filename": p.filename, "total": p.total,
                       "segments": [s.to_dict() for s in p.segments]}
                      for fid, p in zip(self._fids, parts) if p.resumable]
        if item.status == Status.CONNECTING and item.downloaded:
            item.status = Status.DOWNLOADING

    # ------------------------------------------------------- yt-dlp path
    def _hook(self, d: dict) -> None:
        if self._stop.is_set():
            raise _Cancelled("cancelled")
        item = self.item
        if d["status"] == "downloading":
            item.status = Status.DOWNLOADING
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            item.downloaded = self._done_bytes + got
            item.total = self._done_bytes + int(total) if total else -1
            name = d.get("filename") or ""
            if name and not item.filename:
                item.filename = os.path.basename(name)
        elif d["status"] == "finished":
            self._done_bytes += d.get("total_bytes") or d.get("downloaded_bytes") or 0
            item.downloaded = item.total = self._done_bytes

    def _pp_hook(self, d: dict) -> None:
        if d.get("status") == "started":
            self.item.status = Status.PROCESSING
