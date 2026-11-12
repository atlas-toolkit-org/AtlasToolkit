"""Toolkit adapter for Holes: load, set up the model's frame, make it a solid, plan the holes, build the cutters, cut
once, publish a signed mesh."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d, influence
from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import mesh_holes as mh

FAMILY_NAMES = {"tube": "through tubes", "bore": "blind bores", "bubble": "bubbles", "worm": "wormholes"}
PROFILE_NAMES = {"circle": "circle", "polygon": "polygon", "pillow": "pillow", "blob": "blob"}


def num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def solid(V, F):
    """A Manifold of (V, F), faces turned outward; None when it is not a closed 2-manifold. A mesh whose shared
    vertices do not line up (pinched edges, duplicates) is rebuilt from its triangles by manifold3d's own merge."""
    import manifold3d
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    if mh.signed_volume(V, F) < 0:
        F = F[:, ::-1]

    def ok(m):
        return m.status() == manifold3d.Error.NoError and not m.is_empty() and m.volume() > 0
    m = mh.to_manifold(V, F)
    if ok(m):
        return m
    tri = V[F].reshape(-1, 3)
    soup = manifold3d.Mesh(vert_properties=np.ascontiguousarray(tri, dtype=np.float32),
                           tri_verts=np.arange(len(tri), dtype=np.uint32).reshape(-1, 3))
    soup.merge()
    m = manifold3d.Manifold(soup)
    return m if ok(m) else None


