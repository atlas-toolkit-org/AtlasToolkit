"""Toolkit adapter for Melt: set up the model's frame, have Blender melt every stage, publish signed meshes."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import blender as gb
from atlas_toolkit.geom import frame3d, stages
from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import mesh_melt as mm


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    if ctx.dry_run:
        dst = None
    if dst is not None and dst.resolve() == Path(src).resolve():
        raise ValueError("the output must not replace the input model")
    t0 = time.perf_counter()
    ctx.progress(0.02, "loading model")
    mesh = gm.load_mesh(src)
    up, front, rot, source = frame3d.resolve_axes(src, p, mesh.extents)
    frame = frame3d.fit_frame(mesh.vertices, up, front, rot, mode="height", size=frame3d.MODEL_HEIGHT)
    V = frame.apply(mesh.vertices)
    F = np.asarray(mesh.faces, dtype=np.int64)
    faces_in = len(F)
    if ctx.draft:
        V, F = gm.simplify_faces(V, F, int(p.draft_faces), ctx.log)
    e = mm.mean_edge(V, F)
    zones = mm.head_zones(V, (p.head_from, p.head_to)) if p.head else None
    levels = 0 if ctx.draft else int(p.subdiv)
    us = stages.amounts(int(p.stages), float(p.amount))
    n = len(us)
    notes: list = []
    folder = gb.scratch()
    st = os.stat(src)
    key = hashlib.sha1(f"{Path(src).resolve()}|{st.st_size}|{st.st_mtime_ns}|{ctx.draft}|{os.getpid()}|"
                       f"{time.time_ns()}".encode()).hexdigest()[:16]
    npz_in = folder / f"{key}-in.npz"
    reqs, resolved = [], []
    for i, u in enumerate(us):
        out = folder / f"{key}-s{i}.npz"
        if u <= 1e-9:
            reqs.append({"out": str(out), "intact": True, "subdiv": levels})
            resolved.append(None)
        else:
            q = mm.resolve(p, u, e, zones, ctx.draft, notes)
            reqs.append({"out": str(out), "subdiv": levels, "params": q})
            resolved.append(q)
    files = [npz_in] + [Path(r["out"]) for r in reqs]
    manifest = {"tool": TOOL.id, "tool_version": TOOL.version, "source": Path(src).name,
                "frame": {"up": up, "front": front, "rotate": [float(a) for a in rot]}}
    height_model = (frame.hi[2] - frame.lo[2]) / frame.scale
    import trimesh
    try:
        np.savez(npz_in, V=V, F=F.astype(np.int32))
        ctx.progress(0.05, "starting Blender")
        client = gb.client(ctx.log)
        manifest["blender"] = client.version_string
        tb = time.perf_counter()
        reply = client.request({"op": "melt", "mesh": str(npz_in), "stages": reqs, "draft": bool(ctx.draft)},
                               on_progress=lambda f, m: ctx.progress(0.08 + 0.62 * f, m), poll=ctx.check_cancel)
        t_blender = time.perf_counter() - tb
        out = stages.StageOutput(ctx, TOOL, dst, n, p.layout, p.axis, float(p.gap) / 100.0 * height_model,
                                 p.save_stages, manifest)
        with out:
            for i, (u, r) in enumerate(zip(us, reqs)):
                ctx.progress(0.72 + 0.15 * i / n, f"reading stage {i + 1} of {n}")
                with np.load(r["out"]) as d:
                    Vs, Fs = np.asarray(d["V"], dtype=np.float64), np.asarray(d["F"], dtype=np.int64)
                out.add(i, trimesh.Trimesh(frame.inverse(Vs), Fs, process=False),
                        {"amount": round(u, 6), "label": f"stage {i}"})
                ctx.check_cancel()
            outputs = out.finish()
    finally:
        for f in files:
            try:
                f.unlink()
            except OSError:
                pass
    primary = out.primary
    ext = primary.extents

    def num(x) -> str:
        return f"{x:,}".replace(",", " ")

    lines = [Path(src).name,
             f"  frame      up {up}, front {front}" + (f", turned {', '.join(f'{a:g}' for a in rot)}°" if any(rot)
                                                        else "") + f" ({source})",
             f"  model      {num(faces_in)} faces" + (f", {num(len(F))} in the preview" if len(F) != faces_in else "")
             + f"; mean edge {e:.4g} of 2 (v17: {mm.E_REF}); {levels} subdivision level(s)",
             f"  mode       " + ("head or bust: face zones ×{zone_front:.3f} deep, ×{zone_side:.3f} wide".format(**zones)
                                 if zones else "any object (no face terms)"),
             f"  blender    {client.version_string}, {t_blender:.1f} s for {n} stage(s)"]
    for i, (u, q, s) in enumerate(zip(us, resolved, reply.get("stages", []))):
        if q is None:
            lines.append(f"  stage {i}    intact, {num(s.get('faces', 0))} faces")
            continue
        lines.append(f"  stage {i}    amount {u:.3f}: slump {q['slump']:.3f}, crown sag {q['crown_sag']:.3f}, pool "
                     f"{q['puddle_height']:.3f}, drips {q['drip_amount']:.2f}, dissolve {q['dissolve']:.2f}, soften "
                     f"{q['soften']} passes; {num(s.get('faces', 0))} faces, {s.get('seconds', 0):.1f} s")
    lines += [f"  note       {x}" for x in notes]
    lines.append(f"  size       {ext[0]:.4g} x {ext[1]:.4g} x {ext[2]:.4g} units")
    lines += [f"  saved as   {o}" for o in outputs[:1]]
    if len(outputs) > 1:
        lines.append(f"  stages     {len(outputs) - 1} files in {Path(outputs[1]).parent}")
    ctx.progress(1.0)
    return Result(outputs=outputs,
                  stats=[Stat("stages", "Stages", n),
                         Stat("faces", "Faces", len(primary.faces), before=faces_in),
                         Stat("blender", "Blender", t_blender, "s", fmt="{:.1f}"),
                         Stat("size", "Size", " × ".join(f"{v:.4g}" for v in ext), fmt="{}")],
                  report="\n".join(lines), preview={"views": out.views, "prefer": out.prefer},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"amounts": us, "frame": manifest["frame"], "params": resolved,
                        "seconds": time.perf_counter() - t0})
