"""Atlas adapter: signed, staged exports and non-writing previews."""
from __future__ import annotations

import os
from pathlib import Path

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.mesh_export import publish_meshes

from . import TOOL
from .plane_cut import cut


def publish(meshes, dst, ctx):
    """Compatibility wrapper around the shared signed export transaction."""
    return publish_meshes(meshes, dst, ctx, TOOL)


def run(ctx, src: Path, dst: Path | None, p):
    import trimesh
    ctx.progress(0.02, "loading model")
    source = gm.load_mesh(src)
    pieces, (origin, normal), warnings = cut(source, p, ctx.progress)
    for message in warnings:
        ctx.warn(message)
    combined = trimesh.util.concatenate([part for _, part in pieces])
    save_parts = [part for _, part in pieces] if p.cut_type == "slice" and p.export_mode == "separate" else [combined]
    outputs = publish(save_parts, dst, ctx) if dst is not None else []
    ctx.progress(0.97, "preparing preview")
    views = [{"title": "Output", "kind": "mesh", "mesh": gm.preview(combined)}]
    if len(pieces) > 1:
        views += [{"title": side.capitalize(), "kind": "mesh", "mesh": gm.preview(part)} for side, part in pieces]
    report = [src.name, f"  mode       {p.cut_type}, primary side {p.side}, {p.fill} fill",
              "  origin     " + " / ".join(f"{x:.8g}" for x in origin),
              "  normal     " + " / ".join(f"{x:.8g}" for x in normal),
              f"  pieces     {len(pieces)}, original model units"]
    report += [f"  {side:10} {len(part.faces)} faces, {'closed' if part.is_watertight else 'open'}"
               for side, part in pieces]
    report += warnings + ["  saved      " + path for path in outputs]
    if p.cut_type == "slice" and p.export_mode == "combined" and p.separation == 0 and p.fill != "none":
        report.append("  Closed halves touch at the cut. Use separate files or a gap if downstream software welds vertices.")
    ctx.progress(1.0)
    return Result(outputs=outputs, stats=[Stat("pieces", "Pieces", len(pieces)),
                  Stat("faces", "Faces", len(combined.faces), before=len(source.faces)),
                  Stat("closed", "Closed pieces", sum(part.is_watertight for _, part in pieces))],
                  report="\n".join(report), preview={"views": views, "prefer": "Output"},
                  status="Saved" if outputs else "Previewed")


def make_sample(folder):
    import trimesh
    mesh = trimesh.creation.icosphere(subdivisions=2, radius=10)
    mesh.apply_scale((1, 0.8, 1.3))
    path = Path(folder) / "plane-sample.stl"
    mesh.export(path)
    return path
