"""Toolkit adapter for Rods: load, set up the model's frame, make it a solid (Holes' engine), plan the rods, add them
in one union, publish the signed mesh and, optionally, the rods alone."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d, influence
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_holes import engine as holes_engine
from atlas_toolkit.tools.mesh_holes import mesh_holes as mh

from . import TOOL
from . import mesh_rods as mr

FAMILY_NAMES = {"rod": "rods", "pipe": "hollow pipes", "cable": "cables", "spike": "spikes"}


def num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def tubes_path(dst: Path) -> Path:
    return dst.with_name(f"{dst.stem}_tubes{dst.suffix}")


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    import trimesh
    if ctx.dry_run:
        dst = None
    if dst is not None and dst.resolve() == Path(src).resolve():
        raise ValueError("the output must not replace the input model")
    t0 = time.perf_counter()
    ctx.progress(0.02, "loading model")
    mesh = gm.load_mesh(src)
    if len(mesh.faces) == 0:
        raise ValueError("the model has no faces")
    up, front, rot, source = frame3d.resolve_axes(src, p, mesh.extents)
    frame = frame3d.fit_frame(mesh.vertices, up, front, rot, mode="height", size=frame3d.MODEL_HEIGHT)
    V0 = frame.apply(mesh.vertices)
    F0 = np.asarray(mesh.faces, dtype=np.int64)
    faces_in = len(F0)

    V, F = (gm.simplify_faces(V0, F0, int(p.draft_faces), ctx.log) if ctx.draft else (V0, F0))
    model_m = holes_engine.solid(V, F)
    if model_m is None and len(F) != len(F0):
        V, F = V0, F0
        model_m = holes_engine.solid(V, F)
    repaired = False
    if model_m is None:
        res = min(int(p.repair_resolution), 160) if ctx.draft else int(p.repair_resolution)
        ctx.warn(f"the model is not a closed solid: closing it by resampling at {res} samples (fine detail "
                 f"softens; close it first for an exact union)")
        V, F = holes_engine.closed_copy(V0, F0, res,
                                        lambda t, msg="": ctx.progress(0.03 + 0.12 * t, "closing: " + msg))
        if ctx.draft:
            V, F = gm.simplify_faces(V, F, int(p.draft_faces), ctx.log)
        model_m = holes_engine.solid(V, F)
        repaired = True
        if model_m is None:
            raise ValueError("the model could not be closed into a solid")
    vol0 = model_m.volume()

    values = dict(p)
    segments = int(p.draft_segments if ctx.draft else p.segments)
    s = mr.Settings.from_values(values, frame.height, segments)
    model = mh.Model(np.asarray(V, float), np.asarray(F, np.int64), frame.lo.copy(), frame.hi.copy())
    ctx.progress(0.16, "placing rods")
    pl = mr.plan(model, s, values, lambda t, msg="": ctx.progress(0.16 + 0.3 * t, msg or "placing rods"))
    rods = pl.rods
    if not rods:
        ctx.warn("no rod reaches the model: check Width, Spacing, Count, the Acts on region or the minimum gap")
    out_m, tubes = mr.build(model_m, rods, s, lambda t, msg="": ctx.progress(0.46 + 0.3 * t, msg))
    ctx.check_cancel()
    vol1 = out_m.volume()

    Vo, Fo = mh.from_manifold(out_m)
    budget = int(p.max_faces)
    if ctx.draft:
        budget = min(budget or gm.PREVIEW_FACES, gm.PREVIEW_FACES)
    if budget and len(Fo) > budget:
        ctx.progress(0.82, "simplifying")
        Vo, Fo = gm.simplify_faces(Vo, Fo, budget, ctx.log)
    result = trimesh.Trimesh(frame.inverse(Vo), Fo, process=False)
    tube_mesh = None
    if not tubes.is_empty():
        Vt, Ft = mh.from_manifold(tubes)
        tube_mesh = trimesh.Trimesh(frame.inverse(Vt), Ft, process=False)

    written = []
    if dst is not None:
        ctx.progress(0.92, "saving")
        gm.save_mesh(result, dst, TOOL)
        written.append(str(dst))
        if p.save_tubes and tube_mesh is not None:
            written.append(str(gm.save_mesh(tube_mesh, tubes_path(dst), TOOL)))

    views = [{"title": "Output", "kind": "mesh", "mesh": gm.preview(result)}]
    if tube_mesh is not None:
        views.append({"title": "Rods", "kind": "mesh", "mesh": gm.preview(tube_mesh)})

    added = (vol1 / vol0 - 1.0) * 100.0 if vol0 > 0 else 0.0
    ext = result.extents
    pct = frame.height / 100.0
    widths = np.array([2 * r.radius / pct for r in rods]) if rods else np.zeros(1)
    lines = [Path(src).name,
             f"  frame      up {up}, front {front}" + (f", turned {', '.join(f'{a:g}' for a in rot)}°" if any(rot)
                                                        else "") + f" ({source})",
             f"  input      {num(faces_in)} faces, {'closed' if not repaired else 'open (closed by resampling)'}"
             + (f" (from {num(len(F))} for the preview)" if ctx.draft and len(F) != faces_in else ""),
             f"  rods       {num(len(rods))} {FAMILY_NAMES[s.family]}, {s.extend}, {s.layout}, {s.profile} profile, "
             f"width {widths.min():.2f}-{widths.max():.2f}% h, reach {p.reach:g}-{p.reach_max:g}% h, "
             f"irregularity {s.chaos:g}, seed {s.seed}",
             f"  placement  {num(pl.proposed)} proposed"
             + (f", {num(pl.dropped)} left out" if pl.dropped else "")
             + (f", {num(pl.spaced_out)} too close" if pl.spaced_out else "")
             + (f", {num(pl.shrunk)} thinned to fit" if pl.shrunk else "")
             + (f" (gap {p.gap:g}% h)" if s.gap > 0 else " (rods may cross)"),
             f"  acts on    {influence.describe(values)}",
             f"  added      {added:+.1f}% of the volume",
             f"  faces      {num(faces_in)} -> {num(len(result.faces))}",
             f"  size       {ext[0]:.4g} x {ext[1]:.4g} x {ext[2]:.4g} units",
             f"  time       {time.perf_counter() - t0:.1f} s"]
    if ctx.draft:
        lines.append(f"  preview    {segments} segments instead of {p.segments}, input under "
                     f"{num(int(p.draft_faces))} faces; Run uses the full settings")
    lines += [f"  saved as   {o}" for o in written]
    ctx.progress(1.0)
    return Result(outputs=written,
                  stats=[Stat("rods", "Rods", len(rods)),
                         Stat("added", "Added", added, "%", fmt="{:+.1f}"),
                         Stat("faces", "Faces", len(result.faces), before=faces_in),
                         Stat("size", "Size", " × ".join(f"{v:.4g}" for v in ext), fmt="{}")],
                  report="\n".join(lines), preview={"views": views, "prefer": "Output"},
                  status="Saved" if written else ("Draft" if ctx.draft else "Previewed"),
                  data={"frame": {"up": up, "front": front, "rotate": [float(a) for a in rot]}, "rods": len(rods)})
