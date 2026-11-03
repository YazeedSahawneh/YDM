"""Multi-connection HTTP downloader with resume and dynamic segment splitting.

How it works (the same idea IDM uses):
  1. Probe the URL with a `Range: bytes=0-0` request to learn the size and
     whether the server supports ranges.
  2. Split the file into N segments and download them in parallel, each
     connection writing straight into its own spot of a pre-allocated file.
  3. When a connection finishes early, it takes half of the largest segment
     still in progress ("dynamic segmentation"), so all connections stay busy
     until the very end.
  4. Progress of every segment is saved, so a paused or crashed download
     resumes from where each segment stopped.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable

import httpx

from ..utils import filename_from_headers, unique_path
from .item import DownloadItem, Segment, Status
from .limiter import RateLimiter

CHUNK = 64 * 1024
MIN_SEGMENT = 1024 * 1024        # don't create segments smaller than 1 MB
MAX_RETRIES = 6
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class Cancelled(Exception):
    pass


class HttpDownloader:
    def __init__(self, item: DownloadItem, limiter: RateLimiter,
                 resolve_dir: Callable[[DownloadItem], str]):
        self.item = item
        self.limiter = limiter
        self.resolve_dir = resolve_dir
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._error: Exception | None = None
        headers = {"User-Agent": DEFAULT_UA, "Accept-Encoding": "identity"}
        headers.update({k: v for k, v in item.headers.items() if v})
        self.client = httpx.Client(
            follow_redirects=True, headers=headers,
            timeout=httpx.Timeout(30.0, read=60.0),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )

    # ------------------------------------------------------------------ API
    def stop(self) -> None:
        self._stop.set()

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
            self.client.close()

    # ---------------------------------------------------------------- probe
    def _probe(self) -> None:
        item = self.item
        with self.client.stream("GET", item.url, headers={"Range": "bytes=0-0"}) as r:
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
            n = max(1, min(item.connections, total // MIN_SEGMENT or 1))
            size = total // n
            item.segments = [Segment(i * size, (i + 1) * size - 1 if i < n - 1 else total - 1)
                             for i in range(n)]
            with open(item.part_path, "wb") as fh:
                fh.truncate(total)          # pre-allocate
        else:
            item.segments = []

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
        threads = [threading.Thread(target=self._worker, daemon=True)
                   for _ in range(max(1, item.connections))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _next_segment(self) -> Segment | None:
        with self._lock:
            for seg in self.item.segments:
                if not seg.active and seg.remaining > 0:
                    seg.active = True
                    return seg
            # Nothing free: steal half of the largest running segment.
            busy = [s for s in self.item.segments if s.active and s.remaining >= 2 * MIN_SEGMENT]
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
        try:
            with open(self.item.part_path, "r+b") as fh:
                while not self._stop.is_set():
                    seg = self._next_segment()
                    if seg is None:
                        return
                    try:
                        self._download_segment(seg, fh)
                    finally:
                        with self._lock:
                            seg.active = False
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                if self._error is None:
                    self._error = exc
            self._stop.set()

    def _download_segment(self, seg: Segment, fh) -> None:
        attempts = 0
        while not self._stop.is_set() and seg.remaining > 0:
            try:
                rng = {"Range": f"bytes={seg.pos}-{seg.end}"}
                with self.client.stream("GET", self.item.url, headers=rng) as r:
                    if r.status_code != 206:
                        raise RuntimeError(f"Server stopped honouring ranges (HTTP {r.status_code})")
                    for chunk in r.iter_bytes(CHUNK):
                        if self._stop.is_set():
                            return
                        with self._lock:
                            allowed = seg.remaining
                            if allowed <= 0:
                                break
                            if len(chunk) > allowed:
                                chunk = chunk[:allowed]
                            pos = seg.pos
                        fh.seek(pos)
                        fh.write(chunk)
                        with self._lock:
                            seg.done += len(chunk)
                            self.item.downloaded += len(chunk)
                        attempts = 0
                        self.limiter.consume(len(chunk), self._stop)
                        if seg.remaining <= 0:
                            break
            except (httpx.HTTPError, OSError, RuntimeError) as exc:
                attempts += 1
                if attempts > MAX_RETRIES:
                    raise exc
                self._stop.wait(min(2 ** attempts, 20))

    # --------------------------------------------------------- single mode
    def _run_single(self) -> None:
        item = self.item
        attempts = 0
        while not self._stop.is_set():
            try:
                item.downloaded = 0
                with self.client.stream("GET", item.url) as r, open(item.part_path, "wb") as fh:
                    r.raise_for_status()
                    for chunk in r.iter_bytes(CHUNK):
                        if self._stop.is_set():
                            return
                        fh.write(chunk)
                        item.downloaded += len(chunk)
                        self.limiter.consume(len(chunk), self._stop)
                if item.total < 0:
                    item.total = item.downloaded
                return
            except (httpx.HTTPError, OSError) as exc:
                attempts += 1
                if attempts > MAX_RETRIES:
                    raise exc
                self._stop.wait(min(2 ** attempts, 20))

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
        final = Path(item.path)
        if final.exists():              # something appeared there meanwhile
            final = unique_path(final)
        os.replace(part, final)
        item.filename = final.name
        item.segments = []
        item.downloaded = item.total = size
        item.status = Status.COMPLETED
        item.finished = time.time()


def quick_probe(url: str, headers: dict | None = None) -> dict:
    """Lightweight probe used by the Add dialog: name, size, resumable, content type."""
    hdrs = {"User-Agent": DEFAULT_UA, "Accept-Encoding": "identity"}
    hdrs.update({k: v for k, v in (headers or {}).items() if v})
    with httpx.Client(follow_redirects=True, headers=hdrs, timeout=15) as client:
        with client.stream("GET", url, headers={"Range": "bytes=0-0"}) as r:
            r.raise_for_status()
            total, resumable = -1, False
            cr = r.headers.get("content-range", "")
            if r.status_code == 206 and cr.rsplit("/", 1)[-1].isdigit():
                total, resumable = int(cr.rsplit("/", 1)[-1]), True
            elif (r.headers.get("content-length") or "").isdigit():
                total = int(r.headers["content-length"])
            return {"filename": filename_from_headers(str(r.url), r.headers), "total": total,
                    "resumable": resumable,
                    "content_type": r.headers.get("content-type", "").split(";")[0].strip()}


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        hints = {403: "access denied (link may need login or has expired)",
                 404: "file not found", 410: "link expired", 416: "invalid range",
                 429: "too many requests, try fewer connections"}
        return f"HTTP {code}: {hints.get(code, exc.response.reason_phrase)}"
    if isinstance(exc, httpx.ConnectError):
        return "Could not connect to server"
    if isinstance(exc, httpx.TimeoutException):
        return "Connection timed out"
    return str(exc) or exc.__class__.__name__
