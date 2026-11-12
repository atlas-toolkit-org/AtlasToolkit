"""
The step library beside the canvas: inputs and Save, then every tool by category, then the saved workflows. Type to
filter (name, summary, category, "mesh", "svg ..."); drag an entry onto the canvas, or double-click / Enter to add it
next to the selected step, connected. Tools that cannot take the selected step's output are dimmed.
Group headers fold and unfold on a click; the list also shows as a rail of icons split by hairlines (]). Folded groups,
the mode and whether the list is shown are kept in the settings ("library_collapsed", "library_mode", "library_on").

Entries are strings: "input:mesh", "input:svg", "input:image", "save", "tool:<tool id>".
QuickAdd is the same list as a popup at the cursor (Tab on the canvas, or a connection dropped on empty canvas).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QSplitter, QStyle, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from ...core.paths import reveal
from ...core.tool import KINDS
from ...core.workflow import saved_name
from ..sidebar import RAIL_W, group_font
from ..theme import icon, mono, qcolor
from ..widgets.common import apply_icon, icon_button
from .canvas import MIME

GROUP = Qt.UserRole + 1               # a header's group title
COUNT = Qt.UserRole + 2               # a header's number of entries
NEEDS = Qt.UserRole + 3               # a tool's missing required program, for the warning mark

KIND_ICONS = {"mesh": "cube", "svg": "vector", "image": "image"}


def entries(reg) -> list[tuple[str, str, str, str, str]]:
    """(entry, title, subtitle, icon, group) for every step that can be added."""
    out = [(f"input:{k}", f"{badge.capitalize() if k != 'svg' else 'SVG'} input", f"{badge} · the files of a run",
            KIND_ICONS.get(k, "file"), "Workflow") for k, (_exts, badge) in KINDS.items()]
    out.append(("save", "Save", "write a step's file to disk", "save", "Workflow"))
    for cat, tools in reg.by_category():
        for t in tools:
            out.append((f"tool:{t.id}", t.name, t.io_badge, t.icon, cat))
    return out


def entry_kind(entry: str, reg) -> str | None:
    """The input kind an entry takes (None: any, or none)."""
    if entry.startswith("tool:"):
        t = reg.tools.get(entry[5:])
        return t.inputs.kind if t else None
    return None


def matches(query: str, hay: str) -> bool:
    q = query.strip().lower()
    return not q or all(w in hay.lower() for w in q.split())


class _Tree(QTreeWidget):
    def mimeTypes(self):
        return [MIME]

    def mimeData(self, items):
        md = QMimeData()
        entry = items[0].data(0, Qt.UserRole) if items else ""
        if entry and not entry.startswith("workflow:"):
            md.setData(MIME, entry.encode("utf-8"))
        return md


class _Delegate(QStyledItemDelegate):
    """Paints the group headers (chevron, title, count; a hairline in the rail) and the rail's centred icons."""

    def __init__(self, lib: "Library"):
        super().__init__(lib)
        self.lib = lib

    def sizeHint(self, opt, idx):
        s = super().sizeHint(opt, idx)
        if not idx.parent().isValid():
            return QSize(s.width(), (13 if idx.row() else 4) if self.lib.rail else (40 if idx.row() else 32))
        return QSize(s.width(), 34 if self.lib.rail else max(s.height(), 28))

    def paint(self, p: QPainter, opt, idx):
        th = self.lib.theme
        r = opt.rect
        if not idx.parent().isValid():
            p.save()
            if self.lib.rail:
                if idx.row():
                    p.setPen(QPen(qcolor(th["line-2"]), 1))
                    y = r.center().y()
                    p.drawLine(8, y, self.lib.tree.viewport().width() - 8, y)
            else:
                body = QRect(r.left(), r.bottom() - 31, r.width(), 32)
                if opt.state & QStyle.State_MouseOver:
                    p.setRenderHint(QPainter.Antialiasing)
                    p.setPen(Qt.NoPen)
                    p.setBrush(qcolor(th["bg-2"]))
                    p.drawRoundedRect(body, 6, 6)
                open_ = self.lib.tree.isExpanded(idx)
                icon("down" if open_ else "right", th["text-3"], 14).paint(
                    p, QRect(body.left() + 6, body.center().y() - 7, 14, 14))
                p.setPen(qcolor(th["text-3"]))
                p.setFont(mono(10.5))
                count = str(idx.data(COUNT) or "")
                cw = p.fontMetrics().horizontalAdvance(count)
                p.drawText(QRect(body.right() - cw - 10, body.top(), cw, body.height()), Qt.AlignVCenter, count)
                p.setPen(qcolor(th["text"]))
                p.setFont(group_font())
                text = QRect(body.left() + 28, body.top(), body.width() - cw - 46, body.height())
                p.drawText(text, Qt.AlignVCenter,
                           p.fontMetrics().elidedText(idx.data(Qt.DisplayRole), Qt.ElideRight, text.width()))
            p.restore()
            return
        needs = idx.data(NEEDS)
        if not self.lib.rail:
            super().paint(p, opt, idx)
            if needs:                                            # the warning mark at the right
                icon("warn", th["warning"], 14).paint(p, QRect(r.right() - 22, r.center().y() - 7, 14, 14))
            return
        p.save()
        cell = QRect(4, r.top() + 2, self.lib.tree.viewport().width() - 8, r.height() - 4)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        if opt.state & QStyle.State_Selected:
            p.setBrush(qcolor(th["accent-softer"]))
            p.drawRoundedRect(cell, 6, 6)
        elif opt.state & QStyle.State_MouseOver:
            p.setBrush(qcolor(th["bg-2"]))
            p.drawRoundedRect(cell, 6, 6)
        ic = idx.data(Qt.DecorationRole)
        if ic is not None:
            ic.paint(p, QRect(cell.center().x() - 9, cell.center().y() - 9, 18, 18))
        if needs:                                                # a small amber dot in the rail
            p.setBrush(qcolor(th["warning"]))
            p.drawEllipse(QRect(cell.right() - 9, cell.top() + 3, 6, 6))
        p.restore()


