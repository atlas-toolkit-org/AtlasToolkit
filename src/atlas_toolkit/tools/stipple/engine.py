"""Toolkit adapter for Stipple: model in, ray-cast shading, blue-noise dots out as a signed plotter SVG."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import shading
from atlas_toolkit.geom import sketch as sk
from atlas_toolkit.geom.drawing import Drawing, Pen, fmt_duration, fmt_length
from atlas_toolkit.geom.image import view as raster_view
from atlas_toolkit.geom.svgout import Layer, order_polylines, write_svg

from . import TOOL
from . import stipple as st

LIFT_S = 0.25          # plot-time estimate per dot (geom/drawing.PlotStats)


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    t0 = time.perf_counter()
    draft = ctx.draft
    ctx.progress(0.02, "reading the model")
    view = shading.View3D(src, p, draft, int(p.draft_faces), ctx.log)
    W, H = view.paper.sheet
    notes: list = []
    dots_pls, outline_pls, expected, coverage = [], [], 0.0, 0.0
    extra_views = []
    if view.empty:
        notes.append("the model is outside the paper's drawing area: aim the camera in the Aim tab (F fits it)")
    else:
        light = view.proj.light(p.light_az, p.light_el) if p.light_frame == "camera" else \
            shading.model_light(p.light_az, p.light_el, view.frame.rotate)
        ao_rays = (8 if draft else 16) if p.ao > 0 else 0
        params = view.passes(dict(contour_smooth=2, tone_smooth=2), notes)
        ctx.progress(0.05, "analysing the model")
        an = shading.cached_analysis(view, dict(params, curvature=False, ao_rays=ao_rays, ao_distance=0.18),
                                     ctx.check_cancel, lambda f, m: ctx.progress(0.05 + 0.15 * f, m))
        ctx.progress(0.2, "shading")
        px_mm = float(p.px_per_mm) * (0.6 if draft else 1.0)
        tr = shading.trace(an, view.caster, view.proj, view.box, light, ctx.check_cancel,
                           lambda f, m: ctx.progress(0.2 + 0.3 * f, m), px_per_mm=px_mm,
                           shadow_samples=(1 if draft else 4) if p.shadows else 0, softness_deg=float(p.softness),
                           lines=False)
        gb = tr["gbuffer"]
        Hg, Wg, px = gb["shape"][0], gb["shape"][1], gb["px"]
        c = dict(ambient=float(p.ambient), key=1.0, wrap=float(p.wrap), bounce=float(p.bounce),
                 gamma=float(p.gamma), ao_strength=float(p.ao), shadows=bool(p.shadows), rim_dark=float(p.rim_dark),
                 rim_dark_lo=float(p.rim_dark_lo), rim_dark_hl=(float(p.rim_dark_hl_lo), float(p.rim_dark_hl_hi)))
        tone, nl = st.tone(gb, light, c)
        dark = 1.0 - tone
        rect = (gb["x0"], gb["y0"], Wg * px, Hg * px)
        extra_views.append(raster_view("Tone", (np.clip(tone, 0, 1) * 255).astype(np.uint8), rect, (W, H)))
        nv = np.abs(gb["ndotv"]).reshape(Hg, Wg)
        rim = shading.smoothstep(p.rim_lo, p.rim_hi, 1.0 - nv) * (nl > 0)
        mask = gb["hit"].reshape(Hg, Wg).copy()
        # dots stay inside the drawing area with their whole radius
        x0a, y0a, wa, ha = view.paper.area
        m = float(p.dot_radius) + 0.1
        xs = gb["x0"] + (np.arange(Wg) + 0.5) * px
        ys = gb["y0"] + (np.arange(Hg) + 0.5) * px
        mask &= ((xs[None, :] > x0a + m) & (xs[None, :] < x0a + wa - m) &
                 (ys[:, None] > y0a + m) & (ys[:, None] < y0a + ha - m))
        if p.outline != "none":
            outline_pls = [sk.rdp(sk.smooth_mm(xy, 0.35, closed), 0.03) for xy, closed in sk.mask_outline(gb)]
            outline_pls = [pl for pl in outline_pls if sk.plen(pl) >= 1.5]
            keep_out = st.keep_out_mask(outline_pls, gb["x0"], gb["y0"], px, (Hg, Wg),
                                        float(p.clear_mm) + float(p.dot_radius) + float(p.outline_width) / 2)
            mask &= ~keep_out
        ctx.progress(0.55, "density")
        rho = st.dot_density(dark, mask, px, float(p.min_spacing), float(p.void), float(p.density_gamma),
                             rim if p.rim_spacing > 0 else None, float(p.rim_spacing) or 3.0, float(p.noise_amp),
                             float(p.noise_scale), int(p.seed))
        expected = float(rho.sum() * px * px)
        budget = int(p.budget) // (3 if draft else 1)
        ctx.progress(0.6, f"placing up to {budget:,} dots".replace(",", " "))
        pts = st.poisson_dots(rho, gb["x0"], gb["y0"], px, budget, seed=int(p.seed), cancel=ctx.check_cancel)
        if expected > budget * 1.02:
            notes.append(f"the tone asks for about {expected:,.0f} dots and the budget kept {len(pts):,}: every tone "
                         f"is thinned alike (raise the budget, or widen the darkest spacing)".replace(",", " "))
        ctx.progress(0.85, "ordering")
        dots_pls = [d.tolist() for d in st.dot_paths(pts, float(p.dot_radius), int(p.dot_sides))]
        if p.optimise:
            dots_pls = order_polylines(dots_pls)
        if outline_pls:
            outline_pls = order_polylines([pl.tolist() for pl in outline_pls])
    layers = [Layer("dots", dots_pls, "#000000", float(p.dot_width))]
    if outline_pls:
        layers.append(Layer("outline", outline_pls, "#000000", float(p.outline_width)))
    pens = [Pen.from_polylines(L.label, L.label, L.polylines, "#0B1220", L.width) for L in layers]
    drawing = Drawing(W, H, (0.0, 0.0, W, H), pens)
    stats_ = drawing.stats()
    if dots_pls:
        coverage = sk.coverage([{"width": L.width, "strokes": [np.asarray(pl) for pl in L.polylines]}
                                for L in layers], (W, H), px_per_mm=6.0 if draft else 10.0)
        if coverage > 0.7:
            notes.append(f"ink coverage reaches {coverage:.2f} in a 5 mm square (the pieces kept it under 0.7): "
                         "widen the darkest spacing or use smaller dots")
    outputs = []
    if dst is not None:
        ctx.progress(0.95, "writing SVG")
        write_svg(dst, W, H, layers, TOOL, dict(p))
        outputs.append(str(dst))
    secs = time.perf_counter() - t0

    def n(x) -> str:
        return f"{x:,}".replace(",", " ")

    cam = view.cam
    lines = [src.name,
             f"  model      {n(view.faces_in)} faces" + (f", {n(len(view.F))} in the preview" if len(view.F) != view.faces_in
                                                       else ""),
             f"  frame      {view.frame.note}",
             f"  camera     θ {p.theta:g}°, ψ {p.psi:g}°, distance {p.radius:g}, {p.fov:g}° "
             f"{'orthographic' if cam.ortho else 'perspective'}",
             f"  light      {p.light_az:g}° / {p.light_el:g}° ({p.light_frame}), "
             + ("cast shadows" if p.shadows else "no cast shadows") + (f", ambient occlusion {p.ao:g}" if p.ao else ""),
             f"  dots       {n(len(dots_pls))} of a budget of {n(int(p.budget))} (the tone asks for ~{n(round(expected))}), "
             f"r {p.dot_radius:g} mm, {p.dot_width:g} mm pen",
             f"  coverage   max {coverage:.2f} of a 5 mm square",
             f"  plot time  about {fmt_duration(stats_.ink / 25.0 + stats_.travel / 60.0 + stats_.strokes * LIFT_S)}"]
    lines += [f"  note       {x}" for x in notes]
    if outputs:
        lines.append(f"  saved as   {dst}")
    ctx.progress(1.0)
    return Result(outputs=outputs,
                  stats=[Stat("dots", "Dots", len(dots_pls)),
                         Stat("time", "Plot time", fmt_duration(stats_.seconds()), fmt="{}"),
                         Stat("travel", "Pen-up travel", fmt_length(stats_.travel), fmt="{}"),
                         Stat("coverage", "Max coverage", coverage, fmt="{:.2f}",
                              tone="warning" if coverage > 0.7 else ""),
                         Stat("seconds", "Computed in", secs, "s", fmt="{:.1f}")],
                  report="\n".join(lines),
                  preview={"views": [{"title": "Drawing", "kind": "svg", "drawing": drawing}] + extra_views,
                           "prefer": "Drawing"},
                  status="Saved" if outputs else ("Draft" if draft else "Previewed"),
                  data={"dots": len(dots_pls), "expected": expected, "coverage": coverage})
