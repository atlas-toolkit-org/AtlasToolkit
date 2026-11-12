"""Toolkit adapter for svg_shape_outline: options, the outline over the drawing as the preview, stats, report."""
from __future__ import annotations

from pathlib import Path

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom.drawing import Pen, fmt_length
from atlas_toolkit.geom.svg import load, read_drawing, sheet_of, sign_desc, write_run_metadata, write_tree

from . import TOOL
from . import svg_shape_outline as so

OUTLINE_COLOR = "#22BFF0"


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    o = so.Options(method=p.method, layer=p.layer or "", gap=p.gap, concavity=p.concavity, step=p.step,
                   islands=p.islands, holes=p.holes, min_area=p.min_area, offset=p.offset, smooth=p.smooth,
                   output=p.write, width=p.width, flatten=p.flatten, precision=p.precision)
    tree = load(src)
    mpu = sheet_of(tree.getroot())[3]
    drawing = read_drawing(tree)
    ctx.progress(0.1, "reading strokes")
    out, st = so.outline_tree(tree, o, ctx.progress)
    rings = Pen.from_polylines("outline", "Outline", ([(x * mpu, y * mpu) for x, y in r] for r in st.rings),
                               OUTLINE_COLOR, p.width)
    outputs = []
    if dst is not None:
        root = out.getroot()
        sign_desc(root, TOOL.id)
        write_run_metadata(root, TOOL, p)
        write_tree(out, dst)
        outputs.append(str(dst))
    result_drawing = read_drawing(out)
    ctx.progress(1.0)
    report = st.report(src.name, p.method) + f"\n  folder     {src.parent}"
    if outputs:
        report += f"\n  saved as   {dst}"
    return Result(
        outputs=outputs,
        stats=[Stat("parts", "Parts", st.parts, fmt="{:,.0f}"),
               Stat("holes", "Holes", st.holes),
               Stat("length", "Outline", fmt_length(st.length), fmt="{}"),
               Stat("area", "Area", st.area / 100, "cm²", fmt="{:,.1f}")],
        report=report,
        preview={"views": [{"title": "Outline", "kind": "svg", "drawing": drawing, "overlays": [rings]},
                           {"title": "Result", "kind": "svg", "drawing": result_drawing}],
                 "prefer": "Outline"},
        status="Saved" if outputs else "Ready",
    )


def make_sample(folder: Path) -> Path:
    """Two hatched blobs and a curved stroke, in a transformed layer: several parts, curves, transforms."""
    lines = "".join(f'<path d="M {x} 20 Q {x + 6} 45 {x} 70"/>' for x in range(15, 60, 3))
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     width="160mm" height="100mm" viewBox="0 0 160 100">
  <g inkscape:groupmode="layer" inkscape:label="drawing" style="fill:none;stroke:#000;stroke-width:0.3">
    {lines}
    <g transform="translate(100 15)"><circle cx="20" cy="30" r="22"/><circle cx="20" cy="30" r="10"/>
      <line x1="0" y1="30" x2="40" y2="30"/></g>
  </g>
</svg>
"""
    path = Path(folder) / "outline_sample.svg"
    path.write_text(svg, encoding="utf-8")
    return path
