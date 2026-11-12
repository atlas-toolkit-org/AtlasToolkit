"""Rods: every kind in every layout both ways, reach and extension, hollow pipes (open and plugged), spikes, cable
droop and bends, spacing outside the model only, Acts on, the rods-alone file, and clean seams."""
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
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_holes import mesh_holes as mh
from atlas_toolkit.tools.mesh_rods import TOOL, engine
from atlas_toolkit.tools.mesh_rods import mesh_rods as mr

QUICK = dict(segments=12, draft_segments=12, count=20, size=4.0, size_max=6.0, spacing=25.0, spokes=6,
             chaos=0.0, gap=0.0, save_tubes=False)


def context(mode="analyse"):
    return JobContext(lambda k, a: None, lambda: False, mode)


def run(src, mode="analyse", **values):
    """(result, output mesh, rods mesh or None) with QUICK settings and the given ones."""
    res = engine.run(context(mode), src, None, TOOL.values(dict(QUICK, **values)))
    views = {v["title"]: v["mesh"] for v in res.preview["views"]}
    out = trimesh.Trimesh(views["Output"].vertices, views["Output"].faces, process=False)
    rods = views.get("Rods")
    return res, out, (trimesh.Trimesh(rods.vertices, rods.faces, process=False) if rods else None)


def stats(res):
    return {s.key: s.value for s in res.stats}


def bad_edges(m) -> int:
    t = trimesh.Trimesh(m.vertices, m.faces)            # merged, as a saved STL is
    _u, c = np.unique(t.edges_sorted, axis=0, return_counts=True)
    return int((c != 2).sum())


def canonical_model(mesh):
    """The plan-level view of a mesh centred on the origin, 2 tall (the tools' canonical frame)."""
    V = np.asarray(mesh.vertices, float)
    lo, hi = V.min(0), V.max(0)
    k = 2.0 / (hi[2] - lo[2])
    V = (V - (lo + hi) / 2) * k
    return mh.Model(V, np.asarray(mesh.faces), V.min(0), V.max(0))


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


