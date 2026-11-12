"""Strata Sculpt: the stack, capped cuts (volume, holes, oblique cuts, separate parts), motions, influence, stages,
the shared cutting helper and a workflow step."""
import _isolate  # first: settings and caches stay in a temporary folder
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
from atlas_toolkit.geom import cutting, influence, stages
from atlas_toolkit.tools.mesh_strata import TOOL, engine
from atlas_toolkit.tools.mesh_strata import mesh_strata as ms

STILL = dict(separation=0.0, turn=0.0, wave=0.0, jitter_shift=0.0, jitter_spin=0.0, jitter_tilt=0.0)


def context(mode="run"):
    return JobContext(lambda *_: None, lambda: False, mode)


def sculpt(mesh, amount=1.0, **values):
    """Strata of a canonical mesh, as the engine does it (no frame)."""
    p = TOOL.values(dict(values))
    st = ms.stack(mesh.vertices, p.layers, p.direction, p.tilt, p.tilt_toward)
    lo, hi = mesh.bounds
    u = ms.layer_amounts(st, amount, p, lo, hi)
    return ms.sculpt(mesh, st, u, p, ms.draws(st.count, p.seed))


class CuttingTests(unittest.TestCase):
    def test_partition_is_one_piece_per_interval_and_keeps_the_volume(self):
        box = trimesh.creation.box(extents=(2, 2, 2))
        cuts = [-0.6, -0.2, 0.1, 0.5]
        pieces = cutting.partition(box, (0, 0, 1), cuts)
        self.assertEqual([i for i, _ in pieces], [0, 1, 2, 3, 4])
        self.assertTrue(all(m.is_watertight for _, m in pieces))
        self.assertAlmostEqual(sum(m.volume for _, m in pieces), box.volume, places=6)

    def test_planes_outside_the_mesh_are_skipped(self):
        box = trimesh.creation.box(extents=(2, 2, 2))
        pieces = cutting.partition(box, (0, 0, 1), [-5.0, 0.0, 5.0])
        self.assertEqual([i for i, _ in pieces], [1, 2])

    def test_ring_sections_keep_their_hole(self):
        torus = trimesh.creation.torus(1.0, 0.35, major_sections=48, minor_sections=24)
        below, above = cutting.split(torus, (0, 0, -0.1), (0, 0, 1), engine=cutting.best_engine())
        for m in (below, above):
            self.assertTrue(m.is_watertight)
            self.assertEqual(m.euler_number, 0)

    def test_grid_slicer_still_cuts_the_same(self):
        from atlas_toolkit.tools.cube_grid_slicer import cube_grid_slicer as cgs
        box = trimesh.creation.box(extents=(30, 30, 30))
        parts, cells = cgs.slice_grid(box, 3, 2.0)
        self.assertEqual(len(parts), 27)
        self.assertEqual(len(set(cells)), 27)
        self.assertAlmostEqual(sum(m.volume for m in parts), box.volume, places=4)


