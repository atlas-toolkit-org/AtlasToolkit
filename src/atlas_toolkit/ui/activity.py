"""Activity: every finished run, newest first. Reveal outputs, or open the tool again with the same settings."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core.paths import reveal
from .theme import mono, qcolor, sans
from .widgets.common import label


class ActivityPage(QWidget):
    COLS = ["When", "Tool", "Input", "Output", "Time", "Status"]

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 30, 40, 24)
        lay.setSpacing(10)
        lay.addWidget(label("activity"))
        t = QLabel("Runs")
        t.setFont(sans(30, 300))
        lay.addWidget(t)
        bar = QHBoxLayout()
        self.count = QLabel("")
        self.count.setProperty("class", "muted")
        bar.addWidget(self.count)
        bar.addStretch(1)
        self.btn_reveal = QPushButton("Show output")
        self.btn_reveal.clicked.connect(self.reveal_selected)
        self.btn_rerun = QPushButton("Open with these settings")
        self.btn_rerun.setToolTip("Open the tool with this input and the settings that run used.")
        self.btn_rerun.clicked.connect(self.rerun_selected)
        self.btn_clear = QPushButton("Clear history")
        self.btn_clear.setProperty("variant", "ghost")
        self.btn_clear.clicked.connect(self.clear)
        for b in (self.btn_reveal, self.btn_rerun, self.btn_clear):
            bar.addWidget(b)
        lay.addLayout(bar)
        self.table = QTreeWidget()
        self.table.setColumnCount(len(self.COLS))
        self.table.setHeaderLabels([c.upper() for c in self.COLS])
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.itemDoubleClicked.connect(lambda *_: self.reveal_selected())
        self.table.itemSelectionChanged.connect(self._sel)
        h = self.table.header()
        h.setStretchLastSection(False)
        for i, w in enumerate((150, 160, 0, 0, 80, 90)):
            if w:
                h.setSectionResizeMode(i, QHeaderView.Interactive)
                self.table.setColumnWidth(i, w)
            else:
                h.setSectionResizeMode(i, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        self.records = []
        ctx.history_changed.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        self.records = self.ctx.history.read(1000)
        self.table.clear()
        th = self.ctx.theme
        for r in self.records:
            tool = self.ctx.registry.tools.get(r.tool)
            name = (f"Workflow · {r.workflow.get('name', '')}" if r.tool == "workflow"
                    else tool.name if tool else r.tool)
            out = ", ".join(Path(o).name for o in r.outputs) or "—"
            it = QTreeWidgetItem([time.strftime("%Y-%m-%d %H:%M", time.localtime(r.time)),
                                  name, Path(r.src).name if r.src else "—", out,
                                  f"{r.seconds:.1f} s", r.status])
            it.setToolTip(2, r.src)
            it.setToolTip(3, "\n".join(r.outputs) or r.message)
            for c in (0, 4):
                it.setFont(c, mono(11.5))
            tone = {"done": "success", "error": "danger", "cancelled": "warning"}.get(r.status, "text-2")
            it.setForeground(5, qcolor(th[tone]))
            self.table.addTopLevelItem(it)
        self.count.setText(f"{len(self.records)} runs" if self.records else "No runs yet.")
        self._sel()

    def _current(self):
        it = self.table.currentItem()
        if it is None:
            return None
        i = self.table.indexOfTopLevelItem(it)
        return self.records[i] if 0 <= i < len(self.records) else None

    def _sel(self) -> None:
        r = self._current()
        self.btn_reveal.setEnabled(bool(r and r.outputs and Path(r.outputs[0]).exists()))
        if r is not None and r.tool == "workflow":
            self.btn_rerun.setEnabled(bool(r.workflow.get("path")) and Path(r.workflow["path"]).exists())
        else:
            self.btn_rerun.setEnabled(bool(r and r.tool in self.ctx.registry.tools and Path(r.src).exists()))
        self.btn_clear.setEnabled(bool(self.records))

    def reveal_selected(self) -> None:
        r = self._current()
        if r and r.outputs:
            reveal(Path(r.outputs[0]))

    def rerun_selected(self) -> None:
        r = self._current()
        if r and r.tool == "workflow":
            self.ctx.open_workflow(r.workflow.get("path"), [r.src] if r.src and Path(r.src).exists() else None)
        elif r:
            self.ctx.open_tool(r.tool, [r.src], r.values)

    def clear(self) -> None:
        if QMessageBox.question(self, "Clear history", "Forget every run in the Activity list? Output files are "
                                                       "not touched.") == QMessageBox.Yes:
            self.ctx.history.clear()
            self.reload()

    def retheme(self, theme) -> None:
        self.reload()