def closed_copy(V, F, resolution: int, progress=None):
    """An open model closed by resampling: signed distance (winding number) on a grid, marching cubes."""
    from atlas_toolkit.tools.mesh_porous import mesh_porous as mp
    lo, hi = V.min(0), V.max(0)
    grid = mp.make_grid(lo, hi, resolution)
    mp.check_size(grid)
    D = mp.signed_distance(V, F, grid, band=3 * grid.step, progress=progress)
    # extracted a hair outside 0: samples exactly on the surface (flat faces on the grid) make marching cubes
    # leave non-manifold edges
    Vc, Fc = mp.extract(D - 0.013 * grid.step, grid)
    if not len(Fc):
        raise ValueError("the model encloses no volume, so there is nothing to cut holes in")
    import trimesh
    m = trimesh.Trimesh(Vc, Fc)                  # merges the shared vertices
    return np.asarray(m.vertices, float), np.asarray(m.faces, np.int64)


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    import trimesh
    if ctx.dry_run:
        dst = None
    if dst is not None and dst.resolve() == Path(src).resolve():
        raise ValueError("the output must not replace the input model")
    t0 = time.perf_counter()
    ctx.progress(0.02, "loading model")
    mesh = gm.load_mesh(src)
    if len(mesh.faces) == 0:
        raise ValueError("the model has no faces")
    up, front, rot, source = frame3d.resolve_axes(src, p, mesh.extents)
    frame = frame3d.fit_frame(mesh.vertices, up, front, rot, mode="height", size=frame3d.MODEL_HEIGHT)
    V0 = frame.apply(mesh.vertices)
    F0 = np.asarray(mesh.faces, dtype=np.int64)
    faces_in = len(F0)

    # the model as a solid: simplified for the preview, closed by resampling when it is open
    V, F = (gm.simplify_faces(V0, F0, int(p.draft_faces), ctx.log) if ctx.draft else (V0, F0))
    model_m = solid(V, F)
    if model_m is None and len(F) != len(F0):
        V, F = V0, F0
        model_m = solid(V, F)
    repaired = False
    if model_m is None:
        res = min(int(p.repair_resolution), 160) if ctx.draft else int(p.repair_resolution)
        ctx.warn(f"the model is not a closed solid: closing it by resampling at {res} samples (fine detail "
                 f"softens; close it first for an exact cut)")
        V, F = closed_copy(V0, F0, res, lambda t, msg="": ctx.progress(0.03 + 0.12 * t, "closing: " + msg))
        if ctx.draft:
            V, F = gm.simplify_faces(V, F, int(p.draft_faces), ctx.log)
        model_m = solid(V, F)
        repaired = True
        if model_m is None:
            raise ValueError("the model could not be closed into a solid")
    vol0 = model_m.volume()

    values = dict(p)
    segments = int(p.draft_segments if ctx.draft else p.segments)
    s = mh.Settings.from_values(values, frame.height, segments)
    model = mh.Model(np.asarray(V, float), np.asarray(F, np.int64), frame.lo.copy(), frame.hi.copy())
    ctx.progress(0.16, "placing holes")
    pl = mh.plan(model, s, values, lambda t, msg="": ctx.progress(0.16 + 0.24 * t, msg or "placing holes"), ctx.log)
    holes = pl.holes
    if not holes:
        ctx.warn("no hole reaches the model: check Size, Spacing, Count, the Acts on region or the minimum wall")

    cutters = []
    n = len(holes)
    for i, h in enumerate(holes):
        if i % 25 == 0:
            ctx.progress(0.40 + 0.25 * i / max(n, 1), f"building cutters ({i} of {n})")
        cutters.append(mh.cutter(h, s))
    ctx.progress(0.66, f"cutting {n} holes")
    region = mh.region_solid(values, frame.lo, frame.hi)
    out_m, union = mh.carve(model_m, cutters, region, bool(values.get("invert_influence")))
    ctx.check_cancel()
    if out_m.is_empty():
        raise ValueError("nothing is left of the model: the holes took all of it (smaller sizes or a wider wall)")
    out_m, before, after = mh.keep_pieces(out_m, p.keep, float(p.min_piece))
    vol1 = out_m.volume()

    Vo, Fo = mh.from_manifold(out_m)
    budget = int(p.max_faces)
    if ctx.draft:
        budget = min(budget or gm.PREVIEW_FACES, gm.PREVIEW_FACES)
    if budget and len(Fo) > budget:
        ctx.progress(0.85, "simplifying")
        Vo, Fo = gm.simplify_faces(Vo, Fo, budget, ctx.log)
    result = trimesh.Trimesh(frame.inverse(Vo), Fo, process=False)

    written = []
    if dst is not None:
        ctx.progress(0.94, "saving")
        gm.save_mesh(result, dst, TOOL)
        written.append(str(dst))

    views = [{"title": "Output", "kind": "mesh", "mesh": gm.preview(result)}]
    if not union.is_empty():
        Vu, Fu = mh.from_manifold(union)
        views.append({"title": "Cutters", "kind": "mesh",
                      "mesh": gm.preview(trimesh.Trimesh(frame.inverse(Vu), Fu, process=False))})

    removed = max(0.0, 1.0 - vol1 / vol0) * 100.0 if vol0 > 0 else 0.0
    ext = result.extents
    pct = frame.height / 100.0
    sizes = np.array([2 * h.radius / pct for h in holes]) if holes else np.zeros(1)
    lines = [Path(src).name,
             f"  frame      up {up}, front {front}" + (f", turned {', '.join(f'{a:g}' for a in rot)}°" if any(rot)
                                                        else "") + f" ({source})",
             f"  input      {num(faces_in)} faces, {'closed' if not repaired else 'open (closed by resampling)'}"
             + (f" (cut from {num(len(F))} for the preview)" if ctx.draft and len(F) != faces_in else ""),
             f"  holes      {num(n)} {FAMILY_NAMES[s.family]}, {s.layout}, {PROFILE_NAMES[s.profile]} profile, "
             f"size {sizes.min():.2f}-{sizes.max():.2f}% h, irregularity {s.chaos:g}, seed {s.seed}",
             f"  placement  {num(pl.proposed)} proposed"
             + (f", {num(pl.dropped)} left out" if pl.dropped else "")
             + (f", {num(pl.spaced_out)} too close" if pl.spaced_out else "")
             + (f", {num(pl.shrunk)} shrunk to fit" if pl.shrunk else "")
             + (f" (wall {p.gap:g}% h)" if s.gap > 0 else " (holes may merge)"),
             f"  acts on    {influence.describe(values)}",
             f"  removed    {removed:.1f}% of the volume, {after} piece{'s' if after != 1 else ''}"
             + (f" (of {before})" if before != after else ""),
             f"  faces      {num(faces_in)} -> {num(len(result.faces))}",
             f"  size       {ext[0]:.4g} x {ext[1]:.4g} x {ext[2]:.4g} units",
             f"  time       {time.perf_counter() - t0:.1f} s"]
    if ctx.draft:
        lines.append(f"  preview    {segments} segments instead of {p.segments}, input under "
                     f"{num(int(p.draft_faces))} faces; Run uses the full settings")
    lines += [f"  saved as   {o}" for o in written]
    if after > 1 and p.keep == "all":
        ctx.log(f"{after} loose pieces kept (Loose pieces: drop small or largest only removes them)")
    ctx.progress(1.0)
    return Result(outputs=written,
                  stats=[Stat("holes", "Holes", n),
                         Stat("removed", "Removed", removed, "%", fmt="{:.1f}"),
                         Stat("pieces", "Pieces", after),
                         Stat("faces", "Faces", len(result.faces), before=faces_in),
                         Stat("size", "Size", " × ".join(f"{v:.4g}" for v in ext), fmt="{}")],
                  report="\n".join(lines), preview={"views": views, "prefer": "Output"},
                  status="Saved" if written else ("Draft" if ctx.draft else "Previewed"),
                  data={"frame": {"up": up, "front": front, "rotate": [float(a) for a in rot]}, "holes": n})
