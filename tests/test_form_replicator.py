"""Form Replicator: geometry, fused solids, source safety, dry runs, CLI, and a cached mesh-to-SVG workflow."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import contextlib
import hashlib
import io
import math
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
import trimesh

from atlas_toolkit.core import workflow as W
from atlas_toolkit.core.cli import build_parser, main
from atlas_toolkit.core.job import Cancelled, JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.form_replicator import LAYOUTS, TOOL
from atlas_toolkit.tools.form_replicator import engine
from atlas_toolkit.tools.form_replicator import form_replicator as fr
from atlas_toolkit.tools.mesh_holes.engine import solid
from atlas_toolkit.tools.mesh_holes.mesh_holes import from_manifold


def context(mode="run", stop=lambda: False):
    return JobContext(lambda k, a: None, stop, mode)


def mesh_of(result):
    data = result.preview["views"][0]["mesh"]
    return trimesh.Trimesh(data.vertices, data.faces)   # merge coincident vertices across boolean provenance


class PlacementTests(unittest.TestCase):
    def test_every_preset_and_layout_has_finite_positive_transforms_and_expected_count(self):
        trials = [_presets.run_values(TOOL, p) for p in _presets.optional(TOOL)]
        trials += [TOOL.values(dict(layout=k)) for k, _ in LAYOUTS]
        for p in trials:
            with self.subTest(layout=p.layout):
                plan = fr.plan(p)
                self.assertEqual(len(plan.transforms), fr.proposed_count(p))
                self.assertTrue(np.isfinite(plan.transforms).all())
                self.assertTrue((np.linalg.det(plan.transforms[:, :, :3]) > 0).all())

    def test_platonic_directions_are_unit_and_balanced(self):
        for symmetry, n in (("tetrahedron", 4), ("octahedron", 6), ("cube", 8),
                            ("icosahedron", 12), ("dodecahedron", 20)):
            points = fr.directions(symmetry)
            self.assertEqual(points.shape, (n, 3))
            np.testing.assert_allclose(np.linalg.norm(points, axis=1), 1)
            np.testing.assert_allclose(points.sum(0), 0, atol=1e-12)

    def test_clock_has_twelve_distinct_positions_clockwise_from_top_in_the_front_plane(self):
        p = TOOL.values(dict(layout="clock", count=12, radius=100))
        positions = fr.plan(p).transforms[:, :, 3]
        np.testing.assert_allclose(positions[0], (0, 0, 2), atol=1e-12)
        np.testing.assert_allclose(positions[3], (2, 0, 0), atol=1e-12)
        self.assertEqual(len(np.unique(np.round(positions, 10), axis=0)), 12)
        np.testing.assert_allclose(positions[:, 1], 0)

    def test_plane_and_phase_rotate_the_complete_layout(self):
        xy = fr.plan(TOOL.values(dict(layout="clock", plane="xy", count=4, phase=90))).transforms[:, :, 3]
        np.testing.assert_allclose(xy[0], (-3.4, 0, 0), atol=1e-12)
        np.testing.assert_allclose(xy[:, 2], 0)

    def test_aim_maps_the_selected_source_axis_without_flips(self):
        d = fr.unit([1, 2, 3])
        for axis, source_axis in (("up", (0, 0, 1)), ("front", (0, -1, 0))):
            matrix = fr.aim(d, d, axis)      # deliberately parallel secondary reference
            np.testing.assert_allclose(matrix @ source_axis, d)
            np.testing.assert_allclose(matrix.T @ matrix, np.eye(3), atol=1e-12)
            self.assertAlmostEqual(np.linalg.det(matrix), 1)

    def test_taper_repeats_down_each_arm(self):
        p = TOOL.values(dict(layout="star2d", per_arm=3, end_scale=25, centre_copy=False))
        matrices = fr.plan(p).transforms[:, :, :3]
        scales = np.cbrt(np.linalg.det(matrices)).reshape(5, 3)
        np.testing.assert_allclose(scales[:, 0], .75)
        np.testing.assert_allclose(scales[:, -1], .75 * .25)

    def test_seeded_variation_and_thinning_keep_surviving_placements(self):
        p = TOOL.values(dict(layout="urchin", count=96, irregularity=.8, seed=42))
        original = fr.plan(p).transforms
        np.testing.assert_array_equal(fr.plan(p).transforms, original)
        changed = fr.plan(TOOL.values(dict(p, seed=43))).transforms
        self.assertFalse(np.array_equal(original, changed))
        sparse = fr.plan(TOOL.values(dict(p, omit=40))).transforms
        self.assertGreater(len(sparse), 0)
        self.assertLess(len(sparse), len(original))
        self.assertTrue(all(any(np.array_equal(a, b) for b in original) for a in sparse))
        p["irregularity"] = 0
        np.testing.assert_array_equal(fr.plan(p).transforms, fr.plan(TOOL.values(dict(p, seed=43))).transforms)

    def test_sphere_hemisphere_and_sunflower_distribution(self):
        for coverage in (90, 180):
            p = TOOL.values(dict(layout="urchin", coverage=coverage, count=100, plane="xy"))
            positions = fr.plan(p).transforms[:, :, 3]
            np.testing.assert_allclose(np.linalg.norm(positions, axis=1), 3.4)
            self.assertTrue((positions[:, 2] >= 0).all() if coverage == 90 else (positions[:, 2] < 0).any())
        p = TOOL.values(dict(layout="sunflower", count=100, plane="xy", radius=100))
        positions = fr.plan(p).transforms[:, :, 3]
        np.testing.assert_allclose(np.linalg.norm(positions, axis=1) ** 2, 4 * (np.arange(100) + .5) / 100)

    def test_trefoil_is_evenly_sampled_with_smooth_frames(self):
        p = TOOL.values(dict(layout="trefoil", count=128, orientation="tangent", plane="xy", copy_size=100))
        transforms = fr.plan(p).transforms
        chords = np.linalg.norm(np.roll(transforms[:, :, 3], -1, axis=0) - transforms[:, :, 3], axis=1)
        self.assertLess(chords.std() / chords.mean(), .02)
        facing = -transforms[:, :, 1]
        self.assertGreater(np.einsum("ij,ij->i", facing, np.roll(facing, -1, axis=0)).min(), .9)

    def test_count_budget_and_impossible_toroid_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "proposed copies"):
            fr.plan(TOOL.values(dict(layout="torus", count=512, rows=32)))
        with self.assertRaisesRegex(ValueError, "central opening"):
            fr.plan(TOOL.values(dict(layout="torus", radius=50, minor_radius=60)))
        with self.assertRaisesRegex(ValueError, "source faces"):
            fr.check_faces(100, 10000, 100000)


class SolidTests(unittest.TestCase):
    def test_overlaps_are_removed_and_gaps_remain_as_closed_components(self):
        cube = trimesh.creation.box(extents=(2, 2, 2))
        source = solid(cube.vertices, cube.faces)
        for distance, volume, bodies in ((1, 12, 1), (2, 16, 1), (3, 16, 2), (0, 8, 1)):
            transforms = np.array([np.column_stack((np.eye(3), [0, 0, 0])),
                                   np.column_stack((np.eye(3), [distance, 0, 0]))])
            output = fr.fuse(source, transforms, 10000)
            self.assertAlmostEqual(output.volume(), volume, places=5)
            self.assertEqual(len(output.decompose()), bodies)
            V, F = from_manifold(output)
            self.assertTrue(trimesh.Trimesh(V, F).is_volume)

    def test_all_layouts_boolean_union_into_valid_solids(self):
        cube = trimesh.creation.box(extents=(.6, .4, 2))
        source = solid(cube.vertices, cube.faces)
        for layout, _ in LAYOUTS:
            with self.subTest(layout=layout):
                p = TOOL.values(dict(layout=layout, count=12, rows=3, per_arm=2))
                output = fr.fuse(source, fr.plan(p).transforms, 100000)
                V, F = from_manifold(output)
                self.assertTrue(trimesh.Trimesh(V, F).is_volume)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_forms_")
        self.root = Path(self.tmp.name)
        self.src = self.root / "source.stl"
        m = trimesh.creation.box(extents=(12, 10, 40))
        m.apply_translation((30, -10, 20))
        m.export(self.src)
        self.values = TOOL.values(dict(TOOL.test_values, up_axis="+z", front="-y"))

    def tearDown(self):
        self.tmp.cleanup()


class EngineTests(Fixture):
    def test_pinched_source_and_export_contacts_are_not_published_as_solids(self):
        cube = trimesh.creation.box(extents=(12, 10, 40))
        other = cube.copy()
        other.apply_translation((12, 10, 0))     # an edge contact: individually closed sheets, pinched when welded
        pinched = trimesh.util.concatenate((cube, other))
        self.assertIsNone(engine.source_solid(pinched.vertices, pinched.faces))
        pinched.export(self.root / "pinched.stl")
        p = TOOL.values(dict(self.values, count=1, repair_open=False))
        with self.assertRaisesRegex(ValueError, "not a closed solid"):
            engine.run(context(), self.root / "pinched.stl", None, p)
        matrices = np.array([np.column_stack((np.eye(3), (0, 0, 0))),
                             np.column_stack((np.eye(3), (.6, .5, 0)))])
        dst = self.root / "old.stl"
        dst.write_bytes(b"keep me")
        with patch.object(fr, "plan", return_value=fr.Plan(matrices, 2, 0)):
            with self.assertRaisesRegex(ValueError, "pinched contacts"):
                engine.run(context(), self.src, dst, TOOL.values(dict(self.values, contact_weld=0)))
        self.assertEqual(dst.read_bytes(), b"keep me")
        with patch.object(fr, "plan", return_value=fr.Plan(matrices, 2, 0)):
            welded = engine.run(context(), self.src, dst, self.values)
        self.assertTrue(gm.load_mesh(dst).is_volume)
        self.assertEqual(welded.data["solids"], 1)
        self.assertEqual(welded.data["contact_growth"], .1)

    def test_cavity_shell_is_not_counted_as_a_second_solid(self):
        import manifold3d
        hollow = manifold3d.Manifold.cube((40, 40, 40), True) - manifold3d.Manifold.cube((20, 20, 20), True)
        V, F = from_manifold(hollow)
        src = self.root / "hollow.stl"
        trimesh.Trimesh(V, F, process=False).export(src)
        res = engine.run(context("analyse"), src, None, TOOL.values(dict(self.values, count=1)))
        self.assertEqual(res.data["solids"], 1)
        self.assertTrue(mesh_of(res).is_volume)
        self.assertAlmostEqual(mesh_of(res).volume, 40 ** 3 - 20 ** 3, delta=.1)

    def test_reduction_that_pinches_a_solid_retries_at_higher_detail(self):
        cube = trimesh.load(self.src)
        bad = cube.copy()
        bad.apply_translation((12, 10, 0))
        pinched = trimesh.util.concatenate((cube, bad))
        reduced = [(pinched.vertices, pinched.faces), (cube.vertices, cube.faces)]
        p = TOOL.values(dict(self.values, count=1, source_faces=4))
        with patch.object(gm, "simplify_faces", side_effect=reduced) as simplify:
            res = engine.run(context("analyse"), self.src, None, p)
        self.assertEqual(simplify.call_count, 2)
        self.assertTrue(mesh_of(res).is_volume)
        self.assertEqual(res.data["source_faces"], 12)

    def test_all_mesh_formats_are_signed_closed_and_preserve_the_source(self):
        before = hashlib.sha256(self.src.read_bytes()).digest()
        for ext in TOOL.output.formats:
            with self.subTest(ext=ext):
                dst = self.root / ("result" + ext)
                res = engine.run(context(), self.src, dst, self.values)
                self.assertEqual(res.outputs, [str(dst)])
                self.assertTrue(gm.read_signature(dst).startswith(TOOL.signature))
                self.assertTrue(gm.load_mesh(dst).is_volume)
        self.assertEqual(hashlib.sha256(self.src.read_bytes()).digest(), before)

    def test_model_units_and_offset_are_preserved(self):
        p = TOOL.values(dict(self.values, count=1, radius=100, copy_size=100, orientation="upright"))
        res = engine.run(context("analyse"), self.src, None, p)
        out = mesh_of(res)
        np.testing.assert_allclose(out.extents, (12, 10, 40), atol=1e-4)
        np.testing.assert_allclose(out.bounds.mean(0), (30, -10, 60), atol=1e-4)

    def test_draft_and_analyse_write_nothing_even_with_a_destination(self):
        dst = self.root / "old.stl"
        dst.write_bytes(b"old output")
        for mode in ("draft", "analyse"):
            res = engine.run(context(mode), self.src, dst, self.values)
            self.assertFalse(res.outputs)
            self.assertEqual(dst.read_bytes(), b"old output")
            self.assertEqual(res.data["copies"], 6)

    def test_rejects_source_overwrite_and_unsupported_extensions(self):
        with self.assertRaisesRegex(ValueError, "replace the input"):
            engine.run(context(), self.src, self.src, self.values)
        with self.assertRaisesRegex(ValueError, "STL, OBJ or PLY"):
            engine.run(context(), self.src, self.root / "bad.svg", self.values)

    def test_failed_or_cancelled_export_preserves_existing_output_and_cleans_scratch(self):
        dst = self.root / "existing.stl"
        dst.write_bytes(b"keep me")
        cancelled = [False]

        def failed_save(m, path, tool):
            path.write_bytes(b"partial")
            raise OSError("disk full")

        with patch.object(gm, "save_mesh", failed_save), self.assertRaisesRegex(OSError, "disk full"):
            engine.run(context(), self.src, dst, self.values)
        self.assertEqual(dst.read_bytes(), b"keep me")

        def cancelled_save(m, path, tool):
            path.write_bytes(b"complete but cancelled")
            cancelled[0] = True

        with patch.object(gm, "save_mesh", cancelled_save), self.assertRaises(Cancelled):
            engine.run(context(stop=lambda: cancelled[0]), self.src, dst, self.values)
        self.assertEqual(dst.read_bytes(), b"keep me")
        self.assertFalse(list(self.root.glob(".form-replicator-*")))

    def test_invalid_budget_is_rejected_before_loading(self):
        p = TOOL.values(dict(self.values, layout="torus", count=512, rows=32))
        with patch.object(gm, "load_mesh") as load, self.assertRaisesRegex(ValueError, "proposed copies"):
            engine.run(context(), self.src, None, p)
        load.assert_not_called()

    def test_open_source_is_either_explained_or_repaired(self):
        m = trimesh.load(self.src)
        m.update_faces(np.arange(len(m.faces) - 1))
        m.export(self.root / "open.stl")
        p = TOOL.values(dict(self.values, count=1, repair_open=False))
        with self.assertRaisesRegex(ValueError, "not a closed solid"):
            engine.run(context("analyse"), self.root / "open.stl", None, p)
        p["repair_open"] = True
        p["repair_resolution"] = 32
        res = engine.run(context("analyse"), self.root / "open.stl", None, p)
        self.assertTrue(res.data["repaired"])
        self.assertTrue(mesh_of(res).is_volume)

    def test_generated_cli_runs_and_dry_run_writes_nothing(self):
        dst = self.root / "cli.stl"
        arguments = ["run", TOOL.id, str(self.src), "--layout", "clock", "--count", "6", "--radius", "65",
                     "--source-faces", "0", "--up-axis=+z", "--front=-y", "-o", str(dst), "--quiet"]
        self.assertEqual(build_parser().parse_args(arguments).tool, TOOL.id)
        with patch("atlas_toolkit.core.cli.History"):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(arguments + ["--dry-run"]), 0)
                self.assertFalse(dst.exists())
                self.assertEqual(main(arguments), 0)
        self.assertTrue(gm.load_mesh(dst).is_volume)

    def test_workflow_vectorises_caches_and_invalidates_changed_composition(self):
        reg = registry()
        wf = W.Workflow(name="Form to drawing")
        inp = wf.add(W.input_node("mesh"))
        form = wf.add(W.tool_node(reg.get(TOOL.id), values=dict(self.values)))
        pv = reg.get("plotter-vision")
        drawing = wf.add(W.tool_node(pv, values=dict(pv.test_values, theta=90, psi=-90,
                                                    projection="orthographic")))
        clean = wf.add(W.tool_node(reg.get("svg-overlap-cleaner"), values=dict(mode="duplicates")))
        save = wf.add(W.save_node(where="folder", folder=str(self.root / "out")))
        for a, b in zip((inp, form, drawing, clean), (form, drawing, clean, save)):
            wf.connect(a.id, b.id)
        self.assertFalse(W.errors(W.check(wf, reg)))
        cache = W.WorkflowCache(self.root / "cache")
        events = []
        run = lambda action="run", target=None: W.run_workflow(
            wf, self.src, lambda k, a: events.append((k, a)), lambda: False,
            reg=reg, cache=cache, action=action, target=target)
        preview = run("preview", form.id)
        self.assertFalse(preview.outputs)
        self.assertFalse((self.root / "out").exists())
        first = run()
        self.assertEqual(len(first.outputs), 1)
        text = Path(first.outputs[0]).read_text(encoding="utf-8")
        self.assertIn("<svg", text)
        self.assertIn("<path", text)
        events.clear()
        run()
        self.assertEqual(sum(k == "node" and a[1] == "cached" for k, a in events), 3)
        form.values["count"] = 5
        events.clear()
        run()
        self.assertEqual(sum(k == "node" and a[1] == "cached" for k, a in events), 0)


class GuiTests(Fixture):
    def test_generated_page_worker_preview_run_themes_history_and_workflow_library(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from atlas_toolkit.core.history import History
        from atlas_toolkit.core.settings import AppSettings
        from atlas_toolkit.ui.bridge import JobBridge
        from atlas_toolkit.ui.context import AppContext
        from atlas_toolkit.ui.shell import MainWindow
        from atlas_toolkit.ui.theme import load_fonts
        from atlas_toolkit.ui.workflow.library import entries
        leave_home = _isolate.fresh_home()
        app = QApplication.instance() or QApplication([])
        load_fonts()
        bridge = JobBridge()
        history = History(self.root / "history.jsonl")
        ctx = AppContext(registry(), AppSettings(self.root / "cfg"), bridge, history)
        win = MainWindow(ctx)

        def wait(condition):
            stop = time.monotonic() + 60
            while time.monotonic() < stop:
                app.processEvents()
                if condition():
                    return
                time.sleep(.01)
            self.fail("Form Replicator GUI worker timed out")

        try:
            page = win.open_tool(TOOL.id, [str(self.src)])
            wait(lambda: page.runnable() and page.idle())
            for preset in _presets.optional(TOOL):
                page.form.set_values(_presets.run_values(TOOL, preset))
                app.processEvents()
            page.form.set_values(self.values)
            page.preview_selected()
            wait(lambda: page.idle() and page.draft_job is None)
            row = page.rows[str(self.src.resolve())]
            self.assertFalse(row.error)
            self.assertEqual(row.result_mode, "draft")
            self.assertEqual(page.stage.current_title(), "Output")
            self.assertFalse(list(self.root.glob("*_forms_*.stl")))
            ctx.set_theme("light")
            ctx.set_art("blueprint")
            ctx.set_theme("dark")
            ctx.set_art("paper")
            page.run_all()
            wait(lambda: not page.busy() and page.idle())
            self.assertFalse(row.error)
            self.assertTrue(all(Path(p).is_file() for p in row.result.outputs))
            self.assertTrue(any(r.tool == TOOL.id and r.status == "done" for r in history.read()))
            self.assertIn("tool:" + TOOL.id, [e[0] for e in entries(ctx.registry)])
        finally:
            win.close()
            bridge.shutdown()
            leave_home()


if __name__ == "__main__":
    unittest.main()
