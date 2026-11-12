"""Contour Lines: slicing, the shared slice grid of a stage set, hidden lines, plotting hygiene, and parity with the
Blender pieces' own functions (lifted from the plotter-contour-art skill when it is installed)."""
import _isolate  # first: settings and caches stay in a temporary folder
import ast
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.io import output_path
from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.tools.contour_lines import TOOL
from atlas_toolkit.tools.contour_lines import contour_lines as cl
from atlas_toolkit.tools.contour_lines import engine

SKILL = Path.home() / ".claude" / "skills" / "plotter-contour-art" / "scripts" / "head_plot_pipeline.py"


def context(mode="run"):
    return JobContext(lambda *_: None, lambda: False, mode)


def lift(path: Path, names: list) -> dict:
    """Plain functions from a script, without running its imports (bpy)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {"math": math, "np": np}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), ns)
    return ns


class SliceTests(unittest.TestCase):
    def test_heights_share_one_grid(self):
        a = cl.slice_heights(-3.3, 7.1, 0.5, origin=-10.0)
        b = cl.slice_heights(-1.0, 12.0, 0.5, origin=-10.0)
        self.assertTrue(np.allclose(((a + 10.0) / 0.5) % 1.0, 0) or np.allclose(((a + 10.0) / 0.5) % 1.0, 1))
        common = np.intersect1d(np.round(a, 9), np.round(b, 9))
        self.assertGreater(len(common), 10)
        self.assertTrue(((a > -3.3) & (a < 7.1)).all())
        off = cl.slice_heights(-3.3, 7.1, 0.5, origin=-10.0, offset=0.5)
        np.testing.assert_allclose(((off + 10.0) / 0.5) % 1.0, 0.5)

    def test_sphere_slices_are_closed_circles(self):
        m = trimesh.creation.icosphere(subdivisions=5, radius=40.0)
        heights = np.array([-30.0, -10.0, 0.5, 20.0, 35.0])
        cs = cl.slice_mesh(m.vertices, m.faces, heights)
        self.assertEqual(len(cs), len(heights))
        for P, closed, hi in cs:
            self.assertTrue(closed)
            r = np.hypot(P[:, 0], P[:, 1])
            np.testing.assert_allclose(P[:, 2], heights[hi], atol=1e-9)
            self.assertAlmostEqual(float(np.median(r)), math.sqrt(40.0 ** 2 - heights[hi] ** 2), delta=0.6)
            np.testing.assert_allclose(P[0], P[-1], atol=1e-9)

    def test_open_mesh_gives_open_chains(self):
        m = trimesh.creation.cylinder(radius=10.0, height=30.0, sections=48)
        keep = np.abs(m.face_normals[:, 2]) < 0.5           # the side only, no caps
        m.update_faces(keep)
        half = m.triangles_center[:, 0] > 0
        m.update_faces(half)
        cs = cl.slice_mesh(m.vertices, m.faces, [0.0, 5.0])
        self.assertEqual(len(cs), 2)
        self.assertFalse(any(c for _, c, _ in cs))

    @unittest.skipUnless(SKILL.exists(), "the plotter-contour-art skill is not installed")
    def test_heights_match_the_skill(self):
        ns = lift(SKILL, ["_slice_heights"])
        theirs = ns["_slice_heights"](-0.93, 0.97, spacing=0.05, origin=-1.0)
        ours = cl.slice_heights(-0.93, 0.97, 0.05, origin=-1.0)
        np.testing.assert_allclose(ours, theirs, atol=1e-12)


class LayoutTests(unittest.TestCase):
    def test_bridge_short_hidden_gaps(self):
        P = lambda *xs: np.array([[x, 0.0] for x in xs])        # noqa: E731
        runs = [(0, True, P(0, 10)), (0, False, P(10, 11)), (0, True, P(11, 20)),
                (0, False, P(20, 30)), (0, True, P(30, 40)), (1, True, P(0, 5))]
        out = cl.bridge(runs, 1.5)
        self.assertEqual([(c, len(s)) for c, s in out], [(0, 4), (0, 2), (1, 2)])

    def test_taper_cuts_long_close_runs_only(self):
        a = np.array([[0.0, 0.0], [50.0, 0.0]])
        b = np.array([[0.0, 0.3], [50.0, 0.3]])          # runs 0.3 mm away for 50 mm: cut
        c = np.array([[24.0, -5.0], [26.0, 5.0]])        # crosses briefly: kept
        out = cl.taper([(0, a), (1, b), (2, c)], gap=0.45)
        ids = [cid for cid, _ in out]
        self.assertIn(0, ids)
        self.assertNotIn(1, ids)
        self.assertIn(2, ids)

    @unittest.skipUnless(SKILL.exists(), "the plotter-contour-art skill is not installed")
    def test_taper_matches_the_skill(self):
        ns = lift(SKILL, ["_cull_dense", "_resample2d"])
        rng = np.random.default_rng(5)
        strokes = []
        for k in range(30):
            x = np.linspace(0, 60, 80)
            y = 10 + k * 0.4 + 2 * np.sin(x / 9 + k * 0.1) + rng.normal(0, 0.02, len(x))
            strokes.append((k, np.column_stack([x, y])))
        theirs = ns["_cull_dense"]([([tuple(p) for p in P], c) for c, P in strokes], 0.45)
        ours = cl.taper(strokes, 0.45)
        lt = sum(cl.length(np.array(pl)) for pl, _ in theirs)
        lo = sum(cl.length(P) for _, P in ours)
        self.assertAlmostEqual(lo / lt, 1.0, delta=0.03)

    def test_chain_joins_same_contour_and_drops_fragments(self):
        s = [(0, np.array([[0.0, 0.0], [10.0, 0.0]])), (0, np.array([[11.0, 0.0], [20.0, 0.0]])),
             (1, np.array([[40.0, 0.0], [41.0, 0.0]]))]
        out, _, _ = cl.chain(s, join_same=1.5, join_any=0.3, simplify=0.0, min_len=2.0)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0][-1][0], 20.0)

    def test_clip_rect_cuts_at_the_border(self):
        P = np.array([[-5.0, 5.0], [5.0, 5.0], [15.0, 5.0], [15.0, 15.0]])
        pieces = cl.clip_rect(P, (0, 0, 10, 10))
        self.assertEqual(len(pieces), 1)
        np.testing.assert_allclose(pieces[0][0], [0, 5])
        np.testing.assert_allclose(pieces[0][-1], [10, 5])


class EngineTests(unittest.TestCase):
    def setUp(self):
        registry()
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas_contours_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def write(self, mesh, name):
        p = self.folder / name
        mesh.export(p)
        return p

    def test_hidden_lines_remove_ink(self):
        ball = trimesh.creation.icosphere(subdivisions=4, radius=20.0)
        plate = trimesh.creation.box(extents=(30, 2, 60))
        plate.apply_translation((0, -30, 0))
        src = self.write(trimesh.util.concatenate([ball, plate]), "scene.stl")
        vals = TOOL.values(dict(theta=90.0, psi=180.0, radius=200.0, fov=40.0, min_len=1.0, min_gap=0.0))
        seen = engine.run(context(), src, None, vals)
        allx = engine.run(context(), src, None, TOOL.values(dict(vals, hidden=False)))
        ink = lambda r: r.preview["views"][0]["drawing"].stats().ink      # noqa: E731
        self.assertLess(ink(seen), 0.7 * ink(allx))

    def test_orthographic_spacing_on_paper(self):
        cyl = trimesh.creation.cylinder(radius=20.0, height=80.0, sections=64)
        src = self.write(cyl, "cyl.stl")
        vals = TOOL.values(dict(theta=90.0, psi=180.0, radius=200.0, projection="orthographic", spacing_mm=3.0,
                                min_len=1.0, min_gap=0.0, join_any=0.0, join_same=0.0))
        res = engine.run(context(), src, None, vals)
        pen = res.preview["views"][0]["drawing"].pens[0]
        ys = sorted({round(float(np.median(pl[:, 1])), 2) for pl in pen.polylines()})
        gaps = np.diff(ys)
        self.assertGreater(len(ys), 10)
        np.testing.assert_allclose(gaps, 3.0, atol=0.02)

    def test_stage_set_shares_scale_and_heights(self):
        folder = self.folder / "m_stages"
        folder.mkdir()
        small = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
        big = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
        big.apply_scale((2.0, 1.0, 1.5))
        a = self.write(small, "m_stages/m_stage_00.stl")
        b = self.write(big, "m_stages/m_stage_01.stl")
        vals = TOOL.values(dict(spacing_mm=2.5))
        ra, rb = engine.run(context(), a, None, vals), engine.run(context(), b, None, vals)
        self.assertEqual(ra.data["frame"], rb.data["frame"])
        self.assertAlmostEqual(ra.data["mm_per_unit"], rb.data["mm_per_unit"])
        ha, hb = np.round(ra.data["heights"], 9), np.round(rb.data["heights"], 9)
        self.assertTrue(set(ha.tolist()) <= set(hb.tolist()))
        alone = engine.run(context(), a, None, TOOL.values(dict(vals, normalise="model")))
        self.assertNotEqual(alone.data["frame"], ra.data["frame"])

    def test_run_writes_a_signed_svg_inside_the_margins(self):
        src = self.write(trimesh.creation.icosphere(subdivisions=3, radius=25.0), "ball.stl")
        vals = TOOL.values(dict(paper="A5", spacing_mm=3.0))
        dst = output_path(TOOL, src, vals)
        res = engine.run(context(), src, dst, vals)
        text = dst.read_text(encoding="utf-8")
        self.assertIn("<desc>contour-lines</desc>", text)
        drawing = res.preview["views"][0]["drawing"]
        x0, y0, x1, y1 = drawing.bounds()
        W, H = drawing.width, drawing.height
        m = vals.margin
        self.assertTrue(x0 >= m - 1e-6 and y0 >= m - 1e-6 and x1 <= W - m + 1e-6 and y1 <= H - m + 1e-6)


if __name__ == "__main__":
    unittest.main()