class StackTests(unittest.TestCase):
    def test_basis_is_orthonormal_and_right_handed(self):
        for d in ms.DIRECTIONS:
            for tilt, toward in ((0, 0), (35, 0), (60, 120)):
                n, e1, e2 = ms.basis(d, tilt, toward)
                M = np.stack([e1, e2, n])
                np.testing.assert_allclose(M @ M.T, np.eye(3), atol=1e-12)
                self.assertAlmostEqual(float(np.linalg.det(M)), 1.0, places=12)
        n, _, _ = ms.basis("up", 30, 0)
        self.assertAlmostEqual(float(n[2]), np.cos(np.radians(30)), places=12)

    def test_full_thickness_unmoved_slabs_rebuild_the_volume(self):
        sphere = trimesh.creation.icosphere(subdivisions=4, radius=1.0)
        out, info = sculpt(sphere, layers=9, retained=100.0, **STILL)
        self.assertEqual(info["layers"], 9)
        self.assertEqual(info["closed"], info["pieces"])
        self.assertAlmostEqual(out.volume, sphere.volume, delta=1e-6 * sphere.volume)
        np.testing.assert_allclose(out.bounds, sphere.bounds, atol=1e-9)

    def test_retained_thickness_keeps_that_share_of_a_prism(self):
        box = trimesh.creation.box(extents=(1, 1, 2))
        out, info = sculpt(box, layers=8, retained=40.0, **STILL)
        self.assertEqual(info["layers"], 8)
        self.assertAlmostEqual(out.volume, 0.4 * box.volume, places=6)

    def test_rings_keep_their_holes(self):
        torus = trimesh.creation.torus(1.0, 0.35, major_sections=48, minor_sections=24)   # axis along Z
        out, info = sculpt(torus, layers=4, retained=60.0, **STILL)
        slabs = out.split(only_watertight=False)
        self.assertEqual(len(slabs), 4)
        for s in slabs:
            self.assertTrue(s.is_watertight)
            self.assertEqual(s.euler_number, 0)            # a ring: genus 1, the hole is not capped over

    def test_oblique_cuts_through_separate_parts_stay_closed(self):
        a = trimesh.creation.icosphere(3, 0.6)
        b = a.copy()
        a.apply_translation((-1, 0, 0))
        b.apply_translation((1, 0, 0.3))
        two = trimesh.util.concatenate([a, b])
        out, info = sculpt(two, layers=7, retained=100.0, tilt=35.0, tilt_toward=40.0, **STILL)
        self.assertEqual(info["closed"], info["pieces"])
        self.assertAlmostEqual(out.volume, two.volume, delta=1e-6 * two.volume)

    def test_separation_lifts_from_the_anchor(self):
        box = trimesh.creation.box(extents=(1, 1, 2))
        st = ms.stack(box.vertices, 4)
        u = np.ones(4)
        D = ms.lifts(st, u, 1.0, "base")
        np.testing.assert_allclose(D, np.arange(4) * st.pitch)
        D = ms.lifts(st, u, 1.0, "centre")
        self.assertAlmostEqual(float(D[0] + D[-1]), 0.0)
        self.assertAlmostEqual(float(ms.lifts(st, u, 1.0, "top")[-1]), 0.0)

    def test_turn_keeps_the_anchor_layer_and_spins_the_far_end(self):
        box = trimesh.creation.box(extents=(1.0, 0.2, 2))
        out, _ = sculpt(box, layers=4, retained=50.0, **dict(STILL, turn=90.0))
        slabs = sorted(out.split(only_watertight=False), key=lambda m: m.bounds[0][2])
        e0, e3 = slabs[0].extents, slabs[-1].extents
        self.assertGreater(e0[0], 0.9)                     # the base is still wide along X
        self.assertGreater(e3[1], 0.9)                     # the top has turned 90°: wide along Y

    def test_dislocation_is_seeded_and_scaled_by_amount(self):
        sphere = trimesh.creation.icosphere(3, 1.0)
        kw = dict(layers=6, retained=70.0, jitter_shift=10.0, jitter_spin=20.0, jitter_tilt=10.0, seed=4)
        a, _ = sculpt(sphere, **kw)
        b, _ = sculpt(sphere, **kw)
        np.testing.assert_array_equal(a.vertices, b.vertices)
        c, _ = sculpt(sphere, **dict(kw, seed=5))
        self.assertFalse(np.allclose(a.vertices, c.vertices))
        same, info = sculpt(sphere, amount=0.0, **kw)
        self.assertEqual(info["layers"], 0)
        np.testing.assert_array_equal(same.vertices, sphere.vertices)


class InfluenceTests(unittest.TestCase):
    def test_band_and_sphere_weights(self):
        lo, hi = np.array([-1, -1, -1.0]), np.array([1, 1, 1.0])
        P = np.array([[0, 0, -0.9], [0, 0, 0.0], [0, 0, 0.9]])
        w = influence.weights(P, {"influence": "band", "band_from": 50, "band_to": 100, "feather": 0}, lo, hi)
        np.testing.assert_allclose(w, [0, 1, 1])
        w = influence.weights(P, {"influence": "band", "band_from": 60, "band_to": 100, "feather": 25}, lo, hi)
        self.assertTrue(0 < w[1] < 1 and w[0] == 0 and w[2] == 1)
        v = {"influence": "sphere", "sphere_centre": (0, 0, 50), "sphere_radius": 20, "feather": 0}
        np.testing.assert_allclose(influence.weights(P, v, lo, hi), [0, 0, 1])
        np.testing.assert_allclose(influence.weights(P, dict(v, invert_influence=True), lo, hi), [1, 1, 0])
        np.testing.assert_allclose(influence.weights(P, {"influence": "all"}, lo, hi), 1)

    def test_layers_outside_the_band_stay_whole(self):
        box = trimesh.creation.box(extents=(1, 1, 2))
        out, info = sculpt(box, layers=8, retained=50.0, separation=50.0, influence="band", band_from=50,
                           band_to=100, feather=0)
        below = out.vertices[out.vertices[:, 2] < -0.01]
        self.assertAlmostEqual(float(below[:, 2].min()), -1.0)
        # the lower half is untouched: full thickness, so its volume is all there
        lower = trimesh.intersections.slice_mesh_plane(out, (0, 0, -1), (0, 0, 0.0), cap=True)
        self.assertAlmostEqual(lower.volume, box.volume / 2, places=6)


