"""Lightweight orthographic spine editor. Mesh work stays in Atlas's process worker."""
from pathlib import Path
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QWidget

from atlas_toolkit.geom import frame3d
from atlas_toolkit.ui.theme import qcolor
from . import TOOL
from .mesh_bend import Spine


class SpineView(QWidget):
    title = "Spine"
    info_changed = Signal(str)

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.theme = page.theme
        self.values = dict(TOOL.defaults())
        self.path = None
        self.info = None
        self.spine = None
        self.error = "Add a model to shape its spine."
        self.handles = {}
        self.drag = None
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        hint = QLabel("Drag: angle · Shift-drag: direction · Drag range handles at right")
        hint.setProperty("class", "hint")
        hint.setWordWrap(True)
        bar.addWidget(hint, 1)
        self.preview_button = QPushButton("Preview bend")
        self.preview_button.clicked.connect(lambda: page.preview_selected())
        bar.addWidget(self.preview_button)
        layout.addLayout(bar)
        self.canvas = _Canvas(self)
        layout.addWidget(self.canvas, 1)
        self.note = QLabel("Requested spine · Preview shows the refined model and reports any angle reduction by inner-curve protection.")
        self.note.setProperty("class", "hint")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

    def set_input(self, path, info):
        self.path, self.info = Path(path) if path else None, info
        self.values = dict(self.page.values())
        self.rebuild()

    def values_changed(self, values):
        self.values = dict(values)
        self.rebuild()

    def rebuild(self):
        self.spine = None
        self.error = "Add a model to shape its spine."
        if self.info is not None and self.path:
            try:
                from atlas_toolkit.core.params import Params
                p = Params(self.values)
                mesh = self.info.preview["views"][0]["mesh"]
                up, front, rotate, _ = frame3d.resolve_axes(self.path, p, self.info.values["extents"])
                frame = frame3d.fit_frame(mesh.vertices, up, front, rotate, mode="height", size=2.)
                v = frame.apply(mesh.vertices)
                self.spine = Spine(np.array([v.min(0), v.max(0)]), p, p.amount, samples=257)
                self.error = ""
            except (ValueError, KeyError, TypeError) as exc:
                self.error = str(exc)
        self.preview_button.setEnabled(self.spine is not None)
        self.canvas.update()
        self.info_changed.emit(self.info_text())

    def commit(self, updates):
        self.values.update(updates)
        self.page.form.set_values(updates)
        self.page._settings_changed()
        self.rebuild()

    def validation_error(self, path, mode="run"):
        v = self.page.values()
        return "Bend From must be below To." if float(v["bend_from"]) >= float(v["bend_to"]) else ""

    def info_text(self):
        return "Requested spine · drag to shape the gesture" if self.spine else self.error

    def set_theme(self, theme):
        self.theme = theme
        self.canvas.update()

    def set_art(self, art):
        pass

    def fit(self):
        self.canvas.update()


