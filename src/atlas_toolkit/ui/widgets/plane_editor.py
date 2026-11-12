"""Shared controls for contract-hosted plane viewers.

Subclasses supply status text and mesh styling. Drawing, XYZ/normal movement, rotation, projection, undo and
expanded editing have one implementation. This widget knows no tool IDs.
"""
from __future__ import annotations

import math
import warnings
import numpy as np
from scipy.spatial.transform import Rotation

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter, QPen, QPolygonF, QVector3D
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame,
                              QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from atlas_toolkit.ui.widgets.gl_guard import GuardedGL
from atlas_toolkit.ui.keys import shown
from atlas_toolkit.ui.widgets.gl_nav import GestureNavigation
from atlas_toolkit.ui.widgets.mesh_view import gl, GL_ERROR
from atlas_toolkit.ui.widgets.param_form import ParamForm
from atlas_toolkit.geom.plane import normal_angles, plane, reference_origin, screen_plane


def _distance_segment(point, a, b):
    delta = b - a
    t = np.clip(np.dot(point - a, delta) / max(np.dot(delta, delta), 1e-12), 0, 1)
    return float(np.linalg.norm(point - a - t * delta))


if gl is not None:
    from pyqtgraph.opengl import shaders

    class _Guide(QWidget):
        """A transparent Qt child keeps controls visible in native GL rendering and Qt screenshots alike."""
        def __init__(self, canvas):
            super().__init__(canvas)
            self.canvas = canvas
            self.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.setAttribute(Qt.WA_NoSystemBackground)

        def paintEvent(self, event):
            self.canvas.draw_guides(self)

    def _shader(name):
        return shaders.ShaderProgram(name, [shaders.VertexShader("""
            uniform mat4 u_mvp;
            uniform mat3 u_normal;
            uniform float reflection[4];
            uniform float reflect_mesh[1];
            attribute vec4 a_position;
            attribute vec3 a_normal;
            varying vec3 pos;
            varying vec3 norm;
            void main() {
                vec3 p = a_position.xyz;
                vec3 n = a_normal;
                if (reflect_mesh[0] > .5) {
                    vec3 axis = vec3(reflection[0],reflection[1],reflection[2]);
                    p -= 2.0 * axis * (dot(axis,p) + reflection[3]);
                    n -= 2.0 * axis * dot(axis,n);
                }
                pos = p;
                norm = normalize(u_normal * n);
                gl_Position = u_mvp * vec4(p,1.0);
            }
        """), shaders.FragmentShader("""
            #ifdef GL_ES
            precision highp float;
            #endif
            uniform float cut[4];
            uniform float tint[4];
            uniform float band[1];
            uniform float clip_enabled[1];
            varying vec3 pos;
            varying vec3 norm;
            void main() {
                float d = dot(vec3(cut[0],cut[1],cut[2]), pos) + cut[3];
                if (clip_enabled[0] > .5 && d < 0.0) discard;
                vec3 n = normalize(norm);
                float light = .35 + .5 * abs(dot(n, normalize(vec3(.55,.45,.7))))
                                  + .15 * abs(dot(n, normalize(vec3(-.6,-.2,.45))));
                vec3 rgb = vec3(tint[0],tint[1],tint[2]) * light;
                if (d < band[0]) rgb = mix(rgb, vec3(tint[0],tint[1],tint[2]), .8);
                gl_FragColor = vec4(rgb, tint[3]);
            }
        """)])


    class _Canvas(GestureNavigation, GuardedGL, gl.GLViewWidget):
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
            e = self.editor
            if e.bounds is None:
                return []
            origin, normal, _ = e.frame()
            size = self.pixel_scale(origin) * 76
            center = self.project([origin])[0]
            handles = []
            if e.mode == "move":
                for i, axis in enumerate(np.eye(3)):
                    end = self.project([origin + axis * size])[0]
                    handles.append(("move", i, np.vstack((center, end))))
                end = self.project([origin + normal * size * 1.25])[0]
                if np.linalg.norm(end - center) < 25:
                    end = center + [28, -50]
                handles.append(("normal", 3, np.vstack((center, end))))
            elif e.mode == "rotate":
                t = np.linspace(0, 2 * np.pi, 81)
                for i in range(3):
                    a, b = np.eye(3)[(i + 1) % 3], np.eye(3)[(i + 2) % 3]
                    ring = origin + size * (np.cos(t)[:, None] * a + np.sin(t)[:, None] * b)
                    handles.append(("rotate", i, self.project(ring)))
            return handles

        def hit(self, point):
            candidates = []
            for kind, axis, points in self.handles():
                distance = min(_distance_segment(point, a, b) for a, b in zip(points, points[1:]))
                if distance < 10:
                    candidates.append((distance, kind, axis, points))
            return min(candidates, key=lambda h: h[0]) if candidates else None

        def _on_rotation_plane(self, point, origin, axis):
            a = self.unproject(*point, -1)
            ray = self.unproject(*point, 1) - a
            dot = np.dot(ray, axis)
            if abs(dot) < 1e-8 * np.linalg.norm(ray):
                return None
            v = a + ray * (np.dot(origin - a, axis) / dot) - origin
            return v / np.linalg.norm(v) if np.linalg.norm(v) > 1e-10 else None

        def mousePressEvent(self, event):
            self.setFocus()
            e = self.editor
            if event.button() == Qt.LeftButton and e.bounds is not None and not event.modifiers() & Qt.AltModifier:
                point = np.array([event.position().x(), event.position().y()])
                if e.mode == "draw":
                    self.stroke = (point, point)
                    self.update()
                    event.accept()
                    return
                hit = self.hit(point)
                if hit:
                    _, kind, axis, points = hit
                    origin, normal, _ = e.frame()
                    vector = normal if kind == "normal" else np.eye(3)[axis]
                    self.drag_state = dict(start=point, kind=kind, axis=axis, screen=points,
                                           values=dict(e.values), origin=origin, vector=vector,
                                           scale=self.pixel_scale(origin),
                                           v0=self._on_rotation_plane(point, origin, vector) if kind == "rotate" else None)
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
                delta = point - s["start"]
                p = s["values"]
                fine = .1 if event.modifiers() & Qt.ShiftModifier else 1.0
                snap = bool(event.modifiers() & Qt.ControlModifier)
                if s["kind"] == "rotate":
                    v1 = self._on_rotation_plane(point, s["origin"], s["vector"])
                    v0 = s["v0"]
                    angle = (math.degrees(math.atan2(np.dot(s["vector"], np.cross(v0, v1)), np.dot(v0, v1)))
                             if v0 is not None and v1 is not None else (delta[0] - delta[1]) * .6)
                    angle *= fine
                    if snap:
                        angle = round(angle / 15) * 15
                    rot = Rotation.from_rotvec(s["vector"] * math.radians(angle)) * Rotation.from_euler(
                        "xyz", p["rotation"], degrees=True)
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        angles = rot.as_euler("xyz", degrees=True)
                    self.editor.commit({"rotation": tuple(angles)}, remember=False)
                else:
                    screen_delta = s["screen"][-1] - s["screen"][0]
                    amount = np.dot(delta, screen_delta) / max(np.dot(screen_delta, screen_delta), 1e-8)
                    amount *= s["scale"] * (95 if s["kind"] == "normal" else 76) * fine
                    if snap:
                        amount = round(amount / self.editor.move_step) * self.editor.move_step
                    if s["kind"] == "normal":
                        self.editor.commit({"offset": p["offset"] + amount}, remember=False)
                    else:
                        position = np.asarray(p["position"]) + s["vector"] * amount
                        self.editor.commit({"position": tuple(position)}, remember=False)
                event.accept()
                return
            self.setCursor(Qt.CrossCursor if self.editor.mode == "draw" else
                           Qt.SizeAllCursor if self.hit(point) else Qt.ArrowCursor)
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event):
            if self.stroke is not None:
                stroke, self.stroke = self.stroke, None
                try:
                    origin, normal = screen_plane(stroke[0], stroke[1], self.unproject, self.editor.bounds.mean(axis=0))
                    self.editor.set_world_plane(origin, normal)
                    self.editor.set_mode("move")
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
            actions = {Qt.Key_F: self.editor.fit, Qt.Key_D: lambda: self.editor.set_mode("draw"),
                       Qt.Key_G: lambda: self.editor.set_mode("move"), Qt.Key_R: lambda: self.editor.set_mode("rotate"),
                       Qt.Key_V: lambda: self.editor.set_mode("navigate"), Qt.Key_X: self.editor.flip}
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
            # Paint the guide in screen space. It stays legible through the model and owns no context-bound
            # GL resources; the surface clipping remains GPU based and the final caps come from Preview.
            if self.editor.grid_points is not None:
                color = self.editor.theme.q("accent")
                color.setAlphaF(.10)
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                painter.drawPolygon(QPolygonF([QPointF(*p) for p in self.project(self.editor.grid_corners)]))
                color = self.editor.theme.q("highlight")
                color.setAlphaF(.28)
                painter.setPen(QPen(color, 1))
                points = self.project(self.editor.grid_points)
                for a, b in zip(points[::2], points[1::2]):
                    painter.drawLine(QPointF(*a), QPointF(*b))
            colors = [self.editor.theme.q(k) for k in ("danger", "success", "accent", "highlight")]
            for kind, axis, points in self.handles():
                color = colors[axis]
                painter.setPen(QPen(color, 2.5))
                painter.setBrush(color)
                painter.drawPolyline(QPolygonF([QPointF(*p) for p in points]))
                if kind != "rotate":
                    end = points[-1]
                    direction = end - points[0]
                    length = np.linalg.norm(direction)
                    if length > 1:
                        direction /= length
                        perp = np.array([-direction[1], direction[0]])
                        painter.drawPolygon(QPolygonF([QPointF(*end), QPointF(*(end - direction * 11 + perp * 5)),
                                                       QPointF(*(end - direction * 11 - perp * 5))]))
                    painter.drawText(QPointF(*(end + [7, -6])), "XYZN"[axis])
                else:
                    painter.drawText(QPointF(*(points[12] + [5, -5])), "XYZ"[axis])
            if self.stroke is not None:
                painter.setPen(QPen(self.editor.theme.q("danger"), 2.5))
                painter.drawLine(QPointF(*self.stroke[0]), QPointF(*self.stroke[1]))
            painter.end()


