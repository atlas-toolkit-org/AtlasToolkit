"""Stripe solver units/orientation, genuine closed band solids, cache, subprocess lifecycle and Atlas contracts."""
from __future__ import annotations

import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
import trimesh

from atlas_toolkit.core.job import Cancelled, JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.tools.mesh_stripes import TOOL, backend, engine
from atlas_toolkit.tools.mesh_stripes import mesh_stripes as ms


def context(mode="analyse"):
    return JobContext(lambda k, a: None, lambda: False, mode)


def synthetic_curves(points, edges):
    body = trimesh.creation.box((2., 2., 2.))
    phase = body.vertices[body.faces, 1]*2*np.pi/.5
    return dict(schema=backend.PROTOCOL, mesh_vertices=body.vertices.tolist(), mesh_faces=body.faces.tolist(),
                families=[dict(id=0, singular_faces=0, vertices=np.asarray(points).tolist(), edges=edges,
                               corner_phase=phase.tolist())])


class BandGeometryTests(unittest.TestCase):
    def setUp(self):
        self.body = trimesh.creation.box((2., 2., 2.))

    def test_flat_band_width_height_and_positive_closed_union(self):
        data = synthetic_curves([[-.8, 0, 1], [.8, 0, 1]], [[0, 1]])
        solid, info = ms.raised_bands(self.body, data, .2, .04, .5)
        m = solid.to_mesh()
        out = trimesh.Trimesh(np.asarray(m.vert_properties)[:, :3], m.tri_verts, process=False)
        self.assertTrue(out.is_watertight)
        self.assertTrue(out.is_winding_consistent)
        self.assertGreater(out.volume, self.body.volume)
        self.assertAlmostEqual(out.bounds[1, 2], 1.04, places=5)
        top = out.vertices[(out.vertices[:, 2] > 1.039) & (np.abs(out.vertices[:, 0]) < .4)
                           & (np.abs(out.vertices[:, 1]) < .24)]
        self.assertAlmostEqual(np.ptp(top[:, 1]), .2, delta=.02)
        self.assertEqual(info["curves"], 1)
        self.assertEqual(len(out.split()), 1)

    def test_closed_loop_and_intersecting_families_make_a_solid(self):
        P = [[-.5, -.5, 1], [.5, -.5, 1], [.5, .5, 1], [-.5, .5, 1]]
        loop = synthetic_curves(P, [[0, 1], [1, 2], [2, 3], [3, 0]])
        phase = self.body.vertices[self.body.faces, 0]*2*np.pi/.5
        loop["families"].append(dict(id=1, singular_faces=0, vertices=[[-.8, 0, 1], [.8, 0, 1]], edges=[[0, 1]],
                                     corner_phase=phase.tolist()))
        result, info = ms.raised_bands(self.body, loop, .12, .03, .5)
        self.assertEqual(str(result.status()), "Error.NoError")
        self.assertGreater(result.volume(), 8.)
        self.assertEqual(info["curves"], 2)
        self.assertGreater(result.num_tri(), 12)

    def test_zero_relief_keeps_body_and_budget_failure_is_explicit(self):
        data = synthetic_curves([[-.8, 0, 1], [.8, 0, 1]], [[0, 1]])
        result, info = ms.raised_bands(self.body, data, .2, 0., .5)
        self.assertAlmostEqual(result.volume(), 8.)
        self.assertEqual(info["band_faces"], 0)
        with self.assertRaisesRegex(ValueError, "budget"):
            ms.raised_bands(self.body, data, .2, .04, .5, max_faces=10)

    def test_invalid_network_does_not_reach_mesher(self):
        with self.assertRaisesRegex(ValueError, "coordinates"):
            backend.validate(synthetic_curves([[float("nan"), 0, 0], [1, 1, 1]], [[0, 1]]))
        with self.assertRaisesRegex(ValueError, "indices"):
            backend.validate(synthetic_curves([[0, 0, 0], [1, 1, 1]], [[0, 4]]))

    def test_stripes_only_are_closed_ribbons_with_open_gaps_and_no_body(self):
        data = synthetic_curves([[-.8, 0, 1], [.8, 0, 1]], [[0, 1]])
        solid, info = ms.raised_bands(self.body, data, .2, .04, .5, stripes_only=True)
        raw = solid.to_mesh64()
        out = trimesh.Trimesh(raw.vert_properties[:, :3], raw.tri_verts, process=False)
        self.assertTrue(out.is_watertight)
        self.assertTrue(out.is_winding_consistent)
        self.assertGreater(out.volume, 0)
        self.assertLess(out.volume, self.body.volume*.2)
        self.assertFalse(out.contains([[0., 0., 0.]])[0])
        hits, _, _ = out.ray.intersects_location([[0., .25, 3.]], [[0., 0., -1.]])
        self.assertEqual(len(hits), 0, "there must be no front or rear body skin in a stripe gap")
        self.assertAlmostEqual(out.bounds[1, 2], 1.04, places=5)
        self.assertGreater(info["stripe_area"], 0)
        self.assertLess(info["stripe_area"], info["surface_area"])
        self.assertFalse(info["is_void"])

    def test_stripes_only_zero_thickness_is_rejected(self):
        data = synthetic_curves([[-.8, 0, 1], [.8, 0, 1]], [[0, 1]])
        with self.assertRaisesRegex(ValueError, "positive Raised height"):
            ms.raised_bands(self.body, data, .2, 0., .5, stripes_only=True)

    def test_export_cleanup_does_not_join_close_separate_ribbons(self):
        a = trimesh.creation.box((1., 1., .01))
        b = a.copy()
        b.apply_translation([1.+1e-7, 0., 0.])
        source = trimesh.util.concatenate([a, b])
        out = ms.precision_mesh(source.vertices, source.faces, 1e-5, preserve_shells=True)
        self.assertTrue(out.is_watertight)
        self.assertEqual(len(out.split()), 2)


