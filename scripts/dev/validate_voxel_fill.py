"""Repeatable real-model Voxelize Fill comparisons, with SVGs, metrics and a PNG sheet.

Outputs stay in build/qa by default. Does not write to the model library.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401
WORKSPACE = studio_workspace()

from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.voxelize_fill import TOOL
from atlas_toolkit.tools.voxelize_fill.engine import run
from atlas_toolkit.tools.voxelize_fill.voxelize_fill import build
from atlas_toolkit.tools.plotter_vision.engine import run as draw

MODELS = {
    "head": "3d_models_db/head_model/v17_head.stl",
    "augustus": "projects/blender3d_project/augustus/augustus.prima.top.01.mesh160k.stl",
    "julia": "3d_models_db/Julia-fractal/julia_20k_v2.stl",
}
PRESETS = ("Block study", "Cutaway sculpture", "Exploded strata", "Adaptive excavation",
           "Directional engraving", "Nested depth bands", "Dissolving blocks", "Joined solid")


def rasterise_sheet(folder, model, records):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    app = QGuiApplication.instance() or QGuiApplication([])
    from atlas_toolkit.ui.theme import load_fonts
    load_fonts()
    width, cell_w, cell_h = 1600, 400, 560
    sheet = QImage(width, 2 * cell_h + 90, QImage.Format_RGB32)
    sheet.fill(QColor("#ffffff"))
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QColor("#182030"))
    painter.setFont(QFont("Geist", 20))
    painter.drawText(QRectF(30, 10, width - 60, 40), Qt.AlignLeft | Qt.AlignVCenter,
                     "Voxelize Fill / " + model + " / fixed source frame")
    for i, record in enumerate(records):
        x, y = (i % 4) * cell_w, 70 + (i // 4) * cell_h
        painter.setFont(QFont("Geist", 12))
        painter.drawText(QRectF(x + 18, y, cell_w - 36, 30), Qt.AlignLeft, record["preset"])
        painter.setFont(QFont("Geist", 9))
        geometry = f"{record['blocks']:,} blocks" if record["blocks"] else "joined boundary"
        painter.drawText(QRectF(x + 18, y + 28, cell_w - 36, 26), Qt.AlignLeft,
                         f"{record['retained']:,} cells / {geometry}")
        renderer = QSvgRenderer(str(folder / record["svg"]))
        renderer.render(painter, QRectF(x + 10, y + 56, cell_w - 20, cell_h - 68))
    painter.end()
    sheet.save(str(folder / (model + "_comparison.png")))
    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=qa_dir("voxel_fill_validation"))
    ap.add_argument("--models", nargs="+", choices=tuple(MODELS), default=["head", "augustus", "julia"])
    ap.add_argument("--across", type=int, default=28)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    vision = registry().get("plotter-vision")
    previous = args.out / "results.json"
    all_records = [r for r in json.loads(previous.read_text(encoding="utf-8")) if r["model"] not in args.models] \
        if previous.exists() else []
    qt_app = None
    for name in args.models:
        src = WORKSPACE / MODELS[name]
        if not src.is_file():
            raise FileNotFoundError(src)
        model = gm.load_mesh(src)
        # Test a fully filled baseline independently of artistic removals.
        _, baseline, diagnostics = build(model, TOOL.values({"across": args.across, "geometry": "joined"}))
        assert baseline["voxels"] > 0 and baseline["retained"] == baseline["voxels"]
        print(f"{name}: {len(model.faces):,} input triangles; {baseline['voxels']:,} full cells", flush=True)
        records = []
        for preset in PRESETS:
            slug = preset.lower().replace(" ", "_")
            mesh_path = args.out / f"{name}_{slug}.obj"
            svg_path = mesh_path.with_suffix(".svg")
            p = TOOL.values({"across": args.across}, preset=preset)
            logs = []
            ctx = JobContext(lambda kind, payload: logs.append((kind, payload)) if kind == "log" else None,
                             lambda: False)
            t = time.perf_counter()
            result = run(ctx, src, mesh_path, p)
            bounds = np.asarray(result.preview["views"][0]["mesh"].bounds)
            output_span = float(np.ptp(bounds, axis=0).max())
            lookat = (model.bounds.mean(0) - bounds.mean(0)) * 100. / output_span
            drawing_values = vision.values(dict(edge_mode="mesh", decimate=False, crossings=False,
                                                outline_layer=False, show_hidden=False, optimise=True,
                                                theta=65., psi=150., radius=190. * model.extents.max() / output_span,
                                                lookat=tuple(lookat), line_width=0.25,
                                                paper="A3", orientation="portrait"))
            drawing = draw(ctx, mesh_path, svg_path, drawing_values)
            record = dict(model=name, preset=preset, mesh=mesh_path.name, svg=svg_path.name,
                          **result.data, seconds=round(time.perf_counter() - t, 3),
                          mesh_report=result.report, drawing_report=drawing.report)
            records.append(record)
            all_records.append(record)
            print(f"  {preset}: {record['retained']:,} cells; {record['triangles']:,} triangles; "
                  f"{record['seconds']:.2f} s", flush=True)
        qt_app = rasterise_sheet(args.out, name, records)
        (args.out / "results.json").write_text(json.dumps(all_records, indent=2), encoding="utf-8")
    print(f"Saved meshes, SVGs, comparison sheets and results.json to {args.out}", flush=True)


if __name__ == "__main__":
    main()
