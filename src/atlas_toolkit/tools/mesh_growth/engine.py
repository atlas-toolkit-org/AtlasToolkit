"""Toolkit adapter for Reaction-Diffusion Growth: load, set up the model's frame, fit a simulation mesh to the
wavelength, grow the pattern once (capturing every stage), carry it to the output mesh as relief, publish signed
meshes."""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d, influence, stages
from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import mesh_growth as mg

SIM_VERTICES = 800_000           # simulation mesh limit (Run refuses more; Preview lowers the detail)
RELIEF_FACES = 4_000_000         # output mesh budget for the relief refinement
DENSE = 1.5                      # source meshes this many times denser than the simulation needs are simplified
DRAFT_DETAIL = 5.0               # Preview's samples per wavelength
EDGE_SLACK = 1.4                 # split edges longer than this x the spacing (midpoint splits land near it)
NS_PER_VERTEX_STEP = 30          # rough simulation cost, for the log


def _n(x) -> str:
    """Thousands with a thin space: 118 060."""
    return f"{int(x):,}".replace(",", " ")


def stage_steps(n: int, steps: int) -> list[int]:
    """The step of each stage: 0 .. steps, closer together early on (amount^1.5), where the pattern changes most."""
    return [int(round(a ** 1.5 * steps)) for a in stages.amounts(n, 1.0)]


