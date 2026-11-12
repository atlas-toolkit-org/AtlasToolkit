"""
The mesh tools against the code they replace. The legacy scripts import PyQt5 or Tk at the top, so their compute
functions are lifted out of the source with `ast` and run as they are (with a trimesh-5 shim where v3 calls an API
trimesh removed). The scripts are looked up in the studio workspace (dev.local.toml) under code/Python and, once
retired, code/Python/_old; without the workspace these tests skip.
"""
import _isolate  # first: settings and caches stay in a temporary folder
import ast
import random
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.devpaths import workspace_path
from atlas_toolkit.tools.cube_grid_slicer import cube_grid_slicer as cgs
from atlas_toolkit.tools.cubic_replicator import cubic_replicator as cr
from atlas_toolkit.tools.mesh_shatter import mesh_shatter as ms
from atlas_toolkit.tools.voxelizer import voxelizer as vx

PY = workspace_path("code", "Python") or Path("no-studio-workspace")


def legacy_source(*names: str) -> str | None:
    for base in (PY, PY / "_old"):
        for n in names:
            p = base / n
            if p.exists():
                return p.read_text(encoding="utf-8")
    return None


def lift(src: str, names: list, extra: dict | None = None) -> dict:
    """Execute only the named top-level functions/classes (and methods, as 'Class.method') of a legacy script."""
    tree = ast.parse(src)
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            body.append(node)
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and f"{node.name}.{sub.name}" in names:
                    body.append(sub)
    ns = {"np": np, "trimesh": trimesh, "random": random, "List": list, "Callable": object, "Vec3": np.ndarray,
          "dataclass": __import__("dataclasses").dataclass, "annotations": None}
    ns.update(extra or {})
    code = compile(ast.Module(body=body, type_ignores=[]), "<legacy>", "exec")
    exec(code, ns)
    return ns


def model():
    m = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    rng = np.random.default_rng(3)
    m.vertices += rng.normal(scale=0.4, size=m.vertices.shape)   # no ties in distances
    return m


class GridSlicerParity(unittest.TestCase):
    def test_same_blocks_as_v2(self):
        src = legacy_source("cube_grid_slicer-v2.py")
        if src is None:
            self.skipTest("cube_grid_slicer-v2.py not found")
        ns = lift(src, ["GridInfo", "slice_mesh_into_grid"])
        box = trimesh.creation.box(extents=(30, 20, 10))
        old = ns["slice_mesh_into_grid"](box.copy(), 3, 2.0, False)
        parts, _ = cgs.slice_grid(box.copy(), 3, 2.0)
        new = cgs.combine(parts)
        self.assertEqual(len(parts), 27)
        self.assertAlmostEqual(new.volume, old.volume, places=6)
        np.testing.assert_allclose(new.bounds, old.bounds, atol=1e-9)
        self.assertAlmostEqual(new.area, old.area, places=6)

    def test_relative_gap(self):
        box = trimesh.creation.box(extents=(30, 30, 30))
        parts, _ = cgs.slice_grid(box, 3, 0.5, relative=True)   # half a 10-unit cell = 5 units
        np.testing.assert_allclose(cgs.combine(parts).extents, [40, 40, 40], atol=1e-9)


class ReplicatorParity(unittest.TestCase):
    def test_same_grid_as_v2_without_randomness(self):
        src = legacy_source("cubic_replicator_v2.py")
        if src is None:
            self.skipTest("cubic_replicator_v2.py not found")
        ns = lift(src, ["STLReplicator._replicate_mesh"])
        m = trimesh.creation.cone(radius=5, height=12)
        old = ns["_replicate_mesh"](None, m.copy(), [2, 3, 1], [1.0, 2.0, 0.0], {"x": 0, "y": 0, "z": 0}, 45,
                                    {"x": 0, "y": 0, "z": 0})
        new = cr.replicate(m.copy(), (2, 3, 1), (1.0, 2.0, 0.0))
        np.testing.assert_allclose(new.vertices, old.vertices, atol=1e-9)
        np.testing.assert_array_equal(new.faces, old.faces)

    def test_random_variation_is_seeded_and_mirrors_stay_outward(self):
        m = trimesh.creation.box(extents=(4, 6, 8))
        a = cr.replicate(m, (3, 3, 1), (1, 1, 1), (True, True, True), 90, (True, True, True), seed=5)
        b = cr.replicate(m, (3, 3, 1), (1, 1, 1), (True, True, True), 90, (True, True, True), seed=5)
        np.testing.assert_array_equal(a.vertices, b.vertices)
        for part in a.split(only_watertight=False):
            self.assertGreater(part.volume, 0)                   # no copy turned inside out


