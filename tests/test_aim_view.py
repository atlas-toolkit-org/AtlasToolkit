"""The 3D drawing tools in the real window, offscreen: the Aim tab on a stage set (one frame for every stage, F fits
the widest), the Hatch Maker's Aim (its own orthographic camera), the Input tab beside an Aim (inspect only, turns to
the aimed side), and the notice for a missing external program (Melt without Blender)."""
import _isolate  # first: settings and caches stay in a temporary folder
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import trimesh


class AimViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="atlas_aim_"))
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

    def test_aim_tab_uses_the_stage_set_frame(self):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        folder = self.tmp / "egg_stages"
        folder.mkdir()
        files = []
        for i, sx in enumerate((1.0, 1.6, 2.4)):
            m = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
            m.apply_scale((sx, 1.0, 1.3))
            p = folder / f"egg_stage_{i:02d}.stl"
            m.export(p)
            files.append(p)
        page = self.win.open_tool("contour-lines", [str(files[0])])
        self.win.show()
        self.wait(lambda: page.runnable() and page.idle())
        v = page.viewer
        self.assertIsNotNone(v)
        self.assertEqual(v.title, "Aim")
        self.wait(lambda: v.state == "done")
        # the model sits in the set's frame: the widest stage (2.4 x 20 wide) is 100 units, the first one 100 / 2.4
        width = float(np.ptp(v.model.V[:, 0]))
        self.assertAlmostEqual(width, 100.0 / 2.4, delta=0.5)
        self.app.sendEvent(v, QKeyEvent(QEvent.KeyPress, Qt.Key_F, Qt.NoModifier))
        self.wait(lambda: v.state in ("done", "computing", "moving"))
        r_fit = v.cam.radius
        self.assertGreater(r_fit, 1.0)
        stereo_before = page.values().get("stereo")
        self.app.sendEvent(v, QKeyEvent(QEvent.KeyPress, Qt.Key_T, Qt.NoModifier))     # no stereo here
        self.assertEqual(page.values().get("stereo"), stereo_before)

    def key(self, w, k):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        self.app.sendEvent(w, QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier))

    def test_input_tab_is_inspect_only_and_follows_aim(self):
        """Opening Input turns it to the side being aimed at, in the file's own axes (a Y-up OBJ here, so the frame's
        rotation counts), and says the view is set in Aim; the chip goes back to Aim."""
        src = self.tmp / "box.obj"
        trimesh.creation.box((10.0, 30.0, 6.0)).export(src)
        page = self.win.open_tool("contour-lines", [str(src)])
        self.win.show()
        self.wait(lambda: page.runnable() and page.idle())
        v, stage = page.viewer, page.stage
        self.wait(lambda: v.state == "done")
        self.assertEqual(stage.current_title(), "Aim")
        self.assertFalse(stage.btn_inspect.isVisible())
        self.assertFalse(stage.tabs.tabIcon(0).isNull())
        az, el = v.input_view()
        _eye, _u, _v, w = v.cam.basis()
        w_file = np.asarray(v._frame.R).T @ w                       # canonical = R @ file
        self.assertAlmostEqual(np.cos(np.radians(el)) * np.cos(np.radians(az)), w_file[0], places=6)
        self.assertAlmostEqual(np.cos(np.radians(el)) * np.sin(np.radians(az)), w_file[1], places=6)
        self.assertAlmostEqual(np.sin(np.radians(el)), w_file[2], places=6)
        titles = [stage.tabs.tabText(i) for i in range(stage.tabs.count())]
        stage.tabs.setCurrentIndex(titles.index("Input"))
        self.app.processEvents()
        self.assertTrue(stage.btn_inspect.isVisible())
        mv = stage.mesh_view
        if mv.view is not None:                                     # no OpenGL here: nothing to turn
            got = mv.camera()
            self.assertAlmostEqual((got[0] - az + 180.0) % 360.0 - 180.0, 0.0, places=3)
            self.assertAlmostEqual(got[1], el, places=3)
        stage.btn_inspect.click()
        self.assertEqual(stage.current_title(), "Aim")
        self.assertFalse(stage.btn_inspect.isVisible())

    def wheel(self, w, notches=1):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        self.app.sendEvent(w, QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(), QPoint(0, 120 * notches),
                                          Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))

    def drag(self, w, dx, dy, mods=None):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        x, y = 200, 200
        for kind, px, py, btn, held in ((QEvent.MouseButtonPress, x, y, Qt.LeftButton, Qt.LeftButton),
                                        (QEvent.MouseMove, x + dx, y + dy, Qt.NoButton, Qt.LeftButton),
                                        (QEvent.MouseButtonRelease, x + dx, y + dy, Qt.LeftButton, Qt.NoButton)):
            self.app.sendEvent(w, QMouseEvent(kind, QPointF(px, py), w.mapToGlobal(QPointF(px, py)), btn, held,
                                              mods or Qt.NoModifier))

    def test_hatch_maker_aims_like_the_other_tools(self):
        """The Hatch Maker's Aim: the shared controls (Shift-drag moves, the wheel sets the distance, 1-5, F, R),
        through an orthographic camera."""
        from PySide6.QtCore import Qt
        from atlas_toolkit.tools.stl_hatch_maker.aim import HatchAimView
        src = self.tmp / "slab.stl"
        trimesh.creation.box((40.0, 10.0, 20.0)).export(src)
        page = self.win.open_tool("stl-hatch-maker", [str(src)])
        self.win.show()
        self.wait(lambda: page.runnable() and page.idle())
        v = page.viewer
        self.assertIsInstance(v, HatchAimView)
        self.assertEqual(page.stage.current_title(), "Aim")
        self.wait(lambda: v.state == "done")
        self.assertTrue(v.cam.ortho)
        np.testing.assert_allclose(v.input_view(), (-90.0, 10.0), atol=1e-9)    # the front, a little above
        self.key(v, Qt.Key_2)
        self.wait(lambda: page.values()["psi"] == 90.0 and page.values()["theta"] == 90.0)
        r = page.values()["radius"]
        self.wheel(v)                                              # closer
        self.wait(lambda: page.values()["radius"] < r)
        self.drag(v, 40, 25, Qt.ShiftModifier)                     # move the look-at point
        self.wait(lambda: tuple(page.values()["lookat"]) != (0.0, 0.0, 0.0))
        self.key(v, Qt.Key_F)
        self.wait(lambda: not v.commit.isActive() and page.values()["radius"] != r)
        self.key(v, Qt.Key_R)
        self.wait(lambda: page.values()["theta"] == 70.0 and page.values()["psi"] == -150.0)

    def test_always_fit_toggles_with_a_and_lets_go_on_framing_by_hand(self):
        """A (or the button) fits after every move; A again, or Shift-drag / the wheel, goes back to framing by
        hand. It is view state: the settings only ever hold a camera."""
        from PySide6.QtCore import Qt
        from atlas_toolkit.geom.camera import roll_screen
        src = self.tmp / "tall.stl"
        trimesh.creation.box((10.0, 10.0, 40.0)).export(src)
        page = self.win.open_tool("plotter-vision", [str(src)])
        self.win.show()
        self.wait(lambda: page.runnable() and page.idle())
        v = page.viewer
        key = str(src.resolve())                                    # the page keys rows by resolved path (RUNNER~1, /private/var)
        page.queue.setCurrentItem(page.rows[key].item)
        self.wait(lambda: v.path == key and v.state == "done")

        def fills():
            S = roll_screen(v.cam.project(v.cam.to_camera(v.fit_points())), v._roll_deg())
            _, _, hx, hy = v._paper_frame().screen
            ext = (S.max(0) - S.min(0)) / 2.0 / np.array([hx, hy])
            return abs(ext.max() * 1.02 - 1.0) < 0.02 and np.abs((S.max(0) + S.min(0)) / 2.0).max() < 0.02

        self.key(v, Qt.Key_A)
        self.assertTrue(v.always_fit and v.btn_fit.isChecked())
        self.assertTrue(fills())
        self.drag(v, 60, 0)                                         # orbit: still filling the frame
        self.assertTrue(v.always_fit and fills())
        self.wait(lambda: not v.commit.isActive())
        self.key(v, Qt.Key_A)                                       # A again: back to normal
        self.assertFalse(v.always_fit or v.btn_fit.isChecked())
        v.btn_fit.click()                                           # the button, the same way
        self.assertTrue(v.always_fit)
        r = v.cam.radius
        self.wheel(v)                                               # framing by hand lets go
        self.assertFalse(v.always_fit)
        self.assertLess(v.cam.radius, r)
        self.key(v, Qt.Key_A)
        self.drag(v, 30, 20, Qt.ShiftModifier)
        self.assertFalse(v.always_fit)
        self.assertNotIn("always_fit", page.values())

    def test_missing_blender_shows_a_notice_and_blocks_runs(self):
        from atlas_toolkit.core import runtimes
        src = self.tmp / "ball.stl"
        trimesh.creation.icosphere(subdivisions=2, radius=10.0).export(src)
        with mock.patch.object(runtimes, "find", return_value=None):
            page = self.win.open_tool("mesh-melt", [str(src)])
            self.win.show()
            self.wait(lambda: page.runnable() and page.idle())
            self.assertTrue(page.lacking)
            self.assertTrue(page.notices[0].isVisible())
            self.assertFalse(page.btn_run.isEnabled())
            page.notices[0].choose(str(self.tmp / "blender.exe"))
            self.assertEqual(self.ctx.settings.get("blender_exe"), str(self.tmp / "blender.exe"))
        page._check_runtimes()
        if runtimes.find("blender") is not None:            # Blender is installed here: the notice goes away
            self.assertFalse(page.notices[0].isVisible())
            self.assertTrue(page.btn_run.isEnabled())


