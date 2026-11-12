"""
mesh_porous.py - Porous Forms: turn a closed model into a continuous labyrinth of walls (or struts) that keeps its
silhouette: a bust becomes a gyroid, a figure a cellular foam, solid marble gives way to an inner structure.

Everything happens in the model's canonical frame (geom/frame3d: up = Z, front = -Y, 2 units tall), so lengths are %
of the height and settings carry over between models. Fields are negative inside.

    d(x)   the model's signed distance, sampled on a padded grid (libigl, fast winding number sign): exact within a
           band around the surface (and around the core depth), interpolated from a coarse grid elsewhere, where only
           its sign and "far" matter
    f(x)   distance-like field of the pattern, >= 0, 0 on the pattern's skeleton: the mid-surface of the walls
           (sheet) or the centre lines of the struts (network). Triply periodic minimal surfaces use
           |g| / |grad g| for walls (in model units: the first-order distance to the zero set, exact at the walls)
           and the distance to the skeleton of one channel for struts (even rods, a space frame); Voronoi cells use
           the distance to the bisecting walls or edges of seeded, jittered cells
    h(x)   half thickness: t/2 where the pattern acts fully, growing to the field's largest value (solid) where it
           does not:  h = t/2 + (1 - u w(x))^2 (f_max - t/2),  u the stage amount, w the influence weight (squared,
           so the holes are already open a third of the way through a sequence)
    L(x)   f - h, the pattern's solid (<= 0); with a core depth D: min(L, d + D), solid deeper than D
    F(x)   max(d, L): the pattern cut by the model's envelope, extracted at 0 (Lewiner marching cubes)

So amount 0 is the untouched model (returned as it is), holes open where the field is farthest from the walls and
grow as the amount rises, and every stage of a sequence shares d, f and w: only h changes.

The pattern lives in its own frame: turned (degrees about X, Y, Z), shifted (a fraction of a cell) and, optionally,
graded: the cell size changes geometrically from the base to the top (ratio top / base), with the vertical phase
integrated so the layers stay continuous.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from atlas_toolkit.geom import influence

PATTERNS = ("gyroid", "diamond", "primitive", "neovius", "voronoi")
STYLES = ("sheet", "network")
CHUNK = 1_000_000                 # grid points per batch (memory)
MIN_WALL_SAMPLES = 3.0            # final grids must put this many samples across a wall
MAX_POINTS = 90_000_000           # grid points (≈ 512 × 420 × 420): beyond this the run is refused


# ----------------------------------------------------------------------------- grid

@dataclass
class Grid:
    origin: np.ndarray            # canonical position of sample (0, 0, 0)
    step: float                   # spacing (canonical units)
    shape: tuple                  # (nx, ny, nz)

    @property
    def size(self) -> int:
        return int(np.prod(self.shape))

    def points(self, idx) -> np.ndarray:
        """Canonical positions of flat indices `idx`."""
        i, j, k = np.unravel_index(idx, self.shape)
        return self.origin + np.stack([i, j, k], axis=1) * self.step


def make_grid(lo, hi, resolution: int, pad: int = 2) -> Grid:
    """`resolution` samples along the longest side of [lo, hi], plus `pad` samples of air on every side."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    step = float((hi - lo).max()) / max(int(resolution) - 1, 1)
    n = np.ceil((hi - lo) / step).astype(int) + 1 + 2 * pad
    return Grid(lo - pad * step, step, tuple(int(x) for x in n))


def check_size(grid: Grid) -> None:
    if grid.size > MAX_POINTS:
        raise ValueError(f"the grid would have {grid.size / 1e6:.0f} million samples (limit {MAX_POINTS / 1e6:.0f} "
                         f"million): lower Resolution")


# ----------------------------------------------------------------------------- the model's signed distance

def _sd(V, F, P) -> np.ndarray:
    import igl
    S = igl.signed_distance(np.ascontiguousarray(P, dtype=np.float64), V, F,
                            sign_type=igl.SIGNED_DISTANCE_TYPE_FAST_WINDING_NUMBER)[0]
    return np.asarray(S, dtype=np.float64)


