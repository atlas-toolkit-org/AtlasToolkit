"""
The inspector form, generated from a tool's parameter schema: sections (collapsible), one row per parameter,
`visible_if` rules, Advanced parameters behind a switch, AUTO toggles for suggested values, dice for seeds.
A tool never builds its own widgets; a new parameter type needs one more branch in `_make_field`.
"""
from __future__ import annotations

import random

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget)

from ...core.params import AUTO, Bool, Choice, FileRef, Float, Int, Section, Seed, Text, Vec3
from ..theme import icon, label_font
from .common import Segmented, apply_icon, icon_button, repolish
from .spin import DoubleSpin, IntSpin

SLIDER_STEPS = 1000


class Combo(QComboBox):
    """Menus ignore the mouse wheel unless focused, so scrolling the inspector never edits."""

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class Field(QWidget):
    """One parameter's editor. value()/set_value() speak the schema's values (AUTO included)."""
    changed = Signal()

    def __init__(self, param, theme):
        super().__init__()
        self.param = param
        self.theme = theme
        self.auto_btn: QToolButton | None = None
        self.info = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.lay = lay
        self._build()
        self.setToolTip(param.help)

    # ------------------------------------------------------------------ construction
    def _spin(self, p, integer: bool):
        if integer:
            sb = IntSpin()
            lo, hi = int(max(p.min, -2**31)), int(min(p.max, 2**31 - 1))
            sb.setRange(lo, hi)
            sb.setSingleStep(int(getattr(p, "step", 1)) or 1)
        else:
            sb = DoubleSpin()
            sb.setDecimals(p.decimals)
            sb.setRange(max(p.min, -1e12), min(p.max, 1e12))
            sb.setSingleStep(p.step)
        if p.unit:
            sb.setSuffix(f" {p.unit}")
        sb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        sb.valueChanged.connect(self._on_edit)
        return sb

    def _build(self) -> None:
        p = self.param
        self.widgets = []
        if isinstance(p, (Float, Int)):
            sb = self._spin(p, isinstance(p, Int))
            self.widgets = [sb]
            self.lay.addWidget(sb, 1)
            if getattr(p, "slider", False):
                self.slider = QSlider(Qt.Horizontal)
                self.slider.setRange(0, SLIDER_STEPS)
                self.slider.setFocusPolicy(Qt.NoFocus)
                self.slider.valueChanged.connect(self._on_slider)
                self.lay.insertWidget(0, self.slider, 2)
                sb.setFixedWidth(sb.width_for(p.min, p.max))   # the value is never clipped: the slider gives way
            if isinstance(p, Seed):
                dice = icon_button("dice", "New random seed", self.theme, size=16)
                dice.clicked.connect(lambda: sb.setValue(random.randint(0, 99999)))
                self.lay.addWidget(dice)
        elif isinstance(p, Vec3):
            for axis in p.axes:
                sb = self._spin(p, p.integer)
                sb.setSuffix("")
                sb.setPrefix(f"{axis} ")
                self.widgets.append(sb)
                self.lay.addWidget(sb, 1)
        elif isinstance(p, Bool):
            cb = QCheckBox(p.label)
            cb.toggled.connect(self._on_edit)
            self.widgets = [cb]
            self.lay.addWidget(cb, 1)
        elif isinstance(p, Choice):
            if p.segmented and not p.options_from:
                seg = Segmented(p.pairs())
                seg.picked.connect(self._on_edit)
                self.widgets = [seg]
                self.lay.addWidget(seg, 1)
            else:
                cb = Combo()
                cb.setFocusPolicy(Qt.StrongFocus)
                cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                cb.setMinimumContentsLength(8)
                for v, lab in p.pairs():
                    cb.addItem(lab, v)
                cb.currentIndexChanged.connect(self._on_edit)
                self.widgets = [cb]
                self.lay.addWidget(cb, 1)
        elif isinstance(p, Text):
            le = QLineEdit()
            le.setPlaceholderText(p.placeholder)
            le.editingFinished.connect(self._on_edit)
            self.widgets = [le]
            self.lay.addWidget(le, 1)
        elif isinstance(p, FileRef):
            le = QLineEdit()
            le.setPlaceholderText("none")
            le.editingFinished.connect(self._on_edit)
            pick = icon_button("folder", "Choose a file", self.theme, size=16)
            pick.clicked.connect(self._pick_file)
            self.widgets = [le]
            self.lay.addWidget(le, 1)
            self.lay.addWidget(pick)
        if getattr(p, "suggest", None) is not None:
            self.auto_btn = QToolButton()
            self.auto_btn.setText("auto")
            self.auto_btn.setFont(label_font(9.5))
            self.auto_btn.setStyleSheet("padding: 4px 6px;")
            self.auto_btn.setCheckable(True)
            self.auto_btn.setProperty("variant", "ghost")
            self.auto_btn.setToolTip("Automatic: computed for each input file from its size.")
            self.auto_btn.setCursor(Qt.PointingHandCursor)
            self.auto_btn.toggled.connect(self._on_auto)
            self.lay.addWidget(self.auto_btn)

    # ------------------------------------------------------------------ value
    def _pick_file(self) -> None:
        exts = " ".join(f"*{e}" for e in self.param.exts) or "*.*"
        f, _ = QFileDialog.getOpenFileName(self, self.param.label, "", f"Files ({exts});;All files (*.*)")
        if f:
            self.widgets[0].setText(f)
            self._on_edit()

    def _on_slider(self, pos: int) -> None:
        p = self.param
        lo, hi = p.min, p.max
        sb = self.widgets[0]
        sb.setValue(lo + (hi - lo) * pos / SLIDER_STEPS)

    def _sync_slider(self) -> None:
        if hasattr(self, "slider") and self.widgets:
            p = self.param
            v = self.widgets[0].value()
            self.slider.blockSignals(True)
            self.slider.setValue(int(round((v - p.min) / ((p.max - p.min) or 1) * SLIDER_STEPS)))
            self.slider.blockSignals(False)

    def _on_edit(self, *_):
        self._sync_slider()
        self.changed.emit()

    def _on_auto(self, on: bool) -> None:
        for w in self.widgets:
            w.setEnabled(not on)
            w.setProperty("auto", "true" if on else "false")
            repolish(w)
        if hasattr(self, "slider"):
            self.slider.setEnabled(not on)
        if on:
            self._show_suggestion()
        self.changed.emit()

    def is_auto(self) -> bool:
        return bool(self.auto_btn and self.auto_btn.isChecked())

    def set_info(self, info) -> None:
        """Selected input changed: refresh suggestions and input-dependent choices."""
        self.info = info
        p = self.param
        if isinstance(p, Choice) and p.options_from and info is not None:
            cb = self.widgets[0]
            cur = cb.currentData()
            cb.blockSignals(True)
            cb.clear()
            for v, lab in p.pairs(info):
                cb.addItem(lab, v)
            i = cb.findData(cur)
            cb.setCurrentIndex(i if i >= 0 else 0)
            cb.blockSignals(False)
        if self.is_auto():
            self._show_suggestion()

    def _show_suggestion(self) -> None:
        if self.info is None:
            return
        try:
            v = self.param.coerce(self.param.suggest(self.info))
        except Exception:
            return
        self._set_widgets(v, quiet=True)

    def value(self):
        if self.is_auto():
            return AUTO
        p, w = self.param, self.widgets
        if isinstance(p, (Float, Int)):
            return w[0].value()
        if isinstance(p, Vec3):
            return tuple(sb.value() for sb in w)
        if isinstance(p, Bool):
            return w[0].isChecked()
        if isinstance(p, Choice):
            return w[0].value() if isinstance(w[0], Segmented) else w[0].currentData()
        return w[0].text()

    def _set_widgets(self, v, quiet: bool = True) -> None:
        p, w = self.param, self.widgets
        for x in w:
            x.blockSignals(quiet)
        try:
            if isinstance(p, (Float, Int)):
                w[0].setValue(v)
                self._sync_slider()
            elif isinstance(p, Vec3):
                for sb, x in zip(w, v):
                    sb.setValue(x)
            elif isinstance(p, Bool):
                w[0].setChecked(bool(v))
            elif isinstance(p, Choice):
                if isinstance(w[0], Segmented):
                    w[0].set_value(v)
                else:
                    i = w[0].findData(v)
                    if i < 0 and p.options_from:
                        w[0].addItem(str(v), v)
                        i = w[0].count() - 1
                    w[0].setCurrentIndex(max(i, 0))
            else:
                w[0].setText("" if v is None else str(v))
        finally:
            for x in w:
                x.blockSignals(False)

    def set_value(self, v) -> None:
        if self.auto_btn is not None:
            self.auto_btn.blockSignals(True)
            self.auto_btn.setChecked(v == AUTO)
            self.auto_btn.blockSignals(False)
            for x in self.widgets:
                x.setEnabled(v != AUTO)
                x.setProperty("auto", "true" if v == AUTO else "false")
                repolish(x)
        if v == AUTO:
            self._show_suggestion()
            return
        self._set_widgets(v)


