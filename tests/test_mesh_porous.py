"""Porous Forms: the fields (signed distance, wall distance), the envelope, closed output, refinement, influence,
seeds, stage sets and the input checks."""
import _isolate  # first: settings and caches stay in a temporary folder
import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.io import output_path
from atlas_toolkit.core.job import JobContext
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_porous import TOOL, engine
from atlas_toolkit.tools.mesh_porous import mesh_porous as mp

QUICK = dict(resolution=64, cell=30.0, wall=6.0, max_faces=0, smooth=0)


def context(mode="run", log=None):
    def emit(kind, args):
        if log is not None and kind == "log":
            log.append(args)
    return JobContext(emit, lambda: False, mode)


def run_mesh(src, mode="run", log=None, **values):
    """Run with QUICK settings and the given ones; returns (result, last preview mesh as trimesh)."""
    vals = TOOL.values(dict(QUICK, **values))
    res = engine.run(context(mode, log), src, None, vals)
    v = res.preview["views"][-1]["mesh"]
    return res, trimesh.Trimesh(v.vertices, v.faces, process=False)


class FieldTests(unittest.TestCase):
    def test_signed_distance_is_exact_near_the_surface_and_signed_everywhere(self):
        s = trimesh.creation.icosphere(subdivisions=4, radius=1.0)
        grid = mp.make_grid(s.bounds[0], s.bounds[1], 48)
        D = mp.signed_distance(s.vertices, s.faces, grid, band=0.2)
        P = grid.points(np.arange(grid.size))
        exact = np.linalg.norm(P, axis=1) - 1.0
        D = D.reshape(-1)
        near = np.abs(exact) < 0.2
        np.testing.assert_allclose(D[near], exact[near], atol=5e-3)          # faceted sphere vs true sphere
        self.assertTrue(np.all(np.sign(D[~near]) == np.sign(exact[~near])))
        self.assertTrue(np.all(np.abs(D[~near]) >= 0.2 - 5e-3))

    def test_sheet_field_is_the_distance_to_the_walls_near_them(self):
        for name in ("gyroid", "diamond", "primitive", "neovius"):
            rng = np.random.default_rng(1)
            Q = rng.random((4000, 3))
            g = mp.tpms(name, Q)
            on = Q[np.abs(g) < 0.02]
            # step a known distance along the normal from points near the zero set
            for _ in range(3):                                   # Newton steps onto the zero set
                G = np.stack([(mp.tpms(name, on + d) - mp.tpms(name, on - d)) / 2e-4 for d in np.eye(3) * 1e-4], 1)
                on = on - (mp.tpms(name, on) / (G * G).sum(1))[:, None] * G
            n = G / np.linalg.norm(G, axis=1, keepdims=True)
            cell, off = 2.0, 0.01
            f = mp.tpms_field(name, "sheet", on + n * off / cell, np.full(len(on), cell))
            self.assertLess(np.median(np.abs(f - off)), 0.15 * off, name)

    def test_struts_follow_the_channel_centre_lines(self):
        for name in ("gyroid", "diamond", "primitive", "neovius"):
            tree = mp.tpms_skeleton(name)
            P = tree.data[:200]
            self.assertTrue(np.all(mp.tpms(name, P) < 0), name)              # inside the channel
            # periodic: a whole period away is the same distance
            Q = np.random.default_rng(2).random((500, 3))
            c = np.ones(len(Q))
            np.testing.assert_allclose(mp.tpms_network(name, Q, c), mp.tpms_network(name, Q + 1.0, c), atol=1e-12)
        # Schwarz P's channels run along the cell's axes through its centre (or its corners)
        d = mp.tpms_network("primitive", np.array([[0.5, 0.5, 0.2], [0.0, 0.0, 0.3]]), np.ones(2))
        self.assertLess(d.min(), 0.03)

    def test_voronoi_walls_are_equidistant_points(self):
        from scipy.spatial import cKDTree
        seeds = mp.voronoi_seeds((0, 0, 0), (3, 3, 3), seed=4)
        tree = cKDTree(seeds)
        mid = (seeds[0] + seeds[1]) / 2.0
        a, b = np.argsort(np.linalg.norm(seeds - mid, axis=1))[:2]
        mid = (seeds[a] + seeds[b]) / 2.0
        self.assertLess(mp.voronoi_field(tree, "sheet", mid[None], np.array([1.0]))[0], 1e-9)
        np.testing.assert_array_equal(seeds, mp.voronoi_seeds((0, 0, 0), (3, 3, 3), seed=4))
        self.assertFalse(np.allclose(seeds, mp.voronoi_seeds((0, 0, 0), (3, 3, 3), seed=5)))

    def test_grading_keeps_the_pattern_continuous(self):
        z = np.linspace(-1, 1, 2001)
        P = np.stack([np.zeros_like(z), np.zeros_like(z), z], 1)
        Q, c = mp.pattern_coords(P, (0, 0, 0), 0.2, grade=3.0, z0=-1.0, height=2.0)
        dz = np.diff(Q[:, 2])
        self.assertTrue(np.all(dz > 0))
        self.assertAlmostEqual(c[-1] / c[0], 3.0, places=6)
        # one cell of pattern per local cell size
        np.testing.assert_allclose(dz / np.diff(z), 1.0 / c[:-1], rtol=2e-3)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.src = self.folder / "pillar.stl"
        trimesh.creation.capsule(height=60.0, radius=18.0, count=[32, 32]).export(self.src)

    def test_amount_zero_is_the_input(self):
        res, out = run_mesh(self.src, amount=0.0)
        mesh = gm.load_mesh(self.src)
        np.testing.assert_allclose(np.sort(out.vertices, axis=0), np.sort(mesh.vertices, axis=0), atol=1e-6)

    def test_closed_inside_the_envelope_and_porous(self):
        res, out = run_mesh(self.src)
        src = gm.load_mesh(self.src)
        self.assertTrue(out.is_watertight)
        self.assertGreater(out.volume, 0)                       # outward faces
        self.assertLess(out.volume, 0.8 * src.volume)
        self.assertGreater(out.volume, 0.1 * src.volume)
        voxel = res.data["voxel"]
        import igl
        d = igl.signed_distance(out.vertices, src.vertices, np.asarray(src.faces, np.int64))[0]
        self.assertLess(d.max(), 0.75 * voxel)                  # inside the source, within sampling tolerance
        self.assertGreater(out.euler_number, -1000)
        self.assertLess(out.euler_number, 2)                    # holes: genus > 0

    def test_wall_thickness_converges_under_refinement(self):
        fractions = []
        for res in (64, 96):
            _, out = run_mesh(self.src, resolution=res)
            fractions.append(out.volume)
        self.assertLess(abs(fractions[1] - fractions[0]) / fractions[1], 0.08)

    def test_torus_keeps_its_hole_and_two_bodies_both_turn_porous(self):
        torus = trimesh.creation.torus(major_radius=30, minor_radius=12, major_sections=64, minor_sections=32)
        a = trimesh.creation.icosphere(subdivisions=3, radius=15)
        b = a.copy()
        b.apply_translation((50, 0, 0))
        for name, m in (("torus", torus), ("pair", trimesh.util.concatenate([a, b]))):
            src = self.folder / f"{name}.stl"
            m.export(src)
            _, out = run_mesh(src)
            self.assertTrue(out.is_watertight, name)
            self.assertLess(out.volume, 0.8 * m.volume, name)
            if name == "pair":
                bodies = out.split(only_watertight=False)
                xs = [p.centroid[0] for p in bodies if p.volume > 0.02 * out.volume]
                self.assertTrue(any(x < 25 for x in xs) and any(x > 25 for x in xs))
            else:
                # the torus's hole stays empty
                self.assertFalse(trimesh.Trimesh(out.vertices, out.faces).contains([[0, 0, 0]])[0])

    def test_protected_sphere_stays_solid(self):
        # the lower half kept solid: its volume matches the source's lower half
        res, out = run_mesh(self.src, influence="band", band_from=50.0, band_to=100.0, feather=0.0)
        src = gm.load_mesh(self.src)
        zmid = src.bounds[:, 2].mean()
        lo_box = trimesh.creation.box(bounds=[src.bounds[0] - 1, [*(src.bounds[1][:2] + 1), zmid - 8]])
        kept = out.slice_plane([0, 0, zmid - 8], [0, 0, -1], cap=True)
        whole = src.slice_plane([0, 0, zmid - 8], [0, 0, -1], cap=True)
        self.assertGreater(lo_box.volume, 0)
        self.assertAlmostEqual(kept.volume / whole.volume, 1.0, delta=0.03)
        top = out.slice_plane([0, 0, zmid + 8], [0, 0, 1], cap=True)
        top_src = src.slice_plane([0, 0, zmid + 8], [0, 0, 1], cap=True)
        self.assertLess(top.volume, 0.8 * top_src.volume)

    def test_depth_keeps_a_solid_core(self):
        _, through = run_mesh(self.src)
        _, cored = run_mesh(self.src, depth=8.0)
        self.assertGreater(cored.volume, through.volume * 1.2)

    def test_voronoi_seed_is_repeatable(self):
        _, a = run_mesh(self.src, pattern="voronoi", seed=3)
        _, b = run_mesh(self.src, pattern="voronoi", seed=3)
        _, c = run_mesh(self.src, pattern="voronoi", seed=4)
        np.testing.assert_array_equal(a.vertices, b.vertices)
        self.assertNotEqual(len(a.vertices), len(c.vertices))
        _, opened = run_mesh(self.src, pattern="voronoi", seed=3, windows=70.0)
        self.assertLess(opened.volume, 0.9 * a.volume)                  # windows cut into the walls

    def test_struts_and_every_pattern_extract(self):
        for pattern in ("gyroid", "diamond", "primitive", "neovius", "voronoi"):
            for style in ("sheet", "network"):
                _, out = run_mesh(self.src, pattern=pattern, style=style, resolution=48, wall=8.0)
                self.assertGreater(len(out.faces), 100, (pattern, style))

    def test_thin_walls_are_refused_with_the_fix(self):
        with self.assertRaises(ValueError) as e:
            run_mesh(self.src, wall=1.0, resolution=64)
        self.assertIn("Resolution", str(e.exception))
        log = []
        run_mesh(self.src, mode="draft", log=log, wall=1.0, draft_resolution=48)     # preview: a warning only
        self.assertTrue(any(level == "warning" and "Resolution" in msg for level, msg in log))

    def test_open_surface_warns_and_flat_sheet_is_refused(self):
        bowl = trimesh.creation.icosphere(subdivisions=3, radius=20)
        bowl.update_faces(bowl.face_normals[:, 2] < 0.7)          # a small opening on top
        bowl.remove_unreferenced_vertices()
        src = self.folder / "bowl.stl"
        bowl.export(src)
        log = []
        run_mesh(src, log=log)
        self.assertTrue(any(level == "warning" and "not closed" in msg for level, msg in log))
        plane = trimesh.Trimesh([[0, 0, 0], [40, 0, 0], [40, 0, 40], [0, 0, 40]], [[0, 1, 2], [0, 2, 3]])
        src = self.folder / "plane.stl"
        plane.export(src)
        with self.assertRaises(ValueError):
            run_mesh(src)

    def test_stage_set_shares_its_fields_and_starts_from_the_model(self):
        vals = TOOL.values(dict(QUICK, stages=3))
        dst = output_path(TOOL, self.src, vals)
        res = engine.run(context(), self.src, dst, vals)
        self.assertEqual(len(res.outputs), 4)
        folder = Path(res.outputs[1]).parent
        manifest = json.loads((folder / "stages.json").read_text(encoding="utf-8"))
        self.assertEqual([s["amount"] for s in manifest["stages"]], [0.0, 0.5, 1.0])
        first = gm.load_mesh(res.outputs[1])
        np.testing.assert_allclose(first.volume, gm.load_mesh(self.src).volume, rtol=1e-6)
        mid = gm.load_mesh(res.outputs[2])
        _, alone = run_mesh(self.src, amount=0.5)
        self.assertAlmostEqual(mid.volume / alone.volume, 1.0, places=4)
        self.assertIn(b"atlas-toolkit:mesh-porous", Path(res.outputs[0]).read_bytes()[:80])

    def test_output_keeps_model_units_and_place(self):
        m = trimesh.creation.box(extents=(30, 20, 50))
        m.apply_translation((100, -40, 7))
        src = self.folder / "block.stl"
        m.export(src)
        _, out = run_mesh(src)
        np.testing.assert_allclose(out.bounds, m.bounds, atol=1.0)

    def test_dry_runs_write_nothing(self):
        vals = TOOL.values(dict(QUICK, stages=2))
        dst = output_path(TOOL, self.src, vals)
        engine.run(context("draft"), self.src, dst, vals)
        engine.run(context("analyse"), self.src, dst, vals)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["pillar.stl"])


