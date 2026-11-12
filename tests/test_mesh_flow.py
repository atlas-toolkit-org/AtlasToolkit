"""Flow Warp: the field, the integrator (identity, seeds, anchoring, convergence, shared stages), conforming edge
splitting, published stage sets and a workflow step."""
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
from atlas_toolkit.tools.mesh_flow import TOOL, engine
from atlas_toolkit.tools.mesh_flow import mesh_flow as mf


def context(mode="run"):
    return JobContext(lambda *_: None, lambda: False, mode)


def points(n=400, seed=1):
    return np.random.default_rng(seed).uniform(-1, 1, size=(n, 3)) * [0.5, 0.5, 1.0]


class FieldTests(unittest.TestCase):
    def test_current_is_divergence_free_and_scaled_to_its_travel(self):
        f = mf.build_field(current=0.3, swirl=0.8, turbulence=0.6, seed=4)
        X = points(300)
        e = 1e-5
        div = sum((f.velocity(X + e * np.eye(3)[a]) - f.velocity(X - e * np.eye(3)[a]))[:, a] / (2 * e)
                  for a in range(3))
        self.assertLess(np.abs(div).max(), 1e-5)
        rms = np.sqrt((f.velocity(points(20000, 2)) ** 2).sum(1).mean())
        self.assertAlmostEqual(rms, 0.3, delta=0.06)

    def test_seed_repeats_and_each_part_keeps_its_randomness(self):
        a = mf.build_field(current=0.3, seed=7, pull=0.2, streaks=0.5)
        b = mf.build_field(current=0.3, seed=7, pull=0.2, streaks=0.5)
        c = mf.build_field(current=0.3, seed=8, pull=0.2, streaks=0.5)
        X = points()
        np.testing.assert_array_equal(a.velocity(X), b.velocity(X))
        self.assertGreater(np.abs(a.velocity(X) - c.velocity(X)).max(), 1e-3)
        # turning the pull off leaves the current as it was
        d = mf.build_field(current=0.3, seed=7)
        np.testing.assert_allclose(d.velocity(X), a.velocity(X) - a.pull * (1 + a.streak_amp * np.sin(
            X @ a.Q.T + a.psi).sum(1))[:, None], atol=1e-12)

    def test_vortex_turns_about_its_axis(self):
        f = mf.build_field(current=0.0, spin=90.0, radius=0.3, axis="up", depth=100.0)
        X = points()
        v = f.velocity(X)
        np.testing.assert_allclose(v[:, 2], 0.0, atol=1e-12)                    # no motion along the axis
        np.testing.assert_allclose((v[:, :2] * X[:, :2]).sum(1), 0.0, atol=1e-12)   # tangential only
        p = np.array([[0.3, 0.0, 0.0]])
        (Y,) = mf.advect(f, p, np.ones(1), [1.0], step=0.05)
        ang = np.degrees(np.arctan2(Y[0, 1], Y[0, 0]))
        self.assertAlmostEqual(ang, 90.0, delta=3.0)                            # spin = turn at the core's edge
        self.assertAlmostEqual(np.linalg.norm(Y[0, :2]), 0.3, delta=1e-4)

    def test_streaks_vary_only_across_the_pull(self):
        f = mf.build_field(current=0.0, pull=0.2, direction="+x", streaks=1.0, swirl=0.5, seed=3)
        X = points()
        np.testing.assert_allclose(f.velocity(X), f.velocity(X + [0.37, 0, 0]), atol=1e-12)
        self.assertGreater(np.ptp(f.velocity(X)[:, 0]), 0.05)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.f = mf.build_field(current=0.3, swirl=0.7, turbulence=0.5, seed=2, spin=120, radius=0.3,
                                pull=0.1, streaks=0.4)
        self.X = points(500)

    def test_zero_time_or_weight_is_identity(self):
        (Y,) = mf.advect(self.f, self.X, np.ones(len(self.X)), [0.0])
        np.testing.assert_array_equal(Y, self.X)
        (Y,) = mf.advect(self.f, self.X, np.zeros(len(self.X)), [1.0])
        np.testing.assert_array_equal(Y, self.X)
        (Y,) = mf.advect(mf.build_field(current=0.0), self.X, np.ones(len(self.X)), [1.0])
        np.testing.assert_array_equal(Y, self.X)

    def test_anchored_points_stay_put(self):
        w = mf.anchor_weights(self.X, (-1, -1, -1), (1, 1, 1), 30.0, 20.0)
        self.assertTrue((w[self.X[:, 2] < -0.4] == 0).all())
        self.assertTrue((w[self.X[:, 2] > 0.0] == 1).all())
        (Y,) = mf.advect(self.f, self.X, w, [1.0])
        fixed = w == 0
        np.testing.assert_array_equal(Y[fixed], self.X[fixed])
        self.assertGreater(np.abs(Y[~fixed] - self.X[~fixed]).max(), 0.05)

    def test_tighter_steps_converge(self):
        w = np.ones(len(self.X))
        errs = []
        (ref,) = mf.advect(self.f, self.X, w, [1.0], step=0.01)
        for step in (0.4, 0.2, 0.1):
            (Y,) = mf.advect(self.f, self.X, w, [1.0], step=step)
            errs.append(np.abs(Y - ref).max())
        self.assertLess(errs[1], errs[0])
        self.assertLess(errs[2], errs[1])
        self.assertLess(errs[2], 1e-3)

    def test_stage_count_does_not_change_a_shared_stage(self):
        w = np.ones(len(self.X))
        three = mf.advect(self.f, self.X, w, stages.amounts(3, 1.2))
        five = mf.advect(self.f, self.X, w, stages.amounts(5, 1.2))
        np.testing.assert_array_equal(three[1], five[2])          # amount 0.6 in both
        np.testing.assert_array_equal(three[2], five[4])
        np.testing.assert_array_equal(three[0], self.X)

    def test_step_size_depends_on_the_field_only(self):
        h = mf.step_size(self.f, 0.2)
        self.assertGreater(h, 0)
        self.assertEqual(h, mf.step_size(self.f, 0.2))
        self.assertLess(mf.step_size(self.f, 0.1), h)


