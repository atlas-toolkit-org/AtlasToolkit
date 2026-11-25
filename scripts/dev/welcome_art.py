#!/usr/bin/env python3
r"""
welcome_art.py - Draw the plate of the welcome window (ui/welcome.py) with the app itself, from a model of the catalogue.

    .venv\Scripts\python scripts\dev\welcome_art.py             writes build/qa/welcome/welcome.svg to look at
    .venv\Scripts\python scripts\dev\welcome_art.py --publish   also src/atlas_toolkit/ui/theme/welcome.svg

Every X.Y version gets a new plate that shows what changed in it (CLAUDE.md): edit RECIPE (the model, the tools and
their settings, the caption), run this, look at it in the window, then --publish. Keep the contour style: Contour Lines
draws last. The model must be one the app ships (catalogue `kind: bundled`) under CC0 or public domain, so the plate
needs no credit; settings are typed here, never copied from a paid preset. tests/test_welcome.py checks that the plate's
series is this version's X.Y.

The published file is the drawing cropped to its strokes, in one path, with the colour left to the window (currentColor)
and the caption and series in data- attributes the window reads.
"""
from __future__ import annotations

import argparse
import gzip
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from atlas_toolkit import devpaths  # noqa: E402
from atlas_toolkit.version import __version__  # noqa: E402

CATALOGUE = REPO / "src" / "atlas_toolkit" / "catalogue"
TARGET = REPO / "src" / "atlas_toolkit" / "ui" / "theme" / "welcome.svg"
FREE = ("CC0-1.0", "LicenseRef-public-domain")

RECIPE = {
    "series": "2.5",                       # the X.Y it was drawn for
    "model": "nefertiti",                  # a catalogue id
    "steps": [                             # (tool, settings), each run on the previous step's output
        ("contour-lines", ["--theta", "78", "--psi", "145", "--radius", "195", "--spacing-mm", "2.2",
                           "--min-len", "2", "--orientation", "portrait"]),
    ],
    "title": "Bust of Nefertiti",
    "caption": "Mesh, drawn in contour lines",
}
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def run(qa: Path, tool: str, src: Path, args: list) -> Path:
    """One step through the command line, as a person would; returns its output."""
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "ATLAS_TOOLKIT_HOME": str(qa / "home")}
    done = subprocess.run([sys.executable, "-m", "atlas_toolkit", "run", tool, str(src), *args, "--out-dir", str(qa),
                           "--force", "-q"], check=True, env=env, capture_output=True, text=True, encoding="utf-8")
    outs = [line.split("->", 1)[1].strip() for line in done.stdout.splitlines() if line.strip().startswith("->")]
    if not outs:
        raise SystemExit(f"{tool} wrote nothing:\n{done.stdout}\n{done.stderr}")
    return Path(outs[0])


def crop(svg: str, recipe: dict) -> str:
    """The drawing's strokes alone, their bounds at the origin, to a tenth of a millimetre."""
    lines = []
    for d in re.findall(r'<path[^>]*\sd="([^"]+)"', svg):
        if set(re.findall(r"[A-Za-z]", d)) - set("ML"):
            raise SystemExit("the drawing has more than straight strokes: teach crop() about them")
        for stroke in d.split("M")[1:]:
            n = [float(v) for v in NUMBER.findall(stroke)]
            lines.append(list(zip(n[::2], n[1::2])))
    xs, ys = [x for s in lines for x, _y in s], [y for s in lines for _x, y in s]
    x0, y0, pad = min(xs), min(ys), 1.0
    w, h = max(xs) - x0 + 2 * pad, max(ys) - y0 + 2 * pad
    path = "".join("M" + " ".join(f"{x - x0 + pad:.1f},{y - y0 + pad:.1f}" for x, y in s) for s in lines)
    attr = lambda v: html.escape(v, quote=True)    # noqa: E731
    tools = " → ".join(tool for tool, _args in recipe["steps"])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.1f} {h:.1f}" data-series="{recipe["series"]}" '
            f'data-title="{attr(recipe["title"])}" data-caption="{attr(recipe["caption"])}">\n'
            f"<!-- {recipe['model']} from the catalogue, through {tools}: scripts/dev/welcome_art.py -->\n"
            f"<desc>{recipe['steps'][-1][0]}</desc>\n"
            f'<path fill="none" stroke="currentColor" stroke-width="0.38" stroke-linecap="round" stroke-linejoin="round" '
            f'd="{path}"/>\n</svg>\n')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()
    series = ".".join(__version__.split(".")[:2])
    if RECIPE["series"] != series:
        raise SystemExit(f"RECIPE is for {RECIPE['series']}, the app is {series}: draw a plate for this version")
    model = next(m for m in json.loads((CATALOGUE / "index.json").read_text(encoding="utf-8"))["models"]
                 if m["id"] == RECIPE["model"])
    if model.get("kind") != "bundled" or model["license"] not in FREE:
        raise SystemExit(f"{model['id']}: the plate needs a model the app ships, under {' or '.join(FREE)}")
    qa = devpaths.qa_dir("welcome")
    out = qa / f"{model['id']}.ply"
    out.write_bytes(gzip.decompress((CATALOGUE / "models" / model["file"]).read_bytes()))
    for tool, args in RECIPE["steps"]:
        out = run(qa, tool, out, args)
    if RECIPE["steps"][-1][0] != "contour-lines":
        raise SystemExit("the plate keeps the contour style: Contour Lines draws last")
    svg = crop(out.read_text(encoding="utf-8"), RECIPE)
    for target in (qa / "welcome.svg", *([TARGET] if a.publish else [])):
        target.write_text(svg, encoding="utf-8", newline="\n")
        print(f"{target}: {len(svg) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
