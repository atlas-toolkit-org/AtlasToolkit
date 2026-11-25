"""Toolkit adapter for Plotter Vision: model in, signed plotter SVG out, with the drawing as the preview."""
from __future__ import annotations

from pathlib import Path

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom.drawing import Drawing, Pen, fmt_duration, fmt_length, plot_tiles, saved_lines
from atlas_toolkit.geom.svgout import arrange, pen_mode, travel, write_pens

from . import TOOL
from . import model as pm
from . import plotter_vision as pv

COLORS = {"lines": "#0B1220", "near": "#0B1220", "middle": "#2A5FE0", "far": "#6F7D99", "outline": "#0B1220",
          "hidden": "#C9821B", "red lines": "#E8202A", "blue lines": "#1592E8", "red outline": "#E8202A",
          "blue outline": "#1592E8", "red hidden": "#F08A90", "blue hidden": "#8CC8F0"}


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    ctx.progress(0.02, "reading the model")
    mdl = pm.load(src, int(p.target_faces) if p.decimate else 0, bool(p.fix_winding), ctx.log, bool(p.crossings))
    out = pv.render(mdl, p, ctx.progress, ctx.log, ctx.check_cancel)
    W, H = out["sheet"]
    mode = pen_mode(p)
    layers = arrange(out["layers"], mode, bool(p.optimise))
    before, after = out["travel"]
    if mode == "merged" and len(out["layers"]) > 1:
        after = sum(travel(l.polylines) for l in layers)
    outputs = []
    if dst is not None:
        ctx.progress(0.96, "writing SVG")
        outputs += [str(f) for f in write_pens(dst, W, H, layers, mode, TOOL, dict(p))]
    pens = [Pen.from_polylines(l.label, l.label, l.polylines, COLORS.get(l.label, "#0B1220"), l.width)
            for l in layers]
    drawing = Drawing(W, H, (0.0, 0.0, W, H), pens)
    st = drawing.stats()
    cam = out["camera"]
    secs = sum(out["timings"].values())
    stats = [Stat("strokes", "Strokes", st.strokes),
             *plot_tiles(st, secs, (before, after))]
    look = ", ".join(f"{x:g}" for x in cam.lookat)

    def n(x: int) -> str:
        return f"{x:,}".replace(",", " ")

    lines = [src.name,
             f"  model      {n(mdl.faces_in)} faces"
             + (f", {n(mdl.faces)} after decimation" if mdl.faces != mdl.faces_in else "")
             + f", {'watertight' if mdl.watertight else 'open'}; " + ("both sides" if out["two_sided"] else "front faces"),
             f"  camera     θ {cam.theta:g}°, ψ {cam.psi:g}°, distance {cam.radius:g}, look at ({look}), "
             f"{cam.fov:g}° {'orthographic' if cam.ortho else 'perspective'}"
             + (f", model rolled {cam.roll:g}°" if cam.roll else ""),
             f"  edges      {n(out['edges'])} candidates (flatter than {p.flat_angle:g}° left out), "
             f"{n(out['front_faces'])} faces facing the camera",
             f"  paper      {p.paper} {p.orientation}, margin {p.margin:g} mm" + ("  (draft)" if ctx.draft else "")]
    for l in layers:
        lines.append(f"  layer      {l.label:<14} {len(l.polylines):>6} strokes  {l.width:g} mm pen")
    lines += [f"  ink        {fmt_length(st.ink)}, travel {fmt_length(before)} -> {fmt_length(after)} (ordered)",
              f"  plot time  about {fmt_duration(st.seconds())}",
              "  timings    " + ", ".join(f"{k} {v:.2f} s" for k, v in out["timings"].items())]
    lines += [f"  note       {n}" for n in mdl.notes]
    lines += saved_lines(outputs)
    ctx.progress(1.0)
    return Result(outputs=outputs, stats=stats, report="\n".join(lines),
                  preview={"views": [{"title": "Drawing", "kind": "svg", "drawing": drawing}], "prefer": "Drawing"},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"))
