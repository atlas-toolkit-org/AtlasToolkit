"""Attachment, bounded organic geometry, valid unions and three-file Atlas integration."""
import _isolate  # first: settings and caches stay in a temporary folder
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from atlas_toolkit.core.job import Cancelled, JobContext
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_surface_eject import TOOL, engine, mesh_surface_eject as se, connectors as co


def context(cancelled=lambda: False):
    return JobContext(lambda *_: None, cancelled)


def sphere():
    model = trimesh.creation.icosphere(subdivisions=2)
    model.vertices *= [.7, .55, 1.]
    return model


class ConnectorGeometryTests(unittest.TestCase):
    def test_all_styles_have_embedded_joints_and_one_fused_body(self):
        model = sphere()
        for style in ("cylinder", "bar", "organic"):
            with self.subTest(style=style):
                p = TOOL.values(dict(connectors=True, connector_style=style, count=4, connector_branches=2))
                e = se.extract(model, p)
                result = co.build(e, p, 2.)
                self.assertEqual(len(result.connectors), 4)
                self.assertEqual(len(result.combined.decompose()), 1)
                self.assertGreater(result.combined.volume(), e.original_volume)
                self.assertTrue(se.to_mesh(result.combined).is_volume)
                for connector, part, row in zip(result.connectors, e.parts, result.records):
                    self.assertGreater((connector ^ e.body).volume(), 1e-12)
                    self.assertGreater((connector ^ part).volume(), 1e-12)
                    self.assertTrue(se.to_mesh(part).contains([row["fragment_inside"]])[0])
                    self.assertTrue(se.to_mesh(e.body).contains([row["root_inside"]])[0])
                    self.assertLessEqual(row["lateral_max"], row["extent"] + 1e-10)
                    self.assertEqual(row["branches"], 2 if style == "organic" else 0)

    def test_enabling_connectors_does_not_change_the_sample_or_ejection(self):
        model = sphere()
        floating = se.extract(model, TOOL.values(dict(count=5)))
        connected = se.extract(model, TOOL.values(dict(count=5, connectors=True)))
        self.assertEqual(floating.records, connected.records)
        np.testing.assert_array_equal(se.to_mesh(floating.body).vertices, se.to_mesh(connected.body).vertices)
        for a, b in zip(floating.parts, connected.parts):
            np.testing.assert_array_equal(se.to_mesh(a).vertices, se.to_mesh(b).vertices)
        self.assertFalse(floating.interfaces)
        self.assertEqual(len(connected.interfaces), 5)

    def test_concave_fragment_with_centroid_outside_gets_real_interior_attachment(self):
        from shapely.geometry import Polygon
        model = trimesh.creation.extrude_polygon(Polygon([(0, 0), (3, 0), (3, 1), (1, 1),
                                                        (1, 2), (3, 2), (3, 3), (0, 3)]), .4)
        self.assertFalse(model.contains([model.center_mass])[0])
        rest = co.from_arrays(model.vertices, model.faces)
        body = co.mf.Manifold.cube([3., 3., 1.]).translate([0., 0., -1.])
        part = rest.translate([0., 0., .8])
        interface = model.triangles[model.face_normals[:, 2] < -.9]
        e = se.Extraction(body, [part], [dict(normal=[0., 0., 1.], travel=.8, width=3., depth=.4)], {}, 1,
                          body.volume() + rest.volume(), [interface])
        p = TOOL.values(dict(connectors=True, connector_style="organic", connector_branches=2))
        result = co.build(e, p, 3.)
        self.assertEqual(len(result.combined.decompose()), 1)
        self.assertTrue(se.to_mesh(part).contains([result.records[0]["fragment_inside"]])[0])

    def test_fork_count_extent_and_straight_zero_controls(self):
        model = sphere()
        p = TOOL.values(dict(connectors=True, connector_style="organic", count=3, connector_branches=4,
                             connector_extent=10., connector_spread=80., connector_curve=1.))
        e = se.extract(model, p)
        result = co.build(e, p, 2.)
        for row, source in zip(result.records, e.records):
            self.assertLessEqual(row["branches"], 4)
            self.assertLessEqual(row["lateral_max"], source["width"] * .1 + 1e-10)
        p["connector_extent"] = 0.
        result = co.build(e, p, 2.)
        for row in result.records:
            self.assertEqual(row["branches"], 0)
            self.assertLess(row["lateral_max"], 1e-12)
        p.update(connector_extent=35., connector_branches=0, connector_curve=0., connector_irregularity=0.)
        result = co.build(e, p, 2.)
        self.assertTrue(all(row["branches"] == 0 and row["lateral_max"] < 1e-12 for row in result.records))

    def test_seed_is_repeatable_and_thickness_is_fitted_to_thin_fragments(self):
        p = TOOL.values(dict(connectors=True, connector_style="organic", count=3,
                             connector_branches=2, connector_diameter_min=6., connector_diameter_max=6.))
        e = se.extract(sphere(), p)
        a, b = co.build(e, p, 2.), co.build(e, p, 2.)
        self.assertEqual(a.records, b.records)
        self.assertTrue(all(row["clamped"] for row in a.records))
        self.assertEqual(len(a.combined.decompose()), 1)

    def test_unavailable_cavity_and_invalid_controls_fail_before_publication(self):
        p = TOOL.values(dict(connectors=True, count=1))
        e = se.extract(sphere(), p)
        e.interfaces[0] = np.empty((0, 3, 3))
        with self.assertRaisesRegex(ValueError, "no cavity interface"):
            co.build(e, p, 2.)
        for changes in (dict(fully_detach=False), dict(connector_diameter_min=2., connector_diameter_max=1.)):
            with self.assertRaises(ValueError):
                se.validate(TOOL.values(dict(p, **changes)))
        with self.assertRaises(Cancelled):
            co.build(e, p, 2., context(cancelled=lambda: True))


class ConnectorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="eject_bridges_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = engine.make_sample(self.root)

    def test_combined_is_fused_and_separate_writes_three_labeled_signed_files(self):
        for ext in (".stl", ".obj", ".ply"):
            with self.subTest(ext=ext):
                p = TOOL.values(dict(connectors=True, connector_style="organic", connector_branches=2, count=3))
                dst = self.root / ("fused" + ext)
                result = engine.run(context(), self.src, dst, p)
                fused = gm.load_mesh(dst)
                self.assertTrue(fused.is_volume)
                self.assertEqual(len(fused.split()), 1)
                self.assertEqual(len(result.data["connectors"]), 3)
                p["export_mode"] = "separate"
                dst = self.root / ("layers" + ext)
                result = engine.run(context(), self.src, dst, p)
                self.assertEqual([Path(path).stem for path in result.outputs], ["layers", "layers_parts", "layers_connectors"])
                loaded = [gm.load_mesh(Path(path)) for path in result.outputs]
                for path, mesh in zip(result.outputs, loaded):
                    self.assertTrue(mesh.is_volume)
                    self.assertIn(b"atlas-toolkit:mesh-surface-eject", Path(path).read_bytes())
                reconstructed = co.union([co.from_arrays(item.vertices, item.faces) for item in loaded])
                self.assertEqual(len(reconstructed.decompose()), 1)
                self.assertAlmostEqual(reconstructed.volume(), fused.volume, delta=fused.volume * 2e-6)

    def test_existing_siblings_and_third_file_failure_are_safe(self):
        dst = self.root / "out.stl"
        dst.write_bytes(b"old primary")
        for name in ("out_parts.stl", "out_connectors.stl"):
            (self.root / name).write_bytes(b"old sibling")
        p = TOOL.values(dict(connectors=True, count=2, export_mode="separate"))
        result = engine.run(context(), self.src, dst, p)
        self.assertTrue(result.outputs[1].endswith("out_parts-1.stl"))
        self.assertTrue(result.outputs[2].endswith("out_connectors-1.stl"))
        for name in ("out_parts.stl", "out_connectors.stl"):
            self.assertEqual((self.root / name).read_bytes(), b"old sibling")
        before = {path: path.read_bytes() for path in self.root.glob("*.stl")}
        from atlas_toolkit.geom.mesh_export import save_mesh
        calls = []
        def fail_third(mesh, path, tool):
            calls.append(path)
            if len(calls) == 3:
                raise OSError("third staging failed")
            return save_mesh(mesh, path, tool)
        with patch("atlas_toolkit.geom.mesh_export.save_mesh", side_effect=fail_third), self.assertRaisesRegex(OSError, "third staging"):
            engine.run(context(), self.src, dst, p)
        self.assertEqual({path: path.read_bytes() for path in self.root.glob("*.stl")}, before)

    def test_dense_fork_boolean_slivers_stay_closed_after_every_export_format(self):
        p = TOOL.values(dict(connectors=True, connector_style="organic", connector_branches=3, count=14))
        for ext in (".obj", ".ply", ".stl"):
            with self.subTest(ext=ext):
                path = self.root / ("dense" + ext)
                result = engine.run(context(), self.src, path, p)
                saved = gm.load_mesh(path)
                self.assertTrue(saved.is_volume)
                self.assertTrue(saved.nondegenerate_faces().all())
                self.assertEqual(len(saved.split()), 1)
                self.assertIn("sliver stabilization", result.report)

    def test_schema_workflow_cache_three_outputs_and_connected_drawing(self):
        from atlas_toolkit.core import workflow as W
        from atlas_toolkit.core.registry import registry
        from atlas_toolkit.core.cli import build_parser
        reg = registry()
        args = build_parser().parse_args(["run", TOOL.id, str(self.src), "--connectors", "--connector-style", "organic",
                                         "--connector-branches", "2", "--connector-extent", "20"])
        self.assertTrue(args.connectors)
        wf = W.Workflow(name="Connected fragments")
        nodes = [wf.add(W.input_node("mesh")), wf.add(W.tool_node(reg.get(TOOL.id), values=dict(
                 count=3, connectors=True, connector_style="organic", connector_branches=2, export_mode="separate"))),
                 wf.add(W.save_node(where="folder", folder=str(self.root / "saved")))]
        for a, b in zip(nodes, nodes[1:]):
            wf.connect(a.id, b.id)
        self.assertFalse(W.errors(W.check(wf, reg)))
        cache = W.WorkflowCache(self.root / "cache")
        first = W.run_workflow(wf, self.src, lambda *_: None, lambda: False, reg=reg, cache=cache)
        self.assertEqual(len(first.outputs), 3)
        events = []
        W.run_workflow(wf, self.src, lambda k, p: events.append((k, p)), lambda: False, reg=reg, cache=cache)
        self.assertTrue(any(k == "node" and p[1] == "cached" for k, p in events))
        nodes[1].values["connector_extent"] = 15.
        events = []
        W.run_workflow(wf, self.src, lambda k, p: events.append((k, p)), lambda: False, reg=reg, cache=cache)
        self.assertFalse(any(k == "node" and p[1] == "cached" for k, p in events))
        dst = self.root / "connected.obj"
        engine.run(context(), self.src, dst, TOOL.values(dict(count=3, connectors=True, connector_style="bar")))
        draw = reg.get("contour-lines")
        result = draw.engine_fn()(context(), dst, self.root / "drawing.svg", draw.values(dict(draw.test_values, spacing_mm=3.)))
        self.assertGreater(result.stat("strokes").value, 0)

    def test_connector_controls_are_visible_only_in_their_applicable_modes(self):
        off = TOOL.defaults()
        organic = dict(off, connectors=True, connector_style="organic", connector_branches=2)
        cylinder = dict(organic, connector_style="cylinder")
        self.assertFalse(TOOL.param("connector_style").visible(off))
        for key in ("connector_curve", "connector_taper", "connector_branches", "connector_extent", "connector_spread", "connector_fork"):
            self.assertTrue(TOOL.param(key).visible(organic))
            self.assertFalse(TOOL.param(key).visible(cylinder))


if __name__ == "__main__":
    unittest.main()
