import _isolate  # first: settings and caches stay in a temporary folder
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry

SHARED = ["ink", "travel", "time"]
# FlowRep Sketch orders its strokes inside flowrep3d and reports no travel before it; every other one shows the saving
UNORDERED_REPORT = {"flowrep-sketch"}


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
                    if tool.id not in UNORDERED_REPORT:
                        stats = {s.key: s for s in res.stats}
                        self.assertIsNotNone(stats["travel"].before, "the travel ordering saved")
                        # the time's "was" shows only when it reads differently (a ball saves seconds at most)
                        if tool.id in ("sketch-hatching", "stipple"):
                            self.assertIsNotNone(stats["time"].before, "the plot time ordering saved")

    def test_plot_time_shows_what_ordering_saved(self):
        from atlas_toolkit.geom.drawing import PlotStats, fmt_duration, plot_tiles
        st = PlotStats(ink=25_000.0, travel=600.0, strokes=40)
        tiles = {s.key: s for s in plot_tiles(st, 1.0, (60_600.0, 600.0))}
        self.assertEqual(tiles["time"].before, fmt_duration(st.seconds() + 60_000.0 / 60.0))
        self.assertEqual(tiles["travel"].before, "60.60 m")
        # nothing saved (ordering off), or no ordering at all: no "was"
        for travel in ((600.0, 600.0), None):
            tiles = {s.key: s for s in plot_tiles(st, 1.0, travel)}
            self.assertIsNone(tiles["travel"].before)
            self.assertIsNone(tiles["time"].before)


if __name__ == "__main__":
    unittest.main()
