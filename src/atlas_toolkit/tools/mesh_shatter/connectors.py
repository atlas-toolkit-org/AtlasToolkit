"""Connectors from the shards to one central hub, as a tree or as radial stems.

A second, independently seeded pass over the finished shards: it never changes where they went. Shards are open
surface patches, so at Shard thickness 0 the connectors end on them and stay a separate closed solid. Above 0 every
shard gets an inward thickness and the connected ones fuse with the network into one solid, as in Surface Eject.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import manifold3d as mf
import numpy as np
import trimesh

from atlas_toolkit.geom import connector_branches as branches
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom import solid_connectors as co

MINIATURE_FACES = 60_000
PATH_POINTS = 25


def validate(p):
    if not p.get("connectors"):
        return
    if p["connector_diameter_min"] > p["connector_diameter_max"]:
        raise ValueError("Connector thickness minimum must not exceed its maximum.")
    if (p["connector_style"] == "organic" and p["connector_branch_count"] == "random"
            and p["connector_branches_min"] > p["connector_branches_max"]):
        raise ValueError("Branches minimum must not exceed Branches maximum.")


# Where the hub goes ----------------------------------------------------------------------------------------------

def deep_point(mesh) -> np.ndarray:
    """The roomiest point inside a closed model, preferring the middle of it. A centroid can fall outside a concave
    model or in a thin limb; this cannot. An open model has no inside, so it keeps its centroid."""
    centre = np.asarray(mesh.centroid, dtype=float)
    if not mesh.is_watertight:
        return centre
    from scipy.spatial import cKDTree
    lo, hi = mesh.bounds
    axes = [lo[i] + (hi[i] - lo[i]) * (np.arange(20) + .5) / 20 for i in range(3)]
    grid = np.stack(np.meshgrid(*axes, indexing="ij"), -1).reshape(-1, 3)
    centres = mesh.triangles_center
    surface = np.vstack((mesh.vertices, centres[::max(1, len(centres) // 200_000)]))
    room, _ = cKDTree(surface).query(grid)
    score = room - .25 * np.linalg.norm(grid - centre, axis=1)
    order = np.argsort(-score, kind="stable")
    for start in range(0, len(order), 512):      # the best scores can all lie in a hole: a ring's middle
        batch = order[start:start + 512]
        inside = batch[mesh.contains(grid[batch])]
        if len(inside):
            return grid[inside[0]]
    return centre


def hub_point(mesh, p, diag: float) -> np.ndarray:
    mode = p["hub_position"]
    if mode == "centroid":
        point = np.asarray(mesh.centroid, dtype=float)
    elif mode == "mass":
        point = np.asarray(mesh.center_mass if mesh.is_volume else mesh.bounds.mean(0), dtype=float)
    else:
        point = deep_point(mesh)
    offset = np.array([p["hub_offset_x"], p["hub_offset_y"], p["hub_offset_z"]], dtype=float)
    return point + offset * diag / 100.


# Which shards, and where on them ---------------------------------------------------------------------------------

def selected_indices(shards, hub, p) -> list:
    """Exact rounded count (half rounds up). Raising the percentage keeps the earlier choice and adds to it."""
    count = len(shards)
    wanted = min(count, int(np.floor(count * float(p["connector_percent"]) / 100. + .5)))
    if wanted <= 0:
        return []
    if wanted == count:
        return list(range(count))
    selection = p["connector_selection"]
    if selection == "random":
        ranked = np.random.default_rng(np.random.SeedSequence([int(p["connector_seed"]), 7030])).permutation(count)
    elif selection in ("largest", "smallest"):
        area = np.array([s.area for s in shards])
        ranked = np.argsort(-area if selection == "largest" else area, kind="stable")
    else:
        far = np.array([np.linalg.norm(s.centroid - hub) for s in shards])
        ranked = np.argsort(-far if selection == "farthest" else far, kind="stable")
    return sorted(int(i) for i in ranked[:wanted])


@dataclass
class Anchor:
    index: int
    shard: object
    tip: np.ndarray        # where the connector ends: on an open shard, inside a thick one
    width: float
    margin: float          # room to the shard's edge, which limits the tip's thickness


def anchor_on(index, shard, thickness) -> Anchor:
    """A face near the shard's middle and well away from its rim, so the joint reads as gripping the shard."""
    centres = shard.triangles_center
    near = np.argsort(np.linalg.norm(centres - shard.centroid, axis=1), kind="stable")[:48]
    width = float(np.sqrt(shard.area))
    margins = co.edge_clearance(shard, centres[near])
    if not margins.any():              # a closed part of the model has no rim
        margins = np.full(len(near), width)
    best = int(np.argmax(margins - .5 * np.linalg.norm(centres[near] - shard.centroid, axis=1)))
    tip = centres[near[best]] - shard.face_normals[near[best]] * thickness * .5
    return Anchor(index, shard, tip, width, float(margins[best]))