def _blend(z, lo, height, a, b):
    za, zb = lo + a / 100.0 * height, lo + b / 100.0 * height
    if abs(zb - za) < 1e-9:
        return (z >= za).astype(np.float64)
    t = np.clip((z - za) / (zb - za), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    if ctx.dry_run:
        dst = None
    if dst is not None and dst.resolve() == Path(src).resolve():
        raise ValueError("the output must not replace the input model")
    ctx.progress(0.02, "loading model")
    mesh = gm.load_mesh(src)
    up, front, rot, source = frame3d.resolve_axes(src, p, mesh.extents)
    frame = frame3d.fit_frame(mesh.vertices, up, front, rot, mode="height", size=frame3d.MODEL_HEIGHT)
    V0 = frame.apply(mesh.vertices)
    F0 = np.asarray(mesh.faces, dtype=np.int64)
    faces_in = len(F0)
    if ctx.draft:
        V0, F0 = gm.simplify_faces(V0, F0, int(p.draft_faces), ctx.log)
    lo, hi = frame.lo, frame.hi
    height = frame.height
    lam = float(p.wavelength) / 100.0 * height
    detail = min(float(p.detail), DRAFT_DETAIL) if ctx.draft else float(p.detail)
    spacing = lam / detail
    area = mg.surface_area(V0, F0)
    if area <= 0:
        raise ValueError("the model has no surface area")

    # ---- the simulation mesh: about `detail` vertices per wavelength
    notes = []
    need = mg.faces_for_spacing(area, spacing)
    if need / 2 > SIM_VERTICES:
        msg = (f"a {p.wavelength:g}% h wavelength at {detail:g} samples needs ~{_n(need // 2)} simulation vertices "
               f"(limit {_n(SIM_VERTICES)}): raise Wavelength to "
               f"{p.wavelength * math.sqrt(need / 2 / SIM_VERTICES):.2g}% h or lower Simulation detail")
        if not ctx.draft:
            raise ValueError(msg)
        ctx.warn(msg)
        spacing *= math.sqrt(need / 2 / SIM_VERTICES)
        need = mg.faces_for_spacing(area, spacing)
    Vs, Fs = V0, F0
    simplified = False
    if p.refine:
        if len(F0) > DENSE * need:
            ctx.progress(0.04, "simplifying the simulation mesh")
            Vs, Fs = gm.simplify_faces(V0, F0, need)
            simplified = True
        ctx.progress(0.06, "fitting the mesh to the wavelength")
        Vs, Fs, _, _ = mg.refine_with(Vs, Fs, None, spacing * EDGE_SLACK, RELIEF_FACES)
    n = len(Vs)
    if n > SIM_VERTICES and not ctx.draft:
        raise ValueError(f"the simulation mesh has {_n(n)} vertices (limit {_n(SIM_VERTICES)}): raise Wavelength "
                         f"or lower Simulation detail")
    E = mg.edges_of(Fs)
    mean_edge = float(np.linalg.norm(Vs[E[:, 0]] - Vs[E[:, 1]], axis=1).mean()) if len(E) else spacing
    if mean_edge > lam / 3.5:
        ctx.warn(f"the mesh is coarse for this wavelength ({lam / mean_edge:.1f} samples per wavelength): turn on "
                 f"'Fit the mesh to the wavelength' or raise Wavelength")

    # ---- operator, chemistry, start
    ctx.progress(0.08, "building the surface operator")
    Ee, w, A = mg.cotan_weights(Vs, Fs)
    S, has = mg.neighbour_matrix(n, Fs)
    Ns = None
    if p.align != "none" and p.align_strength > 0:
        Ns = mg.smooth_normals(Vs, Fs, S, has, 4)
        w = mg.anisotropic(Vs, Ee, w, mg.tangent_direction(Vs, Ns, p.align), float(p.align_strength))
    op = mg.Operator.build(n, Ee, w, A)
    act = None if (p.influence or "all") == "all" else influence.weights(Vs, p, lo, hi)
    blend = (_blend(Vs[:, 2], lo[2], height, float(p.grad_from), float(p.grad_to)) if p.second != "none"
             else None)
    chem = mg.chemistry(n, p.pattern, lam, feed=p.feed, kill=p.kill, second=p.second, blend=blend, active=act,
                        ratio=float(p.du_dv))
    steps = max(1, int(round(float(p.growth) * 1000)))
    start = mg.seed_state(Vs, A, lam, float(p.density), int(p.seed), p.origin,
                          None if act is None else (act > 0.5).astype(np.float64), steps)

    # ---- grow once, capturing every stage
    capture = stage_steps(int(p.stages), steps)
    nst = len(capture)
    est = n * steps * NS_PER_VERTEX_STEP * 1e-9
    ctx.log(f"growing {_n(steps)} steps on {_n(n)} vertices (about {est:.0f} s)")
    t0 = time.perf_counter()

    def grow_progress(f):
        ctx.progress(0.1 + 0.65 * f, f"growing: step {_n(round(f * steps))} of {_n(steps)}")
    states = mg.simulate(op, chem, start, steps, capture, grow_progress)
    took = time.perf_counter() - t0
    V_states = np.column_stack([states[c] for c in capture]).astype(np.float64)
    if not np.isfinite(V_states).all():                  # pragma: no cover (the scheme is bounded)
        raise RuntimeError("the simulation diverged")

    # ---- the relief mesh and the pattern on it
    ctx.progress(0.77, "carrying the pattern to the surface")
    relief_detail = detail if ctx.draft else max(float(p.relief_detail), detail)
    r_spacing = lam / relief_detail
    if simplified:
        Vr, Fr = V0, F0
        if p.refine and not ctx.draft:
            Vr, Fr, _, _ = mg.refine_with(Vr, Fr, None, r_spacing * EDGE_SLACK, RELIEF_FACES)
        vals = mg.transfer(Vs, V_states, Vr, spacing)
    elif p.refine and not ctx.draft and relief_detail > detail * 1.05:
        Vr, Fr, vals, _ = mg.refine_with(Vs, Fs, V_states, r_spacing * EDGE_SLACK, RELIEF_FACES)
    else:
        Vr, Fr, vals = Vs, Fs, V_states
    m = len(Vr)
    Sr, hasr = (S, has) if Vr is Vs else mg.neighbour_matrix(m, Fr)
    r_mean = mean_edge if Vr is Vs else float(np.linalg.norm(Vr[Fr[:, 0]] - Vr[Fr[:, 1]], axis=1).mean())
    per_lam = lam / max(r_mean, 1e-12)
    soften_passes = int(round(float(p.soften) * per_lam * 0.5))
    if soften_passes:
        vals = mg.smooth(vals, Sr, hasr, soften_passes)
    ctx.progress(0.82, "rest normals")
    Nr = mg.smooth_normals(Vr, Fr, Sr, hasr, max(2, int(round(per_lam * 0.6))))
    wr = (np.ones(m) if (p.influence or "all") == "all" else influence.weights(Vr, p, lo, hi))
    wr = wr * mg.rim_fade(Vr, Fr, 0.5 * lam)
    lv_lo, lv_hi = mg.levels(vals[:, -1], wr)
    depth = float(p.depth) / 100.0 * height

    manifest = {"tool": TOOL.id, "tool_version": TOOL.version, "source": Path(src).name,
                "frame": {"up": up, "front": front, "rotate": [float(a) for a in rot]}}
    height_model = height / frame.scale
    import trimesh
    rows = []
    A_r = _areas(Vr, Fr)
    inside = wr > 0.5
    out = stages.StageOutput(ctx, TOOL, dst, nst, p.layout, p.axis, float(p.gap) / 100.0 * height_model,
                             p.save_stages, manifest)
    with out:
        for i, c in enumerate(capture):
            ctx.progress(0.85 + 0.1 * i / nst, f"stage {i + 1} of {nst}")
            t = mg.shape(vals[:, i], lv_lo, lv_hi, float(p.sharpness), float(p.balance))
            if c == 0 or depth == 0:
                off = np.zeros(m)
            else:
                off = mg.relief_offsets(t, p.relief) * wr * depth
            X = Vr + Nr * off[:, None]
            cover = 0.0 if not off.any() else float(A_r @ ((t > 0.5) & inside) / max(A_r @ inside, 1e-12) * 100)
            rows.append((i, c, cover, float(np.abs(off).max()) / height * 100.0 if m else 0.0))
            out.add(i, trimesh.Trimesh(frame.inverse(X), Fr, process=False),
                    {"amount": round(c / steps, 6), "step": c, "label": f"stage {i}"})
            ctx.check_cancel()
        outputs = out.finish()
    primary = out.primary

    F_, k_, _ = mg.pattern_values(p.pattern, p.feed, p.kill)
    pat = f"{p.pattern} (F {F_:g}, k {k_:g})"
    if p.second != "none":
        F2, k2, _ = mg.pattern_values(p.second, p.feed, p.kill)
        pat += f" -> {p.second} (F {F2:g}, k {k2:g}) from {p.grad_from:g}% to {p.grad_to:g}% h"
    ext = primary.extents
    lines = [Path(src).name,
             f"  frame      up {up}, front {front}" + (f", turned {', '.join(f'{a:g}' for a in rot)}°" if any(rot)
                                                        else "") + f" ({source})",
             f"  pattern    {pat}; wavelength {p.wavelength:g}% h ({lam / frame.scale:.4g} units), Du {chem.du:.3g}, "
             f"Dv {chem.dv:.3g}" + (f"; channels {p.align} ({p.align_strength:g})" if Ns is not None else ""),
             f"  start      {start.spots} spot{'s' if start.spots != 1 else ''} ({p.origin}, "
             f"{p.density:g} per 100 λ², seed {p.seed})",
             f"  simulation {_n(n)} vertices, {lam / mean_edge:.1f} per wavelength"
             + (" (simplified from the scan)" if simplified else "")
             + f"; {_n(steps)} steps in {took:.1f} s"
             + ("  (draft: simplified model, coarse simulation, no relief refinement)" if ctx.draft else ""),
             f"  relief     {p.relief}, depth {p.depth:g}% h, sharpness {p.sharpness:g}, balance {p.balance:g}; "
             f"{_n(len(Fr))} faces ({per_lam:.1f} per wavelength)",
             f"  acts on    {influence.describe(p)}"]
    lines += [f"  faces      {_n(faces_in)} -> {_n(len(Fr))} per stage"]
    for i, c, cover, moved in rows:
        lines.append(f"  stage {i}    step {_n(c)}: pattern covers {cover:.0f}% of the surface, relief up to "
                     f"{moved:.2f}% h")
    lines += [f"  size       {ext[0]:.4g} x {ext[1]:.4g} x {ext[2]:.4g} units"]
    lines += [f"  saved as   {o}" for o in outputs[:1]]
    if len(outputs) > 1:
        lines.append(f"  stages     {len(outputs) - 1} files in {Path(outputs[1]).parent}")
    if rows[-1][2] < 5 and depth > 0:
        ctx.warn("almost no pattern grew: raise Growth time or Starting spots, or pick another pattern (Custom feed "
                 "and kill values often grow nothing)")
    ctx.progress(1.0)
    return Result(outputs=outputs,
                  stats=[Stat("stages", "Stages", nst),
                         Stat("faces", "Faces", len(primary.faces), before=faces_in),
                         Stat("cover", "Pattern", rows[-1][2], "%", fmt="{:.0f}"),
                         Stat("steps", "Steps", steps, fmt="{:,}"),
                         Stat("sim", "Simulation", round(took, 1), "s", fmt="{:.1f}"),
                         Stat("size", "Size", " × ".join(f"{x:.4g}" for x in ext), fmt="{}")],
                  report="\n".join(lines), preview={"views": out.views, "prefer": out.prefer},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"steps": capture, "frame": manifest["frame"], "levels": (lv_lo, lv_hi)})


def _areas(V, F):
    """Per-vertex barycentric areas."""
    a = 0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)
    out = np.zeros(len(V))
    np.add.at(out, F.ravel(), np.repeat(a / 3.0, 3))
    return out
