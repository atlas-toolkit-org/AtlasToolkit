"""Toolkit adapter for Sketch Hatching: model in, ray-cast trace, pen sketch out as a signed two-pen plotter SVG."""
from __future__ import annotations

import copy
import time
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import shading
from atlas_toolkit.geom import sketch as sk
from atlas_toolkit.geom.drawing import Drawing, Pen, fmt_duration, fmt_length
from atlas_toolkit.geom.image import view as raster_view
from atlas_toolkit.geom.svgout import Layer, write_svg

from . import TOOL
from . import sketch_hatching as sh

COLORS = {"fine": "#0B1220", "bold": "#0B1220"}


def config(p, draft: bool) -> dict:
    """The pieces' plate config (Augustus' tuned values) with the tool's settings over it."""
    a = sh.AUGUSTUS
    lines = dict(a["lines"], suggestive=bool(p.suggestive), suggestive_min_strength=float(p.suggestive_strength),
                 valleys=bool(p.valleys), valley_min_strength=float(p.valley_strength), creases=bool(p.creases),
                 light_weighted_outline=bool(p.lost_found))
    style = dict(a["style"], seed=int(p.seed), wobble_mm=float(p.wobble), pass_offset_mm=float(p.outline_weight),
                 double_roles=("outline",) if p.outline_weight > 0 else ())
    tone = dict(a["tone"], ambient=float(p.ambient), key=float(p.key), ao_strength=float(p.ao),
                form_shading=float(p.form_shading), gamma=float(p.gamma), blur_mm=float(p.tone_blur))
    passes = copy.deepcopy(sh.ENGRAVING_PASSES if p.tone_scheme == "engraving" else a["hatching"]["passes"])
    hatching = dict(a["hatching"], enabled=bool(p.hatching), field=p.field, density=float(p.density) * (0.6 if draft
                                                                                                       else 1.0),
                    passes=passes[:int(p.passes)], angle_deg=float(p.angle), max_len_mm=float(p.max_len),
                    min_len_mm=float(p.min_len), edge_gap_mm=float(p.edge_gap), seed=int(p.seed) + 4)
    pens = [{"name": "fine", "width_mm": float(p.fine_width), "order": 1, "min_len_mm": 1.5, "direction": "any",
             "roles": list(sh.FINE_ROLES)},
            {"name": "bold", "width_mm": float(p.bold_width), "order": 2, "min_len_mm": 1.5, "direction": "any",
             "roles": list(sh.BOLD_ROLES)}]
    return {"lines": lines, "style": style, "tone": tone, "hatching": hatching, "pens": pens}


