"""The canonical frame (geom/frame3d) and stage sets (geom/stages): axes, round trips, shared frames, publishing."""
import _isolate  # first: settings and caches stay in a temporary folder
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.job import Cancelled
from atlas_toolkit.geom import frame3d, stages
from atlas_toolkit.tools.plotter_vision import model as pm


class Ctx:
    def __init__(self, cancel=lambda: False):
        self.cancel = cancel

    def progress(self, *_):
        self.check_cancel()

    def check_cancel(self):
        if self.cancel():
            raise Cancelled()


class AxesTests(unittest.TestCase):
    def test_rotations_are_proper_and_map_up_and_front(self):
        for up in ("+x", "-x", "+y", "-y", "+z", "-z"):
            for front in ("+x", "-x", "+y", "-y", "+z", "-z"):
                with self.subTest(up=up, front=front):
                    R = frame3d.rotation(up, front)
                    self.assertAlmostEqual(np.linalg.det(R), 1.0)
                    np.testing.assert_allclose(R @ R.T, np.eye(3), atol=1e-12)
                    np.testing.assert_allclose(R @ frame3d.axis_vec(up), [0, 0, 1], atol=1e-12)
                    if abs(frame3d.axis_vec(up) @ frame3d.axis_vec(front)) < 0.5:
                        np.testing.assert_allclose(R @ frame3d.axis_vec(front), [0, -1, 0], atol=1e-12)

    def test_identity_for_z_up_front_minus_y(self):
        np.testing.assert_allclose(frame3d.rotation("+z", "-y"), np.eye(3), atol=1e-12)

    def test_extra_turn_is_xyz_euler_and_invertible(self):
        R = frame3d.euler_xyz((10, 20, 30))
        np.testing.assert_allclose(frame3d.euler_xyz(frame3d.euler_from_matrix(R)), R, atol=1e-9)
        # about X first: a 90 degree X turn takes +Y to +Z
        np.testing.assert_allclose(frame3d.euler_xyz((90, 0, 0)) @ [0, 1, 0], [0, 0, 1], atol=1e-12)

    def test_winding_is_kept(self):
        m = trimesh.creation.icosphere(subdivisions=1)
        for up, front in (("+y", "+z"), ("-x", "+y")):
            f = frame3d.fit_frame(m.vertices, up, front, (15, 0, 40))
            mc = trimesh.Trimesh(f.apply(m.vertices), m.faces, process=False)
            self.assertGreater(mc.volume, 0)

    def test_guess_follows_the_gallery_rule(self):
        self.assertEqual(frame3d.guess_axes((1, 1, 2), "stl"), ("+z", "-y"))
        self.assertEqual(frame3d.guess_axes((1, 2, 1), "obj"), ("+y", "+z"))
        up, front = frame3d.guess_axes((3, 1, 1.2), "stl")        # a statue stored lying along X
        self.assertEqual(up, "+x")


class FrameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_frame_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def box(self, name, extents, at=(0, 0, 0)):
        m = trimesh.creation.box(extents=extents)
        m.apply_translation(at)
        p = self.folder / name
        m.export(p)
        return p

    def test_round_trip_and_height_frame(self):
        V = np.random.default_rng(1).normal(size=(200, 3)) * [3, 5, 7] + [10, -4, 2]
        f = frame3d.fit_frame(V, "+y", "+x", (5, -10, 20))
        np.testing.assert_allclose(f.inverse(f.apply(V)), V, atol=1e-9)
        C = f.apply(V)
        self.assertAlmostEqual(C[:, 2].max() - C[:, 2].min(), 2.0)
        self.assertAlmostEqual(f.height, 2.0)
        np.testing.assert_allclose((C.max(0) + C.min(0)) / 2, 0, atol=1e-9)

    def test_drawing_frame_matches_plotter_vision(self):
        p = self.box("a.stl", (4, 2, 8), (1, 2, 3))
        f = frame3d.frame_for(p, {"up_axis": "+z", "front": "-y", "normalise": "model"})
        mesh = trimesh.load(p, process=False)
        a = pm.prepare(mesh, crossings=False)
        b = pm.prepare(mesh, crossings=False, frame=f)
        np.testing.assert_allclose(np.sort(a.V, axis=0), np.sort(b.V, axis=0), atol=1e-9)
        self.assertAlmostEqual(float(f.extent.max()), 100.0)

    def test_stage_set_shares_one_frame(self):
        folder = self.folder / "x_stages"
        folder.mkdir()
        small = self.box("x_stages/x_stage_00.stl", (2, 2, 4))
        big = self.box("x_stages/x_stage_01.stl", (6, 2, 4), (1, 0, -1))
        vals = {"up_axis": "auto", "front": "auto", "normalise": "set"}
        fa, fb = frame3d.frame_for(small, vals), frame3d.frame_for(big, vals)
        self.assertEqual(fa.key(), fb.key())
        self.assertIn("stage set", fa.note)
        own = frame3d.frame_for(small, dict(vals, normalise="model"))
        self.assertNotEqual(own.key(), fa.key())
        self.assertAlmostEqual(fa.scale, 100.0 / 6.0)          # the set's union is 6 wide
        self.assertAlmostEqual(own.scale, 100.0 / 4.0)
        ref = frame3d.frame_for(small, dict(vals, normalise="reference", reference_mesh=str(big)))
        self.assertAlmostEqual(ref.scale, 100.0 / 6.0)
        np.testing.assert_allclose(ref.centre, [1, 0, -1], atol=1e-9)

    def test_manifest_frame_wins_over_auto(self):
        folder = self.folder / "h_twist_stages"
        folder.mkdir()
        a = self.box("h_twist_stages/h_twist_stage_00.stl", (2, 4, 2))
        self.box("h_twist_stages/h_twist_stage_01.stl", (2, 4, 2))
        (folder / stages.MANIFEST).write_text(json.dumps({
            "atlas": "stage-set", "version": 1, "frame": {"up": "+y", "front": "+z", "rotate": [0, 0, 0]},
            "stages": [{"file": "h_twist_stage_00.stl", "index": 0}, {"file": "h_twist_stage_01.stl", "index": 1}]}))
        up, front, rot, source = frame3d.resolve_axes(a, {"up_axis": "auto", "front": "auto"})
        self.assertEqual((up, front, source), ("+y", "+z", "its stage set"))
        up, _, _, _ = frame3d.resolve_axes(a, {"up_axis": "+x", "front": "auto"})
        self.assertEqual(up, "+x")


class StageSetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_stages_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def test_amounts(self):
        self.assertEqual(stages.amounts(1, 0.7), [0.7])
        np.testing.assert_allclose(stages.amounts(4, 1.0), [0, 1 / 3, 2 / 3, 1])

    def test_regex_sets_need_two_siblings_of_one_extension(self):
        for name in ("m_stage_00.stl", "m_stage_02.stl", "m_stage_01.stl", "m_stage_03.obj", "n_stage_00.stl"):
            (self.folder / name).write_text("x")
        s = stages.stage_set(self.folder / "m_stage_01.stl")
        self.assertEqual([p.name for p in s.files], ["m_stage_00.stl", "m_stage_01.stl", "m_stage_02.stl"])
        self.assertIsNone(stages.stage_set(self.folder / "n_stage_00.stl"))
        self.assertIsNone(stages.stage_set(self.folder / "lonely.stl"))

    def test_publish_writes_manifest_and_files_all_or_nothing(self):
        dst = self.folder / "h_twist.stl"
        save = lambda item, path: Path(path).write_text(item)        # noqa: E731
        with stages.Publisher(Ctx(), dst, save, separate=True, prefix=".t-",
                              manifest={"tool": "mesh-twist", "frame": {"up": "+z"}}) as pub:
            for i in range(3):
                pub.add_stage(i, f"stage {i}", {"amount": i / 2})
            out = pub.commit("final")
        self.assertEqual(Path(out[0]).read_text(), "final")
        self.assertEqual(len(out), 4)
        man = json.loads((self.folder / "h_twist_stages" / stages.MANIFEST).read_text())
        self.assertEqual([s["file"] for s in man["stages"]], [f"h_twist_stage_{i:02d}.stl" for i in range(3)])
        self.assertEqual(man["atlas"], "stage-set")
        s = stages.stage_set(out[2])
        self.assertIsNotNone(s.manifest)
        # a cancel before commit leaves nothing behind
        before = set(self.folder.rglob("*"))
        flag = [False]
        with self.assertRaises(Cancelled):
            with stages.Publisher(Ctx(lambda: flag[0]), dst, save, separate=True, prefix=".t-") as pub:
                pub.add_stage(0, "a")
                flag[0] = True
                pub.add_stage(1, "b")
        self.assertEqual(set(self.folder.rglob("*")), before)


if __name__ == "__main__":
    unittest.main()
