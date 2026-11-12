"""
The five structures of Skeleton Shell, each a set of wires, plates or frames on the skin (see skin.py, wires.py).

    cells    Voronoi cell walls on the surface: a biomorphic wire net
    trimesh  the dual of those cells, seed to seed: triangles, regular to organic
    flow     long strands along a line field: the form's own curvature, upright, or spirals
    ribs     stations around the form (rods, straps or real ring-plate frames) tied by stringers and a keel
    plate    a sheet of skin pierced by openings, the cells' walls widened into a cut band
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from atlas_toolkit.geom import curvature as cv

from . import wires as wr
from .skin import Skin, chains

MAX_SEEDS = 12_000
AXES = {"up": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
        "across": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
        "depth": ((0, 0, 1), (1, 0, 0), (0, 1, 0))}


@dataclass
class Layout:
    """What a layer settings dictionary asks for, in model units."""
    structure: str
    size: float
    regularity: float = 0.6
    straightness: float = 0.5
    stretch: float = 1.0
    detail: float = 0.0
    missing: float = 0.0
    seed: int = 1
    flow_field: str = "form"
    flow_angle: float = 35.0
    crossing: bool = False
    strand_length: float = 1.0
    min_length: float = 0.1
    wander: float = 0.0
    feature: float = 0.0
    flow_smooth: int = 30
    rib_axis: str = "up"
    rib_profile: str = "frame"
    stringer_spacing: float = 0.0
    stringer_style: str = "radial"
    frame_depth: float = 0.1
    lightening: bool = True
    keel: bool = False
    thickness: float = 0.02
    plate_pattern: str = "cells"
    band: float = 0.03
    rounding: int = 0


@dataclass
class Layer:
    wires: list = field(default_factory=list)
    stringers: list = field(default_factory=list)   # wires always drawn with the layer's wire profile
    solids: list = field(default_factory=list)      # (V, F) closed solids
    plate: np.ndarray | None = None                 # a field on the skin: keep the skin where it is negative
    seeds: np.ndarray | None = None
    notes: list = field(default_factory=list)


def size_field(skin: Skin, size: float, detail: float) -> np.ndarray | None:
    if detail <= 0.0:
        return None
    t = np.clip(skin.bumpiness() * size / 1.5, 0.0, 1.0)
    return size * (1.0 - 0.65 * detail * t)


def seed_cells(skin: Skin, L: Layout, rng) -> tuple:
    """Seeds and the geodesic cells around them. Regularity runs from a random scatter to an even, hexagonal packing."""
    size = size_field(skin, L.size, L.detail)
    area_per_seed = 0.866 * (size if size is not None else np.full(len(skin.V), L.size)) ** 2
    count = int(min((skin.vertex_areas / area_per_seed).sum(), MAX_SEEDS))
    seeds = skin.relax(count, rng, size, L.stretch, iterations=int(round(L.regularity * 10)))
    dist, pred, labels = skin.nearest(skin.metric(size, L.stretch), seeds)
    return seeds, dist, pred, labels


def drop_some(wires: list, missing: float, rng) -> list:
    if missing <= 0.0 or not wires:
        return wires
    keep = rng.random(len(wires)) >= missing
    return [w for w, k in zip(wires, keep) if k]


def cells(skin: Skin, L: Layout, rng) -> Layer:
    seeds, dist, _, labels = seed_cells(skin, L, rng)
    nodes, segments = skin.voronoi(labels, dist)
    out = []
    # node ids stay shared between the runs that meet there, so joints and thickness agree
    for run, closed in chains(len(nodes), segments):
        ends = (-1, -1) if closed else (int(run[0]), int(run[-1]))
        out.append(wr.Wire(nodes[run], closed, ends, follow=1.0 - L.straightness))
    return Layer(wires=drop_some(out, L.missing, rng), seeds=seeds)


def dual_paths(skin: Skin, seeds, dist, pred, labels) -> list:
    """For each pair of neighbouring cells, the shortest path seed to seed through the edge where they meet best."""
    a, b = skin.E[:, 0], skin.E[:, 1]
    cross = np.flatnonzero((labels[a] != labels[b]) & (labels[a] >= 0) & (labels[b] >= 0))
    if not len(cross):
        return []
    low, high = np.minimum(labels[a[cross]], labels[b[cross]]), np.maximum(labels[a[cross]], labels[b[cross]])
    key = low * (len(seeds) + 1) + high
    cost = dist[a[cross]] + dist[b[cross]] + skin.lengths[cross]
    order = np.lexsort((cost, key))
    first = np.r_[True, key[order][1:] != key[order][:-1]]
    best = cross[order[first]]
    out = []

    def back(v):
        path = [int(v)]
        while pred[path[-1]] >= 0:
            path.append(int(pred[path[-1]]))
        return path

    for e in best.tolist():
        one, two = back(a[e]), back(b[e])
        out.append((one[::-1] + two, labels[a[e]], labels[b[e]]))
    return out


def trimesh_net(skin: Skin, L: Layout, rng) -> Layer:
    seeds, dist, pred, labels = seed_cells(skin, L, rng)
    out = []
    for path, u, v in dual_paths(skin, seeds, dist, pred, labels):
        out.append(wr.Wire(skin.V[path], False, (int(u), int(v)), follow=1.0 - L.straightness))
    return Layer(wires=drop_some(out, L.missing, rng), seeds=seeds)


# ------------------------------------------------------------------------------------------------- flow
def _rotate_in_plane(d: np.ndarray, n: np.ndarray, angle) -> np.ndarray:
    angle = np.asarray(angle, np.float64)
    c, s = np.cos(angle), np.sin(angle)
    if angle.ndim == 1:
        c, s = c[:, None], s[:, None]
    return d * c + np.cross(n, d) * s


def line_fields(skin: Skin, L: Layout) -> list:
    """The per-face direction fields of the families to trace."""
    n = skin.face_normals
    if L.flow_field == "form":
        tensor = skin.form_tensor(L.flow_smooth * 4)
        # a slight preference for running up the form settles the field where the surface barely bends
        level = np.trace(tensor, axis1=1, axis2=2).mean() * 0.25
        tensor = tensor + level * np.diag([1.0, 1.0, 0.0])
        pair = skin.face_tensors(tensor)
        fields = [pair[:, 0], pair[:, 1]]
    else:
        # the body's general lean, not its lumps: straight lines over a rough skin stay straight
        up = np.array([0.0, 0.0, 1.0])
        calm = cv._unit(skin.smooth(skin.N, 40)[skin.F].mean(1))
        along = up - (calm @ up)[:, None] * calm
        weak = np.linalg.norm(along, axis=1) < 1e-3
        along[weak] = np.cross(calm[weak], [1.0, 0.0, 0.0])
        along = cv._unit(along - cv._dot(along, n)[:, None] * n)
        if L.flow_field == "upright":
            fields = [along, cv._unit(np.cross(n, along))]
        else:       # spiral: two opposite hands
            angle = np.radians(L.flow_angle)
            fields = [_rotate_in_plane(along, n, np.full(len(n), angle)),
                      _rotate_in_plane(along, n, np.full(len(n), -angle))]
    if L.wander > 0.0:
        centres = skin.V[skin.F].mean(1)
        turn = wr.noise3(centres, max(L.size * 2.5, skin.height * 0.08), L.seed + 17) * np.radians(70.0) * L.wander
        fields = [cv._unit(_rotate_in_plane(f, n, turn)) for f in fields]
    return fields[:2] if L.crossing else fields[:1]


def _sectors(normals: np.ndarray) -> np.ndarray:
    """Which of six directions a normal faces, so the front and back of something thin don't block each other."""
    axis = np.abs(normals).argmax(1)
    return axis * 2 + (normals[np.arange(len(normals)), axis] < 0)


