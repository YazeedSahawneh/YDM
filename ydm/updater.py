"""In-app updates from GitHub Releases.

Each release carries `YDM-Setup-<version>.exe` (built by build.bat). The app
checks the latest release, downloads the installer with its own engine, runs
it silently with /RELAUNCH and exits; the installer closes YDM, replaces the
files and starts it again. Downloads in progress resume after the restart.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import APP_VERSION, UPDATE_REPO
from .tools import Progress, download_to, tools_dir


@dataclass
class Release:
    version: str
    url: str
    notes: str
    size: int


def version_key(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


def enabled() -> bool:
    return bool(UPDATE_REPO)


def can_install() -> bool:
    """Only the installed .exe can replace itself; running from source uses git."""
    return os.name == "nt" and getattr(sys, "frozen", False)


def check(repo: str = UPDATE_REPO, current: str = APP_VERSION) -> Release | None:
    """The newest published release that is newer than `current`, else None.

    A release without the YDM-Setup-*.exe attached (not built yet, or the upload is
    still running) is skipped in favour of an older one that has it; if that leaves
    nothing newer, the newest one is returned with url="" so the app can say the
    installer isn't there yet instead of "you're up to date".
    """
    from .engine import net
    r = net.get(f"https://api.github.com/repos/{repo}/releases?per_page=20",
                headers={"Accept": "application/vnd.github+json"})
    if r.status_code == 404:
        raise RuntimeError(f"GitHub repository {repo} not found (it must be public)")
    r.raise_for_status()
    newer = []
    for data in r.json():
        if data.get("draft") or data.get("prerelease"):
            continue
        version = str(data.get("tag_name", "")).lstrip("vV")
        if version_key(version) > version_key(current):
            newer.append((version_key(version), version, data))
    if not newer:
        return None
    newer.sort(key=lambda x: x[0], reverse=True)
    for _key, version, data in newer:
        asset = next((a for a in data.get("assets", [])
                      if a.get("name", "").lower().startswith("ydm-setup") and a["name"].lower().endswith(".exe")
                      and a.get("state", "uploaded") == "uploaded"), None)
        if asset:
            return Release(version=version, url=asset["browser_download_url"],
                           notes=(data.get("body") or "").strip(), size=int(asset.get("size") or 0))
    _key, version, data = newer[0]
    return Release(version=version, url="", notes=(data.get("body") or "").strip(), size=0)


def download(release: Release, on_progress: Progress | None = None,
             stop: threading.Event | None = None) -> Path:
    path = download_to(release.url, tools_dir() / "updates", f"YDM-Setup-{release.version}.exe",
                       on_progress, stop)
    if release.size and path.stat().st_size != release.size:
        path.unlink(missing_ok=True)
        raise RuntimeError("The update download was incomplete")
    return path


def launch_installer(installer: Path) -> None:
    """Start the silent install; the caller must quit right after."""
    flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                      "/CLOSEAPPLICATIONS", "/RELAUNCH"], creationflags=flags, close_fds=True)