class Library(QWidget):
    add = Signal(str)                 # an entry to add next to the selected step
    open_workflow = Signal(str)       # a saved workflow's path
    remove_workflow = Signal(str)     # a saved workflow to delete (the page asks first)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.theme = ctx.theme
        self.saved: list[Path] = []
        self.kind: str | None = None  # the selected step's output kind: other tools are dimmed
        self.collapsed: set[str] = set(ctx.settings.get("library_collapsed") or [])
        self.mode: str = ctx.settings.get("library_mode") or "full"
        self._full_w = 250
        self.setObjectName("library")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 14, 8, 12)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(2)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search steps")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.search.installEventFilter(self)
        top.addWidget(self.search, 1)
        self.btn_fold = icon_button("up", "Fold every group", size=16)
        self.btn_fold.clicked.connect(self.fold_all)
        self.btn_rail = icon_button("left", "Icons only  ( ] )", size=16)
        self.btn_rail.clicked.connect(lambda: self.set_mode("icons" if self.mode == "full" else "full"))
        top.addWidget(self.btn_fold)
        top.addWidget(self.btn_rail)
        self.top = top
        lay.addLayout(top)
        self.tree = _Tree()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragOnly)
        self.tree.setObjectName("libraryTree")
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.setItemDelegate(_Delegate(self))
        self.tree.setMouseTracking(True)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemActivated.connect(self._activate)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self._activate(it))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)
        self.apply()

    # ------------------------------------------------------------------ folding and the rail
    @property
    def rail(self) -> bool:
        return self.mode == "icons"

    def set_mode(self, mode: str) -> None:
        self.mode = "icons" if mode == "icons" else "full"
        self.ctx.settings.set("library_mode", self.mode)
        self.apply()

    def apply(self) -> None:
        rail = self.rail
        split = self.parentWidget() if isinstance(self.parentWidget(), QSplitter) else None
        if rail and self.width() > RAIL_W:
            self._full_w = self.width()
        if rail:
            self.setFixedWidth(RAIL_W)
        else:
            self.setMinimumWidth(210)
            self.setMaximumWidth(360)
        if split is not None:                   # the splitter keeps the old width until told
            i = split.indexOf(self)
            sizes = split.sizes()
            want = RAIL_W if rail else self._full_w
            sizes[i + 1] += sizes[i] - want
            sizes[i] = want
            split.setSizes(sizes)
        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff if rail else Qt.ScrollBarAsNeeded)
        self.layout().setContentsMargins(*((0, 10, 0, 10) if rail else (12, 14, 8, 12)))
        self.top.setAlignment(self.btn_rail, Qt.AlignHCenter if rail else Qt.Alignment())
        self.search.setVisible(not rail)
        self.btn_fold.setVisible(not rail)
        self.tree.setIconSize(QSize(18, 18) if rail else QSize(16, 16))
        self.refresh()
        self._buttons()

    def _all_groups(self) -> set[str]:
        return {g for _e, _t, _s, _i, g in entries(self.ctx.registry)} | ({"Saved workflows"} if self.saved else set())

    def fold_all(self) -> None:
        """Fold every group, or unfold them all when they already are."""
        groups = self._all_groups()
        self.collapsed = set() if groups <= self.collapsed else groups
        self._save_collapsed()

    def _clicked(self, it: QTreeWidgetItem, _col=0) -> None:
        if it.parent() is None and not self.rail and not self.search.text().strip():
            self.collapsed ^= {it.data(0, GROUP)}
            self._save_collapsed()

    def _save_collapsed(self) -> None:
        self.ctx.settings.set("library_collapsed", sorted(self.collapsed))
        self.refresh()
        self._buttons()

    def _buttons(self) -> None:
        th = self.theme
        folded_all = self._all_groups() <= self.collapsed
        apply_icon(self.btn_fold, th, "down" if folded_all else "up")
        self.btn_fold.setToolTip("Unfold every group" if folded_all else "Fold every group")
        apply_icon(self.btn_rail, th, "right" if self.rail else "left")
        self.btn_rail.setToolTip("Show names  ( ] )" if self.rail else "Icons only  ( ] )")

    def set_saved(self, paths: list) -> None:
        self.saved = [Path(p) for p in paths]
        self.refresh()

    def set_kind(self, kind: str | None) -> None:
        if kind != self.kind:
            self.kind = kind
            self.refresh()

    def refresh(self, *_):
        q = self.search.text()
        th = self.theme
        self.tree.clear()
        groups: dict = {}
        reg = self.ctx.registry
        for entry, title, sub, ic, group in entries(reg):
            t = reg.tools.get(entry[5:]) if entry.startswith("tool:") else None
            hay = f"{title} {sub} {group} {entry} {t.summary if t else ''}"
            if not matches(q, hay):
                continue
            head = groups.get(group)
            if head is None:
                head = self._group(group)
                groups[group] = head
            it = QTreeWidgetItem([f"{title}"])
            it.setData(0, Qt.UserRole, entry)
            needs = self.ctx.needs_text(t) if t else ""
            it.setToolTip(0, (f"⚠ {needs}\n\n" if needs else "") + f"{title} · {sub}"
                          + (f"\n\n{t.summary}" if t else ""))
            it.setData(0, NEEDS, needs)
            fits = (self.kind is None or entry_kind(entry, reg) in (None, self.kind)) and not needs
            it.setIcon(0, icon(ic, th["highlight"] if fits else th["text-4"], 16))
            it.setForeground(0, qcolor(th["text"] if fits else th["text-4"]))
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
            head.addChild(it)
        if self.saved:
            head = None
            names = {p: saved_name(p) for p in self.saved}
            twins = {n for n in names.values() if list(names.values()).count(n) > 1}
            for p in self.saved:
                name = names[p]
                if not matches(q, f"{name} {p.stem} workflow saved"):
                    continue
                head = head or self._group("Saved workflows")
                it = QTreeWidgetItem([f"{name} ({p.stem})" if name in twins else name])
                it.setData(0, Qt.UserRole, f"workflow:{p}")
                it.setToolTip(0, f"{name} · open {p}")
                it.setIcon(0, icon("pipeline", th["text-2"], 16))
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                head.addChild(it)
        searching = bool(q.strip())
        for i in range(self.tree.topLevelItemCount()):
            head = self.tree.topLevelItem(i)
            head.setData(0, COUNT, head.childCount())
            head.setExpanded(searching or self.rail or head.data(0, GROUP) not in self.collapsed)

    def _group(self, title: str) -> QTreeWidgetItem:
        head = QTreeWidgetItem([title.upper()])
        head.setFlags(Qt.ItemIsEnabled)
        head.setData(0, GROUP, title)
        head.setToolTip(0, "Fold or unfold this group")
        self.tree.addTopLevelItem(head)
        return head

    def first_entry(self) -> QTreeWidgetItem | None:
        heads = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        for head in sorted(heads, key=lambda h: not h.isExpanded()):     # an open group first
            if head.childCount():
                return head.child(0)
        return None

    def _activate(self, it: QTreeWidgetItem) -> None:
        entry = it.data(0, Qt.UserRole) if it is not None else None
        if not entry:
            return
        if entry.startswith("workflow:"):
            self.open_workflow.emit(entry[len("workflow:"):])
        else:
            self.add.emit(entry)

    def _menu(self, pos) -> None:
        it = self.tree.itemAt(pos)
        entry = it.data(0, Qt.UserRole) if it is not None else None
        if not entry or not entry.startswith("workflow:"):
            return
        path = entry[len("workflow:"):]
        th = self.theme["text-2"]
        m = QMenu(self)
        m.addAction(icon("pipeline", th, 16), "Open", lambda: self.open_workflow.emit(path))
        m.addAction(icon("folder", th, 16), "Show in folder", lambda: reveal(Path(path)))
        m.addSeparator()
        m.addAction(icon("trash", th, 16), "Remove from the library…", lambda: self.remove_workflow.emit(path))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if obj is self.search and e.type() == QEvent.KeyPress:
            if e.key() in (Qt.Key_Return, Qt.Key_Enter):
                it = self.tree.currentItem() if (self.tree.currentItem() and self.tree.currentItem().parent()) \
                    else self.first_entry()
                self._activate(it)
                return True
            if e.key() == Qt.Key_Down:
                self.tree.setFocus()
                if self.first_entry() is not None:
                    self.tree.setCurrentItem(self.first_entry())
                return True
        return super().eventFilter(obj, e)

    def retheme(self, theme) -> None:
        self.theme = theme
        self.refresh()
        self._buttons()


