"""
mesh_fusion.py - Shape Fusion: transformations between different shapes without matching vertices. A bust melts into a
torus, a head turns into a column from the top down, two copies of a head fuse face to face, a tunnel is bored through
a figure, a head is hewn into a crystal by a turned block.

Everything happens in the model's canonical frame (geom/frame3d: up = Z, front = -Y, 2 units tall, centred), so lengths
are % of the height and settings carry over between models. Both shapes are sampled as signed distances (negative
inside) on one padded grid covering them both (Porous Forms' sampler: libigl, fast winding number sign):

    a(x)   the model's signed distance
    b(x)   the target's: another model, the model itself, or a built-in shape (analytic), placed and repeated
    o(x)   what the mode makes of the two, with a smooth radius k (polynomial smooth min / max):
               morph      b                       the model becomes the target
               fuse       smin(a, b, k)           union, with a fillet of radius k where they meet
               carve      smax(a, -b, k)          the target cut out of the model
               intersect  smax(a, b, k)           only what both hold
    t(x)   how far the transition has gone at x, 0..1: the stage amount u, swept across the model as a soft front
           when Sweep is set, times the influence weight (geom/influence)
    F(x) = a + t (o - a) - 4 s t (1 - t)          extracted at 0 (Lewiner marching cubes)

s is Swell, which adds volume half way (shapes that overlap little thin out or split when their fields are averaged).
So t = 0 is the model (returned as it is) and, in Morph, t = 1 everywhere is the target (returned as placed, not
remeshed). Every stage of a sequence shares a, b and o: only t changes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from atlas_toolkit.geom import influence
from atlas_toolkit.geom.frame3d import euler_xyz
from atlas_toolkit.tools.mesh_porous import mesh_porous as mp

MODES = ("morph", "fuse", "carve", "intersect")
SHAPES = ("torus", "sphere", "box", "cylinder")
SWEEPS = ("none", "up", "down", "front", "back", "left", "right", "out", "in")
CHUNK = mp.CHUNK
BOX_ROUND = 0.03                  # built-in box: edge rounding, canonical units (the box is 2 tall)


# ----------------------------------------------------------------------------- built-in shapes

def shape_sdf(name: str, P, tube: float = 0.3, aspect: float = 1.0) -> np.ndarray:
    """Signed distance of a built-in shape at local points P (n, 3). Shapes are 2 tall (z in -1..1), centred:

        torus     a ring standing in the XZ plane (the hole faces the front), outer radius 1; tube = minor / major
        sphere    radius 1
        box       2 tall, aspect x 2 wide and deep, edges rounded
        cylinder  upright, 2 tall, radius aspect
    """
    P = np.asarray(P, dtype=np.float64)
    x, y, z = P[:, 0], P[:, 1], P[:, 2]
    if name == "sphere":
        return np.linalg.norm(P, axis=1) - 1.0
    if name == "torus":
        R = 1.0 / (1.0 + tube)
        r = tube * R
        return np.hypot(np.hypot(x, z) - R, y) - r
    if name == "box":
        rr = min(BOX_ROUND, 0.5 * aspect)
        b = np.array([aspect, aspect, 1.0]) - rr
        q = np.abs(P) - b
        return np.linalg.norm(np.maximum(q, 0.0), axis=1) + np.minimum(q.max(axis=1), 0.0) - rr
    if name == "cylinder":
        dx = np.hypot(x, y) - aspect
        dz = np.abs(z) - 1.0
        return np.minimum(np.maximum(dx, dz), 0.0) + np.hypot(np.maximum(dx, 0.0), np.maximum(dz, 0.0))
    raise ValueError(f"unknown shape {name!r}")


def shape_mesh(name: str, tube: float = 0.3, aspect: float = 1.0):
    """A mesh of a built-in shape in its local frame (for its bounds after turning)."""
    import trimesh
    if name == "sphere":
        return trimesh.creation.icosphere(subdivisions=4, radius=1.0)
    if name == "torus":
        R = 1.0 / (1.0 + tube)
        m = trimesh.creation.torus(R, tube * R, major_sections=96, minor_sections=32)
        m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, (1, 0, 0)))   # ring in XZ
        return m
    if name == "box":
        return trimesh.creation.box(extents=(2 * aspect, 2 * aspect, 2.0))
    if name == "cylinder":
        return trimesh.creation.cylinder(radius=aspect, height=2.0, sections=96)
    raise ValueError(f"unknown shape {name!r}")


# ----------------------------------------------------------------------------- placing the target

@dataclass
class Placement:
    """canonical = s R (local - c) + t, then turned about the model's vertical axis for each copy."""
    R: np.ndarray
    c: np.ndarray
    s: float
    t: np.ndarray
    turns: list = field(default_factory=lambda: [0.0])     # degrees about Z through the model's centre, per copy

    def copy_matrix(self, k: int) -> np.ndarray:
        a = math.radians(self.turns[k])
        return np.array([[math.cos(a), -math.sin(a), 0.0], [math.sin(a), math.cos(a), 0.0], [0.0, 0.0, 1.0]])

    def apply(self, P, k: int = 0) -> np.ndarray:
        """Local points to canonical, copy k."""
        Q = self.s * (np.asarray(P, dtype=np.float64) - self.c) @ self.R.T + self.t
        return Q @ self.copy_matrix(k).T

    def local(self, Q, k: int = 0) -> np.ndarray:
        """Canonical points to local, copy k (the inverse of apply)."""
        P = np.asarray(Q, dtype=np.float64) @ self.copy_matrix(k)
        return ((P - self.t) / self.s) @ self.R + self.c


