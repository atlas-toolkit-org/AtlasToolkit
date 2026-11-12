"""Disintegrate (formerly Mesh Morph): geometry and integration checks; all files stay in temporary folders."""
import _isolate  # first: settings and caches stay in a temporary folder
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from atlas_toolkit.core import Cancelled, JobContext
from atlas_toolkit.core.io import output_path
from atlas_toolkit.core.job import Job, execute
from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_disintegrate import TOOL
from atlas_toolkit.tools.mesh_disintegrate import engine
from atlas_toolkit.tools.mesh_disintegrate import mesh_disintegrate as mm


def context(mode="run", cancelled=lambda: False):
    return JobContext(lambda *_: None, cancelled, mode=mode)


class DisintegrateGeometryTests(unittest.TestCase):
    def setUp(self):
        self.mesh = trimesh.creation.icosphere(subdivisions=0, radius=40)

    def test_midpoints_child_order_and_winding_match_sketch(self):
        tri = np.array([[[0., 0, 0], [4, 0, 0], [0, 4, 0]]])
        expected = [[[0, 0, 0], [2, 0, 0], [0, 2, 0]],
                    [[4, 0, 0], [2, 2, 0], [2, 0, 0]],
                    [[0, 4, 0], [0, 2, 0], [2, 2, 0]],
                    [[2, 0, 0], [2, 2, 0], [0, 2, 0]]]
        np.testing.assert_array_equal(mm.subdivide(tri), expected)
        self.assertTrue(np.all(mm.as_mesh(mm.subdivide(tri)).face_normals[:, 2] > 0))

    def test_fourfold_growth_and_zero_strength_preserve_surface(self):
        original = self.mesh.triangles.copy()
        result = mm.generate(self.mesh, steps=3, strength=0)
        self.assertEqual([len(s) for s in result.stages], [20, 80, 320, 1280])
        for stage in result.stages:
            np.testing.assert_allclose(mm.bounds(stage), self.mesh.bounds)
            self.assertAlmostEqual(mm.as_mesh(stage).area, self.mesh.area)
        np.testing.assert_array_equal(self.mesh.triangles, original)

    def test_coincident_child_corners_move_independently(self):
        before = mm.subdivide(self.mesh.triangles)
        after = mm.generate(self.mesh, steps=1, strength=1).stages[1]
        np.testing.assert_array_equal(before[0, 1], before[1, 2])
        self.assertFalse(np.array_equal(after[0, 1], after[1, 2]))
        exported = mm.as_mesh(after)
        self.assertEqual(len(exported.vertices), 3 * len(after))
        self.assertEqual(len(np.unique(exported.faces)), 3 * len(after))

    def test_cumulative_displacement_is_from_the_previous_stage(self):
        result = mm.generate(self.mesh, steps=2, strength=1.5, units="units", growth=1)
        self.assertEqual(result.amplitudes, [0, 1.5, 3])
        for i in (1, 2):
            subdivided = mm.subdivide(result.stages[i - 1])
            displacement = np.linalg.norm(result.stages[i] - subdivided, axis=-1)
            self.assertLessEqual(displacement.max(), result.amplitudes[i] + 1e-12)
            self.assertGreater(displacement.max(), result.amplitudes[i] * 0.9)
        # Stage 2 inherits stage 1's deformed corners, rather than restarting from the original.
        self.assertFalse(np.allclose(mm.subdivide(result.stages[1]), mm.subdivide(mm.subdivide(self.mesh.triangles))))

    def test_seeds_prefix_stability_and_batch_size(self):
        a = mm.generate(self.mesh, steps=2, seed=7, batch_faces=3)
        b = mm.generate(self.mesh, steps=3, seed=7, batch_faces=17)
        for first, second in zip(a.stages, b.stages):
            np.testing.assert_array_equal(first, second)
        c = mm.generate(self.mesh, steps=2, seed=8)
        self.assertFalse(np.array_equal(a.stages[-1], c.stages[-1]))

    def test_growth_and_relative_scale_are_based_on_original(self):
        result = mm.generate(self.mesh, steps=3, strength=2, growth=2)
        np.testing.assert_allclose(result.amplitudes, np.array([0, 1, 4, 9]) * result.diagonal * 0.02)
        large = self.mesh.copy()
        large.apply_scale(7)
        scaled = mm.generate(large, steps=3, strength=2, growth=2)
        for a, b in zip(result.stages, scaled.stages):
            np.testing.assert_allclose(b, a * 7, atol=1e-11)

    def test_absolute_strength_is_size_independent(self):
        large = self.mesh.copy()
        large.apply_scale(7)
        small_result = mm.generate(self.mesh, steps=1, strength=2, units="units")
        large_result = mm.generate(large, steps=1, strength=2, units="units")
        np.testing.assert_allclose(large_result.stages[1] - mm.subdivide(large.triangles),
                                   small_result.stages[1] - mm.subdivide(self.mesh.triangles), atol=1e-12)

    def test_axis_influence_scales_without_renormalizing(self):
        free = mm.generate(self.mesh, steps=1).stages[1]
        limited = mm.generate(self.mesh, steps=1, influence=(0, 0.5, 1)).stages[1]
        base = mm.subdivide(self.mesh.triangles)
        np.testing.assert_allclose(limited - base, (free - base) * [0, 0.5, 1], atol=1e-12)

    def test_normal_displacement_is_per_corner_along_triangle_normal(self):
        base = mm.subdivide(self.mesh.triangles)
        moved = mm.generate(self.mesh, steps=1, direction="normal").stages[1]
        normals = mm.as_mesh(base).face_normals
        delta = moved - base
        np.testing.assert_allclose(np.cross(delta, normals[:, None, :]), 0, atol=1e-12)
        self.assertFalse(np.allclose(delta[:, 0], delta[:, 1]))

    def test_radial_displacement_uses_original_centre(self):
        self.mesh.apply_translation((8, -3, 17))
        base = mm.subdivide(self.mesh.triangles)
        moved = mm.generate(self.mesh, steps=1, direction="radial").stages[1]
        radial = base - self.mesh.bounds.mean(axis=0)
        np.testing.assert_allclose(np.cross(moved - base, radial), 0, atol=1e-10)

    def test_undefined_normal_and_radial_directions_have_finite_fallbacks(self):
        tri = np.array([[[0., 0, 0], [0, 0, 0], [1, 0, 0]]])
        for direction in ("normal", "radial"):
            first = mm.distort(tri, 2, direction, np.zeros(3), np.ones(3),
                               np.random.default_rng(1), np.random.default_rng(2))
            second = mm.distort(tri, 2, direction, np.zeros(3), np.ones(3),
                                np.random.default_rng(1), np.random.default_rng(2))
            self.assertTrue(np.isfinite(first).all())
            np.testing.assert_array_equal(first, second)
            self.assertGreater(np.linalg.norm(first[0, 0]), 0)

    def test_random_direction_distribution(self):
        directions = mm.random_directions(np.random.default_rng(1), (10000,))
        np.testing.assert_allclose(np.linalg.norm(directions, axis=-1), 1, atol=1e-12)
        np.testing.assert_allclose(directions.mean(axis=0), 0, atol=0.02)
        np.testing.assert_allclose((directions**2).mean(axis=0), 1/3, atol=0.02)

    def test_zero_steps_returns_original_without_mutation(self):
        result = mm.generate(self.mesh, steps=0)
        self.assertEqual(len(result.stages), 1)
        np.testing.assert_array_equal(result.stages[0], self.mesh.triangles)
        self.assertFalse(np.shares_memory(result.stages[0], self.mesh.vertices))

    def test_budget_rejected_before_subdivision(self):
        dense = trimesh.creation.icosphere(subdivisions=1, radius=40)
        with patch.object(mm, "subdivide", side_effect=AssertionError("must not subdivide")):
            with self.assertRaisesRegex(ValueError, r"Largest supported step count: 7"):
                mm.generate(dense, steps=8)
        self.assertEqual(mm.face_counts(100000, 1), [100000, 400000])
        with self.assertRaisesRegex(ValueError, "2,100,000"):
            mm.face_counts(100000, 2)
        with self.assertRaisesRegex(ValueError, "Even stage 0"):
            mm.face_counts(mm.MAX_TOTAL_FACES + 1, 0)

    def test_empty_nonfinite_and_invalid_arguments(self):
        with self.assertRaisesRegex(ValueError, "no usable triangles"):
            mm.generate(trimesh.Trimesh(), steps=0)
        bad = self.mesh.copy()
        bad.vertices[0] = np.nan
        with self.assertRaisesRegex(ValueError, "finite"):
            mm.generate(bad)
        for kwargs in ({"strength": np.inf}, {"strength": -1}, {"growth": 4},
                       {"direction": "unknown"}, {"influence": (np.nan, 1, 1)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                mm.generate(self.mesh, **kwargs)

    def test_cancellation_during_batched_subdivision(self):
        events = []
        def progress(fraction, message):
            events.append((fraction, message))
            if len(events) == 3:
                raise Cancelled()
        with self.assertRaises(Cancelled):
            mm.generate(self.mesh, steps=3, progress=progress, batch_faces=2)
        self.assertEqual(len(events), 3)

    def test_layout_all_axes_clears_actual_bounds_without_changing_stages(self):
        result = mm.generate(self.mesh, steps=3, strength=10)
        saved = [s.copy() for s in result.stages]
        for ax, axis in enumerate("xyz"):
            offsets = mm.layout_offsets(result.stages, axis, 12)
            np.testing.assert_array_equal(offsets[0], 0)
            for i in range(1, len(offsets)):
                left = mm.bounds(result.stages[i-1]) + offsets[i-1]
                right = mm.bounds(result.stages[i]) + offsets[i]
                self.assertAlmostEqual(right[0, ax] - left[1, ax], 12)
                np.testing.assert_array_equal(np.delete(offsets[i], ax), 0)
        for a, b in zip(saved, result.stages):
            np.testing.assert_array_equal(a, b)

    def test_preview_samples_whole_triangles_and_full_bounds(self):
        stages = mm.generate(self.mesh, steps=3).stages
        offsets = mm.layout_offsets(stages, gap=10)
        view = mm.preview(stages, offsets, max_faces=40)
        self.assertLessEqual(len(view.faces), 40)
        self.assertEqual(view.faces_total, sum(map(len, stages)))
        full = np.concatenate([s + offset for s, offset in zip(stages, offsets)])
        np.testing.assert_allclose(view.bounds, mm.bounds(full))
        sampled = view.vertices[view.faces].astype(np.float64)
        for tri in sampled:
            error = np.abs(full - tri).max(axis=(1, 2))
            self.assertLess(error.min(), 1e-4)
        np.testing.assert_array_equal(view.vertices, mm.preview(stages, offsets, max_faces=40).vertices)


class DisintegrateEngineTests(unittest.TestCase):
    def setUp(self):
        registry()
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_disintegrate_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.src = engine.make_sample(self.folder)
        self.original = self.src.read_bytes()

    def run_tool(self, **overrides):
        values = TOOL.values(overrides)
        dst = output_path(TOOL, self.src, values)
        return engine.run(context(), self.src, dst, values), dst

    def test_all_formats_layouts_signatures_and_unshifted_stage_files(self):
        reference = mm.generate(gm.load_mesh(self.src), steps=2)
        for layout in ("final", "progression"):
            for ext in TOOL.output.formats:
                with self.subTest(layout=layout, ext=ext):
                    p = TOOL.values(dict(layout=layout, separate=True))
                    dst = output_path(TOOL, self.src, p, fmt=ext)
                    result = engine.run(context(), self.src, dst, p)
                    self.assertEqual(len(result.outputs), 4)
                    expected = 320 if layout == "final" else 420
                    written = trimesh.load(str(dst), process=False, force="mesh")
                    self.assertEqual(len(written.faces), expected)
                    for i, path in enumerate(result.outputs):
                        self.assertIn(b"atlas-toolkit:mesh-disintegrate 1.1", Path(path).read_bytes()[:300])
                        if i:
                            stage = trimesh.load(path, process=False, force="mesh")
                            np.testing.assert_allclose(stage.triangles, reference.stages[i-1], atol=1e-4)
                            self.assertEqual(len(stage.vertices), 3 * len(stage.faces))
                    if layout == "final":
                        np.testing.assert_allclose(written.triangles, reference.stages[-1], atol=1e-4)
        self.assertEqual(self.src.read_bytes(), self.original)

    def test_dry_run_and_draft_write_nothing_and_match_run(self):
        p = TOOL.values(dict(separate=True))
        dst = output_path(TOOL, self.src, p)
        before = set(self.folder.iterdir())
        dry = engine.run(context("analyse"), self.src, dst, p)
        draft = engine.run(context("draft"), self.src, dst, p)
        self.assertEqual(set(self.folder.iterdir()), before)
        self.assertFalse(dry.outputs or draft.outputs)
        written = engine.run(context(), self.src, dst, p)
        for a, b, c in zip(dry.preview["views"], draft.preview["views"], written.preview["views"]):
            np.testing.assert_array_equal(a["mesh"].vertices, b["mesh"].vertices)
            np.testing.assert_array_equal(a["mesh"].vertices, c["mesh"].vertices)
        exported = trimesh.load(str(dst), process=False, force="mesh")
        np.testing.assert_allclose(written.preview["views"][-1]["mesh"].vertices[
            written.preview["views"][-1]["mesh"].faces], exported.triangles, atol=1e-4)

    def test_existing_stage_folder_and_existing_primary_are_preserved_on_failure(self):
        result, dst = self.run_tool(separate=True)
        first_paths = [Path(p) for p in result.outputs[1:]]
        saved = [p.read_bytes() for p in first_paths]
        again, _ = self.run_tool(separate=True)
        self.assertNotEqual(Path(again.outputs[1]).parent, first_paths[0].parent)
        self.assertEqual(saved, [p.read_bytes() for p in first_paths])
        prior_primary = dst.read_bytes()
        before = set(self.folder.rglob("*"))
        real_save = gm.save_mesh
        def fail_on_stage(mesh, path, tool):
            if "stage_01" in path.name:
                raise OSError("simulated export failure")
            return real_save(mesh, path, tool)
        with patch.object(gm, "save_mesh", side_effect=fail_on_stage):
            with self.assertRaisesRegex(OSError, "simulated"):
                self.run_tool(separate=True)
        self.assertEqual(set(self.folder.rglob("*")), before)
        self.assertEqual(dst.read_bytes(), prior_primary)

    def test_publish_failure_rolls_back_new_stage_folder(self):
        p = TOOL.defaults()
        dst = output_path(TOOL, self.src, p)
        real_replace = engine.os.replace
        def fail_primary(src, target):
            if target == dst:
                raise OSError("primary locked")
            real_replace(src, target)
        before = set(self.folder.rglob("*"))
        with patch.object(engine.os, "replace", side_effect=fail_primary):
            with self.assertRaisesRegex(OSError, "locked"):
                self.run_tool(separate=True)
        self.assertEqual(set(self.folder.rglob("*")), before)

    def test_cancel_during_export_cleans_temporary_files(self):
        flag = [False]
        real_save = gm.save_mesh
        def cancel_after_save(*args):
            result = real_save(*args)
            flag[0] = True
            return result
        p = TOOL.values(dict(separate=True))
        dst = output_path(TOOL, self.src, p)
        with patch.object(gm, "save_mesh", side_effect=cancel_after_save):
            with self.assertRaises(Cancelled):
                engine.run(context(cancelled=lambda: flag[0]), self.src, dst, p)
        self.assertEqual(list(self.folder.iterdir()), [self.src])

    def test_batched_export_formats_preserve_every_triangle_and_index(self):
        triangles = mm.generate(gm.load_mesh(self.src), steps=2).stages[-1]
        for ext in TOOL.output.formats:
            with self.subTest(ext=ext), patch.object(engine, "EXPORT_BATCH_FACES", 7):
                path = self.folder / f"batched{ext}"
                engine._save_mesh(context(), triangles, path)
                loaded = trimesh.load(str(path), process=False, force="mesh")
                self.assertEqual(len(loaded.faces), len(triangles))
                self.assertEqual(len(loaded.vertices), len(triangles) * 3)
                np.testing.assert_allclose(loaded.triangles, triangles, atol=1e-5)
                self.assertIn(b"atlas-toolkit:mesh-disintegrate 1.1", path.read_bytes()[:300])
        self.assertFalse(list(self.folder.glob(".*-chunk.stl")))

    def test_cancellation_inside_each_batched_export_cleans_scratch(self):
        for ext in TOOL.output.formats:
            with self.subTest(ext=ext), patch.object(engine, "EXPORT_BATCH_FACES", 7):
                checks = [0]
                def cancelled():
                    checks[0] += 1
                    return checks[0] >= 5
                dst = self.folder / f"cancelled{ext}"
                stage = mm.generate(gm.load_mesh(self.src), steps=2).stages[-1]
                with self.assertRaises(Cancelled):
                    engine._publish(context(cancelled=cancelled), dst, stage, [stage], True)
                self.assertEqual(list(self.folder.iterdir()), [self.src])

    def test_input_destination_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "input model"):
            engine.run(context(), self.src, self.src, TOOL.defaults())
        self.assertEqual(self.src.read_bytes(), self.original)

    def test_zero_steps_export_and_preview(self):
        for layout in ("final", "progression"):
            result, dst = self.run_tool(steps=0, layout=layout)
            self.assertEqual(result.data["stage_faces"], [20])
            self.assertEqual(result.preview["prefer"], "Progression")
            np.testing.assert_allclose(trimesh.load(str(dst), process=False).bounds,
                                       gm.load_mesh(self.src).bounds, atol=1e-5)

    def test_budget_failure_is_per_file_and_writes_nothing(self):
        trimesh.creation.icosphere(subdivisions=1, radius=40).export(self.src)
        p = TOOL.values(dict(steps=8))
        dst = output_path(TOOL, self.src, p)
        events = {}
        execute(TOOL, Job(TOOL.id, "run", str(self.src), str(dst), dict(p)),
                lambda k, v: events.__setitem__(k, v), lambda: False)
        self.assertIn("error", events)
        self.assertIn("Largest supported step count", events["error"][0])
        self.assertEqual(list(self.folder.iterdir()), [self.src])
        result, _ = self.run_tool(steps=1)
        self.assertEqual(result.status, "Saved")

    def test_imported_scene_transforms_are_preserved(self):
        scene = trimesh.Scene()
        transform = np.eye(4)
        transform[:3, 3] = [50, -20, 11]
        box = trimesh.creation.box(extents=(2, 4, 6))
        scene.add_geometry(box, transform=transform)
        src = self.folder / "scene.glb"
        scene.export(str(src))
        p = TOOL.values(dict(steps=0, layout="final"))
        dst = output_path(TOOL, src, p)
        engine.run(context(), src, dst, p)
        np.testing.assert_allclose(trimesh.load(str(dst), process=False).bounds,
                                   box.bounds + transform[:3, 3])

    def test_all_supported_input_formats(self):
        box = trimesh.creation.box(extents=(2, 4, 6))
        for ext in TOOL.inputs.exts:
            with self.subTest(ext=ext):
                src = self.folder / f"input{ext}"
                if ext in (".glb", ".gltf"):
                    data = trimesh.Scene(box).export(file_type=ext[1:])
                    if isinstance(data, dict):
                        for name, content in data.items():
                            (self.folder / name).write_bytes(content)
                        src = self.folder / "model.gltf"
                    else:
                        src.write_bytes(data)
                else:
                    box.export(str(src))
                result = engine.run(context("draft"), src, None, TOOL.values(dict(steps=1)))
                self.assertEqual(result.data["stage_faces"], [12, 48])

    def test_registered_cli_settings_round_trip_and_skip_own_outputs(self):
        self.assertIs(registry().get("mesh-disintegrate"), TOOL)
        from atlas_toolkit.core.cli import build_parser
        args = build_parser().parse_args(["run", TOOL.id, str(self.src), "--direction", "radial",
                                         "--steps", "3", "--influence", "0", "0.5", "1", "--separate"])
        p = TOOL.values({param.key: getattr(args, param.key) for param in TOOL.param_list})
        self.assertEqual(TOOL.values(json.loads(json.dumps(p))), p)
        self.assertEqual(p.influence, (0, 0.5, 1))
        self.assertTrue(p.separate)
        for name in ("sample_disintegrate_final_2steps.stl", "sample_disintegrate_progression_2steps_stage_01.OBJ",
                     "sample_morph_final_2steps.stl"):     # its own outputs, also those made under the old name
            self.assertFalse(TOOL.inputs.accepts(Path(name)))

    def test_renamed_from_mesh_morph(self):
        """The former id still works on the command line and in workflows/history, and saved settings carry over."""
        from atlas_toolkit.core.cli import build_parser
        from atlas_toolkit.core.settings import ToolSettings
        reg = registry()
        self.assertIs(reg.get("mesh-morph"), TOOL)
        self.assertIn("mesh-morph", reg.tools)
        self.assertNotIn("mesh-morph", list(reg.tools))          # listings show the current id only
        args = build_parser().parse_args(["run", "mesh-morph", str(self.src), "--steps", "3"])
        self.assertIs(reg.get(args.tool), TOOL)
        tools = self.folder / "config" / "tools"
        tools.mkdir(parents=True)
        (tools / "mesh-morph.json").write_text(json.dumps({"values": {"steps": 4}}), encoding="utf-8")
        self.assertEqual(ToolSettings(TOOL, self.folder / "config").values()["steps"], 4)

    def test_output_can_be_drawn_by_hatch_maker(self):
        result, dst = self.run_tool(steps=1, layout="final")
        hatch = registry().get("stl-hatch-maker")
        self.assertTrue(hatch.inputs.accepts(dst))
        out = self.folder / "disintegrated_hatch.svg"
        params = hatch.values(dict(style="none", silhouette=True))
        drawing = hatch.engine_fn()(context(), dst, out, params)
        self.assertTrue(drawing.outputs)
        self.assertIn(b"<desc>stl-hatch-maker</desc>", out.read_bytes())


if __name__ == "__main__":
    unittest.main()
