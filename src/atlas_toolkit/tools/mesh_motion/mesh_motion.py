"""Capped source layers with an injective planar wake deformation, no GUI or I/O.

Local x points toward travel. For trailing distance t, x' = x - L smoothstep(t).
Its derivative is >= 1. Positive transverse scales and unchanged layer ordering
keep the continuous map invertible within each piece. Bars may meet after curl;
separate shells are deliberately not boolean-unioned.
"""
import math
import numpy as np
from atlas_toolkit.geom import cutting, influence


def smooth(t):
    t = np.clip(t, 0., 1.)
    return t * t * (3. - 2. * t)


def basis(axis, tilt):
    n, a = {"up": ([0., 0., 1.], [1., 0., 0.]),
            "front": ([0., 1., 0.], [1., 0., 0.]),
            "side": ([1., 0., 0.], [0., 1., 0.])}[axis]
    n, a = np.array(n), np.array(a)
    t = math.radians(tilt)
    n, a = math.cos(t)*n + math.sin(t)*a, math.cos(t)*a - math.sin(t)*n
    return n, a, np.cross(n, a)


def partition(mesh, normal, cuts, ctx, progress=None):
    """Native double-precision capped bisection avoids re-triangulating old caps.

    Sequential trimesh caps can leave slivers at the second family of bar cuts.
    Manifold keeps a closed boundary throughout both families, including holes.
    """
    import manifold3d as mf
    import trimesh
    solid = mf.Manifold(mf.Mesh64(np.array(mesh.vertices, dtype=np.float64, order="C", copy=True),
                                  np.array(mesh.faces, dtype=np.uint64, order="C", copy=True)))
    if solid.status() != mf.Error.NoError:
        raise ValueError("Native motion cutting rejected the input solid. Repair it first.")
    todo, out, done = [(solid, 0, len(cuts))], [], 0
    while todo:
        ctx.check_cancel()
        body, start, end = todo.pop()
        if body.is_empty():
            continue
        if start == end:
            data = body.to_mesh64()
            out.append((start, trimesh.Trimesh(np.asarray(data.vert_properties)[:, :3],
                                               np.asarray(data.tri_verts), process=False)))
            continue
        middle = (start+end)//2
        above, below = body.split_by_plane(normal.tolist(), float(cuts[middle]))
        todo.extend([(above, middle+1, end), (below, start, middle)])
        done += 1
        if progress:
            progress(done, len(cuts))
    return sorted(out, key=lambda item: item[0])


# Edge midpoint triangulation, shared-edge indices retain closed topology.
TEMPLATES = {0: ((0, 1, 2),), 1: ((0, 3, 2), (3, 1, 2)), 2: ((1, 4, 0), (4, 2, 0)),
             4: ((2, 5, 1), (5, 0, 1)), 3: ((1, 4, 3), (0, 3, 2), (3, 4, 2)),
             6: ((2, 5, 4), (1, 4, 0), (4, 5, 0)), 5: ((0, 3, 5), (2, 5, 1), (5, 3, 1)),
             7: ((0, 3, 5), (3, 1, 4), (5, 4, 2), (3, 4, 5))}


