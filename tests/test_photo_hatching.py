"""Photo Hatching: direction fields (axial, so they never cancel; strokes that wrap a sphere), the stroke tracer
(spacing, paper white, edges, self-loops, repeatability), its distance raster, outlines traced once, depth contours,
tone layers, superpixels that follow the picture, the SVG, and (when the GPU runtime is installed) the depth models."""
import _isolate  # first: settings and caches stay in a temporary folder
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from scipy.spatial import cKDTree

from atlas_toolkit.geom import depth as gdepth
from atlas_toolkit.tools.dpt_hatching import TOOL
from atlas_toolkit.tools.dpt_hatching import dpt_hatching as dh
from atlas_toolkit.tools.dpt_hatching import field as fld
from atlas_toolkit.tools.dpt_hatching import outlines as ol
from atlas_toolkit.tools.dpt_hatching import styles
from atlas_toolkit.tools.dpt_hatching.streamlines import Dist, trace_layer


def sphere(n: int = 160, r: float = 0.4):
    yy, xx = np.mgrid[0:n, 0:n] / n
    d2 = (xx - 0.5) ** 2 + (yy - 0.5) ** 2
    return (np.sqrt(np.clip(r * r - d2, 0, None)) / r).astype(np.float32)


def axis(f2, y: int, x: int):
    a = 0.5 * math.atan2(f2[y, x, 1], f2[y, x, 0])
    return math.cos(a), math.sin(a)


def flat(shape, angle_deg: float = 0.0):
    return fld.directions("angle", np.zeros(shape, np.float32), np.zeros(shape, np.float32), angle_deg, 1.0)


def trace(f2, sp, **kw):
    args = dict(step=0.5, max_len=1e9, min_len=0.0, turn_deg=35.0)
    args.update(kw)
    return trace_layer(f2, sp, kw.pop("barrier", None), **{k: v for k, v in args.items() if k != "barrier"})


