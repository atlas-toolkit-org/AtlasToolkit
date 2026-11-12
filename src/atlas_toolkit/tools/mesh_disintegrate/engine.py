"""Atlas adapter: load, disintegrate, preview intact fragments, and transactionally publish signed meshes."""
from __future__ import annotations

import os
import struct
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.stages import publish

from . import TOOL
from . import mesh_disintegrate as mm

EXPORT_BATCH_FACES = 4096


def _save_mesh(ctx, triangles, path):
    """Use shared exporters directly for small meshes; bound work and memory for larger triangle soups.

    STL batches still use Atlas's signed exporter/normal calculation. OBJ and PLY use the same signature and
    the minimal untextured triangle representation, streaming vertices first and then globally indexed faces.
    A cancellation checkpoint between batches keeps the worker's 1.5-second kill timer from leaving big exports.
    """
    count = len(triangles)
    if count <= EXPORT_BATCH_FACES:
        gm.save_mesh(mm.as_mesh(triangles), path, TOOL)
        return
    signature = gm.signature_text(TOOL)
    ext = path.suffix.lower()
    if ext == ".stl":
        chunk = path.with_name(f".{path.stem}-chunk.stl")
        try:
            with path.open("wb") as output:
                output.write(signature.encode("ascii")[:80].ljust(80, b" "))
                output.write(struct.pack("<I", count))
                for start in range(0, count, EXPORT_BATCH_FACES):
                    ctx.check_cancel()
                    gm.save_mesh(mm.as_mesh(triangles[start:start + EXPORT_BATCH_FACES]), chunk, TOOL)
                    output.write(chunk.read_bytes()[84:])
        finally:
            chunk.unlink(missing_ok=True)
    elif ext == ".obj":
        with path.open("w", encoding="utf-8", newline="\n") as output:
            output.write(f"# {signature}\n")
            for start in range(0, count, EXPORT_BATCH_FACES):
                ctx.check_cancel()
                np.savetxt(output, triangles[start:start + EXPORT_BATCH_FACES].reshape(-1, 3),
                           fmt="v %.17g %.17g %.17g")
            for start in range(0, count, EXPORT_BATCH_FACES):
                ctx.check_cancel()
                stop = min(count, start + EXPORT_BATCH_FACES)
                faces = np.arange(start * 3 + 1, stop * 3 + 1).reshape(-1, 3)
                np.savetxt(output, faces, fmt="f %d %d %d")
    elif ext == ".ply":
        with path.open("wb") as output:
            output.write((f"ply\nformat binary_little_endian 1.0\ncomment {signature}\n"
                          f"element vertex {count * 3}\nproperty float x\nproperty float y\nproperty float z\n"
                          f"element face {count}\nproperty list uchar int vertex_indices\nend_header\n").encode("ascii"))
            for start in range(0, count, EXPORT_BATCH_FACES):
                ctx.check_cancel()
                output.write(triangles[start:start + EXPORT_BATCH_FACES].astype("<f4").tobytes())
            dtype = np.dtype([("count", "u1"), ("indices", "<i4", (3,))])
            for start in range(0, count, EXPORT_BATCH_FACES):
                ctx.check_cancel()
                stop = min(count, start + EXPORT_BATCH_FACES)
                faces = np.empty(stop - start, dtype=dtype)
                faces["count"] = 3
                faces["indices"] = np.arange(start * 3, stop * 3).reshape(-1, 3)
                output.write(faces.tobytes())
    else:
        raise ValueError("Disintegrate exports STL, OBJ or PLY")


def _publish(ctx, dst, primary, stages, separate):
    """Prepare everything beside the destination; the primary appears only after every export succeeds."""
    return publish(ctx, dst, primary, stages, separate, lambda triangles, path: _save_mesh(ctx, triangles, path),
                   prefix=".mesh-disintegrate-")


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    if ctx.dry_run:
        dst = None
    if dst is not None:
        if dst.resolve() == src.resolve() or (dst.exists() and os.path.samefile(src, dst)):
            raise ValueError("the output must not replace the input model")
        if dst.suffix.lower() not in TOOL.output.formats:
            raise ValueError("Disintegrate exports STL, OBJ or PLY")
    ctx.progress(0.0, "loading model")
    mesh = gm.load_mesh(src)
    frag = mm.generate(mesh, p.steps, p.strength, p.units, p.growth, p.direction, p.influence, p.seed,
                       progress=lambda f, msg: ctx.progress(0.05 + 0.60 * f, msg))
    stages = frag.stages
    gap = p.gap * (frag.diagonal / 100 if p.gap_units == "percent" else 1)
    offsets = mm.layout_offsets(stages, p.axis, gap)
    ctx.progress(0.67, "preparing stage previews")
    views = []
    for i, stage in enumerate(stages[1:], 1):
        views.append({"title": f"Stage {i}", "kind": "mesh", "mesh": mm.preview([stage])})
        ctx.check_cancel()
    progression = mm.preview(stages, offsets)
    views.append({"title": "Progression", "kind": "mesh", "mesh": progression})
    prefer = "Progression" if p.layout == "progression" or p.steps == 0 else f"Stage {p.steps}"
    ctx.progress(0.75, "preparing output")
    if p.layout == "progression":
        primary = np.concatenate([s + offset for s, offset in zip(stages, offsets)])
    else:
        primary = stages[-1]
    mm.checked_triangles(primary)
    box = mm.bounds(primary)
    size = box[1] - box[0]
    report = [src.name, f"  stages     {len(stages)} (original + {p.steps} steps)",
              f"  distortion {p.strength:g} {p.units}, growth {p.growth:g}, {p.direction}, seed {p.seed}",
              "  XYZ        " + ", ".join(f"{x:g}" for x in p.influence),
              f"  layout     {p.layout}, progression along {p.axis.upper()}, gap {gap:.5g} units",
              "  surfaces   independent triangle fragments; not closed solids"]
    for i, (stage, amplitude) in enumerate(zip(stages, frag.amplitudes)):
        report.append(f"  stage {i:<3}  {len(stage):,} faces, amplitude {amplitude:.5g} units")
    report += [f"  budget     {sum(len(s) for s in stages):,} / {mm.MAX_TOTAL_FACES:,} faces across stages",
               f"  size       {size[0]:.5g} x {size[1]:.5g} x {size[2]:.5g} units"]
    if any(v["mesh"].faces_total > len(v["mesh"].faces) for v in views):
        report.append("  preview    whole-triangle sample (up to 80,000 faces per view); exports keep every face")
    # Everything that can fail during geometry/preview preparation happens before publication.
    outputs = _publish(ctx, dst, primary, stages, p.separate) if dst is not None else []
    report.extend(f"  saved as   {path}" for path in outputs)
    # Do not raise cancellation after committing the primary: it is now a completed, traceable run.
    return Result(outputs=outputs,
                  stats=[Stat("stages", "Stages", len(stages)),
                         Stat("faces", "Faces", len(primary), before=len(stages[0])),
                         Stat("size", "Size", " × ".join(f"{v:.4g}" for v in size), fmt="{}")],
                  report="\n".join(report), preview={"views": views, "prefer": prefer},
                  status="Saved" if outputs else "Previewed",
                  data={"stage_faces": [len(s) for s in stages], "amplitudes": frag.amplitudes})


def make_sample(folder: Path) -> Path:
    """A light input keeps even the six-stage preset inexpensive in tests."""
    import trimesh
    path = folder / "disintegrate_sample.stl"
    trimesh.creation.icosphere(subdivisions=0, radius=40).export(path)
    return path
