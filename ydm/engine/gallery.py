"""Photos and whole posts from social sites, through gallery-dl.

yt-dlp knows videos; gallery-dl knows posts: Instagram photos and carousels,
Facebook photos, X/Twitter, Threads, Reddit, Pinterest, Tumblr, TikTok photo
posts, Flickr, DeviantArt, Imgur and a few hundred more. It runs as its own
program (tools\\gallery-dl.exe, fetched like FFmpeg) and only *lists* the
post's media as JSON; YDM then downloads every file itself, so photos get the
same queue, resume and folders as everything else.

The browser's cookies for the page go along in a temporary cookies.txt, so
whatever your own account can see in the browser works here too (Instagram
shows nothing without a login). Cookies are never sent to other domains.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .. import tools
from ..utils import sanitize_filename
from . import net

# Sites where a page is usually a post with photos, so it's worth asking gallery-dl
# alongside yt-dlp (anywhere else it's asked only when yt-dlp finds no video).
SOCIAL = ("instagram.com", "facebook.com", "fb.watch", "twitter.com", "x.com", "threads.net", "threads.com",
          "reddit.com", "redd.it", "pinterest.com", "pin.it", "tumblr.com", "tiktok.com", "flickr.com",
          "deviantart.com", "imgur.com", "bsky.app", "vk.com", "weibo.com", "artstation.com", "behance.net",
          "500px.com", "pixiv.net", "danbooru.donmai.us", "kemono.su", "civitai.com", "patreon.com")
OPTION = "gallery"                  # the format spec the quality lists use for "every photo/video in the post"
_TTL = 300
_cache: dict[str, tuple[float, "Post"]] = {}
_lock = threading.Lock()


@dataclass
class MediaFile:
    url: str
    filename: str
    kind: str = "file"              # "file", or "video" when it needs yt-dlp (HLS/DASH)
    headers: dict = field(default_factory=dict)

    @property
    def is_image(self) -> bool:
        from ..config import CATEGORIES
        return self.kind == "file" and self.filename.rsplit(".", 1)[-1].lower() in CATEGORIES["Images"]


@dataclass
class Post:
    url: str
    title: str = ""
    uploader: str = ""
    files: list[MediaFile] = field(default_factory=list)

    @property
    def photos(self) -> int:
        return sum(1 for f in self.files if f.is_image)

    def label(self) -> str:
        photos, others = self.photos, len(self.files) - self.photos
        parts = [f"{photos} photo{'s' * (photos != 1)}" if photos else "",
                 f"{others} video{'s' * (others != 1)}" if others else ""]
        text = " + ".join(p for p in parts if p)
        return f"All {text}" if len(self.files) > 1 else text.replace("1 ", "The ").capitalize()


def is_social(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == s or host.endswith("." + s) for s in SOCIAL)


def available() -> bool:
    return tools.gallery_dl_path() is not None


def collect(url: str, headers: dict | None = None, limit: int = 1000) -> Post:
    """List every photo/video of a post (or album, profile page…) without downloading it."""
    exe = tools.gallery_dl_path()
    if not exe:
        raise RuntimeError("gallery-dl is not installed (Tools → Install video tools)")
    headers = {k: v for k, v in (headers or {}).items() if v}
    cmd = [exe, "-J", "--config-ignore", "--range", f"1-{limit}"]
    if headers.get("User-Agent"):
        cmd += ["-a", headers["User-Agent"]]
    proxy = net.current_proxy()
    if proxy:
        cmd += ["--proxy", proxy]
    cookie_file = _cookie_file(url, headers.get("Cookie", ""))
    if cookie_file:
        cmd += ["-C", cookie_file]
    try:
        res = subprocess.run(cmd + [url], capture_output=True, timeout=120,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise RuntimeError("The site took too long to list the post") from None
    finally:
        if cookie_file:
            os.unlink(cookie_file)
    out = res.stdout.decode("utf-8", "replace").strip()
    if res.returncode == 64 or (not out and "Unsupported URL" in res.stderr.decode("utf-8", "replace")):
        raise RuntimeError("No photos or videos found on this page")
    try:
        messages = json.loads(out or "[]")
    except ValueError:
        raise RuntimeError(_last_error(res.stderr) or "gallery-dl gave no answer") from None
    return parse(url, messages, headers)


def parse(url: str, messages: list, headers: dict | None = None) -> Post:
    """gallery-dl's -J output → Post. Message types: 2 directory, 3 file, 6 child page, -1 error."""
    headers = headers or {}
    post = Post(url)
    error = ""
    for msg in messages:
        if not isinstance(msg, list) or not msg:
            continue
        kind = msg[0]
        if kind == -1 and len(msg) > 1 and isinstance(msg[1], dict):
            error = str(msg[1].get("message") or msg[1].get("error") or "")
        elif kind == 2 and len(msg) > 1 and isinstance(msg[1], dict) and not post.title:
            kw = msg[1]
            post.uploader = _pick(kw, "fullname", "username", "author", "user", "uploader")
            text = _pick(kw, "description", "title", "content", "caption")
            post.title = " ".join(text.split())[:90] or post.uploader
        elif kind == 3 and len(msg) > 2 and isinstance(msg[2], dict):
            post.files.append(_media(url, str(msg[1]), msg[2], headers))
    if not post.files and error:
        raise RuntimeError(_friendly(error))
    if not post.title:
        post.title = urlparse(url).hostname or "post"
    # several files from one post: number them, so the names sort in the post's order
    seen: dict[str, int] = {}
    for f in post.files:
        seen[f.filename] = seen.get(f.filename, 0) + 1
    for i, f in enumerate(post.files, 1):
        if seen[f.filename] > 1:
            stem, _, ext = f.filename.rpartition(".")
            f.filename = f"{stem}_{i}.{ext}"
    return post


