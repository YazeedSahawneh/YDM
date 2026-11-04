"""Multi-connection HTTP downloader with resume and dynamic segment splitting.

How it works (the same idea IDM uses, plus a few tricks):
  1. Open the URL with `Range: bytes=0-`. The reply tells us the size and
     whether ranges work, and its body is kept as the first connection's data,
     so no round-trip is spent on a separate probe.
  2. Split the file into N segments and download them in parallel, each
     connection writing straight into its own spot of a pre-allocated file
     (sparse on Windows, so NTFS never stalls zero-filling a large file).
  3. When a connection finishes early, it takes half of the largest segment
     still in progress ("dynamic segmentation"), so all connections stay busy
     until the very end.
  4. A watcher reconnects any connection running far below the others (a bad
     route or a slow CDN node). A server that refuses an extra connection
     (429/503, or 403/421/509 while the other connections still work, or it
     keeps cutting that one connection) makes that connection retire, its
     piece goes to the others, and the host's limit is remembered.
  5. Progress of every segment is saved, so a paused or crashed download
     resumes from where each segment stopped.
  6. The connection count can change while it runs (set_connections): extra
     workers join by splitting the biggest pieces, surplus ones finish their
     current chunk and leave their piece to the others.
  7. Mirrors: other addresses of the same file (typed in, announced by the
     server with Metalink `Link: <…>; rel=duplicate` headers, or listed in the
     Metalink XML that mirror systems like MirrorCache/MirrorBrain send when asked). Each is checked
     first: same size, and the same bytes as a piece already downloaded. Then
     every new connection goes to the server with the most room for it (each
     server keeps its own connection limit, so the limits add up), weighted by
     how fast each one is. If one server's link dies, the others finish the file.
  8. In auto mode YDM picks the count itself (_Tuner): it starts where this
     server was fastest last time, adds connections while each step still buys
     at least 10 % more speed, and settles on the best count once it stops
     paying (your line is full, or the server caps the total). It tries one
     step up again every 30 s in case the route got faster.
"""
from __future__ import annotations

import html
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from ..utils import filename_from_headers, unique_path
from . import auth, net
from .hosts import HOSTS, HostMemory
from .item import EXPIRED, LOGIN_NEEDED, DownloadItem, Segment, Status
from .limiter import RateLimiter

CHUNK = 128 * 1024
MIN_SEGMENT = 1024 * 1024        # don't start with segments smaller than 1 MB
STEAL_MIN = 256 * 1024           # near the end, idle connections still split pieces down to this
MAX_RETRIES = 6
SLOW_RATIO = 0.3                 # a connection below 30% of the median speed…
SLOW_TICKS = 4                   # …for this many seconds gets reconnected
SAMPLE = 64 * 1024               # bytes compared to accept a mirror as the same file
MAX_MIRRORS = 8
METALINK_MIN = 32 * 1024 * 1024  # only big files are worth asking the server for its mirror list
AUTO_START = 6                   # auto mode, first visit to a server
AUTO_MAX = 32
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class Cancelled(Exception):
    pass


class _Throttled(Exception):
    """The server refused one more connection (HTTP 429/503)."""

    def __init__(self, code: int = 0, source: "_Source | None" = None):
        super().__init__(code)
        self.source = source


class _Retire(Exception):
    """This worker should stop; the others carry on with its segment."""


class _Surplus(_Retire):
    """The user lowered the connection count: this worker leaves."""


class Fatal(RuntimeError):
    """Retrying won't help (link expired, login refused, file changed)."""


class _SourceGone(Exception):
    """That server stopped serving the file; another one carries on (no wait, no retry count)."""


class _Source:
    """One server that has the file: the original address, or a mirror."""

    def __init__(self, url: str, primary: bool = False, limit: int = 0):
        self.url = url
        self.primary = primary
        self.state = "ok" if primary else "checking"     # checking | ok | bad
        self.note = ""
        self.open = 0            # connections receiving from it right now
        self.limit = limit       # most it accepted before refusing (0 = no limit seen)
        self.speed = 0.0         # bytes/s per connection lately
        self.fails = 0           # connection failures in a row
        self.cool_until = 0.0    # busy or failing: leave it alone until then

    @property
    def host(self) -> str:
        """The server actually answering (after redirects)."""
        return urlparse(self.url).hostname or ""

    def info(self) -> dict:
        return {"host": self.host, "url": self.url, "state": self.state, "note": self.note, "open": self.open,
                "limit": self.limit, "speed": self.speed, "primary": self.primary}


