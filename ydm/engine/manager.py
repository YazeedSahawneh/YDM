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

from ..config import Settings, app_data_dir, category_for
from .http_downloader import HttpDownloader
from .item import DownloadItem, Status
from .limiter import RateLimiter
from .video_downloader import VideoDownloader


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
        self._load()
        self._ticker = threading.Thread(target=self._tick_loop, daemon=True)
        self._ticker.start()

    # ------------------------------------------------------------- queries
    def items(self) -> list[DownloadItem]:
        with self._lock:
            return list(self._items.values())

    def get(self, item_id: str) -> DownloadItem | None:
        return self._items.get(item_id)

    def total_speed(self) -> float:
        return sum(i.speed for i in self.items() if i.status in Status.ACTIVE)

    def add_listener(self, fn: Callable[[str, DownloadItem], None]) -> None:
        self._listeners.append(fn)

    # ------------------------------------------------------------ commands
    def add(self, url: str, *, kind: str = "file", filename: str = "", save_dir: str = "",
            headers: dict | None = None, start: bool = True, video_format: str = "",
            connections: int | None = None, title: str = "") -> DownloadItem:
        item = DownloadItem(url=url.strip(), kind=kind, filename=filename, save_dir=save_dir,
                            headers=headers or {}, video_format=video_format, title=title,
                            connections=connections or self.settings.connections)
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
            cls = VideoDownloader if item.kind == "video" else HttpDownloader
            dl = cls(item, self.limiter, self.resolve_dir)
            t = threading.Thread(target=self._run, args=(item, dl), daemon=True)
            self._running[item_id] = (dl, t)
            self._speed_state[item_id] = (item.downloaded, time.monotonic())
        t.start()

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
        if item.part_path and os.path.exists(item.part_path):
            os.remove(item.part_path)
        item.segments, item.total, item.downloaded = [], -1, 0
        item.status = Status.PAUSED
        self.start(item_id)

    def remove(self, item_id: str, delete_file: bool = False) -> None:
        self.pause(item_id)
        self._wait_stopped(item_id)
        with self._lock:
            item = self._items.pop(item_id, None)
            self._dirty = True
        if item:
            for p in (item.part_path, item.path if delete_file else ""):
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass

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

    def resolve_dir(self, item: DownloadItem) -> str:
        base = Path(self.settings.download_dir)
        if self.settings.use_category_folders:
            base = base / (item.category or "Other")
        return str(base)

    def shutdown(self) -> None:
        self._closing = True
        for item_id, (dl, _t) in list(self._running.items()):
            dl.stop()
        for item_id in list(self._running):
            self._wait_stopped(item_id, timeout=5)
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
            self.save()
            if item.status in (Status.COMPLETED, Status.ERROR):
                for fn in self._listeners:
                    try:
                        fn(item.status.lower(), item)
                    except Exception:  # noqa: BLE001
                        pass

    def _tick_loop(self) -> None:
        last_save = time.monotonic()
        while not self._closing:
            time.sleep(0.5)
            self._update_speeds()
            self._scheduler_tick()
            self._fill_slots()
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
            if item.status in Status.ACTIVE:
                item.status = Status.PAUSED      # app closed mid-download
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