class EngineTests(Fixture):
    def test_registered(self):
        self.assertIn("mesh-rods", registry().tools)

    def test_every_kind_in_every_layout_both_ways_adds_a_closed_solid(self):
        vol = trimesh.load(self.ball).volume
        for fam, ext, lay in itertools.product(mr.FAMILIES, mr.EXTENDS, mr.LAYOUTS):
            with self.subTest(family=fam, extend=ext, layout=lay):
                res, out, rods = run(self.ball, family=fam, extend=ext, layout=lay, collars=(fam == "rod"))
                self.assertGreater(stats(res)["rods"], 0)
                self.assertTrue(out.is_watertight)
                self.assertIsNotNone(rods)
                if fam != "pipe":                        # open bores also cut the model
                    self.assertGreater(abs(out.volume), vol)

    def test_through_rods_stick_out_both_sides_by_reach(self):
        # one width, one reach, flat ends: front-to-back lattice rods through a 40 cube stick out 12% h = 4.8 a side
        # (shifted half a step so no line runs along the cube's edges)
        _, out, _ = run(self.block, family="rod", extend="through", layout="lattice", axes="y", tip="flat",
                        size=5.0, size_max=5.0, reach=12.0, reach_max=12.0, lattice_shift=(50.0, 0.0, 50.0))
        self.assertAlmostEqual(out.bounds[0][1], -20.0 - 4.8, delta=0.05)
        self.assertAlmostEqual(out.bounds[1][1], 20.0 + 4.8, delta=0.05)
        self.assertAlmostEqual(out.extents[0], 40.0, delta=1e-3)          # nothing sideways

    def test_outward_rods_grow_one_side_by_reach(self):
        _, out, _ = run(self.block, family="rod", extend="outward", layout="lattice", axes="y", both_sides=False,
                        tip="flat", size=5.0, size_max=5.0, reach=12.0, reach_max=12.0,
                        lattice_shift=(50.0, 0.0, 50.0))
        self.assertAlmostEqual(out.bounds[1][1], 20.0 + 4.8, delta=0.05)
        self.assertAlmostEqual(out.bounds[0][1], -20.0, delta=1e-3)

    def test_open_pipes_see_through_and_plugged_pipes_do_not(self):
        base = dict(family="pipe", extend="through", layout="lattice", axes="y", size=12.0, size_max=12.0,
                    wall=0.3, reach=10.0, reach_max=10.0, tip="flat", lattice_shift=(0.0, 0.0, 0.0))
        for bore, hit in (("open", False), ("plugged", True)):
            _, out, _ = run(self.block, bore=bore, **base)
            self.assertEqual(bad_edges(out), 0, bore)
            # a ray down the axis of the centre pipe (the lattice is centred on the model): an open bore lets it
            # through untouched, a plugged one stops it at the cube's face
            loc, _, _ = out.ray.intersects_location([[0.0, -100.0, 0.0]], [[0.0, 1.0, 0.0]])
            self.assertEqual(bool(len(loc)), hit, bore)
            if hit:
                self.assertAlmostEqual(min(p[1] for p in loc), -20.0, delta=0.05)

    def test_spikes_narrow_to_a_point(self):
        model = canonical_model(trimesh.load(self.ball))
        vals = TOOL.values(dict(QUICK, family="spike", extend="outward", layout="surface"))
        s = mr.Settings.from_values(dict(vals), 2.0, 12)
        rods = mr.plan(model, s, dict(vals)).rods
        self.assertTrue(rods)
        for r in rods:
            self.assertLess(r.scale[-1], 0.05)
            self.assertAlmostEqual(r.scale[0], 1.0)

    def test_cables_droop_and_never_fold(self):
        rng = np.random.default_rng(1)
        for droop, chaos, wav, swell in ((1.0, 0.0, 0.0, 0.0), (0.4, 1.0, 12.0, 1.0), (0.0, 1.0, 40.0, 0.5)):
            vals = TOOL.values(dict(QUICK, family="cable", droop=droop, chaos=chaos, waviness=wav, swell=swell))
            s = mr.Settings.from_values(dict(vals), 2.0, 12)
            radius = 0.02
            P, sw = mr.cable_branch(np.zeros(3), np.array([1.0, 0, 0]), 1.5, radius, s, rng, 0.3)
            if droop == 1.0 and chaos == 0.0:
                self.assertLess(P[-1][2], -0.3)                         # it hangs
                self.assertGreater(P[-1][0], 0.0)
            T = np.diff(P, axis=0)
            T /= np.linalg.norm(T, axis=1, keepdims=True)
            ang = np.arccos(np.clip((T[:-1] * T[1:]).sum(1), -1, 1))
            ds = np.linalg.norm(np.diff(P, axis=0), axis=1)[1:]
            bend = ds / np.maximum(ang, 1e-12)
            widest = radius * (1 + 0.6 * swell) * 1.3
            self.assertGreaterEqual(bend.min(), 1.6 * widest * 0.999)

    def test_spacing_counts_only_what_is_outside(self):
        # radial through rods all cross at the axis inside the ball: a gap must not remove them
        res, _, _ = run(self.ball, family="rod", extend="through", layout="radial", spokes=6, spacing=30.0,
                        size=4.0, size_max=4.0, gap=2.0)
        st = stats(res)
        res0, _, _ = run(self.ball, family="rod", extend="through", layout="radial", spokes=6, spacing=30.0,
                         size=4.0, size_max=4.0, gap=0.0)
        self.assertEqual(st["rods"], stats(res0)["rods"])

    def test_n_sided_rods_keep_their_corners(self):
        model = canonical_model(trimesh.load(self.ball))
        vals = TOOL.values(dict(QUICK, family="rod", profile="polygon", sides=5))
        s = mr.Settings.from_values(dict(vals), 2.0, 12)
        for r in mr.plan(model, s, dict(vals)).rods:
            self.assertEqual(len(r.shape), 5)

    def test_a_band_only_roots_rods_inside_it(self):
        model = canonical_model(trimesh.load(self.ball))
        vals = dict(TOOL.values(dict(QUICK, family="rod", layout="surface", count=200, influence="band",
                                     band_from=70.0, band_to=100.0, feather=0.0)))
        s = mr.Settings.from_values(vals, 2.0, 12)
        rods = mr.plan(model, s, vals).rods
        self.assertTrue(rods)
        z0 = model.lo[2] + 0.7 * model.height
        for r in rods:
            self.assertGreaterEqual(min(p[2] for p, _ in r.crossings), z0 - 1e-6)

    def test_rods_alone_are_saved_beside_the_result_signed(self):
        dst = Path(self.tmp.name) / "ball_rods.stl"
        engine.run(context("run"), self.ball, dst, TOOL.values(dict(QUICK, save_tubes=True)))
        tubes = engine.tubes_path(dst)
        self.assertTrue(dst.exists() and tubes.exists())
        for f in (dst, tubes):
            self.assertTrue(gm.read_signature(f).startswith("atlas-toolkit:mesh-rods"))
        self.assertLess(abs(trimesh.load(tubes).volume), 0.5 * abs(trimesh.load(dst).volume))

    def test_long_sweeps_have_clean_seams(self):
        # Holes' piecewise sweep, shared: overlapping pieces, seam rings inside the neighbour
        t = np.linspace(0, 6 * math.pi, 200)
        P = np.stack([np.cos(t), np.sin(t), 0.15 * t], 1)
        m = mh.sweep_chunks(mh.profile("polygon", sides=6), P, np.full(len(P), 0.2), np.zeros(len(P)))
        Vo, Fo = mh.from_manifold(m)
        self.assertEqual(bad_edges(trimesh.Trimesh(Vo, Fo)), 0)

    def test_presets_run(self):
        for preset in _presets.each(TOOL):
            name, pre = preset.name, _presets.raw(preset)
            with self.subTest(preset=name):
                res, out, _ = run(self.ball, mode="draft", **dict(pre, segments=12, draft_segments=12,
                                                                  save_tubes=False))
                self.assertGreater(stats(res)["rods"], 0, name)


if __name__ == "__main__":
    unittest.main()
