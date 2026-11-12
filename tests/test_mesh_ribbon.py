"""Ribbon Wrap: paths for every wrap style, the strip riding outside the skin, tension, layers, tails, tips,
fabric, closed solids, the output, and the Blender cloth settle (real Blender when installed)."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.tools.mesh_ribbon import TOOL, engine
from atlas_toolkit.tools.mesh_ribbon import mesh_ribbon as mr

QUICK = dict(width=15.0, tail_start=20.0, tail_end=20.0, face_budget=80_000)


def context(mode="analyse", log=None):
    def emit(kind, args):
        if log is not None and kind == "log":
            log.append(args)
    return JobContext(emit, lambda: False, mode)


def run(src, mode="analyse", **values):
    vals = TOOL.values(dict(QUICK, **values))
    res = engine.run(context(mode), src, None, vals)
    v = res.preview["views"][0]["mesh"]
    return res, trimesh.Trimesh(v.vertices, v.faces, process=False)


def stats(res):
    return {s.key: s.value for s in res.stats}


def sphere_surface():
    m = trimesh.creation.icosphere(subdivisions=4, radius=1.0)
    return mr.Surface(m.vertices, m.faces)


def values(**kw):
    return dict(TOOL.values(dict(QUICK, **kw)))


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        folder = Path(cls.tmp.name)
        cls.ball = folder / "ball.stl"
        trimesh.creation.icosphere(subdivisions=4, radius=30.0).export(cls.ball)
        cls.pill = folder / "pill.stl"
        trimesh.creation.capsule(height=60.0, radius=20.0, count=(32, 32)).export(cls.pill)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class GeometryTests(unittest.TestCase):
    def test_surface_signs_and_pushes_points_out(self):
        s = sphere_surface()
        S, C, D = s.signed(np.array([[0, 0, 0.5], [0, 0, 1.5]]))
        self.assertLess(S[0], 0)
        self.assertAlmostEqual(S[1], 0.5, places=2)
        P, moved = s.push_out(np.array([[0, 0, 0.5], [0, 0, 1.5]]), 0.1)
        self.assertTrue(moved[0] and not moved[1])
        self.assertAlmostEqual(np.linalg.norm(P[0]), 1.1, places=2)
        P, _ = s.push_along(np.array([[0.2, 0, 0.5]]), np.array([[0, 0, 1.0]]), 0.05)
        self.assertGreater(s.signed(P)[0][0], 0.04)

    def test_outer_hits_meet_the_skin_from_outside(self):
        s = sphere_surface()
        P = s.outer_hits(np.zeros((3, 3)), np.eye(3))
        np.testing.assert_allclose(np.linalg.norm(P, axis=1), 1.0, atol=0.01)
        miss = s.outer_hits(np.array([[0, 0, 5.0]]), np.array([[1.0, 0, 0]]))
        self.assertTrue(np.isnan(miss).all())

    def test_sweep_is_a_closed_outward_solid(self):
        t = np.linspace(0, 1, 20)
        C = np.stack([t, np.zeros_like(t), np.zeros_like(t)], 1)
        X = C[:, None] + np.linspace(-0.1, 0.1, 5)[None, :, None] * np.array([0, 1.0, 0])
        n = np.broadcast_to(np.array([0, 0, 1.0]), X.shape)
        V, F = mr.sweep(mr.rings_for(X, n, np.linspace(-0.5, 0.5, 5), 0.02, 0.2))
        m = trimesh.Trimesh(V, F)
        self.assertTrue(m.is_watertight)
        self.assertGreater(m.volume, 0)
        self.assertAlmostEqual(m.volume, 1.0 * 0.2 * 0.02, delta=0.0015)

    def test_helix_pitch_follows_overlap(self):
        s = sphere_surface()
        rng = np.random.default_rng(0)
        tight = mr.helix_paths(s, values(wobble=0.0, overlap=50.0), 0.2, 0.05, rng)[0]
        open_ = mr.helix_paths(s, values(wobble=0.0, overlap=-100.0), 0.2, 0.05, rng)[0]
        L = lambda P: np.sum(np.linalg.norm(np.diff(P, axis=0), axis=1))
        self.assertGreater(L(tight), 3.0 * L(open_))           # pitch 0.1 vs 0.4
        np.testing.assert_allclose(np.linalg.norm(tight, axis=1), 1.0, atol=0.02)

    def test_bands_and_drape_make_paths(self):
        s = sphere_surface()
        rng = np.random.default_rng(1)
        for layout, n in (("meridians", 3), ("rings", 2), ("random", 4)):
            P = mr.band_paths(s, values(style="bands", band_layout=layout, count=n), 0.2, 0.05, rng)
            self.assertEqual(len(P), n, layout)
            for loop in P:
                r = np.linalg.norm(loop[:, :2], axis=1).mean() if layout == "rings" else 1.0
                self.assertGreater(np.sum(np.linalg.norm(np.diff(loop, axis=0), axis=1)), 2 * math.pi * r * 0.95)
        D = mr.drape_paths(s, values(style="drape", count=2, length=2.0), 0.2, 0.05, 0.01, rng)
        self.assertEqual(len(D), 2)
        np.testing.assert_allclose(np.linalg.norm(D[0], axis=1), 1.01, atol=0.03)

    def test_tails_fall_and_rest_on_the_floor(self):
        s = sphere_surface()
        v = values(gravity=1.0, sway=0.0, curl=0.0, tail_lift=30.0)
        P, B = mr.grow_tail(s, np.array([1.0, 0, 0.2]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]),
                            np.array([1.0, 0, 0]), 3.0, 0.05, v, np.random.default_rng(0), -1.0, 0.01)
        self.assertLess(P[-1, 2], P[0, 2] - 0.8)
        self.assertGreaterEqual(P[:, 2].min(), -1.0)
        self.assertGreater(s.signed(P)[0].min(), 0.005)          # never inside the body
        np.testing.assert_allclose(np.linalg.norm(B, axis=1), 1.0, atol=1e-6)

    def test_tension_bridges_a_hollow(self):
        """A ribbon over a dimpled ball rides across the dimple when taut, dips into it when slack."""
        m = trimesh.creation.icosphere(subdivisions=5, radius=1.0)
        V = m.vertices.copy()
        d = np.linalg.norm(V - np.array([1.0, 0, 0]), axis=1)
        V *= (1.0 - 0.25 * np.exp(-(d / 0.25) ** 2))[:, None]
        s = mr.Surface(V, m.faces)
        depth = {}
        for t in (0.0, 1.0):
            v = values(style="bands", band_layout="rings", count=1, width=10.0, tension=t, tail_start=0.0,
                       tail_end=0.0)
            pl = mr.plan(s, v)
            mr.tension_and_layers(s, pl, v)
            c = pl.ribbons[0].grid[:, pl.ribbons[0].grid.shape[1] // 2]
            depth[t] = np.linalg.norm(c[np.argmin(np.abs(np.arctan2(c[:, 1], c[:, 0])))])   # over the dimple
        self.assertLess(depth[0.0], 0.8)
        self.assertGreater(depth[1.0], depth[0.0] + 0.08)

    def test_crossing_bands_stack_in_layers(self):
        s = sphere_surface()
        v = values(style="bands", band_layout="meridians", count=2, tail_start=0.0, tail_end=0.0, tension=0.0)
        pl = mr.plan(s, v)
        top = mr.tension_and_layers(s, pl, v)
        self.assertGreaterEqual(top, 1)
        self.assertEqual(pl.ribbons[0].layers[pl.ribbons[0].rows_wrapped].min(), 0)   # first band partly on skin
        self.assertGreater(pl.ribbons[1].layers.max(), 0.9)                           # the second rides over it


class EngineTests(Fixture):
    def test_every_style_wraps_and_stays_outside(self):
        for style in ("helix", "bands", "drape"):
            res, out = run(self.ball, style=style, ribbon_only=True)
            st = stats(res)
            self.assertGreater(st["ribbons"], 0, style)
            self.assertGreater(len(out.faces), 100, style)
            r = np.linalg.norm(out.vertices, axis=1)
            self.assertGreater(np.percentile(r, 1), 29.7, style)       # model radius 30: all on or off the skin

    def test_overlap_controls_cover(self):
        full, _ = run(self.pill, overlap=40.0, tail_start=0.0, tail_end=0.0)
        part, _ = run(self.pill, overlap=-150.0, tail_start=0.0, tail_end=0.0)
        self.assertGreater(stats(full)["covered"], 80)
        self.assertLess(stats(part)["covered"], stats(full)["covered"] - 30)

    def test_span_limits_the_wrap(self):
        res, out = run(self.pill, span_from=40.0, span_to=60.0, tail_start=0.0, tail_end=0.0, ribbon_only=True,
                       wobble=0.0)
        z = out.vertices[:, 2]
        self.assertLess(z.max() - z.min(), 0.45 * 100.0)                 # the capsule is 100 tall

    def test_model_kept_unless_ribbon_only(self):
        with_model, a = run(self.ball)
        alone, b = run(self.ball, ribbon_only=True)
        self.assertGreater(len(a.faces), len(b.faces))
        self.assertEqual(len(a.faces) - len(b.faces), len(trimesh.load(self.ball).faces))

    def test_tips_and_fabric(self):
        _, cut = run(self.ball, tip="cut", ribbon_only=True)
        _, fray = run(self.ball, tip="fray", ribbon_only=True)
        self.assertGreater(len(fray.split(only_watertight=False)), len(cut.split(only_watertight=False)) + 3)
        smooth, _ = run(self.ball, ribbon_only=True, ripple=0.0, threads=0, weave=0.0, face_budget=2_000_000)
        silk, _ = run(self.ball, ribbon_only=True, ripple=0.0, threads=8, thread_depth=0.3, face_budget=2_000_000)
        self.assertGreater(stats(silk)["ribbon_faces"], 1.5 * stats(smooth)["ribbon_faces"])   # threads need columns

    def test_same_seed_same_ribbon(self):
        a = run(self.ball, style="drape", seed=3, ribbon_only=True)[1]
        b = run(self.ball, style="drape", seed=3, ribbon_only=True)[1]
        c = run(self.ball, style="drape", seed=4, ribbon_only=True)[1]
        np.testing.assert_allclose(a.vertices, b.vertices)
        self.assertFalse(len(a.vertices) == len(c.vertices) and np.allclose(a.vertices, c.vertices))

    def test_writes_a_signed_mesh(self):
        dst = Path(self.tmp.name) / "ball_ribbon.stl"
        res = engine.run(context("run"), self.ball, dst, TOOL.values(QUICK))
        self.assertTrue(dst.exists())
        self.assertIn(b"atlas-toolkit:mesh-ribbon", dst.read_bytes()[:80])
        self.assertEqual(res.outputs[0], str(dst))

    def test_presets_run(self):
        for found in _presets.each(TOOL):
            name, preset = found.name, _presets.raw(found)
            res, out = run(self.ball, **dict(preset, face_budget=60_000))
            self.assertGreater(stats(res)["ribbons"], 0, name)

    def test_registered(self):
        self.assertIn("mesh-ribbon", registry().tools)


class ClothTests(Fixture):
    def test_blender_cloth_lets_the_tails_fall(self):
        from atlas_toolkit.geom import blender as gb
        if gb.find_blender() is None:
            self.skipTest("Blender is not installed")
        base, a = run(self.ball, ribbon_only=True, tail_start=0.0, tail_end=60.0, gravity=0.0, tail_lift=10.0)
        settled, b = run(self.ball, ribbon_only=True, tail_start=0.0, tail_end=60.0, gravity=0.0, tail_lift=10.0,
                         cloth=True, cloth_frames=30)
        # the end tail streams out level from the crown (no gravity of its own): the cloth brings it down
        self.assertLess(np.percentile(b.vertices[:, 2], 99), np.percentile(a.vertices[:, 2], 99) - 5.0)
        self.assertIn("cloth", settled.report)


if __name__ == "__main__":
    unittest.main()
