"""Dark theme + programmatically drawn app icon. Change ACCENT in config.py."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

from ..config import ACCENT

BG = "#0F141B"
SURFACE = "#161D27"
SURFACE_2 = "#1E2733"
BORDER = "#2A3442"
ALT_ROW = "#121821"
TEXT = "#E6EAF0"
MUTED = "#8A96A8"
SUCCESS = "#22C55E"
WARNING = "#F59E0B"
DANGER = "#EF4444"

STATUS_COLORS = {
    "Downloading": ACCENT, "Connecting": ACCENT, "Processing": "#A855F7",
    "Completed": SUCCESS, "Paused": WARNING, "Queued": MUTED, "Error": DANGER,
}

QSS = f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolBar {{ background: {BG}; border: none; spacing: 6px; padding: 8px 10px; }}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 4px 6px; }}
QToolButton {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
              padding: 6px 12px; }}
QToolButton:hover {{ border-color: {ACCENT}; }}
QToolButton:pressed {{ background: {ACCENT}; }}
QToolButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; font-weight: 600; }}
QListWidget#sidebar {{ background: {SURFACE}; border: none; border-right: 1px solid {BORDER};
                       padding: 8px 6px; outline: 0; }}
QListWidget#sidebar::item {{ padding: 7px 10px; border-radius: 6px; margin: 1px 0; }}
QListWidget#sidebar::item:selected {{ background: {SURFACE_2}; color: {TEXT};
                                     border-left: 3px solid {ACCENT}; }}
QListWidget#sidebar::item:hover:!selected {{ background: {SURFACE_2}; }}
QTableView {{ background: {BG}; border: none; gridline-color: transparent;
              selection-background-color: {SURFACE_2}; selection-color: {TEXT};
              alternate-background-color: {ALT_ROW}; outline: 0; }}
QTableView::item {{ padding: 4px 8px; border-bottom: 1px solid {SURFACE}; }}
QHeaderView::section {{ background: {BG}; color: {MUTED}; border: none;
                        border-bottom: 1px solid {BORDER}; padding: 8px; font-weight: 600; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
QStatusBar QLabel {{ color: {MUTED}; padding: 2px 8px; }}
QLineEdit, QSpinBox, QComboBox, QTimeEdit, QPlainTextEdit {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 8px; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTimeEdit:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; selection-background-color: {ACCENT}; }}
QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px;
               padding: 7px 16px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; font-weight: 600; }}
QPushButton:disabled {{ color: {MUTED}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px;
                        border: 1px solid {BORDER}; background: {SURFACE}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 8px 14px; color: {MUTED}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#title {{ font-size: 13pt; font-weight: 600; }}
"""


def make_icon(size: int = 256) -> QPixmap:
    """Rounded tile with a gradient and a bold down-arrow."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92)
    grad = QLinearGradient(r.topLeft(), r.bottomRight())
    grad.setColorAt(0, QColor(ACCENT).lighter(125))
    grad.setColorAt(1, QColor(ACCENT).darker(140))
    p.setBrush(grad)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(r, size * 0.22, size * 0.22)
    pen = QPen(QColor("white"), size * 0.09, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    c = size / 2
    path = QPainterPath(QPointF(c, size * 0.24))
    path.lineTo(c, size * 0.60)
    p.drawPath(path)
    arrow = QPainterPath(QPointF(size * 0.33, size * 0.45))
    arrow.lineTo(c, size * 0.62)
    arrow.lineTo(size * 0.67, size * 0.45)
    p.drawPath(arrow)
    p.drawLine(QPointF(size * 0.28, size * 0.76), QPointF(size * 0.72, size * 0.76))
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(make_icon(s))
    return icon
