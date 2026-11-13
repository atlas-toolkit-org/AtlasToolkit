"""Being at home on each desktop: files handed over by the system, the macOS menu bar and shortcut text, trackpad gestures,
finding Blender, workflows made on another OS, and the depth runtime on a Mac. Each runs on every OS: what is specific to
one is reached by pretending to be it. Real gestures, a real Dock and Apple's GPU still need a real Mac."""
import _isolate  # first: settings and caches stay in a temporary folder
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QAction, QFileOpenEvent, QNativeGestureEvent, QPointingDevice, QWheelEvent
from PySide6.QtWidgets import QApplication, QMenuBar

from atlas_toolkit.core import paths
from atlas_toolkit.core import workflow as W
from atlas_toolkit.geom import blender as gb
from atlas_toolkit.ui import app as ui_app
from atlas_toolkit.ui import keys, shell
from atlas_toolkit.ui.about import setup_commands
from atlas_toolkit.core.runtimes import get as get_runtime


def application():
    return QApplication.instance() or QApplication([])


class ShortcutTextTests(unittest.TestCase):
    def test_other_systems_read_exactly_what_was_written(self):
        with patch.object(sys, "platform", "linux"):
            self.assertEqual(keys.shown("Save the workflow (Ctrl+S)"), "Save the workflow (Ctrl+S)")

    def test_a_mac_never_reads_ctrl(self):
        application()
        with patch.object(sys, "platform", "darwin"):
            for text in ("Save (Ctrl+S)", "Save as (Ctrl+Shift+S)", "Setup (Ctrl+,)", "Open the n-th tool Alt+1", "Ctrl ÷ 10"):
                self.assertNotIn("Ctrl", keys.shown(text), text)
                self.assertNotIn("Alt", keys.shown(text), text)
            self.assertIn("⌘", keys.shown("Number field: step (Shift × 10, Ctrl ÷ 10)"))
            self.assertIn("⇧", keys.shown("Number field: step (Shift × 10, Ctrl ÷ 10)"))
            self.assertEqual(keys.shown("Wheel / drag"), "Wheel / drag")

    def test_every_shortcut_in_the_list_is_covered(self):
        application()
        with patch.object(sys, "platform", "darwin"):
            for keys_text, what in shell.SHORTCUTS:
                self.assertNotIn("Ctrl", keys.shown(keys_text) + keys.shown(what), keys_text)


class MenuBarTests(unittest.TestCase):
    def test_about_setup_and_quit_carry_the_roles_macos_moves_to_its_app_menu(self):
        application()
        win = QObject()         # the window only parents the actions and receives their calls
        for name in ("show_help", "show_setup", "close", "_page_call", "show_home", "show_activity", "open_workflow"):
            setattr(win, name, MagicMock())
        bar = QMenuBar()
        self.addCleanup(bar.deleteLater)
        shell.build_menu_bar(win, bar)
        actions = [a for top in bar.actions() for a in top.menu().actions()]
        roles = {a.menuRole(): a for a in actions}
        for role in (QAction.MenuRole.AboutRole, QAction.MenuRole.PreferencesRole, QAction.MenuRole.QuitRole):
            self.assertIn(role, roles)
        roles[QAction.MenuRole.PreferencesRole].trigger()
        win.show_setup.assert_called_once_with()
        roles[QAction.MenuRole.AboutRole].trigger()
        win.show_help.assert_called_once_with("about")
        roles[QAction.MenuRole.QuitRole].trigger()
        win.close.assert_called_once_with()
        self.assertEqual(roles[QAction.MenuRole.PreferencesRole].shortcut().toString(), "Ctrl+,")


