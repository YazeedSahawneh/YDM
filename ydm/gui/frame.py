"""YDM's own window edges.

YDM draws the title strip itself (icon, title, menus, its own minimise / maximise / close), while
Windows keeps everything else about the frame: the drop shadow, rounded corners on Windows 11,
Snap, resizing from every edge, the minimise/maximise animations and Alt+Space. The window keeps
its native frame styles; WM_NCCALCSIZE hands the whole window to the client area and
WM_NCHITTEST tells Windows which parts of our title bar drag the window and where the resize
edges are. Elsewhere (the Linux test runs) windows keep the system frame and the title bar only
hosts the menus.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QSizePolicy, QWidget

from . import theme

NATIVE = sys.platform == "win32"
EDGE = 6                     # resize border inside the window, logical px

if NATIVE:
    import ctypes
    from ctypes import wintypes

    user32, dwmapi = ctypes.windll.user32, ctypes.windll.dwmapi
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)

    class _NCCALCSIZE_PARAMS(ctypes.Structure):
        _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    GWL_STYLE = -16
    WS_CAPTION, WS_SYSMENU, WS_THICKFRAME = 0x00C00000, 0x00080000, 0x00040000
    WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00020000, 0x00010000
    SWP_FRAMECHANGED = 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010   # + NOSIZE, NOMOVE, NOZORDER, NOACTIVATE
    WM_NCCALCSIZE, WM_NCHITTEST = 0x0083, 0x0084
    HTCLIENT, HTCAPTION = 1, 2
    HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 10, 11, 12, 13, 14, 15, 16, 17


class CaptionButton(QAbstractButton):
    """Minimise / maximise / close, drawn like Windows 11's (thin glyphs, red close)."""

    def __init__(self, kind: str, height: int, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(46, height)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setToolTip({"min": "Minimise", "max": "Maximise", "close": "Close"}[kind])

    def event(self, e):
        if e.type() in (QEvent.HoverEnter, QEvent.HoverLeave):
            self.update()
        return super().event(e)

    def paintEvent(self, _event):
        p = QPainter(self)
        win = self.window()
        hot, down = self.underMouse(), self.isDown()
        if self.kind == "close" and (hot or down):
            p.fillRect(self.rect(), QColor("#A3241A" if down else "#C42B1C"))
            fg = QColor("#FFFFFF")
        else:
            if hot or down:
                p.fillRect(self.rect(), QColor(theme.LINE if down else theme.RAISED))
            fg = QColor(theme.TEXT if win.isActiveWindow() else theme.MUTED)
        pen = QPen(fg, 1.0)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        c = QPointF(self.width() / 2, self.height() / 2)
        s = 5.0
        if self.kind == "min":
            p.drawLine(QPointF(c.x() - s, c.y() + 0.5), QPointF(c.x() + s, c.y() + 0.5))
        elif self.kind == "max" and win.isMaximized():       # restore: two overlapping squares
            p.drawRect(QRectF(c.x() - s + 0.5, c.y() - s + 2.5, 8, 8))
            p.drawPolyline([QPointF(c.x() - s + 2.5, c.y() - s + 2.5), QPointF(c.x() - s + 2.5, c.y() - s + 0.5),
                            QPointF(c.x() + s + 0.5, c.y() - s + 0.5), QPointF(c.x() + s + 0.5, c.y() + s - 2.5),
                            QPointF(c.x() + s - 1.5, c.y() + s - 2.5)])
        elif self.kind == "max":
            p.drawRect(QRectF(c.x() - s + 0.5, c.y() - s + 0.5, 2 * s, 2 * s))
        else:
            p.setRenderHint(QPainter.Antialiasing)
            p.drawLine(QPointF(c.x() - s, c.y() - s), QPointF(c.x() + s, c.y() + s))
            p.drawLine(QPointF(c.x() + s, c.y() - s), QPointF(c.x() - s, c.y() + s))
        p.end()


class TitleBar(QWidget):
    """Icon, title (or menus) and the caption buttons. Empty parts of it drag the window."""

    def __init__(self, window: QWidget, *, height: int = 34, maximize: bool = True, minimize: bool = True,
                 ground: str = "PANEL", show_title: bool = True):
        super().__init__(window)
        self.win, self.ground = window, ground
        self.setFixedHeight(height)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 0, 0)
        lay.setSpacing(8)
        self.icon = QLabel()
        self.icon.setPixmap(theme.app_icon().pixmap(16, 16))
        self.label = QLabel(window.windowTitle() if show_title else "")
        self.label.setObjectName("captionTitle")
        self.slot = QHBoxLayout()
        self.slot.setSpacing(0)
        lay.addWidget(self.icon)
        lay.addLayout(self.slot)
        lay.addWidget(self.label, 1)
        self.buttons: list[CaptionButton] = []
        for kind, on in (("min", minimize), ("max", maximize), ("close", True)):
            if on and NATIVE:
                b = CaptionButton(kind, height, self)
                b.clicked.connect({"min": window.showMinimized, "max": self.toggle_maximized,
                                   "close": window.close}[kind])
                lay.addWidget(b, 0, Qt.AlignTop)
                self.buttons.append(b)
        if show_title:
            window.windowTitleChanged.connect(self.label.setText)

    def add(self, widget: QWidget) -> None:
        """Put a widget (the menu bar) next to the icon. It keeps its own width, so the rest of the
        bar stays free to drag the window by."""
        box = QWidget(self)                   # a QMenuBar stretches itself to its parent's width: give it its own
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(widget)
        box.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.slot.addWidget(box)

    def toggle_maximized(self):
        self.win.showNormal() if self.win.isMaximized() else self.win.showMaximized()

    def drags(self, pos: QPoint) -> bool:
        """Does a press at pos (in this bar) move the window?"""
        child = self.childAt(pos)
        return child is None or child in (self.icon, self.label)

    def mouseDoubleClickEvent(self, event):              # Windows does this itself (HTCAPTION)
        if not NATIVE and self.drags(event.position().toPoint()) and any(b.kind == "max" for b in self.buttons):
            self.toggle_maximized()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(getattr(theme, self.ground)))
        p.end()
        active = self.win.isActiveWindow()
        color = theme.MUTED if active else theme.FAINT
        if self.label.property("ink") != color:
            self.label.setProperty("ink", color)
            self.label.setStyleSheet(f"color: {color};")

    def restyle(self):
        self.icon.setPixmap(theme.app_icon().pixmap(16, 16))
        self.update()
        for b in self.buttons:
            b.update()