class QuickAdd(QFrame):
    """A small search popup at the cursor: type, ↑ ↓, Enter adds, Esc closes."""
    chosen = Signal(str)

    def __init__(self, host: QWidget, ctx):
        super().__init__(host)
        self.ctx = ctx
        self.setObjectName("palette")
        self.kind: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Add a step…")
        self.edit.textEdited.connect(self._filter)
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit)
        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.itemClicked.connect(self._pick)
        lay.addWidget(self.list)
        self.hide()

    def open_at(self, pos: QPoint, kind: str | None) -> None:
        """Show at `pos` (host coordinates); with `kind`, only steps that take it."""
        self.kind = kind
        self.edit.clear()
        self._filter("")
        host = self.parentWidget()
        w, h = 340, min(self.list.count(), 9) * 34 + 62
        x = max(8, min(pos.x(), host.width() - w - 8))
        y = max(8, min(pos.y(), host.height() - h - 8))
        self.setGeometry(x, y, w, h)
        self.show()
        self.raise_()
        self.edit.setFocus()

    def _filter(self, text: str) -> None:
        th = self.ctx.theme
        reg = self.ctx.registry
        self.list.clear()
        for entry, title, sub, ic, group in entries(reg):
            if entry.startswith("input:") and self.kind is not None:
                continue
            if self.kind is not None and entry.startswith("tool:") and entry_kind(entry, reg) != self.kind:
                continue
            if not matches(text, f"{title} {sub} {group}"):
                continue
            t = reg.tools.get(entry[5:]) if entry.startswith("tool:") else None
            needs = self.ctx.needs_short(t) if t else ""
            it = QListWidgetItem(icon(ic, th["text-4" if needs else "text-2"], 16),
                                 f"{title}    {sub}" + (f"    ⚠ {needs}" if needs else ""))
            if needs:
                it.setForeground(qcolor(th["text-3"]))
                it.setToolTip(self.ctx.needs_text(t))
            it.setData(Qt.UserRole, entry)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _pick(self, it) -> None:
        entry = it.data(Qt.UserRole) if it is not None else None
        self.hide()
        if entry:
            self.chosen.emit(entry)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if obj is self.edit:
            if e.type() == QEvent.KeyPress:
                k = e.key()
                if k == Qt.Key_Escape:
                    self.hide()
                    return True
                if k in (Qt.Key_Down, Qt.Key_Up):
                    r = self.list.currentRow() + (1 if k == Qt.Key_Down else -1)
                    self.list.setCurrentRow(max(0, min(self.list.count() - 1, r)))
                    return True
                if k in (Qt.Key_Return, Qt.Key_Enter):
                    self._pick(self.list.currentItem())
                    return True
            elif e.type() == QEvent.FocusOut and not self.list.underMouse():
                self.hide()
        return super().eventFilter(obj, e)
