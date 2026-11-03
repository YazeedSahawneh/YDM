from __future__ import annotations

import os

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout, QHeaderView, QLabel,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QSizePolicy,
                               QSystemTrayIcon, QTableView, QToolBar, QToolButton, QWidget)

from ..config import APP_NAME, APP_TITLE, CATEGORY_NAMES, Settings, is_downloadable_url
from ..engine import DownloadManager, Status
from ..utils import human_speed, open_path, reveal_in_folder
from . import theme
from .dialogs import AddDownloadDialog, SettingsDialog
from .models import COL_PROGRESS, FILTERS, DownloadsModel, ProgressDelegate


class MainWindow(QMainWindow):
    browser_request = Signal(dict)       # emitted from the local server thread
    item_event = Signal(str, str)        # (event, item_id) from engine threads

    def __init__(self, manager: DownloadManager, settings: Settings):
        super().__init__()
        self.manager = manager
        self.settings = settings
        self._quitting = False
        self._last_clip = ""
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(theme.app_icon())
        self.resize(1180, 680)

        self._build_toolbar()
        self._build_body()
        self._build_tray()

        self.browser_request.connect(self.handle_browser_request)
        self.item_event.connect(self._on_item_event)
        manager.add_listener(lambda ev, item: self.item_event.emit(ev, item.id))
        QGuiApplication.clipboard().dataChanged.connect(self._on_clipboard)

        self.timer = QTimer(self, interval=500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)

        def act(text, slot, shortcut=None, primary=False):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            tb.addAction(a)
            if primary:
                tb.widgetForAction(a).setObjectName("primary")
            return a

        act("＋  Add URL", self.add_url, "Ctrl+N", primary=True)
        act("▶  Resume", lambda: self._for_selected(self.manager.start), "Ctrl+R")
        act("⏸  Pause", lambda: self._for_selected(self.manager.pause), "Ctrl+P")
        act("⏹  Pause all", self.manager.pause_all)
        act("✕  Delete", self.delete_selected, "Del")
        tb.addSeparator()
        self.queue_action = act("⏵  Start queue", self.toggle_queue)
        act("⏰  Scheduler", lambda: self.open_settings(2))
        act("⚙  Settings", lambda: self.open_settings(0))

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.speed_label = QLabel("")
        self.speed_label.setObjectName("title")
        tb.addWidget(self.speed_label)

    def _build_body(self):
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(200)
        self._sidebar_names = list(FILTERS) + CATEGORY_NAMES
        for name in self._sidebar_names:
            QListWidgetItem(name, self.sidebar)
        self.sidebar.insertItem(len(FILTERS), QListWidgetItem(""))   # spacer row
        self.sidebar.item(len(FILTERS)).setFlags(Qt.NoItemFlags)
        self.sidebar.setCurrentRow(0)
        self.sidebar.currentRowChanged.connect(self._filter_changed)

        self.model = DownloadsModel(self.manager)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(COL_PROGRESS, ProgressDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(self._open_item)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for col, w in ((1, 100), (2, 200), (3, 110), (4, 90), (5, 150), (6, 110)):
            h.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table.setColumnWidth(col, w)
        h.setHighlightSections(False)

        self.empty = QLabel("No downloads yet.\nPress ＋ Add URL, paste a link, or download from your browser.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("muted")

        body = QWidget()
        lay = QHBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.sidebar)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.empty, 1)
        self.setCentralWidget(body)
        self.status_text = QLabel("")
        self.statusBar().addWidget(self.status_text, 1)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(theme.app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction("Show", self.show_normal)
        menu.addAction("Add URL…", self.add_url)
        menu.addAction("Pause all", self.manager.pause_all)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_normal()
                                    if reason == QSystemTrayIcon.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    # ------------------------------------------------------------- refresh
    def refresh(self):
        selected = self._selected_ids()
        if self.model.refresh():
            sm = self.table.selectionModel()
            for row, item_id in enumerate(self.model.ids):
                if item_id in selected:
                    sm.select(self.model.index(row, 0),
                              QItemSelectionModel.Select | QItemSelectionModel.Rows)
        empty = not self.model.ids
        self.table.setVisible(not empty)
        self.empty.setVisible(empty)

        items = self.manager.items()
        active = sum(1 for i in items if i.status in Status.ACTIVE)
        queued = sum(1 for i in items if i.status == Status.QUEUED)
        speed = self.manager.total_speed()
        self.speed_label.setText(f"↓ {human_speed(speed)}" if speed > 0 else "")
        sched = ""
        if self.settings.scheduler_enabled:
            sched = (f"  ·  Scheduler {self.settings.scheduler_start}–{self.settings.scheduler_stop}"
                     f" ({'running' if self.manager.queue_running else 'waiting'})")
        limit = f"  ·  Limit {self.settings.speed_limit_kbps} KB/s" if self.settings.speed_limit_kbps else ""
        self.status_text.setText(f"{active} downloading  ·  {queued} queued  ·  {len(items)} total{limit}{sched}")
        self.queue_action.setText("⏸  Stop queue" if self.manager.queue_running else "⏵  Start queue")
        self.tray.setToolTip(f"{APP_NAME} — {active} active  {human_speed(speed)}")

        counts = {name: 0 for name in self._sidebar_names}
        for i in items:
            for name, f in FILTERS.items():
                if f(i):
                    counts[name] += 1
            counts[i.category] = counts.get(i.category, 0) + 1
        for row in range(self.sidebar.count()):
            it = self.sidebar.item(row)
            name = it.data(Qt.UserRole) or it.text().split("  ")[0]
            if not name:
                continue
            it.setData(Qt.UserRole, name)
            n = counts.get(name, 0)
            it.setText(f"{name}  ({n})" if n else name)

    def _filter_changed(self, row: int):
        it = self.sidebar.item(row)
        name = it.data(Qt.UserRole) or it.text().split("  ")[0] if it else "All"
        if not name:
            return
        self.model.filter_name = name
        self.refresh()

    # ------------------------------------------------------------- actions
    def _selected_ids(self) -> set[str]:
        rows = {i.row() for i in self.table.selectionModel().selectedRows()} if self.table.selectionModel() else set()
        return {self.model.ids[r] for r in rows if r < len(self.model.ids)}

    def _for_selected(self, fn):
        for item_id in self._selected_ids():
            fn(item_id)
        self.refresh()

    def add_url(self, url: str = "", payload: dict | None = None, kind: str | None = None):
        if not url:
            clip = QGuiApplication.clipboard().text().strip()
            if clip.lower().startswith(("http://", "https://")) and " " not in clip:
                url = clip
        dlg = AddDownloadDialog(self.settings, url, payload, kind, self)
        dlg.setWindowIcon(self.windowIcon())
        if dlg.exec():
            d = dlg.result_data()
            item = self.manager.add(d["url"], kind=d["kind"], filename=d["filename"],
                                    save_dir=d["save_dir"], headers=d["headers"], start=False,
                                    video_format=d["video_format"], connections=d["connections"],
                                    title=d["title"])
            if d["start"]:
                self.manager.start(item.id)
            else:
                self.manager.queue(item.id)
            self.refresh()

    def handle_browser_request(self, payload: dict):
        kind = "video" if payload.get("kind") == "video" else "file"
        if self.settings.show_add_dialog or kind == "video":
            self.show_normal()
            self.add_url(payload.get("url", ""), payload, kind)
        else:
            headers = {"Referer": payload.get("referrer") or "", "User-Agent": payload.get("userAgent") or "",
                       "Cookie": payload.get("cookies") or ""}
            self.manager.add(payload["url"], filename=payload.get("filename") or "",
                             headers={k: v for k, v in headers.items() if v})
            if self.tray.isVisible():
                self.tray.showMessage(APP_NAME, "Download started", QSystemTrayIcon.Information, 2500)

    def delete_selected(self, with_file: bool = False):
        ids = self._selected_ids()
        if not ids:
            return
        text = (f"Delete {len(ids)} download(s) and their files from disk?" if with_file
                else f"Remove {len(ids)} download(s) from the list?\nFinished files stay on disk.")
        if QMessageBox.question(self, APP_NAME, text) == QMessageBox.Yes:
            for item_id in ids:
                self.manager.remove(item_id, delete_file=with_file)
            self.refresh()

    def toggle_queue(self):
        if self.manager.queue_running:
            self.manager.stop_queue()
        else:
            self.manager.resume_all()
        self.refresh()

    def open_settings(self, tab: int = 0):
        old_port = self.settings.port
        dlg = SettingsDialog(self.settings, self, tab)
        if dlg.exec():
            s = dlg.updated_settings()
            s.save()
            self.manager.apply_settings(s)
            if s.port != old_port:
                QMessageBox.information(self, APP_NAME, "Restart YDM to use the new extension port.")
            self.refresh()

    def _open_item(self, index):
        item = self.model.item_at(index.row())
        if item and item.status == Status.COMPLETED and os.path.exists(item.path):
            open_path(item.path)
        elif item:
            self.manager.start(item.id)

    def _context_menu(self, pos):
        index = self.table.indexAt(pos)
        item = self.model.item_at(index.row()) if index.isValid() else None
        if not item:
            return
        m = QMenu(self)
        if item.status == Status.COMPLETED:
            m.addAction("Open", lambda: open_path(item.path))
            m.addAction("Show in folder", lambda: reveal_in_folder(item.path))
        else:
            m.addAction("Resume", lambda: self._for_selected(self.manager.start))
            m.addAction("Pause", lambda: self._for_selected(self.manager.pause))
            m.addAction("Add to queue", lambda: self._for_selected(self.manager.queue))
            if item.save_dir:
                m.addAction("Open folder", lambda: open_path(item.save_dir) if os.path.isdir(item.save_dir) else None)
        m.addAction("Restart from zero", lambda: self._for_selected(self.manager.restart))
        m.addSeparator()
        m.addAction("Copy address", lambda: QGuiApplication.clipboard().setText(item.url))
        m.addSeparator()
        m.addAction("Remove from list", lambda: self.delete_selected(False))
        m.addAction("Delete with file", lambda: self.delete_selected(True))
        m.exec(self.table.viewport().mapToGlobal(pos))

    # -------------------------------------------------------------- events
    def _on_item_event(self, event: str, item_id: str):
        item = self.manager.get(item_id)
        if not item or not self.settings.notify_on_complete or not self.tray.isVisible():
            return
        if event == "completed":
            self.tray.showMessage("Download complete", item.filename, QSystemTrayIcon.Information, 4000)
        elif event == "error":
            self.tray.showMessage("Download failed", f"{item.filename or item.url}\n{item.error}",
                                  QSystemTrayIcon.Warning, 5000)

    def _on_clipboard(self):
        if not self.settings.watch_clipboard:
            return
        text = QGuiApplication.clipboard().text().strip()
        if text == self._last_clip or not text.lower().startswith(("http://", "https://")) or " " in text:
            return
        self._last_clip = text
        if is_downloadable_url(text):
            self.show_normal()
            self.add_url(text)

    def show_normal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if not self._quitting and self.settings.minimize_to_tray and self.tray.isVisible():
            event.ignore()
            self.hide()
            if not getattr(self, "_tray_hint_shown", False):
                self._tray_hint_shown = True
                self.tray.showMessage(APP_NAME, "Still running in the tray. Right-click the icon to quit.",
                                      QSystemTrayIcon.Information, 3000)
            return
        self.quit()
        event.accept()

    def quit(self):
        self._quitting = True
        self.timer.stop()
        self.tray.hide()
        self.manager.shutdown()
        QApplication.quit()
