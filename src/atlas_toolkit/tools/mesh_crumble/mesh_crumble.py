"""
mesh_crumble.py - Fracture part of a closed model into solid blocks, hollow it where they were, and let them fall.

1. Area: a side of the model, a round patch on it, a painted area or everything, measured as a signed distance from
   the edge of the area (positive inside), roughened by smooth noise.
2. Cells: convex cells fill the area (Voronoi for rock, shards and slabs, a staggered lattice for bricks). A cell
   whose centre lies in the area, and not deeper than Hollow depth, becomes blocks. Some split again into rubble.
3. Blocks: each cell, slightly shrunk and chipped, is intersected with the model with exact booleans, through a
   k-d tree of boxes so one cut never touches the whole mesh. The body loses the cells, so what is left is a jagged
   crater with real broken faces. Cells just outside the area leave cracks: tapered slots cut along their walls.
4. Collapse: the blocks are released in a staggered order and fall under gravity, colliding with each other, the body
   and the floor (physics.py). The moment shown is simply how long the simulation ran.
5. Result: loose closed solids, or one fused solid with thin bridges to whatever would float.

All lengths are canonical (a model 2 tall, centred, front at -Y, up +Z); the engine converts back to model units.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import manifold3d as mf
import numpy as np

from . import physics

HEIGHT = 2.0
SETTLE_SECONDS = 2.6        # after the last release, blocks need about this long to come to rest
SIDES = {"front": 0.0, "right": 90.0, "back": 180.0, "left": 270.0}
MAX_CELLS = 120_000


def unit(v, fallback=(0.0, 0.0, 1.0)):
    v = np.asarray(v, dtype=np.float64)
    length = np.linalg.norm(v, axis=-1, keepdims=True)
    return np.where(length > 1e-12, v / np.maximum(length, 1e-12), np.asarray(fallback, dtype=np.float64))


@dataclass
class Report:
    cells: int = 0
    blocks: int = 0
    rubble: int = 0
    released: int = 0
    falling: int = 0
    resting: int = 0
    clinging: int = 0
    lost: int = 0
    tiny: int = 0
    bridges: int = 0
    dropped: int = 0
    pieces: int = 0
    cracks: int = 0
    seconds: float = 0.0
    hollowed: float = 0.0
    notes: list = field(default_factory=list)


@dataclass
class Cell:
    centre: np.ndarray
    normals: np.ndarray
    offsets: np.ndarray
    vertices: np.ndarray
    score: float = 0.0


@dataclass
class Result:
    body: object            # Manifold or None
    blocks: list            # Manifolds where they ended up
    fused: object           # Manifold when Fuse is on
    plate: object           # Manifold or None
    report: Report
    moved: np.ndarray       # how far each block travelled


# ----------------------------------------------------------------------------- the area

def direction(p) -> np.ndarray:
    if p["side"] == "top":
        return np.array([0.0, 0.0, 1.0])
    a = math.radians(SIDES[p["side"]] + p["side_turn"])
    return np.array([math.sin(a), -math.cos(a), 0.0])


class Noise:
    """Smooth seeded noise about -1..1: a sum of a few plane waves."""

    def __init__(self, rng, wavelength: float, waves: int = 8):
        self.k = unit(rng.normal(size=(waves, 3))) * (2 * math.pi / max(wavelength, 1e-6)) * rng.uniform(0.6, 1.8, (waves, 1))
        self.phase = rng.uniform(0, 2 * math.pi, waves)

    def __call__(self, P):
        return np.sin(np.asarray(P) @ self.k.T + self.phase).sum(axis=-1) * math.sqrt(2.0 / len(self.phase))


class Area:
    """score(P) > 0 inside the area, in canonical lengths, with its edge roughened."""

    def __init__(self, V, F, centroid, p, selected, rng):
        self.mode = p["area"]
        self.d = direction(p)
        self.rough = HEIGHT * p["edge_rough"] / 100.0
        self.noise = Noise(rng, max(HEIGHT * p["block_size"] / 100.0 * 2.5, 0.05))
        self.centroid = np.asarray(centroid, float)
        if self.mode == "side":
            along = V @ self.d
            lo, hi = float(along.min()), float(along.max())
            self.shape = p["area_shape"]
            if self.shape == "half":
                self.plane = hi - (hi - lo) * p["coverage"] / 100.0
            else:
                self.radius = HEIGHT * p["patch_radius"] / 100.0
                self.centre = self._patch_centre(V, F, p, lo, hi)
        elif self.mode == "paint":
            from scipy.spatial import cKDTree
            centres = V[F].mean(axis=1)
            normals = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
            if selected.all():
                self.mode = "whole"
            self.tree_in = cKDTree(centres[selected])
            self.tree_out = cKDTree(centres[~selected]) if not selected.all() else None
            self.d = unit(normals[selected].sum(axis=0))

    def _patch_centre(self, V, F, p, lo, hi) -> np.ndarray:
        """Where a ray from outside on this side, at the chosen height, first meets the model."""
        origin = self.centroid + self.d * (hi - lo + 1.0)
        if p["side"] == "top":
            across = np.array([1.0, 0.0, 0.0])
            origin = origin + across * (HEIGHT * p["patch_height"] / 100.0)
        else:
            origin = origin + np.array([0.0, 0.0, HEIGHT * p["patch_height"] / 100.0])
        from atlas_toolkit.geom.raycast import Caster
        caster = Caster(np.ascontiguousarray(V, dtype=np.float64), np.ascontiguousarray(F, dtype=np.int64))
        distance = float(caster.intersect(origin[None], -self.d[None])[0][0])
        if not np.isfinite(distance):           # the ray misses: the nearest vertex stands in
            return V[np.argmin(np.linalg.norm(V - origin, axis=1))]
        return origin - self.d * distance

    def score(self, P) -> np.ndarray:
        P = np.asarray(P, dtype=np.float64)
        if self.mode == "whole":
            s = np.full(len(P), HEIGHT)
        elif self.mode == "paint":
            near_in = self.tree_in.query(P)[0]
            s = self.tree_out.query(P)[0] - near_in
        elif self.shape == "half":
            s = P @ self.d - self.plane
        else:
            s = self.radius - np.linalg.norm(P - self.centre, axis=1)
        return s + self.rough * self.noise(P)


# ----------------------------------------------------------------------------- cells

def signed_distance(V, F, P) -> np.ndarray:
    import igl
    P = np.ascontiguousarray(np.atleast_2d(P), dtype=np.float64)
    return np.asarray(igl.signed_distance(P, np.ascontiguousarray(V, dtype=np.float64),
                                          np.ascontiguousarray(F, dtype=np.int64),
                                          sign_type=igl.SIGNED_DISTANCE_TYPE_FAST_WINDING_NUMBER)[0], dtype=np.float64)


def bound_planes(lo, hi) -> tuple[np.ndarray, np.ndarray]:
    normals = np.vstack([np.eye(3), -np.eye(3)])
    return normals, np.r_[hi, -lo]


def polytope(normals, offsets, inside) -> np.ndarray | None:
    """The corners of {x: n·x <= d}, or None if it is empty or flat."""
    from scipy.spatial import HalfspaceIntersection, QhullError
    margin = offsets - normals @ inside
    if (margin <= 1e-9).any():
        return None
    try:
        corners = HalfspaceIntersection(np.column_stack([normals, -offsets]), inside).intersections
    except (QhullError, ValueError):
        return None
    corners = corners[np.isfinite(corners).all(axis=1)]
    return corners if len(corners) >= 4 else None


def shape_matrix(style: str, rng) -> np.ndarray:
    """Voronoi cells of seeds spread evenly in stretched space come out elongated or flat in real space."""
    if style == "slabs":
        return np.diag([0.75, 0.75, 1.9])
    if style == "shards":
        axis = unit(rng.normal(size=3))
        axis = unit(axis + np.array([0.0, 0.0, 1.2]))       # mostly upright, as breaks in a falling mass run
        return np.eye(3) - (1.0 - 0.42) * np.outer(axis, axis) + 0.35 * (np.eye(3) - np.outer(axis, axis))
    return np.eye(3)


def voronoi_cells(V, F, lo, hi, size: float, jitter: float, style: str, rng, classify):
    """Cells of a jittered grid of seeds. classify(P, sd) returns who needs a cell: 0 no, 1 cracks, 2 block."""
    from scipy.spatial import Delaunay
    A = shape_matrix(style, rng)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    Q = corners @ A.T
    qlo, qhi = Q.min(axis=0) - size, Q.max(axis=0) + size
    counts = np.maximum(np.ceil((qhi - qlo) / size).astype(int), 2)
    if int(np.prod(counts)) > MAX_CELLS * 8:
        raise ValueError("Block size is too small for this model: raise it.")
    axes = [qlo[i] + (np.arange(counts[i]) + 0.5) * size for i in range(3)]
    grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
    grid = grid + rng.uniform(-0.5, 0.5, grid.shape) * size * jitter
    Ainv = np.linalg.inv(A)
    P = grid @ Ainv.T
    sd = signed_distance(V, F, P)
    keep = sd < 1.8 * size * float(np.abs(Ainv).max())
    grid, P, sd = grid[keep], P[keep], sd[keep]
    if len(P) < 8:
        raise ValueError("Block size is too large for this model: lower it.")
    if len(P) > MAX_CELLS:
        raise ValueError("Block size is too small for this model: raise it.")
    kinds, scores = classify(P, sd)
    tri = Delaunay(grid)
    indptr, neighbours = tri.vertex_neighbor_vertices
    bn, bd = bound_planes(lo - size * 2, hi + size * 2)
    out = [[], []]
    for i in np.flatnonzero(kinds):
        js = neighbours[indptr[i]:indptr[i + 1]]
        n = (grid[js] - grid[i]) @ A            # the bisector in stretched space, as a plane in real space
        d = ((grid[js] ** 2).sum(axis=1) - (grid[i] ** 2).sum()) * 0.5
        length = np.linalg.norm(n, axis=1)
        normals = np.vstack([n / length[:, None], bn])
        offsets = np.r_[d / length, bd]
        corners = polytope(normals, offsets, P[i])
        if corners is not None:
            out[kinds[i] - 1].append(Cell(P[i], normals, offsets, corners, float(scores[i])))
    return out[1], out[0], len(P)


def brick_cells(V, F, lo, hi, size: float, jitter: float, ratio: float, facing: np.ndarray, rng, classify):
    """Staggered courses: long bricks along the surface, one course on the next, like masonry."""
    z = np.array([0.0, 0.0, 1.0])
    d = unit(np.array([facing[0], facing[1], 0.0]), (0.0, -1.0, 0.0))
    t = np.cross(z, d)
    tall = size * 0.62
    deep = size * 1.1
    long = size * ratio * 0.9
    centres, halves = [], []
    R = np.stack([t, d, z])
    corners = np.array([[x, y, w] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for w in (lo[2], hi[2])]) @ R.T
    qlo, qhi = corners.min(axis=0) - size, corners.max(axis=0) + size
    for k in range(int(np.ceil((qhi[2] - qlo[2]) / tall))):
        for j in range(int(np.ceil((qhi[1] - qlo[1]) / deep))):
            x = qlo[0] - (0.5 * long if (k + j) % 2 else 0.0) - rng.uniform(0.0, 0.4) * long * jitter
            while x < qhi[0]:
                width = long * (1.0 + jitter * rng.uniform(-0.45, 0.45))
                centres.append((x + width * 0.5, qlo[1] + (j + 0.5) * deep, qlo[2] + (k + 0.5) * tall))
                halves.append((width * 0.5, deep * 0.5, tall * 0.5))
                x += width
    if len(centres) > MAX_CELLS * 4:
        raise ValueError("Block size is too small for this model: raise it.")
    C = np.asarray(centres) @ R
    H = np.asarray(halves)
    sd = signed_distance(V, F, C)
    keep = sd < 1.8 * long
    C, H, sd = C[keep], H[keep], sd[keep]
    if len(C) < 8:
        raise ValueError("Block size is too large for this model: lower it.")
    kinds, scores = classify(C, sd)
    normals = np.vstack([R, -R])
    out = [[], []]
    for i in np.flatnonzero(kinds):
        offsets = np.r_[normals[:3] @ C[i] + H[i], normals[3:] @ C[i] + H[i]]
        signs = np.array([[x, y, w] for x in (-1, 1) for y in (-1, 1) for w in (-1, 1)], dtype=float) * H[i]
        out[kinds[i] - 1].append(Cell(C[i], normals, offsets, C[i] + signs @ R, float(scores[i])))
    return out[1], out[0], len(C)


def split_cell(cell: Cell, count: int, rng) -> list[Cell]:
    """A block broken again: Voronoi cells of random points inside it."""
    lo, hi = cell.vertices.min(axis=0), cell.vertices.max(axis=0)
    points = rng.uniform(lo, hi, (count * 6, 3))
    points = points[(points @ cell.normals.T <= cell.offsets).all(axis=1)][:count]
    if len(points) < 2:
        return [cell]
    pieces = []
    for i, q in enumerate(points):
        others = np.delete(points, i, axis=0)
        n = others - q
        d = ((others ** 2).sum(axis=1) - (q ** 2).sum()) * 0.5
        length = np.linalg.norm(n, axis=1)
        normals = np.vstack([cell.normals, n / length[:, None]])
        offsets = np.r_[cell.offsets, d / length]
        corners = polytope(normals, offsets, q)
        if corners is not None:
            pieces.append(Cell(q, normals, offsets, corners, cell.score))
    return pieces or [cell]


def shrink_and_chip(cell: Cell, gap: float, chip: float, rng) -> np.ndarray | None:
    """The block's corners, with the gap to its neighbours and its corners knocked off."""
    centre = cell.vertices.mean(axis=0)
    normals, offsets = cell.normals, cell.offsets - gap * 0.5
    if chip > 0.0 and len(cell.vertices) >= 4:
        size = float(np.linalg.norm(cell.vertices - centre, axis=1).max())
        count = int(rng.integers(2, 3 + int(round(5 * chip))))
        chosen = rng.choice(len(cell.vertices), min(count, len(cell.vertices)), replace=False)
        corner = cell.vertices[chosen]
        n = unit(unit(corner - centre) + rng.normal(size=corner.shape) * 0.35)
        d = (corner * n).sum(axis=1) - size * chip * rng.uniform(0.12, 0.42, len(chosen))
        normals = np.vstack([normals, n])
        offsets = np.r_[offsets, d]
    corners = polytope(normals, offsets, centre)
    if corners is None and chip > 0.0:      # chipped away entirely: keep the plain shrunk block
        corners = polytope(cell.normals, cell.offsets - gap * 0.5, centre)
    return corners


