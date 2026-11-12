"""Toolkit adapter for Compress: load, set up the model's frame, remesh, build the press or mould, solve (or morph),
smooth, publish one signed mesh."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from atlas_toolkit.core import Stat
from atlas_toolkit.geom import frame3d
from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import mesh_compress as mc

PRESS_NAMES = {"plates": "two plates", "floor": "a plate onto the floor", "radial": "a tube around the axis",
               "iso": "pressure from every side"}


def press_from(p, X: np.ndarray, v0: float) -> tuple[mc.Press | None, mc.Mould | None, float, str]:
    """The press (physical modes) or the mould and scale (morph) for the settings, and a report line."""
    lo, hi = X.min(0), X.max(0)
    if p.mode == "free":
        e = float(p.strain) / 100.0
        if p.press in ("plates", "floor"):
            pr = mc.plates(X, p.axis, e, float(p.tilt), floor=p.press == "floor")
            tilt = f", tilted {p.tilt:g}°" if p.tilt else ""
            return pr, None, 0.0, f"{PRESS_NAMES[p.press]} along {p.axis}, closed {p.strain:g}%{tilt}"
        pr = mc.radial(X, p.axis if p.press == "radial" else None, e)
        about = f" ({p.axis})" if p.press == "radial" else ""
        return pr, None, 0.0, f"{PRESS_NAMES[p.press]}{about}, radius closed {p.strain:g}%"
    centre = (lo + hi) / 2.0
    m = mc.mould(p.shape, p.axis, p.aspect, float(p.turn), int(p.sides), centre)
    seat = float(lo[2]) if p.seat == "floor" else None
    shape = p.shape + (f" ({p.sides} sides)" if p.shape == "prism" else "")
    asp = "" if np.allclose(p.aspect, 1.0) else " aspect " + " × ".join(f"{a:g}" for a in p.aspect)
    where = ", standing on the floor" if seat is not None else ""
    if p.fit == "morph":
        s = (float(p.shape_size) / 100.0 * v0 / m.shape.volume()) ** (1.0 / 3.0)
        if seat is not None:
            m.c = m.c.copy()
            m.c[2] = seat - s * m.A[2] * m.shape.bottom()
        return None, m, s, (f"morph onto a {shape}{asp}, axis {p.axis}, {p.shape_size:g}% of the volume, fill "
                            f"{p.fill:g}, relief {p.relief:g}{where}")
    pr = mc.into(X, m, float(p.shape_size) / 100.0, float(p.fill), v0, seat)
    return pr, m, 0.0, (f"pressed into a {shape}{asp}, axis {p.axis}, closed to {p.shape_size:g}% of the volume "
                        f"at fill {p.fill:g}{where}")


def run(ctx, src: Path, dst: Path | None, p):
    if ctx.dry_run:
        dst = None
    if dst is not None and dst.resolve() == Path(src).resolve():
        raise ValueError("the output must not replace the input model")
    ctx.progress(0.02, "loading model")
    mesh = gm.load_mesh(src)
    up, front, rot, source = frame3d.resolve_axes(src, p, mesh.extents)
    frame = frame3d.fit_frame(mesh.vertices, up, front, rot, mode="height", size=frame3d.MODEL_HEIGHT)
    V = frame.apply(mesh.vertices)
    F = np.asarray(mesh.faces, dtype=np.int64)
    watertight = bool(mesh.is_watertight)

    ctx.progress(0.05, "remeshing")
    sim_faces = int(p.draft_faces if ctx.draft else p.sim_faces)
    S, SF, edge = mc.remesh(V, F, sim_faces, ctx.log)
    SE, _ = mc.edges_of(SF)
    out_faces = int(p.draft_faces * 4 if ctx.draft else p.out_faces)
    D, DF = mc.detail_mesh(V, F, edge, out_faces, ctx.log)
    v0 = mc.volume(D, DF)
    if not watertight:
        ctx.warn("the model is not watertight: its volume is not kept (Compressibility has no effect) and the "
                 "mould is sized from its convex hull")
    vol_ref = abs(mc.volume(V, F)) if watertight else float(mesh.convex_hull.volume * frame.scale ** 3)

    physical = p.mode == "free" or p.fit == "mould"
    pr, m, s, how = press_from(p, V, vol_ref)
    out = None
    if physical:
        steps = int(p.steps) if not ctx.draft else max(8, int(p.steps) // 2)
        its = int(p.iterations)
        mat = mc.Material(float(p.compressibility), float(p.plasticity), float(p.stiffness), float(p.friction),
                          float(p.imperfection), int(p.seed))
        ctx.progress(0.1, "compressing")
        out = mc.solve(S, SF, pr, mat, steps, its, bool(p.collide), edge, keep_volume=mc.is_closed(SF),
                       progress=lambda f: ctx.progress(0.1 + 0.75 * f, "compressing"))
        Sx = out.X
        if int(p.smooth):
            Sx = S + mc.taubin(Sx - S, SE, int(p.smooth))          # smooth the motion, not the model
        ctx.progress(0.87, "carrying the detail along")
        X = mc.transfer(D, mc.vertex_normals(D, DF), S, Sx, mc.vertex_normals(S, SF), SE)
        X, _ = pr.contact(X, 1.0, snap=0.6 * edge)              # flat where the press was
    else:
        ctx.progress(0.3, "morphing")
        X = mc.morph(D, DF, m, s, float(p.fill), float(p.relief))
    V, F = D, DF

    import trimesh
    res = trimesh.Trimesh(frame.inverse(X), F, process=False)
    v1 = mc.volume(X, F)
    ratio = v1 / v0 if watertight and v0 else float("nan")
    ext0, ext1 = V.max(0) - V.min(0), X.max(0) - X.min(0)
    axis = {"up": 2, "front": 1, "side": 0}.get(p.axis, 2)
    strain = (1.0 - ext1[axis] / ext0[axis]) * 100.0 if ext0[axis] else 0.0
    lines = [Path(src).name,
             f"  frame      up {up}, front {front}" + (f", turned {', '.join(f'{a:g}' for a in rot)}°" if any(rot)
                                                        else "") + f" ({source})",
             f"  meshes     simulated on {len(SF):,} faces (edges ≈ {edge / frame.height * 100:.2g}% h); output "
             f"{len(F):,} faces".replace(",", " ") + ("  (draft)" if ctx.draft else ""),
             f"  press      {how}"]
    if out is not None:
        lines.append(f"  material   compressibility {p.compressibility:g}, plasticity {p.plasticity:g}, bending "
                     f"{p.stiffness:g}, friction {p.friction:g}, imperfection {p.imperfection:g} (seed {p.seed})")
        lines.append(f"  solve      {steps} load steps × {its} iterations; {out.contact * 100:.0f}% of the surface "
                     f"touches the press; {out.collisions:,} self-contacts held apart")
    lines.append(f"  result     {strain:.1f}% shorter along {p.axis}" +
                 (f", volume {ratio * 100:.1f}% of the original" if np.isfinite(ratio) else "") +
                 f", moved up to {mc.displacement(V, X) / frame.height * 100:.1f}% h")
    stats = [Stat("strain", "Shorter by", strain, "%", fmt="{:.1f}")]
    if np.isfinite(ratio):
        stats.append(Stat("volume", "Volume", ratio * 100.0, "%", fmt="{:.1f}"))
    return gm.result(ctx, TOOL, mesh, res, dst, stats=stats, report=lines)
