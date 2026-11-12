"""Skeleton Shell: the middle is empty, the silhouette holds, every structure and preset builds, results are seeded."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.job import JobContext
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_skeleton import TOOL, engine, skin, wires

QUICK = dict(size=18.0, second_size=20.0, working_faces=24_000, draft_faces=12_000, up_axis="+z", front="-y",
             band=3.0, strand_length=150.0, min_length=10.0)
STRUCTURES = ("cells", "trimesh", "flow", "ribs", "plate")


def context(mode="run"):
    ctx = JobContext(lambda *_: None, lambda: False, mode)
    ctx.log = ctx.warn = lambda *_: None
    return ctx


def run(source, dst, mode="run", **values):
    merged = dict(TOOL.defaults())
    merged.update(QUICK)
    merged.update(values)
    return engine.run(context(mode), source, dst, TOOL.values(merged))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="atlas_skeleton_")
        cls.folder = Path(cls.tmp.name)
        cls.mesh = trimesh.creation.icosphere(subdivisions=4, radius=30.0)
        cls.mesh.vertices *= [1.0, 0.85, 1.5]
        cls.source = cls.folder / "blob.stl"
        cls.mesh.export(cls.source)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assert_hollow(self, out, depth=0.2):
        """Nothing but the skin's outer layer: the core of the ellipsoid is empty."""
        scaled = np.asarray(out.vertices) / np.array([30.0, 25.5, 45.0])
        self.assertGreater(np.linalg.norm(scaled, axis=1).min(), 1.0 - depth)


class Structures(Base):
    def test_every_structure_is_a_hollow_shell_within_the_silhouette(self):
        for structure in STRUCTURES:
            with self.subTest(structure):
                dst = self.folder / f"{structure}.stl"
                result = run(self.source, dst, structure=structure, stringer_spacing=12.0, rib_profile="strap")
                out = gm.load_mesh(dst)
                self.assertGreater(len(out.faces), 100)
                self.assert_hollow(out)
                self.assertTrue(np.all(out.bounds[0] > self.mesh.bounds[0] - 2.0))
                self.assertTrue(np.all(out.bounds[1] < self.mesh.bounds[1] + 2.0))
                self.assertIn("atlas-toolkit:mesh-skeleton", gm.read_signature(dst))
                self.assertEqual(result.data["notes"], [])

    def test_frames_reach_inward_but_stay_open(self):
        dst = self.folder / "frames.stl"
        result = run(self.source, dst, structure="ribs", rib_profile="frame", size=20.0, frame_depth=6.0,
                     stringer_spacing=0.0, thickness=1.0)
        self.assertGreater(result.data["frames"], 2)
        out = gm.load_mesh(dst)
        middle = np.abs(out.vertices[:, 2]) < 20.0
        radial = np.linalg.norm(out.vertices[middle][:, :2], axis=1)
        self.assertGreater(radial.min(), 5.0)       # an open middle at every station
        self.assertLess(radial.min(), 24.0)         # and real depth

    def test_lightening_holes_remove_metal(self):
        def volume(lightening):
            dst = self.folder / f"holes{lightening}.stl"
            run(self.source, dst, structure="ribs", rib_profile="frame", size=25.0, frame_depth=8.0, thickness=1.0,
                stringer_spacing=0.0, lightening=lightening)
            return gm.load_mesh(dst).volume
        self.assertLess(volume(True), volume(False))

    def test_a_plate_keeps_its_share_of_metal_when_rounded(self):
        sharp = run(self.source, None, "draft", structure="plate", rounding=0, size=25.0).data["coverage"]
        round_ = run(self.source, None, "draft", structure="plate", rounding=3, size=25.0).data["coverage"]
        self.assertLess(sharp, 0.9)
        self.assertLess(abs(round_ - sharp), 0.12)

    def test_flow_families_cross(self):
        one = run(self.source, None, "draft", structure="flow", flow_field="spiral", crossing=False, size=8.0)
        two = run(self.source, None, "draft", structure="flow", flow_field="spiral", crossing=True, size=8.0)
        self.assertGreater(two.data["wires"], one.data["wires"])
        self.assertGreater(one.data["wires"], 4)

    def test_every_field_follows_the_surface(self):
        for flow_field in ("form", "upright", "spiral"):
            with self.subTest(flow_field):
                dst = self.folder / f"flow_{flow_field}.stl"
                run(self.source, dst, structure="flow", flow_field=flow_field, wander=0.3, size=10.0, feature=0.4)
                self.assert_hollow(gm.load_mesh(dst))

    def test_second_layer_adds_to_the_first(self):
        base = dict(structure="ribs", rib_profile="strap", stringer_spacing=0.0)
        one = run(self.source, None, "draft", **base)
        both = run(self.source, None, "draft", second="cells", **base)
        self.assertGreater(both.data["wires"], one.data["wires"] + 10)


