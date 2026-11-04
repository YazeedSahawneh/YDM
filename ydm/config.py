"""App-wide configuration, settings persistence and file categories.

Rename the app or change its look here: APP_NAME / ACCENT are the only
places you need to touch.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_NAME = "YDM"
APP_TITLE = "YDM — Download Manager"
APP_VERSION = "1.5.1"
DEFAULT_PORT = 9614
ACCENT = "#FFB547"          # amber: only on things you can press (buttons, selection)
SIGNAL = "#3DDBC0"          # meter light: bytes arriving

# In-app updates: "owner/repo" of a public GitHub repo whose releases carry
# YDM-Setup-<version>.exe. Empty = update checks are off.
UPDATE_REPO = "YazeedSahawneh/YDM"


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_download_dir() -> Path:
    return Path.home() / "Downloads" / APP_NAME


CATEGORIES: dict[str, set[str]] = {
    "Video": {"mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg", "3gp", "ts"},
    "Music": {"mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "opus"},
    "Compressed": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz", "iso", "zst"},
    "Documents": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "epub", "csv", "odt", "rtf"},
    "Programs": {"exe", "msi", "apk", "dmg", "deb", "rpm", "appimage", "msix", "bat", "jar"},
    "Images": {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "tiff", "ico", "heic"},
}
CATEGORY_NAMES = list(CATEGORIES) + ["Other"]


def category_for(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for name, exts in CATEGORIES.items():
        if ext in exts:
            return name
    return "Other"


def is_downloadable_url(url: str) -> bool:
    """Used by the clipboard watcher: does this URL look like a file link?"""
    from urllib.parse import urlparse
    path = urlparse(url).path
    if "." not in path.rsplit("/", 1)[-1]:
        return False
    return category_for(path) not in ("Other", "Images")


@dataclass
class Settings:
    download_dir: str = str(default_download_dir())
    use_category_folders: bool = True
    connections: int = 16               # used when auto_connections is off
    auto_connections: bool = True       # YDM finds the fastest count per server
    max_active: int = 3
    speed_limit_kbps: int = 0           # 0 = unlimited
    port: int = DEFAULT_PORT
    show_add_dialog: bool = True        # confirm downloads sent from the browser
    watch_clipboard: bool = False
    minimize_to_tray: bool = True
    notify_on_complete: bool = True
    video_quality: str = "best"
    scheduler_enabled: bool = False
    scheduler_start: str = "02:00"
    scheduler_stop: str = "08:00"
    scheduler_days: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # Mon=0
    auto_update: bool = True            # check GitHub daily, download in the background
    last_update_check: float = 0.0
    details_visible: bool = True        # bottom pane with the segment map
    progress_window: bool = True        # small IDM-style window per download
    proxy_mode: str = "none"            # none | system | http | socks5
    proxy_host: str = ""
    proxy_port: int = 8080
    proxy_user: str = ""
    proxy_pass: str = ""                # vault.protect()ed
    accent: str = ACCENT                # theme: pressable things
    signal: str = SIGNAL                # theme: meters
    bar_colors: str = "shift"           # progress bar: "shift" (red → green with the %) or "theme"
    ui_mode: str = "dark"               # dark | light | system
    remember_dir: str = ""              # "remember this folder" in a download window: every download goes here

    @classmethod
    def path(cls) -> Path:
        return app_data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        tmp = self.path().with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, self.path())
