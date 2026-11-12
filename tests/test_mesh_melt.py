"""Melt: the mapping onto the Blender pieces' parameters, the Blender worker protocol (against a fake server, so it
runs anywhere), process cleanup, and real Blender runs where Blender is installed."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.io import output_path
from atlas_toolkit.core.job import Cancelled, JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom import blender as gb
from atlas_toolkit.geom import stages
from atlas_toolkit.tools.mesh_melt import TOOL, engine
from atlas_toolkit.tools.mesh_melt import mesh_melt as mm

FAKE = Path(__file__).parent / "fixtures" / "fake_blender_server.py"
HAVE_BLENDER = gb.find_blender() is not None


def context(mode="run", cancelled=lambda: False):
    return JobContext(lambda *_: None, cancelled, mode)


def fake_client(**env):
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        return gb.BlenderClient(argv=[sys.executable, "-u", str(FAKE)], startup_timeout=30)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class MappingTests(unittest.TestCase):
    def test_v17_preset_maps_exactly_onto_the_sequence(self):
        p = _presets.values(TOOL, "Dissolution sequence")
        for u, stage in zip((1 / 3, 2 / 3, 1.0), mm.STAGES):
            q = mm.resolve(p, u, mm.E_REF, None)
            want = {**mm.MELT_DEFAULTS, **mm.COMMON, **stage}
            for k, v in want.items():
                if k in ("subdiv", "face_dir"):
                    continue
                got = q[k]
                with self.subTest(u=round(u, 3), key=k):
                    if isinstance(v, (list, tuple)):
                        np.testing.assert_allclose(got, v, atol=1e-12)
                    elif isinstance(v, float):
                        self.assertAlmostEqual(got, v, places=12)
                    else:
                        self.assertEqual(got, v)

    def test_trajectory_grows_magnitudes_and_holds_shapes(self):
        a, b = mm.trajectory(1 / 6), mm.trajectory(1 / 3)
        self.assertAlmostEqual(a["slump"], b["slump"] / 2)
        self.assertAlmostEqual(a["drip_amount"], b["drip_amount"] / 2)
        self.assertEqual(a["puddle_flatness"], b["puddle_flatness"])
        mid = mm.trajectory(5 / 6)
        self.assertAlmostEqual(mid["dissolve"], (0.15 + 0.8) / 2)

    def test_objects_have_no_face_terms_and_density_sets_passes(self):
        p = TOOL.values(dict(head=False))
        q = mm.resolve(p, 2 / 3, mm.E_REF / 2, None)
        for k in mm.FACE_TERMS:
            self.assertEqual(q[k], 0.0)
        self.assertFalse(q["head"])
        ref = mm.resolve(p, 2 / 3, mm.E_REF, None)
        self.assertEqual(q["soften"], 4 * ref["soften"])             # half the edge: four times the passes
        self.assertEqual(q["dissolve_smooth"], ref["dissolve_smooth"])  # on the voxel proxy: unchanged
        soft = mm.resolve(TOOL.values(dict(head=False, soften=2.0)), 2 / 3, mm.E_REF, None)
        self.assertEqual(soft["soften"], 4 * ref["soften"])

    def test_head_zones_measure_the_head(self):
        rng = np.random.default_rng(2)
        V = rng.uniform(-1, 1, size=(5000, 3)) * [mm.REF_SIDE, mm.REF_FRONT, 1.0]
        z = mm.head_zones(V)
        self.assertAlmostEqual(z["zone_front"], 1.0, delta=0.01)
        self.assertAlmostEqual(z["zone_side"], 1.0, delta=0.01)
        self.assertEqual((z["head_centre"], z["head_gain"], z["head_x"], z["head_y"]), (0.0, 1.0, 0.0, 0.0))
        bust = mm.head_zones(V, (50.0, 100.0))
        self.assertAlmostEqual(bust["head_centre"], 0.5)
        self.assertAlmostEqual(bust["head_gain"], 2.0)


class ThicknessTests(unittest.TestCase):
    def test_thin_models_get_thinner_drips(self):
        ball = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
        rod = trimesh.creation.capsule(height=2.0, radius=0.12, count=[24, 24])
        tb = mm.thickness(ball.vertices, ball.faces)
        tr = mm.thickness(rod.vertices, rod.faces)
        self.assertGreater(tb, 4 * tr)
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "rod.stl"
            rod.export(src)
            info = mm.inspect(src)
            self.assertLess(info.values["drip_size_auto"], 0.5)
            self.assertGreaterEqual(info.values["drip_size_auto"], 0.35)


class ProtocolTests(unittest.TestCase):
    def tearDown(self):
        c = getattr(self, "c", None)
        if c is not None:
            c.kill()

    def test_handshake_noise_and_requests(self):
        self.c = fake_client()
        self.assertEqual(self.c.version[:2], (5, 2))
        self.assertTrue(self.c.request({"op": "ping"})["pong"])
        seen = []
        r = self.c.request({"op": "slow", "steps": 5}, on_progress=lambda f, m: seen.append(m))
        self.assertEqual(r["steps"], 5)
        self.assertEqual(len(seen), 5)
        self.assertTrue(any("banner" in n or "untagged" in n for n in self.c.noise))

    def test_npz_round_trip(self):
        self.c = fake_client()
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.npz", Path(d) / "b.npz"
            np.savez(a, V=np.ones((3, 3)), F=np.array([[0, 1, 2]]))
            self.c.request({"op": "copy", "mesh": str(a), "out": str(b)})
            with np.load(b) as z:
                np.testing.assert_allclose(z["V"], 1.0)

    def test_errors_carry_the_stderr_tail(self):
        self.c = fake_client()
        with self.assertRaises(gb.BlenderError) as cm:
            self.c.request({"op": "fail"})
        self.assertIn("bad mesh", str(cm.exception))
        self.assertTrue(self.c.alive())
        with self.assertRaises(gb.BlenderError) as cm:
            self.c.request({"op": "die"})
        self.assertIn("exit code 3", str(cm.exception))

    def test_old_blender_and_wrong_protocol_are_refused(self):
        with self.assertRaisesRegex(gb.BlenderError, "too old"):
            fake_client(FAKE_BLENDER_VERSION="3.6.0")
        with self.assertRaisesRegex(gb.BlenderError, "protocol"):
            fake_client(FAKE_PROTOCOL="9")

    def test_cancel_soft_then_hard(self):
        self.c = fake_client()
        flag = {"n": 0}

        def poll():
            flag["n"] += 1
            if flag["n"] > 3:
                raise Cancelled()

        with self.assertRaises(Cancelled):
            self.c.request({"op": "slow", "steps": 200}, poll=poll)
        self.assertTrue(self.c.alive())              # it stopped when asked
        flag["n"] = 0
        t0 = time.monotonic()
        with self.assertRaises(Cancelled):
            self.c.request({"op": "slow", "steps": 200, "stubborn": True}, poll=poll)
        self.c.proc.wait(2)
        self.assertFalse(self.c.alive())             # it did not: killed
        self.assertLess(time.monotonic() - t0, 2.5)

    def test_server_exits_when_stdin_closes(self):
        self.c = fake_client()
        self.c.proc.stdin.close()
        self.assertEqual(self.c.proc.wait(5), 0)

    def test_server_dies_with_its_host_process(self):
        code = ("import sys, time; sys.path.insert(0, r'%s'); from atlas_toolkit.geom import blender as gb; "
                "c = gb.BlenderClient(argv=[sys.executable, '-u', r'%s']); print(c.proc.pid, flush=True); "
                "time.sleep(60)") % (str(Path(gb.__file__).parents[2]), str(FAKE))
        host = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        try:
            pid = int(host.stdout.readline())
            self.assertFalse(_isolate.process_gone(pid, 0.2))
            host.kill()                           # Windows: its job object closes; elsewhere the server sees stdin end
            self.assertTrue(_isolate.process_gone(pid))
        finally:
            host.kill()


class FindTests(unittest.TestCase):
    def test_environment_wins(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "blender.exe"
            exe.write_text("")
            old = os.environ.get(gb.ENV)
            os.environ[gb.ENV] = str(exe)
            try:
                self.assertEqual(gb.find_blender(), exe)
            finally:
                if old is None:
                    os.environ.pop(gb.ENV, None)
                else:
                    os.environ[gb.ENV] = old


@unittest.skipUnless(HAVE_BLENDER, "Blender is not installed")
class BlenderTests(unittest.TestCase):
    def setUp(self):
        registry()
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_melt_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        m = trimesh.creation.icosphere(subdivisions=4, radius=30.0)
        m.vertices[:, 2] *= 1.3
        self.src = self.folder / "egg.stl"
        m.export(self.src)

    def test_two_stages_intact_then_melted_with_drips(self):
        vals = TOOL.values(dict(stages=2, amount=0.8, subdiv=0, head=False))
        dst = output_path(TOOL, self.src, vals)
        res = engine.run(context(), self.src, dst, vals)
        self.assertEqual(len(res.outputs), 3)
        for o in res.outputs:
            self.assertIn(b"atlas-toolkit:mesh-melt", Path(o).read_bytes()[:80])
        intact = trimesh.load(res.outputs[1], process=False)
        melted = trimesh.load(res.outputs[2], process=False)
        egg = trimesh.load(self.src, process=False)
        np.testing.assert_allclose(intact.bounds, egg.bounds, atol=1e-3)
        self.assertGreater(len(melted.faces), 2 * len(intact.faces))        # drip tubes
        self.assertLess(melted.bounds[1][2], egg.bounds[1][2])               # it sank
        man = json.loads((Path(res.outputs[1]).parent / stages.MANIFEST).read_text())
        self.assertIn("blender", man)

    def test_the_largest_seed_melts(self):
        # Blender's int sockets stop at 2^31 - 1, and the drips use seed + 202
        vals = TOOL.values(dict(stages=1, amount=0.5, subdiv=0, seed=2**31 - 1))
        res = engine.run(context(), self.src, output_path(TOOL, self.src, vals), vals)
        self.assertEqual(len(res.outputs), 1)

    def test_twist_matches_the_blender_node_graph(self):
        from atlas_toolkit.tools.mesh_twist import mesh_twist as mt
        m = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
        m.vertices[:, 2] *= 1.0 / m.extents[2] * 2.0
        folder = gb.scratch()
        a, b = folder / "wave_in.npz", folder / "wave_out.npz"
        np.savez(a, V=m.vertices, F=m.faces.astype(np.int32))
        params = dict(sway_amp=0.24, sway_freq=3.0, twist_amp=0.62, twist_freq=2.2, ripple_amp=0.0,
                      ripple_freq=20.0, ripple_lobes=3, ripple_axis_fade=0.3, phase=0.4,
                      sway_axis=[1, 0, 0], depth_axis=[0, 1, 0])
        try:
            gb.client().request({"op": "wave", "mesh": str(a), "out": str(b), "params": params})
            with np.load(b) as d:
                Vb = d["V"]
        finally:
            a.unlink(missing_ok=True)
            b.unlink(missing_ok=True)
        ours = m.vertices + mt.offsets(m.vertices, None, 0.24, 0.62, 0.0, sway_freq=3.0, turn_freq=2.2,
                                       ripple_freq=20.0, lobes=3, fade=0.3, phase=0.4)
        np.testing.assert_allclose(Vb, ours, atol=2e-5)          # Blender stores float32


if __name__ == "__main__":
    unittest.main()