def fetch_media_info(url: str, headers: dict | None = None) -> dict:
    """What can be saved from this page: yt-dlp's video qualities, plus "All N photos"
    when the page is a post with photos (or the only option, when there is no video)."""
    from .video_downloader import fetch_video_info
    found: dict[str, object] = {}

    def ask_gallery():
        try:
            found["post"] = collect(url, headers)
        except Exception as exc:  # noqa: BLE001 - only used when yt-dlp fails too
            found["error"] = exc

    social = is_social(url) and available()
    worker = threading.Thread(target=ask_gallery, daemon=True) if social else None
    if worker:
        worker.start()
    video, video_error = None, None
    try:
        video = fetch_video_info(url, headers)
    except Exception as exc:  # noqa: BLE001
        video_error = exc
    if worker:
        worker.join()
    elif (video is None or not video.get("has_video", True)) and available():
        ask_gallery()                            # no real video here: maybe it's a picture page
    post: Post | None = found.get("post")       # type: ignore[assignment]
    if post is None or not post.files:
        if video is None:
            raise video_error or RuntimeError("No video or photos found")
        return video
    if video is not None and not post.photos and len(post.files) <= 1:
        return video                             # a plain reel/video: the quality list says it all
    if video is not None and not video.get("has_video", True) and post.photos:
        video = None                             # yt-dlp only "found" the picture itself
    with _lock:
        _cache[url] = (time.time(), post)
    option = (post.label(), OPTION, 0, "")
    if video is None:
        return {"title": post.title, "uploader": post.uploader, "duration": None, "thumbnail": None,
                "options": [option], "merge": False, "ext": "", "photos": post.photos}
    video = dict(video)
    video["options"] = [option] + list(video["options"])
    video["photos"] = post.photos
    return video


def take(url: str, headers: dict | None = None) -> Post:
    """The post listed a moment ago (signed media links expire), else list it now."""
    with _lock:
        hit = _cache.pop(url, None)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    return collect(url, headers)


# ---------------------------------------------------------------- helpers
def _pick(kw: dict, *keys: str) -> str:
    for k in keys:
        v = kw.get(k)
        if isinstance(v, dict):
            v = v.get("name") or v.get("username") or v.get("nick")
        if v not in (None, "", 0, []):
            return str(v)
    return ""


def _media(page: str, url: str, kw: dict, headers: dict) -> MediaFile:
    kind = "file"
    if url.startswith("ytdl:"):
        url, kind = url[5:], "video"
    ext = str(kw.get("extension") or "").lower() or ("mp4" if kind == "video" else "jpg")
    who = _pick(kw, "username", "owner_username", "author", "user", "uploader", "blog_name")
    post = _pick(kw, "post_shortcode", "shortcode", "post_id", "tweet_id", "id")
    num = kw.get("num")
    if post:                    # e.g. nasa_C1_2.jpg: who posted it, the post, the place in it
        stem = "_".join(str(x) for x in (who, post, num if isinstance(num, int) and num > 1 else "") if x)
    else:                       # no post id: the file's own name says more than a user name
        stem = str(kw.get("filename") or who or "media")
    name = sanitize_filename(f"{stem}.{ext}")
    hdrs = {"Referer": page}
    if headers.get("User-Agent"):
        hdrs["User-Agent"] = headers["User-Agent"]
    extra = kw.get("_http_headers")
    if isinstance(extra, dict):
        hdrs.update({str(k): str(v) for k, v in extra.items()})
    if headers.get("Cookie") and _same_site(page, url):
        hdrs["Cookie"] = headers["Cookie"]
    return MediaFile(url, name, kind, hdrs)


def _site(host: str) -> str:
    parts = (host or "").lower().split(".")
    return ".".join(parts[-2:])


def _same_site(a: str, b: str) -> bool:
    return _site(urlparse(a).hostname or "") == _site(urlparse(b).hostname or "")


def _cookie_file(url: str, cookie: str) -> str | None:
    """The page's cookies as a Netscape cookies.txt for gallery-dl (deleted right after)."""
    pairs = [c.strip().split("=", 1) for c in cookie.split(";") if "=" in c]
    if not pairs:
        return None
    domain = "." + _site(urlparse(url).hostname or "")
    lines = ["# Netscape HTTP Cookie File"]
    lines += [f"{domain}\tTRUE\t/\tTRUE\t0\t{k.strip()}\t{v.strip()}" for k, v in pairs]
    fd, path = tempfile.mkstemp(prefix="ydm-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _last_error(stderr: bytes) -> str:
    lines = [ln for ln in stderr.decode("utf-8", "replace").splitlines() if "[error]" in ln]
    return _friendly(lines[-1].split("[error]", 1)[1].strip()) if lines else ""


def _friendly(message: str) -> str:
    low = message.lower()
    if "login" in low or "401" in low or "authorization" in low:
        return "Log in to the site in your browser, then try again from the page"
    if "404" in low or "not found" in low:
        return "This post is gone or private"
    return message[:200]
