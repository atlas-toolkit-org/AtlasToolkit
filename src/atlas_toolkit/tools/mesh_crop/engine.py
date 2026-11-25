"""Atlas adapter for Shape Crop: load, pick the closed or open way, publish signed meshes, preview."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat, ToolError
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom import region as R
from atlas_toolkit.geom import repair
from atlas_toolkit.geom.mesh_export import publish_meshes

from . import TOOL
from .mesh_crop import boolean, clip, median_edge

WORDS = {"intersect": "kept inside", "subtract": "cut away", "union": "added", "xor": "both remainders"}


def _trimesh(V, F):
    import trimesh
    return trimesh.Trimesh(V, F, process=False)


def _area(V, F) -> float:
    if not len(F):
        return 0.0
    return float(np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1).sum() / 2)


def crop(ctx, mesh, p, log=None):
    """The cropped pieces of a loaded mesh: ([(label, V, F)], info). Shared by the engine and the checks."""
    V, F = np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces, dtype=np.int64)
    bounds = np.asarray(mesh.bounds, dtype=float)
    m = R.frame(bounds, p)
    op, kind = p["operation"], p["shape"]
    info = {"frame": m, "path": "closed", "times": {}, "notes": [], "segments": None}
    times = info["times"]
    sb = R.world_bounds(kind, m)
    touches = bool(np.all(sb[1] >= bounds[0]) and np.all(sb[0] <= bounds[1]))
    if op == "intersect" and not touches:
        raise ToolError("The shape does not reach the model, so nothing is inside it.",
                        hint="Move the shape onto the model (Centre puts it in the middle) or make it larger.")
    closed = op in ("union", "xor") or p.get("cut", "closed") == "closed"
    body = None
    if closed:
        t = time.perf_counter()
        ctx.progress(0.25, "checking the model is a closed solid")
        body = repair.solid_body(V, F)
        opening = repair.beyond_tidy(V, F) if body is None else None
        if opening is not None:                 # a real opening: no tidy can close it, so do not spend time on one
            if op in ("union", "xor"):
                raise repair.NeedsRepair(opening, "solid", None, TOOL.name)
        elif body is None:
            ctx.progress(0.3, "tidying the input")
            tidied = repair.tidy_input(ctx, mesh)
            if tidied is not mesh:
                V2, F2 = np.asarray(tidied.vertices, np.float64), np.asarray(tidied.faces, np.int64)
                body = repair.solid_body(V2, F2)
                if body is not None:
                    V, F = V2, F2
        times["solid check"] = time.perf_counter() - t
        if body is None:
            if op in ("union", "xor"):
                raise repair.needs_repair(mesh, TOOL)
            closed = False
            info["notes"].append("The model is not a closed solid, so the cut is left open (only its own surface "
                                 "is kept). To close it, run Repair Mesh on the model first.")
    ctx.check_cancel()
    t = time.perf_counter()
    if closed:
        segments = int(p.get("segments") or 0) or R.auto_segments(kind, m, median_edge(V, F))
        info["segments"] = segments if kind in R.CURVED else None
        ctx.progress(0.5, f"{WORDS[op]}: solid boolean")
        pieces, shape_volume = boolean(body, kind, m, op, segments)
        info["model_volume"] = float(body.volume())
        info["shape_volume"] = shape_volume
        info["kept"] = sum(v for *_, v in pieces) / info["model_volume"] if info["model_volume"] else None
        pieces = [(label, v, f) for label, v, f, _ in pieces]
    else:
        info["path"] = "open"
        ctx.progress(0.5, f"{WORDS[op]}: clipping the surface")
        v, f, notes = clip(V, F, kind, m, "inside" if op == "intersect" else "outside")
        info["notes"] += notes
        pieces = [("model", v, f)] if len(f) else []
        total = _area(V, F)
        info["kept"] = _area(v, f) / total if total else None
    times["crop"] = time.perf_counter() - t
    if not pieces:
        if op == "intersect":
            if info["path"] == "open":
                raise ToolError("None of the model's surface is inside the shape.",
                                hint="An open cut keeps only the model's own surface, and this shape sits within "
                                     "the model without reaching it. Make the shape larger or move it onto the "
                                     "surface, or choose Close the cut (closed models).")
            raise ToolError("Nothing of the model is inside the shape.",
                            hint="Move the shape onto the model or make it larger.")
        raise ToolError("The shape covers the whole model, so nothing is left.",
                        hint="Make the shape smaller, or choose Keep inside.")
    if op == "subtract" and not touches:
        info["notes"].append("The shape does not reach the model, which is unchanged.")
    return pieces, info


def run(ctx, src: Path, dst: Path | None, p):
    started = time.perf_counter()
    ctx.progress(0.02, "loading model")
    t = time.perf_counter()
    source = gm.load_mesh(src)
    load_time = time.perf_counter() - t
    pieces, info = crop(ctx, source, p)
    info["times"] = {"load": load_time, **info["times"]}
    meshes = [_trimesh(v, f) for _, v, f in pieces]
    t = time.perf_counter()
    if len(meshes) > 1 and p.export_mode == "combined":
        import trimesh
        save = [trimesh.util.concatenate(meshes)]
    else:
        save = meshes
    outputs = publish_meshes(save, dst, ctx, TOOL, extra_labels=["shape"] * (len(save) - 1)) if dst else []
    if dst:
        info["times"]["save"] = time.perf_counter() - t
    ctx.progress(0.92, "preparing preview")
    t = time.perf_counter()
    combined = save[0] if len(save) == 1 else None
    views = [{"title": "Output", "kind": "mesh", "mesh": gm.preview(combined if combined is not None else
                                                                     _trimesh(*_join(pieces)))}]
    if len(pieces) > 1:
        names = {"model": "Model part", "shape": "Shape part"}
        views += [{"title": names[label], "kind": "mesh", "mesh": gm.preview(mesh)}
                  for (label, _, _), mesh in zip(pieces, meshes)]
    info["times"]["preview"] = time.perf_counter() - t
    faces = sum(len(f) for _, _, f in pieces)
    m = info["frame"]
    size = np.linalg.norm(m[:3, :3], axis=0)
    kept = info.get("kept")
    lines = [src.name,
             f"  operation  {dict(TOOL.param('operation').pairs())[p.operation]}, {p.shape}",
             "  centre     " + " / ".join(f"{x:.6g}" for x in m[:3, 3]),
             "  size       " + " x ".join(f"{x:.6g}" for x in size) + " units",
             "  rotation   " + " / ".join(f"{x:.4g}" for x in p.rotation) + " °",
             f"  cut        {'closed (solid boolean)' if info['path'] == 'closed' else 'open (surface clip)'}"
             + (f", {info['segments']} segments" if info.get("segments") else ""),
             f"  faces      {len(source.faces):,} -> {faces:,}".replace(",", " ")]
    if kept is not None:
        lines.append(f"  kept       {kept * 100:.1f}% of the model's {'volume' if info['path'] == 'closed' else 'area'}")
    for (label, _, f), mesh in zip(pieces, meshes):
        if len(pieces) > 1:
            lines.append(f"  {label:10} {len(f):,} faces".replace(",", " "))
    lines.append("  time       " + ", ".join(f"{k} {v:.2f} s" for k, v in info["times"].items())
                 + f"; total {time.perf_counter() - started:.2f} s")
    lines += ["  " + n for n in info["notes"]]
    lines += ["  saved      " + path for path in outputs]
    if p.operation == "xor" and p.export_mode == "combined":
        lines.append("  The two remainders touch where the model met the shape; use separate files to keep them apart.")
    ctx.progress(1.0)
    stats = [Stat("faces", "Faces", faces, before=len(source.faces)),
             Stat("closed", "Cut", "closed" if info["path"] == "closed" else "open", fmt="{}")]
    if kept is not None:
        stats.append(Stat("kept", "Kept", round(kept * 100, 1), unit="%"))
    info["times"]["total"] = time.perf_counter() - started
    return Result(outputs=outputs, stats=stats, report="\n".join(lines), notes=list(info["notes"]),
                  data={"times": dict(info["times"]), "path": info["path"], "faces": faces},
                  preview={"views": views, "prefer": "Output"}, status="Saved" if outputs else "Previewed")


def _join(pieces):
    vs, fs, n = [], [], 0
    for _, v, f in pieces:
        vs.append(v)
        fs.append(f + n)
        n += len(v)
    return np.vstack(vs), np.vstack(fs)


def make_sample(folder):
    import trimesh
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=10)
    mesh.apply_scale((1.0, 0.8, 1.3))
    path = Path(folder) / "crop-sample.stl"
    mesh.export(path)
    return path
