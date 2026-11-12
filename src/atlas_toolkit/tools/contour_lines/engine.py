"""Toolkit adapter for Contour Lines: model in, signed plotter SVG out, with the drawing as the preview."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom import raycast as rc
from atlas_toolkit.geom.camera import camera_from, frame_for
from atlas_toolkit.geom.drawing import Drawing, Pen, fmt_duration, fmt_length
from atlas_toolkit.geom.svgout import Layer, write_svg

from . import TOOL
from . import contour_lines as cl

EPS = 5e-4            # visibility slack, of the frame's size (100): the Blender pieces' 1e-3 of a 2-tall head


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    t = {}
    t0 = time.perf_counter()
    ctx.progress(0.02, "reading the model")
    mesh = gm.load_mesh(src)
    frame = frame3d.frame_for(src, p, V=mesh.vertices)
    V = frame.apply(mesh.vertices)
    F = np.asarray(mesh.faces, dtype=np.int64)
    faces_in = len(F)
    draft = ctx.draft
    if draft:
        V, F = gm.simplify_faces(V, F, int(p.draft_faces), ctx.log)
    pf = frame_for(p.paper, p.orientation, p.margin, (p.get("paper_width", 200.), p.get("paper_height", 200.)))
    cam = camera_from(p)
    mpu = cam.mm_per_unit(pf)
    spacing = float(p.spacing_mm) * (1.6 if draft else 1.0)
    step = float(p.step_mm) * (2.0 if draft else 1.0)
    heights = cl.slice_heights(float(V[:, 2].min()), float(V[:, 2].max()), spacing / mpu, frame.floor,
                               float(p.slice_offset))
    t["load"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    ctx.progress(0.08, f"slicing at {len(heights)} heights")
    contours = cl.slice_mesh(V, F, heights, cancel=ctx.check_cancel)
    t["slice"] = time.perf_counter() - t1

    t1 = time.perf_counter()
    ctx.progress(0.3, "removing hidden lines")
    caster = rc.Caster(V, F) if p.hidden else None
    runs = cl.trace(V, F, cam, contours, step / mpu, EPS * 100.0, 4 if draft else 8, bool(p.hidden), caster)
    t["hidden lines"] = time.perf_counter() - t1

    t1 = time.perf_counter()
    ctx.progress(0.6, "laying out strokes")
    runs_mm = [(ci, vis, piece) for ci, vis, P in runs for piece in cl.to_paper(cam, pf, P)]
    strokes = cl.bridge(runs_mm, float(p.gap_bridge))
    ctx.progress(0.7, "tapering converging lines")
    strokes = cl.taper(strokes, float(p.min_gap), cancel=ctx.check_cancel)
    ctx.progress(0.85, "joining and ordering")
    pls, before, after = cl.chain(strokes, float(p.join_same), float(p.join_any), float(p.simplify),
                                  float(p.min_len), bool(p.optimise))
    t["layout"] = time.perf_counter() - t1

    W, H = pf.sheet
    outputs = []
    if dst is not None:
        ctx.progress(0.96, "writing SVG")
        write_svg(dst, W, H, [Layer("contours", pls, "#000000", float(p.line_width))], TOOL, dict(p))
        outputs.append(str(dst))
    pen = Pen.from_polylines("contours", "contours", pls, "#0B1220", float(p.line_width))
    drawing = Drawing(W, H, (0.0, 0.0, W, H), [pen])
    st = drawing.stats()
    misses = cl.near_misses(pls, float(p.line_width))
    secs = time.perf_counter() - t0

    def n(x: int) -> str:
        return f"{x:,}".replace(",", " ")

    look = ", ".join(f"{x:g}" for x in cam.lookat)
    lines = [src.name,
             f"  model      {n(faces_in)} faces" + (f", {n(len(F))} in the preview" if len(F) != faces_in else ""),
             f"  frame      {frame.note}",
             f"  camera     θ {cam.theta:g}°, ψ {cam.psi:g}°, distance {cam.radius:g}, look at ({look}), "
             f"{cam.fov:g}° {'orthographic' if cam.ortho else 'perspective'}",
             f"  slices     {len(heights)} planes every {spacing:g} mm on paper ({spacing / mpu:.3g} units), "
             f"{n(len(contours))} contours" + ("  (draft: wider)" if draft else ""),
             f"  scale      {mpu:.3g} mm per unit at the look-at point; the model is "
             f"{frame.height * mpu:.0f} mm tall there",
             f"  paper      {p.paper} {p.orientation}, margin {p.margin:g} mm, {p.line_width:g} mm pen",
             f"  strokes    {n(len(pls))} (shortest {p.min_len:g} mm), ink {fmt_length(st.ink)}, travel "
             f"{fmt_length(before)} -> {fmt_length(after)}",
             f"  plot time  about {fmt_duration(st.seconds())}",
             f"  check      every stroke inside the margins, none under {p.min_len:g} mm; "
             f"{misses} pen-up move(s) shorter than the pen",
             "  timings    " + ", ".join(f"{k} {v:.2f} s" for k, v in t.items())]
    if outputs:
        lines.append(f"  saved as   {dst}")
    stats = [Stat("strokes", "Strokes", st.strokes),
             Stat("slices", "Slices", len(heights)),
             Stat("ink", "Ink", fmt_length(st.ink), fmt="{}"),
             Stat("time", "Plot time", fmt_duration(st.seconds()), fmt="{}"),
             Stat("seconds", "Computed in", secs, "s", fmt="{:.1f}")]
    ctx.progress(1.0)
    return Result(outputs=outputs, stats=stats, report="\n".join(lines),
                  preview={"views": [{"title": "Drawing", "kind": "svg", "drawing": drawing}], "prefer": "Drawing"},
                  status="Saved" if outputs else ("Draft" if draft else "Previewed"),
                  data={"slices": len(heights), "contours": len(contours), "mm_per_unit": mpu,
                        "heights": heights.tolist(), "frame": frame.key(), "travel": (before, after)})
