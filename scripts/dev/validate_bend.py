"""Repeatable Bend acceptance on real sculptures and coarse primitives, using Atlas's SVG renderers."""
from pathlib import Path
import argparse
import json
import os
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from atlas_toolkit.devpaths import preset_names  # noqa: E402
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401


def raster(svg):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG: {svg}")
    image = QImage(700, 990, QImage.Format_RGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    image.save(str(svg.with_suffix(".png")))


def sheet(out, records):
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps
    width, height = 340, 470
    valid = [row for row in records if row.get("png")]
    image = Image.new("RGB", (width * 4, height * ((len(valid) + 3) // 4)), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(DEV_FONT, 15)
    small = ImageFont.truetype(DEV_FONT, 12)
    for i, row in enumerate(valid):
        tile = Image.open(row["png"]).convert("RGB")
        bounds = ImageChops.difference(tile, Image.new("RGB", tile.size, "white")).getbbox()
        if bounds:
            tile = tile.crop(bounds)
        tile = ImageOps.contain(tile, (width - 30, height - 90))
        x, y = (i % 4) * width, (i // 4) * height
        image.paste(tile, (x + (width - tile.width) // 2, y + 75 + (height - 90 - tile.height) // 2))
        draw.text((x + 12, y + 10), row["preset"], font=font, fill="black")
        draw.text((x + 12, y + 33), row["source"] + " / " + row["renderer"], font=small, fill="#555555")
        draw.text((x + 12, y + 50), f"{row['angle']:.1f} deg achieved / {row['faces']:,} triangles", font=small, fill="#555555")
    image.save(out / "bend_contact_sheet.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--head", type=Path, default=studio_workspace() / "3d_models_db/head_model/v17_head.stl")
    parser.add_argument("--quick", action="store_true", help="Draw contours only; skip the extra sketch plates.")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # Isolate caches and settings; never change the studio's artwork or the user's settings.
    os.environ["APPDATA"] = str(out / "roaming")
    os.environ["LOCALAPPDATA"] = str(out / "local")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import numpy as np
    import trimesh
    from atlas_toolkit.core.job import JobContext
    from atlas_toolkit.core.registry import registry
    from atlas_toolkit.core import workflow as W
    from atlas_toolkit.geom import mesh as gm
    from atlas_toolkit.tools.mesh_bend import TOOL
    from atlas_toolkit.tools.mesh_bend.engine import run

    reg = registry()
    ctx = JobContext(lambda k, p: print(p, flush=True) if k == "log" else None, lambda: False)
    pillar = out / "fluted_column.stl"
    mesh = trimesh.creation.cylinder(radius=10, height=100, sections=36)
    angles = np.arctan2(mesh.vertices[:, 1], mesh.vertices[:, 0])
    mesh.vertices[:, :2] *= (1 + .18 * np.cos(6 * angles))[:, None]
    mesh.export(pillar)
    box = out / "coarse_bar.stl"
    trimesh.creation.box(extents=[14, 9, 100]).export(box)
    cases = [(args.head, name) for name in preset_names(TOOL)] if args.head.exists() else []
    cases = [(args.head, "Intact")] + cases if args.head.exists() else cases
    cases += [(pillar, name) for name in ("Serpentine", "Spatial arabesque", "Flowing wave", "Rolled arc")]
    cases += [(box, "Reclining marble")]
    records = []
    for src, name in cases:
        slug = src.stem + "_" + name.lower().replace(" ", "_")
        dst = out / (slug + ".obj")
        print("Building", slug, flush=True)
        started = time.perf_counter()
        try:
            p = TOOL.values(dict(amount=0.)) if name == "Intact" else TOOL.values(preset=name)
            # Sequence itself is tested here; the final primary model is drawn below.
            result = run(ctx, src, dst, p)
            (out / (slug + ".txt")).write_text(result.report, encoding="utf-8")
            saved = gm.load_mesh(dst)
            assert np.isfinite(saved.vertices).all() and saved.nondegenerate_faces().all()
            assert saved.is_winding_consistent
            assert saved.is_watertight == gm.load_mesh(src).is_watertight
            row = dict(preset=name, source=src.stem, faces=len(saved.faces), angle=result.stat("angle").value,
                       seconds=round(time.perf_counter() - started, 3), data=result.data)
            tool = reg.get("contour-lines")
            drawing_values = dict(spacing_mm=1.35, line_width=.3, paper="A3", orientation="portrait",
                                  theta=72.4, psi=148., radius=262., normalise="model")
            if name in ("Deep bow", "Crown hook"):
                drawing_values["psi"] = 75.
            svg = out / (slug + "_contours.svg")
            drawing = tool.engine_fn()(ctx, dst, svg, tool.values(drawing_values))
            assert drawing.stat("strokes").value > 0
            raster(svg)
            row.update(renderer="Contours", png=str(svg.with_suffix(".png")), svg=str(svg),
                       strokes=drawing.stat("strokes").value)
            records.append(row)
            if not args.quick and name in ("Spatial arabesque", "Rolled arc", "Deep bow") and src == args.head:
                tool = reg.get("sketch-hatching")
                svg = out / (slug + "_sketch.svg")
                drawing = tool.engine_fn()(ctx, dst, svg, tool.values(dict(paper="A3", line_width=.3), preset="Classic"))
                raster(svg)
                records.append(dict(row, renderer="Sketch", png=str(svg.with_suffix(".png")), svg=str(svg)))
            print(json.dumps({k: v for k, v in row.items() if k != "data"}), flush=True)
        except Exception as exc:
            records.append(dict(preset=name, source=src.stem, error=str(exc)))
            print("FAILED", name, exc, flush=True)
    (out / "acceptance.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    if any(row.get("png") for row in records):
        sheet(out, records)
    # Check every bundled Bend drawing workflow using the common contract.
    for path in (ROOT / "docs/workflows").glob("bend_*.atlasflow"):
        errors = W.errors(W.check(W.load(path), reg))
        if errors:
            raise ValueError(f"{path.name}: {errors}")
    return int(any("error" in row for row in records))


if __name__ == "__main__":
    raise SystemExit(main())
