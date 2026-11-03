"""Passwords at rest (download logins, site logins, proxy).

Windows: DPAPI, so the stored blob only opens under the same Windows account.
Elsewhere (the Linux test runs): plain base64, marked as such.
"""
from __future__ import annotations

import base64
import sys


def protect(text: str) -> str:
    if not text:
        return ""
    raw = text.encode("utf-8")
    if sys.platform == "win32":
        try:
            return "dpapi:" + base64.b64encode(_dpapi(raw, encrypt=True)).decode("ascii")
        except OSError:
            pass
    return "b64:" + base64.b64encode(raw).decode("ascii")


def reveal(blob: str) -> str:
    if not blob:
        return ""
    kind, _, data = blob.partition(":")
    try:
        raw = base64.b64decode(data)
        if kind == "dpapi":
            raw = _dpapi(raw, encrypt=False)
        elif kind != "b64":
            return ""
        return raw.decode("utf-8")
    except (OSError, ValueError):
        return ""


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt = ctypes.windll.crypt32
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = Blob(len(data), buf), Blob()
    fn = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out))   # 0x1: no UI
    if not ok:
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)
