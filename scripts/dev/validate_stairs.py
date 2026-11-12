"""Stair Sculpt on real models: every preset, with timings and solid checks, as clay renders on a contact sheet.

    python scripts/dev/validate_stairs.py [--models david,venus] [--presets "Double Helix,Ziggurat"] [--no-render]

Writes to build/qa/stairs.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw  # noqa: E402

from atlas_toolkit.devpaths import preset_names, preset_raw  # noqa: E402
from atlas_toolkit import devpaths  # noqa: E402
from atlas_toolkit.core.job import JobContext  # noqa: E402
from atlas_toolkit.geom import mesh as gm  # noqa: E402
from atlas_toolkit.tools.mesh_stairs import TOOL  # noqa: E402
from atlas_toolkit.tools.mesh_stairs import engine  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import readme_gallery as gallery  # noqa: E402

QA = devpaths.qa_dir("stairs")
MODELS = {"david": gallery.DAVID, "venus": gallery.VENUS}


def one(model: str, name: str, extra: dict) -> tuple:
    src = gallery.sample(MODELS[model])
    values = dict(TOOL.defaults())
    values.update(preset_raw(TOOL, name))
    values.update(extra)
    dst = QA / f"{model}_{name.replace(' ', '_')}.stl"
    logs = []
    ctx = JobContext(lambda *a: None, lambda: False, "run")
    ctx.log = logs.append
    ctx.warn = logs.append
    started = time.perf_counter()
    result = engine.run(ctx, src, dst, TOOL.values(values))
    mesh = gm.load_mesh(dst)
    return dst, (f"{model:6s} {name:22s} {time.perf_counter() - started:6.1f}s  faces {len(mesh.faces):8d}  "
                 f"closed {mesh.is_volume}  pieces {result.data['pieces']} (-{result.data['dropped']})  "
                 f"steps {result.data['steps']} flights {result.data['flights']}  left {result.data['remaining']:.0f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="david")
    ap.add_argument("--presets", default="")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--set", default="", help="settings on top of each preset: side=back,climb=reverse")
    ap.add_argument("--theta", type=float, default=78.0)
    ap.add_argument("--psi", type=float, default=None)
    args = ap.parse_args()
    names = [n.strip() for n in preset_names(args).split(",") if n.strip()] or preset_names(TOOL)
    QA.mkdir(parents=True, exist_ok=True)
    made = []
    extra = {}
    for pair in filter(None, args.set.split(",")):
        key, _, text = pair.partition("=")
        try:
            extra[key] = float(text)
        except ValueError:
            extra[key] = text
    for model in args.models.split(","):
        for name in names:
            try:
                dst, line = one(model, name, extra)
                made.append((model, name, dst))
                print(line, flush=True)
            except Exception as exc:
                print(f"{model:6s} {name:22s} FAILED: {exc}", flush=True)
    if args.no_render:
        return

    if not made:
        return

    def render(item):
        model, name, dst = item
        png = dst.with_suffix(".png")
        gallery.clay(dst, png, args.theta, args.psi if args.psi is not None else gallery.PSI[MODELS[model]])
        return item, png
    with ThreadPoolExecutor(4) as pool:
        shots = list(pool.map(render, made))
    size = 460
    columns = min(5, len(shots))
    rows = (len(shots) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * size, rows * size), "white")
    pen = ImageDraw.Draw(sheet)
    for i, ((model, name, _), png) in enumerate(shots):
        image = Image.open(png).convert("RGB")
        image.thumbnail((size, size))
        x, y = (i % columns) * size, (i // columns) * size
        sheet.paste(image, (x, y))
        pen.text((x + 8, y + 6), f"{model}: {name}", fill=(0, 0, 0))
    out = QA / "sheet.png"
    sheet.save(out)
    print(out)


if __name__ == "__main__":
    main()
