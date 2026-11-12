"""Plotter Vision: the same lines as plotter.vision on a reference scene (its own output, rendered by the original
JavaScript), then what it does better: exact hiding (piercing edges, tiny faces, the near plane), no diagonals on
flat faces, lines where parts cross, the frame, layers, and the live view driven by mouse and keys."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import math
import os
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from atlas_toolkit.tools.plotter_vision import TOOL
from atlas_toolkit.tools.plotter_vision import hidden as hl
from atlas_toolkit.tools.plotter_vision import model as pm
from atlas_toolkit.tools.plotter_vision import plotter_vision as pv
from atlas_toolkit.tools.plotter_vision.camera import Camera, frame_for

FIX = Path(__file__).parent / "fixtures"


def values(**kw):
    return TOOL.values(kw)


def pieces(mdl, cam, region=(-2, -2, 2, 2), flat=0.01, sides="auto", hidden=False):
    view = pv.View(mdl, cam, flat, sides)
    out = [b for b in view.pieces(region, hidden=hidden)]
    vis = [(P0, P1) for P0, P1, _, _, h in out if not h]
    if not vis:
        return np.zeros((0, 2)), np.zeros((0, 2)), view
    return np.concatenate([v[0] for v in vis]), np.concatenate([v[1] for v in vis]), view


def samples(P0, P1, step):
    out = []
    for p, q in zip(P0, P1):
        n = max(2, int(np.hypot(*(q - p)) / step) + 1)
        out.append(p + np.linspace(0, 1, n)[:, None] * (q - p))
    return np.concatenate(out) if out else np.zeros((0, 2))


class FaithfulTests(unittest.TestCase):
    def test_same_lines_as_plotter_vision_on_the_reference_scene(self):
        # tests/fixtures/pv_scene_plotter_vision.svg is plotter.vision's own download for pv_scene.stl at its start
        # view (θ 70°, ψ -150°, r 170) in a 1910 x 889 window, made by running its JavaScript unchanged
        mdl = pm.load(FIX / "pv_scene.stl", 0, False)
        cam = Camera(70.0, -150.0, 170.0, (0.0, 0.0, 0.0), 60.0, False)
        W, H = 1910, 889
        scale = 1000.0 / math.tan(math.radians(30.0)) / (200.0 / 199.0)   # its px per unit of x/depth
        f = scale / cam.k
        P0, P1, _ = pieces(mdl, cam, (-W / 2 / f, -H / 2 / f, W / 2 / f, H / 2 / f), sides="one")
        ours = [(np.array([W / 2 + a[0] * f, H / 2 - a[1] * f]), np.array([W / 2 + b[0] * f, H / 2 - b[1] * f]))
                for a, b in zip(P0, P1)]
        d = re.search(r"d='(.*?)'", (FIX / "pv_scene_plotter_vision.svg").read_text(), re.S).group(1)
        ref = []
        for sub in re.split(r"\bM\b", d):
            pts = [np.array(tuple(map(float, m))) for m in re.findall(r"(-?[\d.]+),(-?[\d.]+)", sub)]
            ref += list(zip(pts, pts[1:]))
        so = samples(np.array([a for a, _ in ours]), np.array([b for _, b in ours]), 1.0)
        sr = samples(np.array([a for a, _ in ref]), np.array([b for _, b in ref]), 1.0)
        self.assertGreater(np.mean(cKDTree(sr).query(so)[0] <= 1.5), 0.995)       # nothing it would not draw
        self.assertGreater(np.mean(cKDTree(so).query(sr)[0] <= 1.5), 0.995)       # and all it draws


def box_model(*boxes, crossings=True):
    m = trimesh.util.concatenate([trimesh.creation.box(size).apply_translation(at) for size, at in boxes])
    return pm.prepare(m, 0, True, crossings=crossings)


class EdgeTests(unittest.TestCase):
    def test_a_cube_shows_nine_edges_and_no_diagonals(self):
        mdl = box_model(((10, 10, 10), (0, 0, 0)))
        P0, P1, view = pieces(mdl, Camera(60.0, 40.0, 300.0))
        self.assertEqual(view.edges, 9)                     # 3 faces seen: 3 inner edges + 6 around
        self.assertEqual(len(P0), 9)

    def test_the_angle_decides_which_edges_show(self):
        mdl = pm.prepare(trimesh.creation.icosphere(3), 0, True)
        cam = Camera(70.0, 20.0, 300.0)
        n_all = pv.View(mdl, cam, 0.01).edges
        n_feat = pv.View(mdl, cam, 30.0).edges
        self.assertGreater(n_all, 10 * n_feat)              # a sphere has no creases: only its outline is left
        self.assertTrue(pv.View(mdl, cam, 30.0).outline.all())

    def test_lines_where_parts_cross(self):
        mdl = box_model(((10, 10, 10), (0, 0, 0)), ((10, 10, 10), (5, 5, 5)))
        self.assertGreater(len(mdl.X), 0)
        # boxes [-5, 5]³ and [0, 10]³, normalised: centre 2.5, 100/15 units per model unit
        s = 100.0 / 15.0
        boxes = [((-5 - 2.5) * s, (5 - 2.5) * s), ((0 - 2.5) * s, (10 - 2.5) * s)]
        P = mdl.X.reshape(-1, 3)
        for lo, hi in boxes:                       # every crossing point is on both boxes' surfaces
            inside = ((P >= lo - 1e-6) & (P <= hi + 1e-6)).all(1)
            on_face = (np.isclose(P, lo, atol=1e-6) | np.isclose(P, hi, atol=1e-6)).any(1)
            self.assertTrue((inside & on_face).all())
        self.assertEqual(len(box_model(((10, 10, 10), (0, 0, 0)), ((10, 10, 10), (5, 5, 5)), crossings=False).X), 0)


class HidingTests(unittest.TestCase):
    def two_plates(self, gap=40.0, plate=None):
        front = plate or trimesh.creation.box((60, 60, 1)).apply_translation((0, 0, gap / 2))
        back = trimesh.creation.box((20, 20, 1)).apply_translation((0, 0, -gap / 2))
        return pm.prepare(trimesh.util.concatenate([front, back]), 0, True)

    def test_a_plate_hides_what_is_behind_it(self):
        mdl = self.two_plates()
        P0, P1, _ = pieces(mdl, Camera(5.0, 0.0, 300.0))          # from above
        S = samples(P0, P1, 0.002)
        lim = np.abs(S).max()
        # only the big plate's square is drawn: every visible point is on its outline, none inside it
        inner = (np.abs(S) < lim * 0.9).all(1)
        self.assertFalse(inner.any())

    def test_tiny_faces_still_hide(self):
        # the front plate made of ~20 000 tiny faces (plotter.vision skipped faces under 2 px², so lines leaked)
        plate = trimesh.creation.box((60, 60, 1))
        for _ in range(5):
            plate = plate.subdivide()
        mdl = self.two_plates(plate=plate.apply_translation((0, 0, 20)))
        self.assertGreater(mdl.faces, 10000)
        P0, P1, view = pieces(mdl, Camera(5.0, 0.0, 300.0), flat=45.0)
        S = samples(P0, P1, 0.002)
        lim = np.abs(S).max()
        self.assertFalse(((np.abs(S) < lim * 0.9).all(1)).any())

    def test_an_edge_piercing_a_face_is_cut_where_it_enters(self):
        A, B = np.array([[-1.0, 0.0]]), np.array([[1.0, 0.0]])
        qa, qb = np.array([2.0]), np.array([0.5])                # nearer at A, farther at B
        T = np.array([[[-2.0, -1.0], [2.0, -1.0], [0.0, 2.0]]])
        TQ = np.array([[1.0, 1.0, 1.0]])                          # a flat face at nearness 1
        out = list(hl.solve(A, B, qa, qb, np.array([[-1, -1]]), T, TQ, np.array([7]), (-3, -3, 3, 3)))
        s, t0, t1, _ = out[0]
        # nearness 2 - 1.5 t falls below 1 at t = 2/3: visible before, hidden after
        self.assertEqual(len(t0), 1)
        self.assertAlmostEqual(t0[0], 0.0, places=6)
        self.assertAlmostEqual(t1[0], 2.0 / 3.0, places=4)

    def test_faces_crossing_the_near_plane_are_cut_not_dropped(self):
        T = np.array([[[0.0, 0.0, -1.0], [1.0, 0.0, 5.0], [0.0, 1.0, 5.0]]])
        tri, face = hl.clip_triangles(T, np.array([3]), 0.5)
        self.assertEqual(len(tri), 2)                             # one corner behind: a quad, two triangles
        self.assertTrue((tri[:, :, 2] >= 0.5 - 1e-9).all())
        self.assertTrue((face == 3).all())

    def test_lines_are_cut_at_the_frame_in_mm(self):
        mdl = box_model(((10, 10, 10), (0, 0, 0)))
        p = values(radius=120.0, paper="A5", orientation="landscape", margin=20.0)
        out = pv.render(mdl, p)
        x0, y0, w, h = out["frame"].area
        pts = np.array([xy for l in out["layers"] for pl in l.polylines for xy in pl])
        self.assertTrue(len(pts))
        self.assertTrue(((pts[:, 0] >= x0 - 1e-6) & (pts[:, 0] <= x0 + w + 1e-6)).all())
        self.assertTrue(((pts[:, 1] >= y0 - 1e-6) & (pts[:, 1] <= y0 + h + 1e-6)).all())
        self.assertGreater(np.isclose(pts[:, 0], x0, atol=1e-3).sum() + np.isclose(pts[:, 0], x0 + w, atol=1e-3).sum()
                           + np.isclose(pts[:, 1], y0, atol=1e-3).sum() + np.isclose(pts[:, 1], y0 + h, atol=1e-3).sum(),
                           0)                                     # some lines do reach the frame and stop there


class LayerTests(unittest.TestCase):
    def setUp(self):
        self.mdl = pm.prepare(trimesh.util.concatenate([
            trimesh.creation.box((20, 20, 20)), trimesh.creation.box((20, 20, 20)).apply_translation((0, 60, 0))]),
            0, True)

    def names(self, **kw):
        return [l.label for l in pv.render(self.mdl, values(**kw))["layers"]]

    def test_layers(self):
        self.assertEqual(self.names(), ["lines"])
        sphere = pm.prepare(trimesh.creation.icosphere(3), 0, True)          # depth all the way through
        self.assertEqual([l.label for l in pv.render(sphere, values(depth_layers=3))["layers"]],
                         ["near", "middle", "far"])
        self.assertEqual(self.names(depth_layers=3), ["near", "far"])       # empty bands write no layer
        self.assertIn("outline", self.names(outline_layer=True))
        self.assertIn("hidden", self.names(show_hidden=True))
        cube = box_model(((10, 10, 10), (0, 0, 0)))                         # a lone cube: its 3 back edges
        P0, P1, view = pieces(cube, Camera(60.0, 40.0, 300.0))
        out = [b for b in pv.View(cube, Camera(60.0, 40.0, 300.0), 0.01, back_edges=True).pieces((-2, -2, 2, 2),
                                                                                              hidden=True)]
        vis = sum(len(b[0]) for b in out if not b[4])
        hid = sum(len(b[0]) for b in out if b[4])
        self.assertEqual((vis, hid), (9, 3))
        stereo = pv.render(self.mdl, values(stereo=True))["layers"]
        self.assertEqual([l.label for l in stereo], ["red lines", "blue lines"])
        self.assertEqual({l.color for l in stereo}, {pv.RED, pv.BLUE})

    def test_svg_is_signed_in_mm_with_one_group_per_pen(self):
        from atlas_toolkit.geom.svgout import write_svg
        out = pv.render(self.mdl, values(depth_layers=2, paper="A4"))
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "x_vision.svg"
            write_svg(dst, *out["sheet"], out["layers"], TOOL, {})
            text = dst.read_text(encoding="utf-8")
        self.assertIn('width="210mm"', text)
        self.assertIn("<desc>plotter-vision</desc>", text)
        self.assertEqual(text.count("inkscape:groupmode=\"layer\""), 2)
        self.assertIn(pv.PEN_COLORS["far"], text)                            # the far pen has its own colour


class FitTests(unittest.TestCase):
    def test_fit_puts_the_whole_model_in_the_frame(self):
        mdl = pm.prepare(trimesh.creation.box((10, 30, 50)), 0, True)
        frame = frame_for("A4", "landscape", 10.0)
        cam = Camera(70.0, -150.0, 170.0)
        cam.radius = pv.fit_radius(mdl, cam, frame)
        S = cam.project(cam.to_camera(mdl.V))
        x0, y0, x1, y1 = frame.screen
        self.assertTrue(((S[:, 0] > x0) & (S[:, 0] < x1) & (S[:, 1] > y0) & (S[:, 1] < y1)).all())


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class LiveViewTests(unittest.TestCase):
    """The Vision tab in the real page, offscreen, with mouse and keys."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="atlas_pv_"))
        cls.leave_home = _isolate.fresh_home()
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from atlas_toolkit.core.history import History
        from atlas_toolkit.core.registry import registry
        from atlas_toolkit.core.settings import AppSettings
        from atlas_toolkit.ui.bridge import JobBridge
        from atlas_toolkit.ui.context import AppContext
        from atlas_toolkit.ui.shell import MainWindow
        from atlas_toolkit.ui.theme import load_fonts
        load_fonts()
        cls.ctx = AppContext(registry(), AppSettings(cls.tmp / "cfg"), JobBridge(), History(cls.tmp / "h.jsonl"))
        cls.win = MainWindow(cls.ctx)
        cls.win.resize(1400, 900)

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        cls.ctx.bridge.shutdown()
        cls.leave_home()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def wait(self, cond, timeout=120.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            self.app.processEvents()
            if cond():
                return
            time.sleep(0.02)
        self.fail("timed out")

    def mouse(self, w, kind, x, y, buttons, mods=None):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        types = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove, "release": QEvent.MouseButtonRelease}
        btn = Qt.NoButton if kind == "move" else buttons
        held = Qt.NoButton if kind == "release" else buttons
        e = QMouseEvent(types[kind], QPointF(x, y), w.mapToGlobal(QPointF(x, y)), btn, held,
                        mods or Qt.NoModifier)
        self.app.sendEvent(w, e)

    def key(self, w, k):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        self.app.sendEvent(w, QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier))

    def test_a_settings_update_already_waiting_does_not_undo_a_key_press(self):
        """The page tells the viewer about settings a moment after they change. If the camera moved again in between, those
        settings are out of date: taking them would lose the key press, and then write the old camera back for good."""
        from PySide6.QtCore import Qt
        src = self.tmp / "waiting.stl"
        shutil.copy(FIX / "pv_scene.stl", src)
        page = self.win.open_tool("plotter-vision", [str(src)])
        self.win.show()
        self.wait(lambda: page.runnable() and page.idle())
        v = page.viewer
        self.wait(lambda: v.state == "done")
        self.key(v, Qt.Key_1)
        self.wait(lambda: page.values()["psi"] == 0.0 and not v.commit.isActive())
        waiting = page.values()
        self.key(v, Qt.Key_2)
        self.assertTrue(v.commit.isActive())       # the camera has moved, and the settings have not heard yet
        v.values_changed(waiting)
        self.assertEqual(v.cam.psi, 90.0)
        self.wait(lambda: page.values()["psi"] == 90.0)

    def test_aim_with_mouse_and_keys_then_draw(self):
        from PySide6.QtCore import Qt
        src = self.tmp / "scene.stl"
        shutil.copy(FIX / "pv_scene.stl", src)
        page = self.win.open_tool("plotter-vision", [str(src)])
        self.win.show()
        self.wait(lambda: page.runnable() and page.idle())
        v = page.viewer
        self.assertIsNotNone(v)
        self.assertEqual(page.stage.current_title(), "Vision")
        self.wait(lambda: v.state == "done")
        self.assertTrue(v.pieces)
        # drag 40 px right: ψ turns by 40 x 0.29° as on plotter.vision, and the camera lands in the settings
        psi0 = page.values()["psi"]
        self.mouse(v, "press", 200, 200, Qt.LeftButton)
        self.mouse(v, "move", 220, 200, Qt.LeftButton)
        self.mouse(v, "move", 240, 200, Qt.LeftButton)
        self.assertEqual(v.state, "moving")
        self.mouse(v, "release", 240, 200, Qt.LeftButton)
        self.wait(lambda: abs(page.values()["psi"] - (psi0 + 40 * math.degrees(0.005))) < 0.06)
        self.wait(lambda: v.state == "done")
        # Shift-drag moves the look-at point, not the angles
        psi1 = page.values()["psi"]
        self.mouse(v, "press", 200, 200, Qt.LeftButton, Qt.ShiftModifier)
        self.mouse(v, "move", 260, 230, Qt.LeftButton, Qt.ShiftModifier)
        self.mouse(v, "release", 260, 230, Qt.LeftButton, Qt.ShiftModifier)
        self.wait(lambda: tuple(page.values()["lookat"]) != (0.0, 0.0, 0.0))
        self.assertAlmostEqual(page.values()["psi"], psi1, places=3)
        # 5: from above; F: fit (look at the centre, a new distance)
        self.key(v, Qt.Key_5)
        self.wait(lambda: abs(page.values()["theta"] - 1.0) < 1e-6)
        r = page.values()["radius"]
        self.key(v, Qt.Key_F)
        self.wait(lambda: page.values()["radius"] != r and tuple(page.values()["lookat"]) == (0.0, 0.0, 0.0))
        self.wait(lambda: v.state == "done")
        # a preset keeps the camera
        theta = page.values()["theta"]
        lib = self.ctx.presets
        lib.extra_dirs.append(Path(__file__).resolve().parent / "fixtures" / "presets" / "test-pack")
        lib.reload()
        lib.notify(None)
        self.addCleanup(lambda: (lib.extra_dirs.pop(), lib.reload(), lib.notify(None)))
        page.apply_preset("Test features")
        self.app.processEvents()
        self.assertEqual(page.values()["theta"], theta)
        self.assertEqual(page.values()["flat_angle"], 35.0)
        # Draw writes the view that is on screen
        page.run_all()
        self.wait(lambda: not page.busy() and page.idle())
        out = src.with_name("scene_vision.svg")
        self.assertTrue(out.exists())
        self.assertIn(b"<desc>plotter-vision</desc>", out.read_bytes()[:800])
        text = out.read_text(encoding="utf-8").replace("&quot;", '"')
        self.assertIn('"theta": 1.0', text)                     # the settings record holds the camera


class PresetTests(unittest.TestCase):
    def test_sketch_is_the_aim_view(self):
        """The Sketch preset draws what the Aim tab of the 3D tools shows."""
        from atlas_toolkit.tools.plotter_vision.aim import AimView
        sketch = _presets.raw(_presets.preset(TOOL, "Sketch"))
        for k, v in sketch.items():
            self.assertEqual(TOOL.param(k).validate(v), TOOL.param(k).validate(AimView.DEFAULTS[k]), k)


if __name__ == "__main__":
    unittest.main()
