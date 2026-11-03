from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def human_size(n: float) -> str:
    if n is None or n < 0:
        return "Unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def human_speed(bps: float) -> str:
    return "" if not bps or bps <= 0 else human_size(bps) + "/s"


def human_eta(seconds: float) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def sanitize_filename(name: str, fallback: str = "download") -> str:
    name = _INVALID.sub("_", name).strip().strip(".")
    if not name:
        name = fallback
    stem = name.split(".", 1)[0].upper()
    if stem in _RESERVED:
        name = "_" + name
    if len(name) > 200:
        base, dot, ext = name.rpartition(".")
        name = (base[: 200 - len(ext) - 1] + "." + ext) if dot and len(ext) < 12 else name[:200]
    return name


def unique_path(path: Path) -> Path:
    """Return path, or 'name (1).ext', 'name (2).ext'... if it already exists."""
    if not path.exists() and not Path(str(path) + ".ydm").exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 1
    while True:
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists() and not Path(str(candidate) + ".ydm").exists():
            return candidate
        i += 1


def filename_from_headers(url: str, headers) -> str:
    cd = headers.get("content-disposition", "") if headers else ""
    m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", cd, re.I)
    if m:
        try:
            return sanitize_filename(unquote(m.group(2).strip().strip('"'), encoding=m.group(1) or "utf-8"))
        except LookupError:
            return sanitize_filename(unquote(m.group(2).strip().strip('"')))
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.I) or re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
    if m:
        return sanitize_filename(unquote(m.group(1).strip()))
    path = unquote(urlparse(url).path)
    name = path.rstrip("/").rsplit("/", 1)[-1]
    return sanitize_filename(name or urlparse(url).netloc)


def open_path(path: str) -> None:
    """Open a file or folder with the OS default handler."""
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        import subprocess
        subprocess.Popen(["xdg-open", path])


def open_with(path: str) -> None:
    """Windows' "Open with" chooser for a file; elsewhere, the default handler."""
    if os.name == "nt":
        import subprocess
        subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", os.path.normpath(path)])
    else:
        open_path(path)


def reveal_in_folder(path: str) -> None:
    if os.name == "nt" and os.path.exists(path):
        import subprocess
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_path(os.path.dirname(path) if os.path.isfile(path) else path)
