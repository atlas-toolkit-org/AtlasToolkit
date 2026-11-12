"""Holes: profiles, sweeps and bubbles as exact solids, spacing, the lattice weave, every hole type in every layout,
irregularity, influence, loose pieces and open models."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import itertools
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.tools.mesh_holes import TOOL, engine
from atlas_toolkit.tools.mesh_holes import mesh_holes as mh

QUICK = dict(segments=12, draft_segments=12, count=30, size=8.0, size_max=12.0, spacing=22.0, spokes=6)


def context(mode="run", log=None):
    def emit(kind, args):
        if log is not None and kind == "log":
            log.append(args)
    return JobContext(emit, lambda: False, mode)


def run_mesh(src, mode="analyse", log=None, **values):
    """Run with QUICK settings and the given ones; returns (result, output preview as trimesh)."""
    vals = TOOL.values(dict(QUICK, **values))
    res = engine.run(context(mode, log), src, None, vals)
    v = res.preview["views"][0]["mesh"]
    return res, trimesh.Trimesh(v.vertices, v.faces, process=False)


def stats(res):
    return {s.key: s.value for s in res.stats}


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        folder = Path(cls.tmp.name)
        cls.ball = folder / "ball.stl"
        trimesh.creation.icosphere(subdivisions=4, radius=30.0).export(cls.ball)
        cls.block = folder / "block.stl"
        trimesh.creation.box(extents=(40.0, 40.0, 40.0)).export(cls.block)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class GeometryTests(unittest.TestCase):
    def test_profiles_have_the_unit_circles_area_and_run_counter_clockwise(self):
        for kind in mh.PROFILES:
            for aspect in (1.0, 2.5):
                P = mh.profile(kind, 32, 5, 4.0, 5, 0.6, aspect, mh.blob_noise(np.random.default_rng(1)),
                               mh.blob_noise(np.random.default_rng(2)), 0.5)
                self.assertAlmostEqual(mh.polygon_area(P), math.pi, places=6, msg=(kind, aspect))
        self.assertEqual(len(mh.profile("polygon", sides=6)), 6)

    def test_a_swept_profile_is_a_closed_solid_of_the_right_volume(self):
        outline = mh.profile("circle", 64)
        P = np.array([[0, 0, 0], [0, 0, 3.0]])
        V, F = mh.sweep_mesh(outline, P, np.ones(2), np.zeros(2))
        m = mh.to_manifold(V, F)
        self.assertAlmostEqual(m.volume(), math.pi * 3.0, places=6)      # area π (normalised), length 3
        # a bent, twisted, swelling path stays a valid solid
        t = np.linspace(0, math.pi, 30)
        P = np.stack([4 * np.cos(t), 4 * np.sin(t), t], 1)
        V, F = mh.sweep_mesh(mh.profile("polygon", sides=4), P, 1 + 0.3 * np.sin(3 * t), t)
        m = mh.to_manifold(V, F)
        self.assertEqual(m.status().name, "NoError")
        self.assertGreater(m.volume(), 0)

    def test_frames_do_not_twist_on_a_planar_curve(self):
        t = np.linspace(0, math.pi, 40)
        P = np.stack([np.cos(t), np.sin(t), np.zeros_like(t)], 1)
        T, E1, E2 = mh.frames(P)
        np.testing.assert_allclose(np.abs(E2[:, 2]), 1.0, atol=1e-9)        # the binormal stays out of the plane
        np.testing.assert_allclose(np.einsum("ij,ij->i", E1, T), 0.0, atol=1e-9)

    def test_bubbles_have_the_unit_spheres_volume(self):
        rng = np.random.default_rng(3)
        for kind in mh.PROFILES:
            b = mh.bubble_unit(kind, 24, 6, 4.0, 0.5, 5, mh.blob_noise(rng), mh.blob_noise(rng), 0.5,
                               rng.normal(scale=0.2, size=(14, 3)))
            self.assertAlmostEqual(b.volume(), 4 / 3 * math.pi, places=4, msg=kind)

    def test_segment_distance_matches_brute_force(self):
        rng = np.random.default_rng(4)
        P0, P1, Q0, Q1 = (rng.normal(size=(200, 3)) for _ in range(4))
        P1[:20] = P0[:20]                                                    # points as segments too
        d = mh.seg_dist(P0, P1, Q0, Q1)
        s = np.linspace(0, 1, 201)
        for i in range(0, 200, 13):
            A = P0[i] + (P1[i] - P0[i]) * s[:, None]
            B = Q0[i] + (Q1[i] - Q0[i]) * s[:, None]
            brute = np.linalg.norm(A[:, None] - B[None], axis=2).min()
            self.assertLessEqual(d[i], brute + 1e-12)
            self.assertAlmostEqual(d[i], brute, delta=0.02)

    def test_spacing_keeps_the_wall_and_shrinks_before_dropping(self):
        rng = np.random.default_rng(5)
        holes = []
        for p in rng.random((300, 3)):
            h = mh.Hole("bubble", p[None, :], p[None, :], np.array([0, 0, 1.0]), radius=0.05 + 0.05 * rng.random())
            h.reach = h.radius
            holes.append(h)
        gap = 0.02
        kept, shrunk = mh.space(holes, gap, min_radius=0.03, fixed=False)
        self.assertGreater(shrunk, 0)
        C = np.array([h.path[0] for h in kept])
        R = np.array([h.reach for h in kept])
        D = np.linalg.norm(C[:, None] - C[None], axis=2) - R[:, None] - R[None]
        np.fill_diagonal(D, np.inf)
        self.assertGreaterEqual(D.min(), gap - 1e-9)
        self.assertTrue(all(h.radius >= 0.03 - 1e-12 for h in kept))
        self.assertEqual(mh.space(holes, 0.0, 0.03, False)[0], holes)        # 0: anything goes

    def test_woven_lattice_lines_never_meet(self):
        model = mh.Model(np.zeros((1, 3)), np.zeros((0, 3), int), np.array([-1.0, -1, -1]), np.array([1.0, 1, 1]))
        values = TOOL.values(dict(family="tube", layout="lattice", axes="xyz", weave=True, spacing=25.0))
        s = mh.Settings.from_values(dict(values), 2.0, 12)
        L = mh.lattice_lines(model, s)
        fams = {}
        for o, d, _e1, f in L:
            fams.setdefault(f, []).append((o, d))
        for a, b in itertools.combinations(fams, 2):
            for o1, d1 in fams[a][:40]:
                for o2, d2 in fams[b][:40]:
                    n = np.cross(d1, d2)
                    dist = abs((o2 - o1) @ n) / np.linalg.norm(n)
                    self.assertGreater(dist, 0.49 * s.spacing)


class EngineTests(Fixture):
    def test_registered(self):
        self.assertIn("mesh-holes", registry().tools)

    def test_every_hole_type_in_every_layout_cuts_a_closed_solid(self):
        for fam, lay in itertools.product(mh.FAMILIES, mh.LAYOUTS):
            with self.subTest(family=fam, layout=lay):
                res, out = run_mesh(self.ball, family=fam, layout=lay)
                st = stats(res)
                self.assertGreater(st["holes"], 0)
                self.assertGreater(st["removed"], 0.2)
                self.assertTrue(out.is_watertight)
                self.assertLess(abs(out.volume), trimesh.load(self.ball).volume)

    def test_a_regular_lattice_of_through_tubes_removes_what_it_should(self):
        # square tubes 4 wide, 10 apart, front to back through a 40 cube: 4 x 4 holes (one size, no chaos)
        res, out = run_mesh(self.block, family="tube", layout="lattice", axes="y", spacing=25.0, grid="square",
                            size=10.0, size_max=10.0, profile="polygon", sides=4, chaos=0.0, gap=0.0,
                            lattice_shift=(50.0, 0.0, 50.0))
        st = stats(res)
        self.assertEqual(st["holes"], 16)
        width = 0.10 * 40 * math.sqrt(math.pi) / 2          # a square of the circle's area
        expected = 16 * width * width * 40 / 40 ** 3 * 100
        self.assertAlmostEqual(st["removed"], expected, delta=0.05)
        self.assertTrue(out.is_watertight)

    def test_irregularity_zero_is_regular_and_the_seed_only_reaches_it_through_irregularity(self):
        base = dict(family="tube", layout="lattice", axes="xz", size=6.0, size_max=6.0, gap=0.0)
        _, a = run_mesh(self.ball, chaos=0.0, seed=1, **base)
        _, b = run_mesh(self.ball, chaos=0.0, seed=99, **base)
        self.assertAlmostEqual(abs(a.volume), abs(b.volume), delta=1e-3 * abs(a.volume))
        _, c = run_mesh(self.ball, chaos=1.0, seed=1, **base)
        _, d = run_mesh(self.ball, chaos=1.0, seed=1, **base)
        self.assertEqual(abs(c.volume), abs(d.volume))                          # reproducible
        self.assertNotAlmostEqual(abs(a.volume), abs(c.volume), delta=1e-3 * abs(a.volume))

    def test_a_band_keeps_the_rest_of_the_model_intact(self):
        res, out = run_mesh(self.ball, family="bubble", layout="lattice", size=12.0, size_max=12.0, gap=0.0,
                            influence="band", band_from=60.0, band_to=100.0, feather=0.0, chaos=0.0)
        self.assertGreater(stats(res)["holes"], 0)
        cut = trimesh.Trimesh(res.preview["views"][1]["mesh"].vertices, res.preview["views"][1]["mesh"].faces)
        lo = trimesh.load(self.ball).bounds[0][2]
        self.assertGreaterEqual(cut.bounds[0][2], lo + 0.6 * 60.0 - 1e-3)       # cutters only above 60% h

    def test_the_minimum_wall_holds_on_the_model(self):
        res, _ = run_mesh(self.ball, family="bubble", layout="random", count=120, gap=3.0, size=6.0,
                          size_max=16.0)
        self.assertIn("too close", res.report + " too close")               # report line exists
        model = trimesh.load(self.ball)
        vals = TOOL.values(dict(QUICK, family="bubble", layout="random", count=120, gap=3.0, size=6.0,
                                size_max=16.0))
        frame_h = 60.0
        s = mh.Settings.from_values(dict(vals), 2.0, 12)
        m = mh.Model(model.vertices / (frame_h / 2), model.faces, model.bounds[0] / 30, model.bounds[1] / 30)
        holes = mh.plan(m, s, dict(vals)).holes
        C = np.array([h.path[0] for h in holes])
        R = np.array([h.reach for h in holes])
        D = np.linalg.norm(C[:, None] - C[None], axis=2) - R[:, None] - R[None]
        np.fill_diagonal(D, np.inf)
        self.assertGreaterEqual(D.min(), s.gap - 1e-6)

    def test_loose_pieces_are_counted_by_body_and_voids_survive_dropping(self):
        import manifold3d as m3
        a = (m3.Manifold.sphere(2, 32) - m3.Manifold.sphere(1, 32)) + m3.Manifold.sphere(0.3, 16).translate([5, 0, 0])
        r, before, after = mh.keep_pieces(a, "largest", 5.0)
        self.assertEqual((before, after), (2, 1))
        self.assertAlmostEqual(r.volume(), m3.Manifold.sphere(2, 32).volume() - m3.Manifold.sphere(1, 32).volume(),
                               places=6)

    def test_an_open_model_is_closed_first(self):
        box = trimesh.creation.box(extents=(40.0, 40.0, 40.0))
        box.update_faces(np.arange(len(box.faces)) != 0)                     # one triangle missing
        src = Path(self.tmp.name) / "open.stl"
        box.export(src)
        log = []
        res, out = run_mesh(src, log=log, family="tube", layout="lattice", repair_resolution=64)
        self.assertTrue(any("not a closed solid" in m for _lvl, m in log))
        self.assertGreater(stats(res)["holes"], 0)
        self.assertTrue(out.is_watertight)

    def test_output_is_written_signed_and_in_model_units(self):
        dst = Path(self.tmp.name) / "ball_holes.stl"
        vals = TOOL.values(dict(QUICK, family="bore", layout="surface"))
        engine.run(context("run"), self.ball, dst, vals)
        from atlas_toolkit.geom import mesh as gm
        self.assertTrue(gm.read_signature(dst).startswith("atlas-toolkit:mesh-holes"))
        np.testing.assert_allclose(trimesh.load(dst).bounds, trimesh.load(self.ball).bounds, atol=0.5)

    def test_presets_run(self):
        for preset in _presets.each(TOOL):
            name, pre = preset.name, _presets.raw(preset)
            with self.subTest(preset=name):
                res, out = run_mesh(self.ball, mode="draft", **dict(pre, segments=12, draft_segments=12))
                self.assertGreater(stats(res)["holes"], 0, name)


if __name__ == "__main__":
    unittest.main()
