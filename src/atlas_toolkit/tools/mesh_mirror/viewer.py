"""Live GPU reflection over the same manual plane controls as Plane Cut."""
import numpy as np

from atlas_toolkit.geom.plane import bounds_corners, reference_origin, reflect_points
from atlas_toolkit.ui.widgets.plane_editor import PlaneEditor


class MirrorView(PlaneEditor):
    flip_text = "Other side"
    flip_help = "Move the plane to the opposite side of the model centre. X"
    ghost_text = "Reference"
    ghost_help = "In mirror-only mode, show the original as a transparent reference; it is not exported."
    preview_text = "Preview mirror"
    preview_note = "Original: white · reflection: cyan"

    def flip(self):
        if self.bounds is not None:
            origin, normal, _ = self.frame()
            opposite = 2 * self.bounds.mean(axis=0) - origin
            position = opposite - reference_origin(self.bounds, self.values["reference"]) - normal * self.values["offset"]
            self.commit({"position": tuple(position)})

    def describe_scene(self):
        return "Keep original + reflection; separate shells" if self.values["result_mode"] == "copy" else "Reflection only; reference is not exported"

    def scene_bounds(self):
        points = bounds_corners(self.bounds)
        origin, normal, _ = self.frame()
        reflected = reflect_points(points, origin, normal)
        if self.values["result_mode"] == "copy" or self.ghost.isChecked():
            reflected = np.vstack((points, reflected))
        return np.array([reflected.min(axis=0), reflected.max(axis=0)])

    def update_scene(self, origin, normal, span):
        keep_original = self.values["result_mode"] == "copy"
        self.ghost.setEnabled(not keep_original)
        for index, item in enumerate(self.mesh_items):
            shader = item.shader()
            shader["cut"] = [0.] * 4
            shader["clip_enabled"] = [0.]
            shader["reflection"] = [*normal, -float(np.dot(origin, normal))]
            shader["reflect_mesh"] = [float(index == 1)]
            shader["band"] = [0.]
            self.style_item(item, "highlight" if index == 1 else "paper", 1. if index == 1 or keep_original else .12)
            item.setVisible(index == 1 or keep_original or self.ghost.isChecked())
            item.resetTransform()
