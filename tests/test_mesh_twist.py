"""Twist: the field against the Blender pieces' node graph, stage ramps, frames and published stage sets."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import json
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
from atlas_toolkit.geom import stages
from atlas_toolkit.tools.mesh_twist import TOOL, engine
from atlas_toolkit.tools.mesh_twist import mesh_twist as mt


def context(mode="run"):
    return JobContext(lambda *_: None, lambda: False, mode)


def node_graph(p, n, sway_amp, sway_freq, twist_amp, twist_freq, ripple_amp, ripple_freq, lobes, fade, phase):
    """build_wave_group of head_plot_pipeline.py, one point at a time (sway_axis +X, depth_axis +Y: face at -Y)."""
    x, y, z = p
    grow = z * 0.35 + 0.85
    ph = z * sway_freq + phase
    sway = math.sin(ph) * sway_amp * grow
    depth = math.cos(ph + 0.7) * sway_amp * 0.35
    off = [sway, depth, 0.0]
    ang = math.sin(z * twist_freq + phase) * twist_amp
    rx, ry = x * math.cos(ang) - y * math.sin(ang), x * math.sin(ang) + y * math.cos(ang)
    off[0] += rx - x
    off[1] += ry - y
    theta = math.atan2(y, x)
    ripple = math.sin(z * ripple_freq + theta * lobes) * ripple_amp
    if fade > 0:
        f = min(math.hypot(x, y) / fade, 1.0)
        ripple *= f * f
    return [off[i] + n[i] * ripple for i in range(3)]


class FieldTests(unittest.TestCase):
    def test_matches_the_node_graph(self):
        rng = np.random.default_rng(7)
        C = rng.uniform(-1, 1, size=(300, 3)) * [0.7, 0.9, 1.0]
        N = rng.normal(size=(300, 3))
        N /= np.linalg.norm(N, axis=1, keepdims=True)
        args = dict(sway_freq=3.0, turn_freq=2.2, ripple_freq=20.0, lobes=3.0, fade=0.3, phase=0.4)
        ours = mt.offsets(C, N, 0.24, 0.62, 0.045, ripple_dir="normal", **args)
        theirs = np.array([node_graph(c, nn, 0.24, 3.0, 0.62, 2.2, 0.045, 20.0, 3.0, 0.3, 0.4)
                           for c, nn in zip(C, N)])
        np.testing.assert_allclose(ours, theirs, atol=1e-12)

    def test_stage_ramp_reproduces_the_v17_sequence(self):
        p = _presets.values(TOOL, "Torsion sequence")
        expect = {1 / 3: (0.072, 0.19, 0.014), 2 / 3: (0.15, 0.38, 0.028), 1.0: (0.24, 0.62, 0.045)}
        for u, (s, t, r) in expect.items():
            a = mt.amplitudes(p, u)
            self.assertAlmostEqual(a["sway"], s, delta=1e-4)
            self.assertAlmostEqual(a["turn"], t, delta=1e-4)
            self.assertAlmostEqual(a["ripple"], r, delta=1e-4)
        z = mt.amplitudes(p, 0.0)
        self.assertEqual((z["sway"], z["turn"], z["ripple"]), (0.0, 0.0, 0.0))
        sh = mt.shape(p)
        self.assertAlmostEqual(sh["sway_freq"], 3.0, places=3)
        self.assertAlmostEqual(sh["turn_freq"], 2.2, places=3)
        self.assertAlmostEqual(sh["ripple_freq"], 20.0, places=3)
        self.assertAlmostEqual(sh["phase"], 0.4, places=4)

    def test_turn_keeps_height_and_radius_and_radial_ripple_keeps_height(self):
        C = np.random.default_rng(1).uniform(-1, 1, size=(200, 3))
        off = mt.offsets(C, None, 0.0, 0.8, 0.0)
        D = C + off
        np.testing.assert_allclose(D[:, 2], C[:, 2])
        np.testing.assert_allclose(np.hypot(D[:, 0], D[:, 1]), np.hypot(C[:, 0], C[:, 1]), atol=1e-12)
        off = mt.offsets(C, None, 0.0, 0.0, 0.05, ripple_dir="radial")
        np.testing.assert_allclose(off[:, 2], 0.0)
        self.assertGreater(np.abs(off).max(), 0.01)

    def test_spine_pivot_follows_a_leaning_part(self):
        z = np.linspace(-1, 1, 400)
        lean = np.column_stack([0.5 * (z + 1), np.zeros_like(z), z])      # a centre line leaning in +X
        ring = np.concatenate([lean + [0.05 * math.cos(a), 0.05 * math.sin(a), 0] for a in np.linspace(0, 6.2, 8)])
        about_spine = mt.offsets(ring, None, 0.0, 0.8, 0.0, pivot="spine")
        about_axis = mt.offsets(ring, None, 0.0, 0.8, 0.0, pivot="centre")
        self.assertLess(np.abs(about_spine).max(), 0.2 * np.abs(about_axis).max())


class EngineTests(unittest.TestCase):
    def setUp(self):
        registry()
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_twist_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        m = trimesh.creation.capsule(height=60.0, radius=15.0, count=[24, 24])
        self.src = self.folder / "pillar.stl"
        m.export(self.src)

    def test_amount_zero_is_the_input(self):
        vals = TOOL.values(dict(amount=0.0, subdiv=0))
        res = engine.run(context(), self.src, None, vals)
        mesh = gm.load_mesh(self.src)
        v = res.preview["views"][-1]["mesh"]
        np.testing.assert_allclose(np.sort(v.vertices, axis=0), np.sort(mesh.vertices, axis=0), atol=1e-4)

    def test_stage_set_is_published_with_its_frame(self):
        vals = TOOL.values(dict(stages=3, subdiv=0, up_axis="+y", front="+z"))
        dst = output_path(TOOL, self.src, vals)
        res = engine.run(context(), self.src, dst, vals)
        self.assertEqual(len(res.outputs), 4)
        folder = Path(res.outputs[1]).parent
        man = json.loads((folder / stages.MANIFEST).read_text())
        self.assertEqual(man["frame"]["up"], "+y")
        self.assertEqual(len(man["stages"]), 3)
        for o in res.outputs:
            self.assertIn(b"atlas-toolkit:mesh-twist", Path(o).read_bytes()[:80])
        first = trimesh.load(res.outputs[1], process=False)
        orig = trimesh.load(self.src, process=False)
        np.testing.assert_allclose(first.bounds, orig.bounds, atol=1e-3)     # stage 0 is intact, in place
        last = trimesh.load(res.outputs[-1], process=False)
        # twisted about Y (the up axis): heights along Y keep their range
        self.assertAlmostEqual(last.bounds[0][1], orig.bounds[0][1], delta=1e-3)
        self.assertAlmostEqual(last.bounds[1][1], orig.bounds[1][1], delta=1e-3)
        self.assertFalse(np.allclose(last.vertices, first.vertices))

    def test_progression_and_subdivision_budget(self):
        vals = TOOL.values(dict(stages=2, layout="progression", subdiv=1))
        res = engine.run(context(), self.src, None, vals)
        self.assertEqual(res.preview["prefer"], "Progression")
        V, F, done = mt.subdivide(np.zeros((3, 3)) + [[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.array([[0, 1, 2]]), 3,
                                  budget=20)
        self.assertEqual(done, 2)          # 1 -> 4 -> 16 faces fit 20, 64 would not

    def test_dry_runs_write_nothing(self):
        before = set(self.folder.iterdir())
        vals = TOOL.values(dict(stages=2))
        dst = output_path(TOOL, self.src, vals)
        engine.run(context("draft"), self.src, dst, vals)
        engine.run(context("analyse"), self.src, dst, vals)
        self.assertEqual(set(self.folder.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
