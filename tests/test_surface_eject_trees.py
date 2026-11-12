"""100-branch support, seeded count ranges, origin zones, generations and export conformity."""
import _isolate  # first: settings and caches stay in a temporary folder
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from atlas_toolkit.core.job import Cancelled, JobContext
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_surface_eject import TOOL, engine, branching as br, connectors as co, mesh_surface_eject as se


def context(emit=lambda *_: None, cancelled=lambda: False):
    return JobContext(emit, cancelled)


def model():
    mesh = trimesh.creation.icosphere(subdivisions=2)
    mesh.vertices *= [.7, .55, 1.]
    return mesh


class TreePlanTests(unittest.TestCase):
    def test_fixed_and_random_counts_are_inclusive_seeded_per_fragment(self):
        p = TOOL.values(dict(connectors=True, connector_style="organic", connector_branches=100))
        self.assertEqual(br.requested_count(p, 4), 100)
        p.update(connector_branch_count="random", connector_branches_min=0, connector_branches_max=100)
        counts = [br.requested_count(p, i) for i in range(1024)]
        self.assertEqual(min(counts), 0)
        self.assertEqual(max(counts), 100)
        self.assertEqual(counts, [br.requested_count(p, i) for i in range(1024)])
        p["connector_branch_seed"] = 7
        self.assertNotEqual(counts[:10], [br.requested_count(p, i) for i in range(10)])
        p.update(connector_branches_min=17, connector_branches_max=17)
        self.assertTrue(all(br.requested_count(p, i) == 17 for i in range(10)))

    def test_origin_modes_stay_inside_requested_zones_and_mixed_covers_all(self):
        p = TOOL.values(dict(connectors=True, connector_style="organic"))
        for mode in ("beginning", "middle", "end", "random", "mixed", "split"):
            p["connector_branch_placement"] = mode
            plan = br.origin_plan(p, 3, 100)
            self.assertEqual(plan, br.origin_plan(p, 3, 100))
            for item in plan:
                if mode == "split":
                    self.assertEqual(item["fraction"], p["connector_fork"] / 100)
                else:
                    lo, hi = br.ZONES[item["zone"]]
                    self.assertGreaterEqual(item["fraction"], lo)
                    self.assertLessEqual(item["fraction"], hi)
            if mode == "mixed":
                self.assertEqual({item["zone"] for item in plan[:3]}, {"beginning", "middle", "end"})

    def test_main_stem_and_selection_do_not_change_when_branch_controls_change(self):
        mesh = model()
        p = TOOL.values(dict(connectors=True, connector_style="organic", count=3, segments=12))
        extraction = se.extract(mesh, p)
        base = co.build(extraction, p, 2.)
        p.update(connector_branch_count="random", connector_branches_min=4, connector_branches_max=8,
                 connector_branch_placement="mixed", connector_branch_levels=3)
        trees = co.build(extraction, p, 2.)
        self.assertEqual([r["fragment_index"] for r in base.records], [r["fragment_index"] for r in trees.records])
        for a, b in zip(base.records, trees.records):
            for key in ("cavity", "root_inside", "fragment_inside", "paths"):
                if key == "paths":
                    np.testing.assert_array_equal(a[key][0], b[key][0])
                else:
                    self.assertEqual(a[key], b[key])
            self.assertGreaterEqual(b["requested_branches"], 4)
            self.assertLessEqual(b["requested_branches"], 8)

    def test_geometry_modes_create_connected_bounded_recursive_branches(self):
        mesh = model()
        for mode in ("beginning", "middle", "end", "random", "mixed"):
            with self.subTest(mode=mode):
                p = TOOL.values(dict(connectors=True, connector_style="organic", count=1, segments=12,
                                     connector_branches=12, connector_branch_placement=mode, connector_branch_levels=3))
                extraction = se.extract(mesh, p)
                result = co.build(extraction, p, 2.)
                row = result.records[0]
                self.assertEqual(row["branches"], 12)
                self.assertGreater(row["branch_levels"], 1)
                self.assertLessEqual(row["branch_levels"], 3)
                self.assertLessEqual(row["lateral_max"], row["extent"] + 1e-10)
                self.assertEqual(len(result.combined.decompose()), 1)
                self.assertTrue(se.to_mesh(result.combined).is_volume)
                for path, origin in zip(row["paths"][1:], row["branch_origins"]):
                    parent = np.asarray(row["paths"][origin["parent"]])
                    self.assertLess(np.linalg.norm(np.asarray(path[0]) - br.path_point(parent, origin["path_fraction"])[0]), 1e-10)
                    self.assertTrue(se.to_mesh(extraction.parts[0]).contains([path[-1]])[0])

    def test_cancellation_is_checked_inside_dense_branch_generation(self):
        p = TOOL.values(dict(connectors=True, connector_style="organic", count=1, connector_branches=100, connector_branch_placement="mixed"))
        extraction = se.extract(model(), p)
        stop = [False]
        def emit(kind, payload):
            if kind == "progress" and "branch 5 /" in payload[1]:
                stop[0] = True
        with self.assertRaises(Cancelled):
            co.build(extraction, p, 2., context(emit, lambda: stop[0]))


class TreeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="eject_tree_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = engine.make_sample(self.root)

    def test_one_hundred_recursive_branches_export_closed_in_all_formats(self):
        p = TOOL.values(dict(connectors=True, connector_style="organic", count=1, connector_branches=100,
                             connector_branch_placement="mixed", connector_branch_levels=3, segments=12))
        for ext in (".obj", ".ply", ".stl"):
            with self.subTest(ext=ext):
                dst = self.root / ("hundred" + ext)
                result = engine.run(context(), self.src, dst, p)
                row = result.data["connectors"][0]
                self.assertEqual(row["branches"], 100)
                self.assertEqual(result.stat("branches").value, 100)
                saved = gm.load_mesh(dst)
                self.assertTrue(saved.is_volume)
                self.assertTrue(saved.nondegenerate_faces().all())
                self.assertEqual(len(saved.split()), 1)

    def test_invalid_count_range_and_branch_face_budget_publish_nothing(self):
        dst = self.root / "keep.stl"
        dst.write_bytes(b"old output")
        p = TOOL.values(dict(connectors=True, connector_style="organic", connector_branch_count="random",
                             connector_branches_min=90, connector_branches_max=10))
        with self.assertRaisesRegex(ValueError, "Branches minimum"):
            engine.run(context(), self.src, dst, p)
        p = TOOL.values(dict(connectors=True, connector_style="organic", count=1, connector_branches=100,
                             connector_branch_placement="mixed", connector_branch_levels=3, segments=12, max_faces=10000))
        with self.assertRaisesRegex(ValueError, "Maximum faces"):
            engine.run(context(), self.src, dst, p)
        self.assertEqual(dst.read_bytes(), b"old output")

    def test_cli_schema_visibility_and_workflow_cache_cover_new_controls(self):
        from atlas_toolkit.core.cli import build_parser
        from atlas_toolkit.core.registry import registry
        from atlas_toolkit.core import workflow as W
        args = build_parser().parse_args(["run", TOOL.id, str(self.src), "--connectors", "--connector-style", "organic",
                                         "--connector-branch-count", "random", "--connector-branches-min", "3",
                                         "--connector-branches-max", "100", "--connector-branch-placement", "mixed"])
        self.assertEqual(TOOL.values(vars(args))["connector_branches_max"], 100)
        p = TOOL.values(dict(connectors=True, connector_style="organic", connector_branch_count="random",
                             connector_branch_placement="mixed", connector_branch_levels=3))
        self.assertTrue(TOOL.param("connector_branches_max").visible(p))
        self.assertFalse(TOOL.param("connector_branches").visible(p))
        self.assertFalse(TOOL.param("connector_fork").visible(p))
        self.assertTrue(TOOL.param("connector_branch_seed").visible(p))
        self.assertEqual(TOOL.param("connector_branches").max, 100)
        wf = W.Workflow(name="Organic trees")
        reg = registry()
        a = wf.add(W.input_node("mesh"))
        b = wf.add(W.tool_node(reg.get(TOOL.id), values=dict(p, count=2, connector_branches_min=3, connector_branches_max=6)))
        c = wf.add(W.save_node(where="folder", folder=str(self.root / "saved")))
        wf.connect(a.id, b.id)
        wf.connect(b.id, c.id)
        cache = W.WorkflowCache(self.root / "cache")
        self.assertFalse(W.errors(W.check(wf, reg)))
        W.run_workflow(wf, self.src, lambda *_: None, lambda: False, reg=reg, cache=cache)
        for changes, hit in (({}, True), ({"connector_branch_seed": 7}, False), ({"connector_branch_placement": "end"}, False)):
            b.values.update(changes)
            events = []
            W.run_workflow(wf, self.src, lambda k, p: events.append((k, p)), lambda: False, reg=reg, cache=cache)
            self.assertEqual(any(k == "node" and p[1] == "cached" for k, p in events), hit)

    def test_connector_previews_retain_complete_dense_geometry(self):
        p = TOOL.values(dict(connectors=True, connector_style="organic", count=1, connector_branches=20,
                             connector_branch_placement="mixed", segments=12))
        with patch.object(gm, "PREVIEW_FACES", 100):
            result = engine.run(context(), self.src, None, p)
        views = {view["title"]: view for view in result.preview["views"]}
        connectors = views["Connectors"]["mesh"]
        self.assertEqual(len(connectors.faces), connectors.faces_total)
        self.assertIn("full fragments and connectors", views["Combined"]["status"])


if __name__ == "__main__":
    unittest.main()
