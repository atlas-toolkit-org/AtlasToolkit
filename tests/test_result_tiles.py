import _isolate  # first: settings and caches stay in a temporary folder
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry

SHARED = ["ink", "travel", "time"]


class ResultTileTests(unittest.TestCase):
    """Every Mesh → SVG tool shows the same result tiles in the same order (geom/drawing.plot_tiles): Ink, Pen-up
    travel, Plot time, at most two of its own, then Computed in. The queue's columns come first and are not tiles."""

    def test_mesh_to_svg_tools_share_their_result_tiles(self):
        import trimesh
        tools = [t for t in registry().tools.values() if t.category == "mesh2svg"]
        self.assertGreaterEqual(len(tools), 6)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            model = tmp / "ball.stl"
            trimesh.creation.icosphere(subdivisions=2, radius=20.0).export(model)
            for tool in tools:
                with self.subTest(tool=tool.id), patch.dict(os.environ, {"FLOWREP_CACHE": str(tmp / "cache")}):
                    options = tool.defaults()
                    if "regularize" in options:
                        options["regularize"] = 0
                    res = tool.engine_fn()(JobContext(lambda *a: None, lambda: False, "draft"), model, None, options)
                    tiles = [s.key for s in res.stats if s.key not in tool.columns]
                    self.assertEqual(tiles[:3], SHARED)
                    self.assertEqual(tiles[-1], "seconds")
                    self.assertLessEqual(len(tiles), 5)
                    self.assertTrue(all(s.text() for s in res.stats))


if __name__ == "__main__":
    unittest.main()
