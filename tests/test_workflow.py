"""
Workflows (core/workflow.py): the graph, Check, chain names, runs end to end, the cache, branches, side ports,
previews, cancelling and the worker process. Everything is written to temporary folders.
"""
import _isolate  # first: settings and caches stay in a temporary folder
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from atlas_toolkit.core import samples
from atlas_toolkit.core import workflow as W
from atlas_toolkit.core.job import Cancelled
from atlas_toolkit.core.registry import registry

ONE_LINE_SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100">
  <path d="M 10 10 L 90 90" style="fill:none;stroke:#000;stroke-width:0.3"/>
</svg>
"""


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, kind, payload):
        self.events.append((kind, payload))

    def nodes(self, state):
        return [p[0] for k, p in self.events if k == "node" and p[1] == state]


def example(reg, folder, style="contours"):
    """The pipeline from the brief: model → shatter → hatch (depth contours) → remove duplicates → save."""
    hatch_tool = reg.get("stl-hatch-maker")
    wf = W.Workflow(name="Shatter and contour")
    src = wf.add(W.input_node("mesh"))
    shatter = wf.add(W.tool_node(reg.get("mesh-shatter"), values={"num_pieces": 6}))
    hatch = wf.add(W.tool_node(hatch_tool, values=dict(hatch_tool.test_values, style=style, levels=12)))
    clean = wf.add(W.tool_node(reg.get("svg-overlap-cleaner"), values={"mode": "duplicates"}))
    out = wf.add(W.save_node(where="folder", folder=str(folder)))
    wf.connect(src.id, shatter.id)
    wf.connect(shatter.id, hatch.id)
    wf.connect(hatch.id, clean.id)
    wf.connect(clean.id, out.id)
    return wf, (src, shatter, hatch, clean, out)


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="atlas_wf_"))
        cls.reg = registry()
        (cls.tmp / "in").mkdir()
        cls.mesh = samples.mesh(cls.tmp / "in")
        cls.svg = samples.svg(cls.tmp / "in")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def folder(self, name: str) -> Path:
        d = self.tmp / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        return d

    def cache(self, name: str) -> W.WorkflowCache:
        return W.WorkflowCache(self.folder(f"cache-{name}"))

    def run_wf(self, wf, src, cache, rec=None, **kw):
        rec = rec or Recorder()
        res = W.run_workflow(wf, src, rec, lambda: False, reg=self.reg, cache=cache,
                             tool_cache=str(self.tmp / "toolcache"), **kw)
        return res, rec

    # ------------------------------------------------------------------ model and files
    def test_round_trip_keeps_graph_values_and_positions(self):
        wf, (src, shatter, hatch, clean, out) = example(self.reg, self.tmp)
        pv = self.reg.get("plotter-vision")
        vision = wf.add(W.tool_node(pv, pos=(10, 20), values={"lookat": (1.0, 2.0, 3.0)}))
        wf.connect(shatter.id, vision.id)
        wf.exposed.append((shatter.id, "num_pieces"))
        path = W.save(wf, self.tmp / "example")
        self.assertEqual(path.suffix, W.EXT)
        back = W.load(path)
        self.assertEqual(back.to_dict()["nodes"], wf.to_dict()["nodes"])
        self.assertEqual([(e.src, e.dst, e.port) for e in back.edges], [(e.src, e.dst, e.port) for e in wf.edges])
        self.assertEqual(back.exposed, [(shatter.id, "num_pieces")])
        self.assertEqual(W.node_values(back.nodes[vision.id], pv)["lookat"], (1.0, 2.0, 3.0))
        self.assertEqual(back.nodes[vision.id].pos, (10.0, 20.0))

    def test_graph_refuses_loops_and_one_edge_per_input(self):
        wf, (src, shatter, hatch, clean, out) = example(self.reg, self.tmp)
        with self.assertRaises(W.WorkflowError):
            wf.connect(clean.id, shatter.id)
        with self.assertRaises(W.WorkflowError):
            wf.connect(out.id, clean.id)
        other = wf.add(W.input_node("mesh"))
        wf.connect(other.id, shatter.id)                   # replaces the edge on that port
        self.assertEqual([e.src for e in wf.edges_into(shatter.id)], [other.id])
        self.assertEqual(wf.order().index(shatter.id) < wf.order().index(clean.id), True)
        wf.remove(hatch.id)
        self.assertFalse(any(hatch.id in (e.src, e.dst) for e in wf.edges))

    def test_matching_preset_ignores_view_keys(self):
        from atlas_toolkit.core import presets as P
        hm = self.reg.get("stl-hatch-maker")
        contours = P.Preset("Contours", hm.id, {"spacing": 2.0, "cross": True})
        v = dict(P.apply(hm, contours)[0], azimuth=33.0)
        self.assertIs(P.match(hm, v, [contours]), contours)
        changed = dict(v, spacing=3.3)
        self.assertIsNone(P.match(hm, changed, [contours]))
        mine = P.Preset("Mine", hm.id, P.snapshot(hm, changed))
        self.assertIs(P.match(hm, changed, [contours, mine]), mine)

    # ------------------------------------------------------------------ check
    def test_example_checks_clean_and_names_chain(self):
        wf, nodes = example(self.reg, self.tmp)
        self.assertEqual(W.errors(W.check(wf, self.reg)), [])
        self.assertEqual(W.planned_names(wf, self.reg, "head.stl"),
                         {nodes[-1].id: "head_shattered_6pcs_hatch--nooverlap.svg"})

    def test_check_errors(self):
        reg = self.reg
        msgs = lambda wf: " | ".join(i.message for i in W.errors(W.check(wf, reg)))   # noqa: E731
        self.assertIn("empty", msgs(W.Workflow()))

        wf = W.Workflow()
        a = wf.add(W.input_node("svg"))
        s = wf.add(W.tool_node(reg.get("mesh-shatter")))
        wf.connect(a.id, s.id)
        self.assertIn("Nothing is saved", msgs(wf))
        self.assertIn("takes", msgs(wf))                           # SVG into a mesh tool

        wf, (src, shatter, hatch, clean, out) = example(reg, self.tmp)
        wf.disconnect(hatch.id)
        self.assertIn("Connect an input", msgs(wf))

        wf, (src, shatter, hatch, clean, out) = example(reg, self.tmp)
        wf.nodes[shatter.id].values["num_pieces"] = "many"
        self.assertIn("num_pieces", msgs(wf))
        wf.nodes[shatter.id].values["num_pieces"] = 6
        wf.nodes[hatch.id].tool = "no-such-tool"
        self.assertIn("not installed", msgs(wf))

        wf, (src, shatter, hatch, clean, out) = example(reg, self.tmp)
        wf.add(W.input_node("mesh"))
        self.assertIn("Only one input", msgs(wf))

        wf, (src, shatter, hatch, clean, out) = example(reg, self.tmp)
        wf.nodes[src.id].mode, wf.nodes[src.id].path = "fixed", str(self.tmp / "missing.stl")
        wf.nodes[out.id].where = "next"
        self.assertIn("not found", msgs(wf))
        self.assertIn("needs an input", msgs(wf))                 # Save "next to the input" without a batch

        wf, (src, shatter, hatch, clean, out) = example(reg, self.tmp)
        wf.nodes[out.id].name = "{name}_{seed}"
        self.assertIn("File name", msgs(wf))

        wf, (src, shatter, hatch, clean, out) = example(reg, self.tmp)
        twin = wf.add(W.save_node(where="folder", folder=str(self.tmp)))
        wf.connect(clean.id, twin.id)
        self.assertIn("same file", msgs(wf))

    def test_check_warnings(self):
        reg = self.reg
        wf, (src, shatter, hatch, clean, out) = example(reg, self.tmp)
        wf.nodes[shatter.id].tool_version = "0.1"
        dead = wf.add(W.tool_node(reg.get("voxelizer")))
        wf.connect(shatter.id, dead.id)
        cutter = wf.add(W.tool_node(reg.get("svg-shape-cutter")))       # shape_source "named": the port is unused
        wf.connect(clean.id, cutter.id)
        wf.connect(hatch.id, cutter.id, "shape_file")
        save2 = wf.add(W.save_node(where="folder", folder=str(self.tmp), name="{name}-cut"))
        wf.connect(cutter.id, save2.id)
        warns = {(i.node, i.message.split(" ")[0]) for i in W.check(wf, reg) if i.level == "warning"}
        self.assertIn((shatter.id, "Set"), warns)
        self.assertIn((dead.id, "This"), warns)
        self.assertIn((cutter.id, "Shape"), warns)
        self.assertEqual(W.errors(W.check(wf, reg)), [])

    # ------------------------------------------------------------------ running
    def test_reseeded_draws_new_seeds_and_keeps_every_other_setting(self):
        import random
        wf, (src, shatter, hatch, clean, out) = example(self.reg, self.folder("out-seeds"))
        seeds = W.seeds(wf, self.reg)
        self.assertIn((shatter.id, "seed"), seeds)
        self.assertTrue(all(wf.nodes[n].type == W.TOOL for n, _k in seeds))
        before = wf.to_dict()
        new, drawn = W.reseeded(wf, self.reg, random.Random(7))
        self.assertEqual(wf.to_dict(), before)                       # the workflow itself is untouched
        self.assertEqual({(n, k) for n, vals in drawn.items() for k in vals}, set(seeds))
        for nid, node in new.nodes.items():
            changed = {k for k in set(node.values) | set(wf.nodes[nid].values)
                       if node.values.get(k) != wf.nodes[nid].values.get(k)}
            self.assertLessEqual(changed, set(drawn.get(nid, {})))   # only seeds differ
        self.assertEqual(W.errors(W.check(new, self.reg)), [])
        self.assertEqual(W.reseeded(wf, self.reg, random.Random(7))[1], drawn)   # repeatable from its seeds

    def test_example_runs_saves_only_the_end_and_reuses_the_cache(self):
        out_dir = self.folder("out-example")
        before = sorted(p.name for p in self.mesh.parent.iterdir())
        wf, (src, shatter, hatch, clean, out) = example(self.reg, out_dir)
        cache = self.cache("example")
        res, rec = self.run_wf(wf, self.mesh, cache)
        final = out_dir / "sample_shattered_6pcs_hatch--nooverlap.svg"
        self.assertEqual([Path(o) for o in res.outputs], [final])
        self.assertEqual(sorted(p.name for p in out_dir.iterdir()), [final.name])
        self.assertIn(b"<desc>svg-overlap-cleaner</desc>", final.read_bytes()[:800])
        self.assertEqual(sorted(p.name for p in self.mesh.parent.iterdir()), before)   # nothing beside the input
        self.assertEqual(set(rec.nodes("done")) - {out.id}, {shatter.id, hatch.id, clean.id})
        self.assertTrue(res.preview and res.preview.get("views"))
        self.assertEqual(res.stat("steps").value, 3)

        res, rec = self.run_wf(wf, self.mesh, cache)            # nothing changed: every step from the cache
        self.assertEqual(set(rec.nodes("cached")), {shatter.id, hatch.id, clean.id})
        self.assertEqual(rec.nodes("started"), [])
        self.assertTrue(final.exists())

        wf.nodes[clean.id].values["tol"] = 0.08                 # only the last step changes
        res, rec = self.run_wf(wf, self.mesh, cache)
        self.assertEqual(set(rec.nodes("cached")), {shatter.id, hatch.id})
        self.assertEqual(rec.nodes("started"), [clean.id])

    def test_surface_texture_draw_custom_side_port_and_cache(self):
        from PIL import Image
        import numpy as np
        from atlas_toolkit.tools.mesh_texture import TOOL, engine
        source = engine.make_sample(self.folder("texture-in"))
        height = self.folder("texture-height") / 'height.png'
        Image.fromarray(np.tile(np.arange(16, dtype=np.uint16) * 4096, (16, 1))).save(height)
        wf = W.Workflow(name="Texture and draw")
        inp = wf.add(W.input_node("mesh"))
        side = wf.add(W.input_node("image", mode="fixed", path=str(height)))
        texture = wf.add(W.tool_node(TOOL, values=dict(TOOL.test_values, source='custom', element_size=30.)))
        draw_tool = self.reg.get('contour-lines')
        draw = wf.add(W.tool_node(draw_tool, values=draw_tool.test_values))
        save = wf.add(W.save_node(where='folder', folder=str(self.folder('texture-out'))))
        wf.connect(inp.id, texture.id); wf.connect(side.id, texture.id, 'heightmap')
        wf.connect(texture.id, draw.id); wf.connect(draw.id, save.id)
        wf = W.Workflow.from_dict(wf.to_dict())
        self.assertEqual(W.errors(W.check(wf, self.reg)), [])
        cache = self.cache('texture')
        result, rec = self.run_wf(wf, source, cache)
        self.assertTrue(Path(result.outputs[0]).is_file())
        _, rec = self.run_wf(wf, source, cache)
        self.assertEqual(set(rec.nodes('cached')), {texture.id, draw.id})
        Image.fromarray(np.tile(np.arange(16, dtype=np.uint16)[::-1] * 4096, (16, 1))).save(height)
        _, rec = self.run_wf(wf, source, cache)
        self.assertEqual(set(rec.nodes('started')), {texture.id, draw.id})

    def test_dilate_draw_round_trip_cache_and_invalidation(self):
        from atlas_toolkit.tools.mesh_dilate import TOOL, engine
        source = engine.make_sample(self.folder("dilate-in"))
        for drawing in ("contour-lines", "plotter-vision"):
            with self.subTest(drawing=drawing):
                wf = W.Workflow(name="Inflate and draw")
                inp = wf.add(W.input_node("mesh"))
                dilate = wf.add(W.tool_node(TOOL, values=dict(TOOL.test_values, inflation=60)))
                tool = self.reg.get(drawing)
                draw = wf.add(W.tool_node(tool, values=tool.test_values))
                folder = self.folder("dilate-out-" + drawing)
                save = wf.add(W.save_node(where="folder", folder=str(folder)))
                wf.connect(inp.id, dilate.id)
                wf.connect(dilate.id, draw.id)
                wf.connect(draw.id, save.id)
                wf = W.Workflow.from_dict(wf.to_dict())
                self.assertEqual(W.errors(W.check(wf, self.reg)), [])
                cache = self.cache("dilate-" + drawing)
                result, rec = self.run_wf(wf, source, cache)
                self.assertEqual(len(result.outputs), 1)
                self.assertEqual(Path(result.outputs[0]).suffix, ".svg")
                self.assertIn(dilate.id, rec.nodes("done"))
                _, rec = self.run_wf(wf, source, cache)
                self.assertEqual(set(rec.nodes("cached")), {dilate.id, draw.id})
                wf.nodes[dilate.id].values["inflation"] = 90
                _, rec = self.run_wf(wf, source, cache)
                self.assertEqual(set(rec.nodes("started")), {dilate.id, draw.id})
                self.assertEqual(list(source.parent.iterdir()), [source])

    def test_branches_save_two_drawings(self):
        out_dir = self.folder("out-branch")
        wf, (src, shatter, hatch, clean, out) = example(self.reg, out_dir)
        hm = self.reg.get("stl-hatch-maker")
        lines = wf.add(W.tool_node(hm, values=dict(hm.test_values, style="screen", spacing=3.0)))
        lines.label = "Lines"
        wf.connect(shatter.id, lines.id)
        out2 = wf.add(W.save_node(where="folder", folder=str(out_dir), name="{name}-{node}"))
        wf.connect(lines.id, out2.id)
        self.assertEqual(W.errors(W.check(wf, self.reg)), [])
        res, _rec = self.run_wf(wf, self.mesh, self.cache("branch"))
        self.assertEqual(sorted(Path(o).name for o in res.outputs),
                         ["sample_shattered_6pcs_hatch--nooverlap.svg", "sample_shattered_6pcs_hatch-Lines.svg"])

    def test_side_port_feeds_the_shape_cutter(self):
        out_dir = self.folder("out-side")
        reg = self.reg
        wf = W.Workflow(name="Cut to outline")
        src = wf.add(W.input_node("svg"))
        outline = wf.add(W.tool_node(reg.get("svg-shape-outline")))
        cutter = wf.add(W.tool_node(reg.get("svg-shape-cutter"), values={"shape_source": "file"}))
        out = wf.add(W.save_node(where="folder", folder=str(out_dir)))
        wf.connect(src.id, outline.id)
        wf.connect(src.id, cutter.id)
        wf.connect(outline.id, cutter.id, "shape_file")
        wf.connect(cutter.id, out.id)
        self.assertEqual(W.errors(W.check(wf, reg)), [])
        self.assertFalse([i for i in W.check(wf, reg) if i.port == "shape_file"])
        res, rec = self.run_wf(wf, self.svg, self.cache("side"))
        final = Path(res.outputs[0])
        self.assertEqual(final.name, "sample_cutout.svg")
        self.assertIn(b"<desc>svg-shape-cutter</desc>", final.read_bytes()[:800])
        self.assertEqual(rec.nodes("started"), [outline.id, cutter.id])

    def test_unchanged_step_passes_its_input_on(self):
        out_dir = self.folder("out-pass")
        src_file = samples.svg(self.folder("in-pass"), "line.svg", ONE_LINE_SVG)
        wf = W.Workflow()
        a = wf.add(W.input_node("svg"))
        c = wf.add(W.tool_node(self.reg.get("svg-overlap-cleaner")))
        s = wf.add(W.save_node(where="folder", folder=str(out_dir)))
        wf.connect(a.id, c.id)
        wf.connect(c.id, s.id)
        res, _rec = self.run_wf(wf, src_file, self.cache("pass"))
        self.assertEqual(Path(res.outputs[0]).read_bytes(), src_file.read_bytes())

    def test_preview_and_prepare_write_nothing(self):
        out_dir = self.folder("out-preview")
        wf, (src, shatter, hatch, clean, out) = example(self.reg, out_dir)
        cache = self.cache("preview")
        res, rec = self.run_wf(wf, self.mesh, cache, action="preview", target=hatch.id)
        self.assertTrue(res.preview["views"])
        self.assertEqual(list(out_dir.iterdir()), [])
        self.assertEqual(len(cache.entries()), 1)              # the shatter step; the draft is never cached
        res, rec = self.run_wf(wf, self.mesh, cache, action="prepare", target=hatch.id)
        self.assertTrue(Path(res.data["inputs"]["in"]).name.startswith("sample_shattered_6pcs"))
        self.assertEqual(rec.nodes("cached"), [shatter.id])

    def test_cancel_and_failure_leave_no_cache_hit(self):
        out_dir = self.folder("out-cancel")
        wf, (src, shatter, hatch, clean, out) = example(self.reg, out_dir)
        cache = self.cache("cancel")
        rec = Recorder()
        stop = lambda: bool(rec.nodes("started"))   # noqa: E731 - cancel as soon as the first step starts
        with self.assertRaises(Cancelled):
            W.run_workflow(wf, self.mesh, rec, stop, reg=self.reg, cache=cache)
        self.assertEqual(cache.entries(), [])
        self.assertEqual(list(cache.root.glob("*/*.partial-*")), [])

        bad = self.folder("in-bad") / "broken.stl"
        bad.write_bytes(b"solid nothing\nnot a mesh\n")
        with self.assertRaises(W.StepFailed) as ctx:
            W.run_workflow(wf, bad, Recorder(), lambda: False, reg=self.reg, cache=cache)
        self.assertEqual(ctx.exception.node, shatter.id)
        self.assertEqual(cache.entries(), [])
        self.assertEqual(list(out_dir.iterdir()), [])

    def test_cache_prunes_oldest_entries_over_the_limit(self):
        cache = self.cache("prune")
        for i in range(3):
            p = cache.begin(f"{i:02d}key{i}")
            (p / "f.bin").write_bytes(b"x" * 1000)
            cache.commit(f"{i:02d}key{i}", p, {"main": "f.bin"})
        cache.limit = 2500
        cache.prune()
        self.assertEqual(len(cache.entries()), 2)
        self.assertIsNone(cache.lookup("00key0"))

    def test_job_runs_in_the_worker_process(self):
        from atlas_toolkit.core.runner import Runner
        out_dir = self.folder("out-worker")
        wf, nodes = example(self.reg, out_dir)
        box, ev, seen = {}, threading.Event(), []

        def on_event(_jid, kind, payload):
            seen.append(kind)
            if kind in ("done", "error", "cancelled"):
                box[kind] = payload
                ev.set()

        r = Runner(on_event)
        try:
            r.submit(W.workflow_job(wf, self.mesh, cache_root=self.folder("cache-worker"),
                                    tool_cache=str(self.tmp / "toolcache")), "process")
            self.assertTrue(ev.wait(300), "timed out")
        finally:
            r.shutdown()
        self.assertIn("done", box, box.get("error"))
        self.assertIn("node", seen)
        self.assertTrue((out_dir / "sample_shattered_6pcs_hatch--nooverlap.svg").exists())


class SavedNameTests(unittest.TestCase):
    """The left panel lists saved workflows by the name stored in them, not by their file name."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="atlas_wfname_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_stored_name_wins_over_the_file_name(self):
        path = W.save(W.Workflow(name="Shattered bust, hatched"), self.tmp / "wf-2026-10-27")
        self.assertEqual(W.saved_name(path), "Shattered bust, hatched")

    def test_renaming_the_workflow_is_seen_after_saving(self):
        path = W.save(W.Workflow(name="First"), self.tmp / "a")
        self.assertEqual(W.saved_name(path), "First")
        W.save(W.Workflow(name="Second, longer"), path)
        self.assertEqual(W.saved_name(path), "Second, longer")

    def test_no_name_or_unreadable_falls_back_to_the_file_name(self):
        blank = self.tmp / ("blank" + W.EXT)
        blank.write_text('{"name": "  "}', encoding="utf-8")
        broken = self.tmp / ("broken" + W.EXT)
        broken.write_text("{not json", encoding="utf-8")
        self.assertEqual(W.saved_name(blank), "blank")
        self.assertEqual(W.saved_name(broken), "broken")
        self.assertEqual(W.saved_name(self.tmp / ("missing" + W.EXT)), "missing")

    def test_the_library_panel_shows_names(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from atlas_toolkit.core.history import History
        from atlas_toolkit.core.settings import AppSettings
        from atlas_toolkit.ui.bridge import JobBridge
        from atlas_toolkit.ui.context import AppContext
        from atlas_toolkit.ui.workflow.library import Library
        ctx = AppContext(registry(), AppSettings(self.tmp / "cfg"), JobBridge(), History(self.tmp / "h.jsonl"))
        a = W.save(W.Workflow(name="Melt then contours"), self.tmp / "untitled-1")
        b = W.save(W.Workflow(name="Twin"), self.tmp / "t1")
        c = W.save(W.Workflow(name="Twin"), self.tmp / "t2")
        lib = Library(ctx)
        lib.set_saved([a, b, c])
        labels = []
        for i in range(lib.tree.topLevelItemCount()):
            head = lib.tree.topLevelItem(i)
            if head.text(0) == "SAVED WORKFLOWS":
                labels = [head.child(k).text(0) for k in range(head.childCount())]
        self.assertEqual(labels, ["Melt then contours", "Twin (t1)", "Twin (t2)"])
        lib.search.setText("melt then")
        self.assertTrue(any(lib.tree.topLevelItem(i).text(0) == "SAVED WORKFLOWS"
                            for i in range(lib.tree.topLevelItemCount())))
        lib.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