def thicken(shard, thickness: float) -> tuple[np.ndarray, np.ndarray]:
    """Close an open patch into a solid: a copy offset inward, joined to it around the rim. Raises ValueError where
    the patch is not a simple surface (an edge shared by three faces has no inside to offset to)."""
    V, F = np.asarray(shard.vertices), np.asarray(shard.faces)
    normals = np.nan_to_num(np.asarray(shard.vertex_normals))
    directed = shard.edges
    _, inverse, counts = np.unique(shard.edges_sorted, axis=0, return_inverse=True, return_counts=True)
    inverse = np.asarray(inverse).reshape(-1)
    if (counts > 2).any():
        raise ValueError("a shard is not a simple surface")
    rim = directed[counts[inverse] == 1]
    n = len(V)
    a, b = rim[:, 0], rim[:, 1]
    sides = np.vstack((np.c_[b, a, a + n], np.c_[b, a + n, b + n]))
    vertices = np.vstack((V, V - normals * thickness))
    faces = np.vstack((F, F[:, ::-1] + n, sides))
    if co.signed_volume(vertices, faces) < 0:
        faces = faces[:, ::-1]
    return vertices, faces


# The network -----------------------------------------------------------------------------------------------------

def tip_limit(p) -> float:
    """The thickest tip that stays inside a thick shard, even arriving at a shallow angle, and so never shows as a
    nub on the shard's far face."""
    return float(p["_thickness"]) * .45 if p["_thickness"] > 0 else float("inf")


@dataclass
class Limb:
    path: np.ndarray
    r0: float
    r1: float
    phase: float
    irregularity: float = 0.


@dataclass
class Network:
    limbs: list = field(default_factory=list)
    joints: list = field(default_factory=list)       # (point, radius): rounds off the place where limbs fork
    roots: list = field(default_factory=list)        # (direction, radius) of what leaves the hub
    branches: int = 0
    requested: int = 0


def wander(path, amount, phase, frequency=1.5) -> np.ndarray:
    """A slow sideways drift along a path, zero at both ends, so joints stay where they were planned."""
    t = np.linspace(0., 1., len(path))
    frame = co.basis(path[-1] - path[0])
    angle = 2 * np.pi * frequency * t + phase
    drift = np.cos(angle)[:, None] * frame[:, 0] + np.sin(angle)[:, None] * frame[:, 1]
    return path + (amount * np.sin(np.pi * t))[:, None] * drift


def flowing_edge(a, b, heading, curve, irregularity, phase) -> np.ndarray:
    """A curve from a to b that sets off along `heading`, so a limb carries on from its parent instead of kinking."""
    delta = b - a
    length = float(np.linalg.norm(delta))
    u = delta / length
    blend = min(1., curve * 2.)
    start = co.unit(u * (1 - blend) + co.unit(heading) * blend)
    if start @ u < .2:
        start = co.unit(start + u)
    tension = length * (.25 + .9 * curve)
    t = np.linspace(0., 1., PATH_POINTS)[:, None]
    path = ((2*t**3 - 3*t**2 + 1) * a + (t**3 - 2*t**2 + t) * start * tension
            + (-2*t**3 + 3*t**2) * b + (t**3 - t**2) * u * tension)
    return wander(path, irregularity * .07 * length, phase)