class HatchAimMathTests(unittest.TestCase):
    def test_aim_shows_what_draw_puts_on_paper(self):
        """The Aim tab's picture of the model is exactly where the Hatch Maker's scene puts it on paper, for any
        camera and roll (both use plotter.vision's units: centred, largest side 100)."""
        from atlas_toolkit.core.registry import registry
        from atlas_toolkit.geom.camera import Camera, frame_for, roll_screen
        from atlas_toolkit.tools.plotter_vision import model as pm
        from atlas_toolkit.tools.stl_hatch_maker.scene import FOV, Scene, view_camera
        tool = registry().get("stl-hatch-maker")
        mesh = trimesh.creation.box((40.0, 10.0, 20.0))
        mesh.apply_translation((5.0, -3.0, 8.0))
        mdl = pm.prepare(mesh, 0, True, None, False)
        for th, ps, r, look, roll in ((80, 180, 130, (0, 0, 0), 0), (55, -20, 90, (10, -5, 3), 30),
                                      (120, 45, 300, (0, 0, -20), -75)):
            p = tool.values({"theta": th, "psi": ps, "radius": r, "lookat": look, "roll": roll})
            sc = Scene(mesh, view_camera(p), roll, "A4", "landscape", 20.0)
            cam = Camera(th, ps, r, look, FOV, True)
            mm = frame_for("A4", "landscape", 20.0).to_mm(roll_screen(cam.project(cam.to_camera(mdl.V)), roll))
            np.testing.assert_allclose(mm, sc.P, atol=1e-9)

    def test_azimuth_and_elevation_carry_over(self):
        """6.x settings (a workflow, an SVG's record) keep their view direction."""
        from atlas_toolkit.core.registry import registry
        from atlas_toolkit.tools.stl_hatch_maker.scene import camera_basis, view_basis, view_camera
        from atlas_toolkit.core.params import resolve
        tool = registry().get("stl-hatch-maker")
        for az, el in ((-90.0, 10.0), (0.0, 0.0), (35.5, 60.0), (170.0, -40.0)):
            p = resolve(tool.param_list, {"azimuth": az, "elevation": el})          # a 6.x record
            np.testing.assert_allclose(view_basis(view_camera(p)), camera_basis(az, el), atol=1e-9)


if __name__ == "__main__":
    unittest.main()
