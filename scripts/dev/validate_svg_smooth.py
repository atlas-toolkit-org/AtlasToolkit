"""Repeatable quality/timing report and vector/raster acceptance plates.

Run with the Atlas venv. Outputs stay under build/qa/svg_smooth_validation.
Optional --source path uses an existing plotter SVG for an additional comparison.
"""
from __future__ import annotations
import argparse
import json
import os
import time
from pathlib import Path

import numpy as np

from atlas_toolkit.devpaths import preset_values  # noqa: E402
from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom.svg import load, shape_subpaths, local
from atlas_toolkit.tools.svg_smooth.engine import make_sample, run
from atlas_toolkit.tools.svg_smooth.filters import filter_points
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401

ROOT = studio_workspace()


def render_pair(before, after, dst, view=None):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF, QByteArray
    from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor, QFont, QFontDatabase
    from PySide6.QtSvg import QSvgRenderer
    app = QGuiApplication.instance() or QGuiApplication([])
    for font in (Path(__file__).resolve().parents[2] / "src/atlas_toolkit/ui/theme/fonts").glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(font))
    image = QImage(1800, 950, QImage.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setFont(QFont("Geist", 19))
    for i, (src, title) in enumerate(((before, "Original"), (after, "Smoothed"))):
        renderer = QSvgRenderer(QByteArray(Path(src).read_bytes()))
        if view:
            renderer.setViewBox(QRectF(*view))
        painter.setPen(QColor("#171c28"))
        painter.drawText(QRectF(35 + i * 900, 15, 840, 45), title)
        rect = QRectF(35 + i * 900, 75, 830, 830)
        painter.save()
        painter.setClipRect(rect)
        renderer.render(painter, rect)
        painter.restore()
    painter.end()
    if not image.save(str(dst)):
        raise RuntimeError("Could not write validation image")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path)
    args = ap.parse_args()
    out = qa_dir("svg_smooth_validation")
    out.mkdir(parents=True, exist_ok=True)
    tool = registry().get("svg-smooth")
    ctx = JobContext(lambda *a: None, lambda: False, "run", str(out / "cache"))
    src = make_sample(out)
    metrics = {}
    for method in ("whittaker", "taubin"):
        dst = out / f"calibration_{method}.svg"
        p = tool.values(dict(preset_values(tool, "Surface stripes"), method=method))
        start = time.perf_counter()
        result = run(ctx, src, dst, p)
        metrics[method] = dict(seconds=time.perf_counter() - start, stats={s.key: s.value for s in result.stats})
        (out / f"calibration_{method}.txt").write_text(result.report, encoding="utf-8")
        render_pair(src, dst, out / f"calibration_{method}.png", (8, 17, 50, 26))
        # A large, uniformly sampled curve isolates algorithm cost from XML and guards.
        x = np.linspace(0, 1000, 100_000)
        signal = np.column_stack([x, np.sin(x * .02) + .1 * np.cos(x * 120)])
        start = time.perf_counter()
        q = filter_points(signal, x[1] - x[0], 1., method)
        metrics[method]["filter_100k_seconds"] = time.perf_counter() - start
        metrics[method]["residual_rms"] = float(np.sqrt(np.mean((q[1000:-1000, 1] - np.sin(x[1000:-1000] * .02)) ** 2)))
    if args.source:
        dst = out / f"{args.source.stem}_smoothed.svg"
        start = time.perf_counter()
        result = run(ctx, args.source, dst, tool.values(preset_values(tool, "Surface stripes")))
        metrics["real_svg"] = dict(source=str(args.source), seconds=time.perf_counter() - start,
                                   stats={s.key: s.value for s in result.stats})
        (out / "surface_stripes_report.txt").write_text(result.report, encoding="utf-8")
        # Zoom to a changed stroke with a substantial bend, using the existing mm SVG fixture.
        a, b = load(args.source), load(dst)
        original = [e for e in a.getroot().iter() if local(e.tag) == "path"]
        changed = [e for e in b.getroot().iter() if local(e.tag) == "path"]
        choices = []
        for x, y in zip(original, changed):
            if x.get("d") == y.get("d"):
                continue
            p = np.asarray(shape_subpaths(x, "path", .005)[0][0][0])
            if len(p) > 6:
                score = float(np.linalg.norm(np.diff(p, n=2, axis=0), axis=1).sum())
                choices.append((score, p))
        if choices:
            p = max(choices, key=lambda x: x[0])[1]
            center = (p.min(axis=0) + p.max(axis=0)) / 2
            size = max(12., float(np.ptp(p, axis=0).max()) + 2)
            render_pair(args.source, dst, out / "surface_stripes_detail.png",
                        (center[0] - size / 2, center[1] - size / 2, size, size))
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
