from __future__ import annotations

import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from ..engine import DownloadItem, DownloadManager, Status
from ..utils import human_eta, human_size, human_speed
from . import icons, theme

COLUMNS = ["File name", "Size", "Progress", "Speed", "Time left", "Status", "Added"]
COL_NAME, COL_SIZE, COL_PROGRESS, COL_SPEED, COL_ETA, COL_STATUS, COL_ADDED = range(7)

FILTERS = {
    "All downloads": lambda i: True,
    "Downloading": lambda i: i.status in Status.ACTIVE,
    "Unfinished": lambda i: i.status != Status.COMPLETED,
    "Finished": lambda i: i.status == Status.COMPLETED,
    "Queued": lambda i: i.status == Status.QUEUED,
}

_SORT_KEYS = {
    COL_NAME: lambda i: (i.filename or i.title or i.url).lower(),
    COL_SIZE: lambda i: i.total,
    COL_PROGRESS: lambda i: i.progress,
    COL_SPEED: lambda i: i.speed,
    COL_ETA: lambda i: i.eta if i.eta >= 0 else float("inf"),
    COL_STATUS: lambda i: i.status,
    COL_ADDED: lambda i: i.added,
}


class DownloadsModel(QAbstractTableModel):
    def __init__(self, manager: DownloadManager):
        super().__init__()
        self.manager = manager
        self.filter_name = "All downloads"
        self.search = ""
        self.sort_col, self.sort_order = COL_ADDED, Qt.DescendingOrder
        self.ids: list[str] = []
        self._num = theme.number_font()
        self._bold = QFont(theme.FONT_FAMILY)
        self._bold.setWeight(QFont.DemiBold)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.ids)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal:
            return None
        if role == Qt.DisplayRole:
            return COLUMNS[section]
        if role == Qt.FontRole:
            return theme.label_font()
        if role == Qt.TextAlignmentRole:
            right = section in (COL_SIZE, COL_SPEED, COL_ETA)
            return int((Qt.AlignRight if right else Qt.AlignLeft) | Qt.AlignVCenter)
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        self.sort_col, self.sort_order = column, order
        self.refresh(force=True)

    def item_at(self, row: int) -> DownloadItem | None:
        if 0 <= row < len(self.ids):
            return self.manager.get(self.ids[row])
        return None

    def matches(self, item: DownloadItem) -> bool:
        f = FILTERS.get(self.filter_name)
        if not (f(item) if f else item.category == self.filter_name):
            return False
        if self.search:
            hay = f"{item.filename} {item.title} {item.url}".lower()
            return all(word in hay for word in self.search.lower().split())
        return True

    def refresh(self, force: bool = False) -> bool:
        """Returns True when the row set changed (selection must be restored)."""
        key = _SORT_KEYS.get(self.sort_col, _SORT_KEYS[COL_ADDED])
        items = sorted(self.manager.visible(), key=key, reverse=self.sort_order == Qt.DescendingOrder)
        new_ids = [i.id for i in items if self.matches(i)]
        if new_ids != self.ids or force:
            self.beginResetModel()
            self.ids = new_ids
            self.endResetModel()
            return True
        if self.ids:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.ids) - 1, len(COLUMNS) - 1))
        return False

    def data(self, index, role=Qt.DisplayRole):
        item = self.item_at(index.row())
        if item is None:
            return None
        col = index.column()
        if role == Qt.DisplayRole:
            if col == COL_NAME:
                return item.filename or item.title or item.url
            if col == COL_SIZE:
                return human_size(item.total) if item.total > 0 else ("…" if item.status in Status.ACTIVE else "")
            if col == COL_PROGRESS:
                return item.progress
            if col == COL_SPEED:
                return human_speed(item.speed) if item.status == Status.DOWNLOADING else ""
            if col == COL_ETA:
                return human_eta(item.eta) if item.status == Status.DOWNLOADING else ""
            if col == COL_STATUS:
                return item.error if item.status == Status.ERROR and item.error else item.status
            if col == COL_ADDED:
                return time.strftime("%d %b  %H:%M", time.localtime(item.added))
        if role == Qt.ForegroundRole:
            if col == COL_STATUS:
                return theme.ink(item.status)
            if col in (COL_SIZE, COL_ADDED, COL_ETA):
                return QColor(theme.MUTED)
        if role == Qt.FontRole:
            if col == COL_NAME and not item.seen:
                return self._bold
            if col in (COL_SIZE, COL_SPEED, COL_ETA, COL_ADDED):
                return self._num
        if role == Qt.TextAlignmentRole and col in (COL_SIZE, COL_SPEED, COL_ETA):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.DecorationRole and col == COL_NAME:
            return icons.icon(icons.CATEGORY_ICONS.get(item.category, "other"), theme.MUTED, 16)
        if role == Qt.ToolTipRole:
            lines = [item.url]
            if item.path:
                lines.append(item.path)
            if item.error:
                lines.append("Error: " + item.error)
            return "\n".join(lines)
        if role == Qt.UserRole:
            return item
        return None