def flow(skin: Skin, L: Layout, rng, progress=None) -> Layer:
    spacing = L.size
    want = skin.area / (spacing * max(L.strand_length * 0.6, spacing))
    budget = int(np.clip(want * 25.0, 400, 8000))
    bump = skin.bumpiness()
    face_bump = bump[skin.F].mean(1)
    out = []
    for family, field_ in enumerate(line_fields(skin, L)):
        p = skin.face_areas / skin.face_areas.sum()
        if L.feature > 0.0:
            b = face_bump / (face_bump.max() or 1.0)
            p = p * (b ** (1.0 + 4.0 * L.feature) + 1e-4)
            p = p / p.sum()
        faces = rng.choice(len(skin.F), size=budget, p=p)
        start = skin.V[skin.F[faces]].mean(1)
        forward = field_[faces]
        half = L.strand_length * 0.5
        walker, points = skin.walk(np.r_[faces, faces], np.vstack([start, start]), np.vstack([forward, -forward]),
                                   field_, half, progress=progress)
        order = np.argsort(walker, kind="stable")
        walker, points = walker[order], points[order]
        bounds = np.searchsorted(walker, np.arange(2 * budget + 1))
        # greedy: strongest (or simply first) seeds claim their room
        rank = np.argsort(-face_bump[faces]) if L.feature > 0.0 else np.arange(budget)
        out += _accept(skin, points, bounds, rank, budget, spacing, L, face_bump[faces], family)
    if not out:
        return Layer(notes=["no strand was long enough: lower Shortest strand or Spacing"])
    return Layer(wires=drop_some(out, L.missing, rng))


