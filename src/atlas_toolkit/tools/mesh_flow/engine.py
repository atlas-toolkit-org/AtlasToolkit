"""Toolkit adapter for Flow Warp: load, set up the model's frame, split long edges, advect every stage along one
trajectory, publish signed meshes."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d, influence, stages
from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import mesh_flow as mf

FACE_BUDGET = 2_000_000          # after edge splitting
STRETCH_WARN = 4.0               # edges pulled out more than this many times their length


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
    lo, hi = frame.lo, frame.hi
    fld = mf.field_from(p, lo, hi)

    passes, target = 0, None
    if p.refine and not ctx.draft and fld.active and math.isfinite(fld.detail):
        ctx.progress(0.06, "splitting long edges")
        target = fld.detail / int(p.edges_per_wave)
        V, F, passes = mf.refine(V, F, target, FACE_BUDGET)
        E = np.unique(np.sort(F[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1), axis=0)
        longest = float(np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1).max()) if len(E) else 0.0
        if longest > target * 1.001:
            pct = 100.0 / frame.height
            ctx.warn(f"edge splitting stopped at {len(F):,} faces (budget {FACE_BUDGET:,}); edges up to "
                     f"{longest * pct:.2g}% h remain, longer than the {target * pct:.2g}% h the flow's detail wants: "
                     f"raise Swirl size or lower Edges per eddy")

    w = mf.weights_from(p, V, lo, hi)
    us = stages.amounts(int(p.stages), float(p.amount))
    n = len(us)
    h = mf.step_size(fld, float(p.step))

    def advect_progress(f):
        ctx.progress(0.12 + 0.55 * f, "following the flow")
    ctx.progress(0.12, "following the flow")
    positions = mf.advect(fld, V, w, us, float(p.step), advect_progress)

    manifest = {"tool": TOOL.id, "tool_version": TOOL.version, "source": Path(src).name,
                "frame": {"up": up, "front": front, "rotate": [float(a) for a in rot]}}
    height_model = (frame.hi[2] - frame.lo[2]) / frame.scale
    import trimesh
    rows = []
    out = stages.StageOutput(ctx, TOOL, dst, n, p.layout, p.axis, float(p.gap) / 100.0 * height_model,
                             p.save_stages, manifest)
    with out:
        for i, (u, X) in enumerate(zip(us, positions)):
            ctx.progress(0.7 + 0.25 * i / n, f"stage {i + 1} of {n}")
            st = mf.stretch(V, X, F)
            rows.append((i, u, mf.max_displacement(V, X) / frame.height * 100.0, st))
            out.add(i, trimesh.Trimesh(frame.inverse(X), F, process=False),
                    {"amount": round(u, 6), "label": f"stage {i}"})
            ctx.check_cancel()
        outputs = out.finish()
    primary = out.primary
    last = rows[-1][3]
    if last["over4"] > 1.0:
        ctx.warn(f"{last['over4']:.1f}% of the edges are pulled out over {STRETCH_WARN:g}× their length: lower "
                 f"Travel or the amount, or raise Edges per eddy, for smoother contours")
    if last["degenerate"]:
        ctx.warn(f"{last['degenerate']:,} triangles collapsed to slivers (near the vortex eye or where the flow "
                 f"converges)")

    parts = []
    if len(fld.K):
        parts.append(f"current travel {p.travel * p.current:g}% h, swirl {p.swirl:g}% h, turbulence "
                     f"{p.turbulence:g} ({len(fld.K) // mf.MODES} octave{'s' if len(fld.K) > mf.MODES else ''}), "
                     f"seed {p.seed}")
    if fld.vortex is not None:
        at = ", ".join(f"{c:g}" for c in p.vortex_centre)
        parts.append(f"vortex {p.spin:g}° about the {p.vortex_axis} axis at ({at})%, "
                     f"core {p.vortex_radius:g}% h, draw in {p.inflow:g}")
    if fld.pull is not None:
        parts.append(f"pull {p.pull:g}% h toward {p.pull_dir}, streaks {p.streaks:g}")
    ext = primary.extents
    lines = [Path(src).name,
             f"  frame      up {up}, front {front}" + (f", turned {', '.join(f'{a:g}' for a in rot)}°" if any(rot)
                                                        else "") + f" ({source})",
             f"  faces      {faces_in:,} -> {len(F):,} per stage".replace(",", " ")
             + (f" ({passes} edge-splitting pass{'es' if passes != 1 else ''} to {target / frame.height * 100:.2g}% h)"
                if passes else "") + ("  (draft: simplified, no edge splitting)" if ctx.draft else ""),
             "  field      " + ("; ".join(parts) if parts else "none (nothing moves)"),
             f"  acts on    {influence.describe(p)}" + (f", anchored {p.anchor:g}% h + {p.anchor_blend:g}% h blend"
                                                       if p.anchor > 0 or p.anchor_blend > 0 else "")
             + f" ({(w > 0).mean() * 100:.0f}% of the vertices move)",
             f"  steps      Runge-Kutta 4, dt {h:.4g} ({math.ceil(max(us) / h - 1e-9) if fld.active else 0} steps for "
             f"amount {max(us):g}); flow scale {fld.wavelength / frame.height * 100:.3g}% h"
             if fld.active else "  steps      none"]
    for i, u, disp, st in rows:
        lines.append(f"  stage {i}    amount {u:.3f}: moves up to {disp:.1f}% h; edges stretched up to "
                     f"{st['max']:.2f}× (99%: {st['p99']:.2f}×), {st['over4']:.2f}% over {STRETCH_WARN:g}×"
                     + (f", {st['degenerate']} slivers" if st["degenerate"] else ""))
    lines += [f"  size       {ext[0]:.4g} x {ext[1]:.4g} x {ext[2]:.4g} units"]
    lines += [f"  saved as   {o}" for o in outputs[:1]]
    if len(outputs) > 1:
        lines.append(f"  stages     {len(outputs) - 1} files in {Path(outputs[1]).parent}")
    ctx.progress(1.0)
    return Result(outputs=outputs,
                  stats=[Stat("stages", "Stages", n),
                         Stat("faces", "Faces", len(primary.faces), before=faces_in),
                         Stat("moved", "Moves up to", rows[-1][2], "% h", fmt="{:.1f}"),
                         Stat("stretch", "Stretch", last["p99"], "×", fmt="{:.2f}"),
                         Stat("size", "Size", " × ".join(f"{v:.4g}" for v in ext), fmt="{}")],
                  report="\n".join(lines), preview={"views": out.views, "prefer": out.prefer},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"amounts": us, "frame": manifest["frame"], "dt": h})
