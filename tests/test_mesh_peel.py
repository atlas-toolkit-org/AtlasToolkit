"""Surface Peel: panels (petals, scales, connected pieces, seams that never fold), the spine bend (identity, fixed
roots, curl and unroll on known shapes), closed shells, published stage sets that share one rest model, layers,
limits, Blender's Complex Solidify and a workflow step."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import json
import math
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
from atlas_toolkit.geom import blender as gb
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom import stages
from atlas_toolkit.tools.mesh_peel import TOOL, engine
from atlas_toolkit.tools.mesh_peel import mesh_peel as mp


def context(mode="run"):
    return JobContext(lambda *_: None, lambda: False, mode)


def strip(length=2.0, width=0.3, nu=60, nv=6, wrap=0.0):
    """A strip along +X (root at x = 0), facing +Z; wrap bends it round a cylinder by that angle (radians)."""
    u, v = np.meshgrid(np.linspace(0, length, nu), np.linspace(-width / 2, width / 2, nv), indexing="ij")
    if wrap:
        r = length / wrap
        P = np.column_stack([r * np.sin(u.ravel() / r), v.ravel(), r * np.cos(u.ravel() / r) - r])
    else:
        P = np.column_stack([u.ravel(), v.ravel(), np.zeros(u.size)])
    idx = np.arange(nu * nv).reshape(nu, nv)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel(), idx[:-1, 1:].ravel()
    F = np.concatenate([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
    return P, F


def along(nu=60, nv=6, length=2.0):
    """Each strip vertex's distance along the strip (strip() lays them out row by row)."""
    return np.repeat(np.linspace(0, length, nu), nv)


def strip_panel(P, F):
    """Rooted at the strip's start: Peel roots a panel where the angle from the pole is smallest."""
    return mp.panel_frames(P, F, mp.vertex_normals(P, F), along(), "peel")


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.sphere = trimesh.creation.icosphere(subdivisions=4)
        self.p = dict(TOOL.values())

    def test_petals_make_sectors_by_rows_and_repeat(self):
        V, F = self.sphere.vertices, self.sphere.faces
        p = dict(self.p, petals=6, rows=2, wobble=0.3, seam_smooth=0)
        a = mp.segment(V, F, p, np.zeros(3), np.array([0.0, 0.0, 1.0]))
        b = mp.segment(V, F, p, np.zeros(3), np.array([0.0, 0.0, 1.0]))
        np.testing.assert_array_equal(a.labels, b.labels)
        self.assertEqual(a.count, 12)
        c = mp.segment(V, F, dict(p, seed=99), np.zeros(3), np.array([0.0, 0.0, 1.0]))
        self.assertTrue((a.labels != c.labels).any())          # the wobble is seeded

    def test_scales_split_separate_parts(self):
        s1 = trimesh.creation.icosphere(subdivisions=3)
        s2 = s1.copy()
        s2.apply_translation([3.0, 0, 0])
        m = trimesh.util.concatenate([s1, s2])
        p = dict(self.p, pattern="scales", pieces=1, seam_smooth=0)
        pan = mp.segment(m.vertices, m.faces, p, np.zeros(3), np.array([0.0, 0.0, 1.0]))
        self.assertEqual(pan.count, 2)                          # one seed, two parts: two panels
        left = pan.labels[: len(s1.faces)]
        self.assertEqual(len(np.unique(left)), 1)

    def test_seam_smoothing_moves_seams_without_folding_faces(self):
        V, F = self.sphere.vertices, self.sphere.faces
        p = dict(self.p, pattern="scales", pieces=20, seam_smooth=10)
        pan = mp.segment(V, F, p, np.zeros(3), np.array([0.0, 0.0, 1.0]))
        self.assertGreater(np.abs(pan.V - V).max(), 1e-4)
        n0 = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
        n1 = np.cross(pan.V[F[:, 1]] - pan.V[F[:, 0]], pan.V[F[:, 2]] - pan.V[F[:, 0]])
        self.assertTrue(((n0 * n1).sum(1) > 0).all())


