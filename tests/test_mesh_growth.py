"""Reaction-Diffusion Growth: the surface operator, a bounded simulation, separate pieces, the wavelength across
mesh densities, stages as moments of one run, planted waves, value-carrying refinement, the relief, published stage
sets and a workflow step."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core import samples
from atlas_toolkit.core import workflow as W
from atlas_toolkit.core.io import output_path
from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom import stages
from atlas_toolkit.tools.mesh_growth import TOOL, engine
from atlas_toolkit.tools.mesh_growth import mesh_growth as mg


def context(mode="run"):
    return JobContext(lambda *_: None, lambda: False, mode)


def grid(n, size=1.0):
    """A flat n x n vertex grid of size x size in the XY plane."""
    x = np.linspace(0.0, size, n)
    X, Y = np.meshgrid(x, x, indexing="ij")
    V = np.column_stack([X.ravel(), Y.ravel(), np.zeros(n * n)])
    i = np.arange(n - 1)
    a = (i[:, None] * n + i[None, :]).ravel()
    F = np.vstack([np.column_stack([a, a + n, a + 1]), np.column_stack([a + 1, a + n, a + n + 1])])
    return V, F


def operator(V, F):
    E, w, A = mg.cotan_weights(V, F)
    return mg.Operator.build(len(V), E, w, A), A


def grow(V, F, lam, steps, capture=None, pattern="coral", origin="scattered", density=30.0, seed=1):
    op, A = operator(V, F)
    chem = mg.chemistry(len(V), pattern, lam)
    start = mg.seed_state(V, A, lam, density, seed, origin, steps=steps)
    return mg.simulate(op, chem, start, steps, capture or [steps]), A


def dominant_wavelength(v, n, size):
    img = v.reshape(n, n) - v.mean()
    P = np.abs(np.fft.fft2(img * np.outer(np.hanning(n), np.hanning(n)))) ** 2
    f = np.fft.fftfreq(n, d=size / (n - 1))
    r = np.sqrt(f[:, None] ** 2 + f[None, :] ** 2)
    k = np.round(r / (f[1])).astype(int)
    pw = np.bincount(k.ravel(), P.ravel())
    peak = np.argmax(pw[2:n // 2]) + 2
    return 1.0 / (peak * f[1])


class OperatorTests(unittest.TestCase):
    def test_weights_are_nonnegative_and_areas_sum_to_the_surface(self):
        m = trimesh.creation.icosphere(subdivisions=3)
        E, w, A = mg.cotan_weights(m.vertices, m.faces)
        self.assertTrue((w >= 0).all())
        self.assertAlmostEqual(A.sum(), m.area, places=6)
        self.assertEqual(len(E), len(m.edges_unique))

    def test_diffusion_keeps_a_maximum_principle_even_on_slivers(self):
        rng = np.random.default_rng(3)
        V = rng.uniform(size=(300, 3)) * [1, 1, 1e-4]           # nearly flat: slivers everywhere
        F = rng.integers(0, 300, size=(600, 3))
        F = F[(F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])]
        op, _ = operator(V, F)
        x = rng.uniform(size=300).astype(np.float32)
        y = op.diffuse(x, np.float32(1e3), sweeps=5)
        self.assertGreaterEqual(y.min(), x.min() - 1e-6)
        self.assertLessEqual(y.max(), x.max() + 1e-6)

    def test_refinement_carries_values_linearly(self):
        m = trimesh.creation.box(extents=(2, 1, 1))
        vals = np.column_stack([m.vertices @ [1.0, 2.0, -3.0], m.vertices[:, 0] ** 0])
        V, F, out, passes = mg.refine_with(m.vertices, m.faces, vals, 0.3, 1_000_000)
        self.assertGreater(passes, 0)
        self.assertGreater(len(F), len(m.faces))
        np.testing.assert_allclose(out[:, 0], V @ [1.0, 2.0, -3.0], atol=1e-9)
        np.testing.assert_allclose(out[:, 1], 1.0, atol=1e-12)

    def test_transfer_reproduces_values_at_the_same_points(self):
        m = trimesh.creation.icosphere(subdivisions=2)
        vals = np.column_stack([m.vertices[:, 2], -m.vertices[:, 0]])
        np.testing.assert_allclose(mg.transfer(m.vertices, vals, m.vertices, 0.1), vals, atol=1e-6)


class SimulationTests(unittest.TestCase):
    def test_concentrations_stay_finite_and_bounded(self):
        m = trimesh.creation.icosphere(subdivisions=4)
        out, _ = grow(m.vertices, m.faces, 0.35, 1500, pattern="cells", density=60)
        v = out[1500]
        self.assertTrue(np.isfinite(v).all())
        self.assertGreaterEqual(v.min(), 0.0)
        self.assertLessEqual(v.max(), 1.0)
        self.assertGreater(v.max(), 0.15)                       # something grew

    def test_separate_pieces_exchange_nothing(self):
        a = trimesh.creation.icosphere(subdivisions=3)
        b = a.copy()
        b.apply_translation([5.0, 0, 0])
        both = trimesh.util.concatenate([a, b])
        V, F = both.vertices, both.faces
        op, A = operator(V, F)
        chem = mg.chemistry(len(V), "coral", 0.4)
        allowed = (V[:, 0] < 2.5).astype(float)                # spots only on the first sphere
        start = mg.seed_state(V, A, 0.4, 60, 2, allowed=allowed)
        start.v[V[:, 0] > 2.5] = 0.0
        v = mg.simulate(op, chem, start, 800, [800])[800]
        self.assertGreater(v[V[:, 0] < 2.5].max(), 0.15)
        self.assertEqual(float(v[V[:, 0] > 2.5].max()), 0.0)

    def test_wavelength_holds_across_mesh_densities(self):
        lam, size = 0.12, 1.2
        got = []
        for n in (61, 91):                                      # ~6 and ~9 samples per wavelength
            V, F = grid(n, size)
            out, _ = grow(V, F, lam, 6000, pattern="fingerprint", density=60)
            got.append(dominant_wavelength(out[6000], n, size))
        for g in got:
            self.assertAlmostEqual(g / lam, 1.0, delta=0.3)
        self.assertAlmostEqual(got[0] / got[1], 1.0, delta=0.2)

    def test_stages_are_moments_of_one_run(self):
        m = trimesh.creation.icosphere(subdivisions=3)
        many, _ = grow(m.vertices, m.faces, 0.4, 600, [0, 200, 600])
        one, _ = grow(m.vertices, m.faces, 0.4, 200, [200])
        np.testing.assert_array_equal(many[200], one[200])
        self.assertEqual(sorted(many), [0, 200, 600])

    def test_a_wave_from_the_crown_plants_the_top_first(self):
        m = trimesh.creation.icosphere(subdivisions=4)
        V = m.vertices
        op, A = operator(V, m.faces)
        start = mg.seed_state(V, A, 0.3, 60, 4, "crown", steps=1000)
        self.assertGreater(len(start.plants), 0)
        early = [V[i, 2].mean() for t, i in start.plants.items() if t < 200]
        late = [V[i, 2].mean() for t, i in start.plants.items() if t > 400]
        self.assertGreater(np.mean(early), np.mean(late))
        self.assertLessEqual(max(start.plants), int(mg.SPREAD * 1000))
        bare = mg.seed_state(V, A, 0.3, 60, 4, "scattered", steps=1000)
        self.assertEqual(bare.plants, {})


class ReliefTests(unittest.TestCase):
    def test_shape_is_monotone_and_ends_at_0_and_1(self):
        v = np.linspace(-0.1, 0.6, 200)
        for sharp in (0.0, 0.5, 1.0):
            for bal in (-0.8, 0.0, 0.8):
                t = mg.shape(v, 0.0, 0.5, sharp, bal)
                self.assertTrue((np.diff(t) >= -1e-12).all())
                self.assertAlmostEqual(t[0], 0.0)
                self.assertAlmostEqual(t[-1], 1.0)
        thin, fat = mg.shape(v, 0, 0.5, 0.3, -0.5), mg.shape(v, 0, 0.5, 0.3, 0.5)
        self.assertLess((thin > 0.5).sum(), (fat > 0.5).sum())

    def test_relief_modes(self):
        t = np.array([0.0, 0.5, 1.0])
        np.testing.assert_allclose(mg.relief_offsets(t, "raised"), [0, 0.5, 1])
        np.testing.assert_allclose(mg.relief_offsets(t, "engraved"), [0, -0.5, -1])
        np.testing.assert_allclose(mg.relief_offsets(t, "both"), [-0.5, 0, 0.5])

    def test_rim_fade_is_zero_on_open_edges(self):
        V, F = grid(11)
        f = mg.rim_fade(V, F, 0.25)
        rim = (V[:, 0] == 0) | (V[:, 1] == 0) | (V[:, 0] == 1) | (V[:, 1] == 1)
        np.testing.assert_allclose(f[rim], 0.0)
        self.assertAlmostEqual(f[60], 1.0)                     # the centre
        closed = trimesh.creation.icosphere(subdivisions=1)
        np.testing.assert_allclose(mg.rim_fade(closed.vertices, closed.faces, 0.3), 1.0)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_growth_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.src = self.folder / "pillar.stl"
        trimesh.creation.capsule(height=60.0, radius=15.0, count=[16, 16]).export(self.src)
        self.quick = dict(wavelength=12.0, growth=1.5, detail=5.0, relief_detail=7.0, density=60.0)

    def test_zero_depth_keeps_the_surface(self):
        vals = TOOL.values(dict(self.quick, depth=0.0))
        res = engine.run(context(), self.src, None, vals)
        out = res.preview["views"][-1]["mesh"]
        src = trimesh.load(self.src)
        _, dist, _ = trimesh.proximity.closest_point(src, np.asarray(out.vertices, float))
        self.assertLess(dist.max(), 1e-3 * src.extents.max())

    def test_stage_set_is_published_signed_and_grows(self):
        vals = TOOL.values(dict(self.quick, stages=3, up_axis="+y", front="+z", depth=4.0))
        dst = output_path(TOOL, self.src, vals)
        res = engine.run(context(), self.src, dst, vals)
        self.assertEqual(len(res.outputs), 4)
        man = json.loads((Path(res.outputs[1]).parent / stages.MANIFEST).read_text())
        self.assertEqual(man["frame"]["up"], "+y")
        self.assertEqual([s["step"] for s in man["stages"]], res.data["steps"])
        self.assertEqual(man["stages"][0]["step"], 0)
        for o in res.outputs:
            self.assertIn(b"atlas-toolkit:mesh-growth", Path(o).read_bytes()[:80])
        src = trimesh.load(self.src)
        first = trimesh.load(res.outputs[1], process=False)
        last = trimesh.load(res.outputs[-1], process=False)
        _, d0, _ = trimesh.proximity.closest_point(src, first.vertices)
        _, d1, _ = trimesh.proximity.closest_point(src, last.vertices)
        self.assertLess(d0.max(), 1e-3 * 60)                    # stage 0 is the source
        self.assertGreater(d1.max(), 0.02 * 90 * 0.5)           # the last one carries relief
        self.assertIn("pattern covers", res.report)

    def test_draft_and_refusal(self):
        draft = engine.run(context("draft"), self.src, None, TOOL.values(self.quick))
        self.assertIn("draft", draft.report)
        with self.assertRaises(ValueError):
            engine.run(context(), self.src, None, TOOL.values(dict(self.quick, wavelength=1.0, detail=14.0)))

    def test_presets_are_valid_and_spec_defaults_are_coral_ridges(self):
        for preset in _presets.each(TOOL):
            vals = _presets.run_values(TOOL, preset)
            self.assertIn(vals.pattern, mg.PATTERNS, preset.name)
        d = TOOL.values()
        for k, x in _presets.raw(_presets.preset(TOOL, "Coral Ridges")).items():
            self.assertEqual(d[k], TOOL.param(k).validate(x), k)


class WorkflowStepTests(unittest.TestCase):
    def test_growth_then_contours_in_a_workflow(self):
        tmp = Path(tempfile.mkdtemp(prefix="atlas_growth_wf_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        reg = registry()
        (tmp / "in").mkdir()
        src_mesh = samples.mesh(tmp / "in")
        out_dir = tmp / "out"
        out_dir.mkdir()
        wf = W.Workflow(name="Growth then contours")
        src = wf.add(W.input_node("mesh"))
        growth = wf.add(W.tool_node(reg.get("mesh-growth"), values=dict(TOOL.test_values, stages=1)))
        cl = reg.get("contour-lines")
        lines = wf.add(W.tool_node(cl, values=dict(cl.test_values)))
        out = wf.add(W.save_node(where="folder", folder=str(out_dir)))
        wf.connect(src.id, growth.id)
        wf.connect(growth.id, lines.id)
        wf.connect(lines.id, out.id)
        res = W.run_workflow(wf, src_mesh, lambda *a: None, lambda: False, reg=reg,
                             cache=W.WorkflowCache(tmp / "cache"), tool_cache=str(tmp / "toolcache"))
        self.assertEqual(len(res.outputs), 1)
        final = Path(res.outputs[0])
        self.assertTrue(final.name.endswith(".svg"))
        self.assertIn("_growth", final.name)


if __name__ == "__main__":
    unittest.main()
