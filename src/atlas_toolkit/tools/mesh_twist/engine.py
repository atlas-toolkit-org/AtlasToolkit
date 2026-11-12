"""Toolkit adapter for Twist: load, set up the model's frame, twist every stage, publish signed meshes."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d, stages
from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import mesh_twist as mt

FACE_BUDGET = 2_000_000          # per stage


def run(ctx, src: Path, dst: Path | None, p) -> Result:
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
    faces_in = len(F)
    if ctx.draft:
        V, F = gm.simplify_faces(V, F, int(p.draft_faces), ctx.log)
    levels = 0 if ctx.draft else int(p.subdiv)
    ctx.progress(0.08, "subdividing" if levels else "preparing")
    V, F, done = mt.subdivide(V, F, levels, FACE_BUDGET)
    if done < levels:
        ctx.warn(f"subdivision stopped at {done} level(s): {levels} would pass {FACE_BUDGET:,} faces")
    import trimesh
    canon = trimesh.Trimesh(V, F, process=False)
    N = None
    if p.ripple_dir == "normal":
        N = np.asarray(canon.vertex_normals, dtype=np.float64)
    elif p.ripple_dir == "smooth":
        N = mt.smooth_normals(canon, 8)
    shape = mt.shape(p)
    if p.pivot == "spine":
        shape["centre_line"] = mt.spine(V)
    us = stages.amounts(int(p.stages), float(p.amount))
    n = len(us)
    manifest = {"tool": TOOL.id, "tool_version": TOOL.version, "source": Path(src).name,
                "frame": {"up": up, "front": front, "rotate": [float(a) for a in rot]}}
    height_model = (frame.hi[2] - frame.lo[2]) / frame.scale
    rows = []
    out = stages.StageOutput(ctx, TOOL, dst, n, p.layout, p.axis, float(p.gap) / 100.0 * height_model,
                             p.save_stages, manifest)
    with out:
        for i, u in enumerate(us):
            ctx.progress(0.12 + 0.6 * i / n, f"stage {i + 1} of {n}")
            amp = mt.amplitudes(p, u)
            off = mt.offsets(V, N, amp["sway"], amp["turn"], amp["ripple"], **shape)
            rows.append((i, u, amp, mt.max_displacement(off) / 2.0 * 100.0))
            out.add(i, trimesh.Trimesh(frame.inverse(V + off), F, process=False),
                    {"amount": round(u, 6), "label": f"stage {i}"})
            ctx.check_cancel()
        outputs = out.finish()
    primary = out.primary

    ext = primary.extents
    lines = [Path(src).name,
             f"  frame      up {up}, front {front}" + (f", turned {', '.join(f'{a:g}' for a in rot)}°" if any(rot)
                                                        else "") + f" ({source})",
             f"  faces      {faces_in:,} -> {len(F):,} per stage".replace(",", " ")
             + (f" ({done} Loop subdivision level{'s' if done != 1 else ''})" if done else "")
             + ("  (draft)" if ctx.draft else ""),
             f"  shape      turn waves {p.turn_waves:g}, sway waves {p.sway_waves:g}, ripple waves "
             f"{p.ripple_waves:g}, {p.lobes} lobes, phase {p.phase:g}°, ripple {p.ripple_dir}, about the "
             f"{'spine' if p.pivot == 'spine' else 'vertical axis'}"]
    for i, u, amp, disp in rows:
        lines.append(f"  stage {i}    amount {u:.3f}: turn {math.degrees(amp['turn']):.2f}° ({amp['turn']:.4f} rad), "
                     f"sway {amp['sway']:.4f}, ripple {amp['ripple']:.4f} (2-tall units); moves up to {disp:.1f}% h")
    lines += [f"  size       {ext[0]:.4g} x {ext[1]:.4g} x {ext[2]:.4g} units"]
    lines += [f"  saved as   {o}" for o in outputs[:1]]
    if len(outputs) > 1:
        lines.append(f"  stages     {len(outputs) - 1} files in {Path(outputs[1]).parent}")
    ctx.progress(1.0)
    return Result(outputs=outputs,
                  stats=[Stat("stages", "Stages", n),
                         Stat("faces", "Faces", len(primary.faces), before=faces_in),
                         Stat("moved", "Moves up to", rows[-1][3], "% h", fmt="{:.1f}"),
                         Stat("size", "Size", " × ".join(f"{v:.4g}" for v in ext), fmt="{}")],
                  report="\n".join(lines), preview={"views": out.views, "prefer": out.prefer},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"amounts": us, "frame": manifest["frame"]})