# ------------------------------------------------------------------- the meter
def coverage(item: DownloadItem, n: int) -> list[float]:
    """How full each of n equal slices of the file is (0..1), from the real segment map."""
    if item.status == Status.COMPLETED:
        return [1.0] * n
    total = item.total
    if total <= 0 or n <= 0:
        return [0.0] * n
    if item.kind == "file" and item.segments:
        cov = [0.0] * n
        size = total / n
        for s in item.segments:
            a, b = s.start, s.start + s.done
            if b <= a:
                continue
            for i in range(int(a / size), min(n - 1, int((b - 1) / size)) + 1):
                cov[i] += (min(b, (i + 1) * size) - max(a, i * size)) / size
        return [min(1.0, c) for c in cov]
    filled = item.downloaded / total * n
    return [max(0.0, min(1.0, filled - i)) for i in range(n)]


def heads(item: DownloadItem, n: int) -> set[int]:
    """Blocks where a connection is writing right now."""
    if item.kind != "file" or item.total <= 0 or item.status != Status.DOWNLOADING:
        return set()
    size = item.total / n
    return {min(n - 1, int(s.pos / size)) for s in item.segments if s.active and s.remaining > 0}


class MeterDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._font = theme.number_font(8.5)

    def paint(self, painter: QPainter, option, index):
        item: DownloadItem = index.data(Qt.UserRole)
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(theme.SELECT))
        if item is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(6, 0, -8, 0)
        text_w = 40
        bar = QRectF(r.x(), r.center().y() - 7, max(10.0, r.width() - text_w - 6), 14)
        from .hudbar import draw_hud_bar
        color = draw_hud_bar(painter, bar, item)
        painter.setFont(self._font)
        painter.setPen(color if item.status in Status.ACTIVE else QColor(theme.MUTED))
        label = f"{item.progress * 100:.0f}%" if item.total > 0 or item.status == Status.COMPLETED else ""
        painter.drawText(QRectF(r.right() - text_w, r.y(), text_w, r.height()),
                         Qt.AlignVCenter | Qt.AlignRight, label)
        painter.restore()


class NameDelegate(QStyledItemDelegate):
    """Category glyph + name; a finished file you haven't looked at yet keeps its LED lit."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        item: DownloadItem = index.data(Qt.UserRole)
        if item is not None and not item.seen and item.status == Status.COMPLETED:
            option.rect = option.rect.adjusted(0, 0, -18, 0)      # room for the LED

    def paint(self, painter: QPainter, option, index):
        super().paint(painter, option, index)
        item: DownloadItem = index.data(Qt.UserRole)
        if item is not None and not item.seen and item.status == Status.COMPLETED:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            lime = theme.ink(Status.COMPLETED)
            c = option.rect.right() - 10, option.rect.center().y()
            glow = QColor(lime)
            glow.setAlphaF(0.22)
            painter.setBrush(glow)
            painter.drawEllipse(QRectF(c[0] - 6, c[1] - 6, 12, 12))
            painter.setBrush(lime)
            painter.drawEllipse(QRectF(c[0] - 3, c[1] - 3, 6, 6))
            painter.restore()
