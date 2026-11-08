"""Which browsers are installed, and opening each one's extensions page (Options → Browser).

The same extension folder works in all of them: Chrome, Edge, Brave and Opera load it as an
unpacked extension; Firefox loads its manifest.json as a temporary add-on, or installs the
signed YDM-Firefox.xpi from the release page for good.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# name, program, extensions page, how to add it there
BROWSERS = (
    ("Chrome", "chrome.exe", "chrome://extensions", "Turn on Developer mode, click Load unpacked, pick the folder."),
    ("Edge", "msedge.exe", "edge://extensions", "Turn on Developer mode, click Load unpacked, pick the folder."),
    ("Brave", "brave.exe", "brave://extensions", "Turn on Developer mode, click Load unpacked, pick the folder."),
    ("Opera", "opera.exe", "opera://extensions", "Turn on Developer mode, click Load unpacked, pick the folder."),
    ("Firefox", "firefox.exe", "about:debugging#/runtime/this-firefox",
     "Click Load Temporary Add-on… and pick manifest.json in the folder."),
)


def extension_dir() -> Path:
    """The extension's folder: next to YDM.exe when installed, in the repo when run from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "extension"
    return Path(__file__).resolve().parent.parent / "extension"


def _app_path(exe: str) -> str:
    """Windows' "App Paths" registration (how Start → Run finds chrome.exe), per user or per machine."""
    try:
        import winreg
    except ImportError:
        return ""
    key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, key) as k:
                path = str(winreg.QueryValueEx(k, "")[0]).strip('"')
        except OSError:
            continue
        if path and os.path.exists(path):
            return path
    return ""


def installed() -> list[tuple[str, str, str, str]]:
    """(name, program path, extensions page, how) for each browser found on this PC."""
    found = []
    for name, exe, page, how in BROWSERS:
        path = _app_path(exe)
        if path:
            found.append((name, path, page, how))
    return found


def open_extensions_page(program: str, page: str) -> None:
    """Browsers refuse their own pages from links, but take them on the command line."""
    subprocess.Popen([program, page], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