def place(V_local, model_lo, model_hi, fit: str = "height", scale: float = 1.0, rotate=(0.0, 0.0, 0.0),
          offset=(0.0, 0.0, 0.0), copies: int = 1) -> Placement:
    """Where the target goes, given its vertices in its own upright frame (or, fit "keep", already in the model's
    canonical frame), and the model's canonical bounds:

        height   turned about its centre, as tall as the model x scale, standing on the model's base, centred across
        size     its largest side the model's largest x scale, centred on the model's centre
        centre   as tall as the model x scale, centred on the model's centre (built-in shapes, the model itself)
        keep     where it is, turned and scaled about its own centre

    then moved by offset (canonical units) and repeated `copies` times evenly about the model's vertical axis."""
    V = np.asarray(V_local, dtype=np.float64)
    lo_m, hi_m = np.asarray(model_lo, float), np.asarray(model_hi, float)
    R = euler_xyz(rotate)
    c = (V.min(0) + V.max(0)) / 2.0
    W = (V - c) @ R.T
    wlo, whi = W.min(0), W.max(0)
    ext = np.maximum(whi - wlo, 1e-12)
    centre_m = (lo_m + hi_m) / 2.0
    if fit == "keep":
        s = float(scale)
        t = c.copy()
    elif fit == "size":
        s = float(scale) * float((hi_m - lo_m).max()) / float(ext.max())
        t = centre_m - s * (wlo + whi) / 2.0
    else:
        s = float(scale) * float(hi_m[2] - lo_m[2]) / float(ext[2])
        t = centre_m - s * (wlo + whi) / 2.0
        if fit == "height":
            t[2] = lo_m[2] - s * wlo[2]
    n = max(1, int(copies))
    return Placement(R, c, s, t + np.asarray(offset, dtype=np.float64), [360.0 * k / n for k in range(n)])


# ----------------------------------------------------------------------------- smooth booleans

def smin(a, b, k: float):
    """Polynomial smooth minimum (Quilez): min(a, b) with a round fillet of radius about k."""
    if k <= 0:
        return np.minimum(a, b)
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0.0, 1.0)
    return b + (a - b) * h - k * h * (1.0 - h)


