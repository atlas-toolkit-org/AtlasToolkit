"""
The app's number fields: IntSpin and DoubleSpin. Every numeric value in the UI uses them, so they all behave alike:

- Two triangles inside the field's right edge step the value (hold to repeat). Up / Down keys and the wheel (once
  the field has focus) do the same. Shift steps ten times as far, Ctrl a tenth as far.
- The step fits the parameter (see step_for): the one its spec declares, or one that follows the value when the
  declared step cannot serve the range, such as face counts up to millions or sizes much smaller than their step.
- Typing is forgiving. Any number can be typed: a comma or a point, more decimals than the field shows, a value out
  of range. It is rounded and clamped when it is committed (Enter or leaving the field). A click selects the value,
  so typing replaces it.

Numbers read the same on every computer: point decimals, no thousands separators (the C locale).
"""
from __future__ import annotations

import math
import re

from PySide6.QtCore import QLocale, QPointF, QSize, Qt, QTimer
from PySide6.QtGui import QFontMetrics, QPainter, QPalette, QPolygonF, QValidator
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QDoubleSpinBox, QSpinBox, QStyle, QStyleOptionSpinBox

from ..theme import mono

WIDE_RANGE = 2000       # a declared step this many times smaller than the range cannot cross it: follow the value
FOLLOW = 0.02           # ... stepping about 2% of the value
FINE = 0.25             # a value at or below the step, with a minimum above zero: a quarter of the value


def _round_125(x: float) -> float:
    """The largest 1, 2 or 5 × 10^n not above x."""
    power = 10.0 ** math.floor(math.log10(x) + 1e-9)
    return next(m for m in (5, 2, 1, 0.5) if m * power <= x * (1 + 1e-9)) * power


def step_for(value: float, lo: float, hi: float, step: float) -> float:
    """The step at `value`. The declared one, unless it does not suit the range:
    a very wide range follows the size of the value (150000 faces step 2000, not 1), and a value no larger than its
    step, where zero is out of range, steps by part of itself (an edge of 0.02 does not jump by 0.1)."""
    size = abs(value)
    if size <= 0 or step <= 0:
        return step
    if lo > 0 and size <= step:
        return min(step, _round_125(size * FINE))
    if not (math.isfinite(lo) and math.isfinite(hi)) or (hi - lo) / step > WIDE_RANGE:
        return max(step, _round_125(size * FOLLOW))
    return step


def stepped(value: float, steps: int, lo: float, hi: float, step: float, resolution: float, factor: float = 1.0
            ) -> float:
    """`value` moved by `steps` (negative: down), never finer than `resolution`. A step chosen by step_for lands on
    its round multiples (150000, 152000 …); the declared step keeps the value's own offset (12.3, 17.3 …)."""
    base = step_for(value, lo, hi, step)
    size = max(base * factor, resolution)
    if base != step and factor == 1.0:
        whole = math.floor(value / size + 1e-9) if steps > 0 else math.ceil(value / size - 1e-9)
        out = (whole + steps) * size
    else:
        out = value + steps * size
    return min(max(out, lo), hi)


