"""The Overlap Cleaner's optional stages (join, whole duplicates, basic shapes, pen by width): what they replace from
SVG Join Paths v1/v2 and svg_dedup, and that each stays off unless asked for."""
import _isolate  # first: settings and caches stay in a temporary folder
import unittest
import xml.etree.ElementTree as ET

from atlas_toolkit.tools.svg_overlap_cleaner import svg_overlap_cleaner as so

HEAD = '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100">'


def svg(*body: str) -> str:
    return HEAD + "".join(body) + "</svg>"


def elements(text: str) -> dict:
    root = ET.fromstring(text.split("?>", 1)[-1])
    return {el.get("id"): el for el in root.iter() if el.get("id")}


def strokes(d: str) -> int:
    return d.count("M")


class JoinTests(unittest.TestCase):
    def test_join_is_off_by_default(self):
        out, st = so.clean_string(svg('<path id="a" d="M 0 0 H 10"/>', '<path id="b" d="M 10.05 0 H 20"/>'))
        self.assertEqual(set(elements(out)), {"a", "b"})
        self.assertEqual(st.joined, 0)

    def test_ends_within_tolerance_join_across_paths(self):
        out, st = so.clean_string(svg('<path id="a" d="M 0 0 H 10"/>', '<path id="b" d="M 10.05 0 H 20"/>'),
                                  so.Options(join=0.1))
        els = elements(out)
        self.assertEqual(set(els), {"a"})                 # b was absorbed by the first path in plotting order
        self.assertEqual(strokes(els["a"].get("d")), 1)
        self.assertEqual(st.joined, 1)
        self.assertEqual(st.strokes_out, 1)

    def test_reversed_stroke_joins_unless_reversal_is_off(self):
        body = ('<path id="a" d="M 0 0 H 10"/>', '<path id="b" d="M 20 0 L 10 0"/>')
        _, st = so.clean_string(svg(*body), so.Options(join=0.1))
        self.assertEqual(st.joined, 1)
        _, st = so.clean_string(svg(*body), so.Options(join=0.1, join_reverse=False))
        self.assertEqual(st.joined, 0)

    def test_a_chain_of_three_polylines_becomes_one_stroke(self):   # what SVG Join Paths v1/v2 were for
        out, st = so.clean_string(svg('<polyline id="a" points="0,0 10,0"/>', '<polyline id="c" points="30,0 20,0"/>',
                                      '<polyline id="b" points="10,0 20,0"/>'), so.Options(join=0.05))
        self.assertEqual(st.strokes_out, 1)
        self.assertEqual(st.joined, 2)

    def test_gaps_wider_than_the_tolerance_are_never_bridged(self):
        _, st = so.clean_string(svg('<path id="a" d="M 0 0 H 10"/>', '<path id="b" d="M 10.5 0 H 20"/>'),
                                so.Options(join=0.1))
        self.assertEqual(st.joined, 0)

    def test_other_layers_are_not_joined(self):
        _, st = so.clean_string(svg('<g id="l1"><path id="a" d="M 0 0 H 10"/></g>',
                                    '<g id="l2"><path id="b" d="M 10 0 H 20"/></g>'), so.Options(join=0.1))
        self.assertEqual(st.joined, 0)

    def test_element_scope_only_joins_subpaths(self):
        body = ('<path id="a" d="M 0 0 H 10 M 10 0 H 20"/>', '<path id="b" d="M 20 0 H 30"/>')
        out, st = so.clean_string(svg(*body), so.Options(join=0.1, join_scope="element"))
        els = elements(out)
        self.assertEqual(set(els), {"a", "b"})
        self.assertEqual(strokes(els["a"].get("d")), 1)
        self.assertEqual(st.joined, 1)

    def test_closed_loops_are_left_alone(self):
        _, st = so.clean_string(svg('<path id="a" d="M 0 0 H 10 V 10 Z"/>', '<path id="b" d="M 0 0 L -10 0"/>'),
                                so.Options(join=0.1))
        self.assertEqual(st.joined, 0)


class WholeDuplicateTests(unittest.TestCase):   # what svg_dedup did
    def test_exact_and_reversed_duplicates_are_removed(self):
        out, st = so.clean_string(svg('<path id="a" d="M 10 10 L 50 10 L 50 50"/>',
                                      '<path id="b" d="M 10 10 L 50 10 L 50 50"/>',
                                      '<path id="c" d="M 50 50 L 50 10 L 10 10"/>'), so.Options(mode="duplicates"))
        self.assertEqual(set(elements(out)), {"a"})
        self.assertEqual(st.removed_elements, 2)

    def test_partial_overlaps_are_not_split(self):
        out, st = so.clean_string(svg('<path id="a" d="M 0 50 H 100"/>', '<path id="b" d="M 0 50 H 60 L 60 90"/>'),
                                  so.Options(mode="duplicates"))
        self.assertEqual(elements(out)["b"].get("d"), "M 0 50 H 60 L 60 90")
        self.assertEqual(st.changed, 0)

    def test_duplicate_within_tolerance_is_removed(self):
        _, st = so.clean_string(svg('<path id="a" d="M 0 50 H 100"/>', '<path id="b" d="M 0 50.02 H 100"/>'),
                                so.Options(mode="duplicates"))
        self.assertEqual(st.removed_elements, 1)


class ShapeTests(unittest.TestCase):
    def test_shapes_are_ignored_by_default(self):
        out, st = so.clean_string(svg('<rect id="r" x="10" y="10" width="20" height="10"/>',
                                      '<rect id="s" x="10" y="10" width="20" height="10"/>'))
        self.assertEqual(st.elements, 0)
        self.assertEqual(set(elements(out)), {"r", "s"})

    def test_duplicate_rect_and_circle_are_removed(self):
        out, st = so.clean_string(svg('<rect id="r" x="10" y="10" width="20" height="10"/>',
                                      '<rect id="s" x="10" y="10" width="20" height="10"/>',
                                      '<circle id="c" cx="70" cy="70" r="10"/>',
                                      '<ellipse id="e" cx="70" cy="70" rx="10" ry="10"/>'), so.Options(shapes=True))
        self.assertEqual(set(elements(out)), {"r", "c"})
        self.assertEqual(elements(out)["r"].tag.rsplit("}", 1)[-1], "rect")   # untouched shapes stay shapes

    def test_a_cut_rect_becomes_a_path(self):
        out, _ = so.clean_string(svg('<path id="a" d="M 10 10 H 30"/>',
                                     '<rect id="r" x="10" y="10" width="20" height="10"/>'), so.Options(shapes=True))
        r = elements(out)["r"]
        self.assertEqual(r.tag.rsplit("}", 1)[-1], "path")
        for k in ("x", "y", "width", "height"):
            self.assertIsNone(r.get(k))


class PenByWidthTests(unittest.TestCase):
    BODY = ('<path id="a" style="stroke:#000;stroke-width:0.3" d="M 0 50 H 100"/>',
            '<path id="b" style="stroke:#000;stroke-width:1.2" d="M 0 50 H 100"/>')

    def test_same_colour_different_width_is_one_pen_by_default(self):
        _, st = so.clean_string(svg(*self.BODY))
        self.assertEqual(st.removed_elements, 1)

    def test_pen_by_width_keeps_the_bold_line(self):
        _, st = so.clean_string(svg(*self.BODY), so.Options(pen_by_width=True))
        self.assertEqual(st.removed_elements, 0)


if __name__ == "__main__":
    unittest.main()
