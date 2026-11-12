"""
The tool contract, checked for every registered tool: a new tool gets these tests for free.

- the spec is valid (ids, categories, presets refer to real parameters, defaults within range);
- the parameter schema round-trips through JSON (saved settings) and through the generated CLI;
- the engine runs on a sample input, writes its output under the template name, and signs it (skipped, with the
  reason, for a tool whose external runtime such as Blender is not installed);
- the signature literal appears in the tool's own source folder (Atlas reads it there to link outputs to code).
"""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from atlas_toolkit.core.cli import build_parser
from atlas_toolkit.core.io import output_path
from atlas_toolkit.core.params import AUTO, Choice, Float, Int, Vec3
from atlas_toolkit.core.registry import registry, validate
from atlas_toolkit.core.runtimes import missing
from atlas_toolkit.core.selftest import make_sample, run_tool, signed

REG = registry()


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="atlas_contract_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_tool_loads(self):
        self.assertFalse(REG.broken, "\n".join(b.error for b in REG.broken))
        self.assertTrue(REG.tools)

    def test_presets_are_data_not_code(self):
        """Presets live in .atlaspreset / .atlaspack files (core/presets.py), never in a tool spec, and the app
        ships none: a preset file under src/ would be bundled by collect_data_files."""
        import dataclasses
        import re
        import atlas_toolkit
        from atlas_toolkit.core.tool import Tool
        self.assertFalse({"presets", "preset_tips", "default_preset"} & {f.name for f in dataclasses.fields(Tool)})
        root = Path(atlas_toolkit.__file__).resolve().parent
        pattern = re.compile(r"\b(presets|preset_tips|default_preset)\s*=")
        for f in (root / "tools").rglob("*.py"):
            self.assertIsNone(pattern.search(f.read_text(encoding="utf-8")), f"{f.relative_to(root)} defines presets")
        shipped = [p.relative_to(root) for p in root.rglob("*") if p.suffix.lower() in (".atlaspreset", ".atlaspack")]
        self.assertEqual(shipped, [])

    def test_specs_are_valid(self):
        for t in REG.ordered():
            with self.subTest(tool=t.id):
                self.assertEqual(validate(t), [])
                self.assertTrue(t.summary and t.name and t.version)
                self.assertIn("{stem}", t.output.template)

    def test_tool_icons_are_unique_and_exist(self):
        from atlas_toolkit.ui.theme import icon_paths
        seen = {}
        for t in REG.ordered():
            with self.subTest(tool=t.id):
                self.assertIn(t.icon, icon_paths())
                self.assertNotIn(t.icon, seen, f"{t.id} shares the icon of {seen.get(t.icon)}")
                seen[t.icon] = t.id

    def test_optional_runtimes_are_known_and_explained(self):
        import dataclasses
        from atlas_toolkit.core.runtimes import DEPTH_PACKAGES, RUNTIMES
        for t in registry().ordered():
            for key, what in t.optional:
                self.assertIn(key, RUNTIMES, t.id)
                self.assertTrue(what.strip(), t.id)
        for key, rt in RUNTIMES.items():             # the About window's Setup tab explains every runtime
            self.assertTrue(rt.why and rt.steps and rt.url and rt.setting and rt.file_filter, key)
        bad = dataclasses.replace(registry().ordered()[0], optional=(("nope", "x"),))
        self.assertTrue(any("optional" in e for e in validate(bad)))
        setup = (Path(__file__).resolve().parents[1] / "scripts" / "setup_ml.py").read_text(encoding="utf-8")
        self.assertIn(f"PACKAGES = {DEPTH_PACKAGES!r}".replace("'", '"'), setup)   # the same packages

    def test_defaults_are_within_range(self):
        for t in REG.ordered():
            for p in t.param_list:
                with self.subTest(tool=t.id, param=p.key):
                    v = p.validate(None)
                    if isinstance(p, (Float, Int)) and v != AUTO:
                        self.assertLessEqual(p.min, v)
                        self.assertLessEqual(v, p.max)
                    if isinstance(p, Choice) and not p.options_from:
                        self.assertIn(v, [x for x, _ in p.pairs()])
                    if isinstance(p, Vec3) and v != AUTO:
                        self.assertEqual(len(v), 3)

    def test_visibility_rules_survive_an_unpicked_choice(self):
        # a segmented choice with nothing picked reads None while a page is built (Holes 1.0 crashed on len(None))
        # (Holes 1.0 crashed on len(None)); rules short-circuit, so each unpicked choice is tried from the defaults and
        # every preset (real combinations: tubes on a lattice) and against every option of every other choice
        for t in REG.ordered():
            choices = [p for p in t.param_list if isinstance(p, Choice)]
            trials = []
            for base in [t.defaults()] + [_presets.run_values(t, p) for p in _presets.optional(t)]:
                trials.append(dict(base, **{c.key: None for c in choices}))
                for c in choices:
                    trials.append(dict(base, **{c.key: None}))
                    for other in choices:
                        if other is not c and not other.options_from:
                            trials += [dict(base, **{other.key: o, c.key: None}) for o, _ in other.pairs()]
            for p in t.param_list:
                with self.subTest(tool=t.id, param=p.key):
                    for values in trials:
                        p.visible(values)

    def test_values_round_trip_through_json(self):
        for t in REG.ordered():
            with self.subTest(tool=t.id):
                vals = t.defaults()
                saved = json.loads(json.dumps({p.key: p.to_json(vals[p.key]) for p in t.param_list}))
                self.assertEqual(t.values(saved), vals)

    def test_cli_accepts_every_parameter(self):
        ap = build_parser()
        for t in REG.ordered():
            with self.subTest(tool=t.id):
                args = ["run", t.id, "x"]
                for p in t.param_list:
                    v = p.validate(None)
                    if isinstance(v, bool):
                        args.append(p.flag if v else "--no-" + p.flag[2:])
                    elif isinstance(v, tuple):
                        args += [p.flag, *map(str, v)]
                    else:
                        args += [p.flag, str(v)]
                ns = ap.parse_args(args)
                # Explicit flags override the selected default preset, which may differ from raw defaults.
                expected = t.values({p.key: p.validate(None) for p in t.param_list})
                self.assertEqual(t.values({p.key: getattr(ns, p.key) for p in t.param_list}), expected)
                bare = ap.parse_args(["run", t.id, "x"])
                self.assertEqual(t.values({p.key: getattr(bare, p.key) for p in t.param_list}), t.defaults())

    def test_signature_literal_is_in_the_tool_source(self):
        for t in REG.ordered():
            with self.subTest(tool=t.id):
                self.assertTrue(t.signature, "tools must sign their outputs so Atlas can link them")
                folder = Path(__import__(t.package, fromlist=["x"]).__file__).parent
                text = "".join(f.read_text(encoding="utf-8") for f in folder.glob("*.py"))
                self.assertIn(t.signature, text)

    def test_engine_runs_on_a_sample_and_signs_the_output(self):
        for t in REG.ordered():
            with self.subTest(tool=t.id):
                lacking = missing(t)
                if lacking:              # an external program (Blender ...) is not installed on this machine
                    if _isolate.REQUIRE_HELPERS and any(r.key in _isolate.HELPERS for r in lacking):
                        self.fail(f"{t.id} lacks a native helper, and ATLAS_TEST_REQUIRE_HELPERS is set")
                    self.skipTest(f"{t.id} needs {', '.join(r.label for r in lacking)}")
                folder = self.tmp / t.id
                folder.mkdir(exist_ok=True)
                src = make_sample(t, folder)     # named now: outputs land beside it, in an order that depends on the disk
                ok, msg = run_tool(t, folder)
                self.assertTrue(ok, msg)
                dst = output_path(t, src, t.values(t.test_values))
                self.assertTrue(dst.exists(), f"expected {dst.name}")
                self.assertTrue(signed(t, dst))
                for variant in t.test_variants:
                    values = t.values({**t.test_values, **variant})
                    if missing(t, values) and not _isolate.REQUIRE_HELPERS:
                        continue
                    ok, msg = run_tool(t, folder, variant=variant)
                    self.assertTrue(ok, f"{variant}: {msg}")


if __name__ == "__main__":
    unittest.main()
