"""Shape Outline: what v1 did (a hull around a drawing) and what it got wrong (units, transforms, element types,
islands), plus the envelope method, holes, margins and the two ways of writing the result."""
import _isolate  # first: settings and caches stay in a temporary folder
import io
import unittest

from atlas_toolkit.geom.svg import load, local
from atlas_toolkit.tools.svg_shape_outline import svg_shape_outline as so

HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        'width="{w}mm" height="{w}mm" viewBox="0 0 100 100">')


def run(body: str, w: int = 100, **kw):
    tree = load(io.BytesIO((HEAD.format(w=w) + body + "</svg>").encode()))
    return so.outline_tree(tree, so.Options(**kw))


def hatch(x0, y0, x1, y1, step=1.0, skip=None):
    """Horizontal lines filling a box; `skip` (x0, y0, x1, y1) leaves a blank area."""
    out, y = [], y0
    while y <= y1 + 1e-9:
        if skip and skip[1] <= y <= skip[3]:
            out.append(f'<path d="M {x0} {y} H {skip[0]} M {skip[2]} {y} H {x1}"/>')
        else:
            out.append(f'<path d="M {x0} {y} H {x1}"/>')
        y += step
    return "".join(out)


class OutlineTests(unittest.TestCase):
    def test_a_closed_stroke_is_filled(self):
        _, st = run('<path d="M 20 20 H 80 V 80 H 20 Z"/>')
        self.assertEqual((st.parts, st.holes), (1, 0))
        self.assertAlmostEqual(st.area, 3600, delta=36)
        self.assertAlmostEqual(st.length, 240, delta=2)

    def test_hatching_becomes_its_silhouette(self):
        _, st = run(hatch(10, 10, 50, 50))
        self.assertEqual(st.parts, 1)
        self.assertAlmostEqual(st.area, 1600, delta=40)

    def test_separate_parts_stay_separate_or_only_the_largest(self):
        body = hatch(5, 5, 25, 25) + hatch(60, 60, 95, 95)
        self.assertEqual(run(body)[1].parts, 2)
        _, st = run(body, islands="largest")
        self.assertEqual(st.parts, 1)
        self.assertAlmostEqual(st.area, 35 * 35, delta=40)

    def test_blank_areas_become_holes_only_when_asked(self):
        body = hatch(10, 10, 60, 60, skip=(25, 25, 45, 45))
        self.assertEqual(run(body)[1].holes, 0)
        _, st = run(body, holes=True)
        self.assertEqual(st.holes, 1)
        self.assertLess(st.area, 50 * 50 - 300)

    def test_millimetres_and_transforms(self):
        # a 200 mm sheet on a 100-unit viewBox: 2 mm per unit; the square sits in a translated, scaled group
        _, st = run('<g transform="translate(10 10) scale(0.5)"><rect x="0" y="0" width="40" height="40"/></g>', w=200)
        self.assertAlmostEqual(st.area, 40 * 40, delta=20)            # 20 units = 40 mm a side
        xs = [x for r in st.rings for x, _ in r]
        self.assertAlmostEqual(min(xs), 10, delta=0.02)               # root units, transforms applied

    def test_every_element_type_counts(self):
        _, st = run('<circle cx="30" cy="30" r="10"/><line x1="60" y1="20" x2="90" y2="20"/>'
                    '<polyline points="60,60 90,60 90,90"/><ellipse cx="20" cy="80" rx="8" ry="4"/>')
        self.assertEqual(st.elements, 4)

    def test_hulls(self):
        body = '<polyline points="10,10 90,10 90,20 20,20 20,90 10,90 10,10"/>'   # an L
        convex = run(body, method="convex")[1].area
        concave = run(body, method="concave", concavity=0.9, step=1.0)[1].area
        loose = run(body, method="concave", concavity=0.0, step=1.0)[1].area
        self.assertLess(concave, convex * 0.6)
        self.assertAlmostEqual(loose, convex, delta=convex * 0.01)   # concavity 0 is the convex hull

    def test_margin(self):
        a = run('<path d="M 20 20 H 80 V 80 H 20 Z"/>')[1].area
        b = run('<path d="M 20 20 H 80 V 80 H 20 Z"/>', offset=5)[1].area
        self.assertAlmostEqual(b - a, 4 * 60 * 5 + 3.1416 * 25, delta=20)

    def test_outline_only_keeps_the_sheet_and_layer_mode_keeps_the_drawing(self):
        body = '<path id="d" d="M 20 20 H 80 V 80 H 20 Z"/>'
        out, _ = run(body, output="only")
        root = out.getroot()
        self.assertEqual((root.get("width"), root.get("viewBox")), ("100mm", "0 0 100 100"))
        self.assertIsNone(next((e for e in root.iter() if e.get("id") == "d"), None))
        out, _ = run(body, output="layer")
        ids = [e.get("id") for e in out.getroot().iter() if e.get("id")]
        self.assertEqual(ids, ["d", "outline"])
        self.assertEqual(local(out.getroot()[-1].tag), "g")

    def test_one_layer_only(self):
        body = ('<g inkscape:label="a">' + hatch(5, 5, 25, 25) + '</g><g inkscape:label="b">' + hatch(60, 60, 95, 95)
                + "</g>")
        self.assertEqual(run(body, layer="b")[1].parts, 1)
        with self.assertRaises(so.OutlineError):
            run(body, layer="nope")

    def test_the_outline_file_cuts_the_drawing_in_the_shape_cutter(self):
        import tempfile
        from pathlib import Path
        from atlas_toolkit.geom.svg import read_drawing
        from atlas_toolkit.tools.svg_shape_cutter import svg_shape_cutter as sc
        out, _ = run('<path d="M 20 20 H 80 V 80 H 20 Z"/>', w=200)       # 2 mm per unit: a 120 mm square
        with tempfile.TemporaryDirectory() as d:
            mask = Path(d) / "mask.svg"
            out.write(mask, encoding="UTF-8", xml_declaration=True)
            # another drawing on a plain mm sheet: a line across the middle of the square
            tree = load(io.BytesIO(b'<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="200mm" '
                                   b'viewBox="0 0 200 200"><path d="M 0 100 H 200"/></svg>'))
            sc.cut_tree(tree, sc.Options(shape_source="file", shape_file=str(mask)))
            self.assertAlmostEqual(read_drawing(tree).stats().ink, 120, delta=1)

    def test_nothing_to_outline(self):
        with self.assertRaises(so.OutlineError):
            run('<text x="1" y="1">no strokes</text>')


if __name__ == "__main__":
    unittest.main()
