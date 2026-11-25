"""
ToolPage: the one page every tool gets, built from its spec.

    head      one line: light title, version badge, a hairline, the summary (elided; category · I/O in a tooltip)
    centre    input queue over Report / Log  |  preview stage + stat tiles
              (the button left of INPUTS folds the queue and Report / Log away; a compact picker then selects the
              input; remembered for every tool as "inputs_folded")
    inspector presets, parameter form, output options, primary action, Preview / Cancel / Open folder

Flow: files are inspected as they are added (size, layers, a first preview). Live tools (cheap dry runs) analyse
every input again whenever the settings change, and the primary button writes only inputs with something to
change. Other tools offer Preview (a draft dry run of the selected input) and Run (all inputs).

Each input keeps its own values of the tool's per-input parameters (Param.per_input: the Aim camera, Up / Front /
Extra turn, a plane, a painted area): the form shows the selected file's, and aiming one file never moves another.
A new file starts from the selected one's (a stage set keeps one view), or from what it had when last aimed here.
Every result remembers the values it was made with (a signature), so a preview stays until that file's own values
change, previews of other files go on running, and going back to earlier settings brings their preview back.
"""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSplitter,
                               QTabWidget, QToolButton, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import runtimes
from ..geom.repair import REPAIR_TOOL
from ..core.history import RunRecord
from ..core.io import expand_inputs, output_path
from ..core.job import Job
from ..core.params import AUTO, resolve_auto
from ..core import prefs
from ..core.paths import cache_dir, open_file, reveal
from ..core.tool import KINDS, InputInfo, Result
from ..version import __version__
from .theme import icon, label_font, mono, sans
from .widgets.common import (SLOW_ADVICE, ElidedLabel, Mark, RuntimeNotice, StatTile, apply_icon, badge, draft_tip,
                             hint, icon_button, label, repolish, tip_html)
from .widgets.file_queue import FileQueue
from .widgets.param_form import ParamForm
from .widgets.presets import PresetBar
from .widgets.preview import PreviewStage

INSPECTOR_W = 340          # default width; drag the divider to change it (remembered for every tool)
INSPECTOR_MIN, INSPECTOR_MAX = 300, 640
DRAFTS_KEPT = 4            # previews each input keeps, by settings: going back to earlier settings shows theirs again
VIEWS_KEPT = 200           # files whose own view (per-input values) is remembered between sessions


@dataclass
class Row:
    key: str
    item: QTreeWidgetItem
    info: InputInfo | None = None
    info_error: str = ""
    result: Result | None = None
    result_mode: str = ""
    error: str = ""
    fix_tool: str = ""            # a tool that can fix this input (a ToolError's suggest_tool), e.g. Repair Mesh
    fix_hint: str = ""
    saved: list = field(default_factory=list)
    jobs: set = field(default_factory=set)
    sig: str | None = None        # the signature of the values `result` (or the last analysis) was made with
    view: dict = field(default_factory=dict)        # this input's own values of the per-input keys
    view_set: bool = False        # changed for this file (remembered between sessions), not just inherited
    drafts: OrderedDict = field(default_factory=OrderedDict)   # signature -> preview Result, newest last
    draft_job: int | None = None  # its running preview, made with the values whose signature is draft_sig
    draft_sig: str = ""
    draft_prefer_result: bool = True
    run_values: dict = field(default_factory=dict)  # the values its last Run was submitted with


