"""Shape Crop: the shape's geometry, the exact open clip, the solid booleans, the engine, and big meshes quickly."""
import _isolate  # first: settings and caches stay in a temporary folder
import math
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.errors import ToolError
from atlas_toolkit.core.job import Job, execute, inspect_input
from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom import region as R
from atlas_toolkit.geom import repair
from atlas_toolkit.tools.mesh_crop.engine import crop
from atlas_toolkit.tools.mesh_crop.mesh_crop import clip

TOOL = registry().get("mesh-crop")
PLACE = {"position": (4.0, 2.0, -3.0), "rotation": (20.0, 35.0, 10.0), "size": (70.0, 50.0, 90.0)}


class Ctx:
    def __init__(self):
        self.notes = []

    def progress(self, *a):
        pass

    def note(self, m):
        self.notes.append(m)

    warn = log = note

    def check_cancel(self):
        pass


def sphere(sub=5, radius=10.0, offset=(0.0, 0.0, 0.0)):
    s = trimesh.creation.icosphere(subdivisions=sub, radius=radius)
    s.apply_translation(offset)
    return s


def values(**kw):
    return TOOL.values({**PLACE, **kw})


def area(V, F):
    return float(np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1).sum() / 2)


def open_edges(F):
    return repair.open_half_edges(F)


class RegionTests(unittest.TestCase):
    def test_crossings_lie_on_the_shape(self):
        rng = np.random.default_rng(1)
        for kind in R.SHAPES:
            for c in R.constraints(kind):
                a = rng.uniform(-1, 1, (4000, 3))
                b = rng.uniform(-1, 1, (4000, 3))
                va, vb = R.value(c, a), R.value(c, b)
                cross = (va <= 0) != (vb <= 0)
                t = R.crossing(c, a[cross], b[cross], va[cross], vb[cross])
                p = a[cross] + t[:, None] * (b[cross] - a[cross])
                np.testing.assert_allclose(R.value(c, p), 0, atol=1e-12, err_msg=f"{kind} {c[0]}")

    def test_frame_sizes_and_reference(self):
        bounds = np.array([[0., 0., 0.], [10., 20., 40.]])
        p = {"reference": "center", "position": (1, 2, 3), "rotation": (0, 0, 0), "size_unit": "percent",
             "size": (50, 25, 100)}
        m = R.frame(bounds, p)
        np.testing.assert_allclose(np.linalg.norm(m[:3, :3], axis=0), (20, 10, 40))
        np.testing.assert_allclose(m[:3, 3], (6, 12, 23))
        m = R.frame(bounds, {**p, "reference": "world", "size_unit": "units"})
        np.testing.assert_allclose(np.linalg.norm(m[:3, :3], axis=0), (50, 25, 100))
        np.testing.assert_allclose(m[:3, 3], (1, 2, 3))

    def test_solids_match_the_exact_volumes(self):
        m = R.frame(np.array([[0., 0., 0.], [1., 1., 1.]]),
                    {"position": (0, 0, 0), "rotation": (10, 20, 30), "size_unit": "units", "size": (3, 4, 5)})
        for kind in R.SHAPES:
            body = R.solid(kind, m, 360)
            self.assertAlmostEqual(body.volume() / R.volume(kind, m), 1.0, delta=2e-3, msg=kind)


