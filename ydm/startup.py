"""Start YDM with Windows, kept in step with Task Manager → Startup apps.

Task Manager lists the programs in HKCU\\...\\Run and, when you disable one,
leaves the entry there and writes a "disabled" flag under
Explorer\\StartupApproved\\Run. YDM does exactly the same, so the switch in
Options and the one in Task Manager are the same switch: turning it off here
shows "Disabled" there, and enabling it there shows it on here. (The
installer's "Start with Windows" box creates the same Run entry.)
"""
from __future__ import annotations

import os
import struct
import sys
import time

NAME = "YDM"
RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
ARG = "--minimized"                  # start in the tray


def supported() -> bool:
    return os.name == "nt"


def command() -> str:
    """How Windows should start this copy of YDM."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {ARG}'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    main = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
    return f'"{pythonw if os.path.exists(pythonw) else sys.executable}" "{main}" {ARG}'


def is_enabled() -> bool:
    """On when the Run entry exists and Task Manager hasn't disabled it."""
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN) as key:
            winreg.QueryValueEx(key, NAME)
    except OSError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED) as key:
            flag = winreg.QueryValueEx(key, NAME)[0]
    except OSError:
        return True                  # never touched in Task Manager: enabled
    return not (isinstance(flag, bytes) and flag and flag[0] & 1)    # 02 = enabled, 03 = disabled


def set_enabled(on: bool) -> None:
    """Turn it on or off the way Task Manager does (the entry stays listed there either way)."""
    if not supported():
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN) as key:
        winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, command())
    # 12 bytes: state, 3 zero bytes, then (when disabled) the time it was disabled as a FILETIME
    stamp = int((time.time() + 11644473600) * 10_000_000) if not on else 0
    flag = struct.pack("<I", 2 if on else 3) + struct.pack("<Q", stamp)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, APPROVED) as key:
        winreg.SetValueEx(key, NAME, 0, winreg.REG_BINARY, flag)
