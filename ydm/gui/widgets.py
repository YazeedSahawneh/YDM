"""Custom widgets: the total-speed meter, the details pane, and the notice banner."""
from __future__ import annotations

import math
import os
import time

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..engine import DownloadItem, Status
from ..utils import human_eta, human_size, human_speed, open_path, reveal_in_folder
from . import icons, theme
from .hudbar import HudBar

LOW, HIGH = 64 * 1024, 256 * 1024 * 1024          # meter range, log scale


def _level(bps: float) -> float:
    if bps <= LOW:
        return 0.0
    return min(1.0, math.log(bps / LOW) / math.log(HIGH / LOW))


class SpeedMeter(QWidget):
    """Total throughput as a hi-fi level meter with peak hold."""

    BLOCKS = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self.speed = 0.0
        self.peak = 0.0
        self._peak_t = 0.0
        self.setFixedSize(QSize(330, 44))
        self._big = theme.number_font(13, QFont.DemiBold)
        self._small = theme.label_font(7)
        self._note = theme.number_font(8)

    def set_speed(self, bps: float) -> None:
        now = time.monotonic()
        self.speed = bps
        if bps >= self.peak:
            self.peak, self._peak_t = bps, now
        elif now - self._peak_t > 2.5:                # hold, then fall back like a real peak LED
            self.peak = max(bps, self.peak * 0.85)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        text = human_speed(self.speed) or "0 B/s"
        p.setFont(self._big)
        p.setPen(QColor(theme.TEXT if self.speed > 0 else theme.MUTED))
        tw = 116
        p.drawText(QRectF(0, 0, tw, 26), Qt.AlignRight | Qt.AlignVCenter, text)
        p.setFont(self._note)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(0, 26, tw, 16), Qt.AlignRight | Qt.AlignVCenter,
                   f"peak {human_speed(self.peak)}" if self.peak > 0 else "total speed")
        x0, block, gap = tw + 14, 5.0, 2.0
        lit = round(_level(self.speed) * self.BLOCKS)
        peak_i = round(_level(self.peak) * self.BLOCKS) - 1
        signal = QColor(theme.INK["Downloading"])
        p.setPen(Qt.NoPen)
        for i in range(self.BLOCKS):
            if i < lit:
                c = QColor(signal)
                c.setAlphaF(0.45 + 0.55 * (i + 1) / self.BLOCKS)
            elif i == peak_i and self.peak > 0:
                c = QColor(theme.TEXT)
            else:
                c = QColor(theme.UNLIT)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x0 + i * (block + gap), 9, block, 18), 1.2, 1.2)
        p.setFont(self._small)
        p.setPen(QColor(theme.FAINT))
        for frac, label in ((0.0, "64K"), (_level(1024 ** 2 * 10), "10M"), (1.0, "256M")):
            x = x0 + frac * (self.BLOCKS * (block + gap) - gap)
            p.drawText(QRectF(x - 20, 29, 40, 14), Qt.AlignCenter, label)
        p.end()