class Regions(Base):
    def test_a_band_keeps_its_skin_and_the_rest_is_open(self):
        dst = self.folder / "band.stl"
        result = run(self.source, dst, influence="band", band_from=0.0, band_to=50.0, feather=0.0, rim=True)
        self.assertGreater(result.data["kept"], 0.2)
        self.assertLess(result.data["kept"], 0.8)
        self.assertGreater(result.data["wires"], 10)
        self.assert_hollow(gm.load_mesh(dst))

    def test_acting_nowhere_keeps_the_whole_skin_as_a_shell(self):
        dst = self.folder / "none.stl"
        result = run(self.source, dst, influence="band", band_from=0.0, band_to=100.0, invert_influence=True,
                     feather=0.0, thickness=1.0)
        self.assertGreater(result.data["kept"], 0.99)
        self.assertEqual(result.data["wires"], 0)
        out = gm.load_mesh(dst)
        self.assertTrue(out.is_watertight)
        self.assert_hollow(out)
        self.assertLess(out.volume, 0.25 * self.mesh.volume)

    def test_feather_lets_go_unevenly(self):
        a = run(self.source, None, "draft", influence="band", band_from=30.0, band_to=70.0, feather=0.0)
        b = run(self.source, None, "draft", influence="band", band_from=30.0, band_to=70.0, feather=40.0)
        self.assertNotEqual(a.data["kept"], b.data["kept"])


class Solidity(Base):
    def test_fuse_makes_one_closed_solid(self):
        dst = self.folder / "fused.stl"
        result = run(self.source, dst, structure="cells", size=25.0, profile="round", sides=4, wire=2.0, fuse=True)
        self.assertTrue(result.data["fused"])
        self.assertEqual(result.data["pieces"], 1)
        out = trimesh.load(dst, force="mesh")
        out.merge_vertices()
        self.assertTrue(out.is_watertight)
        self.assertGreater(out.volume, 0.0)

    def test_every_swept_wire_is_closed_and_outward(self):
        path = np.stack([np.linspace(0, 10, 12), np.zeros(12), np.zeros(12)], 1)
        normals = np.tile([0.0, 0.0, 1.0], (12, 1))
        for profile in ("round", "strap", "fin"):
            with self.subTest(profile):
                w = wires.Wire(path, False, normals=normals, radii=np.full(12, 0.5))
                V, F, _ = wires.sweep_one(w, wires.Dress(wire=1.0, profile=profile, sides=5))
                m = trimesh.Trimesh(V, F, process=False)
                self.assertTrue(m.is_watertight)
                self.assertGreater(m.volume, 0.0)

    def test_a_closed_wire_is_closed_without_caps(self):
        t = np.linspace(0, 2 * np.pi, 24, endpoint=False)
        ring = np.stack([np.cos(t) * 5, np.sin(t) * 5, np.zeros(24)], 1)
        w = wires.Wire(ring, True, normals=ring / 5.0, radii=np.full(24, 0.4))
        V, F, _ = wires.sweep_one(w, wires.Dress(wire=0.8, sides=4))
        m = trimesh.Trimesh(V, F, process=False)
        self.assertTrue(m.is_watertight)
        self.assertGreater(m.volume, 0.0)

    def test_the_outer_face_lies_on_the_skin(self):
        path = np.stack([np.linspace(0, 10, 6), np.zeros(6), np.zeros(6)], 1)
        w = wires.Wire(path, False, normals=np.tile([0.0, 0.0, 1.0], (6, 1)), radii=np.full(6, 1.0))
        V, _, _ = wires.sweep_one(w, wires.Dress(wire=2.0, profile="fin", flat=4.0))
        self.assertAlmostEqual(float(V[:, 2].max()), 0.0, places=6)