class PlaneEditor(QWidget):
    title = "Plane"
    info_changed = Signal(str)
    flip_text = "Flip"
    flip_help = "Flip the plane operation. X"
    ghost_text = "Ghost"
    ghost_help = "Show a transparent reference mesh."
    preview_text = "Preview"
    preview_note = "Preview computes the full result"

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
        self.grid_points = self.grid_corners = None
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.editor = QFrame()
        self.layout_editor = QVBoxLayout(self.editor)
        self.layout_editor.setContentsMargins(8, 8, 8, 8)
        self.layout_editor.setSpacing(6)
        self.root.addWidget(self.editor)
        self._build_controls()
        self.canvas = _Canvas(self) if gl else None
        if self.canvas:
            self.canvas.on_gl_failure = self._gl_failed
            self.layout_editor.addWidget(self.canvas, 1)
        else:
            self.layout_editor.addWidget(QLabel(f"Plane editor unavailable: {GL_ERROR}. Use numeric settings and Preview."))
        self.message = QLabel("Add a model. Draw a line to define the plane, or use the numeric Plane settings.")
        self.message.setWordWrap(True)
        self.message.setProperty("class", "hint")
        self.layout_editor.addWidget(self.message)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("class", "mono")
        self.layout_editor.addWidget(self.status)
        self.set_theme(self.theme)

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
        for mode, text, tip in [("navigate", "View", "Orbit with left drag; right/middle drag pans; wheel zooms. V"),
                                ("draw", "Draw plane", "Drag a red line across the model. The plane extends through depth. D"),
                                ("move", "Move", "Drag an XYZ arrow or N to slide along the plane normal. G"),
                                ("rotate", "Rotate", "Drag a global XYZ rotation ring. R")]:
            button = self._button(row, text, lambda _=False, m=mode: self.set_mode(m), tip)
            button.setCheckable(True)
            self.group.addButton(button)
            self.mode_buttons[mode] = button
        self.mode_buttons["navigate"].setChecked(True)
        row.addStretch()
        self.expand_button = self._button(row, "Expand ↗", self.expand, "Open a resizable, maximizable plane editor.")
        self.layout_editor.addLayout(row)
        row = QHBoxLayout()
        self.align = QComboBox()
        self.align.addItems(["Align plane…", "XY · normal Z", "YZ · normal X", "XZ · normal Y"])
        self.align.activated.connect(self.align_plane)
        row.addWidget(self.align)
        self._button(row, "Centre", self.center, "Move the plane to the model bounds centre; retain orientation.")
        self._button(row, self.flip_text, self.flip, self.flip_help)
        self.undo_button = self._button(row, "Undo", self.undo, shown("Undo the last editor gesture. Ctrl+Z in the viewport."))
        self.layout_editor.addLayout(row)
        row = QHBoxLayout()
        self.camera_combo = QComboBox()
        self.camera_combo.addItems(["Camera…", "Isometric", "Top (+Z)", "Bottom (−Z)", "Front (−Y)",
                                   "Back (+Y)", "Right (+X)", "Left (−X)"])
        self.camera_combo.activated.connect(self.camera_view)
        row.addWidget(self.camera_combo)
        self.ortho = QCheckBox("Ortho")
        self.ortho.setToolTip("Orthographic projection for accurate screen alignment; does not change the plane.")
        self.ortho.toggled.connect(self._projection)
        row.addWidget(self.ortho)
        self.ghost = QCheckBox(self.ghost_text)
        self.ghost.setChecked(True)
        self.ghost.setToolTip(self.ghost_help)
        self.ghost.toggled.connect(self.redraw)
        row.addWidget(self.ghost)
        self.grid_toggle = QCheckBox("Plane grid")
        self.grid_toggle.setChecked(True)
        self.grid_toggle.toggled.connect(self.redraw)
        row.addWidget(self.grid_toggle)
        self.layout_editor.addLayout(row)

    def set_mode(self, mode):
        self.mode = mode
        self.mode_buttons[mode].setChecked(True)
        if self.canvas:
            self.canvas.stroke = None
            self.canvas.setCursor(Qt.CrossCursor if mode == "draw" else Qt.ArrowCursor)
            self.canvas.update()
        tips = {"navigate": "Left drag: orbit · right/middle drag: pan · wheel: zoom · F: fit",
                "draw": "Drag a red line across the view to define an infinite plane through depth. Line length does not limit the plane.",
                "move": "Drag X/Y/Z to translate, N to slide along the normal. Shift: fine · Ctrl: snap · Esc: cancel gesture",
                "rotate": "Drag an X/Y/Z ring. Shift: fine · Ctrl: snap to 15° · Esc: cancel gesture"}
        self.message.setText(tips[mode])

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

    def flip(self):
        raise NotImplementedError

    def frame(self):
        return plane(self.bounds, self.values)

    def scene_bounds(self):
        return self.bounds

    def center(self):
        if self.bounds is not None:
            position = tuple(self.bounds.mean(axis=0) - reference_origin(self.bounds, self.values["reference"]))
            self.commit({"position": position, "offset": 0.0})

    def align_plane(self, index):
        if index:
            self.commit({"rotation": ((0., 0., 0.), (0., 90., 0.), (-90., 0., 0.))[index - 1]})
            self.align.setCurrentIndex(0)

    def set_world_plane(self, origin, normal):
        position = np.asarray(origin).copy()
        position -= reference_origin(self.bounds, self.values["reference"])
        self.commit({"position": tuple(position), "rotation": tuple(normal_angles(normal)), "offset": 0.0})

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
        self.grid_points = self.grid_corners = None
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

    def _gl_failed(self, reason: str) -> None:
        self.canvas.hide()
        self.message.setText(f"Plane editor unavailable: {reason}. Use numeric settings and Preview.")

    def rebuild_meshes(self):
        if not self.canvas or self.canvas.gl_failure:
            return
        for item in self.mesh_items:
            self.canvas.removeItem(item)
        self.mesh_items = []
        if self.data is not None:
            for index in range(2):
                shader = _shader(f"atlas-plane-editor-{id(self)}-{index}")
                item = gl.GLMeshItem(vertexes=np.asarray(self.data.vertices), faces=np.asarray(self.data.faces),
                                     smooth=False, shader=shader, glOptions="opaque")
                self.mesh_items.append(item)
                self.canvas.addItem(item)
            self.redraw()

    def values_changed(self, values):
        self.values = dict(values)
        if self.dialog_form is not None and not self._syncing:
            self._syncing = True
            self.dialog_form.set_values(values)
            self._syncing = False
        self.redraw()

    def redraw(self, *_):
        if self.bounds is None:
            return
        origin, normal, rotation = self.frame()
        self.status.setText("Plane at " + ", ".join(f"{v:.4g}" for v in origin) + " units   ·   Normal " +
                            ", ".join(f"{v:.3f}" for v in normal) +
                            "\n" + self.describe_scene() + " · " + self.preview_note)
        self.info_changed.emit(self.info_text())
        if not self.canvas:
            return
        span = max(float(np.linalg.norm(self.bounds[1] - self.bounds[0])), 1e-8)
        self.update_scene(origin, normal, span)
        self.grid_points = self.grid_corners = None
        if self.grid_toggle.isChecked():
            u, v = rotation[:, 0], rotation[:, 1]
            radius = span * .65
            self.grid_corners = np.asarray([origin + radius * (a * u + b * v)
                                            for a, b in [(-1,-1),(1,-1),(1,1),(-1,1)]])
            positions = []
            for t in np.linspace(-radius, radius, 17):
                positions.extend((origin + t*u-radius*v, origin+t*u+radius*v,
                                  origin+t*v-radius*u, origin+t*v+radius*u))
            self.grid_points = np.asarray(positions)
        self.canvas.update()

    def describe_scene(self):
        raise NotImplementedError

    def update_scene(self, origin, normal, span):
        raise NotImplementedError

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

    def info_text(self):
        return "Draw · move · rotate" if self.bounds is not None else "Select a model"

    def expand(self):
        if self.dialog is not None:
            self.dialog.raise_()
            return
        snapshot = dict(self.page.values())
        old_history = list(self.history)
        dialog = QDialog(self.page)
        dialog.setWindowTitle(f"{self.page.tool.name} · manual plane editor")
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
        def preview_cut():
            dialog.accept()
            self.page.debounce.stop()
            self.page._apply_settings()
            self.page.preview_selected()
        preview.clicked.connect(preview_cut)
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
