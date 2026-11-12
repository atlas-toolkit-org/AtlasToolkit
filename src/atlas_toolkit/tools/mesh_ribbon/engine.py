"""Toolkit adapter for Ribbon Wrap: load, set up the model's frame, lay the ribbons, tension and layers, the optional
Blender cloth settle, fabric and solids, join with the model, publish a signed mesh."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Stat
from atlas_toolkit.geom import frame3d
from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import mesh_ribbon as mr

SOLVE_FACES = 200_000          # paths, tension and tails use a lighter copy of bigger models
CLOTH_BODY_FACES = 40_000


def _blender_cloth(ctx, pl, body_V, body_F, v):
    """Blender's cloth on the coarse strips, the model as a collider (and the floor, when tails rest on it)."""
    from atlas_toolkit.geom import blender as gb
    V, F, W = mr.cloth_mesh(pl, float(v["cloth_hold"]))
    folder = gb.scratch()
    key = hashlib.sha1(f"{os.getpid()}|{time.time_ns()}".encode()).hexdigest()[:16]
    body, rib, out = (folder / f"{key}-ribbon-{n}.npz" for n in ("body", "cloth", "out"))
    try:
        np.savez(body, V=np.asarray(body_V, np.float64), F=np.asarray(body_F, np.int32))
        np.savez(rib, V=V, F=F.astype(np.int32), W=W)
        client = gb.client(ctx.log)
        q = mr.cloth_params(pl, v, ctx.draft)
        reply = client.request(dict(op="cloth", body=str(body), ribbon=str(rib), out=str(out), **q),
                               on_progress=lambda f, m: ctx.progress(0.45 + 0.3 * f, m), poll=ctx.check_cancel)
        with np.load(out) as d:
            Vo = np.asarray(d["V"], np.float64)
        if len(Vo) != len(V):
            raise RuntimeError(f"Blender's cloth returned {len(Vo)} points for {len(V)}")
        mr.cloth_unpack(pl, Vo)
        return client.version_string, q, reply
    finally:
        for f in (body, rib, out):
            try:
                f.unlink()
            except OSError:
                pass


def run(ctx, src: Path, dst: Path | None, p):
    if ctx.dry_run:
        dst = None
    if dst is not None and dst.resolve() == Path(src).resolve():
        raise ValueError("the output must not replace the input model")
    t0 = time.perf_counter()
    v = dict(p)
    ctx.progress(0.02, "loading model")
    mesh = gm.load_mesh(src)
    up, front, rot, source = frame3d.resolve_axes(src, p, mesh.extents)
    frame = frame3d.fit_frame(mesh.vertices, up, front, rot, mode="height", size=frame3d.MODEL_HEIGHT)
    V = frame.apply(mesh.vertices)
    F = np.asarray(mesh.faces, dtype=np.int64)
    if len(F) < 4:
        raise ValueError("the model has too few faces to wrap")
    faces_in = len(F)
    Vs, Fs = (gm.simplify_faces(V, F, int(p.draft_faces), ctx.log) if ctx.draft
              else gm.simplify_faces(V, F, SOLVE_FACES, None) if len(F) > SOLVE_FACES else (V, F))
    ctx.progress(0.05, "reading the skin")
    surf = mr.Surface(Vs, Fs)
    surf_full = surf if len(Fs) == len(F) else mr.Surface(V, F)
    floor_z = float(frame.lo[2])
    pl = mr.plan(surf, v, ctx.draft, lambda f, m: ctx.progress(0.06 + 0.12 * f, m), floor_z)
    ctx.check_cancel()
    top = mr.tension_and_layers(surf, pl, v, ctx.draft, lambda f, m: ctx.progress(0.18 + 0.25 * f, m))
    ctx.check_cancel()
    blender = None
    if p.cloth:
        ctx.progress(0.44, "starting Blender")
        bV, bF = gm.simplify_faces(Vs, Fs, CLOTH_BODY_FACES, None) if len(Fs) > CLOTH_BODY_FACES else (Vs, Fs)
        blender, cq, _ = _blender_cloth(ctx, pl, bV, bF, v)
    ctx.progress(0.76, "weaving the fabric")
    pieces = mr.finish(surf_full, pl, v, ctx.draft, lambda f, m: ctx.progress(0.76 + 0.14 * f, m))
    if not pieces:
        raise ValueError("no ribbon was left to build")
    ctx.progress(0.91, "measuring the cover")
    covered = mr.coverage(surf_full, pieces, np.random.default_rng(1), 2500 if ctx.draft else 8000)
    Vr, Fr = mr.join(pieces)
    import trimesh
    ribbon = trimesh.Trimesh(frame.inverse(Vr), Fr, process=False)
    out = ribbon if p.ribbon_only else trimesh.util.concatenate(
        [trimesh.Trimesh(mesh.vertices.copy(), F, process=False), ribbon])

    style = dict(TOOL.param("style").choices)[p.style]
    if p.style == "helix":
        how = (f"{p.overlap:g}% overlap, {p.passes} pass(es), {p.span_from:g}-{p.span_to:g}% along "
               f"{dict(TOOL.param('axis').choices)[p.axis].lower()}")
    elif p.style == "bands":
        how = f"{len(pl.ribbons)} {p.band_layout} band(s)"
    else:
        how = f"{len(pl.ribbons)} drape(s) of {p.length:g} heights, curviness {p.curviness:g}, around {p.around:g}"
    if p.tilt or p.lean:
        how += f", axis tilted {p.tilt:g}° / leaned {p.lean:g}°"
    lengths = ", ".join(f"{100 * r.meta['wrapped_length'] / mr.H:.0f}" for r in pl.ribbons[:8])
    report = [Path(src).name,
              f"  frame      up {up}, front {front}" + (f", turned {', '.join(f'{a:g}' for a in rot)}°" if any(rot)
                                                        else "") + f" ({source})",
              f"  wrap       {style}: {how}; width {p.width:g}% h, tension {p.tension:g}",
              f"  ribbons    {len(pl.ribbons)}, wrapped lengths {lengths}% h; tails {p.tail_start:g} / "
              f"{p.tail_end:g}% h, tips {p.tip}",
              f"  fabric     thickness {p.thickness:g}% h, edges {p.softness:g}, threads {p.threads}, weave "
              f"{p.weave:g}, ripple {p.ripple:g}",
              f"  layers     {'; '.join(pl.notes)}",
              f"  covered    {100 * covered:.0f}% of the skin"]
    if blender:
        report.append(f"  cloth      Blender {blender}: {cq['frames']} frames, hold {p.cloth_hold:g}, bending "
                      f"{cq['bending']:.3g}")
    if ctx.draft:
        report.append(f"  draft      {len(Fs):,} faces solved, coarser ribbon".replace(",", " "))
    report.append(f"  time       {time.perf_counter() - t0:.1f} s")
    stats = [Stat("ribbons", "Ribbons", len(pl.ribbons)),
             Stat("covered", "Covered", round(100 * covered), "%", fmt="{:.0f}"),
             Stat("layers", "Layers", top),
             Stat("ribbon_faces", "Ribbon faces", len(Fr))]
    res = gm.result(ctx, TOOL, mesh, out, dst, stats, report)
    res.data = {"covered": covered, "ribbons": len(pl.ribbons), "layers": top, "frame": frame.frame_values(),
                "seconds": time.perf_counter() - t0}
    if ctx.draft and dst is None:
        res.status = "Draft"
    return res
