"""Analytic volume, packing, presentation, export and drawing integration checks."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from atlas_toolkit.core.job import Cancelled, JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.surface import load_surface
from atlas_toolkit.tools.voxelize_fill import TOOL
from atlas_toolkit.tools.voxelize_fill import voxelize_fill as vf
from atlas_toolkit.tools.voxelize_fill.engine import run


def context(mode="run"):
    return JobContext(lambda *args: None, lambda: False, mode)


class VolumeTests(unittest.TestCase):
    def setUp(self):
        self.box = trimesh.creation.box(extents=(8., 8., 8.))
        self.p = TOOL.values({"across": 8})

    def test_full_volume_includes_every_interior_cell(self):
        out, data, _ = vf.build(self.box, self.p)
        self.assertEqual(data["voxels"], 512)
        self.assertEqual(data["retained"], 512)
        self.assertEqual(data["blocks"], 512)
        self.assertEqual(len(out.triangles), 512 * 12)
        self.assertAlmostEqual(out.as_trimesh().volume, 512.)
        np.testing.assert_allclose(out.bounds, self.box.bounds)
        grid = vf.make_grid(self.box.bounds, 1.)
        cells, _ = vf.classify(self.box, grid)
        self.assertTrue(cells[4, 4, 4])
        self.assertTrue(cells[:, :, 4].all())

    def test_joined_discards_shared_faces_and_is_closed(self):
        out, data, _ = vf.build(self.box, dict(self.p, geometry="joined"))
        self.assertEqual(data["quads"], 6 * 8 * 8)
        mesh = out.as_trimesh()
        self.assertTrue(mesh.is_watertight)
        self.assertTrue(mesh.is_winding_consistent)
        self.assertAlmostEqual(mesh.volume, 512.)

    def test_oriented_cavity_and_global_inversion(self):
        inner = trimesh.creation.box(extents=(4., 4., 4.))
        inner.invert()
        hollow = trimesh.util.concatenate([self.box, inner])
        for m in (hollow, hollow.copy()):
            if m is not hollow:
                m.invert()
            grid = vf.make_grid(m.bounds, 1.)
            cells, _ = vf.classify(m, grid, "closed")
            self.assertEqual(int(cells.sum()), 512 - 64)
            self.assertFalse(cells[4, 4, 4])
            self.assertTrue(cells[0, 4, 4])

    def test_through_hole_stays_empty(self):
        ring = trimesh.creation.annulus(r_min=2., r_max=4., height=4., sections=48)
        grid = vf.make_grid(ring.bounds, 0.5)
        cells, _ = vf.classify(ring, grid, "closed")
        self.assertFalse(cells[7:9, 7:9].any())
        self.assertTrue(cells[1:3, 7:9].any())

    def test_disconnected_solids_and_concave_space(self):
        a = trimesh.creation.box(extents=(2., 2., 2.))
        b = a.copy()
        b.apply_translation((6., 0., 0.))
        model = trimesh.util.concatenate([a, b])
        grid = vf.make_grid(model.bounds, 1.)
        cells, _ = vf.classify(model, grid)
        self.assertEqual(int(cells.sum()), 16)
        self.assertFalse(cells[2:6].any())

    def test_scale_and_translation_preserve_cell_decisions(self):
        baseline, _, _ = vf.build(self.box, dict(self.p, geometry="joined"))
        m = self.box.copy()
        m.apply_scale(0.001)
        m.apply_translation((1200., -500., 700.))
        out, data, _ = vf.build(m, dict(self.p, geometry="joined"))
        self.assertEqual(data["voxels"], 512)
        np.testing.assert_allclose((out.vertices - (1200., -500., 700.)) / 0.001,
                                   baseline.vertices, atol=1e-8)

    def test_open_scan_warning_strict_rejection_and_input_unchanged(self):
        model = self.box.copy()
        model.update_faces(np.arange(len(model.faces)) != 0)
        original = model.faces.copy()
        grid = vf.make_grid(model.bounds, 1.)
        cells, warnings = vf.classify(model, grid)
        self.assertTrue(cells[4, 4, 4])
        self.assertTrue(any("Open input" in x for x in warnings))
        np.testing.assert_array_equal(model.faces, original)
        with self.assertRaisesRegex(ValueError, "watertight"):
            vf.classify(model, grid, "closed")

    def test_mixed_adjacent_winding_is_repaired_on_copy(self):
        m = self.box.copy()
        m.faces[0] = m.faces[0][::-1]
        original = m.faces.copy()
        cells, warnings = vf.classify(m, vf.make_grid(m.bounds, 1.))
        self.assertEqual(int(cells.sum()), 512)
        self.assertTrue(any("winding was repaired" in x for x in warnings))
        np.testing.assert_array_equal(m.faces, original)

    def test_grid_and_face_guards_precede_large_allocation(self):
        with patch.object(vf.np, "empty", side_effect=AssertionError("allocated")):
            with self.assertRaisesRegex(ValueError, "grid cells"):
                vf.make_grid(self.box.bounds, 0.000001)
        with patch.object(vf, "MAX_TRIANGLES", 100):
            with self.assertRaisesRegex(ValueError, "triangles"):
                vf.pack(np.zeros((8, 8, 8), dtype=np.int32))
        for pitch in (0., -1., np.nan, np.inf):
            with self.assertRaises(ValueError):
                vf.make_grid(self.box.bounds, pitch)

    def test_cancellation_during_volume_query(self):
        def cancel(fraction, message):
            if message == "classifying solid interior":
                raise Cancelled()
        with self.assertRaises(Cancelled):
            vf.build(self.box, self.p, cancel)

    def test_empty_mesh_fails_with_volume_guidance(self):
        with self.assertRaisesRegex(ValueError, "enclosing a volume"):
            vf.build(trimesh.Trimesh(), self.p)


class PackingAndPresentationTests(unittest.TestCase):
    def setUp(self):
        self.p = TOOL.values({"across": 8})
        self.grid = vf.Grid(np.zeros(3), 1., (8, 8, 8))
        self.cells = np.ones(self.grid.shape, dtype=bool)

    def reconstruct(self, blocks):
        result = np.full(self.grid.shape, -1, dtype=int)
        for low, size, label in zip(blocks.lower, blocks.sizes, blocks.groups):
            sl = tuple(slice(int(a), int(a + b)) for a, b in zip(low, size))
            self.assertTrue((result[sl] == -1).all(), "blocks overlap")
            result[sl] = label
        return result

    def test_adaptive_cube_and_every_bar_axis_preserve_volume(self):
        groups = np.zeros(self.grid.shape, dtype=np.int32)
        packed = vf.pack(groups, "adaptive", 3)
        self.assertEqual(len(packed.lower), 1)
        np.testing.assert_array_equal(packed.sizes, [[8, 8, 8]])
        np.testing.assert_array_equal(self.reconstruct(packed), groups)
        for axis in range(3):
            bars = vf.pack(groups, "runs", axis=axis)
            self.assertEqual(len(bars.lower), 64)
            np.testing.assert_array_equal(self.reconstruct(bars), groups)

    def test_packing_irregular_holes_and_group_boundaries(self):
        rng = np.random.default_rng(4)
        groups = rng.integers(-1, 3, self.grid.shape, dtype=np.int32)
        groups[0:4, 0:4, 0:4] = 1
        for layout in ("cubes", "adaptive", "runs"):
            for axis in range(3):
                packed = vf.pack(groups, layout, axis=axis)
                np.testing.assert_array_equal(self.reconstruct(packed), groups)

    def test_adaptive_odd_grid_does_not_merge_into_exterior(self):
        groups = np.zeros((5, 7, 3), dtype=np.int32)
        packed = vf.pack(groups, "adaptive", 6)
        self.assertEqual(int(np.prod(packed.sizes, axis=1).sum()), 105)
        self.assertTrue(np.all(packed.lower + packed.sizes <= (5, 7, 3)))

    def test_explosion_retains_volume_and_zero_is_identity(self):
        p = dict(self.p, effect="explode", layer_cells=2, separation=200.)
        cells, groups = vf.present(self.cells, self.grid, p)
        self.assertTrue(cells.all())
        out = vf.joined_surface(groups, self.grid, p)
        mesh = out.as_trimesh()
        self.assertTrue(mesh.is_watertight)
        self.assertAlmostEqual(mesh.volume, 512.)
        self.assertEqual(len(mesh.split()), 4)
        np.testing.assert_allclose(out.extents, (8., 8., 14.))
        zero = dict(p, separation=0.)
        _, groups_zero = vf.present(self.cells, self.grid, zero)
        np.testing.assert_array_equal(groups_zero, np.zeros(self.grid.shape))
        self.assertEqual(len(vf.joined_surface(groups_zero, self.grid, zero).faces), 384)

    def test_cutaway_complement_and_oblique_normal(self):
        p = dict(self.p, cutaway=True, cut_normal=(1., 0., 0.))
        low, _ = vf.present(self.cells, self.grid, p)
        high, _ = vf.present(self.cells, self.grid, dict(p, cut_side="upper"))
        self.assertEqual(int(low.sum()), 256)
        np.testing.assert_array_equal(~low, high)
        oblique, _ = vf.present(self.cells, self.grid, dict(p, cut_normal=(1., 0.3, 0.2)))
        self.assertTrue(oblique[0, 0, 0])
        self.assertFalse(oblique[-1, -1, -1])
        with self.assertRaisesRegex(ValueError, "normal cannot be zero"):
            vf.present(self.cells, self.grid, dict(p, cut_normal=(0., 0., 0.)))

    def test_distance_bands_use_exterior_padding(self):
        cells, _ = vf.present(self.cells, self.grid, dict(self.p, effect="bands", band_cells=1))
        self.assertTrue(cells[0, 4, 4])       # boundary distance 1
        self.assertFalse(cells[1, 4, 4])     # distance 2
        self.assertTrue(cells[2, 4, 4])      # distance 3
        self.assertFalse(cells[3, 4, 4])     # distance 4

    def test_erosion_seed_endpoints_and_repeatability(self):
        p = dict(self.p, effect="erode", roughness=100., erosion=45.)
        a, _ = vf.present(self.cells, self.grid, p)
        b, _ = vf.present(self.cells, self.grid, p)
        np.testing.assert_array_equal(a, b)
        c, _ = vf.present(self.cells, self.grid, dict(p, seed=23))
        self.assertFalse(np.array_equal(a, c))
        self.assertGreater(int(a.sum()), 0)
        self.assertLess(int(a.sum()), 512)
        zero, _ = vf.present(self.cells, self.grid, dict(p, erosion=0.))
        np.testing.assert_array_equal(zero, self.cells)
        with self.assertRaisesRegex(ValueError, "every voxel"):
            vf.present(self.cells, self.grid, dict(p, erosion=100.))

    def test_gap_preserves_block_centres_and_shrinks_volume(self):
        groups = np.zeros(self.grid.shape, dtype=np.int32)
        blocks = vf.pack(groups, "adaptive", 3)
        out = vf.separate_surface(blocks, self.grid, dict(self.p, gap=20.))
        np.testing.assert_allclose(out.bounds.mean(0), (4., 4., 4.))
        np.testing.assert_allclose(out.extents, (7.8, 7.8, 7.8))
        self.assertAlmostEqual(out.as_trimesh().volume, 7.8 ** 3)


class IntegrationTests(unittest.TestCase):
    def test_every_preset_builds_and_export_roundtrips_native_faces(self):
        m = trimesh.creation.icosphere(subdivisions=1, radius=4.)
        with tempfile.TemporaryDirectory(prefix="atlas_voxel_fill_") as folder:
            folder = Path(folder)
            src = folder / "source.stl"
            m.export(src)
            for i, preset in enumerate([None] + _presets.optional(TOOL)):
                p = _presets.run_values(TOOL, preset, across=8) if preset else TOOL.values({"across": 8})
                result = run(context(), src, folder / f"settings-{i}.obj", p)
                self.assertGreater(result.data["retained"], 0)
                self.assertIn(TOOL.signature, Path(result.outputs[0]).read_text())
            p = TOOL.values({"across": 6, "geometry": "joined"})
            for ext in (".obj", ".ply", ".stl"):
                dst = folder / ("roundtrip" + ext)
                result = run(context(), src, dst, p)
                self.assertIn(TOOL.signature, gm.read_signature(dst))
                surface = load_surface(dst)
                self.assertTrue(surface.as_trimesh().is_watertight)
                self.assertEqual(len(surface.faces), result.data["triangles"] if ext == ".stl" else result.data["quads"])
                self.assertGreater(surface.as_trimesh().volume, 0.)

    def test_preview_writes_nothing_and_has_final_geometry(self):
        with tempfile.TemporaryDirectory(prefix="atlas_voxel_preview_") as folder:
            src = Path(folder) / "source.stl"
            trimesh.creation.box().export(src)
            p = TOOL.values({"across": 4})
            preview = run(context("draft"), src, None, p)
            saved = run(context(), src, Path(folder) / "saved.obj", p)
            self.assertEqual(preview.outputs, [])
            self.assertEqual(preview.data, saved.data)
            self.assertIn("Final geometry", preview.preview["views"][0]["status"])
            self.assertEqual(set(x.name for x in Path(folder).iterdir()), {"source.stl", "saved.obj"})
            self.assertEqual(load_surface(Path(folder) / "saved.obj").frame["up"], "+z")

    def test_joined_diagonal_contact_is_reported(self):
        # Two cells sharing only a vertical edge: the boundary has four faces
        # incident on that edge. The engine must not claim a printable manifold.
        groups = np.full((2, 2, 1), -1, dtype=np.int32)
        groups[0, 0, 0] = groups[1, 1, 0] = 0
        p = TOOL.values({"geometry": "joined"})
        out = vf.joined_surface(groups, vf.Grid(np.zeros(3), 1., groups.shape), p)
        self.assertFalse(out.as_trimesh().is_watertight)

    def test_explicit_stl_axes_survive_conversion_to_obj(self):
        with tempfile.TemporaryDirectory(prefix="atlas_voxel_axes_") as folder:
            source = Path(folder) / "source.stl"
            target = Path(folder) / "result.obj"
            m = trimesh.creation.box()
            from atlas_toolkit.geom.surface import from_mesh
            s = from_mesh(m)
            s.frame = dict(up="+x", front="-y", rotate=[0., 0., 0.])
            gm.save_mesh(s, source, TOOL)
            run(context(), source, target, TOOL.values({"across": 3}))
            self.assertEqual(load_surface(target).frame, s.frame)

    def test_cancelled_staged_export_preserves_previous_file(self):
        from atlas_toolkit.geom.mesh_export import publish_meshes
        m, _, _ = vf.build(trimesh.creation.box(), TOOL.values({"across": 2}))
        with tempfile.TemporaryDirectory(prefix="atlas_voxel_cancel_") as folder:
            dst = Path(folder) / "existing.obj"
            dst.write_text("previous output")
            with self.assertRaises(Cancelled):
                publish_meshes([m], dst, JobContext(lambda *a: None, lambda: True), TOOL)
            self.assertEqual(dst.read_text(), "previous output")

    def test_native_quads_draw_without_face_diagonals(self):
        from atlas_toolkit.core.params import Params
        from atlas_toolkit.tools.plotter_vision.engine import run as draw
        vision = registry().get("plotter-vision")
        with tempfile.TemporaryDirectory(prefix="atlas_voxel_draw_") as folder:
            src = Path(folder) / "source.stl"
            trimesh.creation.box().export(src)
            blocks = Path(folder) / "blocks.obj"
            run(context(), src, blocks, TOOL.values({"across": 3, "gap": 12.}))
            surface = load_surface(blocks)
            self.assertEqual(len(surface.edges), 27 * 12)
            svg = Path(folder) / "drawing.svg"
            p = Params(vision.values(dict(vision.test_values, edge_mode="mesh", decimate=False,
                                         crossings=False, outline_layer=False)))
            result = draw(context(), blocks, svg, p)
            self.assertTrue(svg.exists())
            self.assertIn("<svg", svg.read_text())
            self.assertTrue(result.outputs)

    def test_workflow_chains_mesh_to_drawing_and_reuses_cache(self):
        from atlas_toolkit.core import workflow as W
        reg = registry()
        with tempfile.TemporaryDirectory(prefix="atlas_voxel_flow_") as folder:
            root = Path(folder)
            src = root / "source.stl"
            trimesh.creation.box().export(src)
            wf = W.Workflow(name="Filled blocks")
            incoming = wf.add(W.input_node("mesh"))
            voxel = wf.add(W.tool_node(reg.get(TOOL.id), values={"across": 3, "gap": 12.}))
            vision = reg.get("plotter-vision")
            lines = wf.add(W.tool_node(vision, values=dict(vision.test_values, edge_mode="mesh", crossings=False)))
            outgoing = wf.add(W.save_node(where="folder", folder=str(root / "saved")))
            wf.connect(incoming.id, voxel.id)
            wf.connect(voxel.id, lines.id)
            wf.connect(lines.id, outgoing.id)
            self.assertEqual(W.errors(W.check(wf, reg)), [])
            cache = W.WorkflowCache(root / "cache")
            events = []
            result = W.run_workflow(wf, src, lambda k, p: events.append((k, p)), lambda: False, reg=reg, cache=cache)
            self.assertTrue(result.outputs)
            self.assertTrue(all(Path(p).exists() for p in result.outputs))
            events.clear()
            W.run_workflow(wf, src, lambda k, p: events.append((k, p)), lambda: False, reg=reg, cache=cache)
            self.assertTrue(any(k == "node" and p[0] == voxel.id and p[1] == "cached" for k, p in events))


if __name__ == "__main__":
    unittest.main()
