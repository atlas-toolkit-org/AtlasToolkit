"""Painted motion, mask binding, fixed unpainted skin, UI and workflow cache regression tests."""
import _isolate  # first: settings and caches stay in a temporary folder
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import numpy as np
import trimesh
from atlas_toolkit.core.job import JobContext, Cancelled
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_motion import TOOL
from atlas_toolkit.tools.mesh_motion.engine import run
from atlas_toolkit.tools.mesh_motion.selection import PaintedInfluence, encode_selection, decode_selection
from atlas_toolkit.tools.mesh_motion.mesh_motion import sculpt


def context(mode="run"):
    return JobContext(lambda *_: None, lambda: False, mode=mode)


class PaintedMotionTests(unittest.TestCase):
    def setUp(self):
        self.mesh = trimesh.creation.icosphere(subdivisions=2)
        self.mask = self.mesh.face_normals[:, 0] < -.2
        self.p = TOOL.values(dict(influence="paint", layers=6, resolution=4, reach=10., taper=10.,
                                 curl=1., preserve=0., hold_base=0., paint_feather=15., paint_style="connected"))

    def test_exact_surface_weights_do_not_leak_to_hidden_unselected_skin(self):
        field = PaintedInfluence(self.mesh, self.mask, .3, context())
        weights = field(self.mesh.triangles_center)
        np.testing.assert_array_equal(weights[~self.mask], 0.)
        self.assertGreater(weights[self.mask].max(), .9)
        all_field = PaintedInfluence(self.mesh, np.ones(len(self.mask), bool), .3, context())
        np.testing.assert_array_equal(all_field(self.mesh.vertices), 1.)

    def test_painted_sculpt_preserves_unselected_surface_and_closed_shells(self):
        field = PaintedInfluence(self.mesh, self.mask, .3, context())
        out, info = sculpt(self.mesh, self.p, context(), field)
        self.assertTrue(out.is_watertight)
        self.assertTrue(out.is_winding_consistent)
        self.assertGreater(info["paint_guard_scale"], 0.)
        # Whole unpainted source triangles, including the hidden rear side, stay on their original surface.
        distance = trimesh.proximity.closest_point(out, self.mesh.triangles_center[~self.mask])[1]
        self.assertLess(distance.max(), 1e-8)
        self.assertLess(out.bounds[0, 0], self.mesh.bounds[0, 0]-.01)

    def test_missing_mask_rejected_in_geometry_adapter(self):
        with self.assertRaisesRegex(ValueError, "Paint an area"):
            sculpt(self.mesh, self.p, context())

    def test_exact_mesh_binding_empty_and_invalid_masks(self):
        text = encode_selection(self.mesh, self.mask)
        np.testing.assert_array_equal(decode_selection(self.mesh, text), self.mask)
        changed = self.mesh.copy()
        changed.vertices += [1, 0, 0]
        for model, value in [(changed, text), (self.mesh, "{}"),
                             (self.mesh, encode_selection(self.mesh, np.zeros(len(self.mask), bool)))]:
            with self.assertRaises(ValueError):
                decode_selection(model, value)

    def test_engine_mask_file_parity_draft_source_protection_and_invalid_file(self):
        with tempfile.TemporaryDirectory() as temp:
            src, dst, mask_path = (Path(temp)/name for name in ("input.stl", "output.stl", "selection.json"))
            self.mesh.export(src)
            original = gm.load_mesh(src)
            text = encode_selection(original, original.face_normals[:, 0] < -.2)
            mask_path.write_text(text)
            values = {**dict(self.p), "selection_file": str(mask_path)}
            a = run(context(), src, dst, TOOL.values(values))
            b = run(context("draft"), src, None, TOOL.values({**values, "selection_file": "", "selection": text}))
            np.testing.assert_array_equal(a.preview["views"][0]["mesh"].vertices,
                                          b.preview["views"][0]["mesh"].vertices)
            self.assertTrue(gm.load_mesh(dst).is_watertight)
            for ext in ("obj", "ply"):
                other = dst.with_suffix("."+ext)
                run(context(), src, other, TOOL.values(values))
                self.assertTrue(gm.load_mesh(other).is_watertight)
            before = dst.read_bytes()
            mask_path.write_text("{}")
            with self.assertRaises(ValueError):
                run(context(), src, dst, TOOL.values(values))
            self.assertEqual(dst.read_bytes(), before)

    def test_field_cancellation(self):
        field = PaintedInfluence(self.mesh, self.mask, .3, JobContext(lambda *_: None, lambda: True))
        with self.assertRaises(Cancelled):
            field(self.mesh.vertices)

    def test_local_guard_does_not_weaken_safe_disconnected_area(self):
        from atlas_toolkit.tools.mesh_motion.selection import protect
        rest = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0],
                         [10, 0, 0], [11, 0, 0], [10, 1, 0]])
        target = rest.copy()
        target[1, 0] = -1
        target[3:, 0] += 5
        output, strength = protect(rest, target, np.array([[0, 1, 2], [3, 4, 5]]), context())
        np.testing.assert_array_equal(output[3:], target[3:])
        self.assertGreater(output[1, 0], 0.)
        self.assertGreater(strength, .85)

    def test_sliced_paint_has_real_gaps_and_layer_resolution_accuracy(self):
        field = PaintedInfluence(self.mesh, self.mask, .3, context())
        p = TOOL.values({**dict(self.p), "paint_style": "layers", "gap": 40.})
        out, info = sculpt(self.mesh, p, context(), field)
        self.assertTrue(out.is_watertight)
        pieces = sorted(out.split(), key=lambda part: part.bounds.mean(axis=0)[2])
        self.assertEqual(len(pieces), p.layers)
        for first, second in zip(pieces, pieces[1:]):
            self.assertGreater(second.bounds[0, 2]-first.bounds[1, 2], 1e-7)
        distance = trimesh.proximity.closest_point(out, self.mesh.triangles_center[~self.mask])[1]
        self.assertLess(distance.max(), info["paint_section_pitch"])
        self.assertGreater(info["paint_clearance"], 0.)
        self.assertEqual(info["paint_guard_scale"], 1.)

    def test_swept_wake_reach_increases_without_global_motion_attenuation(self):
        field = PaintedInfluence(self.mesh, self.mask, .3, context())
        values = {**dict(self.p), "paint_style": "layers", "gesture": "stream", "variation": 0.}
        short, first = sculpt(self.mesh, TOOL.values({**values, "reach": 35.}), context(), field)
        long, second = sculpt(self.mesh, TOOL.values({**values, "reach": 70.}), context(), field)
        self.assertGreater(short.bounds[0, 0]-long.bounds[0, 0], .3)
        self.assertGreater(second["max_reach"], first["max_reach"]*1.9)
        self.assertEqual(second["paint_guard_scale"], 1.)

    def test_sampled_layers_preserve_section_holes(self):
        model = trimesh.creation.annulus(r_min=.35, r_max=.7, height=2., sections=24)
        field = PaintedInfluence(model, np.ones(len(model.faces), bool), .3, context())
        p = TOOL.values({**dict(self.p), "paint_style": "layers", "reach": 0., "gap": 0.})
        out, _ = sculpt(model, p, context(), field)
        self.assertTrue(out.is_watertight)
        self.assertAlmostEqual(out.volume, model.volume, delta=model.volume*.001)

    def test_sliced_painted_export_reloads_for_both_structures(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp)/"input.stl"
            self.mesh.export(src)
            original = gm.load_mesh(src)
            text = encode_selection(original, original.face_normals[:, 0] < -.2)
            for structure in ("laminae", "splinters"):
                p = TOOL.values({**dict(self.p), "paint_style": "layers", "structure": structure, "lanes": 3,
                                 "selection": text, "selection_file": "", "reach": 35.})
                for ext in ("stl", "obj", "ply"):
                    dst = Path(temp)/(structure+"."+ext)
                    result = run(context(), src, dst, p)
                    saved = gm.load_mesh(dst)
                    self.assertTrue(saved.is_watertight)
                    self.assertGreater(len(saved.split()), 1)
                    self.assertGreater(result.data["paint_guard_scale"], 0.)

    def test_single_coplanar_painted_face_keeps_interior_support(self):
        model = trimesh.creation.box(extents=(1., 1., 2.))
        mask = np.zeros(len(model.faces), bool)
        mask[np.flatnonzero(model.face_normals[:, 0] < -.5)[0]] = True
        field = PaintedInfluence(model, mask, .2, context())
        p = TOOL.values({**dict(self.p), "paint_style": "layers", "gap": 0., "layers": 2,
                         "reach": 20., "taper": 0., "curl": 0., "paint_feather": 10.})
        output, _ = sculpt(model, p, context(), field)
        self.assertLess(output.bounds[0, 0], -.51)
        distance = trimesh.proximity.closest_point(output, model.triangles_center[~mask])[1]
        self.assertLess(distance.max(), 1e-5)

    def test_splinters_and_tilted_layers_keep_closed(self):
        field = PaintedInfluence(self.mesh, self.mask, .3, context())
        p = TOOL.values({**dict(self.p), "structure": "splinters", "lanes": 3, "tilt": 15.})
        out, _ = sculpt(self.mesh, p, context(), field)
        self.assertTrue(out.is_watertight)
        self.assertTrue(out.is_winding_consistent)

    def test_workflow_reuses_mask_and_invalidates_cache_when_file_changes(self):
        from atlas_toolkit.core import workflow as W
        from atlas_toolkit.core.registry import registry
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            src, mask_path = root/"input.stl", root/"paint.json"
            self.mesh.export(src)
            model = gm.load_mesh(src)
            mask_path.write_text(encode_selection(model, model.face_normals[:, 0] < -.2))
            reg = registry()
            wf = W.Workflow(name="Painted motion")
            nodes = [wf.add(W.input_node("mesh")),
                     wf.add(W.tool_node(reg.get(TOOL.id), values={**dict(self.p), "selection_file": str(mask_path)})),
                     wf.add(W.save_node(where="folder", folder=str(root/"saved")))]
            for a, b in zip(nodes, nodes[1:]):
                wf.connect(a.id, b.id)
            self.assertFalse(W.errors(W.check(wf, reg)))
            cache = W.WorkflowCache(root/"cache")
            W.run_workflow(wf, src, lambda *_: None, lambda: False, reg=reg, cache=cache)
            events = []
            W.run_workflow(wf, src, lambda k, p: events.append((k, p)), lambda: False, reg=reg, cache=cache)
            self.assertTrue(any(k == "node" and p[1] == "cached" for k, p in events))
            mask_path.write_text(encode_selection(model, model.face_normals[:, 0] < -.4))
            events = []
            W.run_workflow(wf, src, lambda k, p: events.append((k, p)), lambda: False, reg=reg, cache=cache)
            self.assertFalse(any(k == "node" and p[1] == "cached" for k, p in events))


class PaintedMotionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_shared_editor_commit_cancel_validation_and_model_binding(self):
        from PySide6.QtWidgets import QDialog
        from atlas_toolkit.ui.theme import Theme
        from atlas_toolkit.tools.mesh_motion.viewer import SelectionView, SelectionDialog
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
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp)/"input.stl"
            trimesh.creation.icosphere(subdivisions=1).export(src)
            page = Page()
            view = SelectionView(page)
            self.addCleanup(view.close)
            view.set_input(src, gm.inspect(src))
            page.form.set_values({"influence": "paint"})
            self.assertIn("Paint an area", view.validation_error(src))
            def select(dialog):
                self.assertIn("Motion Sculpture", dialog.windowTitle())
                dialog.selected[:] = dialog.mesh.face_normals[:, 0] < -.2
                return QDialog.Accepted
            with patch.object(SelectionDialog, "exec", select):
                view.open_editor()
            self.assertEqual(page.changed, 1)
            self.assertEqual(page.values()["influence"], "paint")
            self.assertEqual(view.validation_error(src), "")
            selected = decode_selection(gm.load_mesh(src), page.values()["selection"])
            self.assertTrue(selected.any())
            before = dict(page.values())
            with patch.object(SelectionDialog, "exec", lambda _: QDialog.Rejected):
                view.open_editor()
            self.assertEqual(before, page.values())
            self.assertEqual(page.changed, 1)


if __name__ == "__main__":
    unittest.main()