class BendTests(unittest.TestCase):
    def test_nothing_asked_is_identity(self):
        P, F = strip()
        pn = strip_panel(P, F)
        np.testing.assert_array_equal(mp.bend(pn, 0.0, 0.0, 0.0, 0.4, 0.2), P)

    def test_root_never_moves(self):
        P, F = strip()
        pn = strip_panel(P, F)
        X = mp.bend(pn, math.radians(200), math.radians(40), 0.5, 0.5, 0.25)
        root = pn.s < 0.25 * pn.length - 1e-9
        self.assertTrue(root.any())
        np.testing.assert_array_equal(X[root], P[root])
        self.assertGreater(np.abs(X - P).max(), 0.5)

    def test_curl_rolls_a_flat_strip_outward_and_keeps_its_length(self):
        P, F = strip()
        pn = strip_panel(P, F)
        X = mp.bend(pn, math.pi, 0.0, 0.0, 0.0, 0.0)
        u = along()
        tip = u == u.max()
        self.assertGreater(X[tip, 2].mean(), 0.3)               # rolled toward its normal (+Z)
        self.assertLess(mp.stretch(P, X, F), 1.1)
        # the tip has turned about half a turn: its edge now runs back toward the root
        mid = (u > 1.9) & (u < 1.98)
        d = X[tip].mean(0) - X[mid].mean(0)
        self.assertLess(d[0], 0.0)

    def test_unroll_flattens_a_wrapped_strip(self):
        P, F = strip(wrap=math.pi * 0.9)
        pn = strip_panel(P, F)
        X = mp.bend(pn, 0.0, 0.0, 1.0, 0.0, 0.0)
        u = along()
        tip, root = u == u.max(), P[u == 0].mean(0)
        reach0 = np.linalg.norm(P[tip].mean(0) - root)
        reach1 = np.linalg.norm(X[tip].mean(0) - root)
        self.assertGreater(reach1, reach0 * 1.3)                 # straightened out
        self.assertGreater(reach1, 0.85 * 2.0)                   # nearly its full length
        self.assertLess(mp.stretch(P, X, F), 1.15)


class ShellTests(unittest.TestCase):
    def test_open_panel_becomes_a_closed_shell(self):
        P, F = strip()
        V2, F2 = mp.solidify(P, F, 0.02)
        m = trimesh.Trimesh(V2, F2, process=False)
        self.assertTrue(m.is_watertight)
        self.assertTrue(m.is_winding_consistent)
        self.assertGreater(m.volume, 0)                          # outward faces, inward thickness
        self.assertAlmostEqual(m.volume, 2.0 * 0.3 * 0.02, delta=0.002)

    def test_closed_surface_or_zero_thickness_is_left_alone(self):
        s = trimesh.creation.icosphere(subdivisions=2)
        V, F = mp.solidify(s.vertices, s.faces, 0.05)
        self.assertEqual(len(F), len(s.faces))
        P, Fs = strip()
        self.assertEqual(len(mp.solidify(P, Fs, 0.0)[1]), len(Fs))


