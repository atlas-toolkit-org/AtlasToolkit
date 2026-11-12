"""Seeded, embedded bridges between facing cut surfaces of adjacent strata.

Placement is planned on the original cut interfaces, before any stage moves. Caps,
source components and layer transforms remain explicit until the final solid union.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh
from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points, unary_union

from atlas_toolkit.geom import cutting, connector_branches as branches
from atlas_toolkit.geom import solid_connectors as co


@dataclass
class Plan:
    sources: list
    samples: dict


@dataclass
class Slab:
    layer: int
    source: int
    index: int
    rest: object
    mesh: object
    solid: object
    rotation: np.ndarray
    translation: np.ndarray
    caps: dict


def validate(p):
    if not p.get("connectors"):
        return
    if p["connector_diameter_min"] > p["connector_diameter_max"]:
        raise ValueError("Connector thickness minimum must not exceed its maximum.")
    if (p["connector_style"] == "organic" and p["connector_branch_count"] == "random"
            and p["connector_branches_min"] > p["connector_branches_max"]):
        raise ValueError("Branches minimum must not exceed Branches maximum.")


def ordered_components(mesh):
    # A cavity is a negatively wound boundary shell, not another source object.
    # Retain it with its enclosing material component throughout slicing.
    shells = list(mesh.split(only_watertight=False))
    positive = [s for s in shells if s.volume > 0]
    groups = [[s] for s in positive]
    for shell in shells:
        if shell.volume > 0:
            continue
        enclosing = [i for i, outer in enumerate(positive) if outer.contains([shell.vertices[0]])[0]]
        if not enclosing:
            raise ValueError("Connectors require consistently oriented closed solids, including cavity walls.")
        owner = min(enclosing, key=lambda i: positive[i].volume)
        groups[owner].append(shell)
    components = [trimesh.util.concatenate(group) if len(group) > 1 else group[0] for group in groups]
    return sorted(components, key=lambda m: tuple(np.round(m.bounds.ravel(), 10)) + (len(m.faces),))


def solid_count(solid):
    """Count material components without treating inner cavity shells as objects."""
    return sum(part.volume() > 0 for part in solid.decompose())


def cap(mesh, normal, position, sign, st):
    tolerance = max(1e-8, st.pitch * 1e-6)
    on_plane = np.max(np.abs(mesh.triangles @ normal - position), axis=1) <= tolerance
    faces = on_plane & (mesh.face_normals @ normal * sign > .99)
    if not faces.any():
        return None
    triangles = mesh.triangles[faces]
    xy = np.stack((triangles @ st.e1, triangles @ st.e2), axis=-1)
    region = unary_union([Polygon(t) for t in xy if Polygon(t).area > tolerance ** 2])
    if region.is_empty or region.area <= tolerance ** 2:
        return None
    return region, triangles, position


def prepare(mesh, st, p, ctx=None):
    """Stable source ids and area-weighted samples on unthinned, unmoved interfaces."""
    validate(p)
    if not mesh.is_volume:
        raise ValueError("Connectors require an outward-oriented closed solid. Repair the input first.")
    if len(mesh.faces) > p["max_faces"]:
        raise ValueError("Input exceeds Maximum faces. Simplify the model before adding connectors.")
    sources = ordered_components(mesh)
    samples = {}
    planes = [float(st.lo + k * st.pitch) for k in range(1, st.count)]
    for source_id, source in enumerate(sources):
        if ctx:
            ctx.check_cancel()
        pieces = cutting.partition(source, st.n, planes, engine=cutting.best_engine(),
                                   progress=(lambda *_: ctx.check_cancel()) if ctx else None)
        for k, piece in pieces:
            if k >= st.count - 1:
                continue
            surface = cap(piece, st.n, planes[k], 1, st)
            if surface is None:
                continue
            _, triangles, _ = surface
            area = trimesh.triangles.area(triangles)
            rng = np.random.default_rng(np.random.SeedSequence([int(p["connector_seed"]), source_id, k, 9121]))
            draws = rng.random((max(256, int(p["connector_count"]) * 24), 3))
            ids = np.searchsorted(np.cumsum(area) / area.sum(), draws[:, 0])
            root = np.sqrt(draws[:, 1])
            points = np.einsum("ij,ijk->ik", np.c_[1-root, root*(1-draws[:, 2]), root*draws[:, 2]], triangles[ids])
            samples[source_id, k] = np.c_[points @ st.e1, points @ st.e2]
    return Plan(sources, samples)


def make_slabs(st, u, p, dice, plan, progress=None, ctx=None):
    from . import mesh_strata as ms
    r = ms.thickness(u, p["retained"] / 100.)
    cuts = ms.cuts_for(st, r)
    moves = ms.motions(st, u, p, dice)
    slabs, moved = [], 0.
    for source_id, source in enumerate(plan.sources):
        pieces = cutting.partition(source, st.n, cuts, engine=cutting.best_engine(), progress=progress)
        by_layer = {}
        for _, piece in pieces:
            lo, hi = cutting.span(piece, st.n)
            k = ms.classify(st, r, (lo + hi) / 2.)
            if k >= 0:
                by_layer.setdefault(k, []).extend(ordered_components(piece))
        for k in sorted(by_layer):
            R, t = moves[k]
            components = sorted(by_layer[k], key=lambda m: tuple(m.bounds.ravel()))
            for j, piece in enumerate(components):
                if ctx:
                    ctx.check_cancel()
                if not piece.is_volume:
                    raise ValueError(f"Layer {k + 1} is not a closed solid; connectors need closed capped slabs.")
                vertices = piece.vertices @ R.T + t
                moved = max(moved, float(np.linalg.norm(vertices - piece.vertices, axis=1).max()))
                mesh = trimesh.Trimesh(vertices, piece.faces, process=False)
                c = float(st.centre(k))
                caps = {sign: cap(piece, st.n, c + sign * .5 * r[k] * st.pitch, sign, st) for sign in (-1, 1)}
                slabs.append(Slab(k, source_id, j, piece, mesh, co.from_arrays(mesh.vertices, mesh.faces), R, t, caps))
    return slabs, moved, len(cuts)


def pairs(slabs):
    """Overlap matches first, then nearest partners for an otherwise unmatched cap."""
    result = []
    groups = {}
    for slab in slabs:
        groups.setdefault((slab.source, slab.layer), []).append(slab)
    for (source, layer), lower in sorted(groups.items()):
        upper = groups.get((source, layer + 1), [])
        lower = [s for s in lower if s.caps[1] is not None]
        upper = [s for s in upper if s.caps[-1] is not None]
        if not lower or not upper:
            continue
        matches = set()
        for i, a in enumerate(lower):
            for j, b in enumerate(upper):
                if a.caps[1][0].intersection(b.caps[-1][0]).area > 1e-12:
                    matches.add((i, j))
        for i, a in enumerate(lower):
            if not any(x == i for x, _ in matches):
                j = min(range(len(upper)), key=lambda j: a.caps[1][0].distance(upper[j].caps[-1][0]))
                matches.add((i, j))
        for j, b in enumerate(upper):
            if not any(y == j for _, y in matches):
                i = min(range(len(lower)), key=lambda i: b.caps[-1][0].distance(lower[i].caps[1][0]))
                matches.add((i, j))
        result.extend((lower[i], upper[j]) for i, j in sorted(matches))
    return result


def attachment(slab, sign, xy, requested_radius, st, eps):
    region, _, plane = slab.caps[sign]
    # Stay strictly inside the true cap, including around holes. Nearest interior
    # projections let a stable original sample follow a changing cross-section.
    margin = min(requested_radius * 1.8, np.sqrt(region.area) * .035)
    inset = region.buffer(-max(margin, eps * 100))
    if inset.is_empty:
        return None
    point = nearest_points(inset, Point(xy))[0]
    surface = point.x * st.e1 + point.y * st.e2 + plane * st.n
    edge = float(region.boundary.distance(point))
    thickness = float(np.ptp(slab.rest.vertices @ st.n))
    # Leave enough interior clearance for the requested cross-section. A shallow
    # embedding would otherwise shrink even a slender support on a roomy slab.
    depth = min(thickness * .22, max(requested_radius * 2.5, eps * 100))
    inside = surface - sign * depth * st.n
    if not slab.rest.contains([inside])[0]:
        return None
    _, distances, _ = trimesh.proximity.closest_point(slab.rest, [inside])
    clearance = min(edge, float(distances[0]))
    if clearance <= eps * 40:
        return None
    return inside @ slab.rotation.T + slab.translation, clearance, surface @ slab.rotation.T + slab.translation


def build_tree(a, b, start, finish, radius, width, solid_obstacles, earlier, p, identity, ctx, eps):
    """One trunk plus optional forks, all tips in the receiving slab."""
    rng = np.random.default_rng(np.random.SeedSequence([int(p["connector_seed"]), *identity, 9122]))
    phase = float(rng.uniform(0, 2*np.pi))
    axis = finish - start
    length = float(np.linalg.norm(axis))
    axis /= length
    style = p["connector_style"]
    taper = float(p["connector_taper"]) if style == "organic" else 1.
    extent = width * p["connector_extent"] / 100. if style == "organic" else 0.
    segments = min(48, int(p["connector_segments"]))

    def valid(candidate):
        return ((candidate ^ a.solid).volume() > eps ** 3
                and (candidate ^ b.solid).volume() > eps ** 3
                and (solid_obstacles.is_empty() or (candidate ^ solid_obstacles).volume() <= eps ** 3)
                and (earlier.is_empty() or (candidate ^ earlier).volume() <= eps ** 3)
                and len(candidate.decompose()) == 1)

    for scale in (1., .5, 0.):
        if ctx:
            ctx.check_cancel()
        if style == "organic":
            path = co.curve(start, finish, start, axis, extent, p["connector_curve"] * scale,
                            p["connector_irregularity"] * scale, phase)
            candidate = co.tube(path, radius, taper, p["connector_irregularity"] * scale, phase, segments)
        else:
            path = np.array([start, finish])
            candidate = co.beam(start, finish, radius, style, segments)
        if valid(candidate):
            break
    else:
        return None

    paths, solids, levels, radii, origins = [path], [candidate], [0], [radius], []
    branch_p = dict(p, seed=int(p["connector_seed"]))
    tree_id = int(np.random.SeedSequence(identity).generate_state(1)[0])
    requested = branches.requested_count(branch_p, tree_id)
    spread = min(extent, width * p["connector_spread"] / 100.)
    if requested and spread > eps * 10:
        # Sample only the receiving cap's inward neighbourhood, not an arbitrary
        # centroid or the far side of a wafer.
        pool, margins = tip_pool(b, -1, requested, eps)
        frame = co.basis(axis)
        delta = pool - finish
        lateral = delta - np.outer(delta @ axis, axis)
        within = np.linalg.norm(lateral, axis=1) <= spread + eps
        used = [finish]
        for branch_index, spec in enumerate(branches.origin_plan(branch_p, tree_id, requested)):
            if ctx:
                ctx.check_cancel()
            parents = [j for j, level in enumerate(levels) if 0 < level < p["connector_branch_levels"]]
            parent = parents[min(int(spec["parent_draw"] * len(parents)), len(parents)-1)] if parents and branch_index % 4 else 0
            lo, hi = branches.visible_interval(paths[parent], a.mesh, b.mesh)
            path_fraction = lo + (hi-lo) * spec["fraction"]
            junction, fraction = branches.path_point(paths[parent], path_fraction)
            branch_radius = min(radii[parent] * (1 - (1-taper)*fraction) * p["connector_branch_thickness"],
                                radius * p["connector_branch_thickness"] * min(1., np.sqrt(4./requested)) / np.sqrt(levels[parent]+1))
            eligible = within & (margins >= branch_radius * taper * 1.6)
            azimuth = phase + branch_index * np.pi * (3 - np.sqrt(5)) + spec["angle_jitter"]
            reach = spread * np.sqrt((branch_index+.5)/requested)
            desired = finish + reach * (np.cos(azimuth)*frame[:, 0] + np.sin(azimuth)*frame[:, 1])
            ids = np.flatnonzero(eligible)
            ids = ids[np.argsort(np.linalg.norm(pool[ids]-desired, axis=1), kind="stable")][:24]
            for j in ids:
                if ctx:
                    ctx.check_cancel()
                tip = pool[j]
                local_radius = min(branch_radius, np.linalg.norm(tip-junction)*.12)
                if local_radius < eps*40 or min(np.linalg.norm(tip-t) for t in used) < local_radius*3:
                    continue
                branch_path = co.curve(junction, tip, start, axis, extent, p["connector_curve"]*scale*.7,
                                       p["connector_irregularity"]*scale, azimuth)
                branch = co.tube(branch_path, local_radius, taper, p["connector_irregularity"]*scale, azimuth, segments)
                joined = candidate + branch
                if valid(joined) and (branch ^ candidate).volume() > eps**3 and (branch ^ b.solid).volume() > eps**3:
                    candidate = joined
                    paths.append(branch_path)
                    solids.append(branch)
                    levels.append(levels[parent]+1)
                    radii.append(local_radius)
                    origins.append(dict(spec, parent=parent, level=levels[-1], path_fraction=path_fraction))
                    used.append(tip)
                    if candidate.num_tri() > p["max_faces"]:
                        raise ValueError("Branches exceed Maximum faces. Reduce supports, branches or curved shape segments.")
                    break
    # Removing newest leaves preserves all ancestors, and never sacrifices a trunk.
    precision = 0.
    while True:
        if ctx:
            ctx.check_cancel()
        try:
            candidate, adjustment = co.stabilize(candidate, 2.)
            co.stabilize(co.union([a.solid, b.solid, candidate]), 2.)
            precision = max(precision, adjustment)
            break
        except ValueError:
            if len(solids) == 1:
                return None
            solids.pop()
            paths.pop()
            levels.pop()
            radii.pop()
            origins.pop()
            candidate = co.union(solids)
    lateral_max = max(float(np.linalg.norm(path-start-np.outer((path-start)@axis, axis), axis=1).max()) for path in paths)
    return candidate, dict(root_inside=start.tolist(), tip_inside=finish.tolist(), diameter=radius*2,
                           requested_branches=requested, branches=len(origins), branch_origins=origins,
                           branch_levels=max(levels), paths=[path.tolist() for path in paths],
                           extent=extent, lateral_max=lateral_max, curve_scale=scale, precision=precision), solids


def tip_pool(slab, sign, count, eps):
    """Interior candidates just behind the receiving cap, including concave caps."""
    _, triangles, _ = slab.caps[sign]
    count = max(128, 24 * count)
    sequence = np.arange(count) + .5
    area = trimesh.triangles.area(triangles)
    ids = np.searchsorted(np.cumsum(area) / area.sum(), sequence / count)
    root = np.sqrt((sequence * .61803398875) % 1)
    v = (sequence * .41421356237) % 1
    surfaces = np.einsum("ij,ijk->ik", np.c_[1-root, root*(1-v), root*v], triangles[ids])
    normal = np.cross(triangles[0, 1]-triangles[0, 0], triangles[0, 2]-triangles[0, 0])
    normal /= np.linalg.norm(normal)
    depth = float(np.ptp(slab.rest.vertices @ normal))
    pool = np.vstack([surfaces - normal * depth * fraction for fraction in (.12, .22, .35)])
    pool = pool[slab.rest.contains(pool)]
    if not len(pool):
        return np.empty((0, 3)), np.empty(0)
    _, margins, _ = trimesh.proximity.closest_point(slab.rest, pool)
    keep = margins > eps * 40
    return pool[keep] @ slab.rotation.T + slab.translation, margins[keep]


def sculpt(mesh, st, u, p, dice, progress=None, ctx=None, plan=None, connection_progress=None):
    validate(p)
    plan = plan or prepare(mesh, st, p, ctx)
    slabs, moved, cuts = make_slabs(st, u, p, dice, plan, progress, ctx)
    if not slabs:
        raise ValueError("Nothing left after cutting: raise retained thickness.")
    if sum(s.solid.num_tri() for s in slabs) > p["max_faces"]:
        raise ValueError("Slabs exceed Maximum faces. Reduce Layers or simplify the input.")
    records, supports, pair_rows, trees = [], [], [], []
    connections = pairs(slabs)
    for pair_index, (a, b) in enumerate(connections):
        if connection_progress:
            connection_progress(pair_index, len(connections))
        if ctx:
            ctx.check_cancel()
            ctx.log(f"Connecting layers {a.layer+1}–{b.layer+1}")
        fused_pair = a.solid + b.solid
        if solid_count(fused_pair) == 1:
            pair_rows.append(dict(source=a.source, lower=a.layer, upper=b.layer, already_joined=True, supports=0))
            continue
        obstacles = co.union([s.solid for s in slabs if s is not a and s is not b])
        earlier = co.union(supports)
        width = min(np.sqrt(a.caps[1][0].area), np.sqrt(b.caps[-1][0].area))
        samples = plan.samples.get((a.source, a.layer), np.empty((0, 2)))
        count, used = 0, []
        for candidate_index, xy in enumerate(samples):
            if count >= p["connector_count"]:
                break
            if ctx:
                ctx.check_cancel()
            identity = (a.source, a.layer, a.index, b.index, candidate_index)
            rng = np.random.default_rng(np.random.SeedSequence([int(p["connector_seed"]), *identity, 9123]))
            requested_radius = float(rng.uniform(p["connector_diameter_min"], p["connector_diameter_max"])) / 100.
            if used and min(np.linalg.norm(xy-old) for old in used) < max(width*.12, requested_radius*4):
                continue
            start = attachment(a, 1, xy, requested_radius, st, 2e-7)
            finish = attachment(b, -1, xy, requested_radius, st, 2e-7)
            if start is None or finish is None:
                continue
            factor = np.sqrt(2.) if p["connector_style"] == "bar" else 1.
            length = float(np.linalg.norm(finish[0]-start[0]))
            radius = min(requested_radius, start[1]*.6/factor, finish[1]*.6/factor,
                         length*(.12 if p["connector_style"] == "organic" else .3))
            if radius < 8e-6 or length < 1e-6:
                continue
            result = build_tree(a, b, start[0], finish[0], radius, width, obstacles, earlier,
                                p, identity, ctx, 2e-7)
            if result is None:
                continue
            support, row, limbs = result
            supports.append(support)
            trees.append(limbs)
            earlier = earlier + support
            used.append(xy)
            count += 1
            records.append(dict(row, source=a.source, lower=a.layer, upper=b.layer, lower_component=a.index,
                                upper_component=b.index, candidate=candidate_index, requested_diameter=requested_radius*2,
                                clamped=bool(radius < requested_radius*.999)))
            if sum(s.solid.num_tri() for s in slabs) + sum(s.num_tri() for s in supports) > p["max_faces"]:
                raise ValueError("Connectors exceed Maximum faces. Reduce supports, branches or curved shape segments.")
        if count == 0:
            raise ValueError(f"No safe connector between layers {a.layer+1} and {b.layer+1}. "
                             "Reduce dislocation or thickness, or change Connection seed. No output was published.")
        pair_rows.append(dict(source=a.source, lower=a.layer, upper=b.layer, already_joined=False,
                              supports=count, requested=p["connector_count"]))
    while True:
        if ctx:
            ctx.check_cancel()
        combined = co.union([s.solid for s in slabs] + supports)
        try:
            combined, precision = co.stabilize(combined, 2.)
            break
        except ValueError:
            # Independent trees can interact through a thin slab during the final
            # boolean. Drop newest optional leaves, retaining every required trunk.
            if not any(len(tree) > 1 for tree in trees):
                raise
            for i, (tree, row) in enumerate(zip(trees, records)):
                if len(tree) > 1:
                    tree.pop()
                    row["paths"].pop()
                    row["branch_origins"].pop()
                    row["branches"] -= 1
                    row["branch_levels"] = max((o["level"] for o in row["branch_origins"]), default=0)
                    start, finish = np.asarray(row["root_inside"]), np.asarray(row["tip_inside"])
                    axis = (finish-start) / np.linalg.norm(finish-start)
                    row["lateral_max"] = max(float(np.linalg.norm(path-start-np.outer((path-start)@axis, axis), axis=1).max())
                                             for path in map(np.asarray, row["paths"]))
                    supports[i] = co.union(tree)
    # Connectivity is certified pair by pair, rather than assuming every input is
    # one object or assuming an empty layer should be bridged.
    for a, b in connections:
        own = [support for support, row in zip(supports, records) if row["source"] == a.source
               and row["lower"] == a.layer and row["lower_component"] == a.index
               and row["upper_component"] == b.index]
        if solid_count(co.union([a.solid, b.solid, *own])) != 1:
            raise ValueError("A required stratum joint remained disconnected; no output was published.")
    if combined.num_tri() > p["max_faces"]:
        raise ValueError("Fused output exceeds Maximum faces. Reduce supports, branches or curved shape segments.")
    out = co.to_mesh(combined)
    if connection_progress:
        connection_progress(len(connections), len(connections))
    return out, dict(layers=len({s.layer for s in slabs}), pieces=len(slabs), closed=len(slabs), cuts=cuts,
                     moved=moved, connectors=records, pairs=pair_rows, precision=precision,
                     components=solid_count(combined))
