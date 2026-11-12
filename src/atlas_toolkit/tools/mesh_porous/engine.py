"""Toolkit adapter for Porous Forms: load, set up the model's frame, sample the fields once, extract every stage,
publish signed meshes."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d, influence, stages
from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import mesh_porous as mp

PATTERN_NAMES = {"gyroid": "gyroid", "diamond": "diamond", "primitive": "Schwarz P", "neovius": "Neovius",
                 "voronoi": "Voronoi cells"}


def num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def run(ctx, src: Path, dst: Path | None, p) -> Result:
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
    if not mesh.is_watertight:
        ctx.warn("the model is not closed: inside and outside come from its generalised winding number, which "
                 "bridges small holes; a large opening can leave walls missing or stray. Close it first for a "
                 "reliable result.")
    V, F = (gm.simplify_faces(V0, F0, int(p.draft_faces), ctx.log) if ctx.draft else (V0, F0))

    height = float(frame.hi[2] - frame.lo[2])
    pct = height / 100.0
    resolution = int(p.draft_resolution if ctx.draft else p.resolution)
    grid = mp.make_grid(frame.lo, frame.hi, resolution)
    mp.check_size(grid)
    wall = float(p.wall) * pct
    samples = wall / grid.step
    if samples < mp.MIN_WALL_SAMPLES:
        need_res = int(np.ceil(mp.MIN_WALL_SAMPLES * (resolution - 1) / samples)) + 1
        need_wall = mp.MIN_WALL_SAMPLES * grid.step / pct
        msg = (f"walls of {p.wall:g}% h are {samples:.1f} samples wide at resolution {resolution} (they need "
               f"{mp.MIN_WALL_SAMPLES:g}): raise Resolution to {min(need_res, 512)} or Wall thickness to "
               f"{need_wall:.2f}% h")
        if not ctx.draft:
            raise ValueError(msg if need_res <= 512 else
                             f"walls of {p.wall:g}% h are too thin for this model at the highest resolution: "
                             f"raise Wall thickness to {need_wall * resolution / 512:.2f}% h or more")
        ctx.warn("preview only: " + msg)

    values = dict(p)
    us = stages.amounts(int(p.stages), float(p.amount))
    fields = None
    if any(u > 0 for u in us):
        fields = mp.build_fields(V, F, frame.lo, frame.hi, values, resolution,
                                 lambda t, msg="": ctx.progress(0.05 + 0.5 * t, msg))
    n = len(us)
    manifest = {"tool": TOOL.id, "tool_version": TOOL.version, "source": Path(src).name,
                "frame": {"up": up, "front": front, "rotate": [float(a) for a in rot]}}
    height_model = height / frame.scale
    import trimesh
    rows = []
    out = stages.StageOutput(ctx, TOOL, dst, n, p.layout, p.axis, float(p.gap) / 100.0 * height_model,
                             p.save_stages, manifest)
    with out:
        for i, u in enumerate(us):
            ctx.progress(0.56 + 0.32 * i / n, f"stage {i + 1} of {n}")
            if u <= 0 or fields is None:
                # amount 0: the model itself, untouched
                m = trimesh.Trimesh(np.array(mesh.vertices), F0, process=False)
                rows.append((i, u, len(F0), 1, 1, None))
            else:
                Vc, Fc = mp.extract(mp.solid_field(fields, u, wall, float(p.depth) * pct), fields.grid)
                if len(Fc) == 0:
                    raise ValueError("nothing is left: the walls vanished at this resolution; thicken them or "
                                     "raise Resolution")
                m = trimesh.Trimesh(Vc, Fc, process=False)
                m.merge_vertices()
                m, before, after = mp.keep_components(m, p.keep, float(p.min_piece))
                m = mp.smooth(m, int(p.smooth))
                budget = int(p.max_faces)
                if ctx.draft:      # the viewer shows at most PREVIEW_FACES: decimate well rather than cluster
                    budget = min(budget or gm.PREVIEW_FACES, gm.PREVIEW_FACES)
                if budget and len(m.faces) > budget:
                    ctx.progress(0.56 + 0.32 * (i + 0.6) / n, f"stage {i + 1}: simplifying")
                    Vs, Fs = gm.simplify_faces(m.vertices, m.faces, budget, ctx.log)
                    m = trimesh.Trimesh(Vs, Fs, process=False)
                solid = abs(m.volume) / max(abs(trimesh.Trimesh(V0, F0, process=False).volume), 1e-12)
                rows.append((i, u, len(m.faces), before, after, solid))
                m = trimesh.Trimesh(frame.inverse(m.vertices), m.faces, process=False)
            out.add(i, m, {"amount": round(u, 6), "label": f"stage {i}"})
            ctx.check_cancel()
        outputs = out.finish()
    primary = out.primary

    last = rows[-1]
    ext = primary.extents
    step_model = grid.step / frame.scale
    lines = [Path(src).name,
             f"  frame      up {up}, front {front}" + (f", turned {', '.join(f'{a:g}' for a in rot)}°" if any(rot)
                                                        else "") + f" ({source})",
             f"  input      {num(faces_in)} faces, {'closed' if mesh.is_watertight else 'open'}"
             + (f" (sampled from {num(len(F))} for the preview)" if len(F) != faces_in else ""),
             f"  pattern    {PATTERN_NAMES[p.pattern]} {'walls' if p.style == 'sheet' else 'struts'}, cell "
             f"{p.cell:g}% h, wall {p.wall:g}% h"
             + (f", graded ×{p.grade:g} to the top" if abs(p.grade - 1) > 1e-9 else "")
             + (f", solid deeper than {p.depth:g}% h" if p.depth > 0 else "")
             + (f", seed {p.seed}" if p.pattern == "voronoi" else ""),
             f"  acts on    {influence.describe(values)}",
             f"  grid       {' x '.join(str(s) for s in grid.shape)} samples, {step_model:.4g} units apart "
             f"({grid.step / pct:.2f}% h); walls {samples:.1f} samples" + ("  (draft)" if ctx.draft else ""),
             ]
    for i, u, nf, before, after, solid in rows:
        if solid is None:
            lines.append(f"  stage {i}    amount {u:.3f}: the model itself")
        else:
            lines.append(f"  stage {i}    amount {u:.3f}: {num(nf)} faces, {after} piece{'s' if after != 1 else ''}"
                         + (f" (of {before})" if before != after else "") + f", {solid * 100:.0f}% of the volume")
    lines += [f"  size       {ext[0]:.4g} x {ext[1]:.4g} x {ext[2]:.4g} units",
              f"  time       {time.perf_counter() - t0:.1f} s"]
    if ctx.draft:
        lines.append(f"  preview    {resolution} samples instead of {p.resolution}, input under "
                     f"{num(int(p.draft_faces))} faces, output under {num(gm.PREVIEW_FACES)} faces; Run uses the "
                     f"full settings")
    lines += [f"  saved as   {o}" for o in outputs[:1]]
    if len(outputs) > 1:
        lines.append(f"  stages     {len(outputs) - 1} files in {Path(outputs[1]).parent}")
    if last[4] > 1 and p.keep == "all":
        ctx.log(f"{last[4]} loose pieces kept (Loose pieces: drop small or largest only removes them)")
    ctx.progress(1.0)
    return Result(outputs=outputs,
                  stats=[Stat("stages", "Stages", n),
                         Stat("faces", "Faces", len(primary.faces), before=faces_in),
                         Stat("pieces", "Pieces", last[4]),
                         Stat("solid", "Volume kept", (last[5] if last[5] is not None else 1.0) * 100, "%",
                              fmt="{:.0f}"),
                         Stat("size", "Size", " × ".join(f"{v:.4g}" for v in ext), fmt="{}")],
                  report="\n".join(lines), preview={"views": out.views, "prefer": out.prefer},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"amounts": us, "frame": manifest["frame"], "voxel": step_model})
