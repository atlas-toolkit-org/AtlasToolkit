"""
FlowLayout: widgets left to right that wrap onto the next line when the width runs out, so a row of buttons
never makes a window wider than it is (Setup › Components, Setup › Presets).
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, spacing: int = 8):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    # the QLayout contract
    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._place(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._place(rect, move=True)

    def sizeHint(self) -> QSize:                   # everything on one line
        w = sum(i.sizeHint().width() for i in self._items) + self._spacing * max(0, len(self._items) - 1)
        h = max((i.sizeHint().height() for i in self._items), default=0)
        m = self.contentsMargins()
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self) -> QSize:                # one widget a line: as narrow as the widest widget
        s = QSize()
        for i in self._items:
            s = s.expandedTo(i.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _place(self, rect: QRect, move: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            size = item.sizeHint()
            if x > area.x() and x + size.width() > area.right() + 1:      # wrap
                x, y, line = area.x(), y + line + self._spacing, 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), QSize(min(size.width(), area.width()), size.height())))
            x += size.width() + self._spacing
            line = max(line, size.height())
        return y + line - rect.y() + m.bottom()
