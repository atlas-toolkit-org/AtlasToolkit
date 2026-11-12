"""The Selection tab: paint the area that crumbles, in Surface Eject's visible-face editor."""
from pathlib import Path

import numpy as np
import trimesh
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from atlas_toolkit.geom import frame3d
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_surface_eject import mesh_surface_eject as se
from atlas_toolkit.tools.mesh_surface_eject.viewer import SelectionDialog
from atlas_toolkit.ui.widgets.mesh_view import MeshView, gl

from . import TOOL
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
        self.paint_button = QPushButton("Paint the crumbling area…")
        self.paint_button.clicked.connect(self.open_editor)
        row.addWidget(self.paint_button)
        preview = QPushButton("Preview collapse")
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
        previous = self.values.get("area")
        self.values = dict(values)
        self.refresh()
        if (previous != "paint" and values.get("area") == "paint" and self.path
                and not (values.get("selection") or values.get("selection_file"))):
            QTimer.singleShot(0, self.open_editor)

    def refresh(self):
        area = self.values.get("area")
        if area == "paint":
            text = ("Blocks break out where you paint, as deep as Hollow depth. Painting is tied to this exact mesh; "
                    "in Workflows use Aim in tool and Back to workflow.")
        elif area == "whole":
            text = "The whole model crumbles: nothing to paint."
        else:
            text = "The side chosen in Area crumbles. Choose Area: Paint to paint it instead."
        self.note.setText(text)
        self.paint_button.setEnabled(self.path is not None and gl is not None)
        self.info_changed.emit(self.info_text())

    def info_text(self):
        return "Crumble · " + {"paint": "painted area", "whole": "whole model"}.get(self.values.get("area"), "one side")

    def open_editor(self):
        if self.path is None:
            return
        try:
            original = gm.load_mesh(self.path)
            if len(original.faces) > int(self.values["max_faces"]):
                raise ValueError("Input exceeds Maximum faces. Simplify it or raise the limit before painting.")
            up, front, rotate, _ = frame3d.resolve_axes(self.path, self.values, original.extents)
            frame = frame3d.fit_frame(original.vertices, up, front, rotate, mode="height", size=2.0)
            display = trimesh.Trimesh(frame.apply(original.vertices), original.faces, process=False)
            warning = ""
            try:
                text = selection_text(self.values)
                selected = se.decode_selection(original, text) if text else np.zeros(len(original.faces), bool)
            except (ValueError, OSError) as exc:
                selected = np.zeros(len(original.faces), bool)
                warning = str(exc) + " Start a new selection below."
            dialog = SelectionDialog(original, display, selected, self.theme, self)
            dialog.setWindowTitle("Crumble — paint the area")
            if warning:
                dialog.instructions.setText(warning + "\n" + dialog.instructions.text())
            if dialog.exec() == QDialog.Accepted:
                if not dialog.selected.any():
                    raise ValueError("The selection is empty. Paint an area before applying it.")
                self.page.form.set_values({"area": "paint", "selection": se.encode_selection(original, dialog.selected),
                                           "selection_file": ""})
                self.values = dict(self.page.values())
                self.page._settings_changed()
                self.refresh()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Crumble area", str(exc))

    def validation_error(self, path, mode="run"):
        p = self.page.values()
        if p["area"] == "paint" and not (p.get("selection") or p.get("selection_file")):
            return "Paint an area in the Selection tab first, or choose Area: Side."
        return ""

    def set_theme(self, theme):
        self.theme = theme
        self.model.set_theme(theme)

    def set_art(self, art):
        self.model.set_art(art)

    def fit(self):
        self.model.fit()
