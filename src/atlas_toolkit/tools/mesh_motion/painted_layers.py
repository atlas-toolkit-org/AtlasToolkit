"""Section-polygon wakes extruded as real closed laminae, with exact painted roots."""
import math
import numpy as np
import manifold3d as mf
import trimesh
from shapely import union_all, set_precision
from shapely.geometry import Polygon, MultiPoint, box
from shapely.geometry.polygon import orient
from atlas_toolkit.tools.mesh_surface_eject import mesh_surface_eject as se
from .mesh_motion import basis, smooth


def polygons(shape):
    if shape.is_empty:
        return []
    if shape.geom_type == "Polygon":
        return [shape]
    return [part for part in shape.geoms if part.geom_type == "Polygon" and not part.is_empty]


def section_shape(contours):
    result = Polygon()
    for ring in contours:
        polygon = Polygon(ring)
        if polygon.is_valid and polygon.area > 1e-12:
            result = result.symmetric_difference(polygon)
    return result


def sculpt(mesh, p, ctx, painted):
    n, a, b = basis(p.layer_axis, p.tilt)
    projection = mesh.vertices@n
    lo, hi = float(projection.min()), float(projection.max())
    pitch = (hi-lo)/p.layers
    if pitch < 1e-10:
        raise ValueError("Model has no thickness along the layer direction.")
    if p.structure == "splinters" and p.layers*p.lanes > 5000:
        raise ValueError("Use at most 5,000 layer/bar cells. Reduce Layers or Bars per layer.")
    parts, faces, max_reach, sweeps, occupied = [], 0, 0., 0, 0
    precision = 2e-6
    clearance = min(pitch*.01, 1e-5)
    for k in range(p.layers):
        ctx.progress(.08+.72*k/p.layers, f"building painted section {k+1}/{p.layers}")
        s, centre_z = (k+.5)/p.layers, lo+(k+.5)*pitch
        angle = math.radians(p.heading+(p.turn*(s-.5) if p.gesture == "vortex" else 0.))
        h, side = math.cos(angle)*a+math.sin(angle)*b, -math.sin(angle)*a+math.cos(angle)*b
        B = np.column_stack((h, side, n))
        local = np.asarray(mesh.vertices)@B
        source = mf.Manifold(mf.Mesh64(np.array(local, dtype=np.float64, order="C", copy=True),
                                      np.array(mesh.faces, dtype=np.uint64, order="C", copy=True)))
        section = section_shape(source.slice(centre_z).to_polygons())
        if section.is_empty:
            continue
        occupied += 1
        lines, face_ids = trimesh.intersections.mesh_plane(mesh, n, n*centre_z, return_faces=True)
        active = painted.mask[face_ids] & ((mesh.face_normals[face_ids]@(-h)) > 1e-5)
        lines = lines[active]
        weights = painted(lines.mean(axis=1)) if len(lines) else np.zeros(0)
        segments = lines@B[:, :2]
        phase = 2*math.pi*p.cycles*s+math.radians(p.phase)
        rhythm = {"stream": 1., "vortex": 1., "pulse": .15+.85*(.5+.5*math.sin(phase))**2,
                  "gust": .15+.85*(.5+.5*math.sin(phase+.65*math.sin(phase*.47)))**2}[p.gesture]
        hold = smooth((s-p.hold_base/100)/.1) if p.hold_base else 1.
        amount = p.amount*hold
        xlo, ylo, xhi, yhi = section.bounds
        count = p.lanes if p.structure == "splinters" else 1
        bar_pitch = (yhi-ylo)/count
        for j in range(count):
            ctx.check_cancel()
            cell_lo, cell_hi = ylo+j*bar_pitch, ylo+(j+1)*bar_pitch
            cell = section.intersection(box(xlo-1, cell_lo, xhi+1, cell_hi)) if count > 1 else section
            if cell.is_empty:
                continue
            cy = (cell_lo+cell_hi)/2
            rng = np.random.default_rng(np.random.SeedSequence([int(p.seed), k, j]))
            variation = 1-p.variation/100+p.variation/100*rng.random()
            length = p.reach*.02*amount*rhythm*variation
            additions = []
            if length > 0:
                for segment, weight in zip(segments, weights):
                    ctx.check_cancel()
                    low, high = float(segment[:, 1].min()), float(segment[:, 1].max())
                    if high < cell_lo or low > cell_hi:
                        continue
                    centre_x = float(segment[:, 0].mean())
                    q = smooth(((xhi-centre_x)/max(xhi-xlo, 1e-12)-p.preserve/100)/(1-p.preserve/100))
                    reach = length*float(weight)*(1-p.preserve/100+p.preserve/100*q)
                    if reach <= precision*2:
                        continue
                    root = segment+np.array([precision*4, 0.])
                    tip = segment.copy()
                    tip[:, 0] -= reach
                    tip[:, 1] = cy+(tip[:, 1]-cy)*(1-p.taper/100*amount)
                    tip[:, 1] += p.curl*.02*amount*rhythm*weight
                    sweep = MultiPoint(np.vstack((root, tip))).convex_hull
                    if count > 1:
                        sweep = sweep.intersection(box(-1e5, cell_lo, 1e5, cell_hi))
                    if sweep.area > precision**2:
                        additions.append(sweep)
                        max_reach = max(max_reach, reach)
            shape = set_precision(union_all([cell, *additions], grid_size=precision), precision)
            sweeps += len(additions)
            thickness = min(max(pitch*(1-p.gap/100*p.amount), clearance*4), pitch-clearance)
            for polygon in polygons(shape):
                polygon = orient(polygon, sign=1.)
                contours = [np.asarray(polygon.exterior.coords)[:-1].tolist()]
                contours += [np.asarray(ring.coords)[:-1].tolist() for ring in polygon.interiors]
                solid = mf.Manifold.extrude(mf.CrossSection(contours, mf.FillRule.NonZero), thickness)
                solid = solid.as_original().simplify(precision*.25)
                if count > 1:
                    shrink = max(p.bar_gap/100*p.amount, min(.01, clearance/max(bar_pitch, 1e-12)))
                    matrix = np.eye(3)
                    matrix[1, 1] = 1-shrink
                    solid = solid.transform(np.column_stack((matrix, [0., cy*shrink, 0.])).tolist())
                solid = solid.transform(np.column_stack((B, n*(centre_z-thickness/2))).tolist())
                part = se.to_mesh(solid)
                faces += len(part.faces)
                if faces > p.max_faces:
                    raise ValueError("Painted wakes exceed Maximum faces. Reduce layers/bars or simplify the source.")
                if not part.is_watertight or not part.is_winding_consistent or part.volume <= 0:
                    raise ValueError("Painted section failed solid validation. No output was published.")
                parts.append(part)
    if not parts:
        raise ValueError("No solid painted sections found. Increase Layers to capture small features.")
    if not sweeps and p.reach > 0:
        ctx.warn("No outward painted faces face the wake direction. Change Travel heading or paint a larger area.")
    return trimesh.util.concatenate(parts), dict(pieces=len(parts), layers=occupied, max_reach=max_reach,
            max_displacement=max_reach, paint_guard_scale=1., paint_clearance=clearance, paint_sweeps=sweeps,
            paint_section_pitch=pitch)
