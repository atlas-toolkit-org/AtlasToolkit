"""Motion geometry invariants and the real export/preview contract."""
import _isolate  # first: settings and caches stay in a temporary folder
import tempfile
import unittest
from pathlib import Path
import numpy as np
import trimesh
from atlas_toolkit.core.job import JobContext, Cancelled
from atlas_toolkit.core.selftest import signed
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_motion import TOOL
from atlas_toolkit.tools.mesh_motion.engine import run, make_sample
from atlas_toolkit.tools.mesh_motion.mesh_motion import sculpt, wake, basis


def context(mode="run", cancel=False):
    return JobContext(lambda *_: None, lambda: cancel, mode=mode)


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.mesh = trimesh.creation.icosphere(subdivisions=1)
        self.mesh.apply_scale((.5, .4, 1.))
        self.values = dict(layers=6, resolution=4, hold_base=0., gesture="stream", variation=0.)

    def sculpt(self, **kw):
        return sculpt(self.mesh, TOOL.values({**self.values, **kw}), context())

    def test_zero_amount_is_exact_and_does_not_change_source(self):
        before = self.mesh.vertices.copy()
        out, _ = self.sculpt(amount=0.)
        np.testing.assert_array_equal(out.vertices, before)
        np.testing.assert_array_equal(out.faces, self.mesh.faces)
        self.sculpt()
        np.testing.assert_array_equal(self.mesh.vertices, before)

    def test_no_motion_preserves_volume_after_partition(self):
        out, info = self.sculpt(reach=0., taper=0., curl=0., gap=0.)
        self.assertAlmostEqual(out.volume, self.mesh.volume, places=9)
        np.testing.assert_allclose(out.bounds, self.mesh.bounds, atol=1e-10)
        self.assertEqual(info["layers"], 6)

    def test_all_gestures_and_axes_are_closed_positive_solids(self):
        for gesture in ("stream", "pulse", "gust", "vortex"):
            for axis in ("up", "front", "side"):
                with self.subTest(gesture=gesture, axis=axis):
                    out, _ = self.sculpt(gesture=gesture, layer_axis=axis, tilt=21.)
                    self.assertTrue(out.is_watertight)
                    self.assertTrue(out.is_winding_consistent)
                    self.assertTrue(np.isfinite(out.vertices).all())
                    self.assertTrue(all(part.volume > 0 for part in out.split()))

    def test_splinters_split_source_and_keep_closed(self):
        out, info = self.sculpt(structure="splinters", lanes=4)
        self.assertGreater(info["pieces"], 6)
        self.assertTrue(out.is_watertight)

    def test_same_seed_repeats_and_different_seed_changes_wake(self):
        a, _ = self.sculpt(variation=100., seed=17)
        b, _ = self.sculpt(variation=100., seed=17)
        c, _ = self.sculpt(variation=100., seed=18)
        np.testing.assert_array_equal(a.vertices, b.vertices)
        np.testing.assert_array_equal(a.faces, b.faces)
        self.assertFalse(np.allclose(a.vertices, c.vertices))

    def test_heading_reverses_the_trailing_direction(self):
        left, _ = self.sculpt(heading=0., curl=0.)
        right, _ = self.sculpt(heading=180., curl=0.)
        self.assertLess(left.bounds[0, 0], self.mesh.bounds[0, 0]-.1)
        self.assertGreater(right.bounds[1, 0], self.mesh.bounds[1, 0]+.1)
        self.assertLessEqual(left.bounds[1, 0], self.mesh.bounds[1, 0]+1e-10)

    def test_leading_vertices_fixed_and_wake_monotone(self):
        vertices = np.column_stack((np.linspace(-1, 1, 101), np.ones(101)*.1, np.zeros(101)))
        h, side, n = np.eye(3)
        out = wake(vertices, h, side, n, np.zeros(3), 2., .3, .95, .4)
        np.testing.assert_allclose(out[vertices[:, 0] >= .2], vertices[vertices[:, 0] >= .2])
        self.assertTrue(np.all(np.diff(out[:, 0]) > 0))
        self.assertAlmostEqual(out[0, 0], -3.)

    def test_curl_stays_inside_layer_intervals(self):
        out, _ = self.sculpt(curl=60., taper=95., gap=30.)
        for piece in out.split():
            self.assertLess(piece.extents[2], 2/6*.701)

    def test_annular_sections_keep_holes(self):
        self.mesh = trimesh.creation.annulus(r_min=.35, r_max=.7, height=2., sections=24)
        out, _ = self.sculpt(reach=0., curl=0., taper=0., gap=0.)
        self.assertAlmostEqual(out.volume, self.mesh.volume, places=8)
        self.assertTrue(out.is_watertight)

    def test_region_can_disable_sweep(self):
        a, _ = self.sculpt(influence="sphere", sphere_centre=(100., 100., 100.), sphere_radius=1., feather=0.)
        b, _ = self.sculpt(reach=0., taper=0., curl=0.)
        np.testing.assert_allclose(a.bounds, b.bounds)

    def test_face_budget_and_cancellation_fail_before_publication(self):
        self.mesh = trimesh.creation.icosphere(subdivisions=3)
        with self.assertRaisesRegex(ValueError, "Maximum faces"):
            self.sculpt(layers=12, resolution=48, max_faces=10000)
        with self.assertRaises(Cancelled):
            sculpt(self.mesh, TOOL.values(self.values), context(cancel=True))

    def test_basis_is_orthonormal(self):
        for axis in ("up", "front", "side"):
            n, a, b = basis(axis, -37.)
            np.testing.assert_allclose(np.array([a, b, n]) @ np.array([a, b, n]).T, np.eye(3), atol=1e-12)
            np.testing.assert_allclose(np.cross(a, b), n)

    def test_export_formats_source_protection_and_draft_parity(self):
        with tempfile.TemporaryDirectory() as temp:
            src = make_sample(temp)
            source_bytes = src.read_bytes()
            p = TOOL.values(self.values)
            final = run(context(), src, None, p)
            draft = run(context("draft"), src, Path(temp)/"forbidden.stl", p)
            np.testing.assert_array_equal(final.preview["views"][0]["mesh"].vertices,
                                          draft.preview["views"][0]["mesh"].vertices)
            self.assertFalse((Path(temp)/"forbidden.stl").exists())
            for ext in ("stl", "obj", "ply"):
                dst = Path(temp)/f"motion.{ext}"
                result = run(context(), src, dst, p)
                self.assertTrue(signed(TOOL, dst))
                loaded = gm.load_mesh(dst)
                self.assertTrue(loaded.is_watertight)
                self.assertEqual(result.outputs, [str(dst)])
            with self.assertRaisesRegex(ValueError, "replace"):
                run(context(), src, src, p)
            self.assertEqual(src.read_bytes(), source_bytes)

    def test_open_input_and_reversed_band_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            src = make_sample(temp)
            with self.assertRaisesRegex(ValueError, "Band from"):
                run(context(), src, None, TOOL.values(dict(influence="band", band_from=80., band_to=20.)))
            mesh = gm.load_mesh(src)
            mesh.update_faces(np.arange(len(mesh.faces)-1))
            mesh.export(src)
            with self.assertRaisesRegex(ValueError, "closed"):
                run(context(), src, None, TOOL.values())


if __name__ == "__main__":
    unittest.main()