def _accept(skin, points, bounds, rank, budget, spacing, L, seed_bump, family) -> list:
    """Evenly spaced strands: a voxel grid per surface direction marks the room each accepted strand claims."""
    from scipy.spatial import cKDTree
    voxel = max(spacing * 0.5, skin.height / 300.0)
    lo = skin.lo - voxel * 2.0
    dims = np.ceil((skin.hi - lo) / voxel).astype(np.int64) + 3
    grid = np.zeros((6, *dims.tolist()), bool)
    out = []
    top = float(seed_bump.max()) or 1.0
    sectors_all = _sectors(skin.N[cKDTree(skin.V).query(points, workers=-1)[1]])

    def cells_of(P):
        return np.floor((P - lo) / voxel).astype(np.int64)

    def clear_run(P, S):
        """How many points from the seed outward are free."""
        ids = cells_of(P)
        blocked = grid[S, ids[:, 0], ids[:, 1], ids[:, 2]]
        hit = np.flatnonzero(blocked)
        return int(hit[0]) if len(hit) else len(P)

    for k in rank.tolist():
        ahead = points[bounds[k]:bounds[k + 1]]
        behind = points[bounds[k + budget]:bounds[k + budget + 1]]
        if len(ahead) < 2 and len(behind) < 2:
            continue
        s_ahead = sectors_all[bounds[k]:bounds[k + 1]]
        s_behind = sectors_all[bounds[k + budget]:bounds[k + budget + 1]]
        run_a = clear_run(ahead, s_ahead)
        run_b = clear_run(behind, s_behind)
        if run_a + run_b < 3:
            continue
        line = np.vstack([behind[:run_b][::-1], ahead[1:run_a]]) if run_a > 1 else behind[:run_b][::-1]
        if len(line) < 2 or wr.arc_length(line)[-1] < L.min_length:
            continue
        ids = np.vstack([cells_of(ahead[:run_a]), cells_of(behind[:run_b])])
        sectors = np.r_[s_ahead[:run_a], s_behind[:run_b]]
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    grid[sectors, np.clip(ids[:, 0] + dx, 0, dims[0] - 1), np.clip(ids[:, 1] + dy, 0, dims[1] - 1),
                         np.clip(ids[:, 2] + dz, 0, dims[2] - 1)] = True
        weight = 1.0 + 1.2 * L.feature * float(seed_bump[k] / top)
        out.append(wr.Wire(line, False, (-1, -1), weight=weight))
    return out


# ------------------------------------------------------------------------------------------------- ribs
def _levels(lo: float, hi: float, spacing: float) -> np.ndarray:
    span = hi - lo
    count = max(1, int(round(span / spacing)))
    return lo + (np.arange(count) + 0.5) * span / count


