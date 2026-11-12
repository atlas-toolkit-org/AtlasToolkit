"""Plane Cut's scene styling over the shared manual plane editor."""
import numpy as np

from atlas_toolkit.ui.widgets.plane_editor import PlaneEditor


class PlaneView(PlaneEditor):
    flip_text = "Flip side"
    flip_help = "Swap the kept/primary side. X"
    ghost_help = "Show the discarded side as a transparent ghost."
    preview_text = "Preview cut"
    preview_note = "Preview computes final caps"

    def flip(self):
        self.commit({"side": "negative" if self.values["side"] == "positive" else "positive"})

    def describe_scene(self):
        if self.values["cut_type"] == "slice":
            return "Both sides kept"
        return f"Keep {'+' if self.values['side'] == 'positive' else '−'} normal side; ghost is discarded"

    def update_scene(self, origin, normal, span):
        side = 1 if self.values["side"] == "positive" else -1
        slicing = self.values["cut_type"] == "slice"
        for index, item in enumerate(self.mesh_items):
            sign = side if index == 0 else -side
            shader = item.shader()
            shader["cut"] = [*(normal * sign), -np.dot(origin, normal) * sign]
            shader["clip_enabled"] = [1.0]
            shader["reflection"] = [0.0] * 4
            shader["reflect_mesh"] = [0.0]
            shader["band"] = [span * .002]
            self.style_item(item, "paper" if index == 0 else "highlight", 1.0 if index == 0 or slicing else .12)
            item.setVisible(index == 0 or slicing or self.ghost.isChecked())
            item.resetTransform()
            if slicing and self.values["separation"]:
                item.translate(*map(float, normal * sign * self.values["separation"] * .5))
