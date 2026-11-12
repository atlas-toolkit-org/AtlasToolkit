"""Toolkit adapter for Surface Peel: load, set up the model's frame, split long edges, cut and frame the panels of
every layer once at rest, bend them for every stage, thicken them into shells, publish signed meshes."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d, influence, stages
from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import mesh_peel as mp

FACE_BUDGET = 1_500_000          # per layer, after edge splitting
OUTPUT_BUDGET = 6_000_000        # faces of one stage, all layers and shells together


def _refine(V, F, max_len, budget):
    from atlas_toolkit.tools.mesh_flow.mesh_flow import refine
    return refine(V, F, max_len, budget, passes=8)


def _blender_solidify(ctx, pieces, thickness):
    """Blender's Complex Solidify on the open pieces, all in one exchange."""
    from atlas_toolkit.geom import blender as gb
    import trimesh
    joined = trimesh.util.concatenate([trimesh.Trimesh(v, f, process=False) for v, f in pieces])
    folder = gb.scratch()
    key = hashlib.sha1(f"{os.getpid()}|{time.time_ns()}".encode()).hexdigest()[:16]
    src, out = folder / f"{key}-peel-in.npz", folder / f"{key}-peel-out.npz"
    try:
        np.savez(src, V=np.asarray(joined.vertices, np.float64), F=np.asarray(joined.faces, np.int32))
        client = gb.client(ctx.log)
        client.request({"op": "solidify", "mesh": str(src), "out": str(out), "thickness": float(thickness)},
                       on_progress=lambda f, m: None, poll=ctx.check_cancel)
        with np.load(out) as d:
            return np.asarray(d["V"], np.float64), np.asarray(d["F"], np.int64), client.version_string
    finally:
        for f in (src, out):
            try:
                f.unlink()
            except OSError:
                pass


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    if ctx.dry_run:
        dst = None
    if dst is not None and dst.resolve() == Path(src).resolve():
        raise ValueError("the output must not replace the input model")
    t0 = time.perf_counter()
    ctx.progress(0.02, "loading model")
    mesh = gm.load_mesh(src)
    up, front, rot, source = frame3d.resolve_axes(src, p, mesh.extents)
    frame = frame3d.fit_frame(mesh.vertices, up, front, rot, mode="height", size=frame3d.MODEL_HEIGHT)
    V0 = frame.apply(mesh.vertices)
    F0 = np.asarray(mesh.faces, dtype=np.int64)
    faces_in = len(F0)
    if len(F0) < 4:
        raise ValueError("the model has too few faces to peel")
    height = float(frame.hi[2] - frame.lo[2])
    V, F = V0, F0
    if ctx.draft:
        V, F = gm.simplify_faces(V, F, int(p.draft_faces), ctx.log)
    passes = 0
    if not ctx.draft:
        ctx.progress(0.05, "splitting long edges")
        target = float(p.max_edge) / 100.0 * height
        V, F, passes = _refine(V, F, target, FACE_BUDGET)
    n_layers = int(p.layers)
    per_layer = len(F) * (2 if p.thickness > 0 else 1) * 1.1
    if per_layer * n_layers + (len(F) if p.core else 0) > OUTPUT_BUDGET:
        raise ValueError(f"{n_layers} layer(s) of {len(F):,} faces would give over {OUTPUT_BUDGET:,} faces a stage: "
                         f"use fewer Layers, a larger Longest edge, or a lighter model".replace(",", " "))

    lo, hi = frame.lo, frame.hi
    centre = (lo + hi) / 2.0 + np.asarray(p.bloom_centre, float) / 100.0 * (hi - lo)
    axis = np.asarray(mp.AXES[p.bloom_axis], float)
    w_rest = influence.weights(V, p, lo, hi)
    values = dict(p)

    layers = []
    for i in range(n_layers):
        ctx.progress(0.1 + 0.3 * i / n_layers, "cutting panels" + (f" of layer {i + 1}" if n_layers > 1 else ""))
        k = float(p.layer_scale) ** i
        Vi = centre + (V - centre) * k
        layer = mp.build_layer(Vi, F, values, centre, axis, w_rest, i, int(p.seed),
                               progress=lambda m: ctx.check_cancel())
        layer.scale = k
        layers.append(layer)
        ctx.check_cancel()
    core = None
    if p.core:
        k = float(p.layer_scale) ** n_layers
        core = (centre + (V - centre) * k, F)

    us = stages.amounts(int(p.stages), float(p.amount))
    n = len(us)
    thick = float(p.thickness) / 100.0 * height
    manifest = {"tool": TOOL.id, "tool_version": TOOL.version, "source": Path(src).name,
                "frame": {"up": up, "front": front, "rotate": [float(a) for a in rot]}}
    height_model = height / frame.scale
    import trimesh
    rows, blender_version = [], None
    out = stages.StageOutput(ctx, TOOL, dst, n, p.layout, p.axis, float(p.gap) / 100.0 * height_model,
                             p.save_stages, manifest)
    with out:
        for si, u in enumerate(us):
            ctx.progress(0.42 + 0.45 * si / n, f"stage {si + 1} of {n}: bending")
            if u <= 1e-9:                               # stage 0: the untouched model
                out.add(si, trimesh.Trimesh(mesh.vertices.copy(), F0, process=False),
                        {"amount": 0.0, "label": f"stage {si}"})
                rows.append((si, u, 0, sum(len(L.panels) for L in layers), 1.0))
                continue
            pieces, opened, st = [], [], []
            for li, layer in enumerate(layers):
                moved, still, a = mp.pose_layer(layer, values, u, height, float(p.layer_falloff) ** li)
                opened += a
                for (X, Fk), pn in zip(moved, [q for q, x in zip(layer.panels, a) if x > 0]):
                    pieces.append((X, Fk))
                    st.append(mp.stretch(pn.P, X, Fk))
                if len(still):
                    pieces.append(mp.compact(layer.V, still))
                ctx.check_cancel()
            closed = [pc for pc in pieces if not len(mp.boundary_edges(pc[1]))]
            open_ = [pc for pc in pieces if len(mp.boundary_edges(pc[1]))]
            if core is not None:
                closed.append(core)
            if thick > 0 and open_ and p.solidify == "blender":
                ctx.progress(0.42 + 0.45 * (si + 0.6) / n, f"stage {si + 1} of {n}: Blender solidify")
                Vb, Fb, blender_version = _blender_solidify(ctx, open_, thick)
                shells = [(Vb, Fb)]
            else:
                shells = [mp.solidify(v, f, thick) for v, f in open_]
            parts = [trimesh.Trimesh(frame.inverse(v), f, process=False) for v, f in shells + closed if len(f)]
            stage_mesh = trimesh.util.concatenate(parts)
            out.add(si, stage_mesh, {"amount": round(u, 6), "label": f"stage {si}"})
            rows.append((si, u, int(sum(x > 0 for x in opened)), len(opened), max(st) if st else 1.0))
            ctx.check_cancel()
        outputs = out.finish()
    primary = out.primary
    last = rows[-1]
    if last[4] > 3.0:
        ctx.warn(f"some panels are stretched to {last[4]:.1f}× (tight curls on wide panels): lower Curl or Unroll, "
                 f"or use more panels")

    n_panels = sum(len(L.panels) for L in layers)
    pattern = (f"{p.petals} petals × {p.rows} row(s)" + (", staggered" if p.stagger_rows and p.rows > 1 else "")
               + f", tips {p.scallop:g}, wobble {p.wobble:g}" if p.pattern == "petals"
               else f"{p.pieces} scales (edge sensitivity {p.edge_sensitivity:g})")
    lines = [Path(src).name,
             f"  frame      up {up}, front {front}" + (f", turned {', '.join(f'{a:g}' for a in rot)}°" if any(rot)
                                                        else "") + f" ({source})",
             f"  faces      {faces_in:,} -> {len(F):,} per layer".replace(",", " ")
             + (f" ({passes} edge-splitting pass{'es' if passes != 1 else ''} to {p.max_edge:g}% h)" if passes else "")
             + ("  (draft: simplified, no edge splitting)" if ctx.draft else ""),
             f"  panels     {n_panels} in {n_layers} layer(s): {pattern}, seed {p.seed}",
             f"  bloom      {p.root} about {dict(TOOL.param('bloom_axis').choices)[p.bloom_axis].lower()}, curl "
             f"{p.curl:g}°, open {p.open_angle:g}°, unroll {p.unroll:g}, tighten {p.tighten:g}, root "
             f"{p.root_share:g}%, variation {p.variation:g}" + (f", lift {p.lift:g}% h" if p.lift else ""),
             f"  order      {p.order}" + (f", stagger {p.stagger:g}" if p.order != "together" else ""),
             f"  acts on    {influence.describe(p)}",
             f"  shell      " + (f"{p.thickness:g}% h ({'Blender ' + blender_version if blender_version else 'built in'})"
                                 if thick > 0 else "open surfaces")
             + (f"; core at {float(p.layer_scale) ** n_layers:.2f}×" if core is not None else "")]
    for si, u, opened_n, total, s99 in rows:
        lines.append(f"  stage {si}    amount {u:.3f}: " + ("intact" if u <= 1e-9 else
                     f"{opened_n} of {total} panels open, edges stretched up to {s99:.2f}× (99%)"))
    ext = primary.extents
    lines += [f"  size       {ext[0]:.4g} x {ext[1]:.4g} x {ext[2]:.4g} units",
              f"  time       {time.perf_counter() - t0:.1f} s"]
    lines += [f"  saved as   {o}" for o in outputs[:1]]
    if len(outputs) > 1:
        lines.append(f"  stages     {len(outputs) - 1} files in {Path(outputs[1]).parent}")
    ctx.progress(1.0)
    return Result(outputs=outputs,
                  stats=[Stat("panels", "Panels", n_panels),
                         Stat("stages", "Stages", n),
                         Stat("faces", "Faces", len(primary.faces), before=faces_in),
                         Stat("open", "Open", last[2], fmt="{}"),
                         Stat("size", "Size", " × ".join(f"{v:.4g}" for v in ext), fmt="{}")],
                  report="\n".join(lines), preview={"views": out.views, "prefer": out.prefer},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"amounts": us, "frame": manifest["frame"], "panels": n_panels,
                        "seconds": time.perf_counter() - t0})
