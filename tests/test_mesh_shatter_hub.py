"""Mesh Shatter's hub connectors: off by default, exact shares, every network and core, solid and open results."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.job import JobContext
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_shatter import TOOL, connectors as hub, engine, mesh_shatter as ms


def context():
    return JobContext(lambda *_: None, lambda: False)


def model():
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=30.)
    mesh.vertices *= [1., .8, 1.4]
    return mesh


def settings(**over):
    values = dict(connectors=True, num_pieces=12, ejection=.4, perturb=.1, shard_thickness=.6)
    values.update(over)
    return TOOL.values(values)


def shards_of(mesh, p):
    return ms.shatter(mesh, p.num_pieces, p.ejection, p.edge_sensitivity, p.jitter, p.perturb, p.seed)


def join(**over):
    mesh, p = model(), settings(**over)
    shards = shards_of(mesh, p)
    return hub.join(mesh, shards, p), shards


class Placement(unittest.TestCase):
    def test_deep_point_is_inside_where_the_centroid_is_not(self):
        ring = trimesh.creation.annulus(r_min=20., r_max=30., height=12.)
        self.assertFalse(ring.contains([ring.centroid])[0])
        self.assertTrue(ring.contains([hub.deep_point(ring)])[0])

    def test_open_model_keeps_its_centroid(self):
        mesh = model()
        mesh.update_faces(np.arange(len(mesh.faces)) > 8)
        mesh.remove_unreferenced_vertices()
        np.testing.assert_allclose(hub.deep_point(mesh), mesh.centroid)

    def test_offset_moves_the_hub_by_a_share_of_the_diagonal(self):
        mesh = model()
        diag = float(np.linalg.norm(mesh.extents))
        base = hub.hub_point(mesh, settings(hub_position="centroid"), diag)
        moved = hub.hub_point(mesh, settings(hub_position="centroid", hub_offset_z=10.), diag)
        np.testing.assert_allclose(moved - base, [0., 0., diag * .1])


class Selection(unittest.TestCase):
    def test_exact_rounded_count(self):
        mesh, p = model(), settings()
        shards = shards_of(mesh, p)
        for percent, expected in ((0., 0), (4., 0), (5., 1), (50., 6), (100., 12)):
            p["connector_percent"] = percent
            self.assertEqual(len(hub.selected_indices(shards, np.zeros(3), p)), expected)

    def test_raising_the_share_keeps_the_earlier_choice(self):
        mesh, p = model(), settings()
        shards = shards_of(mesh, p)
        for way in ("random", "largest", "smallest", "nearest", "farthest"):
            p["connector_selection"] = way
            p["connector_percent"] = 25.
            small = set(hub.selected_indices(shards, np.zeros(3), p))
            p["connector_percent"] = 75.
            self.assertTrue(small <= set(hub.selected_indices(shards, np.zeros(3), p)), way)

    def test_farthest_picks_the_farthest(self):
        mesh, p = model(), settings(connector_percent=10., connector_selection="farthest")
        shards = shards_of(mesh, p)
        far = [np.linalg.norm(s.centroid) for s in shards]
        self.assertEqual(hub.selected_indices(shards, np.zeros(3), p), [int(np.argmax(far))])


class Thicken(unittest.TestCase):
    def test_a_patch_becomes_a_positive_closed_solid(self):
        mesh, p = model(), settings()
        for shard in shards_of(mesh, p):
            solid = trimesh.Trimesh(*hub.thicken(shard, 1.), process=False)
            self.assertTrue(solid.is_volume)
            self.assertGreater(solid.volume, 0.)


    def test_an_edge_with_three_faces_is_refused(self):
        vertices = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., -1., 0.], [0., 0., 1.]])
        fin = trimesh.Trimesh(vertices, np.array([[0, 1, 2], [0, 1, 3], [0, 1, 4]]), process=False)
        with self.assertRaisesRegex(ValueError, "simple surface"):
            hub.thicken(fin, .1)


class Saving(unittest.TestCase):
    def test_surfaces_touching_along_an_edge_stay_closed_once_welded(self):
        """Two cubes sharing one edge, each with its own copy of it: what broke Venus's saved file."""
        one, two = trimesh.creation.box(), trimesh.creation.box()
        two.apply_translation([1., 1., 0.])
        kissing = trimesh.util.concatenate([one, two])
        self.assertFalse(gm.clean(trimesh.Trimesh(kissing.vertices.copy(), kissing.faces.copy(), process=False)).is_volume)
        parted = hub.part_kisses(kissing, 3.)
        self.assertTrue(gm.clean(trimesh.Trimesh(parted.vertices.astype(np.float32), parted.faces.copy(), process=False)).is_volume)
        self.assertLess(np.abs(parted.vertices - kissing.vertices).max(), 1e-4)

    def test_apart_surfaces_are_left_alone(self):
        box = trimesh.creation.box()
        self.assertIs(hub.part_kisses(box, 2.), box)


