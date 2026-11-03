from __future__ import annotations

import re
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QRectF, QRunnable, QSize, QThreadPool, QTime, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QRadioButton, QSpinBox, QStackedWidget, QTimeEdit, QVBoxLayout, QWidget)

from .. import tools, updater, vault
from ..config import APP_NAME, APP_VERSION, UPDATE_REPO, Settings
from ..engine import Status
from ..engine.auth import LOGINS
from ..utils import human_size
from . import hudbar, icons, theme

VIDEO_SITES = ("youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "instagram.com", "facebook.com",
               "fb.watch", "x.com", "twitter.com", "dailymotion.com", "twitch.tv", "reddit.com",
               "soundcloud.com", "bilibili.com")
URL_RE = re.compile(r"(?:https?|ftps?)://[^\s\"'<>]+", re.I)


def looks_like_video_page(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == s or host.endswith("." + s) for s in VIDEO_SITES)


def find_urls(text: str) -> list[str]:
    seen, out = set(), []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(").,;]")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


class Dialog(QDialog):
    """Base: dark title bar, consistent margins."""

    def showEvent(self, event):
        theme.apply_window_chrome(self)
        super().showEvent(event)


def _hairline() -> QFrame:
    f = QFrame()
    f.setObjectName("hairline")
    f.setFrameShape(QFrame.NoFrame)
    return f


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