class ClipTests(unittest.TestCase):
    def test_open_clip_is_exact_and_conforming(self):
        s = sphere(5)
        V, F = np.asarray(s.vertices), np.asarray(s.faces)
        for kind in R.SHAPES:
            p = values(shape=kind)
            m = R.frame(s.bounds, p)
            v, f, _ = clip(V, F, kind, m, "inside")
            self.assertGreater(len(f), 100)
            local = R.to_local(v, m)
            self.assertTrue(R.inside(kind, local * (1 - 1e-9)).all(), kind)
            # every open edge of the result is on the cut, i.e. on the shape's surface (no cracks inside)
            rim = np.unique(open_edges(f))
            on_surface = np.min(np.abs(np.column_stack([R.value(c, local[rim]) for c in R.constraints(kind)])), 1)
            self.assertLess(on_surface.max(), 1e-9, kind)
            # inside + outside give back the whole surface
            v2, f2, _ = clip(V, F, kind, m, "outside")
            self.assertAlmostEqual(area(v, f) + area(v2, f2), area(V, F), delta=1e-6 * area(V, F), msg=kind)

    def test_open_and_closed_cuts_keep_the_same_surface(self):
        s = sphere(6)
        for kind in R.SHAPES:
            pieces, _ = crop(Ctx(), s.copy(), values(shape=kind, cut="open"))
            open_area = area(pieces[0][1], pieces[0][2])
            pieces, info = crop(Ctx(), s.copy(), values(shape=kind))
            v, f = pieces[0][1], pieces[0][2]
            local = R.to_local(v, info["frame"])
            on_model = np.abs(np.linalg.norm(v[f].mean(1), axis=1) - 10) < 0.02    # faces of the model's own skin
            self.assertAlmostEqual(area(v, f[on_model]) / open_area, 1.0, delta=0.02, msg=kind)
            self.assertTrue(R.inside(kind, local * (1 - 1e-6)).all())

    def test_a_coarse_surface_still_meets_a_curved_shape(self):
        V = np.array([[-50., -50, 0], [50, -50, 0], [50, 50, 0], [-50, 50, 0]])
        F = np.array([[0, 1, 2], [0, 2, 3]])
        m = R.frame(np.array([[-50., -50, -1], [50, 50, 1]]),
                    {"position": (0, 0, 0), "rotation": (0, 0, 0), "size_unit": "units", "size": (40, 40, 40)})
        v, f, notes = clip(V, F, "sphere", m, "inside")
        self.assertAlmostEqual(area(v, f) / (math.pi * 20 ** 2), 1.0, delta=0.02)
        v, f, notes = clip(V, F, "sphere", m, "outside")
        self.assertAlmostEqual(area(v, f), 100 * 100 - math.pi * 400, delta=0.02 * math.pi * 400)
        self.assertEqual(len(open_edges(f)) > 4, True)