class DetailsPane(QFrame):
    """Bottom pane: the selected download's facts, segment map and live connections."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setAutoFillBackground(True)
        self.item: DownloadItem | None = None

        self.name = QLabel("")
        self.name.setObjectName("title")
        self.name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status = QLabel("")
        self.btn_open = QPushButton(icons.icon("open"), " Open")
        self.btn_folder = QPushButton(icons.icon("folder"), " Show in folder")
        for b in (self.btn_open, self.btn_folder):
            b.setObjectName("flat")
            b.setCursor(Qt.PointingHandCursor)
        self.btn_open.clicked.connect(lambda: self.item and open_path(self.item.path))
        self.btn_folder.clicked.connect(self._show_folder)

        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(self.name, 1)
        head.addWidget(self.status)
        head.addWidget(self.btn_open)
        head.addWidget(self.btn_folder)

        self.facts: dict[str, QLabel] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)
        names = ["Address", "Saved to", "Size", "Downloaded", "Speed", "Time left", "Resume", "Connections"]
        for i, key in enumerate(names):
            legend = QLabel(key)
            legend.setFont(theme.label_font(7))
            legend.setObjectName("legend")
            val = QLabel("")
            val.setFont(theme.number_font(9))
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if i < 2:
                grid.addWidget(legend, i, 0)
                grid.addWidget(val, i, 1, 1, 5)
            else:
                r, c = 2 + (i - 2) // 3, ((i - 2) % 3) * 2
                grid.addWidget(legend, r, c)
                grid.addWidget(val, r, c + 1)
            self.facts[key] = val
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setColumnStretch(5, 1)

        self.map = HudBar()
        self.table = QTableWidget(0, 5)
        self.table.setObjectName("connections")
        self.table.setHorizontalHeaderLabels(["#", "From", "Downloaded", "Speed", "State"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setFont(theme.label_font(7))
        hh.setHighlightSections(False)
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 40)

        self.empty = QLabel("Select a download to see its connections at work.")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        body.addLayout(head)
        body.addLayout(grid)
        body.addWidget(self.map)
        body.addWidget(self.table, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 12)
        lay.addWidget(self.body)
        lay.addWidget(self.empty)
        self.show_item(None)

    def _show_folder(self):
        it = self.item
        if not it:
            return
        if it.status == Status.COMPLETED and os.path.exists(it.path):
            reveal_in_folder(it.path)
        elif it.save_dir and os.path.isdir(it.save_dir):
            open_path(it.save_dir)

    def show_item(self, item: DownloadItem | None) -> None:
        self.item = item
        self.body.setVisible(item is not None)
        self.empty.setVisible(item is None)
        self.map.set_item(item)
        if item is None:
            return
        f = self.facts
        fm = QFontMetrics(self.name.font())
        self.name.setText(fm.elidedText(item.filename or item.title or item.url, Qt.ElideMiddle,
                                        max(200, self.width() - 420)))
        self.status.setText(item.error if item.status == Status.ERROR and item.error else item.status)
        self.status.setStyleSheet(f"color: {theme.INK.get(item.status, theme.TEXT)}; font-weight: 600;")
        done = item.status == Status.COMPLETED
        self.btn_open.setEnabled(done and os.path.exists(item.path))
        f["Address"].setText(QFontMetrics(f["Address"].font()).elidedText(item.url, Qt.ElideMiddle,
                                                                          max(200, self.width() - 160)))
        f["Address"].setToolTip(item.url)
        f["Saved to"].setText(item.path or item.save_dir or "Decided when the download starts")
        f["Size"].setText(human_size(item.total) if item.total > 0 else "Unknown")
        f["Downloaded"].setText(f"{human_size(item.downloaded)}  ({item.progress * 100:.1f}%)"
                                if item.total > 0 else human_size(item.downloaded))
        f["Speed"].setText(human_speed(item.speed) or "—")
        f["Time left"].setText(human_eta(item.eta) or "—")
        if item.kind == "video":
            f["Resume"].setText("Yes (links refreshed on resume)")
        else:
            started = item.total != -1 or done
            f["Resume"].setText(("Yes" if item.resumable else "No") if started
                                else ("Checking…" if item.status in Status.ACTIVE else "—"))
        active = [s for s in item.segments if s.active]
        f["Connections"].setText(f"{len(active)} active of {item.connections}" if item.status == Status.DOWNLOADING
                                 else str(item.connections))
        self._fill_table(item)

    def _fill_table(self, item: DownloadItem) -> None:
        rows: list[tuple[str, str, str, str, str]] = []
        if item.kind == "video" and item.parts:
            for i, part in enumerate(item.parts, 1):
                got = sum(s.get("done", 0) for s in part.get("segments", []))
                rows.append((str(i), f"stream {part.get('format_id', '')}",
                             f"{human_size(got)} of {human_size(part.get('total', -1))}", "", ""))
        else:
            for i, s in enumerate(item.segments, 1):
                state = "receiving" if s.active and s.remaining else ("done" if not s.remaining else "waiting")
                rows.append((str(i), human_size(s.start), f"{human_size(s.done)} of {human_size(s.end - s.start + 1)}",
                             human_speed(s.speed) if s.active else "", state))
        t = self.table
        if t.rowCount() != len(rows):
            t.setRowCount(len(rows))
        live = QColor(theme.INK["Downloading"])
        muted = QColor(theme.MUTED)
        for r, values in enumerate(rows):
            for c, v in enumerate(values):
                cell = t.item(r, c)
                if cell is None:
                    cell = QTableWidgetItem()
                    t.setItem(r, c, cell)
                if cell.text() != v:
                    cell.setText(v)
                if c == 4:
                    cell.setForeground(live if v == "receiving" else muted)


class Banner(QFrame):
    """One-line notice above the list (update ready, video tools, …) with actions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("banner")
        self.icon = QLabel()
        self.text = QLabel("")
        self.text.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(160)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(4)
        self._buttons: list[QPushButton] = []
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(16, 8, 10, 8)
        self.row.setSpacing(12)
        self.row.addWidget(self.icon)
        self.row.addWidget(self.text, 1)
        self.row.addWidget(self.progress)
        self.hide()

    def show_message(self, text: str, icon_name: str = "update", actions=(), progress: float | None = None):
        self.icon.setPixmap(icons.pixmap(icon_name, theme.ACCENT, 18))
        self.text.setText(text)
        for b in self._buttons:
            b.deleteLater()
        self._buttons = []
        for label, slot, primary in actions:
            b = QPushButton(label)
            if primary:
                b.setObjectName("primary")
            b.clicked.connect(slot)
            self.row.addWidget(b)
            self._buttons.append(b)
        self.set_progress(progress)
        self.show()

    def set_progress(self, progress: float | None) -> None:
        self.progress.setVisible(progress is not None)
        if progress is not None:
            self.progress.setRange(0, 1000 if progress >= 0 else 0)
            if progress >= 0:
                self.progress.setValue(int(progress * 1000))
