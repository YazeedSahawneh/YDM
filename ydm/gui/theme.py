"""Look of YDM: a studio-rack hi-fi panel.

Graphite anodised ground, warm-white silk-screen labels, tabular numbers.
Signal teal lights the meters (bytes arriving). Amber is reserved for things
you can press. Every status owns one ink and keeps it everywhere.
Branding colours live in config.py (ACCENT, SIGNAL); the user can change them and
switch between the dark panel and a light one (set_mode / set_colors). Code reads
the tokens from this module at paint time, so a switch reaches everything.
"""
from __future__ import annotations

import math
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

# Two grounds. Dark: graphite anodised panel. Light: warm paper-white panel, same layout.
MODES = {
    "dark": {
        "BG": "#121417",        # graphite ground (lists, dialogs)
        "PANEL": "#191C20",     # brushed panel (toolbar, sidebar, details)
        "RAISED": "#22262B",    # inputs, hover
        "LINE": "#2C3137",      # hairlines
        "TEXT": "#ECE8E1",      # warm white silk-screen
        "MUTED": "#969BA2",
        "FAINT": "#5E646C",
        "UNLIT": "#262B31",     # meter cell with nothing in it
        "ROW_LINE": "#191C20",
        "EDGE": "#3A4048",      # hover border, checkbox frame
        "HOVER": "#272C32",
        "SCROLL": "#343A42",
        "SCROLL_HOVER": "#454C55",
        "BANNER": "#1E2226",
        "SELECT_BASE": "#1F2226",
        "INK": {"Processing": "#B49CFF", "Paused": "#9AA7B8", "Queued": "#7A828C",
                "Completed": "#A6E35A", "Error": "#FF6B5E"},
    },
    "light": {
        "BG": "#F6F4F0",
        "PANEL": "#ECE9E3",
        "RAISED": "#FFFFFF",
        "LINE": "#D7D2C9",
        "TEXT": "#1D1F22",
        "MUTED": "#5F656D",
        "FAINT": "#9AA0A7",
        "UNLIT": "#DFDAD2",
        "ROW_LINE": "#EAE6DF",
        "EDGE": "#BDB7AE",
        "HOVER": "#FBFAF7",
        "SCROLL": "#C9C3BA",
        "SCROLL_HOVER": "#B0A99F",
        "BANNER": "#EFE8DA",
        "SELECT_BASE": "#F1EEE8",
        "INK": {"Processing": "#6A4FD8", "Paused": "#5E6B7C", "Queued": "#6B737D",
                "Completed": "#3F8A12", "Error": "#D2382C"},
    },
}
MODE = "dark"
_MODE_ASKED = "dark"     # what the user picked ("system" follows Windows)

