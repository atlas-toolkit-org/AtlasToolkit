"""Shared 3D placement editing for contract-hosted viewers: a GL canvas with screen-space handles and an editor base.

The canvas owns navigation, projection (perspective or orthographic, in float64), the screen-space overlay, handle
hit-testing, strokes and the mouse/key plumbing. The editor base owns the controls (modes, camera, ghost, Expand),
undo and how values reach the page. Subclasses say what the handles are and what a drag or a stroke does:
plane_editor.PlaneEditor (Plane Cut, Mirror) and shape_editor.ShapeEditor (Shape Crop). Neither knows a tool id.
"""
from __future__ import annotations

import math
import warnings

import numpy as np
from scipy.spatial.transform import Rotation

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen, QPolygonF, QVector3D
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame,
                              QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from atlas_toolkit.ui.widgets.gl_guard import GuardedGL
from atlas_toolkit.ui.widgets.gl_nav import GestureNavigation
from atlas_toolkit.ui.widgets.mesh_view import gl, GL_ERROR
from atlas_toolkit.ui.widgets.param_form import ParamForm

HANDLE_PX = 76          # length of an arrow or ring radius on screen


def distance_segment(point, a, b):
    delta = b - a
    t = np.clip(np.dot(point - a, delta) / max(np.dot(delta, delta), 1e-12), 0, 1)
    return float(np.linalg.norm(point - a - t * delta))


