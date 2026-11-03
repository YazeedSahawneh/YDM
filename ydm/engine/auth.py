"""Logins for servers that ask for one: HTTP Basic, Digest and NTLM (Negotiate).

Where the user name and password come from, first match wins:
  1. the download itself (typed in the download window or "Login…" in the list),
  2. the address (https://user:pass@host/file, or ftp://user:pass@host/file),
  3. a saved site login (Options → Proxy & logins), matched on the host or a parent domain.

The scheme is learned from the server's first 401 and then sent up front on every
further connection, so a 16-connection download pays the 401 round-trip once
(Basic, Digest). NTLM authenticates a connection, so each new one shakes hands.
"""
from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

from .. import vault


def split_userinfo(url: str) -> tuple[str, str, str]:
    """('https://host/file', user, password) from 'https://user:pass@host/file'."""
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url, "", ""
    info, _, hostport = parts.netloc.rpartition("@")
    user, _, password = info.partition(":")
    return urlunsplit(parts._replace(netloc=hostport)), unquote(user), unquote(password)


def credentials_for(item) -> tuple[str, str] | None:
    if item.auth_user:
        return item.auth_user, vault.reveal(item.auth_pass)
    _url, user, password = split_userinfo(item.url)
    if user:
        return user, password
    return LOGINS.find(urlsplit(item.url).hostname or "")


class SiteLogins:
    """host -> (user, protected password), in logins.json."""

    def __init__(self, path: Path | None = None):
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] | None = None

    def _file(self) -> Path:
        if self._path is None:
            from ..config import app_data_dir
            self._path = app_data_dir() / "logins.json"
        return self._path

    def _load(self) -> dict[str, dict]:
        if self._data is None:
            try:
                self._data = json.loads(self._file().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._data = {}
        return self._data

    def _write(self) -> None:
        try:
            tmp = self._file().with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data or {}, indent=1), encoding="utf-8")
            os.replace(tmp, self._file())
        except OSError:
            pass

    def all(self) -> list[tuple[str, str]]:
        with self._lock:
            return sorted((h, e.get("user", "")) for h, e in self._load().items())

    def find(self, host: str) -> tuple[str, str] | None:
        host = host.lower()
        with self._lock:
            data = self._load()
            while host:
                entry = data.get(host)
                if entry:
                    return entry.get("user", ""), vault.reveal(entry.get("password", ""))
                host = host.partition(".")[2] if "." in host else ""
        return None

    def save(self, host: str, user: str, password: str) -> None:
        host = host.strip().lower()
        if not host:
            return
        with self._lock:
            self._load()[host] = {"user": user, "password": vault.protect(password)}
            self._write()

    def remove(self, host: str) -> None:
        with self._lock:
            if self._load().pop(host, None) is not None:
                self._write()


LOGINS = SiteLogins()


class AutoAuth(httpx.Auth):
    """Answers whichever challenge the server sends: Basic, Digest, NTLM or Negotiate."""

    def __init__(self, user: str, password: str):
        self.user, self.password = user, password
        self.scheme = ""                          # learned from the first 401
        self._digest = httpx.DigestAuth(user, password)
        self._lock = threading.Lock()

    def auth_flow(self, request):
        if self.scheme == "basic":
            request.headers["Authorization"] = httpx.BasicAuth(self.user, self.password)._build_auth_header(
                self.user, self.password)
        elif self.scheme == "digest" and self._digest._last_challenge:
            with self._lock:
                request.headers["Authorization"] = self._digest._build_auth_header(
                    request, self._digest._last_challenge)
        elif self.scheme in ("ntlm", "negotiate"):
            yield from self._ntlm(request, self.scheme)
            return
        response = yield request
        if response.status_code != 401:
            return
        offers = [h.strip() for h in response.headers.get_list("www-authenticate")]
        lower = [h.lower() for h in offers]
        if any(h.startswith("digest ") for h in lower):
            header = offers[next(i for i, h in enumerate(lower) if h.startswith("digest "))]
            with self._lock:
                self.scheme = "digest"
                self._digest._last_challenge = self._digest._parse_challenge(request, response, header)
                self._digest._nonce_count = 1
                request.headers["Authorization"] = self._digest._build_auth_header(
                    request, self._digest._last_challenge)
            yield request
        elif any(h.startswith(("ntlm", "negotiate")) for h in lower):
            self.scheme = "ntlm" if any(h.startswith("ntlm") for h in lower) else "negotiate"
            yield from self._ntlm(request, self.scheme)
        elif any(h.startswith("basic") for h in lower) and self.scheme != "basic":
            self.scheme = "basic"
            request.headers["Authorization"] = httpx.BasicAuth(self.user, self.password)._build_auth_header(
                self.user, self.password)
            yield request

    def _ntlm(self, request, scheme: str):
        """Three-leg handshake on one connection: negotiate → challenge → authenticate."""
        try:
            import spnego
        except ImportError:
            return
        label = "NTLM" if scheme == "ntlm" else "Negotiate"
        ctx = spnego.client(self.user, self.password, protocol="ntlm" if scheme == "ntlm" else "negotiate")
        request.headers["Authorization"] = f"{label} " + base64.b64encode(ctx.step()).decode("ascii")
        response = yield request
        if response.status_code != 401:
            return
        challenge = next((h.split(" ", 1)[1] for h in response.headers.get_list("www-authenticate")
                          if h.lower().startswith(label.lower() + " ")), "")
        if not challenge:
            return
        token = ctx.step(base64.b64decode(challenge))
        request.headers["Authorization"] = f"{label} " + base64.b64encode(token or b"").decode("ascii")
        yield request
