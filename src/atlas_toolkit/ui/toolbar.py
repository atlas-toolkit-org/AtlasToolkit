"""
The tool list along the top of the window (Setup › General › Tool list: Top bar), instead of the side bar: one button per
category, and a panel under it with that category's tools, its subgroups side by side, all in view.

A click opens a panel; while one is open, pointing at another category shows that one. Arrow keys move (past the first
or last column: to the next category), Enter opens the tool, and Esc, a click elsewhere or opening a tool closes it.
The panel is a child of the window and not a Qt popup, whose mouse grab would stop the pointer reaching the bar.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from .sidebar import tool_tip
from .theme import icon, sans
from .widgets.common import ElidedLabel, apply_icon, icon_button, repolish

HEIGHT = 44
COLUMN_W = 236
COLUMNS = 3              # subgroups side by side; more wrap onto a second row
KEYS = (Qt.Key_Escape, Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Return, Qt.Key_Enter)


class _ToolButton(QPushButton):
    """A tool in the panel. Pointing at it or moving to it with the keys tells the panel."""
    pointed = Signal(object)

    def __init__(self, tool):
        super().__init__(f"  {tool.name}", checkable=True)
        self.tool = tool
        self.needs = ""
        self.setProperty("nav", "true")
        self.setProperty("inpanel", "true")
        self.setIconSize(QSize(17, 17))
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(30)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(6)
        lay.addStretch(1)
        self.slow, self.warn = QLabel(), QLabel()
        for mark in (self.slow, self.warn):
            mark.setFixedSize(14, 14)
            mark.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(mark)
        self.slow.setVisible(bool(tool.slow))

    def enterEvent(self, e):
        super().enterEvent(e)
        self.pointed.emit(self)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.pointed.emit(self)

    def refresh(self, needs: str, theme) -> None:
        self.needs = needs
        self.setProperty("unavailable", "true" if needs else "false")
        self.warn.setVisible(bool(needs))
        self.setIcon(icon(self.tool.icon, theme["text-4" if needs else "text-3"], 17,
                          active=theme["text-3" if needs else "highlight"]))
        self.slow.setPixmap(icon("clock", theme["text-3"], 14).pixmap(14, 14))
        self.warn.setPixmap(icon("warn", theme["warning"], 14).pixmap(14, 14))
        repolish(self)


class ToolPanel(QFrame):
    """One category's tools, laid over the page."""
    open_tool = Signal(str)

    def __init__(self, host: QWidget, ctx):
        super().__init__(host, objectName="toolPanel")
        self.ctx = ctx
        self.category: str | None = None
        self.pages: dict[str, QWidget] = {}
        self.buttons: dict[str, list[_ToolButton]] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget(objectName="transparent")
        self.holder = QVBoxLayout(holder)
        self.holder.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(holder)
        lay.addWidget(self.scroll, 1)
        foot = QFrame(objectName="toolPanelFoot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(20, 0, 20, 0)
        foot.setFixedHeight(38)
        self.note = ElidedLabel("")
        self.note.setProperty("class", "hint")
        fl.addWidget(self.note, 1)
        lay.addWidget(foot)
        for cat, subs in ctx.registry.by_group():
            self._page(cat, subs)
        self.hide()

    def _page(self, cat: str, subs: list) -> None:
        page = QWidget(objectName="transparent")
        grid = QGridLayout(page)
        grid.setContentsMargins(14, 12, 14, 10)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        self.buttons[cat] = []
        columns = min(COLUMNS, len(subs))
        for i, (_key, title, tools) in enumerate(subs):
            col = QVBoxLayout()
            col.setSpacing(1)
            if title:
                head = QLabel(title, objectName="toolPanelHead")
                head.setFont(sans(12.5, 600))
                head.setContentsMargins(14, 0, 0, 6)
                col.addWidget(head)
            for t in tools:
                b = _ToolButton(t)
                b.setFixedWidth(COLUMN_W)
                b.clicked.connect(lambda _=False, tid=t.id: self.open_tool.emit(tid))
                b.pointed.connect(self._pointed)
                col.addWidget(b)
                self.buttons[cat].append(b)
            col.addStretch(1)
            grid.addLayout(col, i // columns, i % columns)
        page.hide()
        self.holder.addWidget(page)
        self.pages[cat] = page

    # ------------------------------------------------------------------ showing
    def open_at(self, cat: str, anchor: QPoint) -> None:
        """Show `cat` with its left edge at `anchor` (host coordinates), kept inside the window."""
        self.category = cat
        for c, page in self.pages.items():
            page.setVisible(c == cat)
        page = self.pages[cat]
        for w in (page, *page.findChildren(QWidget)):            # sizes as styled, before the first show
            w.ensurePolished()
        page.layout().invalidate()
        size = page.sizeHint()
        host = self.parentWidget()
        room = host.height() - anchor.y() - 12
        width = max(size.width(), 420)
        height = size.height() + 38 + 2
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if height > room else Qt.ScrollBarAlwaysOff)
        if height > room:
            width += 12                                          # the scroll bar
        x = max(8, min(anchor.x(), host.width() - width - 8))
        self.setGeometry(x, anchor.y(), width, min(height, room))
        self.scroll.verticalScrollBar().setValue(0)
        n = len(self.buttons[cat])
        self.note.set_full_text(f"{n} tools" if n != 1 else "1 tool")
        self.show()
        self.raise_()

    def _pointed(self, b: _ToolButton) -> None:
        t = b.tool
        self.note.set_full_text(b.needs or "  ·  ".join(x for x in (t.summary, t.io_badge, t.slow_text) if x))
        self.note.setToolTip(tool_tip(t, b.needs, name=True))

    # ------------------------------------------------------------------ keys
    def current_button(self) -> _ToolButton | None:
        w = QApplication.focusWidget()
        return w if isinstance(w, _ToolButton) and w in self.buttons.get(self.category, []) else None

    def focus_first(self) -> None:
        buttons = self.buttons[self.category]
        b = next((b for b in buttons if b.isChecked()), buttons[0])
        b.setFocus(Qt.TabFocusReason)
        self.scroll.ensureWidgetVisible(b, 0, 30)

    def step(self, key) -> bool:
        """Move to the nearest tool in that direction; False when there is none."""
        now = self.current_button()
        if now is None:
            self.focus_first()
            return True
        page = self.pages[self.category]
        at = now.mapTo(page, now.rect().center())
        best = None
        for b in self.buttons[self.category]:
            d = b.mapTo(page, b.rect().center()) - at
            along, across = {Qt.Key_Left: (-d.x(), d.y()), Qt.Key_Right: (d.x(), d.y()),
                             Qt.Key_Up: (-d.y(), d.x()), Qt.Key_Down: (d.y(), d.x())}[key]
            if along > 4 and not (key in (Qt.Key_Up, Qt.Key_Down) and abs(across) > 4):
                cost = along * (4 if key in (Qt.Key_Left, Qt.Key_Right) else 1) + abs(across)
                if best is None or cost < best[0]:
                    best = (cost, b)
        if best is None:
            return False
        best[1].setFocus(Qt.TabFocusReason)
        self.scroll.ensureWidgetVisible(best[1], 0, 30)
        return True

    def refresh(self, current: str | None, theme) -> None:
        for buttons in self.buttons.values():
            for b in buttons:
                b.setChecked(b.tool.id == current)
                b.refresh(self.ctx.needs_text(b.tool), theme)


class ToolBar(QFrame):
    open_tool = Signal(str)
    move_to_side = Signal()

    def __init__(self, ctx, host: QWidget):
        super().__init__(objectName="toolbar")
        self.ctx, self.host, self.theme = ctx, host, ctx.theme
        self.current: str | None = None
        self.setFixedHeight(HEIGHT)
        hl = QHBoxLayout(self)
        hl.setContentsMargins(14, 0, 16, 0)
        hl.setSpacing(2)
        self.cats: dict[str, QPushButton] = {}
        for cat, _subs in ctx.registry.by_group():
            b = QPushButton(cat)
            b.setProperty("toolcat", "true")
            b.setLayoutDirection(Qt.RightToLeft)                 # the chevron after the title
            b.setIconSize(QSize(14, 14))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, c=cat: self.toggle(c))
            hl.addWidget(b)
            self.cats[cat] = b
        self.btn_broken = QPushButton(f"{len(ctx.registry.broken)} failed to load")
        self.btn_broken.setProperty("toolcat", "true")
        self.btn_broken.setProperty("broken", "true")
        self.btn_broken.setToolTip("\n\n".join(f"{b.name}: {b.error.splitlines()[0]}" for b in ctx.registry.broken))
        self.btn_broken.clicked.connect(lambda: QMessageBox.warning(
            self, "Tools that failed to load", "\n\n".join(f"{b.name}\n{b.error}" for b in ctx.registry.broken)))
        self.btn_broken.setVisible(bool(ctx.registry.broken))
        hl.addWidget(self.btn_broken)
        hl.addSpacing(12)
        self.place = ElidedLabel("")                             # where the open tool is listed
        self.place.setProperty("class", "muted")
        self.place.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.place.setCursor(Qt.PointingHandCursor)
        hl.addWidget(self.place, 1)
        hl.addSpacing(8)
        self.btn_side = icon_button("sidebar", "Move the tool list to the side of the window", size=16)
        self.btn_side.clicked.connect(self.move_to_side)
        hl.addWidget(self.btn_side)
        self.panel = ToolPanel(host, ctx)
        self.panel.open_tool.connect(self._open)
        for w in (*self.cats.values(), self.place):
            w.installEventFilter(self)
        self.refresh_availability()

    # ------------------------------------------------------------------ the panel
    @property
    def opened(self) -> str | None:
        return self.panel.category if self.panel.isVisible() else None

    def toggle(self, cat: str) -> None:
        if self.opened == cat:
            self.close_panel()
        else:
            self.show_panel(cat)

    def show_panel(self, cat: str) -> None:
        was = self.opened
        self.panel.refresh(self.current, self.theme)
        b = self.cats[cat]
        self.panel.open_at(cat, b.mapTo(self.host, QPoint(0, b.height())))
        if was is None:
            QApplication.instance().installEventFilter(self)
        self._buttons()

    def close_panel(self) -> None:
        if self.opened is None:
            return
        QApplication.instance().removeEventFilter(self)
        focused = self.panel.current_button() is not None
        self.panel.hide()
        if focused:
            self.window().setFocus()
        self._buttons()

    def _open(self, tool_id: str) -> None:
        self.close_panel()
        self.open_tool.emit(tool_id)

    def _beside(self, step: int) -> None:
        cats = list(self.cats)
        self.show_panel(cats[(cats.index(self.opened) + step) % len(cats)])
        self.panel.focus_first()

    def _key(self, key) -> None:
        if key == Qt.Key_Escape:
            self.close_panel()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            b = self.panel.current_button()
            if b is not None:
                b.click()
        elif not self.panel.step(key) and key in (Qt.Key_Left, Qt.Key_Right):
            self._beside(-1 if key == Qt.Key_Left else 1)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        kind = e.type()
        if obj in self.cats.values():
            if kind == QEvent.Enter and self.opened is not None:
                cat = next(c for c, b in self.cats.items() if b is obj)
                if cat != self.opened:
                    self.show_panel(cat)
        elif obj is self.place and kind == QEvent.MouseButtonRelease and self.current is not None:
            tool = self.ctx.registry.tools.get(self.current)
            if tool is not None:
                self.toggle(tool.category_title)
            return True
        if self.opened is None or not isinstance(obj, QWidget) or obj.window() is not self.window():
            return super().eventFilter(obj, e)
        if kind == QEvent.MouseButtonPress:
            inside = obj is self.panel or self.panel.isAncestorOf(obj) or obj is self or self.isAncestorOf(obj)
            if not inside:
                self.close_panel()
        elif kind == QEvent.ShortcutOverride and e.key() in KEYS and not e.modifiers() & ~Qt.KeypadModifier:
            e.accept()                                           # before the window's own Esc
            return True
        elif kind == QEvent.KeyPress and e.key() in KEYS and not e.modifiers() & ~Qt.KeypadModifier:
            self._key(e.key())
            return True
        elif kind in (QEvent.WindowDeactivate, QEvent.Resize) and obj is self.window():
            self.close_panel()
        return super().eventFilter(obj, e)

    def hideEvent(self, e):
        self.close_panel()
        super().hideEvent(e)

    # ------------------------------------------------------------------ state
    def set_current(self, tool_id: str | None) -> None:
        self.current = tool_id
        tool = self.ctx.registry.tools.get(tool_id) if tool_id else None
        if tool is None:
            self.place.set_full_text(f"{len(self.ctx.registry.tools)} tools")
            self.place.setToolTip("")
        else:
            self.place.set_full_text(f"{tool.group_title} › {tool.name}" if tool.group_title else tool.name)
            self.place.setToolTip(f"{tool.place} › {tool.name}")
        self.close_panel()
        self._buttons()

    def _buttons(self) -> None:
        tool = self.ctx.registry.tools.get(self.current) if self.current else None
        for cat, b in self.cats.items():
            on = tool is not None and tool.category_title == cat
            b.setProperty("current", "true" if on else "false")
            b.setProperty("open", "true" if self.opened == cat else "false")
            b.setIcon(icon("up" if self.opened == cat else "down", self.theme["text-3"], 14))
            repolish(b)

    def refresh_availability(self) -> None:
        self.panel.refresh(self.current, self.theme)

    def retheme(self, theme) -> None:
        self.theme = theme
        apply_icon(self.btn_side, theme)
        self.panel.refresh(self.current, theme)
        self._buttons()
