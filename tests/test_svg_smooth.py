"""Line-art smoothing quality, continuous error, contacts and Atlas integration."""
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
from atlas_toolkit.core.registry import registry, validate
from atlas_toolkit.geom.svg import read_run_metadata, shape_subpaths, apply, mat_mul
from atlas_toolkit.tools.svg_smooth.engine import make_sample, run
from atlas_toolkit.tools.svg_smooth.filters import filter_points, antialias, corner_locks
from atlas_toolkit.tools.svg_smooth.svg_smooth import Options, smooth_tree, physical_matrix


def tree(body, attrs='width="100mm" height="100mm" viewBox="0 0 100 100"'):
    return ET.ElementTree(ET.fromstring(f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}><g fill="none" stroke="black">{body}</g></svg>'))


def poly(p, ident="line"):
    return f'<polyline id="{ident}" points="' + ' '.join(f'{x!r},{y!r}' for x, y in np.asarray(p).tolist()) + '"/>'


def find(t, ident="line"):
    return next(e for e in t.getroot().iter() if e.get("id") == ident)


def points(t, ident="line"):
    el = find(t, ident)
    return np.asarray(shape_subpaths(el, el.tag.rsplit('}', 1)[-1], .00001)[0][0][0])


def jagged(n=801, amplitude=.1):
    x = np.linspace(5, 85, n)
    return np.column_stack([x, 30 + 3 * np.sin(x * .08) + amplitude * (-1.) ** np.arange(n)])


class SmoothTests(unittest.TestCase):
    def test_both_filters_remove_noise_and_preserve_broad_gesture_without_decimation(self):
        p = jagged()
        for method in ("whittaker", "taubin"):
            t = tree(poly(p))
            st = smooth_tree(t, Options(method=method, deviation=.2, span=2, corner_support=2, length_change=100))
            q = points(t)
            self.assertEqual(st.smoothed, 1)
            self.assertGreaterEqual(len(q), len(p))
            np.testing.assert_array_equal(q[[0, -1]], p[[0, -1]])
            inner = (q[:, 0] > 7) & (q[:, 0] < 83)
            residual = q[inner, 1] - (30 + 3 * np.sin(q[inner, 0] * .08))
            self.assertLess(np.sqrt(np.mean(residual ** 2)), .012, method)
            self.assertLess(st.displacement, .2 + 1e-9)
            self.assertGreater(st.length_out, .4 * st.length_in)
            # Independent dense distance probes in both directions.
            a, b = LineString(p), LineString(q)
            for u in np.linspace(0, 1, 601):
                self.assertLessEqual(a.distance(b.interpolate(u, normalized=True)), .2000001)
                self.assertLessEqual(b.distance(a.interpolate(u, normalized=True)), .2000001)

    def test_open_whittaker_matches_dense_constrained_objective(self):
        rng = np.random.default_rng(8)
        for n in (3, 4, 5, 19):
            p = rng.normal(size=(n, 2))
            h, span = .1, .8
            q = filter_points(p, h, span, "whittaker")
            penalty = 1 / (4 * (1 - math.cos(2 * math.pi * h / span)) ** 2)
            d = np.diff(np.eye(n), n=2, axis=0)
            a = np.eye(n) + penalty * d.T @ d
            expected = p.copy()
            expected[1:-1] = np.linalg.solve(a[1:-1, 1:-1], p[1:-1] - a[1:-1][:, [0, -1]] @ p[[0, -1]])
            np.testing.assert_allclose(q, expected, atol=1e-12)

    def test_spectral_taubin_equals_repeated_compensated_passes(self):
        rng = np.random.default_rng(9)
        p = rng.normal(size=(31, 2))
        h, span = .1, .8
        k = 1 - math.cos(2 * math.pi * h / span)
        cycles = max(1, math.ceil(math.log(.5) / math.log1p(-.25 * k ** 2)))
        for periodic in (False, True):
            expected = p.copy()
            for _ in range(cycles):
                for coefficient in (.5, -.5):
                    lap = (np.roll(expected, 1, axis=0) + np.roll(expected, -1, axis=0)) / 2 - expected
                    if not periodic:
                        lap[[0, -1]] = 0
                    expected += coefficient * lap
            np.testing.assert_allclose(filter_points(p, h, span, "taubin", periodic), expected, atol=1e-12)

    def test_wide_span_fine_grid_whittaker_is_stable_and_translation_invariant(self):
        x = np.linspace(0, 100, 20_001)
        p = np.column_stack([x, 2 * x + .1 * np.cos(x * 10)])
        q = filter_points(p, .005, 50, "whittaker")
        self.assertTrue(np.isfinite(q).all())
        self.assertLess(np.max(np.abs(q[:, 0] - x)), 1e-10)
        translated = filter_points(p + [1e6, -1e6], .005, 50, "whittaker")
        np.testing.assert_allclose(translated - [1e6, -1e6], q, atol=1e-8)

    def test_antialias_rejects_dense_sawtooth_aliases(self):
        t = np.linspace(0, 10, 2001)
        p = np.column_stack([t, .1 * (-1.) ** np.arange(len(t))])
        grid = np.linspace(0, 10, 101)
        q = antialias(t, p, grid)
        self.assertLess(np.max(np.abs(q[1:-1, 1])), 1e-10)
        np.testing.assert_allclose(q[:, 0], grid, atol=1e-10)

    def test_zero_strength_displacement_and_all_corner_lock_are_exact_noops(self):
        for overrides in (dict(strength=0), dict(deviation=0), dict(corner_angle=0)):
            t = tree(poly(jagged()) + '<path d="M0 0 C5 9 10 -5 20 0"/>')
            before = ET.tostring(t.getroot())
            st = smooth_tree(t, Options(**overrides))
            self.assertEqual(st.changed, 0)
            self.assertEqual(ET.tostring(t.getroot()), before)

    def test_straight_lines_dots_duplicates_retracing_and_self_crossings_stay_original(self):
        samples = [[(0, 0), (10, 0), (20, 0)], [(2, 2), (2, 2)],
                   [(0, 0), (5, .1), (5, .1), (10, 0)],
                   [(0, 0), (5, 0), (3, 0), (8, 0)],
                   [(0, 0), (5, 5), (0, 5), (5, 0)]]
        for p in samples:
            t = tree(poly(p))
            before = ET.tostring(t.getroot())
            smooth_tree(t, Options(deviation=1, corner_angle=180))
            self.assertEqual(ET.tostring(t.getroot()), before)

    def test_persistent_corners_survive_while_raw_sawteeth_are_not_all_locked(self):
        p = np.array([(0, 0), (1, .05), (2, 0), (3, .05), (4, 0),
                      (4, 1), (4.05, 2), (4, 3), (4.05, 4)])
        locks = corner_locks(p, False, 1.5, 65)
        self.assertTrue(locks[4])
        self.assertLess(np.count_nonzero(locks), 5)
        t = tree(poly(p))
        smooth_tree(t, Options(span=1, deviation=.1, corner_support=1.5))
        q = points(t)
        self.assertTrue(np.any(np.all(q == p[4], axis=1)))

    def test_displacement_cap_and_strength_are_independent_and_monotone(self):
        p = jagged(amplitude=.3)
        values = []
        for strength in (10, 50, 100):
            t = tree(poly(p))
            st = smooth_tree(t, Options(span=2, strength=strength, deviation=.4, length_change=100))
            values.append(st.displacement)
        self.assertTrue(values[0] < values[1] < values[2])
        t = tree(poly(p))
        st = smooth_tree(t, Options(span=2, deviation=.025, length_change=100))
        self.assertLessEqual(st.displacement, .025000001)
        self.assertGreater(st.limited, 0)

    def test_periodic_loops_have_no_fixed_seam_and_preserve_area_winding(self):
        a = np.linspace(0, 2 * np.pi, 361)
        r = 10 + .12 * np.cos(60 * a)
        p = np.column_stack([35 + r * np.cos(a), 35 + r * np.sin(a)])
        p[-1] = p[0]
        for method in ("whittaker", "taubin"):
            t = tree('<polygon id="line" points="' + ' '.join(f'{x},{y}' for x, y in p[:-1]) + '"/>')
            st = smooth_tree(t, Options(method=method, span=2, deviation=.2, corner_angle=180))
            q = points(t)
            self.assertEqual(st.smoothed, 1)
            self.assertFalse(np.array_equal(q[0], p[0]))
            self.assertTrue(LineString(q).is_simple)
            self.assertLess(abs(Polygon(q).area / Polygon(p).area - 1), .03)
            self.assertEqual(Polygon(q).exterior.is_ccw, Polygon(p).exterior.is_ccw)

    def test_area_and_length_limits_can_retain_original_geometry(self):
        p = jagged()
        t = tree(poly(p))
        smooth_tree(t, Options(length_change=0))
        np.testing.assert_array_equal(points(t), p)
        t = tree('<polygon id="line" points="0,0 10,0 10,10 0,10"/>')
        original = ET.tostring(t.getroot())
        smooth_tree(t, Options(span=10, deviation=5, corner_angle=180, area_change=0))
        self.assertEqual(ET.tostring(t.getroot()), original)

    def test_crossing_and_t_junction_segments_are_anchored_across_pens(self):
        p = jagged(n=81)
        t = tree(poly(p) + '<path id="cross" stroke="red" d="M45 0 L45 80"/>')
        # Crossing lies at a source vertex. Its two incident source segments stay original.
        original = LineString(p).intersection(LineString([(45, 0), (45, 80)]))
        st = smooth_tree(t, Options(span=4, deviation=.3, corner_support=4))
        final = LineString(points(t)).intersection(LineString(points(t, "cross")))
        self.assertTrue(final.equals_exact(original, 1e-10))
        self.assertGreater(st.smoothed, 0)

    def test_swept_regions_protect_tiny_enclosed_motif(self):
        # Smoothing the spike can sweep over a motif without intersecting it at the end.
        p = [(0, 0), (4, 0), (5, 1), (6, 0), (10, 0)]
        body = poly(p) + '<circle id="tiny" cx="5" cy="0.3" r="0.05" stroke="red"/>'
        guarded, free = tree(body), tree(body)
        st = smooth_tree(guarded, Options(span=10, deviation=2, corner_angle=180, length_change=100))
        smooth_tree(free, Options(span=10, deviation=2, corner_angle=180, length_change=100, topology=False))
        q, r = points(guarded), points(free)
        self.assertGreater(np.max(q[:, 1]), np.max(r[:, 1]) + .1)
        self.assertGreater(st.limited, 0)
        self.assertFalse(LineString(q).intersects(Point(5, .3).buffer(.05)))
        # The motif stays on the same side/enclosed side of the entire stroke.
        self.assertTrue(Polygon(np.vstack([q, [[10, -2], [0, -2]]])).covers(Point(5, .3)))

    def test_close_parallel_strokes_remain_disjoint(self):
        p = jagged(n=401, amplitude=.08)
        t = tree(poly(p, "a") + poly(p + [0, .22], "b"))
        smooth_tree(t, Options(span=2, deviation=.3, corner_support=2, length_change=100))
        self.assertFalse(LineString(points(t, "a")).intersects(LineString(points(t, "b"))))

    def test_swept_regions_also_protect_zero_length_pen_dots(self):
        p = [(0, 0), (4, 0), (5, 1), (6, 0), (10, 0)]
        t = tree(poly(p) + '<path id="dot" d="M5 .3 L5 .3" stroke="red"/>')
        smooth_tree(t, Options(span=10, deviation=2, corner_angle=180, length_change=100))
        q = points(t)
        self.assertTrue(Polygon(np.vstack([q, [[10, -2], [0, -2]]])).covers(Point(5, .3)))
        np.testing.assert_array_equal(points(t, "dot"), [[5, .3], [5, .3]])

    def test_previously_accepted_geometry_also_constrains_later_motion(self):
        from atlas_toolkit.tools.svg_smooth.safety import Guard
        from atlas_toolkit.tools.svg_smooth.svg_smooth import Stroke
        a = np.array([(0., 0.), (5., 0.), (10., 0.)])
        b = a + [0., 2.]
        strokes = [Stroke(p, p, False, np.zeros(len(p), dtype=bool)) for p in (a, b)]
        guard = Guard(strokes, lambda: None)
        qa, qb = a.copy(), b.copy()
        qa[1, 1], qb[1, 1] = 1.2, .8
        self.assertTrue(guard.safe(0, a, qa, lambda: None))
        self.assertTrue(guard.safe(1, b, qb, lambda: None))
        guard.add(qa)
        self.assertFalse(guard.safe(1, b, qb, lambda: None))

    def test_curve_policy_and_optional_curve_displacement(self):
        body = '<path id="line" d="M5 20 C15 30 25 10 35 20 S55 10 65 20"/>'
        t = tree(body)
        before = ET.tostring(t.getroot())
        smooth_tree(t, Options())
        self.assertEqual(ET.tostring(t.getroot()), before)
        t = tree(body)
        original = LineString(points(t))
        st = smooth_tree(t, Options(curves="smooth", span=10, deviation=.2))
        self.assertGreater(st.smoothed, 0)
        result = LineString(points(t))
        for u in np.linspace(0, 1, 1001):
            self.assertLess(original.distance(result.interpolate(u, normalized=True)), .201)

    def test_layers_attributes_and_untouched_special_geometry_survive(self):
        special = ('<path id="dashed" stroke-dasharray="1 2" d="M0 0 L5 1 L10 0"/>'
                   '<polyline id="marked" style="marker-mid:url(#arrow)" points="0,2 5,3 10,2"/>'
                   '<path id="filled" fill="red" d="M0 3 L5 4 L10 3 Z"/>'
                   '<circle id="circle" cx="20" cy="20" r="2"/>')
        t = tree(poly(jagged()) + special)
        before = {k: ET.tostring(find(t, k)) for k in ("dashed", "marked", "filled", "circle")}
        find(t).set("data-custom", "keep me")
        smooth_tree(t, Options())
        for k, v in before.items():
            self.assertEqual(ET.tostring(find(t, k)), v)
        self.assertEqual(find(t).get("data-custom"), "keep me")
        self.assertEqual(list(t.getroot())[0].get("fill"), "none")

    def test_unsupported_references_and_css_are_explicit_noops(self):
        for body in ('<use href="#a"/>', '<style>path {stroke: red}</style>', '<text>Hello</text>', '<svg/>'):
            t = tree(poly(jagged()) + body)
            before = ET.tostring(t.getroot())
            st = smooth_tree(t, Options())
            self.assertEqual(ET.tostring(t.getroot()), before)
            self.assertIn("expand", " ".join(st.notes))

    def test_anisotropic_units_nested_transform_and_reflection_obey_physical_cap(self):
        from atlas_toolkit.geom.svg import collect
        p = jagged(n=201, amplitude=.4)
        attrs = 'width="200mm" height="50mm" viewBox="0 0 100 100" preserveAspectRatio="none" transform="rotate(3)"'
        t = tree('<g transform="translate(80 0) scale(-.8 1.2)">' + poly(p) + '</g>', attrs)
        matrix = mat_mul(physical_matrix(t.getroot()), collect(t.getroot())[0][0].ctm)
        before = LineString([apply(matrix, point) for point in p])
        smooth_tree(t, Options(span=2, deviation=.05, corner_angle=180, length_change=100))
        after = LineString([apply(matrix, point) for point in points(t)])
        for u in np.linspace(0, 1, 601):
            self.assertLessEqual(before.distance(after.interpolate(u, normalized=True)), .0500001)
        np.testing.assert_array_equal(points(t)[[0, -1]], p[[0, -1]])
        self.assertEqual(list(t.getroot())[0][0].get("transform"), 'translate(80 0) scale(-.8 1.2)')

    def test_bad_parameters_collapsed_transform_resource_cap_and_cancel(self):
        for options in (Options(span=0), Options(deviation=float('nan')), Options(method="rdp"),
                        Options(curves="smooth", deviation=.001, curve_error=.01)):
            with self.assertRaises(ValueError):
                smooth_tree(tree(poly(jagged())), options)
        t = tree('<g transform="scale(0)">' + poly(jagged()) + '</g>')
        with self.assertRaisesRegex(ValueError, "transform"):
            smooth_tree(t, Options())
        o = Options(sample_step=.000001)
        o.point_limit = 10
        from atlas_toolkit.tools.svg_smooth.filters import proposal
        with self.assertRaisesRegex(ValueError, "budget"):
            proposal(jagged(), False, corner_locks(jagged(), False, 1, 65), o, lambda: None)
        def cancel(*args):
            raise Cancelled()
        with self.assertRaises(Cancelled):
            smooth_tree(tree(poly(jagged())), Options(), cancel)

    def test_contract_preview_run_presets_metadata_cli_and_workflow(self):
        from atlas_toolkit.core.cli import build_parser
        from atlas_toolkit.core import workflow as w
        tool = registry().get("svg-smooth")
        self.assertEqual(validate(tool), [])
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            src = make_sample(folder)
            ctx = JobContext(lambda *a: None, lambda: False, "test", folder)
            for i, p in enumerate([tool.values()] + [_presets.run_values(tool, x) for x in _presets.optional(tool)]):
                dst = folder / f"settings-{i}.svg"
                preview = run(ctx, src, None, p)
                final = run(ctx, src, dst, p)
                self.assertEqual(preview.stats, final.stats)
                self.assertEqual(preview.changed, final.changed)
                self.assertTrue(final.changed)
                self.assertEqual(final.preview["views"][0]["drawing"].stats().strokes, 5)
                self.assertEqual(final.preview["views"][1]["drawing"].stats().strokes, 5)
                self.assertIn(b'<desc>svg-smooth</desc>', dst.read_bytes())
                self.assertEqual(read_run_metadata(dst), (tool.id, dict(p)))
            ns = build_parser().parse_args(["run", "svg-smooth", str(src), "--method", "taubin", "--span", "2"])
            self.assertEqual(ns.method, "taubin")
            wf = w.Workflow(name="smooth")
            a = wf.add(w.input_node("svg"))
            b = wf.add(w.tool_node(tool))
            c = wf.add(w.save_node(where="folder", folder=str(folder / "out")))
            wf.connect(a.id, b.id)
            wf.connect(b.id, c.id)
            self.assertEqual(w.errors(w.check(wf, registry())), [])
            result = w.run_workflow(wf, src, lambda *a: None, lambda: False, reg=registry(),
                                    cache=w.WorkflowCache(folder / "cache"), tool_cache=str(folder / "toolcache"))
            self.assertTrue(result.outputs)
            self.assertEqual(read_run_metadata(Path(result.outputs[0]))[0], tool.id)


if __name__ == "__main__":
    unittest.main()