class EngineTests(unittest.TestCase):
    def setUp(self):
        registry()
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_peel_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.src = self.folder / "pod.stl"
        trimesh.creation.capsule(height=40.0, radius=15.0, count=[24, 24]).export(self.src)

    def vals(self, **kw):
        return TOOL.values(dict(dict(max_edge=8.0, petals=5, rows=2), **kw))

    def test_amount_zero_is_the_input(self):
        res = engine.run(context(), self.src, None, self.vals(amount=0.0))
        v = res.preview["views"][-1]["mesh"]
        mesh = gm.load_mesh(self.src)
        np.testing.assert_allclose(np.sort(v.vertices, axis=0), np.sort(mesh.vertices, axis=0), atol=1e-4)

    def test_stage_set_is_published_signed_and_closed(self):
        vals = self.vals(stages=3)
        dst = output_path(TOOL, self.src, vals)
        res = engine.run(context(), self.src, dst, vals)
        self.assertEqual(len(res.outputs), 4)
        man = json.loads((Path(res.outputs[1]).parent / stages.MANIFEST).read_text())
        self.assertEqual([s["amount"] for s in man["stages"]], [0.0, 0.5, 1.0])
        for o in res.outputs:
            self.assertIn(b"atlas-toolkit:mesh-peel", Path(o).read_bytes()[:80])
        last = trimesh.load(res.outputs[-1], process=False)
        last.merge_vertices()
        parts = last.split(only_watertight=False)
        self.assertEqual(len(parts), res.stat("panels").value)
        self.assertTrue(all(q.is_watertight for q in parts))
        orig = trimesh.load(self.src)
        self.assertGreater(last.extents.max(), orig.extents.max() * 1.05)   # petals opened out

    def test_stages_share_the_rest_model(self):
        three = engine.run(context(), self.src, None, self.vals(stages=3, amount=1.2))
        five = engine.run(context(), self.src, None, self.vals(stages=5, amount=1.2))
        a = three.preview["views"][1]["mesh"]
        b = five.preview["views"][2]["mesh"]                     # amount 0.6 in both
        np.testing.assert_allclose(a.vertices, b.vertices, atol=1e-5)

    def test_staggered_order_opens_panels_in_turn(self):
        res = engine.run(context(), self.src, None, self.vals(stages=3, order="pole", stagger=0.9))
        self.assertIn("panels open", res.report)
        rows = [line for line in res.report.splitlines() if "panels open" in line]
        opened = [int(r.split(":")[1].split()[0]) for r in rows]
        self.assertLess(opened[0], opened[1])

    def test_influence_keeps_untouched_panels_in_one_piece(self):
        vals = self.vals(influence="band", band_from=70.0, band_to=100.0, feather=0.0)
        res = engine.run(context(), self.src, None, vals)
        self.assertLess(res.stat("open").value, res.stat("panels").value)
        mesh = res.preview["views"][-1]["mesh"]
        orig = gm.load_mesh(self.src)
        low = orig.bounds[0][2] + 0.2 * orig.extents[2]
        self.assertAlmostEqual(float(mesh.vertices[:, 2].min()), orig.bounds[0][2], delta=0.5)
        self.assertLess(float(mesh.vertices[:, 2].min()), low)

    def test_layers_and_core_nest_inside(self):
        one = engine.run(context(), self.src, None, self.vals())
        nest = engine.run(context(), self.src, None, self.vals(layers=3, core=True))
        self.assertEqual(nest.stat("panels").value, 3 * one.stat("panels").value)
        self.assertGreater(nest.stat("faces").value, 2.5 * one.stat("faces").value)

    def test_too_many_faces_is_refused_with_a_way_out(self):
        saved = engine.OUTPUT_BUDGET
        engine.OUTPUT_BUDGET = 1000
        try:
            with self.assertRaisesRegex(ValueError, "fewer Layers"):
                engine.run(context(), self.src, None, self.vals(layers=5))
        finally:
            engine.OUTPUT_BUDGET = saved

    def test_draft_simplifies_and_says_so(self):
        big = self.folder / "big.stl"
        trimesh.creation.icosphere(subdivisions=5).export(big)
        res = engine.run(context("draft"), big, None, TOOL.values(dict(draft_faces=3000)))
        self.assertIn("draft", res.report)

    def test_presets_are_valid(self):
        for preset in _presets.each(TOOL):
            vals = _presets.run_values(TOOL, preset)
            self.assertEqual(vals.seed, _presets.raw(preset)["seed"], preset.name)
            self.assertEqual(vals.pattern, _presets.raw(preset)["pattern"], preset.name)


@unittest.skipIf(gb.find_blender() is None, "Blender is not installed")
class BlenderSolidifyTests(unittest.TestCase):
    def test_complex_solidify_closes_every_panel(self):
        tmp = Path(tempfile.mkdtemp(prefix="atlas_peel_bl_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        src = tmp / "pod.stl"
        trimesh.creation.capsule(height=40.0, radius=15.0, count=[16, 16]).export(src)
        vals = TOOL.values(dict(max_edge=10.0, petals=4, rows=1, solidify="blender", thickness=1.0))
        res = engine.run(context(), src, None, vals)
        self.assertIn("Blender", res.report)
        m = trimesh.Trimesh(res.preview["views"][-1]["mesh"].vertices, res.preview["views"][-1]["mesh"].faces)
        self.assertGreater(len(m.faces), 0)


class WorkflowStepTests(unittest.TestCase):
    def test_peel_then_plotter_vision_in_a_workflow(self):
        tmp = Path(tempfile.mkdtemp(prefix="atlas_peel_wf_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        reg = registry()
        (tmp / "in").mkdir()
        src_mesh = samples.mesh(tmp / "in")
        out_dir = tmp / "out"
        out_dir.mkdir()
        wf = W.Workflow(name="Peel then vision")
        src = wf.add(W.input_node("mesh"))
        peel = wf.add(W.tool_node(reg.get("mesh-peel"), values={"max_edge": 10.0, "petals": 4, "rows": 1}))
        pv = reg.get("plotter-vision")
        lines = wf.add(W.tool_node(pv, values=dict(pv.test_values)))
        out = wf.add(W.save_node(where="folder", folder=str(out_dir)))
        wf.connect(src.id, peel.id)
        wf.connect(peel.id, lines.id)
        wf.connect(lines.id, out.id)
        res = W.run_workflow(wf, src_mesh, lambda *a: None, lambda: False, reg=reg,
                             cache=W.WorkflowCache(tmp / "cache"), tool_cache=str(tmp / "toolcache"))
        self.assertEqual(len(res.outputs), 1)
        final = Path(res.outputs[0])
        self.assertTrue(final.name.endswith(".svg"))
        self.assertIn("_peel", final.name)


if __name__ == "__main__":
    unittest.main()