class ParamForm(QWidget):
    changed = Signal()

    def __init__(self, tool, theme, label_menu=None):
        """`label_menu(key, global_pos)`: called on a right-click on a parameter's label (e.g. to expose it on a
        workflow's panel); `mark()` highlights labels."""
        super().__init__()
        self.tool = tool
        self.theme = theme
        self.label_menu = label_menu
        self.fields: dict[str, Field] = {}
        self.rows: dict[str, list] = {}          # key -> widgets of its row (label, field)
        self.sections: list = []
        self.show_advanced = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        items = tool.params if any(isinstance(i, Section) for i in tool.params) else [Section("Settings", *tool.params)]
        loose = []
        for item in items:
            if isinstance(item, Section):
                if loose:
                    self._add_section(lay, Section("Settings", *loose))
                    loose = []
                self._add_section(lay, item)
            else:
                loose.append(item)
        if loose:
            self._add_section(lay, Section("Settings", *loose))
        if any(p.advanced for p in tool.param_list):
            self.adv = QCheckBox("Show advanced settings")
            self.adv.toggled.connect(self._toggle_advanced)
            lay.addSpacing(4)
            lay.addWidget(self.adv)
        self.refresh_visibility()

    def _add_section(self, lay, sec: Section) -> None:
        head = QToolButton()
        head.setProperty("section", "true")
        head.setText(sec.title.upper())
        head.setFont(label_font(10.5))
        head.setCheckable(True)
        head.setChecked(not sec.collapsed)
        head.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        head.setIconSize(QSize(12, 12))
        head.setCursor(Qt.PointingHandCursor)
        head.setSizePolicy(head.sizePolicy().horizontalPolicy(), head.sizePolicy().verticalPolicy())
        if sec.help:
            head.setToolTip(sec.help)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 2, 0, 8)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(7)
        grid.setColumnStretch(1, 1)
        r = 0
        for p in sec.params:
            f = Field(p, self.theme)
            f.changed.connect(self._field_changed)
            self.fields[p.key] = f
            if isinstance(p, Bool):
                grid.addWidget(f, r, 0, 1, 2)
                self.rows[p.key] = [f]
            else:
                name = QLabel(p.label)
                name.setProperty("class", "muted")
                name.setToolTip(p.help)
                if isinstance(p, (Vec3, FileRef)):   # several boxes: their own full-width line under the label
                    grid.addWidget(name, r, 0, 1, 2)
                    r += 1
                    grid.addWidget(f, r, 0, 1, 2)
                else:
                    grid.addWidget(name, r, 0)
                    grid.addWidget(f, r, 1)
                self.rows[p.key] = [name, f]
            if self.label_menu is not None:
                handle = self.rows[p.key][0]
                handle.setContextMenuPolicy(Qt.CustomContextMenu)
                handle.customContextMenuRequested.connect(
                    lambda pos, k=p.key, w=handle: self.label_menu(k, w.mapToGlobal(pos)))
            r += 1
        lay.addWidget(head)
        lay.addWidget(body)

        def toggle(on, b=body, h=head):
            b.setVisible(on)
            h.setIcon(icon("down" if on else "right", self.theme["text-3"], 12))
        head.toggled.connect(toggle)
        toggle(head.isChecked())
        self.sections.append((sec, head, body))

    def insert_after(self, title: str, widget: QWidget) -> bool:
        """Put an extra widget (a tool's custom panel) at the end of the section called `title`."""
        for sec, head, body in self.sections:
            if sec.title == title:
                grid = body.layout()
                grid.addWidget(widget, grid.rowCount(), 0, 1, 2)
                return True
        return False

    def _toggle_advanced(self, on: bool) -> None:
        self.show_advanced = on
        self.refresh_visibility()

    def _field_changed(self) -> None:
        self.refresh_visibility()
        self.changed.emit()

    def refresh_visibility(self) -> None:
        values = self.values()
        for key, widgets in self.rows.items():
            p = self.fields[key].param
            show = p.visible(values) and (self.show_advanced or not p.advanced)
            for w in widgets:
                w.setVisible(show)
        for sec, head, body in self.sections:   # a section with nothing visible disappears
            any_visible = any(p.visible(values) and (self.show_advanced or not p.advanced) for p in sec.params)
            head.setVisible(any_visible)
            body.setVisible(any_visible and head.isChecked())

    def values(self) -> dict:
        return {k: f.value() for k, f in self.fields.items()}

    def set_values(self, values: dict) -> None:
        for k, v in values.items():
            if k in self.fields:
                self.fields[k].set_value(v)
        self.refresh_visibility()

    def set_info(self, info) -> None:
        for f in self.fields.values():
            f.set_info(info)

    def set_enabled(self, on: bool) -> None:
        for f in self.fields.values():
            f.setEnabled(on)

    def mark(self, keys) -> None:
        """Highlight these parameters' labels (property `marked`, styled by the theme)."""
        keys = set(keys)
        for k, widgets in self.rows.items():
            w = widgets[0]
            on = "true" if k in keys else "false"
            if w.property("marked") != on:
                w.setProperty("marked", on)
                repolish(w)

    def retheme(self, theme) -> None:
        self.theme = theme
        for f in self.fields.values():
            f.theme = theme
            for b in f.findChildren(QToolButton):
                if getattr(b, "_icon_name", None):
                    apply_icon(b, theme)
        for sec, head, body in self.sections:
            head.setIcon(icon("down" if head.isChecked() else "right", theme["text-3"], 12))
