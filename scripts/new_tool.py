#!/usr/bin/env python3
"""
new_tool.py - Start a new tool from a template. It is registered automatically (no list to edit anywhere).

    python scripts/new_tool.py path_simplifier --kind svg  --name "Path Simplifier"
    python scripts/new_tool.py mesh_twister    --kind mesh --name "Mesh Twister"
    python scripts/new_tool.py relief_lines    --kind mesh2svg --name "Relief Lines"
    python scripts/new_tool.py my_tool --kind svg --plugin "C:/somewhere/plugins"     a plug-in, outside the app

Writes <folder>/__init__.py (the spec), <folder>/<folder>.py (the algorithm, no GUI) and engine.py (the adapter
the app calls), plus tests/test_<folder>.py. Then: fill in the algorithm, run the tests (the contract tests already
cover the new tool), look at it in the app, rebuild. See docs/ADDING_A_TOOL.md.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "src" / "atlas_toolkit" / "tools"

SPEC = '''"""
{name}: {summary}

Outputs <name>{suffix}, signed {signature_doc} so Atlas links them to this tool.
"""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool

TOOL = Tool(
    id="{tool_id}",
    name="{name}",
    version="0.1",
    category="{category}",
    icon="{icon}",
    action="Run",
    summary="{summary}",
    inputs=Inputs("{in_kind}"),
    output=Output("{{stem}}{suffix}", "{out_kind}"{formats}),
    signature="{signature}",
    engine=".engine:run",
    runs_in="{runs_in}",
    preview="{out_kind}",
    params=[
        Section(
            "Settings",
            Float("amount", "Amount", 1.0, unit="mm", min=0.0, max=100.0, step=0.5, decimals=2,
                  help="What this changes, in one sentence."),
            Bool("option", "An option", False, help="What turning it on does."),
        ),
    ],
)
'''

ALGO_SVG = '''"""
{folder}.py - {summary}

Pure algorithm, no GUI: it edits a parsed SVG in place. The shared SVG core (geom/svg.py) reads paths with their
transforms in document order, in any units.
"""
from __future__ import annotations

from dataclasses import dataclass

from atlas_toolkit.geom.svg import collect, mm_per_unit


@dataclass
class Stats:
    elements: int = 0
    changed: int = 0


def process(tree, amount: float, option: bool, progress=None) -> Stats:
    root = tree.getroot()
    mpu = mm_per_unit(root)            # mm per user unit: tolerances in mm / mpu = user units
    items, _ = collect(root)
    st = Stats(elements=len(items))
    for n, it in enumerate(items):
        if progress:
            progress(n, len(items))
        # it.el is the element, it.ctm its transform to the root, it.pen its (layer, stroke) pen
    return st
'''

ENGINE_SVG = '''"""Toolkit adapter for {folder}: parameters in, signed SVG and a before/after preview out."""
from __future__ import annotations

from pathlib import Path

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom.svg import load, read_drawing, sign_desc, write_run_metadata, write_tree

from . import TOOL
from . import {folder} as algo


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    tree = load(src)
    before = read_drawing(tree)
    st = algo.process(tree, p.amount, p.option, progress=lambda i, n: ctx.progress(i / max(n, 1)))
    after = read_drawing(tree)
    outputs = []
    if dst is not None:
        sign_desc(tree.getroot(), TOOL.id)
        write_run_metadata(tree.getroot(), TOOL, p)
        write_tree(tree, dst)
        outputs.append(str(dst))
    return Result(outputs=outputs,
                  stats=[Stat("changed", "Changed", st.changed, before=st.elements)],
                  report=f"{{src.name}}\\n  elements   {{st.elements}}, {{st.changed}} changed",
                  preview={{"views": [{{"title": "Before", "kind": "svg", "drawing": before}},
                                     {{"title": "After", "kind": "svg", "drawing": after}}], "prefer": "After"}})
'''

ALGO_MESH = '''"""
{folder}.py - {summary}

Pure algorithm, no GUI: a trimesh mesh in, a new mesh out (model units).
"""
from __future__ import annotations

import numpy as np


def process(mesh, amount: float, option: bool, progress=None):
    out = mesh.copy()
    if progress:
        progress(0.5, "working")
    return out
'''

ENGINE_MESH = '''"""Toolkit adapter for {folder}: load, process, save signed, preview in 3D."""
from __future__ import annotations

from pathlib import Path

from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import {folder} as algo


def run(ctx, src: Path, dst: Path | None, p):
    m = gm.load_mesh(src)
    out = algo.process(m.copy(), p.amount, p.option, progress=ctx.progress)
    return gm.result(ctx, TOOL, m, out, dst, [], [src.name])
'''

ALGO_MESH2SVG = '''"""
{folder}.py - {summary}