class ToolPage(QWidget):
    send_to = Signal(str, list)     # tool id, paths

    def __init__(self, tool, ctx):
        super().__init__()
        self.tool = tool
        self.ctx = ctx
        self.theme = ctx.theme
        self.store = ctx.tool_settings(tool)
        self.rows: dict[str, Row] = {}
        self.input_keys = frozenset(tool.input_keys)   # per-input parameters: each file keeps its own values
        self._view_key: str | None = None    # the input whose per-input values the form holds
        self.batch: dict | None = None      # the running Run batch
        self.borrowed: dict | None = None   # aiming a workflow step: {key, added, prev, title, on_return}
        self.note = ""
        self.cache = cache_dir() / tool.id
        self.debounce = QTimer(self, singleShot=True, interval=ctx.pref("preview_delay_ms"),
                               timeout=self._apply_settings)
        self.setAcceptDrops(True)
        self.inputs_folded = False
        self._build()
        self._restore()
        self._update_controls()

    # ================================================================== layout
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.outer = QSplitter(Qt.Horizontal)    # page | inspector, the divider drags
        self.outer.setChildrenCollapsible(False)
        self.outer.setHandleWidth(5)
        self.outer.splitterMoved.connect(self._inspector_moved)
        root.addWidget(self.outer)
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(26, 14, 18, 12)
        ml.setSpacing(8)
        ml.addWidget(self._build_banner())
        ml.addLayout(self._build_head())
        ml.addWidget(self._build_notices())             # below the tool's name: why it cannot run yet

        bar = QHBoxLayout()
        bar.setSpacing(6)
        # fold the file list and Report / Log away to the left, for a wider preview (remembered for every tool);
        # while folded, a compact picker in this bar still steps through the inputs
        self.btn_fold = icon_button("left", "", size=16)
        self.btn_fold.clicked.connect(lambda: self.set_inputs_folded(not self.inputs_folded))
        bar.addWidget(self.btn_fold)
        self.files_label = label("inputs")
        bar.addWidget(self.files_label)
        self.btn_prev = icon_button("up", "Previous file", size=14)    # up/down: the list's order, not the fold
        self.btn_prev.clicked.connect(lambda: self._step_input(-1))
        self.picker = QComboBox()
        self.picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.picker.setMinimumContentsLength(18)
        self.picker.setMaximumWidth(320)
        self.picker.setToolTip("The input shown in the preview")
        self.picker.activated.connect(self._picked)
        self.btn_next = icon_button("down", "Next file", size=14)
        self.btn_next.clicked.connect(lambda: self._step_input(1))
        self.picker_status = QLabel("")             # the selected input's status, as the hidden list shows it
        self.picker_status.setFont(mono(11.5))
        self.picker_box = QWidget()
        pl = QHBoxLayout(self.picker_box)
        pl.setContentsMargins(4, 0, 0, 0)
        pl.setSpacing(2)
        for w in (self.btn_prev, self.picker, self.btn_next):
            pl.addWidget(w)
        pl.addSpacing(6)
        pl.addWidget(self.picker_status)
        self.picker_box.hide()
        bar.addWidget(self.picker_box)
        bar.addStretch(1)
        self.btn_add = QPushButton("Add files…")
        self.btn_add.clicked.connect(self.pick_files)
        self.btn_add_dir = QPushButton("Add folder…")
        self.btn_add_dir.clicked.connect(self.pick_folder)
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self.clear)
        self.btn_catalogue = None
        if self.tool.inputs.kind == "mesh" and ".ply" in self.tool.inputs.exts:
            from .catalogue import catalogue_button
            self.btn_catalogue = catalogue_button(self.theme)
            self.btn_catalogue.clicked.connect(self.pick_catalogue)
        for b in (self.btn_add, self.btn_add_dir, self.btn_catalogue, self.btn_remove, self.btn_clear):
            if b is not None:
                bar.addWidget(b)
        ml.addLayout(bar)

        # left: queue over report/log; right: the preview, as tall as the window allows
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(8)
        left = QSplitter(Qt.Vertical)
        left.setChildrenCollapsible(False)
        left.setHandleWidth(8)
        kind = KINDS[self.tool.inputs.kind][1]
        exts = " ".join(self.tool.inputs.exts)
        cols = [(k, k.replace("_", " ").capitalize()) for k in self.tool.columns]
        drop = f"Drop {kind} files or folders here\n{exts}\n\nor use  Add files…"
        if self.btn_catalogue is not None:          # rich text, so the catalogue is a link
            drop = (f"Drop {kind} files or folders here<br>{exts}<br><br>or use&nbsp; Add files…<br><br>No model at "
                    f"hand? Try one from the <a href='catalogue' style='color:{self.theme['accent-text']}'>model "
                    "catalogue</a>")
        self.queue = FileQueue(self.theme, cols, drop)
        if self.btn_catalogue is not None:
            self.queue.drop.linkActivated.connect(lambda _href: self.pick_catalogue())
        self.queue.itemSelectionChanged.connect(self.show_selected)
        self.queue.itemSelectionChanged.connect(self._sync_picker)
        self.queue.itemDoubleClicked.connect(lambda it, _c: self._open_row(it.data(0, Qt.UserRole)))
        self.queue.customContextMenuRequested.connect(self._context_menu)
        left.addWidget(self.queue)
        self.bottom = QTabWidget()
        self.bottom.setDocumentMode(True)
        self.report = QPlainTextEdit(readOnly=True)
        self.report.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.report.setPlaceholderText("Select a file to see its report.")
        self.logview = QPlainTextEdit(readOnly=True)
        self.logview.setMaximumBlockCount(5000)
        self.logview.setPlaceholderText("Messages from this tool's runs appear here.")
        report_tab = QWidget()
        rl = QVBoxLayout(report_tab)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        # "this input needs fixing first": what to do, and a button that opens the tool that does it
        self.fix_bar = QFrame(objectName="warnBanner")
        fb = QHBoxLayout(self.fix_bar)
        fb.setContentsMargins(12, 8, 8, 8)
        self.fix_text = QLabel("", wordWrap=True)
        self.fix_button = QPushButton("Open in Repair Mesh")
        self.fix_button.clicked.connect(self._open_fix_tool)
        fb.addWidget(self.fix_text, 1)
        fb.addWidget(self.fix_button, 0, Qt.AlignVCenter)
        self.fix_bar.hide()
        rl.addWidget(self.fix_bar)
        rl.addWidget(self.report, 1)
        self.bottom.addTab(report_tab, "Report")
        self.bottom.addTab(self.logview, "Log")
        left.addWidget(self.bottom)
        left.setSizes([330, 300])
        self.left = left
        split.addWidget(left)
        self.split = split

        stage = QWidget()
        sl = QVBoxLayout(stage)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(8)
        self.info_line = QLabel("")
        self.info_line.setFont(mono(11.5))
        self.info_line.setProperty("class", "mono")
        self.info_line.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sl.addWidget(self.info_line)
        self.stage = PreviewStage(self.theme, self.ctx.settings)
        self.stage.set_art(self.ctx.art)
        sl.addWidget(self.stage, 1)
        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        self.tiles = [StatTile("—") for _ in range(5)]
        for t in self.tiles:
            tiles.addWidget(t, 1)                # even widths; a tile with a 'was …' gets twice (_show_tiles)
            t.hide()
        sl.addLayout(tiles)
        self.tile_row = tiles
        split.addWidget(stage)
        split.setSizes([440, 740])
        split.setStretchFactor(1, 1)
        ml.addWidget(split, 1)
        self.outer.addWidget(main)
        self.outer.addWidget(self._build_inspector())
        self.viewer = None                       # the tool's interactive view (spec `viewer`), first preview tab
        if self.tool.viewer:                     # after the inspector: it reads the settings
            try:
                self.viewer = self.tool.resolve(self.tool.viewer)(self)
            except Exception as e:               # a broken viewer must not take the page down
                self.ctx.log("error", f"viewer failed: {e}", self.tool.id)
        self.outer.setStretchFactor(0, 1)
        self.outer.setStretchFactor(1, 0)
        self._fold_button()

    def _build_banner(self) -> QWidget:
        """Shown while a workflow step borrows this page (Aim in tool): Back copies the settings into the step."""
        self.banner = QFrame(objectName="borrowBanner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 8, 10, 8)
        bl.setSpacing(8)
        self.banner_text = QLabel("")
        self.banner_text.setWordWrap(True)
        bl.addWidget(self.banner_text, 1)
        back = QPushButton("Back to workflow")
        back.setProperty("variant", "primary")
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(lambda: self.end_borrow(keep=True))
        discard = QPushButton("Discard")
        discard.setToolTip("Go back to the workflow without changing the step.")
        discard.clicked.connect(lambda: self.end_borrow(keep=False))
        bl.addWidget(back)
        bl.addWidget(discard)
        self.banner.hide()
        return self.banner

    def _build_notices(self) -> QWidget:
        """One amber notice per external program the tool requires (core/runtimes) that cannot be found (Locate,
        Get, Setup guide; Run and Preview wait for it), and a quiet note per optional program that is missing."""
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        self.notices = []
        for key in dict.fromkeys((*self.tool.requires, *(k for k, _ in self.tool.requires_if))):
            n = RuntimeNotice(runtimes.get(key), self.ctx.settings, guide=lambda: self.ctx.show_setup("components"))
            n.changed.connect(self._located)
            bl.addWidget(n)
            self.notices.append(n)
        self.opt_notes = []
        for key, what in self.tool.optional:
            rt = runtimes.get(key)
            note = QFrame(objectName="optNote")
            nl = QHBoxLayout(note)
            nl.setContentsMargins(12, 6, 8, 6)
            text = QLabel(f"Optional: {rt.label} was not found, so “{what}” is not available. The rest of the tool "
                          "works without it.")
            text.setWordWrap(True)
            text.setProperty("class", "hint")
            nl.addWidget(text, 1)
            guide = QPushButton("Setup guide")
            guide.setProperty("variant", "ghost")
            guide.clicked.connect(lambda: self.ctx.show_setup("components"))
            nl.addWidget(guide)
            note.runtime = rt
            bl.addWidget(note)
            self.opt_notes.append(note)
        self.lacking = []
        self._check_runtimes(update=False)
        return box

    def _located(self) -> None:
        """A program was located from this page: every page and list hears of it."""
        self._check_runtimes()
        self.ctx.refresh_runtimes()

    def _check_runtimes(self, update: bool = True) -> None:
        self.lacking = runtimes.missing(self.tool, self.values() if hasattr(self, 'form') else self.tool.defaults())
        for n in self.notices:
            n.setVisible(n.runtime in self.lacking)
        for note in self.opt_notes:
            note.setVisible(runtimes.find(note.runtime.key) is None)
        if update:
            self._update_controls()

    def _build_head(self):
        """One line, to leave the preview its height: name, version, a hairline, then the summary, which ends in an
        ellipsis when the page is narrow (its tooltip has it in full, with the description). The category and the
        input → output kind are in the name's tooltip; the sidebar and Home already show them."""
        tl = QHBoxLayout()
        tl.setSpacing(0)
        title = QLabel(self.tool.name)
        title.setProperty("class", "tooltitle")
        title.setFont(sans(21, 300))
        title.setToolTip(f"{self.tool.place}  ·  {self.tool.io_badge}")
        tl.addWidget(title, 0, Qt.AlignVCenter)
        self.slow_mark = None
        if self.tool.slow:
            self.slow_mark = Mark("warn", "warning", tip_html("Can be slow", self.tool.slow, SLOW_ADVICE), self.theme)
            tl.addSpacing(8)
            tl.addWidget(self.slow_mark, 0, Qt.AlignVCenter)
        tl.addSpacing(8)
        tl.addWidget(badge(f"v{self.tool.version}"), 0, Qt.AlignVCenter)
        tl.addSpacing(14)
        rule = QFrame(objectName="headRule")
        rule.setFixedSize(1, 18)
        tl.addWidget(rule, 0, Qt.AlignVCenter)
        tl.addSpacing(14)
        self.summary = ElidedLabel(self.tool.summary)
        self.summary.setProperty("class", "summary")
        tip = self.tool.summary + (f"\n\n{self.tool.description}" if self.tool.description else "")
        self.summary.setToolTip(tip)
        tl.addWidget(self.summary, 1, Qt.AlignVCenter)
        return tl

    def _build_inspector(self) -> QWidget:
        side = QWidget()
        side.setObjectName("inspector")
        side.setMinimumWidth(INSPECTOR_MIN)
        side.setMaximumWidth(INSPECTOR_MAX)
        self.inspector = side
        outer = QVBoxLayout(side)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)   # never hide a control: scroll in the worst case
        body = QWidget()
        body.setObjectName("transparent")
        sl = QVBoxLayout(body)
        sl.setContentsMargins(20, 18, 20, 12)
        sl.setSpacing(8)

        self.form = ParamForm(self.tool, self.theme)
        self.form.changed.connect(self._settings_changed)
        for k in self.input_keys:                    # say so where the value is set
            w = self.form.rows[k][0]
            tip = w.toolTip()
            w.setToolTip((tip + "\n\n" if tip else "") + "Each file in the list keeps its own value: set it with that "
                         "file selected.")
        self.presets = PresetBar(self.tool, self.ctx.presets, self.form, self.theme, self.ctx.toast,
                                 manage=self.ctx.manage_presets)
        self.presets.applied.connect(self._settings_changed)
        sl.addWidget(self.presets)
        sl.addSpacing(4)
        sl.addWidget(self.form)
        if self.tool.panel:
            try:
                self.extra = self.tool.resolve(self.tool.panel)(self)
                if not (self.tool.panel_after and self.form.insert_after(self.tool.panel_after, self.extra)):
                    sl.addWidget(self.extra)
            except Exception as e:   # a broken custom panel must not take the page down
                sl.addWidget(hint(f"Custom panel failed: {e}"))

        # output
        sl.addSpacing(8)
        sl.addWidget(label("output"))
        self.out_next = QRadioButton("Next to each file")
        self.out_folder = QRadioButton("In a folder")
        grp = QButtonGroup(self)
        grp.addButton(self.out_next)
        grp.addButton(self.out_folder)
        sl.addWidget(self.out_next)
        row = QHBoxLayout()
        row.addWidget(self.out_folder)
        row.addStretch(1)
        self.btn_choose = QPushButton("Choose…")
        self.btn_choose.clicked.connect(self.pick_out_folder)
        row.addWidget(self.btn_choose)
        sl.addLayout(row)
        self.folder_label = hint("")
        self.folder_label.setTextFormat(Qt.PlainText)
        sl.addWidget(self.folder_label)
        self.out_next.toggled.connect(self._output_changed)
        self.fmt_combo = None
        if self.tool.output.formats:
            fr = QHBoxLayout()
            fl = QLabel("Format")
            fl.setProperty("class", "muted")
            fr.addWidget(fl)
            self.fmt_combo = QComboBox()
            for ext in self.tool.output.formats:
                self.fmt_combo.addItem(ext.lstrip(".").upper(), ext)
            self.fmt_combo.currentIndexChanged.connect(self._output_changed)
            fr.addWidget(self.fmt_combo, 1)
            sl.addLayout(fr)
        stem_example = re.sub(r"\{(\w+)\}", lambda m: f"<i>{'name' if m.group(1) == 'stem' else m.group(1)}</i>",
                              self.tool.output.template)
        sig = "signed so Atlas links them to this tool" if self.tool.signature else ""
        self.out_hint = hint(f"Saved as {stem_example}{', ' + sig if sig else ''}. Originals are never changed.")
        sl.addWidget(self.out_hint)
        self.format_note = hint(self.tool.output.format_notes.get(self.store.out_format or self.tool.output.ext, ''))
        self.format_note.setVisible(bool(self.tool.output.format_notes))
        sl.addWidget(self.format_note)
        sl.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # actions, always visible
        act = QFrame()
        act.setObjectName("transparent")
        al = QVBoxLayout(act)
        al.setContentsMargins(20, 10, 20, 18)
        al.setSpacing(8)
        self.btn_run = QPushButton(self.tool.action)
        self.btn_run.setProperty("variant", "primary")
        self.btn_run.setCursor(Qt.PointingHandCursor)
        self.btn_run.setIconSize(QSize(14, 14))
        self.btn_run.clicked.connect(self.run_all)
        al.addWidget(self.btn_run)
        r2 = QHBoxLayout()
        r2.setSpacing(6)
        self.btn_preview = QPushButton("Preview")
        self.btn_preview.setToolTip("Run the selected file as a quick draft, without saving anything.")
        self.btn_preview.clicked.connect(self.preview_selected)
        self.btn_preview.setVisible(not self.tool.live)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setProperty("variant", "danger")
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_open = QPushButton("Open folder")
        self.btn_open.clicked.connect(self.open_output_folder)
        self.preview_note = Mark("info", "text-3", draft_tip(self.tool), self.theme)
        self.preview_note.setVisible(not self.tool.live)
        r2.addWidget(self.btn_preview, 1)
        r2.addWidget(self.preview_note)
        r2.addWidget(self.btn_cancel, 1)
        r2.addWidget(self.btn_open, 1)
        al.addLayout(r2)
        outer.addWidget(act)
        return side

    # ================================================================== settings
    # ================================================================== inspector width
    def _inspector_moved(self, *_):
        w = self.outer.sizes()[1]
        if w > 0:
            self.ctx.settings.set("inspector_w", int(w))

    def showEvent(self, e):
        super().showEvent(e)
        want = int(self.ctx.settings.get("inspector_w", INSPECTOR_W))
        want = max(INSPECTOR_MIN, min(INSPECTOR_MAX, want))
        sizes = self.outer.sizes()
        if sizes[1] != want and sum(sizes) > want:
            self.outer.setSizes([sum(sizes) - want, want])
        folded = bool(self.ctx.settings.get("inputs_folded", False))
        if folded != self.inputs_folded:            # folded or unfolded on another tool's page
            self.set_inputs_folded(folded, save=False)

    # ================================================================== folding the file list
    def set_inputs_folded(self, folded: bool, save: bool = True) -> None:
        """Hide the file list and Report / Log (folded to the left) so the preview takes their room, or show them
        again at the width they had. While folded, the picker beside INPUTS selects the input."""
        if folded == self.inputs_folded:
            return
        if folded:
            self._left_w = self.split.sizes()[0]
        self.inputs_folded = folded
        self.left.setVisible(not folded)
        if not folded and getattr(self, "_left_w", 0) > 0:
            total = sum(self.split.sizes())
            if total > self._left_w:
                self.split.setSizes([self._left_w, total - self._left_w])
        if save:
            self.ctx.settings.set("inputs_folded", folded)
        self._fold_button()
        self._sync_picker()

    def _fold_button(self) -> None:
        apply_icon(self.btn_fold, self.theme, "right" if self.inputs_folded else "left")
        self.btn_fold.setToolTip("Show the file list and Report / Log" if self.inputs_folded
                                 else "Fold the file list and Report / Log away, for a wider preview")
        for b in (self.btn_prev, self.btn_next):
            apply_icon(b, self.theme)

    def _sync_picker(self) -> None:
        """Fill the compact picker from the queue (same order, same selection); shown only while folded."""
        keys = self.queue.keys()
        self.picker_box.setVisible(self.inputs_folded and bool(keys))
        if not self.inputs_folded:
            return
        cur = self._current_row()
        if [self.picker.itemData(i) for i in range(self.picker.count())] != keys:
            self.picker.blockSignals(True)
            self.picker.clear()
            for k in keys:
                self.picker.addItem(Path(k).name, k)
                self.picker.setItemData(self.picker.count() - 1, k, Qt.ToolTipRole)
            self.picker.blockSignals(False)
        self.picker.blockSignals(True)
        self.picker.setCurrentIndex(keys.index(cur.key) if cur is not None and cur.key in keys else -1)
        self.picker.blockSignals(False)
        i = self.picker.currentIndex()
        self.btn_prev.setEnabled(len(keys) > 1 and i != 0)
        self.btn_next.setEnabled(len(keys) > 1 and i != len(keys) - 1)
        it = cur.item if cur is not None else None
        text = it.text(1) if it is not None else ""
        self.picker_status.setText(text)
        if it is not None:
            self.picker_status.setStyleSheet(f"color: {it.foreground(1).color().name()};")
        tip = it.toolTip(1) if it is not None else ""
        if cur is not None and (cur.error or cur.info_error):
            tip = (tip + "\n\n" if tip else "") + "Show the file list (the button left of INPUTS) to read the report."
        self.picker_status.setToolTip(tip)

    def _picked(self, i: int) -> None:
        key = self.picker.itemData(i)
        if key in self.rows:
            self.queue.setCurrentItem(self.rows[key].item)

    def _step_input(self, step: int) -> None:
        n = self.picker.count()
        if n:
            i = self.picker.currentIndex()
            self._picked(max(0, min(n - 1, (i + step) if i >= 0 else 0)))

    def _restore(self) -> None:
        self.form.set_values(self.store.values())
        mode, self.out_dir = self.store.out_mode, self.store.out_dir
        if "out_mode" not in self.store.data:    # a tool not run yet: Setup › Outputs › "Where outputs go"
            mode, self.out_dir = self.ctx.pref("default_out_mode"), self.ctx.pref("default_out_dir")
        (self.out_folder if mode == "folder" and self.out_dir else self.out_next).setChecked(True)
        self.folder_label.setText(self.out_dir)
        if self.fmt_combo is not None:
            i = self.fmt_combo.findData(self.store.out_format or self.tool.output.ext)
            self.fmt_combo.setCurrentIndex(max(i, 0))
        self._sync_preset()

    def values(self) -> dict:
        """The form's values: the shared settings and the selected input's own (per-input) ones."""
        return self.form.values()

    def values_for(self, key: str) -> dict:
        """The values `key` runs with: the shared settings and that input's own per-input values."""
        self._capture_view()
        v = self.form.values()
        row = self.rows.get(key)
        if row is not None and row.key != self._view_key:
            v.update(row.view)
        return v

    def _sig(self, values: dict) -> str:
        """A result's signature: the values it was made with (equal signatures draw the same)."""
        return repr(sorted(_jsonable(values).items()))

    def _row_sig(self, row: Row) -> str:
        return self._sig(self.values_for(row.key))

    # ------------------------------------------------------------------ each input's own values
    def _own(self, values: dict) -> dict:
        return {k: values[k] for k in self.input_keys if k in values}

    def _shared(self, values: dict) -> dict:
        return {k: v for k, v in values.items() if k not in self.input_keys}

    def _capture_view(self) -> None:
        """The form's per-input values belong to the input it shows (`_view_key`): keep them with that row."""
        row = self.rows.get(self._view_key) if self._view_key else None
        if row is None or not self.input_keys:
            return
        own = self._own(self.form.values())
        if own != row.view:
            row.view = own
            row.view_set = True

    def _load_view(self, row: Row | None) -> None:
        """Show `row`'s own per-input values in the form (quietly: nothing changed, another file is shown)."""
        if self._view_key is not None and (row is None or row.key != self._view_key):
            flush = getattr(self.viewer, "flush", None)
            if flush is not None:                # a camera still settling lands on the file it was aimed at
                flush()
            self._capture_view()
        self._view_key = row.key if row is not None else None
        if row is not None and self.input_keys and row.view and row.view != self._own(self.form.values()):
            self.form.set_values(row.view)
            self._check_runtimes(update=False)
            self._sync_preset()

    def _initial_view(self, key: str) -> tuple[dict, bool]:
        """A new input's own values: what it had when last aimed here, else the selected file's (or the saved ones)."""
        if not self.input_keys:
            return {}, False
        kept = (self.store.get("input_views") or {}).get(key)
        if kept:
            try:
                return self._own(self.tool.values({**self.form.values(), **kept})), True
            except ValueError:
                pass
        return self._own(self.form.values()), False

    def set_values(self, values: dict, keys: list | None = None) -> None:
        """Put `values` in the settings: the shared ones for every input, the per-input ones for the inputs `keys`
        (the selected one when None)."""
        self._capture_view()
        own = self._own(values)
        targets = [self._view_key] if keys is None else list(keys)
        for k in targets:
            row = self.rows.get(k) if k else None
            if row is not None and own:
                row.view = {**row.view, **own}
                row.view_set = True
        shown = self._view_key is None or self._view_key in targets
        self.form.set_values(values if shown else self._shared(values))
        self._settings_changed()

    def apply_view_to(self, keys: list, source: str | None = None) -> None:
        """Give the inputs `keys` the own values (its Aim, frame, plane or painted area) of the input `source` (the
        selected one by default)."""
        self._capture_view()
        src = self.rows.get(source or self._view_key or "")
        if src is None:
            return
        n = 0
        for k in keys:
            row = self.rows.get(k)
            if row is not None and row is not src:
                row.view, row.view_set = dict(src.view), True
                n += 1
                if row.key == self._view_key:
                    self.form.set_values(row.view)
        self._apply_settings()
        if n:
            self.ctx.status(f"{self.tool.name}: {n} file{'s' if n != 1 else ''} now use the {self._view_words()} of "
                            f"{Path(src.key).name}.")

    def _view_words(self) -> str:
        """What the per-input values are called in the file list's menu."""
        if self.tool.per_input_label:
            return self.tool.per_input_label
        keys = self.input_keys
        if "theta" in keys:
            return "Aim"
        if "position" in keys and "rotation" in keys:
            return "plane"
        if "selection" in keys:
            return "painted area"
        return "Up and Front"

    def _settings_changed(self) -> None:
        if self.tool.needs_if:
            self._show_fix(self._current_row())
        self._capture_view()
        self._drop_stale_drafts()
        self._check_runtimes()
        self._sync_preset()
        self.note = ""
        self.debounce.start()

    def _apply_settings(self) -> None:
        self.debounce.stop()
        self._capture_view()
        self._drop_stale_drafts()
        self._check_runtimes(update=False)
        self._save_settings()
        if self.viewer is not None:
            self.viewer.values_changed(self.values())
        for row in self.rows.values():
            if self.tool.live:
                if row.sig != self._row_sig(row):
                    self.analyse(row.key)        # only the inputs whose values changed
            else:
                self._refresh_result(row)
        if not self.tool.live:
            self.show_selected()
        self._update_controls()

    def _refresh_result(self, row: Row) -> None:
        """A preview shows only while its file has the values it was made with; a kept preview made with the values
        now in force comes back. A run's result stays (it is on disk)."""
        if row.result is not None and row.result_mode == "run":
            return
        sig = self._row_sig(row)
        if row.result is not None and row.sig == sig:
            return
        kept = row.drafts.get(sig)
        if kept is not None:
            row.drafts.move_to_end(sig)
            row.result, row.result_mode, row.sig, row.error = kept, "draft", sig, ""
        elif row.result is None:
            self._fill_row(row)              # "Outdated" or "Ready"
            return
        else:
            row.result, row.result_mode = None, ""
        self._fill_row(row)

    def _drop_stale_drafts(self) -> None:
        """Cancel the previews made with values their file no longer has (other files' previews run on)."""
        for row in self.rows.values():
            if row.draft_job is not None and row.draft_sig != self._row_sig(row):
                self.cancel_preview(row)

    def _remember_views(self) -> None:
        """Files given their own view here keep it for next time (the most recent VIEWS_KEPT)."""
        if not self.input_keys:
            return
        self._capture_view()
        views = dict(self.store.get("input_views") or {})
        params = {p.key: p for p in self.tool.param_list if p.key in self.input_keys}
        for row in self.rows.values():
            if row.view_set and row.view:
                views.pop(row.key, None)
                views[row.key] = {k: (v if v == AUTO else params[k].to_json(v)) for k, v in row.view.items()}
        self.store.set("input_views", dict(list(views.items())[-VIEWS_KEPT:]))

    def _save_settings(self) -> None:
        if self.borrowed is not None:            # a workflow step's settings are not this page's own
            return
        self.store.set_values(self.values())
        self._remember_views()
        self.store.out_mode = "folder" if self.out_folder.isChecked() else "next"
        self.store.out_dir = self.out_dir
        if self.fmt_combo is not None:
            self.store.out_format = self.fmt_combo.currentData()
        try:
            self.store.save()
        except OSError as e:
            self.ctx.log("warning", f"could not save settings: {e}", self.tool.id)

    def _output_changed(self, *_):
        if hasattr(self, 'format_note'):
            ext = self.fmt_combo.currentData() if self.fmt_combo is not None else self.tool.output.ext
            self.format_note.setText(self.tool.output.format_notes.get(ext, ''))
        self._save_settings()
        self.show_selected()
        self._update_controls()

    def apply_preset(self, ref: str) -> None:
        """Pick a preset by uid or name, as the drop-down does."""
        self.presets.apply(ref)

    def _sync_preset(self) -> None:
        self.presets.sync()

    # ================================================================== borrowed by a workflow step
    def borrow(self, path: str, values: dict, title: str, on_return) -> bool:
        """Open a workflow step's input with the step's settings, to aim or tune them here. Back to workflow calls
        on_return(values), Discard on_return(None); either way the page gets its own settings and files back. Run is
        off meanwhile."""
        if self.busy():
            self.ctx.toast("Wait for the current run to finish (or cancel it) first.", "warning")
            return False
        if self.borrowed is not None:
            self.end_borrow(keep=False, notify=False)
        key = str(Path(path).resolve())
        added = key not in self.rows
        self._capture_view()
        self.borrowed = {"key": key, "added": added, "prev": self.values(), "title": title,
                         "on_return": on_return,
                         "prev_view": None if added else (dict(self.rows[key].view), self.rows[key].view_set)}
        if added:
            self._add_row(key)
        self.queue.setCurrentItem(self.rows[key].item)     # the step's input is shown: its values go to it alone
        self._load_view(self.rows[key])
        self.form.set_values(self.tool.values(values))
        self._settings_changed()
        self.banner_text.setText(f"Setting up the workflow step <b>{title}</b> on its input. Change the view and "
                                 f"settings here, then go back: the step takes them. Run is off meanwhile.")
        self.banner.show()
        self._update_controls()
        return True

    def end_borrow(self, keep: bool = True, notify: bool = True) -> None:
        b, self.borrowed = self.borrowed, None
        if b is None:
            return
        values = self.values_for(b["key"]) if b["key"] in self.rows else self.values()
        self.banner.hide()
        if b["added"] and b["key"] in self.rows:
            self._forget(b["key"])
            self._list_changed()
        elif b["key"] in self.rows:                  # the file was here before: its own values come back
            row = self.rows[b["key"]]
            row.view, row.view_set = b["prev_view"]
            if self._view_key == row.key:
                self._view_key = None                # (the form holds the step's: do not keep them)
        self.form.set_values(b["prev"])
        self._load_view(self._current_row())
        self._settings_changed()
        if notify:
            b["on_return"](values if keep else None)

    # ================================================================== inputs
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and not self.busy():
            e.acceptProposedAction()
            self.queue.set_drop_active(True)

    def dragLeaveEvent(self, e):
        self.queue.set_drop_active(False)

    def dropEvent(self, e):
        self.queue.set_drop_active(False)
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def pick_files(self) -> None:
        exts = " ".join(f"*{x}" for x in self.tool.inputs.exts)
        kind = KINDS[self.tool.inputs.kind][1]
        files, _ = QFileDialog.getOpenFileNames(self, f"Add {kind} files", self.store.get("last_dir", ""),
                                                f"{kind} files ({exts});;All files (*.*)")
        self.add_paths(files)

    def pick_catalogue(self) -> None:
        """Ready-made models from the model catalogue (ui/catalogue.py), added like any file."""
        from .catalogue import pick_models
        paths = pick_models(self, multiple=self.tool.inputs.multiple, ctx=self.ctx,
                            title=f"Model catalogue · {self.tool.name}")
        if paths:
            self.add_paths(paths)

    def pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add every file in a folder", self.store.get("last_dir", ""))
        if folder:
            self.add_paths([folder])

    def pick_out_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Save outputs in", self.out_dir or self.store.get("last_dir", ""))
        if folder:
            self.out_dir = folder
            self.folder_label.setText(folder)
            self.out_folder.setChecked(True)
            self._output_changed()

    def add_paths(self, paths: list, values: dict | None = None) -> int:
        if self.busy():
            self.ctx.toast("Wait for the current run to finish (or cancel it) before adding files.", "warning")
            return 0
        found = expand_inputs(self.tool, paths)
        if not found and paths:
            kinds = " ".join(self.tool.inputs.exts)
            self.ctx.toast(f"{self.tool.name} takes {kinds} files.", "warning")
            return 0
        added = []
        for f in found:
            key = str(f)
            if key in self.rows:
                continue
            self._add_row(key)
            added.append(key)
            self.ctx.settings.add_recent(key)
        if found:
            self.store.set("last_dir", str(found[0].parent))
        if values:                                   # the files given with these values take their per-input ones
            self.set_values(self.tool.values(values), keys=[str(f) for f in found])
        elif added:
            self._offer_restore(added)
        if added and not self.queue.selectedItems():
            self.queue.setCurrentItem(self.rows[added[0]].item)
        self.note = ""
        self._update_controls()
        return len(added)

    def _add_row(self, key: str) -> Row:
        it = self.queue.add_row(key, Path(key).name)
        row = self.rows[key] = Row(key, it)
        row.view, row.view_set = self._initial_view(key)
        self.queue.set_status(it, "Reading…", "dim")
        self.inspect(key)
        return self.rows[key]

    def _offer_restore(self, keys: list) -> None:
        """An output of this tool was added: offer to restore the settings it was made with."""
        if self.tool.output.kind != "svg":
            return
        from ..geom.svg import read_run_metadata
        for key in keys:
            md = read_run_metadata(Path(key))
            if md and md[0] == self.tool.id:
                vals = md[1]
                self.ctx.toast(f"{Path(key).name} was made with {self.tool.name}. Restore the settings it used?",
                               "", "Restore", lambda v=vals, k=key: self._restore_from(k, v), ms=8000)
                return

    def _restore_from(self, key: str, values: dict) -> None:
        """Restore the settings an output was made with: its view goes to that file alone."""
        if key in self.rows:
            self.set_values(self.tool.values(values), keys=[key])

    def remove_selected(self) -> None:
        for key in self.queue.selected_keys():
            self._forget(key)
        self._list_changed()

    def clear(self) -> None:
        for key in list(self.rows):
            self._forget(key)
        self._list_changed()

    def _forget(self, key: str) -> None:
        row = self.rows.pop(key)
        for jid in list(row.jobs):
            self.ctx.bridge.cancel(jid)
        self.queue.takeTopLevelItem(self.queue.indexOfTopLevelItem(row.item))

    def _list_changed(self) -> None:
        self.note = ""
        self.show_selected()
        self._update_controls()

    # ================================================================== jobs
    def _lane(self, mode: str) -> str:
        return "process" if (self.tool.runs_in == "process" and mode in ("run", "draft")) else "thread"

    def _submit(self, row: Row, mode: str, values: dict | None = None, dst: str | None = None) -> int:
        values = dict(values or {})
        job = Job(self.tool.id, mode, row.key, dst, values,
                  group=f"{self.tool.id}:{mode}:{row.key}" if mode in ("analyse", "inspect") else "",
                  cache_dir=str(self.cache))
        sig, holder = self._sig(values), {}
        jid = self.ctx.bridge.submit(
            job, self._lane(mode),
            lambda kind, payload, r=row, m=mode, s=sig: self._on_event(r, m, s, holder["jid"], kind, payload))
        holder["jid"] = jid
        row.jobs.add(jid)
        return jid

    def inspect(self, key: str) -> None:
        self._submit(self.rows[key], "inspect")

    def analyse(self, key: str) -> None:
        row = self.rows[key]
        if row.info is None and row.info_error:
            return
        self.queue.set_status(row.item, "Analysing…", "dim")
        self._submit(row, "analyse", self.values_for(key))

    @property
    def draft_job(self) -> int | None:
        """The selected input's running preview (each input has its own)."""
        row = self._current_row()
        return row.draft_job if row is not None else None

    def previewing(self) -> bool:
        return any(r.draft_job is not None for r in self.rows.values())

    def cancel_preview(self, row: Row | None = None) -> None:
        """Stop an input's preview (the selected one's by default); other inputs' previews run on."""
        row = row if row is not None else self._current_row()
        if row is None or row.draft_job is None:
            return
        jid, row.draft_job = row.draft_job, None
        self.ctx.bridge.cancel(jid)
        self._fill_row(row)

    def preview_selected(self, *, keep_view: bool = False) -> None:
        if self.debounce.isActive():
            self._apply_settings()
        row = self._current_row()
        if row is None or row.info is None:
            return
        if self._placement_error(row.key,mode='draft'):
            self.ctx.status(self._placement_error(row.key,mode='draft'))
            return
        self.cancel_preview(row)
        values = self.values_for(row.key)
        row.draft_prefer_result = not keep_view
        self.queue.set_status(row.item, "Previewing…", "busy")
        row.draft_sig = self._sig(values)
        row.draft_job = self._submit(row, "draft", values)
        self._update_controls()

    def run_all(self) -> None:
        keys = self.runnable()
        if not keys or self.busy():
            return
        conflict=next((self._placement_error(key) for key in keys if self._placement_error(key)), '')
        if conflict:
            self.ctx.status(conflict)
            return
        if self.out_folder.isChecked() and not self.out_dir:
            self.pick_out_folder()
            if not self.out_dir:
                return
        values = {k: self.values_for(k) for k in keys}  # each input with its own view
        policy = self.ctx.pref("on_existing")         # Setup › Outputs › "When an output file exists"
        plan, exists = prefs.plan({k: self.output_for(k, values[k]) for k in keys}, policy)
        if exists and policy == "ask":
            names = "\n".join(p.name for p in exists[:8]) + ("\n…" if len(exists) > 8 else "")
            if QMessageBox.question(self, self.tool.name, f"{len(exists)} output file(s) already exist:\n\n{names}"
                                                          "\n\nOverwrite them?") != QMessageBox.Yes:
                return
        self._save_settings()
        self.batch = {"total": len(keys), "done": 0, "saved": 0, "failed": 0, "t0": time.monotonic(),
                      "outputs": []}
        for k in keys:
            row = self.rows[k]
            self.queue.set_status(row.item, "Queued", "dim")
            row.run_values = values[k]
            self._submit(row, "run", values[k], str(plan[k]))
        self._update_controls()

    def cancel(self) -> None:
        for row in self.rows.values():
            for jid in list(row.jobs):
                self.ctx.bridge.cancel(jid)

    def _placement_error(self,path=None,mode='run'):
        validate=getattr(self.viewer,'validation_error',None)
        return validate(path,mode=mode) if validate else ''

    def runnable(self) -> list[str]:
        """Inputs the primary button would write: for live tools only those with something to change."""
        out = []
        for k, row in self.rows.items():
            if row.info is None:
                continue
            if self.tool.live:
                if isinstance(row.result, Result) and row.result.changed and row.sig == self._row_sig(row):
                    out.append(k)
            else:
                out.append(k)
        return out

    def output_for(self, key: str, values: dict | None = None) -> Path:
        values = values if values is not None else self.values_for(key)
        row = self.rows.get(key)
        if row is not None and row.info is not None and any(v == AUTO for v in values.values()):
            values = resolve_auto(self.tool.param_list, values, row.info)
        fmt = self.fmt_combo.currentData() if self.fmt_combo is not None else ""
        return output_path(self.tool, Path(key), values, "folder" if self.out_folder.isChecked() else "next",
                           self.out_dir, fmt)

    def busy(self) -> bool:
        return self.batch is not None

    def idle(self) -> bool:
        return not any(r.jobs for r in self.rows.values())

    # ================================================================== events
    def _on_event(self, row: Row, mode: str, sig: str, jid: int, kind: str, payload) -> None:
        if self.rows.get(row.key) is not row:
            return
        if mode == "draft" and jid != row.draft_job:      # replaced or cancelled: a finished one is still kept
            if kind in ("done", "error", "cancelled"):
                row.jobs.discard(jid)
                if kind == "done":
                    self._keep_draft(row, sig, payload[0])
                    self._refresh_result(row)
                    if self._current_row() is row:
                        self.show_selected()
                self._update_controls()
            return
        if kind == "progress" and mode in ("run", "draft"):
            frac, msg = payload
            self.queue.set_status(row.item, f"{'Running' if mode == 'run' else 'Previewing'} {frac * 100:.0f}%",
                                  "busy", msg)
            return
        if kind == "log":
            level, msg = payload
            self.logview.appendPlainText(f"{time.strftime('%H:%M:%S')}  {level:<7} {Path(row.key).name}: {msg}")
            self.ctx.log(level, msg, f"{self.tool.id} · {Path(row.key).name}")
            return
        if kind == "started" and mode == "run":
            self.queue.set_status(row.item, "Running…", "busy")
            return
        if kind not in ("done", "error", "cancelled"):
            return
        row.jobs.discard(jid)
        if mode == "inspect":
            self._on_inspected(row, kind, payload)
        elif mode == "analyse":
            if sig == self._row_sig(row):                 # an analysis of values the file no longer has is dropped
                self._on_result(row, mode, kind, payload)
                row.sig = sig
        elif mode == "draft":
            row.draft_job = None
            if kind == "done":
                self._keep_draft(row, sig, payload[0])
            if sig == self._row_sig(row):
                row.sig = sig
                self._on_result(row, mode, kind, payload, prefer_result=row.draft_prefer_result)
            else:                                          # the file's values moved on meanwhile
                self._refresh_result(row)
        else:
            self._on_run(row, kind, payload)
        self._update_controls()

    def _on_inspected(self, row: Row, kind: str, payload) -> None:
        if kind == "done":
            row.info = payload[0]
            check = (row.info.values.get("input_check") if row.info is not None else None) or {}
            self.queue.set_status(row.item, "Ready" if not self.tool.live else "Analysing…",
                                  "accent" if not self.tool.live else "dim")
            if check.get("state") == "repair" and self._check_applies(check):
                self.queue.set_status(row.item, "Needs repair", "warning", check.get("text", ""))
                self.ctx.log("warning", check.get("text", ""), f"{self.tool.id} · {Path(row.key).name}")
            if self.tool.live:
                self.analyse(row.key)
        elif kind == "error":
            row.info_error = payload[0]
            self.queue.set_status(row.item, "Unreadable", "danger", payload[0])
            self.ctx.log("error", payload[0], f"{self.tool.id} · {Path(row.key).name}")
        if self._current_row() is row:
            self.show_selected()

    def _on_result(self, row: Row, mode: str, kind: str, payload, *, prefer_result: bool = True) -> None:
        if kind == "done":
            row.result, row.result_mode, row.error = payload[0], mode, ""
            row.fix_tool = row.fix_hint = ""
            self._fill_row(row)
        elif kind == "error":
            row.error = payload[0]
            row.result = None
            self._note_fix(row, payload)
            self.queue.set_status(row.item, "Needs repair" if row.fix_tool else "Error", "danger", payload[0])
            self.queue.clear_cells(row.item)
            self.logview.appendPlainText(f"{time.strftime('%H:%M:%S')}  error   {Path(row.key).name}: {payload[0]}"
                                         + (f"\n{payload[1]}" if len(payload) > 1 and payload[1] else ""))
        elif kind == "cancelled" and mode == "draft":
            self._fill_row(row)
        if self._current_row() is row:
            self.show_selected(prefer_result=prefer_result)

    def _keep_draft(self, row: Row, sig: str, res) -> None:
        """Keep a finished preview with the values it was made with (the newest DRAFTS_KEPT per input)."""
        if not isinstance(res, Result):
            return
        row.drafts.pop(sig, None)
        row.drafts[sig] = res
        while len(row.drafts) > DRAFTS_KEPT:
            row.drafts.popitem(last=False)

    def _on_run(self, row: Row, kind: str, payload) -> None:
        b = self.batch
        rec = None
        if kind == "done":
            res: Result = payload[0]
            row.result, row.result_mode, row.error = res, "run", ""
            row.fix_tool = row.fix_hint = ""
            row.saved = list(res.outputs)
            if b is not None:
                b["saved"] += bool(res.outputs)
                b["outputs"] += list(res.outputs)
            secs = (res.data or {}).get("seconds", 0.0) if isinstance(res.data, dict) else 0.0
            rec = RunRecord(self.tool.id, self.tool.version, "done", row.key, list(res.outputs),
                            _jsonable(row.run_values or self.values_for(row.key)), secs, toolkit_version=__version__,
                            stats={s.key: s.value for s in res.stats if isinstance(s.value, (int, float, str))})
            self._fill_row(row)
        elif kind == "error":
            row.error = payload[0]
            if b is not None:
                b["failed"] += 1
            self._note_fix(row, payload)
            self.queue.set_status(row.item, "Needs repair" if row.fix_tool else "Error", "danger", payload[0])
            self.logview.appendPlainText(f"{time.strftime('%H:%M:%S')}  error   {Path(row.key).name}: {payload[0]}"
                                         + (f"\n{payload[1]}" if len(payload) > 1 and payload[1] else ""))
            diagnostic=payload[2] if len(payload)>2 and isinstance(payload[2],dict) else {}
            used = diagnostic.get("values", row.run_values or self.values_for(row.key))
            rec = RunRecord(self.tool.id, self.tool.version, "error", row.key, [], _jsonable(used),
                            seconds=diagnostic.get('seconds',0.),message=payload[0], toolkit_version=__version__)
        else:
            self.queue.set_status(row.item, "Cancelled", "warning")
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
        if self._current_row() is row:
            self.show_selected(prefer_result=True)

    def _finish_batch(self) -> None:
        b, self.batch = self.batch, None
        secs = time.monotonic() - b["t0"]
        skipped = len(self.rows) - b["total"]
        self.note = (f"Saved {b['saved']} of {b['total']} file(s) in {secs:.1f} s."
                     + (f"  {b['failed']} failed." if b["failed"] else "")
                     + (f"  {skipped} not written (nothing to change or unreadable)." if skipped and self.tool.live
                        else ""))
        tone = "danger" if b["failed"] and not b["saved"] else ("warning" if b["failed"] else "success")
        first = b["outputs"][0] if b["outputs"] else None
        self.ctx.toast(self.note, tone, "Show" if first else "", (lambda: reveal(Path(first))) if first else None)
        self.ctx.status(self.note)
        self.ctx.run_finished(b["outputs"])
        self._update_controls()

    def _fill_row(self, row: Row) -> None:
        res = row.result
        if res is None:
            self.queue.clear_cells(row.item)
            if row.draft_job is not None:
                self.queue.set_status(row.item, "Previewing…", "busy")
            elif row.info is not None and row.drafts and not self.tool.live:
                self.queue.set_status(row.item, "Outdated", "muted",
                                      "Its preview was made with other settings. Preview again, or go back to "
                                      "those settings to see it.")
            elif row.info is not None:
                self.queue.set_status(row.item, "Ready", "accent")
            return
        self.queue.set_cells(row.item, res)
        labels = {s.key: s.label for s in res.stats}
        self.queue.set_header_labels(labels)
        if row.result_mode == "run":
            if res.outputs:
                self.queue.set_status(row.item, "Saved", "success", "Saved as\n" + "\n".join(res.outputs)
                                      + "\nDouble-click to show it.")
            else:
                self.queue.set_status(row.item, res.status or "Nothing to write", "muted")
        elif row.result_mode == "draft":
            self.queue.set_status(row.item, "Previewed", "accent")
        else:
            self.queue.set_status(row.item, res.status or ("Ready" if res.changed else "Nothing to do"),
                                  "accent" if res.changed else "muted")

    # ================================================================== selection
    def _current_row(self) -> Row | None:
        it = self.queue.currentItem()
        return self.rows.get(it.data(0, Qt.UserRole)) if it is not None else None

    def show_selected(self, prefer_result: bool = False) -> None:
        row = self._current_row()
        if row is None or row.key != self._view_key:
            self._load_view(row)                     # the form shows this input's own view
            if row is not None and not self.tool.live:
                self._refresh_result(row)
        if self.viewer is not None and (row is None or row.info_error or row.info is None):
            self.viewer.set_input(None, None)
        if row is None:
            self.fix_bar.hide()
            self.info_line.setText("")
            self.stage.clear("Add files to start." if not self.rows else "Select a file.")
            self.report.clear()
            self._show_tiles(None)
            self.form.set_info(None)
            return
        name = Path(row.key).name
        self.form.set_info(row.info)
        if row.info_error:
            self.info_line.setText(f"{name}  ·  unreadable")
            self.stage.clear("This file could not be read.")
            self.report.setPlainText(f"{row.key}\n\nCould not read this file:\n{row.info_error}")
            self._show_tiles(None)
            return
        if row.info is None:
            self.info_line.setText(f"{name}  ·  reading…")
            self.stage.clear("Reading…")
            self.report.setPlainText(f"{row.key}\n\nReading…")
            return
        self.info_line.setText(f"{name}  ·  {row.info.summary}")
        views = list((row.info.preview or {}).get("views", []))
        prefer = None
        res = row.result
        if res is not None and res.preview:
            rviews = res.preview.get("views", [])
            titles = {v["title"] for v in rviews}
            views = [v for v in views if v["title"] not in titles] + rviews
            prefer = res.preview.get("prefer") if prefer_result else None
        if self.viewer is not None:
            self.viewer.set_input(row.key, row.info)
            views = [{"title": self.viewer.title, "kind": "widget", "widget": self.viewer}] + views
            if prefer is None and self.stage.current_title() not in [v["title"] for v in views]:
                prefer = self.viewer.title
        self.stage.show_views(views, prefer)
        self._show_tiles(res)
        lines = []
        self._show_fix(row)
        if row.error:
            lines.append(f"{row.key}\n\nError: {row.error}")
        elif res is not None:
            for note in getattr(res, "notes", None) or []:
                lines.append(f"Note: {note}")
            if lines:
                lines.append("")
            lines.append(res.report or f"{name}: done")
            if row.result_mode != "run":
                lines.append(f"\n  output     {self.output_for(row.key)}  (not written yet)")
        else:
            lines.append(f"{name}\n  {row.info.summary}\n  folder     {Path(row.key).parent}\n"
                         f"  output     {self.output_for(row.key)}")
        self.report.setPlainText("\n".join(lines))

    def _note_fix(self, row: Row, payload) -> None:
        """Remember what can fix this input (a ToolError names a tool), say it in the Log, offer it in a toast."""
        diag = payload[2] if len(payload) > 2 and isinstance(payload[2], dict) else {}
        row.fix_tool = diag.get("suggest_tool") or ""
        row.fix_hint = diag.get("hint") or ""
        if row.fix_tool and row.fix_tool in self.ctx.registry.tools and row.fix_tool != self.tool.id:
            name = self.ctx.registry.get(row.fix_tool).name
            self.ctx.log("warning", row.fix_hint or f"Run {name} on this file first.",
                         f"{self.tool.id} · {Path(row.key).name}")
            self.ctx.toast(f"{Path(row.key).name} needs repair before {self.tool.name} can use it.", "warning",
                           f"Open in {name}", lambda key=row.key, tid=row.fix_tool: self.send_to.emit(tid, [key]),
                           ms=8000)
        else:
            row.fix_tool = ""

    def _check_applies(self, check: dict) -> bool:
        """An input check made for some settings only (Tool.needs_if) counts while those settings are chosen."""
        return not check.get("when") or self.tool.needs_for(self.values()) is not None

    def _show_fix(self, row: Row | None) -> None:
        check = (row.info.values.get("input_check") if row is not None and row.info is not None else None) or {}
        if (row is not None and not row.error and check.get("state") == "repair" and self._check_applies(check)
                and REPAIR_TOOL in self.ctx.registry.tools and self.tool.id != REPAIR_TOOL):
            row.fix_tool, row.fix_hint = REPAIR_TOOL, check.get("text", "")
        elif row is not None and not row.error:
            row.fix_tool = row.fix_hint = ""
        if row is None or not row.fix_tool:
            self.fix_bar.hide()
            return
        name = self.ctx.registry.get(row.fix_tool).name
        self.fix_text.setText(f"<b>Repair this model first.</b> {row.fix_hint}")
        self.fix_button.setText(f"Open in {name}")
        self.fix_bar.show()

    def _open_fix_tool(self) -> None:
        row = self._current_row()
        if row is not None and row.fix_tool:
            self.send_to.emit(row.fix_tool, [row.key])

    def _show_tiles(self, res: Result | None) -> None:
        stats = [] if res is None else [s for s in res.stats if s.key not in self.tool.columns and not s.key.endswith("_pct")]
        if res is not None and not stats:
            stats = list(res.stats)
        for i, t in enumerate(self.tiles):
            if i < len(stats):
                s = stats[i]
                t.layout().itemAt(0).widget().setText(s.label.upper())
                t.set(s.text(), s.text(s.before) if s.before is not None else "", s.tone)
                self.tile_row.setStretch(i, 2 if s.before is not None else 1)
                t.show()
            else:
                t.hide()

    # ================================================================== row actions
    def _open_row(self, key: str) -> None:
        row = self.rows.get(key)
        if row and row.saved:
            reveal(Path(row.saved[0]))

    def _context_menu(self, pos) -> None:
        it = self.queue.itemAt(pos)
        if it is None:
            return
        key = it.data(0, Qt.UserRole)
        row = self.rows[key]
        th = self.theme["text-2"]
        m = QMenu(self)
        m.addAction(icon("folder", th, 16), "Show input in folder", lambda: reveal(Path(key)))
        out = m.addAction(icon("folder", th, 16), "Show output in folder",
                          lambda: reveal(Path(row.saved[0])) if row.saved else None)
        out.setEnabled(bool(row.saved))
        op = m.addAction(icon("external", th, 16), "Open output", lambda: open_file(Path(row.saved[0])))
        op.setEnabled(bool(row.saved))
        targets = [t for t in self.ctx.registry.ordered() if t.inputs.kind == self.tool.output.kind]
        if targets:
            sm = m.addMenu(icon("send", th, 16), "Send output to")
            sm.setEnabled(bool(row.saved))
            for t in targets:
                sm.addAction(icon(t.icon, th, 16), t.name, lambda tid=t.id: self.send_to.emit(tid, list(row.saved)))
        if self.input_keys and len(self.rows) > 1:
            m.addSeparator()
            # each file keeps its own view; these hand the one under the mouse to the others
            others = [k for k in self.queue.selected_keys() if k != key]
            what = self._view_words()
            labels = [p.label for p in self.tool.param_list if p.key in self.input_keys]
            tip = "Each file keeps its own " + ", ".join(labels) + ". This gives the others this file's."
            if others:
                m.addAction(icon("copy", th, 16), f"Use its {what} for the selected files",
                            lambda: self.apply_view_to(others, key)).setToolTip(tip)
            m.addAction(icon("copy", th, 16), f"Use its {what} for every file",
                        lambda: self.apply_view_to(list(self.rows), key)).setToolTip(tip)
            m.setToolTipsVisible(True)
        m.addSeparator()
        rm = m.addAction("Remove from list", self.remove_selected)
        rm.setEnabled(not self.busy())
        m.exec(self.queue.viewport().mapToGlobal(pos))

    def open_output_folder(self) -> None:
        if self.out_folder.isChecked() and self.out_dir:
            reveal(Path(self.out_dir))
            return
        row = self._current_row() or (next(iter(self.rows.values())) if self.rows else None)
        if row is not None:
            reveal(Path(row.saved[0]) if row.saved else Path(row.key).parent)

    # ================================================================== state
    def _update_controls(self) -> None:
        n = len(self.rows)
        busy = self.busy()
        self.files_label.setText(f"INPUTS  ·  {n}" if n else "INPUTS")
        self.queue.drop.setVisible(n == 0)
        self._sync_picker()
        ready = len(self.runnable())
        pending = any(r.jobs for r in self.rows.values())
        verb = self.tool.action
        if self.tool.live:
            self.btn_run.setText(f"{verb} all  ({ready})" if ready else f"{verb} all")
        else:
            self.btn_run.setText(f"{verb}  ·  {ready} file{'s' if ready != 1 else ''}" if ready else verb)
        analysing = self.tool.live and any(r.info is not None and r.sig != self._row_sig(r) for r in self.rows.values())
        placement_error=self._placement_error(self._current_row().key) if self._current_row() else ''
        preview_error=self._placement_error(self._current_row().key,mode='draft') if self._current_row() else ''
        self.btn_run.setEnabled(ready > 0 and not busy and not placement_error and not (self.tool.live and analysing)
                                and self.borrowed is None and not self.lacking)
        self.btn_run.setToolTip("Off while a workflow step is being set up here: use Back to workflow."
                                if self.borrowed is not None else placement_error)
        self.btn_cancel.setVisible(busy or self.previewing())
        self.btn_preview.setEnabled(not busy and self._current_row() is not None and self.draft_job is None
                                    and not self.lacking and not preview_error)
        self.btn_preview.setToolTip(preview_error)
        self.btn_remove.setEnabled(n > 0 and not busy)
        self.btn_clear.setEnabled(n > 0 and not busy)
        for w in (self.btn_add, self.btn_add_dir, self.btn_choose, self.out_next, self.out_folder):
            w.setEnabled(not busy)
        self.presets.set_enabled(not busy)
        if self.fmt_combo is not None:
            self.fmt_combo.setEnabled(not busy)
        self.form.set_enabled(not busy)
        self.btn_open.setEnabled(n > 0 or bool(self.out_dir))
        if busy:
            b = self.batch
            self.ctx.status(f"{self.tool.name}: {b['done']} of {b['total']} done…")
        elif pending and self.tool.live:
            self.ctx.status(f"{self.tool.name}: analysing…")

    def status_text(self) -> str:
        if self.note:
            return self.note
        if not self.rows:
            return f"{self.tool.name}: add files to start."
        ready = len(self.runnable())
        return f"{self.tool.name}: {len(self.rows)} file(s), {ready} ready."

    # ================================================================== appearance
    def retheme(self, theme) -> None:
        self.theme = theme
        self.queue.retheme(theme)
        self.stage.set_theme(theme)
        self.form.retheme(theme)
        self.presets.retheme(theme)
        for mark in (self.slow_mark, self.preview_note):
            if mark is not None:
                mark.retheme(theme)
        for row in self.rows.values():
            self._fill_row(row)
        self._fold_button()
        self._sync_picker()

    def set_art(self, art: str) -> None:
        self.stage.set_art(art)


def _jsonable(values: dict) -> dict:
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in values.items()}
