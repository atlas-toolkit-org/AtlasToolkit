"""
What keeps the toolkit portable, checked statically so that a Windows machine catches what would only break on macOS
or Linux: encodings, Windows-only calls, the case of imports, shell scripts, machine paths, isolated tests, the locks.
"""
import _isolate  # first: settings and caches stay in a temporary folder
import ast
import os
import re
import shutil
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
# Windows-only calls live here, behind platform checks. Anywhere else they need their own guard and a line here.
WINDOWS_MODULES = {"atlas_toolkit/__main__.py", "atlas_toolkit/core/paths.py", "atlas_toolkit/core/procs.py",
                   "atlas_toolkit/geom/heightfield/resources.py", "atlas_toolkit/ui/app.py", "flowrep3d/metrics.py"}
# Only these start processes directly, so a child always gets procs.child_env and dies with the job that started it.
PROCESS_MODULES = {"atlas_toolkit/core/paths.py", "atlas_toolkit/core/procs.py"}
WINDOWS_ONLY = re.compile(r"\b(ctypes\.windll|ctypes\.WinDLL|os\.startfile|import winreg|import msvcrt|wintypes)\b")
# An absolute path on somebody's machine: a user folder on any drive, a top-level folder on a drive other than C: (where
# people keep their own work), or a home folder. The C:/path/to/... placeholders in examples are fine.
MACHINE_PATH = re.compile(r"(?<![\w/])[A-Za-z]:[\\/]+Users[\\/]|(?<![\w/])[D-Zd-z]:[\\/]+(?!(?:models|data|files|work|path)[\\/])[A-Za-z][\w-]*[\\/]"
                          r"|(?<![\w.~])/(?:home|Users)/(?!(?:Shared|someone|user|username|you|me|example|runner)/)"
                          r"[A-Za-z][\w.-]*/")


def python_files(*folders):
    for folder in folders:
        yield from (p for p in (ROOT / folder).rglob("*.py") if "__pycache__" not in p.parts)


def tracked(*args) -> list[str]:
    if not shutil.which("git") or not (ROOT / ".git").exists():
        raise unittest.SkipTest("needs a git checkout")
    return subprocess.run(["git", "ls-files", *args], cwd=ROOT, capture_output=True, text=True, check=True,
                          encoding="utf-8").stdout.splitlines()