# ------------------------------------------------------------------- batch add
class BatchDialog(Dialog):
    """Several links at once (pasted text, dropped text, a list from a page)."""

    def __init__(self, urls: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add several downloads")
        self.setMinimumSize(620, 420)
        title = QLabel(f"{len(urls)} links found")
        title.setObjectName("title")
        self.list = QListWidget()
        self.list.setObjectName("choices")
        for u in urls:
            it = QListWidgetItem(u)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.list.addItem(it)
        self.start_now = True
        later = QPushButton("Add to queue")
        now = QPushButton("Start all")
        now.setObjectName("primary")
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        later.clicked.connect(lambda: self._done(False))
        now.clicked.connect(lambda: self._done(True))
        row = QHBoxLayout()
        row.addStretch(1)
        for b in (cancel, later, now):
            row.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(12)
        lay.addWidget(title)
        hint = QLabel("Untick anything you don't want. They download in order, a few at a time.")
        hint.setObjectName("muted")
        lay.addWidget(hint)
        lay.addWidget(self.list, 1)
        lay.addLayout(row)

    def _done(self, start: bool):
        self.start_now = start
        self.accept()

    def urls(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


# ----------------------------------------------------------------- login
class LoginDialog(Dialog):
    """User name and password for a server that asks (HTTP Basic/Digest/NTLM or FTP)."""

    def __init__(self, host: str = "", user: str = "", parent=None, ask_host: bool = False,
                 note: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Login")
        self.setMinimumWidth(420)
        title = QLabel(f"Log in to {host}" if host and not ask_host else "Site login")
        title.setObjectName("title")
        sub = QLabel(note or "YDM answers Basic, Digest and NTLM logins, and FTP. "
                             "For Windows domains write DOMAIN\\user.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        self.host = QLineEdit(host)
        self.host.setPlaceholderText("example.com (also covers its subdomains)")
        self.user = QLineEdit(user)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.remember = QCheckBox("Remember for this site")
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        if ask_host:
            form.addRow("Site", self.host)
        form.addRow("User name", self.user)
        form.addRow("Password", self.password)
        if not ask_host:
            form.addRow("", self.remember)
        ok = QPushButton("Save" if ask_host else "Log in")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(10)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addLayout(form)
        lay.addWidget(_hairline())
        lay.addLayout(row)
        (self.user if not user else self.password).setFocus()

    def values(self) -> tuple[str, str, str, bool]:
        return (self.host.text().strip().lower(), self.user.text().strip(), self.password.text(),
                self.remember.isChecked())


# ------------------------------------------------------------- renew address
class RenewDialog(Dialog):
    """The link of an unfinished download stopped working: get a fresh one, keep the progress."""

    def __init__(self, name: str, page: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Renew download address")
        self.setMinimumWidth(500)
        self.choice = ""
        title = QLabel("Renew the address")
        title.setObjectName("title")
        body = QLabel(f"<b>{name}</b> keeps what it already downloaded. Click its download link again "
                      "in the browser: YDM catches the fresh link, checks it is the same file, and "
                      "carries on from where it stopped.")
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        hint = QLabel("No browser page? Paste the new link instead.")
        hint.setObjectName("muted")
        self.btn_page = QPushButton(icons.icon("open", theme.ON_ACCENT, 16),
                                    " Open the page and wait" if page else " Wait for the link")
        self.btn_page.setObjectName("primary")
        self.btn_page.setDefault(True)
        paste = QPushButton(icons.icon("link", theme.TEXT, 16), " Paste a link…")
        cancel = QPushButton("Cancel")
        self.btn_page.clicked.connect(lambda: self._pick("wait"))
        paste.clicked.connect(lambda: self._pick("paste"))
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(paste)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(self.btn_page)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(10)
        lay.addWidget(title)
        lay.addWidget(body)
        lay.addWidget(hint)
        lay.addWidget(_hairline())
        lay.addLayout(row)

    def _pick(self, choice: str):
        self.choice = choice
        self.accept()


# ------------------------------------------------------- when-done countdown
class CountdownDialog(Dialog):
    def __init__(self, action: str, seconds: int = 30, parent=None):
        super().__init__(parent)
        self.action, self.left = action, seconds
        self.setWindowTitle(APP_NAME)
        self.label = QLabel()
        self.label.setObjectName("title")
        sub = QLabel("All downloads have finished.")
        sub.setObjectName("muted")
        cancel = QPushButton("Cancel")
        now = QPushButton(f"{action} now")
        now.setObjectName("primary")
        cancel.clicked.connect(self.reject)
        now.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(now)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.addWidget(sub)
        lay.addWidget(self.label)
        lay.addLayout(row)
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._tick(first=True)

    def _tick(self, first: bool = False):
        if not first:
            self.left -= 1
        if self.left <= 0:
            self.timer.stop()
            self.accept()
            return
        self.label.setText(f"{self.action} in {self.left} s")


# ---------------------------------------------------------------- settings dialog
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
PAGES = ["General", "Connection", "Scheduler", "Browser", "Video", "Proxy & logins", "Appearance", "Updates"]
PROXY_MODES = [("none", "No proxy"), ("system", "Use the Windows proxy settings"),
               ("http", "HTTP proxy"), ("socks5", "SOCKS5 proxy")]


class _Swatch(QPushButton):
    """A theme preset: the accent as a disc, the meter colour as a bar under it."""

    def __init__(self, name: str, accent: str, signal: str):
        super().__init__()
        self.name, self.accent, self.signal = name, accent, signal
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(name)
        self.setFixedSize(QSize(46, 46))
        self.setStyleSheet("QPushButton { background: transparent; border: 1px solid transparent; border-radius: 8px; }"
                           f"QPushButton:hover {{ border-color: {theme.LINE}; }}")

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.isChecked():
            p.setPen(QColor(theme.TEXT))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.accent))
        p.drawEllipse(QRectF(11, 7, 24, 24))
        p.setBrush(QColor(self.signal))
        p.drawRoundedRect(QRectF(11, 35, 24, 4), 2, 2)
        p.end()


class _BarPreview(QWidget):
    """The progress bar at a few points, in the colours being chosen."""

    STOPS = [(0.08, Status.DOWNLOADING), (0.42, Status.DOWNLOADING), (0.78, Status.DOWNLOADING),
             (0.55, Status.PAUSED), (1.0, Status.COMPLETED)]

    def __init__(self):
        super().__init__()
        self.setFixedHeight(24 * len(self.STOPS))
        self._font = theme.number_font(8.5)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setFont(self._font)
        for i, (frac, status) in enumerate(self.STOPS):
            y = i * 24 + 4
            color = hudbar.draw_hud_bar(p, QRectF(0, y, self.width() - 110, 14), None, fraction=frac, status=status)
            p.setPen(color)
            label = f"{frac * 100:.0f}%" + ("  paused" if status == Status.PAUSED else "")
            p.drawText(QRectF(self.width() - 100, y - 3, 100, 20), Qt.AlignLeft | Qt.AlignVCenter, label)
        p.end()



class SettingsDialog(Dialog):
    install_tools = Signal()
    check_updates = Signal()
    update_ytdlp = Signal()
    theme_preview = Signal(str, str, str)       # accent, signal, bar mode: applied live, undone on Cancel

    def __init__(self, settings: Settings, parent=None, open_tab: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setMinimumSize(720, 470)
        self.s = settings
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(190)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav_icons = ["settings", "speed", "scheduler", "link", "video", "globe", "palette", "update"]
        for name, ic in zip(PAGES, nav_icons):
            QListWidgetItem(icons.icon(ic, theme.MUTED, 18), name, self.nav)
        self.pages = QStackedWidget()

        # General
        g, gf = self._page("General", "Where files go and how YDM behaves.")
        self.dir_edit = QLineEdit(settings.download_dir)
        browse = QPushButton(icons.icon("folder", theme.TEXT, 16), "")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.dir_edit, 1)
        row.addWidget(browse)
        gf.addRow("Download folder", row)
        self.cat_box = QCheckBox("Sort into category folders (Video, Music, Programs…)")
        self.cat_box.setChecked(settings.use_category_folders)
        gf.addRow("", self.cat_box)
        self.tray_box = QCheckBox("Closing the window keeps YDM running in the tray")
        self.tray_box.setChecked(settings.minimize_to_tray)
        gf.addRow("", self.tray_box)
        self.window_box = QCheckBox("Show a small window for each download (progress, then Open / Open folder)")
        self.window_box.setChecked(settings.progress_window)
        gf.addRow("", self.window_box)
        self.notify_box = QCheckBox("Notify when a download finishes")
        self.notify_box.setChecked(settings.notify_on_complete)
        gf.addRow("", self.notify_box)
        self.clip_box = QCheckBox("Offer to download links I copy")
        self.clip_box.setChecked(settings.watch_clipboard)
        gf.addRow("", self.clip_box)

        # Connection
        c, cf = self._page("Connection", "More connections usually means faster, until the server says no. "
                                         "YDM backs off on its own and remembers each server's limit.")
        self.conn = QSpinBox()
        self.conn.setRange(1, 32)
        self.conn.setValue(settings.connections)
        cf.addRow("Connections per download", self.conn)
        self.active = QSpinBox()
        self.active.setRange(1, 10)
        self.active.setValue(settings.max_active)
        cf.addRow("Downloads at the same time", self.active)
        self.limit = QSpinBox()
        self.limit.setRange(0, 1_000_000)
        self.limit.setSuffix(" KB/s")
        self.limit.setSpecialValueText("Unlimited")
        self.limit.setValue(settings.speed_limit_kbps)
        cf.addRow("Speed limit", self.limit)

        # Scheduler
        sc, sf = self._page("Scheduler", "Unfinished and queued downloads start at the start time and pause at "
                                         "the stop time. A window like 23:00 → 07:00 runs overnight.")
        self.sched_box = QCheckBox("Only download queued files inside this time window")
        self.sched_box.setChecked(settings.scheduler_enabled)
        sf.addRow("", self.sched_box)
        self.start_time = QTimeEdit(QTime.fromString(settings.scheduler_start, "HH:mm"))
        self.stop_time = QTimeEdit(QTime.fromString(settings.scheduler_stop, "HH:mm"))
        for w in (self.start_time, self.stop_time):
            w.setDisplayFormat("HH:mm")
        sf.addRow("Start at", self.start_time)
        sf.addRow("Stop at", self.stop_time)
        days_row = QHBoxLayout()
        self.day_boxes = []
        for i, name in enumerate(DAYS):
            b = QCheckBox(name)
            b.setChecked(i in settings.scheduler_days)
            self.day_boxes.append(b)
            days_row.addWidget(b)
        sf.addRow("Days", days_row)

        # Browser
        b, bf = self._page("Browser", "The YDM extension hands browser downloads and videos to this app, "
                                      "so you never copy a link by hand.")
        self.confirm_box = QCheckBox("Ask where to save files sent from the browser (they start downloading meanwhile)")
        self.confirm_box.setChecked(settings.show_add_dialog)
        bf.addRow("", self.confirm_box)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(settings.port)
        bf.addRow("Extension port", self.port)
        how = QLabel("Install it once: open chrome://extensions (or edge://extensions), turn on Developer mode, "
                     "click Load unpacked and pick the extension folder. Start menu → YDM browser extension "
                     "folder opens it.")
        how.setObjectName("muted")
        how.setWordWrap(True)
        bf.addRow(how)

        # Video
        v, vf = self._page("Video", "YDM uses yt-dlp to find the video on a page, then downloads it with its "
                                    "own multi-connection engine.")
        self.tools_status = QLabel()
        self.tools_status.setWordWrap(True)
        vf.addRow("Video tools", self.tools_status)
        tools_btn = QPushButton(icons.icon("tools", theme.TEXT, 16), " Install / repair")
        tools_btn.clicked.connect(self.install_tools.emit)
        ytdlp_btn = QPushButton(icons.icon("refresh", theme.TEXT, 16), " Update yt-dlp now")
        ytdlp_btn.clicked.connect(self.update_ytdlp.emit)
        trow = QHBoxLayout()
        trow.addWidget(tools_btn)
        trow.addWidget(ytdlp_btn)
        trow.addStretch(1)
        vf.addRow("", trow)
        self._refresh_tools()

        # Proxy & logins
        n, nf = self._page("Proxy & logins", "A proxy carries web and video downloads (FTP connects directly). "
                                             "Saved logins answer servers that ask for a user name and password.")
        self.proxy_mode = QComboBox()
        for key, label in PROXY_MODES:
            self.proxy_mode.addItem(label, key)
        self.proxy_mode.setCurrentIndex(max(0, [k for k, _ in PROXY_MODES].index(settings.proxy_mode)
                                            if settings.proxy_mode in dict(PROXY_MODES) else 0))
        nf.addRow("Proxy", self.proxy_mode)
        self.proxy_host = QLineEdit(settings.proxy_host)
        self.proxy_host.setPlaceholderText("proxy.example.com")
        self.proxy_port = QSpinBox()
        self.proxy_port.setRange(1, 65535)
        self.proxy_port.setValue(settings.proxy_port or 8080)
        hp = QHBoxLayout()
        hp.addWidget(self.proxy_host, 1)
        hp.addSpacing(6)
        hp.addWidget(QLabel("Port"))
        hp.addWidget(self.proxy_port)
        nf.addRow("Address", hp)
        self.proxy_user = QLineEdit(settings.proxy_user)
        self.proxy_user.setPlaceholderText("Only if the proxy asks")
        self.proxy_pass = QLineEdit(vault.reveal(settings.proxy_pass))
        self.proxy_pass.setEchoMode(QLineEdit.Password)
        self.proxy_pass.setPlaceholderText("Password")
        up = QHBoxLayout()
        up.addWidget(self.proxy_user, 1)
        up.addWidget(self.proxy_pass, 1)
        nf.addRow("Proxy login", up)
        self.proxy_test = QPushButton(icons.icon("globe", theme.TEXT, 16), " Test")
        self.proxy_test.clicked.connect(self._test_proxy)
        self.proxy_result = QLabel("")
        self.proxy_result.setObjectName("muted")
        tp = QHBoxLayout()
        tp.addWidget(self.proxy_test)
        tp.addWidget(self.proxy_result, 1)
        nf.addRow("", tp)
        self.proxy_mode.currentIndexChanged.connect(self._proxy_fields)
        self._proxy_fields()

        self.logins = QListWidget()
        self.logins.setObjectName("choices")
        self.logins.setFixedHeight(118)
        self._fill_logins()
        add_login = QPushButton(icons.icon("key", theme.TEXT, 16), " Add…")
        add_login.clicked.connect(self._add_login)
        del_login = QPushButton("Remove")
        del_login.clicked.connect(self._remove_login)
        lr = QHBoxLayout()
        lr.addWidget(add_login)
        lr.addWidget(del_login)
        lr.addStretch(1)
        lcol = QVBoxLayout()
        lcol.setSpacing(6)
        lcol.addWidget(self.logins)
        lcol.addLayout(lr)
        nf.addRow("Site logins", lcol)

        # Appearance
        a, af = self._page("Appearance", "Pick the colour of the controls and meters. "
                                         "It changes right away; Cancel puts it back.")
        self._accent, self._signal = settings.accent, settings.signal
        self._saved_theme = (settings.accent, settings.signal, settings.bar_colors)
        sw_row = QHBoxLayout()
        sw_row.setSpacing(4)
        self.swatches = QButtonGroup(self)
        for name, (acc, sig) in theme.PRESETS.items():
            sw = _Swatch(name, acc, sig)
            sw.setChecked(acc.upper() == settings.accent.upper() and sig.upper() == settings.signal.upper())
            sw.clicked.connect(lambda _=False, acc=acc, sig=sig: self._pick_colors(acc, sig))
            self.swatches.addButton(sw)
            sw_row.addWidget(sw)
        sw_row.addStretch(1)
        af.addRow("Theme", sw_row)
        custom = QHBoxLayout()
        self.btn_accent = QPushButton(" Controls colour…")
        self.btn_signal = QPushButton(" Meter colour…")
        self.btn_accent.clicked.connect(lambda: self._custom_color(True))
        self.btn_signal.clicked.connect(lambda: self._custom_color(False))
        custom.addWidget(self.btn_accent)
        custom.addWidget(self.btn_signal)
        custom.addStretch(1)
        af.addRow("Custom", custom)
        self.bar_shift = QRadioButton("Shifts with progress: red → amber → yellow → green")
        self.bar_theme = QRadioButton("Always the meter colour")
        (self.bar_shift if settings.bar_colors != "theme" else self.bar_theme).setChecked(True)
        self.bar_shift.toggled.connect(lambda _: self._emit_theme())
        bar_col = QVBoxLayout()
        bar_col.setSpacing(6)
        bar_col.addWidget(self.bar_shift)
        bar_col.addWidget(self.bar_theme)
        af.addRow("Progress bar", bar_col)
        self.preview = _BarPreview()
        af.addRow("Preview", self.preview)
        self._paint_custom_buttons()

        # Updates
        u, uf = self._page("Updates", "New versions of YDM install themselves: no reinstalling by hand, and "
                                      "downloads in progress carry on after the restart.")
        uf.addRow("Installed version", QLabel(APP_VERSION))
        self.auto_box = QCheckBox("Check for updates every day and download them in the background")
        self.auto_box.setChecked(settings.auto_update)
        uf.addRow("", self.auto_box)
        check = QPushButton(icons.icon("update", theme.TEXT, 16), " Check now")
        check.clicked.connect(self.check_updates.emit)
        check.setEnabled(updater.enabled())
        crow = QHBoxLayout()
        crow.addWidget(check)
        crow.addStretch(1)
        uf.addRow("", crow)
        if not updater.enabled():
            off = QLabel("Update checks are off in this build: set UPDATE_REPO in ydm/config.py to your "
                         "GitHub owner/repo.")
            off.setObjectName("muted")
            off.setWordWrap(True)
            uf.addRow(off)
        elif not updater.can_install():
            src = QLabel(f"Running from source: updates are announced here; install them with git pull. "
                         f"Releases: github.com/{UPDATE_REPO}")
            src.setObjectName("muted")
            src.setWordWrap(True)
            uf.addRow(src)

        save = QPushButton("Save")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns = QHBoxLayout()
        btns.setContentsMargins(22, 12, 22, 16)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(save)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.pages, 1)
        right.addWidget(_hairline())
        right.addLayout(btns)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.nav)
        lay.addLayout(right, 1)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(open_tab)

    # ------------------------------------------------------------ proxy & logins
    def _proxy_fields(self):
        mode = self.proxy_mode.currentData()
        manual = mode in ("http", "socks5")
        for w in (self.proxy_host, self.proxy_port, self.proxy_user, self.proxy_pass):
            w.setEnabled(manual)
        self.proxy_test.setEnabled(mode != "none")
        self.proxy_result.setText("")

    def _test_proxy(self):
        from ..engine import net
        url = net.proxy_url(self.proxy_mode.currentData(), self.proxy_host.text().strip(), self.proxy_port.value(),
                            self.proxy_user.text().strip(), self.proxy_pass.text())
        if not url:
            self.proxy_result.setText("No proxy found in the Windows settings.")
            return
        self.proxy_result.setText("Testing…")

        def probe():
            import time as _t
            t0 = _t.monotonic()
            with net.client(proxy=url, timeout=10) as c:
                r = c.get("https://www.gstatic.com/generate_204")
            return r.status_code, _t.monotonic() - t0

        job = _Job(0, probe)
        job.signals.done.connect(lambda _t, res: self.proxy_result.setText(
            f"Works: answered in {res[1] * 1000:.0f} ms." if res[0] in (200, 204) else f"Proxy answered HTTP {res[0]}."))
        job.signals.failed.connect(lambda _t, msg: self.proxy_result.setText(f"Didn't work: {msg}"))
        self._proxy_job = job
        QThreadPool.globalInstance().start(job)

    def _fill_logins(self):
        self.logins.clear()
        for host, user in LOGINS.all():
            it = QListWidgetItem(icons.icon("key", theme.MUTED, 16), f"{host}    {user}")
            it.setData(Qt.UserRole, host)
            self.logins.addItem(it)
        if not self.logins.count():
            it = QListWidgetItem("None yet. Logins you tick “Remember” on show up here.")
            it.setFlags(Qt.NoItemFlags)
            self.logins.addItem(it)

    def _add_login(self):
        dlg = LoginDialog(parent=self, ask_host=True)
        if dlg.exec():
            host, user, password, _ = dlg.values()
            if host and user:
                LOGINS.save(host, user, password)
                self._fill_logins()

    def _remove_login(self):
        it = self.logins.currentItem()
        if it and it.data(Qt.UserRole):
            LOGINS.remove(it.data(Qt.UserRole))
            self._fill_logins()

    # ---------------------------------------------------------------- appearance
    def _bar_mode(self) -> str:
        return "theme" if self.bar_theme.isChecked() else "shift"

    def _pick_colors(self, accent: str, signal: str):
        self._accent, self._signal = accent, signal
        self._emit_theme()

    def _custom_color(self, accent: bool):
        start = QColor(self._accent if accent else self._signal)
        c = QColorDialog.getColor(start, self, "Controls colour" if accent else "Meter colour")
        if not c.isValid():
            return
        self.swatches.setExclusive(False)           # a custom pick isn't one of the presets
        for b in self.swatches.buttons():
            b.setChecked(False)
        self.swatches.setExclusive(True)
        if accent:
            self._accent = c.name().upper()
        else:
            self._signal = c.name().upper()
        self._emit_theme()

    def _paint_custom_buttons(self):
        self.btn_accent.setIcon(icons.icon("palette", self._accent, 16))
        self.btn_signal.setIcon(icons.icon("speed", self._signal, 16))

    def _emit_theme(self):
        self.theme_preview.emit(self._accent, self._signal, self._bar_mode())
        self._paint_custom_buttons()
        self.preview.update()
        for b in self.swatches.buttons():
            b.update()

    def reject(self):
        if (self._accent, self._signal, self._bar_mode()) != self._saved_theme:
            self.theme_preview.emit(*self._saved_theme)
        super().reject()

    def _page(self, title: str, blurb: str) -> tuple[QWidget, QFormLayout]:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(26, 22, 26, 10)
        outer.setSpacing(6)
        t = QLabel(title)
        t.setObjectName("title")
        d = QLabel(blurb)
        d.setObjectName("muted")
        d.setWordWrap(True)
        form = QFormLayout()
        form.setContentsMargins(0, 14, 0, 0)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        outer.addWidget(t)
        outer.addWidget(d)
        outer.addLayout(form)
        outer.addStretch(1)
        self.pages.addWidget(w)
        return w, form

    def _refresh_tools(self):
        missing = tools.missing_for_video()
        if not missing:
            ff = tools.ffmpeg_path() or "on PATH"
            rt = next(iter(tools.js_runtimes()), "none")
            self.tools_status.setText(f"Ready  ·  FFmpeg {ff}  ·  JavaScript: {rt}")
        else:
            names = {"ffmpeg": "FFmpeg", "deno": "Deno"}
            self.tools_status.setText("Missing: " + ", ".join(names.get(m, m) for m in missing)
                                      + ". Without them videos stop at 720p and YouTube may fail.")

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
        s.auto_update = self.auto_box.isChecked()
        s.progress_window = self.window_box.isChecked()
        s.proxy_mode = self.proxy_mode.currentData()
        s.proxy_host = self.proxy_host.text().strip()
        s.proxy_port = self.proxy_port.value()
        s.proxy_user = self.proxy_user.text().strip()
        s.proxy_pass = vault.protect(self.proxy_pass.text())
        s.accent, s.signal, s.bar_colors = self._accent, self._signal, self._bar_mode()
        return s