@_isolate.needs_helper(backend.find_backend(), "native stripe helper not built")
class NativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas stripes ")
        self.folder = Path(self.tmp.name)
        self.exe = backend.find_backend()

    def tearDown(self):
        self.tmp.cleanup()

    def solve(self, mesh, spacing=.2, rotation=0., families="single", field="smoothest", stem="surface ünicode"):
        src = self.folder/(stem+".obj")
        mesh.export(src)
        return backend.run_native(self.exe, src, self.folder/(stem+".json"), spacing=spacing, field=field,
                                  rotation=rotation, families=families, seed=0)

    @staticmethod
    def plane():
        xy = np.linspace(-1, 1, 21)
        X, Y = np.meshgrid(xy, xy)
        V = np.column_stack([X.ravel(), Y.ravel(), np.zeros(X.size)])
        a = (np.arange(20)[:, None]*21+np.arange(20)[None, :]).ravel()
        F = np.vstack([np.column_stack([a, a+1, a+22]), np.column_stack([a, a+22, a+21])])
        return trimesh.Trimesh(V, F, process=False)

    def test_spacing_in_model_units_and_visible_rotation(self):
        plane = self.plane()
        d0, d90 = self.solve(plane), self.solve(plane, rotation=90.)
        def direction(d):
            f = d["families"][0]
            V, E = np.asarray(f["vertices"]), np.asarray(f["edges"])
            seg = V[E[:, 1]]-V[E[:, 0]]
            _, _, axes = np.linalg.svd(seg[:, :2], full_matrices=False)
            return axes[0]
        tangent = direction(d0)
        self.assertLess(abs(tangent@direction(d90)), .03)
        lines = list(ms.paths_from_network(d0))
        normal = np.array([-tangent[1], tangent[0]])
        levels = sorted(np.mean(P[:, :2]@normal) for _, P in lines)
        gaps = np.diff(levels)
        self.assertGreater(len(gaps), 5)
        self.assertAlmostEqual(float(np.median(gaps)), .2, delta=.015)
        self.assertEqual(d0, self.solve(plane))

    def test_two_families_singular_sphere_and_curvature(self):
        ball = trimesh.creation.icosphere(subdivisions=2)
        data = self.solve(ball, spacing=.4, families="orthogonal", field="curvature")
        self.assertEqual(len(data["families"]), 2)
        self.assertGreater(sum(f["singular_faces"] for f in data["families"]), 0)
        for f in data["families"]:
            self.assertGreater(len(f["edges"]), 10)

    def test_curve_cache_reused_and_corruption_recomputed(self):
        mesh = trimesh.creation.icosphere(subdivisions=2)
        kw = dict(spacing=.4, budget=2500, field="smoothest", rotation=0., families="single", seed=0)
        first = ms.curve_network(mesh, self.folder, **kw)
        self.assertFalse(first["cache_hit"])
        with patch.object(backend, "run_native", side_effect=AssertionError("cache miss")):
            second = ms.curve_network(mesh, self.folder, **kw)
        self.assertTrue(second["cache_hit"])
        self.assertEqual(first["families"], second["families"])
        cache_file = next(self.folder.glob("*.json"))
        cache_file.write_text('{"schema":0}', encoding="utf-8")
        self.assertFalse(ms.curve_network(mesh, self.folder, **kw)["cache_hit"])
        rotated = ms.curve_network(mesh, self.folder, **dict(kw, rotation=20.))
        self.assertFalse(rotated["cache_hit"])

    def test_bad_input_and_missing_helper(self):
        src = self.folder/"invalid.obj"
        src.write_text("v 0 0 0\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "solver failed"):
            backend.run_native(self.exe, src, self.folder/"out.json", spacing=.2, field="smoothest",
                               rotation=0, families="single", seed=0)
        with patch.dict(os.environ, ATLAS_TOOLKIT_STRIPES=str(self.folder/"missing.exe")):
            self.assertIsNone(backend.find_backend())

    def test_cancellation_terminates_an_already_started_child(self):
        marker, helper = self.folder/"started", self.folder/"slow helper.py"
        helper.write_text("from pathlib import Path\nimport time\n"+f"Path({str(marker)!r}).write_text('ready')\n"
                          +"time.sleep(30)\n", encoding="utf-8")
        original = subprocess.Popen
        launched = []
        def launch(args, **kwargs):
            proc = original([sys.executable, str(helper)], **kwargs)
            launched.append(proc)
            return proc
        deadline = time.monotonic()+10
        def cancel():
            if marker.exists():
                raise Cancelled()
            if time.monotonic() > deadline:
                raise AssertionError("native child did not start")
        with patch.object(backend.subprocess, "Popen", side_effect=launch), self.assertRaises(Cancelled):
            backend.run_native(self.exe, self.folder/"unused.obj", self.folder/"unused.json", spacing=.2,
                               field="smoothest", rotation=0, families="single", seed=0, check=cancel)
        self.assertTrue(marker.exists())
        self.assertIsNotNone(launched[0].poll())


@_isolate.needs_helper(backend.find_backend(), "native stripe helper not built")
class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas stripes engine ")
        self.folder = Path(self.tmp.name)
        self.env = patch.dict(os.environ, ATLAS_STRIPES_CACHE=str(self.folder/"cache"))
        self.env.start()
        self.src = engine.make_sample(self.folder)
        self.values = dict(TOOL.test_values)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_engine(self, **kw):
        return engine.run(context(), self.src, None, TOOL.values(dict(self.values, **kw)))

    def test_registered_and_writes_signed_closed_formats_in_source_frame(self):
        self.assertIn(TOOL.id, registry().tools)
        original = trimesh.load(self.src)
        for suffix in (".stl", ".obj", ".ply"):
            dst = self.folder/("output"+suffix)
            result = engine.run(context("run"), self.src, dst, TOOL.values(self.values))
            self.assertEqual(result.outputs, [str(dst)])
            self.assertIn(b"atlas-toolkit:mesh-stripes", dst.read_bytes()[:8192])
            m = trimesh.load(dst)
            self.assertTrue(m.is_watertight)
            self.assertTrue(m.is_winding_consistent)
            self.assertGreater(m.volume, original.volume)
            self.assertLess(np.linalg.norm(m.bounds.mean(0)-original.bounds.mean(0)), 1.)
            self.assertLess(np.max(m.extents/original.extents), 1.05)

    def test_stripes_only_writes_one_closed_model_in_every_format(self):
        original = trimesh.load(self.src)
        for suffix in (".stl", ".obj", ".ply"):
            with self.subTest(format=suffix):
                dst = self.folder/("ribbons"+suffix)
                result = engine.run(context("run"), self.src, dst,
                                    TOOL.values(dict(self.values, stripes_only=True)))
                self.assertEqual(result.outputs, [str(dst)])
                self.assertIn(b"atlas-toolkit:mesh-stripes", dst.read_bytes()[:8192])
                out = trimesh.load(dst)
                self.assertTrue(out.is_watertight)
                self.assertTrue(out.is_winding_consistent)
                self.assertGreater(out.volume, 0)
                self.assertLess(out.volume, original.volume*.1)
                self.assertFalse(out.contains([original.centroid])[0])
                self.assertEqual(result.data["stripes_only"], True)
                self.assertGreater(result.data["coverage_pct"], 0)
                self.assertLess(result.data["coverage_pct"], 100)
                self.assertNotIn("added", [s.key for s in result.stats])
                self.assertIn("volume", [s.key for s in result.stats])

    def test_toggle_reuses_curve_cache_and_old_defaults_keep_body(self):
        self.assertFalse(TOOL.defaults().stripes_only)
        retained = self.run_engine()
        with patch.object(backend, "run_native", side_effect=AssertionError("mode toggle recomputed curves")):
            ribbons = self.run_engine(stripes_only=True)
        self.assertFalse(retained.data["stripes_only"])
        self.assertTrue(ribbons.data["stripes_only"])
        self.assertEqual(ribbons.data["cache_hits"], 1)

    def test_ribbon_preview_uses_export_field_and_reuses_its_cache(self):
        values = TOOL.values(dict(self.values, stripes_only=True, draft_faces=1000))
        with patch.object(ms, "curve_network", wraps=ms.curve_network) as network:
            preview = engine.run(context("draft"), self.src, None, values)
        self.assertEqual(network.call_args.kwargs["budget"], values.solve_faces)
        self.assertEqual(preview.outputs, [])
        with patch.object(backend, "run_native", side_effect=AssertionError("export recomputed preview field")):
            saved = engine.run(context("run"), self.src, self.folder/"preview ribbons.stl", values)
        self.assertEqual(saved.data["cache_hits"], 1)
        self.assertEqual(preview.data["coverage_pct"], saved.data["coverage_pct"])

    def test_ribbon_presets_cover_broad_and_dense_treatments(self):
        broad = _presets.values(TOOL, "Plotter broad")
        dense = _presets.values(TOOL, "Plotter dense")
        for values in (broad, dense):
            self.assertTrue(values.stripes_only)
            self.assertGreater(values.relief, 0)
            self.assertEqual(values.families, "single")
            self.assertGreater(values.solve_faces, TOOL.defaults().solve_faces)
        self.assertGreater(broad.band_width, dense.band_width)
        self.assertGreater(broad.spacing, dense.spacing)
        self.assertFalse(_presets.values(TOOL, "Flowing bands").stripes_only)

    def test_stripes_only_zero_height_writes_nothing(self):
        dst = self.folder/"empty.stl"
        with self.assertRaisesRegex(ValueError, "positive Raised height"):
            engine.run(context("run"), self.src, dst, TOOL.values(dict(self.values, stripes_only=True, relief=0.)))
        self.assertFalse(dst.exists())

    def test_preview_no_output_and_width_change_hits_curve_cache(self):
        before = set(self.folder.iterdir())
        first = self.run_engine()
        second = self.run_engine(band_width=45.)
        self.assertEqual(first.outputs, [])
        self.assertEqual(second.data["cache_hits"], 1)
        self.assertEqual(set(self.folder.iterdir())-before, {self.folder/"cache"})
        self.assertTrue(second.preview["views"])

    def test_components_and_open_input_repair(self):
        a = trimesh.creation.icosphere(subdivisions=1)
        b = a.copy()
        b.apply_translation([3., 0, 0])
        trimesh.util.concatenate([a, b]).export(self.src)
        res = self.run_engine(spacing=30., solve_faces=1500)
        self.assertEqual(res.data["components"], 2)
        a.update_faces(np.arange(len(a.faces)) != 0)
        a.export(self.src)
        with self.assertRaisesRegex(ValueError, "Repair open mesh"):
            self.run_engine()
        res = self.run_engine(repair=True, repair_resolution=48, spacing=30., solve_faces=1500)
        self.assertTrue(res.data["repaired"])

    def test_cancelled_job_writes_nothing(self):
        ctx = JobContext(lambda k, a: None, lambda: True, "run")
        dst = self.folder/"cancelled.stl"
        with self.assertRaises(Cancelled):
            engine.run(ctx, self.src, dst, TOOL.values(self.values))
        self.assertFalse(dst.exists())

    def test_enclosed_cavity_remains_empty_and_adds_bands_on_both_surfaces(self):
        outer = trimesh.creation.icosphere(subdivisions=1, radius=2.)
        inner = trimesh.creation.icosphere(subdivisions=1, radius=.8)
        inner.invert()
        source = trimesh.util.concatenate([outer, inner])
        source.export(self.src)
        result = self.run_engine(spacing=25., solve_faces=1800)
        self.assertEqual(result.data["components"], 2)
        data = result.preview["views"][0]["mesh"]
        out = trimesh.Trimesh(data.vertices, data.faces, process=False)
        self.assertTrue(out.is_watertight)
        self.assertTrue(out.is_winding_consistent)
        self.assertGreater(out.volume, source.volume)
        self.assertFalse(out.contains([[0., 0., 0.]])[0])
        ribbons = self.run_engine(spacing=25., solve_faces=1800, stripes_only=True)
        data = ribbons.preview["views"][0]["mesh"]
        only = trimesh.Trimesh(data.vertices, data.faces, process=False)
        self.assertTrue(only.is_watertight)
        self.assertTrue(only.is_winding_consistent)
        self.assertGreater(only.volume, 0)
        self.assertLess(only.volume, source.volume*.1)
        self.assertFalse(only.contains([[0., 0., 0.]])[0])

    def test_stripes_to_svg_workflow(self):
        for stripes_only in (False, True):
            with self.subTest(stripes_only=stripes_only):
                self._run_svg_workflow(stripes_only)

    def _run_svg_workflow(self, stripes_only):
        from atlas_toolkit.core import workflow as W
        import xml.etree.ElementTree as ET
        reg = registry()
        wf = W.Workflow(name="Surface bands to plotter")
        nodes = [wf.add(W.input_node("mesh")),
                 wf.add(W.tool_node(TOOL, values=dict(self.values, stripes_only=stripes_only))),
                 wf.add(W.tool_node(reg.get("plotter-vision"),
                                     values=_presets.values_or_defaults(reg.get("plotter-vision"), "Features",
                                                                        paper="A5"))),
                 wf.add(W.save_node(where="folder", folder=str(self.folder/f"drawings-{stripes_only}")))]
        for a, b in zip(nodes, nodes[1:]):
            wf.connect(a.id, b.id)
        self.assertFalse(W.errors(W.check(wf, reg)))
        res = W.run_workflow(wf, self.src, lambda k, p: None, lambda: False, reg=reg,
                             cache=W.WorkflowCache(self.folder/"workflow-cache"),
                             tool_cache=str(self.folder/"tool-cache"))
        outputs = [Path(p) for p in res.outputs if Path(p).suffix == ".svg"]
        self.assertTrue(outputs)
        root = ET.parse(outputs[0]).getroot()
        self.assertTrue(root.get("width").endswith("mm"))
        self.assertIn(b"plotter-vision", outputs[0].read_bytes())


if __name__ == "__main__":
    unittest.main()
