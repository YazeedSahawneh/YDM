"""Look of YDM: a studio-rack hi-fi panel.

Graphite anodised ground, warm-white silk-screen labels, tabular numbers.
Signal teal lights the meters (bytes arriving). Amber is reserved for things
you can press. Every status owns one ink and keeps it everywhere.
Branding colours live in config.py (ACCENT, SIGNAL).
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap

from ..config import ACCENT, SIGNAL

# Colour presets for Options → Appearance: (accent for pressable things, signal for meters)
PRESETS = {
    "Amber": ("#FFB547", "#3DDBC0"),
    "Ice": ("#7CC8FF", "#5CE1E6"),
    "Saber": ("#5AA9FF", "#9EF0FF"),
    "Violet": ("#B69CFF", "#5CE1C6"),
    "Rose": ("#FF7AA8", "#62D9F0"),
    "Lime": ("#C3F25A", "#3DDBC0"),
    "Ember": ("#FF8A4C", "#FFD166"),
    "Silver": ("#D9D4CA", "#9FD8E6"),
}

BG = "#121417"          # graphite ground (lists, dialogs)
PANEL = "#191C20"       # brushed panel (toolbar, sidebar, details)
RAISED = "#22262B"      # inputs, hover
LINE = "#2C3137"        # hairlines
TEXT = "#ECE8E1"        # warm white silk-screen
MUTED = "#969BA2"
FAINT = "#5E646C"
UNLIT = "#262B31"       # meter block with nothing in it
SELECT = "#2B2721"      # selected row: accent-tinted graphite (recomputed by set_colors)
ON_ACCENT = "#1A1408"   # text on the accent
ACCENT_HOVER = "#FFC266"
ACCENT_DOWN = "#E9A030"

# Fixed state inks (list, sidebar, details pane, tray all use these)
INK = {
    "Downloading": SIGNAL,
    "Connecting": "#2E9C8A",
    "Processing": "#B49CFF",
    "Paused": "#9AA7B8",
    "Queued": "#7A828C",
    "Completed": "#A6E35A",
    "Error": "#FF6B5E",
}
STATUS_COLORS = INK      # older name

FONT_FAMILY = "Segoe UI"


def label_font(size: float = 7.5, weight: QFont.Weight = QFont.DemiBold) -> QFont:
    """Small-caps panel legend: uppercase, tracked."""
    f = QFont(FONT_FAMILY)
    f.setPointSizeF(size)
    f.setWeight(weight)
    f.setCapitalization(QFont.AllUppercase)
    f.setLetterSpacing(QFont.AbsoluteSpacing, 0.9)
    return f


def number_font(size: float = 9.5, weight: QFont.Weight = QFont.Normal) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setPointSizeF(size)
    f.setWeight(weight)
    if hasattr(QFont, "Tag"):                       # Qt 6.7+: force tabular figures
        f.setFeature(QFont.Tag("tnum"), 1)
    return f


def _mix(a: str, b: str, t: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t), round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


def set_colors(accent: str, signal: str) -> None:
    """Switch the theme colours; call before (re)applying build_qss()."""
    global ACCENT, SIGNAL, SELECT, ON_ACCENT, ACCENT_HOVER, ACCENT_DOWN, QSS
    if not QColor(accent).isValid() or not QColor(signal).isValid():
        return
    ACCENT, SIGNAL = QColor(accent).name().upper(), QColor(signal).name().upper()
    SELECT = _mix("#1F2226", ACCENT, 0.075)
    light = QColor(ACCENT).lightnessF() > 0.55
    ON_ACCENT = _mix("#0E0F11", ACCENT, 0.12) if light else "#FFFFFF"
    ACCENT_HOVER = QColor(ACCENT).lighter(112).name()
    ACCENT_DOWN = QColor(ACCENT).darker(112).name()
    INK["Downloading"] = SIGNAL
    INK["Connecting"] = QColor(SIGNAL).darker(140).name()
    QSS = build_qss()


def progress_color(fraction: float, status: str = "Downloading", mode: str = "shift") -> QColor:
    """Colour of the progress bar: red at 0 %, through amber and yellow, to green at 100 %
    ("shift"), or the theme's signal colour ("theme"). Status overrides: failed is red,
    finished is the finished ink, paused/queued are dimmed."""
    if status == "Error":
        return QColor(INK["Error"])
    if status == "Completed":
        return QColor(INK["Completed"])
    if mode == "shift":
        f = max(0.0, min(1.0, fraction))
        c = QColor.fromHsvF((4 + 104 * f) / 360, 0.74 - 0.08 * f, 1.0)
    else:
        c = QColor(SIGNAL)
    if status in ("Paused", "Queued"):
        c = QColor.fromHsvF(c.hsvHueF() if c.hsvHueF() >= 0 else 0, c.hsvSaturationF() * 0.35,
                            c.valueF() * 0.72)
    return c


def build_qss() -> str:
    return f"""
