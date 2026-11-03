from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser

from PySide6.QtCore import QItemSelectionModel, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit,
                               QMainWindow,
                               QMenu, QMessageBox, QSizePolicy, QSplitter, QSystemTrayIcon, QTableView, QToolBar,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import tools, updater
from ..config import APP_NAME, APP_TITLE, APP_VERSION, CATEGORY_NAMES, UPDATE_REPO, Settings, is_downloadable_url
from ..engine import DownloadManager, Status
from ..utils import human_speed, open_path, reveal_in_folder
from . import hudbar, icons, theme
from .dialogs import BatchDialog, CountdownDialog, LoginDialog, RenewDialog, SettingsDialog, find_urls
from .download_window import DownloadWindow
from .models import (COL_ADDED, COL_ETA, COL_NAME, COL_PROGRESS, COL_SIZE, COL_SPEED, COL_STATUS, FILTERS,
                     DownloadsModel, MeterDelegate, NameDelegate)
from .widgets import Banner, DetailsPane, SpeedMeter

SIDEBAR_ICONS = {"All downloads": "all", "Downloading": "speed", "Unfinished": "unfinished",
                 "Finished": "finished", "Queued": "queued"}


class MainWindow(QMainWindow):
    browser_request = Signal(dict)       # emitted from the local server thread
    item_event = Signal(str, str)        # (event, item_id) from engine threads
    bg_progress = Signal(str, float)     # (job, 0..1 or -1) from maintenance threads
    bg_done = Signal(str, object)
    bg_failed = Signal(str, str)

    def __init__(self, manager: DownloadManager, settings: Settings):
        super().__init__()
        self.manager = manager
        self.settings = settings
        self._quitting = False
        self._last_clip = ""
        self._jobs: set[str] = set()       # running maintenance jobs
        self._update_path = None
        self._manual_update = False
        self._manual_ytdlp = False
        self.when_done = "nothing"
        self._windows: set[DownloadWindow] = set()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(theme.app_icon())
        self.resize(1240, 760)
        self.setAcceptDrops(True)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_body()
        self._build_tray()

        self.browser_request.connect(self.handle_browser_request)
        self.item_event.connect(self._on_item_event)
        self.bg_progress.connect(self._on_bg_progress)
        self.bg_done.connect(self._on_bg_done)
        self.bg_failed.connect(self._on_bg_failed)
        manager.add_listener(lambda ev, item: self.item_event.emit(ev, item.id if item else ""))
        QGuiApplication.clipboard().dataChanged.connect(self._on_clipboard)

        self.timer = QTimer(self, interval=400)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()
        QTimer.singleShot(2500, self._prewarm)
        QTimer.singleShot(8000, self._daily_maintenance)

    # ------------------------------------------------------------------ UI
    def _act(self, text, slot, icon=None, shortcut=None, tip=None) -> QAction:
        a = QAction(text, self)
        if icon:
            icons.put(a, icon, "accent_ink" if icon == "add" else "text", 24)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
        a.triggered.connect(slot)
        self.addAction(a)                   # shortcuts work even when the toolbar is hidden
        return a

    def _build_actions(self):
        sel = self._for_selected
        m = self.manager
        self.a_add = self._act("Add URL", lambda: self.add_url(), "add", "Ctrl+N", "Add a link (Ctrl+N)")
        self.a_video = self._act("Grab video", lambda: self.add_url(kind="video"), "video", "Ctrl+G",
                                 "Download a video from a page (Ctrl+G)")
        self.a_resume = self._act("Resume", lambda: sel(m.start), "resume", "Ctrl+R")
        self.a_stop = self._act("Stop", lambda: sel(m.pause), "pause", "Ctrl+P")
        self.a_stop_all = self._act("Stop all", m.pause_all, "stop_all", "Ctrl+Shift+P")
        self.a_delete = self._act("Delete", lambda: self.delete_selected(False), "delete", "Del")
        self.a_delete_done = self._act("Clear finished", self.clear_finished, "delete_done",
                                       tip="Remove finished downloads from the list (files stay)")
        self.a_queue_start = self._act("Start queue", self.start_queue, "queue")
        self.a_queue_stop = self._act("Stop queue", self.stop_queue, "queue_stop")
        self.a_scheduler = self._act("Scheduler", lambda: self.open_settings(2), "scheduler")
        self.a_options = self._act("Options", lambda: self.open_settings(0), "settings", "Ctrl+,")
        self.a_paste = self._act("Paste links", self.paste_links, None, "Ctrl+V")
        self.a_details = QAction("Details pane", self, checkable=True, checked=self.settings.details_visible)
        self.a_details.setShortcut(QKeySequence("F9"))
        self.a_details.toggled.connect(self._toggle_details)
        self.addAction(self.a_details)

    def _build_menus(self):
        mb = self.menuBar()
        tasks = mb.addMenu("&Tasks")
        tasks.addAction(self.a_add)
        tasks.addAction(self.a_video)
        tasks.addAction("Add several links…", self.add_batch, QKeySequence("Ctrl+Shift+N"))
        tasks.addSeparator()
        tasks.addAction("Exit", self.quit, QKeySequence("Ctrl+Q"))

        dl = mb.addMenu("&Downloads")
        for a in (self.a_resume, self.a_stop, self.a_stop_all, self.a_delete, self.a_delete_done):
            dl.addAction(a)
        dl.addSeparator()
        dl.addAction(self.a_queue_start)
        dl.addAction(self.a_queue_stop)
        dl.addAction(self.a_scheduler)
        dl.addSeparator()
        done = dl.addMenu("When everything finishes")
        group = QActionGroup(self)
        for key, label in (("nothing", "Do nothing"), ("quit", "Exit YDM"), ("sleep", "Sleep"),
                           ("shutdown", "Shut down the PC")):
            a = QAction(label, self, checkable=True, checked=key == "nothing")
            a.triggered.connect(lambda _=False, k=key: setattr(self, "when_done", k))
            group.addAction(a)
            done.addAction(a)

        view = mb.addMenu("&View")
        view.addAction(self.a_details)
        self.a_toolbar_text = QAction("Toolbar labels", self, checkable=True, checked=True)
        self.a_toolbar_text.toggled.connect(lambda on: self.toolbar.setToolButtonStyle(
            Qt.ToolButtonTextUnderIcon if on else Qt.ToolButtonIconOnly))
        view.addAction(self.a_toolbar_text)

        tl = mb.addMenu("T&ools")
        tl.addAction(self.a_options)
        icons.put(tl.addAction("Install video tools", lambda: self.install_video_tools(force=True)), "tools")
        icons.put(tl.addAction("Update yt-dlp", lambda: self.update_ytdlp(manual=True)), "refresh")
        icons.put(tl.addAction("Open download folder", lambda: os.makedirs(self.settings.download_dir, exist_ok=True)
                               or open_path(self.settings.download_dir)), "folder")

        hp = mb.addMenu("&Help")
        self.a_update = icons.put(hp.addAction("Check for updates…", lambda: self.check_for_updates(manual=True)),
                                  "update")
        hp.addAction("Browser extension…", lambda: self.open_settings(3))
        hp.addSeparator()
        hp.addAction(f"About {APP_NAME}", self._about)

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(24, 24))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.toolbar = tb
        self.addToolBar(tb)
        for group in ((self.a_add, self.a_video), (self.a_resume, self.a_stop, self.a_stop_all),
                      (self.a_delete, self.a_delete_done), (self.a_queue_start, self.a_queue_stop, self.a_scheduler),
                      (self.a_options,)):
            for a in group:
                tb.addAction(a)
            tb.addSeparator()
        tb.widgetForAction(self.a_add).setObjectName("primary")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.speed_meter = SpeedMeter()
        tb.addWidget(self.speed_meter)

    def _build_body(self):
        # sidebar: categories tree + search
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search downloads")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._search_changed)
        self.sidebar = QTreeWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setColumnCount(2)
        self.sidebar.setIndentation(14)
        self.sidebar.setRootIsDecorated(False)
        self.sidebar.setIconSize(QSize(16, 16))
        self.sidebar.header().setStretchLastSection(False)
        self.sidebar.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.sidebar.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._tree_items: dict[str, QTreeWidgetItem] = {}

        def node(name, parent=None, icon=None):
            it = QTreeWidgetItem([name, ""])
            it.setData(0, Qt.UserRole, name)
            it.setData(0, Qt.UserRole + 1, icon or SIDEBAR_ICONS.get(name, "other"))
            it.setIcon(0, icons.icon(icon or SIDEBAR_ICONS.get(name, "other"), "muted", 16))
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            it.setForeground(1, theme.ink("Queued"))
            it.setFont(1, theme.number_font(8.5))
            (parent.addChild(it) if parent else self.sidebar.addTopLevelItem(it))
            self._tree_items[name] = it
            return it

        root = node("All downloads")
        for cat in CATEGORY_NAMES:
            node(cat, root, icons.CATEGORY_ICONS.get(cat))
        for name in list(FILTERS)[1:]:
            node(name)
        root.setExpanded(True)
        self.sidebar.setCurrentItem(root)
        self.sidebar.currentItemChanged.connect(self._filter_changed)

        side = QWidget()
        side.setObjectName("panel")
        side.setFixedWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 10, 0, 0)
        sl.setSpacing(6)
        self.search.setMaximumWidth(198)
        sl.addWidget(self.search)
        sl.addWidget(self.sidebar, 1)

        # list
        self.model = DownloadsModel(self.manager)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(COL_PROGRESS, MeterDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_NAME, NameDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setIconSize(QSize(16, 16))
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(COL_ADDED, Qt.DescendingOrder)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(self._open_item)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._selection_changed())
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        for col, w in ((COL_SIZE, 90), (COL_PROGRESS, 230), (COL_SPEED, 100), (COL_ETA, 84),
                       (COL_STATUS, 150), (COL_ADDED, 110)):
            h.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table.setColumnWidth(col, w)
        h.setHighlightSections(False)
        h.setSortIndicatorShown(True)

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.addStretch(1)
        big = QLabel("Nothing downloading yet")
        big.setObjectName("title")
        big.setAlignment(Qt.AlignCenter)
        how = QLabel("Download anything in your browser and YDM catches it.\n"
                     "Or press Ctrl+V here to paste a link, drop one on this window, or use Add URL.")
        how.setObjectName("muted")
        how.setAlignment(Qt.AlignCenter)
        el.addWidget(big)
        el.addWidget(how)
        el.addStretch(2)

        self.banner = Banner()
        listbox = QWidget()
        ll = QVBoxLayout(listbox)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        ll.addWidget(self.banner)
        ll.addWidget(self.table, 1)
        ll.addWidget(self.empty, 1)

        self.details = DetailsPane()
        self.split = QSplitter(Qt.Vertical)
        self.split.addWidget(listbox)
        self.split.addWidget(self.details)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        self.split.setSizes([380, 360])
        self.split.setHandleWidth(1)
        self.details.setVisible(self.settings.details_visible)

        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addWidget(side)
        bl.addWidget(self.split, 1)
        self.setCentralWidget(body)

        self.status_text = QLabel("")
        self.status_right = QLabel("")
        self.statusBar().addWidget(self.status_text, 1)
        self.statusBar().addPermanentWidget(self.status_right)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(theme.app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        icons.put(menu.addAction("Show YDM", self.show_normal), "all")
        menu.addAction(self.a_add)
        menu.addAction(self.a_video)
        menu.addAction(self.a_stop_all)
        icons.put(menu.addAction("Check for updates…", lambda: self.check_for_updates(manual=True)), "update")
        menu.addSeparator()
        menu.addAction("Exit", self.quit)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_normal()
                                    if reason == QSystemTrayIcon.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def showEvent(self, event):
        theme.apply_window_chrome(self)
        super().showEvent(event)
        self.table.setFocus()

    # ------------------------------------------------------------- refresh
    def refresh(self):
        selected = self._selected_ids()
        if self.model.refresh():
            sm = self.table.selectionModel()
            for row, item_id in enumerate(self.model.ids):
                if item_id in selected:
                    sm.select(self.model.index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        items = self.manager.visible()
        has_any = bool(items)
        self.table.setVisible(has_any)
        self.empty.setVisible(not has_any)

        active = sum(1 for i in items if i.status in Status.ACTIVE)
        queued = sum(1 for i in items if i.status == Status.QUEUED)
        speed = self.manager.total_speed()
        self.speed_meter.set_speed(speed)
        parts = [f"{active} downloading", f"{queued} queued", f"{len(items)} in list"]
        if self.settings.speed_limit_kbps:
            parts.append(f"limited to {self.settings.speed_limit_kbps} KB/s")
        if self.settings.scheduler_enabled:
            parts.append(f"scheduler {self.settings.scheduler_start}–{self.settings.scheduler_stop} "
                         f"({'running' if self.manager.queue_running else 'waiting'})")
        if self.when_done != "nothing":
            parts.append({"quit": "exit", "sleep": "sleep", "shutdown": "shut down"}[self.when_done] + " when done")
        self.status_text.setText("   ·   ".join(parts))
        self.status_right.setText(f"{APP_NAME} {APP_VERSION}")
        self.a_queue_start.setEnabled(not self.manager.queue_running)
        self.a_queue_stop.setEnabled(self.manager.queue_running)
        self.tray.setToolTip(f"{APP_NAME}: {active} downloading" + (f", {human_speed(speed)}" if speed else ""))

        counts = {name: 0 for name in self._tree_items}
        for i in items:
            for name, f in FILTERS.items():
                if f(i):
                    counts[name] += 1
            counts[i.category] = counts.get(i.category, 0) + 1
        for name, it in self._tree_items.items():
            n = counts.get(name, 0)
            text = str(n) if n else ""
            if it.text(1) != text:
                it.setText(1, text)

        if self.details.isVisible():
            ids = self._selected_ids()
            self.details.show_item(self.manager.get(next(iter(ids))) if len(ids) == 1 else None)

    def _selection_changed(self):
        for item_id in self._selected_ids():
            self.manager.mark_seen(item_id)
        ids = self._selected_ids()
        self.details.show_item(self.manager.get(next(iter(ids))) if len(ids) == 1 else None)

    def _filter_changed(self, current, _previous=None):
        if current is None:
            return
        self.model.filter_name = current.data(0, Qt.UserRole)
        self.model.refresh(force=True)

    def _search_changed(self, text: str):
        self.model.search = text.strip()
        self.model.refresh(force=True)

    def _toggle_details(self, on: bool):
        self.details.setVisible(on)
        self.settings.details_visible = on
        self.settings.save()

    # ------------------------------------------------------------- actions
    def _selected_ids(self) -> set[str]:
        sm = self.table.selectionModel()
        rows = {i.row() for i in sm.selectedRows()} if sm else set()
        return {self.model.ids[r] for r in rows if r < len(self.model.ids)}

    def _for_selected(self, fn):
        for item_id in self._selected_ids():
            fn(item_id)
        self.refresh()

    def add_url(self, url: str = "", payload: dict | None = None, kind: str | None = None):
        if not url:
            found = find_urls(QGuiApplication.clipboard().text())
            if len(found) == 1:
                url = found[0]
        if kind == "video" and tools.missing_for_video():
            self.install_video_tools(force=False)
        self.open_download_window(url=url, payload=payload, kind=kind)

    def open_download_window(self, **kwargs) -> DownloadWindow:
        """The small per-download window; works while the main window stays hidden."""
        w = DownloadWindow(self, **kwargs)
        self._windows.add(w)
        w.closed.connect(self._windows.discard)
        if kwargs.get("item_id"):
            # started from the browser's quality menu: stack quietly near the tray, don't steal focus
            area = QGuiApplication.primaryScreen().availableGeometry()
            w.adjustSize()
            n = sum(1 for x in self._windows if x is not w and x.isVisible())
            w.move(area.right() - w.width() - 24, area.bottom() - w.height() - 24 - n * 40)
            w.setAttribute(Qt.WA_ShowWithoutActivating)
            w.show()
        else:
            w.show()
            w.raise_()
            w.activateWindow()
        return w

    def add_batch(self, urls: list[str] | None = None):
        if urls is None:
            urls = find_urls(QGuiApplication.clipboard().text())
            if not urls:
                text, ok = QInputDialog.getMultiLineText(self, "Add several links", "Paste links, one per line:")
                if not ok:
                    return
                urls = find_urls(text)
        if not urls:
            return
        dlg = BatchDialog(urls, self)
        if dlg.exec():
            for u in dlg.urls():
                item = self.manager.add(u, start=False)
                self.manager.queue(item.id)
            if dlg.start_now:
                self.manager.start_queue()
            self.refresh()

    def paste_links(self):
        if isinstance(QApplication.focusWidget(), QLineEdit):
            QApplication.focusWidget().paste()        # normal paste inside text fields
            return
        urls = find_urls(QGuiApplication.clipboard().text())
        if len(urls) > 1:
            self.add_batch(urls)
        else:
            self.add_url(urls[0] if urls else "")

    def handle_browser_request(self, payload: dict):
        if payload.get("kind") == "batch":              # "Download all links" from the extension
            urls = [u for u in payload.get("urls") or [] if str(u).lower().startswith(("http://", "https://"))]
            if urls:
                self.show_normal()
                self.add_batch(urls)
            return
        kind = "video" if payload.get("kind") == "video" else "file"
        headers = {k: v for k, v in {"Referer": payload.get("referrer") or "",
                                     "User-Agent": payload.get("userAgent") or "",
                                     "Cookie": payload.get("cookies") or ""}.items() if v}
        if kind == "file" and self._renew_from_browser(payload, headers):
            return
        quick = (kind == "file" and not self.settings.show_add_dialog) or \
            (kind == "video" and bool(payload.get("quick")))      # quality already picked on the page
        if not quick:
            self.add_url(payload.get("url", ""), payload, kind)
            return
        item = self.manager.add(payload["url"], kind=kind, filename=payload.get("filename") or "",
                                headers=headers, video_format=payload.get("video_format") or "",
                                convert=payload.get("convert") or "", title=payload.get("title") or "")
        if self.settings.progress_window:
            self.open_download_window(item_id=item.id)
        elif self.tray.isVisible():
            self.tray.showMessage(APP_NAME, f"Downloading {item.title or item.filename or 'file'}",
                                  QSystemTrayIcon.Information, 2500)

    def _renew_from_browser(self, payload: dict, headers: dict) -> bool:
        url = payload.get("url", "")
        item = self.manager.find_renewal(payload.get("filename") or "", int(payload.get("fileSize") or 0))
        if item is None:
            return False
        self.banner.hide()
        self.manager.refresh_url(item.id, url, headers)
        if self.settings.progress_window:
            self.open_download_window(item_id=item.id)
        elif self.tray.isVisible():
            self.tray.showMessage(APP_NAME, f"New link caught: {item.filename} continues",
                                  QSystemTrayIcon.Information, 2500)
        return True

    def renew_address(self, item_id: str):
        """IDM's "refresh download address": catch the fresh link from the browser, keep the progress."""
        item = self.manager.get(item_id)
        if not item:
            return
        page = item.headers.get("Referer", "")
        dlg = RenewDialog(item.dest_name or item.filename or item.url, page, self)
        if not dlg.exec():
            return
        if dlg.choice == "paste":
            self.refresh_address(item_id)
            return
        self.manager.pause(item_id)
        self.manager.renew_wait = item_id
        if page:
            webbrowser.open(page)
        self.banner.show_message(f"Waiting for the new link of {item.filename}: click its download link in the "
                                 "browser.", "link", [("Cancel", self._cancel_renew, False)])

    def _cancel_renew(self):
        self.manager.renew_wait = None
        self.banner.hide()

    def login_for(self, item_id: str):
        from urllib.parse import urlsplit
        item = self.manager.get(item_id)
        if not item:
            return
        dlg = LoginDialog(urlsplit(item.url).hostname or "", item.auth_user, self)
        if dlg.exec():
            _host, user, password, remember = dlg.values()
            if user:
                self.manager.set_login(item_id, user, password, remember)
                self.refresh()

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

    def clear_finished(self):
        for item in self.manager.items():
            if item.status == Status.COMPLETED:
                self.manager.remove(item.id)
        self.refresh()

    def start_queue(self):
        self.manager.resume_all()
        self.refresh()

    def stop_queue(self):
        self.manager.stop_queue()
        self.refresh()

    def toggle_queue(self):
        if self.manager.queue_running:
            self.stop_queue()
        else:
            self.start_queue()

    def open_settings(self, tab: int = 0):
        old_port = self.settings.port
        dlg = SettingsDialog(self.settings, self, tab)
        dlg.install_tools.connect(lambda: self.install_video_tools(force=True))
        dlg.check_updates.connect(lambda: self.check_for_updates(manual=True))
        dlg.update_ytdlp.connect(lambda: self.update_ytdlp(manual=True))
        dlg.theme_preview.connect(self.apply_theme)
        if dlg.exec():
            s = dlg.updated_settings()
            s.save()
            self.manager.apply_settings(s)
            self.apply_theme(s.accent, s.signal, s.bar_colors, s.ui_mode)
            if s.port != old_port:
                QMessageBox.information(self, APP_NAME, "Restart YDM to use the new extension port.")
            self.refresh()

    def apply_theme(self, accent: str, signal: str, bar_mode: str, ui_mode: str = "dark"):
        """Recolour the whole app in place (Options → Appearance): light/dark, accent, meters."""
        theme.set_mode(ui_mode)
        theme.set_colors(accent, signal)
        hudbar.BAR_MODE[0] = bar_mode
        app = QApplication.instance()
        app.setPalette(theme.palette())
        app.setStyleSheet(theme.QSS)
        icons.retint([self, self._tray_menu])
        for it in self._tree_items.values():
            it.setIcon(0, icons.icon(it.data(0, Qt.UserRole + 1), "muted", 16))
            it.setForeground(1, theme.ink("Queued"))
        self.setWindowIcon(theme.app_icon())
        self.tray.setIcon(theme.app_icon())
        theme.apply_window_chrome(self)
        for w in list(self._windows):
            w.apply_theme()
        self.table.viewport().update()
        self.speed_meter.update()
        self.details.show_item(self.details.item)

    def refresh_address(self, item_id: str):
        item = self.manager.get(item_id)
        if not item:
            return
        url, ok = QInputDialog.getText(self, "Refresh download address",
                                       "Paste the new link for this file. Progress is kept.", QLineEdit.Normal,
                                       item.url)
        if ok and url.strip():
            self.manager.refresh_url(item_id, url.strip())
            self.refresh()

    def _open_item(self, index):
        item = self.model.item_at(index.row())
        if item and item.status == Status.COMPLETED and os.path.exists(item.path):
            open_path(item.path)
        elif item and item.expired and item.kind == "file":
            self.renew_address(item.id)
        elif item and item.needs_login:
            self.login_for(item.id)
        elif item:
            self.manager.start(item.id)

    def _context_menu(self, pos):
        index = self.table.indexAt(pos)
        item = self.model.item_at(index.row()) if index.isValid() else None
        if not item:
            return
        m = QMenu(self)
        if item.status == Status.COMPLETED:
            m.addAction(icons.icon("open"), "Open", lambda: open_path(item.path))
            m.addAction(icons.icon("folder"), "Show in folder", lambda: reveal_in_folder(item.path))
        else:
            m.addAction(icons.icon("resume"), "Resume", lambda: self._for_selected(self.manager.start))
            m.addAction(icons.icon("pause"), "Stop", lambda: self._for_selected(self.manager.pause))
            m.addAction(icons.icon("queue"), "Add to queue", lambda: self._for_selected(self.manager.queue))
            if item.save_dir:
                m.addAction(icons.icon("folder"), "Open folder",
                            lambda: open_path(item.save_dir) if os.path.isdir(item.save_dir) else None)
            if item.kind == "file":
                m.addAction(icons.icon("link"), "Renew download address…", lambda: self.renew_address(item.id))
            m.addAction(icons.icon("key"), "Login…", lambda: self.login_for(item.id))
            conn = m.addMenu(icons.icon("plugs"), f"Connections ({item.connections})")
            for n in (1, 2, 4, 8, 12, 16, 24, 32):
                a = conn.addAction(str(n), lambda n=n: self._for_selected(
                    lambda i: self.manager.set_connections(i, n)))
                a.setCheckable(True)
                a.setChecked(n == item.connections)
        m.addAction(icons.icon("refresh"), "Restart from zero", lambda: self._for_selected(self.manager.restart))
        m.addSeparator()
        m.addAction(icons.icon("copy"), "Copy address", lambda: QGuiApplication.clipboard().setText(item.url))
        m.addSeparator()
        m.addAction(icons.icon("delete"), "Remove from list", lambda: self.delete_selected(False))
        m.addAction("Delete with file", lambda: self.delete_selected(True))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _about(self):
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<b>{APP_NAME} {APP_VERSION}</b><br>A personal multi-connection download manager."
                          f"<br><br>Video support by yt-dlp {tools.ytdlp_version() or ''}.")

    # ------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasUrls() or md.hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        md = event.mimeData()
        text = "\n".join(u.toString() for u in md.urls()) if md.hasUrls() else md.text()
        urls = find_urls(text)
        if len(urls) > 1:
            self.add_batch(urls)
        elif urls:
            self.add_url(urls[0])

    # -------------------------------------------------------------- events
    def _on_item_event(self, event: str, item_id: str):
        if event == "all_done":
            self._all_done()
            return
        item = self.manager.get(item_id)
        if not item or not self.settings.notify_on_complete or not self.tray.isVisible():
            return
        if event == "completed":
            self.tray.showMessage("Download complete", item.filename, QSystemTrayIcon.Information, 4000)
        elif event == "error":
            self.tray.showMessage("Download failed", f"{item.filename or item.url}\n{item.error}",
                                  QSystemTrayIcon.Warning, 5000)

    def _all_done(self):
        action = self.when_done
        if action == "nothing":
            return
        label = {"quit": "Exiting YDM", "sleep": "Sleeping", "shutdown": "Shutting down"}[action]
        self.show_normal()
        if not CountdownDialog(label.split()[0], 30, self).exec():
            return
        self.when_done = "nothing"
        if action == "quit":
            self.quit()
        elif sys.platform == "win32":
            if action == "shutdown":
                self.manager.save()
                subprocess.Popen(["shutdown", "/s", "/t", "0"], creationflags=0x08000000)
            else:
                subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=0x08000000)

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

    # --------------------------------------------------------- maintenance
    def _prewarm(self):
        """Import yt-dlp in the background so the first video dialog opens instantly."""
        threading.Thread(target=lambda: __import__("yt_dlp"), daemon=True).start()

    def _spawn(self, job: str, fn) -> bool:
        if job in self._jobs:
            return False
        self._jobs.add(job)

        def run():
            try:
                self.bg_done.emit(job, fn())
            except Exception as exc:  # noqa: BLE001 - shown in the banner
                self.bg_failed.emit(job, str(exc).splitlines()[0][:200] if str(exc) else exc.__class__.__name__)
        threading.Thread(target=run, daemon=True).start()
        return True

    def _daily_maintenance(self):
        s = self.settings
        if not s.auto_update or time.time() - s.last_update_check < 20 * 3600:
            return
        s.last_update_check = time.time()
        s.save()
        self.check_for_updates(manual=False)
        if getattr(sys, "frozen", False):
            self.update_ytdlp(manual=False)
        QTimer.singleShot(24 * 3600 * 1000, self._daily_maintenance)

    def install_video_tools(self, force: bool = False):
        names = list(tools.TOOLS) if force else tools.missing_for_video()
        if not names or sys.platform != "win32":
            if force:
                self.banner.show_message("Video tools are ready.", "finished")
                QTimer.singleShot(4000, self.banner.hide)
            return

        def work():
            for i, name in enumerate(names):
                tools.install(name, lambda d, t, i=i: self.bg_progress.emit(
                    f"tools:{name}", (i + (d / t if t > 0 else 0)) / len(names)))
            return names
        if self._spawn("tools", work):
            self.banner.show_message("Installing video tools (FFmpeg, Deno)…", "tools", progress=0)

    def update_ytdlp(self, manual: bool = False):
        def work():
            return tools.update_ytdlp()
        self._manual_ytdlp = manual
        if self._spawn("ytdlp", work) and manual:
            self.banner.show_message("Checking for a newer yt-dlp…", "refresh", progress=-1)

    def check_for_updates(self, manual: bool = False):
        self._manual_update = manual
        if not updater.enabled():
            if manual:
                self.banner.show_message("Update checks are off: set UPDATE_REPO in ydm/config.py.", "update",
                                         [("OK", self.banner.hide, False)])
            return

        def work():
            rel = updater.check()
            if rel and rel.url and updater.can_install():
                self.bg_progress.emit("update", 0.0)
                path = updater.download(rel, lambda d, t: self.bg_progress.emit("update", d / t if t > 0 else -1))
                return rel, path
            return rel, None
        if self._spawn("update", work) and manual:
            self.banner.show_message("Checking for updates…", "update", progress=-1)

    def _on_bg_progress(self, job: str, value: float):
        if job.startswith("tools:"):
            self.banner.show_message(f"Installing video tools: {job.split(':')[1].capitalize()}…", "tools",
                                     progress=value)
        elif job == "update":
            self.banner.show_message("Downloading the new version of YDM…", "update", progress=value)

    def _on_bg_done(self, job: str, result):
        self._jobs.discard(job)
        if job == "tools":
            self.banner.show_message("Video tools are ready: best quality and YouTube now work.", "finished")
            QTimer.singleShot(5000, self.banner.hide)
        elif job == "ytdlp":
            if result:
                self.banner.show_message(f"yt-dlp {result} downloaded. It's used from the next start of YDM.",
                                         "refresh", [("OK", self.banner.hide, False)])
            elif self._manual_ytdlp:
                self.banner.show_message(f"yt-dlp is up to date ({tools.ytdlp_version()}).", "finished")
                QTimer.singleShot(4000, self.banner.hide)
        elif job == "update":
            rel, path = result
            if rel is None:
                if self._manual_update:
                    self.banner.show_message(f"You have the latest version ({APP_VERSION}).", "finished")
                    QTimer.singleShot(4000, self.banner.hide)
            elif path is not None:
                self._update_path = path
                self.banner.show_message(f"YDM {rel.version} is ready. Restarting takes a few seconds and your "
                                         f"downloads carry on.", "update",
                                         [("Later", self.banner.hide, False),
                                          ("Restart to update", self.restart_to_update, True)])
            elif not rel.url:
                if self._manual_update:
                    self.banner.show_message(f"YDM {rel.version} is published, but its installer isn't attached to "
                                             "the release yet (it's still building, or the upload was missed).",
                                             "update", [("OK", self.banner.hide, False),
                                                        ("Open release page", lambda: webbrowser.open(
                                                            f"https://github.com/{UPDATE_REPO}/releases"), True)])
            else:
                self.banner.show_message(f"YDM {rel.version} is available.", "update",
                                         [("Later", self.banner.hide, False),
                                          ("Open release page", lambda: webbrowser.open(
                                              f"https://github.com/{UPDATE_REPO}/releases/latest"), True)])

    def _on_bg_failed(self, job: str, message: str):
        self._jobs.discard(job)
        what = {"tools": "Couldn't install the video tools", "ytdlp": "Couldn't update yt-dlp",
                "update": "Couldn't check for updates"}[job]
        if job == "update" and not self._manual_update:
            return                      # silent daily check: try again tomorrow
        self.banner.show_message(f"{what}: {message}", "update",
                                 [("Dismiss", self.banner.hide, False)])

    def restart_to_update(self):
        if not self._update_path:
            return
        path = self._update_path
        self._quitting = True
        self.timer.stop()
        self.tray.hide()
        self.manager.shutdown(resume_later=True)
        updater.launch_installer(path)
        QApplication.quit()

    # ----------------------------------------------------------- lifecycle
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
                self.tray.showMessage(APP_NAME, "Still running in the tray. Right-click the icon to exit.",
                                      QSystemTrayIcon.Information, 3000)
            return
        self.quit()
        event.accept()

    def quit(self):
        if self._quitting and not self.timer.isActive():
            return
        self._quitting = True
        for w in list(self._windows):
            w.close()
        self.timer.stop()
        self.tray.hide()
        self.manager.shutdown()
        QApplication.quit()
