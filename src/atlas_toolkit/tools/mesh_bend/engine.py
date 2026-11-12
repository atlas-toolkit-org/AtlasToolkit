"""Load, frame, bend and publish a model through the standard Atlas contract."""
from pathlib import Path
import time
import numpy as np
import trimesh

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d, stages
from atlas_toolkit.geom import mesh as gm
from . import TOOL
from . import mesh_bend as mb


def run(ctx, src, dst, p):
    started = time.perf_counter()
    src = Path(src)
    if ctx.dry_run:
        dst = None
    if dst is not None and Path(dst).resolve() == src.resolve():
        raise ValueError("The output must not replace the input model.")
    ctx.progress(.01, "Loading model")
    original = gm.load_mesh(src)
    if len(original.faces) > int(p.max_faces):
        raise ValueError(f"Input has {len(original.faces):,} faces; Maximum faces is {p.max_faces:,}. Simplify it or raise the limit.")
    up, front, rotate, source = frame3d.resolve_axes(src, p, original.extents)
    frame = frame3d.fit_frame(original.vertices, up, front, rotate, mode="height", size=2.)
    rest = frame.apply(original.vertices)
    bounds = np.array([rest.min(axis=0), rest.max(axis=0)])
    spine, scale = mb.protected_spine(bounds, p, float(p.amount), ctx)
    F = np.asarray(original.faces, np.int64)
    V = rest
    if p.refine and not spine.identity:
        V, F = mb.refine(V, F, spine, int(p.segments), int(p.max_faces), ctx)
    n = int(p.stages)
    if n * len(F) > 6_000_000:
        raise ValueError("The stage set exceeds 6 million total faces. Reduce Stages / Bend resolution or simplify the input.")
    us = stages.amounts(n, float(p.amount))
    manifest = {"tool": TOOL.id, "tool_version": TOOL.version, "source": src.name,
                "frame": {"up": up, "front": front, "rotate": list(rotate)},
                "values": dict(p), "angle_scale": scale}
    out = stages.StageOutput(ctx, TOOL, dst, n, p.layout, p.axis, float(p.gap) / 100 * 2 / frame.scale,
                             p.save_stages, manifest)
    if out.pub is not None:
        def save_validated(mesh, path):
            from atlas_toolkit.geom.surface import from_mesh
            surface = from_mesh(mesh)
            surface.frame = frame.frame_values()
            gm.save_mesh(surface, path, TOOL)
            ctx.check_cancel()
            saved = gm.load_mesh(path)
            if (len(saved.faces) != len(mesh.faces) or not np.isfinite(saved.vertices).all()
                    or not saved.nondegenerate_faces().all()
                    or (original.is_winding_consistent and not saved.is_winding_consistent)
                    or (original.is_watertight and not saved.is_watertight)):
                raise ValueError("Saved bend lost valid topology. Recenter very large coordinates or reduce the bend / roll.")
        out.pub.save = save_validated
    rows = []
    with out:
        for i, amount in enumerate(us):
            ctx.progress(.45 + .4 * i / n, f"Bending stage {i + 1} of {n}")
            # One common guard factor preserves the gesture through a sequence; guard each intermediate too.
            current = mb.Spine(bounds, p, amount, scale)
            if p.fold_guard and current.min_jacobian < .15:
                from atlas_toolkit.core.params import Params
                current, extra = mb.protected_spine(bounds, Params(dict(p, angle=float(p.angle) * scale)), amount, ctx)
                stage_scale = scale * extra
            else:
                stage_scale = scale
            bent = current.deform(V)
            mesh = trimesh.Trimesh(frame.inverse(bent), F.copy(), process=False)
            # Keep exact source coordinates at rest, avoiding frame roundoff for the source vertices.
            if current.identity:
                mesh.vertices[:len(rest)] = original.vertices
            if not np.isfinite(mesh.vertices).all() or not mesh.nondegenerate_faces().all():
                raise ValueError("The bend collapsed a face. Enable Protect inner curve, extend the range or reduce the angle / roll.")
            if original.is_watertight and not mesh.is_watertight:
                raise ValueError("The bend lost closed surface topology; no output was published.")
            moved = float(np.linalg.norm(bent - V, axis=1).max()) / 2 * 100
            rows.append({"amount": amount, "angle": current.angle, "angle_scale": stage_scale,
                         "min_jacobian": current.min_jacobian, "moved": moved})
            out.add(i, mesh, rows[-1])
            ctx.check_cancel()
        outputs = out.finish()
    last = rows[-1]
    if scale < .999:
        ctx.warn(f"Inner-curve protection reduced Angle to {last['angle']:.2f}° ({100 * scale:.1f}% of the requested bend).")
    if not p.fold_guard and min(row['min_jacobian'] for row in rows) < .15:
        ctx.warn("The inner curve may fold: Protect inner curve is off and the local Jacobian bound is below 0.15.")
    report = [src.name, f"  profile    {p.profile}; axis {p.bend_axis}; direction {p.direction:g}°; sweep {p.sweep:g}°; roll {p.roll:g}°",
              f"  range      {p.bend_from:g}–{p.bend_to:g}% of axis; anchor {p.anchor}; neutral U/V {p.neutral_u:g}/{p.neutral_v:g}% h",
              f"  frame      up {up}, front {front} ({source}); original model units and placement",
              f"  geometry   {len(original.faces):,} -> {len(F):,} triangles per stage; linear conforming refinement",
              f"  protection {'enabled' if p.fold_guard else 'disabled'}; conservative local fold bound; distant self-contact is not tested"]
    for i, row in enumerate(rows):
        report.append(f"  stage {i}    amount {row['amount']:.3g}; achieved angle {row['angle']:.3g}°; local Jacobian ≥ {row['min_jacobian']:.4g}; movement {row['moved']:.2f}% h")
    report += ["  drawing    Contour Lines for flowing sections; Sketch Hatching for sculptural light; Plotter Vision for faceted source meshes",
               f"  time       {time.perf_counter() - started:.3f} s"]
    report += ["  saved      " + path for path in outputs]
    views = [{"title": "Before", "kind": "mesh", "mesh": gm.preview(original)}] + out.views
    for view in views[1:]:
        view["status"] = "Final geometry" if len(F) <= gm.PREVIEW_FACES else "Display proxy · final mesh retained"
    ctx.progress(1., "Saved" if outputs else "Previewed")
    return Result(outputs=outputs, report="\n".join(report),
                  stats=[Stat("angle", "Achieved angle", last["angle"], "°", fmt="{:.1f}"),
                         Stat("faces", "Faces", len(out.primary.faces), before=len(original.faces)),
                         Stat("moved", "Movement", last["moved"], "% h", fmt="{:.1f}"),
                         Stat("stages", "Stages", n)],
                  preview={"views": views, "prefer": out.prefer}, status="Saved" if outputs else "Previewed",
                  data={"stages": rows, "angle_scale": scale, "faces_per_stage": len(F), "frame": manifest["frame"],
                        "seconds": time.perf_counter() - started})


def make_sample(folder):
    mesh = trimesh.creation.capsule(height=75., radius=10., count=[12, 16])
    mesh.vertices *= [1., .65, 1.]
    mesh.apply_translation([7., -4., 20.])
    path = Path(folder) / "bend-sample.stl"
    mesh.export(path)
    return path
