"""Atlas mirror adapter; the common contract supplies CLI, presets, history and workflow behavior."""
from pathlib import Path

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.mesh_export import publish_meshes

from . import TOOL
from .mesh_mirror import combine, mirror


def run(ctx, src: Path, dst: Path | None, p):
    ctx.progress(.02, "loading model")
    original = gm.load_mesh(src)
    ctx.progress(.2, "reflecting across plane")
    reflected, (origin, normal) = mirror(original, p)
    keep_original = p.result_mode == "copy"
    output = combine(original, reflected) if keep_original else reflected
    ctx.progress(.6, "preparing mirrored model")
    separate = keep_original and p.export_mode == "separate"
    meshes = [reflected, original] if separate else [output]
    outputs = publish_meshes(meshes, dst, ctx, TOOL, extra_label="original") if dst is not None else []
    ctx.progress(.95, "preparing preview")
    views = [{"title": "Output", "kind": "mesh", "mesh": gm.preview(output)}]
    if keep_original:
        views.append({"title": "Mirrored", "kind": "mesh", "mesh": gm.preview(reflected)})
    report = [src.name, "  result     " + ("original + reflected copy" if keep_original else "reflected model only"),
              "  origin     " + " / ".join(f"{v:.8g}" for v in origin),
              "  normal     " + " / ".join(f"{v:.8g}" for v in normal),
              "  geometry   reflection, reversed triangle winding; original model units",
              f"  faces      {len(original.faces)} input, {len(output.faces)} output",
              f"  reflected  {'closed' if reflected.is_watertight else 'open, like input'}",
              "  joining    none; parts are not cut, welded or unioned"]
    if separate:
        report.append("  workflow   the mirrored main file continues; the original is an extra Save output")
    elif keep_original:
        report.append("  shells     touching/overlapping parts may be welded by downstream importers; separate files retain identity")
    report += ["  saved      " + path for path in outputs]
    ctx.progress(1.)
    return Result(outputs=outputs, stats=[Stat("copies", "Models", 2 if keep_original else 1),
                  Stat("faces", "Faces", len(output.faces), before=len(original.faces))],
                  report="\n".join(report), preview={"views": views, "prefer": "Output"},
                  status="Saved" if outputs else "Previewed")


def make_sample(folder):
    import trimesh
    mesh = trimesh.creation.icosphere(subdivisions=2, radius=10)
    mesh.apply_scale((.7, 1., 1.3))
    # Uneven placement makes accidental centre/normal/coordinate errors visible in the self-test input.
    mesh.apply_translation((4., -3., 7.))
    path = Path(folder) / "mirror-sample.stl"
    mesh.export(path)
    return path
