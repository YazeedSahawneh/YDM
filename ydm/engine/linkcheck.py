"""Can YDM fetch this link by itself? The extension asks before it takes a download away from the browser.

Some downloads need more than the address: a login token the page sends in a header, a form the page
posts, a check page in front of the file. YDM can't repeat those, so the extension leaves such downloads
to the browser instead of cancelling them and failing here. The browser keeps downloading while YDM
checks, so a one-time link isn't used up by the check (the browser got there first).
"""
from __future__ import annotations

import httpx

from . import net

HTML = ("text/html", "application/xhtml+xml")


def check(url: str, headers: dict | None = None, mime: str = "") -> dict:
    """{"ok": True} when a plain GET with the browser's cookies, referrer and user agent gets the file;
    else {"ok": False, "reason": "…"} in words the user can act on."""
    hdrs = {k: v for k, v in (headers or {}).items() if v}
    hdrs["Range"] = "bytes=0-0"                      # one byte is enough to know
    try:
        with net.client(timeout=httpx.Timeout(10.0, connect=6.0)) as c, c.stream("GET", url, headers=hdrs) as r:
            code = r.status_code
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    except httpx.HTTPError as exc:
        return {"ok": False, "reason": f"YDM couldn't reach the server ({exc.__class__.__name__})"}
    if code in (401, 407):
        return {"ok": False, "reason": "the server wants a login that only the browser has"}
    if code == 403:
        return {"ok": False, "reason": "the server lets only the browser in"}
    if code in (404, 410):
        return {"ok": False, "reason": "the link only works inside the browser"}
    if code == 405:
        return {"ok": False, "reason": "the page sends a form to get this file"}
    if code >= 400 and code != 416:                 # 416: an empty file, YDM handles that
        return {"ok": False, "reason": f"the server answered {code}"}
    if ctype in HTML and not (mime or "").lower().startswith(HTML):
        return {"ok": False, "reason": "the server sent a web page instead of the file (a login or check page)"}
    return {"ok": True, "status": code, "type": ctype}
