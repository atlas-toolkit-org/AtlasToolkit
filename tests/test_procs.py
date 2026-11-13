"""Child processes on every OS: nothing outlives Cancel, a crashed worker or a dead window, and a child gets a clean
environment. The orphan tests run the real Runner on a tool that gets stuck, as a job deep inside C++ does."""
import _isolate  # first: settings and caches stay in a temporary folder
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from atlas_toolkit.core import paths, procs, runtimes, samples
from atlas_toolkit.core.job import Job
from atlas_toolkit.core.runner import Runner, _Worker

FIXTURES = Path(__file__).parent / "fixtures"
PLUGINS = str(FIXTURES / "plugin_tools")
HOST = str(FIXTURES / "procs_host.py")


class OrphanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_procs_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.src = samples.svg(self.folder)
        self.report = self.folder / "pids.txt"
        patcher = patch.dict(os.environ, {"ATLAS_TEST_PIDS": str(self.report)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pids = []
        self.addCleanup(self.sweep)

    def sweep(self):
        """Whatever a failed test left running must not survive the run."""
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    def wait_for_pids(self, timeout=90.0):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.pids = [int(p) for p in self.report.read_text(encoding="utf-8").split()]
                break
            except (FileNotFoundError, PermissionError):     # Windows refuses a read while the file is being renamed into place
                self.assertLess(time.monotonic(), deadline, "the stuck job never started")
                time.sleep(0.05)
        self.assertFalse(_isolate.process_gone(self.pids[1], 0.2), "the child should be running")
        return self.pids

    def start_job(self):
        events, ended = [], threading.Event()

        def on_event(_job, kind, payload):
            events.append(kind)
            if kind in ("cancelled", "error", "done"):
                ended.set()
        runner = Runner(on_event, plugin_dirs=[PLUGINS])
        self.addCleanup(runner.shutdown)
        job = runner.submit(Job("test-sleepy", "run", str(self.src), str(self.src) + ".out.svg", {}), "process")
        return runner, job, events, ended

    def test_cancel_stops_a_stuck_worker_and_its_child(self):
        runner, job, events, ended = self.start_job()
        worker, child = self.wait_for_pids()
        runner.cancel(job)
        self.assertTrue(ended.wait(30), "Cancel never finished")
        self.assertIn("cancelled", events)
        self.assertTrue(_isolate.process_gone(worker), "the worker survived Cancel")
        self.assertTrue(_isolate.process_gone(child), "the child survived Cancel")

    def test_recycle_waits_for_jobs_then_ends_idle_workers_and_their_children(self):
        """What an installer does before replacing a runtime that a worker's warm server holds open."""
        runner, job, events, ended = self.start_job()
        worker, child = self.wait_for_pids()
        self.assertFalse(runner.recycle_process_lane(), "a running job is never killed by a recycle")
        self.assertFalse(_isolate.process_gone(child, 0.2))
        runner.cancel(job)
        self.assertTrue(ended.wait(30))
        deadline = time.monotonic() + 10
        while not runner.recycle_process_lane():       # the lane goes idle a moment after the job's last event
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.1)
        self.assertTrue(_isolate.process_gone(worker))
        self.assertTrue(_isolate.process_gone(child))

    def test_a_worker_that_crashes_takes_its_child_along(self):
        _runner, _job, events, ended = self.start_job()
        worker, child = self.wait_for_pids()
        os.kill(worker, signal.SIGTERM)     # with nothing to catch it: TerminateProcess on Windows, a plain kill elsewhere
        self.assertTrue(ended.wait(30), "the crash was never reported")
        self.assertIn("error", events)
        self.assertTrue(_isolate.process_gone(child), "the child outlived its worker")

    def test_when_the_window_dies_the_worker_and_its_child_stop(self):
        host = subprocess.Popen([sys.executable, HOST, "run", PLUGINS, str(self.src)])
        self.addCleanup(host.wait)
        self.addCleanup(host.kill)
        worker, child = self.wait_for_pids()
        host.kill()                         # no shutdown, no goodbye: a crash of the app
        self.assertTrue(_isolate.process_gone(worker), "the worker outlived the window")
        self.assertTrue(_isolate.process_gone(child), "the child outlived the window")

    def test_when_the_window_dies_an_idle_worker_stops_too(self):
        host = subprocess.Popen([sys.executable, HOST, "idle"], stdout=subprocess.PIPE, text=True)
        self.addCleanup(host.stdout.close)
        self.addCleanup(host.wait)
        self.addCleanup(host.kill)
        worker = int(host.stdout.readline())
        self.pids = [worker]
        self.assertFalse(_isolate.process_gone(worker, 1.0))
        host.kill()
        self.assertTrue(_isolate.process_gone(worker), "an idle worker waited for a job forever")


class ShutdownTests(unittest.TestCase):
    def test_shutdown_survives_the_worker_being_cleared_under_it(self):
        """The feeding thread can clear the worker at any moment. This raised inside closeEvent, and the app hung on exit."""
        worker = _Worker(SimpleNamespace())
        import queue

        class Dying:
            pid = 0x3FFFFFF          # no such process, so no group to kill
            exitcode = -9

            def is_alive(self):
                return True

            def join(self, timeout=None):
                worker.proc = None   # what the feeding thread does in the middle of shutdown

            def kill(self):
                pass
        worker.proc, worker.inbox = Dying(), queue.Queue()
        worker.shutdown()
        self.assertIsNone(worker.proc)


class EnvironmentTests(unittest.TestCase):
    def test_a_child_of_a_frozen_app_gets_the_library_path_from_before_the_app(self):
        env = {"LD_LIBRARY_PATH": "/app/_internal", "LD_LIBRARY_PATH_ORIG": "/opt/lib"}
        with patch.dict(os.environ, env), patch.object(sys, "frozen", True, create=True), \
                patch.object(procs, "WINDOWS", False):
            child = procs.child_env(EXTRA="1")
        self.assertEqual(child["LD_LIBRARY_PATH"], "/opt/lib")
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", child)
        self.assertEqual(child["EXTRA"], "1")

    def test_no_library_path_before_the_app_means_none_for_the_child(self):
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/app/_internal"}), patch.object(sys, "frozen", True, create=True), \
                patch.object(procs, "WINDOWS", False):
            os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
            self.assertNotIn("LD_LIBRARY_PATH", procs.child_env())

    def test_from_source_the_environment_is_passed_on(self):
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/mine"}):
            self.assertEqual(procs.child_env()["LD_LIBRARY_PATH"], "/mine")

    def test_a_frozen_app_ignores_another_install_s_gis_data(self):
        with patch.dict(os.environ, {"GDAL_DATA": "/qgis/gdal", "PROJ_DATA": "/qgis/proj", "PROJ_LIB": "/qgis/proj"}):
            procs.keep_bundled_data()                                    # from source: left alone
            self.assertEqual(os.environ["GDAL_DATA"], "/qgis/gdal")
            with patch.object(sys, "frozen", True, create=True):
                procs.keep_bundled_data()
            for name in procs.FOREIGN_DATA:
                self.assertNotIn(name, os.environ)


class AppHomeTests(unittest.TestCase):
    def test_a_bundle_and_an_appimage_count_as_one_file(self):
        with patch.object(sys, "frozen", True, create=True):
            with patch.object(sys, "platform", "darwin"), \
                    patch.object(sys, "executable", str(Path("/Applications/Atlas Toolkit.app/Contents/MacOS/Atlas Toolkit"))):
                os.environ.pop("APPIMAGE", None)
                self.assertEqual(paths.app_home().name, "Applications")
            with patch.dict(os.environ, {"APPIMAGE": str(Path("/home/me/apps/Atlas.AppImage"))}):
                self.assertEqual(paths.app_home().name, "apps")


class HelperResolverTests(unittest.TestCase):
    ENV = "ATLAS_TEST_HELPER"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_helper_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.leave = _isolate.fresh_home()
        self.addCleanup(self.leave)

    def executable(self, name):
        path = self.folder / name
        path.write_text("", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def find(self):
        return runtimes.find_helper("atlas-nothing-core", self.ENV, "nothing_exe")

    def picked(self, path):
        from atlas_toolkit.core.settings import AppSettings
        settings = AppSettings()
        settings.set("nothing_exe", str(path))
        settings.save()

    def test_a_wrong_environment_variable_stays_not_found(self):
        self.picked(self.executable("picked"))
        with patch.dict(os.environ, {self.ENV: str(self.folder / "missing")}):
            self.assertIsNone(self.find())

    def test_the_environment_variable_wins(self):
        chosen = self.executable("chosen")
        self.picked(self.executable("picked"))
        with patch.dict(os.environ, {self.ENV: str(chosen)}):
            self.assertEqual(self.find(), chosen)

    def test_a_picked_path_comes_before_the_bundled_one_and_a_stale_one_falls_through(self):
        picked, bundled = self.executable("picked"), self.executable("bundled")
        self.picked(picked)
        with patch.object(runtimes, "resource", return_value=bundled):
            self.assertEqual(self.find(), picked)
            self.picked(self.folder / "from-another-machine")
            self.assertEqual(self.find(), bundled)

    @unittest.skipIf(sys.platform == "win32", "Windows has no execute bit")
    def test_a_file_that_is_not_executable_does_not_count(self):
        path = self.folder / "plain"
        path.write_text("", encoding="utf-8")
        with patch.dict(os.environ, {self.ENV: str(path)}):
            self.assertIsNone(self.find())

    def test_a_file_that_cannot_start_gets_a_clear_message(self):
        with self.assertRaisesRegex(RuntimeError, "cannot start on this system"):
            runtimes.check_helper(self.executable("garbage.exe"), "atlas-nothing-core")

    def test_a_working_helper_passes_once_and_a_wrong_protocol_is_refused(self):
        from atlas_toolkit.geom.remesh_backend import find_backend
        exe = find_backend()
        if exe is None:
            if _isolate.REQUIRE_HELPERS:
                self.fail("the remesh helper is missing")
            self.skipTest("remesh helper not built")
        self.assertIn("atlas-remesh-core", runtimes.check_helper(exe, "atlas-remesh-core", 1))
        with patch.object(procs, "spawn", side_effect=AssertionError("not started twice")):
            runtimes.check_helper(exe, "atlas-remesh-core", 1)
        with self.assertRaisesRegex(RuntimeError, "another protocol"):
            runtimes.check_helper(exe, "atlas-remesh-core", 99)
        with self.assertRaisesRegex(RuntimeError, "not a working"):
            runtimes.check_helper(exe, "atlas-texture-core")


if __name__ == "__main__":
    unittest.main()
