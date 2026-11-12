"""Contract-hosted selection tab and modal, exact-source visible-face painting.

The orbit camera stays local to the editor. The committed face mask is a portable schema value, so CLI,
history, presets and Workflows execute the same selection without depending on Qt or a running editor.
"""
from pathlib import Path
import numpy as np
import trimesh
from scipy.spatial import cKDTree

from PySide6.QtCore import Qt, QTimer, Signal, QPointF
from PySide6.QtGui import QKeySequence, QShortcut, QVector3D, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from atlas_toolkit.geom import frame3d
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.ui.theme import qcolor
from atlas_toolkit.ui.widgets.gl_guard import GuardedGL
from atlas_toolkit.ui.widgets.gl_nav import GestureNavigation
from atlas_toolkit.ui.widgets.mesh_view import MeshView, gl, SHADER
from atlas_toolkit.ui.widgets.spin import IntSpin
from . import TOOL
from . import mesh_surface_eject as se
from .engine import selection_text


class SelectionView(QWidget):
    title = "Selection"
    info_changed = Signal(str)

    def __init__(self, page):
        super().__init__()
        self.page, self.theme = page, page.theme
        self.path = None
        self.values = dict(TOOL.defaults())
        layout = QVBoxLayout(self)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setProperty("class", "hint")
        layout.addWidget(self.note)
        row = QHBoxLayout()
        self.paint_button = QPushButton("Paint / select area…")
        self.paint_button.clicked.connect(self.open_editor)
        row.addWidget(self.paint_button)
        preview = QPushButton("Preview extraction")
        preview.clicked.connect(lambda: page.preview_selected())
        row.addWidget(preview)
        layout.addLayout(row)
        self.model = MeshView(self.theme)
        layout.addWidget(self.model, 1)
        self.refresh()

    def set_input(self, path, info):
        self.path = Path(path) if path else None
        self.values = dict(self.page.values())
        if info and info.preview:
            self.model.show_mesh(info.preview["views"][0]["mesh"])
        else:
            self.model.show_mesh(None)
        self.refresh()

    def values_changed(self, values):
        previous = self.values.get("region")
        self.values = dict(values)
        self.refresh()
        if previous != "paint" and values.get("region") == "paint" and self.path:
            QTimer.singleShot(0, self.open_editor)

    def refresh(self):
        region = self.values.get("region", "whole")
        description = {"whole": "Sampling over the whole model surface.",
                       "band": "Sampling inside the selected height band. Cutters are clipped at the band edges.",
                       "paint": "Sampling only within painted source faces. Open the editor to paint or inspect the selection."}[region]
        self.note.setText(description + "\nPainting is tied to this exact mesh; source triangles define its precision. In Workflows, use Aim in tool and Back to workflow.")
        self.paint_button.setEnabled(self.path is not None and gl is not None)
        self.info_changed.emit(self.info_text())

    def info_text(self):
        return "Surface Eject · " + self.values.get("region", "whole") + " selection"

    def open_editor(self):
        if self.path is None:
            return
        try:
            original = gm.load_mesh(self.path)
            if len(original.faces) > int(self.values["max_faces"]):
                raise ValueError("Input exceeds Maximum faces. Simplify it or raise the limit before painting.")
            up, front, rotate, _ = frame3d.resolve_axes(self.path, self.values, original.extents)
            frame = frame3d.fit_frame(original.vertices, up, front, rotate, mode="height", size=2.)
            display = trimesh.Trimesh(frame.apply(original.vertices), original.faces, process=False)
            warning = ""
            try:
                text = selection_text(self.values)
                selected = se.decode_selection(original, text) if text else np.zeros(len(original.faces), bool)
            except (ValueError, OSError) as exc:
                selected = np.zeros(len(original.faces), bool)
                warning = str(exc) + " Start a new selection below."
            dialog = SelectionDialog(original, display, selected, self.theme, self)
            if warning:
                dialog.instructions.setText(warning + "\n" + dialog.instructions.text())
            if dialog.exec() == QDialog.Accepted:
                if not dialog.selected.any():
                    raise ValueError("The selection is empty. Paint an area before applying it.")
                self.page.form.set_values({"region": "paint", "selection": se.encode_selection(original, dialog.selected),
                                           "selection_file": ""})
                self.values = dict(self.page.values())
                self.page._settings_changed()
                self.refresh()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Surface selection", str(exc))

    def validation_error(self, path, mode="run"):
        try:
            p = self.page.values()
            se.validate(p)
            if p["region"] == "paint" and not (p.get("selection") or p.get("selection_file")):
                return "Paint an area in the Selection tab before ejecting."
        except ValueError as exc:
            return str(exc)
        return ""

    def set_theme(self, theme):
        self.theme = theme
        self.model.set_theme(theme)

    def set_art(self, art):
        self.model.set_art(art)

    def fit(self):
        self.model.fit()