# ----------------------------------------------------------------------------- exact cuts

def box(lo, hi) -> mf.Manifold:
    return mf.Manifold.cube(np.maximum(hi - lo, 1e-6).tolist()).translate(lo.tolist())


def cut_blocks(body: mf.Manifold, hulls: list, check=None, leaf: int = 6) -> list:
    """body ∩ hull for every hull. A k-d tree of boxes shrinks the body first, so each cut only sees a little mesh."""
    lows = np.array([h[0] for h in hulls])
    highs = np.array([h[1] for h in hulls])
    centres = (lows + highs) * 0.5
    out = [None] * len(hulls)
    done = [0]

    def visit(piece, idx):
        if piece.is_empty():
            done[0] += len(idx)
            return
        if len(idx) <= leaf:
            for i in idx:
                out[i] = piece ^ hulls[i][2]
            done[0] += len(idx)
            if check:
                check(done[0] / len(hulls))
            return
        axis = int(np.argmax(np.ptp(centres[idx], axis=0)))
        order = idx[np.argsort(centres[idx, axis], kind="stable")]
        half = len(order) // 2
        for part in (order[:half], order[half:]):
            visit(piece ^ box(lows[part].min(axis=0), highs[part].max(axis=0)), part)

    order = np.arange(len(hulls))
    visit(body ^ box(lows.min(axis=0), highs.max(axis=0)), order)
    return out


