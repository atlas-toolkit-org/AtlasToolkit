"""A machine with no working OpenGL gets a message where the 3D view would be, not an endless pile of error dialogs."""
import _isolate  # first: settings and caches stay in a temporary folder

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtWidgets import QApplication

from atlas_toolkit.ui import app as app_module
from atlas_toolkit.ui.widgets.gl_guard import GuardedGL

SRC = Path(__file__).resolve().parents[1] / "src" / "atlas_toolkit"


def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


class _Driver:
    """Stands in for GLViewWidget: the driver fails when asked to draw."""
    paints = 0

    def paintGL(self):
        type(self).paints += 1
        raise RuntimeError("no OpenGL here")

    def initializeGL(self):
        raise RuntimeError("cannot start OpenGL")


class _View(GuardedGL, _Driver):
    pass


class Guard(unittest.TestCase):
    def setUp(self):
        self.app = qapp()
        _View.paints = 0
        self.reasons = []
        self.view = _View()
        self.view.on_gl_failure = self.reasons.append

    def test_the_first_failure_is_the_last(self):
        self.view.paintGL()           # raises nothing out into Qt, which would hand it to the crash dialog
        self.view.paintGL()
        self.view.paintGL()
        self.assertEqual(_View.paints, 1)
        self.app.processEvents()
        self.assertEqual(self.reasons, [self.view.gl_failure])
        self.assertIn("no OpenGL here", self.reasons[0])

    def test_a_failure_while_starting_counts_too(self):
        self.view.initializeGL()
        self.view.paintGL()
        self.assertEqual(_View.paints, 0)
        self.assertIn("cannot start OpenGL", self.view.gl_failure)

    def test_the_owner_hears_after_the_paint_has_finished(self):
        self.view.paintGL()
        self.assertEqual(self.reasons, [])

    def test_a_gl_error_reads_as_one_line(self):
        class GLError(Exception):
            description = b"invalid operation"

            def __str__(self):
                return "GLError(\n\terr = 1282,\n\tmany more lines\n)"

        class Failing:
            def paintGL(self):
                raise GLError()

        class View(GuardedGL, Failing):
            pass

        view = View()
        view.paintGL()
        self.assertIn("invalid operation", view.gl_failure)
        self.assertNotIn("\n", view.gl_failure)


class EveryCanvas(unittest.TestCase):
    def test_no_3d_canvas_goes_without_the_guard(self):
        found = {}
        for path in SRC.rglob("*.py"):
            for bases in re.findall(r"class \w+\(([^)]*GLViewWidget[^)]*)\)", path.read_text(encoding="utf-8")):
                found[path.name] = bases
        self.assertGreaterEqual(len(found), 3)
        for name, bases in found.items():
            self.assertIn("GuardedGL", bases, name)


class MeshViewFailure(unittest.TestCase):
    def test_it_shows_why_and_keeps_working(self):
        qapp()
        from types import SimpleNamespace

        import numpy as np

        from atlas_toolkit.ui.theme import Theme
        from atlas_toolkit.ui.widgets import mesh_view
        widget = mesh_view.MeshView(Theme("dark"))
        if widget.view is None:
            self.skipTest(f"no 3D view here: {widget.msg.text()}")
        widget._gl_failed("OpenGL does not work on this machine (test)")
        self.assertIsNone(widget.view)
        self.assertIn("does not work", widget.msg.text())
        self.assertIs(widget.layout().currentWidget(), widget.msg)
        # the page goes on using it as before
        data = SimpleNamespace(vertices=np.zeros((3, 3)), faces=np.array([[0, 1, 2]]), edge_segments=None,
                               bounds=((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)))
        widget.show_mesh(data)
        widget.set_art("blueprint")
        widget.camera()
        self.assertIs(widget.layout().currentWidget(), widget.msg)


class CrashDialog(unittest.TestCase):
    def setUp(self):
        qapp()
        self.addCleanup(self._reset)
        self._reset()
        patcher = mock.patch.object(app_module, "QMessageBox")
        self.box = patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _reset():
        app_module._Errors.unattended = False
        app_module._Errors.showing = False
        app_module._Errors.seen = set()

    @staticmethod
    def raise_and_report(text="boom"):
        try:
            raise RuntimeError(text)
        except RuntimeError:
            app_module._excepthook(*sys.exc_info())

    def test_the_same_error_opens_one_dialog(self):
        for _ in range(3):
            self.raise_and_report()
        self.assertEqual(self.box.return_value.exec.call_count, 1)

    def test_a_different_error_opens_its_own(self):
        self.raise_and_report("first")
        self.raise_and_report("second")
        self.assertEqual(self.box.return_value.exec.call_count, 2)

    def test_dialogs_do_not_stack(self):
        # Showing a dialog runs an event loop, and that is where the next error comes from.
        self.box.return_value.exec.side_effect = lambda: self.raise_and_report("while the first is open")
        self.raise_and_report("first")
        self.assertEqual(self.box.return_value.exec.call_count, 1)
        self.assertFalse(app_module._Errors.showing)

    def test_nobody_is_asked_to_click_in_a_snapshot_run(self):
        app_module._Errors.unattended = True
        self.raise_and_report()
        self.box.return_value.exec.assert_not_called()
        self.assertTrue(app_module._Errors.seen)         # run_app turns this into a failing exit code


if __name__ == "__main__":
    unittest.main()
