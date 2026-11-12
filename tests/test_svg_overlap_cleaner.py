"""Tests for svg_overlap_cleaner (ported unchanged from code/Python/svg_overlap_cleaner; only the import differs)."""
import _isolate  # first: settings and caches stay in a temporary folder
import re
import unittest
import xml.etree.ElementTree as ET

from atlas_toolkit.tools.svg_overlap_cleaner import svg_overlap_cleaner as so

HEAD = '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100">'


def svg(*body: str) -> str:
    return HEAD + "".join(body) + "</svg>"


def paths(text: str) -> dict:
    root = ET.fromstring(text.split("?>", 1)[-1])
    return {el.get("id"): el.get("d") for el in root.iter(f"{{{so.SVG_NS}}}path")}


def strokes(d: str) -> list:
    out = []
    for part in d.split("M")[1:]:
        v = [float(x) for x in so._NUM.findall(part)]
        out.append(list(zip(v[0::2], v[1::2])))
    return out


class OverlapTests(unittest.TestCase):
    def test_duplicate_path_is_removed(self):
        out, st = so.clean_string(svg('<path id="a" d="M 10 10 V 90"/>', '<path id="b" d="M 10 10 V 90"/>'))
        self.assertEqual(set(paths(out)), {"a"})
        self.assertEqual(st.removed_elements, 1)
        self.assertAlmostEqual(st.retraced, 80, places=3)

    def test_baseline_run_is_cut_and_end_stays_on_the_line(self):
        # b runs down the shared line x=10 (with sub-tolerance jitter), leaves it at y=50, comes back at y=60
        out, _ = so.clean_string(svg('<path id="a" d="M 10 10 V 90"/>',
                                     '<path id="b" d="M 10 10 L 10.003 30 L 10 50 L 40 50.5 L 40 59.5 L 10 60 V 90"/>'))
        pieces = strokes(paths(out)["b"])
        self.assertEqual(len(pieces), 1)
        self.assertEqual(pieces[0][0], (10.0, 50.0))     # leaves exactly from the line ...
        self.assertEqual(pieces[0][-1], (10.0, 60.0))    # ... and lands exactly on it

    def test_crossings_are_kept(self):
        out, st = so.clean_string(svg('<path id="a" d="M 0 50 H 100"/>', '<path id="b" d="M 50 0 V 100"/>',
                                      '<path id="c" d="M 0 40 L 100 60"/>'))   # 11 degrees: still a crossing
        self.assertEqual(st.retraced, 0)
        self.assertEqual(st.changed, 0)
        self.assertEqual(paths(out)["c"], "M 0 40 L 100 60")

    def test_short_overlaps_are_left_alone(self):
        out, st = so.clean_string(svg('<path id="a" d="M 0 50 H 100"/>', '<path id="b" d="M 20 10 L 20 50 H 21 V 90"/>'))
        self.assertEqual(st.retraced, 0)                 # 1 mm on top of a: below --min-overlap

    def test_near_parallel_within_tolerance_is_cut(self):
        out, st = so.clean_string(svg('<path id="a" d="M 0 50 H 100"/>', '<path id="b" d="M 0 50.04 H 60 L 60 80"/>'))
        self.assertEqual(strokes(paths(out)["b"]), [[(60.0, 50.04), (60.0, 80.0)]])

    def test_tiny_stroke_touching_a_line_is_kept(self):
        # 0.3 mm stroke, its first 0.02 mm on top of a: not retraced, must survive
        out, st = so.clean_string(svg('<path id="a" d="M 0 50 H 10"/>', '<path id="b" d="M 9.98 50 H 10.28"/>'))
        self.assertEqual(set(paths(out)), {"a", "b"})
        self.assertEqual(st.retraced, 0)

    def test_line_running_past_the_end_of_another_keeps_its_end(self):
        # b retraces a for 10 mm, then goes 0.25 mm further: the tip stays (only bits between two cuts are dropped)
        out, _ = so.clean_string(svg('<path id="a" d="M 0 50 H 10"/>', '<path id="b" d="M 0 50 H 10.25"/>'))
        self.assertEqual(strokes(paths(out)["b"]), [[(10.0, 50.0), (10.25, 50.0)]])

    def test_far_parallel_is_kept(self):
        _, st = so.clean_string(svg('<path id="a" d="M 0 50 H 100"/>', '<path id="b" d="M 0 50.2 H 100"/>'))
        self.assertEqual(st.retraced, 0)

    def test_stroke_doubling_back_on_itself(self):
        out, st = so.clean_string(svg('<path id="a" d="M 0 0 H 50 H 10"/>'))
        self.assertEqual(strokes(paths(out)["a"]), [[(0.0, 0.0), (50.0, 0.0)]])
        self.assertAlmostEqual(st.retraced, 40, places=3)

    def test_transforms_are_respected_and_kept(self):
        out, _ = so.clean_string(svg('<g id="layer"><path id="a" d="M 20 10 V 90"/>',
                                     '<g transform="translate(10 0)"><path id="b" d="M 10 10 V 50 H 60"/></g></g>'))
        self.assertIn('transform="translate(10 0)"', out)
        self.assertEqual(strokes(paths(out)["b"]), [[(10.0, 50.0), (60.0, 50.0)]])   # local coordinates

    def test_layers_are_separate_pens_unless_asked(self):
        doc = svg('<g id="L1"><path id="a" d="M 0 50 H 100"/></g>', '<g id="L2"><path id="b" d="M 0 50 H 100"/></g>')
        self.assertEqual(so.clean_string(doc)[1].retraced, 0)
        self.assertEqual(set(paths(so.clean_string(doc, so.Options(across_pens=True))[0])), {"a"})

    def test_stroke_colours_are_separate_pens(self):
        doc = svg('<path id="a" stroke="red" d="M 0 50 H 100"/>', '<path id="b" style="stroke:blue" d="M 0 50 H 100"/>')
        self.assertEqual(so.clean_string(doc)[1].retraced, 0)

    def test_lines_and_polylines_become_paths_only_when_cut(self):
        out, _ = so.clean_string(svg('<line id="a" x1="0" y1="50" x2="100" y2="50"/>',
                                     '<polyline id="b" points="0,50 50,50 50,90"/>',
                                     '<polyline id="c" points="0,10 50,10"/>'))
        self.assertEqual(strokes(paths(out)["b"]), [[(50.0, 50.0), (50.0, 90.0)]])
        self.assertIn('<line id="a"', out)
        self.assertIn('<polyline id="c"', out)

    def test_untouched_curves_stay_curves(self):
        d = "M 10 10 C 20 0 40 0 50 10 A 10 10 0 0 1 70 10"
        out, st = so.clean_string(svg(f'<path id="a" d="{d}"/>'))
        self.assertEqual(paths(out)["a"], d)
        self.assertEqual(st.changed, 0)

    def test_curve_retracing_a_curve_is_removed(self):
        out, _ = so.clean_string(svg('<path id="a" d="M 10 10 Q 50 60 90 10"/>', '<path id="b" d="M 10 10 Q 50 60 90 10"/>'))
        self.assertEqual(set(paths(out)), {"a"})

    def test_closed_loop_split_keeps_one_piece(self):
        # the square's left side lies on a; the rest of the loop stays one stroke across the loop's start
        out, _ = so.clean_string(svg('<path id="a" d="M 10 0 V 100"/>', '<path id="b" d="M 10 50 V 20 H 40 V 80 H 10 Z"/>'))
        self.assertEqual(strokes(paths(out)["b"]), [[(10.0, 20.0), (40.0, 20.0), (40.0, 80.0), (10.0, 80.0)]])

    def test_signature_and_plotdata(self):
        out, st = so.clean_string(svg('<path id="a" d="M 0 0 H 10"/>', '<plotdata application="axidraw" node="250"/>'))
        self.assertTrue(st.plotdata_removed)
        self.assertNotIn("plotdata", out)
        again, _ = so.clean_string(out)
        self.assertEqual(len(re.findall(r"<desc>svg-overlap-cleaner</desc>", again)), 1)

    def test_px_documents_use_mm_tolerances(self):
        # 96 dpi: 0.05 mm = 0.19 px, so lines 0.15 px apart overlap and 0.3 px apart do not
        head = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400">'
        near = so.clean_string(head + '<path d="M 0 50 H 300"/><path d="M 0 50.15 H 300"/></svg>')[1]
        far = so.clean_string(head + '<path d="M 0 50 H 300"/><path d="M 0 50.3 H 300"/></svg>')[1]
        self.assertGreater(near.retraced, 0)
        self.assertEqual(far.retraced, 0)


class SimplifyTests(unittest.TestCase):
    def test_collinear_and_duplicate_vertices_go(self):
        self.assertEqual(so.simplify([(0, 0), (0, 0), (1, 0.001), (2, 0), (3, 0), (3, 0)], 0.01), [(0, 0), (3, 0)])

    def test_turnaround_vertex_is_kept(self):
        self.assertEqual(so.simplify([(0, 0), (10, 0), (5, 0)], 0.01), [(0, 0), (10, 0), (5, 0)])

    def test_real_corner_is_kept(self):
        self.assertEqual(so.simplify([(0, 0), (5, 0.5), (10, 0)], 0.01), [(0, 0), (5, 0.5), (10, 0)])


if __name__ == "__main__":
    unittest.main()
