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
from atlas_toolkit.geom.solid_connectors import file_safe
from atlas_toolkit.tools.mesh_motion import TOOL
from atlas_toolkit.tools.mesh_motion.engine import run, make_sample
from atlas_toolkit.tools.mesh_motion.mesh_motion import sculpt, sculpt_within, wake, basis


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

    def test_a_hollow_within_one_layer_is_swept_with_the_piece_around_it(self):
        """Human skull refused Quiet acceleration, Held portrait and Shattered slipstream: a hollow that a layer closes
        off is a shell of its own, inside out, and was taken for a piece that did not stay closed."""
        bubble = trimesh.creation.icosphere(subdivisions=2, radius=.08)
        bubble.apply_translation((0., 0., .17))                          # inside layer 4 of 6
        bubble.invert()
        self.mesh = trimesh.creation.icosphere(subdivisions=3)
        plain, plain_info = self.sculpt()
        rest = dict(reach=0., curl=0., taper=0., gap=0.)
        solid_volume = self.sculpt(**rest)[0].volume
        self.mesh = trimesh.util.concatenate((self.mesh, bubble))
        still, _ = self.sculpt(**rest)
        self.assertAlmostEqual(still.volume, solid_volume + bubble.volume, places=9)
        out, info = self.sculpt(curl=20.)
        self.assertEqual(info["pieces"], plain_info["pieces"])           # a wall is not a piece
        self.assertTrue(out.is_watertight and out.is_winding_consistent)
        shells = out.split(only_watertight=False)
        wall = [s for s in shells if s.volume < 0]
        self.assertEqual(len(wall), 1)
        self.assertTrue(any(s.volume > 0 and s.contains(wall[0].vertices).all() for s in shells))
        out, _ = self.sculpt(structure="splinters", lanes=3)             # the middle bar holds it
        self.assertEqual(sum(s.volume < 0 for s in out.split(only_watertight=False)), 1)

    def test_a_hollow_wall_goes_with_the_smallest_shell_that_holds_it(self):
        from atlas_toolkit.geom import cutting

        def ball(radius, x, inside_out=False):
            mesh = trimesh.creation.icosphere(subdivisions=2, radius=radius)
            mesh.apply_translation((x, 0., 0.))
            if inside_out:
                mesh.invert()
            return mesh
        big, small, wall, loose = ball(1., 0.), ball(.4, .2), ball(.1, .2, True), ball(.1, 5., True)
        pieces = cutting.solid_pieces(trimesh.util.concatenate((big, small, wall, loose)))
        self.assertEqual(sorted(round(piece.volume, 9) for _, piece in pieces),
                         sorted((round(small.volume + wall.volume, 9), round(big.volume, 9))))
        both = trimesh.util.concatenate((big, small))
        for (_, piece), shell in zip(cutting.solid_pieces(both), both.split(only_watertight=False), strict=True):
            np.testing.assert_array_equal(piece.vertices, shell.vertices)
            np.testing.assert_array_equal(piece.faces, shell.faces)

    def test_a_piece_that_crosses_itself_is_swept_and_one_turned_inside_out_is_not(self):
        """Human skull refused Glass runner: a piece crossing itself is inside out in part, and the wake made that part
        the larger one."""
        from unittest import mock
        from atlas_toolkit.geom import cutting
        from atlas_toolkit.tools.mesh_motion import mesh_motion
        # A bow tie, two blocks meeting in a line where the walls cross: the trailing one is inside out and smaller.
        outline = np.array([[1., -.5], [1., .5], [-.8, -.4], [-.8, .4]])
        v = np.vstack([np.column_stack((outline, np.full(4, z))) for z in (-.3, .3)])
        f = [(0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
             (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7)]
        tie = trimesh.Trimesh(v, f, process=False)
        self.assertTrue(tie.is_watertight and tie.is_winding_consistent and tie.volume > 0)
        self.assertTrue(cutting.folds_through_itself(tie))
        self.assertFalse(cutting.folds_through_itself(trimesh.creation.torus(1., .3)))
        # A long wake makes the inside-out part the larger one.
        values = TOOL.values(dict(layers=2, resolution=8, hold_base=0., variation=0., gesture="stream", curl=0.,
                                  taper=0., reach=120., preserve=40.))
        with mock.patch.object(mesh_motion, "partition", lambda mesh, *a, **k: [(0, mesh)]):
            out, info = sculpt(tie, values, context())
            self.assertLess(out.volume, 0)
            self.assertTrue(out.is_watertight and out.is_winding_consistent)
            with mock.patch.object(cutting, "folds_through_itself", return_value=False),                     self.assertRaises(mesh_motion.OpenPiece):
                sculpt(tie, values, context())

    def test_region_can_disable_sweep(self):
        a, _ = self.sculpt(influence="sphere", sphere_centre=(100., 100., 100.), sphere_radius=1., feather=0.)
        b, _ = self.sculpt(reach=0., taper=0., curl=0.)
        np.testing.assert_allclose(a.bounds, b.bounds)

    def test_painted_area_is_gone_and_old_settings_act_on_the_whole_model(self):
        # 0.5.0 removed Painted area. A preset, history entry or workflow saved with it still runs, on the whole model.
        choices = [value for value, _ in TOOL.param("influence").choices]
        self.assertNotIn("paint", choices)
        old = TOOL.values(dict(influence="paint", paint_style="layers", selection="{}", layers=6, resolution=4))
        self.assertEqual(old.influence, "all")

    def test_face_budget_and_cancellation_fail_before_publication(self):
        self.mesh = trimesh.creation.icosphere(subdivisions=3)
        with self.assertRaisesRegex(ValueError, "Maximum faces"):
            self.sculpt(layers=12, resolution=48, max_faces=10000)
        with self.assertRaises(Cancelled):
            sculpt(self.mesh, TOOL.values(self.values), context(cancel=True))

    def test_simplify_to_fit_sculpts_a_lighter_copy_only_when_needed(self):
        """Every preset failed on catalogue models of 259k faces and more: the caps of dense sections multiply under
        the wake. Simplify to fit retries on a closed copy with half the faces until the result fits."""
        dense = trimesh.creation.icosphere(subdivisions=5)                  # 20,480 faces: 70,906 sculpted
        dense.apply_scale((.5, .4, 1.))
        p = TOOL.values({**self.values, "layers": 12, "resolution": 12, "max_faces": 65000})
        out, _, worked = sculpt_within(dense, p, context())
        self.assertEqual(worked, 10240)
        self.assertLessEqual(len(out.faces), 65000)
        self.assertTrue(out.is_watertight and out.is_winding_consistent and out.volume > 0)
        with self.assertRaisesRegex(ValueError, "Maximum faces"):
            sculpt_within(dense, TOOL.values({**p, "fit": False}), context())
        light, _ = self.sculpt()
        same, _, worked = sculpt_within(self.mesh, TOOL.values(self.values), context())
        self.assertIsNone(worked)
        np.testing.assert_array_equal(same.vertices, light.vertices)

    def test_a_blade_thinner_than_the_wakes_edges_follow_is_swept_finer(self):
        """Shattered slipstream refused Doryphoros, Laocoön and Discobolus for one sliver the curl turned inside out."""
        from unittest import mock
        from atlas_toolkit.tools.mesh_motion import mesh_motion
        values = TOOL.values(dict(layers=2, resolution=4, hold_base=0., variation=0., gesture="stream", curl=40.))
        for seed in (1, 3, 6):
            with self.subTest(seed=seed):
                blade = trimesh.convex.convex_hull(np.random.default_rng(seed).random((8, 3)) * [1, .004, .4])
                with mock.patch.object(mesh_motion, "FINER", (1,)), self.assertRaises(mesh_motion.OpenPiece):
                    sculpt(blade, values, context())
                out, _ = sculpt(blade, values, context())
                self.assertTrue(all(piece.is_volume for piece in out.split(only_watertight=False)))
        with mock.patch.object(mesh_motion, "FINER", (1,)):
            plain, _ = self.sculpt()
        same, _ = self.sculpt()                                  # nothing turned inside out: nothing changes
        np.testing.assert_array_equal(plain.vertices, same.vertices)

    def test_a_shell_float32_would_open_is_made_safe_and_the_others_kept_exactly(self):
        """Shattered slipstream on Thinker saved a shell with a hole: float32 flattened a few of its triangles."""
        good = trimesh.creation.icosphere(subdivisions=2, radius=1000.)
        good.apply_translation((3000., 3000., 9000.))
        v = (trimesh.creation.icosphere(subdivisions=2, radius=1000.).vertices + 3000.).astype(np.float32).astype(float)
        f = good.faces
        a, b = f[0, 0], f[0, 1]
        on = [i for i, t in enumerate(f) if a in t and b in t]
        v = np.vstack((v, v[a] + (v[b] - v[a]) / np.linalg.norm(v[b] - v[a]) * 1e-5))     # welds with a in float32
        faces = [list(t) for i, t in enumerate(f) if i not in on]
        for i in on:
            first, second = list(f[i]), list(f[i])
            first[first.index(b)], second[second.index(a)] = len(v) - 1, len(v) - 1
            faces += [first, second]
        fragile = trimesh.Trimesh(v, faces, process=False)
        both = trimesh.util.concatenate((good, fragile))
        out, safe, unsafe = file_safe(both)
        self.assertEqual((safe, unsafe), (1, 0))
        n = len(good.vertices)
        np.testing.assert_array_equal(out.vertices[:n], good.vertices)
        np.testing.assert_array_equal(out.faces[:len(good.faces)], good.faces)
        stored = gm.clean(trimesh.Trimesh(out.vertices.astype(np.float32), out.faces.copy(), process=False))
        self.assertEqual(len(stored.faces), len(out.faces))
        self.assertTrue(stored.is_watertight)
        from unittest import mock
        from atlas_toolkit.geom import solid_connectors
        with mock.patch.object(solid_connectors, "stabilize", side_effect=ValueError("not as it is")):
            out, safe, unsafe = file_safe(both)              # the copy the file holds, when that one is closed
        self.assertEqual((safe, unsafe), (1, 0))
        stored = gm.clean(trimesh.Trimesh(out.vertices.astype(np.float32), out.faces.copy(), process=False))
        self.assertTrue(stored.is_volume)
        self.assertAlmostEqual(stored.volume / both.volume, 1., places=6)
        same, safe, _ = file_safe(good)
        self.assertIs(same, good)
        self.assertEqual(safe, 0)

    def test_a_cavity_wall_stays_inside_out(self):
        outer, inner = trimesh.creation.icosphere(radius=10.), trimesh.creation.icosphere(radius=5.)
        inner.invert()
        hollow = trimesh.util.concatenate((outer, inner))
        same, safe, unsafe = file_safe(hollow)
        self.assertIs(same, hollow)
        self.assertEqual((safe, unsafe), (0, 0))

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
            run(context(), src, None, TOOL.values(dict(layers=6, resolution=4)))   # a pin-hole is tidied
            z = mesh.triangles_center[:, 2]
            mesh.update_faces(z < z.max() - 0.15 * np.ptp(z))                  # a real opening is not
            mesh.export(src)
            with self.assertRaisesRegex(ValueError, "Repair Mesh"):
                run(context(), src, None, TOOL.values())


if __name__ == "__main__":
    unittest.main()