* {{ font-family: "{FONT_FAMILY}", "Segoe UI", sans-serif; font-size: 9.5pt; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#panel {{ background: {PANEL}; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {LINE}; padding: 5px 8px; }}

QMenuBar {{ background: {PANEL}; border-bottom: 1px solid {LINE}; padding: 2px 4px; }}
QMenuBar::item {{ background: transparent; padding: 5px 10px; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {RAISED}; }}
QMenu {{ background: {PANEL}; border: 1px solid {LINE}; padding: 5px; }}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {RAISED}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::indicator {{ width: 12px; height: 12px; margin-left: 8px; }}

QToolBar {{ background: {PANEL}; border: none; border-bottom: 1px solid {LINE};
            spacing: 2px; padding: 6px 8px; }}
QToolBar::separator {{ background: {LINE}; width: 1px; margin: 8px 6px; }}
QToolBar QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px;
                        padding: 5px 7px 4px 7px; color: {TEXT}; font-size: 8.5pt; min-width: 54px; }}
QToolBar QToolButton:hover {{ background: {RAISED}; border-color: {LINE}; }}
QToolBar QToolButton:pressed {{ background: {LINE}; }}
QToolBar QToolButton:disabled {{ color: {FAINT}; }}
QToolBar QToolButton#primary {{ color: {ACCENT}; font-weight: 600; }}

QTreeWidget#sidebar {{ background: {PANEL}; border: none; border-right: 1px solid {LINE};
                       padding: 8px 6px; outline: 0; }}
QTreeWidget#sidebar::item {{ padding: 5px 4px; margin: 1px 0; }}
QTreeWidget#sidebar::item:selected {{ background: {SELECT}; color: {TEXT}; }}
QTreeWidget#sidebar::item:hover:!selected {{ background: {RAISED}; }}
QTreeWidget#sidebar::branch {{ background: transparent; }}

QTableView {{ background: {BG}; border: none; gridline-color: transparent; outline: 0;
              selection-background-color: {SELECT}; selection-color: {TEXT}; }}
