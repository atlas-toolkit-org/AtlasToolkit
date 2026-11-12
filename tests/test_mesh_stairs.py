"""Stair Sculpt: every mode and type gives one closed solid that really has steps, reproducibly, and the contract holds."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.core.selftest import run_tool, signed
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_stairs import TOOL, engine
from atlas_toolkit.tools.mesh_stairs import escher
from atlas_toolkit.tools.mesh_stairs import mesh_stairs as ms
from atlas_toolkit.tools.mesh_surface_eject import mesh_surface_eject as se


def context(mode="run"):
    return JobContext(lambda *_: None, lambda: False, mode)


def blob(subdivisions=3):
    model = trimesh.creation.icosphere(subdivisions=subdivisions, radius=30.0)
    model.vertices *= [1.0, 0.85, 1.5]
    return model


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="atlas_stairs_")
        cls.folder = Path(cls.tmp.name)
        cls.source = cls.folder / "blob.stl"
        blob().export(cls.source)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_with(self, name="out", source=None, job="run", **values):
        settings = dict(TOOL.defaults())
        settings.update(values)
        dst = self.folder / f"{name}_stairs.stl"
        result = engine.run(context(job), source or self.source, dst, TOOL.values(settings))
        return result, dst


def horizontal_area(mesh, up=2) -> float:
    """Area of the faces that look straight up: treads, landings."""
    normals = mesh.face_normals
    return float(mesh.area_faces[normals[:, up] > 0.999].sum())


class EveryCombination(Base):
    def test_every_mode_and_type_is_one_closed_solid_with_treads(self):
        for mode in ("carve", "treads", "cut"):
            for kind in ("linear", "turns", "helix"):
                with self.subTest(mode=mode, kind=kind):
                    values = dict(mode=mode, stair_type=kind, steps=30, flights=1, twists=5, turns=2.0, resolution=48,
                                  heading=90.0 if mode == "cut" else 0.0)
                    result, dst = self.run_with(f"{mode}_{kind}", **values)
                    saved = gm.load_mesh(dst)
                    self.assertTrue(saved.is_watertight and saved.is_winding_consistent)
                    self.assertEqual(result.data["pieces"], 1)
                    self.assertGreater(result.data["steps"], 0)
                    self.assertLess(saved.volume, blob().volume)
                    self.assertGreater(horizontal_area(saved), 0.0)
                    self.assertTrue(gm.read_signature(dst).startswith(TOOL.signature))

    def test_every_preset_runs_on_a_closed_model(self):
        for found in _presets.each(TOOL):
            name = found.name
            with self.subTest(preset=name):
                preset = dict(_presets.raw(found), resolution=48, draft_resolution=40)
                result, dst = self.run_with(name.replace(" ", "_"), **preset)
                saved = gm.load_mesh(dst)
                self.assertTrue(saved.is_watertight, name)
                self.assertGreater(result.data["steps"], 0, name)

    def test_the_model_keeps_its_units_and_placement(self):
        moved = blob()
        moved.apply_translation([100.0, -50.0, 200.0])
        path = self.folder / "moved.stl"
        moved.export(path)
        _, dst = self.run_with("moved", source=path, **{"mode": "treads", "stair_type": "helix",
                                                                     "steps": 24, "flights": 1, "keep_bottom": 8.0})
        out = gm.load_mesh(dst)
        self.assertTrue(np.allclose(out.bounds[0][2], moved.bounds[0][2], atol=0.2))
        self.assertLess(abs(out.bounds.mean(0)[0] - moved.bounds.mean(0)[0]), 12.0)

    def test_same_settings_same_solid(self):
        values = dict(mode="carve", stair_type="linear", flights=6, steps=48, seed=3, resolution=48)
        a, path_a = self.run_with("a", **values)
        first = gm.load_mesh(path_a)
        b, path_b = self.run_with("b", **values)
        second = gm.load_mesh(path_b)
        self.assertEqual(len(first.faces), len(second.faces))
        self.assertTrue(np.allclose(first.vertices, second.vertices))


class Steps(unittest.TestCase):
    def test_steps_are_evenly_spaced_in_height(self):
        for steps in (ms.linear_steps(0.0, 1.0, -0.2, 0.2, np.linspace(0.1, 1.0, 10)),
                      ms.helix_steps(lambda z: np.zeros(2), 0.1, 0.5, 0.0, 1.0, 10, 1.0, 0.0),
                      ms.quarter_steps(lambda z: (0.3, 0.2), 0.1, 0.0, 1.0, 20, 3)):
            zs = np.array([s.z for s in steps])
            self.assertTrue((np.diff(zs) > 0).all())
            self.assertAlmostEqual(zs[-1], 1.0)
            self.assertLess(np.ptp(np.diff(zs)), 1e-9)

    def test_a_helix_turns_as_far_as_asked(self):
        steps = ms.helix_steps(lambda z: np.zeros(2), 0.1, 0.5, 0.0, 1.0, 40, 2.0, 0.0)
        angles = np.unwrap([np.arctan2(*s.core[::-1]) for s in steps])
        self.assertAlmostEqual(abs(angles[-1] - angles[0]) / (2 * np.pi), 2.0 * 39 / 40, places=2)

    def test_reverse_climb_mirrors_a_turning_stair(self):
        a = ms.quarter_steps(lambda z: (0.3, 0.3), 0.1, 0.0, 1.0, 20, 3, hand=1.0)
        b = ms.quarter_steps(lambda z: (0.3, 0.3), 0.1, 0.0, 1.0, 20, 3, hand=-1.0)
        self.assertTrue(np.allclose(a[5].core * [-1.0, 1.0], b[5].core))

    def test_quarter_flights_climb_past_their_landings(self):
        steps = ms.quarter_steps(lambda z: (0.3, 0.3), 0.1, 0.0, 1.0, 40, 4)
        self.assertGreaterEqual(len(steps), 30)
        ms.flight_solid(steps, "band", 0.1, 0.5, 0.01, 0.0)

    def test_steps_fuse_with_their_neighbours(self):
        steps = ms.linear_steps(0.0, 1.0, -0.2, 0.2, np.linspace(0.1, 1.0, 10), lip=0.01)
        solid = ms.flight_solid(steps, "band", 0.2, 0.5, 0.02, 0.0)
        self.assertEqual(len(solid.decompose()), 1)

    def test_every_underside_makes_a_solid(self):
        steps = ms.helix_steps(lambda z: np.zeros(2), 0.1, 0.5, 0.0, 1.0, 24, 1.5, 0.0, lip=0.01)
        for style in ("band", "smooth", "open", "solid"):
            with self.subTest(style=style):
                solid = ms.flight_solid(steps, style, 0.1, 0.5, 0.02, -0.1, carriage=0.02)
                self.assertFalse(solid.is_empty())

    def test_touching_edges_stay_closed_once_saved(self):
        # Two boxes that touch along one edge: one solid to Manifold, four faces on an edge once a format merges them.
        a = ms.box((0, 0, 0), (1, 1, 1))
        b = ms.box((1, 1, 0), (2, 2, 1))
        V, F, moved = ms.closed_arrays(a + b, lambda v: v)
        self.assertGreater(moved, 0.0)
        self.assertLess(moved, 1e-3)
        self.assertTrue(gm.clean(trimesh.Trimesh(V, F, process=False)).is_volume)


class Ensemble(unittest.TestCase):
    def test_a_flight_always_goes_to_a_neighbour_one_storey_up(self):
        rng = np.random.default_rng(1)
        platforms, links = escher.walk(4, 3, 6, 18, "linear", rng)
        self.assertGreaterEqual(len(links), 10)
        for here, to, form in links:
            self.assertEqual(form, "flight")
            self.assertEqual(to[2], here[2] + 1)
            self.assertEqual(abs(to[0] - here[0]) + abs(to[1] - here[1]), 1)
            self.assertIn(here, platforms)
            self.assertIn(to, platforms)

    def test_a_turning_ensemble_turns(self):
        _, links = escher.walk(5, 5, 8, 12, "turns", np.random.default_rng(2))
        chained = [(a, b) for a, b in zip(links, links[1:]) if a[1] == b[0]]
        self.assertTrue(chained)
        for (a, b) in chained:
            self.assertNotEqual(a[0][0] != a[1][0], b[0][0] != b[1][0])

    def test_nothing_is_placed_outside_the_cavity(self):
        inside = lambda i, j, k: i < 2  # noqa: E731
        platforms, _ = escher.walk(4, 3, 6, 12, "linear", np.random.default_rng(3), inside)
        self.assertTrue(platforms)
        self.assertTrue(all(i < 2 for i, _, _ in platforms))

    def test_the_seed_rearranges_it(self):
        a = escher.walk(4, 3, 6, 12, "linear", np.random.default_rng(1))[1]
        b = escher.walk(4, 3, 6, 12, "linear", np.random.default_rng(1))[1]
        c = escher.walk(4, 3, 6, 12, "linear", np.random.default_rng(2))[1]
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


class Carving(Base):
    def test_it_hollows_the_model_and_leaves_the_far_side_alone(self):
        values = dict(mode="carve", stair_type="helix", steps=30, flights=1, side="right", side_turn=0.0,
                      depth=50.0, open_shape="ellipse", open_w=60.0, open_h=60.0, resolution=48)
        result, dst = self.run_with("hollow", **values)
        saved = gm.load_mesh(dst)
        self.assertLess(saved.volume, blob().volume * 0.97)
        self.assertGreater(saved.volume, blob().volume * 0.4)
        # The far (left) half of the bounding box is untouched: the extent stays the model's.
        self.assertTrue(np.allclose(saved.bounds[0], blob().bounds[0], atol=0.5))

    def test_a_painted_area_opens_on_its_own_side(self):
        loaded = gm.load_mesh(self.source)
        centres = loaded.triangles_center
        # Paint the +X side: this is the front-right of the canonical frame (X stays X for a Z-up model).
        painted = (centres[:, 0] > 15.0) & (np.abs(centres[:, 2]) < 25.0) & (np.abs(centres[:, 1]) < 15.0)
        self.assertTrue(painted.any())
        values = dict(mode="carve", placement="paint", selection=se.encode_selection(loaded, painted), stair_type="linear",
                      steps=20, flights=1, depth=50.0, wall=3.0, resolution=48)
        _, dst = self.run_with("painted", **values)
        saved = gm.load_mesh(dst)
        self.assertLess(saved.volume, blob().volume)
        # Material went from the painted side.
        side = saved.vertices[(saved.vertices[:, 0] > 5.0) & (np.abs(saved.vertices[:, 2]) < 15.0)]
        far = saved.vertices[(saved.vertices[:, 0] < -5.0) & (np.abs(saved.vertices[:, 2]) < 15.0)]
        self.assertGreater(len(side), len(far))

    def test_painting_needs_a_selection(self):
        with self.assertRaisesRegex(ValueError, "Paint an area"):
            self.run_with("none", **{"mode": "carve", "placement": "paint"})

    def test_a_selection_from_another_mesh_is_refused(self):
        other = trimesh.creation.icosphere(subdivisions=2)
        text = se.encode_selection(other, np.ones(len(other.faces), bool))
        with self.assertRaisesRegex(ValueError, "different mesh"):
            self.run_with("wrong", **{"mode": "carve", "placement": "paint", "selection": text})

    def test_an_opening_outside_the_model_is_explained(self):
        with self.assertRaisesRegex(ValueError, "outside the model|too small"):
            self.run_with("off", **{"mode": "carve", "open_x": 60.0, "open_y": 60.0, "open_w": 5.0,
                                                "open_h": 5.0, "open_shape": "rect", "resolution": 48})


class Inputs(Base):
    def test_an_open_model_is_refused_until_repair_is_asked_for(self):
        model = blob()
        model.update_faces(np.arange(len(model.faces)) != 0)
        path = self.folder / "open.ply"
        model.export(path)
        values = dict(mode="treads", stair_type="helix", steps=20, flights=1)
        with self.assertRaisesRegex(ValueError, "closed solid"):
            self.run_with("open", source=path, **values)
        result, dst = self.run_with("open_fixed", source=path, repair="resample", repair_resolution=64, **values)
        self.assertTrue(result.data["repaired"])
        self.assertTrue(gm.load_mesh(dst).is_watertight)

    def test_preview_writes_nothing(self):
        settings = dict(TOOL.defaults())
        settings.update(mode="treads", stair_type="helix", steps=20, flights=1)
        dst = self.folder / "preview_stairs.stl"
        result = engine.run(context("draft"), self.source, dst, TOOL.values(settings))
        self.assertEqual(result.outputs, [])
        self.assertFalse(dst.exists())
        self.assertEqual(result.preview["prefer"], "Staircase")


class Contract(unittest.TestCase):
    def test_the_engine_meets_the_contract(self):
        tool = registry().tools[TOOL.id]
        with tempfile.TemporaryDirectory(prefix="atlas_stairs_contract_") as folder:
            for variant in (None, *TOOL.test_variants):
                ok, message = run_tool(tool, Path(folder), variant=variant)
                self.assertTrue(ok, message)
            for out in Path(folder).glob("*_stairs.stl"):
                self.assertTrue(signed(tool, out))


if __name__ == "__main__":
    unittest.main()
