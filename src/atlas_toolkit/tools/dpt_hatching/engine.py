"""Toolkit adapter for Photo Hatching: settings in, signed plotter SVG out; depth, tone and the drawing as previews."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import depth as gdepth
from atlas_toolkit.geom.drawing import Drawing, Pen, fmt_duration, fmt_length
from atlas_toolkit.geom.image import view
from atlas_toolkit.geom.svgout import write_svg

from . import TOOL
from . import dpt_hatching as dh

COLORS = {"hatching": "#0B1220", "cross-hatching": "#2A5FE0", "counter-hatching": "#2E9E6B",
          "square hatching": "#C9821B",
          "contours": "#5250D0", "outlines": "#0B1220"}


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    out = dh.render(src, p, ctx.progress, ctx.log, draft=ctx.draft, cancel=ctx.check_cancel)
    W, H = out["sheet"]
    outputs = []
    if dst is not None:
        ctx.progress(0.95, "writing SVG")
        write_svg(dst, W, H, out["layers"], TOOL, dict(p))
        outputs.append(str(dst))
        if p.save_depth and p.depth_source != "file":
            outputs.append(str(gdepth.save_png16(dst.with_name(f"{src.stem}_depth.png"), out["depth"])))
    single = len(out["layers"]) <= 2
    pens = [Pen.from_polylines(l.label, l.label, l.polylines,
                               "#0B1220" if single else COLORS.get(l.label, "#0B1220"), l.width) for l in out["layers"]]
    drawing = Drawing(W, H, (0.0, 0.0, W, H), pens)
    st = drawing.stats()
    rect, sheet = out["rect"], (W, H)
    faint = (255 - (255 - out["rgb"]) * 0.35).astype(np.uint8)
    views = [view("Depth", (out["depth"] * 255).astype(np.uint8), rect, sheet),
             view("Tone", (out["tone"] * 255).astype(np.uint8), rect, sheet),
             {"title": "Drawing", "kind": "svg", "drawing": drawing},
             dict(view("Over photo", faint, rect, sheet), pens=pens)]
    tm = out["timings"]
    before, after = out["travel"]
    lines = [src.name,
             f"  picture    {out['size'][0]} × {out['size'][1]} px worked, {out['mm_per_px']:.3f} mm per px, "
             f"{p.paper} {p.orientation}" + ("  (draft)" if ctx.draft else ""),
             f"  depth      {p.depth_source}" + (f": {p.model}, {p.detail} detail" if p.depth_source == "model" else ""),
             f"  style      {p.style}, {p.direction} direction, {p.layers} layer(s), {p.spacing_dark:g}–"
             f"{p.spacing_light:g} mm"]
    for l in out["layers"]:
        lines.append(f"  layer      {l.label:<18} {len(l.polylines):>6} strokes  {l.width:g} mm pen")
    lines += [f"  ink        {fmt_length(st.ink)}, travel {fmt_length(before)} -> {fmt_length(after)} (ordered)",
              f"  plot time  about {fmt_duration(st.seconds())}",
              "  timings    " + ", ".join(f"{k} {v:.1f} s" for k, v in tm.items())]
    if outputs:
        lines.append(f"  saved as   {outputs[0]}")
    ctx.progress(1.0)
    return Result(
        outputs=outputs,
        stats=[Stat("strokes", "Strokes", st.strokes),
               Stat("ink", "Ink", fmt_length(st.ink), fmt="{}"),
               Stat("travel", "Pen-up travel", fmt_length(after), before=fmt_length(before), fmt="{}"),
               Stat("time", "Plot time", fmt_duration(st.seconds()), fmt="{}"),
               Stat("seconds", "Computed in", sum(tm.values()), "s", fmt="{:.1f}")],
        report="\n".join(lines),
        preview={"views": views, "prefer": "Drawing"},
        status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
    )


def make_sample(folder: Path) -> Path:
    """A small synthetic photo: a lit sphere in front of a shaded wall (works with the rough depth)."""
    from PIL import Image
    yy, xx = np.mgrid[0:300, 0:400] / 300.0
    g = 0.55 + 0.3 * xx / 1.33 - 0.15 * yy
    m = (xx - 0.75) ** 2 + (yy - 0.5) ** 2 < 0.09
    nz = np.sqrt(np.clip(0.09 - (xx - 0.75) ** 2 - (yy - 0.5) ** 2, 0, None)) / 0.3
    g[m] = (0.15 + 0.8 * np.clip(0.5 * nz - 0.4 * (xx - 0.75) / 0.3 - 0.4 * (yy - 0.5) / 0.3, 0, 1))[m]
    img = (np.dstack([g, g * 0.95, g * 0.85]) * 255).clip(0, 255).astype(np.uint8)
    path = Path(folder) / "photo_sample.png"
    Image.fromarray(img).save(path)
    return path
