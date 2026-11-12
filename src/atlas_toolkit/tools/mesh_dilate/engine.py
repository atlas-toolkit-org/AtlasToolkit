"""Atlas adapter: prepare components, inflate, carry detail and publish one mesh."""
from __future__ import annotations

from pathlib import Path
import numpy as np

from atlas_toolkit.core import Stat
from atlas_toolkit.geom import frame3d, influence
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_compress import mesh_compress as mc

from . import TOOL
from . import mesh_dilate as md


def budgets(total, areas):
    """Allocate a shared face budget, including small disconnected parts."""
    count = len(areas)
    if total < 4 * count:
        raise ValueError("Output face limit is too small for the number of separate parts")
    remaining = total - 4 * count
    raw = remaining * np.asarray(areas) / max(float(np.sum(areas)), 1e-18)
    result = np.floor(raw).astype(int) + 4
    for j in np.argsort(-(raw - np.floor(raw)))[:total - int(result.sum())]:
        result[j] += 1
    return result


def run(ctx, src: Path, dst: Path | None, p):
    import trimesh
    if ctx.dry_run:
        dst = None
    if dst is not None and dst.resolve() == Path(src).resolve():
        raise ValueError("the output must not replace the input model")
    ctx.progress(0.01, "loading model")
    mesh = gm.load_mesh(src)
    if not len(mesh.faces) or not np.isfinite(mesh.vertices).all() or mesh.area <= 0:
        raise ValueError("Dilate needs a finite, nonempty triangle surface")
    if float(p.inflation) == 0:
        return gm.result(ctx, TOOL, mesh, mesh.copy(), dst,
                         stats=[Stat("inflation", "Added volume", 0.0, "%", fmt="{:.1f}"),
                                Stat("displacement", "Moved up to", 0.0, "% h", fmt="{:.1f}")],
                         report=[Path(src).name, "  dilation   zero inflation: geometry unchanged; one output"])
    up, front, rotate, source = frame3d.resolve_axes(src, p, mesh.extents)
    frame = frame3d.fit_frame(mesh.vertices, up, front, rotate, mode="height", size=frame3d.MODEL_HEIGHT)
    height = frame.height or float(np.max(frame.extent))
    V, F = frame.apply(mesh.vertices), np.asarray(mesh.faces, np.int64)
    parts = md.components(V, F)
    part_areas = [float(md.areas(V, F[fi])[0].sum()) for _, fi in parts]
    out_budget = int(min(p.out_faces, 40_000) if ctx.draft else p.out_faces)
    sim_budget = min(5_000 if ctx.draft else (36_000 if p.quality == "high" else 12_000),
                     max(1_000, out_budget // 2))
    output_budgets = budgets(out_budget, part_areas)
    simulation_budgets = budgets(max(sim_budget, 4 * len(parts)), part_areas)
    sim_vertices, sim_faces, detail_vertices, detail_faces, records, reliable = [], [], [], [], [], []
    ns = nd = 0
    for k, (indices, fi) in enumerate(parts):
        ctx.progress(0.03 + 0.10 * k / len(parts), f"preparing part {k + 1}/{len(parts)}")
        remap = np.full(len(V), -1, dtype=np.int64)
        remap[indices] = np.arange(len(indices))
        part = trimesh.Trimesh(V[indices], remap[F[fi]], process=False)
        # Inconsistent or reversed winding must not turn pressure inward.
        part.fix_normals(multibody=True)
        P, PF = np.asarray(part.vertices), np.asarray(part.faces)
        closed = bool(part.is_watertight and part.is_winding_consistent and
                      part.volume > max(1e-12, 1e-10 * part.area ** 1.5))
        S, SF, edge = mc.remesh(P, PF, int(simulation_budgets[k]), ctx.log)
        D, DF = mc.detail_mesh(P, PF, edge, int(output_budgets[k]), ctx.log)
        # Decimation must not convert an open scan to a purported closed volume.
        closed = closed and mc.is_closed(SF) and mc.is_closed(DF)
        reliable.append(closed)
        sim_vertices.append(S)
        sim_faces.append(SF + ns)
        detail_vertices.append(D)
        detail_faces.append(DF + nd)
        records.append((slice(ns, ns + len(S)), SF, slice(nd, nd + len(D)), DF))
        ns += len(S)
        nd += len(D)
    S, SF = np.vstack(sim_vertices), np.vstack(sim_faces)
    D, DF = np.vstack(detail_vertices), np.vstack(detail_faces)
    sw = md.mobility(S, SF, influence.weights(S, p, frame.lo, frame.hi), p.anchor, frame.lo, frame.hi)
    dw = md.mobility(D, DF, influence.weights(D, p, frame.lo, frame.hi), p.anchor, frame.lo, frame.hi)
    open_parts = sum(not c for c in reliable)
    if open_parts:
        ctx.warn(f"{open_parts} open or unreliable-volume part(s): preserving openings and using outward surface "
                 "swelling. Inflation is an area-equivalent approximation; enclosed volume is unavailable "
                 "for these parts. Surface winding determines the outward direction.")
    if p.anchor == "boundary" and not len(md.boundary_vertices(SF)):
        ctx.warn("Keep open boundary: this model has no opening edges; expansion is free.")
    ctx.progress(0.14, "inflating surface")
    out = md.solve(S, SF, sw, inflation=float(p.inflation), softness=float(p.softness),
                   roundness=float(p.roundness), direction=p.direction, axis=p.axis, lobes=float(p.lobes),
                   lobe_size=float(p.lobe_size), seed=int(p.seed), height=height, closed=reliable,
                   draft=ctx.draft, progress=lambda f: ctx.progress(0.14 + 0.63 * f, "inflating surface"),
                   check=ctx.check_cancel)
    ctx.progress(0.79, "carrying the detail along")
    X = D.copy()
    for ss, faces_s, ds, faces_d in records:
        ctx.check_cancel()
        if out.trajectory:
            X[ds] = md.detail_base(D[ds], faces_d, dw[ds], float(p.detail))
    guard = md.ContactGuard(D, DF, ctx.check_cancel)
    X, initial_retained = guard.limit(D, X)
    previous, safe = S, X.copy()
    retained = [initial_retained]
    # Advect the detailed mesh through small spatial warps. A single endpoint
    # skinning map can have a negative Jacobian near a thin ear even when the
    # simulated surface is valid; composition avoids that large-deformation fold.
    for i, current in enumerate(out.trajectory):
        proposed = np.empty_like(X)
        for ss, faces_s, ds, _ in records:
            ctx.check_cancel()
            transported = md.warp(X[ds], previous[ss], faces_s, current[ss])
            proposed[ds] = X[ds] + (transported - X[ds]) * md.smoothstep(dw[ds])[:, None]
        proposed[dw <= 1e-12] = D[dw <= 1e-12]
        if p.direction == "radial":
            proposed[:, md.AXIS[p.axis]] = D[:, md.AXIS[p.axis]]
        X = proposed
        previous = current
        if (i + 1) % 8 == 0 or i == len(out.trajectory) - 1:
            X, alpha = guard.limit(safe, X)
            retained.append(alpha)
            safe = X.copy()
        ctx.progress(0.79 + 0.15 * (i + 1) / len(out.trajectory), "carrying detail and checking contacts")
    detail_scale = float(np.mean(retained))
    X[dw <= 1e-12] = D[dw <= 1e-12]
    if p.direction == "radial":
        X[:, md.AXIS[p.axis]] = D[:, md.AXIS[p.axis]]
    if not np.isfinite(X).all():
        raise ValueError("Dilate could not produce a finite surface; try a smaller inflation")
    v0 = v1 = desired = 0.0
    actual_parts = []
    for k, (_, _, ds, faces_d) in enumerate(records):
        before = mc.volume(D[ds], faces_d) if reliable[k] and out.volumes0[k] is not None else None
        after = mc.volume(X[ds], faces_d) if before is not None else None
        actual_parts.append(None if before is None else (after / before - 1) * 100)
        if before is not None:
            v0 += before
            v1 += after
            desired += before * (1 + float(p.inflation) / 100 * out.participation[k])
    limited = out.limited + guard.limited
    target_missed = v0 > 0 and abs(v1 - desired) > 0.03 * desired
    if limited:
        ctx.warn("Contact safeguards restrained local motion. See the report for the achieved inflation.")
    if target_missed:
        ctx.warn("Contacts, anchors, surface detail or solver convergence limited the requested inflation. "
                 "The safe achieved result is reported; reduce inflation or broaden the region for more freedom.")
    if out.existing or guard.existing:
        ctx.warn("The input already has non-adjacent intersecting triangles. Dilate guards against new "
                 "crossings but does not repair the existing intersections.")
    moved = float(np.linalg.norm(X - D, axis=1).max()) / height * 100
    stats = [Stat("displacement", "Moved up to", moved, "% h", fmt="{:.1f}")]
    if not open_parts and v0 > 0:
        stats.insert(0, Stat("inflation", "Added volume", (v1 / v0 - 1) * 100, "%", fmt="{:.1f}"))
    else:
        stats.insert(0, Stat("inflation", "Added volume", "surface swelling", fmt="{}"))
    lines = [Path(src).name,
             f"  frame      up {up}, front {front}, turn {rotate} ({source})",
             f"  dilation   requested {p.inflation:g}% added volume; {p.direction}" +
             (f" about {p.axis}" if p.direction == "radial" else "") + "; one final mesh",
             f"  region     {influence.describe(p)}; anchor {p.anchor}",
             f"  material   softness {p.softness:g}, roundness {p.roundness:g}, detail {p.detail:g}; "
             f"lobes {p.lobes:g}, size {p.lobe_size:g}% h, seed {p.seed}",
             f"  meshes     {len(parts)} part(s), {len(SF):,} simulation faces; "
             f"{len(DF):,} output faces" + (" (draft)" if ctx.draft else ""),
             f"  contacts   {limited} limited updates; detailed motion {detail_scale * 100:.1f}%; "
             "discrete triangle checks, not continuous collision detection"]
    for k, value in enumerate(actual_parts):
        lines.append(f"  part {k + 1:<5} " + (f"added volume {value:.1f}%, "
                     f"requested {p.inflation * out.participation[k]:.1f}% after region and anchors"
                     if value is not None else "open surface: outward swelling, enclosed volume unavailable"))
    result = gm.result(ctx, TOOL, mesh, trimesh.Trimesh(frame.inverse(X), DF, process=False), dst,
                       stats=stats, report=lines)
    result.data = dict(parts=len(parts), open_parts=open_parts, added_volume=actual_parts,
                       participation=out.participation, limited=limited, simulation_faces=len(SF),
                       simulation_added_volume=[None if a is None else (b / a - 1) * 100
                                                for a, b in zip(out.volumes0, out.volumes1)])
    return result


def make_sample(folder: Path):
    """An asymmetric closed balloon; quick enough for contract and frozen checks."""
    import trimesh
    model = trimesh.creation.icosphere(subdivisions=2, radius=20)
    model.apply_scale((0.7, 0.6, 1.0))
    path = Path(folder) / "sample.stl"
    model.export(path)
    return path