class SelectionDialog(QDialog):
    def __init__(self, original, display, selected, theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Surface Eject — paint extraction area")
        self.resize(1050, 760)
        self.setMinimumSize(640, 460)
        self.original, self.mesh, self.theme = original, display, theme
        self.selected = np.array(selected, dtype=bool, copy=True)
        self.history = []
        self.tree = cKDTree(display.triangles_center)
        self.ray = trimesh.ray.ray_triangle.RayMeshIntersector(display)
        layout = QVBoxLayout(self)
        instructions = self.instructions = QLabel("Paint visible faces with left-drag; Shift erases. Alt+left-drag or Orbit mode rotates; wheel zooms. "
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
        for label, callback in (("Undo", self.undo), ("Clear", lambda: self.fill(False)),
                                ("Select all", lambda: self.fill(True)), ("Load…", self.load), ("Export…", self.export)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            bar.addWidget(button)
        bar.addStretch(1)
        layout.addLayout(bar)
        if gl is None:
            raise ValueError("OpenGL is required for visual painting. Use a saved selection JSON through the CLI instead.")
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
        self.redraw()

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
                selected = se.decode_selection(self.original, Path(path).read_text(encoding="utf-8-sig"))
                self.remember()
                self.selected = selected
                self.redraw()
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Selection", str(exc))

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export surface selection", "surface-selection.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(se.encode_selection(self.original, self.selected), encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Selection", str(exc))

    def redraw(self):
        self.canvas.recolor()
        count = int(self.selected.sum())
        area = float(self.mesh.area_faces[self.selected].sum() / self.mesh.area * 100)
        self.status.setText(f"{count:,} / {len(self.selected):,} faces selected · {area:.1f}% of source surface area")

    def paint(self, x, y, erase=False):
        origin, direction = self.canvas.rays(np.array([[x, y]]))
        ids, rays, locations = self.ray.intersects_id(origin, direction, multiple_hits=False, return_locations=True)
        if not len(ids):
            return
        point = locations[0]
        # Convert pixel radius at the picked depth to a local radius; candidates are verified by exact rays.
        screen = self.canvas.project(np.array([point]))[0]
        a = self.canvas.unproject(x, y, screen[2])
        b = self.canvas.unproject(x + self.radius.value(), y, screen[2])
        nearby = np.array(self.tree.query_ball_point(point, np.linalg.norm(b - a)), dtype=np.int64)
        nearby = np.unique(np.r_[nearby, ids[0]])
        projected = self.canvas.project(self.mesh.triangles_center[nearby])
        inside = np.linalg.norm(projected[:, :2] - [x, y], axis=1) <= self.radius.value()
        nearby = nearby[inside]
        if len(nearby):
            origins, directions = self.canvas.rays(projected[inside, :2])
            hits, indexes = self.ray.intersects_id(origins, directions, multiple_hits=False)
            visible = nearby[np.asarray(indexes)[np.asarray(hits) == nearby[np.asarray(indexes)]]]
        else:
            visible = np.array([], dtype=np.int64)
        self.selected[np.r_[visible, ids[0]]] = not erase
        self.redraw()


if gl is not None:
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
            # Modal windows may have independent GL contexts. Never reuse or reset Atlas's shared shader.
            from pyqtgraph.opengl import shaders
            template = shaders.getShaderProgram(SHADER)
            name = f"surface-eject-{id(self)}"
            self.private_shader = shaders.ShaderProgram(name, [type(shader)(shader.code) for shader in template.shaders])
            shaders.ShaderProgram.names.pop(name)
            self.setMinimumSize(400, 260)
            self.setMouseTracking(True)
            self.setFocusPolicy(Qt.StrongFocus)
            c = qcolor(editor.theme["bg-1"])
            self.setBackgroundColor((c.red(), c.green(), c.blue(), 255))
            self.opts["center"] = QVector3D(*editor.mesh.bounds.mean(0))
            self.setCameraPosition(distance=float(np.linalg.norm(editor.mesh.extents)) * 1.5, elevation=18, azimuth=-65)
            self.guide = BrushGuide(self)

        def initializeGL(self):
            self._guarded(self._set_up_context)

        def _set_up_context(self):
            super().initializeGL()
            self.private_shader.prog = None
            if self.item is not None:
                self.removeItem(self.item)
                self.item = None
            self.recolor()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self.guide.setGeometry(self.rect())
            self.guide.raise_()

        def leaveEvent(self, event):
            self.cursor_pos = None
            self.guide.update()
            super().leaveEvent(event)

        def matrix(self):
            rect = (0, 0, max(self.width(), 1), max(self.height(), 1))
            return np.asarray((self.projectionMatrix(rect, rect) * self.viewMatrix()).data()).reshape(4, 4, order="F")

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

        def recolor(self):
            from pyqtgraph.opengl.MeshData import MeshData
            e = self.editor
            base, accent = qcolor(e.theme["text-2"]), qcolor(e.theme["accent"])
            colors = np.tile([base.redF(), base.greenF(), base.blueF(), 1.], (len(e.selected), 1))
            colors[e.selected] = [accent.redF(), accent.greenF(), accent.blueF(), 1.]
            data = MeshData(vertexes=np.asarray(e.mesh.vertices, np.float32), faces=e.mesh.faces, faceColors=colors)
            if self.item is None:
                self.item = gl.GLMeshItem(meshdata=data, smooth=False, shader=self.private_shader, glOptions="opaque")
                self.addItem(self.item)
            else:
                self.item.setMeshData(meshdata=data)

        def mousePressEvent(self, event):
            if (event.button() == Qt.LeftButton and self.editor.mode.currentText() != "Orbit"
                    and not event.modifiers() & Qt.AltModifier):
                self.painting = True
                self.editor.remember()
                self.editor.paint(event.position().x(), event.position().y(),
                                  self.editor.mode.currentText() == "Erase" or bool(event.modifiers() & Qt.ShiftModifier))
                event.accept()
            else:
                super().mousePressEvent(event)

        def mouseMoveEvent(self, event):
            self.cursor_pos = [event.position().x(), event.position().y()]
            self.guide.update()
            if self.painting:
                self.editor.paint(event.position().x(), event.position().y(),
                                  self.editor.mode.currentText() == "Erase" or bool(event.modifiers() & Qt.ShiftModifier))
                event.accept()
            else:
                super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event):
            if self.painting:
                self.painting = False
                event.accept()
            else:
                super().mouseReleaseEvent(event)