class Frameless:
    """Mixin for a top-level widget (first base): `install()` gives it YDM's title bar."""

    _ydm_bar: TitleBar | None = None
    _ydm_overlay = False
    _ydm_resizable = True

    def install_title_bar(self, bar: TitleBar, *, overlay: bool, resizable: bool = True) -> None:
        """overlay=True: the bar floats over the top of the widget and the layout starts below it
        (download window, dialogs). False: the caller places it (QMainWindow.setMenuWidget)."""
        self._ydm_bar, self._ydm_overlay, self._ydm_resizable = bar, overlay, resizable
        if overlay and not NATIVE:
            bar.hide()                                   # the system frame has the title and buttons
            return
        if overlay:
            m = self.contentsMargins()
            self.setContentsMargins(m.left(), bar.height(), m.right(), m.bottom())
        if NATIVE:
            self.setWindowFlag(Qt.FramelessWindowHint, True)

    # Windows -------------------------------------------------------------------------------
    def _ydm_native_frame(self):
        """Give the frameless window its frame styles back, so Windows still draws the shadow
        and rounded corners and does Snap / resize / animations."""
        if not NATIVE or self._ydm_bar is None:
            return
        hwnd = int(self.winId())
        style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        want = style | WS_CAPTION | WS_SYSMENU | WS_THICKFRAME | WS_MINIMIZEBOX
        if any(b.kind == "max" for b in self._ydm_bar.buttons):
            want |= WS_MAXIMIZEBOX
        if want != style:
            user32.SetWindowLongPtrW(hwnd, GWL_STYLE, want)
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FRAMECHANGED)
        pref = ctypes.c_int(2)                                  # DWMWCP_ROUND
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))

    def nativeEvent(self, event_type, message):
        if NATIVE and self._ydm_bar is not None:
            try:
                result = _hit(self, wintypes.MSG.from_address(int(message)))
            except (OSError, ValueError, TypeError):
                result = None
            if result is not None:
                return True, result
        return super().nativeEvent(event_type, message)

    def showEvent(self, event):
        self._ydm_native_frame()
        if self._ydm_bar is not None and self._ydm_overlay:
            self._ydm_bar.setGeometry(0, 0, self.width(), self._ydm_bar.height())
            self._ydm_bar.raise_()
        super().showEvent(event)

    def resizeEvent(self, event):
        if self._ydm_bar is not None and self._ydm_overlay:
            self._ydm_bar.setGeometry(0, 0, self.width(), self._ydm_bar.height())
        super().resizeEvent(event)

    def changeEvent(self, event):
        if self._ydm_bar is not None and event.type() in (QEvent.ActivationChange, QEvent.WindowStateChange):
            self._ydm_bar.restyle()                     # dim when inactive; max ↔ restore glyph
        super().changeEvent(event)


