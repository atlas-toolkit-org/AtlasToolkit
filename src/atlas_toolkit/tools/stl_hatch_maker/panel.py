"""
Inspector panel for the Hatch Maker (a tool's optional custom UI): take the drawing's view from the 3D viewer, or
turn the viewer to the view set in the fields. Orbit the model in the Input tab, then "Use the 3D view".
"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from atlas_toolkit.ui.widgets.common import hint


class ViewPanel(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        take = QPushButton("Use the 3D view")
        take.setToolTip("Set azimuth and elevation from the 3D viewer (orbit the model in the Input tab first).")
        take.clicked.connect(self.take)
        show = QPushButton("Show this view")
        show.setToolTip("Turn the 3D viewer to the azimuth and elevation above.")
        show.clicked.connect(self.show_view)
        row.addWidget(take, 1)
        row.addWidget(show, 1)
        lay.addLayout(row)
        lay.addWidget(hint("The drawing is made from exactly this view: orbit the model, then take its view."))

    def _viewer(self):
        stage = self.page.stage
        mv = stage.mesh_view
        if mv is None or stage.stack.currentWidget() is not mv:
            titles = [stage.tabs.tabText(i) for i in range(stage.tabs.count())]
            if "Input" in titles:
                stage.tabs.setCurrentIndex(titles.index("Input"))
                mv = stage.mesh_view
        return mv

    def take(self) -> None:
        mv = self._viewer()
        if mv is None:
            self.page.ctx.toast("Add a model first: its 3D view appears in the Input tab.", "warning")
            return
        az, el = mv.camera()
        az = (az + 180.0) % 360.0 - 180.0
        self.page.form.set_values({"azimuth": round(az, 1), "elevation": round(el, 1)})
        self.page._settings_changed()
        self.page.ctx.toast(f"View set: azimuth {az:.1f}°, elevation {el:.1f}°.", "success")

    def show_view(self) -> None:
        mv = self._viewer()
        if mv is not None:
            v = self.page.values()
            mv.set_camera(v["azimuth"], v["elevation"])
