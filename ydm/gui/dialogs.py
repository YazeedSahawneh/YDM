from __future__ import annotations

from urllib.parse import urlparse

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTime, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget,
                               QTimeEdit, QVBoxLayout, QWidget)

from ..config import Settings
from ..engine.http_downloader import quick_probe
from ..engine.video_downloader import fetch_video_info, has_ffmpeg
from ..utils import human_size

VIDEO_SITES = ("youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "instagram.com", "facebook.com",
               "fb.watch", "x.com", "twitter.com", "dailymotion.com", "twitch.tv", "reddit.com",
               "soundcloud.com", "bilibili.com")


def looks_like_video_page(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == s or host.endswith("." + s) for s in VIDEO_SITES)


# ---------------------------------------------------------------- background work
class _Signals(QObject):
    done = Signal(int, object)
    failed = Signal(int, str)


class _Job(QRunnable):
    def __init__(self, ticket: int, fn, *args):
        super().__init__()
        self.ticket, self.fn, self.args = ticket, fn, args
        self.signals = _Signals()

    def run(self):
        try:
            result, error = self.fn(*self.args), None
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).replace("ERROR: ", "")
            result, error = None, (msg.splitlines()[0][:200] if msg else exc.__class__.__name__)
        try:
            if error is None:
                self.signals.done.emit(self.ticket, result)
            else:
                self.signals.failed.emit(self.ticket, error)
        except RuntimeError:
            pass                        # dialog already closed