class Surface(Base):
    def test_splitting_keeps_the_surface_conforming(self):
        V, F = skin.split_long_edges(np.asarray(self.mesh.vertices), np.asarray(self.mesh.faces), 1.5)
        m = trimesh.Trimesh(V, F, process=False)
        self.assertTrue(m.is_watertight)
        self.assertAlmostEqual(m.area, self.mesh.area, delta=self.mesh.area * 0.01)
        lengths = np.linalg.norm(V[m.edges[:, 0]] - V[m.edges[:, 1]], axis=1)
        self.assertLessEqual(lengths.max(), 1.5 + 1e-9)

    def test_clip_and_shell_are_closed(self):
        s = skin.Skin(self.mesh.vertices, self.mesh.faces, 3.0, 20_000)
        V, F, N = s.clip(s.V[:, 2] - 5.0)
        self.assertGreater(len(F), 50)
        self.assertLessEqual(V[:, 2].max(), 5.0 + 1e-6)
        m = trimesh.Trimesh(*skin.shell(V, F, N, 1.0), process=False)
        self.assertTrue(m.is_watertight)
        self.assertGreater(m.volume, 0.0)

    def test_isolines_of_an_ellipsoid_are_rings(self):
        s = skin.Skin(self.mesh.vertices, self.mesh.faces, 3.0, 20_000)
        lines = s.isolines(s.V[:, 2], 10.0)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0][1])
        self.assertTrue(np.allclose(lines[0][0][:, 2], 10.0, atol=1e-6))

    def test_voronoi_joints_meet_three_runs(self):
        s = skin.Skin(self.mesh.vertices, self.mesh.faces, 2.0, 30_000)
        seeds = s.relax(40, np.random.default_rng(1), iterations=4)
        dist, _, labels = s.nearest(s.metric(), seeds)
        nodes, segments = s.voronoi(labels, dist)
        runs = skin.chains(len(nodes), segments)
        self.assertGreater(len(runs), 40)
        ends = np.array([n for run, closed in runs if not closed for n in (run[0], run[-1])])
        counts = np.bincount(ends)
        self.assertGreater((counts[np.unique(ends)] == 3).mean(), 0.8)

    def test_seeded(self):
        a = run(self.source, self.folder / "a.stl", seed=5)
        b = run(self.source, self.folder / "b.stl", seed=5)
        run(self.source, self.folder / "c.stl", seed=6)
        va, vb, vc = (gm.load_mesh(self.folder / n).vertices for n in ("a.stl", "b.stl", "c.stl"))
        self.assertEqual(a.data["wires"], b.data["wires"])
        self.assertTrue(np.array_equal(va, vb))
        self.assertFalse(va.shape == vc.shape and np.array_equal(va, vc))


class InputModels(Base):
    def test_an_open_surface_works(self):
        grid = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
        keep = grid.vertices[grid.faces].mean(1)[:, 2] > -2.0
        cap = trimesh.Trimesh(grid.vertices, grid.faces[keep], process=True)
        self.assertFalse(cap.is_watertight)
        path = self.folder / "cap.ply"
        cap.export(path)
        result = run(path, self.folder / "cap_out.stl", size=25.0)
        self.assertGreater(result.data["wires"], 5)

    def test_several_parts_each_get_a_structure(self):
        a = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
        b = a.copy()
        b.apply_translation([40.0, 0.0, 0.0])
        path = self.folder / "two.stl"
        trimesh.util.concatenate([a, b]).export(path)
        dst = self.folder / "two_out.stl"
        run(path, dst, size=40.0)
        out = gm.load_mesh(dst)
        self.assertTrue((out.vertices[:, 0] < 15.0).any() and (out.vertices[:, 0] > 25.0).any())

    def test_the_output_never_replaces_the_input(self):
        with self.assertRaises(ValueError):
            run(self.source, self.source)

    def test_too_many_faces_says_what_to_change(self):
        with self.assertRaisesRegex(ValueError, "Output faces"):
            run(self.source, None, "draft", max_faces=10_000, size=6.0, profile="round", sides=16)

    def test_model_units_are_kept(self):
        dst = self.folder / "units.stl"
        run(self.source, dst)
        self.assertAlmostEqual(float(gm.load_mesh(dst).extents[2]), 90.0, delta=4.0)


class Presets(Base):
    def test_every_preset_builds(self):
        for found in _presets.each(TOOL):
            name, preset = found.name, _presets.raw(found)
            with self.subTest(name):
                merged = dict(TOOL.defaults())
                merged.update(preset)
                merged.update(working_faces=24_000, draft_faces=12_000, up_axis="+z", front="-y")
                merged["size"] = max(merged["size"], 14.0)
                merged["second_size"] = max(merged["second_size"], 14.0)
                merged["band"] = max(merged["band"], 3.0)
                result = engine.run(context("draft"), self.source, None, TOOL.values(merged))
                self.assertGreater(next(s.value for s in result.stats if s.key == "faces"), 100)

    def test_presets_cover_every_structure(self):
        presets = [_presets.raw(p) for p in _presets.each(TOOL)]
        self.assertEqual({p["structure"] for p in presets}, set(STRUCTURES))
        self.assertTrue(any(p["second"] != "none" for p in presets))
        self.assertTrue(any(p["influence"] != "all" for p in presets))

    def test_every_structure_builds(self):
        # without the private packs too: each structure, from the defaults
        for structure in STRUCTURES:
            with self.subTest(structure):
                merged = dict(TOOL.defaults(), structure=structure, working_faces=24_000, draft_faces=12_000,
                              up_axis="+z", front="-y")
                merged["size"] = max(merged["size"], 14.0)
                result = engine.run(context("draft"), self.source, None, TOOL.values(merged))
                self.assertGreater(next(s.value for s in result.stats if s.key == "faces"), 100)


if __name__ == "__main__":
    unittest.main()
