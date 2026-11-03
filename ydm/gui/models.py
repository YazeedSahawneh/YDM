from __future__ import annotations

import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from ..config import ACCENT
from ..engine import DownloadItem, DownloadManager, Status
from ..utils import human_eta, human_size, human_speed
from . import theme

COLUMNS = ["Name", "Size", "Progress", "Speed", "Time left", "Status", "Added"]
COL_PROGRESS = 2

FILTERS = {
    "All": lambda i: True,
    "Downloading": lambda i: i.status in Status.ACTIVE,
    "Queued": lambda i: i.status == Status.QUEUED,
    "Unfinished": lambda i: i.status != Status.COMPLETED,
    "Completed": lambda i: i.status == Status.COMPLETED,
}


class DownloadsModel(QAbstractTableModel):
    def __init__(self, manager: DownloadManager):
        super().__init__()
        self.manager = manager
        self.filter_name = "All"
        self.ids: list[str] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.ids)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def item_at(self, row: int) -> DownloadItem | None:
        if 0 <= row < len(self.ids):
            return self.manager.get(self.ids[row])
        return None

    def matches(self, item: DownloadItem) -> bool:
        f = FILTERS.get(self.filter_name)
        return f(item) if f else item.category == self.filter_name

    def refresh(self) -> bool:
        """Returns True when the row set changed (selection must be restored)."""
        items = sorted(self.manager.items(), key=lambda i: i.added, reverse=True)
        new_ids = [i.id for i in items if self.matches(i)]
        if new_ids != self.ids:
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
            if col == 0:
                return item.filename or item.title or item.url
            if col == 1:
                return human_size(item.total) if item.total > 0 else ("…" if item.status in Status.ACTIVE else "")
            if col == COL_PROGRESS:
                return item.progress
            if col == 3:
                return human_speed(item.speed) if item.status == Status.DOWNLOADING else ""
            if col == 4:
                return human_eta(item.eta) if item.status == Status.DOWNLOADING else ""
            if col == 5:
                return item.error if item.status == Status.ERROR and item.error else item.status
            if col == 6:
                return time.strftime("%d %b %H:%M", time.localtime(item.added))
        if role == Qt.ForegroundRole and col == 5:
            return QColor(theme.STATUS_COLORS.get(item.status, theme.TEXT))
        if role == Qt.ToolTipRole:
            lines = [item.url]
            if item.path:
                lines.append(item.path)
            if item.error:
                lines.append("Error: " + item.error)
            if item.resumable and item.segments:
                lines.append(f"{sum(1 for s in item.segments if s.remaining)} active parts")
            return "\n".join(lines)
        if role == Qt.UserRole:
            return item
        return None


class ProgressDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index):
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(theme.SURFACE_2))
        elif index.row() % 2:
            painter.fillRect(option.rect, QColor(theme.ALT_ROW))
        item: DownloadItem = index.data(Qt.UserRole)
        value = float(index.data(Qt.DisplayRole) or 0)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(8, option.rect.height() / 2 - 4, -54, -(option.rect.height() / 2 - 4))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BORDER))
        painter.drawRoundedRect(r, 4, 4)
        if value > 0:
            color = theme.STATUS_COLORS.get(item.status if item else "", ACCENT)
            fill = QRectF(r.x(), r.y(), max(8.0, r.width() * value), r.height())
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(fill, 4, 4)
        painter.setPen(QColor(theme.MUTED))
        text_rect = QRectF(r.right() + 6, option.rect.y(), 44, option.rect.height())
        label = f"{value * 100:.0f}%" if (item and item.total > 0) or value >= 1 else ""
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, label)
        painter.restore()