QTableView::item {{ padding: 0 8px; border-bottom: 1px solid #191C20; }}
QTableView::item:selected {{ background: {SELECT}; }}
QHeaderView {{ background: {BG}; }}
QHeaderView::section {{ background: {BG}; color: {MUTED}; border: none; border-bottom: 1px solid {LINE};
                        padding: 7px 8px; }}
QHeaderView::section:hover {{ color: {TEXT}; }}

QSplitter::handle {{ background: {LINE}; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {LINE}; }}
QStatusBar QLabel {{ color: {MUTED}; padding: 2px 8px; }}
QStatusBar::item {{ border: none; }}

QLineEdit, QSpinBox, QComboBox, QTimeEdit, QPlainTextEdit {{
    background: {RAISED}; border: 1px solid {LINE}; border-radius: 6px; padding: 6px 8px;
    selection-background-color: {ACCENT}; selection-color: {ON_ACCENT}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTimeEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled {{ color: {FAINT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {LINE}; outline: 0;
                               selection-background-color: {RAISED}; selection-color: {TEXT}; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QTimeEdit::up-button, QTimeEdit::down-button {{ width: 16px; border: none; }}

QPushButton {{ background: {RAISED}; border: 1px solid {LINE}; border-radius: 6px; padding: 7px 16px; }}
QPushButton:hover {{ border-color: #3A4048; background: #272C32; }}
QPushButton:pressed {{ background: {LINE}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: {ON_ACCENT}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_DOWN}; }}
QPushButton#flat {{ background: transparent; border-color: transparent; color: {ACCENT}; padding: 4px 8px; }}
QPushButton#flat:hover {{ background: {RAISED}; }}
QPushButton:disabled {{ color: {FAINT}; background: {PANEL}; border-color: {LINE}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid #3A4048; background: {RAISED}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: none; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 7px; border: 1px solid #3A4048; background: {RAISED}; }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border: 3px solid {RAISED}; }}

QListWidget#nav {{ background: {PANEL}; border: none; border-right: 1px solid {LINE}; padding: 10px 6px; outline: 0; }}
QListWidget#nav::item {{ padding: 8px 12px; border-radius: 5px; margin: 1px 0; }}
QListWidget#nav::item:selected {{ background: {SELECT}; color: {TEXT}; }}
QListWidget#nav::item:hover:!selected {{ background: {RAISED}; }}
QListWidget#choices {{ background: {BG}; border: 1px solid {LINE}; border-radius: 6px; outline: 0; padding: 4px; }}
QListWidget#choices::item {{ padding: 7px 10px; border-radius: 4px; }}
QListWidget#choices::item:selected {{ background: {SELECT}; color: {TEXT}; }}
QListWidget#choices::item:hover:!selected {{ background: {RAISED}; }}

QTableWidget#connections {{ background: {PANEL}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #343A42; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #454C55; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #343A42; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QProgressBar {{ background: {UNLIT}; border: none; border-radius: 2px; height: 4px; color: transparent; }}
QProgressBar::chunk {{ background: {SIGNAL}; border-radius: 2px; }}

QLabel#muted {{ color: {MUTED}; }}
QLabel#title {{ font-size: 13pt; font-weight: 600; }}
QLabel#legend {{ color: {MUTED}; }}
QLabel#error {{ color: {INK["Error"]}; }}
QFrame#banner {{ background: #1E2226; border-bottom: 1px solid {LINE}; }}
QFrame#hairline {{ background: {LINE}; max-height: 1px; border: none; }}
"""


QSS = build_qss()


def apply_window_chrome(widget) -> None:
    """Windows 10/11: dark title bar in the panel colour, matching the app."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))      # immersive dark mode
        c = QColor(PANEL)
        colorref = ctypes.c_int(c.red() | (c.green() << 8) | (c.blue() << 16))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref), ctypes.sizeof(colorref))  # caption colour (Win11)
    except (OSError, AttributeError):
        pass


def make_icon(size: int = 256) -> QPixmap:
    """Graphite tile with a down-arrow built from lit meter blocks."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92)
    grad = QLinearGradient(r.topLeft(), r.bottomLeft())
    grad.setColorAt(0, QColor("#2A2F35"))
    grad.setColorAt(1, QColor("#16191D"))
    p.setBrush(grad)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(r, size * 0.22, size * 0.22)
    signal = QColor(SIGNAL)
    u = size / 16
    # stem: three stacked blocks, then the arrow head as two diagonal rows of blocks
    blocks = [(7, 2.2), (7, 4.4), (7, 6.6), (3.2, 7.4), (10.8, 7.4), (5.1, 9.2), (8.9, 9.2), (7, 11.0)]
    for i, (bx, by) in enumerate(blocks):
        c = QColor(signal)
        c.setAlphaF(0.55 + 0.45 * (i / (len(blocks) - 1)))
        p.setBrush(c)
        p.drawRoundedRect(QRectF(bx * u, by * u, 2 * u, 1.6 * u), u * 0.35, u * 0.35)
    p.setBrush(QColor(ACCENT))
    p.drawRoundedRect(QRectF(4 * u, 13.2 * u, 8 * u, 0.9 * u), u * 0.45, u * 0.45)
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(make_icon(s))
    return icon


def ink(status: str) -> QColor:
    return QColor(INK.get(status, TEXT))
