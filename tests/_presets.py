"""
The private preset packs, for tests that check real presets (studio looks, "every preset runs").

The app ships with no presets: the official packs' sources live in the private AtlasToolkit-presets repository, found
through `[paths] presets` in dev.local.toml or ATLAS_PRESETS_DIR (src/atlas_toolkit/devpaths.py). Without it these
tests skip, saying why; where CI is given the repository, ATLAS_TEST_REQUIRE_PRESETS=1 turns a missing one into an
error instead of a quiet skip. Import _isolate before this module.
"""
from __future__ import annotations

import os
import unittest
from functools import lru_cache

from atlas_toolkit import devpaths
from atlas_toolkit.core import presets as P

WHY = "needs the private preset packs (set presets in dev.local.toml or ATLAS_PRESETS_DIR)"
REQUIRE = os.environ.get("ATLAS_TEST_REQUIRE_PRESETS") == "1"


@lru_cache(maxsize=None)
def _packs() -> tuple:
    return tuple(P.read_pack(folder) for folder in devpaths.preset_packs())


def available() -> bool:
    found = bool(_packs())
    if REQUIRE and not found:
        raise RuntimeError(f"{WHY}, and ATLAS_TEST_REQUIRE_PRESETS is set")
    return found


def needs_packs(obj):
    """Decorate a test (or class) that needs the private packs."""
    return unittest.skipUnless(available(), WHY)(obj)


def pack_presets(tool) -> list:
    """Every preset the private packs hold for `tool` (a Tool or a tool id), one per uid, in pack order."""
    tool_id = getattr(tool, "id", tool)
    seen, out = set(), []
    for pack in _packs():
        for p in pack.presets:
            if p.tool == tool_id and p.uid not in seen:
                seen.add(p.uid)
                out.append(p)
    return out


def names(tool) -> list[str]:
    return [p.name for p in pack_presets(tool)]


def preset(tool, name: str):
    """The pack preset `name` for `tool`; the calling test skips when the packs are missing or lack it."""
    if not available():
        raise unittest.SkipTest(WHY)
    found = next((p for p in pack_presets(tool) if p.name == name), None)
    if found is None:
        raise unittest.SkipTest(f"the private packs have no preset {name!r} for {getattr(tool, 'id', tool)}")
    return found


def values(tool, name: str, current: dict | None = None, **overrides):
    """The values the pack preset `name` gives `tool` (what picking it in the drop-down does), with overrides."""
    return run_values(tool, preset(tool, name), current, **overrides)


def values_or_defaults(tool, name: str, **overrides):
    """The pack preset's values when the packs are here, otherwise the tool's defaults (for a test that only needs a
    sensible drawing, not that look)."""
    if available() and any(p.name == name for p in pack_presets(tool)):
        return values(tool, name, **overrides)
    return tool.values(dict(tool.defaults(), **overrides))


def each(tool) -> list:
    """The pack presets of `tool`, for a test that runs every one; skips without the packs (or without any)."""
    if not available():
        raise unittest.SkipTest(WHY)
    found = pack_presets(tool)
    if not found:
        raise unittest.SkipTest(f"the private packs have no presets for {getattr(tool, 'id', tool)}")
    return found


def optional(tool) -> list:
    """The pack presets of `tool` when the packs are here, else [] (for tests that also run without them)."""
    return pack_presets(tool) if available() else []


def run_values(tool, p, current: dict | None = None, **overrides):
    """What applying preset `p` gives `tool`, with `overrides` on top, validated."""
    v = dict(P.apply(tool, p, current)[0])
    v.update(overrides)
    return tool.values(v)


def raw(p) -> dict:
    """The keys preset `p`'s author set on purpose (what the preset was written as), in JSON form."""
    return {k: p.values[k] for k in p.explicit if k in p.values}
