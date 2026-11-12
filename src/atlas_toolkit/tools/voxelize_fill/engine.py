"""Atlas adapter: shared schema, signed staged exports and non-writing previews."""
from __future__ import annotations

from pathlib import Path
import time

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.frame3d import fmt_of, guess_axes
from atlas_toolkit.geom.mesh_export import publish_meshes
from atlas_toolkit.geom.surface import read_frame

from . import TOOL
from .voxelize_fill import build


def run(ctx, src, dst, p):
    start = time.perf_counter()
    ctx.progress(0.01, "loading model")
    source = gm.load_mesh(src)
    values = dict(p)
    output, data, warnings = build(source, values, ctx.progress)
    if output.frame is None:
        output.frame = read_frame(src)
    if output.frame is None:
        up, front = guess_axes(source.extents, fmt_of(src))
        output.frame = dict(up=up, front=front, rotate=[0., 0., 0.])
    if p.geometry == "joined":
        data["watertight"] = bool(output.as_trimesh().is_watertight)
        if not data["watertight"]:
            warnings.append("Joined boundary has non-manifold cell contacts. It represents the occupied volume, "
                            "but is not a validated printable manifold; use separate blocks to isolate cells.")
    for warning in warnings:
        ctx.warn(warning)
    ctx.progress(0.79, "preparing mesh preview")
    preview = gm.preview(output)
    status = "Final geometry"
    if len(output.triangles) > gm.PREVIEW_FACES:
        status += f" · display proxy {len(preview.faces):,} triangles"
    if preview.edge_segments is not None and len(preview.edge_segments) == 160000:
        warnings.append("Preview wireframe is sampled to 160,000 edges; the saved geometry is complete.")
    outputs = publish_meshes([output], dst, ctx, TOOL) if dst is not None else []
    lines = [src.name, f"  volume grid   {' x '.join(map(str, data['grid']))}; cell edge {data['pitch']:.8g} units",
             "  occupancy     cell-centre winding number, absolute threshold > 0.5",
             f"  source cells  {data['voxels']:,}; occupied volume {data['occupied_volume']:.8g} cubic units",
             f"  presentation  {p.effect}, cutaway {'on' if p.cutaway else 'off'}; {data['retained']:,} cells retained, "
             f"{data['removed']:,} removed",
             f"  geometry      {p.geometry}" + (f", {p.layout}, {data['blocks']:,} blocks, gap {p.gap:g}% cell edge"
                                                          if p.geometry == "separate" else ", exposed boundary only"),
             f"  output        {data['quads']:,} native quads; {data['triangles']:,} triangles",
             "  coordinates   original model units and placement; preview uses final geometry",
             "  draw          OBJ/PLY: Plotter Vision Remeshed network; STL: Features or Clean",
             f"  time          {time.perf_counter() - start:.2f} s"]
    lines += warnings + ["  saved         " + path for path in outputs]
    for line in lines:
        ctx.log(line)
    ctx.progress(1., "saved" if outputs else "previewed")
    return Result(outputs=outputs, data=data, report="\n".join(lines),
                  stats=[Stat("voxels", "Occupied cells", data["voxels"]),
                         Stat("retained", "Retained cells", data["retained"]),
                         Stat("blocks", "Blocks", data["blocks"] if p.geometry == "separate" else "Joined", fmt="{}"),
                         Stat("faces", "Triangles", data["triangles"], before=len(source.faces)),
                         Stat("edge", "Cell edge", data["pitch"], "units", fmt="{:.5g}")],
                  preview={"views": [{"title": "Output", "kind": "mesh", "mesh": preview, "status": status}],
                           "prefer": "Output"}, status="Saved" if outputs else "Previewed")


def make_sample(folder):
    import trimesh
    mesh = trimesh.creation.icosphere(subdivisions=2, radius=10.)
    mesh.apply_scale((1., 0.8, 1.25))
    path = Path(folder) / "voxel-fill-sample.stl"
    mesh.export(path)
    return path
