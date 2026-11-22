"""The progress bar, in three styles (theme.BAR, picked in Options → Appearance):

cells (Studio) — a sci-fi HUD gauge, described below.
line (Simple)  — a thin rounded track that fills smoothly with the percentage.
pill (Modern)  — a rounded pill with a soft gradient and, on the big version, stripes drifting
                 across while bytes arrive.
Only the HUD gauge shows the individual connections; the other two stay calm.

The HUD gauge:

Upright cells between two brackets. Each cell is one slice of the file and
lights up as that slice arrives (straight from the real segment map), so you
see every connection filling its own stretch, with a hot cell where each one
is writing right now. The whole gauge shifts colour with the percentage, red
→ amber → yellow → green (or stays in the theme's signal colour). Flat cells,
no glow; on the big version a light sweeps across while bytes arrive.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..engine import DownloadItem, Status
from . import theme
from .models import coverage, heads

BAR_MODE = ["shift"]          # "shift" or "theme"; set from Settings.bar_colors


def _bracket(p: QPainter, rect: QRectF, left: bool, color: QColor) -> None:
    x = rect.left() if left else rect.right()
    arm = 3.5 if left else -3.5
    p.setPen(color)
    p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
    p.drawLine(QPointF(x, rect.top()), QPointF(x + arm, rect.top()))
    p.drawLine(QPointF(x, rect.bottom()), QPointF(x + arm, rect.bottom()))


def _state(item: DownloadItem | None, fraction: float | None, status: str | None):
    """(status, fraction, colour, live, size unknown) for any bar style."""
    st = status or (item.status if item else Status.DOWNLOADING)
    if item is not None and item.missing:
        st = Status.ERROR                          # finished, but the file was deleted or moved
    frac = fraction if fraction is not None else (item.progress if item else 0.0)
    color = theme.progress_color(frac, Status.DOWNLOADING if st in (Status.CONNECTING, Status.PROCESSING) else st,
                                 BAR_MODE[0])
    live = st in Status.ACTIVE
    return st, frac, color, live, item is not None and live and item.total <= 0


def draw_bar(p: QPainter, rect: QRectF, item: DownloadItem | None, *, big: bool = False, sweep: float | None = None,
             fraction: float | None = None, status: str | None = None) -> QColor:
    """The progress bar in the chosen style; returns the colour used (for the percentage readout)."""
    if theme.BAR == "line":
        return _draw_line(p, rect, item, big, fraction, status)
    if theme.BAR == "pill":
        return _draw_pill(p, rect, item, big, fraction, status)
    return draw_hud_bar(p, rect, item, cell=7 if big else 5, gap=2.5 if big else 2.0, sweep=sweep,
                        fraction=fraction, status=status)


def _fill_path(track: QRectF, frac: float, unknown: bool, h: float) -> QPainterPath:
    path = QPainterPath()
    if unknown:                                    # size unknown: a short stretch glides across
        t = (time.monotonic() * 0.6) % 1.4 - 0.4
        path.addRoundedRect(QRectF(track.left() + t * track.width(), track.top(), track.width() * 0.3, h),
                            h / 2, h / 2)
    elif frac > 0:
        w = max(h, min(1.0, frac) * track.width())
        path.addRoundedRect(QRectF(track.left(), track.top(), w, h), h / 2, h / 2)
    return path


def _draw_line(p: QPainter, rect: QRectF, item, big: bool, fraction, status) -> QColor:
    _st, frac, color, _live, unknown = _state(item, fraction, status)
    h = 6.0 if big else 4.0
    track = QRectF(rect.left(), rect.center().y() - h / 2, rect.width(), h)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    clip = QPainterPath()
    clip.addRoundedRect(track, h / 2, h / 2)
    p.fillPath(clip, QColor(theme.UNLIT))
    p.setClipPath(clip)
    p.fillPath(_fill_path(track, frac, unknown, h), color)
    p.restore()
    return color


def _draw_pill(p: QPainter, rect: QRectF, item, big: bool, fraction, status) -> QColor:
    _st, frac, color, live, unknown = _state(item, fraction, status)
    h = min(rect.height(), 16.0 if big else 12.0)
    track = QRectF(rect.left(), rect.center().y() - h / 2, rect.width(), h)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    clip = QPainterPath()
    clip.addRoundedRect(track, h / 2, h / 2)
    p.fillPath(clip, QColor(theme.UNLIT))
    p.setClipPath(clip)
    grad = QLinearGradient(0, track.top(), 0, track.bottom())
    grad.setColorAt(0.0, QColor(color).lighter(125))
    grad.setColorAt(1.0, color)
    fill = _fill_path(track, frac, unknown, h)
    p.fillPath(fill, grad)
    if big and live and not unknown and not fill.isEmpty():
        # stripes drifting to the right over the filled part, only while it runs
        p.save()
        p.setClipPath(fill, Qt.IntersectClip)
        p.setPen(QPen(QColor(255, 255, 255, 38), h * 0.32))
        step = h * 1.4
        x = track.left() - h + (time.monotonic() * 22) % step
        while x < track.right() + h:
            p.drawLine(QPointF(x, track.bottom() + 1), QPointF(x + h, track.top() - 1))
            x += step
        p.restore()
    p.restore()
    return color


def draw_hud_bar(p: QPainter, rect: QRectF, item: DownloadItem | None, *, cell: float = 5.0, gap: float = 2.0,
                 sweep: float | None = None, fraction: float | None = None, status: str | None = None) -> QColor:
    """Paint the gauge into rect; returns the colour used (for the percentage readout).
    `fraction`/`status` draw a stand-in without an item (the Appearance preview)."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    st = status or (item.status if item else Status.DOWNLOADING)
    if item is not None and item.missing:
        st = Status.ERROR                          # finished, but the file was deleted or moved
    frac = fraction if fraction is not None else (item.progress if item else 0.0)
    color = theme.progress_color(frac, Status.DOWNLOADING if st in (Status.CONNECTING, Status.PROCESSING) else st,
                                 BAR_MODE[0])
    live = st in Status.ACTIVE

    frame = QColor(color if live or st == Status.COMPLETED else theme.FAINT)
    frame.setAlphaF(0.75 if live else 0.6)
    p.setPen(Qt.NoPen)
    _bracket(p, rect, True, frame)
    _bracket(p, rect, False, frame)

    inner = rect.adjusted(5, 2, -5, -2)
    h = inner.height()
    pitch = cell + gap
    n = max(1, int((inner.width() + gap) // pitch))
    x0 = inner.left() + (inner.width() - (n * pitch - gap)) / 2

    if item is not None:
        cov = coverage(item, n)
        hot = heads(item, n)
    else:
        filled = frac * n
        cov = [max(0.0, min(1.0, filled - i)) for i in range(n)]
        hot = {min(n - 1, int(filled))} if 0 < frac < 1 and live else set()
    unknown = item is not None and live and item.total <= 0

    p.setPen(Qt.NoPen)
    unlit = QColor(theme.UNLIT)
    scan = (time.monotonic() * 1.4) % 2 if unknown else 0.0
    scan_pos = (scan if scan <= 1 else 2 - scan) * (n - 1)
    for i in range(n):
        x = x0 + i * pitch
        if unknown:                                   # size unknown: a scanner sweeps back and forth
            d = abs(i - scan_pos)
            if d < 4:
                c = QColor(color)
                c.setAlphaF(1.0 - d / 4)
            else:
                c = unlit
        elif cov[i] <= 0.001:
            c = unlit
        else:
            c = QColor(color)
            c.setAlphaF(0.9 if st == Status.COMPLETED else 0.32 + 0.68 * cov[i])
            if i in hot:                              # where a connection is writing right now
                c = QColor(theme._mix(color.name(), "#FFFFFF", 0.28))
            if sweep is not None and live:
                d = abs(i / max(1, n - 1) - sweep)
                if d < 0.06:
                    c = QColor(c).lighter(int(100 + 55 * (1 - d / 0.06)))
        p.setBrush(c)
        p.drawRoundedRect(QRectF(x, inner.top(), cell, h), 1.0, 1.0)
    p.restore()
    return color


class HudBar(QWidget):
    """Big gauge for the download window and the details pane: cells, a tick scale with
    a marker under every working connection, and the percentage on the right."""

    def __init__(self, parent=None, readout: bool = True):
        super().__init__(parent)
        self.item: DownloadItem | None = None
        self.readout = readout
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(40)
        self._pct = theme.number_font(15, QFont.DemiBold)
        self._anim = QTimer(self, interval=40)       # the sweep, only while bytes arrive
        self._anim.timeout.connect(self.update)

    def sizeHint(self):
        return QSize(420, 40)

    def set_item(self, item: DownloadItem | None) -> None:
        self.item = item
        running = item is not None and item.status in Status.ACTIVE
        if running and self.isVisible() and not self._anim.isActive():
            self._anim.start()
        elif not running and self._anim.isActive():
            self._anim.stop()
        self.update()

    def hideEvent(self, event):
        self._anim.stop()
        super().hideEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        item = self.item
        w = self.width()
        text_w = 86 if self.readout else 0
        bar = QRectF(0, 3, w - text_w - (10 if text_w else 0), 22)
        sweep = (time.monotonic() % 1.8) / 1.8 if item and item.status == Status.DOWNLOADING else None
        if theme.BAR != "cells":                     # line / pill: just the bar and the percentage
            bar = QRectF(0, 0, w - text_w - (14 if text_w else 0), 30)
            color = draw_bar(p, bar, item, big=True, fraction=0 if item is None else None,
                             status=Status.QUEUED if item is None else None)
            self._readout(p, item, color, w, text_w)
            p.end()
            return
        if item is None:
            color = draw_hud_bar(p, bar, None, cell=7, gap=2.5, fraction=0, status=Status.QUEUED)
        else:
            color = draw_hud_bar(p, bar, item, cell=7, gap=2.5, sweep=sweep)

        # scale: a tick every 10 %, longer at the quarters, and a marker under each working connection
        p.setPen(QColor(theme.FAINT))
        left, right = bar.left() + 5, bar.right() - 5
        y = bar.bottom() + 4
        for k in range(11):
            x = left + (right - left) * k / 10
            p.drawLine(QPointF(x, y), QPointF(x, y + (5 if k in (0, 5, 10) else 3)))
        if item is not None and item.total > 0 and item.status == Status.DOWNLOADING and item.kind == "file":
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            for s in item.segments:
                if s.active and s.remaining > 0:
                    x = left + (right - left) * s.pos / item.total
                    p.drawPolygon(QPolygonF([QPointF(x, y), QPointF(x - 3.5, y + 6), QPointF(x + 3.5, y + 6)]))

        self._readout(p, item, color, w, text_w)
        p.end()

    def _readout(self, p: QPainter, item, color: QColor, w: int, text_w: int) -> None:
        if not self.readout:
            return
        known = item is not None and (item.total > 0 or item.status == Status.COMPLETED)
        pct = item.progress * 100 if known else 0.0
        text = (f"{pct:.0f}%" if pct >= 99.95 or pct == 0 else f"{pct:.1f}%") if known else "—"
        p.setFont(self._pct)
        p.setPen(color if known and item.status != Status.QUEUED else QColor(theme.MUTED))
        p.drawText(QRectF(w - text_w, 0, text_w, 30), Qt.AlignRight | Qt.AlignVCenter, text)

