"""Video grabber built on yt-dlp (supports YouTube and ~1800 other sites)."""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from .item import DownloadItem, Status
from .limiter import RateLimiter


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


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
            "logger": _SilentLogger()}
    hdrs = {k: v for k, v in (headers or {}).items() if v}
    if hdrs:
        opts["http_headers"] = hdrs
    return opts


def fetch_video_info(url: str, headers: dict | None = None) -> dict:
    """Return {'title', 'thumbnail', 'duration', 'options': [(label, format_spec), ...]}."""
    import yt_dlp
    with yt_dlp.YoutubeDL(_base_opts(headers or {})) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist" and info.get("entries"):
        info = next(e for e in info["entries"] if e)
    heights = sorted({f.get("height") for f in info.get("formats", [])
                      if f.get("height") and f.get("vcodec") not in (None, "none")}, reverse=True)
    merge = has_ffmpeg()
    options = [("Best quality", "bv*+ba/b" if merge else "b")]
    for h in heights:
        spec = f"bv*[height<={h}]+ba/b[height<={h}]" if merge else f"b[height<={h}]"
        options.append((f"{h}p", spec))
    options.append(("Audio only", "ba[ext=m4a]/ba"))
    return {"title": info.get("title") or "video", "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"), "options": options,
            "ext": "mp4" if merge else info.get("ext", "mp4")}


class VideoDownloader:
    def __init__(self, item: DownloadItem, limiter: RateLimiter,
                 resolve_dir: Callable[[DownloadItem], str]):
        self.item = item
        self.limiter = limiter
        self.resolve_dir = resolve_dir
        self._stop = threading.Event()
        self._done_bytes = 0       # bytes of already-finished parts (video + audio streams)

    def stop(self) -> None:
        self._stop.set()

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
        if self.limiter.rate > 0:
            opts["ratelimit"] = self.limiter.rate
        self._done_bytes = 0
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(item.url, download=True)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = next(e for e in info["entries"] if e)
            path = None
            if info.get("requested_downloads"):
                path = info["requested_downloads"][0].get("filepath")
            path = path or ydl.prepare_filename(info)
            item.title = info.get("title", item.title)
            if path and os.path.exists(path):
                item.save_dir, item.filename = os.path.split(path)
                item.total = item.downloaded = os.path.getsize(path)
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
