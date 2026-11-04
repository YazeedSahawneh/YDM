"""Remembers, per server, how many connections it accepts (it refused or
throttled more) and how many gave the best speed (found by the auto tuner),
so the next download from it starts right there."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

EXPIRY = 7 * 24 * 3600          # servers change; forget a limit after a week


class HostMemory:
    def __init__(self, path: Path | None = None):
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] | None = None

    def _file(self) -> Path:
        if self._path is None:
            from ..config import app_data_dir
            self._path = app_data_dir() / "hosts.json"
        return self._path

    def _load(self) -> dict[str, dict]:
        if self._data is None:
            try:
                self._data = json.loads(self._file().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._data = {}
        return self._data

    def limit(self, host: str) -> int | None:
        with self._lock:
            entry = self._load().get(host)
        if not entry or "limit" not in entry or time.time() - entry.get("t", 0) > EXPIRY:
            return None
        return int(entry["limit"])

    def best(self, host: str) -> int | None:
        """The connection count that was fastest here last time (auto mode)."""
        with self._lock:
            entry = self._load().get(host)
        if not entry or "best" not in entry or time.time() - entry.get("tb", 0) > EXPIRY:
            return None
        return int(entry["best"])

    def note_limit(self, host: str, working: int) -> None:
        """A whole download saw the server take `working` parallel connections and refuse more."""
        self._store(host, limit=max(1, working), t=time.time())

    def note_best(self, host: str, n: int) -> None:
        self._store(host, best=max(1, n), tb=time.time())

    def _store(self, host: str, **fields) -> None:
        if not host:
            return
        with self._lock:
            data = self._load()
            data[host] = {**data.get(host, {}), **fields}
            try:
                tmp = self._file().with_suffix(".tmp")
                tmp.write_text(json.dumps(data), encoding="utf-8")
                os.replace(tmp, self._file())
            except OSError:
                pass


HOSTS = HostMemory()
