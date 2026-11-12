"""Dual Fusion: normalising, field sampling, arrangement, every fusion type's geometry, exact stage 0, the mirrored
default, stage sets and the second model as a workflow side port."""
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
from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.tools.mesh_dual import TOOL, engine
from atlas_toolkit.tools.mesh_dual import mesh_dual as md
from atlas_toolkit.tools.mesh_porous import mesh_porous as mp

QUICK = dict(resolution=56, draft_resolution=40, max_faces=0, smooth=0)


def context(job="run", log=None):
    def emit(kind, args):
        if log is not None and kind == "log":
            log.append(args)
    return JobContext(emit, lambda: False, job)


def run_mesh(src, job="run", dst=None, log=None, **values):
    vals = TOOL.values(dict(QUICK, **values))
    res = engine.run(context(job, log), src, dst, vals)
    return res, [trimesh.Trimesh(v["mesh"].vertices, v["mesh"].faces, process=False) for v in res.preview["views"]]


def pieces(m) -> int:
    m = m.copy()
    m.merge_vertices()
    return len(m.split(only_watertight=False))


class FoundationTests(unittest.TestCase):
    def test_normalise_and_field_sampling(self):
        s = trimesh.creation.icosphere(subdivisions=4, radius=3.0)
        V = md.normalise(s.vertices + 7.0, 2.0)
        np.testing.assert_allclose(np.ptp(V, axis=0)[2], 2.0)
        np.testing.assert_allclose((V.min(0) + V.max(0)) / 2, 0.0, atol=1e-12)
        f = md.ModelField.of_mesh(V, s.faces, 48)
        R, t = md.rot_z(90.0), np.array([3.0, 0.0, 0.0])
        P = np.random.default_rng(0).uniform(-1.6, 1.6, (2000, 3)) + t
        exact = np.linalg.norm(P - t, axis=1) - 1.0
        got = f.at(P, R, t)
        near = np.abs(exact) < 0.2
        np.testing.assert_allclose(got[near], exact[near], atol=0.02)
        far = f.at(np.array([[30.0, 0.0, 0.0]]), R, t)
        self.assertGreater(far[0], 20.0)                                     # outside its grid: still far

    def test_arrangement(self):
        box = trimesh.creation.box(extents=(1.0, 0.6, 2.0))
        A = md.Model(box.vertices, box.faces)
        B = md.Model(md.normalise(box.vertices, 1.0), box.faces)            # half as tall
        pa, pb = md.arrange(A, B, "side", 0.5)
        (la, ha), (lb, hb) = pa.bounds(A), pb.bounds(B)
        np.testing.assert_allclose(lb[0] - ha[0], 0.5)                       # the gap between them
        np.testing.assert_allclose([la[2], lb[2]], [-1.0, -1.0])             # on one base
        pa, pb = md.arrange(A, B, "side", 0.5, closeness=1.0)
        np.testing.assert_allclose((pb.bounds(B)[0] + pb.bounds(B)[1])[:2] / 2, 0.0, atol=1e-12)
        pa, pb = md.arrange(A, B, "face", 0.5)
        np.testing.assert_allclose(pa.R @ [0, -1, 0], [1, 0, 0], atol=1e-12)  # A faces B (+X)
        np.testing.assert_allclose(pb.R @ [0, -1, 0], [-1, 0, 0], atol=1e-12)
        pa, pb = md.arrange(A, B, "stacked", 0.2)
        np.testing.assert_allclose(pb.bounds(B)[0][2], pa.bounds(A)[1][2] + 0.2)

    def test_strata_regions(self):
        z = np.linspace(0, 4, 401)
        rB = md.strata_regions(z, np.full_like(z, 0.25), 1.0)
        self.assertAlmostEqual(np.mean(rB < 0), 0.25, delta=0.02)
        self.assertTrue(np.all(md.strata_regions(z, np.zeros_like(z), 1.0) > 0))
        self.assertTrue(np.all(md.strata_regions(z, np.ones_like(z), 1.0) < 0))

    def test_strands_grow_and_join(self):
        A = trimesh.creation.box(extents=(1.0, 1.0, 2.0)).vertices
        B = A + [2.0, 0, 0]
        v = TOOL.values(dict(strands=6))
        C0, r0 = md.strands(A, B, 0, v, 0.25, np.random.default_rng(1))
        C1, r1 = md.strands(A, B, 0, v, 1.0, np.random.default_rng(1))
        self.assertLess(len(C0), len(C1))                                    # still reaching out from both sides
        self.assertTrue(np.all(np.abs(C0[:, 0] - 1.0) > 0.05))               # nothing across the middle yet
        self.assertGreater(r1.min(), 0)


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="atlas_dual_"))
        cls.ball = cls.tmp / "ball.stl"
        trimesh.creation.icosphere(subdivisions=3, radius=10.0).export(cls.ball)
        cls.box = cls.tmp / "block.stl"
        trimesh.creation.box(extents=(30.0, 30.0, 60.0)).export(cls.box)       # other units, other proportions

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_ratio_and_exact_stage_zero(self):
        res, ms = run_mesh(self.ball, second=str(self.box), ratio=0.5, fusion="convergence", arrangement="side",
                           gap=40.0, amount=1.0, stages=2)
        first = ms[0]
        self.assertEqual(len(first.faces), 1280 + 12)                        # both models, as they are
        parts = sorted(first.split(only_watertight=False), key=lambda m: m.bounds[0][0])
        np.testing.assert_allclose(np.ptp(parts[1].vertices, axis=0)[2], 10.0, rtol=1e-6)   # half the ball's 20
        np.testing.assert_allclose(parts[0].bounds[0][2], parts[1].bounds[0][2], atol=1e-6)  # one base
        self.assertEqual(pieces(ms[-1]), 1)                                  # melted into one

    def test_convergence_joins_before_touching(self):
        res, ms = run_mesh(self.ball, second=str(self.box), fusion="convergence", arrangement="side", gap=40.0,
                           radius=30.0, melt_from=1.0, amount=0.55, stages=1)
        self.assertEqual(pieces(ms[-1]), 1)

    def test_missing_second_is_the_mirror(self):
        log = []
        res, ms = run_mesh(self.ball, log=log, fusion="split", amount=0.5)
        self.assertIn("mirrored", res.report)

    def test_emergence_grows_with_fusion(self):
        vols = []
        for u in (0.2, 0.9):
            _, ms = run_mesh(self.box, second=str(self.ball), ratio=0.5, fusion="emergence", host="model",
                             depth=80.0, amount=u)
            vols.append(abs(ms[-1].volume))
        self.assertGreater(vols[1], vols[0] * 1.01)
        for host in ("wall", "floor"):
            res, ms = run_mesh(self.ball, second=str(self.box), fusion="emergence", host=host, amount=0.6)
            self.assertEqual(pieces(ms[-1]), 1, host)                        # the models fused with the slab

    def test_split_styles(self):
        vol = {}
        for style in ("blend", "groove", "suture", "open"):
            _, ms = run_mesh(self.box, second=str(self.box), fusion="split", style=style, seam_width=6.0,
                             seam_depth=4.0, open=40.0, amount=0.5)
            vol[style] = abs(ms[-1].volume)
            if style == "open":
                self.assertEqual(pieces(ms[-1]), 2)                          # two capped halves
        self.assertLess(vol["groove"], vol["blend"])
        self.assertGreater(vol["suture"], vol["blend"])

    def test_strata_alternate_and_ends_are_exact(self):
        _, ms = run_mesh(self.ball, second=str(self.box), fusion="strata", slabs=6, slab_gap=3.0,
                         strata_axis="up", grade_dir="none", amount=0.5)
        self.assertGreaterEqual(pieces(ms[-1]), 5)                           # gaps part the slabs
        _, ms = run_mesh(self.ball, second=str(self.box), fusion="strata", amount=1.0)
        self.assertEqual(len(ms[-1].faces), 12)                              # all B: the block itself

    def test_bridge_joins_and_is_repeatable(self):
        _, a = run_mesh(self.ball, second=str(self.ball), fusion="bridge", arrangement="side", gap=40.0,
                        strands=8, strand_width=10.0, amount=1.0, seed=5)
        _, b = run_mesh(self.ball, second=str(self.ball), fusion="bridge", arrangement="side", gap=40.0,
                        strands=8, strand_width=10.0, amount=1.0, seed=5)
        self.assertEqual(pieces(a[-1]), 1)
        np.testing.assert_allclose(a[-1].vertices, b[-1].vertices)

    def test_stage_set_is_published_and_signed(self):
        out = self.tmp / "out"
        out.mkdir(exist_ok=True)
        dst = out / "ball_dual.stl"
        run_mesh(self.ball, dst=dst, second=str(self.box), fusion="convergence", amount=1.0, stages=3)
        self.assertIn(b"atlas-toolkit:mesh-dual", dst.read_bytes()[:200])
        manifest = json.loads((out / "ball_dual_stages" / "stages.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["stages"]), 3)


class Release2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="atlas_dual2_"))
        cls.ball = cls.tmp / "ball.stl"
        trimesh.creation.icosphere(subdivisions=3, radius=10.0).export(cls.ball)
        cls.box = cls.tmp / "block.stl"
        trimesh.creation.box(extents=(14.0, 14.0, 20.0)).export(cls.box)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def frags(self, res) -> int:
        return int(res.report.split("fragments")[0].split()[-1]) if "fragments" in res.report else 0

    def test_disintegration(self):
        common = dict(second=str(self.box), fusion="disintegrate", front_dir="left", cell=8.0, seed=3, survive=0.0)
        r1, a = run_mesh(self.ball, amount=0.3, **common)
        r2, b = run_mesh(self.ball, amount=0.3, **common)
        np.testing.assert_allclose(a[-1].vertices, b[-1].vertices)                 # seeded
        self.assertGreater(self.frags(r1), 0)
        _, end = run_mesh(self.ball, amount=1.0, **common)
        self.assertEqual(len(end[-1].faces), 12)                                   # the second model, exactly
        rn, dust = run_mesh(self.ball, amount=1.0, reveal="nothing", **{k: v for k, v in common.items()
                                                                        if k != "second"})
        self.assertGreater(self.frags(rn), 0)                                      # only fragments in the air
        self.assertLess(abs(dust[-1].volume), 0.5 * abs(a[-1].volume))
        rb, _ = run_mesh(self.ball, amount=0.3, max_fragments=100, **dict(common, cell=2.0))
        self.assertEqual(self.frags(rb), 100)                                      # capped at the budget

    def test_motion_echo_spans_the_passage(self):
        _, half = run_mesh(self.ball, second=str(self.box), fusion="echo", arrangement="side", gap=60.0,
                           echoes=4, path="straight", amount=0.5)
        _, full = run_mesh(self.ball, second=str(self.box), fusion="echo", arrangement="side", gap=60.0,
                           echoes=4, path="straight", amount=1.0)
        self.assertGreater(np.ptp(full[-1].vertices[:, 0]), np.ptp(half[-1].vertices[:, 0]) + 5.0)
        self.assertEqual(pieces(full[-1]), 1)
        _, arc = run_mesh(self.ball, second=str(self.box), fusion="echo", arrangement="side", gap=60.0,
                          echoes=4, path="arc", lift=40.0, amount=1.0)
        self.assertGreater(arc[-1].bounds[1][2], full[-1].bounds[1][2] + 2.0)

    def test_patchwork(self):
        shares = []
        for u in (0.2, 0.8):
            res, _ = run_mesh(self.ball, second=str(self.box), fusion="patchwork", cell_size=30.0,
                              patch_dir="none", amount=u, seed=2)
            got, total = res.report.split(" cells from")[0].split()[-3::2]
            shares.append(int(got) / int(total))
        self.assertLess(shares[0], shares[1])
        _, apart = run_mesh(self.ball, second=str(self.box), fusion="patchwork", cell_size=30.0, grout=2.0,
                            explode=40.0, amount=0.5, seed=2)
        self.assertGreater(pieces(apart[-1]), 3)                                   # cells pushed apart

    def test_chrysalis_encloses_and_opens(self):
        vols = []
        for u in (0.0, 1.0):
            _, ms = run_mesh(self.box, second=str(self.ball), ratio=0.5, fusion="chrysalis",
                             shell_pattern="gyroid", pattern_cell=40.0, rib=4.0, open_dir="none", amount=u)
            vols.append(abs(ms[-1].volume))
            inner = np.ptp(ms[-1].vertices, axis=0)
            self.assertGreater(inner[2], 20.0)            # the shell clears the block although the ball is smaller
        self.assertLess(vols[1], vols[0])                 # windows open


