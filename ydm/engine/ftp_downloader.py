"""FTP / FTPS downloads with the same multi-connection engine as HTTP.

Each segment is its own FTP session: login, `REST <offset>`, `RETR`, and the
connection is dropped once the segment's bytes are in. Servers that allow REST
get dynamic segmentation, live connection changes and resume just like HTTP;
a "421 too many connections" reply retires the extra connection and the
host's limit is remembered. Without REST it falls back to one connection.

ftp:// is anonymous unless a login is given (address, download or site login).
The proxy setting does not apply to FTP.
"""
from __future__ import annotations

import ftplib
import os
from urllib.parse import unquote, urlsplit

from . import auth
from .http_downloader import Fatal, HttpDownloader, _Throttled
from .item import LOGIN_NEEDED

TIMEOUT = 30


class _Stream:
    """Looks enough like an httpx response for the segment loop: headers, iter_raw, close."""

    headers: dict = {}

    def __init__(self, ftp: ftplib.FTP, sock):
        self.ftp, self.sock = ftp, sock

    def iter_raw(self, size: int):
        while True:
            data = self.sock.recv(size)
            if not data:
                return
            yield data

    iter_bytes = iter_raw

    def close(self) -> None:
        for fn in (self.sock.close, self.ftp.close):     # no QUIT handshake: just drop both sockets
            try:
                fn()
            except (OSError, EOFError):
                pass


class FtpDownloader(HttpDownloader):
    supports_mirrors = False
    def __init__(self, item, limiter, resolve_dir, hosts=None):
        super().__init__(item, limiter, resolve_dir, hosts)
        parts = urlsplit(auth.split_userinfo(item.url)[0])
        self.tls = parts.scheme.lower() == "ftps"
        self.port = parts.port or 21
        self.remote = unquote(parts.path) or "/"
        creds = auth.credentials_for(item)
        self.user, self.password = creds if creds else ("anonymous", "anonymous@")

    def _connect(self) -> ftplib.FTP:
        ftp = (ftplib.FTP_TLS if self.tls else ftplib.FTP)(timeout=TIMEOUT)
        try:
            ftp.connect(self.host, self.port)
            ftp.login(self.user, self.password)
            if self.tls:
                ftp.prot_p()
            ftp.voidcmd("TYPE I")
        except ftplib.error_temp as exc:
            ftp.close()
            if str(exc).startswith("421"):
                raise _Throttled(421) from exc
            raise RuntimeError(f"FTP: {exc}") from exc
        except ftplib.error_perm as exc:
            ftp.close()
            if str(exc).startswith("530"):
                raise Fatal(f"{LOGIN_NEEDED}: the FTP server refused the login ({exc})") from exc
            raise Fatal(f"FTP: {exc}") from exc
        except (OSError, EOFError, ftplib.Error) as exc:
            ftp.close()
            raise OSError(f"FTP: {exc}") from exc
        return ftp

    def _probe(self) -> None:
        ftp = self._connect()
        try:
            try:
                total = ftp.size(self.remote) or -1
            except ftplib.error_perm as exc:
                if str(exc).startswith("550") and not self.remote.endswith("/"):
                    total = -1                   # some servers refuse SIZE; RETR will tell
                else:
                    raise
            try:
                resumable = ftp.sendcmd("REST 0").startswith("350")
            except ftplib.all_errors:
                resumable = False
        except ftplib.error_perm as exc:
            raise Fatal(f"FTP: {exc}") from exc
        finally:
            ftp.close()
        name = os.path.basename(self.remote.rstrip("/")) or "download"
        self._layout(name, total, resumable and total > 0)

    def _retr(self, offset: int) -> _Stream:
        ftp = self._connect()
        try:
            sock = ftp.transfercmd(f"RETR {self.remote}", rest=offset or None)
        except ftplib.error_temp as exc:
            ftp.close()
            if str(exc).startswith(("421", "425")):
                raise _Throttled(421) from exc
            raise RuntimeError(f"FTP: {exc}") from exc
        except ftplib.error_perm as exc:
            ftp.close()
            raise Fatal(f"FTP: {exc}") from exc
        except (OSError, EOFError, ftplib.Error) as exc:
            ftp.close()
            raise OSError(f"FTP: {exc}") from exc
        return _Stream(ftp, sock)

    def _open_range(self, seg) -> _Stream:
        return self._retr(seg.pos)

    def _open_full(self) -> _Stream:
        return self._retr(0)
