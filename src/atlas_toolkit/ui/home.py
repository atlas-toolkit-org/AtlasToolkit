"""
Home: what the toolkit can do, by category, as cards (the Atlas project-card look with registration marks on
hover) or as a compact list, and a drop zone that routes files to the tools that accept them.

Every section (Workflows, then each tool category) folds under its header; a toolbar switches Cards / List and
folds or unfolds them all. The view and the folded sections are kept in the settings ("home_view",
"home_collapsed"). Saved workflows show by the name stored in them, not their file name. A tool whose required
program is missing is faded, with an amber "needs …" badge in place of its input → output and the reason in its
tooltip.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ..core.paths import config_dir, reveal
from ..core.workflow import saved_name
from ..version import __version__
from .sidebar import GroupHead
from .theme import icon, mono, qcolor, sans
from .widgets.common import Segmented, apply_icon, badge, label, repolish, section_head

CARD_W = 300
FLOWS = "Workflows"                     # the workflows section's key, before the tool categories


class ToolCard(QFrame):
    clicked = Signal(str)

    def __init__(self, tool, theme):
        super().__init__()
        self.tool = tool
        self.theme = theme
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(240)
        self.setFixedHeight(156)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self.ic = QLabel()
        top.addWidget(self.ic)
        top.addStretch(1)
        needs = getattr(tool, "needs_short", "")
        top.addWidget(badge(needs, "warning") if needs else badge(tool.io_badge, "accent"))
        lay.addLayout(top)
        name = QLabel(tool.name)
        name.setFont(sans(16, 500))
        lay.addWidget(name)
        s = QLabel(tool.summary)
        s.setWordWrap(True)
        s.setProperty("class", "summary")
        s.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lay.addWidget(s, 1)
        v = QLabel(getattr(tool, "foot", "") or f"v{tool.version}")
        v.setFont(mono(10.5))
        v.setProperty("class", "mono")
        lay.addWidget(v)
        _mark_unavailable(self, tool, getattr(tool, "tip", "") or "")
        self.retheme(theme)

    def retheme(self, theme) -> None:
        self.theme = theme
        tone = theme["text-4"] if getattr(self.tool, "needs", "") else theme["highlight"]
        self.ic.setPixmap(icon(self.tool.icon, tone, 22, 1.6).pixmap(22, 22))

    def enterEvent(self, e):
        self.setProperty("hover", "true")
        repolish(self)

    def leaveEvent(self, e):
        self.setProperty("hover", "false")
        repolish(self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.tool.id)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.property("hover") == "true":   # .ds-reg on hover
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing, False)
            col = qcolor(self.theme["highlight"])
            col.setAlphaF(0.9)
            p.setPen(QPen(col, 1))
            r = QRectF(self.rect()).adjusted(4.5, 4.5, -5.5, -5.5)
            arm = 12
            for x, y, dx, dy in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                                 (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
                p.drawLine(QPointF(x, y), QPointF(x + arm * dx, y))
                p.drawLine(QPointF(x, y), QPointF(x, y + arm * dy))
            p.end()


class ToolRow(QFrame):
    """The list view's line: icon, name, input → output, one line of summary, version or date."""
    clicked = Signal(str)

    def __init__(self, tool, theme):
        super().__init__(objectName="toolRow")
        self.tool = tool
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(44)
        hl = QHBoxLayout(self)
        hl.setContentsMargins(12, 0, 14, 0)
        hl.setSpacing(12)
        self.ic = QLabel()
        self.ic.setFixedSize(18, 18)
        hl.addWidget(self.ic)
        name = QLabel(tool.name)
        name.setProperty("class", "h3")
        name.setFixedWidth(230)
        hl.addWidget(name)
        needs = getattr(tool, "needs_short", "")
        b = badge(needs, "warning") if needs else badge(tool.io_badge, "accent")
        b.setFixedWidth(118)
        hl.addWidget(b)
        self.summary = QLabel()
        self.summary.setProperty("class", "muted")
        self.summary.setMinimumWidth(60)
        hl.addWidget(self.summary, 1)
        v = QLabel(getattr(tool, "foot", "") or f"v{tool.version}")
        v.setFont(mono(10.5))
        v.setProperty("class", "mono")
        hl.addWidget(v)
        _mark_unavailable(self, tool, getattr(tool, "tip", "") or tool.summary)
        self.retheme(theme)

    def retheme(self, theme) -> None:
        tone = theme["text-4"] if getattr(self.tool, "needs", "") else theme["highlight"]
        self.ic.setPixmap(icon(self.tool.icon, tone, 18, 1.6).pixmap(18, 18))

    def resizeEvent(self, e):               # one line of summary, elided to the room left
        super().resizeEvent(e)
        fm = self.summary.fontMetrics()
        self.summary.setText(fm.elidedText(self.tool.summary, Qt.ElideRight, max(20, self.summary.width())))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.tool.id)


