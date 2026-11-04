"""One small window per download, like IDM's "Download File Info" + progress box.

The download starts the moment a link arrives (pending, hidden from the list),
so while you pick a name and folder the bytes are already coming in; pressing
Start just confirms it. The same window then shows progress and a finish
screen. The full app never has to open.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSizePolicy,
                               QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from .. import tools
from ..config import APP_NAME, category_for
from ..engine import Status
from ..engine import gallery
from ..engine.gallery import fetch_media_info
from ..engine.video_downloader import has_ffmpeg
from ..utils import human_eta, human_size, human_speed, open_path, open_with, reveal_in_folder
from . import icons, theme
from .dialogs import _hairline, _Job, _segment_qss, find_urls, looks_like_video_page
from .hudbar import HudBar


class DownloadWindow(QWidget):
    closed = Signal(object)

    def __init__(self, main, *, url: str = "", payload: dict | None = None, kind: str | None = None,
                 item_id: str | None = None):
        super().__init__(None, Qt.Window)
        self.main, self.manager, self.settings = main, main.manager, main.settings
        self.payload = payload or {}
        self.headers = {k: v for k, v in {
            "Referer": self.payload.get("referrer") or "",
            "User-Agent": self.payload.get("userAgent") or "",
            "Cookie": self.payload.get("cookies") or "",
        }.items() if v}
        self.item_id = item_id
        self._prestarted_url = ""
        self._info: dict | None = None
        self._ticket = 0
        self._jobs: list[_Job] = []
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowIcon(theme.app_icon())
        self.setWindowTitle(APP_NAME)
        self.setMinimumWidth(640)
        self.setStyleSheet(f"DownloadWindow {{ background: {theme.BG}; }}")

        # header: what is being downloaded
        self.glyph = QLabel()
        self.title = QLabel("New download")
        self.title.setObjectName("title")
        self.sub = QLabel("")
        self.sub.setObjectName("muted")
        self.btn_app = btn_app = icons.put(QPushButton(" Open YDM"), "all", "accent_ink", 16)
        btn_app.setObjectName("flat")
        btn_app.setToolTip("Open the full download list")
        btn_app.clicked.connect(self.main.show_normal)
        names = QVBoxLayout()
        names.setSpacing(2)
        names.addWidget(self.title)
        names.addWidget(self.sub)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(self.glyph, 0, Qt.AlignTop)
        head.addLayout(names, 1)
        head.addWidget(btn_app, 0, Qt.AlignTop)

        self.meter = HudBar()
        self.live = QLabel("")
        self.live.setFont(theme.number_font(9))
        self.live.setObjectName("muted")

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_setup())
        self.pages.addWidget(self._build_progress())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(12)
        lay.addLayout(head)
        lay.addWidget(self.meter)
        lay.addWidget(self.live)
        lay.addWidget(self.pages)

        self.timer = QTimer(self, interval=250)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._debounce = QTimer(self, singleShot=True, interval=350)
        self._debounce.timeout.connect(self._setup_for_url)

        if item_id:                                   # started elsewhere (on-page quality menu)
            self._to_progress()
        else:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            self.url_edit.setText(url)
            video = kind == "video" or (kind is None and looks_like_video_page(url))
            (self.btn_video if video else self.btn_file).setChecked(True)
            self._mode_changed()
            self.url_edit.textChanged.connect(lambda _: self._debounce.start())
            self.btn_video.toggled.connect(lambda _: self._mode_changed())
        self._tick()

    # ------------------------------------------------------------------ build
    def _build_setup(self) -> QWidget:
        w = QWidget()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Paste a link: a file, a video, or a post with photos (YouTube, Instagram, Facebook…)")
        self.url_edit.setClearButtonEnabled(True)
        self.btn_file = icons.put(QPushButton(" File"), "document", "text", 16)
        self.btn_video = icons.put(QPushButton(" Video / photos"), "video", "text", 16)
        self.btn_video.setToolTip("A page with a video, a reel or photos: YDM reads it and lists what it can save")
        group = QButtonGroup(self)
        for i, b in enumerate((self.btn_file, self.btn_video)):
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(_segment_qss(i, 2))
            group.addButton(b)
        url_row = QHBoxLayout()
        url_row.setSpacing(0)
        url_row.addWidget(self.url_edit, 1)
        url_row.addSpacing(10)
        url_row.addWidget(self.btn_file)
        url_row.addWidget(self.btn_video)

        self.name_edit = QLineEdit(self.payload.get("filename") or "")
        self.name_edit.setPlaceholderText("Automatic")
        save_as = QPushButton("Save as…")
        save_as.setToolTip("Pick the name and the folder in one go")
        save_as.clicked.connect(self._save_as_setup)
        name_row = QHBoxLayout()
        name_row.addWidget(self.name_edit, 1)
        name_row.addWidget(save_as)
        self.dir_edit = QLineEdit(self.settings.remember_dir)
        self.dir_edit.setPlaceholderText(f"{self.settings.download_dir}"
                                         f"{'  ·  by category' if self.settings.use_category_folders else ''}")
        browse = icons.put(QPushButton(""), "folder", "text", 16)
        browse.setToolTip("Choose folder")
        browse.clicked.connect(self._browse)
        self.remember_box = QCheckBox("Remember")
        self.remember_box.setToolTip("Save every download to this folder from now on "
                                     "(Options → General puts it back)")
        self.remember_box.setChecked(bool(self.settings.remember_dir))
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.dir_edit, 1)
        folder_row.addWidget(browse)
        folder_row.addWidget(self.remember_box)

        self.login_user = QLineEdit()
        self.login_user.setPlaceholderText("User name")
        self.login_pass = QLineEdit()
        self.login_pass.setPlaceholderText("Password")
        self.login_pass.setEchoMode(QLineEdit.Password)
        self.login_keep = QCheckBox("Remember")
        self.login_keep.setToolTip("Save this login for the site (Options → Proxy & logins)")
        self.login_pass.returnPressed.connect(lambda: self._start(True))
        login_row = QHBoxLayout()
        login_row.addWidget(self.login_user, 1)
        login_row.addWidget(self.login_pass, 1)
        login_row.addWidget(self.login_keep)

        self.quality = QListWidget()
        self.quality.setObjectName("choices")
        self.quality.itemDoubleClicked.connect(lambda _: self._start(True))
        self.tools_row = QWidget()
        tr = QHBoxLayout(self.tools_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tools_text = QLabel("1080p and up, MP3, YouTube and photos from social sites need FFmpeg, Deno and "
                            "gallery-dl (about 260 MB, once).")
        tools_text.setObjectName("muted")
        tools_text.setWordWrap(True)
        tools_btn = icons.put(QPushButton(" Install"), "tools", "text", 16)
        tools_btn.clicked.connect(lambda: self.main.install_video_tools(force=False))
        tr.addWidget(tools_text, 1)
        tr.addWidget(tools_btn)

        self.form = QFormLayout()
        self.form.setHorizontalSpacing(14)
        self.form.setVerticalSpacing(10)
        self.form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.form.addRow("Address", url_row)
        self.form.addRow("Save as", name_row)
        self.form.addRow("Folder", folder_row)
        self.form.addRow("Quality", self.quality)
        self.form.addRow("Login", login_row)
        self.form.setRowVisible(4, False)

        self.btn_later = QPushButton("Download later")
        self.btn_start = QPushButton("Start download")
        self.btn_start.setObjectName("primary")
        self.btn_start.setDefault(True)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        self.btn_later.clicked.connect(lambda: self._start(False))
        self.btn_start.clicked.connect(lambda: self._start(True))
        self.btn_login = icons.put(QPushButton(" Login"), "key", "text", 16)
        self.btn_login.setObjectName("flat")
        self.btn_login.setToolTip("For servers that ask for a user name and password")
        self.btn_login.clicked.connect(lambda: self._show_login(True))
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_login)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.btn_later)
        buttons.addWidget(self.btn_start)

        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(12)
        lay.addLayout(self.form)
        lay.addWidget(self.tools_row)
        lay.addWidget(_hairline())
        lay.addLayout(buttons)
        return w

    def _build_progress(self) -> QWidget:
        w = QWidget()
        addr_legend = QLabel("Address")
        addr_legend.setFont(theme.label_font(7))
        addr_legend.setObjectName("legend")
        self.addr = QLabel("")
        self.addr.setObjectName("muted")
        self.addr.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.addr.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.btn_copy = copy = icons.put(QPushButton(""), "copy", "text", 14)
        copy.setObjectName("flat")
        copy.setToolTip("Copy the download address")
        copy.clicked.connect(self._copy_address)
        addr_row = QHBoxLayout()
        addr_row.setSpacing(10)
        addr_row.addWidget(addr_legend)
        addr_row.addWidget(self.addr, 1)
        addr_row.addWidget(copy)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(5)
        self.facts: dict[str, QLabel] = {}
        for i, key in enumerate(["Downloaded", "Speed", "Time left", "Connections"]):
            legend = QLabel(key)
            legend.setFont(theme.label_font(7))
            legend.setObjectName("legend")
            grid.addWidget(legend, 0, i)
            if key == "Connections":
                continue
            val = QLabel("—")
            val.setFont(theme.number_font(10))
            grid.addWidget(val, 1, i)
            self.facts[key] = val
        self.conn_spin = QSpinBox()
        self.conn_spin.setRange(1, 32)
        self.conn_spin.setFont(theme.number_font(10))
        self.conn_spin.setFixedWidth(70)
        self.conn_spin.setToolTip("Change it while it downloads: new connections join at once, extra ones "
                                  "finish their piece and leave")
        self.conn_spin.valueChanged.connect(self._connections_changed)
        self.conn_active = QLabel("")
        self.conn_active.setObjectName("muted")
        self.conn_active.setFont(theme.number_font(8.5))
        conn = QHBoxLayout()
        conn.setSpacing(8)
        conn.addWidget(self.conn_spin)
        conn.addWidget(self.conn_active)
        conn.addStretch(1)
        grid.addLayout(conn, 1, 3)
        self.btn_pause = icons.put(QPushButton(" Pause"), "pause", "text", 16)
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_open = icons.put(QPushButton(" Open"), "open", "on_accent", 16)
        self.btn_open.setObjectName("primary")
        self.btn_open.clicked.connect(self._open_file)
        self.btn_folder = icons.put(QPushButton(" Open folder"), "folder", "text", 16)
        self.btn_folder.clicked.connect(self._open_folder)
        self.btn_hide = QPushButton("Hide")
        self.btn_hide.setToolTip("The download keeps going; it stays in YDM's list")
        self.btn_hide.clicked.connect(self.close)
        self.btn_fix = QPushButton()
        self.btn_fix.setObjectName("primary")
        self.btn_fix.clicked.connect(self._fix)
        self.btn_later_p = QPushButton("Later")
        self.btn_later_p.setToolTip("Stop now and keep it in the list; Resume or Start queue continues it")
        self.btn_later_p.clicked.connect(self._later)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setToolTip("Stop and delete what was downloaded")
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_with = icons.put(QPushButton(" Open with…"), "open", "text", 16)
        self.btn_with.setToolTip("Choose the program to open it with")
        self.btn_with.clicked.connect(self._open_with)
        self.btn_save = QPushButton("Save as…")
        self.btn_save.setToolTip("Rename it or move it to another folder (a running download moves when it finishes)")
        self.btn_save.clicked.connect(self._save_as)
        row = QHBoxLayout()
        row.setSpacing(8)
        for b in (self.btn_pause, self.btn_later_p, self.btn_cancel, self.btn_with, self.btn_fix):
            row.addWidget(b)
        row.addStretch(1)
        for b in (self.btn_save, self.btn_folder, self.btn_hide, self.btn_open):
            row.addWidget(b)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(14)
        lay.addLayout(addr_row)
        lay.addLayout(grid)
        lay.addWidget(_hairline())
        lay.addLayout(row)
        return w

    # ------------------------------------------------------------ setup phase
    def _video(self) -> bool:
        return self.btn_video.isChecked()

    def _mode_changed(self):
        video = self._video()
        self.form.setRowVisible(1, not video)          # Save as
        self.form.setRowVisible(3, False)               # Quality appears once the page is read
        self.tools_row.setVisible(video and bool(tools.missing_for_video()))
        self.btn_later.setVisible(not video)
        if video:
            self.form.setRowVisible(4, False)
        self.btn_login.setVisible(not video and not self.form.isRowVisible(4))
        self._setup_for_url()

    def _setup_for_url(self):
        url = self.url_edit.text().strip()
        if not url.lower().startswith(("http://", "https://", "ftp://", "ftps://")):
            urls = find_urls(url)
            if not urls:
                self._drop_prestart()
                self.live.setText("")
                return
            url = urls[0]
        if self._video():
            self._drop_prestart()
            self._info = None
            self.quality.clear()
            self.form.setRowVisible(3, False)
            self.live.setText("Reading the page…")
            self._run(fetch_media_info, url, self.headers)
        elif url != self._prestarted_url:
            self._prestart(url)
        self.adjustSize()

    def _prestart(self, url: str):
        """IDM's trick: download first, ask questions while it runs."""
        self._drop_prestart()
        item = self.manager.add(url, headers=self.headers, filename=self.payload.get("filename") or "",
                                pending=True)
        self.item_id, self._prestarted_url = item.id, url

    def _drop_prestart(self):
        if self.item_id and self._prestarted_url:
            item = self.manager.get(self.item_id)
            if item and item.pending:
                self.manager.discard(self.item_id)
        self.item_id, self._prestarted_url = None, ""

    def _run(self, fn, *args):
        self._ticket += 1
        job = _Job(self._ticket, fn, *args)
        job.signals.done.connect(self._on_info)
        job.signals.failed.connect(self._on_info_failed)
        self._jobs.append(job)
        QThreadPool.globalInstance().start(job)

    def _on_info(self, ticket: int, info: dict):
        if ticket != self._ticket:
            return
        self._info = info
        self.quality.clear()
        for label, spec, size, convert in info["options"]:
            it = QListWidgetItem(f"{label:<16}{('≈ ' + human_size(size)) if size else ''}")
            it.setData(Qt.UserRole, (spec, convert))
            if spec == gallery.OPTION:
                it.setIcon(icons.icon("image", theme.MUTED, 16))
            elif convert == "mp3" or label.startswith("Audio"):
                it.setIcon(icons.icon("music", theme.MUTED, 16))
            else:
                it.setIcon(icons.icon("video", theme.MUTED, 16))
            self.quality.addItem(it)
        self.quality.setCurrentRow(0)
        self.quality.setFixedHeight(min(260, 8 + 31 * self.quality.count()))
        self.form.setRowVisible(3, True)
        self.title.setText(_elide(self.title, info["title"], 430))
        by = info.get("uploader") or ""
        self.sub.setText(by)
        only_photos = all(spec == gallery.OPTION for _l, spec, _s, _c in info["options"])
        note = "" if has_ffmpeg() or only_photos else "Up to 720p until the video tools are installed."
        self.live.setText(note)
        self.glyph.setPixmap(icons.pixmap("image" if only_photos else "video", theme.MUTED, 28))
        self.adjustSize()

    def _on_info_failed(self, ticket: int, message: str):
        if ticket == self._ticket:
            self.live.setText(f"Couldn't read this page: {message}")

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save to", self.dir_edit.text() or self.settings.download_dir)
        if d:
            self.dir_edit.setText(os.path.normpath(d))

    def _save_as_setup(self):
        item = self.manager.get(self.item_id) if self.item_id else None
        name = self.name_edit.text().strip() or self.name_edit.placeholderText().replace("Automatic", "") \
            or (item.filename if item else "") or "download"
        folder = self.dir_edit.text().strip() or (item.save_dir if item and item.save_dir else
                                                  self.settings.download_dir)
        path, _ = QFileDialog.getSaveFileName(self, "Save as", os.path.join(folder, name))
        if path:
            folder, name = os.path.split(os.path.normpath(path))
            self.name_edit.setText(name)
            self.dir_edit.setText(folder)

    def _start(self, now: bool):
        url = self.url_edit.text().strip()
        if self._video():
            current = self.quality.currentItem()
            if not current:
                self.live.setText("Wait for the quality list, or check the address.")
                return
            spec, convert = current.data(Qt.UserRole)
            if spec == gallery.OPTION:                 # every photo/video of the post, each its own download
                self.main.save_post(url, self.headers, self.dir_edit.text().strip())
                self._remember_folder()
                self.close()
                return
            item = self.manager.add(url, kind="video", video_format=spec, convert=convert,
                                    save_dir=self.dir_edit.text().strip(), headers=self.headers,
                                    title=(self._info or {}).get("title", ""))
            self.item_id = item.id
        else:
            if not self.item_id:
                self.live.setText("Enter an address that starts with http://, https:// or ftp://")
                self.url_edit.setFocus()
                return
            user = self.login_user.text().strip()
            if user and self.form.isRowVisible(4):
                self.manager.set_login(self.item_id, user, self.login_pass.text(), self.login_keep.isChecked())
            self.manager.confirm(self.item_id, filename=self.name_edit.text().strip(),
                                 save_dir=self.dir_edit.text().strip(), start=now)
        self._remember_folder()
        self._prestarted_url = ""                    # confirmed: closing no longer cancels it
        if now and self.settings.progress_window:
            self._to_progress()
        else:
            self.close()

    # --------------------------------------------------------- progress phase
    def _to_progress(self):
        self.pages.widget(0).setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)   # don't keep its height
        self.pages.setCurrentIndex(1)
        if self.windowFlags() & Qt.WindowStaysOnTopHint:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
            self.show()
        self._tick()
        self.adjustSize()

    def _remember_folder(self):
        folder = self.dir_edit.text().strip()
        want = folder if self.remember_box.isChecked() and folder else ""
        if want != self.settings.remember_dir:
            self.settings.remember_dir = want
            self.settings.save()

    def _item(self):
        return self.manager.get(self.item_id) if self.item_id else None

    def _copy_address(self):
        item = self._item()
        if item:
            QGuiApplication.clipboard().setText(item.url)
            self.btn_copy.setText(" Copied")
            QTimer.singleShot(1500, lambda: self.btn_copy.setText(""))

    def _later(self):
        item = self._item()
        if item:
            self.manager.later(item.id)
            self.close()

    def _cancel(self):
        item = self._item()
        if not item:
            return
        if item.downloaded > 0 and QMessageBox.question(
                self, APP_NAME, f"Cancel {item.dest_name or item.filename or 'this download'} and delete what "
                                f"was downloaded ({human_size(item.downloaded)})?") != QMessageBox.Yes:
            return
        self.manager.discard(item.id)
        self.item_id = None
        self.close()

    def _open_with(self):
        item = self._item()
        if item and os.path.exists(item.path):
            self.manager.mark_seen(item.id)
            open_with(item.path)

    def _save_as(self):
        item = self._item()
        if not item:
            return
        name = item.dest_name or item.filename or item.title or "download"
        folder = item.dest_dir or item.save_dir or self.settings.download_dir
        path, _ = QFileDialog.getSaveFileName(self, "Save as", os.path.join(folder, name))
        if not path:
            return
        folder, new = os.path.split(os.path.normpath(path))
        old_ext = os.path.splitext(item.filename or "")[1]
        if old_ext and not os.path.splitext(new)[1]:
            new += old_ext                           # keep the type when only a name was typed
        self.manager.rename(item.id, new, folder)
        self._tick()

    def _show_login(self, focus: bool = False):
        if not self.form.isRowVisible(4):
            self.form.setRowVisible(4, True)
            self.btn_login.hide()
            self.adjustSize()
        if focus:
            self.login_user.setFocus()

    def _connections_changed(self, n: int):
        item = self.manager.get(self.item_id) if self.item_id else None
        if item and n != item.connections:
            self.manager.set_connections(item.id, n)

    def _fix(self):
        item = self.manager.get(self.item_id) if self.item_id else None
        if item and item.expired:
            self.main.renew_address(item.id)
        elif item and item.needs_login:
            self.main.login_for(item.id)

    def apply_theme(self):
        self.btn_file.setStyleSheet(_segment_qss(0, 2))
        self.btn_video.setStyleSheet(_segment_qss(1, 2))
        self.setStyleSheet(f"DownloadWindow {{ background: {theme.BG}; }}")
        theme.apply_window_chrome(self)
        self.meter.update()
        self._tick()

    def _toggle_pause(self):
        item = self.manager.get(self.item_id) if self.item_id else None
        if not item:
            return
        if item.status in Status.ACTIVE or item.status == Status.QUEUED:
            self.manager.pause(item.id)
        else:
            self.manager.start(item.id)

    def _open_file(self):
        item = self.manager.get(self.item_id) if self.item_id else None
        if item and os.path.exists(item.path):
            self.manager.mark_seen(item.id)
            open_path(item.path)
            self.close()

    def _open_folder(self):
        item = self.manager.get(self.item_id) if self.item_id else None
        if not item:
            return
        if item.status == Status.COMPLETED and os.path.exists(item.path):
            reveal_in_folder(item.path)
        elif item.save_dir and os.path.isdir(item.save_dir):
            open_path(item.save_dir)

    # ------------------------------------------------------------------ live
    def _tick(self):
        item = self.manager.get(self.item_id) if self.item_id else None
        self.meter.setVisible(item is not None)
        self.meter.set_item(item)
        if item is None:
            return
        name = item.dest_name or item.filename or item.title or ""
        if name:
            self.title.setText(_elide(self.title, name, 430))
            self.glyph.setPixmap(icons.pixmap(icons.CATEGORY_ICONS.get(
                "Video" if item.kind == "video" else category_for(name), "other"), theme.MUTED, 28))
        size = human_size(item.total) if item.total > 0 else ""
        where = item.dest_dir or item.save_dir
        self.sub.setText("  ·  ".join(x for x in (size, where) if x))
        if self.pages.currentIndex() == 0:
            self._tick_setup(item)
        else:
            self._tick_progress(item)

    def _tick_setup(self, item):
        if item.filename and not self.name_edit.text():
            self.name_edit.setPlaceholderText(item.filename)
        if item.needs_login:
            self._show_login(focus=not self.login_user.text())
            self.live.setText("This server asks for a login: enter it and press Start.")
        elif item.status == Status.ERROR:
            self.live.setText(f"Problem: {item.error}")
        elif item.status == Status.COMPLETED:
            self.live.setText(f"Already finished ({human_size(item.total)}). Press Start to keep it.")
        elif item.downloaded:
            got = f"{human_size(item.downloaded)} of {human_size(item.total)}" if item.total > 0 \
                else human_size(item.downloaded)
            speed = human_speed(item.speed)
            self.live.setText(f"Already downloading  ·  {got}" + (f"  ·  {speed}" if speed else ""))
        else:
            self.live.setText("Connecting…")

    def _tick_progress(self, item):
        f = self.facts
        f["Downloaded"].setText(f"{human_size(item.downloaded)}  ({item.progress * 100:.0f}%)"
                                if item.total > 0 else human_size(item.downloaded))
        running = item.status == Status.DOWNLOADING
        f["Speed"].setText((human_speed(item.speed) if running else "") or "—")
        f["Time left"].setText((human_eta(item.eta) if running else "") or "—")
        active = sum(1 for s in item.segments if s.active)
        if not self.conn_spin.hasFocus() and self.conn_spin.value() != item.connections:
            self.conn_spin.blockSignals(True)
            self.conn_spin.setValue(min(32, item.connections))
            self.conn_spin.blockSignals(False)
        self.conn_spin.setEnabled(not item.status == Status.COMPLETED and (item.resumable or item.kind == "video"
                                                                           or item.total < 0))
        working = f"{active} working" if running and item.kind == "file" else ""
        self.conn_active.setText(" · ".join(x for x in ("auto" if item.auto_connections else "", working) if x))
        self.conn_active.setToolTip("YDM is picking the count: it adds connections while they add speed"
                                    if item.auto_connections else "")
        fix = "Renew address" if item.expired and item.kind == "file" else "Log in…" if item.needs_login else ""
        self.btn_fix.setText(fix)
        self.btn_fix.setVisible(bool(fix))
        done = item.status == Status.COMPLETED
        self.btn_open.setVisible(done)
        self.btn_pause.setVisible(not done)
        self.btn_hide.setText("Close" if done else "Hide")
        if done:
            self.live.setText("Download complete.")
            self.live.setStyleSheet(f"color: {theme.INK['Completed']};")
        elif item.status == Status.ERROR:
            self.live.setText(f"Stopped: {item.error}")
            self.live.setStyleSheet(f"color: {theme.INK['Error']};")
        else:
            self.live.setText(item.status if item.status != Status.DOWNLOADING else "")
            self.live.setStyleSheet("")
        paused = item.status in (Status.PAUSED, Status.ERROR)
        self.btn_pause.setText(" Resume" if paused else " Pause")
        icons.put(self.btn_pause, "resume" if paused else "pause", "text", 16)
        for b in (self.btn_later_p, self.btn_cancel):
            b.setVisible(not done)
        self.btn_with.setVisible(done)
        self.btn_save.setVisible(item.status != Status.PROCESSING)
        fm = QFontMetrics(self.addr.font())
        self.addr.setText(fm.elidedText(item.url, Qt.ElideMiddle, max(120, self.addr.width())))
        self.addr.setToolTip(item.url)

    # ------------------------------------------------------------- lifecycle
    def showEvent(self, event):
        theme.apply_window_chrome(self)
        super().showEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.timer.stop()
        self._drop_prestart()           # closed without confirming: cancel and clean up
        self.closed.emit(self)
        super().closeEvent(event)


def _elide(label: QLabel, text: str, width: int) -> str:
    return QFontMetrics(label.font()).elidedText(text, Qt.ElideMiddle, width)


