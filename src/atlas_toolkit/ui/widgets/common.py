"""Small building blocks shared by every page: the Qt counterparts of the ds-* web components."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QStyle, QStyleOptionButton, QStylePainter, QToolButton,
                               QVBoxLayout, QWidget)

from ..theme import icon, label_font, mono


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def label(text: str, px: float = 10.5) -> QLabel:
    """.ds-label: uppercase mono micro-label."""
    lab = QLabel(text.upper())
    lab.setProperty("class", "label")
    lab.setFont(label_font(px))
    return lab


def hint(text: str, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("class", "hint")
    lab.setWordWrap(wrap)
    lab.setTextFormat(Qt.RichText)
    return lab


def badge(text: str, tone: str = "") -> QLabel:
    b = QLabel(text.upper())
    b.setProperty("badge", tone or "plain")
    b.setFont(label_font(10))
    b.setAlignment(Qt.AlignCenter)
    b.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return b


def set_badge(b: QLabel, text: str, tone: str = "") -> None:
    b.setText(text.upper())
    b.setProperty("badge", tone or "plain")
    repolish(b)


def hairline() -> QFrame:
    f = QFrame()
    f.setObjectName("hairline")
    f.setFixedHeight(1)
    return f


def section_head(text: str) -> QWidget:
    """.ds-section-head: a micro-label followed by a hairline."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    lay.addWidget(label(text))
    line = hairline()
    lay.addWidget(line, 1)
    lay.setAlignment(line, Qt.AlignVCenter)
    return w


def icon_button(name: str, tip: str = "", theme=None, checkable: bool = False, size: int = 18) -> QToolButton:
    b = QToolButton()
    b.setProperty("variant", "icon")
    b.setCheckable(checkable)
    b.setToolTip(tip)
    b.setIconSize(QSize(size, size))
    b.setCursor(Qt.PointingHandCursor)
    b._icon_name = name
    if theme is not None:
        apply_icon(b, theme)
    return b


def apply_icon(b, theme, name: str | None = None, tone: str = "text-2") -> None:
    """(Re)tint a button's icon for the current theme: text-2 at rest, accent text when checked."""
    n = name or getattr(b, "_icon_name", None)
    if n:
        b._icon_name = n
        b.setIcon(icon(n, theme[tone], active=theme["accent-text"] if b.isCheckable() else theme["text"],
                       disabled=theme["text-4"]))


class _SegmentButton(QPushButton):
    """Keep full names/actions accessible while painting long labels with an ellipsis, never a clipped middle."""

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SE_PushButtonContents, option, self)
        text = self.text().replace("&&", "&")
        option.text = self.fontMetrics().elidedText(text, Qt.ElideRight, max(0, rect.width())).replace("&", "&&")
        painter = QStylePainter(self)
        painter.drawControl(QStyle.CE_PushButton, option)


class Segmented(QFrame):
    """.ds-seg: a few exclusive (or none-selected) options. Emits picked(value)."""
    picked = Signal(object)

    def __init__(self, options: list, exclusive: bool = True, tips: dict | None = None, columns: int = 3):
        super().__init__()
        self.setObjectName("seg")
        lay = QGridLayout(self)        # more than `columns` options wrap onto further rows
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(False)
        self.exclusive = exclusive
        self.buttons: dict = {}
        for n, opt in enumerate(options):
            value, text = opt if isinstance(opt, tuple) else (opt, str(opt))
            b = _SegmentButton(text.replace("&", "&&"), checkable=True)     # "Pen & ink", not a mnemonic
            b.setProperty("seg", "true")
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)   # share the width, never widen the panel
            b.setMinimumWidth(40)
            if tips and value in tips:
                b.setToolTip(tips[value])
            else:
                b.setToolTip(text)
            b.clicked.connect(lambda _=False, v=value: self._click(v))
            self.group.addButton(b)
            lay.addWidget(b, n // columns, n % columns)
            self.buttons[value] = b
        for c in range(min(columns, len(options))):
            lay.setColumnStretch(c, 1)

    def _click(self, value) -> None:
        if self.exclusive:
            self.set_value(value)
        self.picked.emit(value)

    def set_value(self, value) -> None:
        for v, b in self.buttons.items():
            b.setChecked(v == value)

    def value(self):
        return next((v for v, b in self.buttons.items() if b.isChecked()), None)


class StatTile(QFrame):
    """.ds-stat: micro-label, a mono value and an optional 'before' line."""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("stat")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(3)
        lay.addWidget(label(title, 10))
        self.value = QLabel("—")
        self.value.setObjectName("statValue")
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.value)
        self.before = QLabel("")
        self.before.setObjectName("statBefore")
        lay.addWidget(self.before)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set(self, value: str, before: str = "", tone: str = "") -> None:
        self.value.setText(value)
        self.value.setProperty("tone", tone)
        repolish(self.value)
        self.before.setText(f"was {before}" if before else " ")    # keeps every tile the same height


