"""Shared controls for viewers that place a box, sphere, cylinder or pyramid on a model (geom/region.py).

The model is clipped live on the GPU by the shape's inside test, so moving, turning and resizing it stay instant
whatever the model's size (the editor draws the input's light preview copy); Preview computes the exact result.
It reads and writes these settings: `shape`, `operation` (intersect, subtract, union, xor), `reference`,
`position`, `rotation`, `size_unit` and `size`. This widget knows no tool IDs.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPen
from PySide6.QtWidgets import QHBoxLayout

from atlas_toolkit.geom import region as R
from atlas_toolkit.geom.plane import reference_origin
from atlas_toolkit.ui.keys import shown
from atlas_toolkit.ui.widgets.gizmo import GizmoEditor, rotate_euler
from atlas_toolkit.ui.widgets.mesh_view import gl

if gl is not None:
    from pyqtgraph.opengl import shaders

    def _shader(name):
        return shaders.ShaderProgram(name, [shaders.VertexShader("""
            uniform mat4 u_mvp;
            uniform mat3 u_normal;
            attribute vec4 a_position;
            attribute vec3 a_normal;
            varying vec3 pos;
            varying vec3 norm;
            void main() {
                pos = a_position.xyz;
                norm = normalize(u_normal * a_normal);
                gl_Position = u_mvp * a_position;
            }
        """), shaders.FragmentShader("""
            #ifdef GL_ES
            precision highp float;
            #endif
            uniform float frame[12];   // world -> unit shape: three rows of (x, y, z, offset)
            uniform float shape[3];    // kind (0 box, 1 sphere, 2 cylinder, 3 pyramid), keep (1 in, -1 out, 0 all), band
            uniform float tint[4];
            varying vec3 pos;
            varying vec3 norm;
            void main() {
                vec3 q = vec3(dot(vec3(frame[0], frame[1], frame[2]), pos) + frame[3],
                              dot(vec3(frame[4], frame[5], frame[6]), pos) + frame[7],
                              dot(vec3(frame[8], frame[9], frame[10]), pos) + frame[11]);
                float d;
                if (shape[0] < .5) d = max(max(abs(q.x), abs(q.y)), abs(q.z)) - .5;
                else if (shape[0] < 1.5) d = length(q) - .5;
                else if (shape[0] < 2.5) d = max(length(q.xy) - .5, abs(q.z) - .5);
                else d = max(-q.z - .5, max(2.0 * abs(q.x) + q.z, 2.0 * abs(q.y) + q.z) - .5);
                if (shape[1] > .5 && d > 0.0) discard;
                if (shape[1] < -.5 && d < 0.0) discard;
                vec3 n = normalize(norm);
                float light = .35 + .5 * abs(dot(n, normalize(vec3(.55,.45,.7))))
                                  + .15 * abs(dot(n, normalize(vec3(-.6,-.2,.45))));
                vec3 rgb = vec3(tint[0],tint[1],tint[2]) * light;
                if (shape[1] != 0.0 && abs(d) < shape[2]) rgb = mix(rgb, vec3(tint[0],tint[1],tint[2]), .8);
                gl_FragColor = vec4(rgb, tint[3]);
            }
        """)])


def unit_mesh(kind: str, segments: int = 48):
    """The unit shape (geom/region.py) as (vertices, faces) for drawing."""
    import trimesh
    if kind == "box":
        m = trimesh.creation.box((1.0, 1.0, 1.0))
    elif kind == "sphere":
        m = trimesh.creation.icosphere(subdivisions=3, radius=.5)
    elif kind == "cylinder":
        m = trimesh.creation.cylinder(radius=.5, height=1.0, sections=segments)
    else:
        m = trimesh.Trimesh(np.array([[-.5, -.5, -.5], [.5, -.5, -.5], [.5, .5, -.5], [-.5, .5, -.5], [0, 0, .5]]),
                            np.array([[0, 2, 1], [0, 3, 2], [0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]]),
                            process=False)
    return np.asarray(m.vertices, np.float32), np.asarray(m.faces, np.int32)


class ShapeEditor(GizmoEditor):
    title = "Shape"
    dialog_title = "shape editor"
    flip_text = "Inside ⇄ outside"
    flip_help = "Swap Keep inside and Cut away. X"
    ghost_text = "Ghost"
    ghost_help = "Show what the operation removes as a transparent ghost."
    preview_text = "Preview crop"
    preview_note = "Preview computes the closed cut"
    empty_message = "Add a model. Drag a box over it (Draw), or move, turn and size the shape with its handles."
    modes = [("navigate", "View", "Orbit with left drag; right/middle drag pans; wheel zooms. V"),
             ("draw", "Draw", "Drag a rectangle over the view: the shape fills it, through the model's depth. D"),
             ("move", "Move", "Drag an X/Y/Z arrow to move the shape. G"),
             ("rotate", "Rotate", "Drag an X/Y/Z ring to turn the shape. R"),
             ("scale", "Scale", "Drag a square handle to size the shape along its own axis, or the white one "
                                "to size it evenly. S")]
    mode_tips = {"navigate": "Left drag: orbit · right/middle drag: pan · wheel: zoom · F: fit",
                 "draw": "Drag a rectangle over the part to keep. The shape fills it and spans the model's depth "
                         "seen from here.",
                 "move": "Drag X/Y/Z to move the shape. Shift: fine · Ctrl: snap · Esc: cancel gesture",
                 "rotate": "Drag an X/Y/Z ring. Shift: fine · Ctrl: snap to 15° · Esc: cancel gesture",
                 "scale": "Drag a square to size the shape along its own axis; the white one sizes it evenly. "
                          "Shift: fine · Ctrl: snap · Esc: cancel gesture"}
    stroke_shape = "rect"
    mesh_count = 2

    def __init__(self, page):
        self.shape_item = None
        self._shape_kind = None
        super().__init__(page)

    # ------------------------------------------------------------------ controls
    def build_rows(self):
        row = QHBoxLayout()
        self._button(row, "Centre", self.center, "Move the shape to the middle of the model; keep its size and turn.")
        self._button(row, "Fit to model", self.fit_to_model, "Turn the shape square and size it to the model's bounds.")
        self._button(row, "Reset turn", self.reset_rotation, "Turn the shape back to the model's axes.")
        self._button(row, self.flip_text, self.flip, self.flip_help)
        self.undo_button = self._button(row, "Undo", self.undo,
                                        shown("Undo the last editor gesture. Ctrl+Z in the viewport."))
        self.layout_editor.addLayout(row)

    def make_shader(self, name):
        return _shader(name)

    # ------------------------------------------------------------------ values
    def frame(self):
        return R.frame(self.bounds, self.values)

    def model_size(self):
        return R.model_size(self.bounds)

    def to_setting(self, sizes):
        """World sizes in the unit the settings use."""
        sizes = np.maximum(np.asarray(sizes, float), 1e-9)
        if self.values.get("size_unit") == "percent":
            sizes = sizes / self.model_size() * 100.0
        return tuple(float(s) for s in sizes)

    def set_world(self, centre, rotation_matrix, sizes):
        """Commit a placement given in world terms."""
        with np.errstate(all="ignore"):
            angles = Rotation.from_matrix(rotation_matrix).as_euler("xyz", degrees=True)
        position = np.asarray(centre, float) - reference_origin(self.bounds, self.values.get("reference", "center"))
        self.commit({"position": tuple(float(x) for x in position), "rotation": tuple(float(a) for a in angles),
                     "size": self.to_setting(sizes)})

    def center(self):
        if self.bounds is not None:
            position = self.bounds.mean(axis=0) - reference_origin(self.bounds, self.values.get("reference", "center"))
            self.commit({"position": tuple(float(x) for x in position)})

    def fit_to_model(self):
        if self.bounds is not None:
            self.set_world(self.bounds.mean(axis=0), np.eye(3), np.ptp(self.bounds, axis=0) * 1.02)

    def reset_rotation(self):
        self.commit({"rotation": (0.0, 0.0, 0.0)})

    def flip(self):
        swap = {"intersect": "subtract", "subtract": "intersect"}
        op = self.values.get("operation")
        if op in swap:
            self.commit({"operation": swap[op]})

    # ------------------------------------------------------------------ gizmo
    def gizmo_handles(self, canvas):
        m = self.frame()
        origin = m[:3, 3]
        if self.mode == "move":
            return self.axis_handles(canvas, origin, np.eye(3), "move")
        if self.mode == "rotate":
            return self.ring_handles(canvas, origin)
        if self.mode == "scale":
            axes = m[:3, :3] / np.maximum(np.linalg.norm(m[:3, :3], axis=0), 1e-30)
            handles = self.axis_handles(canvas, origin, axes.T, "scale")
            center = canvas.project([origin])[0]
            handles.append(("scale", 3, np.vstack((center, center + [36, 36]))))
            return handles
        return []

    def handle_label(self, kind, axis):
        if kind == "scale":
            return ("W", "D", "H", "")[axis]
        return "XYZ"[axis]

    def start_drag(self, canvas, kind, axis, point):
        m = self.frame()
        origin = m[:3, 3]
        vector = np.eye(3)[axis] if axis < 3 else np.zeros(3)
        return dict(origin=origin, vector=vector, scale=canvas.pixel_scale(origin),
                    sizes=np.linalg.norm(m[:3, :3], axis=0),
                    v0=canvas.on_rotation_plane(point, origin, vector) if kind == "rotate" else None)

    def drag_to(self, canvas, s, point, fine, snap):
        p = s["values"]
        if s["kind"] == "rotate":
            angle = self.ring_angle(canvas, s, point, fine, snap)
            self.commit({"rotation": rotate_euler(p["rotation"], s["vector"], angle)}, remember=False)
        elif s["kind"] == "move":
            amount = self.along(s, point) * fine
            if snap:
                amount = round(amount / self.move_step) * self.move_step
            self.commit({"position": tuple(np.asarray(p["position"]) + s["vector"] * amount)}, remember=False)
        elif s["kind"] == "scale":
            # one handle length dragged outward doubles the size; inward halves it
            screen = s["screen"][-1] - s["screen"][0]
            t = np.dot(point - s["start"], screen) / max(np.dot(screen, screen), 1e-8)
            factor = float(2.0 ** (t * fine))
            sizes = s["sizes"].copy()
            if s["axis"] < 3:
                sizes[s["axis"]] *= factor
            else:
                sizes *= factor
            values = np.asarray(self.to_setting(sizes))
            if snap:
                step = 5.0 if self.values.get("size_unit") == "percent" else self.move_step * 10
                values = np.maximum(np.round(values / step) * step, step)
            self.commit({"size": tuple(float(v) for v in values)}, remember=False)

    def finish_stroke(self, canvas, start, end):
        """A screen rectangle: the shape faces the view, fills the rectangle and spans the model's depth."""
        if np.min(np.abs(np.asarray(end) - start)) < 6:
            raise ValueError("Drag a rectangle at least a few pixels wide and high.")
        mid = (np.asarray(start) + end) / 2
        _, forward = canvas.ray(*mid)
        right = canvas.unproject(mid[0] + 1, mid[1], 0) - canvas.unproject(mid[0], mid[1], 0)
        up = canvas.unproject(mid[0], mid[1] - 1, 0) - canvas.unproject(mid[0], mid[1], 0)
        x = right / np.linalg.norm(right)
        z = -forward
        y = np.cross(z, x)
        y /= np.linalg.norm(y)
        x = np.cross(y, z)
        if np.dot(y, up) < 0:              # keep +Y up on screen
            y, x = -y, -x
        rot = np.column_stack((x, y, z))
        if np.linalg.det(rot) < 0:
            rot[:, 0] *= -1
        c = self.bounds.mean(axis=0)

        def on_plane(px, py):              # where the ray under (px, py) meets the plane through the model's centre
            a, d = canvas.ray(px, py)
            denom = np.dot(d, z)
            if abs(denom) < 1e-12:
                raise ValueError("The view cannot place a shape here. Turn the view and draw again.")
            return a + d * (np.dot(c - a, z) / denom)
        p0, p1 = on_plane(*start), on_plane(*end)
        corners = np.array([[a, b, cc] for a in self.bounds[:, 0] for b in self.bounds[:, 1] for cc in self.bounds[:, 2]])
        depth = corners @ z
        centre = (p0 + p1) / 2
        centre += z * ((depth.max() + depth.min()) / 2 - np.dot(centre, z))
        sizes = np.array([abs(np.dot(p1 - p0, x)), abs(np.dot(p1 - p0, y)), (depth.max() - depth.min()) * 1.02])
        self.set_world(centre, rot, sizes)
        self.set_mode("move")

    def paint_guides(self, canvas, painter):
        m = self.frame()
        lines = R.to_world(R.outline(self.values.get("shape", "box")).reshape(-1, 3), m)
        points = canvas.project(lines)
        color = self.theme.q("accent")
        painter.setPen(QPen(color, 1.6))
        for a, b in zip(points[::2], points[1::2]):
            painter.drawLine(QPointF(*a), QPointF(*b))

    # ------------------------------------------------------------------ scene
    def rebuild_meshes(self):
        if self.canvas and self.shape_item is not None:
            self.canvas.removeItem(self.shape_item)
        self.shape_item, self._shape_kind = None, None
        super().rebuild_meshes()

    def _sync_shape_item(self):
        """The shape as a solid (for Add the shape and Both remainders), rebuilt when its kind changes."""
        if not self.canvas or self.canvas.gl_failure or self.data is None:
            return None
        kind = self.values.get("shape", "box")
        if self.shape_item is None or self._shape_kind != kind:
            if self.shape_item is not None:
                self.canvas.removeItem(self.shape_item)
            v, f = unit_mesh(kind)
            self.shape_item = gl.GLMeshItem(vertexes=v, faces=f, smooth=False, glOptions="opaque",
                                            shader=self.make_shader(f"atlas-shape-editor-{id(self)}-shape"))
            self._shape_kind = kind
            self.canvas.addItem(self.shape_item)
        return self.shape_item

    def describe_scene(self):
        op = self.values.get("operation", "intersect")
        return {"intersect": "Keeps the model inside the shape",
                "subtract": "Cuts the shape's region away",
                "union": "Adds the shape to the model",
                "xor": "Keeps the model without the shape, and the shape without the model"}.get(op, op)

    def redraw(self, *_):
        if self.bounds is None:
            return
        m = self.frame()
        sizes = np.linalg.norm(m[:3, :3], axis=0)
        kind = self.values.get("shape", "box")
        self.status.setText(f"{kind.capitalize()} at " + ", ".join(f"{v:.4g}" for v in m[:3, 3]) + " · size "
                            + " × ".join(f"{v:.4g}" for v in sizes) + " units\n" + self.describe_scene()
                            + " · " + self.preview_note)
        self.info_changed.emit(self.info_text())
        if not self.canvas:
            return
        try:
            inverse = np.linalg.inv(m)[:3, :4]
        except np.linalg.LinAlgError:
            return
        op = self.values.get("operation", "intersect")
        keep = {"intersect": 1.0, "subtract": -1.0, "union": 0.0, "xor": -1.0}.get(op, 1.0)
        band = 0.004
        code = float(R.KIND_CODE.get(kind, 0))
        for index, item in enumerate(self.mesh_items):
            shader = item.shader()
            shader["frame"] = [float(v) for v in inverse.reshape(-1)]
            ghost = index == 1
            shader["shape"] = [code, -keep if ghost else keep, band]
            self.style_item(item, "highlight" if ghost else "paper", .12 if ghost else 1.0)
            item.setVisible(not ghost or (keep != 0.0 and self.ghost.isChecked()))
        shape = self._sync_shape_item()
        if shape is not None:
            from PySide6.QtGui import QMatrix4x4
            shape.setTransform(QMatrix4x4(*[float(v) for v in m.reshape(-1)]))
            shader = shape.shader()
            shader["frame"] = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0.]
            shader["shape"] = [code, 0.0, 0.0]
            self.style_item(shape, "accent", 1.0 if op == "union" else .35)
            shape.setVisible(op in ("union", "xor"))
        self.canvas.update()

    def info_text(self):
        return "Draw · move · rotate · scale" if self.bounds is not None else "Select a model"