def split_cluster(points) -> tuple[np.ndarray, np.ndarray]:
    """Two groups across the widest direction of a cluster. Cutting at the mean, not the median, leaves uneven
    groups: a main limb that carries on and a side limb, as a real tree does."""
    centred = points - points.mean(0)
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    side = centred @ vt[0]
    low = np.flatnonzero(side <= 0)
    high = np.flatnonzero(side > 0)
    if not len(low) or not len(high):
        order = np.arange(len(points))
        return order[:len(order) // 2], order[len(order) // 2:]
    return low, high


def trunk_groups(directions, count, rng) -> list:
    """Shards grouped by the direction they lie in, each group fed by one trunk."""
    picks = [int(rng.integers(len(directions)))]
    for _ in range(count - 1):
        picks.append(int(np.argmin((directions @ directions[picks].T).max(axis=1))))
    centres = directions[picks]
    labels = np.zeros(len(directions), dtype=int)
    for _ in range(12):
        labels = np.argmax(directions @ centres.T, axis=1)
        for j in range(len(centres)):
            if (labels == j).any():
                centres[j] = co.unit(directions[labels == j].mean(0))
    return [np.flatnonzero(labels == j) for j in range(len(centres)) if (labels == j).any()]


def tree_network(hub, anchors, p, floor) -> Network:
    """Few thick trunks that keep forking until each shard has its own twig. A fork's limbs thin by the share of
    shards they carry (Murray's law for real branching), so a heavy limb stays heavy, and no twig fades to a thread."""
    net = Network()
    floor = max(floor, float(p["connector_diameter_min"]) * float(p["_diag"]) / 200. * .3)
    curve, irregularity = float(p["connector_curve"]), float(p["connector_irregularity"])
    taper, fork = float(p["connector_taper"]), float(p["connector_fork"]) / 100.
    tips = np.array([a.tip for a in anchors])
    rng = np.random.default_rng(np.random.SeedSequence([int(p["seed"]), 7031]))
    directions = co.unit(tips - hub)
    groups = trunk_groups(directions, min(int(p["connector_trunks"]), len(anchors)), rng)
    largest = max(len(g) for g in groups)

    def grow(members, start, heading, radius):
        if len(members) == 1:
            anchor = anchors[members[0]]
            if np.linalg.norm(anchor.tip - start) < floor:
                return
            end = min(max(floor, min(radius * taper, anchor.margin * .8)), tip_limit(p))
            path = flowing_edge(start, anchor.tip, heading, curve, irregularity, float(rng.uniform(0, 2 * np.pi)))
            net.limbs.append(Limb(path, radius, end, float(rng.uniform(0, 2 * np.pi)), irregularity))
            grip(net, path, radius, end, anchor, hub, p, floor)
            return
        middle = tips[members].mean(0)
        share = np.clip(fork * float(rng.uniform(.85, 1.15)), .1, .9)
        joint = start + (middle - start) * share
        path = flowing_edge(start, joint, heading, curve, irregularity, float(rng.uniform(0, 2 * np.pi)))
        end = max(floor, radius * .93)
        net.limbs.append(Limb(path, radius, end, float(rng.uniform(0, 2 * np.pi)), irregularity))
        net.joints.append((joint, end))
        onward = path[-1] - path[-2]
        for part in split_cluster(tips[members]):
            grow(members[part], joint, onward, max(floor, end * (len(part) / len(members)) ** (1 / 3)))

    for group in groups:
        radius = float(rng.uniform(p["connector_diameter_min"], p["connector_diameter_max"])) * float(p["_diag"]) / 200.
        radius = max(floor, radius * (len(group) / largest) ** (1 / 3))
        heading = co.unit(directions[group].mean(0))
        net.roots.append((heading, radius))
        grow(group, hub, heading, radius)
    return net


def radial_network(hub, anchors, p, floor) -> Network:
    """One stem per shard, straight or bowed, each with its own thickness."""
    net = Network()
    style = p["connector_style"]
    for anchor in anchors:
        rng = np.random.default_rng(np.random.SeedSequence([int(p["seed"]), anchor.index, 7032]))
        radius = float(rng.uniform(p["connector_diameter_min"], p["connector_diameter_max"])) * float(p["_diag"]) / 200.
        phase = float(rng.uniform(0., 2 * np.pi))
        axis = anchor.tip - hub
        length = float(np.linalg.norm(axis))
        if length < floor:
            continue
        axis /= length
        if style == "organic":
            radius = max(floor, min(radius, length * .12))
            end = min(max(floor, min(radius * float(p["connector_taper"]), anchor.margin * .8)), tip_limit(p))
            irregularity = float(p["connector_irregularity"])
            extent = length * float(p["connector_extent"]) / 100.
            path = co.curve(hub, anchor.tip, hub, axis, extent, float(p["connector_curve"]), irregularity, phase)
            path = wander(path, irregularity * .05 * length, phase)
            net.limbs.append(Limb(path, radius, end, phase, irregularity))
            grip(net, path, radius, end, anchor, hub, p, floor)
        else:
            factor = np.sqrt(2.) if style == "bar" else 1.
            radius = max(floor, min(radius, length * .3, anchor.margin * .6 / factor))
            net.limbs.append(Limb(np.array([hub, anchor.tip]), radius, radius, phase))
        net.roots.append((axis, radius))
    return net


def tip_pool(shard, count, thickness) -> tuple[np.ndarray, np.ndarray]:
    """Evenly spread places to land on a shard, with their room to its rim."""
    number = int(np.clip(16 * count, 48, 160))
    area = shard.area_faces
    sequence = np.arange(number) + .5
    ids = np.searchsorted(np.cumsum(area) / area.sum(), sequence / number)
    ids = np.minimum(ids, len(area) - 1)
    root = np.sqrt((sequence * .61803398875) % 1)
    v = (sequence * .41421356237) % 1
    surface = np.einsum("ij,ijk->ik", np.c_[1 - root, root * (1 - v), root * v], shard.triangles[ids])
    margins = co.edge_clearance(shard, surface)
    if not margins.any():
        margins = np.full(number, float(np.sqrt(shard.area)))
    return surface - shard.face_normals[ids] * thickness * .5, margins


def grip(net, stem, radius, end, anchor, hub, p, floor) -> None:
    """Extra branches: finer limbs that leave a stem and spread over its shard, like roots gripping a stone."""
    bp = dict(p)
    requested = branches.requested_count(bp, anchor.index)
    net.requested += requested
    spread = anchor.width * float(p["connector_spread"]) / 100.
    if not requested or spread <= floor:
        return
    pool, margins = tip_pool(anchor.shard, requested, float(p["_thickness"]))
    axis = co.unit(stem[-1] - stem[0])
    frame = co.basis(axis)
    taper = float(p["connector_taper"])
    ratio = float(p["connector_branch_thickness"])
    density = min(1., np.sqrt(4. / requested))
    rng = np.random.default_rng(np.random.SeedSequence([int(p["seed"]), anchor.index, 7033]))
    phase = float(rng.uniform(0., 2 * np.pi))
    paths, levels, radii = [stem], [0], [radius]
    used = [anchor.tip]
    length = float(np.linalg.norm(stem[-1] - stem[0]))
    extent = max(spread * 1.25, length * float(p["connector_extent"]) / 100.)
    irregularity = float(p["connector_irregularity"])
    for i, spec in enumerate(branches.origin_plan(bp, anchor.index, requested)):
        parents = [j for j, level in enumerate(levels) if 0 < level < int(p["connector_branch_levels"])]
        parent = parents[min(int(spec["parent_draw"] * len(parents)), len(parents) - 1)] if parents and i % 4 else 0
        junction, fraction = branches.path_point(paths[parent], spec["fraction"])
        scale = 1 - (1 - end / radius) * fraction
        limb_radius = min(radii[parent] * scale * ratio, radius * ratio * density / np.sqrt(levels[parent] + 1))
        tip_radius = limb_radius * taper
        azimuth = phase + i * np.pi * (3 - np.sqrt(5)) + spec["angle_jitter"]
        reach = spread * np.sqrt((i + .5) / requested)
        desired = anchor.tip + reach * (np.cos(azimuth) * frame[:, 0] + np.sin(azimuth) * frame[:, 1])
        spacing = max(tip_radius * 3., spread * min(.25, .9 / np.sqrt(requested)))
        for j in np.argsort(np.linalg.norm(pool - desired, axis=1), kind="stable")[:24]:
            tip = pool[j]
            if margins[j] < tip_radius * 1.2 or min(np.linalg.norm(tip - t) for t in used) < spacing:
                continue
            distance = float(np.linalg.norm(tip - junction))
            if distance < floor * 4:
                continue
            start = max(floor, min(limb_radius, distance * .12))
            path = co.curve(junction, tip, stem[0], axis, extent, float(p["connector_curve"]) * .7, irregularity, azimuth)
            path = wander(path, irregularity * .05 * distance, azimuth)
            net.limbs.append(Limb(path, start, min(max(floor, start * taper), tip_limit(p)), azimuth, irregularity))
            paths.append(path)
            levels.append(levels[parent] + 1)
            radii.append(start)
            used.append(tip)
            net.branches += 1
            break


def limb_solid(limb, style, segments):
    if style != "organic":
        return co.beam(limb.path[0], limb.path[-1], limb.r0, style, segments)
    return co.tube(limb.path, limb.r0, max(.05, limb.r1 / limb.r0), limb.irregularity, limb.phase, segments)


# The hub ---------------------------------------------------------------------------------------------------------

def knot_solid(hub, radius, roots, seed):
    """A lumpy, seeded mass that swells where limbs leave it, so they grow out of it and are not stuck into it."""
    rng = np.random.default_rng(np.random.SeedSequence([int(seed), 7034]))
    ball = trimesh.creation.icosphere(subdivisions=4, radius=1.)
    directions = np.asarray(ball.vertices)
    lumps = np.zeros(len(directions))
    for _ in range(7):
        pull = co.unit(rng.normal(size=3))
        lumps += float(rng.uniform(.5, 1.)) * np.sin(float(rng.uniform(2.5, 6.)) * (directions @ pull) + float(rng.uniform(0, 6.28)))
    lumps /= max(float(np.abs(lumps).max()), 1e-9)
    swell = np.zeros(len(directions))
    if roots:
        widest = max(r for _, r in roots)
        for direction, r in sorted(roots, key=lambda row: -row[1])[:24]:
            swell = np.maximum(swell, (r / widest) * np.exp((directions @ direction - 1.) / .05))
    distance = radius * np.maximum(.55, 1. + .2 * lumps + .55 * swell)
    return co.from_arrays(hub + directions * distance[:, None], ball.faces)


def miniature(mesh, hub, radius, diag) -> trimesh.Trimesh:
    """A small copy of the model around the hub. It is scaled about the hub when the hub lies inside the model, so
    the limbs start inside the copy as well; a hub moved outside takes the copy with it."""
    vertices, faces = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    if len(faces) > MINIATURE_FACES:
        vertices, faces = gm.simplify_faces(vertices, faces, MINIATURE_FACES)
    inside = mesh.is_volume and bool(mesh.contains([hub])[0])
    centre = hub if inside else deep_point(mesh)
    scale = 4. * radius / max(diag, 1e-12)
    return trimesh.Trimesh(hub + (np.asarray(vertices, dtype=float) - centre) * scale, np.asarray(faces), process=False)


# Everything together ---------------------------------------------------------------------------------------------

def part_kisses(mesh, diag: float) -> trimesh.Trimesh:
    """A boolean can leave two surfaces touching along an edge, with a copy of each vertex for either side. Closed as
    it stands, but a saved file welds the copies and puts four faces on one edge. A tiny step into each copy's own
    faces really parts them: 2 parts in a million of the diagonal, and always more than 32-bit rounding undoes."""
    V, F = np.array(mesh.vertices, dtype=float), np.asarray(mesh.faces)
    _, inverse, counts = np.unique(V.astype(np.float32), axis=0, return_inverse=True, return_counts=True)
    copies = np.flatnonzero(counts[np.asarray(inverse).reshape(-1)] > 1)
    if not len(copies):
        return mesh
    centres = np.zeros_like(V)
    np.add.at(centres, F.ravel(), np.repeat(V[F].mean(1), 3, axis=0))
    uses = np.maximum(np.bincount(F.ravel(), minlength=len(V)), 1)
    step = max(diag * 2e-6, 8. * float(np.spacing(np.float32(np.abs(V).max()))))
    V[copies] += co.unit(centres[copies] / uses[copies, None] - V[copies]) * step
    return trimesh.Trimesh(V, F, process=False)


def seal(solid, diag: float) -> tuple[trimesh.Trimesh, bool]:
    """(the mesh of a solid, whether it stays closed in a saved file with 32-bit coordinates, as STL has). This tool
    keeps the model's own units, so tolerances follow the model's size."""
    def settle(candidate):
        mesh = part_kisses(co.to_mesh(candidate), diag)
        saved = gm.clean(trimesh.Trimesh(mesh.vertices.astype(np.float32), mesh.faces.copy(), process=False))
        return mesh, saved.is_volume

    first, closed = settle(solid)
    if closed:
        return first, True
    pieces = len(solid.decompose())
    reset = co.from_arrays(first.vertices, first.faces)
    for tolerance in (2e-7, 5e-7, 1e-6, 2e-6, 5e-6):      # slivers that rounding collapses; at most 5 ppm: invisible
        fixed = reset.simplify(tolerance * diag)
        if fixed.status() == mf.Error.NoError and len(fixed.decompose()) == pieces:
            mesh, closed = settle(fixed)
            if closed:
                return mesh, True
    return first, False


@dataclass
class Joined:
    combined: object
    network: object
    parts: list
    connected: list
    hub: np.ndarray
    hub_radius: float
    thickness: float
    limbs: int
    branches: int
    requested: int
    solid: bool
    notes: list = field(default_factory=list)


def join(mesh, shards, p, ctx=None):
    """The shards plus their connectors, or None when the percentage rounds down to no shard at all."""
    validate(p)
    diag = float(np.linalg.norm(mesh.extents)) or 1.
    notes = []

    def say(fraction, text):
        if ctx:
            ctx.check_cancel()
            ctx.progress(fraction, text)

    def warn(text):
        notes.append(text)
        if ctx:
            ctx.warn(text)

    say(.74, "placing the hub")
    hub = hub_point(mesh, p, diag)
    chosen = selected_indices(shards, hub, p)
    if not chosen:
        return None
    thickness = float(p["shard_thickness"]) * diag / 100.
    solids = None
    if thickness > 0:
        say(.76, "thickening shards")
        try:
            solids = [co.from_arrays(*thicken(s, thickness)) for s in shards]
        except ValueError as error:
            warn(f"Shards stayed open and connectors are not fused: {error}.")
            thickness = 0.
    settings = dict(p, _diag=diag, _thickness=thickness)
    floor = diag * 2e-4
    anchors = [anchor_on(i, shards[i], thickness) for i in chosen]
    say(.78, f"growing connectors to {len(anchors)} shards")
    style = p["connector_style"]
    if style == "organic" and p["connector_network"] == "tree":
        net = tree_network(hub, anchors, settings, floor)
    else:
        net = radial_network(hub, anchors, settings, floor)
    if not net.limbs:
        return None
    segments = min(48, int(p["connector_segments"]))
    parts = []
    for i, limb in enumerate(net.limbs):
        if i % 16 == 0:
            say(.78 + .08 * i / len(net.limbs), f"limb {i + 1} / {len(net.limbs)}")
        parts.append(limb_solid(limb, style, segments))
    parts += [mf.Manifold.sphere(r, circular_segments=max(8, segments)).translate(list(point)) for point, r in net.joints]

    # The hub: never so large that it swallows a shard.
    widest = max(r for _, r in net.roots) if net.roots else floor
    room = min(float(np.linalg.norm(s.vertices - hub, axis=1).min()) for s in shards)
    core = p["hub_core"]
    asked = float(p["hub_size"]) * diag / 100.
    radius = asked if core != "none" else widest * (1.5 if style == "bar" else 1.05)
    if core != "none" and room > 0 and radius > room * .9:
        radius = max(widest, room * .9)
        notes.append(f"hub size limited to {radius / diag * 100:.2g}% of the diagonal by the nearest shard")
    loose = None
    if core == "knot":
        parts.append(knot_solid(hub, radius, net.roots, p["seed"]))
    elif core == "miniature":
        loose = miniature(mesh, hub, radius, diag)
        try:
            parts.append(co.from_arrays(loose.vertices, loose.faces))
            loose = None
        except ValueError:
            warn("The miniature is not a closed solid, so it is kept as a separate surface.")
    else:
        parts.append(mf.Manifold.sphere(radius, circular_segments=max(16, segments * 2)).translate(list(hub)))

    say(.87, "joining connectors")
    network = co.union(parts)
    if network.is_empty() or network.status() != mf.Error.NoError:
        raise ValueError("The connectors did not form a valid solid; no output was published.")
    network_mesh = part_kisses(co.to_mesh(network), diag)
    if loose is not None:
        network_mesh = trimesh.util.concatenate([network_mesh, loose])

    if solids is not None:
        say(.9, "fusing shards")
        fused = co.union([network, *(solids[i] for i in chosen)])
        pieces = sum(part.volume() > 0 for part in fused.decompose())
        if pieces > 1:
            warn(f"{pieces - 1} connected pieces did not fuse with the hub; raise Shard thickness or change Seed.")
        whole = co.union([fused, *(s for i, s in enumerate(solids) if i not in set(chosen))])
        combined, closed = seal(whole, diag)
        if not closed:
            warn("The saved file may not be fully closed at very fine joints; thicken the connectors for 3D printing.")
        if loose is not None:
            combined = trimesh.util.concatenate([combined, loose])
        shard_meshes = [co.to_mesh(s) for s in solids]
    else:
        combined = trimesh.util.concatenate([*shards, network_mesh])
        shard_meshes = list(shards)
    if len(combined.faces) > int(p["max_faces"]):
        raise ValueError("The result exceeds Maximum faces. Reduce Pieces, Extra branches or Curve segments.")
    return Joined(combined, network_mesh, shard_meshes, chosen, hub, radius, thickness, len(net.limbs), net.branches,
                  net.requested, solids is not None, notes)