# Designs: the whole look in one pick (Options → Appearance). A design sets the progress bar, the icon
# style, corner radius, list row height, the text font and its own grounds over MODES; the bar and the
# icons can also be changed on their own. Status inks and the user's accent/signal stay the same.
DESIGNS = {
    "studio": {"label": "Studio", "blurb": "Graphite rack, HUD meter", "bar": "cells", "icons": "line",
               "radius": 6, "row": 34, "font": "Segoe UI", "grounds": {}},
    "simple": {"label": "Simple", "blurb": "Plain and quiet, thin line", "bar": "line", "icons": "thin",
               "radius": 4, "row": 30, "font": "Segoe UI",
               "grounds": {
                   "dark": {"BG": "#141414", "PANEL": "#1A1A1A", "RAISED": "#232323", "LINE": "#2B2B2B",
                            "TEXT": "#EDEDED", "MUTED": "#9A9A9A", "FAINT": "#5F5F5F", "UNLIT": "#2A2A2A",
                            "ROW_LINE": "#1B1B1B", "EDGE": "#3A3A3A", "HOVER": "#262626", "SCROLL": "#353535",
                            "SCROLL_HOVER": "#474747", "BANNER": "#1D1D1D", "SELECT_BASE": "#1E1E1E"},
                   "light": {"BG": "#FFFFFF", "PANEL": "#F6F6F6", "RAISED": "#FFFFFF", "LINE": "#E4E4E4",
                             "TEXT": "#1A1A1A", "MUTED": "#626262", "FAINT": "#A0A0A0", "UNLIT": "#E8E8E8",
                             "ROW_LINE": "#F0F0F0", "EDGE": "#C8C8C8", "HOVER": "#FAFAFA", "SCROLL": "#D0D0D0",
                             "SCROLL_HOVER": "#B5B5B5", "BANNER": "#F2F2F2", "SELECT_BASE": "#F3F3F3"}}},
    "modern": {"label": "Modern", "blurb": "Soft and rounded, pill bar", "bar": "pill", "icons": "duotone",
               "radius": 10, "row": 40, "font": "Segoe UI Variable Text",
               "grounds": {
                   "dark": {"BG": "#0E1320", "PANEL": "#141B2B", "RAISED": "#1D2539", "LINE": "#27314A",
                            "TEXT": "#E6ECF7", "MUTED": "#8C98B0", "FAINT": "#56627C", "UNLIT": "#202A40",
                            "ROW_LINE": "#141A28", "EDGE": "#36425E", "HOVER": "#222B42", "SCROLL": "#2E3953",
                            "SCROLL_HOVER": "#3F4B68", "BANNER": "#172036", "SELECT_BASE": "#18203A"},
                   "light": {"BG": "#EEF2F9", "PANEL": "#FFFFFF", "RAISED": "#FFFFFF", "LINE": "#D7DEEA",
                             "TEXT": "#141A26", "MUTED": "#566078", "FAINT": "#98A2B6", "UNLIT": "#DEE5F0",
                             "ROW_LINE": "#E3E9F2", "EDGE": "#B5C0D3", "HOVER": "#F7F9FD", "SCROLL": "#C3CCDB",
                             "SCROLL_HOVER": "#A9B4C6", "BANNER": "#E2EAF8", "SELECT_BASE": "#E8EEF9"}}},
}
DESIGNS["terminal"] = {
    "label": "Terminal", "blurb": "Green phosphor, block meter", "bar": "blocks", "icons": "thin",
    "radius": 0, "row": 30, "font": "Cascadia Mono|Consolas",
    "grounds": {
        "dark": {"BG": "#070B07", "PANEL": "#0B120B", "RAISED": "#111C11", "LINE": "#1C301C", "TEXT": "#B6F2A8",
                 "MUTED": "#6FA567", "FAINT": "#3D6338", "UNLIT": "#132313", "ROW_LINE": "#0C150C", "EDGE": "#2A4A27",
                 "HOVER": "#142414", "SCROLL": "#1F3A1D", "SCROLL_HOVER": "#2C5229", "BANNER": "#0E180E",
                 "SELECT_BASE": "#0F1C0F"},
        "light": {"BG": "#F3F1E4", "PANEL": "#E9E6D4", "RAISED": "#FBFAF2", "LINE": "#CFCBB3", "TEXT": "#1E3A1A",
                  "MUTED": "#4F6B48", "FAINT": "#8E9A80", "UNLIT": "#DEDAC4", "ROW_LINE": "#E7E4D2", "EDGE": "#B5B096",
                  "HOVER": "#F7F5EA", "SCROLL": "#C6C1A6", "SCROLL_HOVER": "#ACA68A", "BANNER": "#E4E0C8",
                  "SELECT_BASE": "#ECE9D8"}}}