class EngineTests(unittest.TestCase):
    def setUp(self):
        registry()
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_strata_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.src = self.folder / "pillar.stl"
        trimesh.creation.capsule(height=60.0, radius=15.0, count=[24, 24]).export(self.src)

    def test_amount_zero_is_the_input(self):
        res = engine.run(context(), self.src, None, TOOL.values(dict(amount=0.0)))
        v = res.preview["views"][-1]["mesh"]
        orig = trimesh.load(self.src, process=False)
        self.assertEqual(v.faces_total, len(orig.faces))
        np.testing.assert_allclose(v.bounds, orig.bounds, atol=1e-6)

    def test_stage_set_is_published_with_its_frame(self):
        vals = TOOL.values(dict(stages=3, layers=6, up_axis="+y", front="+z"))
        dst = output_path(TOOL, self.src, vals)
        res = engine.run(context(), self.src, dst, vals)
        self.assertEqual(len(res.outputs), 4)
        folder = Path(res.outputs[1]).parent
        man = json.loads((folder / stages.MANIFEST).read_text())
        self.assertEqual(man["frame"]["up"], "+y")
        self.assertEqual([s["amount"] for s in man["stages"]], [0.0, 0.5, 1.0])
        for o in res.outputs:
            self.assertIn(b"atlas-toolkit:mesh-strata", Path(o).read_bytes()[:80])
        orig = trimesh.load(self.src, process=False)
        first = trimesh.load(res.outputs[1], process=False)
        np.testing.assert_allclose(first.vertices, orig.vertices, atol=1e-5)     # stage 0: the model itself
        last = trimesh.load(res.outputs[-1], process=False)
        self.assertGreater(last.extents[1], orig.extents[1] * 1.05)             # lifted apart along Y (up)

    def test_a_shared_stage_does_not_depend_on_the_stage_count(self):
        def stage(n, i):
            vals = TOOL.values(dict(stages=n, layers=6, jitter_shift=5.0, jitter_tilt=5.0, seed=2))
            return engine.run(context("analyse"), self.src, None, vals).preview["views"][i]["mesh"]
        a, b = stage(3, 1), stage(5, 2)          # both at amount 0.5
        np.testing.assert_allclose(np.sort(a.vertices, axis=0), np.sort(b.vertices, axis=0), atol=1e-5)

    def test_dry_runs_write_nothing(self):
        before = set(self.folder.iterdir())
        vals = TOOL.values(dict(stages=2, layers=5))
        dst = output_path(TOOL, self.src, vals)
        engine.run(context("draft"), self.src, dst, vals)
        engine.run(context("analyse"), self.src, dst, vals)
        self.assertEqual(set(self.folder.iterdir()), before)


class WorkflowStepTests(unittest.TestCase):
    def test_strata_then_plotter_vision(self):
        reg = registry()
        tmp = Path(tempfile.mkdtemp(prefix="atlas_strata_wf_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "in").mkdir()
        (tmp / "out").mkdir()
        mesh = samples.mesh(tmp / "in")
        vision = reg.get("plotter-vision")
        wf = W.Workflow(name="Strata drawing")
        src = wf.add(W.input_node("mesh"))
        strata = wf.add(W.tool_node(reg.get("mesh-strata"), values={"layers": 6}))
        draw = wf.add(W.tool_node(vision, values=dict(vision.test_values or {})))
        out = wf.add(W.save_node(where="folder", folder=str(tmp / "out")))
        wf.connect(src.id, strata.id)
        wf.connect(strata.id, draw.id)
        wf.connect(draw.id, out.id)
        self.assertFalse([m for m in W.check(wf, reg) if m.level == "error"])
        res = W.run_workflow(wf, mesh, lambda *_: None, lambda: False, reg=reg,
                             cache=W.WorkflowCache(tmp / "cache"), tool_cache=str(tmp / "toolcache"))
        final = [Path(o) for o in res.outputs]
        self.assertEqual(len(final), 1)
        self.assertTrue(final[0].name.endswith("_strata_vision.svg"))
        self.assertIn(b"<desc>plotter-vision</desc>", final[0].read_bytes())


if __name__ == "__main__":
    unittest.main()
