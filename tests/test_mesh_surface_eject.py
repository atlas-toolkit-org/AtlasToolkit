"""Volume/skin/clearance invariants, safe exports, selection UI and Workflow integration."""
import _isolate  # first: settings and caches stay in a temporary folder
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from atlas_toolkit.core.job import JobContext, Cancelled
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.surface import read_frame
from atlas_toolkit.tools.mesh_surface_eject import TOOL, engine
from atlas_toolkit.tools.mesh_surface_eject import mesh_surface_eject as se


def context(mode="run", cancelled=lambda: False):
    return JobContext(lambda *_: None, cancelled, mode)


def sphere():
    model = trimesh.creation.icosphere(subdivisions=2)
    model.vertices *= [.7, .55, 1.]
    return model


class GeometryTests(unittest.TestCase):
    def test_all_shapes_preserve_material_surface_and_orientation_and_clear_obstacles(self):
        model = sphere()
        original, _ = se.solid(model)
        for shape in ("cube", "box", "sphere", "ellipsoid", "cylinder"):
            with self.subTest(shape=shape):
                p = TOOL.values(dict(shape=shape, count=5, segments=16))
                result = se.extract(model, p)
                self.assertEqual(len(result.parts), 5)
                self.assertAlmostEqual(result.body.volume() + sum(r["volume"] for r in result.records), original.volume(), places=10)
                restored = []
                for part, row in zip(result.parts, result.records):
                    delta = np.array(row["normal"]) * row["travel"]
                    rest = part.translate(-delta)
                    restored.append(rest)
                    self.assertAlmostEqual((rest - original).volume(), 0., places=10)
                    self.assertAlmostEqual(rest.volume(), row["volume"], places=10)
                    self.assertGreater(row["skin_area"], 0)
                    self.assertGreater(np.dot(row["anchor"], row["normal"]), 0)
                    self.assertLess((part ^ original).volume(), 1e-12)
                    self.assertGreaterEqual(part.min_gap(original, row["gap"] * 2), row["gap"] - 1e-7)
                    v = se.to_mesh(rest).vertices
                    moved = se.to_mesh(part).vertices
                    # Translation alone: pairwise distances and volume stay unchanged.
                    np.testing.assert_allclose(np.linalg.norm(v - v.mean(0), axis=1), np.linalg.norm(moved - moved.mean(0), axis=1), atol=1e-9)
                rebuilt = result.body
                for piece in restored:
                    rebuilt = rebuilt + piece
                self.assertLess((rebuilt - original).volume() + (original - rebuilt).volume(), 1e-10)
                for i, part in enumerate(result.parts):
                    for j in range(i):
                        self.assertLess((part ^ result.parts[j]).volume(), 1e-12)
                        self.assertGreaterEqual(part.min_gap(result.parts[j], 1.), result.records[i]["gap"] - 1e-7)

    def test_seed_repeats_and_translation_mode_respects_exact_interval(self):
        p = TOOL.values(dict(count=6, fully_detach=False, distance_min=3., distance_max=3.))
        a, b = se.extract(sphere(), p), se.extract(sphere(), p)
        self.assertEqual(a.records, b.records)
        for part, other, row in zip(a.parts, b.parts, a.records):
            np.testing.assert_allclose(se.to_mesh(part).vertices, se.to_mesh(other).vertices)
            self.assertAlmostEqual(row["travel"], .06)

    def test_band_clips_complete_extracted_volumes(self):
        model = sphere()
        p = TOOL.values(dict(region="band", band_from=46., band_to=54., count=6, spacing=0.))
        out = se.extract(model, p)
        for part, row in zip(out.parts, out.records):
            rest = se.to_mesh(part.translate(-np.asarray(row["normal"]) * row["travel"]))
            self.assertGreaterEqual(rest.bounds[0, 2], -.08 - 1e-8)
            self.assertLessEqual(rest.bounds[1, 2], .08 + 1e-8)

    def test_paint_respects_selected_skin_including_coplanar_triangle_boundaries(self):
        model = trimesh.creation.box(extents=[2., 2., 2.])
        selected = np.zeros(len(model.faces), bool)
        selected[np.flatnonzero(model.face_normals[:, 2] > .9)[0]] = True
        p = TOOL.values(dict(region="paint", count=4, size_min=8., size_max=12., spacing=0.))
        out = se.extract(model, p, selected)
        self.assertEqual(len(out.parts), 4)
        for row in out.records:
            self.assertTrue(selected[row["source_faces"]].all())
            np.testing.assert_allclose(row["normal"], [0., 0., 1.], atol=1e-10)
        source, ident = se.solid(model, selected)
        crossing = source ^ se.sampler("cube", .4, .1, [0, 0, 1], [0, 0, 1])
        _, ids = se.source_skin(crossing, ident)
        self.assertTrue(selected[ids].any())
        self.assertTrue((~selected[ids]).any())

    def test_masks_roundtrip_reject_wrong_mesh_empty_and_bad_ranges(self):
        model = sphere()
        mask = model.triangles_center[:, 0] > 0
        text = se.encode_selection(model, mask)
        np.testing.assert_array_equal(se.decode_selection(model, text), mask)
        changed = model.copy()
        changed.vertices += [1, 0, 0]
        with self.assertRaisesRegex(ValueError, "different mesh"):
            se.decode_selection(changed, text)
        with self.assertRaisesRegex(ValueError, "empty"):
            se.decode_selection(model, se.encode_selection(model, np.zeros(len(mask), bool)))
        data = json.loads(text)
        data["ranges"] = [[0, len(mask)]]
        with self.assertRaisesRegex(ValueError, "Invalid selected"):
            se.decode_selection(model, json.dumps(data))

    def test_independent_depth_samplers_reach_requested_depth(self):
        for shape in ("box", "ellipsoid", "cylinder"):
            cutter = se.sampler(shape, .2, .6, [0, 0, 0], [0, 0, 1], 32)
            self.assertAlmostEqual(cutter.bounding_box()[2], -.6, places=8)

    def test_concavity_fallback_still_certifies_full_gap(self):
        # A second body sits above the cavity. The support-plane fallback must clear that obstruction.
        model = trimesh.util.concatenate([trimesh.creation.box([2., 2., 2.]),
                                         trimesh.creation.box([2., 2., .5], transform=trimesh.transformations.translation_matrix([0, 0, 1.65]))])
        body, _ = se.solid(model)
        piece = se.sampler("box", .2, .2, [0, 0, 1], [0, 0, 1]) ^ body
        moved, distance = se.detached(piece, body, [0, 0, 1], np.array([0., 0., 1.]), .1, .2, 2e-7)
        self.assertGreater(distance, 1.)
        self.assertGreaterEqual(moved.min_gap(body, .2), .1)

    def test_invalid_ranges_and_cancellation(self):
        for values in (dict(size_min=20., size_max=10.), dict(depth_min=5., depth_max=2.),
                       dict(distance_min=20., distance_max=10.), dict(depth_max=20.),
                       dict(region="band", band_from=80., band_to=20.)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                se.extract(sphere(), TOOL.values(values))
        with self.assertRaises(Cancelled):
            se.extract(sphere(), TOOL.values(), ctx=context(cancelled=lambda: True))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="atlas_eject_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = engine.make_sample(self.root)

    def test_exports_preserve_units_frame_and_volume_all_formats(self):
        source = gm.load_mesh(self.src)
        for ext in (".stl", ".obj", ".ply"):
            for mode in ("combined", "separate"):
                with self.subTest(ext=ext, mode=mode):
                    dst = self.root / (mode + ext)
                    p = TOOL.values(dict(count=3, export_mode=mode, up_axis="+z", front="-y"))
                    result = engine.run(context(), self.src, dst, p)
                    self.assertEqual(len(result.outputs), 1 if mode == "combined" else 2)
                    saved = [gm.load_mesh(Path(path)) for path in result.outputs]
                    for item in saved:
                        self.assertTrue(item.is_watertight)
                        self.assertTrue(item.is_winding_consistent)
                    self.assertAlmostEqual(sum(item.volume for item in saved), source.volume, delta=source.volume * 2e-6)
                    self.assertEqual(read_frame(dst)["up"], "+z")
                    self.assertIn(b"atlas-toolkit:mesh-surface-eject", dst.read_bytes())

    def test_preview_is_run_geometry_and_separate_extras_never_overwrite(self):
        p = TOOL.values(dict(count=3, export_mode="separate"))
        preview = engine.run(context("preview"), self.src, None, p)
        dst = self.root / "out.stl"
        prior = self.root / "out_parts.stl"
        prior.write_bytes(b"preserve existing sibling")
        run = engine.run(context(), self.src, dst, p)
        self.assertEqual(prior.read_bytes(), b"preserve existing sibling")
        self.assertTrue(run.outputs[1].endswith("out_parts-1.stl"))
        self.assertEqual(preview.data["pieces"], run.data["pieces"])

    def test_paint_selection_file_and_invalid_source_do_not_publish(self):
        original = gm.load_mesh(self.src)
        selected = original.face_normals[:, 0] > .4
        mask = self.root / "mask.json"
        mask.write_text(se.encode_selection(original, selected), encoding="utf-8")
        p = TOOL.values(dict(region="paint", selection_file=str(mask), count=3))
        result = engine.run(context(), self.src, self.root / "paint.obj", p)
        self.assertEqual(len(result.data["pieces"]), 3)
        changed = original.copy()
        changed.vertices += [1, 0, 0]
        changed.export(self.root / "other.stl")
        with self.assertRaisesRegex(ValueError, "different mesh"):
            engine.run(context(), self.root / "other.stl", self.root / "no.stl", p)
        self.assertFalse((self.root / "no.stl").exists())

    def test_strict_open_input_and_explicit_resampling(self):
        model = gm.load_mesh(self.src)
        model.update_faces(np.arange(len(model.faces)) != 0)
        model.export(self.root / "open.stl")
        with self.assertRaisesRegex(ValueError, "closed solid"):
            engine.run(context(), self.root / "open.stl", None, TOOL.values(dict(count=2)))
        out = engine.run(context(), self.root / "open.stl", None,
                         TOOL.values(dict(count=2, repair="resample", repair_resolution=64)))
        self.assertTrue(out.data["repaired"])

    def test_staged_failure_keeps_old_primary_and_publishes_no_extras(self):
        dst = self.root / "out.stl"
        dst.write_bytes(b"previous result")
        from atlas_toolkit.geom.mesh_export import save_mesh
        calls = []
        def failing(mesh, path, tool):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("simulated disk failure")
            return save_mesh(mesh, path, tool)
        with patch("atlas_toolkit.geom.mesh_export.save_mesh", side_effect=failing):
            with self.assertRaisesRegex(OSError, "disk failure"):
                engine.run(context(), self.src, dst, TOOL.values(dict(count=2, export_mode="separate")))
        self.assertEqual(dst.read_bytes(), b"previous result")
        self.assertFalse(list(self.root.glob("out_parts*")))

    def test_dry_run_ignores_destination_and_input_cannot_be_replaced(self):
        dst = self.root / "ignored.stl"
        engine.run(context("draft"), self.src, dst, TOOL.values(dict(count=2)))
        self.assertFalse(dst.exists())
        with self.assertRaisesRegex(ValueError, "replace the input"):
            engine.run(context(), self.src, self.src, TOOL.values(dict(count=2)))

    def test_workflow_cache_separate_outputs_and_selection_file_invalidation(self):
        from atlas_toolkit.core import workflow as W
        from atlas_toolkit.core.registry import registry
        reg = registry()
        wf = W.Workflow(name="Surface extraction")
        nodes = [wf.add(W.input_node("mesh")), wf.add(W.tool_node(reg.get(TOOL.id), values=dict(count=3, export_mode="separate"))),
                 wf.add(W.save_node(where="folder", folder=str(self.root / "saved")))]
        for a, b in zip(nodes, nodes[1:]):
            wf.connect(a.id, b.id)
        self.assertFalse(W.errors(W.check(wf, reg)))
        cache = W.WorkflowCache(self.root / "cache")
        first = W.run_workflow(wf, self.src, lambda *_: None, lambda: False, reg=reg, cache=cache)
        self.assertEqual(len(first.outputs), 2)
        events = []
        W.run_workflow(wf, self.src, lambda k, p: events.append((k, p)), lambda: False, reg=reg, cache=cache)
        self.assertTrue(any(k == "node" and p[1] == "cached" for k, p in events))
        original = gm.load_mesh(self.src)
        mask = self.root / "mask.json"
        mask.write_text(se.encode_selection(original, original.face_normals[:, 0] > .3), encoding="utf-8")
        nodes[1].values.update(region="paint", selection_file=str(mask))
        W.run_workflow(wf, self.src, lambda *_: None, lambda: False, reg=reg, cache=cache)
        mask.write_text(se.encode_selection(original, original.face_normals[:, 1] > .3), encoding="utf-8")
        events = []
        W.run_workflow(wf, self.src, lambda k, p: events.append((k, p)), lambda: False, reg=reg, cache=cache)
        self.assertFalse(any(k == "node" and p[1] == "cached" for k, p in events))

    def test_generated_cli_and_contour_drawing(self):
        from atlas_toolkit.core.cli import build_parser
        from atlas_toolkit.core.registry import registry
        args = build_parser().parse_args(["run", TOOL.id, str(self.src), "--count", "3", "--shape", "ellipsoid"])
        p = TOOL.values({key: getattr(args, key) for key in ("count", "shape")})
        dst = self.root / "extracted.obj"
        engine.run(context(), self.src, dst, p)
        draw = registry().get("contour-lines")
        svg = self.root / "drawing.svg"
        result = draw.engine_fn()(context(), dst, svg, draw.values(dict(draw.test_values, spacing_mm=3.)))
        self.assertGreater(result.stat("strokes").value, 0)
        self.assertIn(b"<desc>contour-lines</desc>", svg.read_bytes())


class SelectionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_visible_paint_does_not_select_backside_and_undo_preserves_mask(self):
        from atlas_toolkit.ui.theme import Theme
        from atlas_toolkit.tools.mesh_surface_eject.viewer import SelectionDialog
        model = sphere()
        dialog = SelectionDialog(model, model, np.zeros(len(model.faces), bool), Theme("dark"))
        self.addCleanup(dialog.close)
        dialog.resize(900, 700)
        dialog.show()
        self.app.processEvents()
        canvas = dialog.canvas
        dialog.remember()
        dialog.paint(canvas.width() / 2, canvas.height() / 2)
        self.assertTrue(dialog.selected.any())
        selected = np.flatnonzero(dialog.selected)
        projected = canvas.project(model.triangles_center[selected])
        origins, directions = canvas.rays(projected[:, :2])
        ids, indexes = dialog.ray.intersects_id(origins, directions, multiple_hits=False)
        np.testing.assert_array_equal(ids, selected[indexes])
        dialog.undo()
        self.assertFalse(dialog.selected.any())
        dialog.fill(True)
        self.assertTrue(dialog.selected.all())
        dialog.undo()
        self.assertFalse(dialog.selected.any())

    def test_modal_commit_persists_selection_and_cancel_preserves_settings(self):
        from PySide6.QtWidgets import QDialog
        from atlas_toolkit.ui.theme import Theme
        from atlas_toolkit.tools.mesh_surface_eject.viewer import SelectionView, SelectionDialog
        class Form:
            def __init__(self):
                self.state = dict(TOOL.defaults())
            def set_values(self, values):
                self.state.update(values)
        class Page:
            def __init__(self):
                self.form, self.theme, self.changed = Form(), Theme("light"), 0
            def values(self):
                return self.form.state
            def _settings_changed(self):
                self.changed += 1
            def preview_selected(self):
                pass
        with tempfile.TemporaryDirectory(prefix="eject_editor_") as folder:
            src = engine.make_sample(folder)
            page = Page()
            view = SelectionView(page)
            self.addCleanup(view.close)
            view.set_input(src, gm.inspect(src))
            def select(dialog):
                dialog.selected[:] = dialog.mesh.face_normals[:, 0] > .4
                return QDialog.Accepted
            with patch.object(SelectionDialog, "exec", select):
                view.open_editor()
            self.assertEqual(page.values()["region"], "paint")
            self.assertTrue(page.values()["selection"])
            self.assertEqual(page.changed, 1)
            values = dict(page.values())
            with patch.object(SelectionDialog, "exec", lambda _: QDialog.Rejected):
                view.open_editor()
            self.assertEqual(page.values(), values)
            self.assertEqual(page.changed, 1)


if __name__ == "__main__":
    unittest.main()
