"""Download manager: owns all items, the queue, the scheduler and persistence.

It is GUI-agnostic (plain threads). The GUI polls `items()` and calls the
public methods; listeners get ("completed"|"error", item) callbacks.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import vault
from ..config import Settings, app_data_dir, category_for
from . import auth, net
from .ftp_downloader import FtpDownloader
from .http_downloader import HttpDownloader
from .item import DownloadItem, Status
from .limiter import RateLimiter
from .video_downloader import VideoDownloader

SCHEMES = ("http://", "https://", "ftp://", "ftps://")


def _same_name(ours: str, theirs: str) -> bool:
    """'setup (2).exe' (renamed to avoid a clash) is the same file as 'setup.exe'."""
    import re
    strip = lambda n: re.sub(r" \(\d+\)(?=\.[^.]*$|$)", "", n).lower()
    return strip(ours) == strip(theirs)


CRASH_ROLLBACK = 1024 * 1024     # after a crash, re-fetch the last 1 MB of every piece (may not have hit the disk)


class DownloadManager:
    def __init__(self, settings: Settings, store_path: Path | None = None):
        self.settings = settings
        self.store_path = store_path or (app_data_dir() / "downloads.json")
        self.limiter = RateLimiter(settings.speed_limit_kbps * 1024)
        self._items: dict[str, DownloadItem] = {}
        self._running: dict[str, tuple[object, threading.Thread]] = {}
        self._lock = threading.RLock()
        self._listeners: list[Callable[[str, DownloadItem], None]] = []
        self._speed_state: dict[str, tuple[int, float]] = {}
        self._dirty = False
        self._closing = False
        self.queue_running = not settings.scheduler_enabled
        self._sched_was_inside: bool | None = None
        self.renew_wait: str | None = None     # item waiting for a fresh link from the browser
        self._apply_proxy()
        self._load()
        self._ticker = threading.Thread(target=self._tick_loop, daemon=True)
        self._ticker.start()

    # ------------------------------------------------------------- queries
    def items(self) -> list[DownloadItem]:
        with self._lock:
            return list(self._items.values())

    def get(self, item_id: str) -> DownloadItem | None:
        return self._items.get(item_id)

    def visible(self) -> list[DownloadItem]:
        return [i for i in self.items() if not i.pending]

    def total_speed(self) -> float:
        return sum(i.speed for i in self.items() if i.status in Status.ACTIVE)

    def add_listener(self, fn: Callable[[str, DownloadItem], None]) -> None:
        self._listeners.append(fn)

    # ------------------------------------------------------------ commands
    def add(self, url: str, *, kind: str = "file", filename: str = "", save_dir: str = "",
            headers: dict | None = None, start: bool = True, video_format: str = "",
            connections: int | None = None, title: str = "", pending: bool = False,
            convert: str = "") -> DownloadItem:
        """pending=True starts it right away but keeps it out of the list until confirm()."""
        item = DownloadItem(url=url.strip(), kind=kind, filename=filename, save_dir=save_dir,
                            headers=headers or {}, video_format=video_format, title=title,
                            connections=connections or self.settings.connections,
                            pending=pending, convert=convert)
        if filename:
            item.category = "Video" if kind == "video" else category_for(filename)
        elif kind == "video":
            item.category = "Video"
        with self._lock:
            self._items[item.id] = item
            self._dirty = True
        if start:
            self.start(item.id)
        return item

    def start(self, item_id: str) -> None:
        with self._lock:
            item = self._items.get(item_id)
            if not item or item_id in self._running or item.status == Status.COMPLETED:
                return
            item.error = ""
            item.status = Status.CONNECTING
            if item.kind == "video":
                cls = VideoDownloader
            elif item.url.lower().startswith(("ftp://", "ftps://")):
                cls = FtpDownloader
            else:
                cls = HttpDownloader
            dl = cls(item, self.limiter, self.resolve_dir)
            t = threading.Thread(target=self._run, args=(item, dl), daemon=True)
            self._running[item_id] = (dl, t)
            self._speed_state[item_id] = (item.downloaded, time.monotonic())
        t.start()

    def confirm(self, item_id: str, *, filename: str = "", save_dir: str = "",
                connections: int | None = None, start: bool = True) -> None:
        """The user accepted a pre-started download. Name/folder apply when it finishes,
        so nothing already downloaded is thrown away."""
        item = self._items.get(item_id)
        if not item:
            return
        item.pending = False
        if filename and filename != item.filename:
            item.dest_name = filename
        if save_dir and save_dir != item.save_dir:
            item.dest_dir = save_dir
        self._dirty = True
        if item.status == Status.COMPLETED and (item.dest_name or item.dest_dir):
            self._move_finished(item)          # it finished while the window was still open
            return
        if connections and connections != item.connections:
            self.set_connections(item_id, connections)
        if not start:
            threading.Thread(target=self._park, args=(item_id,), daemon=True).start()
        elif item.status not in Status.ACTIVE and item.status != Status.COMPLETED:
            self.start(item_id)

    def rename(self, item_id: str, filename: str = "", save_dir: str = "") -> None:
        """Save as: a new name and/or folder. Finished files move now; running ones when they finish."""
        item = self._items.get(item_id)
        if not item:
            return
        if filename and filename != item.filename:
            item.dest_name = filename
        if save_dir and os.path.normcase(os.path.abspath(save_dir)) != os.path.normcase(os.path.abspath(item.save_dir or ".")):
            item.dest_dir = save_dir
        if item.status == Status.COMPLETED and (item.dest_name or item.dest_dir):
            self._move_finished(item)
        self._dirty = True

    def later(self, item_id: str) -> None:
        """Download later: stop now, keep it in the list (Resume or Start queue continues it)."""
        item = self._items.get(item_id)
        if item:
            item.pending = False
            self._dirty = True
            threading.Thread(target=self._park, args=(item_id,), daemon=True).start()

    def discard(self, item_id: str) -> None:
        """Cancel a pre-started download: stop it and delete what it fetched (non-blocking)."""
        threading.Thread(target=self.remove, args=(item_id,), kwargs={"delete_file": True}, daemon=True).start()

    @staticmethod
    def _move_finished(item: DownloadItem) -> None:
        import shutil
        from ..utils import unique_path
        target = Path(item.dest_dir or item.save_dir) / (item.dest_name or item.filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target = unique_path(target)
        try:
            shutil.move(item.path, target)
            item.save_dir, item.filename = str(target.parent), target.name
        except OSError:
            pass
        item.dest_dir = item.dest_name = ""

    def _park(self, item_id: str) -> None:
        self.pause(item_id)
        self._wait_stopped(item_id)
        item = self._items.get(item_id)
        if item and item.status not in (Status.COMPLETED, Status.ERROR):
            item.status = Status.PAUSED        # a queued item would start again at once
            self._dirty = True

    def set_connections(self, item_id: str, n: int) -> None:
        """Change a download's connection count; a running one adjusts on the fly."""
        item = self._items.get(item_id)
        if not item:
            return
        item.connections = max(1, min(64, int(n)))
        entry = self._running.get(item_id)
        if entry and hasattr(entry[0], "set_connections"):
            entry[0].set_connections(item.connections)
        self._dirty = True

    def set_login(self, item_id: str, user: str, password: str, remember: bool = False) -> None:
        """Login for one download (Basic / Digest / NTLM / FTP). Restarts it if it was waiting for one."""
        item = self._items.get(item_id)
        if not item:
            return
        item.auth_user, item.auth_pass = user, vault.protect(password)
        if remember and user:
            from urllib.parse import urlsplit
            auth.LOGINS.save(urlsplit(item.url).hostname or "", user, password)
        self._dirty = True
        if item.status == Status.ERROR:
            self.start(item_id)

    def queue(self, item_id: str) -> None:
        item = self._items.get(item_id)
        if item and item_id not in self._running and item.status != Status.COMPLETED:
            item.status = Status.QUEUED
            self._dirty = True

    def pause(self, item_id: str) -> None:
        with self._lock:
            entry = self._running.get(item_id)
            item = self._items.get(item_id)
        if entry:
            entry[0].stop()
        elif item and item.status == Status.QUEUED:
            item.status = Status.PAUSED
            self._dirty = True

    def restart(self, item_id: str) -> None:
        item = self._items.get(item_id)
        if not item:
            return
        self.pause(item_id)
        self._wait_stopped(item_id)
        for p in self._partial_files(item):
            try:
                os.remove(p)
            except OSError:
                pass
        item.segments, item.parts, item.total, item.downloaded = [], [], -1, 0
        item.status = Status.PAUSED
        self.start(item_id)

    def remove(self, item_id: str, delete_file: bool = False) -> None:
        self.pause(item_id)
        self._wait_stopped(item_id)
        with self._lock:
            item = self._items.pop(item_id, None)
            self._dirty = True
        if item:
            for p in self._partial_files(item) + ([item.path] if delete_file and item.path else []):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass

    def refresh_url(self, item_id: str, url: str, headers: dict | None = None, start: bool = True) -> None:
        """Swap in a fresh link (expired or changed) and keep the progress made so far.
        The engine checks the new link serves a file of the same size before writing anything."""
        item = self._items.get(item_id)
        if not item or not url.lower().startswith(SCHEMES):
            return
        if self.renew_wait == item_id:
            self.renew_wait = None
        was_running = item_id in self._running
        self.pause(item_id)
        self._wait_stopped(item_id)
        item.url = url.strip()
        if headers:
            item.headers = {**item.headers, **{k: v for k, v in headers.items() if v}}
        item.error = ""
        item.retries = 0
        self._dirty = True
        if start and (was_running or item.status in (Status.ERROR, Status.PAUSED, Status.QUEUED)):
            self.start(item_id)

    def find_renewal(self, filename: str, size: int) -> DownloadItem | None:
        """A link just arrived from the browser: is it the fresh address of a download whose
        link expired? The one the user is renewing wins; otherwise an unfinished file
        download with the same name and the same size."""
        if self.renew_wait:
            item = self._items.get(self.renew_wait)
            if item and item.status != Status.COMPLETED:
                return item
            self.renew_wait = None
        if size <= 0:
            return None
        same_size = [i for i in self.visible()
                     if i.kind == "file" and i.status in (Status.ERROR, Status.PAUSED)
                     and i.total == size and i.downloaded > 0]
        if filename:
            named = [i for i in same_size if _same_name(i.dest_name or i.filename, filename)]
            # an expired one first, then the most recent
            return max(named, key=lambda i: (i.expired, i.added), default=None)
        # the browser often can't tell the name yet; the exact byte count of one expired download is enough
        expired = [i for i in same_size if i.expired]
        return expired[0] if len(expired) == 1 else None

    def mark_seen(self, item_id: str) -> None:
        item = self._items.get(item_id)
        if item and not item.seen:
            item.seen = True
            self._dirty = True

    @staticmethod
    def _partial_files(item: DownloadItem) -> list[str]:
        files = [item.part_path] if item.part_path else []
        for part in item.parts:              # video streams downloaded separately
            if item.save_dir and part.get("filename"):
                files.append(os.path.join(item.save_dir, part["filename"]) + ".ydm")
        return files

    def start_queue(self) -> None:
        self.queue_running = True

    def stop_queue(self) -> None:
        self.queue_running = False

    def pause_all(self) -> None:
        for item in self.items():
            self.pause(item.id)

    def resume_all(self) -> None:
        for item in self.items():
            if item.status in (Status.PAUSED, Status.ERROR):
                self.queue(item.id)
        self.queue_running = True

    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        self.limiter.set_rate(settings.speed_limit_kbps * 1024)
        self._sched_was_inside = None
        self._apply_proxy()

    def _apply_proxy(self) -> None:
        s = self.settings
        net.configure_proxy(s.proxy_mode, s.proxy_host, s.proxy_port, s.proxy_user, vault.reveal(s.proxy_pass))

    def resolve_dir(self, item: DownloadItem) -> str:
        if self.settings.remember_dir:
            return self.settings.remember_dir
        base = Path(self.settings.download_dir)
        if self.settings.use_category_folders:
            base = base / (item.category or "Other")
        return str(base)

    def shutdown(self, resume_later: bool = False) -> None:
        """Stop everything and save. resume_later=True (app update) queues the
        downloads that were running so they continue after the restart."""
        self._closing = True
        running = list(self._running)
        for item_id, (dl, _t) in list(self._running.items()):
            dl.stop()
        for item_id in running:
            self._wait_stopped(item_id, timeout=5)
        if resume_later:
            for item_id in running:
                item = self._items.get(item_id)
                if item and item.status in (Status.PAUSED, *Status.ACTIVE):
                    item.status = Status.QUEUED
        self.save()

    # ------------------------------------------------------------ internals
    def _wait_stopped(self, item_id: str, timeout: float = 10) -> None:
        entry = self._running.get(item_id)
        if entry:
            entry[1].join(timeout)

    def _run(self, item: DownloadItem, dl) -> None:
        try:
            dl.run()
        finally:
            with self._lock:
                self._running.pop(item.id, None)
                self._dirty = True
        if item.status == Status.COMPLETED:
            item.seen = False
            item.retries = 0
            if item.dest_name or item.dest_dir:       # "Save as" while a video was still downloading
                self._move_finished(item)
            if item.pending:
                self.save()
                return                        # finished before the user confirmed; the window reports it
        elif item.status == Status.ERROR and not self._closing and self._worth_retrying(item):
            item.retries += 1                 # network hiccup: try again in 10 s, 20 s, 30 s
            timer = threading.Timer(10 * item.retries, self._retry, args=(item.id,))
            timer.daemon = True
            timer.start()
            self.save()
            return
        self.save()
        if item.status in (Status.COMPLETED, Status.ERROR):
            self._emit(item.status.lower(), item)

    _TRANSIENT = ("Could not connect", "timed out", "Connection", "Server is busy", "HTTP 5", "HTTP 429")

    def _worth_retrying(self, item: DownloadItem) -> bool:
        if item.kind == "video" and item.expired:
            return item.retries < 2            # a video re-reads its page, which gives fresh links
        return item.retries < 3 and any(t in item.error for t in self._TRANSIENT)

    def _retry(self, item_id: str) -> None:
        item = self._items.get(item_id)
        if item and item.status == Status.ERROR and not self._closing:
            item.status = Status.QUEUED

    def _emit(self, event: str, item: DownloadItem | None) -> None:
        for fn in self._listeners:
            try:
                fn(event, item)
            except Exception:  # noqa: BLE001
                pass

    def _tick_loop(self) -> None:
        last_save = time.monotonic()
        was_busy = False
        while not self._closing:
            time.sleep(0.5)
            self._update_speeds()
            self._scheduler_tick()
            self._fill_slots()
            busy = bool(self._running) or any(i.status == Status.QUEUED for i in self.items())
            if was_busy and not busy:
                self._emit("all_done", None)      # drives "when finished: sleep / shut down"
            was_busy = busy
            now = time.monotonic()
            if self._running and now - last_save > 3 or self._dirty:
                self.save()
                last_save = now

    def _update_speeds(self) -> None:
        now = time.monotonic()
        for item_id in list(self._running):
            item = self._items.get(item_id)
            if not item:
                continue
            prev_bytes, prev_t = self._speed_state.get(item_id, (item.downloaded, now))
            dt = now - prev_t
            if dt <= 0:
                continue
            inst = max(0.0, (item.downloaded - prev_bytes) / dt)
            item.speed = inst if item.speed == 0 else item.speed * 0.7 + inst * 0.3
            self._speed_state[item_id] = (item.downloaded, now)
            if item.total > 0 and item.speed > 1:
                item.eta = (item.total - item.downloaded) / item.speed
            else:
                item.eta = -1

    def _fill_slots(self) -> None:
        if not self.queue_running:
            return
        with self._lock:
            free = self.settings.max_active - len(self._running)
            if free <= 0:
                return
            queued = sorted((i for i in self._items.values()
                             if i.status == Status.QUEUED and i.id not in self._running),
                            key=lambda i: i.added)
        for item in queued[:free]:
            self.start(item.id)

    def _scheduler_tick(self) -> None:
        s = self.settings
        if not s.scheduler_enabled:
            return
        inside = self._in_window(datetime.now())
        if inside == self._sched_was_inside:
            return
        self._sched_was_inside = inside
        if inside:
            for item in self.items():           # wake paused/unfinished items
                if item.status in (Status.PAUSED, Status.ERROR):
                    item.status = Status.QUEUED
            self.queue_running = True
        else:
            self.queue_running = False
            for item_id in list(self._running):
                item = self._items.get(item_id)
                self.pause(item_id)
                if item:
                    threading.Thread(target=self._requeue_after_stop, args=(item,), daemon=True).start()

    def _requeue_after_stop(self, item: DownloadItem) -> None:
        self._wait_stopped(item.id)
        if item.status == Status.PAUSED:
            item.status = Status.QUEUED     # continues in the next window

    def _in_window(self, now: datetime) -> bool:
        s = self.settings
        try:
            start = datetime.strptime(s.scheduler_start, "%H:%M").time()
            stop = datetime.strptime(s.scheduler_stop, "%H:%M").time()
        except ValueError:
            return True
        t = now.time()
        if start <= stop:
            return now.weekday() in s.scheduler_days and start <= t < stop
        # window crosses midnight (e.g. 23:00 -> 07:00): belongs to the start day
        if t >= start:
            return now.weekday() in s.scheduler_days
        return t < stop and (now.weekday() - 1) % 7 in s.scheduler_days

    # ---------------------------------------------------------- persistence
    def _load(self) -> None:
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for d in data.get("items", []):
            try:
                item = DownloadItem.from_dict(d)
            except TypeError:
                continue
            if item.pending:                     # closed before the user confirmed it
                for p in self._partial_files(item):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                continue
            if item.status in Status.ACTIVE:
                # still "downloading" on disk means YDM didn't shut down cleanly (crash, power cut):
                # re-fetch the last bit of each piece, which may never have reached the disk, and carry on
                for seg in item.segments:
                    seg.done = max(0, seg.done - CRASH_ROLLBACK)
                for part in item.parts:
                    for seg in part.get("segments", []):
                        seg["done"] = max(0, seg.get("done", 0) - CRASH_ROLLBACK)
                item.downloaded = sum(seg.done for seg in item.segments) if item.segments else item.downloaded
                item.status = Status.QUEUED
            self._items[item.id] = item

    def save(self) -> None:
        with self._lock:
            payload = {"version": 1, "items": [i.to_dict() for i in self._items.values()]}
            # serialise segments under the item lock-free snapshot; small race is harmless
            self._dirty = False
        tmp = self.store_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self.store_path)
        except OSError:
            pass
