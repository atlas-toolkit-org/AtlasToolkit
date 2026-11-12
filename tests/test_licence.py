"""One licence everywhere: the app's SPDX id agrees across the metadata, the notices ship, and the bundle leaves out
the libigl modules whose licences do not fit (THIRD_PARTY_NOTICES.md, Compliance)."""
import _isolate  # first: settings and caches stay in a temporary folder
import json
import re
import tomllib
import unittest
from pathlib import Path

from atlas_toolkit import version

ROOT = Path(__file__).resolve().parents[1]
LICENSES = ROOT / "src" / "atlas_toolkit" / "licenses"
NOTICE_OF = {"texture": "surface-texture.txt", "stripes": "surface-stripes.txt",      # helper: its tracked notice
             "remesh": "remesh-decimate.txt", "developable": "developable-surfaces.txt"}


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class LicenceTests(unittest.TestCase):
    def test_one_spdx_id(self):
        sid = version.LICENSE_ID
        self.assertEqual(sid, "GPL-3.0-or-later")
        project = tomllib.loads(read("pyproject.toml"))["project"]
        self.assertEqual(project["license"], sid)
        for f in project["license-files"]:
            self.assertTrue((ROOT / f).is_file(), f"license-files names a missing file: {f}")
        self.assertRegex(read("packaging/linux/nfpm.yaml"), rf"(?m)^license: {re.escape(sid)}$")
        self.assertIn(sid, read("COPYRIGHT.md"))
        self.assertIn(sid, read("README.md"))
        self.assertIn("LICENSE_ID", read("src/atlas_toolkit/ui/about.py"))

    def test_licence_text_is_the_gpl_3(self):
        text = read("LICENSE")
        self.assertTrue(text.lstrip().startswith("GNU GENERAL PUBLIC LICENSE"))
        self.assertIn("Version 3, 29 June 2007", text[:200])
        self.assertIn("END OF TERMS AND CONDITIONS", text)
        self.assertIn("either version 3 of the License, or", read("COPYRIGHT.md"))

    def test_bundle_carries_the_notices(self):
        spec = read("packaging/atlas_toolkit.spec")
        for name in ("LICENSE", "COPYRIGHT.md", "THIRD_PARTY_NOTICES.md"):
            self.assertIn(f'ROOT / "{name}", "atlas_toolkit/licenses"', spec)
        self.assertIn('"igl.copyleft"', spec)
        self.assertIn('"igl.triangle"', spec)

    def test_every_native_helper_has_its_notice(self):
        lock = json.loads(read("native/deps.lock.json"))
        helpers = {h for dep in lock.values() if isinstance(dep, dict) for h in dep.get("used_by", ())}
        self.assertTrue(helpers)
        for helper in sorted(helpers | set(NOTICE_OF)):
            self.assertIn(helper, NOTICE_OF, f"a new helper needs a notice: {helper}")
            self.assertTrue((LICENSES / NOTICE_OF[helper]).is_file(), NOTICE_OF[helper])

    def test_ci_names_no_private_repository(self):
        """The presets repository comes from a variable, never from an owner written into CI."""
        for path in (ROOT / ".github").rglob("*.yml"):
            self.assertNotRegex(path.read_text(encoding="utf-8"), r"[\w.-]+/AtlasToolkit-presets", path.name)


if __name__ == "__main__":
    unittest.main()
