"""Standard Atlas adapter; preview and exports use identical extraction geometry."""
from pathlib import Path
import time

import numpy as np
import trimesh

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.mesh_export import publish_meshes
from atlas_toolkit.geom.surface import from_mesh
from . import TOOL
from . import mesh_surface_eject as se


def selection_text(p):
    if p.get("selection_file"):
        path = Path(p["selection_file"])
        if path.stat().st_size > 32_000_000:
            raise ValueError("Selection file is too large (maximum 32 MB).")
        return path.read_text(encoding="utf-8-sig")
    return p.get("selection", "")


def run(ctx, src, dst, p):
    started = time.perf_counter()
    se.validate(p)
    src = Path(src)
    if ctx.dry_run:
        dst = None
    if dst is not None and Path(dst).resolve() == src.resolve():
        raise ValueError("The output must not replace the input model.")
    ctx.progress(.01, "Loading source")
    original = gm.load_mesh(src)
    if len(original.faces) > p.max_faces:
        raise ValueError("Input exceeds Maximum faces. Simplify the model or raise the limit.")
    if not len(original.faces) or not np.isfinite(original.vertices).all():
        raise ValueError("Input has no finite surface.")
    selected = se.decode_selection(original, selection_text(p)) if p.region == "paint" else None
    up, front, rotate, source = frame3d.resolve_axes(src, p, original.extents)
    frame = frame3d.fit_frame(original.vertices, up, front, rotate, mode="height", size=2.)
    mesh = trimesh.Trimesh(frame.apply(original.vertices), original.faces.copy(), process=False)
    if mesh.is_watertight and mesh.is_winding_consistent and mesh.volume < 0:
        mesh.invert()
    repaired = False
    try:
        se.solid(mesh)
    except ValueError:
        if p.repair == "strict":
            raise
        if p.region == "paint":
            raise ValueError("Repair changes the painted faces. Repair and save this model first, then paint the closed input.")
        from atlas_toolkit.tools.mesh_holes.engine import closed_copy
        ctx.warn("Resampling repair changes the original surface and may soften detail.")
        v, f = closed_copy(mesh.vertices, mesh.faces, int(p.repair_resolution),
                           lambda t, msg="": ctx.progress(.03 + .1 * t, "Repair: " + msg))
        mesh = trimesh.Trimesh(v, f)
        repaired = True
    result = se.extract(mesh, p, selected, ctx)
    if len(result.parts) < p.count:
        ctx.warn(f"Extracted {len(result.parts)} of {p.count} requested pieces. Constraints left fewer suitable sites; see Report.")
    removed = 100 * (result.original_volume - result.body.volume()) / result.original_volume
    # Check the partition before any file is staged. Translation never changes extracted material volume.
    error = abs(result.body.volume() + sum(r["volume"] for r in result.records) - result.original_volume)
    if error > result.original_volume * 1e-7:
        raise ValueError("Extraction did not conserve model volume; no output was published.")
    connection = None
    if p.connectors and p.connector_percent > 0:
        from . import connectors as co
        connection = co.build(result, p, float(mesh.extents[2]), ctx)
        if not connection.records:
            connection = None

    def source_mesh(body):
        out = se.to_mesh(body)
        out.vertices = frame.inverse(out.vertices)
        return out

    body = source_mesh(result.body)
    fragments = trimesh.util.concatenate([source_mesh(part) for part in result.parts])
    combined = trimesh.util.concatenate([body, fragments])
    connector_mesh = None
    if connection is not None:
        combined = source_mesh(connection.combined)
        connector_solid, connector_precision = co.stabilize(co.union(connection.connectors), float(mesh.extents[2]))
        connection.precision = max(connection.precision, connector_precision)
        connector_mesh = source_mesh(connector_solid)
    for item in (body, fragments, combined, *([connector_mesh] if connector_mesh is not None else [])):
        if len(item.faces) > int(p.max_faces):
            raise ValueError("An output exceeds Maximum faces. Reduce Pieces / Extra branches / Curved shape segments.")
        if not item.is_watertight or not item.is_winding_consistent or not np.isfinite(item.vertices).all():
            raise ValueError("Extracted meshes failed closed-solid validation; no output was published.")
    outputs = []
    if dst is not None:
        meshes = [combined] if p.export_mode == "combined" else [body, fragments] + ([connector_mesh] if connector_mesh is not None else [])
        surfaces = [from_mesh(item) for item in meshes]
        for item in surfaces:
            item.frame = frame.frame_values()

        def validate_saved(path):
            saved = gm.load_mesh(path)
            if (not saved.is_watertight or not saved.is_winding_consistent or not np.isfinite(saved.vertices).all()
                    or not saved.nondegenerate_faces().all()):
                raise ValueError("Saved extraction lost valid solid topology. Recenter large coordinates or use OBJ / PLY.")

        outputs = publish_meshes(surfaces, dst, ctx, TOOL, extra_label="parts", validator=validate_saved,
                                 extra_labels=["parts", "connectors"] if p.export_mode == "separate" and connector_mesh is not None else None)
    records = []
    for row in result.records:
        r = dict(row)
        r["anchor"] = frame.inverse(np.array([row["anchor"]]))[0].tolist()
        r["normal"] = (np.asarray(row["normal"]) @ frame.R).tolist()
        for key in ("width", "depth", "gap", "travel"):
            r[key] /= frame.scale
        r["volume"] /= frame.scale ** 3
        r["skin_area"] /= frame.scale ** 2
        records.append(r)
    report = [src.name, f"  sampler    {p.shape}; actual source material, original orientation",
              f"  pieces     {len(result.parts)} / {p.count}; {result.attempts} candidate sites",
              f"  removed    {removed:.3f}% of body volume, retained in fragments",
              f"  region     {p.region}; {'repaired surface' if repaired else 'exact original surface'}",
              f"  frame      up {up}, front {front} ({source}); original units and placement retained",
              f"  ejection   {'placed with clearance; selected pieces bridged to their cavities' if connection is not None else 'fully detached; clearance certified against original body and previous fragments' if p.fully_detach else 'translation only; intersections may remain'}",
              f"  range      size {p.size_min:g}–{p.size_max:g}, depth {p.depth_min:g}–{p.depth_max:g}, gap {p.distance_min:g}–{p.distance_max:g} % original height",
              f"  export     {p.export_mode}; separate outputs share their original placement",
              "  drawing    Use an oblique view to reveal cavity walls. Detachment guarantees 3D clearance; 2D visibility depends on the camera."]
    report += [f"  rejected   {key}: {value}" for key, value in result.rejected.items()]
    connection_records = []
    if connection is not None:
        for row in connection.records:
            item = dict(row)
            for key in ("cavity", "root_inside", "fragment_inside"):
                item[key] = frame.inverse(np.array([item[key]]))[0].tolist()
            item["paths"] = [frame.inverse(np.asarray(path)).tolist() for path in item["paths"]]
            for key in ("requested_diameter", "diameter", "extent", "lateral_max"):
                item[key] /= frame.scale
            connection_records.append(item)
        clamped = sum(row["clamped"] for row in connection.records)
        achieved = sum(row["branches"] for row in connection.records)
        requested = sum(row["requested_branches"] for row in connection.records)
        reduced = sum(row["curve_scale"] < 1. for row in connection.records)
        report += [f"  connectors {p.connector_style}; {len(connection.records)} embedded and fused cavity-to-fragment bridges",
                   f"  fitting    {clamped} thickness limits; {achieved} / {requested} extra branches; {reduced} curvature reductions",
                   "  joints     positive-volume contact to each matching cavity and fragment; final boolean union validated",
                   "  material   extraction conserves source volume; connectors add material beyond that partition"]
        if p.connector_style == "organic":
            counts = (f"random {p.connector_branches_min}–{p.connector_branches_max} inclusive"
                      if p.connector_branch_count == "random" else f"fixed {p.connector_branches}")
            report += [f"  branching  {counts}; origins {p.connector_branch_placement}; up to {p.connector_branch_levels} levels; branch seed {p.connector_branch_seed}",
                       "  density    high branch counts use finer tips and denser interior sampling; achieved counts remain constrained by fit and face budget"]
        if connection.precision:
            report.append(f"  precision  joint sliver stabilization ≤ {connection.precision / frame.scale:.5g} source units; closed double/float32 copies validated")
        report += [f"  bridge {row['fragment_index']+1:<3} thickness {row['diameter']:.5g}; forks {row['branches']}/{row['requested_branches']}; levels {row['branch_levels']}; lateral extent {row['lateral_max']:.5g} / {row['extent']:.5g} source units"
                   for row in connection_records]
    connected_count = len(connection_records)
    connected_percent = 100. * connected_count / len(result.parts)
    if p.connectors:
        report += [f"  connected  {connected_count} / {len(result.parts)} ({connected_percent:.1f}%); requested {p.connector_percent:g}%, nearest whole count",
                   f"  selection  {p.connector_selection}; connection seed {p.connector_seed}; unselected pieces remain floating"]
    report += [f"  piece {i+1:<3} travel {row['travel']:.5g}; gap ≥ {row['gap']:.5g} source units" for i, row in enumerate(records)]
    report += [f"  saved      {path}" for path in outputs]
    report.append(f"  time       {time.perf_counter() - started:.3f} s")
    views = [{"title": name, "kind": "mesh", "mesh": gm.preview(item), "status": "Final geometry"}
             for name, item in (("Before", original), ("Combined", combined), ("Cavity body", body), ("Ejected parts", fragments))]
    if connector_mesh is not None:
        if len(combined.faces) > gm.PREVIEW_FACES:
            body_proxy = gm.preview(body, max_faces=gm.PREVIEW_FACES // 2)
            display = trimesh.util.concatenate([trimesh.Trimesh(body_proxy.vertices, body_proxy.faces, process=False),
                                                fragments, connector_mesh])
            data = gm.preview(display, max_faces=len(display.faces))
            data.faces_total = len(combined.faces)
            data.bounds = tuple(tuple(float(x) for x in row) for row in combined.bounds)
            views[1].update(mesh=data, status="Body display proxy · full fragments and connectors")
        views.append({"title": "Connectors", "kind": "mesh", "mesh": gm.preview(connector_mesh, max_faces=int(p.max_faces)), "status": "Final connector geometry"})
    ctx.progress(1., "Saved" if outputs else "Previewed")
    return Result(outputs=outputs, stats=[Stat("pieces", "Ejected pieces", len(result.parts)),
                  Stat("removed", "Excavated volume", removed, "%", fmt="{:.1f}"),
                  Stat("faces", "Faces", len(combined.faces), before=len(original.faces))]
                  + ([Stat("connected", "Connected fragments", connected_count),
                      Stat("connected_pct", "Connected share", connected_percent, "%", fmt="{:.1f}")] if p.connectors else [])
                  + ([Stat("branches", "Extra branches", sum(row["branches"] for row in connection.records))] if connection else []),
                  report="\n".join(report), preview={"views": views, "prefer": "Combined"},
                  status="Saved" if outputs else "Previewed",
                  data={"pieces": records, "rejected": result.rejected, "repaired": repaired,
                        "frame": frame.frame_values(), "export_mode": p.export_mode, "connectors": connection_records,
                        "connected_fragments": [row["fragment_index"] for row in connection_records],
                        "connected_percent": connected_percent})


def make_sample(folder):
    mesh = trimesh.creation.icosphere(subdivisions=2, radius=30.)
    mesh.vertices *= [1., .8, 1.5]
    path = Path(folder) / "surface-eject-sample.stl"
    mesh.export(path)
    return path
