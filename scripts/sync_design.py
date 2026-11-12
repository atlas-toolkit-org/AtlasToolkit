#!/usr/bin/env python3
"""
sync_design.py - Copy the studio design system into the toolkit, so the app is self-contained and portable.

Reads  studio/design/tokens.json  (source of truth) and  studio/design/icons.js
Writes src/atlas_toolkit/ui/theme/tokens.json  (themes with every {ref} resolved, plus type/space/radius scales)
       src/atlas_toolkit/ui/theme/icons.json   (icon name -> SVG fragment on the 24x24 grid)

Run it after the design system changes:  python scripts/sync_design.py [path/to/studio/design]
Without a path it uses ATLAS_DESIGN_DIR, then dev.local.toml (design, or <workspace>/studio/design).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
THEME = HERE.parent / "src" / "atlas_toolkit" / "ui" / "theme"
sys.path.insert(0, str(HERE.parent / "src"))
from atlas_toolkit.devpaths import design_dir  # noqa: E402  (standard library only)
REF = re.compile(r"\{([\w-]+)\.([\w-]+)\}")


def resolve(value, prims: dict):
    if isinstance(value, str):
        return REF.sub(lambda m: prims[m.group(1)][m.group(2)], value)
    return value


def main() -> int:
    design = Path(sys.argv[1]) if len(sys.argv) > 1 else design_dir()
    if design is None:
        sys.exit("where is the studio design system? pass its folder, or set it in dev.local.toml")
    tokens = json.loads((design / "tokens.json").read_text(encoding="utf-8"))
    prims = tokens["primitives"]
    out = {
        "$meta": {**tokens.get("$meta", {}), "synced_from": "studio/design"},
        "primitives": prims,
        "themes": {name: {k: resolve(v, prims) for k, v in theme.items()} for name, theme in tokens["themes"].items()},
    }
    for key in ("font", "size", "space", "radius", "motion", "layout"):
        out[key] = tokens.get(key, {})
    THEME.mkdir(parents=True, exist_ok=True)
    (THEME / "tokens.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    js = (design / "icons.js").read_text(encoding="utf-8")
    icons = dict(re.findall(r"^\s*([\w-]+):\s*'([^']*)'", js, re.M))
    (THEME / "icons.json").write_text(json.dumps(icons, indent=1) + "\n", encoding="utf-8")
    print(f"tokens: {len(out['themes'])} themes; icons: {len(icons)}  ->  {THEME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