class WorkflowTests(unittest.TestCase):
    def test_second_model_is_a_side_port(self):
        reg = registry()
        tmp = Path(tempfile.mkdtemp(prefix="atlas_dual_wf_"))
        try:
            src = samples.mesh(tmp)
            other = tmp / "other.stl"
            trimesh.creation.icosphere(radius=12.0).export(other)
            self.assertIn("second", [p.key for p in W.ports(W.tool_node(TOOL), reg)])
            wf = W.Workflow(name="Two into one")
            a = wf.add(W.input_node("mesh"))
            b = wf.add(W.input_node("mesh", mode="fixed", path=str(other)))
            f = wf.add(W.tool_node(TOOL, values=dict(QUICK, fusion="split", amount=0.5)))
            s = wf.add(W.save_node(where="folder", folder=str(tmp / "out")))
            wf.connect(a.id, f.id)
            wf.connect(b.id, f.id, "second")
            wf.connect(f.id, s.id)
            self.assertEqual(W.errors(W.check(wf, reg)), [])
            res = W.run_workflow(wf, src, lambda *x: None, lambda: False, reg=reg, cache=W.WorkflowCache(tmp / "c"),
                                 tool_cache=str(tmp / "tc"))
            self.assertEqual([Path(o).name for o in res.outputs], ["sample_dual.stl"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