def _hit(win, msg):
    """The two messages that make the frame ours; None = let Qt/Windows handle it."""
    if msg.message == WM_NCCALCSIZE:
        if msg.wParam and user32.IsZoomed(msg.hWnd):
            # maximised: Windows pushes the frame past the screen edge; keep the window on the work area
            info = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
            if user32.GetMonitorInfoW(user32.MonitorFromWindow(msg.hWnd, 2), ctypes.byref(info)):
                r, w = _NCCALCSIZE_PARAMS.from_address(msg.lParam).rgrc[0], info.rcWork
                r.left, r.top = max(r.left, w.left), max(r.top, w.top)
                r.right, r.bottom = min(r.right, w.right), min(r.bottom, w.bottom)
        return 0                                    # the whole window is client area
    if msg.message != WM_NCHITTEST:
        return None
    x = ctypes.c_short(msg.lParam & 0xFFFF).value
    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
    rect = wintypes.RECT()
    user32.GetWindowRect(msg.hWnd, ctypes.byref(rect))
    lx, ly = x - rect.left, y - rect.top
    w, h = rect.right - rect.left, rect.bottom - rect.top
    dpr = win.devicePixelRatioF() or 1.0
    if win._ydm_resizable and not user32.IsZoomed(msg.hWnd):
        e = round(EDGE * dpr)
        left, right, top, bottom = lx < e, lx >= w - e, ly < e, ly >= h - e
        if top and left:
            return HTTOPLEFT
        if top and right:
            return HTTOPRIGHT
        if bottom and left:
            return HTBOTTOMLEFT
        if bottom and right:
            return HTBOTTOMRIGHT
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
        if bottom:
            return HTBOTTOM
        if top and not (win._ydm_bar.buttons and _over_button(win, lx / dpr, ly / dpr)):
            return HTTOP
    bar = win._ydm_bar
    if bar.isVisible():
        p = bar.mapFrom(win, QPoint(int(lx / dpr), int(ly / dpr)))
        if bar.rect().contains(p):
            return HTCAPTION if bar.drags(p) else HTCLIENT
    return HTCLIENT


def _over_button(win, x: float, y: float) -> bool:
    child = win.childAt(QPoint(int(x), int(y)))
    return isinstance(child, CaptionButton)
