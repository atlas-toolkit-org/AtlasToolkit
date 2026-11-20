"""
The private preset packs (tests/_presets.py): every preset still fits its tool and stores no file paths. When the
presets moved out of the tool specs (Atlas Toolkit 2.1), a one-off check proved the packs give exactly what the specs
gave, for all 357 presets. Skips without the private repository.
"""
import _isolate  # noqa: F401
import importlib.util
import sys
import unittest
from pathlib import Path

import _presets
from atlas_toolkit.core import presets as P
from atlas_toolkit.core.params import FileRef
from atlas_toolkit.core.registry import registry


@_presets.needs_packs
class PrivatePacks(unittest.TestCase):
    def setUp(self):
        self.reg = registry()

    def test_every_preset_fits_its_tool(self):
        n = 0
        for tool in self.reg.ordered():
            for p in _presets.pack_presets(tool):
                values, problems = P.apply(tool, p)
                self.assertEqual(problems, [], f"{tool.id} / {p.name}")
                self.assertTrue(p.uid.startswith(f"{p.source}/{tool.id}/"), p.uid)
                n += 1
        tools = {p.tool for pk in _presets._packs() for p in pk.presets}
        self.assertTrue(tools <= set(self.reg.tools), tools - set(self.reg.tools))
        self.assertGreater(n, 300)

    def test_every_tool_has_presets(self):
        """A new tool needs its presets before the next pack (Repair Mesh shipped in 2.4.1 without any); the pack
        tool's `check` holds the exact counts per tier."""
        have = {p.tool for pk in _presets._packs() for p in pk.presets}
        missing = [t.id for t in self.reg.ordered() if t.id not in have]
        self.assertEqual(missing, [], "tools with no preset in the packs")

    def test_presets_use_current_names(self):
        """Renamed or dropped keys and choices still load through `former` / `former_values`, but the official packs
        are kept current: a preset that leans on them (Motion Sculpture's Painted area, gone in 0.5.0) is redone."""
        stale = []
        for tool in self.reg.ordered():
            params = {q.key: q for q in tool.param_list}
            formers = {old: q.key for q in tool.param_list for old in q.former}
            for p in _presets.pack_presets(tool):
                for k, v in p.values.items():
                    if k in formers:
                        stale.append(f"{tool.id} / {p.name}: {k} is now {formers[k]}")
                    q = params.get(k)
                    if q is not None and isinstance(v, str) and v in q.former_values:
                        stale.append(f"{tool.id} / {p.name}: {k}={v!r} is now {q.former_values[v]!r}")
        self.assertEqual(stale, [], "\n".join(stale))

    def test_no_preset_paints_an_area(self):
        """A painted area is a face mask of one model: an official preset never stores one or selects Paint
        (Crumble's Area, Stair Sculpt's Opening, Surface Eject's Extract from). Band and Sphere regions are % of the
        model's size and carry over."""
        painted = []
        for tool in self.reg.ordered():
            for p in _presets.pack_presets(tool):
                if p.values.get("selection") or "paint" in p.values.values():
                    painted.append(f"{tool.id} / {p.name}")
        self.assertEqual(painted, [], "presets that use a painted area")

    def test_packs_store_no_paths(self):
        for tool in self.reg.ordered():
            files = {p.key for p in tool.param_list if isinstance(p, FileRef)}
            for p in _presets.pack_presets(tool):
                self.assertFalse(files & set(p.values), f"{tool.id} / {p.name}")


    def test_no_paid_preset_leaks_into_the_repository(self):
        """Paid presets are sold in packs: none of their values may sit in a tracked file (scripts/dev/preset_leak_check.py)."""
        path = Path(__file__).resolve().parents[1] / "scripts" / "dev" / "preset_leak_check.py"
        spec = importlib.util.spec_from_file_location("preset_leak_check", path)
        check = sys.modules.setdefault(spec.name, importlib.util.module_from_spec(spec))
        spec.loader.exec_module(check)
        looks = check.looks(_presets._packs(), self.reg)
        self.assertGreater(len(looks), 0)
        leaks = [f.text() for f in check.scan(looks) if f.kind == "LEAK"]
        self.assertEqual(leaks, [], "paid preset values in the repository:\n" + "\n".join(leaks))


if __name__ == "__main__":
    unittest.main()
