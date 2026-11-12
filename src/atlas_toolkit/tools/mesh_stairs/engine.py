"""Toolkit adapter for Stair Sculpt: load, set up the model's frame, build, publish one signed closed solid."""
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
from . import mesh_stairs as ms

MODES = {"carve": "carved", "treads": "treads", "cut": "cut"}
TYPES = {"linear": "linear", "turns": "turning", "helix": "helical"}


def selection_text(p) -> str:
    if p.get("selection_file"):
        path = Path(p["selection_file"])
        if path.stat().st_size > 32_000_000:
            raise ValueError("Selection file is too large (maximum 32 MB).")
        return path.read_text(encoding="utf-8-sig")
    return p.get("selection", "")


def uses_paint(p) -> bool:
    return p["mode"] == "carve" and p["placement"] == "paint"


def run(ctx, src, dst, p) -> Result:
    started = time.perf_counter()
    src = Path(src)
    if ctx.dry_run:
        dst = None
    if dst is not None and Path(dst).resolve() == src.resolve():
        raise ValueError("The output must not replace the input model.")
    ctx.progress(0.01, "loading model")
    original = gm.load_mesh(src)
    if len(original.faces) > p.max_faces:
        raise ValueError("Input exceeds Maximum faces. Simplify the model or raise the limit.")
    if not len(original.faces) or not np.isfinite(original.vertices).all():
        raise ValueError("Input has no finite surface.")
    selected = None
    if uses_paint(p):
        text = selection_text(p)
        if not text:
            raise ValueError("Paint an area in the Selection tab first, or choose Opening: On a side.")
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
                           lambda t, msg="": ctx.progress(0.02 + 0.08 * t, "repair: " + msg))
        mesh = trimesh.Trimesh(v, f)
        body, _ = se.solid(mesh)
        repaired = True
    ctx.progress(0.12, "reading the model's shape")
    shape = ms.Shape(mesh.vertices, mesh.faces, body)
    report = ms.Report()
    ctx.progress(0.18, "building the staircase")
    if p.mode == "carve":
        built = ms.carve(shape, p, report, ctx, selected)
    elif p.mode == "treads":
        built = ms.treads(shape, p, report, ctx)
    else:
        built = ms.cut(shape, p, report, ctx)
    ctx.check_cancel()
    ctx.progress(0.8, "fusing the solid")
    if built.is_empty():
        raise ValueError("Nothing of the model is left inside the stairs: lower Well or raise Thickness.")
    built, kept, dropped = ms.tidy(built, bool(p.keep_fragments))
    V, F, precision = ms.closed_arrays(built, frame.inverse)
    out = trimesh.Trimesh(V, F, process=False)
    if len(out.faces) > p.max_faces:
        raise ValueError("The output exceeds Maximum faces. Lower Steps or Flights.")
    if not out.is_watertight or not out.is_winding_consistent or not np.isfinite(out.vertices).all():
        raise ValueError("The staircase failed closed-solid validation; nothing was published.")
    remaining = float(abs(out.volume)) / max(float(abs(original.volume)), 1e-12) * 100.0
    if dropped:
        ctx.log(f"{dropped} floating piece(s) were left out; Keep floating pieces keeps them.")
    outputs = []
    if dst is not None:
        surface = from_mesh(out)
        surface.frame = frame.frame_values()

        def validate_saved(path):
            saved = gm.load_mesh(path)
            if not saved.is_watertight or not saved.is_winding_consistent or not np.isfinite(saved.vertices).all():
                raise ValueError("The saved staircase lost its closed topology. Try OBJ or PLY.")
        outputs = publish_meshes([surface], dst, ctx, TOOL, validator=validate_saved)
    lines = [src.name,
             f"  mode       {MODES[p.mode]}, {TYPES[p.stair_type]}: {report.flights} flight(s), {report.steps} steps",
             f"  frame      up {up}, front {front} ({source}); original units and placement retained",
             f"  material   {remaining:.1f}% of the model's volume remains"]
    if p.mode == "carve":
        where = "painted area" if selected is not None else f"{p.side} side, {p.open_shape}"
        lines.append(f"  opening    {where}; the cavity took {report.cavity:.1f}% of the volume")
    lines += [f"  note       {n}" for n in report.note]
    if repaired:
        lines.append("  input      repaired by resampling")
    lines.append(f"  solid      {kept} piece(s)" + (f", {dropped} floating piece(s) left out" if dropped else ""))
    if precision:
        lines.append(f"  precision  touching edges opened by {precision / frame.scale:.5g} model units")
    lines += [f"  saved as   {o}" for o in outputs]
    lines.append(f"  time       {time.perf_counter() - started:.1f} s")
    ctx.progress(1.0, "Saved" if outputs else "Previewed")
    views = [{"title": "Before", "kind": "mesh", "mesh": gm.preview(original)},
             {"title": "Staircase", "kind": "mesh", "mesh": gm.preview(out, max_faces=int(p.max_faces)),
              "status": "Final geometry"}]
    return Result(outputs=outputs,
                  stats=[Stat("steps", "Steps", report.steps),
                         Stat("flights", "Flights", report.flights),
                         Stat("remaining", "Material left", remaining, "%", fmt="{:.0f}"),
                         Stat("faces", "Faces", len(out.faces), before=len(original.faces))],
                  report="\n".join(lines), preview={"views": views, "prefer": "Staircase"},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"steps": report.steps, "flights": report.flights, "pieces": kept, "dropped": dropped,
                        "remaining": remaining, "repaired": repaired, "frame": frame.frame_values()})


def make_sample(folder):
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=30.0)
    mesh.vertices *= [1.0, 0.85, 1.5]
    path = Path(folder) / "stair-sample.stl"
    mesh.export(path)
    return path
