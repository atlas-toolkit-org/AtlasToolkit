"""
Command palette under the top-bar search box: type to find a tool or an action; ↑ ↓ to move, Enter to run,
Esc to close. Opened with / or Ctrl+K.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import QFrame, QListWidget, QListWidgetItem, QVBoxLayout

from .theme import icon


class Palette(QFrame):
    def __init__(self, host, search, theme):
        super().__init__(host)
        self.setObjectName("palette")
        self.search = search
        self.theme = theme
        self.entries: list = []         # (title, subtitle, icon, callback)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.itemClicked.connect(self._activate_item)
        lay.addWidget(self.list)
        self.hide()
        search.textEdited.connect(self._filter)
        search.installEventFilter(self)

    def set_entries(self, entries: list) -> None:
        self.entries = entries

    def open(self) -> None:
        self.search.setFocus()
        self.search.selectAll()
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        q = text.strip().lower()
        self.list.clear()
        for title, sub, ic, cb in self.entries:
            hay = f"{title} {sub}".lower()
            if not q or all(w in hay for w in q.split()):
                it = QListWidgetItem(icon(ic, self.theme["text-2"], 16), f"{title}    {sub}" if sub else title)
                it.setData(Qt.UserRole, cb)
                self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)
            self._place()
            self.show()
            self.raise_()
        else:
            self.hide()

    def _place(self) -> None:
        host = self.parentWidget()
        pos = self.search.mapTo(host, QPoint(0, self.search.height() + 6))
        w = max(self.search.width(), 420)
        rows = min(self.list.count(), 10)
        self.setGeometry(pos.x(), pos.y(), w, rows * 34 + 16)

    def _activate_item(self, it) -> None:
        cb = it.data(Qt.UserRole)
        self.close_palette()
        if cb:
            cb()

    def close_palette(self) -> None:
        self.hide()
        self.search.clear()
        self.search.clearFocus()

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if obj is self.search:
            if e.type() == QEvent.FocusIn:
                self._filter(self.search.text())
            elif e.type() == QEvent.FocusOut:
                if not self.list.underMouse():
                    self.hide()
            elif e.type() == QEvent.KeyPress:
                k = e.key()
                if k == Qt.Key_Escape:
                    self.close_palette()
                    return True
                if k in (Qt.Key_Down, Qt.Key_Up) and self.isVisible():
                    r = self.list.currentRow() + (1 if k == Qt.Key_Down else -1)
                    self.list.setCurrentRow(max(0, min(self.list.count() - 1, r)))
                    return True
                if k in (Qt.Key_Return, Qt.Key_Enter) and self.isVisible() and self.list.currentItem():
                    self._activate_item(self.list.currentItem())
                    return True
        return super().eventFilter(obj, e)