class _Tuner:
    """Hill-climbs the connection count on measured total speed. feed() gets one speed
    sample a second and returns a new count when it wants one."""
    SETTLE = 2              # seconds after a change before measuring (handshakes, TCP slow start)
    MEASURE = 3             # seconds averaged per step
    GAIN = 1.10             # a step must buy at least 10 % more speed
    REPROBE = 30            # seconds settled before trying one step up again

    def __init__(self, n: int, ceiling: int):
        self.n, self.ceiling = n, max(1, ceiling)
        self.best_n, self.best_rate = n, 0.0
        self.settled = False         # found the best count (stays True after a re-probe)
        self._climbing = True
        self._tick = 0
        self._samples: list[float] = []

    def restart(self, n: int) -> None:
        """Something changed outside (a mirror joined with its own connections): climb again from n."""
        self.n = self.best_n = n
        self.best_rate, self._climbing, self._tick, self._samples = 0.0, True, 0, []

    def feed(self, rate: float, remaining: int) -> int | None:
        self._tick += 1
        if not self._climbing:
            if self._tick >= self.REPROBE and self.n < self.ceiling:
                self._climbing, self._tick, self._samples, self.best_rate = True, 0, [], 0.0
            return None
        if self._tick <= self.SETTLE:
            return None
        self._samples.append(rate)
        if len(self._samples) < self.MEASURE:
            return None
        measured = sum(self._samples) / len(self._samples)
        self._samples, self._tick = [], 0
        if measured <= 0 or remaining < measured * 6:
            return None              # stalled, or too close to the end to learn anything
        if measured > self.best_rate * self.GAIN:
            self.best_rate, self.best_n = measured, self.n
            if self.n < self.ceiling:
                self.n = min(self.ceiling, self.n + max(2, self.n // 2))
                return self.n
        self._climbing, self.settled = False, True
        if self.n != self.best_n:    # the last step didn't pay: go back to the best count
            self.n = self.best_n
            return self.n
        return None


class HttpDownloader:
    supports_mirrors = True

    def __init__(self, item: DownloadItem, limiter: RateLimiter,
                 resolve_dir: Callable[[DownloadItem], str], hosts: HostMemory | None = None):
        self.item = item
        self.limiter = limiter
        self.resolve_dir = resolve_dir
        self.hosts = hosts or HOSTS
        self.host = urlparse(item.url).hostname or ""
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._error: Exception | None = None
        self._first: httpx.Response | None = None    # probe reply, reused for the first segment
        self._reconnect: set[int] = set()             # id(segment) of connections to re-open
        self._alive = 0                               # worker threads still running
        self._open = 0                                # connections currently receiving
        self._accepted = 0                            # most parallel connections the server took before refusing
        self._threads: list[threading.Thread] = []    # segment workers (more join when the user raises the count)
        self._target = 0                              # how many workers the user wants right now
        self._leaving = 0                             # workers told to leave that haven't yet
        self._joined = False                          # all workers finished: no more can join
        self._tuner: _Tuner | None = None             # auto connection count
        self.sources: list[_Source] = []              # [0] = the address itself, then verified mirrors
        self._mirror_client: httpx.Client | None = None
        headers = {"User-Agent": DEFAULT_UA, "Accept-Encoding": "identity"}
        headers.update({k: v for k, v in item.headers.items() if v})
        creds = auth.credentials_for(item)
        self.sources = [_Source(auth.split_userinfo(item.url)[0], primary=True,
                                limit=self.hosts.limit(self.host) or 0)]
        self._base_headers = {"User-Agent": headers["User-Agent"], "Accept-Encoding": "identity"}
        self.client = net.client(
            headers=headers,
            auth=auth.AutoAuth(*creds) if creds else None,
            timeout=httpx.Timeout(30.0, read=60.0),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )

    @property
    def url(self) -> str:
        """The original address (becomes the final one after redirects)."""
        return self.sources[0].url

    @url.setter
    def url(self, value: str) -> None:
        self.sources[0].url = value

    # ------------------------------------------------------------------ API
    def stop(self) -> None:
        self._stop.set()

    def add_mirrors(self, urls: list[str]) -> None:
        """More addresses of the same file; while it runs they're checked and join at once."""
        known = {s.url for s in self.sources} | set(self.item.mirrors) | {self.item.url}
        new = [u for u in urls if u.lower().startswith(("http://", "https://")) and u not in known]
        self.item.mirrors = (list(self.item.mirrors) + new)[:MAX_MIRRORS]
        with self._lock:
            running = bool(self._threads) and not self._joined
        if running:
            self._start_mirrors()

    def set_connections(self, n: int) -> None:
        """The user changed the count: that wins over auto mode."""
        self.item.auto_connections = False
        self._tuner = None
        self._resize(n)

    def _resize(self, n: int) -> None:
        """Change the number of connections of a running download."""
        n = max(1, min(64, int(n)))
        self.item.connections = n
        with self._lock:
            if not self._threads or self._joined:
                return                              # not split yet: the split will use the new count
            self._target = n
            missing = n - (self._alive - self._leaving)
            for _ in range(max(0, missing)):
                t = threading.Thread(target=self._worker, daemon=True)
                self._threads.append(t)
                self._alive += 1
                t.start()

    def run(self) -> None:
        item = self.item
        try:
            if not item.segments or item.total < 0 or not item.path:
                item.status = Status.CONNECTING
                self._probe()
            if self._stop.is_set():
                raise Cancelled
            item.status = Status.DOWNLOADING
            if item.resumable:
                self._run_segmented()
            else:
                self._run_single()
            if self._error:
                raise self._error
            if self._stop.is_set():
                raise Cancelled
            self._finalize()
        except Cancelled:
            item.status = Status.PAUSED
        except Exception as exc:  # noqa: BLE001 - shown to the user
            item.status = Status.ERROR
            item.error = _friendly_error(exc)
        finally:
            item.speed = 0
            for s in item.segments:
                s.speed = 0
            if self._first is not None:
                self._first.close()
                self._first = None
            self.client.close()
            if self._mirror_client is not None:
                self._mirror_client.close()
            item.sources = [s.info() for s in self.sources] if len(self.sources) > 1 else []

    # ---------------------------------------------------------------- probe
    def _probe(self) -> None:
        r = self.client.send(self.client.build_request("GET", self.url, headers={"Range": "bytes=0-"}),
                             stream=True)
        try:
            r.raise_for_status()
            total, resumable = -1, False
            if r.status_code == 206:
                cr = r.headers.get("content-range", "")
                size = cr.rsplit("/", 1)[-1] if "/" in cr else "*"
                if size.isdigit():
                    total, resumable = int(size), True
            else:
                cl = r.headers.get("content-length")
                total = int(cl) if cl and cl.isdigit() else -1
            name = filename_from_headers(str(r.url), r.headers)
        except BaseException:
            r.close()
            raise
        self._first = r
        self.url = str(r.url)          # every connection goes straight there, no redirect hop each time
        found = _metalink_mirrors([*r.history, r])
        if found:
            self.add_mirrors(found)
        self._layout(name, total, resumable)
        if self.supports_mirrors and self.item.resumable and total >= METALINK_MIN:
            threading.Thread(target=self._ask_metalink, daemon=True).start()

    def _layout(self, name: str, total: int, resumable: bool) -> None:
        """Pick the file name and folder, split into segments and reserve the file."""
        item = self.item
        if item.part_path and os.path.exists(item.part_path):
            os.remove(item.part_path)       # stale partial from a non-resumable attempt
        if not item.filename:
            item.filename = name
        from ..config import category_for
        item.category = category_for(item.filename)
        if not item.save_dir:
            item.save_dir = self.resolve_dir(item)
        Path(item.save_dir).mkdir(parents=True, exist_ok=True)
        final = unique_path(Path(item.save_dir) / item.filename)
        item.filename = final.name

        item.total = total
        item.resumable = resumable and total > 0
        item.downloaded = 0
        if item.resumable:
            n = max(1, min(self._connections(), total // MIN_SEGMENT or 1))
            size = total // n
            item.segments = [Segment(i * size, (i + 1) * size - 1 if i < n - 1 else total - 1)
                             for i in range(n)]
            _preallocate(item.part_path, total)
        else:
            item.segments = []

    def _connections(self) -> int:
        limit = self.hosts.limit(self.host)
        if self.item.auto_connections:
            n = self.hosts.best(self.host) or AUTO_START
            n = min(n, limit) if limit else n
            self.item.connections = n
            return n
        n = max(1, self.item.connections)
        return min(n, limit) if limit else n

    # ------------------------------------------------------ segmented mode
    def _run_segmented(self) -> None:
        item = self.item
        if not os.path.exists(item.part_path):
            # Partial file vanished: start over cleanly.
            item.segments, item.total, item.filename = [], -1, ""
            self._probe()
        for s in item.segments:
            s.active = False
        item.downloaded = sum(s.done for s in item.segments)
        with self._lock:
            self._target = self._connections()
            if self.item.auto_connections:
                limit = self.hosts.limit(self.host)
                self._tuner = _Tuner(self._target, min(AUTO_MAX, limit) if limit else AUTO_MAX)
            self._threads = [threading.Thread(target=self._worker, daemon=True) for _ in range(self._target)]
            self._alive = len(self._threads)
            for t in self._threads:
                t.start()
        finished = threading.Event()
        watcher = threading.Thread(target=self._watch, args=(finished,), daemon=True)
        watcher.start()
        self._start_mirrors()
        while True:                    # set_connections may add workers while we wait
            with self._lock:
                running = [t for t in self._threads if t.is_alive()]
                if not running:
                    self._joined = True
                    break
            running[0].join()
        finished.set()
        watcher.join()
        if not isinstance(self._error, Fatal):                  # an expired link isn't a limit
            for src in self.sources:
                if src.limit and src.state != "bad":
                    self.hosts.note_limit(src.host, src.limit)
        if not self._accepted and self._tuner is not None and self._tuner.settled and len(self.sources) == 1:
            self.hosts.note_best(self.host, self._tuner.best_n)

    # -------------------------------------------------------------- mirrors
    def _start_mirrors(self) -> None:
        if not self.supports_mirrors or not self.item.resumable:
            return
        with self._lock:
            known = {s.url for s in self.sources}
            new = [_Source(u, limit=self.hosts.limit(urlparse(u).hostname or "") or 0)
                   for u in self.item.mirrors if u not in known]
            self.sources = self.sources + new
        for src in new:
            threading.Thread(target=self._check_mirror, args=(src,), daemon=True).start()

    def _ask_metalink(self) -> None:
        """Mirror systems hand out the mirror list as Metalink XML when asked for it. Servers that
        don't just start sending the file: that answer is closed at once."""
        url = auth.split_userinfo(self.item.url)[0]
        body = b""
        try:
            with self.client.stream("GET", url, headers={
                    "Accept": "application/metalink4+xml, application/metalink+xml;q=0.9"}) as r:
                if r.status_code != 200 or "metalink" not in r.headers.get("content-type", ""):
                    return
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > 2 * 1024 * 1024:
                        return
        except httpx.HTTPError:
            return
        found = _metalink_xml(body.decode("utf-8", "replace"), self.item.total)
        if found and not self._stop.is_set():
            self.add_mirrors(found)

    def _mirror_http(self) -> httpx.Client:
        """Mirrors are other sites: they never get this download's cookies or login."""
        with self._lock:
            if self._mirror_client is None:
                self._mirror_client = net.client(headers=dict(self._base_headers),
                                                 timeout=httpx.Timeout(30.0, read=60.0),
                                                 limits=httpx.Limits(max_connections=64,
                                                                     max_keepalive_connections=32))
            return self._mirror_client

    def _sample(self) -> tuple[int, bytes] | None:
        """SAMPLE bytes this download already has on disk, to compare a mirror against."""
        end = time.monotonic() + 60
        while not self._stop.is_set() and not self._joined and time.monotonic() < end:
            with self._lock:
                seg = next((s for s in self.item.segments if s.done >= SAMPLE), None)
            if seg is not None:
                try:
                    with open(self.item.part_path, "rb") as fh:
                        fh.seek(seg.start)
                        return seg.start, fh.read(SAMPLE)
                except OSError:
                    return None              # finished (renamed) meanwhile
            self._stop.wait(0.25)
        return None

    def _check_mirror(self, src: _Source) -> None:
        """Same size and same bytes, or it isn't the same file."""
        def bad(note: str):
            src.state, src.note = "bad", note
        try:
            sample = self._sample()
            if sample is None:
                return bad("" if self._stop.is_set() else "nothing to compare with yet")
            start, data = sample
            r = self._mirror_http().get(src.url, headers={"Range": f"bytes={start}-{start + len(data) - 1}"})
            if r.status_code != 206:
                return bad(f"can't send pieces (HTTP {r.status_code})")
            size = r.headers.get("content-range", "").rsplit("/", 1)[-1]
            if not size.isdigit() or int(size) != self.item.total:
                return bad(f"a different file ({int(size):,} bytes)" if size.isdigit() else "size unknown")
            if r.content != data:
                return bad("a different file (the bytes don't match)")
            src.url = str(r.url)
            src.state = "ok"
        except httpx.HTTPError as exc:
            return bad(_friendly_error(exc))
        with self._lock:
            grow = not self._joined and not self._stop.is_set()
            n = self._target + (AUTO_START if self.item.auto_connections else 0)
        if grow:
            n = min(AUTO_MAX if self.item.auto_connections else 64, max(n, self._target))
            tuner = self._tuner
            if tuner is not None:
                tuner.restart(n)             # the new server has its own limit: find the best total again
            self._resize(n)

    def _pick_source(self) -> _Source | None:
        """The server with the most room for one more connection, favouring the fast ones."""
        with self._lock:
            now = time.monotonic()
            usable = [s for s in self.sources if s.state == "ok" and (not s.limit or s.open < s.limit)
                      and s.cool_until <= now]
            if not usable:
                return None
            top = max((s.speed for s in usable), default=0.0) or 1.0
            return min(usable, key=lambda s: ((s.open + 1) / (s.speed or top), not s.primary))

    def _other_good(self, src: _Source) -> bool:
        return any(s is not src and s.state == "ok" for s in self.sources)

    def _next_segment(self) -> Segment | None:
        with self._lock:
            for seg in self.item.segments:
                if not seg.active and seg.remaining > 0:
                    seg.active = True
                    return seg
            # Nothing free: steal half of the largest running segment.
            busy = [s for s in self.item.segments if s.active and s.remaining >= 2 * STEAL_MIN]
            if not busy:
                return None
            big = max(busy, key=lambda s: s.remaining)
            mid = big.pos + big.remaining // 2
            new = Segment(mid, big.end, active=True)
            big.end = mid - 1
            # assign a new list (never mutate in place) so a concurrent save sees a consistent list
            self.item.segments = sorted(self.item.segments + [new], key=lambda s: s.start)
            return new

    def _worker(self) -> None:
        left = False
        try:
            with open(self.item.part_path, "r+b", buffering=0) as fh:
                while not self._stop.is_set():
                    seg = self._next_segment()
                    if seg is None:
                        return
                    try:
                        self._download_segment(seg, fh)
                    finally:
                        with self._lock:
                            seg.active = False
                            seg.speed = 0
        except _Surplus:
            left = True
        except _Retire:
            pass
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                if self._error is None:
                    self._error = exc
            self._stop.set()
        finally:
            with self._lock:
                self._alive -= 1
                if left:
                    self._leaving -= 1

    def _surplus(self) -> bool:
        """Called between chunks: is this worker one too many after the user lowered the count?"""
        with self._lock:
            if self._alive - self._leaving > self._target:
                self._leaving += 1
                return True
        return False

    def _open_range(self, seg: Segment) -> httpx.Response:
        with self._lock:
            first = self._first if seg.start == 0 and seg.done == 0 else None
            if first is not None:
                self._first = None
        if first is not None:
            return first
        src = self._pick_source()
        if src is None:
            raise _Throttled(0)             # every server is at its limit
        client = self.client if src.primary else self._mirror_http()
        req = client.build_request("GET", src.url, headers={"Range": f"bytes={seg.pos}-{seg.end}"})
        try:
            r = client.send(req, stream=True)
        except httpx.HTTPError as exc:
            exc.ydm_source = src             # so the caller knows which server failed
            raise
        r.ydm_source = src
        if r.history and r.history[-1].status_code != 401:
            src.url = str(r.url)            # resumed download: learn the final address once
        if r.status_code != 206:
            code = r.status_code
            r.close()
            if code in (429, 503):
                raise _Throttled(code, src)
            if code in (403, 421, 509) and (src.open > 0 or src.limit):
                # its other connections still work, or it has refused extras like this before:
                # a connection limit (a closed connection can count a moment longer), not the link
                raise _Throttled(code, src)
            if code == 401:
                self._drop(src, "asks for a login")
                raise Fatal(f"{LOGIN_NEEDED}: the server refused the login")
            if code in (403, 404, 410) or (code == 200 and self.item.downloaded):
                self._drop(src, f"link stopped working (HTTP {code})")
                raise Fatal(f"{EXPIRED}: renew the address to continue (HTTP {code})")
            self._drop(src, f"stopped sending pieces (HTTP {code})")
            raise RuntimeError(f"Server stopped honouring ranges (HTTP {code})")
        cr = r.headers.get("content-range", "")
        size = cr.rsplit("/", 1)[-1]
        if size.isdigit() and self.item.total > 0 and int(size) != self.item.total:
            r.close()
            self._drop(src, f"now serves a different file ({int(size):,} bytes)")
            raise Fatal(f"{EXPIRED}: this address now serves a different file "
                        f"({int(size):,} bytes, expected {self.item.total:,})")
        return r

    def _drop(self, src: _Source, note: str) -> None:
        """This server can't go on. With another good one, carry on there instead of failing."""
        with self._lock:
            if not self._other_good(src):
                return                       # the caller's error stands
            src.state, src.note = "bad", note
        raise _SourceGone(note)

    def _download_segment(self, seg: Segment, fh) -> None:
        attempts = 0
        while not self._stop.is_set() and seg.remaining > 0:
            try:
                r = self._open_range(seg)
                src = getattr(r, "ydm_source", None) or self.sources[0]
                with self._lock:
                    self._open += 1
                    src.open += 1
                    seg.source = src
                try:
                    # raw bytes: we asked for identity encoding, so skip httpx's decoder layer
                    chunks = r.iter_raw(CHUNK) if not r.headers.get("content-encoding") else r.iter_bytes(CHUNK)
                    for chunk in chunks:
                        if self._stop.is_set():
                            return
                        with self._lock:          # one lock per chunk: claim the bytes, then write them
                            allowed = seg.remaining
                            if allowed <= 0:
                                break
                            if len(chunk) > allowed:
                                chunk = chunk[:allowed]
                            pos = seg.pos
                            seg.done += len(chunk)
                            self.item.downloaded += len(chunk)
                        fh.seek(pos)
                        fh.write(chunk)
                        attempts = src.fails = 0
                        self.limiter.consume(len(chunk), self._stop)
                        if seg.remaining <= 0:
                            break
                        if id(seg) in self._reconnect:
                            self._reconnect.discard(id(seg))
                            break               # close this slow connection and open a fresh one
                        if self._alive - self._leaving > self._target and self._surplus():
                            raise _Surplus
                except (httpx.HTTPError, OSError) as exc:
                    exc.ydm_source = src
                    raise
                finally:
                    r.close()
                    with self._lock:
                        self._open -= 1
                        src.open -= 1
                        seg.source = None
            except _SourceGone:
                continue                        # straight on with another server
            except _Throttled as t:
                with self._lock:
                    if t.source is not None and t.source.open > 0:
                        # that server's limit: the ones it's serving now (never lower it on one refusal:
                        # a connection we just closed may still count on its side for a moment)
                        t.source.limit = max(t.source.limit, t.source.open)
                    elif t.source is not None and len(self.sources) > 1:
                        t.source.cool_until = time.monotonic() + 10     # busy right now: try the others
                    working, others = self._open, self._alive - 1
                other = self._pick_source() if t.source is not None else None
                if other is not None and other is not t.source:
                    continue                    # another server has room for this connection
                if others > 0:
                    with self._lock:
                        self._accepted = max(self._accepted, working)
                    raise _Retire
                attempts += 1
                if t.args[0] == 403 and attempts > 2:
                    raise Fatal(f"{EXPIRED}: renew the address to continue (HTTP 403)")
                if attempts > MAX_RETRIES:
                    raise RuntimeError("Server is busy (too many requests)")
                self._stop.wait(min(2 ** attempts, 30))
            except Fatal:
                raise
            except (httpx.HTTPError, OSError, RuntimeError) as exc:
                attempts += 1
                src = getattr(exc, "ydm_source", None)
                many = len(self.sources) > 1
                with self._lock:
                    receiving = self._open
                    if many and src is not None:     # a mirror (or the original) acting up: rest it a while
                        src.fails += 1
                        src.cool_until = time.monotonic() + min(5 * 2 ** src.fails, 60)
                        if src.fails >= 4 and self._other_good(src):
                            src.state, src.note = "bad", f"keeps failing ({_friendly_error(exc)})"
                if many and attempts < MAX_RETRIES and self._pick_source() is not None:
                    continue                         # another server takes this piece
                if attempts >= 2 and receiving > 0:
                    # this connection keeps failing while the others download: the server allows only
                    # that many (some cut extra connections instead of saying so). Leave the piece to them.
                    with self._lock:
                        self._accepted = max(self._accepted, receiving)
                        target = src if src is not None else (None if many else self.sources[0])
                        if target is not None and target.open > 0 and not target.limit:
                            target.limit = target.open
                    raise _Retire from exc
                if attempts > MAX_RETRIES:
                    raise exc
                self._stop.wait(min(2 ** attempts, 20))

    def _watch(self, finished: threading.Event) -> None:
        """Every second: per-connection speed, and reconnect the stragglers."""
        prev: dict[int, int] = {}
        slow: dict[int, int] = {}
        best = 0.0                      # best single-connection speed lately (for the last few pieces)
        last_total = self.item.downloaded
        while not finished.wait(1.0):
            total = self.item.downloaded
            tuner = self._tuner
            if tuner is not None:
                if self._accepted:      # the server refused a connection: it set the limit, stop tuning
                    self._tuner = None
                else:
                    remaining = sum(s.remaining for s in self.item.segments)
                    want = tuner.feed(total - last_total, remaining)
                    if want is not None and self._tuner is tuner and not self._stop.is_set():
                        self._resize(want)
            last_total = total
            with self._lock:
                segs = self.item.segments
                active = [s for s in segs if s.active]
                for s in active:
                    got = s.done - prev.get(id(s), s.done)
                    s.speed = got if s.speed == 0 else s.speed * 0.5 + got * 0.5
                prev = {id(s): s.done for s in segs}
                if len(self.sources) > 1:          # per-connection speed of every server, for _pick_source
                    for src in self.sources:
                        mine = [s.speed for s in active if s.source is src]
                        if mine:
                            src.speed = src.speed * 0.5 + (sum(mine) / len(mine)) * 0.5 if src.speed else \
                                sum(mine) / len(mine)
                    self.item.sources = [src.info() for src in self.sources]
                best = max([best * 0.97] + [s.speed for s in active])
                if not active:
                    continue
                speeds = sorted(s.speed for s in active)
                ref = speeds[len(speeds) // 2] if len(active) >= 3 else best
                for s in active:
                    if ref > 64 * 1024 and s.speed < ref * SLOW_RATIO and s.remaining > 2 * STEAL_MIN:
                        slow[id(s)] = slow.get(id(s), 0) + 1
                        if slow[id(s)] >= SLOW_TICKS:
                            self._reconnect.add(id(s))
                            slow[id(s)] = -SLOW_TICKS       # grace period while it reconnects
                    else:
                        slow.pop(id(s), None)

    # --------------------------------------------------------- single mode
    def _run_single(self) -> None:
        item = self.item
        attempts = 0
        while not self._stop.is_set():
            try:
                item.downloaded = 0
                r = self._open_full()
                try:
                    with open(item.part_path, "wb") as fh:
                        for chunk in r.iter_bytes(CHUNK):
                            if self._stop.is_set():
                                return
                            fh.write(chunk)
                            item.downloaded += len(chunk)
                            self.limiter.consume(len(chunk), self._stop)
                finally:
                    r.close()
                if item.total < 0:
                    item.total = item.downloaded
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403, 404, 410):
                    raise
                attempts += 1
                if attempts > MAX_RETRIES:
                    raise exc
                self._stop.wait(min(2 ** attempts, 20))
            except (httpx.HTTPError, OSError, RuntimeError) as exc:
                if isinstance(exc, Fatal):
                    raise
                attempts += 1
                if attempts > MAX_RETRIES:
                    raise exc
                self._stop.wait(min(2 ** attempts, 20))

    def _open_full(self):
        """The whole file on one connection (server without ranges)."""
        r, self._first = self._first, None
        if r is None:
            r = self.client.send(self.client.build_request("GET", self.url), stream=True)
        try:
            r.raise_for_status()
        except BaseException:
            r.close()
            raise
        return r

    # ------------------------------------------------------------ finalize
    def _finalize(self) -> None:
        item = self.item
        item.status = Status.PROCESSING
        size = os.path.getsize(item.part_path)
        if item.total > 0 and size != item.total:
            raise RuntimeError(f"Size mismatch: got {size} of {item.total} bytes")
        if item.resumable and any(s.remaining for s in item.segments):
            raise RuntimeError("Some parts are incomplete; press Resume to continue")
        part = item.part_path
        # name/folder picked while the download was already running take effect now
        final = Path(item.dest_dir or item.save_dir) / (item.dest_name or item.filename)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():              # something appeared there meanwhile
            final = unique_path(final)
        try:
            os.replace(part, final)
        except OSError:
            shutil.move(part, final)    # another drive
        item.save_dir, item.filename = str(final.parent), final.name
        item.dest_dir = item.dest_name = ""
        item.segments = []
        item.downloaded = item.total = size
        item.status = Status.COMPLETED
        item.finished = time.time()


def _metalink_mirrors(responses) -> list[str]:
    """Metalink/HTTP (RFC 6249): mirror servers announce copies as `Link: <url>; rel=duplicate`."""
    found = []
    for r in responses:
        for value in r.headers.get_list("link"):
            for url, params in re.findall(r"<([^>]+)>\s*((?:;[^,<]*)*)", value):
                if re.search(r"rel\s*=\s*\"?duplicate", params, re.I) and url.lower().startswith(("http://", "https://")):
                    found.append(url)
    return list(dict.fromkeys(found))[:MAX_MIRRORS]


def _metalink_xml(text: str, total: int) -> list[str]:
    """Mirror addresses from a Metalink 3/4 document, if it describes a file of this size."""
    sizes = {int(x) for x in re.findall(r"<size>\s*(\d+)\s*</size>", text)}
    if sizes and total not in sizes:
        return []                        # it describes some other file
    urls = [html.unescape(u.strip()) for u in re.findall(r"<url\b[^>]*>([^<]+)</url>", text)]
    return list(dict.fromkeys(u for u in urls if u.lower().startswith(("http://", "https://"))))[:MAX_MIRRORS]


def _preallocate(path: str, total: int) -> None:
    with open(path, "wb") as fh:
        if os.name == "nt" and _set_size_sparse(fh, total):
            return
        fh.truncate(total)


def _set_size_sparse(fh, total: int) -> bool:
    """Windows: mark the file sparse and set its size with the Win32 API.

    Python's truncate() on Windows writes zeros through the whole file (about 3 s per GB,
    all before the first byte is downloaded), and a non-sparse file makes NTFS zero-fill
    the gap before any write at a far offset. Sparse + SetEndOfFile is instant at any size.
    """
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD]
        k32.SetEndOfFile.argtypes = [wintypes.HANDLE]
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(fh.fileno()))
        returned = wintypes.DWORD()
        k32.DeviceIoControl(handle, 0x000900C4, None, 0, None, 0,  # FSCTL_SET_SPARSE
                            ctypes.byref(returned), None)
        return bool(k32.SetFilePointerEx(handle, total, None, 0) and k32.SetEndOfFile(handle))
    except (OSError, AttributeError, ImportError):
        return False


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401:
            return f"{LOGIN_NEEDED}: enter a user name and password"
        hints = {403: "access denied (link may need login or has expired)",
                 404: "file not found", 410: "link expired", 416: "invalid range",
                 429: "too many requests, try fewer connections"}
        return f"HTTP {code}: {hints.get(code, exc.response.reason_phrase)}"
    if isinstance(exc, httpx.ConnectError):
        return "Could not connect to server"
    if isinstance(exc, httpx.TimeoutException):
        return "Connection timed out"
    return str(exc) or exc.__class__.__name__
