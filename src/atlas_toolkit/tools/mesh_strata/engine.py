"""Toolkit adapter for Strata Sculpt: load, set up the model's frame, layer it, sculpt every stage, publish signed
meshes."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d, influence, stages
from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import mesh_strata as ms
from . import connectors

DIRECTION = {"up": "horizontal", "front": "front to back", "side": "side to side"}


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    if ctx.dry_run:
        dst = None
    if dst is not None and dst.resolve() == Path(src).resolve():
        raise ValueError("the output must not replace the input model")
    ctx.progress(0.02, "loading model")
    mesh = gm.load_mesh(src)
    connectors.validate(p)
    if p.connectors and not mesh.is_volume:
        raise ValueError("Connectors require an outward-oriented closed solid. Repair the input first.")
    if p.connectors and len(mesh.faces) > p.max_faces:
        raise ValueError("Input exceeds Maximum faces. Simplify the model before adding connectors.")
    if not mesh.is_watertight:
        ctx.warn("the model is not watertight: cuts through its open parts cannot be capped, so those slabs stay "
                 "hollow shells (fine for Plotter Vision, not for a print)")
    up, front, rot, source = frame3d.resolve_axes(src, p, mesh.extents)
    frame = frame3d.fit_frame(mesh.vertices, up, front, rot, mode="height", size=frame3d.MODEL_HEIGHT)
    V = frame.apply(mesh.vertices)
    F = np.asarray(mesh.faces, dtype=np.int64)
    faces_in = len(F)
    if ctx.draft and not p.connectors:
        V, F = gm.simplify_faces(V, F, int(p.draft_faces), ctx.log)
    import trimesh
    canon = trimesh.Trimesh(V, F, process=False)
    st = ms.stack(V, p.layers, p.direction, p.tilt, p.tilt_toward)
    dice = ms.draws(st.count, p.seed)
    connector_plan = connectors.prepare(canon, st, p, ctx) if p.connectors and p.amount > 0 else None
    us = stages.amounts(int(p.stages), float(p.amount))
    n = len(us)
    manifest = {"tool": TOOL.id, "tool_version": TOOL.version, "source": Path(src).name,
                "frame": {"up": up, "front": front, "rotate": [float(a) for a in rot]}}
    height_model = (frame.hi[2] - frame.lo[2]) / frame.scale
    rows = []
    out = stages.StageOutput(ctx, TOOL, dst, n, p.layout, p.axis, float(p.gap) / 100.0 * height_model,
                             p.save_stages, manifest)
    if p.connectors and out.pub is not None:
        def save_validated(item, path):
            if len(item.faces) > p.max_faces:
                raise ValueError("Output exceeds Maximum faces. Reduce stages, supports or branches.")
            gm.save_mesh(item, path, TOOL)
            saved = gm.load_mesh(path)
            expected = len(item.split(only_watertight=False))
            if (not saved.is_volume or not saved.nondegenerate_faces().all()
                    or len(saved.split(only_watertight=False)) != expected):
                raise ValueError("Saved stratum joints lost valid solid topology. Try OBJ / PLY or reduce branches.")
        out.pub.save = save_validated
    with out:
        for i, u in enumerate(us):
            a, b = 0.06 + 0.8 * i / n, 0.06 + 0.8 * (i + 1) / n

            def progress(done, total, a=a, b=b, i=i):
                ctx.check_cancel()
                share = .4 if p.connectors else 1.
                ctx.progress(a + (b - a) * share * done / max(total, 1), f"stage {i + 1} of {n}: cut {done} of {total}")

            def connection_progress(done, total, a=a, b=b, i=i):
                ctx.progress(a + (b - a) * (.4 + .6 * done / max(total, 1)),
                             f"stage {i + 1} of {n}: connected {done} of {total} facing pairs")

            ctx.progress(a, f"stage {i + 1} of {n}")
            uk = ms.layer_amounts(st, u, p, frame.lo, frame.hi)
            if u == 0.0 or not np.any(uk > 0):
                staged = mesh.copy()                     # the untouched model, exactly
                info = {"layers": 0, "pieces": 1, "cuts": 0, "moved": 0.0}
            else:
                sculpted, info = ms.sculpt(canon, st, uk, p, dice, progress, ctx=ctx, connector_plan=connector_plan,
                                           connection_progress=connection_progress)
                staged = trimesh.Trimesh(frame.inverse(sculpted.vertices), sculpted.faces, process=False)
                if p.connectors:
                    # Test model-unit coordinates too: formats may round differently after inverse framing.
                    solid = connectors.co.from_arrays(staged.vertices, staged.faces)
                    solid, adjustment = connectors.co.stabilize(solid, height_model)
                    staged = connectors.co.to_mesh(solid)
                    info["precision_model"] = adjustment
            rows.append((i, u, info))
            out.add(i, staged, {"amount": round(u, 6), "label": f"stage {i}"})
            ctx.check_cancel()
        if p.connectors and out.progression and sum(len(m.faces) for m in out.kept) > p.max_faces:
            raise ValueError("Progression exceeds Maximum faces. Reduce stages, supports or branches.")
        outputs = out.finish()
    primary = out.primary

    ext = primary.extents
    last = rows[-1][2]
    acting = int(np.count_nonzero(ms.layer_amounts(st, 1.0, p, frame.lo, frame.hi) > 0))
    lines = [Path(src).name,
             f"  frame      up {up}, front {front}" + (f", turned {', '.join(f'{a:g}' for a in rot)}°" if any(rot)
                                                        else "") + f" ({source})",
             f"  faces      {faces_in:,} in".replace(",", " ")
             + ("  (draft: simplified)" if ctx.draft and not p.connectors else ""),
             f"  stack      {st.count} layers {DIRECTION[p.direction]}"
             + (f", tilted {p.tilt:g}° toward {p.tilt_toward:g}°" if p.tilt else "")
             + f", pitch {st.pitch / frame.scale:.4g} units, {p.retained:g}% kept at full amount",
             f"  motion     separation {p.separation:g}% of a layer from the {p.anchor}, turn {p.turn:g}°, wave "
             f"{p.wave:g}% h ({p.wave_shape}, {p.waves:g} waves)",
             f"  dislocate  slip {p.jitter_shift:g}% h, spin {p.jitter_spin:g}°, tilt {p.jitter_tilt:g}° on "
             f"{p.share:g}% of the layers, seed {p.seed}",
             f"  acts on    {influence.describe(p)} ({acting} of {st.count} layers move)"]
    for i, u, info in rows:
        if info["layers"]:
            closed = info.get("closed", 0)
            lines.append(f"  stage {i}    amount {u:.3f}: {info['layers']} slabs, {info['cuts']} capped cuts, "
                         f"{closed} of {info['pieces']} pieces closed, moves up to "
                         f"{info['moved'] / 2.0 * 100.0:.1f}% h")
        else:
            lines.append(f"  stage {i}    amount {u:.3f}: the untouched model")
    lines += [f"  size       {ext[0]:.4g} x {ext[1]:.4g} x {ext[2]:.4g} units"]
    lines += [f"  saved as   {o}" for o in outputs[:1]]
    if len(outputs) > 1:
        lines.append(f"  stages     {len(outputs) - 1} files in {Path(outputs[1]).parent}")
    if last["layers"] and mesh.is_watertight and last.get("closed", 0) < last["pieces"]:
        ctx.warn(f"{last['pieces'] - last['closed']} of {last['pieces']} slab pieces are not closed: a cut met a "
                 "thin or self-touching part of the model")
    if p.connectors:
        lines.append(f"  connectors {p.connector_style}; {p.connector_count} requested per facing pair; connection seed {p.connector_seed}")
        for i, u, info in rows:
            records = info.get("connectors", [])
            requested = sum(row["requested_branches"] for row in records)
            achieved = sum(row["branches"] for row in records)
            pairs = info.get("pairs", [])
            fitted = sum(row.get("supports", 0) < row.get("requested", 0) for row in pairs)
            lines.append(f"  stage {i}    {len(records)} fused supports; {achieved}/{requested} extra branches; "
                         f"{fitted} pairs fitted below requested count; {info.get('components', 1)} solid components")
            for row in records:
                lines.append(f"    layers {row['lower']+1}–{row['upper']+1}: thickness {row['diameter']/frame.scale:.5g} units; "
                             f"forks {row['branches']}/{row['requested_branches']}; levels {row['branch_levels']}; "
                             f"extent {row['lateral_max']/frame.scale:.5g}/{row['extent']/frame.scale:.5g} units")
            precision = max(info.get("precision", 0.) / frame.scale, info.get("precision_model", 0.))
            if precision:
                lines.append(f"    joint precision stabilization {precision:.5g} model units")

    def model_records(info):
        converted = []
        for record in info.get("connectors", []):
            row = dict(record)
            for key in ("root_inside", "tip_inside"):
                row[key] = frame.inverse(np.asarray([row[key]]))[0].tolist()
            row["paths"] = [frame.inverse(np.asarray(path)).tolist() for path in row["paths"]]
            for key in ("diameter", "requested_diameter", "extent", "lateral_max", "precision"):
                row[key] /= frame.scale
            converted.append(row)
        return converted
    ctx.progress(1.0)
    return Result(outputs=outputs,
                  stats=[Stat("layers", "Layers", last["layers"] or st.count),
                         Stat("stages", "Stages", n),
                         Stat("faces", "Faces", len(primary.faces), before=faces_in),
                         Stat("moved", "Moves up to", last["moved"] / 2.0 * 100.0, "% h", fmt="{:.1f}"),
                         Stat("size", "Size", " × ".join(f"{v:.4g}" for v in ext), fmt="{}")]
                         + ([Stat("supports", "Supports", len(last.get("connectors", []))),
                             Stat("branches", "Extra branches", sum(r["branches"] for r in last.get("connectors", [])))]
                            if p.connectors else []),
                  report="\n".join(lines), preview={"views": out.views, "prefer": out.prefer},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"amounts": us, "frame": manifest["frame"],
                        "connectors": model_records(last),
                        "connector_stages": [{"amount": u, "connectors": model_records(info),
                                              "pairs": info.get("pairs", [])} for _, u, info in rows]})
