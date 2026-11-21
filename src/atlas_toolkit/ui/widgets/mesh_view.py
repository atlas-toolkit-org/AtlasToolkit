"""
3D preview (pyqtgraph.opengl): the model on a gradient backdrop with a soft vignette, over a floor grid scaled to the
model (major and minor lines that fade out at the edge), with an optional wireframe. `look()` has the colours: on
paper the view follows the theme (a light studio with a clay model, or a dark one with a paper-tone model), on
blueprint it is the blueprint sheet's navy and blue grid in both themes. Drag orbits, right-drag or
middle-drag pans, wheel zooms, double-click fits. Z is up, as in STL files.

`camera()` returns (azimuth, elevation) in degrees and `set_camera` turns the view; beside an Aim tab the stage turns
it to the side being aimed at (ui/widgets/preview.py).
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
    from OpenGL.GL import (GL_BLEND, GL_CULL_FACE, GL_DEPTH_TEST, GL_LEQUAL, GL_LESS, GL_ONE, GL_ONE_MINUS_SRC_ALPHA,
                           GL_POLYGON_OFFSET_FILL, GL_SRC_ALPHA)
    from pyqtgraph.opengl import shaders as _shaders
    from pyqtgraph.opengl.MeshData import MeshData as GLMeshData
    GL_ERROR = ""
except Exception as e:   # no OpenGL (remote desktop, broken drivers): the viewer degrades to a message
    gl = None
    GL_ERROR = f"{type(e).__name__}: {e}"

LIT_VERTEX = """
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
"""


def _lit_fragment(back: str = "") -> str:
    """Two-sided studio light: a key and a fill light that follow the camera, lighting front and back faces alike
    (shards and slices are open shells), and a faint rim on grazing faces. `back` tints the faces seen from behind
    (inside-out or open shells), as Meshmixer and MeshLab show them."""
    tint = (f"if (!gl_FrontFacing) {{ rgb = mix(rgb, vec3({back}) * (0.45 + 0.55 * key), 0.85); }}"
            if back else "")
    return f"""
        #ifdef GL_ES
        precision mediump float;
        #endif
        varying vec4 v_color;
        varying vec3 v_normal;
        void main() {{
            vec3 n = normalize(v_normal);
            float key = abs(dot(n, normalize(vec3(0.55, 0.45, 0.70))));
            float fill = abs(dot(n, normalize(vec3(-0.60, -0.20, 0.45))));
            float rim = pow(1.0 - abs(n.z), 3.0);
            vec3 rgb = v_color.rgb * (0.30 + 0.55 * key + 0.18 * fill) + vec3(0.10, 0.16, 0.30) * rim;
            {tint}
            gl_FragColor = vec4(rgb, v_color.a);
        }}
    """


SHADER = "atlas"
SHADER_BACK = "atlas-backfaces"      # the same, with back faces in a warm orange
FLAT = "atlas-flat"                  # unlit: hidden-line faces, the depth pass of See-through
XRAY = "atlas-xray"                  # a ghost: faces fade as they face the camera, so silhouettes and folds show
if gl is not None and SHADER not in _shaders.ShaderProgram.names:
    _shaders.ShaderProgram(SHADER, [_shaders.VertexShader(LIT_VERTEX), _shaders.FragmentShader(_lit_fragment())])
    _shaders.ShaderProgram(SHADER_BACK, [_shaders.VertexShader(LIT_VERTEX),
                                         _shaders.FragmentShader(_lit_fragment("0.96, 0.48, 0.22"))])
    _shaders.ShaderProgram(FLAT, [_shaders.VertexShader(LIT_VERTEX), _shaders.FragmentShader("""
        #ifdef GL_ES
        precision mediump float;
        #endif
        varying vec4 v_color;
        void main() { gl_FragColor = v_color; }
    """)])
    _shaders.ShaderProgram(XRAY, [_shaders.VertexShader(LIT_VERTEX), _shaders.FragmentShader("""
        #ifdef GL_ES
        precision mediump float;
        #endif
        varying vec4 v_color;
        varying vec3 v_normal;
        void main() {
            float edge = 1.0 - abs(normalize(v_normal).z);
            gl_FragColor = vec4(v_color.rgb, v_color.a * (0.06 + 0.94 * edge * edge));
        }
    """)])

# The display styles of the 3D view (the Display menu beside the face count), after MeshLab and Meshmixer's.
STYLES = [
    ("shaded", "Shaded", "The lit surface."),
    ("edges", "Shaded with edges", "The lit surface with every edge on it (polygons for OBJ / PLY polygon meshes)."),
    ("hidden", "Hidden line", "Edges only where the surface can be seen, on a plain fill: close to how a pen "
                              "plotter draws a mesh."),
    ("wire", "Wireframe", "Every edge, front and back, with nothing hiding them: the mesh's structure and density."),
    ("seethrough", "See-through", "The lit surface, half transparent: the floor and what is behind show through."),
    ("xray", "X-ray", "A ghost: faces turned to you vanish, edges of the form and inner shells show."),
    ("points", "Points", "The vertices only."),
]
OPTIONS = [
    ("smooth", "Smooth shading", "Light by vertex normals rather than by face: soft, without facets."),
    ("backfaces", "Back faces in orange", "Faces seen from behind in orange: inside-out parts and the inside of "
                                          "open shells stand out."),
    ("open_edges", "Open edges", "The edges with a face on one side only: holes, open shells and cuts."),
    ("bbox", "Bounding box", "The box the model fits in."),
    ("grid", "Floor grid", "The grid under the model."),
]
DISPLAY = {"style": "shaded", "smooth": False, "backfaces": False, "open_edges": False, "bbox": False, "grid": True}

BACKDROP = "atlas-backdrop"
if gl is not None and BACKDROP not in _shaders.ShaderProgram.names:
    # The viewport's backdrop: a full-window quad in clip space (no camera), a vertical gradient from its vertex
    # colours and a soft vignette whose strength rides in the alpha, so the model sits in a lit pool.
    _shaders.ShaderProgram(BACKDROP, [
        _shaders.VertexShader("""
            uniform mat4 u_mvp;
            attribute vec4 a_position;
            attribute vec4 a_color;
            varying vec4 v_color;
            varying vec2 v_uv;
            void main() {
                v_color = a_color;
                v_uv = a_position.xy;
                gl_Position = vec4(a_position.xy, 0.0, 1.0);
            }
        """),
        _shaders.FragmentShader("""
            #ifdef GL_ES
            precision mediump float;
            #endif
            varying vec4 v_color;
            varying vec2 v_uv;
            void main() {
                vec2 d = v_uv * vec2(0.85, 1.0);
                float vig = smoothstep(0.35, 1.65, length(d));
                gl_FragColor = vec4(v_color.rgb * (1.0 - v_color.a * vig), 1.0);
            }
        """),
    ])

PAPER = (0.93, 0.94, 0.96, 1.0)      # the model on the dark studio backdrop
CLAY = (0.72, 0.75, 0.80, 1.0)       # on the light one: a mid tone, so lit faces still stand off the backdrop
BLUEPRINT = (0.80, 0.88, 1.0, 1.0)
BLUEPRINT_TOP, BLUEPRINT_BOTTOM = "#16306A", "#0A1430"   # plot_view's blueprint sheet at the bottom
BLUEPRINT_LINE = (120, 170, 255)                         # and its grid's blue
BLUEPRINT_PAINT = (0.36, 0.85, 1.0)                      # a painted area: cyan, which navy and blue do not swallow


def _rgb(c) -> tuple:
    c = qcolor(c)
    return c.redF(), c.greenF(), c.blueF()


def look(theme, art: str) -> dict:
    """Backdrop, floor grid, model and wireframe colours for a theme and art mode. Paper follows the app's theme (a
    light studio in light mode, a dark one in dark mode); blueprint is the blueprint sheet in both. The grid has
    major lines (the spacing of its units) and fainter minor ones, as RGBA 0-1."""
    open_ = _rgb(theme["highlight"])     # cool, against the warm back faces it often borders
    if art == "blueprint":
        r, g, b = (v / 255 for v in BLUEPRINT_LINE)
        return {"top": _rgb(BLUEPRINT_TOP), "bottom": _rgb(BLUEPRINT_BOTTOM), "vignette": 0.30,
                "major": (r, g, b, 0.42), "minor": (r, g, b, 0.16), "model": BLUEPRINT,
                "edge": (0.10, 0.20, 0.45, 0.55), "paint": BLUEPRINT_PAINT,
                "fill": (0.07, 0.13, 0.30), "ink": (0.80, 0.89, 1.0, 0.90), "ghost": (0.62, 0.82, 1.0, 0.70),
                "open": BLUEPRINT_PAINT}
    if theme.dark:
        return {"top": _rgb(theme["bg-3"]), "bottom": _rgb(theme["bg"]), "vignette": 0.35,
                "major": (*_rgb(theme["text-3"]), 0.50), "minor": (*_rgb(theme["text-4"]), 0.30), "model": PAPER,
                "edge": (*_rgb(theme["bg-2"]), 0.50), "paint": _rgb(theme["accent"]),
                "fill": _rgb(theme["bg-2"]), "ink": (*_rgb(theme["text"]), 0.85),
                "ghost": (0.78, 0.86, 1.0, 0.65), "open": open_}
    return {"top": _rgb(theme["bg-1"]), "bottom": _rgb(theme["bg-4"]), "vignette": 0.10,
            "major": (*_rgb(theme["text-3"]), 0.55), "minor": (*_rgb(theme["text-4"]), 0.28), "model": CLAY,
            "edge": (*_rgb(theme["text-2"]), 0.45), "paint": _rgb(theme["accent"]),
            "fill": _rgb(theme["bg-1"]), "ink": (*_rgb(theme["paper-ink"]), 0.85),
            "ghost": (*_rgb(theme["text-2"]), 0.60), "open": open_}


def floor_grid(bounds, fade_segments: int = 24):
    """Line segments (N×3, pairs) for a floor grid under the model, major and minor, and a per-vertex fade (0-1)
    for each: full strength under the model, fading to nothing at the edge, so the floor has no hard border.
    Major lines keep the old spacing (a power of ten); minor ones split it in five when that is not too dense."""
    (x0, y0, z0), (x1, y1, z1) = bounds
    size = max(x1 - x0, y1 - y0, z1 - z0) or 1.0
    step = float(10 ** np.floor(np.log10(size / 4)))
    half = size * 0.9
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

    def lines(spacing, skip=None):
        n = int(np.floor(half / spacing))
        ticks = np.arange(-n, n + 1) * spacing
        if skip is not None:
            ticks = ticks[np.abs(np.round(ticks / skip) * skip - ticks) > spacing * 1e-3]
        t = np.linspace(-half, half, fade_segments + 1)
        segs = []
        for v in ticks:
            a = np.stack([t[:-1], np.full(fade_segments, v)], 1)
            b = np.stack([t[1:], np.full(fade_segments, v)], 1)
            segs.append(np.stack([a, b], 1))
            segs.append(np.stack([a[:, ::-1], b[:, ::-1]], 1))
        if not segs:
            return np.zeros((0, 3), np.float32), np.zeros(0, np.float32)
        xy = np.concatenate(segs).reshape(-1, 2)
        r = np.hypot(xy[:, 0], xy[:, 1]) / half
        fade = np.clip((1.0 - r) / 0.55, 0.0, 1.0)
        fade = fade * fade * (3 - 2 * fade)          # smoothstep
        pos = np.column_stack([xy[:, 0] + cx, xy[:, 1] + cy, np.full(len(xy), z0)]).astype(np.float32)
        return pos, fade.astype(np.float32)

    major = lines(step)
    minor = lines(step / 5, skip=step) if half / (step / 5) <= 40 else (np.zeros((0, 3), np.float32),
                                                                        np.zeros(0, np.float32))
    return major, minor


def triangle_edges(vertices, faces) -> np.ndarray:
    """Every edge of a triangle mesh once, as points in pairs for a line item."""
    f = np.asarray(faces, dtype=np.int64)
    e = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
    e = np.unique(e, axis=0)
    return np.asarray(vertices, dtype=np.float32)[e].reshape(-1, 3)


def box_edges(bounds) -> np.ndarray:
    """The 12 edges of the bounding box, as 24 points (pairs) for a line item."""
    (x0, y0, z0), (x1, y1, z1) = bounds
    c = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)], np.float32)
    pairs = [(a, b) for a in range(8) for b in range(a + 1, 8) if bin(a ^ b).count("1") == 1]
    return c[np.array(pairs).ravel()]


def grid_lines(bounds, lk) -> list:
    """[(key, positions, RGBA per vertex)] for the floor grid in a look, minor lines first; empty sets left out."""
    out = []
    for key, (pos, fade) in zip(("major", "minor"), floor_grid(bounds)):
        if len(pos):
            col = np.tile(np.asarray(lk[key], np.float32), (len(pos), 1))
            col[:, 3] *= fade
            out.append((key, pos, col))
    return out[::-1]


def set_backdrop(view, lk, item=None, shader=None):
    """Put the look's backdrop in a GL view: a gradient quad drawn before everything else (no depth written), and
    its bottom colour as the clear colour for the frame before it draws. Returns the item; pass it back to recolour.
    A view in another window (its own GL context) passes its own copy of the BACKDROP shader."""
    view.setBackgroundColor(tuple(int(v * 255) for v in lk["bottom"]) + (255,))
    quad = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], np.float32)
    cols = np.array([(*lk["bottom"], lk["vignette"])] * 2 + [(*lk["top"], lk["vignette"])] * 2, np.float32)
    md = GLMeshData(vertexes=quad, faces=np.array([[0, 1, 2], [0, 2, 3]]), vertexColors=cols)
    if item is None:
        # drawn first in every frame, it also puts back what the display styles change (mask, depth test, offset)
        item = gl.GLMeshItem(meshdata=md, smooth=True, shader=shader or BACKDROP, computeNormals=False,
                             glOptions={GL_DEPTH_TEST: False, GL_BLEND: False, GL_CULL_FACE: False,
                                        GL_POLYGON_OFFSET_FILL: False, "glColorMask": (True,) * 4,
                                        "glDepthFunc": (GL_LESS,)})
        item.setDepthValue(-10 ** 6)
        view.addItem(item)
    else:
        item.setMeshData(meshdata=md)
    return item


class MeshView(QWidget):
    camera_changed = Signal(float, float)

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.art = "paper"
        self.mesh = None
        self.item = None                   # the model's own item; the rest of what is drawn is in self.items
        self.items: list = []
        self.backdrop = None
        self.display = dict(DISPLAY)
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
        self.item = self.backdrop = None
        self.items = []
        self.msg.setText(f"3D preview unavailable: {reason}.")
        self.layout().setCurrentWidget(self.msg)

    # ------------------------------------------------------------------ content
    def show_mesh(self, data, keep_view: bool = False, wire: bool | None = None) -> None:
        """Show a MeshData in the current display (set_display). `wire` is the older switch: True shows the edges
        on the shaded surface, False the plain surface."""
        if self.view is None:
            return
        if wire is not None:
            self.display["style"] = "edges" if wire else ("shaded" if self.display["style"] == "edges"
                                                          else self.display["style"])
        same = (self.mesh is not None and data is not None and self.mesh.bounds == data.bounds)
        self.mesh = data
        for it in self.items:
            self.view.removeItem(it)
        self.items = []
        self.item = None
        if data is None or len(data.faces) == 0:
            self.layout().setCurrentWidget(self.msg)
            return
        self.layout().setCurrentWidget(self.view)
        self._draw(data, look(self.theme, self.art))
        if not (keep_view and same):
            self.fit()

    def _add(self, item, depth: float = 0.0):
        item.setDepthValue(depth)
        self.view.addItem(item)
        self.items.append(item)
        return item

    def _draw(self, data, lk) -> None:
        """Order (depth value): backdrop, grid (-2, -1), the model (0 to 2), then the overlays (10), which X-ray and
        See-through would otherwise wash over."""
        d = self.display
        style = d["style"]
        verts = np.asarray(data.vertices, dtype=np.float32)
        md = GLMeshData(vertexes=verts, faces=np.asarray(data.faces))
        lit = SHADER_BACK if d["backfaces"] else SHADER
        smooth = bool(d["smooth"])
        # polygon edges for OBJ / PLY polygon meshes, else the triangles' own (each once)
        edges = (data.edge_segments.reshape(-1, 3) if data.edge_segments is not None
                 else triangle_edges(verts, data.faces) if style in ("edges", "hidden", "wire") else None)
        back = {GL_POLYGON_OFFSET_FILL: True, "glPolygonOffset": (1.0, 1.0)}   # edges on the surface win
        line_opts = {GL_DEPTH_TEST: True, GL_CULL_FACE: False, GL_BLEND: True,
                     "glBlendFuncSeparate": (GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ONE, GL_ONE_MINUS_SRC_ALPHA)}
        # blend colour only: the alpha written stays opaque, or the window would show through the widget
        blend = {GL_BLEND: True, "glBlendFuncSeparate": (GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ONE,
                                                         GL_ONE_MINUS_SRC_ALPHA)}
        if style in ("shaded", "edges"):
            self.item = self._add(gl.GLMeshItem(
                meshdata=md, smooth=smooth, shader=lit, color=lk["model"],
                glOptions={GL_DEPTH_TEST: True, GL_BLEND: False, GL_CULL_FACE: False, **back}))
            if style == "edges":
                self._add(gl.GLLinePlotItem(pos=edges, mode="lines", color=lk["edge"], width=1., antialias=True,
                                            glOptions=line_opts), 1)
        elif style == "hidden":
            # a plain fill pushed back a little in depth, so the edges on it win and those behind it are hidden
            self.item = self._add(gl.GLMeshItem(
                meshdata=md, smooth=False, shader=FLAT, color=(*lk["fill"], 1.0),
                glOptions={GL_DEPTH_TEST: True, GL_BLEND: False, GL_CULL_FACE: False, **back}))
            self._add(gl.GLLinePlotItem(pos=edges, mode="lines", color=lk["ink"], width=1., antialias=True,
                                        glOptions=line_opts), 1)
        elif style == "wire":
            ink = (*lk["ink"][:3], 0.35)                    # where edges crowd, the lines add up: density shows
            self.item = self._add(gl.GLLinePlotItem(pos=edges, mode="lines", color=ink, width=1., antialias=True,
                                                    glOptions={**line_opts, GL_DEPTH_TEST: False}))
        elif style == "seethrough":
            # depth first, colour nowhere: then only the nearest surface is drawn, half transparent, so it reads
            # as one clean skin over whatever is behind it, in any face order
            self._add(gl.GLMeshItem(meshdata=md, smooth=False, shader=FLAT, color=(0, 0, 0, 1), glOptions={
                GL_DEPTH_TEST: True, GL_BLEND: False, GL_CULL_FACE: False, "glColorMask": (False,) * 4}), 1)
            self.item = self._add(gl.GLMeshItem(
                meshdata=md, smooth=smooth, shader=lit, color=(*lk["model"][:3], 0.45), glOptions={
                    GL_DEPTH_TEST: True, GL_CULL_FACE: False, "glColorMask": (True,) * 4,
                    "glDepthFunc": (GL_LEQUAL,), **blend}), 2)
        elif style == "xray":
            # one colour, so the blend does not depend on the order the faces come in
            self.item = self._add(gl.GLMeshItem(meshdata=md, smooth=True, shader=XRAY, color=lk["ghost"],
                                                glOptions={GL_DEPTH_TEST: False, GL_CULL_FACE: False, **blend}))
        elif style == "points":
            pts = gl.GLScatterPlotItem(pos=verts, size=2.5, color=lk["ink"], pxMode=True)
            pts.setGLOptions("translucent")
            self.item = self._add(pts)
        marks = getattr(data, "marks", None)
        if marks is not None and len(marks):
            col = qcolor(self.theme['danger'])
            self._add(gl.GLLinePlotItem(pos=np.asarray(marks, dtype=np.float32).reshape(-1, 3), mode='lines',
                                        color=(col.redF(), col.greenF(), col.blueF(), 1.0), width=3.,
                                        antialias=True, glOptions="translucent"), 10)   # a problem is never hidden
        opened = getattr(data, "open_edges", None)
        if d["open_edges"] and opened is not None and len(opened):
            self._add(gl.GLLinePlotItem(pos=np.asarray(opened, np.float32).reshape(-1, 3), mode="lines",
                                        color=(*lk["open"], 1.0), width=2.5, antialias=True,
                                        glOptions="translucent"), 10)
        if d["bbox"]:
            self._add(gl.GLLinePlotItem(pos=box_edges(data.bounds), mode="lines", color=(*lk["ink"][:3], 0.55),
                                        width=1., antialias=True, glOptions="translucent"), 10)
        if d["grid"]:
            for key, pos, col in grid_lines(data.bounds, lk):
                self._add(gl.GLLinePlotItem(pos=pos, mode="lines", color=col, width=1.2 if key == "major" else 1.0,
                                            antialias=True, glOptions="translucent"), -1 if key == "major" else -2)

    def set_display(self, **changes) -> None:
        """Change the display (keys of DISPLAY: style, smooth, backfaces, open_edges, bbox, grid) and redraw."""
        self.display.update({k: v for k, v in changes.items() if k in DISPLAY})
        if self.mesh is not None:
            self.show_mesh(self.mesh, keep_view=True)

    def open_edge_count(self) -> int | None:
        """Open edges of the mesh shown (from its full mesh), or None when the preview did not work them out."""
        opened = getattr(self.mesh, "open_edges", None) if self.mesh is not None else None
        return None if opened is None else len(opened)

    def set_wire(self, on: bool) -> None:
        self.set_display(style="edges" if on else "shaded")

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
        """The backdrop: a gradient quad drawn before everything else, and the same bottom colour as the clear
        colour, for the frame before it draws."""
        if self.view is None:
            return
        self.backdrop = set_backdrop(self.view, look(self.theme, self.art), self.backdrop)

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