DESIGNS["ocean"] = {
    "label": "Ocean", "blurb": "Deep sea blues, liquid wave", "bar": "wave", "icons": "duotone",
    "radius": 8, "row": 36, "font": "Segoe UI",
    "grounds": {
        "dark": {"BG": "#061A24", "PANEL": "#08212D", "RAISED": "#0D2D3B", "LINE": "#143A4A", "TEXT": "#E3F3F6",
                 "MUTED": "#88AAB4", "FAINT": "#4E7380", "UNLIT": "#0E3140", "ROW_LINE": "#071D28", "EDGE": "#235265",
                 "HOVER": "#103545", "SCROLL": "#1A4456", "SCROLL_HOVER": "#255A70", "BANNER": "#0A2634",
                 "SELECT_BASE": "#0B2A37"},
        "light": {"BG": "#EAF6F8", "PANEL": "#FFFFFF", "RAISED": "#FFFFFF", "LINE": "#CFE3E8", "TEXT": "#0D2B36",
                  "MUTED": "#4B6E79", "FAINT": "#93AEB6", "UNLIT": "#D6EAEF", "ROW_LINE": "#DEEEF2", "EDGE": "#A9C8D1",
                  "HOVER": "#F5FBFC", "SCROLL": "#B9D3DA", "SCROLL_HOVER": "#9CBEC8", "BANNER": "#DDF0F4",
                  "SELECT_BASE": "#E3F2F5"}}}
BAR_STYLES = {"cells": "HUD cells", "line": "Thin line", "pill": "Pill", "blocks": "Terminal blocks",
              "wave": "Liquid wave", "beads": "Beads"}
ICON_STYLES = {"line": "Line", "thin": "Thin", "duotone": "Duotone"}
DESIGN, BAR, ICONS, RADIUS, ROW = "studio", "cells", "line", 6, 34

BG = PANEL = RAISED = LINE = TEXT = MUTED = FAINT = UNLIT = ""
ROW_LINE = EDGE = HOVER = SCROLL = SCROLL_HOVER = BANNER = SELECT_BASE = ""
SELECT = "#2B2721"      # selected row: accent-tinted ground (recomputed by set_colors)
ON_ACCENT = "#1A1408"   # text on the accent
ACCENT_INK = ACCENT     # the accent used as text/lines on the ground (darker in light mode)
ACCENT_HOVER = "#FFC266"
ACCENT_DOWN = "#E9A030"

# Fixed state inks (list, sidebar, details pane, tray all use these)
INK: dict[str, str] = {"Downloading": SIGNAL, "Connecting": "#2E9C8A"}
STATUS_COLORS = INK      # older name


def system_mode() -> str:
    """Windows' app mode (Settings → Personalisation → Colours)."""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
        except OSError:
            pass
    return "dark"


def set_mode(mode: str) -> None:
    """"dark", "light" or "system". Call set_colors() afterwards (it rebuilds the QSS)."""
    global MODE, BG, PANEL, RAISED, LINE, TEXT, MUTED, FAINT, UNLIT, _MODE_ASKED
    global ROW_LINE, EDGE, HOVER, SCROLL, SCROLL_HOVER, BANNER, SELECT_BASE
    _MODE_ASKED = mode
    MODE = system_mode() if mode == "system" else ("light" if mode == "light" else "dark")
    t = dict(MODES[MODE])
    t.update(DESIGNS[DESIGN]["grounds"].get(MODE, {}))
    BG, PANEL, RAISED, LINE, TEXT = t["BG"], t["PANEL"], t["RAISED"], t["LINE"], t["TEXT"]
    MUTED, FAINT, UNLIT = t["MUTED"], t["FAINT"], t["UNLIT"]
    ROW_LINE, EDGE, HOVER, SCROLL = t["ROW_LINE"], t["EDGE"], t["HOVER"], t["SCROLL"]
    SCROLL_HOVER, BANNER, SELECT_BASE = t["SCROLL_HOVER"], t["BANNER"], t["SELECT_BASE"]
    INK.update(t["INK"])


def is_light() -> bool:
    return MODE == "light"


set_mode("dark")

FONT_FAMILY = "Segoe UI"       # text font of the style sheet (the design's, if Windows has it)
NUMBER_FAMILY = "Segoe UI"     # numbers and panel legends: always Segoe UI (tabular figures)


