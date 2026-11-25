import _isolate  # first: settings and caches stay in a temporary folder
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

from atlas_toolkit.core.params import PEN_OUTPUTS, resolve
from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom import svgout
from atlas_toolkit.geom.svg import INKSCAPE_NS, SVG_NS, read_run_metadata
from atlas_toolkit.geom.svgout import Layer

# Every tool that can draw with more than one pen (widths, dashes, colours) offers the shared Save pens as setting.
MULTI_PEN_TOOLS = ("dpt-hatching", "flowrep-sketch", "plotter-vision", "sketch-hatching", "stipple",
                   "stl-hatch-maker")
GROUPMODE = f"{{{INKSCAPE_NS}}}groupmode"
LABEL = f"{{{INKSCAPE_NS}}}label"


def pens():
    return [Layer("hatching", [[(10, 10), (50, 10)], [(10, 20), (50, 20)]], "#000000", 0.3),
            Layer("outline", [[(5, 5), (60, 5), (60, 60)]], "#000000", 0.5),
            Layer("empty", [], "#000000", 0.7),
            Layer("hidden lines", [[(0, 30), (2, 30)], [(4, 30), (6, 30)]], "#999999", 0.3)]


def top_layers(root):
    return [g for g in root if g.tag == f"{{{SVG_NS}}}g" and g.get(GROUPMODE) == "layer"]


def paths(el):
    return el.findall(f".//{{{SVG_NS}}}path")


class WritePensTests(unittest.TestCase):
    """geom/svgout.write_pens: the four ways a drawing with several pens is saved."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.tool = registry().get("stipple")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, mode, layers=None):
        dst = self.dir / f"art_{mode}.svg"
        files = svgout.write_pens(dst, 100, 80, pens() if layers is None else layers, mode, self.tool,
                                  {"pen_output": mode})
        self.assertEqual(files[0], dst)
        for f in files:          # every file is signed, carries the settings and sits on the same sheet in mm
            root = ET.parse(f).getroot()
            self.assertEqual(root[0].text, "stipple")
            self.assertEqual(read_run_metadata(f), ("stipple", {"pen_output": mode}))
            self.assertEqual((root.get("width"), root.get("height"), root.get("viewBox")), ("100mm", "80mm",
                                                                                         "0 0 100 80"))
            self.assertNotIn("dasharray", f.read_text(encoding="utf-8"))
        return files, ET.parse(dst).getroot()

    def test_the_modes_match_the_setting(self):
        self.assertEqual(svgout.PEN_OUTPUT_MODES, tuple(v for v, _ in PEN_OUTPUTS))

    def test_layers_one_inkscape_layer_per_pen(self):
        files, root = self.write("layers")
        self.assertEqual(len(files), 1)
        layers = top_layers(root)
        self.assertEqual([g.get(LABEL) for g in layers], ["1 hatching", "2 outline", "3 hidden lines"])
        self.assertEqual([len(paths(g)) for g in layers], [2, 1, 2])
        self.assertIn("stroke-width:0.5", layers[1].get("style"))
        self.assertIn("stroke:#999999", layers[2].get("style"))

    def test_groups_one_layer_holding_a_group_per_pen(self):
        _, root = self.write("groups")
        layers = top_layers(root)
        self.assertEqual(len(layers), 1)
        groups = [g for g in layers[0] if g.tag == f"{{{SVG_NS}}}g"]
        self.assertEqual([g.get(LABEL) for g in groups], ["1 hatching", "2 outline", "3 hidden lines"])
        self.assertEqual([g.get("id") for g in groups], ["pen1-hatching", "pen2-outline", "pen3-hidden-lines"])
        self.assertTrue(all(g.get(GROUPMODE) is None for g in groups))     # plain groups: plotted in one pass
        self.assertIn("stroke-width:0.5", groups[1].get("style"))
        self.assertEqual(len(paths(root)), 5)

    def test_files_the_drawing_and_a_file_per_pen(self):
        files, root = self.write("files")
        self.assertEqual([f.name for f in files], ["art_files.svg", "art_files-1-hatching.svg",
                                                    "art_files-2-outline.svg", "art_files-3-hidden-lines.svg"])
        self.assertEqual(len(top_layers(root)), 3)          # the whole drawing, as layers
        for f, n in zip(files[1:], (2, 1, 2)):
            layers = top_layers(ET.parse(f).getroot())
            self.assertEqual(len(layers), 1)
            self.assertEqual(len(paths(layers[0])), n)

    def test_files_with_one_pen_writes_only_the_drawing(self):
        files, _ = self.write("files", [Layer("dots", [[(1, 1), (2, 2)]])])
        self.assertEqual(len(files), 1)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["art_files.svg"])

    def test_merged_one_pen_at_the_first_width(self):
        _, root = self.write("merged")
        layers = top_layers(root)
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0].get(LABEL), "1 drawing")
        self.assertIn("stroke-width:0.3", layers[0].get("style"))
        self.assertEqual(len(paths(root)), 5)

    def test_merged_orders_strokes_together_only_when_asked(self):
        far, near = [(90, 70), (95, 70)], [(1, 0), (2, 0)]
        layers = [Layer("a", [far]), Layer("b", [near])]
        self.assertEqual(svgout.arrange(layers, "merged")[0].polylines, [far, near])
        self.assertEqual(svgout.arrange(layers, "merged", optimise=True)[0].polylines, [near, far])
        for mode in ("layers", "groups", "files"):
            self.assertEqual([l.label for l in svgout.arrange(layers + [Layer("c")], mode)], ["a", "b"])

    def test_ready_path_data_is_written_as_it_is(self):
        layers = [Layer("curves", [[(0, 0), (9, 9)]], paths=["M 0,0 C 3,0 6,9 9,9"]), Layer("b", [[(1, 1), (2, 2)]])]
        _, root = self.write("layers", layers)
        self.assertEqual(paths(root)[0].get("d"), "M 0,0 C 3,0 6,9 9,9")
        self.assertEqual(paths(root)[1].get("d"), "M 1,1 L 2,2")

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            svgout.write_pens(self.dir / "x.svg", 10, 10, pens(), "folders")
        with self.assertRaises(ValueError):
            svgout.pen_mode({"pen_output": "folders"})
        self.assertEqual(svgout.pen_mode({}), "layers")

    def test_dashes_are_geometry(self):
        dashes = svgout.dashed([[(0, 0), (10, 0)]], 1.5, 1.5)
        self.assertEqual(len(dashes), 4)
        self.assertAlmostEqual(dashes[1][0][0], 3.0)
        self.assertAlmostEqual(dashes[1][-1][0], 4.5)


class SettingTests(unittest.TestCase):
    """The setting is one and the same in every tool that draws several pens."""

    def test_every_multi_pen_tool_offers_the_same_setting(self):
        reg = registry()
        for tid in MULTI_PEN_TOOLS:
            p = reg.get(tid).param("pen_output")
            self.assertEqual((p.label, p.default, p.pairs()), ("Save pens as", "layers", PEN_OUTPUTS), tid)

    def test_drawing_tools_dash_with_geometry(self):
        """A stroke-dasharray plots solid and is lost on a merged pen: the drawing tools cut dashes themselves."""
        import importlib
        for tool in registry().tools.values():
            if tool.output.kind != "svg" or tool.inputs.kind == "svg":     # post-processors keep their input's style
                continue
            folder = Path(importlib.import_module(tool.package).__file__).parent
            for py in folder.glob("*.py"):       # written as an attribute or a style, not named in a comment
                hit = re.search(r"""["']stroke-dasharray|stroke-dasharray\s*:""", py.read_text(encoding="utf-8"))
                self.assertIsNone(hit, str(py))

    def test_drawing_tools_with_one_pen_do_not(self):
        self.assertFalse(any(p.key == "pen_output" for p in registry().get("contour-lines").param_list))

    def test_hatch_makers_one_pen_setting_carries_over(self):
        tool = registry().get("stl-hatch-maker")
        self.assertEqual(resolve(tool.param_list, {"one_layer": True})["pen_output"], "merged")
        self.assertEqual(resolve(tool.param_list, {"one_layer": False})["pen_output"], "layers")

    def test_shown_only_when_there_are_several_pens(self):
        reg = registry()
        hm = reg.get("stl-hatch-maker").param("pen_output")
        self.assertTrue(hm.visible({"style": "streamlines", "silhouettes": True}))
        self.assertFalse(hm.visible({"style": "streamlines", "silhouettes": False, "creases": False}))
        pv = reg.get("plotter-vision").param("pen_output")
        self.assertFalse(pv.visible({"depth_layers": 1}))
        self.assertTrue(pv.visible({"depth_layers": 1, "show_hidden": True}))
        st = reg.get("stipple").param("pen_output")
        self.assertFalse(st.visible({"outline": "none"}))
        self.assertTrue(st.visible({"outline": "pen"}))