def _mark_unavailable(w: QWidget, item, tip: str) -> None:
    """Faded, with the reason first in the tooltip, when the tool's required program is missing."""
    needs = getattr(item, "needs", "")
    w.setProperty("unavailable", "true" if needs else "false")
    w.setToolTip(f"⚠ {needs}\n\n{tip}" if needs and tip else needs or tip)


class DropZone(QLabel):
    dropped = Signal(list)

    def __init__(self):
        super().__init__("Drop SVG drawings or 3D models here\nthe toolkit suggests the tools that take them")
        self.setObjectName("drop")
        self.setAlignment(Qt.AlignCenter)
        self.setAcceptDrops(True)
        self.setMinimumHeight(96)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setProperty("active", "true")
            repolish(self)

    def dragLeaveEvent(self, e):
        self.setProperty("active", "false")
        repolish(self)

    def dropEvent(self, e):
        self.setProperty("active", "false")
        repolish(self)
        self.dropped.emit([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])


class HomePage(QScrollArea):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.theme = ctx.theme
        self.view: str = ctx.settings.get("home_view") or "cards"
        self.collapsed: set[str] = set(ctx.settings.get("home_collapsed") or [])
        self.setWidgetResizable(True)
        self.setObjectName("page")
        body = QWidget()
        body.setObjectName("transparent")
        self.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(40, 34, 40, 34)
        lay.setSpacing(14)
        lay.addWidget(label(f"pen-plotter toolkit · v{__version__}"))
        title = QLabel("Atlas Toolkit")
        title.setFont(sans(40, 300))
        lay.addWidget(title)
        sub = QLabel("Clean SVGs for the plotter, prepare 3D models and turn models and photos into line drawings, "
                     "with one look, one way of working and every result recording how it was made.")
        sub.setProperty("class", "summary")
        sub.setWordWrap(True)
        sub.setMaximumWidth(760)
        lay.addWidget(sub)
        lay.addSpacing(6)
        self.drop = DropZone()
        self.drop.dropped.connect(self.route)
        lay.addWidget(self.drop)
        lay.addSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addStretch(1)
        self.view_seg = Segmented([("cards", "Cards"), ("list", "List")],
                                  tips={"cards": "Cards: icon, name and summary", "list": "List: one line per tool"})
        self.view_seg.setFixedWidth(170)
        self.view_seg.set_value(self.view)
        self.view_seg.picked.connect(self.set_view)
        bar.addWidget(self.view_seg)
        self.btn_fold = QPushButton("Fold all")
        self.btn_fold.setProperty("variant", "ghost")
        self.btn_fold.clicked.connect(self.fold_all)
        bar.addWidget(self.btn_fold)
        lay.addLayout(bar)

        # key -> {"head": GroupHead, "body": QWidget, "items": [...]}
        self.sections: dict[str, dict] = {}
        self.cards: list = []                                    # the cards or rows shown now
        self._section(lay, FLOWS, self._flow_items())
        for cat, tools in ctx.registry.by_category():
            self._section(lay, cat, [self._tool_item(t) for t in tools])
        if ctx.registry.broken:
            lay.addSpacing(10)
            lay.addWidget(section_head("Tools that failed to load"))
            for b in ctx.registry.broken:
                e = QLabel(f"{b.name}: {b.error.splitlines()[0]}")
                e.setProperty("class", "error")
                e.setToolTip(b.error)
                e.setWordWrap(True)
                lay.addWidget(e)
        lay.addSpacing(10)
        foot = QHBoxLayout()
        plug = QPushButton("Plug-ins folder")
        plug.setProperty("variant", "ghost")
        plug.setToolTip("Drop a tool package here to add it without rebuilding the app (see docs/ADDING_A_TOOL.md).")
        plug.clicked.connect(lambda: reveal(_plugins_dir()))
        foot.addWidget(plug)
        foot.addStretch(1)
        lay.addLayout(foot)
        lay.addStretch(1)
        self.apply()

    # ------------------------------------------------------------------ items
    def _tool_item(self, t) -> SimpleNamespace:
        return SimpleNamespace(id=t.id, name=t.name, icon=t.icon, io_badge=t.io_badge, summary=t.summary,
                               version=t.version, foot="", tip="", needs=self.ctx.needs_text(t),
                               needs_short=self.ctx.needs_short(t), open=lambda tid=t.id: self.ctx.open_tool(tid))

    def _flow_items(self) -> list:
        """A New workflow item and the most recent saved workflows, by the names stored in them."""
        folder = Path(self.ctx.settings.get("workflow_dir") or (config_dir() / "workflows"))
        try:
            saved = sorted((p for p in folder.glob("*.atlasflow") if not p.name.startswith(".")),
                           key=lambda p: p.stat().st_mtime, reverse=True)[:5]
        except OSError:
            saved = []
        items = [SimpleNamespace(id="new", name="New workflow", icon="plus", io_badge="PIPELINE", version="",
                                 summary="Chain tools on a canvas, check the graph and run it on any number of "
                                         "models, drawings or photos.", foot="ctrl+3", tip="",
                                 open=self._new_workflow)]
        for p in saved:
            items.append(SimpleNamespace(
                id=str(p), name=saved_name(p), icon="pipeline", io_badge="WORKFLOW", version="",
                summary=_flow_summary(p), tip=f"{p.name}",
                foot=time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
                open=lambda p=p: self.ctx.open_workflow(p)))
        return items

    # ------------------------------------------------------------------ sections
    def _section(self, lay, key: str, items: list) -> None:
        lay.addSpacing(8)
        head = GroupHead(key, self._count(key, items))
        head.clicked.connect(lambda _=False, k=key: self.toggle(k))
        lay.addWidget(head)
        body = QWidget(objectName="transparent")
        lay.addWidget(body)
        self.sections[key] = {"head": head, "body": body, "items": items}

    @staticmethod
    def _count(key: str, items: list) -> int:
        return len(items) - 1 if key == FLOWS else len(items)       # "New workflow" is not a saved one

    def _fill(self, key: str) -> None:
        """(Re)build a section's cards or rows."""
        sec = self.sections[key]
        old = sec["body"].layout()
        if old is not None:
            while old.count():
                w = old.takeAt(0).widget()
                if w is not None:                                # gone at once, deleted with the next loop
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()
            QWidget().setLayout(old)                             # detach the old layout
        widgets = []
        if self.view == "list":
            box = QVBoxLayout(sec["body"])
            box.setContentsMargins(0, 2, 0, 0)
            box.setSpacing(2)
            for it in sec["items"]:
                w = ToolRow(it, self.theme)
                box.addWidget(w)
                widgets.append(w)
        else:
            grid = QGridLayout(sec["body"])
            grid.setContentsMargins(0, 4, 0, 0)
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(14)
            for i, it in enumerate(sec["items"]):
                w = ToolCard(it, self.theme)
                grid.addWidget(w, i // 3, i % 3)
                widgets.append(w)
            for col in range(3):
                grid.setColumnStretch(col, 1)
        for w in widgets:
            w.clicked.connect(lambda _id, it=w.tool: it.open())
        sec["widgets"] = widgets

    def apply(self) -> None:
        """The view and the folding, as the settings say."""
        for key, sec in self.sections.items():
            if sec.get("view") != self.view:
                self._fill(key)
                sec["view"] = self.view
            open_ = key not in self.collapsed
            sec["body"].setVisible(open_)
            sec["head"].set_state(open_, False, self.theme)
            sec["head"].count.setText(str(self._count(key, sec["items"])))
        self.cards = [w for sec in self.sections.values() for w in sec["widgets"]]
        folded_all = set(self.sections) <= self.collapsed
        self.btn_fold.setText("Unfold all" if folded_all else "Fold all")
        apply_icon(self.btn_fold, self.theme, "down" if folded_all else "up")

    def set_view(self, view: str) -> None:
        self.view = "list" if view == "list" else "cards"
        self.view_seg.set_value(self.view)
        self.ctx.settings.set("home_view", self.view)
        self.apply()

    def toggle(self, key: str) -> None:
        self.collapsed ^= {key}
        self._save()

    def fold_all(self) -> None:
        keys = set(self.sections)
        self.collapsed = set() if keys <= self.collapsed else keys
        self._save()

    def _save(self) -> None:
        self.ctx.settings.set("home_collapsed", sorted(self.collapsed))
        self.apply()

    def refresh_availability(self) -> None:
        """A required program was found or lost: rebuild the tool sections' items."""
        for cat, tools in self.ctx.registry.by_category():
            if cat in self.sections:
                self.sections[cat]["items"] = [self._tool_item(t) for t in tools]
                self.sections[cat].pop("view", None)
        self.apply()

    def refresh_workflows(self) -> None:
        sec = self.sections[FLOWS]
        sec["items"] = self._flow_items()
        sec.pop("view", None)                                    # rebuilt by apply()
        self.apply()

    def _new_workflow(self) -> None:
        page = self.ctx.open_workflow()
        if page is not None:
            page.new_workflow()

    def route(self, paths: list) -> None:
        """Open the one tool that takes these files, or ask which."""
        files = [Path(p) for p in paths]
        tools = []
        for t in self.ctx.registry.ordered():
            if any(t.inputs.accepts(f) or f.is_dir() for f in files):
                tools.append(t)
        if not tools:
            self.ctx.toast("No tool takes these files.", "warning")
            return
        if len(tools) == 1:
            self.ctx.open_tool(tools[0].id, paths)
            return
        m = QMenu(self)
        m.addSection("Open with")
        for t in tools:
            m.addAction(icon(t.icon, self.ctx.theme["text-2"], 16), f"{t.name}   ·  {t.io_badge}",
                        lambda tid=t.id: self.ctx.open_tool(tid, paths))
        m.exec(self.drop.mapToGlobal(self.drop.rect().center()))

    def retheme(self, theme) -> None:
        self.theme = theme
        for c in self.cards:
            c.retheme(theme)
        for sec in self.sections.values():
            sec["head"].set_state(sec["body"].isVisibleTo(self), False, theme)
        apply_icon(self.btn_fold, theme)


def _flow_summary(path: Path) -> str:
    """A saved workflow's description, or its steps."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "Could not read this workflow."
    if d.get("description"):
        return d["description"]
    steps = [n.get("label") or n.get("tool", "") for n in d.get("nodes", []) if n.get("type") == "tool"]
    return " → ".join(steps) or "No steps yet."


def _plugins_dir() -> Path:
    d = config_dir() / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    return d
