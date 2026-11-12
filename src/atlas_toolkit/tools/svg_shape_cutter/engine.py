"""Toolkit adapter for svg_shape_cutter: options, previews (mask outline over the result), stats and report."""
from __future__ import annotations

from pathlib import Path

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom.drawing import Pen, fmt_length
from atlas_toolkit.geom.svg import read_drawing, sheet_of, sign_desc, write_run_metadata, write_tree

from . import TOOL
from . import svg_shape_cutter as sc

MASK_COLOR = "#22BFF0"


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    opts = sc.Options(shape_source=p.shape_source, shape_layer=p.shape_layer or "", shape_file=p.shape_file or "",
                      keep=p.keep, keep_shape=p.keep_shape, merge=p.merge, min_length=p.min_length,
                      flatten=p.flatten, precision=p.precision)
    tree = sc.load(src)
    before = read_drawing(tree)
    ctx.progress(0.05, "building the shape")
    st = sc.cut_tree(tree, opts, progress=lambda i, n: ctx.progress(0.1 + 0.8 * i / max(n, 1), "clipping"),
                     log=ctx.warn)
    for n in st.notes:
        ctx.log(n)
    after = read_drawing(tree)
    mpu = sheet_of(tree.getroot())[3]
    mask = Pen.from_polylines("mask", "Shape", ([(x * mpu, y * mpu) for x, y in ring] for ring in st.mask),
                              MASK_COLOR, 0.4)
    changed = bool(st.clipped or st.removed or (not opts.keep_shape and st.shape_elements))
    outputs = []
    if dst is not None and changed:
        root = tree.getroot()
        sign_desc(root, TOOL.id)
        write_run_metadata(root, TOOL, p)
        write_tree(tree, dst)
        outputs.append(str(dst))
    ctx.progress(1.0)
    a, b = after.stats(), before.stats()
    stats = [
        Stat("clipped", "Clipped", st.clipped + st.removed, fmt="{:,.0f}",
             tone="" if (st.clipped or st.removed) else "warning"),
        Stat("strokes", "Strokes", a.strokes, before=b.strokes),
        Stat("ink", "Ink", fmt_length(a.ink), before=fmt_length(b.ink), fmt="{}"),
        Stat("shape", "Shape", f"{st.shape_parts} part{'s' if st.shape_parts != 1 else ''}, "
                               f"{st.shape_holes} hole{'s' if st.shape_holes != 1 else ''}", fmt="{}"),
        Stat("travel", "Pen-up travel", fmt_length(a.travel), before=fmt_length(b.travel), fmt="{}"),
    ]
    report = st.report(src.name) + f"\n  folder     {src.parent}"
    if outputs:
        report += f"\n  saved as   {dst}"
    return Result(
        outputs=outputs, stats=stats, report=report, changed=changed,
        status=("Saved" if outputs else "Ready") if changed else "Nothing to cut",
        preview={"views": [{"title": "Before", "kind": "svg", "drawing": before, "overlays": [mask]},
                           {"title": "After", "kind": "svg", "drawing": after},
                           {"title": "With shape", "kind": "svg", "drawing": after, "overlays": [mask]}],
                 "prefer": "With shape"},
    )


def make_sample(folder: Path) -> Path:
    """A ring-shaped 'shape' layer (an island with a hole) over a 'hatch' layer of diagonal lines."""
    lines = "".join(f'<path d="M {x} 0 L {x + 90} 90"/>' for x in range(-90, 120, 4))
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     width="120mm" height="90mm" viewBox="0 0 120 90">
  <g inkscape:groupmode="layer" inkscape:label="shape" style="fill:none;stroke:#000;stroke-width:0.3">
    <circle cx="60" cy="45" r="35"/><circle cx="60" cy="45" r="15"/>
  </g>
  <g inkscape:groupmode="layer" inkscape:label="hatch" style="fill:none;stroke:#2A5FE0;stroke-width:0.3">{lines}</g>
</svg>
"""
    path = Path(folder) / "shape_sample.svg"
    path.write_text(svg, encoding="utf-8")
    return path
