"""Compress: shapes (gauges, projections, volumes), the press (plates, radial, moulds), the solver (volume kept or
given up, folds stay out of the press, seeds), the detail transfer, morphing, the engine and every preset."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.io import output_path
from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_compress import TOOL, engine
from atlas_toolkit.tools.mesh_compress import mesh_compress as mc


def context(mode="run"):
    return JobContext(lambda *_: None, lambda: False, mode)


def sphere(level=3):
    m = trimesh.creation.icosphere(level)
    return np.asarray(m.vertices, float), np.asarray(m.faces, np.int64)


class ShapeTests(unittest.TestCase):
    def test_volumes_are_exact(self):
        expect = {"cube": 8.0, "sphere": 4 * math.pi / 3, "cylinder": 2 * math.pi, "cone": 2 * math.pi / 3,
                  "pyramid": 8.0 / 3.0, "octahedron": 4.0 / 3.0 * math.sqrt(3) ** 3}   # |x|+|y|+|z| <= sqrt 3
        for name, v in expect.items():
            self.assertAlmostEqual(mc.make_shape(name).volume(), v, places=6, msg=name)
        rng = np.random.default_rng(0)
        U = rng.uniform(-1.6, 1.6, size=(400_000, 3))
        for name in ("icosahedron", "dodecahedron", "prism"):
            s = mc.make_shape(name, 5)
            mc_vol = float((s.gauge(U) <= 1).mean()) * 3.2 ** 3
            self.assertAlmostEqual(s.volume(), mc_vol, delta=0.05 * mc_vol, msg=name)

    def test_projection_lands_inside_and_on_the_boundary(self):
        rng = np.random.default_rng(1)
        U = rng.normal(size=(500, 3)) * 2.0
        for name in mc.SHAPES:
            s = mc.make_shape(name, 4)
            P = U
            for _ in range(6):                                  # polytopes converge over repeated sweeps
                P = s.project(P)
            g = s.gauge(P)
            self.assertLessEqual(g.max(), 1.0 + 1e-3, name)
            outside = s.gauge(U) > 1.0
            np.testing.assert_allclose(g[outside], 1.0, atol=2e-3, err_msg=name)
            inside = ~outside
            np.testing.assert_allclose(P[inside], U[inside], err_msg=name)

    def test_solids_stand_on_a_face(self):
        for name in ("cube", "icosahedron", "dodecahedron", "pyramid", "prism"):
            self.assertAlmostEqual(mc.make_shape(name, 3).bottom(), -1.0, places=6, msg=name)

    def test_mould_encloses_the_model_at_its_start_scale(self):
        V, F = sphere()
        V = V * [1.0, 0.6, 1.4]
        for name in mc.SHAPES:
            m = mc.mould(name, "up", (1.2, 1.0, 0.8), 15.0, 5)
            s0 = m.enclosing_scale(V)
            self.assertAlmostEqual(m.shape.gauge(m.to_unit(V, s0)).max(), 1.0, places=9)
            np.testing.assert_allclose(m.from_unit(m.to_unit(V, s0), s0), V, atol=1e-12)


class SolverTests(unittest.TestCase):
    def test_edges_and_bending_pairs(self):
        V, F = sphere(2)
        E, B = mc.edges_of(F)
        self.assertEqual(len(E), len(F) * 3 // 2)
        self.assertEqual(len(B), len(E))                         # closed: every edge has two triangles
        self.assertTrue(mc.is_closed(F))
        self.assertFalse(mc.is_closed(F[1:]))

    def test_volume_gradient_matches_finite_differences(self):
        V, F = sphere(2)
        G = mc.volume_gradient(V, F)
        h = 1e-6
        for i in (0, 7, 40):
            W = V.copy()
            W[i, 1] += h
            self.assertAlmostEqual((mc.volume(W, F, 0.0) - mc.volume(V, F, 0.0)) / h, G[i, 1], places=5)

    def test_clay_keeps_volume_and_bulges_foil_gives_it_up(self):
        V, F = sphere(3)
        press = mc.plates(V, "up", 0.4)
        clay = mc.solve(V, F, press, mc.Material(0.0, 0.8, 0.5, 0.8, 0.0), 24, 10)
        foil = mc.solve(V, F, press, mc.Material(1.0, 0.0, 0.2, 0.5, 0.0), 24, 10)
        self.assertAlmostEqual(clay.volume1 / clay.volume0, 1.0, delta=0.05)
        self.assertLess(foil.volume1 / foil.volume0, 0.85)
        ext = np.ptp(clay.X, axis=0)
        self.assertLessEqual(ext[2], 1.2 + 1e-6)                          # the plates closed 40%
        self.assertGreater(ext[0], 2.05)                                  # and it barrelled out

    def test_nothing_ends_outside_the_press(self):
        V, F = sphere(3)
        for press in (mc.plates(V, "front", 0.3, tilt=12.0, floor=True), mc.radial(V, "side", 0.3),
                      mc.radial(V, None, 0.25),
                      mc.into(V, mc.mould("octahedron"), 0.9, 1.0, mc.volume(V, F))):
            out = mc.solve(V, F, press, mc.Material(), 12, 6, collide=False)
            Y, hit = press.contact(out.X, 1.0)
            self.assertLess(np.abs(Y - out.X).max(), 1e-3, press.kind)

    def test_seed_repeats(self):
        V, F = sphere(2)
        press = mc.plates(V, "up", 0.3)
        a = mc.solve(V, F, press, mc.Material(seed=4), 8, 4).X
        b = mc.solve(V, F, press, mc.Material(seed=4), 8, 4).X
        c = mc.solve(V, F, press, mc.Material(seed=5), 8, 4).X
        np.testing.assert_array_equal(a, b)
        self.assertGreater(np.abs(a - c).max(), 1e-4)

    def test_transfer_carries_rigid_motion_exactly(self):
        V, F = sphere(2)
        E, _ = mc.edges_of(F)
        t = math.radians(30)
        R = np.array([[math.cos(t), -math.sin(t), 0], [math.sin(t), math.cos(t), 0], [0, 0, 1]])
        D, DF = sphere(4)
        D = D * 0.98
        X = mc.transfer(D, mc.vertex_normals(D, DF), V, V @ R.T + [0.3, 0, 0], mc.vertex_normals(V, F), E)
        np.testing.assert_allclose(X, D @ R.T + [0.3, 0, 0], atol=1e-9)


class MorphTests(unittest.TestCase):
    def test_full_morph_without_relief_is_the_shape(self):
        V, F = sphere(3)
        m = mc.mould("cube", centre=(0, 0, 0))
        X = mc.morph(V, F, m, 0.8, 1.0, 0.0)
        np.testing.assert_allclose(m.shape.gauge(m.to_unit(X, 0.8)), 1.0, atol=1e-9)
        np.testing.assert_allclose(mc.morph(V, F, m, 0.8, 0.0, 0.5), V)


class EngineTests(unittest.TestCase):
    def setUp(self):
        registry()
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_compress_")
        self.addCleanup(self.tmp.cleanup)
        self.src = Path(self.tmp.name) / "bust.stl"
        trimesh.creation.capsule(height=40.0, radius=15.0, count=[24, 24]).export(self.src)

    def test_every_preset_runs_and_is_signed(self):
        quick = dict(TOOL.test_values)
        for preset in _presets.each(TOOL):
            name = preset.name
            vals = _presets.run_values(TOOL, preset, **quick)
            vals.update(quick)
            dst = output_path(TOOL, self.src, vals)
            res = engine.run(context(), self.src, dst, vals)
            self.assertTrue(dst.exists(), name)
            self.assertTrue(gm.read_signature(dst).startswith(TOOL.signature), name)
            out = gm.load_mesh(dst)
            self.assertTrue(np.isfinite(out.vertices).all(), name)
            # model units and place kept: within the input's neighbourhood
            src = gm.load_mesh(self.src)
            self.assertLess(np.abs(out.bounds - src.bounds).max(), 0.8 * src.extents.max(), name)
            self.assertIn("press", res.report, name)

    def test_plates_compress_by_the_strain(self):
        vals = TOOL.values(dict(TOOL.test_values, strain=30.0, up_axis="+z"))
        res = engine.run(context(), self.src, None, vals)
        strain = next(s for s in res.stats if s.key == "strain").value
        self.assertAlmostEqual(strain, 30.0, delta=1.0)

    def test_preview_writes_nothing(self):
        vals = TOOL.values(dict(TOOL.test_values))
        res = engine.run(context("preview"), self.src, None, vals)
        self.assertEqual(res.outputs, [])
        self.assertEqual(res.preview["views"][0]["kind"], "mesh")


if __name__ == "__main__":
    unittest.main()