def refine(v, f, heading, span, resolution, budget, ctx, mask_step=None):
    v, f = np.asarray(v, float), np.asarray(f, np.int64)
    for _ in range(24):
        ctx.check_cancel()
        edges, inv = np.unique(np.sort(f[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1),
                               axis=0, return_inverse=True)
        inv = inv.reshape(-1, 3)
        x = v @ heading
        split = np.abs(x[edges[:, 1]] - x[edges[:, 0]]) > span / resolution * (1 + 1e-9)
        if mask_step is not None:
            split |= np.linalg.norm(v[edges[:, 1]]-v[edges[:, 0]], axis=1) > mask_step
        if not split.any():
            return v, f
        requested = len(f) + int(np.count_nonzero(split[inv]))
        if requested > budget:
            raise ValueError("Motion refinement exceeds Maximum faces. Reduce wake resolution, layers or bars.")
        mid = np.full(len(edges), -1, np.int64)
        mid[split] = np.arange(split.sum()) + len(v)
        mids = mid[inv]
        code = (mids >= 0).astype(np.uint8) @ np.array([1, 2, 4], np.uint8)
        corners = np.column_stack((f, mids))
        f = np.vstack([corners[code == k][:, template] for k, templates in TEMPLATES.items() for template in templates])
        v = np.vstack((v, v[edges[split]].mean(axis=1)))
    raise ValueError("Motion refinement did not converge. Lower wake resolution.")


def wake(vertices, heading, transverse, normal, centre, length, curl, taper, preserve):
    """Monotone stretch, positive taper and transverse shear; front vertices stay fixed."""
    v = np.asarray(vertices, float)
    x, y, z = v @ heading, v @ transverse, v @ normal
    lo, hi = float(x.min()), float(x.max())
    extent = max(hi - lo, 1e-12)
    q = smooth(((hi - x) / extent - preserve) / (1. - preserve))
    shrink = 1. - taper * q
    cy, cz = float(centre @ transverse), float(centre @ normal)
    return ((x - length*q)[:, None]*heading +
            (cy + (y-cy)*shrink + curl*q*q)[:, None]*transverse +
            (cz + (z-cz)*shrink)[:, None]*normal)


def sculpt(mesh, p, ctx, painted=None):
    import trimesh
    if p.amount == 0:
        return mesh.copy(), {"pieces": 1, "layers": 0, "max_reach": 0.}
    if p.influence == "paint" and painted is None:
        raise ValueError("Paint an area in the Selection tab before sculpting.")
    if len(mesh.faces) > p.max_faces:
        raise ValueError("Input exceeds Maximum faces. Simplify it or raise the budget.")
    if painted is not None:
        if p.paint_style == "connected":
            return sculpt_painted(mesh, p, ctx, painted)
        from .painted_layers import sculpt as swept_layers
        return swept_layers(mesh, p, ctx, painted)
    if p.structure == "splinters" and p.layers * p.lanes > 5000:
        raise ValueError("Use at most 5,000 layer/bar cells. Reduce Layers or Bars per layer.")
    n, a, b = basis(p.layer_axis, p.tilt)
    lo, hi = cutting.span(mesh, n)
    pitch = (hi-lo) / p.layers
    if pitch < 1e-10:
        raise ValueError("Model has no thickness along the layer direction.")
    ctx.progress(.08, "cutting source into capped layers")
    slabs = partition(mesh, n, np.linspace(lo, hi, p.layers+1)[1:-1], ctx,
                              progress=lambda d, total: ctx.progress(.08+.28*d/max(total, 1), "cutting layers"))
    parts, total_faces, max_reach = [], 0, 0.
    for index, (k, slab) in enumerate(slabs):
        ctx.progress(.37 + .45*index/max(len(slabs), 1), f"sweeping layer {index+1}/{len(slabs)}")
        s = (k + .5) / p.layers
        angle = math.radians(p.heading + (p.turn * (s-.5) if p.gesture == "vortex" else 0.))
        h, side = math.cos(angle)*a + math.sin(angle)*b, -math.sin(angle)*a + math.cos(angle)*b
        phase = 2*math.pi*p.cycles*s + math.radians(p.phase)
        rhythm = {"stream": 1., "vortex": 1., "pulse": .15+.85*(.5+.5*math.sin(phase))**2,
                  "gust": .15+.85*(.5+.5*math.sin(phase + .65*math.sin(phase*.47)))**2}[p.gesture]
        centre = (slab.bounds[0] + slab.bounds[1]) / 2
        region = float(influence.weights(centre[None], p, mesh.bounds[0], mesh.bounds[1])[0])
        hold = smooth((s-p.hold_base/100)/.1) if p.hold_base else 1.
        amount = p.amount * region * hold
        bar_pitch = 0.
        cells = [(0, slab)]
        if p.structure == "splinters":
            lower, upper = cutting.span(slab, side)
            bar_pitch = (upper-lower)/p.lanes
            cells = partition(slab, side, np.linspace(lower, upper, p.lanes+1)[1:-1], ctx)
        for j, cell in cells:
            ctx.check_cancel()
            # Split disconnected limbs before finding their leading fronts.
            for component_index, piece in enumerate(cell.split(only_watertight=False)):
                # Section planes through existing edges can leave an isolated,
                # zero-area cap triangle. Do not delete degenerate bridge faces
                # from closed components: they retain the cap's shared topology.
                if piece.area < 1e-18:
                    continue
                centre = piece.bounds.mean(axis=0)
                rng = np.random.default_rng(np.random.SeedSequence([int(p.seed), k, j, component_index]))
                variability = (1-p.variation/100) + p.variation/100 * rng.random()
                length = p.reach*.02 * amount * rhythm * variability
                curl = p.curl*.02 * amount * rhythm
                budget = p.max_faces-total_faces
                if amount > 0 and (length > 0 or curl != 0 or p.taper > 0):
                    lower, upper = cutting.span(piece, h)
                    v, f = refine(piece.vertices, piece.faces, h, max(upper-lower, 1e-10),
                                  p.resolution, budget, ctx)
                    v = wake(v, h, side, n, centre, length, curl, p.taper/100*amount, p.preserve/100)
                else:
                    v, f = np.array(piece.vertices), np.array(piece.faces)
                # Compress about layer and cell centres; preserves disjoint layer intervals.
                layer_centre = lo+(k+.5)*pitch
                v -= np.outer((v@n-layer_centre) * p.gap/100*p.amount, n)
                if bar_pitch:
                    v -= np.outer((v@side-float(centre@side)) * p.bar_gap/100*p.amount, side)
                total_faces += len(f)
                if total_faces > p.max_faces:
                    raise ValueError("Motion output exceeds Maximum faces. Reduce layers, bars or wake resolution.")
                part = trimesh.Trimesh(v, f, process=False)
                if not np.isfinite(v).all() or not part.is_watertight or not part.is_winding_consistent or part.volume <= 0:
                    raise ValueError("A motion piece could not be kept closed and oriented. Repair the input or use fewer layers.")
                parts.append(part)
                max_reach = max(max_reach, length)
    if not parts:
        raise ValueError("No solid layers found. Check the input model.")
    return trimesh.util.concatenate(parts), {"pieces": len(parts), "layers": len(slabs), "max_reach": max_reach}


def sculpt_painted(mesh, p, ctx, painted):
    """Continuous layer-sampled motion on the source surface, with pinned unpainted skin.

    Unlike separate full-model slabs, a partially painted connected surface must
    not acquire coincident internal caps. Blend adjacent layer gestures instead.
    """
    import trimesh
    from .selection import protect
    n, a, b = basis(p.layer_axis, p.tilt)
    lo, hi = cutting.span(mesh, n)
    pitch = (hi-lo)/p.layers
    if pitch < 1e-10:
        raise ValueError("Model has no thickness along the layer direction.")
    ctx.progress(.08, "refining connected painted surface")
    rest, faces = refine(mesh.vertices, mesh.faces, n, hi-lo, max(p.resolution, p.layers*2),
                         p.max_faces, ctx, mask_step=max(p.paint_feather*.01, .01))
    weights = painted(rest)
    coordinate = np.clip((rest@n-lo)/pitch-.5, 0., p.layers-1.)
    lower = np.floor(coordinate).astype(int)
    blend = coordinate-lower
    target = np.zeros_like(rest)
    reach = 0.
    for k in range(p.layers):
        ctx.progress(.35+.4*k/p.layers, f"shaping painted layer {k+1}/{p.layers}")
        contribution = np.where(lower == k, 1.-blend, 0.) + np.where(lower+1 == k, blend, 0.)
        active = contribution > 0
        if not active.any():
            continue
        s = (k+.5)/p.layers
        angle = math.radians(p.heading+(p.turn*(s-.5) if p.gesture == "vortex" else 0.))
        h, side = math.cos(angle)*a+math.sin(angle)*b, -math.sin(angle)*a+math.cos(angle)*b
        phase = 2*math.pi*p.cycles*s+math.radians(p.phase)
        rhythm = {"stream": 1., "vortex": 1., "pulse": .15+.85*(.5+.5*math.sin(phase))**2,
                  "gust": .15+.85*(.5+.5*math.sin(phase+.65*math.sin(phase*.47)))**2}[p.gesture]
        hold = smooth((s-p.hold_base/100)/.1) if p.hold_base else 1.
        amount = p.amount*hold
        v = rest[active]
        x, y = v@h, v@side
        xlo, xhi = cutting.span(mesh, h)
        q = smooth(((xhi-x)/max(xhi-xlo, 1e-12)-p.preserve/100)/(1-p.preserve/100))
        q = 1-p.preserve/100+p.preserve/100*q
        rng = np.random.default_rng(np.random.SeedSequence([int(p.seed), k]))
        draws = rng.random(p.lanes+1 if p.structure == "splinters" else 1)
        if p.structure == "splinters":
            ylo, yhi = cutting.span(mesh, side)
            lane = np.clip((y-ylo)/max(yhi-ylo, 1e-12)*p.lanes, 0., p.lanes)
            randomness = np.interp(lane, np.arange(p.lanes+1), draws)
        else:
            randomness = draws[0]
        variation = 1-p.variation/100+p.variation/100*randomness
        length = p.reach*.02*amount*rhythm*variation
        curl = p.curl*.02*amount*rhythm
        cy = float(mesh.bounds.mean(axis=0)@side)
        displacement = -np.outer(length*q, h)
        displacement += np.outer(-(y-cy)*p.taper/100*amount*q+curl*q*q, side)
        if p.structure == "splinters":
            bar_pitch = max(yhi-ylo, 1e-12)/p.lanes
            displacement -= np.outer(p.bar_gap/100*p.amount*bar_pitch/(2*math.pi)*np.sin(2*math.pi*lane), side)
        target[active] += (v+displacement)*contribution[active, None]
        reach = max(reach, float(np.max(length)))
    # Continuous corrugation expresses layer separation without opening a seam
    # through the pinned region. Its unmasked longitudinal derivative stays positive.
    phase = 2*math.pi*((rest@n-lo)/pitch-.5)
    target -= np.outer(p.gap/100*p.amount*pitch/(2*math.pi)*np.sin(phase), n)
    target = rest+(target-rest)*weights[:, None]
    target, achieved = protect(rest, target, faces, ctx)
    output = trimesh.Trimesh(target, faces, process=False)
    if not output.is_watertight or not output.is_winding_consistent or output.volume <= 0:
        raise ValueError("Painted deformation failed solid validation. Increase Paint feather or reduce motion.")
    return output, {"pieces": len(output.split()), "layers": p.layers, "max_reach": reach*achieved,
                    "paint_guard_scale": achieved, "max_displacement": float(np.linalg.norm(target-rest, axis=1).max())}
