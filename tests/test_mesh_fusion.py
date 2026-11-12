"""Shape Fusion: built-in shapes, placement, smooth booleans, sweeps, exact endpoints, different topologies, empty
results, sealed pockets, stage sets, the target file and the two-input workflow (side port and cache)."""
import _isolate  # first: settings and caches stay in a temporary folder
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core import samples
from atlas_toolkit.core import workflow as W
from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.tools.mesh_fusion import TOOL, engine
from atlas_toolkit.tools.mesh_fusion import mesh_fusion as mf
from atlas_toolkit.tools.mesh_porous import mesh_porous as mp

QUICK = dict(resolution=56, draft_resolution=40, max_faces=0, smooth=0)


def context(mode="run", log=None):
    def emit(kind, args):
        if log is not None and kind == "log":
            log.append(args)
    return JobContext(emit, lambda: False, mode)


def run_mesh(src, job="run", dst=None, log=None, **values):
    vals = TOOL.values(dict(QUICK, **values))
    res = engine.run(context(job, log), src, dst, vals)
    v = res.preview["views"][-1]["mesh"]
    return res, trimesh.Trimesh(v.vertices, v.faces, process=False)


def genus(m) -> int:
    m = m.copy()
    m.merge_vertices()
    return int(round((2 - m.euler_number) / 2))


