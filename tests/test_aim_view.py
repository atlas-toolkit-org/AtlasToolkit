"""The 3D drawing tools in the real window, offscreen: the Aim tab on a stage set (one frame for every stage, F fits
the widest), and the notice for a missing external program (Melt without Blender)."""
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


if __name__ == "__main__":
    unittest.main()