def ring_plates(skin: Skin, axis: str, levels, L: Layout, rng) -> list:
    """Real frames: for each station, a flat ring cut from the model's own section, with lightening holes."""
    import manifold3d as mf
    u, v, w = (np.asarray(a, np.float64) for a in AXES[axis])
    field_ = skin.V @ w
    solids = []
    for level in levels:
        loops = [P for P, closed in skin.isolines(field_, float(level))
                 if closed and len(P) >= 8 and wr.arc_length(P, True)[-1] >= L.frame_depth * 2.0]
        if not loops:
            continue
        contours = [np.stack([P @ u, P @ v], 1) for P in loops]
        section = mf.CrossSection(contours, mf.FillRule.EvenOdd)
        for part in section.decompose():
            area = float(part.area())
            if area <= 0.0:
                continue
            radius = float(np.sqrt(area / np.pi))
            depth = min(L.frame_depth, 0.55 * radius)
            inner = part.offset(-depth, mf.JoinType.Round, 2.0, 24)
            ring = part - inner
            if ring.is_empty():
                continue
            if L.lightening and depth >= L.frame_depth * 0.8 and not inner.is_empty():
                ring = _lighten(ring, part, depth, mf)
            ring = ring.simplify(depth * 0.01)
            solid = mf.Manifold.extrude(ring, L.thickness).translate((0.0, 0.0, float(level) - L.thickness * 0.5))
            mesh = solid.to_mesh()
            if not len(mesh.tri_verts):
                continue
            P = np.asarray(mesh.vert_properties, np.float64)[:, :3]
            solids.append((np.outer(P[:, 0], u) + np.outer(P[:, 1], v) + np.outer(P[:, 2], w),
                           np.asarray(mesh.tri_verts, np.int64)))
    return solids


def _lighten(ring, part, depth: float, mf):
    """Round holes down the middle of a frame, as in an old airframe's formers."""
    middle = part.offset(-depth * 0.5, mf.JoinType.Round, 2.0, 24)
    radius = depth * 0.27
    pitch = depth * 1.05
    holes = []
    for loop in middle.to_polygons():
        loop = np.asarray(loop, np.float64)
        if len(loop) < 3:
            continue
        s = wr.arc_length(np.c_[loop, np.zeros(len(loop))], closed=True)
        total = float(s[-1])
        count = int(total / pitch)
        if count < 3:
            continue
        closed = np.vstack([loop, loop[:1]])
        at = (np.arange(count) + 0.5) * total / count
        xs, ys = np.interp(at, s, closed[:, 0]), np.interp(at, s, closed[:, 1])
        for x, y in zip(xs, ys):
            holes.append(mf.CrossSection.circle(radius, 14).translate((float(x), float(y))))
    return ring - mf.CrossSection.batch_boolean(holes, mf.OpType.Add) if holes else ring


def ribs(skin: Skin, L: Layout, rng) -> Layer:
    layer = Layer()
    planar = L.rib_axis in AXES
    if planar:
        w = np.asarray(AXES[L.rib_axis][2], np.float64)
        field_ = skin.V @ w
    else:       # rings: distance over the surface from the crown
        crown = np.asarray([int(np.argmax(skin.V[:, 2]))])
        field_ = skin.nearest(skin.metric(), crown)[0]
        field_ = np.where(np.isfinite(field_), field_, np.nanmax(field_[np.isfinite(field_)]))
    levels = _levels(float(field_.min()), float(field_.max()), L.size)
    if planar and L.rib_profile == "frame":
        layer.solids += ring_plates(skin, L.rib_axis, levels, L, rng)
    else:
        for level in levels:
            for P, closed in skin.isolines(field_, float(level)):
                if wr.arc_length(P, closed)[-1] >= L.size * 2.0:
                    layer.wires.append(wr.Wire(P, closed))
    if L.keel and planar:
        keel_axis = "across" if L.rib_axis != "across" else "depth"
        c = 0.5 * (skin.lo + skin.hi) @ np.asarray(AXES[keel_axis][2], np.float64)
        keel = Layout(**{**L.__dict__, "lightening": L.lightening})
        layer.solids += ring_plates(skin, keel_axis, [float(c)], keel, rng)
    if L.stringer_spacing > 0.0:
        layer.stringers += _stringers(skin, L)
    layer.wires = drop_some(layer.wires, L.missing, rng)
    layer.stringers = drop_some(layer.stringers, L.missing, rng)
    return layer