class VoxelizerParity(unittest.TestCase):
    def legacy(self, mesh, edge, gap, shape):
        """Verbatim from voxelizer-tool.py, VoxelWorker.run (signals removed)."""
        vg = mesh.voxelized(pitch=edge)
        points = vg.points
        if shape == 'cube' and gap == 0.0:
            return vg.as_boxes()
        parts = []
        shrink = edge - gap
        for centre in points:
            if shape == 'cube':
                part = trimesh.creation.box(extents=[shrink] * 3)
            elif shape == 'sphere':
                part = trimesh.creation.icosphere(subdivisions=2, radius=shrink / 2)
            else:
                part = trimesh.creation.cylinder(radius=shrink / 2, height=shrink)
            part.apply_translation(centre)
            parts.append(part)
        return trimesh.util.concatenate(parts)

    def test_same_voxels_as_v1(self):
        m = trimesh.creation.icosphere(subdivisions=2, radius=10)
        for shape, gap in (("cube", 0.0), ("cube", 0.5), ("sphere", 0.3), ("cylinder", 0.2)):
            with self.subTest(shape=shape, gap=gap):
                old = self.legacy(m.copy(), 2.0, gap, shape)
                new, n = vx.voxelize(m.copy(), 2.0, gap, shape)
                np.testing.assert_allclose(new.vertices, old.vertices, atol=1e-9)
                np.testing.assert_array_equal(new.faces, old.faces)

    def test_fill_adds_the_inside(self):
        m = trimesh.creation.icosphere(subdivisions=2, radius=10)
        _, shell = vx.voxelize(m.copy(), 2.0, 0.5, "cube")
        _, solid = vx.voxelize(m.copy(), 2.0, 0.5, "cube", fill=True)
        self.assertGreater(solid, shell)


class ShatterParity(unittest.TestCase):
    def test_same_shards_as_v3(self):
        src = legacy_source("mesh_shatter_gui/mesh_shatter_gui_v3.py")
        if src is None:
            self.skipTest("mesh_shatter_gui_v3.py not found")
        from trimesh.transformations import rotation_matrix
        ns = lift(src, ["bbox_diagonal", "farthest_point_sampling", "build_adjacency", "multisource_dijkstra_labels",
                        "shards_from_labels", "explode_shards", "segment_mesh"], {"rotation_matrix": rotation_matrix})
        m = model()
        old = ns["segment_mesh"](m.copy(), 12, 1.0, 7)
        new = ms.segment(m.copy(), 12, 1.0, 7)
        self.assertEqual(len(new), len(old))
        for a, b in zip(new, old):
            self.assertEqual(len(a.faces), len(b.faces))
            self.assertAlmostEqual(a.area, b.area, places=9)
        rng_o, rng_n = np.random.default_rng(7), np.random.default_rng(7)
        diag = ns["bbox_diagonal"](m)
        eo = ns["explode_shards"](old, m.centroid, diag, 0.3, 0.2, rng_o)
        en = ms.explode(new, m.centroid, diag, 0.3, 0.2, rng_n)
        for a, b in zip(en, eo):
            np.testing.assert_allclose(a.centroid, b.centroid, atol=1e-9)

    def test_mirrored_shards_keep_their_normals_outward(self):
        box = trimesh.creation.box(extents=(4, 4, 4))
        out = ms.perturb([box.copy() for _ in range(40)], 1.0, 10.0, np.random.default_rng(1))
        for s in out:
            self.assertGreater(s.volume, 0)

    def test_unreached_parts_become_shards(self):
        a = trimesh.creation.box(extents=(2, 2, 2))
        b = trimesh.creation.box(extents=(2, 2, 2))
        b.apply_translation((10, 0, 0))
        both = trimesh.util.concatenate([a, b])
        shards = ms.segment(both, 1, 1.0, 0)                     # one seed reaches only one of the boxes
        self.assertEqual(sum(len(s.faces) for s in shards), len(both.faces))


if __name__ == "__main__":
    unittest.main()
