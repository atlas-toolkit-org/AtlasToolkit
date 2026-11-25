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


def stepped_sphere(path: Path, steps: int = 60, sections: int = 96) -> Path:
    """A sphere of stacked cylinders, like Stair Sculpt's output: nearly every edge is a crease, so the light trace
    has thousands of lines (about 10 000 here, from 23 000 faces)."""
    if not path.exists():
        parts, h = [], 100.0 / steps
        for i in range(steps):
            z = -50.0 + h * (i + 0.5)
            c = trimesh.creation.cylinder(radius=max(float(np.sqrt(max(2500.0 - z * z, 0.0))), 1.0), height=h,
                                          sections=sections)
            c.apply_translation((0.0, 0.0, z))
            parts.append(c)
        trimesh.util.concatenate(parts).export(path)
    return path


def pixels(img) -> np.ndarray:
    return np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes()).reshape(img.height(), -1).copy()


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
        x, y = w.rect().center().toTuple()                          # inside the roll ring, whatever the size
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

    def ring_drag(self, v, to_deg, mods=None):
        """Grab the roll ring at 12 o'clock and drag it round to `to_deg` (clockwise from 12)."""
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        c, r = v.ring()
        at = lambda a: QPointF(c.x() + r * np.sin(np.radians(a)), c.y() - r * np.cos(np.radians(a)))  # noqa: E731
        steps = [(QEvent.MouseButtonPress, at(0), Qt.LeftButton, Qt.LeftButton)]
        steps += [(QEvent.MouseMove, at(a), Qt.NoButton, Qt.LeftButton) for a in np.linspace(0, to_deg, 9)[1:]]
        steps += [(QEvent.MouseButtonRelease, at(to_deg), Qt.LeftButton, Qt.NoButton)]
        for kind, pt, btn, held in steps:
            self.app.sendEvent(v, QMouseEvent(kind, pt, v.mapToGlobal(pt), btn, held, mods or Qt.NoModifier))

    def test_roll_ring_keys_and_dial_turn_the_model_per_file(self):
        """The ring turns the model like a clock hand (Ctrl snaps to 15°), Q / E step it, the dial and 0 set it
        upright, R too; the roll goes into the settings like the camera, and each file keeps its own. The lines
        already worked out only turn: a roll starts no new hidden-line work."""
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        srcs = []
        for name in ("roll_a.stl", "roll_b.stl"):
            srcs.append(self.tmp / name)
            trimesh.creation.box((10.0, 10.0, 40.0)).export(srcs[-1])
        page = self.win.open_tool("contour-lines", [str(s) for s in srcs])
        self.win.show()
        self.wait(lambda: page.runnable() and page.idle())
        v = page.viewer
        keys = [str(s.resolve()) for s in srcs]
        page.queue.setCurrentItem(page.rows[keys[0]].item)
        self.wait(lambda: v.path == keys[0] and v.state == "done")
        self.assertTrue(v.has_roll)
        self.assertEqual(page.values()["roll"], 0.0)
        gen = v.gen
        self.ring_drag(v, 50.0)
        self.assertAlmostEqual(v.roll, 50.0, delta=0.5)
        self.assertEqual(v.gen, gen)                                 # the lines turned, nothing was worked out again
        self.wait(lambda: abs(page.values()["roll"] - 50.0) < 0.5)
        self.ring_drag(v, 50.0, Qt.ControlModifier)                  # 50 + 50 = 100, snapped to 105
        self.wait(lambda: page.values()["roll"] == 105.0)
        self.app.sendEvent(v, QKeyEvent(QEvent.KeyPress, Qt.Key_E, Qt.NoModifier))
        self.wait(lambda: page.values()["roll"] == 110.0)
        self.app.sendEvent(v, QKeyEvent(QEvent.KeyPress, Qt.Key_Q, Qt.ShiftModifier))
        self.wait(lambda: page.values()["roll"] == 109.0)
        self.assertIn("roll +109.0°", v._camera_text())
        page.queue.setCurrentItem(page.rows[keys[1]].item)          # the other file: upright, its own
        self.wait(lambda: v.path == keys[1] and v.state == "done")
        self.assertEqual(page.values()["roll"], 0.0)
        self.assertEqual(v.roll, 0.0)
        self.app.sendEvent(v, QKeyEvent(QEvent.KeyPress, Qt.Key_E, Qt.NoModifier))
        self.wait(lambda: page.values()["roll"] == 5.0)
        page.queue.setCurrentItem(page.rows[keys[0]].item)          # back: still 109
        self.wait(lambda: v.path == keys[0] and v.state == "done")
        self.assertEqual(page.values()["roll"], 109.0)
        self.assertEqual(v.roll, 109.0)
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent
        pt = QPointF(17, 17)
        self.app.sendEvent(v.dial, QMouseEvent(QEvent.MouseButtonPress, pt, v.dial.mapToGlobal(pt), Qt.LeftButton,
                                               Qt.LeftButton, Qt.NoModifier))
        self.wait(lambda: page.values()["roll"] == 0.0)
        page.form.set_values({"roll": -30.0})                         # typed into the Camera section
        page._settings_changed()
        self.wait(lambda: v.roll == -30.0)
        self.key(v, Qt.Key_0)
        self.wait(lambda: page.values()["roll"] == 0.0)
        self.app.sendEvent(v, QKeyEvent(QEvent.KeyPress, Qt.Key_E, Qt.NoModifier))
        self.wait(lambda: page.values()["roll"] == 5.0)
        self.key(v, Qt.Key_R)                                        # the start view, upright
        self.wait(lambda: page.values()["roll"] == 0.0 and page.values()["theta"] == 70.0)
        th = page.values()["theta"]
        self.app.sendEvent(v, QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.ShiftModifier))       # tilt from higher
        self.wait(lambda: page.values()["theta"] == th - 5.0)

    def test_ctrl_snaps_the_orbit_to_15_degrees(self):
        from PySide6.QtCore import Qt
        src = self.tmp / "snap.stl"
        trimesh.creation.box((10.0, 10.0, 40.0)).export(src)
        page = self.win.open_tool("plotter-vision", [str(src)])
        self.win.show()
        self.wait(lambda: page.runnable() and page.idle())
        v = page.viewer
        key = str(src.resolve())
        page.queue.setCurrentItem(page.rows[key].item)
        self.wait(lambda: v.path == key and v.state == "done")
        self.drag(v, 37, -23, Qt.ControlModifier)
        self.wait(lambda: not v.commit.isActive() and page.values()["psi"] != -150.0)
        for k in ("theta", "psi"):
            self.assertEqual(page.values()[k] % 15.0, 0.0, k)

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

    def aim_on(self, src: Path, tool: str = "contour-lines"):
        """The Aim tab of `tool` on src, once its hidden lines are done."""
        page = self.win.open_tool(tool, [str(src)])
        self.win.show()
        self.wait(lambda: page.runnable() and page.idle())
        key = str(src.resolve())
        page.queue.setCurrentItem(page.rows[key].item)
        page.stage.show_aim()
        v = page.viewer
        self.wait(lambda: v.path == key and v.model is not None and v.state == "done")
        return page, v

    def press(self, w, kind, x, y):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        kinds = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove, "release": QEvent.MouseButtonRelease}
        self.app.sendEvent(w, QMouseEvent(kinds[kind], QPointF(x, y), w.mapToGlobal(QPointF(x, y)),
                                          Qt.LeftButton if kind != "move" else Qt.NoButton,
                                          Qt.LeftButton if kind != "release" else Qt.NoButton, Qt.NoModifier))

    def test_lines_draw_fast_on_hidpi_screens(self):
        """Above 100 % display scaling Qt stroked the Aim's round-capped lines through its outline stroker: 1.4 s a
        frame for 8 600 edges at 175 %, with the hidden-line thread stopped meanwhile (the paint holds the GIL). CI
        runs at 100 %, where it never showed, so this draws at 2x on purpose, every edge (the frame budget would
        otherwise thin a slow frame and hide it)."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QImage
        from atlas_toolkit.tools.plotter_vision import viewer as vw
        self.assertEqual(vw._pen(QColor("#000000"), 1.0).capStyle(), Qt.FlatCap)
        self.assertEqual(vw._pen(QColor("#000000"), 1.0, dashed=True).capStyle(), Qt.FlatCap)
        _page, v = self.aim_on(stepped_sphere(self.tmp / "stepped.stl"))
        self.assertGreater(v.edges, 5000)                          # a crease-heavy trace, or this proves nothing
        self.press(v, "press", *v.rect().center().toTuple())                           # moving: every candidate edge is drawn
        self.assertEqual(v.state, "moving")
        times = []
        for _ in range(3):
            v._budget = 10 ** 9                                    # every edge, not what the frame budget allows
            img = QImage(v.width() * 2, v.height() * 2, QImage.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(2.0)
            t0 = time.perf_counter()
            v.render(img)
            times.append(time.perf_counter() - t0)
            self.assertEqual(v.shown, v.edges)
        self.press(v, "release", 200, 200)
        self.assertEqual(v._live.image.devicePixelRatio(), 2.0)    # drawn at the resolution it is shown at
        # flat caps: under 0.01 s on a laptop; round ones: 0.5 s
        self.assertLess(min(times), 0.15, f"a moving frame of {v.edges} edges at 2x took {min(times):.2f} s")
        self.wait(lambda: v.state == "done")

    def test_mouse_moves_only_move_the_camera(self):
        """A mouse sends hundreds of moves a second: each only turns the camera, and the next frame works out what it
        sees, once (it used to work it out for every move, so the view fell behind the hand)."""
        from atlas_toolkit.geom.camera import wrap
        from atlas_toolkit.tools.plotter_vision.viewer import DRAG_DEG, VisionView
        from PySide6.QtGui import QImage
        _page, v = self.aim_on(stepped_sphere(self.tmp / "stepped.stl"))
        psi = v.cam.psi
        with mock.patch.object(VisionView, "_candidates", autospec=True,
                               side_effect=VisionView._candidates) as cand:
            self.press(v, "press", 100, 200)
            for i in range(1, 41):
                self.press(v, "move", 100 + 3 * i, 200)
            self.assertEqual(cand.call_count, 0)
            self.assertAlmostEqual((v.cam.psi - wrap(psi + 120 * DRAG_DEG) + 180.0) % 360.0 - 180.0, 0.0, places=6)
            v.render(QImage(v.width(), v.height(), QImage.Format_ARGB32_Premultiplied))
            self.assertEqual(cand.call_count, 1)
            v.render(QImage(v.width(), v.height(), QImage.Format_ARGB32_Premultiplied))
            self.assertEqual(cand.call_count, 1)                    # nothing moved since
            self.press(v, "release", 220, 200)
        self.wait(lambda: v.state == "done")
        self.wait(lambda: not v.commit.isActive())
        self.assertAlmostEqual(_page.values()["psi"], round(v.cam.psi, 2), places=2)

    def pump(self, seconds):
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            self.app.processEvents()
            time.sleep(0.01)

    def test_pause_lines_lets_you_aim_over_the_trace(self):
        """Pause lines (P, or the corner button): a move no longer starts hidden-line work, the trace shows in full,
        and the camera still goes into the settings. Resuming draws the lines for the view as it is, once. Pausing
        while lines are worked out stops that work. It is view state, never saved."""
        from PySide6.QtCore import Qt
        page, v = self.aim_on(stepped_sphere(self.tmp / "stepped.stl"))
        self.assertFalse(v.paused or v.btn_pause.isChecked())
        self.assertEqual(v.btn_pause.text(), "Pause lines · P")
        self.key(v, Qt.Key_P)
        self.assertTrue(v.paused and v.btn_pause.isChecked())
        self.assertEqual(v.btn_pause.text(), "Resume lines · P")
        self.assertEqual(v.state, "done")                           # the lines on screen stay until a move
        psi = page.values()["psi"]
        self.drag(v, 60, 0)
        self.wheel(v)
        self.wait(lambda: page.values()["psi"] != psi and not v.commit.isActive())   # the view still goes back
        self.pump(0.4)                                              # time enough for work to start: none does
        self.assertEqual((v.state, v.pieces), ("paused", []))
        self.assertNotIn("paused", page.values())
        v.btn_pause.click()                                         # resume: the lines of this view, now
        self.assertFalse(v.paused)
        self.assertEqual(v.btn_pause.text(), "Pause lines · P")
        self.assertEqual(v.state, "computing")
        self.wait(lambda: v.state == "done")
        self.assertTrue(v.pieces)
        self.drag(v, -40, 0)                                        # pausing while lines are worked out
        self.wait(lambda: v.state == "computing")
        self.key(v, Qt.Key_P)
        gen = v.gen
        self.pump(0.5)
        self.assertEqual((v.state, v.pieces, v.gen), ("paused", [], gen))
        self.key(v, Qt.Key_P)
        self.wait(lambda: v.state == "done")
        self.assertTrue(v.pieces)

    def test_slow_moving_frames_draw_fewer_edges_next(self):
        """The frame budget: after a moving frame over FRAME_S the next one thins the candidates (the outline
        stays), and says so; once frames are quick again it draws more, up to every edge."""
        from PySide6.QtGui import QImage
        from atlas_toolkit.tools.plotter_vision.viewer import DRAG_MIN, FRAME_S
        _page, v = self.aim_on(stepped_sphere(self.tmp / "stepped.stl"))
        img = QImage(v.width(), v.height(), QImage.Format_ARGB32_Premultiplied)
        self.press(v, "press", *v.rect().center().toTuple())
        v._budget = 10 ** 9
        v.render(img)
        self.assertEqual(v.shown, v.edges)
        v._pace(FRAME_S * 4)                                       # as if that frame had taken four times too long
        self.assertLess(v._budget, v.edges)
        self.assertGreaterEqual(v._budget, DRAG_MIN)
        thinned = v._budget
        v.render(img)
        self.assertEqual(v.shown, thinned)
        for _ in range(20):                                        # quick frames: back to every edge
            v._pace(FRAME_S / 10)
        self.assertGreaterEqual(v._budget, v.edges)
        self.press(v, "release", 200, 200)
        self.wait(lambda: v.state == "done")

    def test_lines_filled_in_batch_by_batch_match_a_full_redraw(self):
        """While the hidden lines come in, each frame draws only the batches that arrived since the last one (it used
        to redraw them all every time). The finished picture is the same as drawing everything at once."""
        from PySide6.QtGui import QImage
        from atlas_toolkit.tools.plotter_vision.viewer import VisionView
        _page, v = self.aim_on(stepped_sphere(self.tmp / "stepped.stl"))
        batches = list(v.pieces)
        self.assertGreater(len(batches), 1)

        def frame():
            img = QImage(v.width(), v.height(), QImage.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(1.0)
            v.render(img)
            return img

        drawn = []
        draw = VisionView._draw_pieces
        with mock.patch.object(VisionView, "_draw_pieces", autospec=True,
                               side_effect=lambda self, qp, b, *a: (drawn.append(len(b)), draw(self, qp, b, *a))):
            v._clear_pieces()
            blank = pixels(frame()).astype(int)
            for b in batches:
                v.pieces.append(b)
                frame()
            step_by_step = pixels(frame()).astype(int)
            self.assertEqual(drawn, [1] * len(batches))             # each batch drawn once, on its own
            v._result.clear()
            at_once = pixels(frame()).astype(int)
        self.assertEqual(drawn[-1], len(batches))
        ink = int((np.abs(at_once - blank) > 48).sum())
        self.assertGreater(ink, 1000)
        # the same picture: only where lines of different batches cross can antialiasing differ a little
        self.assertLess(int((np.abs(step_by_step - at_once) > 48).sum()), 0.02 * ink)

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


class FrameBudgetTests(unittest.TestCase):
    def test_thinning_keeps_the_outline(self):
        from atlas_toolkit.tools.plotter_vision.viewer import _thin
        outline = np.zeros(10_000, bool)
        outline[::50] = True                                       # 200 outline edges
        keep = _thin(outline, 1000)
        self.assertEqual(len(np.unique(keep)), 1000)
        self.assertTrue(set(np.flatnonzero(outline)) <= set(keep.tolist()))
        keep = _thin(outline, 100)                                 # fewer than the outline: an even share of it
        self.assertEqual(len(np.unique(keep)), 100)
        self.assertTrue(outline[keep].all())


class LightTraceTests(unittest.TestCase):
    """The Aim tabs' light copy of a model (plotter_vision/model.py: load_light)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="atlas_light_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_stays_within_budget_when_decimation_stalls(self):
        """Quadric decimation can stop far short of the budget (two thirds of a 480 000-face Motion Sculpture model
        stayed, and its hidden lines took 30 s): vertex clustering then finishes the job."""
        from atlas_toolkit.tools.plotter_vision import model as pm
        src = self.tmp / "stalls.stl"
        trimesh.creation.icosphere(subdivisions=5, radius=20.0).export(src)       # 20 480 faces
        with mock.patch.object(trimesh.Trimesh, "simplify_quadric_decimation",
                               lambda self, face_count=None, **_k: self.copy()):
            m = pm.load_light(str(src), 5000)
        self.assertLessEqual(m.faces, 5000)
        self.assertGreater(m.faces, 1500)
        np.testing.assert_allclose(np.ptp(m.V, axis=0).max(), 100.0, rtol=0.02)   # the whole model, still

    def test_a_new_frame_does_not_decimate_again(self):
        """A new Up, Front or turn places the reduced model again: the file is decimated once."""
        from atlas_toolkit.geom import frame3d, mesh as gm
        from atlas_toolkit.tools.contour_lines import TOOL
        from atlas_toolkit.tools.plotter_vision import model as pm
        src = self.tmp / "box_ball.stl"
        ball = trimesh.creation.icosphere(subdivisions=5, radius=10.0)
        ball.apply_scale((1.0, 2.0, 3.0))
        ball.export(src)
        values = dict(TOOL.defaults())
        with mock.patch.object(gm, "simplify_faces", wraps=gm.simplify_faces) as simplify:
            a = pm.load_light(str(src), 4000, True, frame=frame3d.frame_for(str(src), {**values, "up_axis": "+z"}))
            b = pm.load_light(str(src), 4000, True, frame=frame3d.frame_for(str(src), {**values, "up_axis": "+y"}))
        self.assertEqual(simplify.call_count, 1)
        self.assertEqual(a.faces, b.faces)
        self.assertLessEqual(a.faces, 4000)
        self.assertFalse(np.allclose(np.ptp(a.V, axis=0), np.ptp(b.V, axis=0)))   # placed differently

    def test_light_enough_models_keep_their_faces(self):
        from atlas_toolkit.tools.plotter_vision import model as pm
        src = self.tmp / "small.stl"
        trimesh.creation.icosphere(subdivisions=3, radius=10.0).export(src)       # 1 280 faces
        self.assertEqual(pm.load_light(str(src), 20_000).faces, 1280)