class FieldTests(unittest.TestCase):
    def test_built_in_shapes_are_signed_distances(self):
        rng = np.random.default_rng(0)
        P = rng.uniform(-1.5, 1.5, (4000, 3))
        for name in mf.SHAPES:
            d = mf.shape_sdf(name, P, 0.3, 0.5)
            m = mf.shape_mesh(name, 0.3, 0.5)
            inside = m.contains(P)
            agree = np.mean((d < 0) == inside)
            self.assertGreater(agree, 0.98, name)            # mesh facets vs the exact shape
            self.assertTrue(np.all(np.abs(np.diff(d[:200])) <= np.linalg.norm(np.diff(P[:200], axis=0), axis=1)
                                   + 1e-9), name)                # 1-Lipschitz, a true distance
        np.testing.assert_allclose(mf.shape_sdf("sphere", np.array([[2.0, 0, 0]])), [1.0])
        R = 1 / 1.3
        np.testing.assert_allclose(mf.shape_sdf("torus", np.array([[R, 0, 0]]), 0.3), [-0.3 * R])

    def test_smooth_booleans(self):
        a, b = np.array([-1.0, 0.5, 2.0]), np.array([1.0, -0.5, 2.0])
        np.testing.assert_allclose(mf.operate("fuse", a, b, 0.0), np.minimum(a, b))
        np.testing.assert_allclose(mf.operate("carve", a, b, 0.0), np.maximum(a, -b))
        np.testing.assert_allclose(mf.operate("intersect", a, b, 0.0), np.maximum(a, b))
        self.assertTrue(np.all(mf.smin(a, b, 0.5) <= np.minimum(a, b) + 1e-12))     # a fillet only adds
        self.assertTrue(np.all(mf.smax(a, b, 0.5) >= np.maximum(a, b) - 1e-12))
        np.testing.assert_allclose(mf.smin(a, b, 0.5)[0], -1.0)                     # far from the seam: exact

    def test_sweep_runs_from_nothing_to_everything(self):
        P = np.random.default_rng(1).uniform(-1, 1, (500, 3))
        for how in mf.SWEEPS[1:]:
            pos = mf.sweep_position(P, how, (-1, -1, -1), (1, 1, 1))
            self.assertTrue(np.all((pos >= 0) & (pos <= 1)), how)
            self.assertTrue(np.all(mf.progress_at(0.0, pos, 0.3) == 0), how)
            self.assertTrue(np.all(mf.progress_at(1.0, pos, 0.3) == 1), how)
            mid = mf.progress_at(0.5, pos, 0.3)
            self.assertTrue(mid.min() == 0 and mid.max() == 1, how)              # half way: one end done
        up = mf.sweep_position(np.array([[0, 0, -1.0], [0, 0, 1.0]]), "up", (-1, -1, -1), (1, 1, 1))
        self.assertLess(up[0], up[1])                                              # the base first

    def test_placement(self):
        box = trimesh.creation.box(extents=(1.0, 2.0, 4.0))                 # 4 tall, its own frame
        lo, hi = np.array([-0.5, -0.5, -1.0]), np.array([0.5, 0.5, 1.0])    # the model: 2 tall
        pl = mf.place(box.vertices, lo, hi, "height")
        Q = pl.apply(box.vertices)
        np.testing.assert_allclose(Q.min(0)[2], -1.0)                       # on the same base
        np.testing.assert_allclose(np.ptp(Q, axis=0)[2], 2.0)               # as tall
        np.testing.assert_allclose(pl.local(Q), box.vertices, atol=1e-12)   # local() inverts apply()
        pl = mf.place(box.vertices, lo, hi, "keep", offset=(1.0, 0, 0))
        np.testing.assert_allclose(pl.apply(box.vertices), box.vertices + [1.0, 0, 0], atol=1e-12)
        pl = mf.place(box.vertices, lo, hi, "centre", copies=4)
        self.assertEqual(pl.turns, [0.0, 90.0, 180.0, 270.0])
        np.testing.assert_allclose(np.ptp(pl.apply(box.vertices, 1), axis=0)[:2], [2.0 / 4 * 2, 1.0 / 4 * 2],
                                   atol=1e-9)                               # a quarter turn swaps X and Y

    def test_sealed_pockets_are_filled(self):
        grid = mp.make_grid((-1, -1, -1), (1, 1, 1), 24)
        P = grid.points(np.arange(grid.size))
        r = np.linalg.norm(P, axis=1)
        shell = np.maximum(r - 0.9, 0.5 - r).reshape(grid.shape)          # a hollow ball
        F, n = mf.fill_voids(shell, grid.step)
        self.assertEqual(n, 1)
        self.assertTrue(np.all(F.reshape(-1)[r < 0.4] < 0))
        F, n = mf.fill_voids(F, grid.step)
        self.assertEqual(n, 0)


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="atlas_fusion_"))
        sphere = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
        cls.sphere = cls.tmp / "ball.stl"
        sphere.export(cls.sphere)
        torus = trimesh.creation.torus(8.0, 3.0, major_sections=48, minor_sections=16)
        torus.apply_translation((40.0, 0.0, 0.0))                           # somewhere else, other units
        cls.torus = cls.tmp / "ring.stl"
        torus.export(cls.torus)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_endpoints_are_the_shapes_themselves(self):
        res, _ = run_mesh(self.sphere, target="file", target_file=str(self.torus), mode="morph", amount=1.0,
                          stages=3)
        src = trimesh.load(self.sphere)
        first, last = res.preview["views"][0]["mesh"], res.preview["views"][-1]["mesh"]
        np.testing.assert_allclose(first.vertices, src.vertices)            # stage 0: untouched
        tor = trimesh.load(self.torus)
        self.assertEqual(len(last.faces), len(tor.faces))                   # the target, not remeshed
        np.testing.assert_allclose(np.ptp(last.vertices, axis=0)[2], np.ptp(src.vertices, axis=0)[2], rtol=1e-6)

    def test_morph_changes_topology_without_correspondence(self):
        res, m = run_mesh(self.sphere, target="torus", mode="morph", amount=0.75)
        self.assertEqual(genus(m), 1)                                        # a ball with a hole through it
        _, m0 = run_mesh(self.sphere, target="torus", mode="morph", amount=0.2)
        self.assertEqual(genus(m0), 0)

    def test_fuse_carve_intersect(self):
        vol = abs(trimesh.load(self.sphere).volume)
        _, fused = run_mesh(self.sphere, target="box", aspect=30.0, target_scale=1.4, mode="fuse", amount=1.0,
                            radius=4.0)
        _, carved = run_mesh(self.sphere, target="cylinder", aspect=40.0, target_scale=0.4,
                             target_rotate=(90.0, 0.0, 0.0), mode="carve", amount=1.0, radius=2.0)
        _, cut = run_mesh(self.sphere, target="box", aspect=60.0, target_scale=0.9, mode="intersect", amount=1.0,
                          radius=0.0)
        self.assertGreater(abs(fused.volume), vol * 1.02)
        self.assertLess(abs(carved.volume), vol * 0.95)
        self.assertEqual(genus(carved), 1)                                   # a tunnel through the ball
        self.assertLess(abs(cut.volume), vol * 0.98)
        self.assertLessEqual(np.ptp(cut.vertices, axis=0)[2], 20.0 * 0.9 + 0.5)

    def test_translated_target_uses_the_joint_domain(self):
        res, m = run_mesh(self.sphere, target="file", target_file=str(self.torus), fit="keep", mode="fuse",
                          amount=1.0, radius=0.0)
        self.assertGreater(m.bounds[1][0], 49.0)                             # the ring at x 40 ± 11, whole
        self.assertLess(m.bounds[0][0], -9.0)
        self.assertEqual(res.stat("pieces").value, 2)

    def test_misaligned_morph_says_what_to_move(self):
        log = []
        with self.assertRaisesRegex(ValueError, "Offset"):
            run_mesh(self.sphere, log=log, target="sphere", target_scale=0.3, target_offset=(200.0, 0.0, 0.0),
                     mode="morph", amount=0.5)

    def test_missing_target_file(self):
        with self.assertRaisesRegex(ValueError, "target model"):
            run_mesh(self.sphere, target="file", target_file="")

    def test_sweep_stages_share_the_rest_and_progress(self):
        res, _ = run_mesh(self.sphere, target="box", aspect=100.0, mode="morph", amount=1.0, sweep="up",
                          stages=3)
        vols = [abs(trimesh.Trimesh(v["mesh"].vertices, v["mesh"].faces).volume) for v in res.preview["views"]]
        self.assertTrue(vols[0] < vols[1] < vols[2])                          # the block fills in from the base

    def test_influence_keeps_the_rest(self):
        _, m = run_mesh(self.sphere, target="box", aspect=100.0, mode="morph", amount=1.0, influence="band",
                        band_from=60.0, band_to=100.0, feather=5.0)
        low = m.vertices[m.vertices[:, 2] < -5.0]
        np.testing.assert_allclose(np.linalg.norm(low, axis=1), 10.0, atol=0.6)   # the lower half still a ball

    def test_self_copies_make_one_body(self):
        cap = trimesh.creation.box(extents=(4.0, 24.0, 10.0))               # a bar: three copies make a star
        path = self.tmp / "bar.stl"
        cap.export(path)
        res, m = run_mesh(path, target="self", copies=3, mode="fuse", amount=1.0, radius=3.0)
        self.assertEqual(res.stat("pieces").value, 1)
        self.assertGreater(abs(m.volume), abs(cap.volume) * 2.0)

    def test_draft_and_published_stage_set(self):
        out = self.tmp / "out"
        out.mkdir(exist_ok=True)
        dst = out / "ball_fusion.stl"
        res, _ = run_mesh(self.sphere, dst=dst, target="torus", mode="morph", amount=1.0, stages=3)
        self.assertTrue(dst.exists())
        self.assertIn(b"atlas-toolkit:mesh-fusion", dst.read_bytes()[:200])
        manifest = json.loads((out / "ball_fusion_stages" / "stages.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["stages"]), 3)
        self.assertEqual(manifest["tool"], "mesh-fusion")
        res, _ = run_mesh(self.sphere, job="draft", target="torus")
        self.assertIn("preview", res.report)
        self.assertEqual(res.outputs, [])


class WorkflowTests(unittest.TestCase):
    def test_target_is_a_side_port_and_its_file_keys_the_cache(self):
        reg = registry()
        tmp = Path(tempfile.mkdtemp(prefix="atlas_fusion_wf_"))
        try:
            src = samples.mesh(tmp)
            ring = tmp / "ring.stl"
            trimesh.creation.torus(10.0, 3.0).export(ring)
            ports = [p.key for p in W.ports(W.tool_node(TOOL), reg)]
            self.assertIn("target_file", ports)
            # a second input feeds the target port
            wf = W.Workflow(name="Fuse two")
            a = wf.add(W.input_node("mesh"))
            b = wf.add(W.input_node("mesh", mode="fixed", path=str(ring)))
            f = wf.add(W.tool_node(TOOL, values=dict(QUICK, target="file", mode="fuse", amount=1.0)))
            s = wf.add(W.save_node(where="folder", folder=str(tmp / "out")))
            wf.connect(a.id, f.id)
            wf.connect(b.id, f.id, "target_file")
            wf.connect(f.id, s.id)
            self.assertEqual(W.errors(W.check(wf, reg)), [])
            cache = W.WorkflowCache(tmp / "cache")
            res = W.run_workflow(wf, src, lambda *a: None, lambda: False, reg=reg, cache=cache,
                                 tool_cache=str(tmp / "tc"))
            self.assertEqual([Path(o).name for o in res.outputs], ["sample_fusion.stl"])
            # a target chosen in the step's settings: changing the file's contents makes a new cache entry
            wf2 = W.Workflow(name="Fuse fixed")
            a2 = wf2.add(W.input_node("mesh"))
            f2 = wf2.add(W.tool_node(TOOL, values=dict(QUICK, target="file", target_file=str(ring), mode="fuse",
                                                       amount=1.0)))
            g2 = wf2.add(W.tool_node(reg.get("mesh-fusion"), values=dict(QUICK, target="sphere", mode="fuse",
                                                                       amount=1.0, target_scale=0.3)))
            s2 = wf2.add(W.save_node(where="folder", folder=str(tmp / "out2")))
            wf2.connect(a2.id, f2.id)
            wf2.connect(f2.id, g2.id)
            wf2.connect(g2.id, s2.id)
            cache2 = W.WorkflowCache(tmp / "cache2")
            W.run_workflow(wf2, src, lambda *a: None, lambda: False, reg=reg, cache=cache2, tool_cache=str(tmp / "tc"))
            n1 = len(cache2.entries())
            trimesh.creation.torus(12.0, 2.0).export(ring)
            st = ring.stat()
            os.utime(ring, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
            W.run_workflow(wf2, src, lambda *a: None, lambda: False, reg=reg, cache=cache2, tool_cache=str(tmp / "tc"))
            self.assertGreater(len(cache2.entries()), n1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
