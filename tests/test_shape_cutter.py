"""Shape Cutter: the first cutter's naming convention still works, plus what it could not do (transforms, holes,
several islands, outside, layers, a shape from another file, keeping the rest of the drawing)."""
import _isolate  # first: settings and caches stay in a temporary folder
import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from atlas_toolkit.geom.svg import load, read_drawing
from atlas_toolkit.tools.svg_shape_cutter import svg_shape_cutter as sc

HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        'width="100mm" height="100mm" viewBox="0 0 100 100">')
SQUARE = '<path id="shape" d="M 20 20 H 80 V 80 H 20 Z"/>'
HATCH = "".join(f'<path id="h{y}" d="M 0 {y} H 100"/>' for y in range(10, 100, 10))


def cut(body: str, **kw):
    tree = load(io.BytesIO((HEAD + body + "</svg>").encode()))
    st = sc.cut_tree(tree, sc.Options(**kw))
    return tree, st


def ink(tree) -> float:
    return read_drawing(tree).stats().ink


class ShapeCutterTests(unittest.TestCase):
    def test_named_shape_clips_the_hatch_like_the_first_cutter(self):
        tree, st = cut(SQUARE + HATCH)
        self.assertAlmostEqual(ink(tree), 7 * 60, places=3)    # lines y=20..80 cut to the 60 mm wide square
        self.assertEqual(st.clipped, 7)
        self.assertEqual(st.removed, 2)                         # y=10 and y=90 miss the square
        ids = {el.get("id") for el in tree.getroot().iter() if el.get("id")}
        self.assertNotIn("shape", ids)                          # the shape itself goes, as before

    def test_other_content_is_kept_when_a_hatch_is_named(self):
        body = SQUARE + '<path id="hatch" d="M 0 50 H 100"/>' + '<path id="frame" d="M 0 0 H 100"/>'
        tree, st = cut(body)
        ids = {el.get("id") for el in tree.getroot().iter() if el.get("id")}
        self.assertIn("frame", ids)                             # not the hatch: left alone
        self.assertEqual(st.clipped, 1)

    def test_holes_and_islands_count(self):
        ring = '<g id="shape"><circle cx="50" cy="50" r="40"/><circle cx="50" cy="50" r="20"/></g>'
        tree, st = cut(ring + '<path id="h" d="M 0 50 H 100"/>')
        self.assertEqual((st.shape_parts, st.shape_holes), (1, 1))
        self.assertAlmostEqual(ink(tree), 40, delta=0.2)       # two 20 mm pieces either side of the hole
        two = '<g id="shape"><rect x="0" y="0" width="30" height="100"/><rect x="70" y="0" width="30" height="100"/></g>'
        tree, st = cut(two + '<path id="h" d="M 0 50 H 100"/>')
        self.assertEqual(st.shape_parts, 2)
        self.assertAlmostEqual(ink(tree), 60, places=3)

    def test_outside_cuts_a_hole(self):
        tree, _ = cut(SQUARE + '<path id="h" d="M 0 50 H 100"/>', keep="outside")
        self.assertAlmostEqual(ink(tree), 40, places=3)

    def test_transforms_are_respected(self):
        body = '<g transform="translate(10 0)">' + SQUARE + '</g><g transform="scale(2)"><path id="h" d="M 0 25 H 50"/></g>'
        tree, _ = cut(body)
        self.assertAlmostEqual(ink(tree), 60, places=3)        # the line at y=50 inside the moved square
        d = read_drawing(tree)
        xs = d.pens[0].xy[:, 0]
        self.assertAlmostEqual(float(xs.min()), 30, places=3)
        self.assertAlmostEqual(float(xs.max()), 90, places=3)

    def test_untouched_elements_keep_their_curves(self):
        tree, st = cut(SQUARE + '<path id="c" d="M 30 50 C 40 30 60 70 70 50"/>')
        el = next(e for e in tree.getroot().iter() if e.get("id") == "c")
        self.assertEqual(el.get("d"), "M 30 50 C 40 30 60 70 70 50")
        self.assertEqual(st.untouched, 1)

    def test_shape_from_a_layer(self):
        body = ('<g inkscape:label="silhouette"><rect x="20" y="20" width="60" height="60"/></g>'
                '<g inkscape:label="lines"><path d="M 0 50 H 100"/></g>')
        tree, st = cut(body, shape_source="layer", shape_layer="silhouette")
        self.assertAlmostEqual(ink(tree), 60, places=3)

    def test_shape_from_another_file_in_mm(self):
        with tempfile.TemporaryDirectory() as d:
            other = Path(d) / "mask.svg"   # 200 mm sheet in a 0..100 viewBox: 2 mm per unit
            other.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="200mm" '
                             'viewBox="0 0 100 100"><rect x="10" y="10" width="20" height="20"/></svg>')
            tree, _ = cut('<path d="M 0 50 H 100"/>', shape_source="file", shape_file=str(other))
            self.assertAlmostEqual(ink(tree), 40, places=3)   # the mask is 20..60 mm wide

    def test_missing_shape_is_an_error(self):
        with self.assertRaises(sc.ShapeError):
            cut('<line x1="0" y1="0" x2="10" y2="10"/>')


if __name__ == "__main__":
    unittest.main()