def set_design(design: str, bar: str = "", icons: str = "") -> None:
    """Switch the design (and optionally its bar / icon style). Call set_colors() afterwards."""
    global DESIGN, BAR, ICONS, RADIUS, ROW, FONT_FAMILY
    DESIGN = design if design in DESIGNS else "studio"
    d = DESIGNS[DESIGN]
    BAR = bar if bar in BAR_STYLES else d["bar"]
    ICONS = icons if icons in ICON_STYLES else d["icons"]
    RADIUS, ROW = d["radius"], d["row"]
    FONT_FAMILY = _installed(d["font"])
    set_mode(_MODE_ASKED)


def _installed(families: str) -> str:
    """The first of "A|B|…" that Windows has, else Segoe UI."""
    try:
        from PySide6.QtGui import QFontDatabase
        have = set(QFontDatabase.families())
    except Exception:  # noqa: BLE001 - no QGuiApplication yet
        return "Segoe UI"
    return next((f for f in families.split("|") if f in have), "Segoe UI")


def label_font(size: float = 7.5, weight: QFont.Weight = QFont.DemiBold) -> QFont:
    """Small-caps panel legend: uppercase, tracked."""
    f = QFont(NUMBER_FAMILY)
    f.setPointSizeF(size)
    f.setWeight(weight)
    f.setCapitalization(QFont.AllUppercase)
    f.setLetterSpacing(QFont.AbsoluteSpacing, 0.9)
    return f


def number_font(size: float = 9.5, weight: QFont.Weight = QFont.Normal) -> QFont:
    f = QFont(NUMBER_FAMILY)
    f.setPointSizeF(size)
    f.setWeight(weight)
    if hasattr(QFont, "Tag"):                       # Qt 6.7+: force tabular figures
        f.setFeature(QFont.Tag("tnum"), 1)
    return f


def _mix(a: str, b: str, t: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t), round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


def _readable_on(ground: str, color: str, minimum: float = 3.0) -> str:
    """Darken (or lighten) `color` until it reads on `ground` (WCAG contrast >= minimum)."""
    def lum(c: QColor) -> float:
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())
    g, c = QColor(ground), QColor(color)
    for _ in range(20):
        a, b = sorted((lum(g), lum(c)), reverse=True)
        if (a + 0.05) / (b + 0.05) >= minimum:
            break
        c = c.darker(112) if lum(g) > 0.5 else c.lighter(112)
    return c.name().upper()


def set_colors(accent: str, signal: str) -> None:
    """Switch the theme colours; call before (re)applying build_qss()."""
    global ACCENT, SIGNAL, SELECT, ON_ACCENT, ACCENT_INK, ACCENT_HOVER, ACCENT_DOWN, QSS
    if not QColor(accent).isValid() or not QColor(signal).isValid():
        return
    ACCENT, SIGNAL = QColor(accent).name().upper(), QColor(signal).name().upper()
    SELECT = _mix(SELECT_BASE, ACCENT, 0.075 if MODE == "dark" else 0.16)
    light = QColor(ACCENT).lightnessF() > 0.55
    ON_ACCENT = _mix("#0E0F11", ACCENT, 0.12) if light else "#FFFFFF"
    ACCENT_INK = _readable_on(BG, ACCENT)
    ACCENT_HOVER = QColor(ACCENT).lighter(112).name()
    ACCENT_DOWN = QColor(ACCENT).darker(112).name()
    INK["Downloading"] = _readable_on(BG, SIGNAL)
    INK["Connecting"] = QColor(INK["Downloading"]).darker(130).name() if MODE == "dark" \
        else QColor(INK["Downloading"]).lighter(118).name()
    QSS = build_qss()