class SourceTests(unittest.TestCase):
    def test_text_files_name_their_encoding(self):
        """The default encoding is the locale's on Windows and UTF-8 elsewhere: never rely on it."""
        bad = []
        for path in python_files("src"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                keys = {k.arg for k in node.keywords}
                if name in ("read_text", "write_text"):
                    text = True
                elif name == "open" and isinstance(node.func, ast.Name):
                    mode = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else "r"
                    text = isinstance(mode, str) and "b" not in mode and "mode" not in keys
                else:
                    continue
                if text and "encoding" not in keys:
                    bad.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(bad, [])

    def test_windows_only_calls_stay_in_their_modules(self):
        for path in python_files("src"):
            rel = path.relative_to(SRC).as_posix()
            text = path.read_text(encoding="utf-8")
            if WINDOWS_ONLY.search(text):
                self.assertIn(rel, WINDOWS_MODULES, "a Windows-only call outside the modules listed in this test")
                self.assertRegex(text, r"sys\.platform|os\.name", f"{rel} has Windows-only calls and no platform check")

    def test_processes_start_through_core_procs(self):
        for path in (SRC / "atlas_toolkit").rglob("*.py"):
            rel = path.relative_to(SRC).as_posix()
            text = path.read_text(encoding="utf-8")
            if rel not in PROCESS_MODULES:
                self.assertNotRegex(text, r"subprocess\.(Popen|run|call|check_call|check_output)\(",
                                    f"{rel}: use core/procs.py, so the child gets a clean environment and dies with its job")
            if rel != "atlas_toolkit/core/paths.py":
                self.assertNotIn("QDesktopServices", text, f"{rel}: use paths.open_url: Qt passes on a frozen app's library path")

    def test_imports_match_the_case_of_the_files(self):
        """Windows and macOS forgive a wrong case, Linux does not."""
        def exact(folder: Path, part: str) -> bool:
            return folder.is_dir() and (part in os.listdir(folder) or f"{part}.py" in os.listdir(folder))

        for path in python_files("src"):
            package = path.parent
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.level:
                        base = package
                        for _ in range(node.level - 1):
                            base = base.parent
                    elif node.module.split(".")[0] in ("atlas_toolkit", "flowrep3d"):
                        base = SRC
                    else:
                        continue
                    for part in node.module.split("."):
                        self.assertTrue(exact(base, part), f"{path.relative_to(ROOT)}:{node.lineno} {node.module}")
                        base = base / part

    def test_no_machine_paths(self):
        documents = [ROOT / "requirements.lock.txt", *ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md"), *(ROOT / "native").glob("*.md")]
        for path in [*python_files("src", "tests", "scripts"), *(ROOT / "native").glob("*.py"), *documents]:
            if path != Path(__file__).resolve():
                self.assertIsNone(MACHINE_PATH.search(path.read_text(encoding="utf-8")), path.relative_to(ROOT))


class RepositoryTests(unittest.TestCase):
    def test_no_two_tracked_paths_differ_only_by_case(self):
        files = tracked()
        folded = {}
        for name in files:
            folded.setdefault(name.casefold(), []).append(name)
        self.assertEqual([names for names in folded.values() if len(names) > 1], [])

    def test_shell_scripts_are_executable_with_lf_endings(self):
        for line in tracked("--stage", "*.sh"):
            mode, _sha, _stage, name = line.split(maxsplit=3)
            self.assertEqual(mode, "100755", f"{name}: git update-index --chmod=+x")
        for line in tracked("--eol", "*.sh"):
            self.assertTrue(line.startswith("i/lf"), line)

    def test_lock_files_agree(self):
        """requirements.lock.txt is exported from uv.lock: `python scripts/setup_env.py --lock` refreshes both."""
        locked = {p["name"]: p.get("version") for p in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]}
        pins = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", (ROOT / "requirements.lock.txt").read_text(encoding="utf-8"), re.M)
        self.assertGreater(len(pins), 20)
        for name, version in pins:
            self.assertEqual(locked.get(re.sub(r"[-_.]+", "-", name).lower()), version, name)
        self.assertEqual(len(pins), len(locked) - 1, "uv.lock has packages the export lacks")   # all but the app itself


class TestModuleTests(unittest.TestCase):
    def test_every_test_module_isolates_itself_first(self):
        for path in (ROOT / "tests").rglob("test_*.py"):
            first = next(n for n in ast.parse(path.read_text(encoding="utf-8")).body
                         if isinstance(n, (ast.Import, ast.ImportFrom))
                         and not (isinstance(n, ast.ImportFrom) and n.module == "__future__"))
            self.assertEqual(getattr(first, "names", [None])[0].name, "_isolate", path.relative_to(ROOT))

    def test_the_tests_really_are_away_from_the_real_profile(self):
        from atlas_toolkit.core import paths
        for folder in (paths.config_dir(), paths.data_dir(), paths.cache_dir()):
            self.assertTrue(folder.is_relative_to(_isolate.ROOT), folder)


class HelperTests(unittest.TestCase):
    @unittest.skipUnless(_isolate.REQUIRE_HELPERS, "CI only: ATLAS_TEST_REQUIRE_HELPERS=1")
    def test_every_helper_is_built_and_starts_on_this_os(self):
        for helper in _isolate.HELPERS:
            exe = ROOT / "src" / "atlas_toolkit" / "bin" / (f"atlas-{helper}-core" + (".exe" if sys.platform == "win32" else ""))
            done = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn(helper, done.stdout)

    def test_the_remesh_version_is_the_same_everywhere(self):
        """A helper's version lives in its source, its CMake project and its notice: they move together."""
        version = re.search(r'atlas-remesh-core ([\d.]+) protocol', (ROOT / "native/remesh/main.cpp").read_text(encoding="utf-8"))[1]
        self.assertIn(f"project(atlas_remesh VERSION {version} ", (ROOT / "native/remesh/CMakeLists.txt").read_text(encoding="utf-8"))
        self.assertIn(f"helper {version},", (ROOT / "native/build.py").read_text(encoding="utf-8"))
        self.assertIn(f"helper {version},", (ROOT / "src/atlas_toolkit/licenses/remesh-decimate.txt").read_text(encoding="utf-8"))


class DoctorTests(unittest.TestCase):
    def test_doctor_reports_without_failing_on_what_it_cannot_find(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "doctor.py")], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        self.assertIn("Python", r.stdout)
        self.assertIn("git", r.stdout)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
