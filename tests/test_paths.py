"""Where the app looks for things outside itself: the GPU depth runtime, and the optional dev folders."""
import _isolate  # first: settings and caches stay in a temporary folder
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atlas_toolkit import devpaths
from atlas_toolkit.core import paths
from atlas_toolkit.geom import depth


class RuntimePathTests(unittest.TestCase):
    def test_ml_runtime_is_machine_local_and_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"LOCALAPPDATA": str(Path(tmp) / "local"), "APPDATA": str(Path(tmp) / "roaming"),
                   "XDG_DATA_HOME": str(Path(tmp) / "data")}
            with patch.dict(os.environ, env), patch.object(paths, "app_home", return_value=Path(tmp) / "app"):
                os.environ.pop(paths.HOME_ENV)
                paths._base.cache_clear()
                try:
                    where = paths.ml_runtime_dir()
                finally:
                    paths._base.cache_clear()
            self.assertEqual(where.name, "ml-runtime")
            self.assertFalse(where.exists())
            if sys.platform == "win32":      # never in the roaming profile: it is ~4.5 GB and machine specific
                self.assertEqual(where.parent, Path(tmp) / "local" / "AtlasToolkit")

    def test_home_variable_keeps_everything_in_one_folder(self):
        leave = _isolate.fresh_home()
        self.addCleanup(leave)
        home = Path(os.environ[paths.HOME_ENV])
        self.assertEqual(paths.config_dir(), home)
        self.assertEqual(paths.data_dir(), home / "data")
        self.assertEqual(paths.cache_dir(), home / "cache")

    @unittest.skipIf(sys.platform in ("win32", "darwin"), "XDG folders are for Linux")
    def test_an_empty_xdg_variable_counts_as_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"HOME": tmp, "XDG_CONFIG_HOME": "", "XDG_DATA_HOME": "", "XDG_CACHE_HOME": ""}
            with patch.dict(os.environ, env), patch.object(paths, "app_home", return_value=Path(tmp) / "app"):
                os.environ.pop(paths.HOME_ENV)
                paths._base.cache_clear()
                try:
                    found = paths._base("config"), paths._base("cache")
                finally:
                    paths._base.cache_clear()
        self.assertEqual(found, (Path(tmp) / ".config" / "atlas-toolkit", Path(tmp) / ".cache" / "atlas-toolkit"))

    def test_venv_python_per_platform(self):
        rel = paths.venv_python(Path("env")).relative_to("env").as_posix()
        self.assertEqual(rel, "Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    def test_depth_runtime_candidates_in_order(self):
        with patch.dict(os.environ, {depth.ENV_PYTHON: "chosen-python"}):
            cands = depth.runtime_candidates()
        self.assertEqual(cands[0], Path("chosen-python"))
        self.assertEqual(cands[-1], paths.venv_python(paths.ml_runtime_dir()))
        repo = Path(depth.__file__).resolve().parents[3]
        self.assertIn(paths.venv_python(repo / ".venv-ml"), cands)
        self.assertFalse(any("atlas_toolkit" in c.parts and ".venv-ml" in c.parts for c in cands),
                         "the old code/Python/atlas_toolkit/.venv-ml layout is gone")


class DevPathTests(unittest.TestCase):
    def tearDown(self):
        devpaths._local.cache_clear()

    def test_environment_wins_and_missing_folders_are_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"ATLAS_STUDIO_WORKSPACE": tmp}):
                self.assertEqual(devpaths.workspace(), Path(tmp))
                self.assertEqual(devpaths.workspace_path("a", "b"), Path(tmp) / "a" / "b")
            with patch.dict(os.environ, {"ATLAS_STUDIO_WORKSPACE": str(Path(tmp) / "absent")}):
                self.assertIsNone(devpaths.workspace())
                self.assertIn("STUDIO_WORKSPACE_NOT_SET", str(devpaths.studio_workspace()))

    def test_local_config_is_read_when_no_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "dev.local.toml"
            config.write_text(f'[paths]\nworkspace = "{Path(tmp).as_posix()}"\n', encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k not in ("ATLAS_STUDIO_WORKSPACE", "ATLAS_DESIGN_DIR")}
            with patch.dict(os.environ, env, clear=True), patch.object(devpaths, "LOCAL_CONFIG", config):
                devpaths._local.cache_clear()
                self.assertEqual(devpaths.workspace(), Path(tmp))

    def test_dev_font_ships_with_the_app(self):
        self.assertTrue(Path(devpaths.DEV_FONT).is_file())


if __name__ == "__main__":
    unittest.main()