def rotate_euler(rotation, axis, degrees) -> tuple:
    """XYZ Euler angles after turning by `degrees` about the world `axis`."""
    rot = Rotation.from_rotvec(np.asarray(axis, float) * math.radians(degrees)) * Rotation.from_euler(
        "xyz", rotation, degrees=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return tuple(float(a) for a in rot.as_euler("xyz", degrees=True))


if gl is not None:
    class _Guide(QWidget):
        """A transparent Qt child keeps controls visible in native GL rendering and Qt screenshots alike."""
        def __init__(self, canvas):
            super().__init__(canvas)
            self.canvas = canvas
            self.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.setAttribute(Qt.WA_NoSystemBackground)

        def paintEvent(self, event):
            self.canvas.draw_guides(self)

    class GizmoCanvas(GestureNavigation, GuardedGL, gl.GLViewWidget):
        def __init__(self, editor):
            super().__init__()
            self.editor = editor
            self.orthographic = False
            self.drag_state = None
            self.stroke = None
            self.setMouseTracking(True)
            self.setFocusPolicy(Qt.StrongFocus)
            self.setMinimumSize(240, 220)
            self.setCameraPosition(distance=100, elevation=22, azimuth=-60)
            self.overlay = _Guide(self)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self.overlay.setGeometry(self.rect())
            self.overlay.raise_()

        def projectionMatrix(self, region, viewport):
            if not self.orthographic:
                return super().projectionMatrix(region, viewport)
            from PySide6.QtGui import QMatrix4x4
            distance = self.opts["distance"]
            half = distance * math.tan(math.radians(self.opts["fov"] / 2))
            aspect = viewport[3] / max(viewport[2], 1)
            matrix = QMatrix4x4()
            matrix.ortho(-half, half, -half * aspect, half * aspect, distance * .001, distance * 1000)
            return matrix

        def initializeGL(self):
            self._guarded(self._set_up_context)

        def _set_up_context(self):
            # QOpenGLWidget creates a new context when moved into/out of a top-level dialog. Recreate this
            # editor's private buffers/shaders in that context; never reset pyqtgraph's shared shader cache.
            from PySide6.QtOpenGL import QOpenGLVertexArrayObject
            self.default_vao = QOpenGLVertexArrayObject(self)
            self.editor.rebuild_meshes()
            super().initializeGL()

        def matrix(self):
            rect = (0, 0, max(self.width(), 1), max(self.height(), 1))
            return self.projectionMatrix(rect, rect) * self.viewMatrix()

        def project3(self, point):
            # Qt's QVector3D map/invert uses float32. Do the homogeneous math in float64 so a screen line
            # remains a plane even for far clip rays and models far from the world origin.
            matrix = np.asarray(self.matrix().data(), dtype=float).reshape(4, 4, order="F")
            q = matrix @ np.r_[point, 1.0]
            q = q[:3] / q[3]
            return np.array([(q[0] + 1) * self.width() / 2, (1 - q[1]) * self.height() / 2, q[2]])

        def project(self, points):
            matrix = np.asarray(self.matrix().data(), dtype=float).reshape(4, 4, order="F")
            points = np.asarray(points)
            q = np.column_stack((points, np.ones(len(points)))) @ matrix.T
            q = q[:, :2] / q[:, 3:4]
            return (q + [1, -1]) * [self.width()/2, -self.height()/2]

        def unproject(self, x, y, z):
            matrix = np.asarray(self.matrix().data(), dtype=float).reshape(4, 4, order="F")
            q = np.linalg.solve(matrix, [2*x/max(self.width(),1)-1, 1-2*y/max(self.height(),1), z, 1.0])
            return q[:3] / q[3]

        def pixel_scale(self, origin):
            p = self.project3(origin)
            return max(float(np.linalg.norm(self.unproject(p[0] + 1, p[1], p[2]) - origin)), 1e-12)

        def handles(self):
            """[(kind, axis, screen points)] of the editor's current mode."""
            return self.editor.gizmo_handles(self) if self.editor.bounds is not None else []

        def hit(self, point):
            candidates = []
            for kind, axis, points in self.handles():
                distance = min(distance_segment(point, a, b) for a, b in zip(points, points[1:]))
                if distance < 10:
                    candidates.append((distance, kind, axis, points))
            return min(candidates, key=lambda h: h[0]) if candidates else None

        def on_rotation_plane(self, point, origin, axis):
            a = self.unproject(*point, -1)
            ray = self.unproject(*point, 1) - a
            dot = np.dot(ray, axis)
            if abs(dot) < 1e-8 * np.linalg.norm(ray):
                return None
            v = a + ray * (np.dot(origin - a, axis) / dot) - origin
            return v / np.linalg.norm(v) if np.linalg.norm(v) > 1e-10 else None

        def ray(self, x, y):
            """A point under the screen position and the viewing direction there (towards the scene)."""
            a = self.unproject(x, y, -1)
            b = self.unproject(x, y, 1)
            return a, (b - a) / max(np.linalg.norm(b - a), 1e-30)

        def mousePressEvent(self, event):
            self.setFocus()
            e = self.editor
            if event.button() == Qt.LeftButton and e.bounds is not None and not event.modifiers() & Qt.AltModifier:
                point = np.array([event.position().x(), event.position().y()])
                if e.mode in e.stroke_modes:
                    self.stroke = (point, point)
                    self.update()
                    event.accept()
                    return
                hit = self.hit(point)
                if hit:
                    _, kind, axis, points = hit
                    self.drag_state = dict(start=point, kind=kind, axis=axis, screen=points, values=dict(e.values))
                    self.drag_state.update(e.start_drag(self, kind, axis, point) or {})
                    e.begin_edit()
                    event.accept()
                    return
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event):
            point = np.array([event.position().x(), event.position().y()])
            if self.stroke is not None:
                self.stroke = (self.stroke[0], point)
                self.update()
                event.accept()
                return
            s = self.drag_state
            if s:
                fine = .1 if event.modifiers() & Qt.ShiftModifier else 1.0
                snap = bool(event.modifiers() & Qt.ControlModifier)
                self.editor.drag_to(self, s, point, fine, snap)
                event.accept()
                return
            self.setCursor(Qt.CrossCursor if self.editor.mode in self.editor.stroke_modes else
                           Qt.SizeAllCursor if self.hit(point) else Qt.ArrowCursor)
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event):
            if self.stroke is not None:
                stroke, self.stroke = self.stroke, None
                try:
                    self.editor.finish_stroke(self, stroke[0], stroke[1])
                except ValueError as ex:
                    self.editor.message.setText(str(ex))
                self.update()
                event.accept()
                return
            if self.drag_state:
                self.drag_state = None
                self.update()
                event.accept()
                return
            super().mouseReleaseEvent(event)

        def mouseDoubleClickEvent(self, event):
            if self.editor.mode == "navigate":
                self.editor.fit()
            else:
                event.accept()

        def keyPressEvent(self, event):
            if event.key() == Qt.Key_Escape:
                if self.drag_state:
                    self.editor.commit(self.drag_state["values"], remember=False)
                self.drag_state, self.stroke = None, None
                self.editor.set_mode("navigate")
                self.update()
                return
            if event.key() == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
                self.editor.undo()
                return
            actions = self.editor.key_actions()
            if event.key() in actions:
                actions[event.key()]()
                return
            super().keyPressEvent(event)

        def paintGL(self):
            super().paintGL()
            self.overlay.update()

        def draw_guides(self, target):
            painter = QPainter(target)
            painter.setRenderHint(QPainter.Antialiasing)
            # Paint guides in screen space. They stay legible through the model and own no context-bound
            # GL resources; the surface clipping remains GPU based and the exact result comes from Preview.
            self.editor.paint_guides(self, painter)
            colors = [self.editor.theme.q(k) for k in ("danger", "success", "accent", "highlight")]
            for kind, axis, points in self.handles():
                color = colors[axis]
                painter.setPen(QPen(color, 2.5))
                painter.setBrush(color)
                painter.drawPolyline(QPolygonF([QPointF(*p) for p in points]))
                label = self.editor.handle_label(kind, axis)
                if kind == "rotate":
                    painter.drawText(QPointF(*(points[12] + [5, -5])), label)
                    continue
                end = points[-1]
                direction = end - points[0]
                length = np.linalg.norm(direction)
                if length > 1:
                    direction /= length
                    perp = np.array([-direction[1], direction[0]])
                    if kind == "scale":
                        painter.drawRect(QRectF(end[0] - 5, end[1] - 5, 10, 10))
                    else:
                        painter.drawPolygon(QPolygonF([QPointF(*end), QPointF(*(end - direction * 11 + perp * 5)),
                                                       QPointF(*(end - direction * 11 - perp * 5))]))
                painter.drawText(QPointF(*(end + [7, -6])), label)
            if self.stroke is not None:
                painter.setPen(QPen(self.editor.theme.q("danger"), 2.5))
                painter.setBrush(Qt.NoBrush)
                a, b = self.stroke
                if self.editor.stroke_shape == "rect":
                    painter.drawRect(QRectF(QPointF(*np.minimum(a, b)), QPointF(*np.maximum(a, b))))
                else:
                    painter.drawLine(QPointF(*a), QPointF(*b))
            painter.end()
