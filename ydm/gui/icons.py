"""YDM's icon set, drawn in code: one 24-unit grid, one stroke weight, round caps.

icon("pause") -> QIcon in the current label colour; icon("add", "accent") for a tinted one.
put(button, "pause", "text") also re-tints it when the theme changes (retint()).
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from . import theme

STROKE = 1.7


def _p(*pts) -> QPainterPath:
    path = QPainterPath(QPointF(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(QPointF(*pt))
    return path


def _rect(x, y, w, h, r=2.0) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), r, r)
    return path


def _circle(cx, cy, r) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), r, r)
    return path


def _arc(cx, cy, r, start, span) -> QPainterPath:
    path = QPainterPath()
    rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
    path.arcMoveTo(rect, start)
    path.arcTo(rect, start, span)
    return path


def _ellipse(cx, cy, rx, ry) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), rx, ry)
    return path


def _moon() -> QPainterPath:
    outer = QPainterPath()
    outer.addEllipse(QPointF(12, 12), 8, 8)
    bite = QPainterPath()
    bite.addEllipse(QPointF(16.5, 8.5), 6.5, 6.5)
    return outer.subtracted(bite)


def _folder() -> QPainterPath:
    return _p((3, 7), (3, 18.5), (21, 18.5), (21, 9), (11.5, 9), (9.5, 6), (3, 6), (3, 7))


# Each glyph: list of (path, filled)
def _glyph(name: str) -> list[tuple[QPainterPath, bool]]:
    g: dict[str, list] = {
        "add": [(_p((12, 5), (12, 19)), False), (_p((5, 12), (19, 12)), False)],
        "resume": [(_p((8, 5.5), (18.5, 12), (8, 18.5), (8, 5.5)), True)],
        "pause": [(_rect(6.5, 5.5, 3.6, 13, 1.2), True), (_rect(13.9, 5.5, 3.6, 13, 1.2), True)],
        "stop": [(_rect(6, 6, 12, 12, 2), True)],
        "stop_all": [(_rect(4, 7, 10, 10, 1.6), True), (_p((17, 7), (17, 17)), False), (_p((20, 7), (20, 17)), False)],
        "delete": [(_p((4.5, 7), (19.5, 7)), False), (_p((9.5, 7), (10, 4.5), (14, 4.5), (14.5, 7)), False),
                   (_p((6.5, 7), (7.5, 19.5), (16.5, 19.5), (17.5, 7)), False),
                   (_p((10.5, 10.5), (10.5, 16)), False), (_p((13.5, 10.5), (13.5, 16)), False)],
        "delete_done": [(_p((4, 12.5), (8, 16.5), (16, 7.5)), False), (_p((15, 15), (20, 20)), False),
                        (_p((20, 15), (15, 20)), False)],
        "queue": [(_p((4, 7), (14, 7)), False), (_p((4, 12), (14, 12)), False), (_p((4, 17), (10, 17)), False),
                  (_p((15.5, 13), (20.5, 16.5), (15.5, 20), (15.5, 13)), True)],
        "queue_stop": [(_p((4, 7), (14, 7)), False), (_p((4, 12), (14, 12)), False), (_p((4, 17), (10, 17)), False),
                       (_rect(14.5, 13.5, 6, 6, 1.2), True)],
        "scheduler": [(_circle(12, 12.5, 7.5), False), (_p((12, 8.5), (12, 12.5), (15, 14.5)), False),
                      (_p((5, 4.5), (7.5, 2.8)), False), (_p((19, 4.5), (16.5, 2.8)), False)],
        "video": [(_rect(3, 6, 13, 12, 2.5), False), (_p((16, 10.5), (21, 7.5), (21, 16.5), (16, 13.5)), False)],
        "settings": [(_circle(12, 12, 3), False)] + [
            (_p((12 + 6.2 * c, 12 + 6.2 * s), (12 + 8.6 * c, 12 + 8.6 * s)), False)
            for c, s in ((1, 0), (-1, 0), (0, 1), (0, -1), (0.707, 0.707), (-0.707, 0.707), (0.707, -0.707), (-0.707, -0.707))
        ] + [(_circle(12, 12, 6.2), False)],
        "folder": [(_folder(), False)],
        "open": [(_p((14, 4), (20, 4), (20, 10)), False), (_p((20, 4), (11, 13)), False),
                 (_p((17, 14), (17, 20), (4, 20), (4, 7), (10, 7)), False)],
        "link": [(_arc(8.5, 15.5, 4.2, 45, 180), False), (_arc(15.5, 8.5, 4.2, 225, 180), False),
                 (_p((9.5, 14.5), (14.5, 9.5)), False)],
        "refresh": [(_arc(12, 12, 7, 60, 300), False), (_p((15.5, 3.5), (15.8, 6.4), (12.9, 6.9)), False)],
        "update": [(_arc(12, 12, 7.5, 100, 290), False), (_p((12, 8), (12, 15.5)), False),
                   (_p((9, 12.8), (12, 15.8), (15, 12.8)), False)],
        "tools": [(_p((14.5, 4.5), (19.5, 9.5)), False), (_p((17, 7), (8, 16)), False),
                  (_p((5, 15), (9, 19)), False), (_p((4, 20), (7, 17)), False)],
        "all": [(_p((12, 4), (12, 15)), False), (_p((7.5, 10.5), (12, 15), (16.5, 10.5)), False),
                (_p((5, 19.5), (19, 19.5)), False)],
        "unfinished": [(_arc(12, 12, 7.5, 90, -270), False), (_p((12, 4.5), (12, 12), (17, 12)), False)],
        "finished": [(_circle(12, 12, 8), False), (_p((8, 12.3), (11, 15.2), (16.2, 9.2)), False)],
        "queued": [(_p((5, 7), (19, 7)), False), (_p((5, 12), (19, 12)), False), (_p((5, 17), (13, 17)), False)],
        "music": [(_p((9, 17), (9, 5.5), (19, 4), (19, 15.5)), False), (_circle(6.8, 17, 2.3), True),
                  (_circle(16.8, 15.5, 2.3), True)],
        "archive": [(_rect(4, 5, 16, 15, 2), False), (_p((4, 9.5), (20, 9.5)), False),
                    (_p((10, 13), (14, 13)), False)],
        "document": [(_p((6, 3.5), (14, 3.5), (18.5, 8), (18.5, 20.5), (6, 20.5), (6, 3.5)), False),
                     (_p((14, 3.5), (14, 8), (18.5, 8)), False), (_p((9, 13), (15.5, 13)), False),
                     (_p((9, 16.5), (14, 16.5)), False)],
        "program": [(_rect(3.5, 5, 17, 14, 2), False), (_p((3.5, 9), (20.5, 9)), False),
                    (_p((7.5, 12.5), (10, 15), (7.5, 17.5)), False)],
        "image": [(_rect(3.5, 5, 17, 14, 2), False), (_circle(9, 10, 1.6), True),
                  (_p((3.5, 17), (9.5, 12.5), (13, 15.5), (16, 13), (20.5, 16.5)), False)],
        "other": [(_p((6, 3.5), (14, 3.5), (18.5, 8), (18.5, 20.5), (6, 20.5), (6, 3.5)), False),
                  (_p((14, 3.5), (14, 8), (18.5, 8)), False)],
        "chevron": [(_p((9, 6), (15, 12), (9, 18)), False)],
        "close": [(_p((7, 7), (17, 17)), False), (_p((17, 7), (7, 17)), False)],
        "copy": [(_rect(8, 8, 12, 12, 2), False), (_p((16, 5), (16, 4), (4, 4), (4, 16), (5, 16)), False)],
        "power": [(_arc(12, 13, 7, 125, 290), False), (_p((12, 3.5), (12, 11)), False)],
        "speed": [(_arc(12, 15, 8.5, 0, 180), False), (_p((12, 15), (16.5, 9.5)), False)],
        "sun": [(_circle(12, 12, 4), False)] + [
            (_p((12 + 6.5 * c, 12 + 6.5 * s), (12 + 8.8 * c, 12 + 8.8 * s)), False)
            for c, s in ((1, 0), (-1, 0), (0, 1), (0, -1), (0.707, 0.707), (-0.707, 0.707), (0.707, -0.707), (-0.707, -0.707))
        ],
        "moon": [(_moon(), False)],
        "globe": [(_circle(12, 12, 8), False), (_p((4, 12), (20, 12)), False), (_ellipse(12, 12, 3.6, 8), False)],
        "key": [(_circle(8, 12, 3.6), False), (_p((11.6, 12), (20.5, 12)), False),
                (_p((17.5, 12), (17.5, 15.2)), False), (_p((20.5, 12), (20.5, 14.5)), False)],
        "palette": [(_circle(12, 12, 8), False), (_circle(9, 9.2, 1.5), True), (_circle(14.2, 8.4, 1.5), True),
                    (_circle(16.2, 13.2, 1.5), True), (_circle(10.5, 15.6, 2.2), False)],
        "plugs": [(_p((4, 8), (20, 8)), False), (_p((4, 12), (20, 12)), False), (_p((4, 16), (20, 16)), False),
                  (_circle(8, 8, 1.6), True), (_circle(15, 12, 1.6), True), (_circle(11, 16, 1.6), True)],
    }
    return g.get(name, g["other"])


ROLES = {"text": "TEXT", "muted": "MUTED", "accent": "ACCENT", "accent_ink": "ACCENT_INK", "on_accent": "ON_ACCENT"}


def _color(color: str | None) -> str:
    """A colour, or a role name ("text", "muted", "accent", …) resolved against the current theme."""
    if not color:
        return theme.TEXT
    return getattr(theme, ROLES[color]) if color in ROLES else color


def pixmap(name: str, color: str | None = None, size: int = 24, dpr: float = 2.0) -> QPixmap:
    return _pixmap(name, _color(color), size, dpr)


def icon(name: str, color: str | None = None, size: int = 24) -> QIcon:
    return _icon(name, _color(color), size, theme.FAINT)


def put(target, name: str, role: str = "text", size: int = 16):
    """Set a themed icon on a button or action and remember it, so retint() can recolour it
    when the theme changes (light/dark, accent). Returns the target."""
    target.setProperty("ydm_icon", f"{name}|{role}|{size}")
    target.setIcon(icon(name, role, size))
    return target


def retint(roots=()) -> None:
    """Recolour every icon set with put() in all open widgets (and the actions under `roots`)."""
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication
    targets = list(QApplication.allWidgets())
    for root in roots:
        targets += root.findChildren(QAction)
    for t in targets:
        spec = t.property("ydm_icon")
        if spec:
            name, role, size = spec.split("|")
            t.setIcon(icon(name, role, int(size)))


@lru_cache(maxsize=512)
def _pixmap(name: str, color: str, size: int = 24, dpr: float = 2.0) -> QPixmap:
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 24, size / 24)
    c = QColor(color)
    pen = QPen(c, STROKE * (24 / size) ** 0.35, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    for path, filled in _glyph(name):
        p.setPen(pen)
        p.setBrush(c if filled else Qt.NoBrush)
        p.drawPath(path)
    p.end()
    return pm


@lru_cache(maxsize=512)
def _icon(name: str, color: str, size: int, faint: str) -> QIcon:
    ic = QIcon()
    ic.addPixmap(_pixmap(name, color, size), QIcon.Normal)
    ic.addPixmap(_pixmap(name, faint, size), QIcon.Disabled)
    return ic


CATEGORY_ICONS = {"Video": "video", "Music": "music", "Compressed": "archive", "Documents": "document",
                  "Programs": "program", "Images": "image", "Other": "other"}