def crack_slots(cells: list, width: float, area: Area, zone: float, rng) -> list:
    """Tapered slots along the walls of the cells next to the area: fewer and finer toward the edge of the zone."""
    slots = []
    favour = np.array([0.31, 0.53, 0.79])       # a wall is listed by both its cells: keep one
    for cell in cells:
        distance = cell.vertices @ cell.normals.T - cell.offsets
        for f, n in enumerate(cell.normals):
            if n @ favour <= 0.0:
                continue
            polygon = cell.vertices[np.abs(distance[:, f]) < 1e-7]
            if len(polygon) < 3 or len(polygon) > 24:
                continue
            middle = polygon.mean(axis=0)
            odds = float(np.clip(1.0 + area.score(middle[None])[0] / max(zone, 1e-6), 0.0, 1.0))
            if rng.random() > odds * odds:
                continue
            taper = rng.uniform(0.15, 1.0, (len(polygon), 1)) * width * (0.45 + 0.55 * odds) * 0.5
            hull = mf.Manifold.hull_points(np.vstack([polygon + n * taper, polygon - n * taper]))
            if not hull.is_empty():
                slots.append(hull)
    return slots


def to_arrays(m: mf.Manifold) -> tuple[np.ndarray, np.ndarray]:
    data = m.to_mesh64()
    return np.asarray(data.vert_properties)[:, :3].astype(np.float64), np.asarray(data.tri_verts).astype(np.int64)