class ToolTests(unittest.TestCase):
    """The tools write what the setting asks and report every file, the drawing first."""

    def test_stipple_files_and_merged(self):
        from atlas_toolkit.core import samples
        from atlas_toolkit.core.job import JobContext
        tool = registry().get("stipple")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = samples.for_kind("mesh", tmp)
            for mode, n_files, n_pens in (("files", 3, 2), ("merged", 1, 1)):
                dst = tmp / f"s_{mode}.svg"
                res = tool.engine_fn()(JobContext(lambda *a: None, lambda: False), src, dst,
                                       tool.values(dict(tool.test_values, outline="pen", pen_output=mode)))
                self.assertEqual(res.outputs[0], str(dst))
                self.assertEqual(len(res.outputs), n_files)
                self.assertTrue(all(Path(o).is_file() for o in res.outputs))
                self.assertEqual(len(res.preview["views"][0]["drawing"].pens), n_pens)   # the preview shows the file
                self.assertEqual(len(top_layers(ET.parse(dst).getroot())), n_pens)
                self.assertIn("saved as", res.report)

    def test_flowrep_hidden_curves_are_dashed_strokes(self):
        from atlas_toolkit.tools.flowrep_sketch import engine
        drawing = SimpleNamespace(layers={"features": [[(0, 0), (10, 0)]], "silhouette": [], "flowlines": [],
                                          "hidden": [[(0, 5), (12, 5)]]}, line_width=0.3)
        layers = engine.pen_layers(drawing, "layers")
        self.assertEqual([l.label for l in layers], ["features", "hidden"])
        self.assertEqual(len(layers[1].polylines), 4)                   # 12 mm in 1.5 mm dashes, 1.5 mm gaps
        self.assertEqual(layers[1].color, engine.HIDDEN_COLOR)
        merged = engine.pen_layers(drawing, "merged")
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0].polylines), 5)


if __name__ == "__main__":
    unittest.main()
