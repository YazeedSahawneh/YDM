"""YDM — a personal multi-connection download manager.

Run:  python main.py            (or  python main.py <url>  to add a link)
"""
from __future__ import annotations

import json
import sys
import urllib.request

from PySide6.QtWidgets import QApplication, QMessageBox

from ydm.config import APP_NAME, Settings
from ydm.engine import DownloadManager
from ydm.gui import theme
from ydm.gui.main_window import MainWindow
from ydm.server import LocalServer


def send_to_running_instance(port: int, url: str | None) -> bool:
    """If YDM is already running, hand it the URL (if any) and return True."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=1) as r:
            if json.load(r).get("app") != APP_NAME:
                return False
        if url:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/add", method="POST",
                                         data=json.dumps({"url": url}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=2).close()
        return True
    except (OSError, ValueError):
        return False


def main() -> int:
    url_arg = next((a for a in sys.argv[1:] if a.lower().startswith(("http://", "https://"))), None)
    settings = Settings.load()

    if send_to_running_instance(settings.port, url_arg):
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)
    app.setWindowIcon(theme.app_icon())
    app.setQuitOnLastWindowClosed(False)       # keep running in the tray

    manager = DownloadManager(settings)
    window = MainWindow(manager, settings)

    server = None
    try:
        server = LocalServer(settings.port, lambda payload: window.browser_request.emit(payload))
        server.start()
    except OSError:
        QMessageBox.warning(window, APP_NAME,
                            f"Port {settings.port} is busy, so the browser extension can't connect.\n"
                            "Change the port in Settings → Browser.")

    if "--minimized" not in sys.argv:
        window.show()
    if url_arg:
        window.add_url(url_arg)

    code = app.exec()
    if server:
        server.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