def smax(a, b, k: float):
    return -smin(-a, -b, k)


def operate(mode: str, a, b, k: float):
    """o(x): the field the transition heads for."""
    if mode == "morph":
        return b
    if mode == "fuse":
        return smin(a, b, k)
    if mode == "carve":
        return smax(a, -b, k)
    if mode == "intersect":
        return smax(a, b, k)
    raise ValueError(f"unknown mode {mode!r}")


# ----------------------------------------------------------------------------- where and when

def sweep_position(P, how: str, lo, hi) -> np.ndarray:
    """0..1: the order in which a sweep reaches each point (0 first)."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    ext = np.maximum(hi - lo, 1e-12)
    axis = {"up": 2, "down": 2, "front": 1, "back": 1, "left": 0, "right": 0}
    if how in axis:
        i = axis[how]
        s = (P[:, i] - lo[i]) / ext[i]
        return 1.0 - s if how in ("down", "back", "right") else s
    centre = (lo + hi) / 2.0
    r = np.linalg.norm((P - centre) / (ext.max() / 2.0), axis=1) / math.sqrt(3.0)
    r = np.clip(r, 0.0, 1.0)
    return r if how == "out" else 1.0 - r


def progress_at(u: float, pos, width: float) -> np.ndarray:
    """t for a front sweeping through `pos` (0..1) as u goes 0..1, soft over `width` (share of the way): 0 everywhere
    at u = 0, 1 everywhere at u = 1."""
    w = max(float(width), 1e-3)
    return influence.smoothstep((u * (1.0 + w) - pos) / w)


# ----------------------------------------------------------------------------- the fields

@dataclass
class Fields:
    grid: mp.Grid
    a: np.ndarray                 # model, float32, grid.shape
    o: np.ndarray                 # the mode's field
    w: np.ndarray | None          # influence weight (None: 1 everywhere)
    pos: np.ndarray | None        # sweep order 0..1 (None: no sweep)
    overlap: float                # share of the smaller shape's samples inside the other one
    inside_a: int
    inside_b: int


def joint_bounds(boxes, pad: float = 0.0):
    lo = np.min([b[0] for b in boxes], axis=0) - pad
    hi = np.max([b[1] for b in boxes], axis=0) + pad
    return lo, hi


def mesh_distance(V, F, grid: mp.Grid, band: float, progress=None) -> np.ndarray:
    return mp.signed_distance(V, F, grid, band, progress)


def shape_distance(name: str, place_: Placement, grid: mp.Grid, tube: float, aspect: float) -> np.ndarray:
    """A built-in shape's signed distance on the grid, the union of its copies (exact: a similarity keeps
    distances, scaled)."""
    out = np.empty(grid.size, dtype=np.float32)
    for s in range(0, grid.size, CHUNK):
        idx = np.arange(s, min(s + CHUNK, grid.size))
        Q = grid.points(idx)
        d = None
        for k in range(len(place_.turns)):
            dk = place_.s * shape_sdf(name, place_.local(Q, k), tube, aspect)
            d = dk if d is None else np.minimum(d, dk)
        out[idx] = d
    return out.reshape(grid.shape)


def copies_of_field(a: np.ndarray, place_: Placement, grid: mp.Grid) -> np.ndarray:
    """The model's own field a, placed (the target "the model itself"): each copy is a similarity of the model, so
    its distance is s x a at the point taken back to the model, read trilinearly from the grid. Outside the grid the
    nearest sample plus the distance to the grid's box: a lower bound, right in sign."""
    from scipy import ndimage
    out = np.empty(grid.size, dtype=np.float32)
    n = np.array(grid.shape) - 1
    for s in range(0, grid.size, CHUNK):
        idx = np.arange(s, min(s + CHUNK, grid.size))
        Q = grid.points(idx)
        d = None
        for k in range(len(place_.turns)):
            L = (place_.local(Q, k) - grid.origin) / grid.step
            Lc = np.clip(L, 0, n)
            v = ndimage.map_coordinates(a, Lc.T, order=1, mode="nearest")
            v = v + np.linalg.norm(L - Lc, axis=1) * grid.step
            dk = place_.s * v
            d = dk if d is None else np.minimum(d, dk)
        out[idx] = d
    return out.reshape(grid.shape)


