"""Render Dilate presets through Atlas's real drawing engines in a QA folder."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from atlas_toolkit.devpaths import preset_names, preset_values  # noqa: E402
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401
WORKSPACE = studio_workspace()


def fixtures(folder):
    import numpy as np
    import trimesh
    balloon = trimesh.creation.icosphere(subdivisions=3, radius=20)
    balloon.apply_scale((.7, .6, 1.0))
    balloon.export(folder / "balloon.stl")
    body = trimesh.creation.icosphere(subdivisions=3)
    body.apply_scale((1.3, .4, .7))

    def fin(points):
        return trimesh.convex.convex_hull(np.array([[x, y, z] for y in (-.06, .06) for x, z in points]))

    tail = fin([(-1.05, 0), (-2.15, -.65), (-2.15, .65)])
    dorsal = fin([(-.75, .4), (.15, 1.0), (.7, .4)])
    mouth = trimesh.creation.icosphere(subdivisions=2, radius=.16)
    mouth.apply_scale((1, .7, .7))
    mouth.apply_translation((1.25, 0, -.07))
    fish = trimesh.boolean.union([body, tail, dorsal, mouth], engine="manifold")
    fish.apply_scale(20)
    fish.export(folder / "fish.stl")
    return {"head": WORKSPACE / "3d_models_db/head_model/v17_head_decim7.5K-v2.stl",
            "david": WORKSPACE / "3d_models_db/david_15k_reshape.stl",
            "balloon": folder / "balloon.stl", "fish": folder / "fish.stl"}


def raster(svg):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG: {svg}")
    size = renderer.defaultSize()
    image = QImage(700, round(700 * size.height() / size.width()), QImage.Format_RGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    image.save(str(svg.with_suffix(".png")))
    return svg.with_suffix(".png")


def sheet(folder, model, entries):
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps
    width, height = 350, 420
    image = Image.new("RGB", (width * len(entries), height * 2 + 50), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(DEV_FONT, 18)
    small = ImageFont.truetype(DEV_FONT, 14)
    draw.text((12, 8), model + " — Contour Lines (top) / Plotter Vision (bottom)", fill="black", font=font)
    for column, entry in enumerate(entries):
        x = column * width
        for row, path in enumerate(entry["renders"]):
            source = Image.open(path).convert("RGB")
            bounds = ImageChops.difference(source, Image.new("RGB", source.size, "white")).getbbox()
            if bounds:
                source = source.crop(bounds)
            source = ImageOps.contain(source, (width - 30, height - 65))
            y = row * height + 50
            image.paste(source, (x + (width - source.width) // 2, y + 45))
            draw.text((x + 10, y + 5), entry["preset"], fill="black", font=font)
            draw.text((x + 10, y + 28), entry.get("caption", "Input geometry"), fill="#444444", font=small)
    path = folder / (model + "_comparison.png")
    image.save(path)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True, help="Temporary QA folder, outside source model folders")
    ap.add_argument("--models", nargs="+", default=["head", "david", "balloon", "fish"])
    ap.add_argument("--presets", nargs="+")
    ap.add_argument("--out-faces", type=int, default=80_000)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication([])
    from atlas_toolkit.core.job import JobContext
    from atlas_toolkit.core.registry import registry
    from atlas_toolkit.geom.mesh import load_mesh
    from atlas_toolkit.tools.mesh_dilate import TOOL
    reg = registry()
    models = fixtures(args.out)
    records = []
    for model in args.models:
        entries = []
        src = models[model]
        for preset in ["Original"] + (args.presets or preset_names(TOOL)):
            started = time.perf_counter()
            name = model + "_" + preset.lower().replace(" ", "_")
            warnings = []
            last_progress = [0.0]

            def emit(kind, payload):
                if kind == "log" and payload[0] == "warning":
                    warnings.append(payload[1])
                elif kind == "progress" and time.perf_counter() - last_progress[0] > 5:
                    print(f"{name}: {payload[0]:.0%} {payload[1]}", flush=True)
                    last_progress[0] = time.perf_counter()

            ctx = JobContext(emit, lambda: False)
            dst = src
            entry = dict(model=model, preset=preset, renders=[])
            if preset != "Original":
                dst = args.out / (name + ".ply")
                values = TOOL.values(dict(out_faces=args.out_faces, up_axis="+z", front="-y"), preset=preset)
                result = TOOL.engine_fn()(ctx, src, dst, values)
                entry.update(diagnostics=result.data, warnings=warnings,
                             caption=f"Added volume {result.stat('inflation').text()}")
                (args.out / (name + ".txt")).write_text(result.report + "\n" + "\n".join(warnings), encoding="utf-8")
                mesh = load_mesh(dst)
                assert mesh.is_watertight and mesh.volume > 0, name
            for renderer in ("contour-lines", "plotter-vision"):
                tool = reg.get(renderer)
                values = preset_values(tool, "Features") if renderer == "plotter-vision" else tool.defaults()
                values.update(up_axis="+z", front="-y", paper="A4", orientation="portrait",
                              theta=75., psi=-140. if model in ("head", "david") else -175., line_width=.25)
                if renderer == "contour-lines":
                    values.update(spacing_mm=2.2, min_len=2.0)
                else:
                    values.update(decimate=False)
                svg = args.out / (name + "_" + renderer + ".svg")
                drawing = tool.engine_fn()(ctx, dst, svg, tool.values(values))
                assert drawing.stat("strokes").value > 0, name
                entry["renders"].append(str(raster(svg)))
            entry["seconds"] = round(time.perf_counter() - started, 2)
            records.append(entry)
            entries.append(entry)
            (args.out / "validation.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
            print(f"{name}: {entry.get('caption', 'input')} - {entry['seconds']}s", flush=True)
        print("comparison: " + str(sheet(args.out, model, entries)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
