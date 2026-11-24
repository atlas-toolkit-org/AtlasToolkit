"""Standard Atlas adapter: same final geometry for Preview, UI, CLI and Workflows."""
from pathlib import Path
import time
from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d, mesh as gm
from atlas_toolkit.geom import repair as R
from atlas_toolkit.geom.mesh_export import publish_meshes
from . import TOOL
from .mesh_motion import sculpt_within


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
    original = R.tidy_input(ctx, gm.load_mesh(src))
    if not original.is_watertight or not original.is_winding_consistent or original.volume <= 0:
        if p.repair == "strict":
            raise R.needs_repair(original, TOOL, "Open input › Repair by resampling")
        ctx.note("Closed by resampling (Open input › Repair by resampling): fine detail may soften.")
        v, f, _ = R.rebuild(original.vertices, original.faces, int(p.repair_resolution),
                            lambda t, msg="": ctx.progress(0.02 + 0.03 * float(t), "repair: " + str(msg)))
        original = trimesh.Trimesh(v, f, process=False)
        if not original.is_watertight or not original.is_winding_consistent or original.volume <= 0:
            raise R.needs_repair(original, TOOL)
    up, front, rotate, _ = frame3d.resolve_axes(src, p, original.extents)
    frame = frame3d.fit_frame(original.vertices, up, front, rotate, mode="height", size=2.)
    canonical = trimesh.Trimesh(frame.apply(original.vertices), original.faces, process=False)
    output, info, worked = sculpt_within(canonical, p, ctx)
    if worked:
        ctx.note(f"Sculpted a copy simplified from {len(canonical.faces):,} to {worked:,} faces: at full detail the "
                 f"wake would exceed Maximum faces ({p.max_faces:,}). Simplify to fit.")
    output.vertices = frame.inverse(output.vertices)
    if p.amount == 0:
        output = original.copy()
    surface = from_mesh(output)
    surface.frame = frame.frame_values()
    outputs = publish_meshes([surface], dst, ctx, TOOL) if dst is not None else []
    ctx.progress(.96, "preparing final geometry preview")
    report = [src.name, f"  motion     {p.structure}, {p.gesture}; heading {p.heading:g}°; seed {p.seed}",
              f"  layers     {info['layers']}; {info['pieces']} closed shells",
              f"  wake       up to {info['max_reach']/frame.scale:.5g} source units",
              f"  geometry   {len(original.faces):,} → {len(output.faces):,} faces; original units and placement",
              *([f"  working    simplified to {worked:,} faces to fit Maximum faces"] if worked else []),
              "  assembly   separate shells, not fused; bars may intersect after sideways curl",
              "  preview    final geometry; display may use a proxy",
              f"  time       {time.perf_counter()-started:.2f} s"]
    display = gm.preview(output)
    ctx.progress(1.)
    stats = [Stat("pieces", "Pieces", info["pieces"]), Stat("faces", "Faces", len(output.faces), before=len(original.faces))]
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
