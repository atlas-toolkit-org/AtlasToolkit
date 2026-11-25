"""Shared controls for contract-hosted plane viewers (Plane Cut, Mirror).

Subclasses supply status text and mesh styling. Drawing, XYZ/normal movement, rotation, projection, undo and
expanded editing have one implementation (the plane's here, the rest in gizmo.py). This widget knows no tool IDs.
"""
from __future__ import annotations

import numpy as np

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPen, QPolygonF
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout

from atlas_toolkit.geom.plane import normal_angles, plane, reference_origin, screen_plane
from atlas_toolkit.ui.keys import shown
from atlas_toolkit.ui.widgets.gizmo import HANDLE_PX, GizmoEditor, distance_segment, rotate_euler
from atlas_toolkit.ui.widgets.mesh_view import gl

_distance_segment = distance_segment        # the former name, for anything that imported it

if gl is not None:
    from pyqtgraph.opengl import shaders

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


class PlaneEditor(GizmoEditor):
    title = "Plane"
    dialog_title = "manual plane editor"
    flip_text = "Flip"
    flip_help = "Flip the plane operation. X"
    empty_message = "Add a model. Draw a line to define the plane, or use the numeric Plane settings."
    modes = [("navigate", "View", "Orbit with left drag; right/middle drag pans; wheel zooms. V"),
             ("draw", "Draw plane", "Drag a red line across the model. The plane extends through depth. D"),
             ("move", "Move", "Drag an XYZ arrow or N to slide along the plane normal. G"),
             ("rotate", "Rotate", "Drag a global XYZ rotation ring. R")]
    mode_tips = {"navigate": "Left drag: orbit · right/middle drag: pan · wheel: zoom · F: fit",
                 "draw": "Drag a red line across the view to define an infinite plane through depth. Line length "
                         "does not limit the plane.",
                 "move": "Drag X/Y/Z to translate, N to slide along the normal. Shift: fine · Ctrl: snap · Esc: "
                         "cancel gesture",
                 "rotate": "Drag an X/Y/Z ring. Shift: fine · Ctrl: snap to 15° · Esc: cancel gesture"}

    def __init__(self, page):
        self.grid_points = self.grid_corners = None
        super().__init__(page)

    def build_rows(self):
        row = QHBoxLayout()
        self.align = QComboBox()
        self.align.addItems(["Align plane…", "XY · normal Z", "YZ · normal X", "XZ · normal Y"])
        self.align.activated.connect(self.align_plane)
        row.addWidget(self.align)
        self._button(row, "Centre", self.center, "Move the plane to the model bounds centre; retain orientation.")
        self._button(row, self.flip_text, self.flip, self.flip_help)
        self.undo_button = self._button(row, "Undo", self.undo,
                                        shown("Undo the last editor gesture. Ctrl+Z in the viewport."))
        self.layout_editor.addLayout(row)

    def build_toggles(self, row):
        self.grid_toggle = QCheckBox("Plane grid")
        self.grid_toggle.setChecked(True)
        self.grid_toggle.toggled.connect(self.redraw)
        row.addWidget(self.grid_toggle)

    def make_shader(self, name):
        return _shader(name)

    def clear_guides(self):
        self.grid_points = self.grid_corners = None

    def frame(self):
        return plane(self.bounds, self.values)

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

    # ------------------------------------------------------------------ gizmo
    def gizmo_handles(self, canvas):
        origin, normal, _ = self.frame()
        if self.mode == "move":
            handles = self.axis_handles(canvas, origin, np.eye(3), "move")
            center = canvas.project([origin])[0]
            size = canvas.pixel_scale(origin) * HANDLE_PX
            end = canvas.project([origin + normal * size * 1.25])[0]
            if np.linalg.norm(end - center) < 25:
                end = center + [28, -50]
            handles.append(("normal", 3, np.vstack((center, end))))
            return handles
        if self.mode == "rotate":
            return self.ring_handles(canvas, origin)
        return []

    def start_drag(self, canvas, kind, axis, point):
        origin, normal, _ = self.frame()
        vector = normal if kind == "normal" else np.eye(3)[axis]
        return dict(origin=origin, vector=vector, scale=canvas.pixel_scale(origin),
                    v0=canvas.on_rotation_plane(point, origin, vector) if kind == "rotate" else None)

    def drag_to(self, canvas, s, point, fine, snap):
        p = s["values"]
        if s["kind"] == "rotate":
            angle = self.ring_angle(canvas, s, point, fine, snap)
            self.commit({"rotation": rotate_euler(p["rotation"], s["vector"], angle)}, remember=False)
            return
        amount = self.along(s, point, 95 if s["kind"] == "normal" else HANDLE_PX) * fine
        if snap:
            amount = round(amount / self.move_step) * self.move_step
        if s["kind"] == "normal":
            self.commit({"offset": p["offset"] + amount}, remember=False)
        else:
            position = np.asarray(p["position"]) + s["vector"] * amount
            self.commit({"position": tuple(position)}, remember=False)

    def finish_stroke(self, canvas, start, end):
        origin, normal = screen_plane(start, end, canvas.unproject, self.bounds.mean(axis=0))
        self.set_world_plane(origin, normal)
        self.set_mode("move")

    def paint_guides(self, canvas, painter):
        if self.grid_points is None:
            return
        color = self.theme.q("accent")
        color.setAlphaF(.10)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QPointF(*p) for p in canvas.project(self.grid_corners)]))
        color = self.theme.q("highlight")
        color.setAlphaF(.28)
        painter.setPen(QPen(color, 1))
        points = canvas.project(self.grid_points)
        for a, b in zip(points[::2], points[1::2]):
            painter.drawLine(QPointF(*a), QPointF(*b))

    # ------------------------------------------------------------------ scene
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
                                            for a, b in [(-1, -1), (1, -1), (1, 1), (-1, 1)]])
            positions = []
            for t in np.linspace(-radius, radius, 17):
                positions.extend((origin + t*u - radius*v, origin + t*u + radius*v,
                                  origin + t*v - radius*u, origin + t*v + radius*u))
            self.grid_points = np.asarray(positions)
        self.canvas.update()

    def describe_scene(self):
        raise NotImplementedError

    def update_scene(self, origin, normal, span):
        raise NotImplementedError

    def info_text(self):
        return "Draw · move · rotate" if self.bounds is not None else "Select a model"
