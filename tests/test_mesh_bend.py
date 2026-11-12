"""Geometric invariants and Atlas publication/drawing/workflow coverage for Bend."""
import _isolate  # first: settings and caches stay in a temporary folder
import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.job import Cancelled, JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom import mesh as gm, stages
from atlas_toolkit.geom.surface import read_frame
from atlas_toolkit.tools.mesh_bend import TOOL, engine
from atlas_toolkit.tools.mesh_bend import mesh_bend as mb


def context(mode="run", emit=lambda *_: None):
    return JobContext(emit, lambda: False, mode)


BOUNDS = np.array([[-.2, -.15, -1.], [.2, .15, 1.]])


class GeometryTests(unittest.TestCase):
    def test_arc_matches_closed_form_at_arbitrary_points_and_extrapolated_ends(self):
        p = TOOL.values(dict(profile="arc", angle=90., bend_from=20., bend_to=80., fold_guard=False))
        spine = mb.Spine(BOUNDS, p)
        points = np.random.default_rng(4).uniform(BOUNDS[0], BOUNDS[1], (300, 3))
        k = math.radians(p.angle) / spine.length
        s = np.clip(points[:, 2] - spine.start, 0, spine.length)
        theta = k * s
        extension = points[:, 2] - spine.start - s
        expected = np.column_stack(((1 - np.cos(theta)) / k + points[:, 0] * np.cos(theta) + extension * np.sin(theta),
                                     points[:, 1], spine.start + np.sin(theta) / k - points[:, 0] * np.sin(theta) + extension * np.cos(theta)))
        np.testing.assert_allclose(spine.deform(points), expected, atol=2e-7)

    def test_zero_is_exact_and_tiny_angles_are_continuous(self):
        points = np.random.default_rng(2).uniform(BOUNDS[0], BOUNDS[1], (40, 3))
        for kind in ("arc", "soft", "s", "wave"):
            zero = mb.Spine(BOUNDS, TOOL.values(dict(angle=0., profile=kind, sweep=600.)))
            np.testing.assert_array_equal(zero.deform(points), points)
        tiny = mb.Spine(BOUNDS, TOOL.values(dict(angle=1e-7)))
        np.testing.assert_allclose(tiny.deform(points), points, atol=4e-9)

    def test_anchor_section_and_outside_parts_are_rigid(self):
        for anchor, t in (("base", 0.), ("centre", .5), ("tip", 1.)):
            p = TOOL.values(dict(anchor=anchor, angle=100., sweep=160., roll=130., bend_from=20., bend_to=80., neutral_u=8.))
            spine = mb.Spine(BOUNDS, p)
            section = np.array([[-.1, -.1, spine.start + t * spine.length], [.1, .1, spine.start + t * spine.length]])
            np.testing.assert_allclose(spine.deform(section), section, atol=2e-12)
            for z in (-1., .95):
                q = np.array([[.1, .1, z], [-.1, .1, z + .03], [0., 0., z + .02]])
                r = spine.deform(q)
                np.testing.assert_allclose(np.linalg.norm(q[:, None] - q, axis=2), np.linalg.norm(r[:, None] - r, axis=2), atol=1e-12)
            if anchor == "base":
                np.testing.assert_allclose(spine.deform(np.array([[.1, -.1, -.9]])), [[.1, -.1, -.9]], atol=1e-12)

    def test_sections_keep_distances_and_spine_keeps_arc_length(self):
        p = TOOL.values(dict(profile="soft", angle=125., sweep=300., roll=200.))
        spine = mb.Spine(BOUNDS, p, samples=4097)
        s = np.linspace(-1, 1, 1025)
        C, N, V, T = spine.sections(s)
        np.testing.assert_allclose(np.linalg.norm(T, axis=1), 1, atol=1e-12)
        np.testing.assert_allclose(np.einsum("ij,ij->i", N, V), 0, atol=1e-12)
        np.testing.assert_allclose(np.cross(N, V), T, atol=1e-12)
        self.assertAlmostEqual(np.linalg.norm(np.diff(C, axis=0), axis=1).sum(), 2., delta=2e-5)

    def test_direction_sign_axis_and_scale_covariance(self):
        points = np.array([[0., 0., 1.], [.1, -.1, .2]])
        a = mb.Spine(BOUNDS, TOOL.values(dict(angle=60., profile="arc"))).deform(points)
        b = mb.Spine(BOUNDS, TOOL.values(dict(angle=-60., direction=180., profile="arc"))).deform(points)
        np.testing.assert_allclose(a, b, atol=1e-12)
        B = mb.basis("x")
        p = TOOL.values(dict(angle=60., profile="arc", bend_axis="x"))
        c = mb.Spine(BOUNDS @ B.T, p).deform(points @ B.T)
        np.testing.assert_allclose(c, a @ B.T, atol=1e-12)
        d = mb.Spine(BOUNDS * 31., TOOL.values(dict(angle=60., profile="arc"))).deform(points * 31.)
        np.testing.assert_allclose(d, a * 31., atol=1e-11)

    def test_guard_prevents_inner_folding_and_reports_reduction(self):
        bounds = np.array([[-.9, -.8, -1.], [.9, .8, 1.]])
        for kind in ("arc", "soft", "s", "wave"):
            p = TOOL.values(dict(profile=kind, angle=280., sweep=500., roll=240., bend_from=30., bend_to=70.))
            spine, scale = mb.protected_spine(bounds, p, 1.)
            self.assertLess(scale, 1.)
            self.assertGreaterEqual(spine.min_jacobian, .15)
            self.assertEqual(spine.roll, 240.)
        off, scale = mb.protected_spine(bounds, TOOL.values(dict(angle=280., fold_guard=False)), 1.)
        self.assertEqual(scale, 1.)
        self.assertLess(off.min_jacobian, .15)

    def test_invalid_range_and_flat_axis_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "From must be below"):
            mb.Spine(BOUNDS, TOOL.values(dict(bend_from=80., bend_to=20.)))
        with self.assertRaisesRegex(ValueError, "no extent"):
            mb.Spine(np.array([[0., 0., 0.], [1., 1., 0.]]), TOOL.values())

    def test_fold_bound_agrees_with_numerical_mapping_jacobians(self):
        points = np.random.default_rng(18).uniform(BOUNDS[0], BOUNDS[1], (300, 3))
        for kind in ("arc", "soft", "s", "wave"):
            p = TOOL.values(dict(profile=kind, angle=150., sweep=230., roll=170., anchor="centre",
                                 bend_from=10., bend_to=90., neutral_u=5.))
            spine, _ = mb.protected_spine(BOUNDS, p, 1.)
            columns = []
            for axis in range(3):
                delta = np.eye(3)[axis] * 1e-6
                columns.append((spine.deform(points + delta) - spine.deform(points - delta)) / 2e-6)
            determinants = np.linalg.det(np.stack(columns, axis=2))
            self.assertGreater(determinants.min(), .14)
            self.assertGreaterEqual(determinants.min(), spine.min_jacobian - 1e-3)

    def test_refinement_preserves_closed_and_open_topology_without_shrinking(self):
        for closed in (True, False):
            mesh = trimesh.creation.box(extents=[.4, .3, 2.])
            if not closed:
                mesh.update_faces(np.arange(len(mesh.faces)) >= 2)
            before = mesh.euler_number
            p = TOOL.values(dict(segments=16))
            spine = mb.Spine(mesh.bounds, p)
            v, f = mb.refine(mesh.vertices, mesh.faces, spine, 16, 200_000, context())
            refined = trimesh.Trimesh(v, f, process=False)
            self.assertEqual(refined.is_watertight, closed)
            self.assertTrue(refined.is_winding_consistent)
            self.assertEqual(refined.euler_number, before)
            np.testing.assert_array_equal(refined.bounds, mesh.bounds)
            self.assertAlmostEqual(refined.area, mesh.area, places=10)
            self.assertTrue(refined.nondegenerate_faces().all())
            self.assertGreater(len(f), len(mesh.faces))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.reg = registry()
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_bend_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.src = engine.make_sample(self.folder)

    def test_signed_formats_units_frames_and_saved_preview_agree(self):
        for ext in (".stl", ".obj", ".ply"):
            dst = self.folder / ("result" + ext)
            p = TOOL.values(dict(segments=12, up_axis="+z", front="-y"))
            result = engine.run(context(), self.src, dst, p)
            saved = gm.load_mesh(dst)
            self.assertIn(b"atlas-toolkit:mesh-bend", dst.read_bytes()[:80])
            self.assertEqual(read_frame(dst)["up"], "+z")
            self.assertTrue(saved.is_watertight)
            self.assertTrue(saved.is_winding_consistent)
            self.assertGreater(saved.volume, 0)
            view = result.preview["views"][-1]["mesh"]
            np.testing.assert_allclose(saved.bounds, view.bounds, rtol=1e-6, atol=1e-5)
            self.assertEqual(len(saved.faces), view.faces_total)
            self.assertLess(np.max(saved.extents), 250.)

    def test_zero_no_subdivision_and_no_coordinate_drift(self):
        result = engine.run(context(), self.src, None, TOOL.values(dict(amount=0., segments=192)))
        before, after = [v["mesh"] for v in result.preview["views"]]
        np.testing.assert_array_equal(before.vertices, after.vertices)
        self.assertEqual(before.faces_total, after.faces_total)

    def test_sequence_manifest_and_common_frame_and_primary(self):
        dst = self.folder / "sequence.stl"
        result = engine.run(context(), self.src, dst, TOOL.values(dict(stages=3, segments=12, layout="progression")))
        self.assertEqual(len(result.outputs), 4)
        manifest = stages.read_manifest(Path(result.outputs[1]).parent)
        self.assertEqual(manifest["tool"], TOOL.id)
        self.assertEqual([row["amount"] for row in manifest["stages"]], [0., .5, 1.])
        np.testing.assert_allclose(gm.load_mesh(result.outputs[1]).bounds, gm.load_mesh(self.src).bounds, atol=1e-5)
        self.assertEqual(result.preview["prefer"], "Progression")
        self.assertEqual(len(gm.load_mesh(dst).faces), 3 * result.data["faces_per_stage"])

    def test_dry_runs_and_same_path_and_budget_failure_write_nothing(self):
        before = set(self.folder.iterdir())
        for mode in ("draft", "analyse"):
            engine.run(context(mode), self.src, self.folder / "ignored.stl", TOOL.values(dict(segments=8)))
        self.assertEqual(before, set(self.folder.iterdir()))
        with self.assertRaisesRegex(ValueError, "replace the input"):
            engine.run(context(), self.src, self.src, TOOL.values())
        coarse = self.folder / "coarse.stl"
        trimesh.creation.box(extents=[20., 20., 100.]).export(coarse)
        dst = self.folder / "existing.stl"
        dst.write_bytes(b"keep me")
        with self.assertRaisesRegex(ValueError, "refinement needs"):
            engine.run(context(), coarse, dst, TOOL.values(dict(segments=192, max_faces=10_000)))
        self.assertEqual(dst.read_bytes(), b"keep me")
        self.assertFalse(list(self.folder.glob(".mesh-bend-*")))

    def test_cancel_rolls_back_staged_outputs(self):
        dst = self.folder / "cancelled.stl"
        flag = [False]
        def emit(kind, args):
            if kind == "progress" and "stage 2" in args[1]:
                flag[0] = True
        ctx = JobContext(emit, lambda: flag[0])
        before = set(self.folder.iterdir())
        with self.assertRaises(Cancelled):
            engine.run(ctx, self.src, dst, TOOL.values(dict(stages=3, segments=8)))
        self.assertEqual(before, set(self.folder.iterdir()))

    def test_bent_model_draws_actual_plotter_svg(self):
        dst = self.folder / "bent.stl"
        engine.run(context(), self.src, dst, TOOL.values(dict(segments=12)))
        tool = self.reg.get("contour-lines")
        svg = self.folder / "bent_contours.svg"
        result = tool.engine_fn()(context(), dst, svg, tool.values(dict(tool.test_values, spacing_mm=3.)))
        self.assertTrue(svg.exists())
        self.assertIn(b"<desc>contour-lines</desc>", svg.read_bytes())
        self.assertGreater(result.stat("strokes").value, 0)

    def test_workflow_run_and_cache_use_schema_values(self):
        from atlas_toolkit.core import workflow as W
        wf = W.Workflow(name="Bend test")
        nodes = [wf.add(W.input_node("mesh")),
                 wf.add(W.tool_node(self.reg.get(TOOL.id), values=dict(segments=8, angle=40.))),
                 wf.add(W.save_node(where="folder", folder=str(self.folder / "saved")))]
        for a, b in zip(nodes, nodes[1:]):
            wf.connect(a.id, b.id)
        self.assertFalse(W.errors(W.check(wf, self.reg)))
        cache = W.WorkflowCache(self.folder / "cache")
        first = W.run_workflow(wf, self.src, lambda *_: None, lambda: False, reg=self.reg, cache=cache)
        self.assertTrue(first.outputs)
        events = []
        W.run_workflow(wf, self.src, lambda k, p: events.append((k, p)), lambda: False, reg=self.reg, cache=cache)
        self.assertTrue(any(k == "node" and p[1] == "cached" for k, p in events))


class SpineEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_editor_paints_both_themes_commits_and_gates_reversed_range(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from atlas_toolkit.ui.theme import Theme
        from atlas_toolkit.tools.mesh_bend.viewer import SpineView

        class Form:
            data = dict(TOOL.defaults())
            def set_values(self, values):
                self.data.update(values)

        class Page:
            theme = Theme("dark")
            form = Form()
            changed = 0
            def values(self):
                return self.form.data
            def _settings_changed(self):
                self.changed += 1
            def preview_selected(self):
                pass

        with tempfile.TemporaryDirectory(prefix="atlas_bend_view_") as folder:
            src = engine.make_sample(folder)
            page = Page()
            editor = SpineView(page)
            messages = []
            editor.info_changed.connect(messages.append)
            self.addCleanup(editor.close)
            editor.resize(800, 600)
            editor.set_input(src, gm.inspect(src))
            editor.show()
            self.app.processEvents()
            self.assertIsNotNone(editor.spine)
            self.assertIn("Requested spine", messages[-1])
            self.assertFalse(editor.grab().isNull())
            self.assertEqual(set(editor.handles), {"bend_from", "bend_to"})
            editor.commit({"angle": 85.})
            self.assertEqual(page.values()["angle"], 85.)
            self.assertGreater(page.changed, 0)
            QTest.mousePress(editor.canvas, Qt.LeftButton, pos=editor.canvas.rect().center())
            self.assertEqual(editor.drag[0], "angle")
            QTest.mouseRelease(editor.canvas, Qt.LeftButton, pos=editor.canvas.rect().center())
            self.assertIsNone(editor.drag)
            editor.set_theme(Theme("light"))
            self.app.processEvents()
            self.assertFalse(editor.grab().isNull())
            editor.commit({"bend_from": 80., "bend_to": 20.})
            self.assertIn("From must be below", editor.validation_error(src))
            editor.set_input(None, None)
            self.assertIsNone(editor.spine)
            self.assertFalse(editor.preview_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
