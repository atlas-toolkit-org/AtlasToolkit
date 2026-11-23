"""Toolkit adapter for the Hatch Maker: model in, signed plotter SVG out, with the drawing as the preview."""
from __future__ import annotations

import time
from pathlib import Path

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.drawing import Drawing, Pen, fmt_duration, fmt_length, plot_tiles, saved_lines
from atlas_toolkit.geom.svgout import arrange, pen_mode, travel, write_pens

from . import TOOL
from . import stl_hatch_maker as hm

COLORS = {"hatching": "#0B1220", "silhouettes": "#0B1220", "creases": "#2A5FE0", "drawing": "#0B1220"}


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    import trimesh
    t0 = time.perf_counter()
    m = gm.load_mesh(src)
    faces = len(m.faces)
    bounds = (m.vertices.min(0), m.vertices.max(0))      # the full model's: a draft keeps Draw's framing
    if ctx.draft and faces > p.draft_faces:
        v, f = gm.decimate(m.vertices, m.faces, int(p.draft_faces))
        m = gm.clean(trimesh.Trimesh(v, f, process=False))
        ctx.log(f"draft: {faces:,} → {len(m.faces):,} faces".replace(",", " "))
    out = hm.render(m, p, ctx.progress, ctx.log, bounds=bounds)
    W, H = out["sheet"]
    mode = pen_mode(p)
    layers = arrange(out["layers"], mode, bool(p.optimise))
    after = out["travel_after"]
    if mode == "merged" and len(out["layers"]) > 1:
        after = sum(travel(l.polylines) for l in layers)
    outputs = []
    if dst is not None:
        ctx.progress(0.95, "writing SVG")
        outputs += [str(f) for f in write_pens(dst, W, H, layers, mode, TOOL, dict(p))]
    pens = [Pen.from_polylines(l.label, l.label, l.polylines, COLORS.get(l.label, "#0B1220"), l.width)
            for l in layers]
    drawing = Drawing(W, H, (0.0, 0.0, W, H), pens)
    st = drawing.stats()
    stats = [Stat("strokes", "Strokes", st.strokes),
             *plot_tiles(st, time.perf_counter() - t0, (out["travel_before"], after),
                         [Stat("layers", "Pens", len(pens))])]
    lines = [src.name,
             f"  view       θ {p.theta:g}°, ψ {p.psi:g}°, distance {p.radius:g}, look at "
             f"({', '.join(f'{x:g}' for x in p.lookat)}), roll {p.roll:g}°, orthographic; light {p.light}",
             f"  paper      {p.paper} {p.orientation}, margin {p.margin:g} mm" + ("  (draft)" if ctx.draft else ""),
             f"  style      {hm.STYLES[p.style][0]}"]
    for l in layers:
        n = sum(len(pl) for pl in l.polylines)
        lines.append(f"  layer      {l.label:<12} {len(l.polylines):>6} strokes {n:>8} points  {l.width:g} mm pen")
    lines += [f"  ink        {fmt_length(st.ink)}",
              f"  travel     {fmt_length(out['travel_before'])} -> {fmt_length(after)} (ordered)",
              f"  plot time  about {fmt_duration(st.seconds())}"]
    lines += saved_lines(outputs)
    ctx.progress(1.0)
    return Result(outputs=outputs, stats=stats, report="\n".join(lines),
                  preview={"views": [{"title": "Drawing", "kind": "svg", "drawing": drawing}], "prefer": "Drawing"},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"))
