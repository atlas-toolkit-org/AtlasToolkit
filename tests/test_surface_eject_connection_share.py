"""Exact connection shares, nested seeded masks, size selection and mixed topology/exports."""
import _isolate  # first: settings and caches stay in a temporary folder
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.job import JobContext
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_surface_eject import TOOL, engine, connectors as co, mesh_surface_eject as se


def context():
    return JobContext(lambda *_: None, lambda: False)


class ShareTests(unittest.TestCase):
    def extraction(self, count=10):
        model = trimesh.creation.icosphere(subdivisions=2)
        model.vertices *= [.7, .55, 1.]
        p = TOOL.values(dict(count=count, connectors=True, connector_style="organic", connector_branches=1))
        return se.extract(model, p), p

    def test_exact_count_rounding_and_zero_hundred_endpoints(self):
        extraction, p = self.extraction()
        for percent, expected in ((0, 0), (.1, 0), (4.9, 0), (5, 1), (25, 3), (50, 5), (95, 10), (100, 10)):
            p["connector_percent"] = percent
            self.assertEqual(len(co.selected_indices(extraction, p)), expected)
        p["connectors"] = False
        self.assertEqual(co.selected_indices(extraction, p), [])

    def test_random_selection_is_nested_repeatable_and_uses_its_own_seed(self):
        extraction, p = self.extraction()
        p["connector_percent"] = 30.
        small = co.selected_indices(extraction, p)
        self.assertEqual(small, co.selected_indices(extraction, p))
        p["connector_percent"] = 60.
        large = co.selected_indices(extraction, p)
        self.assertTrue(set(small) <= set(large))
        p["connector_seed"] = 42
        self.assertNotEqual(large, co.selected_indices(extraction, p))
        p["seed"] += 100
        # Selection depends only on its independent seed and extracted count.
        selected = co.selected_indices(extraction, p)
        p["seed"] += 100
        self.assertEqual(selected, co.selected_indices(extraction, p))

    def test_largest_and_smallest_use_actual_extracted_volume_with_stable_ties(self):
        extraction, p = self.extraction()
        p["connector_percent"] = 30.
        volumes = np.array([row["volume"] for row in extraction.records])
        for mode, ordered in (("largest", np.argsort(-volumes, kind="stable")),
                              ("smallest", np.argsort(volumes, kind="stable"))):
            p["connector_selection"] = mode
            self.assertEqual(co.selected_indices(extraction, p), sorted(ordered[:3].tolist()))
        for row in extraction.records:
            row["volume"] = 1.
        self.assertEqual(co.selected_indices(extraction, p), [0, 1, 2])

    def test_mixed_union_joins_only_selected_fragments_and_keeps_connector_shapes(self):
        extraction, p = self.extraction(count=6)
        full = co.build(extraction, p, 2.)
        p["connector_percent"] = 50.
        mixed = co.build(extraction, p, 2.)
        selected = co.selected_indices(extraction, p)
        self.assertEqual(len(mixed.connectors), 3)
        self.assertEqual(len(mixed.combined.decompose()), 4)  # body + three intentionally floating pieces
        self.assertTrue(se.to_mesh(mixed.combined).is_volume)
        for connector, row in zip(mixed.connectors, mixed.records):
            index = row["fragment_index"]
            self.assertIn(index, selected)
            self.assertEqual(row, full.records[index])
            self.assertGreater((connector ^ extraction.body).volume(), 1e-12)
            self.assertGreater((connector ^ extraction.parts[index]).volume(), 1e-12)
            for other, part in enumerate(extraction.parts):
                if other != index:
                    self.assertLess((connector ^ part).volume(), 1e-12)

    def test_zero_percent_needs_no_interfaces_or_detachment_and_preserves_floating_geometry(self):
        extraction, p = self.extraction(count=3)
        p.update(connector_percent=0., fully_detach=False)
        se.validate(p)
        extraction.interfaces = []
        result = co.build(extraction, p, 2.)
        self.assertFalse(result.records)
        self.assertFalse(result.connectors)
        self.assertIsNone(result.combined)


class ShareIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="eject_share_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = engine.make_sample(self.root)

    def test_partial_and_zero_export_all_formats_with_correct_files_and_stats(self):
        for ext in (".obj", ".ply", ".stl"):
            for percent, expected in ((0., 0), (1., 0), (50., 3), (100., 6)):
                with self.subTest(ext=ext, percent=percent):
                    p = TOOL.values(dict(count=6, connectors=True, connector_percent=percent, export_mode="separate"))
                    dst = self.root / (f"share_{percent:g}" + ext)
                    result = engine.run(context(), self.src, dst, p)
                    self.assertEqual(len(result.outputs), 3 if expected else 2)
                    self.assertEqual(result.stat("connected").value, expected)
                    self.assertEqual(len(result.data["connected_fragments"]), expected)
                    self.assertEqual(result.stat("connected_pct").value, expected / 6 * 100)
                    self.assertIn(f"connected  {expected} / 6", result.report)
                    saved = [gm.load_mesh(Path(path)) for path in result.outputs]
                    self.assertTrue(all(mesh.is_volume for mesh in saved))
                    if not expected:
                        self.assertFalse(any("_connectors" in Path(path).stem for path in result.outputs))
        p = TOOL.values(dict(count=6, connectors=True, connector_percent=50.))
        result = engine.run(context(), self.src, self.root / "mixed.obj", p)
        self.assertEqual(len(gm.load_mesh(self.root / "mixed.obj").split()), 4)
        off = engine.run(context(), self.src, None, TOOL.values(dict(count=6)))
        zero = engine.run(context(), self.src, None, TOOL.values(dict(count=6, connectors=True, connector_percent=0.)))
        self.assertEqual(off.data["pieces"], zero.data["pieces"])

    def test_percentage_uses_achieved_extraction_count(self):
        p = TOOL.values(dict(count=18, region="band", band_from=45., band_to=55., connectors=True, connector_percent=50.))
        result = engine.run(context(), self.src, None, p)
        count = len(result.data["pieces"])
        self.assertLess(count, 18)
        self.assertEqual(result.stat("connected").value, int(np.floor(count / 2 + .5)))

    def test_cli_schema_visibility_and_workflow_cache_include_share_and_connection_seed(self):
        from atlas_toolkit.core.cli import build_parser
        from atlas_toolkit.core import workflow as W
        from atlas_toolkit.core.registry import registry
        reg = registry()
        args = build_parser().parse_args(["run", TOOL.id, str(self.src), "--connectors", "--connector-percent", "50",
                                         "--connector-selection", "random", "--connector-seed", "7"])
        self.assertEqual(TOOL.values(vars(args))["connector_percent"], 50.)
        values = dict(count=4, connectors=True, connector_percent=50., export_mode="separate")
        p = TOOL.values(values)
        self.assertTrue(TOOL.param("connector_seed").visible(p))
        self.assertFalse(TOOL.param("connector_selection").visible(dict(p, connector_percent=100.)))
        self.assertFalse(TOOL.param("connector_style").visible(dict(p, connector_percent=0.)))
        self.assertEqual(TOOL.values(json.loads(json.dumps(p))), p)
        wf = W.Workflow(name="Mixed connected fragments")
        a = wf.add(W.input_node("mesh"))
        b = wf.add(W.tool_node(reg.get(TOOL.id), values=values))
        c = wf.add(W.save_node(where="folder", folder=str(self.root / "saved")))
        wf.connect(a.id, b.id)
        wf.connect(b.id, c.id)
        self.assertFalse(W.errors(W.check(wf, reg)))
        cache = W.WorkflowCache(self.root / "cache")
        first = W.run_workflow(wf, self.src, lambda *_: None, lambda: False, reg=reg, cache=cache)
        self.assertEqual(len(first.outputs), 3)
        for changes, expect_hit in (({}, True), ({"connector_seed": 7}, False), ({"connector_percent": 0.}, False)):
            b.values.update(changes)
            events = []
            result = W.run_workflow(wf, self.src, lambda k, p: events.append((k, p)), lambda: False, reg=reg, cache=cache)
            self.assertEqual(any(k == "node" and p[1] == "cached" for k, p in events), expect_hit)
            self.assertEqual(len(result.outputs), 2 if changes.get("connector_percent") == 0 else 3)


if __name__ == "__main__":
    unittest.main()
