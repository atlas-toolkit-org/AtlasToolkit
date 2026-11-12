"""Render repeatable connector acceptance plates into build/qa/strata-connectors.

Optional reference sculptures are read through devpaths, or supplied with --model.
Only QA copies and generated results are written; source artwork stays untouched.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from atlas_toolkit.devpaths import DEV_FONT, qa_dir, workspace_path  # noqa: E402


def raster(svg):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise ValueError(f"Invalid drawing: {svg.name}")
    image = QImage(700, 990, QImage.Format_RGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    image.save(str(svg.with_suffix(".png")))


def sheet(out, records):
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps
    valid = [r for r in records if "png" in r]
    if not valid:
        return
    width, height = 400, 560
    image = Image.new("RGB", (width*3, height*((len(valid)+2)//3)), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(DEV_FONT, 19)
    small = ImageFont.truetype(DEV_FONT, 13)
    for i, record in enumerate(valid):
        tile = Image.open(record["png"]).convert("RGB")
        bounds = ImageChops.difference(tile, Image.new("RGB", tile.size, "white")).getbbox()
        if bounds:
            tile = tile.crop(bounds)
        tile = ImageOps.contain(tile, (width-30, height-95))
        x, y = i % 3 * width, i // 3 * height
        image.paste(tile, (x+(width-tile.width)//2, y+85+(height-95-tile.height)//2))
        draw.text((x+15, y+10), record["name"], fill="black", font=font)
        draw.text((x+15, y+38), f"{record['supports']} supports / {record['branches']} forks", fill="black", font=small)
        draw.text((x+15, y+57), f"{record['components']} closed component(s)", fill="black", font=small)
    image.save(out / "strata_connectors.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, help="Optional reference sculpture; read only.")
    args = parser.parse_args()
    out = qa_dir("strata-connectors")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["APPDATA"], os.environ["LOCALAPPDATA"] = str(out/"roaming"), str(out/"local")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import trimesh
    from atlas_toolkit.core.job import JobContext
    from atlas_toolkit.core.registry import registry
    from atlas_toolkit.geom import mesh as gm
    from atlas_toolkit.tools.mesh_strata import TOOL, engine

    ctx = JobContext(lambda *_: None, lambda: False)
    pillar = out / "qa_pillar.stl"
    trimesh.creation.capsule(height=60., radius=15., count=[24, 24]).export(pillar)
    ring = out / "qa_ring.stl"
    trimesh.creation.torus(30., 11., major_sections=48, minor_sections=24).export(ring)
    cases = [(pillar, name, name, {}) for name in ("Pinned Strata", "Square Supports", "Airy Roots")]
    cases += [(ring, "Rooted Ring", "Airy Roots", dict(layers=4, separation=130.)),
              (pillar, "Living Spiral", "Airy Roots", dict(layers=9, turn=55., wave=3.,
                                                         jitter_tilt=3., jitter_shift=1., seed=7))]
    reference = args.model or workspace_path("3d_models_db", "head_model", "v17_head.stl")
    if reference is not None and reference.is_file():
        # Keep acceptance plates fast and reproducible without changing the artwork.
        original = gm.load_mesh(reference)
        v, f = gm.simplify_faces(original.vertices, original.faces, 40_000, lambda *_: None)
        copy = out / "qa_reference.stl"
        trimesh.Trimesh(v, f, process=False).export(copy)
        cases.append((copy, "Sculpture Roots", "Airy Roots", dict(layers=10, up_axis="+z", front="-y")))
    else:
        print("No optional reference model available; validating primitives only.", flush=True)
    draw = registry().get("plotter-vision")
    records = []
    for source, name, preset, changes in cases:
        slug = name.lower().replace(" ", "_")
        print(f"Building {name}", flush=True)
        try:
            dst = out / (slug + ".obj")
            p = TOOL.values(changes, preset=preset)
            result = engine.run(ctx, source, dst, p)
            mesh = gm.load_mesh(dst)
            if not mesh.is_volume:
                raise ValueError("Reloaded output is not a closed solid")
            (out / (slug+".txt")).write_text(result.report, encoding="utf-8")
            svg = out / (slug+".svg")
            draw.engine_fn()(ctx, dst, svg, draw.values(dict(flat_angle=35., line_width=.25,
                                paper="A3", orientation="portrait", theta=72., psi=148., radius=262.,
                                normalise="model")))
            raster(svg)
            records.append(dict(name=name, supports=result.stat("supports").value,
                                branches=result.stat("branches").value, components=len(mesh.split()),
                                faces=len(mesh.faces), png=str(svg.with_suffix(".png"))))
            print(json.dumps({k:v for k,v in records[-1].items() if k != "png"}), flush=True)
        except Exception as exc:
            records.append(dict(name=name, error=str(exc)))
            print(f"FAILED: {name}: {exc}", flush=True)
    (out / "acceptance.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    sheet(out, records)
    app.processEvents()
    return int(any("error" in r for r in records))


if __name__ == "__main__":
    raise SystemExit(main())
