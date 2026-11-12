"""The README is the catalogue of tools: every tool has a card, every card its image, and no link goes nowhere."""
import _isolate  # first: settings and caches stay in a temporary folder
import re
import unittest
from pathlib import Path
from urllib.parse import unquote

from atlas_toolkit.core.registry import registry

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
IMAGES = ROOT / "docs" / "images" / "readme"
LINK = re.compile(r"""(?:\]\(|(?:src|href)=")([^)"\s]+)""")
TOOLS = registry().tools


class ReadmeTests(unittest.TestCase):
    def test_every_tool_is_named(self):
        text = README.read_text(encoding="utf-8")
        for tool in TOOLS.values():
            self.assertIn(tool.name, text, f"{tool.id} has no card in README.md")

    def test_every_tool_but_the_svg_ones_has_its_image(self):
        text = README.read_text(encoding="utf-8")
        for tool in TOOLS.values():
            path = f"docs/images/readme/{tool.id}.webp"
            if tool.category == "svg":
                self.assertNotIn(path, text)
            else:
                self.assertIn(path, text, f"{tool.id} has no image in README.md")
                self.assertTrue((IMAGES / f"{tool.id}.webp").is_file(), f"{path} is missing")

    def test_relative_links_resolve(self):
        for page in (README, ROOT / "docs" / "TOOLS.md"):
            for target in LINK.findall(page.read_text(encoding="utf-8")):
                if re.match(r"[a-z]+:", target) or target.startswith("#"):
                    continue
                path = unquote(target.split("#")[0])
                self.assertTrue((page.parent / path).exists(), f"{page.name}: {target}")

    def test_the_guide_keeps_the_anchors_the_readme_links_to(self):
        guide = (ROOT / "docs" / "TOOLS.md").read_text(encoding="utf-8")
        for anchor in re.findall(r"docs/TOOLS\.md#([a-z0-9-]+)", README.read_text(encoding="utf-8")):
            headings = (re.sub(r"[^\w\- ]", "", h).strip().lower().replace(" ", "-")
                        for h in re.findall(r"^#+ (.+)$", guide, re.M))
            self.assertIn(anchor, set(headings))


if __name__ == "__main__":
    unittest.main()
