"""
The tool list beside Home, the tool pages and Activity: tools by category and subgroup (core/tool.GROUPS) under headers
that fold and unfold, shown in full or as a narrow rail of icons (groups split by hairlines), or hidden by the shell.
A tool whose required program is missing (AppContext.lacking) is faded, with an amber warning mark and a tooltip that
says what is missing; one that can be slow (Tool.slow) has a quiet clock. What is folded and the mode are kept in the
settings ("sidebar_collapsed": categories, "sidebar_folded": subgroups, "sidebar_mode").
ui/toolbar.py is the same list along the top of the window.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)
from shiboken6 import isValid

from ..version import __version__
from .theme import icon, mono, sans
from .widgets.common import apply_icon, hairline, icon_button, label, repolish

SIDEBAR_W = 264
RAIL_W = 58


def group_font() -> QFont:
    """The group headers: Geist semibold, uppercase, tracked, a step above the tool names."""
    f = sans(12, 600)
    f.setLetterSpacing(QFont.AbsoluteSpacing, 1.1)
    return f


class GroupHead(QPushButton):
    """A category header: chevron, title and tool count; a click folds or unfolds the group."""

    CHEV = 14

    def __init__(self, title: str, count: int):
        super().__init__()
        self.setProperty("navgroup", "true")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(34)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 12, 0)
        lay.setSpacing(8)
        self.chev = QLabel()
        self.chev.setFixedSize(14, 14)
        self.title = QLabel(title.upper(), objectName="navGroupTitle")
        self.title.setFont(group_font())
        self.count = QLabel(str(count), objectName="navGroupCount")
        self.count.setFont(mono(10.5))
        for w in (self.chev, self.title, self.count):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self.chev)
        lay.addWidget(self.title, 1)
        lay.addWidget(self.count)

    def set_state(self, open_: bool, active: bool, theme) -> None:
        self.setProperty("active", "true" if active else "false")
        self.setToolTip("Fold this group" if open_ else "Unfold this group")
        self.chev.setPixmap(icon("down" if open_ else "right", theme["highlight" if active else "text-3"], self.CHEV)
                            .pixmap(self.CHEV, self.CHEV))
        repolish(self)
        for w in (self.title, self.count):
            repolish(w)


class SubHead(GroupHead):
    """A subgroup's header, a step below the category's: smaller, quieter, in sentence case and indented."""
    CHEV = 12

    def __init__(self, title: str, count: int, indent: int = 24):
        super().__init__(title, count)
        self.setProperty("navsub", "true")
        self.setFixedHeight(30)
        self.layout().setContentsMargins(indent, 0, 12, 0)
        self.layout().setSpacing(7)
        self.chev.setFixedSize(self.CHEV, self.CHEV)
        self.title.setText(title)
        self.title.setFont(sans(12.5, 500))


def saved_folds(settings, key: str) -> set[str]:
    """The folded categories a list saved ("Mesh · model prep" was Mesh → Mesh's name before 2.5.2)."""
    return {"Mesh → Mesh" if c == "Mesh · model prep" else c for c in settings.get(key) or []}


def saved_subfolds(settings, key: str, registry) -> set[str]:
    """The folded subgroups a list saved, or those a first start folds."""
    saved = settings.get(key)
    return set(saved) if isinstance(saved, list) else registry.folded_at_first()


def tool_tip(tool, needs: str = "", tail: str = "", name: bool = False) -> str:
    """A tool's tooltip in any list: what it lacks, its summary, whether it can be slow."""
    parts = [f"⚠ {needs}" if needs else "", (f"{tool.name}\n" if name else "") + tool.summary, tool.slow_text, tail]
    return "\n\n".join(p for p in parts if p)


class Sidebar(QWidget):
    open_tool = Signal(str)
    move_to_top = Signal()               # the caption's button: show the list along the top instead

    def __init__(self, ctx):
        super().__init__(objectName="sidebar")
        self.ctx = ctx
        self.theme = ctx.theme
        self.nav: dict[str, QPushButton] = {}
        self.groups: list[tuple[str, GroupHead, list[QPushButton], QWidget]] = []
        self.subs: list[tuple[str, str, SubHead, list[QPushButton], QWidget]] = []   # (key, category, ...)
        self.current: str | None = None
        self.collapsed: set[str] = saved_folds(ctx.settings, "sidebar_collapsed")
        self.folded: set[str] = saved_subfolds(ctx.settings, "sidebar_folded", ctx.registry)
        self.mode: str = ctx.settings.get("sidebar_mode") or "full"
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.top = QWidget(objectName="transparent")
        tl = QHBoxLayout(self.top)
        tl.setContentsMargins(20, 10, 8, 4)
        tl.setSpacing(2)
        self.caption = label("Tools")
        tl.addWidget(self.caption, 1)
        self.btn_fold = icon_button("up", "Fold every group", size=16)
        self.btn_fold.clicked.connect(self.fold_all)
        self.btn_rail = icon_button("left", "Icons only  ( ] )", size=16)
        self.btn_rail.clicked.connect(lambda: self.set_mode("icons" if self.mode == "full" else "full"))
        self.btn_top = icon_button("topbar", "Move the tool list to the top of the window", size=16)
        self.btn_top.clicked.connect(self.move_to_top)
        tl.addWidget(self.btn_top)
        tl.addWidget(self.btn_fold)
        tl.addWidget(self.btn_rail)
        outer.addWidget(self.top)

        self.scroll = scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="transparent")
        self.body = QVBoxLayout(body)
        self.body.setSpacing(2)
        grp = QButtonGroup(self)
        n = 0
        for cat, subgroups in ctx.registry.by_group():
            if self.groups:
                self.body.addSpacing(6)
            line = hairline()
            self.body.addWidget(line)
            head = GroupHead(cat, sum(len(ts) for _k, _t, ts in subgroups))
            head.clicked.connect(lambda _=False, c=cat: self.toggle_group(c))
            self.body.addWidget(head)
            buttons = []
            for key, title, tools in subgroups:
                sub_line = QWidget(objectName="subRule")
                sub_line.setFixedSize(22, 1)
                self.body.addWidget(sub_line, 0, Qt.AlignHCenter)
                sub_line.hide()
                first = len(buttons)
                if title:
                    sub = SubHead(title, len(tools))
                    sub.clicked.connect(lambda _=False, k=key: self.toggle_sub(k))
                    self.body.addWidget(sub)
                for t in tools:
                    n += 1
                    b = self._tool_button(t, f"Alt+{n}" if n < 10 else "", bool(title))
                    grp.addButton(b)
                    self.body.addWidget(b)
                    self.nav[t.id] = b
                    buttons.append(b)
                if title:
                    self.subs.append((key, cat, sub, buttons[first:], sub_line))
            self.groups.append((cat, head, buttons, line))
        for br in ctx.registry.broken:
            b = QPushButton()
            b.setProperty("nav", "true")
            b.setProperty("broken", "true")
            b._icon_name = "warn"
            b._name = br.name
            b._tip = br.error
            b.clicked.connect(lambda _=False, e=br.error, nm=br.name: QMessageBox.warning(self, nm, e))
            self.body.addWidget(b)
            self.nav[f"!{br.name}"] = b
        self.body.addStretch(1)
        self.ver = QLabel(f"{len(ctx.registry.tools)} tools · v{__version__}")
        self.ver.setFont(mono(10.5))
        self.ver.setProperty("class", "mono")
        self.ver.setContentsMargins(14, 0, 0, 0)
        self.body.addWidget(self.ver)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.refresh_availability(apply=False)
        self.apply()

    def _tool_button(self, t, shortcut: str, nested: bool) -> QPushButton:
        b = QPushButton(checkable=True)
        b.setProperty("nav", "true")
        b.setProperty("nested", "true" if nested else "false")
        b.setIconSize(QSize(17, 17))
        b._icon_name = t.icon
        b._name = t.name
        b._shortcut = shortcut
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda _=False: self.open_tool.emit(t.id))
        b._tool = t
        b._needs = ""
        wl = QHBoxLayout(b)                     # the marks, right of the name; each with its own tooltip
        wl.setContentsMargins(0, 0, 10, 0)
        wl.setSpacing(6)
        wl.addStretch(1)
        b._slow = QLabel()
        b._warn = QLabel()
        for mark in (b._slow, b._warn):
            mark.setFixedSize(14, 14)
            mark.hide()
            wl.addWidget(mark)
        b._slow.setToolTip(t.slow_text)
        return b

    def refresh_availability(self, apply: bool = True) -> None:
        """Fade the tools whose required programs are missing; called again when that changes."""
        for b in self.nav.values():
            t = getattr(b, "_tool", None)
            if t is None:
                continue
            b._needs = self.ctx.needs_text(t)
            b.setProperty("unavailable", "true" if b._needs else "false")
            b._warn.setToolTip(b._needs)
        if apply:
            self.apply()
            self.retheme(self.theme)

    # ------------------------------------------------------------------ state
    @property
    def rail(self) -> bool:
        return self.mode == "icons"

    def set_mode(self, mode: str) -> None:
        self.mode = "icons" if mode == "icons" else "full"
        self.ctx.settings.set("sidebar_mode", self.mode)
        self.apply()

    def toggle_group(self, cat: str) -> None:
        self.collapsed ^= {cat}
        self._save_collapsed()

    def toggle_sub(self, key: str) -> None:
        self.folded ^= {key}
        self._save_collapsed()

    def fold_all(self) -> None:
        """Fold every category and subgroup, or unfold them all when every category already is."""
        cats = {c for c, *_ in self.groups}
        folded = cats <= self.collapsed
        self.collapsed = set() if folded else cats
        self.folded = set() if folded else {k for k, *_ in self.subs}
        self._save_collapsed()

    def _save_collapsed(self) -> None:
        self.ctx.settings.set("sidebar_collapsed", sorted(self.collapsed))
        self.ctx.settings.set("sidebar_folded", sorted(self.folded))
        self.apply()

    def _reveal(self, tool_id: str) -> None:
        """Unfold the groups that hold a tool opened from elsewhere, and scroll to it."""
        b = self.nav.get(tool_id)
        cats = {c for c, _h, bs, _l in self.groups if b in bs} & self.collapsed
        subs = {k for k, _c, _h, bs, _l in self.subs if b in bs} & self.folded
        if cats or subs:
            self.collapsed -= cats
            self.folded -= subs
            self._save_collapsed()
        if b is not None:
            QTimer.singleShot(0, lambda: isValid(b) and b.isVisible() and self.scroll.ensureWidgetVisible(b, 0, 40))

    def set_current(self, tool_id: str | None) -> None:
        moved, self.current = tool_id != self.current, tool_id
        if moved and tool_id is not None:
            self._reveal(tool_id)
        for k, b in self.nav.items():
            b.setChecked(k == tool_id)
        self._heads()

    # ------------------------------------------------------------------ layout
    def apply(self) -> None:
        rail = self.rail
        self.setFixedWidth(RAIL_W if rail else SIDEBAR_W)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff if rail else Qt.ScrollBarAsNeeded)  # wheel scrolls
        self.body.setContentsMargins(*((6, 4, 6, 10) if rail else (10, 4, 10, 14)))
        self.top.layout().setContentsMargins(*((0, 10, 0, 4) if rail else (20, 10, 8, 4)))
        self.top.layout().setAlignment(self.btn_rail, Qt.AlignHCenter if rail else Qt.Alignment())
        self.caption.setVisible(not rail)
        self.btn_fold.setVisible(not rail)
        self.btn_top.setVisible(not rail)
        self.ver.setVisible(not rail)
        for i, (cat, head, buttons, line) in enumerate(self.groups):
            head.setVisible(not rail)
            line.setVisible(rail and i > 0)
            for b in buttons:
                b.setVisible(rail or cat not in self.collapsed)
        seen = set()
        for key, cat, head, buttons, line in self.subs:
            head.setVisible(not rail and cat not in self.collapsed)
            line.setVisible(rail and cat in seen)                # between two subgroups of a category
            seen.add(cat)
            for b in buttons:
                b.setVisible(rail or (cat not in self.collapsed and key not in self.folded))
        for b in self.nav.values():
            needs = getattr(b, "_needs", "")
            b.setText("" if rail else f"  {b._name}")
            if hasattr(b, "_tool"):
                b.setToolTip(tool_tip(b._tool, needs, b._shortcut, name=rail))
                b._slow.setVisible(bool(b._tool.slow) and not rail)
            else:
                b.setToolTip(f"{b._name}\n{b._tip}" if rail else b._tip)
            b.setProperty("rail", "true" if rail else "false")
            if hasattr(b, "_warn"):
                b._warn.setVisible(bool(needs) and not rail)
            repolish(b)
        self._heads()
        self._buttons()

    def _heads(self) -> None:
        for cat, head, buttons, _line in self.groups:
            folded = cat in self.collapsed
            head.set_state(not folded, folded and any(b.isChecked() for b in buttons), self.theme)
        for key, _cat, head, buttons, _line in self.subs:
            folded = key in self.folded
            head.set_state(not folded, folded and any(b.isChecked() for b in buttons), self.theme)

    def _buttons(self) -> None:
        th = self.theme
        folded_all = {c for c, *_ in self.groups} <= self.collapsed
        apply_icon(self.btn_fold, th, "down" if folded_all else "up")
        self.btn_fold.setToolTip("Unfold every group" if folded_all else "Fold every group")
        apply_icon(self.btn_top, th)
        apply_icon(self.btn_rail, th, "right" if self.rail else "left")
        self.btn_rail.setToolTip("Show names  ( ] )" if self.rail else "Icons only  ( ] )")

    def retheme(self, theme) -> None:
        self.theme = theme
        for b in self.nav.values():
            needs = getattr(b, "_needs", "")
            tone = theme["danger"] if b.property("broken") else theme["text-4"] if needs else theme["text-3"]
            b.setIcon(icon(b._icon_name, tone, 17, active=theme["text-3"] if needs else theme["highlight"]))
            if hasattr(b, "_warn"):
                b._slow.setPixmap(icon("clock", theme["text-3"], 14).pixmap(14, 14))
                b._warn.setPixmap(icon("warn", theme["warning"], 14).pixmap(14, 14))
        self._heads()
        self._buttons()
