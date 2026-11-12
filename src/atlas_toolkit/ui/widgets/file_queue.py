"""The input queue: one row per file with its status and the tool's key numbers, and a drop zone when empty."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QTreeWidget, QTreeWidgetItem

from ..theme import mono, qcolor
from .common import repolish

STATUS_TONES = {"accent": "accent-text", "muted": "text-2", "busy": "highlight", "success": "success",
                "warning": "warning", "danger": "danger", "dim": "text-3"}


class FileQueue(QTreeWidget):
    def __init__(self, theme, columns: list[tuple[str, str]], drop_text: str):
        super().__init__()
        self.theme = theme
        self.columns = columns                      # [(stat key, header)]
        heads = ["File", "Status"] + [h for _, h in columns]
        self.setColumnCount(len(heads))
        self.setHeaderLabels([h.upper() for h in heads])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setTextElideMode(Qt.ElideMiddle)     # keep the -v2 / A3 endings of long names visible
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setAllColumnsShowFocus(True)
        h = self.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setMinimumSectionSize(60)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        self.setColumnWidth(1, 88)
        for i in range(2, len(heads)):
            h.setSectionResizeMode(i, QHeaderView.Interactive)
            self.setColumnWidth(i, 84)
        self.drop = QLabel(drop_text, self.viewport())
        self.drop.setObjectName("drop")
        self.drop.setAlignment(Qt.AlignCenter)
        self.drop.setWordWrap(True)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.drop.setGeometry(self.viewport().rect().adjusted(14, 14, -14, -14))

    def set_drop_active(self, on: bool) -> None:
        self.drop.setProperty("active", "true" if on else "false")
        repolish(self.drop)

    def set_header_labels(self, labels: dict) -> None:
        heads = ["File", "Status"] + [labels.get(k, h) for k, h in self.columns]
        self.setHeaderLabels([x.upper() for x in heads])

    def add_row(self, key: str, name: str) -> QTreeWidgetItem:
        it = QTreeWidgetItem([name, ""] + [""] * len(self.columns))
        it.setData(0, Qt.UserRole, key)
        it.setToolTip(0, key)
        f = mono(11.5)
        for c in range(2, 2 + len(self.columns)):
            it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
            it.setFont(c, f)
        it.setFont(1, mono(11))
        self.addTopLevelItem(it)
        return it

    def set_status(self, it: QTreeWidgetItem, text: str, tone: str = "muted", tip: str = "") -> None:
        it.setText(1, text)
        it.setForeground(1, qcolor(self.theme[STATUS_TONES.get(tone, "text-2")]))
        it.setToolTip(1, tip)

    def set_cells(self, it: QTreeWidgetItem, result) -> None:
        for i, (key, _h) in enumerate(self.columns):
            st = result.stat(key) if result is not None else None
            c = 2 + i
            if st is None:
                it.setText(c, "")
                continue
            it.setText(c, st.display())
            it.setForeground(c, qcolor(self.theme[{"warning": "warning", "danger": "danger",
                                                   "success": "success"}.get(st.tone, "text")]))

    def clear_cells(self, it: QTreeWidgetItem) -> None:
        for c in range(2, 2 + len(self.columns)):
            it.setText(c, "")

    def keys(self) -> list[str]:
        return [self.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.topLevelItemCount())]

    def selected_keys(self) -> list[str]:
        return [it.data(0, Qt.UserRole) for it in self.selectedItems()]

    def retheme(self, theme) -> None:
        self.theme = theme

    @staticmethod
    def color(theme, tone: str) -> QColor:
        return qcolor(theme[STATUS_TONES.get(tone, "text-2")])