class Networks(unittest.TestCase):
    def assert_fused(self, joined, shards):
        """One connected assembly, plus one piece for every shard left free."""
        self.assertTrue(joined.solid)
        self.assertTrue(joined.combined.is_volume)
        self.assertEqual(len(joined.combined.split(only_watertight=False)), 1 + len(shards) - len(joined.connected))
        self.assertEqual(joined.notes, [])

    def test_every_shape_network_and_core_fuses(self):
        for over in (dict(connector_style="organic", connector_network="tree", hub_core="knot"),
                     dict(connector_style="organic", connector_network="radial", hub_core="sphere"),
                     dict(connector_style="organic", connector_network="tree", hub_core="miniature"),
                     dict(connector_style="organic", connector_network="tree", hub_core="none"),
                     dict(connector_style="cylinder", hub_core="sphere"),
                     dict(connector_style="bar", hub_core="none"),
                     dict(connector_style="cylinder", hub_core="knot", connector_percent=50.)):
            with self.subTest(**over):
                joined, shards = join(**over)
                self.assert_fused(joined, shards)

    def test_a_tree_shares_trunks_and_radial_does_not(self):
        tree, _ = join(connector_network="tree", connector_trunks=3, hub_core="none")
        radial, _ = join(connector_network="radial", hub_core="none")
        self.assertGreater(tree.limbs, len(tree.connected))      # forks on the way
        self.assertEqual(radial.limbs, len(radial.connected))    # exactly one stem each

    def test_trunks_leave_the_hub(self):
        mesh, p = model(), settings(connector_trunks=3)
        shards = shards_of(mesh, p)
        anchors = [hub.anchor_on(i, s, 0.) for i, s in enumerate(shards)]
        net = hub.tree_network(np.zeros(3), anchors, dict(p, _diag=100., _thickness=0.), .02)
        self.assertEqual(len(net.roots), 3)
        self.assertEqual(sum(np.allclose(limb.path[0], 0.) for limb in net.limbs), 3)

    def test_limbs_thin_towards_the_shards(self):
        mesh, p = model(), settings(connector_trunks=2, connector_taper=.5)
        anchors = [hub.anchor_on(i, s, 0.) for i, s in enumerate(shards_of(mesh, p))]
        net = hub.tree_network(np.zeros(3), anchors, dict(p, _diag=100., _thickness=0.), .02)
        self.assertTrue(all(limb.r1 <= limb.r0 + 1e-12 for limb in net.limbs))
        self.assertLess(min(limb.r1 for limb in net.limbs), max(limb.r0 for limb in net.limbs) * .6)

    def test_tips_stay_inside_a_thick_shard(self):
        mesh, p = model(), settings(connector_diameter_min=3., connector_diameter_max=4., connector_branches=3)
        anchors = [hub.anchor_on(i, s, 1.) for i, s in enumerate(shards_of(mesh, p))]
        for build in (hub.tree_network, hub.radial_network):
            net = build(np.zeros(3), anchors, dict(p, _diag=100., _thickness=1.), .02)
            ends = [limb for limb in net.limbs if any(np.allclose(limb.path[-1], a.tip) for a in anchors)]
            self.assertTrue(ends)
            self.assertLessEqual(max(limb.r1 for limb in ends), .45 + 1e-9)

    def test_extra_branches_grow_and_stay_seeded(self):
        joined, _ = join(connector_branches=3, connector_branch_levels=2, connector_network="radial", num_pieces=8)
        self.assertEqual(joined.requested, 24)
        self.assertGreater(joined.branches, 12)
        ranged, _ = join(connector_branch_count="random", connector_branches_min=1, connector_branches_max=3, num_pieces=8)
        self.assertTrue(8 <= ranged.requested <= 24)

    def test_open_shards_stay_open_and_the_network_is_closed(self):
        joined, shards = join(shard_thickness=0.)
        self.assertFalse(joined.solid)
        self.assertFalse(joined.combined.is_watertight)
        self.assertTrue(joined.network.is_volume)
        self.assertEqual(len(joined.combined.faces), sum(len(s.faces) for s in shards) + len(joined.network.faces))

    def test_the_hub_never_swallows_a_shard(self):
        mesh, p = model(), settings(hub_size=25., ejection=0.)
        shards = shards_of(mesh, p)
        joined = hub.join(mesh, shards, p)
        room = min(float(np.linalg.norm(s.vertices - joined.hub, axis=1).min()) for s in shards)
        self.assertLess(joined.hub_radius, room)

    def test_a_tiny_share_gives_nothing_to_join(self):
        joined, _ = join(connector_percent=1.)
        self.assertIsNone(joined)

    def test_repeatable_and_shards_do_not_move(self):
        mesh, p = model(), settings()
        before = shards_of(mesh, p)
        positions = [s.vertices.copy() for s in before]
        first = hub.join(mesh, before, p).combined
        second = hub.join(mesh, shards_of(mesh, p), p).combined
        for shard, position in zip(before, positions):
            np.testing.assert_array_equal(shard.vertices, position)
        self.assertEqual(len(first.faces), len(second.faces))
        np.testing.assert_allclose(first.bounds, second.bounds)

    def test_bad_ranges_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Connector thickness"):
            hub.validate(settings(connector_diameter_min=3., connector_diameter_max=1.))
        with self.assertRaisesRegex(ValueError, "Branches minimum"):
            hub.validate(settings(connector_branch_count="random", connector_branches_min=9, connector_branches_max=2))

    def test_face_budget_stops_the_run(self):
        with self.assertRaisesRegex(ValueError, "Maximum faces"):
            join(max_faces=10_000)


