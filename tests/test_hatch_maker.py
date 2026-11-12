"""Hatch Maker v6: the view matches the 3D viewer, every style draws, hidden lines go, output is mm on paper."""
import _isolate  # first: settings and caches stay in a temporary folder
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom.svg import read_drawing
from atlas_toolkit.tools.stl_hatch_maker import stl_hatch_maker as hm
from atlas_toolkit.tools.stl_hatch_maker.scene import Scene, camera_basis
from atlas_toolkit.tools.stl_hatch_maker.styles import STYLES

TOOL = registry().get("stl-hatch-maker")


def params(**kw):
    return TOOL.values({"draft_faces": 20000, **kw})


def model():
    return trimesh.creation.icosphere(subdivisions=4, radius=20.0)


class ViewTests(unittest.TestCase):
    def test_camera_matches_the_3d_viewer(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import pyqtgraph.opengl as gl
        from PySide6.QtGui import QVector3D
        w = gl.GLViewWidget()
        w.opts["center"] = QVector3D(0, 0, 0)
        for az, el in ((-90, 10), (30, 45), (170, -20)):
            w.setCameraPosition(distance=50, azimuth=az, elevation=el)
            m = np.array(w.viewMatrix().data()).reshape(4, 4).T     # world -> eye
            R = camera_basis(az, el)
            np.testing.assert_allclose(m[:3, :3], R, atol=1e-6)    # same right, up and viewing axes

    def test_front_view_puts_the_top_of_the_model_at_the_top_of_the_paper(self):
        cone = trimesh.creation.cone(radius=10, height=30)            # apex up (+Z)
        sc = Scene(cone, -90, 0, 0, "A4", "portrait", 20)
        apex = sc.P[np.argmax(cone.vertices[:, 2])]
        self.assertLess(apex[1], sc.P[:, 1].mean())                   # paper y grows downwards

    def test_fits_inside_the_margins(self):
        sc = Scene(model(), 0, 0, 0, "A3", "landscape", 25)
        self.assertEqual(sc.sheet, (420.0, 297.0))
        self.assertGreaterEqual(sc.P.min(), 25 - 1e-6)
        self.assertLessEqual(sc.P[:, 1].max(), 297 - 25 + 1e-6)


class StyleTests(unittest.TestCase):
    def test_every_style_draws(self):
        m = model()
        for style in STYLES:
            if style == "none":
                continue
            with self.subTest(style=style):
                out = hm.render(m.copy(), params(style=style, spacing_dark=2.0, spacing_light=5.0, levels=8,
                                                 dots=10, spacing=3.0))
                hatch = next((l for l in out["layers"] if l.label == "hatching"), None)
                self.assertIsNotNone(hatch, f"{style} drew nothing")
                self.assertGreater(len(hatch.polylines), 3)

    def test_seed_repeats_the_drawing(self):
        m = model()
        a = hm.render(m.copy(), params(style="stipple", dots=10, seed=3))["layers"][0].polylines
        b = hm.render(m.copy(), params(style="stipple", dots=10, seed=3))["layers"][0].polylines
        c = hm.render(m.copy(), params(style="stipple", dots=10, seed=4))["layers"][0].polylines
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


class HiddenLineTests(unittest.TestCase):
    def test_a_sphere_hidden_behind_a_box_is_not_drawn(self):
        box = trimesh.creation.box(extents=(60, 10, 60))               # a wall facing the camera (-Y)
        ball = trimesh.creation.icosphere(subdivisions=3, radius=10)
        ball.apply_translation((0, 30, 0))                             # behind the wall
        scene = trimesh.util.concatenate([box, ball])
        out = hm.render(scene, params(style="contours", levels=12, silhouettes=True, elevation=0))
        sil = next(l for l in out["layers"] if l.label == "silhouettes")
        pts = np.concatenate([np.asarray(pl) for pl in sil.polylines])
        sc = out["scene"]
        cx, cy = sc.to_paper(np.array([[0.0, 0.0, 0.0]]))[0]
        r_mm = sc.mm(10)
        inside = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) < r_mm * 0.9   # nothing drawn where the hidden ball is
        self.assertFalse(inside.any())


class OutputTests(unittest.TestCase):
    def test_svg_is_mm_signed_layered(self):
        from atlas_toolkit.core.job import Job, execute
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "ball.stl"
            model().export(src)
            dst = Path(d) / "ball_hatch.svg"
            box = {}
            execute(TOOL, Job(TOOL.id, "run", str(src), str(dst), dict(params(style="screen", spacing=3.0))),
                    lambda k, p: box.__setitem__(k, p), lambda: False)
            self.assertIn("done", box, box.get("error"))
            text = dst.read_text(encoding="utf-8")
            self.assertIn('width="297mm"', text)
            self.assertIn("<desc>stl-hatch-maker</desc>", text[:600])
            self.assertIn('inkscape:groupmode="layer"', text)
            d = read_drawing(dst)
            self.assertEqual((round(d.width), round(d.height)), (297, 420))
            self.assertGreater(d.stats().ink, 100)


if __name__ == "__main__":
    unittest.main()
