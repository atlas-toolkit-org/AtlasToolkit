"""Crumble: blocks are closed, the collapse is seeded and obeys gravity, painting and fusing work, the solver is sound."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.job import JobContext
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_crumble import TOOL, engine, physics
from atlas_toolkit.tools.mesh_crumble import mesh_crumble as mc
from atlas_toolkit.tools.mesh_surface_eject import mesh_surface_eject as se

QUICK = dict(block_size=26.0, rubble=0.0, resolution=48, draft_resolution=40, stagger=0.3, up_axis="+z", front="-y")


def context():
    return JobContext(lambda *_: None, lambda: False, "run")


def run(source, dst, **values):
    merged = dict(TOOL.defaults())
    merged.update(QUICK)
    merged.update(values)
    return engine.run(context(), source, dst, TOOL.values(merged))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="atlas_crumble_")
        cls.folder = Path(cls.tmp.name)
        cls.mesh = trimesh.creation.icosphere(subdivisions=3, radius=30.0)
        cls.mesh.vertices *= [1.0, 0.85, 1.5]
        cls.source = cls.folder / "blob.stl"
        cls.mesh.export(cls.source)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class Collapse(Base):
    def test_moment_zero_moves_nothing_and_stays_within_the_model(self):
        dst = self.folder / "zero.stl"
        result = run(self.source, dst, collapse=0.0, cling=0.0, crack_width=0.0)
        self.assertEqual(max(result.data["moved"]), 0.0)
        out = gm.load_mesh(dst)
        self.assertTrue(np.allclose(out.bounds, self.mesh.bounds, atol=0.5))
        self.assertGreater(result.data["blocks"], 3)

    def test_settled_blocks_fall_and_rest_on_the_floor(self):
        dst = self.folder / "settled.stl"
        result = run(self.source, dst, collapse=100.0, cling=0.0, area="side", side="right", side_turn=0.0, burst=0.3)
        self.assertEqual(result.data["falling"], 0)
        self.assertGreater(np.median(result.data["moved"]), 0.1)
        out = gm.load_mesh(dst)
        low = self.mesh.bounds[0][2]
        # model height 90 units: nothing sinks more than 2% through the floor
        self.assertGreater(out.bounds[0][2], low - 0.02 * 90.0)

    def test_every_loose_block_is_a_closed_solid(self):
        dst = self.folder / "loose.stl"
        run(self.source, dst, collapse=30.0, separate=True)
        files = list(dst.with_name(f"{self.source.stem}_blocks_" + next(
            p.name.split("_blocks_")[1] for p in self.folder.iterdir() if "_blocks_" in p.name)).glob("*.stl"))
        self.assertGreater(len(files), 3)
        for path in files:
            self.assertTrue(gm.load_mesh(path).is_volume, path.name)

    def test_seeded(self):
        a, b, c = (self.folder / n for n in ("a.stl", "b.stl", "c.stl"))
        run(self.source, a, collapse=20.0)
        run(self.source, b, collapse=20.0)
        run(self.source, c, collapse=20.0, seed=99)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertNotEqual(a.read_bytes(), c.read_bytes())

    def test_gravity_pulls_down(self):
        result = run(self.source, None, collapse=50.0, cling=0.0, burst=0.0, spin=0.0, floor=False, area="whole",
                     depth=100.0, crack_width=0.0, stagger=0.0)
        moved = result.preview["views"][1]["mesh"]
        self.assertLess(moved.bounds[0][2], self.mesh.bounds[0][2] - 5.0)


class Areas(Base):
    def test_every_style_and_shape(self):
        for extra in ({"block_style": "bricks"}, {"block_style": "shards"}, {"block_style": "slabs"},
                      {"area_shape": "patch", "patch_radius": 40.0}, {"side": "top"}):
            with self.subTest(extra):
                self.assertGreater(run(self.source, None, collapse=10.0, **extra).data["blocks"], 0)

    def test_painted_area_breaks_only_there(self):
        saved = gm.load_mesh(self.source)
        up = saved.triangles_center[:, 2] > 15.0
        text = se.encode_selection(saved, up)
        result = run(self.source, None, area="paint", selection=text, depth=12.0, collapse=0.0, crack_width=0.0)
        self.assertGreater(result.data["blocks"], 0)
        self.assertLess(result.data["hollowed"], 60.0)
        with self.assertRaises(ValueError):
            run(self.source, None, area="paint", selection="")

    def test_fused_is_one_closed_solid(self):
        dst = self.folder / "fused.stl"
        run(self.source, dst, collapse=100.0, fuse=True, base_plate=True, bridge_reach=80.0)
        out = gm.load_mesh(dst)
        self.assertTrue(out.is_volume)
        self.assertEqual(len(out.split(only_watertight=False)), 1)

    def test_presets_stay_in_range(self):
        for preset in _presets.each(TOOL):
            merged = dict(TOOL.defaults())
            merged.update(_presets.raw(preset))
            TOOL.values(merged)


class Solver(unittest.TestCase):
    @staticmethod
    def cube(size=0.2):
        return trimesh.creation.box(extents=(size, size, size))

    def world(self, heights, **kw):
        box = self.cube()
        shape = physics.collision_shape(np.asarray(box.vertices))
        n = len(heights)
        inertia = np.eye(3) * float(box.volume) * 0.2 * 0.2 / 6.0
        return physics.World([shape] * n, [[0.0, 0.0, h] for h in heights], [float(box.volume)] * n, [inertia] * n, **kw)

    def go(self, world, seconds=2.5):
        n = world.n
        physics.run(world, np.zeros(n), np.zeros((n, 3)), np.zeros((n, 3)), seconds, False)

    def test_block_lands_and_stays_on_the_floor(self):
        world = self.world([1.0], floor=0.0)
        self.go(world)
        self.assertAlmostEqual(world.x[0, 2], 0.1, delta=0.02)

    def test_blocks_stack_without_exploding(self):
        world = self.world([0.11, 0.33, 0.55], floor=0.0)
        self.go(world, 3.0)
        self.assertTrue((np.diff(world.x[:, 2]) > 0.12).all(), world.x)
        self.assertLess(np.abs(world.x[:, :2]).max(), 0.2)

    def test_held_block_does_not_move(self):
        world = self.world([0.5, 1.0], floor=0.0)
        physics.run(world, np.array([np.inf, 0.0]), np.zeros((2, 3)), np.zeros((2, 3)), 1.0, False)
        self.assertAlmostEqual(world.x[0, 2], 0.5)

    def test_mass_properties_of_a_box(self):
        box = trimesh.creation.box(extents=(1.0, 2.0, 3.0))
        volume, centre, inertia = mc.mass_properties(np.asarray(box.vertices), np.asarray(box.faces, dtype=np.int64))
        self.assertAlmostEqual(volume, 6.0, places=6)
        self.assertTrue(np.allclose(centre, 0.0, atol=1e-6))
        self.assertTrue(np.allclose(np.diag(inertia), np.diag(box.moment_inertia), atol=0.05))


if __name__ == "__main__":
    unittest.main()
