"""Toolkit adapter for Crumble: load, set up the model's frame, collapse, publish in the model's own units."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.mesh_export import publish_meshes
from atlas_toolkit.geom.surface import from_mesh
from atlas_toolkit.tools.mesh_surface_eject import mesh_surface_eject as se

from . import TOOL
from . import mesh_crumble as mc


def selection_text(p) -> str:
    if p.get("selection_file"):
        path = Path(p["selection_file"])
        if path.stat().st_size > 32_000_000:
            raise ValueError("Selection file is too large (maximum 32 MB).")
        return path.read_text(encoding="utf-8-sig")
    return p.get("selection", "")


def validate(p) -> None:
    if p["fuse"] and p["bridge_size"] <= 0.0:
        raise ValueError("Bridge thickness must be above 0 when fusing.")


def to_trimesh(manifold, inverse) -> trimesh.Trimesh:
    v, f = mc.to_arrays(manifold)
    return trimesh.Trimesh(inverse(v), f, process=False)


def run(ctx, src, dst, p) -> Result:
    started = time.perf_counter()
    src = Path(src)
    if ctx.dry_run:
        dst = None
    if dst is not None and Path(dst).resolve() == src.resolve():
        raise ValueError("The output must not replace the input model.")
    validate(p)
    ctx.progress(0.01, "loading model")
    original = gm.load_mesh(src)
    if len(original.faces) > p.max_faces:
        raise ValueError("Input exceeds Maximum faces. Simplify the model or raise the limit.")
    if not len(original.faces) or not np.isfinite(original.vertices).all():
        raise ValueError("Input has no finite surface.")
    selected = None
    if p.area == "paint":
        text = selection_text(p)
        if not text:
            raise ValueError("Paint an area in the Selection tab first, or choose Area: Side.")
        selected = se.decode_selection(original, text)
    up, front, rotate, source = frame3d.resolve_axes(src, p, original.extents)
    frame = frame3d.fit_frame(original.vertices, up, front, rotate, mode="height", size=frame3d.MODEL_HEIGHT)
    mesh = trimesh.Trimesh(frame.apply(original.vertices), original.faces.copy(), process=False)
    if mesh.is_watertight and mesh.is_winding_consistent and mesh.volume < 0:
        mesh.invert()
    repaired = False
    try:
        body, _ = se.solid(mesh)
    except ValueError:
        if p.repair == "strict":
            raise
        if selected is not None:
            raise ValueError("Repair changes the painted faces. Repair and save this model first, then paint the "
                             "closed input.")
        from atlas_toolkit.tools.mesh_holes.engine import closed_copy
        ctx.warn("Resampling repair changes the original surface and may soften detail.")
        v, f = closed_copy(mesh.vertices, mesh.faces, int(p.repair_resolution),
                           lambda t, msg="": ctx.progress(0.02 + 0.03 * t, "repair: " + msg))
        mesh = trimesh.Trimesh(v, f)
        body, _ = se.solid(mesh)
        repaired = True
    built = mc.crumble(np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces, dtype=np.int64), body, p,
                       selected, progress=ctx.progress, draft=ctx.draft)
    ctx.check_cancel()
    report = built.report
    ctx.progress(0.92, "assembling the result")
    inverse = frame.inverse
    standing = to_trimesh(built.body, inverse) if built.body is not None else None
    loose = [to_trimesh(b, inverse) for b in built.blocks]
    plate = to_trimesh(built.plate, inverse) if built.plate is not None else None
    if built.fused is not None:
        combined = to_trimesh(built.fused, inverse)
    else:
        combined = trimesh.util.concatenate([m for m in (standing, *loose, plate) if m is not None])
    if len(combined.faces) > p.max_faces:
        raise ValueError("The output exceeds Maximum faces. Raise Block size or simplify the model.")
    if not np.isfinite(combined.vertices).all():
        raise ValueError("The collapse produced invalid geometry; nothing was published.")
    outputs = []
    if dst is not None:
        surface = from_mesh(combined)
        surface.frame = frame.frame_values()
        outputs = publish_meshes([surface], dst, ctx, TOOL)
        if p.separate:
            folder = Path(dst).with_name(f"{src.stem}_blocks_{len(loose)}pcs")
            folder.mkdir(parents=True, exist_ok=True)
            suffix = Path(dst).suffix
            for i, block in enumerate(loose):
                ctx.progress(0.94 + 0.05 * i / max(len(loose), 1), "saving blocks")
                outputs.append(str(gm.save_mesh(block, folder / f"{src.stem}_block_{i:04d}{suffix}", TOOL)))
            if standing is not None:
                outputs.append(str(gm.save_mesh(standing, folder / f"{src.stem}_standing{suffix}", TOOL)))
    lines = [src.name,
             f"  frame      up {up}, front {front} ({source}); original units and placement retained",
             f"  blocks     {report.blocks}" + (f", {report.rubble} of them rubble" if report.rubble else "")
             + f", from {p.block_style} cells of {p.block_size:g}% of the height",
             f"  hollowed   {report.hollowed:.1f}% of the model's volume left its place",
             f"  collapse   {report.seconds:.2f} s simulated at {p.collapse:g}%: {report.released} let go "
             f"({report.falling} still moving, {report.resting} at rest), {report.clinging} still in place"]
    if report.cracks:
        lines.append(f"  cracks     {report.cracks} slots around the area")
    if report.tiny:
        lines.append(f"  crumbs     {report.tiny} pieces under Smallest block were left out")
    if report.lost:
        lines.append(f"  lost       {report.lost} blocks fell away past the model (no floor)")
    if built.fused is not None:
        lines.append(f"  fused      one solid, {report.bridges} bridges; {report.dropped} floating pieces left out")
    else:
        lines.append("  solids     loose closed blocks and the standing body, in one file")
    if repaired:
        lines.append("  input      repaired by resampling")
    lines += [f"  note       {n}" for n in report.notes]
    lines += [f"  saved as   {o}" for o in outputs[:1]]
    if len(outputs) > 1:
        lines.append(f"             + {len(outputs) - 1} more file(s)")
    lines.append(f"  time       {time.perf_counter() - started:.1f} s")
    ctx.progress(1.0, "Saved" if outputs else "Previewed")
    views = [{"title": "Before", "kind": "mesh", "mesh": gm.preview(original)},
             {"title": "Crumble", "kind": "mesh", "mesh": gm.preview(combined, max_faces=int(p.max_faces)),
              "status": "Final geometry" if not ctx.draft else "Draft · coarser collisions"}]
    if standing is not None and loose and built.fused is None:
        views.append({"title": "Hollowed body", "kind": "mesh", "mesh": gm.preview(standing)})
        views.append({"title": "Blocks", "kind": "mesh", "mesh": gm.preview(trimesh.util.concatenate(loose))})
    return Result(outputs=outputs,
                  stats=[Stat("blocks", "Blocks", report.blocks),
                         Stat("falling", "Still falling", report.falling),
                         Stat("hollowed", "Hollowed", report.hollowed, "%", fmt="{:.0f}"),
                         Stat("faces", "Faces", len(combined.faces), before=len(original.faces))],
                  report="\n".join(lines), preview={"views": views, "prefer": "Crumble"},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"blocks": report.blocks, "released": report.released, "falling": report.falling,
                        "resting": report.resting, "clinging": report.clinging, "hollowed": report.hollowed,
                        "moved": built.moved.tolist(), "bridges": report.bridges, "dropped": report.dropped,
                        "cracks": report.cracks, "repaired": repaired, "frame": frame.frame_values()})


def make_sample(folder):
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=30.0)
    mesh.vertices *= [1.0, 0.85, 1.5]
    path = Path(folder) / "crumble-sample.stl"
    mesh.export(path)
    return path
