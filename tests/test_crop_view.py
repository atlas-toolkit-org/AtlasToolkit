"""Shape Crop's shape editor: handles move, turn and size the shape, Draw fits it to a screen rectangle, and the
live view stays cheap on a big model (it draws the input's light copy)."""
import _isolate  # first: settings and caches stay in a temporary folder
import os
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import trimesh
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from atlas_toolkit.core.tool import InputInfo
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom import region as R
from atlas_toolkit.tools.mesh_crop import TOOL
from atlas_toolkit.tools.mesh_crop.viewer import CropView
from atlas_toolkit.ui.theme import Theme
from atlas_toolkit.ui.widgets.param_form import ParamForm


class _Page(QWidget):
    def __init__(self):
        super().__init__()
        self.tool, self.theme = TOOL, Theme("dark")
        self.form = ParamForm(TOOL, self.theme)
        self.form.set_values(TOOL.defaults())
        self.debounce = QTimer(self)
        self.updates = 0
        self.viewer = CropView(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self.viewer)
        self.resize(800, 700)

    def values(self):
        return self.form.values()

    def _settings_changed(self):
        self.updates += 1


class ShapeEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="atlas_shape_editor_")
        cls.leave_home = _isolate.fresh_home()
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        cls.leave_home()
        cls.temp.cleanup()

    def setUp(self):
        self.page = _Page()
        self.view = self.page.viewer
        self.mesh = trimesh.creation.icosphere(subdivisions=2, radius=10)
        self.info = InputInfo("test.stl", "mesh", preview={"views": [{"kind": "mesh", "mesh": gm.preview(self.mesh)}]})
        self.view.set_input("test.stl", self.info)
        self.page.resize(900, 750)
        self.page.layout().activate()
        self.view.root.activate()
        self.view.layout_editor.activate()
        self.view.canvas.resize(700, 450)

    def tearDown(self):
        if self.view.dialog:
            self.view.dialog.reject()
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()

    def mouse(self, kind, pos, button=Qt.LeftButton, buttons=Qt.LeftButton, modifiers=Qt.NoModifier):
        event = QMouseEvent(kind, QPointF(*pos), QPointF(*pos), button, buttons, modifiers)
        self.app.sendEvent(self.view.canvas, event)

    def drag(self, start, end, modifiers=Qt.NoModifier):
        self.mouse(QEvent.MouseButtonPress, start, modifiers=modifiers)
        self.mouse(QEvent.MouseMove, end, Qt.NoButton, modifiers=modifiers)
        self.mouse(QEvent.MouseButtonRelease, end, buttons=Qt.NoButton, modifiers=modifiers)

    def test_move_rotate_and_scale_handles(self):
        self.view.set_mode("move")
        x = next(h for h in self.view.canvas.handles() if h[1] == 0)
        start = x[2][0] * .2 + x[2][-1] * .8
        self.drag(start, start + (x[2][-1] - x[2][0]) * .25)
        self.assertGreater(abs(self.page.values()["position"][0]), .1)
        self.assertAlmostEqual(self.page.values()["position"][1], 0)
        self.view.undo()
        self.assertEqual(self.page.values()["position"], (0, 0, 0))
        self.view.set_mode("rotate")
        ring = next(h for h in self.view.canvas.handles() if h[1] == 2)
        self.drag(ring[2][8], ring[2][16])
        self.assertGreater(np.linalg.norm(self.page.values()["rotation"]), 1)
        self.view.reset_rotation()
        self.view.set_mode("scale")
        handles = self.view.canvas.handles()
        self.assertEqual(len(handles), 4)
        w = next(h for h in handles if h[1] == 0)
        self.drag(w[2][-1], w[2][-1] + (w[2][-1] - w[2][0]))           # one handle length outward: twice the size
        size = self.page.values()["size"]
        self.assertAlmostEqual(size[0], 120.0, delta=1.0)
        self.assertEqual(size[1:], (60.0, 60.0))
        even = next(h for h in self.view.canvas.handles() if h[1] == 3)
        self.drag(even[2][-1], even[2][-1] - (even[2][-1] - even[2][0]) * .5, Qt.ControlModifier)
        self.assertTrue(all(v % 5 == 0 for v in self.page.values()["size"]), self.page.values()["size"])

    def test_draw_fills_the_rectangle_through_the_model(self):
        self.view.camera_view(3)                                          # top view
        self.view.ortho.setChecked(True)
        canvas = self.view.canvas
        a, b = np.array((300., 180.)), np.array((420., 300.))
        self.view.set_mode("draw")
        self.drag(a, b)
        self.assertEqual(self.view.mode, "move")
        m = R.frame(self.view.bounds, self.page.values())
        corners = R.to_world(np.array([[-.5, -.5, 0], [.5, .5, 0]]), m)
        screen = canvas.project(corners)
        np.testing.assert_allclose(np.sort(screen[:, 0]), (a[0], b[0]), atol=1.5)
        np.testing.assert_allclose(np.sort(screen[:, 1]), (a[1], b[1]), atol=1.5)
        depth = np.linalg.norm(m[:3, 2])
        self.assertGreater(depth, 20.0)                                   # spans the model (20 units across)

    def test_flip_fit_and_modal_cancel(self):
        self.view.flip()
        self.assertEqual(self.page.values()["operation"], "subtract")
        self.view.fit_to_model()
        np.testing.assert_allclose(self.page.values()["size"], (102, 102, 102), atol=1e-6)
        snapshot = self.page.values()
        self.view.expand()
        self.view.commit({"position": (1, 2, 3)})
        self.assertEqual(self.view.dialog_form.values()["position"], (1, 2, 3))
        self.view.dialog.reject()
        self.assertEqual(self.page.values(), snapshot)
        self.assertIs(self.view.editor.parent(), self.view)

    def test_every_operation_and_shape_draws(self):
        for op in ("intersect", "subtract", "union", "xor"):
            for kind in R.SHAPES:
                self.view.commit({"operation": op, "shape": kind})
                self.assertIn(kind.capitalize(), self.view.status.text())

    def test_a_big_model_costs_its_light_copy_only(self):
        big = trimesh.creation.icosphere(subdivisions=8)
        info = InputInfo("big.stl", "mesh", preview={"views": [{"kind": "mesh", "mesh": gm.preview(big)}]})
        t = time.perf_counter()
        self.view.set_input("big.stl", info)
        for i in range(10):
            self.view.commit({"position": (i * .01, 0, 0)}, remember=False)
        self.assertLess(time.perf_counter() - t, 3.0)
        self.assertLessEqual(len(self.view.data.faces), gm.PREVIEW_FACES)


if __name__ == "__main__":
    unittest.main()
