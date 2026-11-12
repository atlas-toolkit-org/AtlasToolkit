"""Release safeguards: dependency search precedence and retaining the installed app until publication succeeds."""
import _isolate  # first: settings and caches stay in a temporary folder
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("atlas_build", Path(__file__).resolve().parents[1] / "scripts/build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class BuildSafetyTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows DLL search order")
    def test_system32_precedes_unrelated_dlls_and_path_is_restored_on_error(self):
        original = os.environ.get("PATH")
        with self.assertRaisesRegex(RuntimeError, "stop"):
            with build.system_dlls_first():
                first = os.environ["PATH"].split(os.pathsep)[0]
                self.assertEqual(Path(first), Path(os.environ["SystemRoot"]) / "System32")
                self.assertTrue(os.environ["PATH"].endswith(original or ""))
                raise RuntimeError("stop")
        self.assertEqual(os.environ.get("PATH"), original)

    def test_failed_publication_preserves_existing_executable(self):
        with tempfile.TemporaryDirectory(prefix="atlas_publish_") as folder:
            folder = Path(folder)
            installed = folder / "Atlas Toolkit.exe"
            installed.write_bytes(b"old app")
            staged = folder / "staged" / installed.name
            staged.parent.mkdir()
            staged.write_bytes(b"new app")
            with patch.object(build, "DIST", folder), patch.object(build.os, "replace", side_effect=OSError("locked")):
                with self.assertRaisesRegex(OSError, "locked"):
                    build.publish(staged)
            self.assertEqual(installed.read_bytes(), b"old app")
            self.assertFalse(list(folder.glob(".atlas-release-*")))
            with patch.object(build, "DIST", folder):
                self.assertEqual(build.publish(staged), installed)
            self.assertEqual(installed.read_bytes(), b"new app")

    def test_publishing_a_folder_replaces_the_old_one_whole(self):
        with tempfile.TemporaryDirectory(prefix="atlas_publish_") as folder:
            folder = Path(folder)
            with patch.object(build, "DIST", folder):
                staged = folder / "staged" / "atlas-toolkit"
                staged.mkdir(parents=True)
                (staged / "new.txt").write_text("new", encoding="utf-8")
                old = folder / "atlas-toolkit"
                old.mkdir()
                (old / "old.txt").write_text("old", encoding="utf-8")
                self.assertEqual(build.publish(staged), old)
                self.assertEqual([p.name for p in old.iterdir()], ["new.txt"])
                self.assertFalse(list(folder.glob(".atlas-retired-*")))
                self.assertFalse(staged.exists())

    def test_a_folder_that_cannot_be_published_leaves_the_old_one(self):
        with tempfile.TemporaryDirectory(prefix="atlas_publish_") as folder:
            folder = Path(folder)
            with patch.object(build, "DIST", folder):
                staged = folder / "staged" / "atlas-toolkit"
                staged.mkdir(parents=True)
                old = folder / "atlas-toolkit"
                old.mkdir()
                (old / "old.txt").write_text("old", encoding="utf-8")
                real = os.replace

                def locked(a, b):
                    if Path(a) == staged:
                        raise OSError("locked")
                    real(a, b)
                with patch.object(build.os, "replace", side_effect=locked), self.assertRaisesRegex(OSError, "locked"):
                    build.publish(staged)
                self.assertEqual([p.name for p in old.iterdir()], ["old.txt"])

    def test_gui_smoke_requires_success_and_a_screenshot(self):
        with patch.object(build.sys, "stdout", io.StringIO()), patch.object(build.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertEqual(build.gui_smoke_test(Path("candidate.exe")), 1)
            def render(args, **kwargs):
                Path(args[-1]).write_bytes(b"PNG")
                return run.return_value
            run.side_effect = render
            self.assertEqual(build.gui_smoke_test(Path("candidate.exe")), 0)
            self.assertEqual(run.call_args.kwargs["timeout"], 60)
            self.assertNotEqual(run.call_args.kwargs["env"]["ATLAS_TOOLKIT_HOME"], os.environ.get("ATLAS_TOOLKIT_HOME"))


if __name__ == "__main__":
    unittest.main()
