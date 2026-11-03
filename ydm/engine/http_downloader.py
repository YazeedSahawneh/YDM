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
     route or a slow CDN node), and a server that answers 429/503 to an extra
     connection makes that connection retire; the host's limit is remembered.
  5. Progress of every segment is saved, so a paused or crashed download
     resumes from where each segment stopped.
  6. The connection count can change while it runs (set_connections): extra
     workers join by splitting the biggest pieces, surplus ones finish their
     current chunk and leave their piece to the others.
"""
from __future__ import annotations

import os
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
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class Cancelled(Exception):
    pass


class _Throttled(Exception):
    """The server refused one more connection (HTTP 429/503)."""


class _Retire(Exception):
    """This worker should stop; the others carry on with its segment."""


class _Surplus(_Retire):
    """The user lowered the connection count: this worker leaves."""


class Fatal(RuntimeError):
    """Retrying won't help (link expired, login refused, file changed)."""


class HttpDownloader:
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
        headers = {"User-Agent": DEFAULT_UA, "Accept-Encoding": "identity"}
        headers.update({k: v for k, v in item.headers.items() if v})
        creds = auth.credentials_for(item)
        self.url = auth.split_userinfo(item.url)[0]   # becomes the final address after redirects
        self.client = net.client(
            headers=headers,
            auth=auth.AutoAuth(*creds) if creds else None,
            timeout=httpx.Timeout(30.0, read=60.0),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )

    # ------------------------------------------------------------------ API
    def stop(self) -> None:
        self._stop.set()

    def set_connections(self, n: int) -> None:
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
        self._layout(name, total, resumable)

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
        n = max(1, self.item.connections)
        limit = self.hosts.limit(self.host)
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
            self._threads = [threading.Thread(target=self._worker, daemon=True) for _ in range(self._target)]
            self._alive = len(self._threads)
            for t in self._threads:
                t.start()
        finished = threading.Event()
        watcher = threading.Thread(target=self._watch, args=(finished,), daemon=True)
        watcher.start()
        while True:                    # set_connections may add workers while we wait
            with self._lock:
                running = [t for t in self._threads if t.is_alive()]
                if not running:
                    self._joined = True
                    break
            running[0].join()
        finished.set()
        watcher.join()
        if self._accepted:
            self.hosts.note_limit(self.host, self._accepted)

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
        req = self.client.build_request("GET", self.url, headers={"Range": f"bytes={seg.pos}-{seg.end}"})
        r = self.client.send(req, stream=True)
        if r.history and r.history[-1].status_code != 401:
            self.url = str(r.url)          # resumed download: learn the final address once
        if r.status_code != 206:
            code = r.status_code
            r.close()
            if code in (429, 503):
                raise _Throttled(code)
            if code == 401:
                raise Fatal(f"{LOGIN_NEEDED}: the server refused the login")
            if code in (403, 404, 410) or (code == 200 and self.item.downloaded):
                raise Fatal(f"{EXPIRED}: renew the address to continue (HTTP {code})")
            raise RuntimeError(f"Server stopped honouring ranges (HTTP {code})")
        cr = r.headers.get("content-range", "")
        size = cr.rsplit("/", 1)[-1]
        if size.isdigit() and self.item.total > 0 and int(size) != self.item.total:
            r.close()
            raise Fatal(f"{EXPIRED}: this address now serves a different file "
                        f"({int(size):,} bytes, expected {self.item.total:,})")
        return r

    def _download_segment(self, seg: Segment, fh) -> None:
        attempts = 0
        while not self._stop.is_set() and seg.remaining > 0:
            try:
                r = self._open_range(seg)
                with self._lock:
                    self._open += 1
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
                        attempts = 0
                        self.limiter.consume(len(chunk), self._stop)
                        if seg.remaining <= 0:
                            break
                        if id(seg) in self._reconnect:
                            self._reconnect.discard(id(seg))
                            break               # close this slow connection and open a fresh one
                        if self._alive - self._leaving > self._target and self._surplus():
                            raise _Surplus
                finally:
                    r.close()
                    with self._lock:
                        self._open -= 1
            except _Throttled:
                with self._lock:
                    working, others = self._open, self._alive - 1
                if others > 0:
                    with self._lock:
                        self._accepted = max(self._accepted, working)
                    raise _Retire
                attempts += 1
                if attempts > MAX_RETRIES:
                    raise RuntimeError("Server is busy (too many requests)")
                self._stop.wait(min(2 ** attempts, 30))
            except Fatal:
                raise
            except (httpx.HTTPError, OSError, RuntimeError) as exc:
                attempts += 1
                if attempts > MAX_RETRIES:
                    raise exc
                self._stop.wait(min(2 ** attempts, 20))

    def _watch(self, finished: threading.Event) -> None:
        """Every second: per-connection speed, and reconnect the stragglers."""
        prev: dict[int, int] = {}
        slow: dict[int, int] = {}
        best = 0.0                      # best single-connection speed lately (for the last few pieces)
        while not finished.wait(1.0):
            with self._lock:
                segs = self.item.segments
                active = [s for s in segs if s.active]
                for s in active:
                    got = s.done - prev.get(id(s), s.done)
                    s.speed = got if s.speed == 0 else s.speed * 0.5 + got * 0.5
                prev = {id(s): s.done for s in segs}
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