def palette():
    """Qt palette matching the tokens, for widgets the style sheet doesn't reach (colour picker, message boxes)."""
    from PySide6.QtGui import QPalette
    pal = QPalette()
    for role, value in ((QPalette.Window, PANEL), (QPalette.WindowText, TEXT), (QPalette.Base, RAISED),
                        (QPalette.AlternateBase, BG), (QPalette.Text, TEXT), (QPalette.Button, RAISED),
                        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT),
                        (QPalette.HighlightedText, ON_ACCENT), (QPalette.ToolTipBase, RAISED),
                        (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, FAINT), (QPalette.Mid, LINE)):
        pal.setColor(role, QColor(value))
    return pal


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
        if MODE == "light":                       # deeper inks so yellow still reads on paper
            c = QColor.fromHsvF((4 + 104 * f) / 360, 0.86, 0.86 - 0.12 * math.sin(math.pi * f))
        else:
            c = QColor.fromHsvF((4 + 104 * f) / 360, 0.74 - 0.08 * f, 1.0)
    else:
        c = QColor(INK["Downloading"])
    if status in ("Paused", "Queued"):
        c = QColor.fromHsvF(c.hsvHueF() if c.hsvHueF() >= 0 else 0, c.hsvSaturationF() * 0.35,
                            c.valueF() * 0.72)
    return c


