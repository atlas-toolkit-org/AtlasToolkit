"""
The app's general options (core/prefs.py): checked values, outputs that already exist (overwrite, ask, keep both)
in tool runs, workflows and the command line, settings export and import, and several worker processes at once.
Everything is written to temporary folders.
"""
import _isolate  # first: settings and caches stay in a temporary folder
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from atlas_toolkit.core import prefs, samples
from atlas_toolkit.core import workflow as W
from atlas_toolkit.core.registry import registry
from atlas_toolkit.core.settings import AppSettings


class PrefsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="atlas_prefs_"))
        cls.leave_home = _isolate.fresh_home()
        cls.reg = registry()

    @classmethod
    def tearDownClass(cls):
        cls.leave_home()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def folder(self, name: str) -> Path:
        d = self.tmp / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_values_are_checked_against_their_kind_and_range(self):
        s = AppSettings(self.folder("cfg-check"))
        self.assertEqual({k: prefs.get(s, k) for k in prefs.DEFAULTS}, prefs.DEFAULTS)
        s.data.update(on_existing="sometimes", workers=99, preview_delay_ms="fast", confirm_quit=1, ui_scale=9.0,
                      theme_start="light")
        self.assertEqual(prefs.get(s, "on_existing"), "overwrite")
        self.assertEqual(prefs.get(s, "workers"), prefs.MAX_WORKERS)
        self.assertEqual(prefs.get(s, "preview_delay_ms"), 450)
        self.assertIs(prefs.get(s, "confirm_quit"), True)
        self.assertEqual(prefs.get(s, "ui_scale"), prefs.UI_SCALES[-1])
        self.assertEqual(prefs.get(s, "theme_start"), "light")
        s.data["theme"] = "dark"
        prefs.reset(s)
        self.assertEqual(prefs.get(s, "theme_start"), "last")
        self.assertEqual(s.data, {"theme": "dark"})                 # state is not an option: it stays

    def test_plan_overwrites_asks_or_keeps_both(self):
        d = self.folder("plan")
        old, new = d / "a_out.svg", d / "b_out.svg"
        old.write_text("old")
        paths = {"a": old, "b": new}
        for policy in ("overwrite", "ask"):
            plan, exists = prefs.plan(paths, policy)
            self.assertEqual((plan, exists), (paths, [old]))
        plan, exists = prefs.plan(paths, "suffix", "20261103-142530")
        self.assertEqual(exists, [])
        self.assertEqual(plan["a"].name, "a_out_20261103-142530.svg")
        self.assertEqual(plan["b"], new)                             # a free name is not stamped
        plan["a"].write_text("")
        self.assertEqual(prefs.stamped(old, "20261103-142530").name, "a_out_20261103-142530-2.svg")
        self.assertRegex(prefs.stamp(), r"^\d{8}-\d{6}$")

    def test_export_and_import_move_options_and_tool_settings(self):
        a = AppSettings(self.folder("cfg-a"))
        a.data.update(on_existing="suffix", workers=2, geometry="00ff", blender_exe="somewhere/blender")
        (a.folder / "tools").mkdir(exist_ok=True)
        (a.folder / "tools" / "svg-overlap-cleaner.json").write_text('{"out_mode": "folder"}', encoding="utf-8")
        bundle = self.tmp / "settings.json"
        self.assertEqual(prefs.export_settings(a, bundle), 1)
        b = AppSettings(self.folder("cfg-b"))
        b.data["geometry"] = "keep"
        self.assertEqual(prefs.import_settings(b, bundle), 1)
        b = AppSettings(b.folder)                                    # saved at once
        self.assertEqual((prefs.get(b, "on_existing"), prefs.get(b, "workers")), ("suffix", 2))
        self.assertEqual(b.get("geometry"), "keep")                  # this computer's own entries stay
        self.assertIsNone(b.get("blender_exe"))
        self.assertIn("folder", (b.folder / "tools" / "svg-overlap-cleaner.json").read_text(encoding="utf-8"))
        junk = self.tmp / "junk.json"
        junk.write_text('{"theme": "light"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            prefs.import_settings(b, junk)

    def test_command_line_suffix_keeps_the_existing_output(self):
        from atlas_toolkit.core.cli import main
        d = self.folder("cli")
        src = samples.svg(d)
        out = src.with_name("sample--nooverlap.svg")
        out.write_text("old")
        self.assertEqual(main(["run", "svg-overlap-cleaner", str(src), "-q"]), 0)      # skipped
        self.assertEqual(out.read_text(), "old")
        self.assertEqual(main(["run", "svg-overlap-cleaner", str(src), "-q", "--suffix"]), 0)
        self.assertEqual(out.read_text(), "old")
        made = list(d.glob("sample--nooverlap_*.svg"))
        self.assertEqual(len(made), 1)
        self.assertIn(b"<desc>svg-overlap-cleaner</desc>", made[0].read_bytes()[:600])

    def test_workflow_stamp_names_every_save(self):
        d = self.folder("wf")
        src = samples.svg(d)
        wf = W.Workflow(name="Clean")
        inp = wf.add(W.input_node("svg"))
        clean = wf.add(W.tool_node(self.reg.get("svg-overlap-cleaner")))
        save = wf.add(W.save_node())
        wf.connect(inp.id, clean.id)
        wf.connect(clean.id, save.id)
        res = W.run_workflow(wf, src, lambda *_: None, lambda: False, reg=self.reg,
                             cache=W.WorkflowCache(self.folder("wf-cache")), stamp="20261103-142530")
        self.assertTrue(res.outputs)
        self.assertTrue(all(Path(p).stem.endswith("_20261103-142530") for p in res.outputs))

    def test_process_lane_runs_jobs_side_by_side(self):
        from atlas_toolkit.core.job import Job
        from atlas_toolkit.core.runner import Runner
        d = self.folder("lane")
        tool = self.reg.get("svg-overlap-cleaner")
        srcs = [samples.svg(d, f"s{i}.svg") for i in range(3)]
        events, done = [], threading.Event()

        def on_event(jid, kind, payload):
            events.append((jid, kind))
            if sum(k in ("done", "error") for _j, k in events) == len(srcs):
                done.set()
        runner = Runner(on_event, workers=2)
        self.addCleanup(runner.shutdown)
        self.assertEqual(len(runner.process.workers), 2)
        for s in srcs:
            runner.submit(Job(tool.id, "run", str(s), str(s.with_name(s.stem + "_out.svg")), tool.defaults()),
                          "process")
        self.assertTrue(done.wait(240))
        self.assertEqual([k for _j, k in events].count("done"), len(srcs))
        self.assertTrue(all(s.with_name(s.stem + "_out.svg").exists() for s in srcs))
        runner.process.set_workers(1)                                 # the extra one stops once it is idle
        self.assertEqual(len([w for w in runner.process.workers if not w.retire]), 1)
        deadline = time.monotonic() + 30             # "done" arrives just before the worker's own "idle"
        while runner.busy() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(runner.busy())


if __name__ == "__main__":
    unittest.main()