class _StepField:
    """What both number fields share; the number patterns and the resolution differ."""

    def _setup_field(self) -> None:
        self.setLocale(QLocale.c())
        self.setGroupSeparatorShown(False)
        self.setAccelerated(True)                  # holding a triangle speeds up
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setCorrectionMode(QAbstractSpinBox.CorrectToPreviousValue)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setKeyboardTracking(False)

    # ------------------------------------------------------------------ the wheel, sizes, selecting
    def wheelEvent(self, e):
        if self.hasFocus():                        # scrolling the inspector never edits
            super().wheelEvent(e)
        else:
            e.ignore()

    def minimumSizeHint(self):
        h = super().minimumSizeHint()
        return QSize(min(h.width(), 60), h.height())

    def sizeHint(self):                            # not as wide as the widest value: fit the inspector's column
        h = super().sizeHint()
        return QSize(min(h.width(), 112), h.height())

    def width_for(self, *values) -> int:
        """The width that shows the longest of these values whole, with its unit and the triangles."""
        fm = QFontMetrics(mono(12.5))
        return max(fm.horizontalAdvance(self.prefix() + self.textFromValue(v) + self.suffix()) for v in values) + 44

    def focusInEvent(self, e):
        super().focusInEvent(e)
        QTimer.singleShot(0, self.selectAll)       # after the click that gave the focus has placed the cursor

    # ------------------------------------------------------------------ stepping
    def stepBy(self, steps: int) -> None:
        self.interpretText()                       # a number just typed is what the step starts from
        mods = QApplication.keyboardModifiers()
        factor = 10.0 if mods & Qt.ShiftModifier else 1.0
        if mods & Qt.ControlModifier:              # Qt has already made Ctrl ten steps: ours is a tenth of one
            factor = 0.1
            steps = int(math.copysign(max(abs(steps) // 10, 1), steps))
        self.setValue(self._step(steps, factor))
        self.selectAll()

    # ------------------------------------------------------------------ typing
    def _bare(self, text: str) -> str:
        """The number in a field's text: no prefix or suffix (X, mm), and a comma counts as a point."""
        t = text
        if self.prefix() and t.startswith(self.prefix()):
            t = t[len(self.prefix()):]
        if self.suffix() and t.endswith(self.suffix()):
            t = t[:-len(self.suffix())]
        return t.strip().replace(",", ".")

    def validate(self, text: str, pos: int):
        t = self._bare(text)
        if self._COMPLETE.fullmatch(t):
            return QValidator.Acceptable, text, pos
        if self._PARTIAL.fullmatch(t):             # on the way to a number: "", "-", "1e"
            return QValidator.Intermediate, text, pos
        return QValidator.Invalid, text, pos

    def valueFromText(self, text: str):
        try:
            return self._number(self._bare(text))
        except (ValueError, OverflowError):
            return self.value()

    # ------------------------------------------------------------------ the triangles
    def paintEvent(self, e):
        super().paintEvent(e)
        opt = QStyleOptionSpinBox()
        self.initStyleOption(opt)
        can = self.stepEnabled() if self.isEnabled() else QAbstractSpinBox.StepNone
        color = self.palette().color(QPalette.Text)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for control, flag, tip in ((QStyle.SC_SpinBoxUp, QAbstractSpinBox.StepUpEnabled, -1.0),
                                   (QStyle.SC_SpinBoxDown, QAbstractSpinBox.StepDownEnabled, 1.0)):
            c = QPointF(self.style().subControlRect(QStyle.CC_SpinBox, opt, control, self).center()) + QPointF(.5, .5)
            color.setAlphaF(0.8 if can & flag else 0.22)   # a faint triangle: at its limit
            p.setBrush(color)
            p.drawPolygon(QPolygonF([c + QPointF(-3.5, -2.0 * tip), c + QPointF(3.5, -2.0 * tip),
                                     c + QPointF(0, 2.0 * tip)]))
        p.end()


class IntSpin(_StepField, QSpinBox):
    _COMPLETE = re.compile(r"[+-]?\d+([.]0*)?")
    _PARTIAL = re.compile(r"[+-]?\d*[.]?0*")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_field()

    @staticmethod
    def _number(t: str) -> int:
        return int(float(t))

    def _step(self, steps: int, factor: float) -> int:
        return int(round(stepped(self.value(), steps, self.minimum(), self.maximum(), self.singleStep(), 1, factor)))


class DoubleSpin(_StepField, QDoubleSpinBox):
    _COMPLETE = re.compile(r"[+-]?(\d+[.]?\d*|[.]\d+)([eE][+-]?\d+)?")
    _PARTIAL = re.compile(r"[+-]?\d*[.]?\d*([eE][+-]?)?")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_field()

    def _number(self, t: str) -> float:
        return round(float(t), self.decimals())

    def _step(self, steps: int, factor: float) -> float:
        return stepped(self.value(), steps, self.minimum(), self.maximum(), self.singleStep(),
                       10.0 ** -self.decimals(), factor)
