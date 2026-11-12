"""Toolkit adapter for Shape Fusion: load both shapes, place the target in the model's frame, sample the fields once
on a joint grid, extract every stage, publish signed meshes."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d, influence, stages
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_porous import mesh_porous as mp

from . import TOOL
from . import mesh_fusion as mf

MODE_NAMES = {"morph": "morph", "fuse": "fuse", "carve": "carve", "intersect": "intersect"}
SHAPE_NAMES = {"torus": "torus", "sphere": "sphere", "box": "block", "cylinder": "column"}
SWEEP_NAMES = {"up": "from the base", "down": "from the top", "front": "from the front", "back": "from the back",
               "left": "from the left", "right": "from the right", "out": "from the centre", "in": "from the outside"}
LOW_OVERLAP = 0.35


def num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


@dataclass
class Target:
    name: str                     # for the report
    place: mf.Placement
    V: np.ndarray | None          # local vertices (mesh targets), sampled
    F: np.ndarray | None
    shape: str | None = None      # built-in shape
    exact: tuple | None = None    # (V local, F) at full detail, for Morph's last stage
    closed: bool = True
    own: bool = False             # the model itself: its field is the model's, placed

    def copies(self):
        """Canonical (V, F) of every copy (mesh targets)."""
        return [(self.place.apply(self.V, k), self.F) for k in range(len(self.place.turns))]

    def bounds(self, local_pts) -> list:
        out = []
        for k in range(len(self.place.turns)):
            Q = self.place.apply(local_pts, k)
            out.append((Q.min(0), Q.max(0)))
        return out


def make_target(ctx, p, src, VA, FA, VA_full, FA_full, frame) -> tuple[Target, np.ndarray]:
    """The placed target, and the local points whose image bounds it."""
    lo, hi = frame.lo, frame.hi
    pct = float(hi[2] - lo[2]) / 100.0
    offset = np.asarray(p.target_offset, dtype=np.float64) * pct
    rot = tuple(p.target_rotate)
    copies = int(p.copies)
    if p.target in mf.SHAPES:
        tube, aspect = float(p.tube) / 100.0, float(p.aspect) / 100.0
        pts = mf.shape_mesh(p.target, tube, aspect).vertices
        pl = mf.place(pts, lo, hi, "centre", float(p.target_scale), rot, offset, copies)
        return Target(SHAPE_NAMES[p.target], pl, None, None, p.target), pts
    if p.target == "self":
        pl = mf.place(VA_full, lo, hi, "centre", float(p.target_scale), rot, offset, copies)
        return Target("the model itself", pl, VA, FA, exact=(VA_full, FA_full), own=True), VA
    if p.target != "file":
        raise ValueError(f"unknown target {p.target!r}")
    path = Path(p.target_file) if p.target_file else None
    if path is None:
        raise ValueError("choose the target model (Target model), or a built-in target shape")
    if not path.exists():
        raise ValueError(f"the target model {path} does not exist")
    tm = gm.load_mesh(path)
    if len(tm.faces) == 0:
        raise ValueError(f"the target model {path.name} has no faces")
    VB = np.asarray(tm.vertices, dtype=np.float64)
    FB = np.asarray(tm.faces, dtype=np.int64)
    if p.fit == "keep":
        VL = frame.apply(VB)                       # the same coordinates as the model, in its frame
    else:
        up, front, _, _ = frame3d.resolve_axes(path, {"up_axis": p.target_up, "front": p.target_front},
                                               tm.extents)
        VL = VB @ frame3d.rotation(up, front, (0.0, 0.0, 0.0)).T
    pl = mf.place(VL, lo, hi, p.fit, float(p.target_scale), rot, offset, copies)
    Vs, Fs = gm.simplify_faces(VL, FB, int(p.draft_faces), ctx.log) if ctx.draft else (VL, FB)
    return Target(path.name, pl, np.asarray(Vs, float), np.asarray(Fs, np.int64), exact=(VL, FB),
                  closed=bool(tm.is_watertight)), VL


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
    V0 = frame.apply(mesh.vertices)
    F0 = np.asarray(mesh.faces, dtype=np.int64)
    faces_in = len(F0)
    if not mesh.is_watertight:
        ctx.warn("the model is not closed: inside and outside come from its generalised winding number, which "
                 "bridges small holes; a large opening can leave stray surfaces. Close it first for a reliable "
                 "result.")
    V, F = (gm.simplify_faces(V0, F0, int(p.draft_faces), ctx.log) if ctx.draft else (V0, F0))
    V, F = np.asarray(V, float), np.asarray(F, np.int64)
    target, local_pts = make_target(ctx, p, src, V, F, V0, F0, frame)
    if not target.closed:
        ctx.warn(f"the target {target.name} is not closed: its inside comes from its winding number")

    height = float(frame.hi[2] - frame.lo[2])
    pct = height / 100.0
    k = float(p.radius) * pct if p.mode != "morph" else 0.0
    swell = float(p.swell) * pct
    resolution = int(p.draft_resolution if ctx.draft else p.resolution)
    boxes = [(frame.lo, frame.hi)] + target.bounds(local_pts)
    jlo, jhi = mf.joint_bounds(boxes, pad=k + swell)
    grid = mp.make_grid(jlo, jhi, resolution)
    mp.check_size(grid)
    band = 3.0 * grid.step + k + swell + 6.0 * pct

    values = dict(p)
    us = stages.amounts(int(p.stages), float(p.amount))
    fields = None
    if any(u > 0 for u in us):
        ctx.progress(0.05, "model distance")
        a = mp.signed_distance(V, F, grid, band, lambda t, msg="": ctx.progress(0.05 + 0.3 * t, "model: " + msg))
        if not (a < 0).any():
            raise ValueError("the model encloses no volume at this resolution: it must be a closed solid (or raise "
                             "Resolution for a very thin one)")
        ctx.progress(0.35, "target distance")
        if target.own:
            b = mf.copies_of_field(a, target.place, grid)
        elif target.shape:
            b = mf.shape_distance(target.shape, target.place, grid, float(p.tube) / 100.0, float(p.aspect) / 100.0)
        else:
            b = None
            parts = target.copies()
            for i, (Vb, Fb) in enumerate(parts):
                lo_i, hi_i = 0.35 + 0.3 * i / len(parts), 0.35 + 0.3 * (i + 1) / len(parts)
                bi = mp.signed_distance(Vb, Fb, grid, band,
                                        lambda t, msg="", lo_i=lo_i, hi_i=hi_i: ctx.progress(lo_i + (hi_i - lo_i) * t,
                                                                                            "target: " + msg))
                b = bi if b is None else np.minimum(b, bi)
        if not (b < 0).any():
            raise ValueError(f"the target ({target.name}) encloses no volume at this resolution: make it bigger "
                             f"(Scale) or raise Resolution")
        overlap, na, nb = mf.overlap_share(a, b)
        o = mf.operate(p.mode, a, b, k)
        w, pos = mf.weights_on_grid(grid, values, frame.lo, frame.hi, p.sweep, jlo, jhi)
        fields = mf.Fields(grid, a, o.astype(np.float32), w, pos, overlap, na, nb)
        del b
        if p.mode in ("morph", "intersect") and overlap < LOW_OVERLAP:
            ctx.warn(f"the model and the target share only {overlap * 100:.0f}% of the smaller one's volume: half "
                     f"way they thin out or split. Move the target over the model (Offset, Fit, Scale) or add Swell.")
        if p.mode == "fuse" and overlap == 0 and k <= 0:
            ctx.log("the shapes do not touch: Fuse keeps them apart (raise Smooth radius or move them together)")

    n = len(us)
    width = float(p.sweep_width) / 100.0
    manifest = {"tool": TOOL.id, "tool_version": TOOL.version, "source": Path(src).name,
                "frame": {"up": up, "front": front, "rotate": [float(x) for x in rot]}}
    height_model = height / frame.scale
    import trimesh
    rows = []
    vol0 = abs(trimesh.Trimesh(V0, F0, process=False).volume) or 1e-12
    out = stages.StageOutput(ctx, TOOL, dst, n, p.layout, p.axis, float(p.gap) / 100.0 * height_model,
                             p.save_stages, manifest)
    with out:
        for i, u in enumerate(us):
            ctx.progress(0.66 + 0.22 * i / n, f"stage {i + 1} of {n}")
            if u <= 0 or fields is None:
                m = trimesh.Trimesh(np.array(mesh.vertices), F0, process=False)        # the model, untouched
                rows.append((i, u, len(F0), 1, 1, 1.0, "the model itself"))
            elif (p.mode == "morph" and target.exact is not None and len(target.place.turns) == 1
                  and mf.is_target_everywhere(fields, u, width)):
                Ve, Fe = target.exact                                                   # the target, as placed
                m = trimesh.Trimesh(target.place.apply(Ve), Fe, process=False)
                rows.append((i, u, len(Fe), 1, 1, abs(m.volume) / vol0, "the target itself"))
                m = trimesh.Trimesh(frame.inverse(m.vertices), m.faces, process=False)
            else:
                Fv, pockets = mf.fill_voids(mf.blend_field(fields, u, width, swell), grid.step)
                if pockets:
                    ctx.log(f"stage {i}: {pockets} sealed pocket{'s' if pockets != 1 else ''} of air inside the "
                            f"solid filled")
                Vc, Fc = mp.extract(Fv, grid)
                if len(Fc) == 0:
                    if p.mode == "carve":
                        raise ValueError(f"nothing is left at blend {u:.2f}: the target covers the whole model; "
                                         f"make it smaller (Scale) or move it (Offset)")
                    raise ValueError(f"nothing is left at blend {u:.2f}: the model and the target barely overlap. "
                                     f"Move the target over the model (Offset, Fit, Scale) or add Swell")
                m = trimesh.Trimesh(Vc, Fc, process=False)
                m.merge_vertices()
                m = mf.drop_specks(m)
                m, before, after = mp.keep_components(m, p.keep, float(p.min_piece))
                m = mp.smooth(m, int(p.smooth))
                budget = int(p.max_faces)
                if ctx.draft:
                    budget = min(budget or gm.PREVIEW_FACES, gm.PREVIEW_FACES)
                if budget and len(m.faces) > budget:
                    ctx.progress(0.66 + 0.22 * (i + 0.6) / n, f"stage {i + 1}: simplifying")
                    Vs, Fs = gm.simplify_faces(m.vertices, m.faces, budget, ctx.log)
                    m = trimesh.Trimesh(Vs, Fs, process=False)
                rows.append((i, u, len(m.faces), before, after, abs(m.volume) / vol0, None))
                m = trimesh.Trimesh(frame.inverse(m.vertices), m.faces, process=False)
            out.add(i, m, {"amount": round(u, 6), "label": f"stage {i}"})
            ctx.check_cancel()
        outputs = out.finish()
    primary = out.primary

    last = rows[-1]
    ext = primary.extents
    step_model = grid.step / frame.scale
    tgt = target.name + (f" x{len(target.place.turns)} around the vertical axis" if len(target.place.turns) > 1
                         else "")
    lines = [Path(src).name,
             f"  frame      up {up}, front {front}" + (f", turned {', '.join(f'{a:g}' for a in rot)}°" if any(rot)
                                                        else "") + f" ({source})",
             f"  input      {num(faces_in)} faces, {'closed' if mesh.is_watertight else 'open'}"
             + (f" (sampled from {num(len(F))} for the preview)" if len(F) != faces_in else ""),
             f"  target     {tgt}, scale {p.target_scale:g}"
             + (f", turned {', '.join(f'{a:g}' for a in p.target_rotate)}°" if any(p.target_rotate) else "")
             + (f", moved {', '.join(f'{a:g}' for a in p.target_offset)}% h" if any(p.target_offset) else ""),
             f"  mode       {MODE_NAMES[p.mode]}" + (f", smooth radius {p.radius:g}% h" if p.mode != "morph" else "")
             + (f", swell {p.swell:g}% h" if p.swell > 0 else "")
             + (f", swept {SWEEP_NAMES[p.sweep]} (front {p.sweep_width:g}%)" if p.sweep != "none" else ""),
             f"  acts on    {influence.describe(values)}",
             f"  grid       {' x '.join(str(s) for s in grid.shape)} samples, {step_model:.4g} units apart "
             f"({grid.step / pct:.2f}% h)" + ("  (draft)" if ctx.draft else "")]
    if fields is not None:
        lines.append(f"  overlap    {fields.overlap * 100:.0f}% of the smaller shape lies inside the other")
    for i, u, nf, before, after, solid, what in rows:
        if what:
            lines.append(f"  stage {i}    blend {u:.3f}: {what}, {num(nf)} faces")
        else:
            lines.append(f"  stage {i}    blend {u:.3f}: {num(nf)} faces, {after} piece{'s' if after != 1 else ''}"
                         + (f" (of {before})" if before != after else "") + f", {solid * 100:.0f}% of the model's "
                         f"volume")
    lines += [f"  size       {ext[0]:.4g} x {ext[1]:.4g} x {ext[2]:.4g} units",
              f"  time       {time.perf_counter() - t0:.1f} s"]
    if ctx.draft:
        lines.append(f"  preview    {resolution} samples instead of {p.resolution}, inputs under "
                     f"{num(int(p.draft_faces))} faces, output under {num(gm.PREVIEW_FACES)} faces; Run uses the "
                     f"full settings")
    lines += [f"  saved as   {o}" for o in outputs[:1]]
    if len(outputs) > 1:
        lines.append(f"  stages     {len(outputs) - 1} files in {Path(outputs[1]).parent}")
    if last[4] > 1 and p.keep == "all":
        ctx.log(f"{last[4]} loose pieces kept (Loose pieces: drop small or largest only removes them)")
    ctx.progress(1.0)
    return Result(outputs=outputs,
                  stats=[Stat("stages", "Stages", n),
                         Stat("faces", "Faces", len(primary.faces), before=faces_in),
                         Stat("pieces", "Pieces", last[4]),
                         Stat("volume", "Volume", last[5] * 100, "%", fmt="{:.0f}"),
                         Stat("size", "Size", " × ".join(f"{v:.4g}" for v in ext), fmt="{}")],
                  report="\n".join(lines), preview={"views": out.views, "prefer": out.prefer},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"amounts": us, "frame": manifest["frame"], "voxel": step_model,
                        "overlap": None if fields is None else fields.overlap})
