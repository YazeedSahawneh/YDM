"""Tiny local HTTP endpoint the browser extension talks to (127.0.0.1 only).

GET  /ping  -> {"app": "YDM", "version": "..."}
POST /add   -> {"url": "...", "kind": "file"|"video", "filename", "referrer",
                "cookies", "userAgent", "fileSize"}
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .config import APP_NAME, APP_VERSION

ALLOWED_ORIGINS = ("chrome-extension://", "moz-extension://", "extension://")


class _Handler(BaseHTTPRequestHandler):
    on_add: Callable[[dict], None] = staticmethod(lambda payload: None)

    def log_message(self, *args):  # silence console
        pass

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        # No Origin = a local program (curl, scripts). Web pages always send one,
        # so only browser extensions are accepted from a browser.
        return origin is None or origin.startswith(ALLOWED_ORIGINS)

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        origin = self.headers.get("Origin")
        if origin and self._origin_ok():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204 if self._origin_ok() else 403, {})

    def do_GET(self):
        if not self._origin_ok():
            return self._send(403, {"error": "forbidden"})
        if self.path.startswith("/ping"):
            return self._send(200, {"app": APP_NAME, "version": APP_VERSION})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._origin_ok():
            return self._send(403, {"error": "forbidden"})
        if not self.path.startswith("/add"):
            return self._send(404, {"error": "not found"})
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 1_000_000)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            return self._send(400, {"error": "bad json"})
        url = str(payload.get("url", ""))
        if not url.lower().startswith(("http://", "https://")):
            return self._send(400, {"error": "only http/https urls"})
        type(self).on_add(payload)
        self._send(200, {"ok": True})


class LocalServer:
    def __init__(self, port: int, on_add: Callable[[dict], None]):
        handler = type("Handler", (_Handler,), {"on_add": staticmethod(on_add)})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)   # raises OSError if busy
        self.httpd.daemon_threads = True
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