# --------------------------------------------------------------------- add dialog
class AddDownloadDialog(QDialog):
    def __init__(self, settings: Settings, url: str = "", payload: dict | None = None,
                 kind: str | None = None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.payload = payload or {}
        self.headers = {
            "Referer": self.payload.get("referrer") or "",
            "User-Agent": self.payload.get("userAgent") or "",
            "Cookie": self.payload.get("cookies") or "",
        }
        self.start_now = True
        self._ticket = 0
        self._jobs: list[_Job] = []
        self._video_info: dict | None = None
        self.setWindowTitle("New download")
        self.setMinimumWidth(560)

        self.url_edit = QLineEdit(url)
        self.url_edit.setPlaceholderText("https://…")
        self.video_box = QCheckBox("Video page — grab the video (YouTube and 1,800+ sites)")
        self.name_edit = QLineEdit(self.payload.get("filename") or "")
        self.name_edit.setPlaceholderText("Automatic")
        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText(f"Automatic ({settings.download_dir}"
                                         f"{' / category' if settings.use_category_folders else ''})")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        self.conn_spin = QSpinBox()
        self.conn_spin.setRange(1, 32)
        self.conn_spin.setValue(settings.connections)
        self.quality = QComboBox()
        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Address", self.url_edit)
        form.addRow("", self.video_box)
        form.addRow("Save as", self.name_edit)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.dir_edit, 1)
        folder_row.addWidget(browse)
        form.addRow("Folder", folder_row)
        form.addRow("Connections", self.conn_spin)
        self.quality_label = QLabel("Quality")
        form.addRow(self.quality_label, self.quality)

        self.btn_queue = QPushButton("Add to queue")
        self.btn_now = QPushButton("Download now")
        self.btn_now.setObjectName("primary")
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.btn_queue.clicked.connect(lambda: self._finish(False))
        self.btn_now.clicked.connect(lambda: self._finish(True))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.btn_queue)
        buttons.addWidget(self.btn_now)

        title = QLabel("New download")
        title.setObjectName("title")
        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addLayout(form)
        lay.addWidget(self.info)
        lay.addLayout(buttons)

        self._debounce = QTimer(self, singleShot=True, interval=600)
        self._debounce.timeout.connect(self._probe)
        self.url_edit.textChanged.connect(lambda _: self._debounce.start())
        self.video_box.toggled.connect(self._mode_changed)

        is_video = kind == "video" or (kind is None and looks_like_video_page(url))
        self.video_box.setChecked(is_video)
        self._mode_changed(is_video)

    # ---------------------------------------------------------------- helpers
    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save to", self.dir_edit.text() or self.settings.download_dir)
        if d:
            self.dir_edit.setText(d)

    def _mode_changed(self, video: bool):
        self.quality.setVisible(video)
        self.quality_label.setVisible(video)
        self.name_edit.setEnabled(not video)
        if video:
            self.name_edit.setPlaceholderText("Uses the video title")
        self._probe()

    def _run(self, fn, *args):
        self._ticket += 1
        job = _Job(self._ticket, fn, *args)
        job.signals.done.connect(self._on_done)
        job.signals.failed.connect(self._on_failed)
        self._jobs.append(job)       # keep signal objects alive until the dialog closes
        QThreadPool.globalInstance().start(job)

    def _probe(self):
        url = self.url_edit.text().strip()
        self._video_info = None
        if not url.lower().startswith(("http://", "https://")):
            self.info.setText("")
            return
        if self.video_box.isChecked():
            self.info.setText("Reading video details…")
            self.quality.clear()
            self._run(fetch_video_info, url, self.headers)
        else:
            self.info.setText("Checking link…")
            self._run(quick_probe, url, self.headers)

    def _on_done(self, ticket: int, result: dict):
        if ticket != self._ticket:
            return
        if "options" in result:
            self._video_info = result
            self.quality.clear()
            for label, spec in result["options"]:
                self.quality.addItem(label, spec)
            note = "" if has_ffmpeg() else "  ·  install FFmpeg for 1080p+ and best quality"
            self.info.setText(f"🎬 {result['title']}{note}")
            return
        if not self.name_edit.text():
            self.name_edit.setPlaceholderText(result["filename"])
        parts = [human_size(result["total"]) if result["total"] > 0 else "Unknown size",
                 "resume supported ✓" if result["resumable"] else "no resume (single connection)"]
        if result["content_type"].startswith("text/html"):
            parts.append("this is a web page — tick ‘Video page’ to grab its video")
        self.info.setText("  ·  ".join(parts))

    def _on_failed(self, ticket: int, message: str):
        if ticket == self._ticket:
            self.info.setText(f"⚠ {message}")

    def _finish(self, start: bool):
        if not self.url_edit.text().strip().lower().startswith(("http://", "https://")):
            self.info.setText("⚠ Enter an http(s) address")
            return
        self.start_now = start
        self.accept()

    # ----------------------------------------------------------------- result
    def result_data(self) -> dict:
        video = self.video_box.isChecked()
        return {
            "url": self.url_edit.text().strip(),
            "kind": "video" if video else "file",
            "filename": "" if video else self.name_edit.text().strip(),
            "save_dir": self.dir_edit.text().strip(),
            "connections": self.conn_spin.value(),
            "video_format": (self.quality.currentData() or "") if video else "",
            "title": (self._video_info or {}).get("title", ""),
            "headers": {k: v for k, v in self.headers.items() if v},
            "start": self.start_now,
        }


