"""Skeleton Shell on real models: every preset, with timings, drawn with Plotter Vision on a contact sheet.

    python scripts/dev/validate_skeleton.py [--models david,venus] [--presets "Wire Dancer,Airframe"] [--clay]
                                            [--set size=8,seed=3] [--draft] [--flat 25] [--tag x]

Writes to build/qa/skeleton.
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
from atlas_toolkit.tools.mesh_skeleton import TOOL, engine  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import readme_gallery as gallery  # noqa: E402

QA = devpaths.qa_dir("skeleton")
MODELS = {"david": gallery.DAVID, "venus": gallery.VENUS}


def build(model: str, name: str, extra: dict, tag: str, draft: bool) -> tuple:
    src = gallery.sample(MODELS[model])
    values = dict(TOOL.defaults())
    values.update(preset_raw(TOOL, name))
    values.update(extra)
    dst = QA / f"{model}_{name.replace(' ', '_').replace(chr(39), '')}{tag}.stl"
    ctx = JobContext(lambda *a: None, lambda: False, "draft" if draft else "run")
    ctx.log = ctx.warn = lambda *a: None
    started = time.perf_counter()
    result = engine.run(ctx, src, dst, TOOL.values(values))
    d = result.data
    faces = next(s.value for s in result.stats if s.key == "faces")
    return dst, (f"{model:6s} {name:20s} {time.perf_counter() - started:6.1f}s  wires {d['wires']:5d}  "
                 f"frames {d['frames']:3d}  sheet {d['coverage'] * 100:3.0f}%  faces {faces:9,d}  {d['notes']}")


def draw(item, flat: float, psi: float | None, theta: float) -> tuple:
    """Plotter Vision draws it, as the README does; returns the item and a panel of its ink."""
    model, name, dst = item
    folder = dst.parent / (dst.stem + "_draw")
    folder.mkdir(exist_ok=True)
    angle = psi if psi is not None else gallery.PSI[MODELS[model]]
    code, output = gallery.execute([sys.executable, "-m", "atlas_toolkit", "run", "plotter-vision", dst, "--out-dir",
                                    folder, "--force", "--preset", "Clean", "--flat-angle", flat,
                                    *gallery.camera_args("plotter-vision", theta, angle)], timeout=600)
    svgs = sorted(folder.glob("*.svg"))
    if code or not svgs:
        print(f"{model} {name}: drawing failed: {gallery.reason(gallery.clean_log(output))}", flush=True)
        return item, None
    return item, gallery.ink_panel(gallery.svg_coverage(svgs[0], 2200), 620, 800)


def sheet_of(panels: list, out: Path) -> None:
    columns = min(4, len(panels))
    rows = (len(panels) + columns - 1) // columns
    w, h = panels[0][1].size
    sheet = Image.new("RGB", (columns * w, rows * h), "white")
    pen = ImageDraw.Draw(sheet)
    for i, ((model, name, _), image) in enumerate(panels):
        x, y = (i % columns) * w, (i // columns) * h
        sheet.paste(image, (x, y))
        pen.text((x + 8, y + 6), f"{model}: {name}", fill=(0, 0, 0))
    sheet.save(out)
    print(out, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="david")
    ap.add_argument("--presets", default="")
    ap.add_argument("--clay", action="store_true")
    ap.add_argument("--draw", action="store_true")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--set", default="", help="settings on top of each preset: size=8,sides=4")
    ap.add_argument("--tag", default="")
    ap.add_argument("--flat", type=float, default=25.0)
    ap.add_argument("--psi", type=float, default=None)
    ap.add_argument("--theta", type=float, default=78.0)
    args = ap.parse_args()
    names = [n.strip() for n in preset_names(args).split(",") if n.strip()] or preset_names(TOOL)
    QA.mkdir(parents=True, exist_ok=True)
    extra = {}
    for pair in filter(None, args.set.split(",")):
        key, _, text = pair.partition("=")
        try:
            extra[key] = float(text) if "." in text else int(text)
        except ValueError:
            extra[key] = {"true": True, "false": False}.get(text.lower(), text)
    made = []
    for model in args.models.split(","):
        for name in names:
            try:
                dst, line = build(model, name, extra, args.tag, args.draft)
                made.append((model, name, dst))
                print(line, flush=True)
            except Exception as exc:
                print(f"{model:6s} {name:20s} FAILED: {type(exc).__name__}: {exc}", flush=True)
    if not made or not (args.clay or args.draw):
        return

    if args.draw:
        with ThreadPoolExecutor(3) as pool:
            panels = list(pool.map(lambda item: draw(item, args.flat, args.psi, args.theta), made))
        panels = [p for p in panels if p[1] is not None]
        if panels:
            sheet_of(panels, QA / f"drawn{args.tag}.png")
    if args.clay:
        def clay(item):
            model, name, dst = item
            png = dst.with_suffix(".png")
            angle = args.psi if args.psi is not None else gallery.PSI[MODELS[model]]
            gallery.clay(dst, png, args.theta, angle)
            return item, gallery.clay_panel(png, 620, 800)
        with ThreadPoolExecutor(3) as pool:
            sheet_of(list(pool.map(clay, made)), QA / f"clay{args.tag}.png")


if __name__ == "__main__":
    main()
