"""Every tool with a painted area changes the model where it was painted, and nowhere else.

One check for all three (Motion Sculpture had one until 0.5.0): paint one side of the same solid, run the real engine (the exact JSON a painted selection is
stored as), and find the input faces that are no longer on the output surface (carved, ejected, moved, broken off).
Nearly all of them must lie on the painted side; paint the other side and the change must follow it there.
"""
import _isolate  # first: settings and caches stay in a temporary folder
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from atlas_toolkit.core.job import JobContext
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_surface_eject import mesh_surface_eject as se

SETTINGS = {   # quick settings that still do something visible; `key` is the tool's painted-area choice
    "mesh-surface-eject": dict(region="paint", count=4, size_min=8., size_max=10., depth_min=2., depth_max=3.,
                               distance_min=6., distance_max=8., connectors=False),
    "mesh-stairs": dict(mode="carve", placement="paint", stair_type="linear", steps=12, flights=1, depth=30.,
                        wall=3., resolution=40),
    "mesh-crumble": dict(area="paint", depth=10., collapse=100., block_size=14., rubble=0., resolution=40,
                         draft_resolution=40, stagger=0.3),
}


def solid():
    model = trimesh.creation.icosphere(subdivisions=3, radius=30.0)
    model.vertices *= [1.0, 0.85, 1.4]
    return model


class PaintedSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="painted_"))
        cls.src = cls.root / "solid.stl"
        solid().export(cls.src)
        cls.model = gm.load_mesh(cls.src)            # what the paint dialog sees: the exact loaded faces
        cls.centers = cls.model.triangles_center
        cls.areas = cls.model.area_faces

    def changed(self, tool_id, painted, name, margin=0.):
        """Area-weighted share of the changed input faces that were painted (or lie within `margin` mm of a painted
        face), and the changed faces."""
        from atlas_toolkit.core.registry import registry
        tool = registry().get(tool_id)
        values = dict(tool.defaults(), **SETTINGS[tool_id], selection=se.encode_selection(self.model, painted))
        dst = self.root / f"{tool_id}_{name}.stl"
        tool.engine_fn()(JobContext(lambda *_: None, lambda: False, "run"), self.src, dst, tool.values(values))
        out = gm.load_mesh(dst)
        distance = trimesh.proximity.closest_point(out, self.centers)[1]
        moved = distance > 0.5                       # mm: off the output surface
        self.assertGreater(self.areas[moved].sum(), 0.005 * self.areas.sum(), f"{tool_id} changed nothing")
        near = cKDTree(self.centers[painted]).query(self.centers)[0] <= margin
        return self.areas[moved & near].sum() / self.areas[moved].sum(), moved

    def check(self, tool_id, first, second, margin=0.):
        """Paint `first`, then `second` (disjoint areas): each run changes its own area (give or take `margin` mm)."""
        a = self.centers
        self.assertFalse((first(a) & second(a)).any(), "the two areas must not overlap")
        for name, painted, other in (("first", first(a), second(a)), ("second", second(a), first(a))):
            with self.subTest(tool=tool_id, painted=name):
                inside, moved = self.changed(tool_id, painted, name, margin)
                self.assertGreater(inside, 0.8, f"{tool_id}: only {inside:.0%} of the change is where it was painted")
                self.assertLess(self.areas[moved & other].sum(), 0.05 * self.areas[moved].sum(),
                                f"{tool_id}: changed the side that was not painted")

    # The two sides: top and bottom, or front-right and back-left for the tools that work on a side.
    def test_surface_eject_ejects_from_the_painted_area(self):
        self.check("mesh-surface-eject", lambda c: c[:, 2] > 15., lambda c: c[:, 2] < -15.)

    def test_stairs_carve_into_the_painted_area(self):
        self.check("mesh-stairs", lambda c: (c[:, 0] > 15.) & (np.abs(c[:, 2]) < 20.),
                   lambda c: (c[:, 0] < -15.) & (np.abs(c[:, 2]) < 20.))

    def test_crumble_breaks_the_painted_area(self):
        # Crumble breaks whole blocks: the break reaches the edges of the blocks the paint touches.
        self.check("mesh-crumble", lambda c: (c[:, 0] > 15.) & (np.abs(c[:, 2]) < 20.),
                   lambda c: (c[:, 0] < -15.) & (np.abs(c[:, 2]) < 20.), margin=SETTINGS["mesh-crumble"]["block_size"])


class StoredSelectionTests(unittest.TestCase):
    def test_the_settings_form_keeps_a_long_selection_whole(self):
        """Qt cuts a text field at 32 767 characters by default; a cut selection is invalid JSON (2.4.2)."""
        from PySide6.QtWidgets import QApplication
        from atlas_toolkit.tools.mesh_surface_eject import TOOL
        from atlas_toolkit.ui.theme import Theme
        from atlas_toolkit.ui.widgets.param_form import ParamForm
        QApplication.instance() or QApplication([])
        form = ParamForm(TOOL, Theme("dark"))
        long = "x" * 100_000
        form.set_values(dict(TOOL.defaults(), region="paint", selection=long))
        self.assertEqual(form.values()["selection"], long)


if __name__ == "__main__":
    unittest.main()