def resolve_focus(view, p, notes):
    """The focus anchor on the surface (2-tall units) from a point given as fractions of the drawing's box."""
    if not p.focus or view.empty:
        return []
    x0, y0, x1, y1 = view.proj.bounds(view.V)
    xy = np.array([[x0 + p.focus_u * (x1 - x0), y0 + p.focus_v * (y1 - y0)]])
    O, D = view.proj.rays(xy)
    t, face, u, v = view.caster.intersect(O, D)
    if face[0] < 0:
        notes.append(f"the focus point ({p.focus_u:g}, {p.focus_v:g}) misses the model: no focus")
        return []
    anchor = view.caster.points(face, u, v)[0]
    return [{"anchor": anchor.tolist(), "radius": float(p.focus_radius) / 50.0, "weight": 1.0}]


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    t0 = time.perf_counter()
    timings = {}
    draft = ctx.draft
    ctx.progress(0.02, "reading the model")
    view = shading.View3D(src, p, draft, int(p.draft_faces), ctx.log)
    W, H = view.paper.sheet
    notes: list = []
    layers, n_lines, n_hatch, coverage = [], 0, 0, 0.0
    extra_views = []
    focus = []
    if view.empty:
        notes.append("the model is outside the paper's drawing area: aim the camera in the Aim tab (F fits it)")
    else:
        light = view.proj.light(p.light_az, p.light_el) if p.light_frame == "camera" else \
            shading.model_light(p.light_az, p.light_el, view.frame.rotate)
        focus = resolve_focus(view, p, notes)
        a = dict(sh.AUGUSTUS["analysis"], crease_angle=float(p.crease_angle), ao_rays=(8 if draft else 24)
                 if p.ao > 0 else 0, focus_line_smooth=6, focus_field_smooth=30)
        a = view.passes(a, notes)
        a["focus"] = tuple((tuple(f["anchor"]), f["radius"], f["weight"]) for f in focus)
        ctx.progress(0.05, "analysing the model")
        t1 = time.perf_counter()
        an = shading.cached_analysis(view, dict(a, focus=[{"anchor": f[0], "radius": f[1], "weight": f[2]}
                                                          for f in a["focus"]]),
                                     ctx.check_cancel, lambda f, m: ctx.progress(0.05 + 0.25 * f, m))
        timings["analysis"] = time.perf_counter() - t1
        t1 = time.perf_counter()
        ctx.progress(0.3, "tracing")
        tp = dict(sh.AUGUSTUS["trace"], px_per_mm=float(p.px_per_mm) * (0.5 if draft else 1.0),
                  shadow_samples=(1 if draft else 4) if p.shadows else 0, softness_deg=float(p.softness),
                  valleys=bool(p.valleys), ridges=False,
                  focus_light=shading.model_light(p.focus_light_az, p.focus_light_el, view.frame.rotate) if focus else None)
        tr = shading.trace(an, view.caster, view.proj, view.box, light, ctx.check_cancel,
                           lambda f, m: ctx.progress(0.3 + 0.3 * f, m), **tp)
        timings["trace"] = time.perf_counter() - t1
        t1 = time.perf_counter()
        ctx.progress(0.62, "drawing")
        pc = sh.apply_complexity(config(p, draft), float(p.complexity))
        strokes, raw = sh.budget_layers(tr, pc, float(p.max_ink), 0, ctx.check_cancel)
        timings["drawing"] = time.perf_counter() - t1
        gb = tr["gbuffer"]
        tone = sh.tone_map(gb, **pc["tone"])
        rect = (gb["x0"], gb["y0"], gb["shape"][1] * gb["px"], gb["shape"][0] * gb["px"])
        extra_views.append(raster_view("Tone", (np.clip(tone, 0, 1) * 255).astype(np.uint8), rect, (W, H)))
        n_hatch = sum(1 for s in strokes if s["role"].startswith("hatch"))
        n_lines = len(strokes) - n_hatch
        layers = [Layer(L["name"], [pl.tolist() for pl in L["strokes"]], COLORS.get(L["name"], "#000000"),
                        L["width"]) for L in raw if L["strokes"]]
        if layers:
            coverage = sk.coverage([{"width": L.width, "strokes": [np.asarray(pl) for pl in L.polylines]}
                                    for L in layers], (W, H), px_per_mm=6.0 if draft else 10.0)
            if coverage > 0.7:
                notes.append(f"ink coverage reaches {coverage:.2f} in a 5 mm square (the pieces kept it under 0.7): "
                             "lower the density or raise the ambient light")
    pens = [Pen.from_polylines(L.label, L.label, L.polylines, "#0B1220", L.width) for L in layers]
    drawing = Drawing(W, H, (0.0, 0.0, W, H), pens)
    st = drawing.stats()
    outputs = []
    if dst is not None:
        ctx.progress(0.96, "writing SVG")
        write_svg(dst, W, H, layers, TOOL, dict(p))
        outputs.append(str(dst))
    secs = time.perf_counter() - t0

    def n(x) -> str:
        return f"{x:,}".replace(",", " ")

    lines = [src.name,
             f"  model      {n(view.faces_in)} faces" + (f", {n(len(view.F))} in the preview" if len(view.F) != view.faces_in
                                                       else "") + f"; mean edge {view.edge:.4g} of 2",
             f"  frame      {view.frame.note}",
             f"  camera     θ {p.theta:g}°, ψ {p.psi:g}°, distance {p.radius:g}, {p.fov:g}° "
             f"{'orthographic' if view.cam.ortho else 'perspective'}",
             f"  light      {p.light_az:g}° / {p.light_el:g}° ({p.light_frame})"
             + (", cast shadows" if p.shadows else "") + (f", ambient occlusion {p.ao:g}" if p.ao else ""),
             f"  focus      " + (f"at ({p.focus_u:g}, {p.focus_v:g}), radius {p.focus_radius:g}% h, portrait light "
                                 f"{p.focus_light_az:g}° / {p.focus_light_el:g}°" if focus else "none"),
             f"  strokes    {n(n_lines)} lines, {n(n_hatch)} hatching (complexity {p.complexity:g}, density "
             f"{p.density:g}{', draft' if draft else ''})"]
    for L in layers:
        lines.append(f"  pen        {L.label:<6} {L.width:g} mm: {n(len(L.polylines))} strokes")
    lines += [f"  ink        {fmt_length(st.ink)}, plot time about {fmt_duration(st.seconds())}",
              f"  coverage   max {coverage:.2f} of a 5 mm square",
              "  timings    " + ", ".join(f"{k} {v:.1f} s" for k, v in timings.items())]
    lines += [f"  note       {x}" for x in notes]
    if outputs:
        lines.append(f"  saved as   {dst}")
    ctx.progress(1.0)
    return Result(outputs=outputs,
                  stats=[Stat("strokes", "Strokes", st.strokes),
                         Stat("ink", "Ink", fmt_length(st.ink), fmt="{}"),
                         Stat("time", "Plot time", fmt_duration(st.seconds()), fmt="{}"),
                         Stat("coverage", "Max coverage", coverage, fmt="{:.2f}",
                              tone="warning" if coverage > 0.7 else ""),
                         Stat("seconds", "Computed in", secs, "s", fmt="{:.1f}")],
                  report="\n".join(lines),
                  preview={"views": [{"title": "Drawing", "kind": "svg", "drawing": drawing}] + extra_views,
                           "prefer": "Drawing"},
                  status="Saved" if outputs else ("Draft" if draft else "Previewed"),
                  data={"lines": n_lines, "hatch": n_hatch, "coverage": coverage, "timings": timings})
