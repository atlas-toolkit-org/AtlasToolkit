"""Dilate's physical behavior, geometry safeguards and contract integration."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from atlas_toolkit.core.cli import build_parser, main as cli
from atlas_toolkit.core.io import output_path
from atlas_toolkit.core.history import History
from atlas_toolkit.core.job import Cancelled, JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.devpaths import workspace_path
from atlas_toolkit.geom import influence
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_dilate import TOOL, engine
from atlas_toolkit.tools.mesh_dilate import mesh_dilate as md
from atlas_toolkit.tools.mesh_compress import mesh_compress as mc


def sphere():
    m = trimesh.creation.icosphere(subdivisions=2)
    return np.asarray(m.vertices) * [0.7, 0.5, 1.0], np.asarray(m.faces)


class DilationTests(unittest.TestCase):
    def test_targets_and_monotonic_inflation(self):
        V, F = sphere()
        previous = 1
        for inflation in (30, 80, 150, 300):
            with self.subTest(inflation=inflation):
                out = md.solve(V, F, np.ones(len(V)), inflation=inflation)
                ratio = out.volumes1[0] / out.volumes0[0]
                self.assertAlmostEqual(ratio, 1 + inflation / 100, delta=0.03)
                self.assertGreater(ratio, previous)
                self.assertTrue(md.valid_faces(V, out.vertices, F))
                previous = ratio

    def test_radial_expansion_leaves_axial_coordinates_exact(self):
        V, F = sphere()
        for axis, column in md.AXIS.items():
            out = md.solve(V, F, np.ones(len(V)), inflation=150, direction="radial", axis=axis)
            np.testing.assert_array_equal(out.vertices[:, column], V[:, column])
            self.assertAlmostEqual(out.volumes1[0] / out.volumes0[0], 2.5, delta=0.03)

    def test_zero_and_empty_influence_are_exact(self):
        V, F = sphere()
        for weights, inflation in ((np.ones(len(V)), 0), (np.zeros(len(V)), 150)):
            out = md.solve(V, F, weights, inflation=inflation)
            np.testing.assert_array_equal(out.vertices, V)

    def test_local_influence_and_base_pins(self):
        V, F = sphere()
        w = influence.weights(V, dict(influence="band", band_from=45, band_to=65, feather=15), V.min(0), V.max(0))
        out = md.solve(V, F, w, inflation=100)
        np.testing.assert_array_equal(out.vertices[w == 0], V[w == 0])
        self.assertGreater(np.linalg.norm(out.vertices - V), 0.1)
        self.assertAlmostEqual(out.volumes1[0] / out.volumes0[0],
                               out.targets[0] / out.volumes0[0], delta=0.03)
        w = md.mobility(V, F, np.ones(len(V)), "base", V.min(0), V.max(0))
        self.assertTrue(np.any(w == 0))
        self.assertTrue(np.any((w > 0) & (w < 1)))
        out = md.solve(V, F, w, inflation=150)
        np.testing.assert_array_equal(out.vertices[w == 0], V[w == 0])

    def test_open_surface_and_boundary_anchor(self):
        V, F = sphere()
        F = F[np.all(V[F, 2] > -0.7, axis=1)]
        used, inverse = np.unique(F, return_inverse=True)
        V, F = V[used], inverse.reshape(-1, 3)
        w = md.mobility(V, F, np.ones(len(V)), "boundary", V.min(0), V.max(0))
        out = md.solve(V, F, w, inflation=80)
        boundary = md.boundary_vertices(F)
        self.assertIsNone(out.volumes0[0])
        np.testing.assert_array_equal(out.vertices[boundary], V[boundary])
        self.assertGreater(np.linalg.norm(out.vertices - V), 0.05)
        self.assertFalse(mc.is_closed(F))

    def test_seeded_lobes_preserve_placement(self):
        V, F = sphere()
        a = md.solve(V, F, np.ones(len(V)), lobes=0.4, seed=2).vertices
        b = md.solve(V, F, np.ones(len(V)), lobes=0.4, seed=2).vertices
        c = md.solve(V, F, np.ones(len(V)), lobes=0.4, seed=3).vertices
        np.testing.assert_array_equal(a, b)
        self.assertGreater(np.linalg.norm(a - c), 0.1)
        area = md.areas(V, F)[1]
        np.testing.assert_allclose(np.average(a - V, weights=area, axis=0), 0, atol=1e-10)
        np.testing.assert_allclose(md.lobe_field(V, 2, .4, 35, 2),
                                   md.lobe_field(V * 10, 20, .4, 35, 2), atol=1e-12)

    def test_material_controls_change_shape_without_losing_the_volume_target(self):
        V, F = sphere()
        elastic = md.solve(V, F, np.ones(len(V)), softness=0, roundness=0)
        soft = md.solve(V, F, np.ones(len(V)), softness=1, roundness=1)
        for out in (elastic, soft):
            self.assertAlmostEqual(out.volumes1[0] / out.volumes0[0], 1.8, delta=.03)
        self.assertGreater(np.linalg.norm(elastic.vertices - soft.vertices), .1)

    def test_separate_parts_keep_individual_targets(self):
        V, F = sphere()
        W, G = np.vstack((V - [3, 0, 0], V * 0.4 + [3, 0, 0])), np.vstack((F, F + len(V)))
        out = md.solve(W, G, np.ones(len(W)), inflation=100)
        self.assertEqual(len(out.targets), 2)
        for before, after in zip(out.volumes0, out.volumes1):
            self.assertAlmostEqual(after / before, 2, delta=0.03)

    def test_triangle_contacts_limit_crossing(self):
        # Two unconnected triangles: the second is driven through the first.
        V = np.array([[-1., -1, 0], [1, -1, 0], [0, 1, 0],
                      [-.2, 0, 1], [.2, 0, 1], [0, .5, 1]])
        F = np.array([[0, 1, 2], [3, 4, 5]])
        target = V.copy()
        target[3:, 2] = [0.2, -.2, -.2]
        self.assertTrue(md.intersections(target, F))
        guard = md.ContactGuard(V, F)
        safe, alpha = guard.limit(V, target)
        self.assertLess(alpha, 1)
        self.assertFalse(md.intersections(safe, F))
        self.assertTrue(md.valid_faces(V, safe, F))

    def test_nearby_parts_keep_new_triangle_crossings_out(self):
        V, F = sphere()
        W = np.vstack((V - [.8, 0, 0], V + [.8, 0, 0]))
        G = np.vstack((F, F + len(V)))
        out = md.solve(W, G, np.ones(len(W)), inflation=150)
        self.assertFalse(md.intersections(out.vertices, G))
        self.assertGreater(min(b / a for a, b in zip(out.volumes0, out.volumes1)), 1.1)

    def test_finite_and_nonempty_surface_required(self):
        with self.assertRaises(ValueError):
            md.solve(np.empty((0, 3)), np.empty((0, 3), dtype=int), np.empty(0))


class EngineTests(unittest.TestCase):
    def setUp(self):
        registry()
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_dilate_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.src = engine.make_sample(self.folder)
        self.messages = []
        self.ctx = JobContext(lambda k, p: self.messages.append((k, p)), lambda: False)

    def values(self, **overrides):
        return TOOL.values(dict(TOOL.test_values, **overrides))

    def test_every_preset_and_format_is_a_single_signed_mesh(self):
        self.assertFalse({"steps", "stages", "iterations"} & {p.key for p in TOOL.param_list})
        shapes = []
        for preset in _presets.each(TOOL):
            name = preset.name
            for ext in (".stl", ".obj", ".ply"):
                with self.subTest(preset=name, format=ext):
                    p = _presets.run_values(TOOL, preset, **TOOL.test_values)
                    dst = self.folder / (name + ext)
                    result = engine.run(self.ctx, self.src, dst, p)
                    self.assertEqual(result.outputs, [str(dst)])
                    self.assertTrue(gm.read_signature(dst).startswith(TOOL.signature))
                    mesh = gm.load_mesh(dst)
                    self.assertTrue(mesh.is_watertight)
                    self.assertTrue(np.isfinite(mesh.vertices).all())
                    self.assertLessEqual(len(mesh.faces), p.out_faces)
                    self.assertIn("one final mesh", result.report)
                    if ext == ".stl":
                        shapes.append(mesh.extents)
        self.assertGreater(np.ptp(np.array(shapes), axis=0).max(), 3)

    def test_dry_run_and_draft_write_nothing_even_if_given_a_destination(self):
        for mode in ("analyse", "draft"):
            dst = self.folder / "never.stl"
            r = engine.run(JobContext(lambda *_: None, lambda: False, mode), self.src, dst, self.values())
            self.assertEqual(r.outputs, [])
            self.assertFalse(dst.exists())
            self.assertEqual(r.preview["views"][0]["kind"], "mesh")

    def test_source_protection_cancellation_and_invalid_input(self):
        with self.assertRaisesRegex(ValueError, "replace"):
            engine.run(self.ctx, self.src, self.src, self.values())
        dst = self.folder / "cancelled.stl"
        with self.assertRaises(Cancelled):
            engine.run(JobContext(lambda *_: None, lambda: True), self.src, dst, self.values())
        self.assertFalse(dst.exists())
        bad = self.folder / "bad.stl"
        bad.write_text("not a model")
        with self.assertRaises((ValueError, IndexError)):
            engine.run(self.ctx, bad, dst, self.values())
        self.assertFalse(dst.exists())

    def test_zero_preserves_loaded_geometry_and_orientation(self):
        before = gm.load_mesh(self.src)
        dst = self.folder / "zero.ply"
        engine.run(self.ctx, self.src, dst, self.values(inflation=0, up_axis="+x", front="+y"))
        after = gm.load_mesh(dst)
        np.testing.assert_allclose(after.vertices, before.vertices)
        np.testing.assert_array_equal(after.faces, before.faces)

    def test_reversed_winding_inflates_outward(self):
        m = gm.load_mesh(self.src)
        m.invert()
        m.export(self.src)
        r = engine.run(self.ctx, self.src, None, self.values())
        self.assertAlmostEqual(r.stat("inflation").value, 80, delta=3)
        self.assertEqual(r.data["open_parts"], 0)

    def test_open_mesh_stays_open_and_warns(self):
        m = gm.load_mesh(self.src)
        m.update_faces(np.all(m.vertices[m.faces, 2] > -10, axis=1))
        m.export(self.src)
        dst = self.folder / "open.ply"
        r = engine.run(self.ctx, self.src, dst, self.values(anchor="boundary"))
        self.assertEqual(r.data["open_parts"], 1)
        self.assertFalse(gm.load_mesh(dst).is_watertight)
        self.assertEqual(r.stat("inflation").value, "surface swelling")
        self.assertTrue(any(k == "log" and p[0] == "warning" and "preserving openings" in p[1]
                            for k, p in self.messages))

    def test_flat_open_surface_has_a_finite_fallback(self):
        m = trimesh.Trimesh([[-1., -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]],
                            [[0, 1, 2], [0, 2, 3]], process=False)
        m.export(self.src)
        r = engine.run(self.ctx, self.src, None, self.values(up_axis="+z", front="-y", anchor="boundary"))
        self.assertEqual(r.data["open_parts"], 1)
        self.assertTrue(np.isfinite(r.stat("displacement").value))

    def test_cancellation_during_the_solve_writes_nothing(self):
        progress = [0.0]
        def emit(kind, payload):
            if kind == "progress":
                progress[0] = payload[0]
        ctx = JobContext(emit, lambda: progress[0] > .25)
        dst = self.folder / "interrupted.ply"
        with self.assertRaises(Cancelled):
            engine.run(ctx, self.src, dst, self.values())
        self.assertFalse(dst.exists())

    def test_units_position_and_lobes_survive_scaling(self):
        dst = self.folder / "first.ply"
        p = self.values(lobes=.25, seed=4, detail=1)
        engine.run(self.ctx, self.src, dst, p)
        first = gm.load_mesh(dst)
        source = gm.load_mesh(self.src)
        source.vertices = source.vertices * 7 + [120, -90, 35]
        scaled = self.folder / "scaled.ply"
        source.export(scaled)
        target = self.folder / "scaled_dilate.ply"
        engine.run(self.ctx, scaled, target, p)
        actual = gm.load_mesh(target)
        # STL/PLY quantization can change refinement connectivity; compare bounds.
        np.testing.assert_allclose(actual.bounds, first.bounds * 7 + [120, -90, 35], rtol=.005, atol=.15)

    def test_dense_head_contacts_do_not_freeze_the_entire_model(self):
        source = workspace_path("3d_models_db", "head_model", "v17_head_decim7.5K-v2.stl")
        if source is None or not source.exists():
            self.skipTest("studio's head reference is not installed")
        for budget in (20_000, 80_000):
            r = engine.run(self.ctx, source, None, self.values(out_faces=budget))
            self.assertGreater(r.stat("inflation").value, 50)
            self.assertLess(r.stat("inflation").value, 100)
            # The reference has intersecting triangles at its neck. Finer
            # triangulation resolves more contacts and can legitimately clamp
            # more motion; this must be reported, rather than silently frozen.
            if r.stat("inflation").value < 77:
                self.assertGreater(r.data["limited"], 0)
                self.assertTrue(any(k == "log" and p[0] == "warning" and "achieved" in p[1]
                                    for k, p in self.messages))

    def test_clean_surface_volume_target_survives_refinement(self):
        for budget in (4_000, 80_000):
            r = engine.run(self.ctx, self.src, None, self.values(out_faces=budget))
            self.assertAlmostEqual(r.stat("inflation").value, 80, delta=3)

    def test_cli_json_and_output_template(self):
        saved = json.loads(json.dumps(TOOL.defaults()))
        self.assertEqual(TOOL.values(saved), TOOL.defaults())
        args = build_parser().parse_args(["run", TOOL.id, str(self.src), "--inflation", "120", "--anchor", "base"])
        self.assertEqual(TOOL.values({"inflation": args.inflation})["inflation"], 120)
        dst = self.folder / "cli.ply"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                patch("atlas_toolkit.core.cli.History", return_value=History(self.folder / "history.jsonl")):
            self.assertEqual(cli(["run", TOOL.id, str(self.src), "-o", str(dst), "--format", "ply",
                                  "--out-faces", "4000", "--quiet"]), 0)
        self.assertTrue(gm.read_signature(dst).startswith(TOOL.signature))
        self.assertEqual(output_path(TOOL, self.src, TOOL.defaults()).name, "sample_dilate.stl")


if __name__ == "__main__":
    unittest.main()
