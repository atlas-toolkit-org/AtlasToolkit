"""Repair Mesh and geom/repair.py: each kind of damage is found, fixed, and the safe tidy never moves the surface."""
import _isolate  # first: settings and caches stay in a temporary folder
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.errors import ToolError
from atlas_toolkit.core.job import Job, execute
from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom import repair as R


def sphere(sub=3):
    m = trimesh.creation.icosphere(subdivisions=sub, radius=10.0)
    return np.asarray(m.vertices, float), np.asarray(m.faces, np.int64)


def with_sheet(V, F):
    """Decimation debris as the catalogue had it: a triangle and its back-to-back twin (a sheet with no volume)
    standing on one of the body's edges."""
    a, b = F[5][0], F[5][1]
    tip = (V[a] + V[b]) / 2 * 1.02
    n = len(V)
    return np.vstack([V, tip]), np.vstack([F, [[a, b, n], [b, a, n]]])


def with_fin(V, F):
    """A third face on an edge (non-manifold): a sliver standing on edge (F[0][0], F[0][1])."""
    a, b = F[0][0], F[0][1]
    tip = (V[a] + V[b]) / 2 + 3.0 * (V[a] + V[b]) / np.linalg.norm(V[a] + V[b])
    return np.vstack([V, tip]), np.vstack([F, [[a, b, len(V)]]])


def with_hole(V, F, n=1):
    return V, np.delete(F, np.arange(n), axis=0)


def with_flipped_patch(V, F):
    F = F.copy()
    F[30:60] = F[30:60, ::-1]
    return V, F


def touching_spheres():
    """Two spheres sharing one vertex position welded into one: a bow-tie vertex."""
    V1, F1 = sphere(2)
    top = int(np.argmax(V1[:, 2]))
    V2 = V1.copy()
    V2[:, 2] = -V2[:, 2] + 2 * V1[top, 2]                # mirrored above, touching at the top vertex
    F2 = F1[:, ::-1] + len(V1)
    V = np.vstack([V1, V2])
    F = np.vstack([F1, F2])
    bottom2 = len(V1) + top
    F[F == bottom2] = top                                 # weld the touching vertices
    return V, F


def mobius():
    """A non-orientable strip (a Möbius band): no winding can agree everywhere."""
    n = 24
    V, F = [], []
    for i in range(n):
        t = 2 * np.pi * i / n
        for s in (-1, 1):
            w = 2.0 * s
            V.append([(10 + w * np.cos(t / 2)) * np.cos(t), (10 + w * np.cos(t / 2)) * np.sin(t), w * np.sin(t / 2)])
    for i in range(n):
        a, b = 2 * i, 2 * i + 1
        c, d = (2 * (i + 1)) % (2 * n), (2 * (i + 1) + 1) % (2 * n)
        if i == n - 1:
            c, d = d, c                                   # the half twist
        F += [[a, b, d], [a, d, c]]
    return np.asarray(V, float), np.asarray(F, np.int64)


class DiagnoseTests(unittest.TestCase):
    def test_clean_sphere(self):
        d = R.diagnose_arrays(*sphere())
        self.assertTrue(d.solid)
        self.assertTrue(d.manifold)
        self.assertEqual(d.problems(), [])

    def test_each_kind_of_damage_is_named(self):
        V, F = sphere()
        cases = {
            "zero-volume sheet": with_sheet(V, F),
            "non-manifold edge": with_fin(V, F),
            "open edge": with_hole(V, F, 2),
            "wound in opposite directions": with_flipped_patch(V, F),
            "bow-tie": touching_spheres(),
            "inside out": (V, F[:, ::-1]),
        }
        for words, (v, f) in cases.items():
            with self.subTest(words):
                d = R.diagnose_arrays(v, f)
                if words != "bow-tie":            # parts touching at a point still make a valid solid
                    self.assertFalse(d.solid)
                else:
                    self.assertFalse(d.manifold)
                self.assertIn(words, d.summary())

    def test_crumbs_counted(self):
        V, F = sphere()
        crumb = np.array([[30, 0, 0], [30.1, 0, 0], [30, 0.1, 0]], float)
        d = R.diagnose_arrays(np.vstack([V, crumb]), np.vstack([F, [[len(V), len(V) + 1, len(V) + 2]]]))
        self.assertEqual((d.pieces, d.crumbs, d.crumb_faces), (2, 1, 1))


