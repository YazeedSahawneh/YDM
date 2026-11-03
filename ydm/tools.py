"""Helper programs YDM fetches for itself into %APPDATA%\\YDM\\tools:

- FFmpeg   merges YouTube's separate video + audio streams (1080p and up)
- Deno     JavaScript runtime yt-dlp needs to read YouTube's player
- yt-dlp   a newer copy (zip) that overrides the bundled one, because sites
           change often and a frozen .exe can't `pip install -U`

Everything here is plain threading; the GUI shows progress via callbacks.
"""
from __future__ import annotations

import importlib.abc
import json
import os
import re
import shutil
import sys
import threading
import zipfile
from pathlib import Path
from typing import Callable

from .config import APP_VERSION, app_data_dir

Progress = Callable[[int, int], None]          # (downloaded, total)

TOOLS = {
    "ffmpeg": ("https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
               "ffmpeg-master-latest-win64-gpl.zip", ("ffmpeg.exe", "ffprobe.exe")),
    "deno": ("https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
             ("deno.exe",)),
}
YTDLP_LATEST_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
YTDLP_ZIP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"


def tools_dir() -> Path:
    path = app_data_dir() / "tools"
    path.mkdir(parents=True, exist_ok=True)
    return path


def find(name: str) -> str | None:
    local = tools_dir() / f"{name}.exe"
    if local.exists():
        return str(local)
    return shutil.which(name)


def ffmpeg_path() -> str | None:
    return find("ffmpeg")


def js_runtimes() -> dict:
    """yt-dlp `js_runtimes` option: our Deno, else one already on the PC."""
    deno = find("deno")
    if deno:
        return {"deno": {"path": deno}}
    if shutil.which("node"):
        return {"node": {}}
    return {}


def missing_for_video() -> list[str]:
    if os.name != "nt":
        return []                     # Linux/macOS: use the system packages
    out = []
    if not ffmpeg_path():
        out.append("ffmpeg")
    if not js_runtimes():
        out.append("deno")
    return out


# ---------------------------------------------------------------- downloading
def download_to(url: str, folder: Path, filename: str, on_progress: Progress | None = None,
                stop: threading.Event | None = None, connections: int = 8) -> Path:
    """Fetch one file with YDM's own segmented engine (outside the download list)."""
    from .engine.http_downloader import HttpDownloader
    from .engine.item import DownloadItem, Status
    from .engine.limiter import RateLimiter

    folder.mkdir(parents=True, exist_ok=True)
    for leftover in (folder / filename, folder / (filename + ".ydm")):
        leftover.unlink(missing_ok=True)
    item = DownloadItem(url=url, filename=filename, save_dir=str(folder), connections=connections)
    dl = HttpDownloader(item, RateLimiter(0), lambda _i: str(folder))
    t = threading.Thread(target=dl.run, daemon=True)
    t.start()
    while t.is_alive():
        t.join(0.25)
        if stop is not None and stop.is_set():
            dl.stop()
        if on_progress:
            on_progress(item.downloaded, item.total)
    if item.status != Status.COMPLETED:
        raise RuntimeError(item.error or "Download stopped")
    return Path(item.path)


def install(name: str, on_progress: Progress | None = None, stop: threading.Event | None = None) -> str:
    url, wanted = TOOLS[name]
    target = tools_dir()
    archive = download_to(url, target / "tmp", f"{name}.zip", on_progress, stop)
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                base = os.path.basename(info.filename)
                if base in wanted:
                    with zf.open(info) as src, open(target / (base + ".new"), "wb") as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
                    os.replace(target / (base + ".new"), target / base)
    finally:
        archive.unlink(missing_ok=True)
    path = target / wanted[0]
    if not path.exists():
        raise RuntimeError(f"{wanted[0]} was not in the download")
    return str(path)


# --------------------------------------------------------- yt-dlp hot update
def _ytdlp_zip() -> Path:
    return tools_dir() / "yt-dlp.zip"


def _state_file() -> Path:
    return tools_dir() / "state.json"


def _read_state() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(**changes) -> None:
    state = _read_state()
    state.update(changes)
    try:
        _state_file().write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


class _ZipFirst(importlib.abc.MetaPathFinder):
    """Serve every yt_dlp module from the downloaded zip, ahead of the copy
    frozen into the .exe (PyInstaller's importer would otherwise win)."""

    def __init__(self, zip_path: str):
        self.zip_path = zip_path

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "yt_dlp" and not fullname.startswith("yt_dlp."):
            return None
        import zipimport
        package = fullname.split(".")[:-1]
        try:
            return zipimport.zipimporter(os.path.join(self.zip_path, *package)).find_spec(fullname)
        except zipimport.ZipImportError:
            return None


def activate_ytdlp_override() -> str | None:
    """Call before anything imports yt_dlp. Returns the override's version, if used."""
    zp = _ytdlp_zip()
    if not zp.exists():
        return None
    state = _read_state()
    if state.get("app_version") != APP_VERSION:
        # A new YDM release ships its own, newer yt-dlp; drop the old override.
        zp.unlink(missing_ok=True)
        _write_state(app_version=APP_VERSION, ytdlp_zip_version=None)
        return None
    sys.meta_path.insert(0, _ZipFirst(str(zp)))
    return state.get("ytdlp_zip_version")


def ytdlp_version() -> str:
    try:
        from yt_dlp.version import __version__
        return __version__
    except ImportError:
        return ""


def update_ytdlp(stop: threading.Event | None = None) -> str | None:
    """Download a newer yt-dlp if one exists. Returns the new version (active after restart)."""
    from .engine import net
    r = net.get(YTDLP_LATEST_API, headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    latest = str(r.json().get("tag_name", "")).lstrip("v")
    if not latest or _version_key(latest) <= _version_key(ytdlp_version()):
        return None
    tmp = download_to(YTDLP_ZIP_URL, tools_dir() / "tmp", "yt-dlp.zip", stop=stop, connections=4)
    with zipfile.ZipFile(tmp) as zf:          # sanity check before swapping it in
        if "yt_dlp/__init__.py" not in zf.namelist():
            raise RuntimeError("Unexpected yt-dlp download")
    os.replace(tmp, _ytdlp_zip())
    _write_state(app_version=APP_VERSION, ytdlp_zip_version=latest)
    return latest


def _version_key(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))