def draw_reg_marks(p: QPainter, rect: QRectF, color: QColor, arm: float = 12.0, inset: float = -7.0,
                   width: float = 1.0) -> None:
    """Plotter registration-mark corners (.ds-reg) around `rect`."""
    r = rect.adjusted(inset, inset, -inset, -inset)
    p.save()
    p.setPen(QPen(color, width, Qt.SolidLine, Qt.FlatCap))
    for x, y, dx, dy in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                         (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
        p.drawLine(QPointF(x, y), QPointF(x + arm * dx, y))
        p.drawLine(QPointF(x, y), QPointF(x, y + arm * dy))
    p.restore()


class Toast(QFrame):
    """.ds-toast: bottom-right message with an optional action, fades out on its own."""
    action = Signal()

    def __init__(self, parent: QWidget, text: str, theme, tone: str = "", action_text: str = "", ms: int = 3200):
        super().__init__(parent)
        self.setObjectName("toast")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 10, 10)
        lay.setSpacing(10)
        ic = QLabel()
        name = {"success": "check", "warning": "warn", "danger": "warn"}.get(tone, "info")
        color = theme[{"success": "success", "warning": "warning", "danger": "danger"}.get(tone, "highlight")]
        ic.setPixmap(icon(name, color, 16).pixmap(16, 16))
        lay.addWidget(ic)
        msg = QLabel(text)
        msg.setWordWrap(True)
        msg.setMaximumWidth(380)
        lay.addWidget(msg, 1)
        if action_text:
            b = QPushButton(action_text)
            b.setProperty("variant", "ghost")
            b.clicked.connect(self.action.emit)
            b.clicked.connect(self.close)
            lay.addWidget(b)
        self.adjustSize()
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.anim = QPropertyAnimation(self.effect, b"opacity", self)
        self.anim.setDuration(300)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        QTimer.singleShot(ms, self.fade)

    def fade(self) -> None:
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.finished.connect(self.close)
        self.anim.start()


class Toaster:
    def __init__(self, host: QWidget):
        self.host = host
        self.toasts: list = []

    def show(self, text: str, theme, tone: str = "", action_text: str = "", on_action=None, ms: int = 3200) -> None:
        t = Toast(self.host, text, theme, tone, action_text, ms)
        if on_action:
            t.action.connect(on_action)
        t.destroyed.connect(lambda *_: self.toasts.remove(t) if t in self.toasts else None)
        self.toasts.append(t)
        t.setAttribute(Qt.WA_DeleteOnClose)
        t.show()
        t.raise_()
        self.layout()

    def layout(self) -> None:
        y = self.host.height() - 52
        for t in reversed([t for t in self.toasts if t.isVisible()]):
            t.adjustSize()
            y -= t.height()
            t.move(self.host.width() - t.width() - 20, y)
            y -= 10


def mono_label(text: str = "", px: float = 11.5) -> QLabel:
    lab = QLabel(text)
    lab.setFont(mono(px))
    lab.setProperty("class", "mono")
    return lab


class RuntimeNotice(QFrame):
    """A program a tool needs that is not installed here (core/runtimes): what is missing, how to get it, and
    "Locate …" to point at it. `changed` fires after a path was chosen (saved in the app settings)."""
    changed = Signal()

    def __init__(self, runtime, settings, guide=None, compact: bool = False):
        """`compact` stacks the badge, the text and the buttons, for narrow panels (a workflow step's inspector)."""
        super().__init__()
        self.setObjectName("warnBanner")
        self.runtime, self.settings = runtime, settings
        text = QLabel(f"{runtime.label} {runtime.need} was not found. {runtime.hint}" if not compact else
                      f"{runtime.label} {runtime.need} was not found on this computer, so this step cannot run yet.")
        text.setWordWrap(True)
        locate = QPushButton(f"Locate {runtime.label}…" if not compact else "Locate…")
        locate.setProperty("variant", "primary")
        locate.clicked.connect(self.locate)
        buttons = [locate]
        if runtime.url:
            get = QPushButton(runtime.get or f"Get {runtime.label}")
            get.clicked.connect(self.open_url)
            buttons.append(get)
        if guide is not None:                       # Setup › Components: why, when and how to install it
            g = QPushButton("Setup guide")
            g.setProperty("variant", "ghost")
            g.clicked.connect(guide)
            buttons.append(g)
        mark = badge(f"needs {runtime.label}", "warning")
        if compact:
            vl = QVBoxLayout(self)
            vl.setContentsMargins(12, 10, 12, 10)
            vl.setSpacing(8)
            vl.addWidget(mark, 0, Qt.AlignLeft)
            vl.addWidget(text)
            row = QHBoxLayout()
            row.setSpacing(6)
            for b in buttons:
                row.addWidget(b)
            row.addStretch(1)
            vl.addLayout(row)
            return
        hl = QHBoxLayout(self)
        hl.setContentsMargins(14, 8, 10, 8)
        hl.setSpacing(10)
        hl.addWidget(mark, 0, Qt.AlignVCenter)
        hl.addWidget(text, 1)
        for b in buttons:
            hl.addWidget(b)

    def locate(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, f"Locate {self.runtime.label}", "", self.runtime.file_filter)
        if path:
            self.choose(path)

    def choose(self, path: str) -> None:
        """Remember the program (through the app's own settings object, which the shell saves too)."""
        from ...core.paths import program_inside
        self.settings.set(self.runtime.setting, program_inside(str(path)))
        self.settings.save()
        self.changed.emit()

    def open_url(self) -> None:
        from ...core.paths import open_url
        open_url(self.runtime.url)
