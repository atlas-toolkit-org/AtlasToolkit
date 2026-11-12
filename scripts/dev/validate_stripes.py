"""Generate checked Surface Stripes meshes and renderer examples in an explicitly named output folder."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"src"))
from atlas_toolkit.devpaths import preset_names, preset_values  # noqa: E402
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401
WORKSPACE = studio_workspace()


def png(svg):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise RuntimeError(f"invalid SVG: {svg}")
    size = renderer.defaultSize()
    image = QImage(1000, max(1, round(1000*size.height()/size.width())), QImage.Format_ARGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    image.save(str(svg.with_suffix(".png")))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--models", nargs="+", default=["head", "zenobia"], choices=["head", "zenobia"])
    ap.add_argument("--presets", nargs="+", default=["default"],
                    choices=["default", "Plotter broad", "Plotter dense"])
    ap.add_argument("--stripes-only", action=argparse.BooleanOptionalAction, default=None)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    os.environ["ATLAS_STRIPES_CACHE"] = str(ROOT/"build/stripes-validation-cache")
    from atlas_toolkit.core.job import JobContext
    from atlas_toolkit.core.registry import registry
    from atlas_toolkit.geom.mesh import load_mesh
    import xml.etree.ElementTree as ET
    reg = registry()
    models = {"head": WORKSPACE/"3d_models_db/head_model/v17_head_decim7.5K-v2.stl",
              "zenobia": WORKSPACE/"3d_models_db/zenobia_tf_v3-50K.stl"}
    records = []
    cases = [(model, preset) for model in args.models for preset in preset_names(args)]
    for model, preset in cases:
        name = model if preset == "default" else model+"_"+preset.lower().replace(" ", "_")
        if preset == "default" and args.stripes_only:
            name += "_only"
        src = models[model]
        last = [0.]
        def emit(kind, payload):
            if kind == "progress" and time.monotonic()-last[0] > 2:
                print(f"{name}: {payload[0]:.0%} {payload[1]}", flush=True)
                last[0] = time.monotonic()
            elif kind == "log":
                print(name+": "+payload[1], flush=True)
        ctx = JobContext(emit, lambda: False, "run")
        tool = reg.get("mesh-stripes")
        dst = args.out/(name+"_stripes.stl")
        params = tool.defaults() if preset == "default" else preset_values(tool, preset)
        if args.stripes_only is not None:
            params = tool.values(dict(params, stripes_only=args.stripes_only))
        result = tool.engine_fn()(ctx, src, dst, params)
        original, output = load_mesh(src), load_mesh(dst)
        assert output.is_watertight and output.is_winding_consistent and output.volume > 0
        assert output.volume < original.volume if params.stripes_only else output.volume > original.volume
        record = dict(model=name, source=str(src), mesh=str(dst), faces=len(output.faces),
                      input_faces=len(original.faces), volume_ratio=float(output.volume/original.volume),
                      closed=bool(output.is_watertight), preset=preset, values=dict(params),
                      stripe_diagnostics=result.data, renders=[])
        for renderer in ("plotter-vision", "stl-hatch-maker") if model == "head" else ("plotter-vision",):
            draw = reg.get(renderer)
            svg = args.out/(name+"_stripes_"+renderer+".svg")
            if renderer == "plotter-vision":
                values = preset_values(draw, "Features")
                values.update(psi=-100., theta=80., paper="A4", line_width=.25)
                # Projection uses every final rim; no downstream mesh decimation.
                values.update(decimate=False)
                if params.stripes_only:
                    values.update(outline_layer=False, line_width=.2, outline_width=.2, min_stroke=1., simplify=.05)
            else:
                values = draw.values(dict(style="none", creases=True, crease_angle=35., paper="A4"))
                if params.stripes_only:
                    values = draw.values(dict(values, one_layer=True, hatch_width=.2, outline_width=.2,
                                              crease_width=.2, simplify=.05))
            drawing = draw.engine_fn()(ctx, dst, svg, draw.values(values))
            root = ET.parse(svg).getroot()
            assert root.get("width").endswith("mm") and root.get("height").endswith("mm")
            strokes = next((s.value for s in drawing.stats if s.key == "strokes"), 0)
            assert strokes > 0
            png(svg)
            record["renders"].append(dict(tool=renderer, svg=str(svg), strokes=strokes))
        records.append(record)
        (args.out/"validation.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        print(json.dumps(record, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