class _Canvas(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setMinimumSize(300, 240)
        self.setCursor(Qt.OpenHandCursor)
        self.track = QRectF()

    def paintEvent(self, event):
        e = self.editor
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bg, muted = qcolor(e.theme["bg-1"]), qcolor(e.theme["text-2"])
        accent, line = qcolor(e.theme["accent"]), qcolor(e.theme["line-3"])
        painter.fillRect(self.rect(), bg)
        if e.spine is None:
            painter.setPen(muted)
            painter.drawText(self.rect().adjusted(20, 20, -20, -20), Qt.AlignCenter | Qt.TextWordWrap, e.error)
            return
        spine = e.spine
        guide = spine.guide() @ spine.B
        source = np.column_stack((np.full(2, spine.neutral[0]), np.full(2, spine.neutral[1]), spine.bounds[:, 2]))
        e.handles = {}
        w, h = self.width(), self.height()
        for index, label in enumerate(("U / axis", "V / axis")):
            rect = QRectF(22 + index * (w - 100) / 2, 40, (w - 115) / 2, h - 85)
            points = guide[:, [index, 2]]
            reference = source[:, [index, 2]]
            all_points = np.vstack((points, reference))
            lo, hi = all_points.min(0), all_points.max(0)
            span = np.maximum(hi - lo, spine.height * .5)
            scale = min(rect.width() / span[0], rect.height() / span[1]) * .82
            centre = (lo + hi) / 2
            def screen(p):
                return QPointF(rect.center().x() + (p[0] - centre[0]) * scale,
                               rect.center().y() - (p[1] - centre[1]) * scale)
            painter.setPen(QPen(line, 1))
            painter.drawRoundedRect(rect, 10, 10)
            painter.setPen(muted)
            painter.drawText(QRectF(rect.x(), 10, rect.width(), 25), Qt.AlignCenter, label)
            painter.setPen(QPen(muted, 1, Qt.DashLine))
            painter.drawLine(screen(reference[0]), screen(reference[1]))
            path = QPainterPath(screen(points[0]))
            for point in points[1:]:
                path.lineTo(screen(point))
            painter.setPen(QPen(accent, 2.5))
            painter.drawPath(path)
            # Transported section marks show the turn, rather than just lateral displacement.
            stations = np.linspace(spine.start, spine.start + spine.length, 9)
            rest = np.column_stack((np.full(9, spine.neutral[0]), np.full(9, spine.neutral[1]), stations))
            delta = np.zeros(3)
            delta[index] = min(spine.height * .06, float(spine.bounds[1, index] - spine.bounds[0, index]) * .25)
            left = spine.deform((rest - delta) @ spine.B.T) @ spine.B
            right = spine.deform((rest + delta) @ spine.B.T) @ spine.B
            painter.setPen(QPen(accent, 1.2))
            for a, b in zip(left, right):
                painter.drawLine(screen(a[[index, 2]]), screen(b[[index, 2]]))
            for point, filled in ((points[0], False), (points[-1], True)):
                painter.setBrush(accent if filled else bg)
                painter.drawEllipse(screen(point), 4.5, 4.5)
            anchor = np.array([[spine.neutral[0], spine.neutral[1], spine.start + spine.anchor_t * spine.length]])
            painter.setBrush(bg)
            painter.drawEllipse(screen(anchor[0, [index, 2]]), 6.5, 6.5)
        self.track = QRectF(w - 55, 65, 12, h - 130)
        x = self.track.center().x()
        painter.setPen(QPen(line, 3))
        painter.drawLine(QPointF(x, self.track.top()), QPointF(x, self.track.bottom()))
        painter.setPen(muted)
        painter.drawText(QRectF(w - 75, 15, 65, 30), Qt.AlignCenter, "Range")
        for key in ("bend_from", "bend_to"):
            point = QPointF(x, self.track.bottom() - float(e.values[key]) / 100 * self.track.height())
            e.handles[key] = point
            painter.setPen(QPen(accent, 2))
            painter.setBrush(bg)
            painter.drawRect(QRectF(point.x() - 6, point.y() - 6, 12, 12))
        painter.setPen(muted)
        painter.drawText(QRectF(15, h - 34, w - 30, 25), Qt.AlignCenter,
                         f"{e.values['profile']} · {e.values['angle']:g}° · direction {e.values['direction']:g}° · dashed = straight spine")

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self.editor.spine is None:
            return
        e = self.editor
        key = next((key for key, point in e.handles.items() if (point - event.position()).manhattanLength() < 22), None)
        e.drag = (key or ("direction" if event.modifiers() & Qt.ShiftModifier else "angle"), event.position(), dict(e.values))
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        e = self.editor
        if e.drag is None:
            return
        key, start, values = e.drag
        if key in ("bend_from", "bend_to"):
            value = np.clip(100 * (self.track.bottom() - event.position().y()) / max(self.track.height(), 1), 0, 100)
            if key == "bend_from":
                value = min(value, e.values["bend_to"] - .1, 99.9)
            else:
                value = max(value, e.values["bend_from"] + .1, .1)
        else:
            parameter = TOOL.param(key)
            value = np.clip(values[key] + event.position().x() - start.x(), parameter.min, parameter.max)
        e.commit({key: round(float(value), 1)})

    def mouseReleaseEvent(self, event):
        self.editor.drag = None
        self.setCursor(Qt.OpenHandCursor)
