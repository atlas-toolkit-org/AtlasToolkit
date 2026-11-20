"""The face-paint dialog: paint or erase the visible faces of a mesh with a brush, for any tool with a painted area.

The mask is one flag per source face (a portable schema value, see Surface Eject's `encode_selection`); this dialog
only edits it. It stays quick on meshes of millions of faces:

- picking is screen-space (`geom/screen_pick.py`): once per camera the mesh is drawn offscreen with each face's index
  as its colour, and a brush stroke reads that buffer, so its cost does not grow with the mesh;
- mouse moves are gathered and painted at most once a frame, as swept segments, so a fast stroke leaves no gaps and
  never queues up work;
- only the colours of the faces that changed go to the GPU; positions and normals go once.

The orbit camera stays local to the dialog.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QOpenGLContext, QPainter, QPen, QShortcut, QVector3D
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)

from ...geom.screen_pick import ScreenPicker, decode_ids, encode_ids
from ..theme import qcolor
from .gl_guard import GuardedGL
from .gl_nav import GestureNavigation
from .mesh_view import SHADER, gl
from .spin import IntSpin

FRAME_MS = 16          # a stroke is painted at most this often
BLOCK = 4096           # faces per block of colours written to the GPU together
TOLERANCE = 0.003      # of the model's diagonal: how far behind the visible surface a face centre may be and still count


def _rgba(color) -> np.ndarray:
    return np.array([color.red(), color.green(), color.blue(), 255], np.uint8)


class SelectionDialog(QDialog):
    """`original` is the source mesh, `display` the same faces in the view's frame. `codec` is the tool's
    (encode, decode) pair for its selection files, which adds Load… and Export…"""

    def __init__(self, original, display, selected, theme, parent=None, codec=None):
        super().__init__(parent)
        self.setWindowTitle("Paint area")
        self.resize(1050, 760)
        self.setMinimumSize(640, 460)
        self.original, self.mesh, self.theme = original, display, theme
        self.selected = np.array(selected, dtype=bool, copy=True)
        self.history = []
        self.areas = np.asarray(display.area_faces, dtype=np.float64)
        self.total_area = float(self.areas.sum()) or 1.
        self.picker = ScreenPicker(display.triangles_center,
                                   TOLERANCE * float(np.linalg.norm(display.extents)))
        self.shown = None              # the mask the canvas and status show; None until the first refresh
        self.shown_area, self.shown_count = 0., 0
        self.touched, self.whole = [], True     # faces a stroke changed since the last refresh; or look at them all
        self.pending, self.last = [], None
        self.codec = codec
        layout = QVBoxLayout(self)
        instructions = self.instructions = QLabel(
            "Paint visible faces with left-drag; Shift erases. Alt+left-drag or Orbit mode rotates; wheel zooms. "
            "Selection follows original triangles. Rotate to paint other sides; hidden faces stay unselected.")
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        bar = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["Paint", "Erase", "Orbit"])
        bar.addWidget(self.mode)
        bar.addWidget(QLabel("Brush radius"))
        self.radius = IntSpin()
        self.radius.setRange(5, 150)
        self.radius.setValue(40)
        self.radius.setSuffix(" px")
        bar.addWidget(self.radius)
        actions = [("Undo", self.undo), ("Clear", lambda: self.fill(False)), ("Select all", lambda: self.fill(True))]
        if codec is not None:
            actions += [("Load…", self.load), ("Export…", self.export)]
        for label, callback in actions:
            button = QPushButton(label)
            button.clicked.connect(callback)
            bar.addWidget(button)
        bar.addStretch(1)
        layout.addLayout(bar)
        if gl is None:
            raise ValueError("OpenGL is required for visual painting. Use a saved selection JSON through the CLI instead.")
        self.stroke_timer = QTimer(self)
        self.stroke_timer.setSingleShot(True)
        self.stroke_timer.setInterval(FRAME_MS)
        self.stroke_timer.timeout.connect(self.flush)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(0)
        self.refresh_timer.timeout.connect(self.refresh)
        self.canvas = PaintCanvas(self)
        layout.addWidget(self.canvas, 1)
        self.status = QLabel()
        layout.addWidget(self.status)
        self.canvas.on_gl_failure = lambda reason: (self.canvas.hide(), self.status.setText(f"Painting is unavailable: {reason}."))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Use selected area")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.undo_shortcut = QShortcut(QKeySequence.Undo, self)
        self.undo_shortcut.activated.connect(self.undo)
        self.refresh()

    # ------------------------------------------------------------------ mask edits
    def remember(self):
        self.history.append(self.selected.copy())
        self.history = self.history[-20:]

    def undo(self):
        if self.history:
            self.selected = self.history.pop()
            self.redraw()

    def fill(self, value):
        self.remember()
        self.selected[:] = value
        self.redraw()

    def load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load surface selection", "", "Surface selection (*.json)")
        if path:
            try:
                selected = self.codec[1](self.original, Path(path).read_text(encoding="utf-8-sig"))
                self.remember()
                self.selected = selected
                self.redraw()
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Selection", str(exc))

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export surface selection", "surface-selection.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(self.codec[0](self.original, self.selected), encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Selection", str(exc))

    # ------------------------------------------------------------------ painting
    def paint(self, x, y, erase=False, start=None):
        """Paint (or erase) the visible faces under the brush, swept from `start` (default: the same point) to (x, y)."""
        self.paint_path([(x, y)] if start is None else [start, (x, y)], erase)

    def paint_path(self, path, erase=False):
        """Paint (or erase) the visible faces under the brush swept along `path` (pixels)."""
        if not self.canvas.pick_view():
            return
        faces = self.picker.stroke(path, self.radius.value())
        faces = faces[self.selected[faces] == erase]
        if len(faces):
            self.selected[faces] = not erase
            self.touched.append(faces)
            self.redraw(whole=False)

    def begin(self, x, y, erase):
        self.remember()
        self.pending.clear()
        self.last = (x, y)
        self.paint(x, y, erase)

    def queue(self, x, y, erase):
        """A mouse move while painting: painted on the next frame with every other move since."""
        self.pending.append((x, y, erase))
        if not self.stroke_timer.isActive():
            self.stroke_timer.start()

    def flush(self):
        self.stroke_timer.stop()
        points, self.pending = self.pending, []
        # Every move since the last frame is one swept path; a new path where Shift (erase) changes.
        while points:
            erase = points[0][2]
            n = next((i for i, point in enumerate(points) if point[2] != erase), len(points))
            path = [self.last] + [(x, y) for x, y, _ in points[:n]]
            self.paint_path(path, erase)
            self.last, points = path[-1], points[n:]

    def busy(self):
        """Work still waiting: strokes not yet painted, or colours not yet shown."""
        return bool(self.pending) or self.stroke_timer.isActive() or self.refresh_timer.isActive()

    # ------------------------------------------------------------------ display
    def redraw(self, whole=True):
        """Show the mask on the next turn of the event loop (several edits in a row show once). `whole` when the
        mask may have changed anywhere (undo, fill, load), not only where strokes said."""
        self.whole = self.whole or whole
        if not self.refresh_timer.isActive():
            self.refresh_timer.start()

    def refresh(self):
        self.refresh_timer.stop()
        touched, self.touched = self.touched, []
        if self.shown is None or len(self.shown) != len(self.selected):
            self.shown = self.selected.copy()
            self.shown_area = float(self.areas[self.selected].sum())
            self.shown_count = int(np.count_nonzero(self.selected))
            self.whole = False
            self.canvas.recolor(None)
        else:
            if self.whole:
                changed = np.flatnonzero(self.shown != self.selected)
                self.whole = False
            elif touched:
                changed = np.unique(np.concatenate(touched))
                changed = changed[self.shown[changed] != self.selected[changed]]
            else:
                return
            if not len(changed):
                return
            now = self.selected[changed]
            self.shown[changed] = now
            self.shown_area += float(self.areas[changed[now]].sum() - self.areas[changed[~now]].sum())
            self.shown_count += int(np.count_nonzero(now)) * 2 - len(changed)
            self.canvas.recolor(changed)
        count = self.shown_count
        area = max(self.shown_area, 0.) / self.total_area * 100
        self.status.setText(f"{count:,} / {len(self.selected):,} faces selected · {area:.1f}% of source surface area")

    def accept(self):
        self.flush()
        super().accept()


if gl is not None:
    from OpenGL import GL
    from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLFramebufferObject
    from pyqtgraph.opengl import shaders
    from pyqtgraph.opengl.GLGraphicsItem import GLGraphicsItem

    PICK_VERTEX = """
        uniform mat4 u_mvp;
        attribute vec4 a_position;
        attribute vec4 a_color;
        varying vec4 v_color;
        void main() {
            v_color = a_color;
            gl_Position = u_mvp * a_position;
        }
    """
    PICK_FRAGMENT = """
        #ifdef GL_ES
        precision highp float;
        #endif
        varying vec4 v_color;
        void main() {
            gl_FragColor = v_color;
        }
    """

    def _private_shader(name, stages):
        """Modal windows may have their own GL contexts: never reuse or reset Atlas's shared shaders."""
        program = shaders.ShaderProgram(name, stages)
        shaders.ShaderProgram.names.pop(name)
        return program

    class PaintMeshItem(GLGraphicsItem):
        """Flat-shaded mesh with one colour per face, drawn from unindexed vertex buffers.

        Positions, normals and face ids go to the GPU once (their CPU copies are then dropped); afterwards only the
        colours of faces that changed are written. `picking` draws face ids instead of colours, unlit."""

        def __init__(self, vertices, faces, shader, pick_shader):
            super().__init__()
            self.setGLOptions("opaque")
            self.vertices, self.faces = vertices, np.asarray(faces)
            self.shader, self.pick_shader = shader, pick_shader
            self.colors = np.zeros((len(self.faces), 3, 4), np.uint8)
            self.count = len(self.faces) * 3
            self.dirty = np.zeros((len(self.faces) + BLOCK - 1) // BLOCK, bool)   # colour blocks to write
            self.picking = False
            self.reset_gl()

        def reset_gl(self):
            """A new GL context: buffers are created and filled again on the next paint."""
            self.buffers = None

        def set_colors(self, faces, rgba):
            """`faces` None for all of them; `rgba` one colour per face, or one for all."""
            if faces is None:
                self.colors[:] = np.asarray(rgba, np.uint8)[..., None, :] if np.ndim(rgba) == 2 else rgba
                self.dirty[:] = True
            elif len(faces):
                rgba = np.asarray(rgba, np.uint8)
                self.colors[faces] = rgba[:, None, :] if rgba.ndim == 2 else rgba
                self.dirty[faces // BLOCK] = True
            self.update()

        def _upload(self):
            tri = np.asarray(self.vertices, np.float32)[self.faces]
            normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            normals /= np.maximum(np.linalg.norm(normals, axis=1), 1e-30)[:, None]
            arrays = {"position": tri.reshape(-1, 3),
                      "normal": np.repeat(normals.astype(np.float32), 3, axis=0),
                      "id": np.repeat(encode_ids(len(self.faces)), 3, axis=0),
                      "color": self.colors.reshape(-1, 4)}
            self.buffers = {}
            for name, array in arrays.items():
                vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
                vbo.create()
                vbo.bind()
                vbo.allocate(np.ascontiguousarray(array), array.nbytes)
                vbo.release()
                self.buffers[name] = vbo
            self.dirty[:] = False

        def paint(self):
            if not self.count:
                return
            self.setupGLState()
            if self.buffers is None:
                self._upload()
            elif self.dirty.any():
                # One write per run of changed blocks.
                edges = np.flatnonzero(np.diff(np.r_[0, self.dirty.view(np.int8), 0]))
                vbo = self.buffers["color"]
                vbo.bind()
                for first, end in zip(edges[::2], edges[1::2]):
                    lo, hi = first * BLOCK, min(end * BLOCK, len(self.faces))
                    chunk = self.colors[lo:hi]
                    vbo.write(lo * 12, chunk, chunk.nbytes)
                vbo.release()
                self.dirty[:] = False
            shader = self.pick_shader if self.picking else self.shader
            context = QOpenGLContext.currentContext()
            program = shader.program(es2_compat=context.hasExtension(b"GL_ARB_ES2_compatibility"))
            attributes = (("a_position", "position", 3, GL.GL_FLOAT, False),
                          ("a_normal", "normal", 3, GL.GL_FLOAT, False),
                          ("a_color", "id" if self.picking else "color", 4, GL.GL_UNSIGNED_BYTE, True))
            enabled = []
            for attribute, name, size, kind, normalized in attributes:
                loc = GL.glGetAttribLocation(program, attribute)
                if loc == -1:
                    continue
                self.buffers[name].bind()
                GL.glVertexAttribPointer(loc, size, kind, normalized, 0, None)
                self.buffers[name].release()
                GL.glEnableVertexAttribArray(loc)
                enabled.append(loc)
            with shader:
                GL.glUniformMatrix4fv(GL.glGetUniformLocation(program, "u_mvp"), 1, False,
                                      np.array(self.mvpMatrix().data(), dtype=np.float32))
                if (loc := GL.glGetUniformLocation(program, "u_normal")) != -1:
                    GL.glUniformMatrix3fv(loc, 1, False,
                                          np.array(self.modelViewMatrix().normalMatrix().data(), dtype=np.float32))
                GL.glDrawArrays(GL.GL_TRIANGLES, 0, self.count)
            for loc in enabled:
                GL.glDisableVertexAttribArray(loc)

    class BrushGuide(QWidget):
        def __init__(self, canvas):
            super().__init__(canvas)
            self.canvas = canvas
            self.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.setAttribute(Qt.WA_NoSystemBackground)

        def paintEvent(self, event):
            canvas = self.canvas
            if canvas.cursor_pos is None or canvas.editor.mode.currentText() == "Orbit":
                return
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(qcolor(canvas.editor.theme["accent"]), 1.5))
            radius = canvas.editor.radius.value()
            painter.drawEllipse(QPointF(*canvas.cursor_pos), radius, radius)

    class PaintCanvas(GestureNavigation, GuardedGL, gl.GLViewWidget):
        def __init__(self, editor):
            super().__init__()
            self.editor, self.painting, self.item = editor, False, None
            self.cursor_pos = None
            self.fbo, self.view_key = None, None
            template = shaders.getShaderProgram(SHADER)
            self.private_shader = _private_shader(f"face-paint-{id(self)}",
                                                  [type(stage)(stage.code) for stage in template.shaders])
            self.pick_shader = _private_shader(f"face-pick-{id(self)}", [shaders.VertexShader(PICK_VERTEX),
                                                                          shaders.FragmentShader(PICK_FRAGMENT)])
            self.setMinimumSize(400, 260)
            self.setMouseTracking(True)
            self.setFocusPolicy(Qt.StrongFocus)
            c = qcolor(editor.theme["bg-1"])
            self.setBackgroundColor((c.red(), c.green(), c.blue(), 255))
            self.opts["center"] = QVector3D(*editor.mesh.bounds.mean(0))
            self.setCameraPosition(distance=float(np.linalg.norm(editor.mesh.extents)) * 1.5, elevation=18, azimuth=-65)
            self.guide = BrushGuide(self)
            self.item = PaintMeshItem(editor.mesh.vertices, editor.mesh.faces, self.private_shader, self.pick_shader)
            self.addItem(self.item)

        def initializeGL(self):
            self._guarded(self._set_up_context)

        def _set_up_context(self):
            super().initializeGL()
            self.private_shader.prog = None
            self.pick_shader.prog = None
            self.fbo, self.view_key = None, None
            self.item.reset_gl()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self.guide.setGeometry(self.rect())
            self.guide.raise_()

        def leaveEvent(self, event):
            self.cursor_pos = None
            self.guide.update()
            super().leaveEvent(event)

        # -------------------------------------------------------------- camera maths
        def _matrices(self):
            rect = (0, 0, max(self.width(), 1), max(self.height(), 1))
            P = np.asarray(self.projectionMatrix(rect, rect).data()).reshape(4, 4, order="F")
            V = np.asarray(self.viewMatrix().data()).reshape(4, 4, order="F")
            return P, V

        def matrix(self):
            P, V = self._matrices()
            return P @ V

        def project(self, points):
            q = np.c_[points, np.ones(len(points))] @ self.matrix().T
            q = q[:, :3] / q[:, 3:4]
            return np.column_stack(((q[:, 0] + 1) * self.width() / 2, (1 - q[:, 1]) * self.height() / 2, q[:, 2]))

        def unproject(self, x, y, z):
            q = np.linalg.solve(self.matrix(), [2 * x / self.width() - 1, 1 - 2 * y / self.height(), z, 1])
            return q[:3] / q[3]

        def rays(self, pixels):
            xy = np.asarray(pixels) * [2 / self.width(), -2 / self.height()] + [-1, 1]
            inv = np.linalg.inv(self.matrix())
            ends = []
            for z in (-1., 1.):
                q = np.c_[xy, np.full(len(xy), z), np.ones(len(xy))] @ inv.T
                ends.append(q[:, :3] / q[:, 3:4])
            direction = ends[1] - ends[0]
            direction /= np.linalg.norm(direction, axis=1)[:, None]
            return ends[0], direction

        # -------------------------------------------------------------- picking
        def pick_view(self) -> bool:
            """Draw the face-id buffer for the current camera, unless it is already drawn. False if GL cannot."""
            w, h = max(self.width(), 1), max(self.height(), 1)
            P, V = self._matrices()
            key = (w, h, P.tobytes(), V.tobytes())
            if key == self.view_key and self.editor.picker.ready:
                return True
            if not self.isValid() or self.gl_failure:
                return False
            self._guarded(lambda: self._pick_pass(w, h, P, V))
            if self.gl_failure:
                return False
            self.view_key = key
            return True

        def _pick_pass(self, w, h, P, V):
            self.makeCurrent()
            if self.fbo is None or self.fbo.size() != QSize(w, h):
                self.fbo = QOpenGLFramebufferObject(w, h, QOpenGLFramebufferObject.Attachment.Depth)
            rgba = np.empty((h, w, 4), np.uint8)
            depth = np.empty((h, w), np.float32)
            background = self.opts["bgcolor"]
            self.fbo.bind()
            try:
                self.opts["bgcolor"] = (0., 0., 0., 0.)
                self.item.picking = True
                GL.glViewport(0, 0, w, h)
                GL.glDisable(GL.GL_DITHER)
                self.paint(region=(0, 0, w, h), viewport=(0, 0, w, h))
                GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
                GL.glReadPixels(0, 0, w, h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, rgba)
                GL.glReadPixels(0, 0, w, h, GL.GL_DEPTH_COMPONENT, GL.GL_FLOAT, depth)
            finally:
                self.item.picking = False
                self.opts["bgcolor"] = background
                GL.glEnable(GL.GL_DITHER)
                self.fbo.release()
            self.editor.picker.set_view(P, V, w, h, decode_ids(rgba[::-1]), depth[::-1])

        # -------------------------------------------------------------- colours
        def recolor(self, changed=None):
            """Colour faces by the editor's mask: all of them, or only `changed` (face indices)."""
            e = self.editor
            base, accent = _rgba(qcolor(e.theme["text-2"])), _rgba(qcolor(e.theme["accent"]))
            if changed is None:
                self.item.set_colors(None, np.where(e.selected[:, None], accent, base))
            else:
                self.item.set_colors(changed, np.where(e.selected[changed, None], accent, base))

        # -------------------------------------------------------------- mouse
        def _erase(self, event):
            return self.editor.mode.currentText() == "Erase" or bool(event.modifiers() & Qt.ShiftModifier)

        def mousePressEvent(self, event):
            if (event.button() == Qt.LeftButton and self.editor.mode.currentText() != "Orbit"
                    and not event.modifiers() & Qt.AltModifier):
                self.painting = True
                self.editor.begin(event.position().x(), event.position().y(), self._erase(event))
                event.accept()
            else:
                super().mousePressEvent(event)

        def mouseMoveEvent(self, event):
            self.cursor_pos = [event.position().x(), event.position().y()]
            self.guide.update()
            if self.painting:
                self.editor.queue(event.position().x(), event.position().y(), self._erase(event))
                event.accept()
            else:
                super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event):
            if self.painting:
                self.painting = False
                self.editor.flush()
                event.accept()
            else:
                super().mouseReleaseEvent(event)