# ---------------------------------------------------------------- settings dialog
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None, open_tab: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        self.s = settings
        tabs = QTabWidget()

        # General
        g = QWidget()
        gf = QFormLayout(g)
        self.dir_edit = QLineEdit(settings.download_dir)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.dir_edit, 1)
        row.addWidget(browse)
        gf.addRow("Download folder", row)
        self.cat_box = QCheckBox("Sort into category folders (Video, Music, Programs…)")
        self.cat_box.setChecked(settings.use_category_folders)
        gf.addRow("", self.cat_box)
        self.tray_box = QCheckBox("Closing the window keeps YDM in the tray")
        self.tray_box.setChecked(settings.minimize_to_tray)
        gf.addRow("", self.tray_box)
        self.notify_box = QCheckBox("Notify when a download finishes")
        self.notify_box.setChecked(settings.notify_on_complete)
        gf.addRow("", self.notify_box)
        self.clip_box = QCheckBox("Watch clipboard for download links")
        self.clip_box.setChecked(settings.watch_clipboard)
        gf.addRow("", self.clip_box)
        tabs.addTab(g, "General")

        # Connection
        c = QWidget()
        cf = QFormLayout(c)
        self.conn = QSpinBox()
        self.conn.setRange(1, 32)
        self.conn.setValue(settings.connections)
        cf.addRow("Connections per download", self.conn)
        self.active = QSpinBox()
        self.active.setRange(1, 10)
        self.active.setValue(settings.max_active)
        cf.addRow("Simultaneous downloads", self.active)
        self.limit = QSpinBox()
        self.limit.setRange(0, 1_000_000)
        self.limit.setSuffix(" KB/s")
        self.limit.setSpecialValueText("Unlimited")
        self.limit.setValue(settings.speed_limit_kbps)
        cf.addRow("Speed limit", self.limit)
        tabs.addTab(c, "Connection")

        # Scheduler
        sc = QWidget()
        sl = QVBoxLayout(sc)
        self.sched_box = QCheckBox("Only download queued files inside this time window")
        self.sched_box.setChecked(settings.scheduler_enabled)
        sl.addWidget(self.sched_box)
        grid = QGridLayout()
        self.start_time = QTimeEdit(QTime.fromString(settings.scheduler_start, "HH:mm"))
        self.stop_time = QTimeEdit(QTime.fromString(settings.scheduler_stop, "HH:mm"))
        for w in (self.start_time, self.stop_time):
            w.setDisplayFormat("HH:mm")
        grid.addWidget(QLabel("Start at"), 0, 0)
        grid.addWidget(self.start_time, 0, 1)
        grid.addWidget(QLabel("Stop at"), 1, 0)
        grid.addWidget(self.stop_time, 1, 1)
        sl.addLayout(grid)
        days_row = QHBoxLayout()
        self.day_boxes = []
        for i, name in enumerate(DAYS):
            b = QCheckBox(name)
            b.setChecked(i in settings.scheduler_days)
            self.day_boxes.append(b)
            days_row.addWidget(b)
        sl.addLayout(days_row)
        hint = QLabel("Unfinished and queued downloads start at the start time and pause at the stop "
                      "time. A window like 23:00 → 07:00 runs overnight.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        sl.addWidget(hint)
        sl.addStretch(1)
        tabs.addTab(sc, "Scheduler")

        # Browser
        b = QWidget()
        bf = QFormLayout(b)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(settings.port)
        bf.addRow("Extension port", self.port)
        self.confirm_box = QCheckBox("Show the download dialog for links sent from the browser")
        self.confirm_box.setChecked(settings.show_add_dialog)
        bf.addRow("", self.confirm_box)
        note = QLabel("Load the ‘extension’ folder in Chrome/Edge (Developer mode → Load unpacked). "
                      "Port changes need an app restart and the same port in the extension popup.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        bf.addRow(note)
        tabs.addTab(b, "Browser")
        tabs.setCurrentIndex(open_tab)

        save = QPushButton("Save")
        save.setObjectName("primary")
        save.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(save)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addLayout(btns)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Download folder", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def updated_settings(self) -> Settings:
        s = self.s
        s.download_dir = self.dir_edit.text().strip() or s.download_dir
        s.use_category_folders = self.cat_box.isChecked()
        s.minimize_to_tray = self.tray_box.isChecked()
        s.notify_on_complete = self.notify_box.isChecked()
        s.watch_clipboard = self.clip_box.isChecked()
        s.connections = self.conn.value()
        s.max_active = self.active.value()
        s.speed_limit_kbps = self.limit.value()
        s.scheduler_enabled = self.sched_box.isChecked()
        s.scheduler_start = self.start_time.time().toString("HH:mm")
        s.scheduler_stop = self.stop_time.time().toString("HH:mm")
        s.scheduler_days = [i for i, b in enumerate(self.day_boxes) if b.isChecked()]
        s.port = self.port.value()
        s.show_add_dialog = self.confirm_box.isChecked()
        return s