class RollMathTests(unittest.TestCase):
    def test_turning_the_model_is_turning_the_picture(self):
        """Roll turns the model about the line of sight through the look-at point. That axis runs through the eye, so
        the turned model's picture is the picture turned (roll_screen, which the Aim tab shows), in perspective and
        orthographic alike, and the same as a rolled camera (FlowRep, Plotter Vision); depth does not change."""
        from atlas_toolkit.geom.camera import Camera, roll_screen, turn_in_view, turn_matrix
        rng = np.random.default_rng(3)
        P = rng.normal(0.0, 30.0, (200, 3))
        for th, ps, r, look, ortho, roll in ((70, -150, 170, (0, 0, 0), False, 30.0),
                                             (120, 45, 260, (12, -5, 8), False, -75.0),
                                             (5, 10, 90, (3, 2, 1), True, 160.0)):
            cam = Camera(th, ps, r, look, 50.0, ortho)
            C0, C1 = cam.to_camera(P), cam.to_camera(turn_in_view(P, cam, roll))
            np.testing.assert_allclose(C1[:, 2], C0[:, 2], atol=1e-9)
            np.testing.assert_allclose(cam.project(C1), roll_screen(cam.project(C0), roll), atol=1e-9)
            rolled = Camera(th, ps, r, look, 50.0, ortho, roll)
            np.testing.assert_allclose(rolled.project(rolled.to_camera(P)), cam.project(C1), atol=1e-9)
            M = turn_matrix(cam, roll)
            np.testing.assert_allclose(M @ M.T, np.eye(3), atol=1e-12)
            self.assertAlmostEqual(float(np.linalg.det(M)), 1.0, places=12)
        cam = Camera(90.0, 180.0, 200.0, (0, 0, 0), 40.0, True)     # from the front: clockwise on the paper
        top = cam.project(cam.to_camera(turn_in_view(np.array([[0.0, 0.0, 10.0]]), cam, 90.0)))[0]
        np.testing.assert_allclose(top, (10.0 * cam.k / 200.0, 0.0), atol=1e-12)     # 12 o'clock went to 3


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
