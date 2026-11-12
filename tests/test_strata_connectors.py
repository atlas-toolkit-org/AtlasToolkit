"""Real cap placement, embedded joints, organic trees and transactional stage output."""
import _isolate  # first: settings and caches stay in a temporary folder
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh
from shapely.geometry import Polygon

from atlas_toolkit.core.job import Cancelled, JobContext
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom import solid_connectors as co
from atlas_toolkit.tools.mesh_strata import TOOL, connectors, engine, mesh_strata as ms


def context(mode="run", cancelled=lambda: False):
    return JobContext(lambda *_: None, cancelled, mode)


def model():
    return trimesh.creation.box(extents=(1., 1., 2.))


def sculpt(mesh=None, amount=1., **values):
    mesh = model() if mesh is None else mesh
    p = TOOL.values(dict(dict(connectors=True, layers=3, separation=140.), **values))
    st = ms.stack(mesh.vertices, p.layers, p.direction, p.tilt, p.tilt_toward)
    u = ms.layer_amounts(st, amount, p, *mesh.bounds)
    return ms.sculpt(mesh, st, u, p, ms.draws(st.count, p.seed))


class StrataConnectorGeometryTests(unittest.TestCase):
    def test_all_styles_have_spaced_supports_with_real_embedded_fused_contacts(self):
        for style in ("cylinder", "bar", "organic"):
            with self.subTest(style=style):
                out, info = sculpt(connector_style=style, connector_branches=2)
                self.assertTrue(out.is_volume)
                self.assertEqual(len(out.split()), 1)
                self.assertEqual(len(info["connectors"]), 6)
                for pair in info["pairs"]:
                    self.assertEqual(pair["supports"], 3)
                json.dumps(info)  # connector records travel in run/Workflow metadata
                for row in info["connectors"]:
                    self.assertEqual(row["branches"], 2 if style == "organic" else 0)
                    self.assertLessEqual(row["lateral_max"], row["extent"] + 1e-10)
                # Establish contact independently from the final union's topology.
                p = TOOL.values(dict(connectors=True, layers=3, separation=140., connector_style=style,
                                     connector_branches=2))
                st = ms.stack(model().vertices, 3)
                plan = connectors.prepare(model(), st, p)
                slabs, _, _ = connectors.make_slabs(st, np.ones(3), p, ms.draws(3, 0), plan)
                for row in info["connectors"]:
                    a, b = slabs[row["lower"]], slabs[row["upper"]]
                    self.assertTrue(a.mesh.contains([row["root_inside"]])[0])
                    self.assertTrue(b.mesh.contains([row["tip_inside"]])[0])
                    path = np.asarray(row["paths"][0])
                    support = (co.tube(path, row["diameter"]/2, p.connector_taper, 0., 0., 24)
                               if style == "organic" else co.beam(path[0], path[-1], row["diameter"]/2, style, 24))
                    self.assertGreater((support ^ a.solid).volume(), 1e-12)
                    self.assertGreater((support ^ b.solid).volume(), 1e-12)

    def test_ring_and_concave_caps_preserve_voids_and_find_safe_interiors(self):
        ring = trimesh.creation.torus(1., .35, major_sections=32, minor_sections=16)
        concave = trimesh.creation.extrude_polygon(Polygon([(0,0), (1,0), (1,.3), (.3,.3),
                                                          (.3,.7), (1,.7), (1,1), (0,1)]), 2.)
        for mesh in (ring, concave):
            with self.subTest(mesh=mesh.euler_number):
                out, info = sculpt(mesh, connector_style="organic", connector_branches=2)
                self.assertTrue(out.is_volume)
                self.assertEqual(len(out.split()), 1)
                self.assertTrue(info["connectors"])
        out, _ = sculpt(ring)
        self.assertFalse(out.contains([[0, 0, 0], [0, 0, 1.], [0, 0, 2.]]).any())

    def test_seed_streams_are_repeatable_and_do_not_change_slab_motion(self):
        a, first = sculpt(connector_seed=3, connector_style="organic", connector_branches=2)
        b, second = sculpt(connector_seed=3, connector_style="organic", connector_branches=2)
        np.testing.assert_array_equal(a.vertices, b.vertices)
        self.assertEqual(first["connectors"], second["connectors"])
        _, different = sculpt(connector_seed=4, connector_style="organic", connector_branches=2)
        self.assertNotEqual(first["connectors"][0]["root_inside"], different["connectors"][0]["root_inside"])
        _, branch_seed = sculpt(connector_seed=3, connector_style="organic", connector_branches=2,
                                connector_branch_seed=8, connector_branch_placement="mixed")
        _, initial = sculpt(connector_seed=3, connector_style="organic", connector_branches=2,
                            connector_branch_placement="mixed")
        self.assertEqual(initial["connectors"][0]["paths"][0], branch_seed["connectors"][0]["paths"][0])
        # Without dislocation, even changing the dislocation seed leaves supports unchanged.
        _, unrelated = sculpt(seed=33, connector_seed=3, connector_style="organic", connector_branches=2)
        self.assertEqual(first["connectors"], unrelated["connectors"])

    def test_all_slice_directions_oblique_cuts_motions_and_influence(self):
        for direction in ("up", "front", "side"):
            with self.subTest(direction=direction):
                out, info = sculpt(trimesh.creation.icosphere(2), direction=direction, tilt=20., turn=25.,
                                   wave=2., wave_shape="orbit", jitter_shift=1., jitter_spin=3., jitter_tilt=3.,
                                   connector_style="organic", connector_branches=1)
                self.assertTrue(out.is_volume)
                self.assertEqual(len(out.split()), 1)
                self.assertTrue(info["connectors"])
        out, info = sculpt(influence="band", band_from=50., band_to=100., feather=0.)
        self.assertTrue(out.is_volume)
        self.assertEqual(len(out.split()), 1)

    def test_separate_sources_and_empty_layers_are_not_bridged(self):
        a, b = model(), model()
        a.apply_translation((-2., 0, 0))
        b.apply_translation((2., 0, 0))
        out, info = sculpt(trimesh.util.concatenate([a, b]))
        self.assertEqual(len(out.split()), 2)
        self.assertEqual({row["source"] for row in info["connectors"]}, {0, 1})
        a, b = model(), model()
        a.apply_translation((0, 0, -4.))
        b.apply_translation((0, 0, 4.))
        out, info = sculpt(trimesh.util.concatenate([a, b]), layers=10)
        self.assertEqual(len(out.split()), 2)
        self.assertTrue(all(row["upper"] == row["lower"] + 1 for row in info["connectors"]))
        self.assertLess(len({row["lower"] for row in info["connectors"]}), 9)

    def test_thin_caps_fit_thickness_and_requested_support_counts(self):
        out, info = sculpt(layers=4, retained=8., connector_diameter_min=8., connector_diameter_max=8.,
                           connector_count=20)
        self.assertTrue(out.is_volume)
        self.assertTrue(all(row["clamped"] for row in info["connectors"]))
        self.assertTrue(all(1 <= row["supports"] <= 20 for row in info["pairs"]))
        self.assertTrue(any(row["supports"] < 20 for row in info["pairs"]))

    def test_roomy_slabs_honor_a_fixed_requested_thickness(self):
        _, info = sculpt(connector_diameter_min=.6, connector_diameter_max=.6)
        for row in info["connectors"]:
            self.assertAlmostEqual(row["diameter"], .012)
            self.assertAlmostEqual(row["diameter"], row["requested_diameter"])
            self.assertFalse(row["clamped"])

    def test_split_slabs_within_one_source_are_all_connected(self):
        # A continuous arch has two separate legs in the lower strata, and one
        # connected crossbar above. Both legs must reach the same upper component.
        left = co.mf.Manifold.cube([.3, .5, 2.]).translate([-.8, -.25, -1.])
        right = co.mf.Manifold.cube([.3, .5, 2.]).translate([.5, -.25, -1.])
        bridge = co.mf.Manifold.cube([1.6, .5, .4]).translate([-.8, -.25, .6])
        arch = co.to_mesh(co.union([left, right, bridge]))
        out, info = sculpt(arch, layers=4, connector_count=2)
        self.assertTrue(out.is_volume)
        self.assertEqual(len(out.split()), 1)
        self.assertEqual({row["source"] for row in info["connectors"]}, {0})
        self.assertTrue(any(row["lower_component"] == 1 for row in info["connectors"]))

    def test_enclosed_cavity_shell_is_retained_with_its_source(self):
        hollow = co.mf.Manifold.cube([2., 2., 2.], True) - co.mf.Manifold.cube([.2, .2, .2], True)
        out, info = sculpt(co.to_mesh(hollow))
        self.assertTrue(out.is_volume)
        self.assertEqual(info["components"], 1)
        self.assertEqual({r["source"] for r in info["connectors"]}, {0})
        self.assertFalse(out.contains([[0., 0., (2./3.)*1.4]])[0])

    def test_hundred_branch_request_is_bounded_and_reports_fitted_counts(self):
        out, info = sculpt(layers=2, connector_count=1, connector_style="organic", connector_branches=100,
                           connector_branch_levels=4, connector_branch_placement="mixed", connector_segments=12,
                           connector_spread=40., connector_extent=45.)
        self.assertTrue(out.is_volume)
        row = info["connectors"][0]
        self.assertEqual(row["requested_branches"], 100)
        self.assertGreater(row["branches"], 4)
        self.assertLessEqual(row["branches"], 100)
        self.assertLessEqual(row["branch_levels"], 4)
        self.assertLessEqual(row["lateral_max"], row["extent"]+1e-10)

    def test_zero_amount_disabled_and_already_joined_keep_original_behavior(self):
        inp = model()
        out, info = sculpt(inp, amount=0.)
        np.testing.assert_array_equal(inp.vertices, out.vertices)
        np.testing.assert_array_equal(inp.faces, out.faces)
        self.assertNotIn("connectors", info)
        out, info = sculpt(retained=100., separation=0.)
        self.assertTrue(out.is_volume)
        self.assertEqual(len(out.split()), 1)
        self.assertFalse(info["connectors"])
        self.assertTrue(all(row["already_joined"] for row in info["pairs"]))
        p = TOOL.values(dict(connectors=False, layers=3))
        st = ms.stack(inp.vertices, 3)
        a, _ = ms.sculpt(inp, st, np.ones(3), p, ms.draws(3, 0))
        p.update(connector_count=20, connector_seed=43, connector_style="organic", connector_branches=100)
        b, _ = ms.sculpt(inp, st, np.ones(3), p, ms.draws(3, 0))
        np.testing.assert_array_equal(a.vertices, b.vertices)
        np.testing.assert_array_equal(a.faces, b.faces)

    def test_random_branch_ranges_zones_generations_and_zero_controls(self):
        for zone in ("split", "beginning", "middle", "end", "random", "mixed"):
            with self.subTest(zone=zone):
                out, info = sculpt(connector_style="organic", connector_count=1, connector_branch_count="random",
                                   connector_branches_min=4, connector_branches_max=5,
                                   connector_branch_placement=zone, connector_branch_levels=3, connector_branch_seed=7)
                self.assertTrue(out.is_volume)
                self.assertTrue(all(4 <= row["requested_branches"] <= 5 for row in info["connectors"]))
                self.assertTrue(any(row["branch_levels"] > 1 for row in info["connectors"]))
                for row in info["connectors"]:
                    self.assertLessEqual(row["lateral_max"], row["extent"]+1e-10)
                    for origin in row["branch_origins"]:
                        if origin["zone"] != "split":
                            lo, hi = connectors.branches.ZONES[origin["zone"]]
                            self.assertTrue(lo <= origin["fraction"] <= hi)
        _, zero = sculpt(connector_style="organic", connector_extent=0., connector_branches=10)
        self.assertTrue(all(row["branches"] == 0 and row["lateral_max"] < 1e-10 for row in zero["connectors"]))
        _, straight = sculpt(connector_style="organic", connector_curve=0., connector_irregularity=0.)
        self.assertTrue(all(row["lateral_max"] < 1e-10 for row in straight["connectors"]))

    def test_invalid_ranges_open_models_face_limits_and_cancellation(self):
        for values in (dict(connector_diameter_min=2., connector_diameter_max=1.),
                       dict(connector_style="organic", connector_branch_count="random",
                            connector_branches_min=5, connector_branches_max=2)):
            with self.assertRaises(ValueError):
                sculpt(**values)
        mesh = model()
        mesh.update_faces(np.arange(len(mesh.faces)-1))
        with self.assertRaisesRegex(ValueError, "closed solid"):
            sculpt(mesh)
        with self.assertRaisesRegex(ValueError, "Maximum faces"):
            sculpt(connector_style="organic", connector_count=10, connector_branches=8, max_faces=10_000)
        p = TOOL.values(dict(connectors=True, layers=3, connector_style="organic", connector_branches=30))
        st = ms.stack(model().vertices, 3)
        # Cancel inside tree generation, after placement and cuts have succeeded.
        calls = []
        original = connectors.branches.origin_plan
        def start_branches(*args):
            calls.append(True)
            return original(*args)
        with patch.object(connectors.branches, "origin_plan", side_effect=start_branches), self.assertRaises(Cancelled):
            ms.sculpt(model(), st, np.ones(3), p, ms.draws(3, 0), ctx=context(cancelled=lambda: bool(calls)))


class StrataConnectorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="strata_connectors_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / "pillar.stl"
        inp = model()
        inp.apply_scale(40.)
        inp.export(self.src)
        self.p = TOOL.values(dict(connectors=True, layers=3, separation=140., connector_style="organic",
                                  connector_branches=2))

    def test_all_formats_signed_closed_and_stages_registered(self):
        for ext in (".stl", ".obj", ".ply"):
            with self.subTest(ext=ext):
                dst = self.root / ("joined"+ext)
                result = engine.run(context(), self.src, dst, self.p)
                saved = gm.load_mesh(dst)
                self.assertTrue(saved.is_volume)
                self.assertTrue(saved.nondegenerate_faces().all())
                self.assertEqual(len(saved.split()), 1)
                self.assertIn(b"atlas-toolkit:mesh-strata", dst.read_bytes())
                self.assertEqual(result.stat("supports").value, 6)
                self.assertEqual(result.stat("branches").value, 12)
                points = [r[key] for r in result.data["connectors"] for key in ("root_inside", "tip_inside")]
                self.assertTrue(saved.contains(points).all())  # records use source model units
        p = TOOL.values(dict(self.p, stages=3))
        result = engine.run(context(), self.src, self.root / "staged.stl", p)
        self.assertEqual(len(result.outputs), 4)
        first = gm.load_mesh(result.outputs[1])
        original = gm.load_mesh(self.src)
        np.testing.assert_array_equal(first.vertices, original.vertices)
        self.assertEqual([row["amount"] for row in result.data["connector_stages"]], [0., .5, 1.])

    def test_same_stage_is_independent_of_stage_count_and_preview_is_full_geometry(self):
        a = engine.run(context("analyse"), self.src, None, TOOL.values(dict(self.p, stages=3)))
        b = engine.run(context("analyse"), self.src, None, TOOL.values(dict(self.p, stages=5)))
        self.assertEqual(a.data["connector_stages"][1], b.data["connector_stages"][2])
        draft = engine.run(context("draft"), self.src, None, self.p)
        run = engine.run(context(), self.src, None, self.p)
        self.assertEqual(draft.data, run.data)
        np.testing.assert_array_equal(draft.preview["views"][-1]["mesh"].vertices,
                                      run.preview["views"][-1]["mesh"].vertices)

    def test_failure_and_cancellation_leave_existing_primary_and_stage_folder_unchanged(self):
        dst = self.root / "existing.stl"
        dst.write_bytes(b"existing artwork")
        folder = self.root / "existing_stages"
        folder.mkdir()
        (folder / "keep.txt").write_text("keep", encoding="utf-8")
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        original = connectors.sculpt
        calls = []
        def fail_later(*args, **kwargs):
            calls.append(True)
            if len(calls) == 2:
                raise ValueError("required joint cannot be built")
            return original(*args, **kwargs)
        with patch.object(connectors, "sculpt", side_effect=fail_later), self.assertRaisesRegex(ValueError, "required joint"):
            engine.run(context(), self.src, dst, TOOL.values(dict(self.p, stages=3)))
        with self.assertRaises(Cancelled):
            engine.run(context(cancelled=lambda: True), self.src, dst, self.p)
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_reload_validation_is_transactional_and_dry_runs_write_nothing(self):
        dst = self.root / "out.stl"
        before = set(self.root.iterdir())
        for mode in ("analyse", "draft"):
            engine.run(context(mode), self.src, dst, self.p)
        self.assertEqual(set(self.root.iterdir()), before)
        original = gm.save_mesh
        def invalid(item, path, tool):
            broken = item.copy()
            broken.update_faces(np.arange(len(broken.faces)-1))
            original(broken, path, tool)
        with patch.object(gm, "save_mesh", side_effect=invalid), self.assertRaisesRegex(ValueError, "Saved stratum joints"):
            engine.run(context(), self.src, dst, self.p)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_schema_cli_workflow_cache_and_connected_drawing(self):
        from atlas_toolkit.core import workflow as W
        from atlas_toolkit.core.cli import build_parser
        from atlas_toolkit.core.registry import registry, validate
        self.assertFalse(validate(TOOL))
        self.assertFalse(TOOL.values().connectors)
        args = build_parser().parse_args(["run", TOOL.id, str(self.src), "--connectors", "--connector-count", "3",
                                         "--connector-style", "organic", "--connector-branches", "2", "--connector-seed", "7"])
        self.assertEqual(int(args.connector_count), 3)
        self.assertEqual(int(args.connector_seed), 7)
        values = TOOL.values(dict(self.p, connector_branch_count="random", connector_branch_placement="mixed"))
        self.assertTrue(TOOL.param("connector_branches_max").visible(values))
        self.assertFalse(TOOL.param("connector_branches").visible(values))
        self.assertFalse(TOOL.param("connector_fork").visible(values))
        self.assertTrue(TOOL.param("connector_branch_seed").visible(values))
        self.assertFalse(TOOL.param("connector_count").visible(TOOL.values()))
        reg = registry()
        wf = W.Workflow(name="Rooted strata drawing")
        src = wf.add(W.input_node("mesh"))
        node = wf.add(W.tool_node(reg.get(TOOL.id), values=dict(self.p)))
        draw = wf.add(W.tool_node(reg.get("plotter-vision"), values=dict(reg.get("plotter-vision").test_values or {})))
        save = wf.add(W.save_node(where="folder", folder=str(self.root / "drawings")))
        wf.connect(src.id, node.id)
        wf.connect(node.id, draw.id)
        wf.connect(draw.id, save.id)
        self.assertFalse(W.errors(W.check(wf, reg)))
        cache = W.WorkflowCache(self.root / "cache")
        result = W.run_workflow(wf, self.src, lambda *_: None, lambda: False, reg=reg, cache=cache)
        self.assertEqual(len(result.outputs), 1)
        self.assertIn(b"<desc>plotter-vision</desc>", Path(result.outputs[0]).read_bytes())
        events = []
        W.run_workflow(wf, self.src, lambda k, p: events.append((k,p)), lambda: False, reg=reg, cache=cache)
        self.assertTrue(any(k == "node" and p[1] == "cached" for k,p in events))
        node.values["connector_seed"] = 7
        events = []
        W.run_workflow(wf, self.src, lambda k, p: events.append((k,p)), lambda: False, reg=reg, cache=cache)
        self.assertFalse(any(k == "node" and p[1] == "cached" for k,p in events))


if __name__ == "__main__":
    unittest.main()
