"""Toolkit adapter for the Hatch Maker: model in, signed plotter SVG out, with the drawing as the preview."""
from __future__ import annotations

from pathlib import Path

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.drawing import Drawing, Pen, fmt_duration, fmt_length
from atlas_toolkit.geom.svgout import write_svg

from . import TOOL
from . import stl_hatch_maker as hm

COLORS = {"hatching": "#0B1220", "silhouettes": "#0B1220", "creases": "#2A5FE0", "drawing": "#0B1220"}


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    import trimesh
    m = gm.load_mesh(src)
    faces = len(m.faces)
    if ctx.draft and faces > p.draft_faces:
        v, f = gm.decimate(m.vertices, m.faces, int(p.draft_faces))
        m = gm.clean(trimesh.Trimesh(v, f, process=False))
        ctx.log(f"draft: {faces:,} → {len(m.faces):,} faces".replace(",", " "))
    out = hm.render(m, p, ctx.progress, ctx.log)
    W, H = out["sheet"]
    outputs = []
    if dst is not None:
        ctx.progress(0.95, "writing SVG")
        write_svg(dst, W, H, out["layers"], TOOL, dict(p))
        outputs.append(str(dst))
    pens = [Pen.from_polylines(l.label, l.label, l.polylines, COLORS.get(l.label, "#0B1220"), l.width)
            for l in out["layers"]]
    drawing = Drawing(W, H, (0.0, 0.0, W, H), pens)
    st = drawing.stats()
    stats = [Stat("strokes", "Strokes", st.strokes),
             Stat("ink", "Ink", fmt_length(st.ink), fmt="{}"),
             Stat("travel", "Pen-up travel", fmt_length(out["travel_after"]), before=fmt_length(out["travel_before"]),
                  fmt="{}"),
             Stat("time", "Plot time", fmt_duration(st.seconds()), fmt="{}"),
             Stat("layers", "Pens", len(pens))]
    lines = [src.name,
             f"  view       azimuth {p.azimuth:g}°, elevation {p.elevation:g}°, roll {p.roll:g}°; light {p.light}",
             f"  paper      {p.paper} {p.orientation}, margin {p.margin:g} mm" + ("  (draft)" if ctx.draft else ""),
             f"  style      {hm.STYLES[p.style][0]}"]
    for l in out["layers"]:
        n = sum(len(pl) for pl in l.polylines)
        lines.append(f"  layer      {l.label:<12} {len(l.polylines):>6} strokes {n:>8} points  {l.width:g} mm pen")
    lines += [f"  ink        {fmt_length(st.ink)}",
              f"  travel     {fmt_length(out['travel_before'])} -> {fmt_length(out['travel_after'])} (ordered)",
              f"  plot time  about {fmt_duration(st.seconds())}"]
    if outputs:
        lines.append(f"  saved as   {dst}")
    ctx.progress(1.0)
    return Result(outputs=outputs, stats=stats, report="\n".join(lines),
                  preview={"views": [{"title": "Drawing", "kind": "svg", "drawing": drawing}], "prefer": "Drawing"},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"))
