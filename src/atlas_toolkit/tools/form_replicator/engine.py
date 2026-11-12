"""Atlas adapter: shared frame and solid repair, sculptural placement, union, preview, atomic signed export."""
from __future__ import annotations

import os
from pathlib import Path
import time

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.stages import Publisher
from atlas_toolkit.tools.mesh_holes.engine import closed_copy, solid
from atlas_toolkit.tools.mesh_holes.mesh_holes import from_manifold

from . import LAYOUTS, TOOL
from . import form_replicator as fr


def source_solid(V, F):
    """A valid indexed solid that also stays closed when an STL reader welds coincident vertices.

    Manifold can represent pinched sheets using duplicate vertices. A triangle-soup format cannot preserve
    that distinction, so these sources need the existing volumetric repair rather than an apparently exact pass.
    """
    import trimesh
    candidate = solid(V, F)
    if candidate is None:
        return None
    vc, fc = from_manifold(candidate)
    welded = gm.clean(trimesh.Trimesh(vc, fc, process=False))
    return candidate if welded.is_volume else None


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    import trimesh
    start = time.perf_counter()
    src = Path(src)
    if ctx.dry_run:
        dst = None
    if dst is not None:
        dst = Path(dst)
        if dst.resolve() == src.resolve() or (dst.exists() and os.path.samefile(src, dst)):
            raise ValueError("the output must not replace the input model")
        if dst.suffix.lower() not in TOOL.output.formats:
            raise ValueError("Form Replicator exports STL, OBJ or PLY")
    ctx.progress(0, "planning composition")
    composition = fr.plan(p)                 # reject impossible layouts before loading a large source
    copies = len(composition.transforms)
    ctx.progress(0.02, "loading source model")
    mesh = gm.load_mesh(src)
    up, front, rotate, axes_source = frame3d.resolve_axes(src, p, mesh.extents)
    frame = frame3d.fit_frame(mesh.vertices, up, front, rotate, mode="height", size=frame3d.MODEL_HEIGHT)
    original_v = frame.apply(mesh.vertices)
    original_f = np.asarray(mesh.faces, np.int64)
    detail = p.source_faces
    if ctx.draft:
        preview_detail = min(p.draft_faces, max(64, fr.DRAFT_TOTAL_FACES // copies))
        detail = min(detail, preview_detail) if detail else preview_detail
    fr.check_faces(copies, min(len(original_f), detail) if detail else len(original_f), p.work_faces)
    ctx.progress(0.07, "checking source solid")
    V, F = original_v, original_f
    model = source_solid(V, F)
    repaired = False
    if model is None:
        if not p.repair_open:
            raise ValueError("the source is not a closed solid; enable Close open source or close it before replication")
        else:
            resolution = min(p.repair_resolution, 96) if ctx.draft else p.repair_resolution
            ctx.warn(f"the source is not a closed solid: closing it at {resolution} samples; fine detail may soften")
            V, F = closed_copy(original_v, original_f, resolution,
                               lambda t, msg="": ctx.progress(0.08 + 0.14 * t, "closing source: " + msg))
            model = source_solid(V, F)
            repaired = True
            if model is None:
                raise ValueError("the source could not be closed into a solid; close it before replication")
    if detail and len(F) > detail:
        # A decimator's smallest target can pinch thin features (notably fractals). Increase its target until
        # an exportable solid survives, respecting the user's total budget; never silently export the pinch.
        target = int(detail)
        chosen = None
        while target < len(F) and target * copies <= p.work_faces:
            ctx.progress(0.22, f"simplifying source (target {target:,} faces)")
            reduced = gm.simplify_faces(V, F, target, ctx.log)
            chosen = source_solid(*reduced)
            if chosen is not None:
                break
            target *= 2
        if chosen is not None:
            model = chosen
        if model.num_tri() > detail * 1.05:
            ctx.warn(f"keeping {model.num_tri():,} source faces: a lower detail pinched the solid")
    fr.check_faces(copies, model.num_tri(), p.work_faces)
    if p.anchor == "base":
        model = model.translate((0, 0, -float(original_v[:, 2].min())))
    ctx.progress(0.24, "replicating source")
    output = fr.fuse(model, composition.transforms, p.work_faces,
                     lambda t, msg="": ctx.progress(0.24 + 0.58 * t, msg))
    ctx.progress(0.83, "preparing sculpture preview")
    # STL and PLY store float32 model coordinates. Check precisely that representation before publishing any
    # format; a very small boolean sliver can collapse when the original units/offset are restored.
    def export_mesh(m):
        vertices, faces = from_manifold(m)
        return gm.clean(trimesh.Trimesh(frame.inverse(vertices).astype(np.float32), faces, process=False))

    result_mesh = export_mesh(output)
    contact_growth = 0.0
    if not result_mesh.is_volume and p.contact_weld > 0:
        # An exact point/edge contact is not a manifold solid in triangle-soup formats. A bounded, explicit
        # enlargement turns that contact into overlap. Keep the planned centres/anchors and every source detail.
        ctx.progress(0.84, "welding pinched contacts")
        transforms = composition.transforms.copy()
        transforms[:, :, :3] *= 1 + p.contact_weld / 100
        output = fr.fuse(model, transforms, p.work_faces,
                         lambda t, msg="": ctx.progress(0.84 + 0.08 * t, msg))
        result_mesh = export_mesh(output)
        contact_growth = p.contact_weld
        ctx.warn(f"contact weld enlarged copies by {contact_growth:g}% to resolve pinched contacts")
    if not result_mesh.is_volume:
        raise ValueError("the composition has pinched contacts or details smaller than the mesh format can preserve; "
                         "change Arm step, Radius or Copy size slightly, or lower Source detail")
    preview = gm.preview(result_mesh)
    bodies = sum(part.volume() > 0 for part in output.decompose())  # closed cavity shells are not bodies
    ext = result_mesh.extents
    source_detail = model.num_tri()
    report = [src.name,
              f"  layout     {dict(LAYOUTS)[p.layout]}, {p.plane.upper()} plane",
              f"  frame      up {up}, front {front}, extra turn {tuple(rotate)} ({axes_source})",
              f"  copies     {copies} ({composition.omitted} left out of {composition.proposed})",
              f"  placement  {p.orientation}, aim {p.aim_axis}, anchor {p.anchor}; size {p.copy_size:g}%, end {p.end_scale:g}%",
              f"  variation  irregularity {p.irregularity:g}, seed {p.seed}",
              f"  source     {len(original_f):,} -> {source_detail:,} faces" + ("; closed by resampling" if repaired else "; exact solid"),
              f"  union      exact; {bodies} separate solid component(s); touching copies fused, gaps retained",
              f"  faces      {len(result_mesh.faces):,}",
              f"  size       {ext[0]:.5g} x {ext[1]:.5g} x {ext[2]:.5g} model units",
              f"  drawing    feed this mesh to Plotter Vision, Contour Lines or Sketch Hatching",
              f"  time       {time.perf_counter() - start:.1f} s"]
    if contact_growth:
        report.append(f"  weld       copies enlarged {contact_growth:g}% to turn pinched contacts into overlap")
    if ctx.draft:
        report.append("  preview    reduced source detail; all copy placements match Run")
    # Prepare everything before publication. The shared publisher leaves an existing output intact on failure/cancel.
    ctx.progress(0.94, "saving sculpture" if dst is not None else "preview ready")
    outputs = []
    if dst is not None:
        with Publisher(ctx, dst, lambda m, path: gm.save_mesh(m, path, TOOL), prefix=".form-replicator-") as pub:
            outputs = pub.commit(result_mesh)
        report.append(f"  saved as   {dst}")
    return Result(outputs=outputs,
                  stats=[Stat("copies", "Copies", copies), Stat("solids", "Solids", bodies),
                         Stat("faces", "Faces", len(result_mesh.faces), before=len(original_f)),
                         Stat("size", "Size", " × ".join(f"{x:.4g}" for x in ext), fmt="{}")],
                  report="\n".join(report), preview={"views": [{"title": "Output", "kind": "mesh", "mesh": preview}],
                                                    "prefer": "Output"},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"copies": copies, "solids": bodies, "source_faces": source_detail, "repaired": repaired,
                        "contact_growth": contact_growth})


def make_sample(folder: Path) -> Path:
    import trimesh
    path = folder / "form_sample.stl"
    trimesh.creation.icosphere(subdivisions=1, radius=20).export(path)
    return path
