"""Toolkit adapter for Dual Fusion: load and normalise both models, sample each one's field once, build every stage's
scene, extract, clean up, publish signed meshes in the first model's units."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d, stages
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_fusion import mesh_fusion as mf
from atlas_toolkit.tools.mesh_porous import mesh_porous as mp

from . import TOOL
from . import mesh_dual as md

NAMES = {"convergence": "convergence", "emergence": "emergence", "split": "split with seam",
         "strata": "interleaved strata", "bridge": "liquid bridge", "disintegrate": "disintegration",
         "echo": "motion echo", "patchwork": "cellular patchwork", "chrysalis": "chrysalis shell"}
SWAPPABLE = ("emergence", "split", "strata", "disintegrate", "patchwork", "chrysalis")
ARR_NAMES = {"side": "side by side", "face": "face to face", "back": "back to back", "stacked": "stacked",
             "overlaid": "overlaid"}


def num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def second_model(ctx, p, A_local, FA, ratio: float):
    """(local vertices, faces, name, closed) of model B: the chosen file upright and normalised, or A mirrored."""
    path = Path(p.second) if p.second else None
    if path is None:
        ctx.warn("no second model chosen: the first one, mirrored, is used (choose one under Second model)")
        V, F, name, closed = A_local * np.array([-1.0, 1.0, 1.0]), FA[:, ::-1], "the first model, mirrored", True
    else:
        if not path.exists():
            raise ValueError(f"the second model {path} does not exist")
        m = gm.load_mesh(path)
        if len(m.faces) == 0:
            raise ValueError(f"the second model {path.name} has no faces")
        up, front, _, _ = frame3d.resolve_axes(path, {"up_axis": p.second_up, "front": p.second_front}, m.extents)
        V = np.asarray(m.vertices, dtype=np.float64) @ frame3d.rotation(up, front, (0.0, 0.0, 0.0)).T
        F, name, closed = np.asarray(m.faces, dtype=np.int64), path.name, bool(m.is_watertight)
    if p.mirror_b:
        V, F = V * np.array([-1.0, 1.0, 1.0]), F[:, ::-1]
    return md.normalise(V, 2.0 * ratio), np.ascontiguousarray(F), name, closed


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    if ctx.dry_run:
        dst = None
    if dst is not None and dst.resolve() == Path(src).resolve():
        raise ValueError("the output must not replace the input model")
    t0 = time.perf_counter()
    ctx.progress(0.02, "loading models")
    mesh = gm.load_mesh(src)
    if len(mesh.faces) == 0:
        raise ValueError("the model has no faces")
    up, front, rot, source = frame3d.resolve_axes(src, p, mesh.extents)
    frame = frame3d.fit_frame(mesh.vertices, up, front, rot, mode="height", size=frame3d.MODEL_HEIGHT)
    VA = frame.apply(mesh.vertices)
    FA = np.asarray(mesh.faces, dtype=np.int64)
    if not mesh.is_watertight:
        ctx.warn("the first model is not closed: its inside comes from its winding number; close it first for a "
                 "reliable result")
    ratio = float(p.ratio)
    VB, FB, b_name, b_closed = second_model(ctx, p, VA, FA, ratio)
    if not b_closed:
        ctx.warn(f"the second model ({b_name}) is not closed: its inside comes from its winding number")

    resolution = int(p.draft_resolution if ctx.draft else p.resolution)
    band = md.field_band(p)
    models = []
    for i, (V, F, name) in enumerate(((VA, FA, Path(src).name), (VB, FB, b_name))):
        Vs, Fs = gm.simplify_faces(V, F, int(p.draft_faces), ctx.log) if ctx.draft else (V, F)
        a, b = 0.05 + 0.25 * i, 0.05 + 0.25 * (i + 1)
        field = md.ModelField.of_mesh(Vs, Fs, resolution, band,
                                      lambda t, msg="", a=a, b=b, name=name: ctx.progress(a + (b - a) * t,
                                                                                         f"{name}: {msg}"))
        if not (field.d < 0).any():
            raise ValueError(f"{name} encloses no volume at this resolution: it must be a closed solid")
        models.append(md.Model(np.asarray(V, float), np.asarray(F, np.int64), field, name))
    A, B = models
    if p.swap and p.fusion in SWAPPABLE:
        A, B = B, A
    step = max(2.0, 2.0 * ratio) * (1.0 + 2.0 * md.FIELD_PAD) / (resolution - 1)

    import trimesh
    us = stages.amounts(int(p.stages), float(p.amount))
    n = len(us)
    manifest = {"tool": TOOL.id, "tool_version": TOOL.version, "source": Path(src).name, "second": b_name,
                "frame": {"up": up, "front": front, "rotate": [float(x) for x in rot]}}
    height_model = 2.0 / frame.scale
    vol0 = abs(trimesh.Trimesh(VA, FA, process=False).volume) or 1e-12
    build = md.BUILD[p.fusion]
    values = dict(p)
    rows = []
    coarse_any = False
    out = stages.StageOutput(ctx, TOOL, dst, n, p.layout, p.axis, float(p.stage_gap) / 100.0 * height_model,
                             p.save_stages, manifest)
    with out:
        for i, u in enumerate(us):
            ctx.progress(0.56 + 0.32 * i / n, f"stage {i + 1} of {n}")
            st = build(A, B, values, float(u), step)
            loose = [trimesh.Trimesh(V, F, process=False) for V, F in st.extra]
            if st.exact is not None or st.grid is None:
                parts = [trimesh.Trimesh(V, F, process=False) for V, F in (st.exact or [])] + loose
                if not parts:
                    raise ValueError(f"nothing is left at Fusion {u:.2f}")
                m = trimesh.util.concatenate(parts)
                rows.append((i, u, len(m.faces), len(parts), abs(m.volume) / vol0, st.note, None))
            else:
                coarse_any |= st.coarse
                Fv, pockets = mf.fill_voids(st.F, st.grid.step) if st.fill else (st.F, 0)
                if pockets:
                    ctx.log(f"stage {i}: {pockets} sealed pocket{'s' if pockets != 1 else ''} of air filled")
                Vc, Fc = mp.extract(Fv, st.grid)
                if len(Fc) == 0:
                    raise ValueError(f"nothing is left at Fusion {u:.2f}: the models do not overlap here; move the "
                                     f"second one (Move second, Gap) or change the Size ratio")
                m = trimesh.Trimesh(Vc, Fc, process=False)
                m.merge_vertices()
                m = mf.drop_specks(m)
                m, before, after = mp.keep_components(m, p.keep, float(p.min_piece))
                m = mp.smooth(m, int(p.smooth))
                budget = int(p.max_faces)
                if ctx.draft:
                    budget = min(budget or gm.PREVIEW_FACES, gm.PREVIEW_FACES)
                if budget and len(m.faces) > budget:
                    ctx.progress(0.56 + 0.32 * (i + 0.6) / n, f"stage {i + 1}: simplifying")
                    Vs, Fs = gm.simplify_faces(m.vertices, m.faces, budget, ctx.log)
                    m = trimesh.Trimesh(Vs, Fs, process=False)
                if loose:
                    m = trimesh.util.concatenate([m] + loose)          # the fragments are counted in the note
                rows.append((i, u, len(m.faces), after, abs(m.volume) / vol0, st.note or None, st.grid))
            m = trimesh.Trimesh(frame.inverse(m.vertices), m.faces, process=False)
            out.add(i, m, {"amount": round(float(u), 6), "label": f"stage {i}"})
            ctx.check_cancel()
        outputs = out.finish()
    primary = out.primary

    ext = primary.extents
    pct = 2.0 / 100.0
    lines = [Path(src).name,
             f"  frame      up {up}, front {front}" + (f", turned {', '.join(f'{a:g}' for a in rot)}°" if any(rot)
                                                        else "") + f" ({source})",
             f"  models     {Path(src).name} ({num(len(FA))} faces) and {b_name} ({num(len(FB))} faces), the "
             f"second {ratio:g}× as tall" + (", mirrored" if p.mirror_b else "")
             + (", roles swapped" if p.swap and p.fusion in SWAPPABLE else ""),
             f"  fusion     {NAMES[p.fusion]}"
             + (f", {ARR_NAMES[p.arrangement]}, gap {p.gap:g}% h" if p.fusion in ("convergence", "bridge") else "")
             + (f", from {'the other model' if p.host == 'model' else 'a ' + p.host}" if p.fusion == "emergence"
                else "")
             + (f", {p.style} seam" if p.fusion == "split" else "")
             + (f", {p.slabs} slabs" if p.fusion == "strata" else "")
             + (f", {p.echoes} echoes on a {p.path} path" if p.fusion == "echo" else "")
             + (f", {p.shell_pattern}" if p.fusion == "chrysalis" else ""),
             f"  fields     {' x '.join(str(s) for s in A.field.grid.shape)} and "
             f"{' x '.join(str(s) for s in B.field.grid.shape)} samples"
             + ("  (draft)" if ctx.draft else ""),
             f"  scene      samples {step / frame.scale:.4g} units apart ({step / pct:.2f}% h)"
             + (" (coarsened for a wide scene: lower the Gap or the Size ratio, or Resolution)" if coarse_any else "")]
    for i, u, nf, pieces, vol, note, grid in rows:
        what = f"{note}, " if note else ""
        lines.append(f"  stage {i}    fusion {u:.3f}: {what}{num(nf)} faces, {pieces} piece{'s' if pieces != 1 else ''}"
                     f", {vol * 100:.0f}% of the first model's volume")
    lines += [f"  size       {ext[0]:.4g} x {ext[1]:.4g} x {ext[2]:.4g} units",
              f"  time       {time.perf_counter() - t0:.1f} s"]
    if ctx.draft:
        lines.append(f"  preview    {resolution} samples instead of {p.resolution}, models under "
                     f"{num(int(p.draft_faces))} faces, output under {num(gm.PREVIEW_FACES)} faces; Run uses the "
                     f"full settings")
    lines += [f"  saved as   {o}" for o in outputs[:1]]
    if len(outputs) > 1:
        lines.append(f"  stages     {len(outputs) - 1} files in {Path(outputs[1]).parent}")
    last = rows[-1]
    ctx.progress(1.0)
    return Result(outputs=outputs,
                  stats=[Stat("stages", "Stages", n),
                         Stat("faces", "Faces", len(primary.faces), before=len(FA)),
                         Stat("pieces", "Pieces", last[3]),
                         Stat("volume", "Volume", last[4] * 100, "%", fmt="{:.0f}"),
                         Stat("size", "Size", " × ".join(f"{v:.4g}" for v in ext), fmt="{}")],
                  report="\n".join(lines), preview={"views": out.views, "prefer": out.prefer},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"amounts": us, "frame": manifest["frame"]})