class RefineTests(unittest.TestCase):
    def test_splitting_is_conforming(self):
        m = trimesh.creation.icosphere(subdivisions=1)
        V = m.vertices.copy()
        V[0] *= 1.6                                                 # one long spike: only nearby edges split
        F = m.faces
        V2, F2, passes = mf.refine(V, F, 0.35, 10**6)
        self.assertGreater(passes, 0)
        self.assertGreater(len(F2), len(F))
        r = trimesh.Trimesh(V2, F2, process=False)
        r.merge_vertices()
        self.assertTrue(r.is_watertight)                            # no T-junctions, no cracks
        self.assertTrue(r.is_winding_consistent)
        self.assertAlmostEqual(r.volume, trimesh.Trimesh(V, F).volume, places=9)
        E = r.edges_unique_length
        self.assertLessEqual(E.max(), 0.35 + 1e-9)

    def test_budget_stops_splitting(self):
        m = trimesh.creation.icosphere(subdivisions=2)
        V, F, passes = mf.refine(m.vertices, m.faces, 0.01, len(m.faces) * 2)
        self.assertLessEqual(len(F), len(m.faces) * 2)


class EngineTests(unittest.TestCase):
    def setUp(self):
        registry()
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_flow_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.src = self.folder / "pillar.stl"
        trimesh.creation.capsule(height=60.0, radius=15.0, count=[16, 16]).export(self.src)

    def test_amount_zero_is_the_input(self):
        vals = TOOL.values(dict(amount=0.0, refine=False))
        res = engine.run(context(), self.src, None, vals)
        v = res.preview["views"][-1]["mesh"]
        mesh = gm.load_mesh(self.src)
        np.testing.assert_allclose(np.sort(v.vertices, axis=0), np.sort(mesh.vertices, axis=0), atol=1e-4)

    def test_stage_set_is_published_signed_and_anchored(self):
        vals = TOOL.values(dict(stages=3, refine=False, up_axis="+y", front="+z", anchor=30.0, anchor_blend=10.0,
                                travel=20.0))
        dst = output_path(TOOL, self.src, vals)
        res = engine.run(context(), self.src, dst, vals)
        self.assertEqual(len(res.outputs), 4)
        man = json.loads((Path(res.outputs[1]).parent / stages.MANIFEST).read_text())
        self.assertEqual(man["frame"]["up"], "+y")
        self.assertEqual([s["amount"] for s in man["stages"]], [0.0, 0.5, 1.0])
        for o in res.outputs:
            self.assertIn(b"atlas-toolkit:mesh-flow", Path(o).read_bytes()[:80])
        orig = trimesh.load(self.src, process=False)
        first = trimesh.load(res.outputs[1], process=False)
        last = trimesh.load(res.outputs[-1], process=False)
        np.testing.assert_allclose(first.bounds, orig.bounds, atol=1e-3)
        base = orig.vertices[:, 1] < orig.bounds[0][1] + 0.3 * orig.extents[1] - 1e-6   # up is Y
        np.testing.assert_allclose(last.vertices[base], orig.vertices[base], atol=1e-3)
        self.assertGreater(np.abs(last.vertices - orig.vertices).max(), 1.0)
        self.assertIn("stretched", res.report)

    def test_refinement_splits_a_coarse_model(self):
        vals = TOOL.values(dict(refine=True, edges_per_wave=4))
        res = engine.run(context(), self.src, None, vals)
        self.assertGreater(res.stat("faces").value, res.stat("faces").before)
        draft = engine.run(context("draft"), self.src, None, vals)
        self.assertIn("draft", draft.report)

    def test_presets_are_valid(self):
        for preset in _presets.each(TOOL):
            vals = _presets.run_values(TOOL, preset)
            self.assertEqual(vals.seed, _presets.raw(preset)["seed"], preset.name)


class WorkflowStepTests(unittest.TestCase):
    def test_flow_then_contours_in_a_workflow(self):
        tmp = Path(tempfile.mkdtemp(prefix="atlas_flow_wf_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        reg = registry()
        (tmp / "in").mkdir()
        src_mesh = samples.mesh(tmp / "in")
        out_dir = tmp / "out"
        out_dir.mkdir()
        wf = W.Workflow(name="Flow then contours")
        src = wf.add(W.input_node("mesh"))
        flow = wf.add(W.tool_node(reg.get("mesh-flow"), values={"refine": False, "travel": 10.0}))
        cl = reg.get("contour-lines")
        lines = wf.add(W.tool_node(cl, values=dict(cl.test_values)))
        out = wf.add(W.save_node(where="folder", folder=str(out_dir)))
        wf.connect(src.id, flow.id)
        wf.connect(flow.id, lines.id)
        wf.connect(lines.id, out.id)
        res = W.run_workflow(wf, src_mesh, lambda *a: None, lambda: False, reg=reg,
                             cache=W.WorkflowCache(tmp / "cache"), tool_cache=str(tmp / "toolcache"))
        self.assertEqual(len(res.outputs), 1)
        final = Path(res.outputs[0])
        self.assertTrue(final.name.endswith(".svg"))
        self.assertIn("_flow", final.name)


if __name__ == "__main__":
    unittest.main()
