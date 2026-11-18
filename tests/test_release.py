"""
scripts/release.py's edits, on text only (no git, no network): the install guides move to the new version in their
file names, links and "current version" line and nowhere else, and the changelog heading takes the release date.
"""
import _isolate  # first: settings and caches stay in a temporary folder
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_script", ROOT / "scripts" / "release.py")
rel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rel)


class ReleaseEditTests(unittest.TestCase):
    def test_install_guides_move_their_file_names_only(self):
        text = ("The current version is\n**2.3.1**:\n"
                "| [`AtlasToolkit-2.3.1-windows-x64-setup.exe`](https://x/releases/download/v2.3.1/AtlasToolkit-2.3.1-"
                "windows-x64-setup.exe) |\n"
                "sudo apt install ./atlas-toolkit_2.3.1_amd64.deb\nsudo dnf install ./atlas-toolkit-2.3.1-1.x86_64.rpm\n"
                "and 2.3.1 was used by hand on a remote Mac.\n")
        new = rel.install_docs_for(text, "2.3.1", "2.4.0")
        self.assertIn("**2.4.0**:", new)
        self.assertIn("/v2.4.0/AtlasToolkit-2.4.0-windows-x64-setup.exe", new)
        self.assertIn("atlas-toolkit_2.4.0_amd64.deb", new)
        self.assertIn("atlas-toolkit-2.4.0-1.x86_64.rpm", new)
        self.assertIn("and 2.3.1 was used by hand", new, "what a version was tested on stays true")

    def test_the_guides_in_the_repository_name_this_version(self):
        version = rel.app_version()
        for f in rel.INSTALL_DOCS:
            self.assertIn(f"**{version}**:", f.read_text(encoding="utf-8"), f.name)

    def test_changelog_heading_takes_the_date(self):
        text = "# Changelog\n\n## 2.4.0 (2026-11-18)\n\n- a\n\n## 2.3.1 (2026-11-14)\n"
        self.assertIn("## 2.4.0 (2026-12-01)\n", rel.dated_changelog(text, "2.4.0", "2026-12-01"))
        self.assertIn("## 2.3.1 (2026-11-14)", rel.dated_changelog(text, "2.4.0", "2026-12-01"))
        self.assertIn("## 2.5.0 (2026-12-01)", rel.dated_changelog("## 2.5.0\n", "2.5.0", "2026-12-01"))

    def test_the_server_step_goes_live_only_after_every_checksum(self):
        s = rel.SERVER_FILL
        self.assertLess(s.index("sha256sum -c"), s.index("mv sums.tmp SHA256SUMS.txt"))
        for name in rel.INSTALLERS:
            self.assertIn(name.split("{v}")[0], s)


if __name__ == "__main__":
    unittest.main()
