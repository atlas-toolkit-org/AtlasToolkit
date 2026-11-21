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
"""
from __future__ import annotations

import re
import time
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
from .widgets.common import (ElidedLabel, RuntimeNotice, StatTile, apply_icon, badge, hint, icon_button, label,
                             repolish)
from .widgets.file_queue import FileQueue
from .widgets.param_form import ParamForm
from .widgets.presets import PresetBar
from .widgets.preview import PreviewStage

INSPECTOR_W = 340          # default width; drag the divider to change it (remembered for every tool)
INSPECTOR_MIN, INSPECTOR_MAX = 300, 640


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
    gen: int = -1


class ToolPage(QWidget):
    send_to = Signal(str, list)     # tool id, paths

    def __init__(self, tool, ctx):
        super().__init__()
        self.tool = tool
        self.ctx = ctx
        self.theme = ctx.theme
        self.store = ctx.tool_settings(tool)
        self.rows: dict[str, Row] = {}
        self.gen = 0
        self.batch: dict | None = None      # the running Run batch
        self.borrowed: dict | None = None   # aiming a workflow step: {key, added, prev, title, on_return}
        self.draft_job: int | None = None
        self.draft_prefer_result = True
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
            tiles.addWidget(t)
            t.hide()
        sl.addLayout(tiles)
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
        title.setToolTip(f"{self.tool.category_title}  ·  {self.tool.io_badge}")
        tl.addWidget(title, 0, Qt.AlignVCenter)
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
        r2.addWidget(self.btn_preview, 1)
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
        return self.form.values()

    def _settings_changed(self) -> None:
        self.cancel_preview()
        self._check_runtimes()
        self._sync_preset()
        self.note = ""
        self.debounce.start()

    def _apply_settings(self) -> None:
        self.cancel_preview()
        self._check_runtimes(update=False)
        self._save_settings()
        self.gen += 1
        if self.viewer is not None:
            self.viewer.values_changed(self.values())
        if self.tool.live:
            for key in self.rows:
                self.analyse(key)
        else:
            for row in self.rows.values():
                if row.result is not None and row.result_mode != "run":
                    row.result = None
                    self._fill_row(row)
            self.show_selected()
        self._update_controls()

    def _save_settings(self) -> None:
        if self.borrowed is not None:            # a workflow step's settings are not this page's own
            return
        self.store.set_values(self.values())
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
        self.borrowed = {"key": key, "added": added, "prev": self.values(), "title": title, "on_return": on_return}
        if added:
            self._add_row(key)
        self.form.set_values(self.tool.values(values))
        self._settings_changed()
        self.queue.setCurrentItem(self.rows[key].item)
        self.banner_text.setText(f"Setting up the workflow step <b>{title}</b> on its input. Change the view and "
                                 f"settings here, then go back: the step takes them. Run is off meanwhile.")
        self.banner.show()
        self._update_controls()
        return True

    def end_borrow(self, keep: bool = True, notify: bool = True) -> None:
        b, self.borrowed = self.borrowed, None
        if b is None:
            return
        values = self.values()
        self.banner.hide()
        if b["added"] and b["key"] in self.rows:
            self._forget(b["key"])
            self._list_changed()
        self.form.set_values(b["prev"])
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
        if values:
            self.form.set_values(self.tool.values(values))
            self._settings_changed()
        elif added:
            self._offer_restore(added)
        if added and not self.queue.selectedItems():
            self.queue.setCurrentItem(self.rows[added[0]].item)
        self.note = ""
        self._update_controls()
        return len(added)

    def _add_row(self, key: str) -> Row:
        it = self.queue.add_row(key, Path(key).name)
        self.rows[key] = Row(key, it)
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
                               "", "Restore", lambda v=vals: (self.form.set_values(self.tool.values(v)),
                                                             self._settings_changed()), ms=8000)
                return

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
        job = Job(self.tool.id, mode, row.key, dst, dict(values or {}),
                  group=f"{self.tool.id}:{mode}:{row.key}" if mode in ("analyse", "inspect") else "",
                  cache_dir=str(self.cache))
        gen, holder = self.gen, {}
        jid = self.ctx.bridge.submit(
            job, self._lane(mode),
            lambda kind, payload, r=row, m=mode, g=gen: self._on_event(r, m, g, holder["jid"], kind, payload))
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
        self._submit(row, "analyse", self.values())

    def cancel_preview(self) -> None:
        """Invalidate immediately: a finishing worker must not restore geometry from old settings."""
        jid, self.draft_job = self.draft_job, None
        if jid is not None:
            self.ctx.bridge.cancel(jid)
            for row in self.rows.values():
                if jid in row.jobs:
                    self._fill_row(row)

    def preview_selected(self, *, keep_view: bool = False) -> None:
        if self.debounce.isActive():
            self.debounce.stop()
            self._apply_settings()
        row = self._current_row()
        if row is None or row.info is None:
            return
        if self._placement_error(row.key,mode='draft'):
            self.ctx.status(self._placement_error(row.key,mode='draft'))
            return
        self.cancel_preview()
        self.draft_prefer_result = not keep_view
        self.queue.set_status(row.item, "Previewing…", "busy")
        self.draft_job = self._submit(row, "draft", self.values())
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
        values = self.values()
        policy = self.ctx.pref("on_existing")         # Setup › Outputs › "When an output file exists"
        plan, exists = prefs.plan({k: self.output_for(k, values) for k in keys}, policy)
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
            self._submit(row, "run", values, str(plan[k]))
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
                if isinstance(row.result, Result) and row.result.changed and row.gen == self.gen:
                    out.append(k)
            else:
                out.append(k)
        return out

    def output_for(self, key: str, values: dict | None = None) -> Path:
        values = values if values is not None else self.values()
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
    def _on_event(self, row: Row, mode: str, gen: int, jid: int, kind: str, payload) -> None:
        if row.key not in self.rows:
            return
        if mode == "draft" and (jid != self.draft_job or gen != self.gen):
            if kind in ("done", "error", "cancelled"):
                row.jobs.discard(jid)
                if jid == self.draft_job:
                    self.draft_job = None
                    self._fill_row(row)
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
            if gen == self.gen:
                self._on_result(row, mode, kind, payload)
                row.gen = gen
        elif mode == "draft":
            self.draft_job = None
            self._on_result(row, mode, kind, payload, prefer_result=self.draft_prefer_result)
        else:
            self._on_run(row, kind, payload)
        self._update_controls()

    def _on_inspected(self, row: Row, kind: str, payload) -> None:
        if kind == "done":
            row.info = payload[0]
            check = (row.info.values.get("input_check") if row.info is not None else None) or {}
            self.queue.set_status(row.item, "Ready" if not self.tool.live else "Analysing…",
                                  "accent" if not self.tool.live else "dim")
            if check.get("state") == "repair":
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
                            _jsonable(self.values()), secs, toolkit_version=__version__,
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
            rec = RunRecord(self.tool.id, self.tool.version, "error", row.key, [], _jsonable(diagnostic.get('values',self.values())),
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
            if row.info is not None:
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

    def _show_fix(self, row: Row | None) -> None:
        check = (row.info.values.get("input_check") if row is not None and row.info is not None else None) or {}
        if row is not None and not row.error and check.get("state") == "repair" and REPAIR_TOOL in                 self.ctx.registry.tools and self.tool.id != REPAIR_TOOL:
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
        analysing = self.tool.live and any(r.gen != self.gen and r.info is not None for r in self.rows.values())
        placement_error=self._placement_error(self._current_row().key) if self._current_row() else ''
        preview_error=self._placement_error(self._current_row().key,mode='draft') if self._current_row() else ''
        self.btn_run.setEnabled(ready > 0 and not busy and not placement_error and not (self.tool.live and analysing)
                                and self.borrowed is None and not self.lacking)
        self.btn_run.setToolTip("Off while a workflow step is being set up here: use Back to workflow."
                                if self.borrowed is not None else placement_error)
        self.btn_cancel.setVisible(busy or self.draft_job is not None)
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
        for row in self.rows.values():
            self._fill_row(row)
        self._fold_button()
        self._sync_picker()

    def set_art(self, art: str) -> None:
        self.stage.set_art(art)


def _jsonable(values: dict) -> dict:
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in values.items()}