class FileOpenTests(unittest.TestCase):
    def window(self):
        return SimpleNamespace(open_workflow=MagicMock(), home=SimpleNamespace(route=MagicMock()), raise_=MagicMock(),
                               activateWindow=MagicMock())

    def test_a_file_that_arrives_before_the_window_waits_for_it(self):
        application()
        opener = ui_app.FileOpener()
        event = QFileOpenEvent("/some/where/model.stl")
        self.assertTrue(opener.eventFilter(None, event))
        self.assertEqual(opener.waiting, ["/some/where/model.stl"])
        win = self.window()
        opener.win = win
        opener.flush()
        win.home.route.assert_called_once_with(["/some/where/model.stl"])
        self.assertEqual(opener.waiting, [])

    def test_a_file_that_arrives_later_opens_on_the_next_turn_of_the_loop(self):
        application()
        opener = ui_app.FileOpener()
        win = self.window()
        opener.attach(win)
        self.assertTrue(opener.eventFilter(None, QFileOpenEvent("/some/where/drawing.svg")))
        win.home.route.assert_not_called()      # at launch the event comes from inside AppKit: a menu opened there aborts the app
        QApplication.processEvents()
        win.home.route.assert_called_once_with(["/some/where/drawing.svg"])

    def test_a_file_on_the_command_line_does_not_open_again(self):
        application()
        opener = ui_app.FileOpener(["/some/where/model.stl"])
        win = self.window()
        opener.attach(win)
        self.assertTrue(opener.eventFilter(None, QFileOpenEvent("/some/where/model.stl")))     # AppKit's copy of the argument
        QApplication.processEvents()
        win.home.route.assert_not_called()
        opener.eventFilter(None, QFileOpenEvent("/some/where/model.stl"))                       # dropped on the Dock later
        QApplication.processEvents()
        win.home.route.assert_called_once_with(["/some/where/model.stl"])

    def test_a_workflow_opens_as_one_with_the_other_files_as_its_inputs(self):
        win = self.window()
        ui_app.open_paths(win, ["/a/model.stl", "/a/Flow.ATLASFLOW"])
        win.open_workflow.assert_called_once_with("/a/Flow.ATLASFLOW", ["/a/model.stl"])
        win.home.route.assert_not_called()

    def test_other_events_are_left_alone(self):
        application()
        self.assertFalse(ui_app.FileOpener().eventFilter(None, QEvent(QEvent.Type.Timer)))


class CommandLineHelpTests(unittest.TestCase):
    """About › Command line says how to start it, in the way that really works on this install."""

    def program(self, frozen, appimage=None):
        from atlas_toolkit.ui import about
        env = {k: v for k, v in os.environ.items() if k != "APPIMAGE"}
        if appimage:
            env["APPIMAGE"] = appimage
        with patch.object(about, "frozen", return_value=frozen), patch.dict(os.environ, env, clear=True):
            return about.cli_program()

    def test_from_source(self):
        self.assertEqual(self.program(False), "python -m atlas_toolkit")

    def test_an_installed_app_is_atlas_toolkit_on_every_os(self):
        self.assertEqual(self.program(True), "atlas-toolkit")

    def test_an_appimage_is_its_own_file_and_never_a_path(self):
        self.assertEqual(self.program(True, "/home/someone/apps/Atlas-1.AppImage"), '"./Atlas-1.AppImage"')


class GestureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        application()
        from atlas_toolkit.ui.widgets import mesh_view
        if mesh_view.gl is None:
            raise unittest.SkipTest("no OpenGL stack here")
        cls.view_class = mesh_view._View

    def view(self):
        view = self.view_class()
        self.addCleanup(view.deleteLater)
        return view

    def wheel(self, pixels=(0, 0), angle=(0, 0), phase=Qt.ScrollPhase.ScrollUpdate, modifiers=Qt.KeyboardModifier.NoModifier):
        return QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(*pixels), QPoint(*angle), Qt.MouseButton.NoButton,
                           modifiers, phase, False)

    def test_a_pinch_zooms(self):
        view = self.view()
        before = view.opts["distance"]
        event = QNativeGestureEvent(Qt.NativeGestureType.ZoomNativeGesture, QPointingDevice.primaryPointingDevice(), 2,
                                    QPointF(10, 10), QPointF(10, 10), QPointF(10, 10), 0.25, QPointF(0, 0))
        self.assertTrue(view.event(event))
        self.assertAlmostEqual(view.opts["distance"], before / 1.25)

    def test_on_a_mac_a_two_finger_swipe_orbits_and_with_shift_pans(self):
        view = self.view()
        with patch.object(sys, "platform", "darwin"):
            azimuth, distance, centre = view.opts["azimuth"], view.opts["distance"], QPointF(view.opts["center"].x(), view.opts["center"].y())
            view.wheelEvent(self.wheel(pixels=(40, 0), angle=(40, 0)))
            self.assertNotEqual(view.opts["azimuth"], azimuth)
            self.assertEqual(view.opts["distance"], distance)
            view.wheelEvent(self.wheel(pixels=(40, 0), angle=(40, 0), modifiers=Qt.KeyboardModifier.ShiftModifier))
            self.assertNotEqual(QPointF(view.opts["center"].x(), view.opts["center"].y()), centre)

    def test_a_real_wheel_still_zooms_on_a_mac(self):
        view = self.view()
        with patch.object(sys, "platform", "darwin"):
            distance = view.opts["distance"]
            view.wheelEvent(self.wheel(angle=(0, 120), phase=Qt.ScrollPhase.NoScrollPhase))
            self.assertNotEqual(view.opts["distance"], distance)

    def test_other_systems_keep_what_a_touchpad_does_today(self):
        view = self.view()
        with patch.object(sys, "platform", "win32"):
            azimuth, distance = view.opts["azimuth"], view.opts["distance"]
            view.wheelEvent(self.wheel(pixels=(0, 40), angle=(0, 120)))
            self.assertEqual(view.opts["azimuth"], azimuth)
            self.assertNotEqual(view.opts["distance"], distance)


class FindingProgramsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_programs_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(_isolate.fresh_home())

    def program(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return path

    def test_a_bundle_means_the_program_inside_it(self):
        inside = self.program("Blender.app/Contents/MacOS/Blender")
        self.assertEqual(paths.program_inside(str(self.root / "Blender.app")), str(inside))
        other = self.program("Odd Name.app/Contents/MacOS/runner")
        self.assertEqual(paths.program_inside(str(self.root / "Odd Name.app")), str(other))

    def test_anything_else_stays_as_it_is(self):
        plain = self.program("blender")
        self.assertEqual(paths.program_inside(str(plain)), str(plain))
        (self.root / "Empty.app").mkdir()
        self.assertEqual(paths.program_inside(str(self.root / "Empty.app")), str(self.root / "Empty.app"))

    def found(self, platform):
        os.environ.pop(gb.ENV, None)
        with patch.object(sys, "platform", platform), patch.object(gb.Path, "home", return_value=self.root), \
                patch.object(gb.shutil, "which", return_value=None):
            return gb.candidates()

    def test_on_a_mac_any_version_in_the_applications_folders_newest_first(self):
        old = self.program("Applications/Blender 4.2.app/Contents/MacOS/Blender")
        new = self.program("Applications/Blender 5.1.app/Contents/MacOS/Blender")
        found = self.found("darwin")
        self.assertLess(found.index(new), found.index(old))

    def test_on_linux_an_unpacked_tarball_counts_too(self):
        old = self.program("blender-4.2.1-linux-x64/blender")
        new = self.program("Applications/blender-5.0-linux-x64/blender")
        found = self.found("linux")
        self.assertIn(old, found)
        self.assertLess(found.index(new), found.index(old))


class WorkflowPathTests(unittest.TestCase):
    def test_a_windows_path_gives_its_file_name_on_any_os(self):
        self.assertEqual(W.saved_path(r"D:\models\head.stl").name, "head.stl")
        self.assertEqual(W.saved_path(r"D:\models\head.stl").suffix, ".stl")
        self.assertEqual(W.saved_path("/home/me/head.stl").name, "head.stl")

    def test_case_makes_a_different_file_only_on_linux(self):
        with patch.object(sys, "platform", "linux"):
            self.assertNotEqual(W.same_file_key("A.svg"), W.same_file_key("a.svg"))
        for platform in ("win32", "darwin"):
            with patch.object(sys, "platform", platform):
                self.assertEqual(W.same_file_key("A.svg"), W.same_file_key("a.svg"))


class DepthRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("atlas_depth_server", Path(paths.__file__).parents[1] / "ml" / "depth_server.py")
        cls.server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.server)

    def test_a_mac_stays_on_the_cpu_until_asked(self):
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
        self.assertEqual(self.server.choose_device(torch), "cpu")
        self.assertEqual(self.server.choose_device(torch, "mps"), "mps")

    def test_cuda_is_chosen_where_there_is_one(self):
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
        self.assertEqual(self.server.choose_device(torch), "cuda")
        self.assertEqual(self.server.choose_device(torch, "nonsense"), "cuda")

    def test_the_installer_fits_the_system(self):
        """A Mac gets PyPI's PyTorch (no CUDA builds exist for it) and never a CUDA choice; Windows and Linux get the
        CUDA builds and the CPU one. The command line is one command, the same in every shell."""
        from atlas_toolkit.core import gpu
        from atlas_toolkit.geom import depth_runtime as dr
        rt = get_runtime("depth")
        for platform in ("darwin", "win32", "linux"):
            with patch.object(sys, "platform", platform):
                keys = [k for k, _, _ in dr.builds()]
                chosen = dr.choose(gpu.GpuReport("apple" if platform == "darwin" else "none")).build
                command = setup_commands(rt)
            self.assertNotIn("pushd", command)
            self.assertIn("components install depth", command)
            if platform == "darwin":
                self.assertEqual((keys, chosen), (["mac"], "mac"))
                self.assertEqual(dr.lock()["builds"]["mac"]["index"], "")
            else:
                self.assertEqual((keys, chosen), (["cu130", "cu126", "cpu"], "cpu"))


if __name__ == "__main__":
    unittest.main()
