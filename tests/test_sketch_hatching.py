"""Sketch Hatching: roles of the lines, hatching rules, complexity, the ink budget, and parity with the Blender
pieces' own drawing module (projects/blender3d_project/.../sketch_draw.py, numpy only) where it is present."""
import _isolate  # first: settings and caches stay in a temporary folder
import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.io import output_path
from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.devpaths import workspace_path
from atlas_toolkit.tools.sketch_hatching import TOOL, engine
from atlas_toolkit.tools.sketch_hatching import sketch_hatching as sh

# the original Blender piece, in the studio workspace (dev.local.toml); the parity test skips without it
PIECE = (workspace_path("projects", "blender3d_project", "highs_and_lows", "plotter_lib", "sketch_draw.py")
         or Path("no-studio-workspace"))


def context(mode="run"):
    return JobContext(lambda *_: None, lambda: False, mode)


def pieces_module():
    spec = importlib.util.spec_from_file_location("pieces_sketch_draw", PIECE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_gbuffer(H=60, W=80, seed=0):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    hit = ((xx - W / 2) ** 2 / (W / 2.4) ** 2 + (yy - H / 2) ** 2 / (H / 2.4) ** 2) < 1
    n = H * W
    f = rng.normal(size=(H, W, 2))
    return {"shape": (H, W), "x0": 10.0, "y0": 20.0, "px": 0.2, "hit": hit.ravel(),
            "ndotl": np.cos(xx / W * 3).ravel(), "ndotl_soft": np.cos(xx / W * 2.5).ravel(),
            "lit": (rng.random(n) > 0.2).astype(float), "ao": rng.random(n), "attn": np.zeros(n),
            "fields": {"principal": f, "slice": np.roll(f, 3, axis=1)}}


class DrawTests(unittest.TestCase):
    def test_complexity_scales_lines_density_and_passes(self):
        pc = engine.config(TOOL.values(), False)
        lo = sh.apply_complexity(pc, 0.0)
        hi = sh.apply_complexity(pc, 1.0)
        self.assertAlmostEqual(lo["lines"]["suggestive_min_strength"], 2 * pc["lines"]["suggestive_min_strength"])
        self.assertAlmostEqual(hi["lines"]["suggestive_min_strength"], 0.5 * pc["lines"]["suggestive_min_strength"])
        self.assertEqual(len(lo["hatching"]["passes"]), 1)
        self.assertEqual(len(hi["hatching"]["passes"]), 3)
        self.assertLess(lo["hatching"]["density"], pc["hatching"]["density"])
        self.assertIs(sh.apply_complexity(pc, 0.5), pc)

    @unittest.skipUnless(PIECE.exists(), "the Blender pieces are not here")
    def test_tone_and_field_match_the_pieces(self):
        theirs = pieces_module()
        gb = fake_gbuffer()
        cfg = dict(sh.AUGUSTUS["tone"])
        np.testing.assert_allclose(sh.tone_map(gb, **cfg), theirs.tone_map(gb, **cfg))
        tone = sh.tone_map(gb, **cfg)
        for field in ("principal", "slice", "isophote", "screen"):
            np.testing.assert_allclose(sh.hatch_field(gb, tone, field), theirs.hatch_field(gb, tone, field))


class EngineTests(unittest.TestCase):
    def setUp(self):
        registry()
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_sketch_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def write(self, mesh, name):
        p = self.folder / name
        mesh.export(p)
        return p

    def test_a_sphere_is_an_outline_and_shadow_hatching(self):
        src = self.write(trimesh.creation.icosphere(subdivisions=5, radius=30.0), "ball.stl")
        vals = TOOL.values(dict(paper="A5", theta=90.0, psi=180.0, radius=250.0, light_az=-60.0, light_el=30.0))
        res = engine.run(context("analyse"), src, None, vals)
        pens = {p.label: p for p in res.preview["views"][0]["drawing"].pens}
        self.assertEqual(set(pens), {"fine", "bold"})
        self.assertGreaterEqual(pens["bold"].strokes, 1)              # the outline
        self.assertGreater(res.data["hatch"], 20)
        self.assertLess(res.data["coverage"], 0.7)
        # hatching sits on the shadow side (the light is on the left): most hatch ink right of centre
        xy = pens["fine"].xy
        mid = res.preview["views"][0]["drawing"].width / 2
        self.assertGreater((xy[:, 0] > mid).mean(), 0.6)

    def test_lines_only_and_inner_contours_of_a_torus(self):
        src = self.write(trimesh.creation.torus(major_radius=30.0, minor_radius=10.0, major_sections=96,
                                                minor_sections=48), "ring.stl")
        vals = TOOL.values(dict(paper="A5", hatching=False, theta=60.0, psi=180.0, radius=300.0))
        res = engine.run(context("analyse"), src, None, vals)
        self.assertEqual(res.data["hatch"], 0)
        self.assertGreaterEqual(res.data["lines"], 2)                 # outer outline + the hole

    def test_ink_budget_thins_the_hatching(self):
        src = self.write(trimesh.creation.icosphere(subdivisions=5, radius=30.0), "ball.stl")
        base = dict(paper="A5", theta=90.0, psi=180.0, radius=250.0, light_az=-60.0, light_el=10.0, density=2.0)
        free = engine.run(context("analyse"), src, None, TOOL.values(base))
        capped = engine.run(context("analyse"), src, None, TOOL.values(dict(base, max_ink=1.0)))
        ink = lambda r: r.preview["views"][0]["drawing"].stats().ink        # noqa: E731
        self.assertLess(ink(capped), ink(free))

    def test_run_writes_two_signed_pen_layers(self):
        src = self.write(trimesh.creation.icosphere(subdivisions=4, radius=30.0), "ball.stl")
        vals = TOOL.values(dict(paper="A5", theta=90.0, psi=180.0, radius=250.0))
        dst = output_path(TOOL, src, vals)
        engine.run(context(), src, dst, vals)
        text = dst.read_text(encoding="utf-8")
        self.assertIn("<desc>sketch-hatching</desc>", text)
        self.assertIn('inkscape:label="1 fine"', text)
        self.assertIn('inkscape:label="2 bold"', text)


if __name__ == "__main__":
    unittest.main()