Pure algorithm, no GUI: a mesh in, polylines in paper millimetres out (see stl_hatch_maker/scene.py for a camera,
visibility and paper layout you can reuse).
"""
from __future__ import annotations

import numpy as np


def draw(mesh, amount: float, option: bool, progress=None) -> list:
    v = mesh.vertices
    lo, hi = v.min(axis=0), v.max(axis=0)
    s = 200.0 / max(float((hi - lo)[:2].max()), 1e-9)
    return [[(20 + (x - lo[0]) * s, 20 + (hi[1] - y) * s) for x, y in v[f][:, :2]] for f in mesh.faces[:200]]
'''

ENGINE_MESH2SVG = '''"""Toolkit adapter for {folder}: mesh in, signed plotter SVG out."""
from __future__ import annotations

from pathlib import Path

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.drawing import Drawing, Pen
from atlas_toolkit.geom.svgout import Layer, write_svg

from . import TOOL
from . import {folder} as algo


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    m = gm.load_mesh(src)
    lines = algo.draw(m, p.amount, p.option, progress=ctx.progress)
    W, H = 297.0, 420.0
    outputs = []
    if dst is not None:
        write_svg(dst, W, H, [Layer("lines", lines)], TOOL, dict(p))
        outputs.append(str(dst))
    d = Drawing(W, H, (0, 0, W, H), [Pen.from_polylines("lines", "lines", lines)])
    return Result(outputs=outputs, stats=[Stat("strokes", "Strokes", len(lines))], report=src.name,
                  preview={{"views": [{{"title": "Drawing", "kind": "svg", "drawing": d}}], "prefer": "Drawing"}})
'''

TEST = '''"""Behaviour tests for {name} (the contract tests in test_contract.py already run it on a sample)."""
import _isolate  # first: settings and caches stay in a temporary folder
import unittest

from atlas_toolkit.core.registry import registry


class {cls}Tests(unittest.TestCase):
    def test_registered(self):
        self.assertIn("{tool_id}", registry().tools)


if __name__ == "__main__":
    unittest.main()
'''

KINDS = {
    "svg": dict(in_kind="svg", out_kind="svg", category="svg", icon="vector", suffix="_out.svg", runs_in="thread",
                algo=ALGO_SVG, engine=ENGINE_SVG, formats=""),
    "mesh": dict(in_kind="mesh", out_kind="mesh", category="mesh", icon="cube", suffix="_out.stl", runs_in="process",
                 algo=ALGO_MESH, engine=ENGINE_MESH, formats=', formats=(".stl", ".obj", ".ply")'),
    "mesh2svg": dict(in_kind="mesh", out_kind="svg", category="mesh2svg", icon="hatch", suffix="_lines.svg",
                     runs_in="process", algo=ALGO_MESH2SVG, engine=ENGINE_MESH2SVG, formats=""),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="snake_case folder name, e.g. path_simplifier (also the Atlas code unit)")
    ap.add_argument("--kind", choices=KINDS, default="svg")
    ap.add_argument("--name", help="display name (default: from the folder)")
    ap.add_argument("--summary", default="One sentence on what this tool does.")
    ap.add_argument("--plugin", help="write a plug-in into this folder instead of the built-in tools")
    a = ap.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", a.folder):
        sys.exit("the folder must be snake_case: letters, digits and _")
    k = KINDS[a.kind]
    tool_id = a.folder.replace("_", "-")
    name = a.name or a.folder.replace("_", " ").title()
    signature = f"<desc>{tool_id}</desc>" if k["out_kind"] == "svg" else f"atlas-toolkit:{tool_id}"
    base = Path(a.plugin) if a.plugin else TOOLS
    folder = base / a.folder
    if folder.exists():
        sys.exit(f"{folder} already exists")
    folder.mkdir(parents=True)
    fill = dict(folder=a.folder, name=name, summary=a.summary, tool_id=tool_id, signature=signature,
                signature_doc=signature, cls="".join(w.title() for w in a.folder.split("_")), **k)
    (folder / "__init__.py").write_text(SPEC.format(**fill), encoding="utf-8")
    (folder / f"{a.folder}.py").write_text(k["algo"].format(**fill), encoding="utf-8")
    (folder / "engine.py").write_text(k["engine"].format(**fill), encoding="utf-8")
    if not a.plugin:
        test = ROOT / "tests" / f"test_{a.folder}.py"
        test.write_text(TEST.format(**fill), encoding="utf-8")
    print(f"created {folder}")
    print("next: write the algorithm, then  .venv\\Scripts\\python -m unittest discover -s tests"
          f"   and   .venv\\Scripts\\python -m atlas_toolkit --tool {tool_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
