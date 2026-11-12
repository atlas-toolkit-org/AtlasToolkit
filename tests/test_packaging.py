"""The installers' descriptions agree with the app and with each other. Whether they really install is scripts/install_test.py."""
import _isolate  # first: settings and caches stay in a temporary folder

import ast
import importlib.util
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from atlas_toolkit.version import APP_ID, __version__

ROOT = Path(__file__).resolve().parents[1]
LINUX = ROOT / "packaging" / "linux"
INNO = ROOT / "packaging" / "windows" / "atlas_toolkit.iss"
SCRIPTS = ROOT / "scripts"


def script(name: str):
    spec = importlib.util.spec_from_file_location(f"atlas_script_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LinuxPackages(unittest.TestCase):
    def setUp(self):
        self.desktop = dict(line.split("=", 1) for line in (LINUX / f"{APP_ID}.desktop").read_text(encoding="utf-8").splitlines() if "=" in line)
        self.mime = (LINUX / f"{APP_ID}.xml").read_text(encoding="utf-8")
        self.nfpm = (LINUX / "nfpm.yaml").read_text(encoding="utf-8")

    def test_the_desktop_entry_matches_the_app(self):
        # On Wayland the window finds its menu entry and icon through setDesktopFileName(APP_ID).
        self.assertEqual(self.desktop["Icon"], APP_ID)
        self.assertEqual(self.desktop["StartupWMClass"], APP_ID)
        self.assertEqual(self.desktop["Exec"], f"{APP_ID} %F")

    def test_the_file_types_the_entry_claims_are_the_ones_defined(self):
        claimed = [t for t in self.desktop["MimeType"].split(";") if t]
        self.assertEqual(len(claimed), 3)
        for mime in claimed:
            self.assertIn(f'mime-type type="{mime}"', self.mime)
        for ext in ("atlasflow", "atlaspreset", "atlaspack"):
            self.assertIn(f'glob pattern="*.{ext}"', self.mime)

    def test_every_installer_opens_presets_and_packs(self):
        iss = (ROOT / "packaging" / "windows" / "atlas_toolkit.iss").read_text(encoding="utf-8")
        spec = (ROOT / "packaging" / "atlas_toolkit.spec").read_text(encoding="utf-8")
        for ext in ("atlasflow", "atlaspreset", "atlaspack"):
            self.assertIn(f'Subkey: "Software\Classes\.{ext}"', iss)
            self.assertIn(f'["{ext}"]', spec)

    def test_every_file_the_package_names_exists(self):
        sources = re.findall(r"^\s*-\s*src:\s*(\S+)", self.nfpm, re.M)
        scripts = re.findall(r"^\s*post\w+:\s*(\S+)", self.nfpm, re.M)
        names = [s for s in sources + scripts if "$" not in s and not s.startswith("/")]
        self.assertGreaterEqual(len(names), 5)
        for name in names:
            self.assertTrue((ROOT / name).is_file(), name)

    def test_the_package_scripts_keep_unix_line_endings(self):
        for name in ("refresh-desktop.sh", "AppRun"):
            self.assertNotIn(b"\r", (LINUX / name).read_bytes(), name)

    def test_the_appimage_runs_the_program_the_deb_and_rpm_install(self):
        self.assertIn(f'usr/lib/{APP_ID}/{APP_ID}"', (LINUX / "AppRun").read_text(encoding="utf-8"))

    def test_deb_and_rpm_declare_the_same_libraries(self):
        deb = re.search(r"^  deb:\n    depends:\n((?:      - .*\n)+)", self.nfpm, re.M)[1]
        rpm = re.search(r"^  rpm:\n(?:    #.*\n)?    depends:\n((?:      - .*\n)+)", self.nfpm, re.M)[1]
        self.assertEqual(deb.count("\n"), rpm.count("\n"))


class WindowsInstaller(unittest.TestCase):
    def setUp(self):
        self.text = INNO.read_text(encoding="utf-8")

    def test_the_app_id_never_changes(self):
        # Windows finds the installed version by this: a new one would install a second copy beside the first.
        self.assertIn("AppId={{7C1E5A52-6D0B-4B7E-9C1E-3F0B8D2A4E11}", self.text)

    def test_it_installs_per_user_unless_asked(self):
        self.assertIn("PrivilegesRequired=lowest", self.text)
        self.assertIn("PrivilegesRequiredOverridesAllowed=dialog commandline", self.text)

    def test_an_upgrade_replaces_the_payload(self):
        self.assertRegex(self.text, r'\[InstallDelete\]\s+Type: filesandordirs; Name: "\{app\}\\_internal"')

    def test_every_define_the_script_uses_is_passed(self):
        used = set(re.findall(r"\{#(\w+)\}", self.text))
        passed = set(re.findall(r'/D(\w+)=', (SCRIPTS / "package.py").read_text(encoding="utf-8")))
        self.assertEqual(used, passed)


class Scripts(unittest.TestCase):
    def test_package_reads_the_app_version(self):
        self.assertEqual(script("package").version(), __version__)

    def test_the_install_test_reads_the_list_the_packages_declare(self):
        declared = script("install_test").declared_dependencies
        self.assertIn("libegl1", declared("deb"))
        self.assertIn("libc6", declared("deb"))            # without its version condition: this goes to apt-get as a name
        self.assertIn("libEGL.so.1()(64bit)", declared("rpm"))
        self.assertEqual(len(declared("deb")), len(declared("rpm")))

    def test_release_notes_hold_the_changelog_entry_and_every_installer(self):
        notes = script("release_notes").notes(f"v{__version__}")
        self.assertIn(script("release_notes").changelog_entry(__version__), notes)
        for name in (f"AtlasToolkit-{__version__}-windows-x64-setup.exe", f"atlas-toolkit_{__version__}_amd64.deb",
                     f"atlas-toolkit-{__version__}-1.x86_64.rpm", f"AtlasToolkit-{__version__}-linux-x86_64.AppImage",
                     f"AtlasToolkit-{__version__}-macos-arm64.dmg"):
            self.assertIn(name, notes)

    def test_a_tag_that_is_not_this_version_stops_the_release(self):
        with self.assertRaises(SystemExit) as stopped:
            script("release_notes").notes("v0.0.0")
        self.assertIn(f"v{__version__}", str(stopped.exception))

    def test_a_tag_only_ever_makes_a_draft(self):
        text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("--draft", text)
        self.assertEqual(text.count("contents: write"), 1)      # only the job that makes the draft can write
        self.assertRegex(text, r"(?m)^permissions:\n  contents: read$")
        self.assertIn("uses: ./.github/workflows/build.yml", text)

    def test_build_can_be_called_for_a_release_and_still_needs_the_label_on_a_pull_request(self):
        text = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_call:", text)
        self.assertEqual(text.count("if: github.event_name != 'pull_request' || github.event.label.name == 'full-ci'"), 3)

    def test_the_disk_image_shows_the_app_and_a_link_to_applications(self):
        settings = (ROOT / "packaging" / "macos" / "dmg_settings.py").read_text(encoding="utf-8")
        self.assertIn('symlinks = {"Applications": "/Applications"}', settings)
        self.assertIn('icon_locations = {appname:', settings)

    def test_the_install_test_will_not_change_a_machine_unasked(self):
        env = {k: v for k, v in os.environ.items() if k != "CI"}
        done = subprocess.run([sys.executable, str(SCRIPTS / "install_test.py"), "nothing.deb"], capture_output=True, text=True, env=env)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("--container or --throwaway", done.stderr)

    def test_the_install_test_only_uses_the_standard_library(self):
        # It runs in clean containers, under a Python that has nothing else.
        for name in ("install_test", "check_frozen_gui"):
            tree = ast.parse((SCRIPTS / f"{name}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) else []
                for module in modules:
                    root = module.split(".")[0]
                    self.assertTrue(root in sys.stdlib_module_names or root == "check_frozen_gui", module)


if __name__ == "__main__":
    unittest.main()
