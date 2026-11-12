"""
The workflow inspector: the selected step's settings, or the workflow's own panel when nothing is selected.

A tool step shows the same presets and form as the tool's page (core/params → ParamForm), so a step is set up exactly
like the tool on its own; right-click a setting's name to show it on the workflow panel. Every edit goes back to the
page, which records it (undo) and marks the workflow as needing a new Check.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPlainTextEdit, QPushButton, QRadioButton, QVBoxLayout, QWidget)

from ...core import workflow as W
from ...core.params import ParamError, Section
from ...core.tool import KINDS
from ..theme import icon, sans
from ..widgets.common import RuntimeNotice, Segmented, badge, hint, label, section_head
from ..widgets.param_form import ParamForm
from ..widgets.presets import PresetBar


def _title(text: str) -> QLabel:
    t = QLabel(text)
    t.setProperty("class", "title")
    t.setFont(sans(20, 300))
    t.setWordWrap(True)
    return t


class NodeInspector(QWidget):
    """Built for one node; the page makes a new one when the selection changes."""

    def __init__(self, page, node: W.Node):
        super().__init__()
        self.page, self.node, self.ctx = page, node, page.ctx
        self.theme = self.ctx.theme
        self.form: ParamForm | None = None
        self.presets: PresetBar | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.lay = lay
        reg = self.ctx.registry
        tool = reg.tools.get(node.tool) if node.type == W.TOOL else None
        top = QHBoxLayout()
        if node.type == W.TOOL:
            top.addWidget(label(tool.category_title if tool else "missing tool"))
            if tool:
                top.addWidget(badge(tool.io_badge, "accent"))
        else:
            top.addWidget(label("workflow input" if node.type == W.INPUT else "workflow output"))
        top.addStretch(1)
        if tool:
            top.addWidget(badge(f"v{tool.version}"))
        lay.addLayout(top)
        lay.addWidget(_title(W.label_of(node, reg)))
        self.name = QLineEdit(node.label)
        self.name.setPlaceholderText(W.label_of(W.Node("", node.type, tool=node.tool, kind=node.kind), reg))
        self.name.setToolTip("The step's name on the canvas (and {node} in Save file names).")
        self.name.editingFinished.connect(lambda: self.page.set_node_attrs(node.id, "label", label=self.name.text()
                                                                           .strip()))
        lay.addWidget(self.name)
        if node.type == W.TOOL:
            self._tool(tool)
        elif node.type == W.INPUT:
            self._input()
        else:
            self._save()
        lay.addStretch(1)

    # ------------------------------------------------------------------ tool steps
    def _tool(self, tool) -> None:
        n = self.node
        if tool is None:
            self.lay.addWidget(hint(f"The tool “{n.tool}” is not installed here (a plug-in?). Install it, or remove "
                                    "this step."))
            return
        from ...core import runtimes
        try:
            saved_values = W.node_values(n, tool)
            settings_error = None
        except ParamError as e:
            saved_values = tool.defaults()
            settings_error = str(e)
        self.runtime_notices = []
        for key in dict.fromkeys((*tool.requires, *(key for key, _ in tool.requires_if))):
            rt = runtimes.get(key)
            notice = RuntimeNotice(rt, self.ctx.settings, guide=lambda: self.ctx.show_setup("components"), compact=True)
            notice.changed.connect(self.ctx.refresh_runtimes)
            notice.changed.connect(self._values_changed)
            notice.setVisible(rt in runtimes.missing(tool, saved_values))
            self.runtime_notices.append(notice)
            self.lay.addWidget(notice)
        if tool.summary:
            s = QLabel(tool.summary)
            s.setProperty("class", "summary")
            s.setWordWrap(True)
            self.lay.addWidget(s)
        self.form = ParamForm(tool, self.theme, label_menu=self._label_menu)
        self.form.set_values(saved_values)
        if settings_error:
            self.lay.addWidget(hint(f"Saved settings were not valid ({settings_error}); showing the defaults."))
        self.presets = PresetBar(tool, self.ctx.presets, self.form, self.theme, self.ctx.toast,
                                 extra=[("Use the tool page's current settings", self._from_tool_page)],
                                 manage=self.ctx.manage_presets)
        self.presets.sync()
        self.presets.applied.connect(self._values_changed)
        self.form.changed.connect(self._values_changed)
        self.form.mark(k for nid, k in self.page.wf.exposed if nid == n.id)
        self.lay.addSpacing(4)
        self.lay.addWidget(self.presets)
        self.lay.addWidget(self.form)

        self.lay.addSpacing(6)
        self.lay.addWidget(label("output"))
        if tool.output.formats:
            fr = QHBoxLayout()
            fl = QLabel("Format")
            fl.setProperty("class", "muted")
            fr.addWidget(fl)
            self.fmt = QComboBox()
            for ext in tool.output.formats:
                self.fmt.addItem(ext.lstrip(".").upper(), ext)
            i = self.fmt.findData(n.format or tool.output.ext)
            self.fmt.setCurrentIndex(max(i, 0))
            self.fmt.currentIndexChanged.connect(
                lambda _i: self.page.set_node_attrs(n.id, "format", format=self.fmt.currentData()
                                                    if self.fmt.currentData() != tool.output.ext else ""))
            fr.addWidget(self.fmt, 1)
            self.lay.addLayout(fr)
        self.lay.addWidget(hint(f"Hands on {tool.output.template.replace('{stem}', 'name')}, kept in the workflow "
                                "cache. Connect a Save to keep it on disk."))
        if tool.output.format_notes:
            note = hint(tool.output.format_notes.get(n.format or tool.output.ext, ''))
            self.lay.addWidget(note)
            if tool.output.formats:
                self.fmt.currentIndexChanged.connect(lambda _i: note.setText(tool.output.format_notes.get(self.fmt.currentData(), '')))
        self.lay.addSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        prev = QPushButton("Preview")
        prev.setToolTip("Run the workflow up to this step and show its result (P). Unchanged steps come from the "
                        "cache; nothing is saved.")
        prev.clicked.connect(lambda: self.page.preview_node(n.id))
        aim = QPushButton("Aim in tool…" if (tool.viewer or tool.panel) else "Open in tool…")
        aim.setToolTip("Prepare this step's input, open it in the tool's own page with these settings, and bring "
                       "the settings back with “Back to workflow”.")
        aim.clicked.connect(lambda: self.page.aim(n.id))
        row.addWidget(prev, 1)
        row.addWidget(aim, 1)
        self.lay.addLayout(row)
        keep = QPushButton("Save this step's file")
        keep.setToolTip("Add a Save connected to this step.")
        keep.clicked.connect(lambda: self.page.save_step(n.id))
        self.lay.addWidget(keep)

    def _values_changed(self) -> None:
        from ...core import runtimes
        tool = self.ctx.registry.get(self.node.tool)
        for notice in getattr(self, 'runtime_notices', []):
            notice.setVisible(notice.runtime in runtimes.missing(tool, self.form.values()))
        if self.presets is not None:
            self.presets.sync()
        self.page.set_node_values(self.node.id, self.form.values())

    def _from_tool_page(self) -> None:
        tool = self.ctx.registry.get(self.node.tool)
        self.form.set_values(self.ctx.tool_settings(tool).values())
        self._values_changed()

    def _label_menu(self, key: str, pos) -> None:
        on = (self.node.id, key) in self.page.wf.exposed
        m = QMenu(self)
        m.addAction(icon("star", self.theme["text-2"], 16),
                    "Remove from the workflow panel" if on else "Show on the workflow panel",
                    lambda: self.page.toggle_exposed(self.node.id, key))
        m.exec(pos)

    def mark_exposed(self) -> None:
        if self.form is not None:
            self.form.mark(k for nid, k in self.page.wf.exposed if nid == self.node.id)

    def set_info(self, info) -> None:
        if self.form is not None:
            self.form.set_info(info)

    # ------------------------------------------------------------------ inputs
    def _input(self) -> None:
        n = self.node
        self.lay.addSpacing(4)
        self.lay.addWidget(label("kind"))
        kinds = [(k, "SVG" if k == "svg" else badge_.capitalize()) for k, (_e, badge_) in KINDS.items()]
        seg = Segmented(kinds)
        seg.set_value(n.kind)
        seg.picked.connect(lambda v: self.page.set_node_attrs(n.id, "kind", kind=v))
        self.lay.addWidget(seg)
        self.lay.addSpacing(6)
        self.lay.addWidget(label("files"))
        batch = QRadioButton("Each file of the run")
        fixed = QRadioButton("One fixed file")
        grp = QButtonGroup(self)
        grp.addButton(batch)
        grp.addButton(fixed)
        (batch if n.mode == "batch" else fixed).setChecked(True)
        batch.toggled.connect(lambda on: self.page.set_node_attrs(n.id, "mode", mode="batch" if on else "fixed"))
        self.lay.addWidget(batch)
        self.lay.addWidget(fixed)
        if n.mode == "fixed":
            row = QHBoxLayout()
            self.path = QLineEdit(n.path)
            self.path.setPlaceholderText("choose a file")
            self.path.editingFinished.connect(lambda: self.page.set_node_attrs(n.id, "path", path=self.path.text()
                                                                               .strip()))
            pick = QPushButton("Choose…")
            pick.clicked.connect(self._pick_fixed)
            row.addWidget(self.path, 1)
            row.addWidget(pick)
            self.lay.addLayout(row)
            self.lay.addWidget(hint("The same file for every run: a shape to cut with, a depth map, a reference "
                                    "model."))
        else:
            self.lay.addWidget(hint("Add the files to run on in the Inputs tab under the canvas, or drop them on the "
                                    "window. The workflow runs once per file."))
        prev = QPushButton("Preview")
        prev.clicked.connect(lambda: self.page.preview_node(n.id))
        self.lay.addWidget(prev)

    def _pick_fixed(self) -> None:
        exts = " ".join(f"*{x}" for x in KINDS.get(self.node.kind, ((), ""))[0]) or "*.*"
        f, _ = QFileDialog.getOpenFileName(self, "Fixed input", str(Path(self.node.path).parent) if self.node.path
                                           else "", f"Files ({exts});;All files (*.*)")
        if f:
            self.path.setText(f)
            self.page.set_node_attrs(self.node.id, "path", path=f)

    # ------------------------------------------------------------------ saves
    def _save(self) -> None:
        n = self.node
        self.lay.addSpacing(4)
        self.lay.addWidget(label("where"))
        nxt = QRadioButton("Next to each input file")
        fold = QRadioButton("In a folder")
        grp = QButtonGroup(self)
        grp.addButton(nxt)
        grp.addButton(fold)
        (fold if n.where == "folder" else nxt).setChecked(True)
        nxt.toggled.connect(lambda on: self.page.set_node_attrs(n.id, "where", where="next" if on else "folder"))
        self.lay.addWidget(nxt)
        row = QHBoxLayout()
        row.addWidget(fold)
        row.addStretch(1)
        pick = QPushButton("Choose…")
        pick.clicked.connect(self._pick_folder)
        row.addWidget(pick)
        self.lay.addLayout(row)
        if n.folder:
            f = hint(n.folder)
            f.setTextFormat(Qt.PlainText)
            self.lay.addWidget(f)
        self.lay.addSpacing(6)
        self.lay.addWidget(label("file name"))
        self.tpl = QLineEdit(n.name)
        self.tpl.setFont(sans(13))
        self.tpl.editingFinished.connect(lambda: self.page.set_node_attrs(n.id, "name", name=self.tpl.text().strip()
                                                                          or "{name}"))
        self.lay.addWidget(self.tpl)
        self.example = hint("")
        self.example.setTextFormat(Qt.PlainText)
        self.lay.addWidget(self.example)
        self.lay.addWidget(hint("{name} the step's file name (each tool adds its suffix) · {stem} the input's "
                                "name · {workflow} · {node} the step's name. The extension is the step's. "
                                "Existing files: you are asked before they are replaced."))
        self.refresh_example()
        prev = QPushButton("Preview")
        prev.setToolTip("Show the file this Save would write (P).")
        prev.clicked.connect(lambda: self.page.preview_node(n.id))
        self.lay.addWidget(prev)

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Save in", self.node.folder or "")
        if folder:
            self.page.set_node_attrs(self.node.id, "folder", folder=folder, where="folder")

    def refresh_example(self) -> None:
        if self.node.type == W.SAVE and hasattr(self, "example"):
            name = self.page.example_save_name(self.node.id)
            self.example.setText(f"→ {name}" if name else "→ connect a step to see the name")


class WorkflowPanel(QWidget):
    """Nothing selected: the workflow's name, description and the parameters shown on its panel."""

    def __init__(self, page):
        super().__init__()
        self.page, self.ctx = page, page.ctx
        self.theme = self.ctx.theme
        wf = page.wf
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(label("workflow"))
        self.name = QLineEdit(wf.name)
        self.name.setFont(sans(16, 400))
        self.name.editingFinished.connect(lambda: self.page.set_workflow_attrs(name=self.name.text().strip()
                                                                               or "Untitled workflow"))
        lay.addWidget(self.name)
        self.desc = QPlainTextEdit(wf.description)
        self.desc.setPlaceholderText("What this workflow makes, and from what.")
        self.desc.setMaximumHeight(84)
        self.desc.setFont(sans(12.5))
        self.desc.textChanged.connect(lambda: self.page.set_workflow_attrs(description=self.desc.toPlainText(),
                                                                           merge="description"))
        lay.addWidget(self.desc)
        steps = sum(1 for n in wf.nodes.values() if n.type == W.TOOL)
        saves = len(wf.saves())
        lay.addWidget(hint(f"{steps} step{'s' if steps != 1 else ''} · {saves} save{'s' if saves != 1 else ''}"
                           + (f" · {page.path.name}" if page.path else " · not saved yet")))
        lay.addSpacing(8)
        lay.addWidget(section_head("Workflow parameters"))
        self.form = None
        params, values = self._exposed()
        if params:
            spec = SimpleNamespace(params=params, param_list=[p for s in params for p in s.params])
            self.form = ParamForm(spec, self.theme, label_menu=self._label_menu)
            self.form.set_values(values)
            self.form.changed.connect(self._changed)
            lay.addWidget(self.form)
        else:
            lay.addWidget(hint("Right-click a setting's name in a step to show it here: the few knobs you turn when "
                               "you reuse the workflow (pieces, seed, paper …)."))
        lay.addStretch(1)

    def _exposed(self):
        reg = self.ctx.registry
        wf = self.page.wf
        sections: dict = {}
        values: dict = {}
        for nid, key in wf.exposed:
            node = wf.nodes.get(nid)
            tool = reg.tools.get(node.tool) if node is not None else None
            if tool is None or key not in {p.key for p in tool.param_list}:
                continue
            p = tool.param(key)
            q = dataclasses.replace(p, key=f"{nid}.{key}", visible_if=None, advanced=False)
            sections.setdefault(nid, Section(W.label_of(node, reg))).params.append(q)
            try:
                values[q.key] = W.node_values(node, tool)[key]
            except ParamError:
                values[q.key] = p.validate(None)
        return list(sections.values()), values

    def _changed(self) -> None:
        by_node: dict = {}
        for k, v in self.form.values().items():
            nid, key = k.split(".", 1)
            by_node.setdefault(nid, {})[key] = v
        for nid, vals in by_node.items():
            self.page.set_node_values(nid, vals, partial=True)

    def _label_menu(self, key: str, pos) -> None:
        nid, k = key.split(".", 1)
        m = QMenu(self)
        m.addAction("Remove from the workflow panel", lambda: self.page.toggle_exposed(nid, k))
        m.addAction("Select its step", lambda: self.page.select(nid))
        m.exec(pos)
