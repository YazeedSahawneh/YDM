"""Shared HTTP plumbing.

Building an SSL context loads the whole certificate store: ~1 s the first time
and ~0.25 s after that. httpx does it for every Client, and YDM makes a Client
per download (and per video stream, per link check), so that cost used to sit
in front of every "Connecting…". One context, built once and shared, removes it.
"""
from __future__ import annotations

import ssl
import threading

import httpx

_ctx: ssl.SSLContext | None = None
_lock = threading.Lock()
_proxy: str | None = None          # "http://user:pass@host:port" or "socks5://…"; None = direct

# httpcore reads the socket 64 KB at a time. Letting one read take up to 1 MB (whatever has
# arrived) means far fewer trips through Python per byte: measured 180 → 375 MB/s on a fast
# link, and the window stays smooth because the download threads hold the GIL less often.
try:
    import httpcore._sync.http11 as _h11
    _h11.HTTP11Connection.READ_NUM_BYTES = 1 << 20
except (ImportError, AttributeError):
    pass


def ssl_context() -> ssl.SSLContext:
    global _ctx
    with _lock:
        if _ctx is None:
            import certifi
            _ctx = ssl.create_default_context(cafile=certifi.where())
        return _ctx


def warm_up() -> None:
    """At app start, in the background: build the context and one throwaway client
    (the first Client also pays ~0.25 s of one-time imports)."""
    threading.Thread(target=lambda: client().close(), daemon=True).start()


def configure_proxy(mode: str, host: str = "", port: int = 0, user: str = "", password: str = "") -> None:
    """mode: "none" | "system" (Windows/IE settings) | "http" | "socks5". Applies to new connections."""
    global _proxy
    _proxy = proxy_url(mode, host, port, user, password)


def proxy_url(mode: str, host: str = "", port: int = 0, user: str = "", password: str = "") -> str | None:
    from urllib.parse import quote
    if mode == "system":
        import urllib.request
        found = urllib.request.getproxies()
        url = found.get("https") or found.get("http") or found.get("socks") or ""
        if url.startswith("socks://"):
            url = "socks5://" + url[len("socks://"):]
        return url or None
    if mode not in ("http", "socks5") or not host:
        return None
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}@" if user else ""
    return f"{mode}://{auth}{host}:{port or (1080 if mode == 'socks5' else 8080)}"


def current_proxy() -> str | None:
    return _proxy


def client(**kwargs) -> httpx.Client:
    kwargs.setdefault("verify", ssl_context())
    kwargs.setdefault("follow_redirects", True)
    if _proxy and "proxy" not in kwargs:
        kwargs["proxy"] = _proxy
    return httpx.Client(**kwargs)


def get(url: str, **kwargs) -> httpx.Response:
    with client(timeout=kwargs.pop("timeout", 15)) as c:
        return c.get(url, **kwargs)
