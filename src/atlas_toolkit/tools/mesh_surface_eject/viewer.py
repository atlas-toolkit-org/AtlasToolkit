"""Contract-hosted selection tab; the paint dialog itself is shared (`ui/widgets/face_paint.py`).

The orbit camera stays local to the editor. The committed face mask is a portable schema value, so CLI,
history, presets and Workflows execute the same selection without depending on Qt or a running editor.
"""
import json
from pathlib import Path
import numpy as np
import trimesh

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from atlas_toolkit.geom import frame3d
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.ui.widgets.face_paint import SelectionDialog
from atlas_toolkit.ui.widgets.mesh_view import MeshView, gl
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
        self.whole_if_paint_is_elsewhere(info)
        if info and info.preview:
            self.model.show_mesh(info.preview["views"][0]["mesh"])
        else:
            self.model.show_mesh(None)
        self.refresh()

    def whole_if_paint_is_elsewhere(self, info):
        """A painted selection belongs to one exact mesh. When the remembered settings say Painted area but this
        input has no selection of its own (another model's, or none), act on the whole surface instead."""
        if self.values.get("region") != "paint" or self.values.get("selection_file") or info is None:
            return
        faces = (info.values or {}).get("faces")
        try:
            painted_faces = json.loads(self.values.get("selection") or "{}").get("faces")
        except (ValueError, AttributeError):
            painted_faces = None
        if painted_faces is not None and painted_faces == faces:
            return
        self.page.form.set_values({"region": "whole", "selection": ""})
        self.values = dict(self.page.values())
        self.page._settings_changed()
        self.page.ctx.log("info", "Surface Eject: the painted area belonged to another model, so this one starts "
                          "on the whole surface. Open Paint / select area to paint it.", "mesh-surface-eject")

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
            dialog = SelectionDialog(original, display, selected, self.theme, self, codec=(se.encode_selection, se.decode_selection))
            dialog.setWindowTitle("Surface Eject — paint extraction area")
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
