"""
3D preview (pyqtgraph.opengl): the model shaded in the studio's paper tone on navy (or pale blue-white on the
blueprint grid), an optional wireframe, a floor grid scaled to the model. Drag orbits, right-drag or
middle-drag pans, wheel zooms, double-click fits. Z is up, as in STL files.

`camera()` returns (azimuth, elevation) in degrees; the Hatch Maker turns that into its view rotation, so the
drawing is made from exactly the view on screen.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import QLabel, QStackedLayout, QVBoxLayout, QWidget

from ..theme import qcolor
from .gl_guard import GuardedGL
from .gl_nav import GestureNavigation

try:
    import pyqtgraph.opengl as gl
    from pyqtgraph.opengl import shaders as _shaders
    from pyqtgraph.opengl.MeshData import MeshData as GLMeshData
    GL_ERROR = ""
except Exception as e:   # no OpenGL (remote desktop, broken drivers): the viewer degrades to a message
    gl = None
    GL_ERROR = f"{type(e).__name__}: {e}"

SHADER = "atlas"
if gl is not None and SHADER not in _shaders.ShaderProgram.names:
    # Two-sided studio light: a key and a fill light that follow the camera, lighting front and back faces alike
    # (shards and slices are open shells), and a faint rim on grazing faces.
    _shaders.ShaderProgram(SHADER, [
        _shaders.VertexShader("""
            uniform mat4 u_mvp;
            uniform mat3 u_normal;
            attribute vec4 a_position;
            attribute vec3 a_normal;
            attribute vec4 a_color;
            varying vec4 v_color;
            varying vec3 v_normal;
            void main() {
                v_normal = normalize(u_normal * a_normal);
                v_color = a_color;
                gl_Position = u_mvp * a_position;
            }
        """),
        _shaders.FragmentShader("""
            #ifdef GL_ES
            precision mediump float;
            #endif
            varying vec4 v_color;
            varying vec3 v_normal;
            void main() {
                vec3 n = normalize(v_normal);
                float key = abs(dot(n, normalize(vec3(0.55, 0.45, 0.70))));
                float fill = abs(dot(n, normalize(vec3(-0.60, -0.20, 0.45))));
                float rim = pow(1.0 - abs(n.z), 3.0);
                vec3 rgb = v_color.rgb * (0.30 + 0.55 * key + 0.18 * fill) + vec3(0.10, 0.16, 0.30) * rim;
                gl_FragColor = vec4(rgb, v_color.a);
            }
        """),
    ])

PAPER = (0.93, 0.94, 0.96, 1.0)
BLUEPRINT = (0.80, 0.88, 1.0, 1.0)


class MeshView(QWidget):
    camera_changed = Signal(float, float)

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.art = "paper"
        self.mesh = None
        self.item = None
        self.edge_item = None
        self.grid = None
        self.wire = False
        lay = QStackedLayout(self)
        self.msg = QLabel("No model to show yet.")
        self.msg.setAlignment(Qt.AlignCenter)
        self.msg.setProperty("class", "hint")
        self.view = None
        if gl is not None:
            try:
                self.view = _View()
                self.view.on_gl_failure = self._gl_failed
                self.view.moved.connect(lambda: self.camera_changed.emit(*self.camera()))
                lay.addWidget(self.view)
            except Exception as e:   # pragma: no cover - depends on the machine's OpenGL
                self.msg.setText(f"3D preview unavailable: {e}")
        else:
            self.msg.setText(f"3D preview unavailable ({GL_ERROR}).")
        lay.addWidget(self.msg)
        lay.setCurrentWidget(self.msg if self.view is None else self.view)
        self._apply_bg()

    def _gl_failed(self, reason: str) -> None:
        """From here on this is the same as having no OpenGL at all: every method already copes with no view."""
        self._failed_view, self.view = self.view, None
        self.item = self.edge_item = self.grid = None
        self.msg.setText(f"3D preview unavailable: {reason}.")
        self.layout().setCurrentWidget(self.msg)

    # ------------------------------------------------------------------ content
    def show_mesh(self, data, keep_view: bool = False, wire: bool | None = None) -> None:
        if self.view is None:
            return
        if wire is not None:
            self.wire = wire
        same = (self.mesh is not None and data is not None and self.mesh.bounds == data.bounds)
        self.mesh = data
        if self.edge_item is not None:
            self.view.removeItem(self.edge_item)
            self.edge_item = None
        if self.item is not None:
            self.view.removeItem(self.item)
            self.item = None
        if self.grid is not None:
            self.view.removeItem(self.grid)
            self.grid = None
        if data is None or len(data.faces) == 0:
            self.layout().setCurrentWidget(self.msg)
            return
        self.layout().setCurrentWidget(self.view)
        md = GLMeshData(vertexes=np.asarray(data.vertices, dtype=np.float32), faces=np.asarray(data.faces))
        blue = self.art == "blueprint"
        self.item = gl.GLMeshItem(meshdata=md, smooth=False, shader=SHADER, glOptions="opaque",
                                  color=BLUEPRINT if blue else PAPER, drawEdges=self.wire and data.edge_segments is None,
                                  edgeColor=(0.24, 0.48, 1.0, 0.55))
        self.view.addItem(self.item)
        if self.wire and data.edge_segments is not None:
            col = qcolor(self.theme['text-2'])
            self.edge_item = gl.GLLinePlotItem(pos=data.edge_segments.reshape(-1,3), mode='lines',
                color=(col.redF(), col.greenF(), col.blueF(), .75), width=1., antialias=True)
            self.view.addItem(self.edge_item)
        (x0, y0, z0), (x1, y1, z1) = data.bounds
        size = max(x1 - x0, y1 - y0, z1 - z0) or 1.0
        self.grid = gl.GLGridItem()
        step = 10 ** np.floor(np.log10(size / 4)) if size > 0 else 1
        self.grid.setSize(size * 1.5, size * 1.5)
        self.grid.setSpacing(step, step)
        self.grid.translate((x0 + x1) / 2, (y0 + y1) / 2, z0)
        col = qcolor(self.theme["line-3"])
        self.grid.setColor((col.red(), col.green(), col.blue(), 90))
        self.view.addItem(self.grid)
        if not (keep_view and same):
            self.fit()

    def set_wire(self, on: bool) -> None:
        self.wire = on
        if self.mesh is not None:
            self.show_mesh(self.mesh, keep_view=True)

    def fit(self) -> None:
        if self.view is None or self.mesh is None:
            return
        (x0, y0, z0), (x1, y1, z1) = self.mesh.bounds
        c = QVector3D((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
        r = float(np.linalg.norm([x1 - x0, y1 - y0, z1 - z0])) / 2 or 1.0
        self.view.opts["center"] = c
        self.view.setCameraPosition(distance=r * 2.1, elevation=self.view.opts.get("elevation", 18),
                                    azimuth=self.view.opts.get("azimuth", -60))

    def camera(self) -> tuple[float, float]:
        if self.view is None:
            return 0.0, 0.0
        return float(self.view.opts["azimuth"]), float(self.view.opts["elevation"])

    def set_camera(self, azimuth: float, elevation: float) -> None:
        if self.view is not None:
            self.view.setCameraPosition(azimuth=azimuth, elevation=elevation)

    # ------------------------------------------------------------------ appearance
    def _apply_bg(self) -> None:
        if self.view is None:
            return
        if self.art == "blueprint":
            self.view.setBackgroundColor((10, 20, 48, 255))
        else:
            c = qcolor(self.theme["bg-1"] if self.theme.dark else "#16213A")
            self.view.setBackgroundColor((c.red(), c.green(), c.blue(), 255))

    def set_theme(self, theme) -> None:
        self.theme = theme
        self._apply_bg()
        if self.mesh is not None:
            self.show_mesh(self.mesh, keep_view=True)

    def set_art(self, art: str) -> None:
        self.art = art
        self._apply_bg()
        if self.mesh is not None:
            self.show_mesh(self.mesh, keep_view=True)


if gl is not None:
    class _View(GestureNavigation, GuardedGL, gl.GLViewWidget):
        moved = Signal()

        def __init__(self):
            super().__init__()
            self.setCameraPosition(distance=100, elevation=18, azimuth=-60)

        def mouseReleaseEvent(self, ev):
            super().mouseReleaseEvent(ev)
            self.moved.emit()

        def wheelEvent(self, ev):
            super().wheelEvent(ev)
            self.moved.emit()

        def mouseDoubleClickEvent(self, ev):
            p = self.parent()
            while p is not None and not isinstance(p, MeshView):
                p = p.parent()
            if p is not None:
                p.fit()
else:   # pragma: no cover
    _View = None