def _stringers(skin: Skin, L: Layout) -> list:
    out = []
    axis = np.asarray(AXES[L.rib_axis if L.rib_axis in AXES else "up"][2], np.float64)
    centre = 0.5 * (skin.lo + skin.hi)
    if L.stringer_style == "radial":
        a = np.cross(axis, [1.0, 0.0, 0.0] if abs(axis[0]) < 0.9 else [0.0, 1.0, 0.0])
        a /= np.linalg.norm(a)
        b = np.cross(axis, a)
        reach = float(np.linalg.norm(skin.V - centre, axis=1).max())
        count = max(2, int(round(np.pi * reach * 0.7 / L.stringer_spacing)))
        for k in range(count):
            angle = np.pi * (k + 0.5) / count
            normal = a * np.cos(angle) + b * np.sin(angle)
            out += [wr.Wire(P, closed) for P, closed in skin.isolines((skin.V - centre) @ normal, 0.0)
                    if wr.arc_length(P, closed)[-1] >= L.stringer_spacing * 2.0]
    else:
        side = np.asarray([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.asarray([0.0, 0.0, 1.0])
        field_ = skin.V @ side
        for level in _levels(float(field_.min()), float(field_.max()), L.stringer_spacing):
            out += [wr.Wire(P, closed) for P, closed in skin.isolines(field_, float(level))
                    if wr.arc_length(P, closed)[-1] >= L.stringer_spacing * 2.0]
    return out


# ------------------------------------------------------------------------------------------------- plate
def plate(skin: Skin, L: Layout, rng) -> Layer:
    """Cut openings: the skin stays within half a band of the cells' walls (or of the triangles between their seeds)."""
    from scipy.spatial import cKDTree
    structure = Layout(**{**L.__dict__, "missing": L.missing})
    base = cells(skin, structure, rng) if L.plate_pattern == "cells" else trimesh_net(skin, structure, rng)
    dressing = wr.Dress(wire=L.band, step=max(L.band * 0.35, skin.edge * 0.75), smooth=3, seed=L.seed)
    lines = wr.dress(base.wires, skin, dressing)
    if not lines:
        return Layer(notes=["the pattern came out empty: lower Cell size"])
    samples = np.vstack([w.points for w in lines])
    distance, _ = cKDTree(samples).query(skin.V, workers=-1)
    level = L.band * 0.5
    if L.rounding > 0:
        # blur the distance, then cut at the level that keeps the same share of metal: corners round off and joints
        # fatten, while the band stays the width that was asked for
        share = float(skin.vertex_areas[distance < level].sum() / skin.vertex_areas.sum())
        passes = int(L.rounding * max(L.band / max(skin.edge, 1e-9), 1.0) ** 2)
        distance = skin.smooth(distance, min(passes, 400))
        order = np.argsort(distance)
        reached = np.cumsum(skin.vertex_areas[order]) / skin.vertex_areas.sum()
        level = float(distance[order[min(np.searchsorted(reached, share), len(order) - 1)]])
    return Layer(plate=distance - level, seeds=base.seeds)


def build(skin: Skin, L: Layout, rng, progress=None) -> Layer:
    if L.structure == "cells":
        return cells(skin, L, rng)
    if L.structure == "trimesh":
        return trimesh_net(skin, L, rng)
    if L.structure == "flow":
        return flow(skin, L, rng, progress)
    if L.structure == "ribs":
        return ribs(skin, L, rng)
    if L.structure == "plate":
        return plate(skin, L, rng)
    raise ValueError(f"unknown structure {L.structure!r}")


def wanted_edge(L: Layout) -> float:
    """The working mesh's edge length this structure needs."""
    if L.structure == "plate":
        return min(L.band / 3.0, L.size / 10.0)
    if L.structure == "flow":
        return L.size / 4.0
    return L.size / 9.0