def build_qss() -> str:
    R, r = RADIUS, max(3, RADIUS - 2)              # the design's corners: controls, and small things
    return f"""
* {{ font-family: "{FONT_FAMILY}", "Segoe UI", sans-serif; font-size: 9.5pt; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#panel {{ background: {PANEL}; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {LINE}; padding: 5px 8px; }}

QMenuBar {{ background: {PANEL}; border-bottom: 1px solid {LINE}; padding: 2px 4px; }}
QMenuBar::item {{ background: transparent; padding: 5px 10px; border-radius: {r}px; }}
QMenuBar::item:selected {{ background: {RAISED}; }}
QMenuBar#captionMenu {{ background: transparent; border: none; padding: 0; }}
QMenuBar#captionMenu::item {{ padding: 5px 9px; }}
QLabel#captionTitle {{ font-size: 9pt; }}
QWidget#listhead {{ background: {BG}; }}
QScrollArea#page, QScrollArea#page > QWidget > QWidget {{ background: transparent; }}
QLabel#listTitle {{ font-weight: 600; }}
QCheckBox#muted {{ color: {MUTED}; }}
QMenu {{ background: {PANEL}; border: 1px solid {LINE}; padding: 5px; }}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: {r}px; }}
QMenu::item:selected {{ background: {RAISED}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::indicator {{ width: 12px; height: 12px; margin-left: 8px; }}

QToolBar {{ background: {PANEL}; border: none; border-bottom: 1px solid {LINE};
            spacing: 2px; padding: 6px 8px; }}
QToolBar::separator {{ background: {LINE}; width: 1px; margin: 8px 6px; }}
QToolBar QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: {R}px;
                        padding: 5px 7px 4px 7px; color: {TEXT}; font-size: 8.5pt; min-width: 54px; }}
QToolBar QToolButton:hover {{ background: {RAISED}; border-color: {LINE}; }}
QToolBar QToolButton {{ font-family: "{NUMBER_FAMILY}"; }}  /* labels fit their buttons in every design (Terminal is monospace) */
QToolBar QToolButton:pressed {{ background: {LINE}; }}
QToolBar QToolButton:disabled {{ color: {FAINT}; }}
QToolBar QToolButton#primary {{ color: {ACCENT_INK}; font-weight: 600; }}

QTreeWidget#sidebar {{ background: {PANEL}; border: none; border-right: 1px solid {LINE};
                       padding: 8px 6px; outline: 0; }}
QTreeWidget#sidebar::item {{ padding: 5px 4px; margin: 1px 0; border-radius: {r if RADIUS >= 8 else 0}px; }}
QTreeWidget#sidebar::item:selected {{ background: {SELECT}; color: {TEXT}; }}
QTreeWidget#sidebar::item:hover:!selected {{ background: {RAISED}; }}
QTreeWidget#sidebar::branch {{ background: transparent; }}

QTableView {{ background: {BG}; border: none; gridline-color: transparent; outline: 0;
              selection-background-color: {SELECT}; selection-color: {TEXT}; }}
QTableView::item {{ padding: 0 8px; border-bottom: 1px solid {ROW_LINE}; }}
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
    background: {RAISED}; border: 1px solid {LINE}; border-radius: {R}px; padding: 6px 8px;
    selection-background-color: {ACCENT}; selection-color: {ON_ACCENT}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTimeEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled {{ color: {FAINT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {LINE}; outline: 0;
                               selection-background-color: {RAISED}; selection-color: {TEXT}; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QTimeEdit::up-button, QTimeEdit::down-button {{ width: 16px; border: none; }}

QPushButton {{ background: {RAISED}; border: 1px solid {LINE}; border-radius: {R}px; padding: 7px 16px; }}
QPushButton:hover {{ border-color: {EDGE}; background: {HOVER}; }}
QPushButton:pressed {{ background: {LINE}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: {ON_ACCENT}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_DOWN}; }}
QPushButton#flat {{ background: transparent; border-color: transparent; color: {ACCENT_INK}; padding: 4px 8px; }}
QPushButton#flat:hover {{ background: {RAISED}; }}
QPushButton:disabled {{ color: {FAINT}; background: {PANEL}; border-color: {LINE}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid {EDGE}; background: {RAISED}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: none; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 7px; border: 1px solid {EDGE}; background: {RAISED}; }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border: 3px solid {RAISED}; }}

QListWidget#nav {{ background: {PANEL}; border: none; border-right: 1px solid {LINE}; padding: 10px 6px; outline: 0; }}
QListWidget#nav::item {{ padding: 8px 12px; border-radius: {r}px; margin: 1px 0; }}
QListWidget#nav::item:selected {{ background: {SELECT}; color: {TEXT}; }}
QListWidget#nav::item:hover:!selected {{ background: {RAISED}; }}
QListWidget#choices {{ background: {BG}; border: 1px solid {LINE}; border-radius: {R}px; outline: 0; padding: 4px; }}
QListWidget#choices::item {{ padding: 7px 10px; border-radius: {r}px; }}
QListWidget#choices::item:selected {{ background: {SELECT}; color: {TEXT}; }}
QListWidget#choices::item:hover:!selected {{ background: {RAISED}; }}

QTableWidget#connections {{ background: {PANEL}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SCROLL}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QProgressBar {{ background: {UNLIT}; border: none; border-radius: 2px; height: 4px; color: transparent; }}
QProgressBar::chunk {{ background: {SIGNAL}; border-radius: 2px; }}

QLabel#muted {{ color: {MUTED}; }}
QLabel#title {{ font-size: 13pt; font-weight: 600; }}
QLabel#legend {{ color: {MUTED}; }}
QLabel#error {{ color: {INK["Error"]}; }}
QFrame#banner {{ background: {BANNER}; border-bottom: 1px solid {LINE}; }}
QFrame#hairline {{ background: {LINE}; max-height: 1px; border: none; }}
"""


QSS = build_qss()


def apply_window_chrome(widget) -> None:
    """Windows 10/11: the frame in the app's colours (dark/light system parts, the caption colour for
    windows that keep Windows' title bar, the 1 px Windows 11 border in the hairline colour)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(0 if MODE == "light" else 1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))      # immersive dark mode
        c = QColor(PANEL)
        colorref = ctypes.c_int(c.red() | (c.green() << 8) | (c.blue() << 16))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref), ctypes.sizeof(colorref))  # caption colour (Win11)
        e = QColor(EDGE)
        border = ctypes.c_int(e.red() | (e.green() << 8) | (e.blue() << 16))
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))      # border colour (Win11)
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