class WorkflowStepTests(unittest.TestCase):
    def test_porous_then_contours_in_a_workflow(self):
        from atlas_toolkit.core import samples
        from atlas_toolkit.core import workflow as W
        from atlas_toolkit.core.registry import registry
        reg = registry()
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "in").mkdir()
            (tmp / "out").mkdir()
            mesh = samples.mesh(tmp / "in")
            contours = reg.get("contour-lines")
            wf = W.Workflow(name="Porous contours")
            src = wf.add(W.input_node("mesh"))
            porous = wf.add(W.tool_node(reg.get("mesh-porous"), values=dict(QUICK)))
            draw = wf.add(W.tool_node(contours, values=dict(contours.test_values)))
            out = wf.add(W.save_node(where="folder", folder=str(tmp / "out")))
            wf.connect(src.id, porous.id)
            wf.connect(porous.id, draw.id)
            wf.connect(draw.id, out.id)
            self.assertFalse([m for m in W.check(wf, reg) if m.level == "error"])
            res = W.run_workflow(wf, mesh, lambda *_: None, lambda: False, reg=reg,
                                 cache=W.WorkflowCache(tmp / "cache"), tool_cache=str(tmp / "toolcache"))
            self.assertEqual(len(res.outputs), 1)
            self.assertIn("_porous_contours", Path(res.outputs[0]).name)
            self.assertIn(b"<desc>contour-lines</desc>", Path(res.outputs[0]).read_bytes()[:2000])


if __name__ == "__main__":
    unittest.main()
