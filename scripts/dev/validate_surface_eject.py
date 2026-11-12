"""Repeatable Surface Eject real-model line-art acceptance and selection-editor screenshots."""
from pathlib import Path
import argparse
import json
import os
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from atlas_toolkit.devpaths import preset_names, preset_raws, preset_values  # noqa: E402
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
    valid = [row for row in records if row.get("png")]
    w, h = 340, 470
    image = Image.new("RGB", (w * 4, h * ((len(valid) + 3) // 4)), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(DEV_FONT, 15)
    small = ImageFont.truetype(DEV_FONT, 12)
    for i, row in enumerate(valid):
        tile = Image.open(row["png"]).convert("RGB")
        bounds = ImageChops.difference(tile, Image.new("RGB", tile.size, "white")).getbbox()
        if bounds:
            tile = tile.crop(bounds)
        tile = ImageOps.contain(tile, (w - 30, h - 85))
        x, y = (i % 4) * w, (i // 4) * h
        image.paste(tile, (x + (w - tile.width) // 2, y + 70 + (h - 85 - tile.height) // 2))
        draw.text((x + 12, y + 9), row["preset"], font=font, fill="black")
        draw.text((x + 12, y + 31), row["source"] + " / " + row["renderer"], font=small, fill="#555555")
        draw.text((x + 12, y + 48), f"{row['pieces']} pieces / {row['removed']:.1f}% excavated", font=small, fill="#555555")
    image.save(out / "surface_eject_contact_sheet.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--head", type=Path, default=studio_workspace() / "3d_models_db/head_model/v17_head.stl")
    parser.add_argument("--quick", action="store_true", help="Omit extra Sketch Hatching plates.")
    parser.add_argument("--connectors-only", action="store_true", help="Validate connector presets plus connected primitive and painted examples.")
    parser.add_argument("--trees-only", action="store_true", help="Validate organic tree presets, a 100-branch primitive and painted tree regions.")
    parser.add_argument("--editor", action="store_true", help="Capture selection modal in both themes; use a working OpenGL Qt platform.")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"], os.environ["LOCALAPPDATA"] = str(out / "roaming"), str(out / "local")
    if not args.editor:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import numpy as np
    import trimesh
    from atlas_toolkit.core.job import JobContext
    from atlas_toolkit.core.registry import registry
    from atlas_toolkit.core import workflow as W
    from atlas_toolkit.geom import mesh as gm, frame3d
    from atlas_toolkit.tools.mesh_surface_eject import TOOL, engine
    from atlas_toolkit.tools.mesh_surface_eject import mesh_surface_eject as se
    reg = registry()
    ctx = JobContext(lambda k, p: print(p, flush=True) if k == "log" else None, lambda: False)
    primitive = engine.make_sample(out)
    cases = [(args.head, name, {}) for name, values in preset_raws(TOOL).items()
             if (name in ("Root Fans", "Twig Crowns", "Wandering Branches", "Wild Canopy") if args.trees_only
                 else not args.connectors_only or values.get("connectors"))] if args.head.exists() else []
    if args.trees_only:
        cases += [(primitive, "Hundred Branches", dict(connectors=True, connector_style="organic", count=1,
                    connector_branches=100, connector_branch_placement="mixed", connector_branch_levels=3, segments=12))]
    elif args.connectors_only:
        cases += [(primitive, "Primitive Branches", dict(connectors=True, connector_style="organic", connector_branches=3, count=14))]
    else:
        cases += [(primitive, "Primitive Ellipsoid", dict(shape="ellipsoid", count=20)),
                  (primitive, "Clipped Narrow Band", dict(region="band", band_from=45., band_to=55., count=18))]
    if args.head.exists():
        original = gm.load_mesh(args.head)
        frame = frame3d.fit_frame(original.vertices, "+z", "-y", (0., 0., 0.), mode="height", size=2.)
        display = trimesh.Trimesh(frame.apply(original.vertices), original.faces, process=False)
        mask = (display.triangles_center[:, 2] > -.15) & (display.face_normals[:, 1] < -.3)
        text = se.encode_selection(original, mask)
        (out / "head-selection.json").write_text(text, encoding="utf-8")
        cases.append((args.head, "Painted Front", dict(region="paint", selection=text, up_axis="+z", front="-y", count=16,
                     **(dict(connectors=True, connector_style="organic", connector_branches=8,
                             connector_branch_placement="mixed", connector_branch_levels=2) if args.trees_only
                        else dict(connectors=True, connector_style="organic", connector_branches=1) if args.connectors_only else {}))))
        if args.editor:
            from atlas_toolkit.ui.theme import Theme, load_fonts
            from atlas_toolkit.ui.theme.qss import stylesheet
            from atlas_toolkit.tools.mesh_surface_eject.viewer import SelectionDialog
            load_fonts()
            for name in ("dark", "light"):
                theme = Theme(name)
                app.setStyleSheet(stylesheet(theme))
                dialog = SelectionDialog(original, display, mask, theme)
                dialog.show()
                for _ in range(8):
                    app.processEvents()
                    time.sleep(.03)
                dialog.grab().save(str(out / f"selection_{name}.png"))
                dialog.canvas.grabFramebuffer().save(str(out / f"selection_mesh_{name}.png"))
                dialog.close()
    records = []
    for src, name, values in cases:
        slug = src.stem + "_" + name.lower().replace(" ", "_")
        dst = out / (slug + ".obj")
        print("Building", slug, flush=True)
        try:
            p = (preset_values(TOOL, name, **values) if name in preset_names(TOOL) else TOOL.values(values))
            result = engine.run(ctx, src, dst, p)
            (out / (slug + ".txt")).write_text(result.report, encoding="utf-8")
            row = dict(preset=name, source=src.stem, pieces=result.stat("pieces").value,
                       removed=result.stat("removed").value, data=result.data)
            draw = reg.get("contour-lines")
            svg = out / (slug + "_contours.svg")
            drawing = draw.engine_fn()(ctx, dst, svg, draw.values(dict(spacing_mm=1.4, line_width=.3,
                        paper="A3", orientation="portrait", theta=72.4, psi=148., radius=262., normalise="model")))
            assert drawing.stat("strokes").value > 0
            raster(svg)
            row.update(renderer="Contours", png=str(svg.with_suffix(".png")), svg=str(svg))
            records.append(row)
            if not args.quick and name in ("Quiet Excavation", "Round Relics", "Living Stems", "Rooted Canopy", "Wandering Branches", "Wild Canopy") and src == args.head:
                draw = reg.get("sketch-hatching")
                svg = out / (slug + "_sketch.svg")
                draw.engine_fn()(ctx, dst, svg, draw.values(dict(paper="A3", line_width=.3), preset="Classic"))
                raster(svg)
                records.append(dict(row, renderer="Sketch", png=str(svg.with_suffix(".png")), svg=str(svg)))
            print(json.dumps({k: v for k, v in row.items() if k != "data"}), flush=True)
        except Exception as exc:
            records.append(dict(preset=name, source=src.stem, error=str(exc)))
            print("FAILED", name, exc, flush=True)
    (out / "acceptance.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    if any(row.get("png") for row in records):
        sheet(out, records)
    for path in (ROOT / "docs/workflows").glob("surface_eject_*.atlasflow"):
        errors = W.errors(W.check(W.load(path), reg))
        if errors:
            raise ValueError(f"{path.name}: {errors}")
    return int(any("error" in row for row in records))


if __name__ == "__main__":
    raise SystemExit(main())
