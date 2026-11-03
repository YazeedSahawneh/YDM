from __future__ import annotations

import threading
import time


class RateLimiter:
    """Thread-safe token bucket shared by every connection. rate=0 means unlimited."""

    def __init__(self, rate: int = 0):
        self._rate = max(0, int(rate))
        self._lock = threading.Lock()
        self._allowance = 0.0
        self._last = time.monotonic()

    @property
    def rate(self) -> int:
        return self._rate

    def set_rate(self, rate: int) -> None:
        with self._lock:
            self._rate = max(0, int(rate))
            self._allowance = 0.0
            self._last = time.monotonic()

    def consume(self, n: int, stop_event: threading.Event | None = None) -> None:
        if self._rate <= 0:
            return
        with self._lock:
            now = time.monotonic()
            self._allowance = min(self._rate, self._allowance + (now - self._last) * self._rate)
            self._last = now
            self._allowance -= n
            wait = -self._allowance / self._rate if self._allowance < 0 else 0.0
        if wait > 0:
            if stop_event is not None:
                stop_event.wait(wait)
            else:
                time.sleep(wait)