class Engine(unittest.TestCase):
    def run_tool(self, folder, **over):
        src = Path(folder) / "sample.stl"
        model().export(src)
        p = TOOL.values(dict(dict(num_pieces=10), **over))
        dst = Path(folder) / "out.stl"
        return engine.run(context(), src, dst, p), dst

    def test_off_is_the_plain_shatter(self):
        with tempfile.TemporaryDirectory() as folder:
            result, dst = self.run_tool(folder)
            saved = gm.load_mesh(dst)
            source = gm.load_mesh(Path(folder) / "sample.stl")
            p = TOOL.values(dict(num_pieces=10))
            plain = trimesh.util.concatenate(shards_of(source, p))
            self.assertEqual(len(saved.faces), len(plain.faces))
            np.testing.assert_allclose(saved.bounds, plain.bounds, atol=1e-3)
            self.assertNotIn("Connected shards", [s.label for s in result.stats])
            self.assertEqual([v["title"] for v in result.preview["views"]], ["Output"])

    def test_presets_save_closed_files(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("Living Roots", "Taut Tethers"):
                with self.subTest(name):
                    src = Path(folder) / "sample.stl"
                    # A model in large units, as scans are: sealing must follow its size.
                    mesh = model()
                    mesh.vertices *= 12.
                    mesh.export(src)
                    dst = Path(folder) / f"{name}.stl"
                    result = engine.run(context(), src, dst, _presets.values(TOOL, name))
                    self.assertTrue(gm.load_mesh(dst).is_volume)
                    self.assertIn("Connectors", [v["title"] for v in result.preview["views"]])
                    self.assertIn("Connected shards", [s.label for s in result.stats])

    def test_separate_files_include_the_connectors(self):
        with tempfile.TemporaryDirectory() as folder:
            result, _ = self.run_tool(folder, connectors=True, separate=True)
            names = [Path(path).name for path in result.outputs]
            self.assertIn("sample_connectors.stl", names)
            self.assertEqual(sum("_piece_" in name for name in names), 10)


if __name__ == "__main__":
    unittest.main()