def weights_on_grid(grid: mp.Grid, values: dict, model_lo, model_hi, sweep: str, joint_lo, joint_hi):
    """(influence weights or None, sweep positions or None) on every sample."""
    use_w = (values.get("influence", "all") or "all") != "all"
    use_s = sweep != "none"
    if not (use_w or use_s):
        return None, None
    w = np.empty(grid.size, dtype=np.float32) if use_w else None
    pos = np.empty(grid.size, dtype=np.float32) if use_s else None
    for s in range(0, grid.size, CHUNK):
        idx = np.arange(s, min(s + CHUNK, grid.size))
        P = grid.points(idx)
        if use_w:
            w[idx] = influence.weights(P, values, model_lo, model_hi)
        if use_s:
            pos[idx] = sweep_position(P, sweep, joint_lo, joint_hi)
    return (None if w is None else w.reshape(grid.shape)), (None if pos is None else pos.reshape(grid.shape))


def overlap_share(a, b) -> tuple[float, int, int]:
    ia, ib = a < 0, b < 0
    na, nb = int(ia.sum()), int(ib.sum())
    both = int((ia & ib).sum())
    return (both / max(min(na, nb), 1)), na, nb


def transition(fields: Fields, u: float, width: float) -> np.ndarray | float:
    """t at every sample for stage amount u."""
    t = progress_at(u, fields.pos, width) if fields.pos is not None else u
    if fields.w is not None:
        t = t * fields.w
    return t


def blend_field(fields: Fields, u: float, width: float, swell: float) -> np.ndarray:
    """F (<= 0 solid) for stage amount u; swell in canonical units."""
    t = transition(fields, u, width)
    F = fields.a + t * (fields.o - fields.a)
    if swell > 0:
        F = F - 4.0 * swell * t * (1.0 - t)
    return np.asarray(F, dtype=np.float32)


def fill_voids(F: np.ndarray, step: float) -> tuple[np.ndarray, int]:
    """Air sealed inside the solid (pockets where two overlapping shapes enclose a gap) made solid: only the air that
    reaches the grid's border stays. Returns (field, pockets filled)."""
    from scipy import ndimage
    air = F > 0
    labels, n = ndimage.label(air)
    if n <= 1:
        return F, 0
    border = np.unique(np.concatenate([labels[[0, -1]].ravel(), labels[:, [0, -1]].ravel(),
                                       labels[:, :, [0, -1]].ravel()]))
    sealed = air & ~np.isin(labels, border[border > 0])
    if not sealed.any():
        return F, 0
    out = F.copy()
    out[sealed] = -0.5 * step
    return out, int(n - len(border[border > 0]))


SPECK_FACES = 24                  # marching-cubes specks: a sample or two of solid grazing the level


def drop_specks(mesh):
    """Leaves out pieces of fewer than SPECK_FACES faces (single grid cells, nothing a plotter can show)."""
    import trimesh
    parts = mesh.split(only_watertight=False)
    if len(parts) <= 1:
        return mesh
    kept = [p for p in parts if len(p.faces) >= SPECK_FACES]
    if not kept or len(kept) == len(parts):
        return mesh
    return trimesh.util.concatenate(kept)


def is_target_everywhere(fields: Fields, u: float, width: float) -> bool:
    """True when t = 1 at every sample (Morph's end: the target itself)."""
    if u < 1.0 or fields.w is not None:
        return False
    return fields.pos is None or bool(np.all(progress_at(u, fields.pos, width) >= 1.0))