else:                                   # pragma: no cover (no OpenGL bindings)
    GizmoCanvas = None


class GizmoEditor(QWidget):
    """The controls every placement editor shares. Subclasses set the texts and `modes`, add their own rows in
    `build_rows`, and implement frame/redraw and the gizmo hooks below."""
    title = "Edit"
    info_changed = Signal(str)
    dialog_title = "manual editor"
    flip_text = "Flip"
    flip_help = "Flip the operation. X"
    ghost_text = "Ghost"
    ghost_help = "Show a transparent reference mesh."
    preview_text = "Preview"
    preview_note = "Preview computes the full result"
    empty_message = "Add a model."
    modes = [("navigate", "View", "Orbit with left drag; right/middle drag pans; wheel zooms. V")]
    mode_tips = {"navigate": "Left drag: orbit · right/middle drag: pan · wheel: zoom · F: fit"}
    stroke_modes = ("draw",)
    stroke_shape = "line"
    mesh_count = 2

    def __init__(self, page):
        super().__init__()
        self.page, self.theme = page, page.theme
        self.values = dict(page.values())
        self.bounds, self.path, self.data = None, None, None
        self.mode, self.move_step = "navigate", 1.0
        self.history = []
        self.dialog, self.dialog_form = None, None
        self._syncing = False
        self.items = []
        self.mesh_items = []
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.editor = QFrame()
        self.layout_editor = QVBoxLayout(self.editor)
        self.layout_editor.setContentsMargins(8, 8, 8, 8)
        self.layout_editor.setSpacing(6)
        self.root.addWidget(self.editor)
        self._build_controls()
        self.canvas = GizmoCanvas(self) if gl else None
        if self.canvas:
            self.canvas.on_gl_failure = self._gl_failed
            self.layout_editor.addWidget(self.canvas, 1)
        else:
            self.layout_editor.addWidget(QLabel(f"{self.title} editor unavailable: {GL_ERROR}. "
                                                "Use numeric settings and Preview."))
        self.message = QLabel(self.empty_message)
        self.message.setWordWrap(True)
        self.message.setProperty("class", "hint")
        self.layout_editor.addWidget(self.message)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("class", "mono")
        self.layout_editor.addWidget(self.status)
        self.set_theme(self.theme)

    # ------------------------------------------------------------------ controls
    def _button(self, layout, text, fn, tip=""):
        b = QPushButton(text)
        b.setToolTip(tip)
        b.clicked.connect(fn)
        layout.addWidget(b)
        return b

    def _build_controls(self):
        row = QHBoxLayout()
        self.mode_buttons = {}
        self.group = QButtonGroup(self)
        for mode, text, tip in self.modes:
            button = self._button(row, text, lambda _=False, m=mode: self.set_mode(m), tip)
            button.setCheckable(True)
            self.group.addButton(button)
            self.mode_buttons[mode] = button
        self.mode_buttons["navigate"].setChecked(True)
        row.addStretch()
        self.expand_button = self._button(row, "Expand ↗", self.expand,
                                          f"Open a resizable, maximizable {self.dialog_title}.")
        self.layout_editor.addLayout(row)
        self.build_rows()
        row = QHBoxLayout()
        self.camera_combo = QComboBox()
        self.camera_combo.addItems(["Camera…", "Isometric", "Top (+Z)", "Bottom (−Z)", "Front (−Y)",
                                   "Back (+Y)", "Right (+X)", "Left (−X)"])
        self.camera_combo.activated.connect(self.camera_view)
        row.addWidget(self.camera_combo)
        self.ortho = QCheckBox("Ortho")
        self.ortho.setToolTip("Orthographic projection for accurate screen alignment; changes no setting.")
        self.ortho.toggled.connect(self._projection)
        row.addWidget(self.ortho)
        self.ghost = QCheckBox(self.ghost_text)
        self.ghost.setChecked(True)
        self.ghost.setToolTip(self.ghost_help)
        self.ghost.toggled.connect(self.redraw)
        row.addWidget(self.ghost)
        self.build_toggles(row)
        self.layout_editor.addLayout(row)

    def build_rows(self):
        """Rows of the subclass's own buttons, between the modes and the camera row."""

    def build_toggles(self, row):
        """Extra check boxes on the camera row."""

    def set_mode(self, mode):
        self.mode = mode
        self.mode_buttons[mode].setChecked(True)
        if self.canvas:
            self.canvas.stroke = None
            self.canvas.setCursor(Qt.CrossCursor if mode in self.stroke_modes else Qt.ArrowCursor)
            self.canvas.update()
        self.message.setText(self.mode_tips.get(mode, ""))

    def key_actions(self) -> dict:
        keys = {"navigate": Qt.Key_V, "draw": Qt.Key_D, "move": Qt.Key_G, "rotate": Qt.Key_R, "scale": Qt.Key_S}
        actions = {keys[m]: (lambda m=m: self.set_mode(m)) for m, _, _ in self.modes if m in keys}
        actions[Qt.Key_F] = self.fit
        actions[Qt.Key_X] = self.flip
        return actions

    # ------------------------------------------------------------------ values
    def begin_edit(self):
        self.history.append(dict(self.page.values()))
        self.history = self.history[-40:]

    def commit(self, changes, remember=True):
        if remember:
            self.begin_edit()
        self.page.form.set_values(changes)
        self.page._settings_changed()
        self.values_changed(self.page.values())

    def undo(self):
        if self.history:
            self.commit(self.history.pop(), remember=False)

    def values_changed(self, values):
        self.values = dict(values)
        if self.dialog_form is not None and not self._syncing:
            self._syncing = True
            self.dialog_form.set_values(values)
            self._syncing = False
        self.redraw()

    # ------------------------------------------------------------------ hooks
    def flip(self):
        raise NotImplementedError

    def frame(self):
        raise NotImplementedError

    def redraw(self, *_):
        raise NotImplementedError

    def make_shader(self, name):
        raise NotImplementedError

    def gizmo_handles(self, canvas) -> list:
        return []

    def handle_label(self, kind, axis) -> str:
        return "XYZN"[axis]

    def start_drag(self, canvas, kind, axis, point) -> dict:
        return {}

    def drag_to(self, canvas, state, point, fine, snap):
        pass

    def finish_stroke(self, canvas, start, end):
        pass

    def paint_guides(self, canvas, painter):
        pass

    def scene_bounds(self):
        return self.bounds

    def info_text(self):
        return "Select a model"

    # ------------------------------------------------------------------ shared drag maths
    def axis_handles(self, canvas, origin, axes, kind, scale=1.0):
        """Arrows from `origin` along each axis, HANDLE_PX long on screen."""
        size = canvas.pixel_scale(origin) * HANDLE_PX * scale
        center = canvas.project([origin])[0]
        return [(kind, i, np.vstack((center, canvas.project([origin + axis * size])[0])))
                for i, axis in enumerate(axes)]

    def ring_handles(self, canvas, origin):
        size = canvas.pixel_scale(origin) * HANDLE_PX
        t = np.linspace(0, 2 * np.pi, 81)
        out = []
        for i in range(3):
            a, b = np.eye(3)[(i + 1) % 3], np.eye(3)[(i + 2) % 3]
            ring = origin + size * (np.cos(t)[:, None] * a + np.sin(t)[:, None] * b)
            out.append(("rotate", i, canvas.project(ring)))
        return out

    @staticmethod
    def along(state, point, length_px=HANDLE_PX):
        """How far the mouse moved along a dragged arrow, in world units at the arrow's depth."""
        screen_delta = state["screen"][-1] - state["screen"][0]
        amount = np.dot(point - state["start"], screen_delta) / max(np.dot(screen_delta, screen_delta), 1e-8)
        return amount * state["scale"] * length_px

    @staticmethod
    def ring_angle(canvas, state, point, fine, snap):
        v1 = canvas.on_rotation_plane(point, state["origin"], state["vector"])
        v0 = state["v0"]
        delta = point - state["start"]
        angle = (math.degrees(math.atan2(np.dot(state["vector"], np.cross(v0, v1)), np.dot(v0, v1)))
                 if v0 is not None and v1 is not None else (delta[0] - delta[1]) * .6)
        angle *= fine
        return round(angle / 15) * 15 if snap else angle

    # ------------------------------------------------------------------ camera and input
    def camera_view(self, index):
        if not self.canvas or not index:
            return
        azimuth, elevation = [(-60, 22), (-90, 90), (-90, -90), (-90, 0), (90, 0), (0, 0), (180, 0)][index - 1]
        self.canvas.setCameraPosition(azimuth=azimuth, elevation=elevation)
        self.camera_combo.setCurrentIndex(0)

    def _projection(self, on):
        if self.canvas:
            self.canvas.orthographic = on
            self.canvas.update()

    def set_input(self, path, info):
        self.values = dict(self.page.values())
        if path == self.path and info is not None and self.data is not None:
            return
        self.path, self.data = path, None
        self.bounds = None
        self.clear_guides()
        self.history.clear()
        if info is not None and info.preview:
            self.data = next((v.get("mesh") for v in info.preview.get("views", []) if v.get("kind") == "mesh"), None)
        if self.canvas:
            for item in self.items + self.mesh_items:
                self.canvas.removeItem(item)
        self.items, self.mesh_items = [], []
        if self.data is None:
            self.status.setText("No model selected.")
            self.info_changed.emit(self.info_text())
            if self.canvas:
                self.canvas.update()
            return
        self.bounds = np.asarray(self.data.bounds, dtype=float)
        self.move_step = 10 ** math.floor(math.log10(max(float(np.ptp(self.bounds, axis=0).max()) / 100, 1e-9)))
        if self.canvas:
            self.rebuild_meshes()
            self.fit()
        self.redraw()
        self.set_mode(self.mode)

    def clear_guides(self):
        """Forget guide geometry computed for the previous input."""

    def _gl_failed(self, reason: str) -> None:
        self.canvas.hide()
        self.message.setText(f"{self.title} editor unavailable: {reason}. Use numeric settings and Preview.")

    def make_items(self) -> list:
        """The GL items drawn for the input: by default `mesh_count` copies of the model with the editor's shader."""
        return [gl.GLMeshItem(vertexes=np.asarray(self.data.vertices), faces=np.asarray(self.data.faces),
                              smooth=False, shader=self.make_shader(f"atlas-{self.title.lower()}-editor-{id(self)}-{i}"),
                              glOptions="opaque") for i in range(self.mesh_count)]

    def rebuild_meshes(self):
        if not self.canvas or self.canvas.gl_failure:
            return
        for item in self.mesh_items:
            self.canvas.removeItem(item)
        self.mesh_items = []
        if self.data is not None:
            for item in self.make_items():
                self.mesh_items.append(item)
                self.canvas.addItem(item)
            self.redraw()

    def style_item(self, item, color_key, alpha=1.0):
        color = self.theme.q(color_key)
        item.shader()["tint"] = [color.redF(), color.greenF(), color.blueF(), alpha]
        item.setGLOptions("opaque" if alpha == 1 else "translucent")
        item.setDepthValue(0 if alpha == 1 else 10)

    def fit(self):
        if self.canvas is not None and self.bounds is not None:
            bounds = self.scene_bounds()
            c = bounds.mean(axis=0)
            radius = max(float(np.linalg.norm(bounds[1] - bounds[0])) * .5, .001)
            self.canvas.opts["center"] = QVector3D(*map(float, c))
            self.canvas.setCameraPosition(distance=radius * 2.8)

    def set_theme(self, theme):
        self.theme = theme
        if self.canvas:
            self.canvas.setBackgroundColor(theme.q("bg-1"))
        self.redraw()

    def set_art(self, art):
        self.redraw()

    # ------------------------------------------------------------------ expanded editing
    def expand(self):
        if self.dialog is not None:
            self.dialog.raise_()
            return
        snapshot = dict(self.page.values())
        old_history = list(self.history)
        dialog = QDialog(self.page)
        dialog.setWindowTitle(f"{self.page.tool.name} · {self.dialog_title}")
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowMinMaxButtonsHint)
        dialog.resize(1280, 860)
        dialog.setMinimumSize(850, 600)
        outer = QVBoxLayout(dialog)
        content = QHBoxLayout()
        self.root.removeWidget(self.editor)
        content.addWidget(self.editor, 1)
        self.expand_button.hide()
        form = ParamForm(self.page.tool, self.theme)
        form.set_values(snapshot)
        self.dialog, self.dialog_form = dialog, form

        def edit():
            if not self._syncing:
                self.commit(form.values())
        form.changed.connect(edit)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(320)
        scroll.setMaximumWidth(430)
        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(8, 8, 8, 8)
        wrapper_layout.addWidget(form)
        wrapper_layout.addStretch(1)
        scroll.setWidget(wrapper)
        content.addWidget(scroll)
        outer.addLayout(content, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Use settings")
        preview = buttons.addButton(self.preview_text, QDialogButtonBox.ActionRole)

        def preview_result():
            dialog.accept()
            self.page.debounce.stop()
            self.page._apply_settings()
            self.page.preview_selected()
        preview.clicked.connect(preview_result)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)

        def finished(result):
            self.dialog_form = None
            self.root.addWidget(self.editor)
            self.expand_button.show()
            self.dialog = None
            if result != QDialog.Accepted:
                self.commit(snapshot, remember=False)
                self.history = old_history
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()