class BooleanTests(unittest.TestCase):
    def test_inclusion_exclusion_for_every_shape(self):
        for offset in ((0.0, 0.0, 0.0), (1e6, -2e6, 5e5)):
            s = sphere(5, offset=offset)
            A = s.volume
            for kind in R.SHAPES:
                vol = {}
                for op in ("intersect", "subtract", "union", "xor"):
                    pieces, info = crop(Ctx(), s.copy(), values(shape=kind, operation=op))
                    meshes = [trimesh.Trimesh(v, f, process=False) for _, v, f in pieces]
                    for mesh in meshes:
                        self.assertTrue(repair.closed_and_consistent(mesh.faces), f"{kind} {op} {offset}")
                    vol[op] = sum(mesh.volume for mesh in meshes)
                B = info["shape_volume"]
                tol = 1e-6 * (A + B)
                self.assertAlmostEqual(vol["union"] + vol["intersect"], A + B, delta=tol, msg=kind)
                self.assertAlmostEqual(vol["intersect"] + vol["subtract"], A, delta=tol, msg=kind)
                self.assertAlmostEqual(vol["xor"], vol["union"] - vol["intersect"], delta=tol, msg=kind)
                self.assertGreater(vol["intersect"], 0)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="atlas_crop_"))

    def run_tool(self, src, out="out_crop.stl", **kw):
        box = {}
        job = Job(TOOL.id, "run", str(src), str(self.dir / out), dict(values(**kw)))
        execute(TOOL, job, lambda k, p: box.__setitem__(k, p) if k in ("done", "error") else None, lambda: False)
        return box

    def save(self, mesh, name):
        path = self.dir / name
        gm.write_stl(path, mesh.vertices, mesh.faces, "test")
        return path

    def test_signed_output_and_separate_remainders(self):
        src = self.save(sphere(4), "ball.stl")
        box = self.run_tool(src)
        self.assertNotIn("error", box)
        res = box["done"][0]
        self.assertTrue(gm.read_signature(Path(res.outputs[0])).startswith("atlas-toolkit:mesh-crop"))
        self.assertEqual(res.data["path"], "closed")
        box = self.run_tool(src, out="ball_crop.stl", operation="xor", shape="pyramid", export_mode="separate")
        res = box["done"][0]
        self.assertEqual([Path(o).name for o in res.outputs], ["ball_crop.stl", "ball_crop_shape.stl"])
        self.assertTrue(all(TOOL.inputs.exclude and any(__import__("re").search(x, Path(o).name)
                                                         for x in TOOL.inputs.exclude) for o in res.outputs))

    def test_open_models_clip_and_union_asks_for_repair(self):
        s = sphere(4)
        z = s.vertices[s.faces].mean(1)[:, 2]
        cut = trimesh.Trimesh(s.vertices, s.faces[z > -8], process=False)
        src = self.save(cut, "scan.stl")
        box = self.run_tool(src)
        res = box["done"][0]
        self.assertEqual(res.data["path"], "open")
        self.assertTrue(any("not a closed solid" in n for n in res.notes))
        box = self.run_tool(src, operation="union")
        err = box["error"]
        self.assertIn("Repair Mesh", str(err))
        info = inspect_input(TOOL, str(src))
        check = info.values["input_check"]
        self.assertEqual(check["state"], "repair")
        self.assertIn("union", check["when"])
        self.assertIsNone(TOOL.needs_for(TOOL.values({"operation": "intersect"})))
        self.assertEqual(TOOL.needs_for(TOOL.values({"operation": "xor"})), "solid")

    def test_a_shape_away_from_the_model(self):
        s = sphere(3)
        with self.assertRaises(ToolError):
            crop(Ctx(), s.copy(), values(position=(100.0, 0.0, 0.0)))
        pieces, info = crop(Ctx(), s.copy(), values(position=(100.0, 0.0, 0.0), operation="subtract"))
        self.assertEqual(len(pieces[0][2]), len(s.faces))
        self.assertTrue(any("does not reach" in n for n in info["notes"]))

    def test_the_contract_fields(self):
        self.assertFalse(TOOL.official_presets)
        self.assertEqual(TOOL.per_input_label, "shape")
        self.assertEqual(set(TOOL.input_keys), set(TOOL.preset_skip))
        self.assertEqual(TOOL.strongest_need, "solid")
        self.assertIn("symmetric difference", TOOL.need_options("solid"))


class LargeMeshTests(unittest.TestCase):
    """A 1.3-million-face model through the whole tool. Generous ceilings (a busy CI runner), well above what a
    desktop takes (about 4 to 6 s each; scripts/dev/validate_mesh_crop.py measures up to 5M faces)."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="atlas_crop_big_"))
        s = trimesh.creation.icosphere(subdivisions=8, radius=50.0)
        cls.V, cls.F = np.asarray(s.vertices), np.asarray(s.faces)
        cls.src = cls.dir / "big.stl"
        gm.write_stl(cls.src, cls.V, cls.F, "test")

    def run_tool(self, **kw):
        box = {}
        job = Job(TOOL.id, "run", str(self.src), str(self.dir / "big_crop.stl"), dict(values(**kw)))
        t = time.perf_counter()
        execute(TOOL, job, lambda k, p: box.__setitem__(k, p) if k in ("done", "error") else None, lambda: False)
        self.assertNotIn("error", box)
        return box["done"][0], time.perf_counter() - t

    def test_closed_crop_and_union_are_quick(self):
        for op, kind in (("intersect", "box"), ("union", "cylinder")):
            res, took = self.run_tool(operation=op, shape=kind)
            self.assertEqual(res.data["path"], "closed")
            self.assertLess(took, 40.0, f"{op} {kind}: {res.data['times']}")

    def test_open_clip_alone_is_quick(self):
        m = R.frame(np.array([self.V.min(0), self.V.max(0)]), values(shape="sphere", size=(130.0, 80.0, 110.0)))
        t = time.perf_counter()
        v, f, _ = clip(self.V, self.F, "sphere", m, "inside")
        self.assertLess(time.perf_counter() - t, 8.0)
        self.assertGreater(len(f), 10000)


if __name__ == "__main__":
    unittest.main()
