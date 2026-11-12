"""Toolkit adapter for Skeleton Shell: load, set up the model's frame, build, publish in the model's own units."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d, influence
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.mesh_export import publish_meshes
from atlas_toolkit.geom.surface import from_mesh

from . import TOOL
from . import mesh_skeleton as ms


def run(ctx, src, dst, p) -> Result:
    started = time.perf_counter()
    src = Path(src)
    if ctx.dry_run:
        dst = None
    if dst is not None and Path(dst).resolve() == src.resolve():
        raise ValueError("The output must not replace the input model.")
    ctx.progress(0.01, "loading model")
    original = gm.load_mesh(src)
    if not len(original.faces) or not np.isfinite(original.vertices).all():
        raise ValueError("Input has no finite surface.")
    up, front, rotate, source = frame3d.resolve_axes(src, p, original.extents)
    frame = frame3d.fit_frame(original.vertices, up, front, rotate, mode="height", size=frame3d.MODEL_HEIGHT)
    mesh = trimesh.Trimesh(frame.apply(original.vertices), original.faces.copy(), process=False)
    if not mesh.is_winding_consistent:
        trimesh.repair.fix_winding(mesh)
    if mesh.is_watertight and mesh.volume < 0:
        mesh.invert()
    built = ms.build(mesh.vertices, mesh.faces, p, frame.lo, frame.hi,
                     progress=lambda t=None, msg="": ctx.progress(t, msg) if t is not None else ctx.check_cancel(),
                     draft=ctx.draft)
    ctx.check_cancel()
    report = built.report
    ctx.progress(0.95, "assembling the result")
    result_mesh = trimesh.Trimesh(frame.inverse(built.V), built.F, process=False)
    if not np.isfinite(result_mesh.vertices).all():
        raise ValueError("The structure produced invalid geometry; nothing was published.")
    outputs = []
    if dst is not None:
        surface = from_mesh(result_mesh)
        surface.frame = frame.frame_values()
        outputs = publish_meshes([surface], dst, ctx, TOOL)
    names = dict((k, v) for k, v in [("none", "none"), ("cells", "wire cells"), ("trimesh", "trimesh"),
                                     ("flow", "flow strands"), ("ribs", "ribs and stringers"), ("plate", "cut plate")])
    lines = [src.name,
             f"  frame      up {up}, front {front} ({source}); original units and placement retained",
             f"  structure  {names[p.structure]}, {p.size:g}% of the height"
             + (f", over {names[p.second]} at {p.second_size:g}%" if p.second != "none" else ""),
             f"  wires      {report.wires}, {report.joints} joints, {report.length / frame3d.MODEL_HEIGHT:.1f} model "
             f"heights of wire"]
    if report.frames:
        lines.append(f"  frames     {report.frames} flat frames")
    if report.plates:
        lines.append(f"  sheet      {report.coverage * 100.0:.0f}% of the skin stays metal, "
                     f"{p.thickness:g}% h thick")
    if p.influence != "all":
        lines.append(f"  hollowed   {influence.describe(p)}; {report.kept * 100.0:.0f}% of the skin kept")
    lines.append(f"  skin       worked on edges of {report.edge / frame3d.MODEL_HEIGHT * 100.0:.2f}% of the height")
    if report.fused:
        lines.append(f"  fused      one closed solid in {report.pieces} piece(s)")
    lines += [f"  note       {n}" for n in report.notes]
    lines += [f"  saved as   {o}" for o in outputs[:1]]
    lines.append(f"  time       {time.perf_counter() - started:.1f} s")
    ctx.progress(1.0, "Saved" if outputs else "Previewed")
    views = [{"title": "Before", "kind": "mesh", "mesh": gm.preview(original)},
             {"title": "Skeleton", "kind": "mesh", "mesh": gm.preview(result_mesh, max_faces=int(p.max_faces)),
              "status": "Draft · coarser skin" if ctx.draft else "Final geometry"}]
    return Result(outputs=outputs,
                  stats=[Stat("wires", "Wires", report.wires),
                         *([Stat("frames", "Frames", report.frames)] if report.frames else []),
                         *([Stat("metal", "Metal on skin", report.coverage * 100.0, "%", fmt="{:.0f}")]
                           if report.plates else []),
                         Stat("faces", "Faces", len(built.F), before=len(original.faces))],
                  report="\n".join(lines), preview={"views": views, "prefer": "Skeleton"},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"wires": report.wires, "joints": report.joints, "frames": report.frames,
                        "plates": report.plates, "coverage": report.coverage, "kept": report.kept,
                        "pieces": report.pieces, "fused": report.fused, "notes": list(report.notes),
                        "frame": frame.frame_values()})


def make_sample(folder):
    mesh = trimesh.creation.icosphere(subdivisions=4, radius=30.0)
    mesh.vertices *= [1.0, 0.85, 1.5]
    path = Path(folder) / "skeleton-sample.stl"
    mesh.export(path)
    return path