class TidyTests(unittest.TestCase):
    """The safe tidy: fixes debris and topology, never moves an existing vertex."""

    def assert_tidy_solid(self, V, F):
        V2, F2, notes = R.tidy_arrays(V, F)
        self.assertTrue(R.is_solid(V2, F2), (R.diagnose_arrays(V2, F2).summary(), notes))
        # every vertex of the result is one of the input's (or a hair from an edge midpoint split)
        from scipy.spatial import cKDTree
        dist, _ = cKDTree(V).query(V2)
        self.assertLess(float(dist.max()), 1e-3, "tidy moved the surface")
        return notes

    def test_sheet(self):
        notes = self.assert_tidy_solid(*with_sheet(*sphere()))
        self.assertTrue(any("back-to-back" in n for n in notes) or any("non-manifold" in n for n in notes))

    def test_sheet_after_load_mesh_clean(self):
        """load_mesh drops one face of the pair: the lone fin left behind is tidied too (the catalogue's case)."""
        V, F = with_sheet(*sphere())
        m = trimesh.Trimesh(V, F, process=False)
        from atlas_toolkit.geom import mesh as gm
        gm.clean(m)
        self.assertFalse(R.is_solid(*R._arrays(m)))
        self.assert_tidy_solid(*R._arrays(m))

    def test_fin(self):
        self.assert_tidy_solid(*with_fin(*sphere()))

    def test_flipped_and_inside_out(self):
        self.assert_tidy_solid(*with_flipped_patch(*sphere()))
        V, F = sphere()
        self.assert_tidy_solid(V, F[:, ::-1])

    def test_pinhole(self):
        self.assert_tidy_solid(*with_hole(*sphere(), 1))

    def test_bowtie(self):
        self.assert_tidy_solid(*touching_spheres())

    def test_large_hole_is_left_to_repair(self):
        V, F = sphere()
        top = np.nonzero(V[F].mean(1)[:, 2] > 8)[0]           # a cap of many faces: a real opening
        V2, F2, _ = R.tidy_arrays(V, np.delete(F, top, axis=0))
        self.assertFalse(R.is_solid(V2, F2))

    def test_clean_input_untouched(self):
        V, F = sphere()
        V2, F2, notes = R.tidy_arrays(V, F)
        self.assertEqual(notes, [])
        self.assertEqual(len(F2), len(F))

    def test_a_simplified_solid_stays_a_solid(self):
        """What a tool that must work lighter than its input uses: decimation, then the tidy for what it pinches."""
        V, F = sphere()
        v, f = R.simplified_solid(V, F, len(F) // 4)
        self.assertLessEqual(len(f), len(F) // 4)
        self.assertTrue(R.is_solid(v, f))
        cube = trimesh.creation.box(extents=(12, 10, 40))
        other = cube.copy()
        other.apply_translation((12, 10, 0))              # an edge contact, as decimation leaves in thin drapery
        joined = trimesh.util.concatenate((cube, other))
        pinched = trimesh.Trimesh(joined.vertices, joined.faces)         # welded: four faces on the shared edge
        self.assertFalse(R.is_solid(pinched.vertices, pinched.faces))
        v, f = R.simplified_solid(pinched.vertices, pinched.faces, 1000)
        self.assertTrue(R.is_solid(v, f))
        self.assertIsNone(R.simplified_solid(np.eye(3), np.array([[0, 1, 2]]), 10))


class FlatSliverTests(unittest.TestCase):
    """refill_flat_slivers: a cut closed by a fan of slivers gets well-shaped triangles, without changing shape."""

    def test_fan_caps_refilled_in_place(self):
        from atlas_toolkit.geom.contact import crossings
        m = trimesh.creation.cylinder(radius=1.0, height=2.0, sections=128)
        V, F, count = R.refill_flat_slivers(m.vertices, m.faces, longer_than=0.2)
        self.assertEqual(count, 2)
        out = trimesh.Trimesh(V, F, process=False)
        self.assertTrue(out.is_watertight and out.is_winding_consistent)
        self.assertAlmostEqual(out.volume, m.volume, places=9)
        self.assertAlmostEqual(out.area, m.area, places=9)
        self.assertEqual(len(crossings(V, F)), 0)
        caps = np.abs(out.face_normals[:, 2]) > 0.999
        tri = V[F[caps]]
        self.assertLess(np.linalg.norm(tri - tri[:, [1, 2, 0]], axis=2).max(), 0.4)   # the fan's slivers were 2 long
        twice = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
        slim = np.linalg.norm(tri - tri[:, [1, 2, 0]], axis=2).max(1) ** 2 / twice
        before = m.triangles[np.abs(m.face_normals[:, 2]) > 0.999]
        was = np.linalg.norm(before - before[:, [1, 2, 0]], axis=2).max(1) ** 2 / np.linalg.norm(
            np.cross(before[:, 1] - before[:, 0], before[:, 2] - before[:, 0]), axis=1)
        self.assertLess(np.median(slim), 5)          # 1.15 for an equilateral triangle
        self.assertLess(np.median(slim), np.median(was) / 5)

    def test_fine_or_curved_surfaces_left_alone(self):
        sphere = trimesh.creation.icosphere(subdivisions=3)
        V, F, count = R.refill_flat_slivers(sphere.vertices, sphere.faces)
        self.assertEqual(count, 0)
        self.assertEqual(len(F), len(sphere.faces))
        m = trimesh.creation.cylinder(radius=1.0, height=2.0, sections=128)
        self.assertEqual(R.refill_flat_slivers(m.vertices, m.faces, longer_than=5.0)[2], 0)   # short enough already


class RepairTests(unittest.TestCase):
    def test_large_hole_filled_without_rebuild(self):
        V, F = sphere()
        top = np.nonzero(V[F].mean(1)[:, 2] > 7)[0]
        for shape in ("auto", "flat", "smooth"):
            with self.subTest(shape):
                V2, F2, notes, rebuilt = R.repair_arrays(V, np.delete(F, top, axis=0),
                                                         R.Settings(fill_shape=shape))
                self.assertTrue(R.is_solid(V2, F2), notes)
                self.assertFalse(rebuilt)

    def test_smooth_patch_bulges_with_the_surface(self):
        """A curved opening filled smooth stays near the sphere, flat cuts straight across."""
        V, F = sphere(4)
        cap = np.nonzero(V[F].mean(1)[:, 2] > 6)[0]
        out = {}
        for shape in ("flat", "smooth"):
            V2, F2, _, _ = R.repair_arrays(V, np.delete(F, cap, axis=0), R.Settings(fill_shape=shape))
            out[shape] = float(np.max(V2[:, 2]))
        self.assertGreaterEqual(out["smooth"], out["flat"])

    def test_all_damage_at_once(self):
        V, F = sphere()
        V, F = with_flipped_patch(V, F)
        V, F = with_hole(V, F, 3)
        V, F = with_fin(V, F)
        V, F = with_sheet(V, F)
        V2, F2, notes, rebuilt = R.repair_arrays(V, F)
        self.assertTrue(R.is_solid(V2, F2), notes)
        self.assertFalse(rebuilt)

    def test_non_orientable_needs_rebuild(self):
        V, F = mobius()
        V2, F2, notes, rebuilt = R.repair_arrays(V, F, R.Settings(strategy="repair"))
        self.assertFalse(R.is_solid(V2, F2))
        self.assertTrue(any("non-orientable" in n for n in notes), notes)

    def test_rebuild_closes_anything(self):
        V, F = sphere()
        V2, F2, notes, rebuilt = R.repair_arrays(V, F[: len(F) // 2], R.Settings(strategy="rebuild",
                                                                                   resolution=64))
        self.assertTrue(rebuilt)
        self.assertTrue(R.is_solid(V2, F2))

    def test_rebuild_comes_back_to_the_models_size_in_one_piece(self):
        """Marching cubes makes many more faces than the model had, specks and slivers: none of it survives."""
        V, F = sphere(4)
        V2, F2, notes, rebuilt = R.repair_arrays(V, F, R.Settings(strategy="rebuild", resolution=160))
        self.assertTrue(rebuilt)
        self.assertLessEqual(len(F2), 1.3 * max(len(F), 5000))
        d = R.diagnose_arrays(V2, F2)
        self.assertTrue(d.solid)
        self.assertEqual(d.pieces, 1)

    def test_keep_largest(self):
        V, F = sphere()
        V2, F2, _ = R.drop_pieces(np.vstack([V, V + 40]), np.vstack([F, F + len(V)]), "largest")
        self.assertEqual(len(F2), len(F))


class GuidanceTests(unittest.TestCase):
    def test_prepare_tidies_or_raises(self):
        V, F = with_sheet(*sphere())
        logged = []
        m, notes, _ = R.prepare(trimesh.Trimesh(V, F, process=False), "solid", log=logged.append)
        self.assertTrue(R.is_solid(*R._arrays(m)))
        self.assertTrue(notes and logged)
        self.assertTrue(any("Repair Mesh" in line for line in logged))
        V, F = sphere()
        cap = np.nonzero(V[F].mean(1)[:, 2] > 7)[0]
        with self.assertRaises(R.NeedsRepair) as cm:
            R.prepare(trimesh.Trimesh(V, np.delete(F, cap, axis=0), process=False), "solid",
                      tool_option="Repair by resampling", tool_name="Crumble")
        e = cm.exception
        self.assertIsInstance(e, ToolError)
        self.assertIsInstance(e, ValueError)          # the tools' callers still catch ValueError
        self.assertEqual(e.suggest_tool, "mesh-repair")
        self.assertIn("open edge", e.message)
        self.assertIn("Repair Mesh", e.hint)
        self.assertIn("Repair by resampling", e.hint)

    def test_clean_input_is_returned_as_is(self):
        m = trimesh.creation.icosphere()
        out, notes, d = R.prepare(m, "solid")
        self.assertIs(out, m)
        self.assertEqual(notes, [])

    def test_job_reports_a_tool_error_without_traceback(self):
        from atlas_toolkit.core import job as J

        class Fake:
            id = "fake"

            def engine_fn(self):
                def run(ctx, src, dst, p):
                    raise ToolError("not a solid", hint="Run Repair Mesh.", suggest_tool="mesh-repair")
                return run
        box = {}
        J.execute(Fake(), J.Job("fake", "run", "x.stl"), lambda k, p: box.__setitem__(k, p), lambda: False)
        text, trace, diag = box["error"]
        self.assertEqual(trace, "")
        self.assertIn("What to do: Run Repair Mesh.", text)
        self.assertEqual(diag["suggest_tool"], "mesh-repair")


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tool = registry().get("mesh-repair")
        self.dir = Path(tempfile.mkdtemp())

    def run_tool(self, src, **values):
        box = {}
        job = Job("mesh-repair", "run", str(src), str(self.dir / "out.ply"), dict(self.tool.values(values)))
        execute(self.tool, job, lambda k, p: box.__setitem__(k, p) if k in ("done", "error") else None,
                lambda: False)
        self.assertNotIn("error", box, box.get("error"))
        return box["done"][0]

    def test_sample_repaired_and_signed(self):
        src = self.tool.resolve(self.tool.sample)(self.dir)
        res = self.run_tool(src)
        self.assertEqual(res.stat("problems").value, 0)
        self.assertGreater(res.stat("fixed").value, 0)
        out = Path(res.outputs[0])
        from atlas_toolkit.geom import mesh as gm
        self.assertIn("atlas-toolkit:mesh-repair", gm.read_signature(out) or "")
        self.assertTrue(R.diagnose(gm.load_mesh(out)).solid)
        self.assertIn("Ready for:", res.report)
        self.assertIn("Crumble", res.report)

    def test_inspect_shows_problems(self):
        src = self.tool.resolve(self.tool.sample)(self.dir)
        info = self.tool.inspect_fn()(src)
        self.assertIn("to repair", info.summary)
        titles = [v["title"] for v in info.preview["views"]]
        self.assertIn("Problems", titles)
        marks = info.preview["views"][titles.index("Problems")]["mesh"].marks
        self.assertGreater(len(marks), 0)

    def test_repair_only_reports_what_is_left(self):
        V, F = mobius()
        src = self.dir / "band.stl"
        trimesh.Trimesh(V, F, process=False).export(src)
        res = self.run_tool(src, strategy="repair")
        self.assertTrue(res.notes)
        self.assertIn("Still not a closed solid", res.report)

    def test_rebuild_runs_in_the_tool(self):
        """The resampler reports progress with a message too: 1.0.0 stopped on it whenever it rebuilt."""
        src = self.tool.resolve(self.tool.sample)(self.dir)
        res = self.run_tool(src, strategy="rebuild", resolution=64)
        from atlas_toolkit.geom import mesh as gm
        self.assertTrue(R.diagnose(gm.load_mesh(Path(res.outputs[0]))).solid)


if __name__ == "__main__":
    unittest.main()