def signed_distance(V, F, grid: Grid, band: float, progress=None, coarse: int = 4) -> np.ndarray:
    """Signed distance (negative inside) at every grid sample, float32, shape grid.shape. Exact where |d| < band,
    elsewhere trilinear from a grid `coarse` times coarser (right in sign, and at least `band` in size, because a
    distance field changes no faster than the distance moved)."""
    from scipy import ndimage
    V = np.ascontiguousarray(V, dtype=np.float64)
    F = np.ascontiguousarray(F, dtype=np.int64)
    n = np.array(grid.shape)
    k = max(1, int(coarse))
    nc = (n - 1 + k - 1) // k + 1 + 1               # coarse samples covering the fine grid (one extra)
    ci = [np.arange(m) * k for m in nc]
    C = np.stack(np.meshgrid(*ci, indexing="ij"), axis=-1).reshape(-1, 3) * grid.step + grid.origin
    Dc = _sd(V, F, C).reshape(tuple(nc))
    if progress:
        progress(0.15, "distance: coarse grid")
    # coarse cells whose corners are all farther than band + the cell's diagonal hold no sample nearer than band
    A = np.abs(Dc)
    corner_min = A[:-1, :-1, :-1]
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                corner_min = np.minimum(corner_min, A[dx:dx + nc[0] - 1, dy:dy + nc[1] - 1, dz:dz + nc[2] - 1])
    near_cell = corner_min < band + k * grid.step * math.sqrt(3.0)
    cell = [np.minimum(np.arange(m) // k, c - 2) for m, c in zip(n, nc)]
    near = near_cell[np.ix_(*cell)]
    D = np.empty(tuple(n), dtype=np.float32)
    far = ~near
    if far.any():
        idx = np.nonzero(far)
        D[idx] = ndimage.map_coordinates(Dc, [a / k for a in idx], order=1, mode="nearest")
    flat = np.flatnonzero(near.ravel())
    Dflat = D.reshape(-1)
    for s in range(0, len(flat), CHUNK):
        part = flat[s:s + CHUNK]
        Dflat[part] = _sd(V, F, grid.points(part))
        if progress:
            progress(0.15 + 0.85 * min(1.0, (s + CHUNK) / max(len(flat), 1)), "distance: near the surface")
    return D


# ----------------------------------------------------------------------------- pattern space

def pattern_coords(P, centre, cell: float, rotate=(0.0, 0.0, 0.0), offset=(0.0, 0.0, 0.0), grade: float = 1.0,
                   z0: float = 0.0, height: float = 2.0):
    """Points P (canonical) in cell units of the pattern's frame, and the local cell size at each point.

    Grading makes the cell grow geometrically with height: c(z) = cell · grade^s, s = (z - z0) / height; the
    vertical coordinate is the integral of dz / c, so the pattern stays continuous."""
    from atlas_toolkit.geom.frame3d import euler_xyz
    X = np.asarray(P, dtype=np.float64) - np.asarray(centre, dtype=np.float64)
    if abs(grade - 1.0) < 1e-9:
        c = np.full(len(X), cell)
        Q = X / cell
    else:
        g = math.log(grade)
        s = (X[:, 2] + np.asarray(centre, float)[2] - z0) / height
        c = cell * np.exp(g * s)
        Q = np.empty_like(X)
        Q[:, :2] = X[:, :2] / c[:, None]
        # z' = ∫_{zc}^{z} dz / c(z) = height / (cell g) (e^{-g s_c} - e^{-g s})
        sc = (np.asarray(centre, float)[2] - z0) / height
        Q[:, 2] = height / (cell * g) * (math.exp(-g * sc) - np.exp(-g * s))
    R = euler_xyz(rotate)
    return Q @ R.T + np.asarray(offset, dtype=np.float64), c


# ----------------------------------------------------------------------------- periodic surfaces

def tpms(name: str, Q) -> np.ndarray:
    """Triply periodic implicit g at points Q (cell units: period 1)."""
    x, y, z = (2.0 * math.pi * Q[:, i] for i in range(3))
    if name == "gyroid":
        return np.sin(x) * np.cos(y) + np.sin(y) * np.cos(z) + np.sin(z) * np.cos(x)
    sx, sy, sz, cx, cy, cz = np.sin(x), np.sin(y), np.sin(z), np.cos(x), np.cos(y), np.cos(z)
    if name == "diamond":
        return sx * sy * sz + sx * cy * cz + cx * sy * cz + cx * cy * sz
    if name == "primitive":
        return cx + cy + cz
    if name == "neovius":
        return 3.0 * (cx + cy + cz) + 4.0 * cx * cy * cz
    raise ValueError(f"unknown pattern {name!r}")


def tpms_gradient(name: str, Q, eps: float = 1e-4) -> np.ndarray:
    """|grad g| in cell units (central differences)."""
    G = np.empty((len(Q), 3))
    for i in range(3):
        d = np.zeros(3)
        d[i] = eps
        G[:, i] = (tpms(name, Q + d) - tpms(name, Q - d)) / (2 * eps)
    return np.linalg.norm(G, axis=1)


_RMS: dict = {}


def tpms_rms_gradient(name: str) -> float:
    """Root mean square |grad g| on the zero set (cell units), measured once per pattern."""
    if name not in _RMS:
        rng = np.random.default_rng(0)
        Q = rng.random((200_000, 3))
        g = tpms(name, Q)
        gr = tpms_gradient(name, Q)
        near = np.abs(g) < 0.05 * np.abs(g).max()
        _RMS[name] = float(np.sqrt(np.mean(gr[near] ** 2)))
    return _RMS[name]


def tpms_field(name: str, style: str, Q, c) -> np.ndarray:
    """Distance-like field in model units, 0 on the walls' mid-surface (sheet) or the struts' centre lines
    (network). The gradient is floored at half its typical size, so the field stays finite and smooth where g has
    no direction (the centres of the channels)."""
    if style == "network":
        return tpms_network(name, Q, c)
    g = tpms(name, Q)
    gr = np.maximum(tpms_gradient(name, Q), 0.5 * tpms_rms_gradient(name))
    to_model = c                    # |grad g| in cell units already carries the period's 2π
    return np.abs(g) / gr * to_model


_SKELETON: dict = {}


def tpms_skeleton(name: str, n: int = 64):
    """The centre lines of the negative channel of one period, as a KD-tree of points (cell units) repeated over
    the 26 neighbouring periods: 3D-skeletonised on a 3 × 3 × 3 tile, the middle period kept, and smoothed (each
    point moved to the mean of its neighbours within two samples) so rods do not wobble at the sample scale."""
    if name not in _SKELETON:
        from scipy.spatial import cKDTree
        from skimage.morphology import skeletonize
        a = (np.arange(3 * n) + 0.5) / n
        Q = np.stack(np.meshgrid(a, a, a, indexing="ij"), -1).reshape(-1, 3)
        sk = skeletonize(tpms(name, Q).reshape((3 * n,) * 3) < 0)
        idx = np.argwhere(sk)
        P = (idx + 0.5) / n
        for _ in range(3):
            tree = cKDTree(P)
            P = np.array([P[nb].mean(0) for nb in tree.query_ball_point(P, 2.0 / n)])
        P = P[np.all((P >= 1.0) & (P < 2.0), axis=1)] - 1.0
        shifts = np.stack(np.meshgrid(*[np.arange(-1, 2)] * 3, indexing="ij"), -1).reshape(-1, 3)
        _SKELETON[name] = cKDTree((P[None] + shifts[:, None]).reshape(-1, 3))
    return _SKELETON[name]


def tpms_network(name: str, Q, c) -> np.ndarray:
    """Distance to the channel's centre lines (model units): rods of even thickness, a space frame."""
    d, _ = tpms_skeleton(name).query(np.mod(Q, 1.0))
    return d * c


# ----------------------------------------------------------------------------- Voronoi cells

def voronoi_seeds(lo, hi, seed: int, jitter: float = 0.85) -> np.ndarray:
    """One seed per unit cell of pattern space covering [lo, hi] (plus a margin), jittered (seeded)."""
    a = np.floor(np.asarray(lo, float)).astype(int) - 2
    b = np.ceil(np.asarray(hi, float)).astype(int) + 2
    axes = [np.arange(a[i], b[i]) for i in range(3)]
    cells = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3).astype(np.float64)
    rng = np.random.default_rng(int(seed))
    return cells + 0.5 + jitter * (rng.random(cells.shape) - 0.5)


WINDOW_REACH = 0.35               # a wall's rim at windows 0, in cells from its edges: wide enough to close it


def voronoi_field(tree, style: str, Q, c, windows: float = 0.0) -> np.ndarray:
    """Distance-like field of Voronoi cells (model units): to the nearest wall (sheet) or edge (network). Windows
    (0..1, walls only) keeps the walls within a rim of their edges and opens the rest, like a radiolarian:
    max(wall, edge - rim), the rim (1 - windows) x WINDOW_REACH cells wide."""
    d, _ = tree.query(Q, k=3)
    a = (d[:, 1] - d[:, 0]) / 2.0
    b = (d[:, 2] - d[:, 0]) / 2.0
    edge = np.sqrt(a * a + b * b)
    if style == "sheet":
        if windows > 0:
            return np.maximum(a, edge - (1.0 - windows) * WINDOW_REACH) * c
        return a * c
    return edge * c


# ----------------------------------------------------------------------------- the whole field

@dataclass
class Fields:
    grid: Grid
    d: np.ndarray                 # model signed distance, float32, grid.shape
    f: np.ndarray                 # pattern field (model units), float32; +inf where not needed
    w: np.ndarray                 # influence weight, float32; 0 where not needed
    f_max: float                  # largest pattern field inside the model (the half thickness that fills it)
    inside: int                   # samples inside the model


def build_fields(V, F, lo, hi, values: dict, resolution: int, progress=None, d_band: float | None = None) -> Fields:
    """Sample everything the stages share. `values`: the tool's settings (pattern, style, cell, wall, depth,
    rotation, offset, grade, seed and the influence keys), lengths in % of the height."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    height = float(hi[2] - lo[2]) or 1.0
    pct = height / 100.0
    grid = make_grid(lo, hi, resolution)
    check_size(grid)
    depth = float(values.get("depth", 0.0)) * pct
    band = (d_band if d_band is not None else 0.0) + 3.0 * grid.step + depth

    def sub(a, b):
        return None if progress is None else (lambda t, msg="": progress(a + (b - a) * t, msg))

    d = signed_distance(V, F, grid, band, sub(0.0, 0.6))
    need = np.flatnonzero(d.reshape(-1) < 2.0 * grid.step)      # inside, and the air the extraction looks at
    inside = int((d < 0).sum())
    if inside == 0:
        raise ValueError("the model encloses no volume at this resolution: it must be a closed solid (or raise "
                         "Resolution for a very thin one)")
    cell = float(values.get("cell", 12.0)) * pct
    pattern, style = values.get("pattern", "gyroid"), values.get("style", "sheet")
    centre = (lo + hi) / 2.0
    rot = tuple(values.get("pattern_rotate", (0.0, 0.0, 0.0)))
    off = tuple(values.get("pattern_offset", (0.0, 0.0, 0.0)))
    grade = float(values.get("grade", 1.0))
    off = tuple(float(o) / 100.0 for o in off)
    windows = float(values.get("windows", 0.0)) / 100.0 if pattern == "voronoi" else 0.0
    tree = None
    if pattern == "voronoi":
        from scipy.spatial import cKDTree
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        Qc, _ = pattern_coords(corners, centre, cell, rot, off, grade, lo[2], height)
        tree = cKDTree(voronoi_seeds(Qc.min(0), Qc.max(0), int(values.get("seed", 1))))
    f = np.full(grid.size, np.inf, dtype=np.float32)
    w = np.zeros(grid.size, dtype=np.float32)
    for s in range(0, len(need), CHUNK):
        part = need[s:s + CHUNK]
        P = grid.points(part)
        Q, c = pattern_coords(P, centre, cell, rot, off, grade, lo[2], height)
        f[part] = voronoi_field(tree, style, Q, c, windows) if tree is not None else tpms_field(pattern, style, Q, c)
        w[part] = influence.weights(P, values, lo, hi)
        if progress:
            progress(0.6 + 0.4 * min(1.0, (s + CHUNK) / max(len(need), 1)), "pattern")
    f = f.reshape(grid.shape)
    w = w.reshape(grid.shape)
    f_max = float(f[d < 0].max())
    return Fields(grid, d, f, w, f_max, inside)


def solid_field(fields: Fields, u: float, wall: float, depth: float = 0.0) -> np.ndarray:
    """F (<= 0 solid) for amount u; wall and depth in canonical units."""
    half = wall / 2.0
    fill = max(fields.f_max, half) * 1.001
    h = half + (1.0 - u * fields.w) ** 2 * (fill - half)     # squared: holes open early in a sequence
    L = fields.f - h
    if depth > 0:
        L = np.minimum(L, fields.d + depth)
    return np.maximum(fields.d, L).astype(np.float32)


def extract(Fv: np.ndarray, grid: Grid):
    """Lewiner marching cubes at 0 -> (V canonical, F int64), outward-facing (the field is negative inside)."""
    from skimage import measure
    if not (Fv < 0).any():
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    V, Fc, _, _ = measure.marching_cubes(Fv, level=0.0, spacing=(grid.step,) * 3, method="lewiner",
                                         gradient_direction="descent", allow_degenerate=False)
    return V.astype(np.float64) + grid.origin, Fc.astype(np.int64)


# ----------------------------------------------------------------------------- after extraction

def keep_components(mesh, mode: str, min_share: float = 1.0):
    """'all' keeps everything; 'largest' the biggest piece by volume; 'drop' pieces smaller than `min_share` % of
    the total volume. Returns (mesh, pieces before, pieces after)."""
    import trimesh
    parts = mesh.split(only_watertight=False)
    n = len(parts)
    if mode == "all" or n <= 1:
        return mesh, n, n
    vols = np.array([abs(p.volume) for p in parts])
    if mode == "largest":
        kept = [parts[int(vols.argmax())]]
    else:
        kept = [p for p, v in zip(parts, vols) if v >= min_share / 100.0 * vols.sum()] or [parts[int(vols.argmax())]]
    return trimesh.util.concatenate(kept), n, len(kept)


def smooth(mesh, iterations: int):
    """Taubin smoothing (keeps the volume), `iterations` passes; 0 leaves the mesh as it is."""
    if iterations <= 0:
        return mesh
    import trimesh
    m = mesh.copy()
    trimesh.smoothing.filter_taubin(m, lamb=0.5, nu=-0.53, iterations=int(iterations))
    return m