class FieldTests(unittest.TestCase):
    def test_form_strokes_wrap_a_sphere_and_cross_form_runs_over_it(self):
        d = sphere()
        L = np.full(d.shape, 0.5, np.float32)
        n = d.shape[0]
        form = fld.directions("form", d, L, 0.0, 2.0)
        over = fld.directions("cross-form", d, L, 0.0, 2.0)
        for y, x in [(n // 2, int(n * 0.75)), (int(n * 0.3), n // 2), (int(n * 0.35), int(n * 0.35))]:
            rx, ry = x - n / 2, y - n / 2
            rl = math.hypot(rx, ry)
            ux, uy = axis(form, y, x)
            self.assertLess(abs(ux * rx + uy * ry) / rl, 0.15, (y, x))         # along the circle
            ux, uy = axis(over, y, x)
            self.assertGreater(abs(ux * rx + uy * ry) / rl, 0.95, (y, x))      # along the radius

    def test_opposite_gradients_do_not_cancel(self):
        # vertical stripes: the gradient flips sign every half period; plain vector averaging (notebook d) cancels it
        xx = np.mgrid[0:80, 0:120][1]
        L = (0.5 + 0.4 * np.sin(xx / 3.0)).astype(np.float32)
        f2 = fld.directions("image", np.zeros_like(L), L, 45.0, 6.0)
        for y, x in [(40, 30), (40, 61), (20, 90)]:
            ux, uy = axis(f2, y, x)
            self.assertGreater(abs(uy), 0.97)                                   # strokes along the stripes

    def test_rotation_and_fixed_angle(self):
        h = flat((10, 10), 0.0)
        self.assertAlmostEqual(abs(axis(h, 5, 5)[0]), 1.0, places=5)
        v = fld.rotate(h, 90.0)
        self.assertAlmostEqual(abs(axis(v, 5, 5)[1]), 1.0, places=5)
        d45 = flat((10, 10), 45.0)
        ux, uy = axis(d45, 5, 5)
        self.assertAlmostEqual(ux * uy, -0.5, places=4)                         # up-right on paper (y down)


class TracerTests(unittest.TestCase):
    def test_strokes_keep_their_spacing(self):
        f2, sp = flat((100, 140)), np.full((100, 140), 4.0, np.float32)
        lines = trace(f2, sp, rng=np.random.default_rng(1))
        self.assertGreater(len(lines), 15)
        pts = np.array([p for pl in lines for p in pl])
        owner = np.concatenate([[i] * len(pl) for i, pl in enumerate(lines)])
        tree = cKDTree(pts)
        for a, b in tree.query_pairs(4.0 * 0.5 - 0.05):
            self.assertEqual(owner[a], owner[b])                                # no two strokes closer than d/2
        # horizontal strokes about one spacing apart: the y gaps between neighbouring rows are near 4
        ys = np.sort(np.array([np.mean([p[1] for p in pl]) for pl in lines if len(pl) > 100]))
        gaps = np.diff(ys)
        self.assertAlmostEqual(float(np.median(gaps)), 4.0, delta=0.6)

    def test_no_ink_on_paper_white(self):
        f2 = flat((60, 120))
        sp = np.full((60, 120), 3.0, np.float32)
        sp[:, 60:] = np.inf
        lines = trace(f2, sp, rng=np.random.default_rng(1))
        self.assertTrue(lines)
        self.assertLessEqual(max(x for pl in lines for x, _ in pl), 60.0)

    def test_strokes_start_only_where_seedable(self):
        f2, sp = flat((60, 120)), np.full((60, 120), 3.0, np.float32)
        seedable = np.zeros((60, 120), bool)
        seedable[25:35, 10:20] = True
        lines = trace(f2, sp, rng=np.random.default_rng(1), seedable=seedable, order=np.flatnonzero(seedable))
        self.assertTrue(lines)
        for pl in lines:                                           # every stroke passes through the seedable box
            self.assertTrue(any(10 <= x <= 20 and 25 <= y <= 35 for x, y in pl))

    def test_strokes_stop_at_occlusion_edges(self):
        f2, sp = flat((60, 120)), np.full((60, 120), 3.0, np.float32)
        bar = np.zeros((60, 120), bool)
        bar[:, 70] = True
        lines = trace(f2, sp, rng=np.random.default_rng(1), barrier=bar)
        for pl in lines:
            xs = [x for x, _ in pl]
            self.assertFalse(min(xs) < 69.5 and max(xs) > 70.5)

    def test_a_stroke_never_runs_into_itself(self):
        d = sphere(120)
        f2 = fld.directions("form", d, np.full(d.shape, 0.5, np.float32), 0.0, 1.5)
        sp = np.full(d.shape, 3.0, np.float32)
        lines = trace(f2, sp, rng=np.random.default_rng(2), turn_deg=60.0)
        r = 0.5 * 3.0
        for pl in lines:
            P = np.array(pl)
            for i, j in cKDTree(P).query_pairs(r * 0.95):
                self.assertLessEqual(abs(i - j) * 0.5, r + 2 * 0.5 + 1e-6)      # only its own neighbours are close

    def test_the_same_seed_repeats_and_another_differs(self):
        d = sphere(100)
        f2 = fld.directions("form", d, np.full(d.shape, 0.5, np.float32), 0.0, 1.5)
        sp = np.full(d.shape, 3.0, np.float32)
        a = trace(f2, sp, rng=np.random.default_rng(5), hand=0.5)
        b = trace(f2, sp, rng=np.random.default_rng(5), hand=0.5)
        c = trace(f2, sp, rng=np.random.default_rng(6), hand=0.5)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_distance_raster_matches_brute_force(self):
        rng = np.random.default_rng(3)
        reach = np.full((80, 80), 12, np.int32)
        dist = Dist(reach)
        pts = rng.uniform(20, 60, (40, 2))
        dist.stamp([tuple(p) for p in pts])
        tree = cKDTree(pts)
        for q in rng.uniform(15, 65, (300, 2)):
            true = tree.query(q)[0]
            if 1.5 < true < 10:
                # bilinear: a hair over on a single cone (convex), under at the creases between two points; under
                # only makes strokes stop a touch early, and seeds have slack for it
                err = dist.at(*q) - true
                self.assertLess(err, 0.1)
                self.assertGreater(err, -0.5)


class LinesAndLevelsTests(unittest.TestCase):
    def test_outlines_are_traced_once(self):
        e = np.zeros((60, 60), bool)
        e[10, 10:50] = e[50, 10:50] = e[10:51, 10] = e[10:51, 49] = True       # a square ...
        e[10:51, 30] = True                                                    # ... with a bar: two T junctions
        lines = ol.trace(e)
        segs = [tuple(sorted((a, b))) for pl in lines for a, b in zip(pl, pl[1:])]
        self.assertEqual(len(segs), len(set(segs)))                             # no segment drawn twice
        total = sum(math.dist(a, b) for a, b in segs)
        self.assertAlmostEqual(total, 4 * 40 - 1 + 40, delta=6)

    def test_contour_levels_spread_by_area_or_depth(self):
        d = (np.mgrid[0:100, 0:100][1] / 99.0) ** 4                             # mostly far, a little near
        area = styles.contour_levels(d.astype(np.float32), 9, "area", 0.0, 0.0)
        frac = [(d < v).mean() for v in area]
        self.assertTrue(np.allclose(np.diff(frac), 0.1, atol=0.03))             # equal area between levels
        even = styles.contour_levels(d.astype(np.float32), 9, "depth", 0.0, 0.0)
        self.assertTrue(np.allclose(np.diff(even), np.diff(even)[0], atol=1e-3))
        near = styles.contour_levels(d.astype(np.float32), 9, "depth", 1.0, 0.0)
        self.assertGreater(np.diff(near)[0], np.diff(near)[-1])                 # bias > 0: bunched near (high)

    def test_tone_layers_and_spacing(self):
        self.assertEqual(dh.layer_thresholds(0.85, 0.5, 1), [0.85])
        self.assertEqual([round(t, 3) for t in dh.layer_thresholds(0.85, 0.5, 3)], [0.85, 0.5, 0.25])
        t = np.array([0.0, 0.425, 0.849, 0.9])
        sp = dh.spacing_for(t, 0.85, 0.6, 2.4)
        self.assertAlmostEqual(sp[0], 0.6)
        self.assertAlmostEqual(sp[2], 2.4, delta=0.01)
        self.assertTrue(np.isinf(sp[3]))
        self.assertAlmostEqual(1 / sp[1], (1 / 0.6 + 1 / 2.4) / 2, delta=1e-3)    # density linear in tone
        self.assertEqual(dh.layer_angles(55.0)[:3], [0.0, 55.0, -55.0])

    def test_superpixels_follow_the_picture(self):
        # left half dark, right half paper white: patches must not straddle the edge, so no ink on the right
        h, w = 90, 120
        lab = np.zeros((h, w, 3), np.float32)
        lab[:, :w // 2, 0], lab[:, w // 2:, 0] = 20.0, 95.0
        tone = np.where(np.arange(w) < w // 2, 0.2, 0.95)[None, :].repeat(h, 0).astype(np.float32)
        out = styles.patches(lab, np.zeros((h, w), np.float32), tone, flat((h, w), 30.0), n_segments=30,
                             compactness=10.0, depth_weight=1.0, layer_t=[0.85], layer_angles=[0.0],
                             spacing_px=lambda t, tk: 3.0, white=0.85)
        xs = [x for pl in out[0] for x, _ in pl]
        self.assertTrue(xs)
        self.assertLessEqual(max(xs), w // 2 + 1.0)


class OutputTests(unittest.TestCase):
    def test_svg_is_in_mm_signed_and_has_no_background(self):
        from atlas_toolkit.tools.dpt_hatching.engine import make_sample, run
        with tempfile.TemporaryDirectory() as tmp:
            src = make_sample(Path(tmp))
            dst = Path(tmp) / "out.svg"
            ctx = mock.Mock(draft=False, progress=lambda *a: None, log=lambda *a: None, check_cancel=lambda: None)
            p = TOOL.values({"depth_source": "rough", "work_px": 400, "paper": "A5", "orientation": "landscape"})
            res = run(ctx, src, dst, p)
            text = dst.read_text(encoding="utf-8")
        self.assertIn('width="210mm"', text)
        self.assertIn("<desc>dpt-hatching</desc>", text)
        self.assertNotIn("<rect", text)
        self.assertNotIn('fill="black"', text)
        self.assertGreater(next(s.value for s in res.stats if s.key == "strokes"), 50)


@unittest.skipUnless(gdepth.runtime_python(), "depth environment not installed (atlas-toolkit components install depth)")
class DepthModelTests(unittest.TestCase):
    """Runs the real model through the runtime once (a few seconds when the weights are already downloaded)."""

    def test_depth_has_the_photo_shape_near_is_white_and_is_cached(self):
        from atlas_toolkit.tools.dpt_hatching.engine import make_sample
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(gdepth, "cache_dir", lambda: Path(tmp)):
            src = make_sample(Path(tmp))
            logs = []
            d = gdepth.from_model(src, "dpt-beit-large-512", "native", (150, 200), logs.append)
            self.assertEqual(d.shape, (150, 200))
            self.assertAlmostEqual(float(d.min()), 0.0, delta=0.01)
            self.assertAlmostEqual(float(d.max()), 1.0, delta=0.01)
            ys, xs = np.mgrid[0:150, 0:200]
            ball = (xs / 150 - 0.75) ** 2 + (ys / 150 - 0.5) ** 2 < 0.05      # the sample's sphere stands out
            self.assertGreater(d[ball].mean(), d[~ball].mean())
            logs.clear()
            gdepth.from_model(src, "dpt-beit-large-512", "native", (150, 200), logs.append)
            self.assertTrue(any("cache" in m for m in logs))


if __name__ == "__main__":
    unittest.main()
