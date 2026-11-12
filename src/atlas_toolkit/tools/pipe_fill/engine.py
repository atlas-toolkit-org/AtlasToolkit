"""Atlas adapter: original units, result-only preview, staged signed publication."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.mesh_export import publish_meshes
from . import TOOL
from . import pipe_fill as pf


def make_sample(folder):
    import trimesh
    path = Path(folder)/"pipe envelope.stl"
    mesh = trimesh.creation.icosphere(subdivisions=2, radius=20.)
    mesh.vertices *= [.8, .65, 1.2]
    mesh.export(path)
    return path


def validate_saved(path):
    mesh = gm.load_mesh(path)
    if (not mesh.is_watertight or not mesh.is_winding_consistent or mesh.volume <= 0 or
            not np.isfinite(mesh.vertices).all() or not mesh.nondegenerate_faces().all()):
        raise ValueError("The saved pipe mesh lost closed-shell topology. Recenter large coordinates, "
                         "increase Outer diameter, or reduce Curve samples / Tube sides.")


def run(ctx, src, dst, p):
    import trimesh
    from atlas_toolkit.tools.mesh_holes import engine as holes
    src = Path(src)
    if ctx.dry_run:
        dst = None
    if dst is not None and Path(dst).resolve() == src.resolve():
        raise ValueError("The output must not replace the input model.")
    started = time.perf_counter()
    ctx.progress(.01, "Loading pipe envelope")
    mesh = gm.load_mesh(src)
    if not np.isfinite(mesh.vertices).all() or np.max(mesh.extents) <= 0:
        raise ValueError("The envelope must have finite coordinates and nonzero size.")
    up, front, rotate, _ = frame3d.resolve_axes(src, p, mesh.extents)
    frame = frame3d.fit_frame(mesh.vertices, up, front, rotate, mode="height", size=2.)
    body = trimesh.Trimesh(frame.apply(mesh.vertices), mesh.faces.copy(), process=False)
    closed = body.is_watertight and body.is_winding_consistent and holes.solid(body.vertices, body.faces) is not None
    repaired = not closed
    if repaired:
        if not p.repair:
            raise ValueError("The input must be a closed, consistently oriented solid. Close it first or enable "
                             "Repair open mesh; inferred repair can soften detail and close openings.")
        ctx.warn(f"Inferring a closed volume at {p.repair_resolution} samples; fine detail and openings may change.")
        v, f = holes.closed_copy(body.vertices, body.faces, int(p.repair_resolution),
                                lambda t, m='': ctx.progress(.02+.13*t, 'Repair: '+m))
        body = trimesh.Trimesh(v, f, process=True)
        if (not body.is_watertight or not body.is_winding_consistent or
                holes.solid(body.vertices, body.faces) is None):
            raise ValueError("Repair could not infer a valid closed volume. Close the model before packing it.")
    if body.volume < 0:
        body.invert()
    rotation = frame3d.euler_xyz(p.pattern_rotate)
    vertices = np.asarray(body.vertices) @ rotation
    ctx.progress(.16, "Building boundary-distance and winding hierarchies")
    domain = pf.Domain(vertices, body.faces, ctx.check_cancel)
    curves, info = pf.pack(domain, p, lambda f, m: ctx.progress(.18+.59*f, m), ctx.check_cancel)
    ctx.progress(.78, "Sweeping tube walls")
    result = pf.build_mesh(curves, int(p.sides), p.section == 'hollow', float(p.wall)/100.,
                           int(p.max_faces), ctx.check_cancel)
    result.vertices = frame.inverse(result.vertices @ rotation.T)
    # Float32 files must retain the small tube details at the source coordinates.
    smallest = float(p.diameter)/100./frame.scale*(1-float(p.variation)/100.)
    if p.section == 'hollow':
        smallest *= float(p.wall)/100.
    precision = max(1., float(np.abs(result.vertices).max()))*np.finfo(np.float32).eps
    if precision > smallest*.03:
        raise ValueError("Source coordinates are too large for the pipe detail in STL/PLY. Recenter the model "
                         "or increase diameter/wall thickness.")
    ctx.check_cancel()
    outputs = []
    if dst is not None:
        from atlas_toolkit.geom.surface import from_mesh
        surface = from_mesh(result)
        surface.frame = {'up': up, 'front': front, 'rotate': list(rotate)}
        outputs = publish_meshes([surface], dst, ctx, TOOL, validator=validate_saved)
    if info['coverage'] < 70.:
        ctx.warn(f"Paths use {info['coverage']:.1f}% of eligible interior sites; narrow regions and clearance "
                 "checks can leave gaps. Try thinner tubes or Independent strands for greater coverage.")
    nominal_length = sum(np.linalg.norm(b-a, axis=1).sum() for a, b in map(pf.segments, curves))/frame.scale
    material = 100.*result.volume/(abs(body.volume)/frame.scale**3)
    report = [src.name,
              f"  mode       {p.mode}; {p.topology}; {p.section}; seed {p.seed}",
              f"  curves     {len(curves):,}; {nominal_length:.4g} model units of centerline",
              f"  diameter   {p.diameter:g}% h, variation {p.variation:g}%; spacing {p.spacing:g}% h",
              f"  clearance  boundary {p.clearance:g}% h; inter-pipe gap {p.gap:g}% h",
              f"  routing    {info['used']:,}/{info['sites']:,} eligible sites ({info['coverage']:.1f}%); "
              f"{info['rejected']} paths rejected by thickness/clearance checks",
              f"  material   {material:.2f}% of the enclosing solid's volume (gaps are intentional)",
              f"  mesh       {len(result.faces):,} faces; closed material shells; original coordinates and units",
              "  output     pipes only; original envelope omitted; independent shells are not fused",
              "  method     Atlas finite-radius packing with local repulsion, not the original tangent-point solver",
              "  drawing    Plotter Vision Features (decimation off), or Hatch Maker",
              f"  time       {time.perf_counter()-started:.2f} s"]
    if repaired:
        report.append("  repair     inferred volume by resampling; original openings/details may change")
    if ctx.draft:
        report.append("  preview    same paths and tube geometry as Run; display proxy only for very large outputs")
    report.extend(f"  saved      {path}" for path in outputs)
    # Vertex clustering can flatten narrow tubes or merge nearby strands. Use
    # intact normal outputs and a quadric display copy only above this threshold.
    display = result
    if len(result.faces) > 300000:
        v, f = gm.simplify_faces(result.vertices, result.faces, 200000, ctx.log)
        display = trimesh.Trimesh(v, f, process=False)
    preview_mesh = gm.preview(display, max_faces=len(display.faces))
    preview_mesh.faces_total = len(result.faces)
    preview_mesh.bounds = tuple(map(tuple, result.bounds))
    ctx.progress(1., "Saved" if outputs else "Previewed")
    return Result(outputs=outputs,
                  stats=[Stat('curves', 'Curves', len(curves)),
                         Stat('coverage', 'Routing coverage', info['coverage'], '%', fmt='{:.1f}'),
                         Stat('material', 'Material fill', material, '%', fmt='{:.1f}'),
                         Stat('faces', 'Faces', len(result.faces))],
                  report='\n'.join(report),
                  preview={'views': [{'title': 'Output', 'kind': 'mesh', 'mesh': preview_mesh,
                                      'status': 'Final geometry' if display is result else 'Display proxy · final geometry retained'}],
                           'prefer': 'Output'},
                  status='Saved' if outputs else 'Previewed',
                  data=dict(info, curves=len(curves), repaired=repaired,
                            frame={'up': up, 'front': front, 'rotate': list(rotate)}))
