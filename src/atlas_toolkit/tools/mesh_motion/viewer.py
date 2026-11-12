"""Motion's Selection tab reuses Surface Eject's visible-face paint dialog unchanged."""
from pathlib import Path
import numpy as np
import trimesh
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QDialog, QMessageBox
from atlas_toolkit.geom import frame3d, mesh as gm
from atlas_toolkit.ui.widgets.mesh_view import MeshView, gl
from atlas_toolkit.tools.mesh_surface_eject.viewer import SelectionDialog
from .selection import selection_text, encode_selection, decode_selection
from . import TOOL


class SelectionView(QWidget):
    title = "Selection"
    info_changed = Signal(str)

    def __init__(self, page):
        super().__init__()
        self.page, self.theme, self.path = page, page.theme, None
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
        preview = QPushButton("Preview motion")
        preview.clicked.connect(lambda: page.preview_selected())
        row.addWidget(preview)
        layout.addLayout(row)
        self.model = MeshView(self.theme)
        layout.addWidget(self.model, 1)
        self.refresh()

    def set_input(self, path, info):
        self.path = Path(path) if path else None
        self.values = dict(self.page.values())
        self.model.show_mesh(info.preview["views"][0]["mesh"] if info and info.preview else None)
        self.refresh()

    def values_changed(self, values):
        previous = self.values.get("influence")
        self.values = dict(values)
        self.refresh()
        if previous != "paint" and values.get("influence") == "paint" and self.path:
            QTimer.singleShot(0, self.open_editor)

    def refresh(self):
        self.note.setText("Choose Region → Painted area, then paint the source faces that should move. "
                          "Sliced layers samples the silhouette into plates/bars and grows wakes from painted edges. "
                          "Connected surface keeps unpainted skin fixed. Paint feather softens the painted boundary. "
                          "Selections belong to the exact input mesh. In Workflows use Aim in tool, then Back to workflow.")
        self.paint_button.setEnabled(self.path is not None and gl is not None)
        self.info_changed.emit(self.info_text())

    def info_text(self):
        return "Motion Sculpture · " + self.values.get("influence", "all") + " selection"

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
                selected = decode_selection(original, text) if text else np.zeros(len(original.faces), bool)
            except (ValueError, OSError) as exc:
                selected = np.zeros(len(original.faces), bool)
                warning = str(exc) + " Start a new selection below."
            dialog = SelectionDialog(original, display, selected, self.theme, self)
            dialog.setWindowTitle("Motion Sculpture — paint motion area")
            if warning:
                dialog.instructions.setText(warning + "\n" + dialog.instructions.text())
            if dialog.exec() == QDialog.Accepted:
                if not dialog.selected.any():
                    raise ValueError("The selection is empty. Paint an area before applying it.")
                self.page.form.set_values({"influence": "paint", "selection": encode_selection(original, dialog.selected),
                                           "selection_file": ""})
                self.values = dict(self.page.values())
                self.page._settings_changed()
                self.refresh()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Motion selection", str(exc))

    def validation_error(self, path, mode="run"):
        p = self.page.values()
        if p.get("influence") == "paint" and not (p.get("selection") or p.get("selection_file")):
            return "Paint an area in the Selection tab before sculpting."
        return ""

    def set_theme(self, theme):
        self.theme = theme
        self.model.set_theme(theme)

    def set_art(self, art):
        self.model.set_art(art)

    def fit(self):
        self.model.fit()
