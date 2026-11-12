"""Standard Atlas adapter: same final geometry for Preview, UI, CLI and Workflows."""
from pathlib import Path
import time
from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d, mesh as gm
from atlas_toolkit.geom.mesh_export import publish_meshes
from . import TOOL
from .mesh_motion import sculpt


def run(ctx, src, dst, p):
    import trimesh
    from atlas_toolkit.geom.surface import from_mesh
    started = time.perf_counter()
    src = Path(src)
    if ctx.dry_run:
        dst = None
    if dst is not None and Path(dst).resolve() == src.resolve():
        raise ValueError("Output must not replace the input model.")
    if p.influence == "band" and p.band_from >= p.band_to:
        raise ValueError("Band from must be below Band to.")
    ctx.progress(.02, "loading source model")
    original = gm.load_mesh(src)
    if not original.is_watertight or not original.is_winding_consistent or original.volume <= 0:
        raise ValueError("Motion Sculpture requires a closed, consistently oriented solid. Repair the model first.")
    up, front, rotate, _ = frame3d.resolve_axes(src, p, original.extents)
    frame = frame3d.fit_frame(original.vertices, up, front, rotate, mode="height", size=2.)
    canonical = trimesh.Trimesh(frame.apply(original.vertices), original.faces, process=False)
    painted = None
    if p.influence == "paint":
        from .selection import PaintedInfluence, selection_text, decode_selection
        selected = decode_selection(original, selection_text(p))
        painted = PaintedInfluence(canonical, selected, p.paint_feather*.02, ctx)
    output, info = sculpt(canonical, p, ctx, painted)
    if info.get("paint_guard_scale", 1.) < 1.:
        ctx.warn(f"Local paint protection retained {100*info['paint_guard_scale']:.2g}% average motion. "
                 "Paint a larger area, increase Paint feather, or reduce wake length/curl/taper.")
    output.vertices = frame.inverse(output.vertices)
    if p.amount == 0:
        output = original.copy()
    if painted is not None and p.amount != 0:
        from .selection import export_safe
        output, precision = export_safe(output, float(original.extents.max()), ctx)
        info["paint_export_precision"] = precision
    surface = from_mesh(output)
    surface.frame = frame.frame_values()
    def validate_saved(path):
        if painted is not None:
            saved = gm.load_mesh(path)
            if not saved.is_watertight or not saved.is_winding_consistent or saved.volume <= 0:
                raise ValueError("Painted output failed file reload validation; no output was published.")
    outputs = publish_meshes([surface], dst, ctx, TOOL, validator=validate_saved) if dst is not None else []
    ctx.progress(.96, "preparing final geometry preview")
    report = [src.name, f"  motion     {p.structure}, {p.gesture}; heading {p.heading:g}°; seed {p.seed}",
              f"  layers     {info['layers']}; {info['pieces']} closed shells",
              f"  wake       up to {info['max_reach']/frame.scale:.5g} source units",
              f"  geometry   {len(original.faces):,} → {len(output.faces):,} faces; original units and placement",
              (("  assembly   sampled solid layers; wakes rooted only at painted outward faces" if p.paint_style == "layers" else
                "  assembly   connected painted surface; unpainted surface fixed") if painted is not None else
               "  assembly   separate shells, not fused; bars may intersect after sideways curl"),
              "  preview    final geometry; display may use a proxy",
              f"  time       {time.perf_counter()-started:.2f} s"]
    if painted is not None:
        report += [f"  painted    {int(selected.sum())} / {len(selected)} source faces; feather {p.paint_feather:g}% h",
                   f"  protection average retained motion {info.get('paint_guard_scale', 1.):.6g}",
                   "  contact    local triangle guard; distant self-intersections are not tested"]
        if p.paint_style == "layers":
            report.append(f"  section    pitch {info.get('paint_section_pitch', 0.)/frame.scale:.6g} source units; silhouette sampled at layer centres")
        else:
            report.append(f"  displacement actual maximum {info.get('max_displacement', 0.)/frame.scale:.6g} source units")
    if painted is not None and p.paint_style == "layers":
        # Vertex clustering erases thin fins and gaps. Preserve their silhouette
        # with quadric reduction instead, or show the full modest-sized result.
        v, f = gm.simplify_faces(output.vertices, output.faces, 220000, ctx.log)
        display = gm.preview(trimesh.Trimesh(v, f, process=False), max_faces=220000)
        display.faces_total = len(output.faces)
        display.bounds = tuple(tuple(float(x) for x in row) for row in output.bounds)
    else:
        display = gm.preview(output)
    ctx.progress(1.)
    stats = [Stat("pieces", "Pieces", info["pieces"]), Stat("faces", "Faces", len(output.faces), before=len(original.faces))]
    if painted is not None:
        stats += [Stat("retained", "Motion retained", 100*info.get("paint_guard_scale", 1.), "%", fmt="{:.1f}")]
    return Result(outputs=outputs, stats=stats,
                  report="\n".join(report), status="Saved" if outputs else "Previewed",
                  preview={"views": [{"title": "Motion", "kind": "mesh", "mesh": display},
                                     {"title": "Source", "kind": "mesh", "mesh": gm.preview(original)}], "prefer": "Motion"},
                  data={**info, "frame": frame.frame_values()})


def make_sample(folder):
    import trimesh
    mesh = trimesh.creation.icosphere(subdivisions=2, radius=10.)
    mesh.apply_scale((.6, .45, 1.3))
    mesh.apply_translation((4., -2., 8.))
    path = Path(folder) / "motion-sample.stl"
    mesh.export(path)
    return path
