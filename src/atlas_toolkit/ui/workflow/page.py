"""
The Workflows page: build a pipeline of tools on a canvas, check it, run it on any number of files.

    head      WORKFLOW eyebrow, name, status (not checked / ready / problems), New Open Save Save as Undo Redo …
    library   inputs, Save and every tool, searchable; the saved workflows
    canvas    the graph (canvas.py)                      inspector   the selected step, or the workflow's panel
    bottom    Inputs · Problems · Log · Report | preview             Check · Run · Cancel · Open folder

Run is enabled only after a Check without errors; any edit asks for a new Check. Each input file is one job in the
worker process (core/workflow.py), and steps whose tool, settings and inputs did not change come from the cache.
Edits are undoable (snapshots of the workflow), and the current workflow is kept as a draft across restarts.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTabWidget,
                               QVBoxLayout, QWidget)

from ...core import workflow as W
from ..keys import shown
from ...core.history import RunRecord
from ...core.params import ParamError
from ...core import prefs
from ...core import presets as preset_lib
from ...core.paths import cache_dir, config_dir, reveal
from ...core.settings import _write
from ...core.tool import KINDS
from ...version import __version__
from ..theme import icon, mono, sans
from ..widgets.common import badge, hint, label, set_badge
from ..widgets.file_queue import FileQueue
from ..widgets.preview import PreviewStage
from .canvas import HEAD_H, NODE_W, Card, WorkflowView
from .inspector import NodeInspector, WorkflowPanel
from .library import KIND_ICONS, Library, QuickAdd

INSPECTOR_W = 340
DRAFT = ".draft" + W.EXT


class Snapshot(QUndoCommand):
    """One edit: the workflow before and after. Edits with the same merge key (one field typed into) merge."""

    def __init__(self, page, before: dict, after: dict, text: str, merge: str = ""):
        super().__init__(text)
        self.page, self.before, self.after, self.merge = page, before, after, merge
        self.first = True

    def id(self) -> int:
        return (int(hashlib.md5(self.merge.encode()).hexdigest()[:7], 16)) if self.merge else -1

    def mergeWith(self, other) -> bool:
        if not self.merge or other.merge != self.merge:
            return False
        self.after = other.after
        return True

    def redo(self):
        if self.first:                  # the edit is already done when the command is pushed
            self.first = False
            return
        self.page._restore_snapshot(self.after)

    def undo(self):
        self.page._restore_snapshot(self.before)


class WorkflowPage(QWidget):
    saved_changed = Signal()            # the library of saved workflows changed (the shell rebuilds its palette)
    info_ready = Signal(str, object)    # a file inspected in the background: path, InputInfo

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.reg = ctx.registry
        self.theme = ctx.theme
        self.wf = W.Workflow()
        self.path: Path | None = None
        self.dirty = False
        self.issues: list[W.Issue] = []
        self.checked = False
        self.selected: str | None = None
        self.states: dict[str, dict] = {}      # input file ("" without one) -> node id -> state dict
        self.files: dict[str, object] = {}      # batch files -> queue item
        self.saved: dict[str, list] = {}        # batch file -> files saved by its last run
        self.reports: dict[str, str] = {}
        self.jobs: dict[int, dict] = {}         # job id -> {"file", "action", "target"}
        self.batch: dict | None = None
        self.preview_job: int | None = None
        self.auto_previewed: set = set()
        self.infos: dict = {}                   # path -> InputInfo, for the step forms
        self._inspecting: set = set()
        self.info_ready.connect(self._info_ready)
        self.undo = QUndoStack(self)
        self.autosave = QTimer(self, singleShot=True, interval=800, timeout=self._save_draft)
        self.inspector_widget = None
        self._fit_pending = True
        self.setAcceptDrops(True)
        self._build()
        self._shortcuts()
        self._restore()

    # ================================================================== layout
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.outer = QSplitter(Qt.Horizontal)
        self.outer.setChildrenCollapsible(False)
        self.outer.setHandleWidth(5)
        root.addWidget(self.outer)

        self.library = Library(self.ctx)          # its own width: a range in full, fixed as a rail
        self.library.setVisible(bool(self.ctx.settings.get("library_on", True)))
        self.library.add.connect(lambda e: self.add_entry(e))
        self.library.open_workflow.connect(self.open_path)
        self.library.remove_workflow.connect(self.remove_saved)
        self.outer.addWidget(self.library)

        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(22, 16, 16, 12)
        ml.setSpacing(10)
        ml.addLayout(self._build_head())
        self.vsplit = QSplitter(Qt.Vertical)
        self.vsplit.setChildrenCollapsible(False)
        self.vsplit.setHandleWidth(8)
        frame = QFrame(objectName="stage")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        self.view = WorkflowView(self.theme)
        self.view.can_connect = lambda s, d, p: W.can_feed(self.wf, s, d, p, self.reg)
        self.view.selected.connect(self._on_selected)
        self.view.connect_requested.connect(self.connect_nodes)
        self.view.rewire_requested.connect(self._rewire)
        self.view.unplug_requested.connect(lambda d, p: self.edit("Unplug", lambda: self.wf.disconnect(d, p)))
        self.view.delete_requested.connect(self.delete)
        self.view.moved.connect(self._moved)
        self.view.entry_dropped.connect(lambda e, pos: self.add_entry(e, pos))
        self.view.quick_add.connect(self._quick_add)
        self.view.activated.connect(self.preview_node)
        self.view.context.connect(self._context_menu)
        self.view.action.connect(self._view_action)
        self.view.files_dropped.connect(self.add_paths)
        fl.addWidget(self.view)
        self.vsplit.addWidget(frame)

        bottom = QSplitter(Qt.Horizontal)
        bottom.setChildrenCollapsible(False)
        bottom.setHandleWidth(8)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_inputs(), "Inputs")
        self.problems = QListWidget()
        self.problems.itemClicked.connect(lambda it: self.select(it.data(Qt.UserRole)) if it.data(Qt.UserRole)
                                          else None)
        self.tabs.addTab(self.problems, "Problems")
        self.logview = QPlainTextEdit(readOnly=True)
        self.logview.setMaximumBlockCount(5000)
        self.logview.setPlaceholderText("Messages from this workflow's runs appear here.")
        self.tabs.addTab(self.logview, "Log")
        self.report = QPlainTextEdit(readOnly=True)
        self.report.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.report.setPlaceholderText("The selected step's report, or the selected file's run.")
        self.tabs.addTab(self.report, "Report")
        bottom.addWidget(self.tabs)
        st = QWidget()
        sl = QVBoxLayout(st)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(6)
        self.info_line = QLabel("")
        self.info_line.setFont(mono(11.5))
        self.info_line.setProperty("class", "mono")
        sl.addWidget(self.info_line)
        self.stage = PreviewStage(self.theme)
        self.stage.set_art(self.ctx.art)
        sl.addWidget(self.stage, 1)
        bottom.addWidget(st)
        bottom.setSizes([520, 520])
        self.vsplit.addWidget(bottom)
        self.vsplit.setSizes([520, 330])
        ml.addWidget(self.vsplit, 1)
        self.outer.addWidget(main)
        self.outer.addWidget(self._build_inspector())
        self.outer.setStretchFactor(0, 0)
        self.outer.setStretchFactor(1, 1)
        self.outer.setStretchFactor(2, 0)
        self.outer.setSizes([250, 900, INSPECTOR_W])
        self.quick = QuickAdd(self, self.ctx)
        self.quick.chosen.connect(self._quick_chosen)
        self._quick_ctx = (None, None)

    def _build_head(self):
        head = QVBoxLayout()
        head.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(label("workflow"))
        self.file_label = QLabel("")
        self.file_label.setFont(mono(11))
        self.file_label.setProperty("class", "mono")
        top.addWidget(self.file_label)
        top.addStretch(1)
        self.state_badge = badge("not checked")
        top.addWidget(self.state_badge)
        head.addLayout(top)
        tl = QHBoxLayout()
        tl.setSpacing(6)
        self.title = QLabel("")
        self.title.setProperty("class", "title")
        self.title.setFont(sans(26, 300))
        tl.addWidget(self.title, 1)
        self.toolbar = {}
        for key, text, tip, fn in (
                ("new", "New", "Start an empty workflow (Ctrl+N)", self.new_workflow),
                ("open", "Open…", "Open a saved workflow", self.open_dialog),
                ("save", "Save", "Save the workflow (Ctrl+S)", self.save),
                ("saveas", "Save as…", "Save under another name (Ctrl+Shift+S)", self.save_as),
                ("undo", "Undo", "Undo (Ctrl+Z)", self.undo.undo),
                ("redo", "Redo", "Redo (Ctrl+Y)", self.undo.redo),
                ("layout", "Lay out", "Arrange the steps left to right (L)", self.layout_graph),
                ("fit", "Fit", "Fit the graph in view (F)", self.view_fit)):
            b = QPushButton(text)
            b.setProperty("variant", "ghost")
            b.setToolTip(shown(tip))
            b.clicked.connect(fn)
            tl.addWidget(b)
            self.toolbar[key] = b
        self.undo.canUndoChanged.connect(self.toolbar["undo"].setEnabled)
        self.undo.canRedoChanged.connect(self.toolbar["redo"].setEnabled)
        self.toolbar["undo"].setEnabled(False)
        self.toolbar["redo"].setEnabled(False)
        head.addLayout(tl)
        return head

    def _build_inputs(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.files_label = label("files to run on")
        bar.addWidget(self.files_label)
        bar.addStretch(1)
        self.btn_add = QPushButton("Add files…")
        self.btn_add.clicked.connect(self.pick_files)
        self.btn_add_dir = QPushButton("Add folder…")
        self.btn_add_dir.clicked.connect(self.pick_folder)
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.clicked.connect(self.remove_selected)
        for b in (self.btn_add, self.btn_add_dir, self.btn_remove):
            bar.addWidget(b)
        lay.addLayout(bar)
        self.queue = FileQueue(self.theme, [("saved", "Saved")],
                               "Drop the files to run the workflow on here\n\nor use  Add files…")
        self.queue.itemSelectionChanged.connect(self._file_selected)
        self.queue.itemDoubleClicked.connect(lambda it, _c: self._reveal_saved(it.data(0, Qt.UserRole)))
        lay.addWidget(self.queue, 1)
        return w

    def _build_inspector(self) -> QWidget:
        side = QWidget()
        side.setObjectName("inspector")
        side.setMinimumWidth(300)
        side.setMaximumWidth(640)
        outer = QVBoxLayout(side)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        outer.addWidget(self.scroll, 1)
        act = QFrame()
        act.setObjectName("transparent")
        al = QVBoxLayout(act)
        al.setContentsMargins(20, 10, 20, 18)
        al.setSpacing(8)
        self.run_note = hint("")
        al.addWidget(self.run_note)
        self.btn_check = QPushButton("Check")
        self.btn_check.setToolTip("Look for anything that would stop the workflow, before running it.")
        self.btn_check.setCursor(Qt.PointingHandCursor)
        self.btn_check.clicked.connect(self.check)
        al.addWidget(self.btn_check)
        self.btn_run = QPushButton("Run")
        self.btn_run.setProperty("variant", "primary")
        self.btn_run.setCursor(Qt.PointingHandCursor)
        self.btn_run.clicked.connect(lambda: self.run_all())
        al.addWidget(self.btn_run)
        self.btn_vary = QPushButton("New variation")
        self.btn_vary.setIcon(icon("dice", self.theme["text-2"], 16))
        self.btn_vary.setCursor(Qt.PointingHandCursor)
        self.btn_vary.clicked.connect(self.run_variation)
        al.addWidget(self.btn_vary)
        r2 = QHBoxLayout()
        r2.setSpacing(6)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setProperty("variant", "danger")
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_open = QPushButton("Open folder")
        self.btn_open.clicked.connect(self.open_output_folder)
        r2.addWidget(self.btn_cancel, 1)
        r2.addWidget(self.btn_open, 1)
        al.addLayout(r2)
        outer.addWidget(act)
        return side

    def _shortcuts(self) -> None:
        def sc(seq, fn):
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
        sc("Ctrl+S", self.save)
        sc("Ctrl+Shift+S", self.save_as)
        sc("Ctrl+N", self.new_workflow)
        sc("Ctrl+Z", self.undo.undo)
        sc("Ctrl+Y", self.undo.redo)
        sc("Ctrl+Shift+Z", self.undo.redo)

    # ================================================================== files
    def workflow_dir(self) -> Path:
        d = Path(self.ctx.settings.get("workflow_dir") or (config_dir() / "workflows"))
        d.mkdir(parents=True, exist_ok=True)
        return d

    def saved_workflows(self) -> list[Path]:
        try:
            files = [p for p in self.workflow_dir().glob(f"*{W.EXT}") if not p.name.startswith(".")]
        except OSError:
            return []
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    def _restore(self) -> None:
        draft = self.workflow_dir() / DRAFT
        loaded = False
        if draft.exists():
            try:
                data = json.loads(draft.read_text(encoding="utf-8"))
                self._set_workflow(W.Workflow.from_dict(data), Path(data["draft_of"]) if data.get("draft_of") else
                                   None, dirty=bool(data.get("dirty")))
                loaded = True
            except (OSError, ValueError, KeyError, W.WorkflowError):
                loaded = False
        if not loaded:
            last = self.ctx.settings.get("last_workflow")
            if last and Path(last).exists():
                try:
                    self._set_workflow(W.load(last), Path(last))
                    loaded = True
                except W.WorkflowError:
                    pass
        if not loaded:
            self._set_workflow(W.Workflow(), None)
        self.library.set_saved(self.saved_workflows())

    def _set_workflow(self, wf: W.Workflow, path: Path | None, dirty: bool = False) -> None:
        self.wf, self.path, self.dirty = wf, path, dirty
        self.issues, self.checked, self.selected = [], False, None
        self.states, self.auto_previewed = {}, set()
        self.undo.clear()
        self._rebuild()
        self._show_inspector()
        self._show_preview()
        self._update_controls()
        self._fit_pending = not self.isVisible()
        QTimer.singleShot(0, self.view_fit)

    def confirm_discard(self) -> bool:
        if not self.dirty or not self.wf.nodes:
            return True
        r = QMessageBox.question(self, "Workflow", f"Save the changes to “{self.wf.name}” first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def new_workflow(self) -> None:
        if self.busy() or not self.confirm_discard():
            return
        self._set_workflow(W.Workflow(), None)
        self._save_draft()

    def open_dialog(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Open workflow", str(self.workflow_dir()),
                                           f"Workflows (*{W.EXT});;All files (*.*)")
        if f:
            self.open_path(f)

    def open_path(self, path) -> bool:
        if self.busy():
            self.ctx.toast("Wait for the run to finish (or cancel it) first.", "warning")
            return False
        if self.path is not None and Path(path).resolve() == self.path.resolve() and not self.dirty:
            return True
        if not self.confirm_discard():
            return False
        try:
            wf = W.load(path)
        except W.WorkflowError as e:
            self.ctx.toast(str(e), "danger")
            return False
        self._set_workflow(wf, Path(path))
        self.ctx.settings.set("last_workflow", str(path))
        self._save_draft()
        self.ctx.toast(f"Opened “{wf.name}”.", "success")
        return True

    def remove_saved(self, path) -> None:
        path = Path(path)
        if QMessageBox.question(self, "Remove workflow", f"Delete {path.name} from the workflow library? Files it "
                                                         "made are not touched.") != QMessageBox.Yes:
            return
        try:
            path.unlink()
        except OSError as e:
            self.ctx.toast(f"Could not remove it: {e}", "danger")
            return
        if self.path is not None and self.path.resolve() == path.resolve():
            self.path, self.dirty = None, True       # the open workflow stays, as unsaved work
            self._update_head()
            self._save_draft()
        self.library.set_saved(self.saved_workflows())
        self.saved_changed.emit()

    def save(self) -> bool:
        if self.path is None:
            return self.save_as()
        return self._write(self.path)

    def save_as(self) -> bool:
        start = self.workflow_dir() / (W.slug(self.wf.name) + W.EXT)
        f, _ = QFileDialog.getSaveFileName(self, "Save workflow", str(start), f"Workflows (*{W.EXT})")
        return self._write(Path(f)) if f else False

    def _write(self, path: Path) -> bool:
        try:
            path = W.save(self.wf, path)
        except OSError as e:
            self.ctx.toast(f"Could not save: {e}", "danger")
            return False
        self.path, self.dirty = path, False
        self.undo.setClean()
        self.ctx.settings.set("last_workflow", str(path))
        self._save_draft()
        self.library.set_saved(self.saved_workflows())
        self.saved_changed.emit()
        self._update_head()
        self.ctx.toast(f"Saved {path.name}.", "success", "Show", lambda: reveal(path))
        return True

    def _save_draft(self) -> None:
        d = self.wf.to_dict()
        d["draft_of"] = str(self.path) if self.path else ""
        d["dirty"] = self.dirty
        try:
            _write(self.workflow_dir() / DRAFT, d)
        except OSError:
            pass

    # ================================================================== editing
    def edit(self, text: str, fn, merge: str = "", structural: bool = True, inspector: bool = False) -> bool:
        """Apply fn() to the workflow as one undoable step."""
        before = self.wf.to_dict()
        try:
            fn()
        except W.WorkflowError as e:
            self.wf = W.Workflow.from_dict(before)
            self.ctx.toast(str(e), "warning")
            self._rebuild()
            return False
        after = self.wf.to_dict()
        if after == before:
            return False
        self.undo.push(Snapshot(self, before, after, text, merge))
        self._changed(structural, inspector)
        return True

    def _restore_snapshot(self, d: dict) -> None:
        self.wf = W.Workflow.from_dict(d)
        if self.selected not in self.wf.nodes:
            self.selected = None
        self._changed(True, True)

    def _changed(self, structural: bool, inspector: bool) -> None:
        self.dirty = True
        self.checked = False
        self.issues = []
        self.problems.clear()
        if structural:
            self._rebuild()
        else:
            self._refresh_cards()
        if inspector:
            self._show_inspector()
        elif isinstance(self.inspector_widget, NodeInspector):
            self.inspector_widget.refresh_example()
        self.library.set_kind(self._out_kind(self.selected))
        self._update_controls()
        self.autosave.start()

    def add_entry(self, entry: str, pos: QPointF | None = None, from_node: str | None = None) -> str | None:
        """Add a step from the library (entry "tool:<id>", "input:<kind>", "save"), connected when it fits."""
        if entry.startswith("tool:"):
            tool = self.reg.tools.get(entry[5:])
            if tool is None:
                return None
            node = W.tool_node(tool)
        elif entry.startswith("input:"):
            node = W.input_node(entry[6:])
        elif entry == "save":
            node = W.save_node()
        else:
            return None
        anchor = from_node or (self.selected if pos is None else None)
        if pos is not None:
            node.pos = (pos.x() - NODE_W / 2, pos.y() - HEAD_H / 2)
        elif anchor in self.wf.nodes:
            ax, ay = self.wf.nodes[anchor].pos
            x, y = ax + NODE_W + 70, ay
            taken = {(round(n.pos[0]), round(n.pos[1])) for n in self.wf.nodes.values()}
            while (round(x), round(y)) in taken:
                y += 120
            node.pos = (x, y)
        else:
            c = self.view.center_pos()
            node.pos = (c.x() - NODE_W / 2, c.y() - HEAD_H / 2)
        holder = {}

        def fn():
            n = self.wf.add(node)
            holder["id"] = n.id
            src = from_node or anchor
            if src in self.wf.nodes and node.type != W.INPUT and W.can_feed(self.wf, src, n.id, W.MAIN, self.reg):
                self.wf.connect(src, n.id, W.MAIN)

        if self.edit(f"Add {W.label_of(node, self.reg)}", fn):
            self.select(holder["id"])
            return holder["id"]
        return None

    def connect_nodes(self, src: str, dst: str, port: str = W.MAIN) -> None:
        self.edit("Connect", lambda: self.wf.connect(src, dst, port))

    def _rewire(self, src, old_dst, old_port, dst, port) -> None:
        def fn():
            self.wf.disconnect(old_dst, old_port)
            self.wf.connect(src, dst, port)
        self.edit("Move arrow", fn)

    def delete(self, nodes: list, edges: list = ()) -> None:
        def fn():
            for d, p in edges:
                self.wf.disconnect(d, p)
            for n in nodes:
                self.wf.remove(n)
        if nodes and self.selected in nodes:
            self.selected = None
        self.edit("Delete", fn, inspector=bool(nodes))

    def _moved(self, positions: dict) -> None:
        def fn():
            for nid, (x, y) in positions.items():
                if nid in self.wf.nodes:
                    self.wf.nodes[nid].pos = (x, y)
        before = self.wf.to_dict()
        fn()
        after = self.wf.to_dict()
        if after != before:
            self.undo.push(Snapshot(self, before, after, "Move"))
            self.dirty = True
            self._update_head()
            self.autosave.start()

    def duplicate(self, nid: str | None = None) -> None:
        nid = nid or self.selected
        if nid not in self.wf.nodes:
            return
        src = self.wf.nodes[nid]
        copy = W.Node.from_dict(json.loads(json.dumps(src.to_dict())))
        copy.id = ""
        copy.pos = (src.pos[0] + 30, src.pos[1] + 130)
        holder = {}

        def fn():
            n = self.wf.add(copy)
            holder["id"] = n.id
            e = self.wf.edge_into(nid)
            if e is not None:
                self.wf.connect(e.src, n.id, e.port)
        if self.edit("Duplicate", fn):
            self.select(holder["id"])

    def save_step(self, nid: str) -> None:
        node = self.wf.nodes.get(nid)
        if node is None:
            return
        out = W.save_node(pos=(node.pos[0] + NODE_W + 70, node.pos[1] + 110))
        holder = {}

        def fn():
            n = self.wf.add(out)
            holder["id"] = n.id
            self.wf.connect(nid, n.id)
        if self.edit("Save this step", fn):
            self.select(holder["id"])

    def layout_graph(self) -> None:
        self.edit("Lay out", lambda: W.auto_layout(self.wf))
        QTimer.singleShot(0, self.view_fit)

    def view_fit(self) -> None:
        self.view.fit()

    def set_node_values(self, nid: str, values: dict, partial: bool = False) -> None:
        node = self.wf.nodes.get(nid)
        tool = self.reg.tools.get(node.tool) if node is not None else None
        if tool is None:
            return
        try:
            base = dict(W.node_values(node, tool)) if partial else {}
        except ParamError:
            base = dict(tool.defaults())
        base.update(values)
        try:
            new = W.jsonable(tool, tool.values(base))
        except ParamError as e:
            self.ctx.toast(str(e), "warning")
            return

        def fn():
            node.values = new
            node.tool_version = tool.version
        self.edit(f"Change {W.label_of(node, self.reg)}", fn, merge=f"values:{nid}", structural=False,
                  inspector=partial and self.selected == nid)

    def set_node_attrs(self, nid: str, merge: str, **attrs) -> None:
        node = self.wf.nodes.get(nid)
        if node is None:
            return

        def fn():
            for k, v in attrs.items():
                setattr(node, k, v)
        structural = bool({"kind", "mode", "format", "path"} & set(attrs))
        inspector = bool({"kind", "mode", "folder", "where"} & set(attrs))
        self.edit(f"Change {W.label_of(node, self.reg)}", fn, merge=f"{merge}:{nid}", structural=structural,
                  inspector=inspector)

    def set_workflow_attrs(self, name: str | None = None, description: str | None = None, merge: str = "") -> None:
        def fn():
            if name is not None:
                self.wf.name = name
            if description is not None:
                self.wf.description = description
        self.edit("Rename workflow" if name is not None else "Describe workflow", fn, merge=merge or "name",
                  structural=False)
        self._update_head()

    def toggle_exposed(self, nid: str, key: str) -> None:
        def fn():
            if (nid, key) in self.wf.exposed:
                self.wf.exposed.remove((nid, key))
            else:
                self.wf.exposed.append((nid, key))
        self.edit("Workflow parameters", fn, structural=False)
        if isinstance(self.inspector_widget, NodeInspector):
            self.inspector_widget.mark_exposed()
        else:
            self._show_inspector()

    def example_save_name(self, nid: str) -> str:
        f = self.current_file()
        return W.planned_names(self.wf, self.reg, Path(f).name if f else "input").get(nid, "")

    # ================================================================== canvas
    def _out_kind(self, nid: str | None) -> str | None:
        node = self.wf.nodes.get(nid) if nid else None
        return W.output_kind(node, self.reg) if node is not None else None

    def _sig(self, nid: str) -> str:
        """What a node's result depends on: itself and everything upstream (positions and names aside)."""
        ids = sorted(self.wf.ancestors([nid]) | {nid})
        parts = []
        for i in ids:
            d = self.wf.nodes[i].to_dict()
            d.pop("pos", None)
            d.pop("label", None)
            parts.append(d)
        edges = sorted((e.src, e.dst, e.port) for e in self.wf.edges if e.dst in ids)
        return hashlib.md5(json.dumps([parts, edges], sort_keys=True, default=str).encode()).hexdigest()

    def _state(self, nid: str, file: str | None = None) -> dict | None:
        f = self.current_file() if file is None else file
        st = self.states.get(f or "", {}).get(nid)
        if st is None or st.get("sig") != self._sig(nid):
            return None
        return st

    def _card(self, node: W.Node) -> Card:
        reg = self.reg
        level = ""
        for i in self.issues:
            if i.node == node.id:
                level = "error" if i.level == "error" or level == "error" else "warning"
        st = self._state(node.id)
        chip, tone, progress = "", "", -1.0
        if st is not None:
            s = st["state"]
            if s == "queued":
                chip, tone = "queued", ""
            elif s in ("started", "progress"):
                frac = st.get("frac", 0.0)
                chip, tone, progress = f"{frac * 100:.0f}%", "live", frac
            elif s == "cached":
                chip, tone = "cached", "accent"
            elif s == "done":
                chip, tone = (f"{st['seconds']:.1f} s" if st.get("seconds") else "done"), "success"
                if st.get("draft"):
                    chip, tone = "preview", "accent"
            elif s == "error":
                chip, tone = "error", "danger"
        if node.type == W.TOOL:
            tool = reg.tools.get(node.tool)
            if tool is None:
                return Card(node.id, W.label_of(node, reg), "not installed", "warn", "text-3",
                            [(W.MAIN, "Input", None, False)], None, True, chip, tone, level or "error", True)
            try:
                vals = W.node_values(node, tool)
                offered = self.ctx.presets.presets(tool)
                found = preset_lib.match(tool, vals, offered)
                preset = found.name if found else None
            except ParamError:
                preset, offered = None, []
            n_exp = sum(1 for nid, _k in self.wf.exposed if nid == node.id)
            sub = tool.io_badge + (f" · {preset}" if preset else (" · custom" if offered else ""))
            sub += f" · ★{n_exp}" if n_exp else ""
            needs = self.ctx.needs_short(tool)
            if needs:                          # amber at once, before any Check; Check reports it as an error
                level = level or "warning"
                if not chip:
                    chip, tone = needs, "warning"
            ports = [(p.key, p.label, p.kind, p.side) for p in W.ports(node, reg)]
            return Card(node.id, W.label_of(node, reg), sub, tool.icon, "highlight", ports, tool.output.kind, True,
                        chip, tone, level, False, progress)
        if node.type == W.INPUT:
            sub = (f"{KINDS.get(node.kind, ('', node.kind))[1]} · each file" if node.mode == "batch"
                   else f"fixed · {Path(node.path).name or 'choose a file'}")
            return Card(node.id, W.label_of(node, reg), sub, KIND_ICONS.get(node.kind, "file"), "highlight", [],
                        node.kind, True, chip, tone, level)
        e = self.wf.edge_into(node.id)
        kind = self._out_kind(e.src) if e is not None else None
        name = self.example_save_name(node.id)
        where = "beside the input" if node.where == "next" else Path(node.folder).name or "a folder"
        sub = f"→ {name or node.name} · {where}"
        return Card(node.id, W.label_of(node, reg), sub, "save", "accent", [(W.MAIN, "File", kind, False)], None,
                    False, chip, tone, level)

    def _rebuild(self) -> None:
        cards = [self._card(n) for n in self.wf.nodes.values()]
        positions = {n.id: n.pos for n in self.wf.nodes.values()}
        edges = [(e.src, e.dst, e.port) for e in self.wf.edges]
        self.view.rebuild(cards, positions, edges, self.selected)
        self._update_head()

    def _refresh_cards(self) -> None:
        for n in self.wf.nodes.values():
            self.view.update_card(self._card(n))
        self._update_head()

    def select(self, nid: str | None) -> None:
        self.view.select(nid)                  # emits selected -> _on_selected

    def _on_selected(self, nid) -> None:
        if nid == self.selected and self.inspector_widget is not None:
            return
        self.selected = nid
        self.library.set_kind(self._out_kind(nid))
        self._show_inspector()
        self._show_preview()

    def refresh_availability(self) -> None:
        """A required program was found or lost: the step list, the cards and the open step say so."""
        self.library.refresh()
        self._refresh_cards()
        self._show_inspector()

    def _show_inspector(self) -> None:
        node = self.wf.nodes.get(self.selected) if self.selected else None
        body = QWidget()
        body.setObjectName("transparent")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 18, 20, 12)
        self.inspector_widget = NodeInspector(self, node) if node is not None else WorkflowPanel(self)
        lay.addWidget(self.inspector_widget)
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.scroll.setWidget(body)
        self._feed_info()

    def _feed_info(self) -> None:
        """Give the step's form what it knows of its input (AUTO suggestions, choices from the file)."""
        if not isinstance(self.inspector_widget, NodeInspector) or self.selected not in self.wf.nodes:
            return
        e = self.wf.edge_into(self.selected)
        if e is None:
            return
        st = self._state(e.src)
        src = self.wf.nodes[e.src]
        path = (st or {}).get("output") or (self.current_file() if src.type == W.INPUT and src.mode == "batch"
                                            else src.path if src.type == W.INPUT else None)
        if not path or not Path(path).exists():
            return
        if path in self.infos:
            self.inspector_widget.set_info(self.infos[path])
        elif path not in self._inspecting:      # read it off the UI thread (a big mesh takes seconds)
            self._inspecting.add(path)
            kind = W.output_kind(src, self.reg)

            def work(p=path, k=kind):
                try:
                    info = W.inspect_file(Path(p), k)
                except Exception:              # an unreadable file: the form just gets no suggestions
                    info = None
                self.info_ready.emit(p, info)
            threading.Thread(target=work, name="atlas-wf-inspect", daemon=True).start()

    def _info_ready(self, path: str, info) -> None:
        self._inspecting.discard(path)
        self.infos[path] = info
        if info is not None:
            self._feed_info()

    def _quick_add(self, pos: QPointF, from_node) -> None:
        kind = self._out_kind(from_node) if from_node else None
        self._quick_ctx = (pos, from_node)
        host = self.view.mapTo(self, self.view.mapFromScene(pos))
        self.quick.open_at(host, kind)

    def _quick_chosen(self, entry: str) -> None:
        pos, src = self._quick_ctx
        self.add_entry(entry, pos, src)

    def _view_action(self, name: str) -> None:
        if name == "layout":
            self.layout_graph()
        elif name == "duplicate":
            self.duplicate()
        elif name == "preview" and self.selected:
            self.preview_node(self.selected)

    def _context_menu(self, nid, gpos, spos) -> None:
        th = self.theme["text-2"]
        m = QMenu(self)
        if nid is not None:
            node = self.wf.nodes[nid]
            m.addAction(icon("eye", th, 16), "Preview up to here\tP", lambda: self.preview_node(nid))
            if node.type == W.TOOL and node.tool in self.reg.tools:
                t = self.reg.get(node.tool)
                m.addAction(icon("external", th, 16), "Aim in tool…" if (t.viewer or t.panel) else "Open in tool…",
                            lambda: self.aim(nid))
            if node.type != W.SAVE:
                m.addAction(icon("save", th, 16), "Save this step's file", lambda: self.save_step(nid))
            m.addSeparator()
            m.addAction(icon("copy", th, 16), "Duplicate\tCtrl+D", lambda: self.duplicate(nid))
            m.addAction(icon("trash", th, 16), "Delete\tDel", lambda: self.delete([nid]))
        else:
            m.addAction(icon("plus", th, 16), "Add a step…\tTab", lambda: self._quick_add(spos, None))
            m.addAction(icon("pipeline", th, 16), "Lay out\tL", self.layout_graph)
            m.addAction(icon("expand", th, 16), "Fit\tF", self.view_fit)
        m.exec(gpos)

    # ================================================================== input files
    def batch_kind(self) -> str | None:
        b = self.wf.batch_inputs()
        return b[0].kind if b else None

    def add_paths(self, paths: list) -> int:
        flows = [p for p in paths if str(p).lower().endswith(W.EXT)]
        if flows:
            self.open_path(flows[0])
            return 0
        if self.busy():
            self.ctx.toast("Wait for the run to finish (or cancel it) before adding files.", "warning")
            return 0
        kind = self.batch_kind()
        files = []
        for p in map(Path, paths):
            cands = sorted(f for f in p.iterdir() if f.is_file()) if p.is_dir() else [p]
            files += [f for f in cands if f.suffix.lower() in {e for exts, _b in KINDS.values() for e in exts}]
        if kind is None and files and not self.wf.nodes:   # an empty workflow: start it with an input of their kind
            k = next((k for k, (exts, _b) in KINDS.items() if files[0].suffix.lower() in exts), None)
            if k:
                self.add_entry(f"input:{k}")
                kind = k
        if kind is None:
            self.ctx.toast("Add an input step (from the list) to take the files of a run first.", "warning")
            return 0
        exts = KINDS[kind][0]
        n = 0
        for f in files:
            if f.suffix.lower() not in exts:
                continue
            key = str(f.resolve())
            if key in self.files:
                continue
            self.files[key] = self.queue.add_row(key, f.name)
            self.queue.set_status(self.files[key], "Ready", "accent")
            self.ctx.settings.add_recent(key)
            n += 1
        if not n and paths:
            self.ctx.toast(f"This workflow takes {' '.join(exts)} files.", "warning")
        if n and not self.queue.selectedItems():
            self.queue.setCurrentItem(self.queue.topLevelItem(0))
        self._update_controls()
        return n

    def pick_files(self) -> None:
        kind = self.batch_kind() or "mesh"
        exts = " ".join(f"*{x}" for x in KINDS[kind][0])
        files, _ = QFileDialog.getOpenFileNames(self, "Files to run the workflow on",
                                                self.ctx.settings.get("workflow_last_dir", ""),
                                                f"{KINDS[kind][1]} files ({exts});;All files (*.*)")
        if files:
            self.ctx.settings.set("workflow_last_dir", str(Path(files[0]).parent))
            self.add_paths(files)

    def pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Run on every file in a folder",
                                                  self.ctx.settings.get("workflow_last_dir", ""))
        if folder:
            self.add_paths([folder])

    def remove_selected(self) -> None:
        if self.busy():
            return
        for key in self.queue.selected_keys():
            it = self.files.pop(key, None)
            if it is not None:
                self.queue.takeTopLevelItem(self.queue.indexOfTopLevelItem(it))
        self._update_controls()
        self._file_selected()

    def current_file(self) -> str:
        it = self.queue.currentItem()
        if it is not None:
            return it.data(0, Qt.UserRole)
        return next(iter(self.files), "")

    def _file_selected(self) -> None:
        self._refresh_cards()
        self._show_preview()
        self._feed_info()
        f = self.current_file()
        if f in self.reports and not self.selected:
            self.report.setPlainText(self.reports[f])

    def _reveal_saved(self, key: str) -> None:
        if self.saved.get(key):
            reveal(Path(self.saved[key][0]))

    # ================================================================== check and run
    def check(self) -> bool:
        self.issues = W.check(self.wf, self.reg)
        errs = W.errors(self.issues)
        self.checked = not errs
        self.problems.clear()
        th = self.theme
        for i in self.issues:
            node = self.wf.nodes.get(i.node)
            where = f"{W.label_of(node, self.reg)}: " if node is not None else ""
            it = QListWidgetItem(icon("warn" if i.level == "error" else "info",
                                      th["danger"] if i.level == "error" else th["warning"], 16), f"{where}{i.message}")
            it.setData(Qt.UserRole, i.node or None)
            self.problems.addItem(it)
        if not self.issues:
            it = QListWidgetItem(icon("check", th["success"], 16), "No problems found.")
            self.problems.addItem(it)
        if errs or self.issues:
            self.tabs.setCurrentWidget(self.problems)
        self._refresh_cards()
        self._update_controls()
        return self.checked

    def _cache(self) -> W.WorkflowCache:
        return W.WorkflowCache(cache_dir() / "workflows", float(self.ctx.settings.get("workflow_cache_gb", 5.0)))

    def _job(self, file: str | None, action: str = "run", target: str | None = None, stamp: str = "", wf=None):
        cache = self._cache()
        return W.workflow_job(wf or self.wf, file or None, action, target, cache.root, cache.limit / 1024 ** 3,
                              str(cache_dir()), stamp)

    def run_files(self) -> list[str]:
        return list(self.files) if self.wf.batch_inputs() else [""]

    def run_variation(self) -> None:
        """Run again with a new random seed in every step that has one, every other setting as it is. The steps
        keep their own seeds (nothing changes on the canvas); the files get the run's date and time, so a
        variation never overwrites a design, and the log lists the seeds to repeat it."""
        if not self.checked or not W.seeds(self.wf, self.reg):
            return
        wf, drawn = W.reseeded(self.wf, self.reg)
        seeds = ", ".join(f"{W.label_of(wf.nodes[nid], self.reg)} {k} {v}" for nid, vals in drawn.items()
                          for k, v in vals.items())
        self._log("info", f"New variation: {seeds}", "")
        self.run_all(variant=(wf, drawn))

    def run_all(self, variant: tuple | None = None) -> None:
        """Run every file. `variant` (a reseeded workflow, its seeds) is run_variation's: always stamped."""
        if self.busy():
            return
        if not self.checked:
            if not self.check():
                self.ctx.toast("Fix the problems Check found first (see Problems).", "warning")
                return
            self.ctx.toast("Checked: the workflow is ready to run.", "success")
            return
        files = self.run_files()
        if not files:
            self.ctx.toast("Add the files to run the workflow on (Inputs tab).", "warning")
            return
        exists, stamps = [], {}
        policy, mark = self.ctx.pref("on_existing"), prefs.stamp()   # Setup › Outputs: one stamp per run
        for f in files:
            names = W.planned_names(self.wf, self.reg, Path(f).name if f else "input")
            for nid, name in names.items():
                node = self.wf.nodes[nid]
                folder = Path(node.folder) if node.where == "folder" else (Path(f).parent if f else None)
                if folder is not None and (folder / name).exists():
                    exists.append(folder / name)
                    if policy == "suffix":            # keep both: every Save of this file gets the stamp
                        stamps[f] = mark
        if variant is not None:                       # a new design: every file gets the stamp, nothing to ask
            exists, stamps = [], {f: mark for f in files}
        if exists and policy == "ask":
            names = "\n".join(p.name for p in exists[:8]) + ("\n…" if len(exists) > 8 else "")
            if QMessageBox.question(self, self.wf.name, f"{len(exists)} output file(s) already exist:\n\n{names}"
                                                        "\n\nOverwrite them?") != QMessageBox.Yes:
                return
        self.batch = {"total": len(files), "done": 0, "saved": 0, "failed": 0, "t0": time.monotonic(),
                      "outputs": [], "seeds": variant[1] if variant else None}
        needed = self.wf.needed()
        for f in files:
            for nid in needed:
                if self.wf.nodes[nid].type != W.INPUT:
                    self._set_state(f, nid, "queued")
            if f in self.files:
                self.queue.set_status(self.files[f], "Queued", "dim")
            self._submit(f, "run", stamp=stamps.get(f, ""), wf=variant[0] if variant else None)
        self._refresh_cards()
        self._update_controls()

    def _submit(self, file: str, action: str, target: str | None = None, stamp: str = "", wf=None) -> int:
        job = self._job(file, action, target, stamp, wf)
        holder = {}
        jid = self.ctx.bridge.submit(job, "process",
                                     lambda kind, payload: self._on_event(holder["jid"], kind, payload))
        holder["jid"] = jid
        self.jobs[jid] = {"file": file, "action": action, "target": target}
        return jid

    def preview_node(self, nid: str, quiet: bool = False) -> None:
        node = self.wf.nodes.get(nid)
        if node is None:
            return
        needed = self.wf.needed("preview", nid)
        needs_file = any(self.wf.nodes[n].type == W.INPUT and self.wf.nodes[n].mode == "batch" for n in needed)
        file = self.current_file() if needs_file else ""
        if needs_file and not file:
            if not quiet:
                self.ctx.toast("Add a file to preview with (Inputs tab).", "warning")
            return
        errs = [i for i in W.errors(W.check(self.wf, self.reg)) if i.node in needed]
        if errs:
            if not quiet:
                self.ctx.toast(errs[0].message, "warning")
                self.check()
            return
        if self.selected != nid:
            self.auto_previewed.add((nid, self._sig(nid), self.current_file()))
            self.select(nid)
        if self.preview_job is not None:
            self.ctx.bridge.cancel(self.preview_job)
        self.stage.clear("Preparing the preview…")
        self.preview_job = self._submit(file, "preview", nid)
        self._update_controls()

    def preview_selected(self) -> None:
        """Preview the selected step, or the last step before a Save."""
        nid = self.selected
        if nid is None:
            try:
                order = self.wf.order()
            except W.WorkflowError:
                order = list(self.wf.nodes)
            nid = next((n for n in reversed(order) if self.wf.nodes[n].type == W.TOOL), None)
        if nid is not None:
            self.preview_node(nid)

    def aim(self, nid: str) -> None:
        node = self.wf.nodes.get(nid)
        if node is None or node.tool not in self.reg.tools:
            return
        needed = self.wf.needed("prepare", nid)
        if self.wf.edge_into(nid) is None:
            self.ctx.toast("Connect an input to this step first.", "warning")
            return
        needs_file = any(self.wf.nodes[n].type == W.INPUT and self.wf.nodes[n].mode == "batch" for n in needed)
        file = self.current_file() if needs_file else ""
        if needs_file and not file:
            self.ctx.toast("Add a file to aim with (Inputs tab).", "warning")
            return
        self.ctx.status(f"Preparing the input of {W.label_of(node, self.reg)}…")
        self._submit(file, "prepare", nid)
        self._update_controls()

    def _aim_ready(self, nid: str, res) -> None:
        node = self.wf.nodes.get(nid)
        path = (res.data or {}).get("inputs", {}).get(W.MAIN) if res is not None else None
        if node is None or not path:
            self.ctx.toast("Could not prepare the step's input.", "danger")
            return
        tool = self.reg.get(node.tool)
        try:
            values = dict(W.node_values(node, tool))
        except ParamError:
            values = dict(tool.defaults())
        page = self.ctx.open_tool(tool.id)
        if page is not None:
            page.borrow(path, values, W.label_of(node, self.reg), lambda v, n=nid: self._aim_back(n, v))

    def _aim_back(self, nid: str, values: dict | None) -> None:
        self.ctx.open_workflow()
        if values is None:                     # discarded: the step stays as it was
            self.select(nid)
            return
        if nid in self.wf.nodes:
            self.set_node_values(nid, values)
            self.select(nid)
            self._show_inspector()
            self.ctx.toast("The step took the settings from the tool page.", "success")

    def cancel(self) -> None:
        for jid in list(self.jobs):
            self.ctx.bridge.cancel(jid)

    # ================================================================== events
    def _set_state(self, file: str, nid: str, state: str, **info) -> dict:
        st = {"state": state, "sig": self._sig(nid), **info}
        self.states.setdefault(file or "", {})[nid] = st
        return st

    def _on_event(self, jid: int, kind: str, payload) -> None:
        job = self.jobs.get(jid)
        if job is None:
            return
        file = job["file"]
        current = (file or "") == (self.current_file() or "")
        if kind == "node":
            nid, state, info = payload
            if nid not in self.wf.nodes:
                return
            prev = self.states.get(file or "", {}).get(nid) or {}
            if state == "cached" and prev.get("sig") == self._sig(nid) and prev.get("state") == "done" \
                    and not prev.get("draft") and "result" not in info:
                return                             # the output this session just made: keep "done" and its time
            st = self._set_state(file, nid, state, **info)
            if state == "cached" and "result" not in info and prev.get("sig") == st["sig"] and prev.get("result"):
                st["result"] = prev["result"]      # the same output: keep the preview already made
            if state == "error":
                self._log("error", f"{W.label_of(self.wf.nodes[nid], self.reg)}: {info.get('error', '')}", file)
            if current:
                self.view.update_card(self._card(self.wf.nodes[nid]))
                if nid == self.selected and state in ("done", "cached", "error"):
                    self._show_preview()
                    self._feed_info()
            return
        if kind == "progress":
            frac, msg = payload
            if file in self.files and job["action"] == "run":
                self.queue.set_status(self.files[file], f"{frac * 100:.0f}%", "busy", msg)
            return
        if kind == "log":
            self._log(payload[0], payload[1], file)
            return
        if kind == "started":
            if file in self.files and job["action"] == "run":
                self.queue.set_status(self.files[file], "Running…", "busy")
            return
        if kind not in ("done", "error", "cancelled"):
            return
        self.jobs.pop(jid, None)
        action = job["action"]
        if action == "preview":
            if jid == self.preview_job:
                self.preview_job = None
            if kind == "done" and job["target"] in self.wf.nodes:
                res = payload[0]
                st = self._state(job["target"], file) or self._set_state(file, job["target"], "done")
                st["result"] = res
                if job["target"] == self.selected and current:
                    self._show_preview()
            elif kind == "error":
                self.stage.clear("The preview failed: see the Log.")
                self._log("error", payload[0], file)
        elif action == "prepare":
            if kind == "done":
                self._aim_ready(job["target"], payload[0])
            elif kind == "error":
                self.ctx.toast(payload[0], "danger")
        else:
            self._on_run_end(file, kind, payload)
            for st in self.states.get(file or "", {}).values():   # steps that never started are not queued now
                if st.get("state") == "queued":
                    st["state"] = "cancelled"
        self._refresh_cards()
        self._update_controls()

    def _on_run_end(self, file: str, kind: str, payload) -> None:
        b = self.batch
        it = self.files.get(file)
        rec = None
        meta = {"name": self.wf.name, "path": str(self.path or "")}
        if b is not None and b.get("seeds"):              # a new variation: its seeds, to repeat it
            meta["seeds"] = b["seeds"]
        if kind == "done":
            res = payload[0]
            self.saved[file] = list(res.outputs)
            self.reports[file] = res.report
            if b is not None:
                b["saved"] += bool(res.outputs)
                b["outputs"] += list(res.outputs)
            if it is not None:
                self.queue.set_status(it, "Saved" if res.outputs else (res.status or "Nothing saved"),
                                      "success" if res.outputs else "muted",
                                      "Saved as\n" + "\n".join(res.outputs) + "\nDouble-click to show it.")
                self.queue.set_cells(it, res)
            secs = (res.data or {}).get("seconds", 0.0) if isinstance(res.data, dict) else 0.0
            rec = RunRecord("workflow", str(W.FORMAT_VERSION), "done", file, list(res.outputs), {}, secs,
                            toolkit_version=__version__, workflow=meta,
                            stats={s.key: s.value for s in res.stats if isinstance(s.value, (int, float, str))})
            if not self.selected and (file or "") == (self.current_file() or ""):
                self.report.setPlainText(res.report)
        elif kind == "error":
            if b is not None:
                b["failed"] += 1
            if it is not None:
                self.queue.set_status(it, "Error", "danger", payload[0])
            self._log("error", payload[0] + (f"\n{payload[1]}" if len(payload) > 1 and payload[1] else ""), file)
            rec = RunRecord("workflow", str(W.FORMAT_VERSION), "error", file, [], {}, message=payload[0],
                            toolkit_version=__version__, workflow=meta)
        elif it is not None:
            self.queue.set_status(it, "Cancelled", "warning")
        if rec is not None:
            try:
                self.ctx.history.append(rec)
                self.ctx.history_changed.emit()
            except OSError:
                pass
        if b is not None:
            b["done"] += 1
            if b["done"] >= b["total"]:
                self._finish_batch()

    def _finish_batch(self) -> None:
        b, self.batch = self.batch, None
        secs = time.monotonic() - b["t0"]
        note = (f"{self.wf.name}: saved {len(b['outputs'])} file(s) from {b['total']} run(s) in {secs:.1f} s."
                + (f"  {b['failed']} failed." if b["failed"] else ""))
        tone = "danger" if b["failed"] and not b["saved"] else ("warning" if b["failed"] else "success")
        first = b["outputs"][0] if b["outputs"] else None
        self.ctx.toast(note, tone, "Show" if first else "", (lambda: reveal(Path(first))) if first else None)
        self.ctx.status(note)
        self._log("info", note, "")
        self.ctx.run_finished(b["outputs"])

    def _log(self, level: str, msg: str, file: str) -> None:
        src = Path(file).name if file else self.wf.name
        self.logview.appendPlainText(f"{time.strftime('%H:%M:%S')}  {level:<7} {src}: {msg}")
        self.ctx.log(level, msg, f"workflow · {src}")

    # ================================================================== preview
    def _show_preview(self) -> None:
        nid = self.selected
        if nid is None or nid not in self.wf.nodes:
            self.info_line.setText("")
            self.stage.clear("Select a step to see its result.")
            return
        node = self.wf.nodes[nid]
        label_ = W.label_of(node, self.reg)
        st = self._state(nid)
        res = (st or {}).get("result")
        if res is not None and res.preview:
            self.stage.show_views(res.preview.get("views", []), res.preview.get("prefer"))
            self.info_line.setText(f"{label_}  ·  {Path(self.current_file()).name or 'fixed inputs'}"
                                   + ("  ·  draft" if (st or {}).get("draft") else ""))
            self.report.setPlainText(res.report or "")
            return
        self.info_line.setText(label_)
        if st is not None and st["state"] == "error":
            self.stage.clear("This step failed: see the Log.")
            self.report.setPlainText(st.get("error", ""))
            return
        if st is not None and st["state"] in ("done", "cached"):
            key = (nid, st["sig"], self.current_file())
            if key not in self.auto_previewed and self.preview_job is None:
                self.auto_previewed.add(key)
                self.preview_node(nid, quiet=True)
                return
        self.stage.clear("Not run yet. Preview (P or double-click) runs the workflow up to this step.")
        self.report.setPlainText(st.get("report", "") if st else "")

    # ================================================================== state
    def busy(self) -> bool:
        return self.batch is not None

    def idle(self) -> bool:
        return not self.jobs

    def _update_head(self) -> None:
        self.title.setText(self.wf.name + ("  •" if self.dirty else ""))
        self.file_label.setText(self.path.name if self.path else "not saved")
        self.file_label.setToolTip(str(self.path or ""))

    def _update_controls(self) -> None:
        busy = self.busy()
        errs = W.errors(self.issues)
        warns = [i for i in self.issues if i.level == "warning"]
        if not self.wf.nodes:
            set_badge(self.state_badge, "empty", "")
        elif errs:
            set_badge(self.state_badge, f"{len(errs)} problem{'s' if len(errs) != 1 else ''}", "danger")
        elif self.checked:
            set_badge(self.state_badge, "ready" + (f" · {len(warns)} warning{'s' if len(warns) != 1 else ''}"
                                                   if warns else ""), "warning" if warns else "success")
        else:
            set_badge(self.state_badge, "not checked", "")
        files = self.run_files()
        n = len([f for f in files if f])
        self.files_label.setText(f"FILES TO RUN ON  ·  {n}" if n else "FILES TO RUN ON")
        self.queue.drop.setVisible(not self.files)
        self.btn_run.setText(f"Run  ·  {n} file{'s' if n != 1 else ''}" if n else "Run")
        self.btn_run.setEnabled(self.checked and not busy and bool(files))
        self.btn_run.setToolTip("" if self.checked else "Check the workflow first: Run is available once it passes.")
        self.btn_check.setEnabled(not busy and bool(self.wf.nodes))
        n_seeds = len(W.seeds(self.wf, self.reg)) if self.checked else 0
        self.btn_vary.setVisible(bool(n_seeds))
        self.btn_vary.setEnabled(not busy and bool(files))
        self.btn_vary.setToolTip(f"Run again with new random seeds ({n_seeds}) and the same settings. The files "
                                 "get the date and time, so every variation is kept." if n_seeds else "")
        self.btn_cancel.setVisible(bool(self.jobs))
        for w in (self.btn_add, self.btn_add_dir, self.btn_remove):
            w.setEnabled(not busy)
        self.btn_open.setEnabled(bool(self.saved) or any(n.where == "folder" and n.folder for n in self.wf.saves()))
        if busy:
            b = self.batch
            self.run_note.setText(f"Running: {b['done']} of {b['total']} done…")
        elif not self.wf.nodes:
            self.run_note.setText("Add an input, the tools and a Save from the list, then Check.")
        elif not self.checked:
            self.run_note.setText("Check the workflow; Run is available once it passes.")
        elif not files:
            self.run_note.setText("Add the files to run on in the Inputs tab.")
        else:
            self.run_note.setText("Ready. Unchanged steps come from the cache.")
        self._update_head()

    def status_text(self) -> str:
        return f"Workflow {self.wf.name}: {len(self.wf.nodes)} steps, {len(self.files)} file(s)."

    def open_output_folder(self) -> None:
        f = self.current_file()
        if self.saved.get(f):
            reveal(Path(self.saved[f][0]))
            return
        for n in self.wf.saves():
            if n.where == "folder" and n.folder:
                reveal(Path(n.folder))
                return
        if f:
            reveal(Path(f).parent)

    def clear_cache(self) -> None:
        if self.busy():
            return
        c = self._cache()
        size = c.size()
        c.clear()
        self.states = {}
        self._refresh_cards()
        self.ctx.toast(f"Workflow cache cleared ({size / 1024 ** 2:.0f} MB).", "success")

    def toggle_library(self) -> None:
        self.library.setVisible(self.library.isHidden())
        self.ctx.settings.set("library_on", not self.library.isHidden())

    def toggle_library_rail(self) -> None:
        """Icons only or names, showing the step list if it was hidden."""
        self.library.set_mode("full" if self.library.rail else "icons")
        if self.library.isHidden():
            self.toggle_library()

    def retheme(self, theme) -> None:
        self.theme = theme
        self.view.set_theme(theme)
        self.stage.set_theme(theme)
        self.queue.retheme(theme)
        self.library.retheme(theme)
        self.btn_vary.setIcon(icon("dice", theme["text-2"], 16))
        self._refresh_cards()
        self._show_inspector()

    def set_art(self, art: str) -> None:
        self.stage.set_art(art)

    def showEvent(self, e):
        super().showEvent(e)
        if self._fit_pending:                  # fit once the canvas has its real size
            self._fit_pending = False
            QTimer.singleShot(0, self.view_fit)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
