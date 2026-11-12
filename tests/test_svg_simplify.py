"""Independent geometric regressions: error, crossings, enclosed motifs, gesture and SVG semantics."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import math
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point, Polygon

from atlas_toolkit.core.job import JobContext, Cancelled
from atlas_toolkit.geom.svg import read_run_metadata, shape_subpaths
from atlas_toolkit.tools.svg_simplify import TOOL
from atlas_toolkit.tools.svg_simplify.engine import make_sample, run
from atlas_toolkit.tools.svg_simplify.svg_simplify import Options, simplify_tree, physical_matrix
from atlas_toolkit.tools.svg_simplify.curves import cubic, quad, arc


def tree(body, attrs='width="100mm" height="100mm" viewBox="0 0 100 100"'):
    return ET.ElementTree(ET.fromstring(f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}><g fill="none" stroke="black">{body}</g></svg>'))


def points(el):
    return shape_subpaths(el, el.tag.rsplit('}', 1)[-1], 0.0001)[0][0][0]


def poly(coords, ident="line"):
    return f'<polyline id="{ident}" points="' + ' '.join(f'{x},{y}' for x, y in coords) + '"/>'


def find(t, ident="line"):
    return next(e for e in t.getroot().iter() if e.get("id") == ident)


class SimplifyTests(unittest.TestCase):
    def test_both_methods_reduce_and_bound_continuous_line_error(self):
        p = [(x, 5 + math.sin(x * .12)) for x in np.linspace(0, 80, 3000)]
        for method in ("rdp", "vw"):
            with self.subTest(method=method):
                t = tree(poly(p))
                st = simplify_tree(t, Options(method=method, tolerance=.06))
                q = points(find(t))
                self.assertLess(len(q), len(p) // 4)
                self.assertEqual(q[0], p[0])
                self.assertEqual(q[-1], p[-1])
                # Densify the ORIGINAL line independently; error applies between vertices too.
                original, result = LineString(p), LineString(q)
                self.assertLessEqual(max(result.distance(original.interpolate(k / 3000, normalized=True))
                                         for k in range(3001)), .06000001)
                self.assertLessEqual(st.deviation, .06000001)
                self.assertGreaterEqual(st.length_out, .98 * st.length_in - 1e-9)

    def test_corner_lock_and_zero_angle(self):
        p = [(0, 0), (1, 0), (2, 0), (2, 1), (3, 1)]
        for method in ("rdp", "vw"):
            t = tree(poly(p))
            simplify_tree(t, Options(method=method, tolerance=5, length_loss=100))
            self.assertIn((2., 0.), points(find(t)))
            self.assertIn((2., 1.), points(find(t)))
            t = tree(poly(p))
            simplify_tree(t, Options(method=method, tolerance=5, corner_angle=0))
            self.assertEqual(points(find(t)), p)

    def test_zero_is_exact_geometry_noop_including_curves_and_duplicate_dots(self):
        t = tree('<path id="line" d="M0 0 C5 9 10 -5 20 0"/>' + poly([(1, 2), (1, 2), (1, 2)], "dots"))
        before = ET.tostring(t.getroot())
        st = simplify_tree(t, Options(tolerance=0, curves="flatten"))
        self.assertEqual(ET.tostring(t.getroot()), before)
        self.assertEqual(st.changed, 0)

    def test_retracing_and_hooks_are_not_erased(self):
        p = [(0, 0), (5, .01), (3, .02), (8, 0)]
        t = tree(poly(p))
        simplify_tree(t, Options(tolerance=5, corner_angle=180, length_loss=100, topology=False))
        self.assertEqual(points(find(t)), p)
        t = tree(poly(p))
        simplify_tree(t, Options(tolerance=5, corner_angle=180, length_loss=100, topology=False, reversals=False))
        self.assertEqual(len(points(find(t))), 2)

    def test_length_guard_preserves_shallow_waviness(self):
        p = [(x, .3 * (x % 2)) for x in range(100)]
        t = tree(poly(p))
        st = simplify_tree(t, Options(tolerance=1, corner_angle=180, length_loss=.5, topology=False))
        self.assertGreaterEqual(st.length_out, st.length_in * .995 - 1e-8)

    def test_new_crossings_blocked_across_pens(self):
        p = [(0, 0), (5, 2), (10, 0)]
        body = poly(p) + '<path stroke="red" d="M5 -1 L5 1"/>'
        for method in ("rdp", "vw"):
            t = tree(body)
            simplify_tree(t, Options(method=method, tolerance=5, area_factor=100, corner_angle=180, length_loss=100))
            self.assertFalse(LineString(points(find(t))).intersects(LineString([(5, -1), (5, 1)])))
            t = tree(body)
            simplify_tree(t, Options(method=method, tolerance=5, area_factor=100, corner_angle=180,
                                     length_loss=100, topology=False))
            self.assertTrue(LineString(points(find(t))).intersects(LineString([(5, -1), (5, 1)])))

    def test_existing_crossing_and_t_junction_span_stays_at_original_coordinates(self):
        p = [(0, 0), (2, 1), (5, 2), (8, 1), (10, 0)]
        for stroke in ('M5 -1 L5 3', 'M5 2 L5 4'):
            t = tree(poly(p) + f'<path d="{stroke}"/>')
            simplify_tree(t, Options(tolerance=5, corner_angle=180, length_loss=100))
            q = LineString(points(find(t)))
            self.assertEqual(q.intersection(LineString([(5, -1), (5, 4)])), Point(5, 2))

    def test_swept_region_does_not_swallow_island_even_without_chord_intersection(self):
        p = [(0, 0), (5, 5), (10, 0)]
        t = tree(poly(p) + '<circle cx="5" cy="1" r=".1"/>')
        simplify_tree(t, Options(tolerance=10, corner_angle=180, length_loss=100))
        self.assertEqual(points(find(t)), p)

    def test_swept_region_protects_zero_length_stipple_motifs(self):
        p = [(0,0),(5,5),(10,0)]
        for dot in ('<path d="M5 1 L5 1"/>', '<line x1="5" y1="1" x2="5" y2="1"/>'):
            t = tree(poly(p) + '<g stroke-linecap="round">' + dot + '</g>')
            simplify_tree(t, Options(tolerance=10,corner_angle=180,length_loss=100))
            self.assertEqual(points(find(t)), p)

    def test_self_intersections_remain_unchanged(self):
        p = [(0, 0), (4, 4), (0, 4), (4, 0), (8, 0)]
        t = tree(poly(p))
        simplify_tree(t, Options(tolerance=10, corner_angle=180, length_loss=100, reversals=False))
        q = LineString(points(find(t)))
        self.assertFalse(q.is_simple)
        self.assertEqual(q.intersection(LineString([(2, 1), (2, 3)])), Point(2, 2))

    def test_closed_loops_keep_winding_area_and_small_nested_motif(self):
        angles = np.linspace(0, 2 * math.pi, 1001)
        p = [(30 + 10 * math.cos(a), 30 + 10 * math.sin(a)) for a in angles]
        body = '<polygon id="line" points="' + ' '.join(f'{x},{y}' for x,y in p[:-1]) + '"/><circle cx="30" cy="30" r="1"/>'
        for method in ("rdp", "vw"):
            t = tree(body)
            simplify_tree(t, Options(method=method, tolerance=2, area_factor=100, area_loss=1))
            q = points(find(t))
            self.assertGreaterEqual(len(set(q)), 3)
            self.assertLess(abs(Polygon(p).area - Polygon(q).area), Polygon(p).area * .0100001)
            self.assertTrue(Polygon(q).contains(Point(30, 30)))

    def test_layers_style_order_and_nonuniform_transforms_are_preserved(self):
        t = tree('<g id="pen" transform="scale(100 .01)"><polyline id="line" class="ink" '
                 'style="stroke:blue;stroke-width:0.2" points="0,0 .01,.5 .02,0"/></g>')
        simplify_tree(t, Options(tolerance=.01))
        self.assertEqual(len(points(find(t))), 2)  # 0.005 mm deviation, not determinant-based 0.5.
        self.assertEqual(find(t).get('style'), 'stroke:blue;stroke-width:0.2')
        self.assertEqual(find(t, 'pen').get('transform'), 'scale(100 .01)')
        self.assertTrue(find(t).tag.endswith('polyline'))

    def test_viewport_none_and_meet_use_correct_physical_metric(self):
        t = tree('', 'width="200mm" height="100mm" viewBox="0 0 100 100" preserveAspectRatio="none"')
        self.assertEqual(physical_matrix(t.getroot())[:4], (2., 0., 0., 1.))
        t.getroot().set('preserveAspectRatio', 'xMidYMid meet')
        self.assertEqual(physical_matrix(t.getroot())[:4], (1., 0., 0., 1.))
        t.getroot().set('preserveAspectRatio', 'none')
        t.getroot().set('transform', 'matrix(0 1 -1 0 0 0)')
        self.assertEqual(physical_matrix(t.getroot())[:4], (0., 2., -1., 0.))

    def test_coordinates_do_not_round_or_disconnect_junctions(self):
        p = [(0.123456789012345, 0), (1., 0), (2.123456789012345, 0)]
        t = tree(poly(p) + '<path d="M2.123456789012345 0 L3 1"/>')
        simplify_tree(t, Options())
        self.assertEqual(points(find(t))[-1], p[-1])
        self.assertEqual(len(points(find(t))), 2)

    def test_geometrically_closed_polyline_retains_area_and_duplicate_dots_survive(self):
        p = [(0,0), (2,0), (4,0), (4,4), (2,4), (0,4), (0,0)]
        t = tree(poly(p) + poly([(8,8),(8,8),(8,8)], 'dot'))
        simplify_tree(t, Options(tolerance=10, corner_angle=180, area_loss=0, length_loss=100))
        self.assertEqual(Polygon(points(find(t))).area, 16.)
        self.assertEqual(points(find(t,'dot')), [(8.,8.),(8.,8.),(8.,8.)])

    def test_curves_kept_by_default_and_optional_conversion_is_bounded(self):
        body = '<path id="line" d="M0 0 C20 40 40 -40 60 0"/>'
        t = tree(body)
        original = find(t).get('d')
        simplify_tree(t, Options())
        self.assertEqual(find(t).get('d'), original)
        t = tree(body)
        simplify_tree(t, Options(tolerance=.2, curves='flatten', topology=False))
        self.assertNotIn('C', find(t).get('d'))
        q = LineString(points(find(t)))
        for tval in np.linspace(0, 1, 4001):
            u = 1-tval
            p = Point(3*u*u*tval*20+3*u*tval*tval*40+tval**3*60,
                      3*u*u*tval*40-3*u*tval*tval*40)
            self.assertLessEqual(q.distance(p), .200001)

    def test_adaptive_flatteners_bound_quadratic_and_arc(self):
        q = LineString([(0,0)] + quad((0,0),(10,20),(20,0),.01))
        for t in np.linspace(0,1,1001):
            self.assertLessEqual(q.distance(Point(20*t,40*t*(1-t))), .010001)
        q = LineString([(10,0)] + arc((10,0),10,5,0,False,True,(-10,0),.01))
        for a in np.linspace(0,math.pi,1001):
            self.assertLessEqual(q.distance(Point(10*math.cos(a),5*math.sin(a))), .010001)
        # A long almost-flat Bézier with overshoot must not collapse to its endpoint chord.
        self.assertGreater(len(cubic((0,0),(100,0),(-100,0),(1,0),.01)), 2)

    def test_referenced_or_styled_documents_are_conservative_noops(self):
        for extra in ('<use href="#line" x="5"/>', '<style>polyline {transform:scale(3)}</style>', '<text>motif</text>', '<svg/>'):
            t = tree(poly([(0,0),(1,0),(2,0)]) + extra)
            before = ET.tostring(t.getroot())
            st = simplify_tree(t, Options())
            self.assertEqual(ET.tostring(t.getroot()), before)
            self.assertTrue(st.notes)

    def test_ordinary_pen_stylesheets_are_preserved_and_simplified(self):
        t = tree('<style>polyline {fill:none;stroke:red;stroke-width:.2}</style>' + poly([(0,0),(1,0),(2,0)]))
        simplify_tree(t, Options())
        self.assertEqual(len(points(find(t))), 2)
        self.assertEqual(next(e for e in t.getroot().iter() if e.tag.endswith('style')).text,
                         'polyline {fill:none;stroke:red;stroke-width:.2}')

    def test_dashed_and_marker_semantics_remain_original(self):
        for attr in ('stroke-dasharray="2 3"', 'marker-mid="url(#arrow)"', 'pathLength="2"'):
            t = tree(f'<path id="line" {attr} d="M0 0 L1 0 L2 0"/>')
            simplify_tree(t, Options())
            self.assertEqual(find(t).get('d'), 'M0 0 L1 0 L2 0')

    def test_inline_css_geometry_is_kept_unchanged(self):
        t = tree('<polyline style="transform:scale(8)" points="0,0 1,0 2,0"/>')
        before = ET.tostring(t.getroot())
        self.assertTrue(simplify_tree(t, Options()).notes)
        self.assertEqual(ET.tostring(t.getroot()), before)

    def test_random_inputs_have_no_new_intersections_and_error_stays_bounded(self):
        rng = np.random.default_rng(137)
        for method in ('rdp', 'vw'):
            for k in range(8):
                p = np.c_[np.linspace(0,20,180), rng.normal(0,.07,180)]
                t = tree(poly(p))
                simplify_tree(t, Options(method=method, tolerance=.15, corner_angle=180, length_loss=5))
                result = LineString(points(find(t)))
                self.assertTrue(result.is_simple)
                self.assertLessEqual(max(result.distance(Point(v)) for v in p), .1500001)

    def test_cancellation_propagates_without_output(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            src, dst = make_sample(folder), folder / 'out.svg'
            ctx = JobContext(lambda *a: None, lambda: True)
            with self.assertRaises(Cancelled):
                run(ctx, src, dst, TOOL.values())
            self.assertFalse(dst.exists())

    def test_engine_draft_matches_run_signature_metadata_and_presets(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            src, dst = make_sample(folder), folder / 'out.svg'
            original = src.read_bytes()
            for values in [TOOL.values()] + [_presets.run_values(TOOL, p) for p in _presets.optional(TOOL)]:
                preview = run(JobContext(lambda *a: None, lambda: False, mode='draft'), src, None, values)
                result = run(JobContext(lambda *a: None, lambda: False), src, dst, values)
                self.assertEqual([(s.key,s.value) for s in preview.stats], [(s.key,s.value) for s in result.stats])
                self.assertEqual(read_run_metadata(dst), (TOOL.id, dict(values)))
                self.assertIn('<desc>svg-simplify</desc>', dst.read_text(encoding='utf-8'))
                self.assertEqual(src.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