def mass_properties(vertices: np.ndarray, faces: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """Volume, centre of mass and inertia about it (density 1), from signed tetrahedra."""
    a, b, c = vertices[faces[:, 0]], vertices[faces[:, 1]], vertices[faces[:, 2]]
    origin = vertices.mean(axis=0)
    a, b, c = a - origin, b - origin, c - origin
    det = (a * np.cross(b, c)).sum(axis=1)
    volume = det.sum() / 6.0
    if abs(volume) < 1e-14:
        return 0.0, vertices.mean(axis=0), np.eye(3)
    centre = ((a + b + c) * det[:, None]).sum(axis=0) / (24.0 * volume)
    # second moments of each tetrahedron
    moment = np.zeros((3, 3))
    for i in range(3):
        for j in range(i, 3):
            term = (2 * (a[:, i] * a[:, j] + b[:, i] * b[:, j] + c[:, i] * c[:, j])
                    + a[:, i] * b[:, j] + a[:, j] * b[:, i] + a[:, i] * c[:, j] + a[:, j] * c[:, i]
                    + b[:, i] * c[:, j] + b[:, j] * c[:, i])
            moment[i, j] = moment[j, i] = (det * term).sum() / 120.0
    moment -= volume * np.outer(centre, centre)
    inertia = np.eye(3) * np.trace(moment) - moment
    size = max(abs(volume) ** (2.0 / 3.0), 1e-9) * abs(volume)
    inertia = (inertia + inertia.T) * 0.5 + np.eye(3) * size * 1e-3
    return abs(volume), centre + origin, inertia * np.sign(volume)


# ----------------------------------------------------------------------------- the pipeline

def release_schedule(C, scores, p, rng, facing, seconds: float) -> np.ndarray:
    """When each block lets go, 0..seconds, ordered by the collapse's direction with some randomness."""
    if p["order"] == "bottom":
        key = C[:, 2]
    elif p["order"] == "top":
        key = -C[:, 2]
    elif p["order"] == "outside":
        key = -(C @ facing)
    elif p["order"] == "edge":              # the middle of the area first, its edge last
        key = -scores
    else:
        key = rng.random(len(C))
    rank = np.argsort(np.argsort(key, kind="stable"), kind="stable") / max(len(C) - 1, 1)
    mix = 0.45 * p["variety"] + 0.15
    return (rank * (1.0 - mix) + rng.random(len(C)) * mix) * seconds


def build_field(body_v, body_f, lo, hi, floor, resolution: int, progress=None):
    from atlas_toolkit.tools.mesh_porous import mesh_porous as mp
    pad = np.array([0.7, 0.7, 0.35]) * HEIGHT
    a = lo - pad
    if floor is not None:
        a[2] = min(a[2], floor - 0.12)
    b = hi + pad
    grid = mp.make_grid(a, b, int(resolution), pad=1)
    values = mp.signed_distance(body_v, body_f, grid, band=grid.step * 4.0, progress=progress, coarse=3)
    return physics.Field(np.asarray(values, dtype=np.float64), grid.origin, grid.step)


def crumble(V, F, body: mf.Manifold, p, selected=None, *, progress=None, draft=False) -> Result:
    """The collapse, in canonical coordinates. progress(fraction, message) may raise to cancel."""
    def say(t, message):
        if progress:
            progress(t, message)

    report = Report()
    rng = np.random.default_rng(int(p["seed"]))
    lo, hi = V.min(axis=0), V.max(axis=0)
    size = HEIGHT * p["block_size"] / 100.0
    centroid = np.asarray(V.mean(axis=0))
    area = Area(V, F, centroid, p, selected, rng)
    depth = HEIGHT * p["depth"] / 100.0
    zone = HEIGHT * p["crack_zone"] / 100.0 if p["crack_width"] > 0 else 0.0
    depth_noise = Noise(rng, size * 3.0)

    def classify(P, sd):
        s = area.score(P)
        inside = -sd                        # how deep: negative outside the model
        reach = depth * (1.0 + 0.35 * p["edge_rough"] / 10.0 * depth_noise(P) * 0.3)
        kinds = np.zeros(len(P), dtype=np.int8)
        kinds[(s > 0.0) & (inside <= reach) & (inside > -size * 1.5)] = 2
        near = (s <= 0.0) & (s > -zone) & (inside <= size * 1.3) & (inside > -size * 0.8)
        kinds[near] = 1
        return kinds, s

    say(0.02, "cutting the area into cells")
    if p["block_style"] == "bricks":
        blocks, cracked, report.cells = brick_cells(V, F, lo, hi, size, p["variety"], p["brick_ratio"], area.d, rng, classify)
    else:
        blocks, cracked, report.cells = voronoi_cells(V, F, lo, hi, size, 0.35 + 0.65 * p["variety"], p["block_style"],
                                                       rng, classify)
    if not blocks:
        raise ValueError("No blocks fall in this area: widen it, raise Hollow depth or lower Block size.")
    if len(blocks) > p["max_blocks"]:
        raise ValueError(f"This would make {len(blocks)} blocks (limit {p['max_blocks']}): raise Block size or shrink "
                         "the area.")
    # the body gives up the whole cells, a hair larger so that neighbours fuse into one clean crater
    grow = max(1e-4, HEIGHT * 2e-5)
    removal = []
    for cell in blocks:
        centre = cell.vertices.mean(axis=0)
        removal.append(mf.Manifold.hull_points(centre + (cell.vertices - centre) * (1.0 + grow / max(
            float(np.linalg.norm(cell.vertices - centre, axis=1).min()), 1e-6))))
    # rubble: finer pieces, mostly toward the edge of the area, where the break is fresh
    if p["rubble"] > 0.0:
        scale = max(float(np.quantile([c.score for c in blocks], 0.9)), 1e-6)
        kept = []
        room = int(p["max_blocks"]) - len(blocks)       # rubble stops at the block limit instead of failing the run
        capped = False
        for cell in blocks:
            odds = p["rubble"] / 100.0 * (1.35 - 0.7 * min(max(cell.score, 0.0) / scale, 1.0))
            if rng.random() < odds:
                wanted = int(rng.integers(3, 3 + 1 + int(p["rubble_parts"])))
                if wanted - 1 > room:
                    capped = True
                    kept.append(cell)
                    continue
                parts = split_cell(cell, wanted, rng)
                room -= len(parts) - 1
                report.rubble += len(parts) if len(parts) > 1 else 0
                kept.extend(parts)
            else:
                kept.append(cell)
        blocks = kept
        if capped:
            report.notes.append(f"Rubble stopped at the limit of {p['max_blocks']} blocks; raise Maximum blocks or "
                                "Block size for more.")

    say(0.08, "hollowing the body")
    gap = HEIGHT * p["gap"] / 100.0
    hulls = []
    for cell in blocks:
        corners = shrink_and_chip(cell, gap, p["chip"], rng)
        if corners is None:
            continue
        hull = mf.Manifold.hull_points(corners)
        if not hull.is_empty():
            hulls.append((corners.min(axis=0), corners.max(axis=0), hull, cell))
    if not hulls:
        raise ValueError("No blocks are left: lower Crack gap or Chipped corners, or raise Block size.")
    scar = mf.Manifold.batch_boolean(removal, mf.OpType.Add)
    original_volume = body.volume()
    standing = body - scar
    report.hollowed = 100.0 * (1.0 - standing.volume() / max(original_volume, 1e-12)) if not standing.is_empty() else 100.0

    say(0.14, f"cutting {len(hulls)} blocks")
    pieces = cut_blocks(body, hulls, lambda t: say(0.14 + 0.2 * t, f"cutting {len(hulls)} blocks"))
    tiny = (HEIGHT * max(p["min_block"], 0.01) / 100.0) ** 3
    solids, scores = [], []
    for piece, h in zip(pieces, hulls):
        if piece is None or piece.is_empty():
            continue
        for part in piece.decompose():
            if part.volume() >= tiny:
                solids.append(part)
                scores.append(h[3].score)
            else:
                report.tiny += 1
    # the body may have come apart: islands that lost their hold fall as blocks too
    body_part = None
    if not standing.is_empty():
        parts = sorted(standing.decompose(), key=lambda m: -m.volume())
        body_part = parts[0]
        if p["area"] == "whole" and p["depth"] >= 100.0 and parts[0].volume() < 0.02 * original_volume:
            body_part = None
            parts = [None] + parts
        for part in parts[1:]:
            if part.volume() >= tiny:
                solids.append(part)
                scores.append(0.0)
            else:
                report.tiny += 1
    if not solids:
        raise ValueError("No blocks came out of the model here: move the area or raise Block size.")
    report.blocks = len(solids)
    if zone > 0.0 and cracked and body_part is not None:
        say(0.36, "cutting cracks")
        slots = crack_slots(cracked, HEIGHT * p["crack_width"] / 100.0, area, zone, rng)
        if slots:
            report.cracks = len(slots)
            whole = body_part - mf.Manifold.batch_boolean(slots, mf.OpType.Add)
            parts = sorted(whole.decompose(), key=lambda m: -m.volume())
            body_part = parts[0]
            for part in parts[1:]:
                if part.volume() >= tiny:
                    solids.append(part)
                    scores.append(0.0)

    # the physics
    say(0.40, "weighing the blocks")
    n = len(solids)
    shapes, centres, masses, inertias, meshes = [], [], [], [], []
    for solid in solids:
        v, f = to_arrays(solid)
        volume, centre, inertia = mass_properties(v, f)
        if volume <= 0.0:
            centre = v.mean(axis=0)
            volume = max(float(solid.volume()), 1e-9)
            inertia = np.eye(3) * max(volume ** (5.0 / 3.0), 1e-12)
        shapes.append(physics.collision_shape(v - centre))
        centres.append(centre)
        masses.append(volume)
        inertias.append(inertia)
        meshes.append(solid)
    C = np.asarray(centres)
    scores = np.asarray(scores, dtype=float)

    floor = None
    if p["floor"]:
        floor = float(lo[2]) - HEIGHT * p["floor_drop"] / 100.0
    field_ = None
    if body_part is not None:
        say(0.42, "measuring the standing body")
        bv, bf = to_arrays(body_part)
        resolution = int(p["draft_resolution"] if draft else p["resolution"])
        field_ = build_field(bv, bf, lo, hi, floor, resolution,
                             lambda t, msg="": say(0.42 + 0.06 * t, "measuring the standing body"))
    down = np.array([0.0, 0.0, -physics.GRAVITY * p["gravity"]])
    facing = area.d if p["area"] != "whole" else np.zeros(3)
    wind = facing * physics.GRAVITY * p["wind"]
    world = physics.World(shapes, C, masses, inertias, field_, floor, p["friction"], down + wind)

    # who lets go, when and how
    cling_count = int(round(n * p["cling"] / 100.0))
    stay = np.zeros(n, bool)
    if cling_count:     # the blocks nearest the edge of the area hold on longest
        stay[np.argsort(scores + rng.normal(size=n) * size * 0.5, kind="stable")[:cling_count]] = True
    window = float(p["stagger"])
    times = np.full(n, np.inf)
    free = np.flatnonzero(~stay)
    if len(free):
        times[free] = release_schedule(C[free], scores[free], p, rng, area.d, window)
    outward = unit(C - centroid)
    if p["area"] != "whole":
        outward = unit(outward * 0.45 + area.d * 0.55)
    speeds = p["burst"] * rng.uniform(0.35, 1.15, n)
    velocities = outward * speeds[:, None] + rng.normal(size=(n, 3)) * p["burst"] * 0.22
    spin_axis = unit(rng.normal(size=(n, 3)))
    spins = spin_axis * (rng.uniform(0.2, 1.0, n) * p["spin"] * 7.0)[:, None]
    # blocks that hold on have still worked loose
    if p["loosen"] > 0.0:
        held = np.flatnonzero(stay)
        scale = rng.uniform(0.3, 1.0, len(held)) * p["loosen"]
        shift = (outward[held] * 0.8 + np.array([0.0, 0.0, -0.45])) * (scale * HEIGHT * 0.012)[:, None]
        axes = unit(rng.normal(size=(len(held), 3)))
        physics.tilt(world, held, axes, np.radians(scale * 9.0) * rng.choice([-1.0, 1.0], len(held)), shift)

    end = window + SETTLE_SECONDS
    moment = float(p["collapse"]) / 100.0
    settle = moment >= 0.999
    say(0.5, "collapse")
    outcome = physics.run(world, times, velocities, spins, moment * end, settle,
                          lambda t, msg: say(0.5 + 0.3 * t, msg))
    report.seconds = outcome["seconds"]
    report.released = int(world.released.sum())
    report.clinging = int(n - report.released)
    report.falling = outcome["moving"]
    report.resting = int((world.released & world.asleep).sum())
    R, X = physics.poses(world)

    # where they ended up
    placed, moved = [], np.zeros(n)
    keep = np.ones(n, bool)
    if floor is None:       # with no floor, whatever has fallen far past the model is gone
        keep = X[:, 2] > float(lo[2]) - HEIGHT * 1.5
        report.lost = int((~keep).sum())
    for i, solid in enumerate(meshes):
        if not keep[i]:
            continue
        T = np.column_stack([R[i], X[i] - R[i] @ C[i]])
        placed.append(solid.transform(T))
        moved[i] = float(np.linalg.norm(X[i] - C[i]))

    plate = None
    if p["floor"] and p["base_plate"]:
        # sized for the heap, not for the odd block that rolled away
        spread = X[keep][:, :2]
        box_lo = np.quantile(spread, 0.04, axis=0)
        box_hi = np.quantile(spread, 0.96, axis=0)
        if body_part is not None:
            extent = np.asarray(body_part.bounding_box())
            box_lo = np.minimum(box_lo, extent[:2])
            box_hi = np.maximum(box_hi, extent[3:5])
        margin = HEIGHT * 0.04
        box_lo, box_hi = box_lo - margin, box_hi + margin
        plate = box(np.array([box_lo[0], box_lo[1], floor - HEIGHT * 0.03]),
                    np.array([box_hi[0], box_hi[1], floor + HEIGHT * 0.0005]))
        if p["fuse"]:           # a print cannot keep what lies off its base
            off = [b for b in placed if not _over(b, box_lo, box_hi)]
            if off:
                placed = [b for b in placed if _over(b, box_lo, box_hi)]
                report.dropped += len(off)
                report.notes.append(f"{len(off)} blocks that rolled off the base plate were left out.")

    fused = None
    if p["fuse"]:
        say(0.84, "fusing the blocks")
        fused = fuse(body_part, placed, plate, p, report, say)
    return Result(body_part, placed, fused, plate, report, moved)


def _over(block: mf.Manifold, lo, hi) -> bool:
    """Is the middle of this block above the plate's footprint?"""
    b = np.asarray(block.bounding_box())
    middle = (b[:2] + b[3:5]) * 0.5
    return bool((middle >= lo).all() and (middle <= hi).all())


def fuse(body, blocks, plate, p, report: Report, say) -> mf.Manifold:
    """One closed solid: blocks swell a hair to weld where they touch, and thin bridges reach what still floats."""
    from scipy.spatial import cKDTree
    swell = 1.0 + float(p["weld"]) / 100.0
    parts = []
    for block in blocks:
        lo_hi = block.bounding_box()
        c = (np.array(lo_hi[:3]) + np.array(lo_hi[3:])) * 0.5
        parts.append(block.translate((-c).tolist()).scale([swell] * 3).translate(c.tolist()))
    solids = list(parts)
    if body is not None:
        solids.append(body)
    if plate is not None:
        solids.append(plate)
    joined = mf.Manifold.batch_boolean(solids, mf.OpType.Add)
    pieces = sorted(joined.decompose(), key=lambda m: -m.volume())
    if len(pieces) == 1:
        report.pieces = 1
        return pieces[0]
    reach = HEIGHT * p["bridge_reach"] / 100.0
    radius = HEIGHT * p["bridge_size"] / 200.0
    attached = [pieces[0]]
    waiting = pieces[1:]
    bridges = []
    points = [to_arrays(pieces[0])[0]]
    while waiting:
        say(0.88, f"bridging {len(waiting)} floating pieces")
        tree = cKDTree(np.vstack(points))
        next_wait = []
        added = False
        for piece in waiting:
            v = to_arrays(piece)[0]
            dist, idx = tree.query(v)
            k = int(np.argmin(dist))
            if dist[k] <= reach:
                a, b = v[k], tree.data[idx[k]]
                bridges.append((a, b))
                points.append(v)
                attached.append(piece)
                added = True
            else:
                next_wait.append(piece)
        waiting = next_wait
        if not added:
            break
    report.dropped += len(waiting)
    if bridges:
        ball = mf.Manifold.sphere(max(radius, 1e-4), 12)
        rods = []
        for a, b in bridges:
            inward_a = a + unit(b - a) * -radius
            inward_b = b + unit(a - b) * -radius
            rods.append(mf.Manifold.batch_hull([ball.translate(inward_a.tolist()), ball.translate(inward_b.tolist())]))
        attached.extend(rods)
        report.bridges = len(rods)
    out = mf.Manifold.batch_boolean(attached, mf.OpType.Add)
    final = sorted(out.decompose(), key=lambda m: -m.volume())
    report.pieces = 1
    if len(final) > 1:
        report.dropped += len(final) - 1
    return final[0]
