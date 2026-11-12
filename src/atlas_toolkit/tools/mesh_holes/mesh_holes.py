"""
mesh_holes.py - Holes: carve a closed model with many solid cutters (straight tubes, blind bores, bubbles or
wormholes), placed on a lattice, radially, at random or on the surface, from a regular perforation to cheese.

Everything happens in the model's canonical frame (geom/frame3d: up = Z, front = -Y, 2 units tall), so sizes are %
of the height and settings carry over between models. The cut is exact (manifold3d):

    result = model - (union of cutters  ∩ the Acts-on region)

A hole is first a skeleton (a polyline, or a point for a bubble) with a radius, so placement, spacing and influence
work without geometry; cutters are built only for the holes that survive.

    profile     2D outline of a tube, bore or wormhole, normalised to the area of the unit circle (so Size means
                the same for every shape): circle, regular polygon, superellipse (pillow) or a seeded blob (a
                regular flower of `lobes` plus noise that only Irregularity brings in)
    sweep       the profile carried along a path with rotation-minimising frames, a scale and a turn per ring,
                closed with caps: one routine for tubes (2 rings, more when twisted), bores (plus a round or
                pointed tip) and wormholes (chunks of a long path, unioned, so a path that crosses itself far away
                is still a valid solid)
    bubble      a closed 3D shape of unit volume-equivalent radius: sphere, faceted crystal (convex hull),
                superellipsoid (pillow) or a seeded lumpy blob, stretched along its axis
    layout      lattice (lines on a square or hex grid along 1-3 axes, woven so families never meet; points on a
                cubic or close-packed grid for bubbles), radial (spokes around an axis or from a point; rings or
                shells for bubbles), random (inside the model) or surface (on its skin, aimed along the normal)
    spacing     with a minimum wall, holes are accepted largest first and kept that far from every accepted hole
                (segment-to-segment distance of the skeletons minus their reach), shrinking down to the smallest
                Size before they are dropped; 0 lets them merge into caverns

Irregularity is one number c in 0..1: every random draw is made once, from streams that do not depend on c, and
scaled by c, so raising it moves a regular lattice smoothly toward chaos (same seed, same holes, more disorder).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from atlas_toolkit.geom import influence
from atlas_toolkit.geom.frame3d import euler_xyz

FAMILIES = ("tube", "bore", "bubble", "worm")
LAYOUTS = ("lattice", "radial", "random", "surface")
PROFILES = ("circle", "polygon", "pillow", "blob")
MAX_HOLES = 5000                  # cutters per run (the boolean stays a few seconds below this)
MAX_TILT = math.radians(50.0)     # direction jitter at full irregularity
NUDGE = 1e-4                      # every cutter moves and resizes by up to this share of the height (see place)
WORM_CHUNK = 24                   # rings per wormhole piece (pieces are unioned)


# ----------------------------------------------------------------------------- small helpers

def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n > 0, n, 1.0)


def perpendicular(t) -> np.ndarray:
    """A unit vector perpendicular to unit t, chosen the same way for the same t (so a lattice's profiles line up)."""
    t = np.asarray(t, dtype=np.float64)
    a = np.array([1.0, 0.0, 0.0]) if abs(t[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    return unit(a - (a @ t) * t)


def tilt(d, angle: float, u: float) -> np.ndarray:
    """Unit d turned by `angle` toward a direction picked by u in 0..1 (the azimuth around d)."""
    e1 = perpendicular(d)
    e2 = np.cross(d, e1)
    az = 2 * math.pi * u
    return unit(math.cos(angle) * d + math.sin(angle) * (math.cos(az) * e1 + math.sin(az) * e2))


def fibonacci_sphere(n: int) -> np.ndarray:
    i = np.arange(n) + 0.5
    z = 1 - 2 * i / n
    r = np.sqrt(np.maximum(0.0, 1 - z * z))
    a = i * math.pi * (3 - math.sqrt(5))
    return np.stack([r * np.cos(a), r * np.sin(a), z], axis=1)


def random_dirs(rng, n: int) -> np.ndarray:
    return unit(rng.normal(size=(n, 3)))


# ----------------------------------------------------------------------------- 2D profiles

def profile(kind: str, segments: int = 40, sides: int = 6, roundness: float = 4.0, lobes: int = 5,
            lumpiness: float = 0.4, aspect: float = 1.0, shared: np.ndarray | None = None,
            own: np.ndarray | None = None, mix: float = 0.0) -> np.ndarray:
    """Outline (N, 2), counter-clockwise, area π (the unit circle's). `shared` and `own` are blob noise draws
    (phases and amplitudes, see blob_noise): the blob's noise is shared by every hole at mix 0 and its own at 1."""
    if kind == "polygon":
        n = max(3, int(sides))
        a = 2 * math.pi * np.arange(n) / n + math.pi / n - math.pi / 2
        pts = np.stack([np.cos(a), np.sin(a)], axis=1)
    else:
        n = max(8, int(segments))
        if kind == "blob":
            n = max(n, 6 * int(lobes) + 12)
        t = 2 * math.pi * np.arange(n) / n
        if kind == "pillow":
            e = 2.0 / max(float(roundness), 1.01)
            c, s = np.cos(t), np.sin(t)
            pts = np.stack([np.sign(c) * np.abs(c) ** e, np.sign(s) * np.abs(s) ** e], axis=1)
        elif kind == "blob":
            noise = _blob_noise_2d(t, shared, own, mix)
            r = 1.0 + lumpiness * (0.55 * np.cos(lobes * t) + 0.45 * noise)
            r = np.maximum(r, 0.25)
            pts = np.stack([r * np.cos(t), r * np.sin(t)], axis=1)
        else:
            pts = np.stack([np.cos(t), np.sin(t)], axis=1)
    if aspect != 1.0:
        k = math.sqrt(max(float(aspect), 1e-3))
        pts = pts * np.array([k, 1.0 / k])
    return pts * math.sqrt(math.pi / max(polygon_area(pts), 1e-12))


def polygon_area(P) -> float:
    x, y = P[:, 0], P[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def blob_noise(rng, k: int = 6) -> np.ndarray:
    """A blob's noise draw: k rows of (phase, amplitude, direction x, y, z) used by the 2D and the 3D blobs."""
    out = np.empty((k, 5))
    out[:, 0] = rng.uniform(0, 2 * math.pi, k)
    out[:, 1] = rng.uniform(0.4, 1.0, k)
    out[:, 2:] = random_dirs(rng, k)
    return out


def _blob_noise_2d(t, shared, own, mix: float) -> np.ndarray:
    def wave(d):
        if d is None:
            return np.zeros_like(t)
        w = sum(d[i, 1] * np.cos((i + 2) * t + d[i, 0]) / (i + 2) ** 0.5 for i in range(len(d)))
        return w / sum(d[i, 1] / (i + 2) ** 0.5 for i in range(len(d)))
    return (1 - mix) * wave(shared) + mix * wave(own)


# ----------------------------------------------------------------------------- sweeps (tubes, bores, worms)

def frames(P) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rotation-minimising frames along a polyline (double reflection): tangents T, normals E1, binormals E2 with
    (E1, E2, T) right-handed."""
    P = np.asarray(P, dtype=np.float64)
    k = len(P)
    T = np.empty((k, 3))
    T[0] = P[1] - P[0]
    T[-1] = P[-1] - P[-2]
    if k > 2:
        T[1:-1] = P[2:] - P[:-2]
    T = unit(T)
    E1 = np.empty((k, 3))
    E1[0] = perpendicular(T[0])
    for i in range(k - 1):
        v1 = P[i + 1] - P[i]
        c1 = v1 @ v1
        if c1 < 1e-24:
            E1[i + 1] = E1[i]
            continue
        rL = E1[i] - (2.0 / c1) * (v1 @ E1[i]) * v1
        tL = T[i] - (2.0 / c1) * (v1 @ T[i]) * v1
        v2 = T[i + 1] - tL
        c2 = v2 @ v2
        E1[i + 1] = rL - (2.0 / c2) * (v2 @ rL) * v2 if c2 > 1e-24 else rL
        E1[i + 1] = unit(E1[i + 1] - (E1[i + 1] @ T[i + 1]) * T[i + 1])
    E2 = np.cross(T, E1)
    return T, E1, E2


def sweep_mesh(outline, P, scale, turn, e1=None) -> tuple[np.ndarray, np.ndarray]:
    """A closed solid: `outline` (N, 2, CCW) placed at every point of P (k, 3), scaled by scale (k,) and turned by
    turn (k,) radians about the path, with flat caps at both ends. `e1` fixes the first frame's normal (a lattice's
    profiles all face the same way). Returns (V, F) with outward faces."""
    P = np.asarray(P, dtype=np.float64)
    T, E1, E2 = frames(P)
    if e1 is not None:
        turn = np.asarray(turn, float) + align_angle(T[0], E1[0], e1)
    k, n = len(P), len(outline)
    c, s = np.cos(turn), np.sin(turn)
    x, y = outline[:, 0], outline[:, 1]
    # ring i, point j: P + sc*(x cos - y sin) E1 + sc*(x sin + y cos) E2
    X = (x[None, :] * c[:, None] - y[None, :] * s[:, None]) * scale[:, None]
    Y = (x[None, :] * s[:, None] + y[None, :] * c[:, None]) * scale[:, None]
    V = P[:, None, :] + X[..., None] * E1[:, None, :] + Y[..., None] * E2[:, None, :]
    V = V.reshape(-1, 3)
    i = np.arange(k - 1)[:, None] * n
    j = np.arange(n)[None, :]
    jn = (j + 1) % n
    a, b, cc, d = i + j, i + jn, i + n + jn, i + n + j
    side = np.concatenate([np.stack([a, b, cc], -1).reshape(-1, 3), np.stack([a, cc, d], -1).reshape(-1, 3)])
    cap = cap_triangles(outline)
    F = np.concatenate([side, cap[:, ::-1], cap + (k - 1) * n]).astype(np.int64)
    if signed_volume(V, F) < 0:
        F = F[:, ::-1]
    return V, F


def align_angle(t, e1_now, e1_want) -> float:
    """The turn about t that brings the frame normal e1_now onto e1_want (projected across t); 0 when e1_want runs
    along t."""
    w = np.asarray(e1_want, float)
    w = w - (w @ t) * t
    if np.linalg.norm(w) < 1e-6:
        return 0.0
    w = unit(w)
    return math.atan2(float(np.cross(e1_now, w) @ t), float(e1_now @ w))


_CAPS: dict = {}


def cap_triangles(outline) -> np.ndarray:
    import manifold3d
    key = (len(outline), outline.tobytes())
    tri = _CAPS.get(key)
    if tri is None:
        tri = np.asarray(manifold3d.triangulate([np.ascontiguousarray(outline, dtype=np.float64)]), dtype=np.int64)
        if len(_CAPS) > 4096:
            _CAPS.clear()
        _CAPS[key] = tri
    return tri


def signed_volume(V, F) -> float:
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def to_manifold(V, F):
    import manifold3d
    mesh = manifold3d.Mesh(vert_properties=np.ascontiguousarray(V, dtype=np.float32),
                           tri_verts=np.ascontiguousarray(F, dtype=np.uint32))
    return manifold3d.Manifold(mesh)


def from_manifold(m) -> tuple[np.ndarray, np.ndarray]:
    mesh = m.to_mesh()
    V = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
    F = np.asarray(mesh.tri_verts, dtype=np.int64)
    return V, F


# ----------------------------------------------------------------------------- bubbles

def bubble_unit(kind: str, segments: int, sides: int, roundness: float, lumpiness: float, lobes: int,
                shared: np.ndarray, own: np.ndarray, mix: float, facets_jitter: np.ndarray | None = None):
    """A closed shape of the unit sphere's volume, centred at the origin, as a Manifold (not yet stretched,
    turned or placed)."""
    import manifold3d
    if kind == "polygon":
        n = max(4, 2 * int(sides) + 2)
        pts = fibonacci_sphere(n)
        if facets_jitter is not None:
            pts = unit(pts + facets_jitter[:n])
        m = manifold3d.Manifold.hull_points(pts)
    else:
        m = manifold3d.Manifold.sphere(1.0, max(8, int(segments)))
        if kind == "pillow":
            e = max(float(roundness), 1.01)

            def to_superellipsoid(P):
                U = unit(P)
                return U / (np.abs(U) ** e).sum(1, keepdims=True) ** (1.0 / e)
            m = m.warp_batch(to_superellipsoid)
        elif kind == "blob":
            def lumpy(P):
                U = unit(P)
                return U * blob_radius(U, lumpiness, lobes, shared, own, mix)[:, None]
            m = m.warp_batch(lumpy)
    vol = m.volume()
    if vol <= 0:
        raise ValueError("a bubble came out empty")
    return m.scale(np.full(3, (4.0 / 3.0 * math.pi / vol) ** (1.0 / 3.0)))


def blob_radius(U, lumpiness: float, lobes: int, shared, own, mix: float) -> np.ndarray:
    """Radius of a 3D blob along unit directions U: waves along the draw's directions, frequency from Lobes."""
    def wave(d):
        w = np.zeros(len(U))
        for i in range(len(d)):
            f = 0.5 * lobes * (0.6 + 0.8 * i / max(len(d) - 1, 1))
            w += d[i, 1] * np.cos(f * (U @ d[i, 2:]) * math.pi + d[i, 0])
        return w / np.sum(d[:, 1])
    r = 1.0 + lumpiness * 1.4 * ((1 - mix) * wave(shared) + mix * wave(own))
    return np.maximum(r, 0.3)


# ----------------------------------------------------------------------------- holes

@dataclass
class Hole:
    kind: str                     # tube | bore | bubble | worm
    path: np.ndarray              # (k, 3) where the cutter runs (bubble: its centre, k = 1)
    skeleton: np.ndarray          # the part that matters for spacing: inside the model, tip included
    axis: np.ndarray              # unit direction (bubble: its stretch axis)
    radius: float = 0.0           # equal-area radius (bubble: equal-volume)
    roll: float = 0.0             # profile turn about the path (radians)
    aspect: float = 1.0
    e1: np.ndarray | None = None  # the lattice's normal for this family, so profiles line up
    rnd: np.ndarray = field(default_factory=lambda: np.zeros(8))     # per-hole draws in 0..1
    reach: float = 0.0            # radius of the cutter around its skeleton
    shape: object = None          # profile outline (N, 2) or unit bubble (Manifold)
    scale: np.ndarray | None = None   # per-ring scale (worms)
    normal: np.ndarray | None = None  # outward surface normal at a surface hole's seed


@dataclass
class Model:
    """What placement needs to know about the model, canonical frame."""
    V: np.ndarray
    F: np.ndarray
    lo: np.ndarray
    hi: np.ndarray
    caster: object = None
    normals: np.ndarray = None

    @property
    def centre(self) -> np.ndarray:
        return (self.lo + self.hi) / 2.0

    @property
    def height(self) -> float:
        return float(self.hi[2] - self.lo[2])

    @property
    def radius(self) -> float:
        return float(np.linalg.norm(self.hi - self.lo)) / 2.0

    def ray(self):
        if self.caster is None:
            from atlas_toolkit.geom.raycast import Caster
            self.caster = Caster(self.V, self.F)
        return self.caster

    def signed_distance(self, P) -> tuple[np.ndarray, np.ndarray]:
        """(distance, closest face): negative inside (generalised winding number, so small gaps are bridged)."""
        import igl
        P = np.ascontiguousarray(np.atleast_2d(P), dtype=np.float64)
        if not len(P):
            return np.zeros(0), np.zeros(0, dtype=np.int64)
        S, I, _C = igl.signed_distance(P, self.V, self.F,
                                       sign_type=igl.SIGNED_DISTANCE_TYPE_FAST_WINDING_NUMBER)[:3]
        return np.asarray(S, dtype=np.float64), np.asarray(I, dtype=np.int64)

    def face_normals(self) -> np.ndarray:
        if self.normals is None:
            a, b, c = (self.V[self.F[:, i]] for i in range(3))
            self.normals = unit(np.cross(b - a, c - a))
        return self.normals

    def chord(self, origins, dirs) -> tuple[np.ndarray, np.ndarray]:
        """Where lines (origin, unit dir) first enter and last leave the model, as parameters along dir (nan when
        the line misses it)."""
        R = self.radius * 3.0 + float(np.linalg.norm(self.centre))
        o = np.asarray(origins, float)
        d = np.asarray(dirs, float)
        far = o - d * R
        t_in, _, _, _ = self.ray().intersect(far, d)
        back = o + d * R
        t_out, _, _, _ = self.ray().intersect(back, -d)
        a = np.where(np.isfinite(t_in), t_in - R, np.nan)
        b = np.where(np.isfinite(t_out), R - t_out, np.nan)
        return a, b

    def box_chord(self, o, d, margin: float) -> tuple[float, float] | None:
        """The parameter interval of the line o + t d inside the bounding box grown by margin."""
        lo, hi = self.lo - margin, self.hi + margin
        with np.errstate(divide="ignore", invalid="ignore"):
            t1 = (lo - o) / d
            t2 = (hi - o) / d
        tmin = np.where(np.abs(d) > 1e-12, np.minimum(t1, t2), -np.inf)
        tmax = np.where(np.abs(d) > 1e-12, np.maximum(t1, t2), np.inf)
        inside = np.all((np.abs(d) > 1e-12) | ((o >= lo) & (o <= hi)))
        a, b = float(np.max(tmin)), float(np.min(tmax))
        if not inside or a >= b:
            return None
        return a, b


# ----------------------------------------------------------------------------- settings in canonical units

@dataclass
class Settings:
    family: str
    layout: str
    size: float                   # canonical diameters
    size_max: float
    size_mix: str
    depth: float
    depth_max: float
    length: float
    length_max: float
    tip: str
    sink: float
    gap: float
    axes: str
    spacing: float
    grid: str
    weave: bool
    both_sides: bool
    turn: tuple
    shift: tuple
    centre_mode: str
    centre: tuple
    spokes: int
    spiral: float
    count: int
    aim: str
    profile: str
    sides: int
    roundness: float
    lobes: int
    lumpiness: float
    aspect: float
    roll: float
    twist: float                  # radians per canonical unit
    taper: float
    waviness: float
    wavelength: float
    swell: float
    chaos: float
    drop: float
    jitter_pos: float
    jitter_dir: float
    jitter_shape: float
    segments: int
    seed: int

    @classmethod
    def from_values(cls, v: dict, height: float, segments: int) -> "Settings":
        pct = height / 100.0
        lo, hi = sorted((float(v["size"]), float(v["size_max"])))
        d0, d1 = sorted((float(v["depth"]), float(v["depth_max"])))
        l0, l1 = sorted((float(v["length"]), float(v["length_max"])))
        return cls(
            family=v["family"], layout=v["layout"], size=lo * pct, size_max=hi * pct, size_mix=v["size_mix"],
            depth=d0 * pct, depth_max=d1 * pct, length=l0 * pct, length_max=l1 * pct, tip=v["tip"],
            sink=float(v["sink"]), gap=float(v["gap"]) * pct, axes=v["axes"], spacing=float(v["spacing"]) * pct,
            grid=v["grid"], weave=bool(v["weave"]), both_sides=bool(v["both_sides"]),
            turn=tuple(float(a) for a in v["lattice_turn"]), shift=tuple(float(a) for a in v["lattice_shift"]),
            centre_mode=v["centre_mode"], centre=tuple(float(a) for a in v["radial_centre"]),
            spokes=int(v["spokes"]), spiral=math.radians(float(v["spiral"])), count=int(v["count"]), aim=v["aim"],
            profile=v["profile"], sides=int(v["sides"]), roundness=float(v["roundness"]), lobes=int(v["lobes"]),
            lumpiness=float(v["lumpiness"]), aspect=float(v["aspect"]), roll=math.radians(float(v["roll"])),
            twist=math.radians(float(v["twist"])) / (10.0 * pct), taper=float(v["taper"]),
            waviness=float(v["waviness"]) * pct, wavelength=max(float(v["wavelength"]) * pct, 1e-6),
            swell=float(v["swell"]), chaos=float(v["chaos"]), drop=float(v["drop"]) / 100.0,
            jitter_pos=float(v["jitter_pos"]), jitter_dir=float(v["jitter_dir"]),
            jitter_shape=float(v["jitter_shape"]), segments=int(segments), seed=int(v["seed"]))

    @property
    def c_pos(self) -> float:
        return self.chaos * self.jitter_pos

    @property
    def c_dir(self) -> float:
        return self.chaos * self.jitter_dir

    @property
    def c_shape(self) -> float:
        return min(1.0, self.chaos * self.jitter_shape)


def streams(seed: int) -> dict:
    """Independent random streams, so turning one setting does not reshuffle the others."""
    names = ("place", "size", "jitter", "shape", "drop", "worm", "nudge")
    seqs = np.random.SeedSequence(int(seed)).spawn(len(names))
    return {n: np.random.default_rng(s) for n, s in zip(names, seqs)}


def draw_sizes(s: Settings, u: np.ndarray) -> np.ndarray:
    """Diameters from uniform draws u: even, mostly small or mostly large across [size, size_max]."""
    if s.size_mix == "small":
        u = u ** 2.5
    elif s.size_mix == "large":
        u = 1 - (1 - u) ** 2.5
    return s.size + (s.size_max - s.size) * u


# ----------------------------------------------------------------------------- layouts

def lattice_axes(s: Settings) -> list[int]:
    return ["xyz".index(c) for c in s.axes]


def lattice_frame(s: Settings) -> np.ndarray:
    return euler_xyz(s.turn)


def lattice_lines(model: Model, s: Settings) -> list[tuple[np.ndarray, np.ndarray, np.ndarray, int]]:
    """(origin, direction, profile normal e1, family) of every lattice line near the model."""
    R = lattice_frame(s)
    sp = s.spacing
    reach = model.radius + sp
    n = int(math.ceil(reach / sp)) + 1
    out = []
    axes = lattice_axes(s)
    for fam, a in enumerate(axes):
        u_ax, v_ax = [k for k in range(3) if k != a]
        if s.weave and len(axes) > 1:
            # X lines at (y, z) = (0, 0), Y lines at (x, z) = (0, 1/2), Z lines at (x, y) = (1/2, 1/2): never meet
            off = {0: (0.0, 0.0), 1: (0.0, 0.5), 2: (0.5, 0.5)}[a]
        else:
            off = (0.0, 0.0)
        shift = np.asarray(s.shift) / 100.0
        for j in range(-n, n + 1):
            row_dv = math.sqrt(3) / 2 if s.grid == "hex" else 1.0
            for i in range(-n, n + 1):
                du = i + off[0] + shift[u_ax] + (0.5 * (j % 2) if s.grid == "hex" else 0.0)
                dv = j * row_dv + off[1] + shift[v_ax]
                local = np.zeros(3)
                local[u_ax], local[v_ax] = du * sp, dv * sp
                if math.hypot(local[u_ax], local[v_ax]) > reach:
                    continue
                o = model.centre + R @ local
                out.append((o, R[:, a].copy(), R[:, u_ax].copy(), fam))
    return out


def lattice_points(model: Model, s: Settings) -> list[tuple[np.ndarray, np.ndarray]]:
    """(centre, axis) of bubbles on a cubic grid, or on close-packed layers for hex."""
    R = lattice_frame(s)
    sp = s.spacing
    shift = np.asarray(s.shift) / 100.0
    half = (model.hi - model.lo) / 2.0 + sp
    rad = float(np.linalg.norm(half))
    n = int(math.ceil(rad / sp)) + 1
    dz = sp * math.sqrt(2) / 2 if s.grid == "hex" else sp
    nz = int(math.ceil(rad / dz)) + 1
    axis = R[:, lattice_axes(s)[0]]
    out = []
    for k in range(-nz, nz + 1):
        lay = (0.5, 0.5) if (s.grid == "hex" and k % 2) else (0.0, 0.0)
        for j in range(-n, n + 1):
            for i in range(-n, n + 1):
                local = np.array([(i + lay[0] + shift[0]) * sp, (j + lay[1] + shift[1]) * sp,
                                  k * dz + shift[2] * sp])
                p = model.centre + R @ local
                if np.all(np.abs(p - model.centre) <= half):
                    out.append((p, axis))
    return out


def radial_spokes(model: Model, s: Settings, full_lines: bool) -> list[tuple[np.ndarray, np.ndarray]]:
    """(origin, direction) of spokes. Axis mode: rows up the turned Z axis, `spokes` per row (half a turn for full
    lines through the axis), each row turned by Spiral (and half a step for hex). Point mode: Fibonacci directions
    from the centre (a hemisphere for full lines)."""
    R = lattice_frame(s)
    C = model.centre + np.asarray(s.centre) / 100.0 * (model.hi - model.lo)
    out = []
    if s.centre_mode == "point":
        n = max(1, s.spokes)
        D = fibonacci_sphere(2 * n if full_lines else n)
        if full_lines:
            D = D[D[:, 2] >= 0][:n] if (D[:, 2] >= 0).sum() >= n else D[:n]
        for d in D:
            out.append((C.copy(), R @ d))
        return out
    sp = s.spacing
    z_ax, x_ax, y_ax = R[:, 2], R[:, 0], R[:, 1]
    span = model.radius + sp
    rows = int(math.ceil(span / sp))
    sweep = math.pi if full_lines else 2 * math.pi
    for r in range(-rows, rows + 1):
        o = C + z_ax * (r * sp + s.shift[2] / 100.0 * sp)
        base = r * s.spiral + (0.5 * sweep / s.spokes if (s.grid == "hex" and r % 2) else 0.0)
        for j in range(s.spokes):
            a = base + sweep * j / s.spokes
            out.append((o, math.cos(a) * x_ax + math.sin(a) * y_ax))
    return out


def radial_points(model: Model, s: Settings) -> list[tuple[np.ndarray, np.ndarray]]:
    """Bubbles in rings around the axis (axis mode, rows up the axis) or shells around the point."""
    R = lattice_frame(s)
    C = model.centre + np.asarray(s.centre) / 100.0 * (model.hi - model.lo)
    sp = s.spacing
    span = model.radius + sp
    out = []
    if s.centre_mode == "point":
        out.append((C.copy(), R[:, 2].copy()))
        for k in range(1, int(math.ceil(span / sp)) + 1):
            rho = k * sp
            m = max(4, int(round(4 * math.pi * rho * rho / (sp * sp))))
            for d in fibonacci_sphere(m):
                out.append((C + rho * (R @ d), R @ d))
        return out
    z_ax, x_ax, y_ax = R[:, 2], R[:, 0], R[:, 1]
    rows = int(math.ceil(span / sp))
    dz = sp * math.sqrt(3) / 2 if s.grid == "hex" else sp
    for r in range(-int(math.ceil(span / dz)), int(math.ceil(span / dz)) + 1):
        o = C + z_ax * (r * dz + s.shift[2] / 100.0 * sp)
        out.append((o, z_ax.copy()))
        for k in range(1, rows + 1):
            rho = k * sp
            m = max(3, int(round(2 * math.pi * rho / sp)))
            base = r * s.spiral + (math.pi / m if (s.grid == "hex" and r % 2) else 0.0)
            for j in range(m):
                a = base + 2 * math.pi * j / m
                d = math.cos(a) * x_ax + math.sin(a) * y_ax
                out.append((o + rho * d, d))
    return out


def inside_points(model: Model, n: int, rng, margin: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """n points uniformly inside the model (or within `margin` outside), with their closest face."""
    pts, faces = [], []
    got = 0
    for _ in range(40):
        P = model.lo - margin + rng.random((max(64, 3 * (n - got)), 3)) * (model.hi - model.lo + 2 * margin)
        d, f = model.signed_distance(P)
        ok = d < margin
        pts.append(P[ok])
        faces.append(f[ok])
        got += int(ok.sum())
        if got >= n:
            break
    P = np.concatenate(pts)[:n] if pts else np.zeros((0, 3))
    F = np.concatenate(faces)[:n] if faces else np.zeros(0, dtype=np.int64)
    return P, F


def surface_points(model: Model, n: int, rng) -> tuple[np.ndarray, np.ndarray]:
    """n points spread evenly over the surface (Poisson-like), with their outward normals."""
    import trimesh
    m = trimesh.Trimesh(model.V, model.F, process=False)
    area = float(m.area)
    radius = math.sqrt(area / max(n, 1) / math.pi) * 0.9
    P, fi = trimesh.sample.sample_surface_even(m, n, radius=radius, seed=rng)
    if len(P) < n:                       # even sampling ran short: top up with plain samples
        Q, fq = trimesh.sample.sample_surface(m, n - len(P), seed=rng)
        P, fi = np.concatenate([P, Q]), np.concatenate([fi, fq])
    return np.asarray(P, float)[:n], model.face_normals()[np.asarray(fi)[:n]]


def aim_dirs(model: Model, s: Settings, P, normals, rng) -> np.ndarray:
    """Directions for random and surface holes: inward along the normal, along the lattice axes, toward the
    centre (axis or point) or random."""
    n = len(P)
    if s.aim == "normal":
        return -normals
    if s.aim == "axes":
        R = lattice_frame(s)
        ax = lattice_axes(s)
        pick = rng.integers(0, len(ax), n)
        sign = np.where(rng.random(n) < 0.5, -1.0, 1.0)
        return R[:, [ax[k] for k in pick]].T * sign[:, None]
    if s.aim == "centre":
        R = lattice_frame(s)
        C = model.centre + np.asarray(s.centre) / 100.0 * (model.hi - model.lo)
        D = C - P
        if s.centre_mode != "point":
            z = R[:, 2]
            D = D - (D @ z)[:, None] * z
        bad = np.linalg.norm(D, axis=1) < 1e-9
        D[bad] = R[:, 0]
        return unit(D)
    return random_dirs(rng, n)


def candidates(model: Model, s: Settings, rs: dict, log=None) -> list[Hole]:
    """Every hole the layout proposes, with skeleton and radius, before influence, drop and spacing."""
    fam, lay = s.family, s.layout
    holes: list[Hole] = []
    rng = rs["place"]

    def add(kind, path, axis, e1=None):
        holes.append(Hole(kind, np.atleast_2d(np.asarray(path, float)), np.atleast_2d(np.asarray(path, float)),
                          unit(axis), e1=e1))

    if fam == "bubble":
        if lay == "lattice":
            for p, a in lattice_points(model, s):
                add("bubble", p, a)
        elif lay == "radial":
            for p, a in radial_points(model, s):
                add("bubble", p, a)
        elif lay == "random":
            P, fi = inside_points(model, s.count, rng, margin=0.25 * s.size_max)
            A = aim_dirs(model, s, P, model.face_normals()[fi] if len(fi) else np.zeros((0, 3)), rng)
            for p, a in zip(P, A):
                add("bubble", p, a)
        else:
            P, N = surface_points(model, s.count, rng)
            A = aim_dirs(model, s, P, N, rng)
            for p, nrm, a in zip(P, N, A):
                add("bubble", p, a)
                holes[-1].normal = nrm      # sink is applied once the radius is known
    else:
        lines: list[tuple] = []         # (origin, dir, e1)
        if lay == "lattice":
            for o, d, e1, _fam in lattice_lines(model, s):
                if fam == "bore" and s.both_sides:
                    lines.append((o, d, e1))
                    lines.append((o, -d, e1))
                else:
                    lines.append((o, d, e1))
        elif lay == "radial":
            for o, d in radial_spokes(model, s, full_lines=(fam == "tube")):
                lines.append((o, d, None))
        else:
            if lay == "random":
                P, fi = inside_points(model, s.count, rng)
                N = model.face_normals()[fi] if len(fi) else np.zeros((0, 3))
            else:
                P, N = surface_points(model, s.count, rng)
            D = aim_dirs(model, s, P, N, rng)
            for p, d, nrm in zip(P, D, N):
                lines.append((p, d, None, nrm))
        for item in lines:
            o, d = item[0], unit(item[1])
            holes.append(Hole(fam, np.atleast_2d(o), np.atleast_2d(o), d, e1=item[2],
                              normal=item[3] if len(item) > 3 else None))
    if len(holes) > 40 * MAX_HOLES:
        raise ValueError(f"the layout proposes {len(holes)} holes: raise Spacing or lower Count")
    for h in holes:
        h.rnd = rs["jitter"].random(8)
    return holes


# ----------------------------------------------------------------------------- shaping the candidates

def place(model: Model, s: Settings, holes: list[Hole], rs: dict, progress=None) -> list[Hole]:
    """Sizes, jitter, paths and skeletons, and the cull of holes that never touch the model."""
    n = len(holes)
    if not n:
        return []
    sizes = draw_sizes(s, rs["size"].random(n))
    depth_u = rs["size"].random(n)
    length_u = rs["size"].random(n)
    pct = model.height / 100.0
    margin = 2.0 * pct
    out = []
    regular = s.layout in ("lattice", "radial")
    # A nudge far below what a pen can show keeps cutters from meeting exactly (radial tubes through one point,
    # lattice tubes of one size side by side): exact coincidences come out of the boolean as coincident vertices,
    # which a float STL merges into non-manifold edges.
    nudge = rs["nudge"].random((n, 4)) - 0.5
    eps = NUDGE * model.height
    for i, h in enumerate(holes):
        h.radius = sizes[i] / 2.0 * (1.0 + NUDGE * nudge[i, 3])
        h.path = h.path + nudge[i, :3] * eps
        r = h.rnd
        if regular and s.c_pos > 0:
            step = s.spacing * 0.5 * s.c_pos
            off = (r[0] * 2 - 1, r[1] * 2 - 1, r[2] * 2 - 1)
            jit = np.asarray(off) * step
            if h.kind != "bubble":
                jit = jit - (jit @ h.axis) * h.axis          # slide lines sideways only
            h.path = h.path + jit
        if s.c_dir > 0 and not (s.layout in ("random", "surface") and s.aim == "random"):
            h.axis = tilt(h.axis, MAX_TILT * s.c_dir * r[3], r[4])
        h.roll = s.roll + s.c_shape * (r[5] * 2 - 1) * math.pi
        h.aspect = s.aspect * math.exp(s.c_shape * (r[6] - 0.5) * 0.9)
    if s.family == "bubble":
        P = np.array([h.path[0] for h in holes])
        d, _ = model.signed_distance(P)
        for i, h in enumerate(holes):
            if s.layout == "surface":
                h.path = (h.path[0] - h.normal * s.sink * h.radius)[None, :]
                h.skeleton = h.path
                out.append(h)
            elif d[i] < 0.5 * h.radius:
                h.skeleton = h.path
                out.append(h)
        return out
    # lines: tubes, bores and worms
    O = np.array([h.path[0] for h in holes])
    D = np.array([h.axis for h in holes])
    t_in, t_out = model.chord(O, D)
    for i, h in enumerate(holes):
        if progress and i % 200 == 0:
            progress(i / n)
        o, dvec = h.path[0], h.axis
        if s.family == "tube":
            if not np.isfinite(t_in[i]):
                continue
            box = model.box_chord(o, dvec, margin + 4 * h.radius)
            if box is None:
                continue
            h.path = np.array([o + dvec * box[0], o + dvec * box[1]])
            h.skeleton = np.array([o + dvec * t_in[i], o + dvec * t_out[i]])
            out.append(h)
        elif s.family == "bore":
            if s.layout == "surface":
                entry = o
            else:
                # where a bore along dir first enters the model (behind a random point inside it)
                if not np.isfinite(t_in[i]):
                    continue
                entry = o + dvec * t_in[i]
            depth = s.depth + (s.depth_max - s.depth) * depth_u[i]
            over = 2.0 * h.radius * max(h.aspect, 1 / h.aspect) ** 0.5 + margin
            h.path = np.array([entry - dvec * over, entry + dvec * depth])
            tip = h.radius * s.taper if s.tip != "flat" else 0.0
            h.skeleton = np.array([entry, entry + dvec * (depth + tip)])
            out.append(h)
        else:   # worm
            if s.layout in ("lattice", "radial"):
                if s.layout == "lattice":
                    if not np.isfinite(t_in[i]):
                        continue
                    a, b = t_in[i] - 4 * h.radius - margin, t_out[i] + 4 * h.radius + margin
                    start = o + dvec * a
                    length = b - a
                else:
                    if not np.isfinite(t_out[i]) or t_out[i] <= 0:
                        continue
                    start = o
                    length = t_out[i] + 4 * h.radius + margin
            else:
                length = s.length + (s.length_max - s.length) * length_u[i]
                start = o
                if s.layout == "surface":
                    start = o + h.normal * (h.radius + margin)
            path, sc = worm_path(start, dvec, length, h, s, rs["worm"], h.e1)
            h.path, h.scale = path, sc
            h.skeleton = path
            out.append(h)
    return out


def worm_path(start, d0, length: float, h: Hole, s: Settings, rng, e1=None) -> tuple[np.ndarray, np.ndarray]:
    """A wormhole's centre line and ring scales: a heading that wanders (a smooth random turn, by Irregularity),
    plus a sideways wave (Waviness, Wavelength), with the bend kept wider than the tube so it never folds. The
    radius swells along it; the ends round off inside the model."""
    r = h.radius * max(h.aspect, 1 / h.aspect) ** 0.5
    ds = float(np.clip(0.5 * r, length / 400.0, length / 12.0))
    k = max(3, int(math.ceil(length / ds)) + 1)
    ds = length / (k - 1)
    max_turn = ds / (1.6 * r)                    # radians per step: bend radius >= 1.6 reach
    wander = s.c_dir * 0.9 * max_turn
    d = unit(d0)
    P = np.empty((k, 3))
    P[0] = start
    omega = np.zeros(3)
    noise = rng.normal(size=(k, 3))
    for i in range(1, k):
        if wander > 0:
            omega = 0.85 * omega + 0.15 * noise[i] * wander * 3.0
            om = omega - (omega @ d) * d
            ang = float(np.linalg.norm(om))
            if ang > max_turn:
                om *= max_turn / ang
                ang = max_turn
            if ang > 1e-12:
                axis = unit(om)
                side = np.cross(axis, d)
                d = unit(d * math.cos(ang) + side * math.sin(ang))
        P[i] = P[i - 1] + d * ds
    svals = np.arange(k) * ds
    if s.waviness > 0:
        T, E1, E2 = frames(P)
        if e1 is not None:
            E1 = np.repeat(unit(np.asarray(e1, float))[None, :], k, 0)
            E1 = unit(E1 - (E1 * T).sum(1, keepdims=True) * T)
        amp = s.waviness / 2.0
        kk = 2 * math.pi / s.wavelength
        if amp * kk * kk > 1.0 / (1.6 * r):      # keep the wave's bend wider than the tube
            amp = 1.0 / (1.6 * r * kk * kk)
        phase = 2 * math.pi * h.rnd[7] * s.c_pos
        P = P + (amp * np.sin(kk * svals + phase))[:, None] * E1
    swell = np.ones(k)
    if s.swell > 0:
        reg = np.sin(2 * math.pi * svals / s.wavelength + math.pi / 2)
        ph = rng.uniform(0, 2 * math.pi, 3)
        rnd = sum(np.sin(2 * math.pi * svals / (s.wavelength * f) + p) for f, p in zip((0.7, 1.3, 2.1), ph)) / 2.0
        swell = 1.0 + s.swell * 0.6 * ((1 - s.c_shape) * reg + s.c_shape * np.clip(rnd, -1, 1))
    end = np.minimum(svals, svals[-1] - svals)
    ramp = np.sqrt(np.clip(1 - (1 - np.clip(end / max(r, 1e-9), 0, 1)) ** 2, 0.0, 1.0))
    sc = np.maximum(swell * np.maximum(ramp, 0.08), 0.05)
    return P, sc


def apply_influence(holes: list[Hole], values: dict, lo, hi) -> list[Hole]:
    """Scale each hole by the influence weight at its most affected point; drop the ones it leaves out."""
    if (values.get("influence", "all") or "all") == "all":
        return holes
    out = []
    for h in holes:
        w = float(influence.weights(densify(h.skeleton, 12), values, lo, hi).max())
        if w < 0.05:
            continue
        h.radius *= w
        out.append(h)
    return out


def densify(P, n: int) -> np.ndarray:
    P = np.atleast_2d(P)
    if len(P) == 1:
        return P
    t = np.linspace(0, 1, n)
    seg = np.minimum((t * (len(P) - 1)).astype(int), len(P) - 2)
    f = t * (len(P) - 1) - seg
    return P[seg] * (1 - f)[:, None] + P[seg + 1] * f[:, None]


# ----------------------------------------------------------------------------- shapes and reach

def shape_holes(holes: list[Hole], s: Settings, rs: dict) -> None:
    """Each hole's outline (or unit bubble) and its reach around the skeleton."""
    shared = blob_noise(np.random.default_rng(np.random.SeedSequence(s.seed).spawn(1)[0]))
    srng = rs["shape"]
    jitters = None
    for h in holes:
        own = blob_noise(srng)
        if s.family == "bubble":
            if s.profile == "polygon":
                jitters = srng.normal(scale=0.35 * s.c_shape, size=(2 * max(4, s.sides) + 2, 3))
            unit_shape = bubble_unit(s.profile, s.segments, s.sides, s.roundness, s.lumpiness, s.lobes, shared, own,
                                     s.c_shape, jitters)
            bb = np.asarray(unit_shape.bounding_box()).reshape(2, 3)
            ext = float(np.abs(bb).max())
            h.shape = unit_shape
            h.reach = h.radius * ext * max(h.aspect ** (2 / 3), h.aspect ** (-1 / 3))
        else:
            h.shape = profile(s.profile, s.segments, s.sides, s.roundness, s.lobes, s.lumpiness, h.aspect,
                              shared, own, s.c_shape)
            k = float(np.linalg.norm(h.shape, axis=1).max())
            sc = 1.0 if h.scale is None else float(np.max(h.scale))
            h.reach = h.radius * k * max(sc, s.taper if s.family != "worm" else 1.0)


# ----------------------------------------------------------------------------- spacing

def seg_dist(P0, P1, Q0, Q1) -> np.ndarray:
    """Distances between segments P0P1 and Q0Q1 (broadcast over the leading axis), Ericson's closest points."""
    d1, d2, r = P1 - P0, Q1 - Q0, P0 - Q0
    a = np.einsum("...i,...i", d1, d1)
    e = np.einsum("...i,...i", d2, d2)
    f = np.einsum("...i,...i", d2, r)
    c = np.einsum("...i,...i", d1, r)
    b = np.einsum("...i,...i", d1, d2)
    eps = 1e-18
    denom = a * e - b * b
    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.where(denom > eps, np.clip((b * f - c * e) / np.where(denom > eps, denom, 1), 0, 1), 0.0)
        t = np.where(e > eps, (b * s + f) / np.where(e > eps, e, 1), 0.0)
        s = np.where(t < 0, np.where(a > eps, np.clip(-c / np.where(a > eps, a, 1), 0, 1), 0.0), s)
        s = np.where(t > 1, np.where(a > eps, np.clip((b - c) / np.where(a > eps, a, 1), 0, 1), 0.0), s)
        t = np.clip(t, 0, 1)
    cp = P0 + d1 * s[..., None]
    cq = Q0 + d2 * t[..., None]
    return np.linalg.norm(cp - cq, axis=-1)


def segments_of(skel, cell: float) -> tuple[np.ndarray, np.ndarray]:
    """A skeleton as segments no longer than `cell` (a point: one zero-length segment). A list of polylines is
    several separate parts (Rods: the two ends of a rod outside the model, not the chord inside it)."""
    if isinstance(skel, (list, tuple)):
        parts = [segments_of(p, cell) for p in skel]
        return np.concatenate([a for a, _ in parts]), np.concatenate([b for _, b in parts])
    P = np.atleast_2d(skel)
    if len(P) == 1:
        return P.copy(), P.copy()
    A, B = [], []
    for p, q in zip(P[:-1], P[1:]):
        m = max(1, int(math.ceil(np.linalg.norm(q - p) / cell)))
        t = np.linspace(0, 1, m + 1)[:, None]
        pts = p + (q - p) * t
        A.append(pts[:-1])
        B.append(pts[1:])
    return np.concatenate(A), np.concatenate(B)


def space(holes: list[Hole], gap: float, min_radius: float, fixed: bool, progress=None) -> tuple[list[Hole], int]:
    """Keep holes at least `gap` apart (wall between their surfaces), largest first; a hole that does not fit
    shrinks down to `min_radius` (unless sizes are fixed) before it is dropped. Returns (kept, shrunk)."""
    if gap <= 0 or not holes:
        return holes, 0
    order = sorted(range(len(holes)), key=lambda i: -holes[i].reach)
    reach_max = max(h.reach for h in holes)
    cell = max(2 * reach_max + gap, 1e-6)
    grid: dict = defaultdict(list)
    SA, SB, SR = np.empty((1024, 3)), np.empty((1024, 3)), np.empty(1024)   # accepted segments, their reach
    used = 0
    kept, shrunk = [], 0
    for c_i, i in enumerate(order):
        if progress and c_i % 100 == 0:
            progress(c_i / len(order))
        h = holes[i]
        A, B = segments_of(h.skeleton, cell)
        mids = (A + B) / 2.0
        keys = {tuple(k) for k in np.floor(mids / cell).astype(int)}
        near = set()
        for kx, ky, kz in keys:
            for dx in (-2, -1, 0, 1, 2):
                for dy in (-2, -1, 0, 1, 2):
                    for dz in (-2, -1, 0, 1, 2):
                        near.update(grid.get((kx + dx, ky + dy, kz + dz), ()))
        allowed = math.inf
        if near:
            idx = np.fromiter(near, dtype=np.int64)
            QA, QB, QR = SA[idx], SB[idx], SR[idx]
            D = seg_dist(A[:, None, :], B[:, None, :], QA[None, :, :], QB[None, :, :])
            allowed = float((D - QR[None, :] - gap).min())
        if allowed < h.reach:
            factor = allowed / h.reach if h.reach > 0 else 0.0
            floor = min_radius if h.radius >= min_radius else 0.5 * h.radius      # Acts on made it smaller
            if fixed or factor <= 0 or h.radius * factor < floor:
                continue
            h.radius *= factor
            h.reach *= factor
            shrunk += 1
        kept.append(h)
        if used + len(A) > len(SR):
            size = max(2 * len(SR), used + len(A))
            SA, SB, SR = (np.resize(SA, (size, 3)), np.resize(SB, (size, 3)), np.resize(SR, size))
        SA[used:used + len(A)], SB[used:used + len(A)], SR[used:used + len(A)] = A, B, h.reach
        for j, k in enumerate(map(tuple, np.floor(mids / cell).astype(int))):
            grid[k].append(used + j)
        used += len(A)
    return kept, shrunk


# ----------------------------------------------------------------------------- cutters

def cutter(h: Hole, s: Settings):
    """The solid of one hole, canonical frame, as a Manifold (a worm may be a union of pieces)."""
    import manifold3d
    if h.kind == "bubble":
        a = h.aspect
        m = h.shape.scale(np.array([a ** (-1 / 3), a ** (-1 / 3), a ** (2 / 3)]) * h.radius)
        ez = h.axis
        ex = perpendicular(ez)
        ey = np.cross(ez, ex)
        c, sn = math.cos(h.roll), math.sin(h.roll)
        ex, ey = c * ex + sn * ey, -sn * ex + c * ey
        M = np.column_stack([ex, ey, ez, h.path[0]])
        return m.transform(M)
    outline = h.shape * h.radius
    P = h.path
    if h.kind == "tube":
        L = float(np.linalg.norm(P[1] - P[0]))
        rings = max(2, int(math.ceil(abs(s.twist) * L / math.radians(8))) + 1)
        if s.taper != 1.0:
            rings = max(rings, 2)
        t = np.linspace(0, 1, rings)
        Pr = P[0] + (P[1] - P[0]) * t[:, None]
        scale = 1.0 + (s.taper - 1.0) * t
        turn = h.roll + s.twist * L * t
        V, F = sweep_mesh(outline, Pr, scale, turn, h.e1)
        return to_manifold(V, F)
    if h.kind == "bore":
        start, end = P[0], P[1]
        L = float(np.linalg.norm(end - start))
        d = unit(end - start)
        over = float(np.linalg.norm(h.skeleton[0] - start))
        rings = max(2, int(math.ceil(abs(s.twist) * L / math.radians(8))) + 1)
        z = np.linspace(0, L, rings)
        z = np.unique(np.concatenate([z, [over]]))
        frac = np.clip((z - over) / max(L - over, 1e-9), 0, 1)
        scale = 1.0 + (s.taper - 1.0) * frac
        tip_h = h.radius * s.taper
        if s.tip == "round":
            phi = np.linspace(0, math.pi / 2, 9)[1:]
            z = np.concatenate([z, L + tip_h * np.sin(phi)])
            scale = np.concatenate([scale, s.taper * np.maximum(np.cos(phi), 0.06)])
        elif s.tip == "pointed":
            z = np.concatenate([z, [L + tip_h]])
            scale = np.concatenate([scale, [0.03]])
        Pr = start + d[None, :] * z[:, None]
        turn = h.roll + s.twist * z
        V, F = sweep_mesh(outline, Pr, scale, turn, h.e1)
        return to_manifold(V, F)
    # worm: chunks of the path, unioned
    k = len(P)
    sc = h.scale if h.scale is not None else np.ones(k)
    arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
    return sweep_chunks(outline, P, sc, h.roll + s.twist * arc, h.e1)


def sweep_chunks(outline, P, scale, turn, e1=None):
    """A swept solid along a long polyline as a union of pieces of WORM_CHUNK rings, so a path that crosses itself
    far away is still valid. Neighbours overlap by two rings and each piece's ring at a seam is shrunk by 2%, so
    it lies strictly inside the other piece: a shared ring would leave coincident faces, which the union turns
    into pinched edges."""
    import manifold3d
    P = np.asarray(P, dtype=np.float64)
    k = len(P)
    scale = np.asarray(scale, dtype=np.float64)
    turn = np.asarray(turn, dtype=np.float64)
    if k <= WORM_CHUNK:
        V, F = sweep_mesh(outline, P, scale, turn, e1)
        return to_manifold(V, F)
    T0, E10, _ = frames(P)
    turn = turn + (align_angle(T0[0], E10[0], e1) if e1 is not None else 0.0)
    pieces = []
    a = 0
    while a < k - 1:
        b = min(k, a + WORM_CHUNK)
        sc = scale[a:b].copy()
        if a > 0:
            sc[0] *= 0.98
        if b < k:
            sc[-1] *= 0.98
        # every piece starts from the whole path's frame there, so the outline does not jump at the seams
        V, F = sweep_mesh(outline, P[a:b], sc, turn[a:b], E10[a])
        pieces.append(to_manifold(V, F))
        if b >= k:
            break
        a = b - 2
    if len(pieces) == 1:
        return pieces[0]
    return manifold3d.Manifold.batch_boolean(pieces, manifold3d.OpType.Add)


def region_solid(values: dict, lo, hi):
    """The Acts-on region (band or sphere, grown by the feather) as a solid, or None for the whole model."""
    import manifold3d
    mode = values.get("influence", "all") or "all"
    if mode == "all":
        return None
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    height = float(hi[2] - lo[2]) or 1.0
    feather = float(values.get("feather", 0.0)) / 100.0 * height
    big = float(np.linalg.norm(hi - lo)) * 2.0
    if mode == "band":
        a, b = sorted((float(values.get("band_from", 0.0)), float(values.get("band_to", 100.0))))
        z0 = lo[2] + a / 100.0 * height - feather
        z1 = hi[2] - (100.0 - b) / 100.0 * height + feather
        if a <= 0:
            z0 = lo[2] - big
        if b >= 100:
            z1 = hi[2] + big
        c = (lo + hi) / 2.0
        box = manifold3d.Manifold.cube(np.array([2 * big, 2 * big, max(z1 - z0, 1e-6)]), True)
        return box.translate(np.array([c[0], c[1], (z0 + z1) / 2.0]))
    centre = (lo + hi) / 2.0 + np.asarray(values.get("sphere_centre", (0, 0, 0)), float) / 100.0 * (hi - lo)
    radius = float(values.get("sphere_radius", 30.0)) / 100.0 * height + feather
    return manifold3d.Manifold.sphere(radius, 96).translate(centre)


def carve(model_m, cutters: list, region=None, invert: bool = False):
    """model − (∪ cutters ∩ region) (or − region when inverted). Returns (result, union of cutters as cut)."""
    import manifold3d
    if not cutters:
        return model_m, manifold3d.Manifold()
    union = manifold3d.Manifold.batch_boolean(cutters, manifold3d.OpType.Add)
    if region is not None:
        union = union - region if invert else union ^ region
    return model_m - union, union


def keep_pieces(m, mode: str, min_share: float):
    """'all', 'largest' or 'drop' (bodies under min_share % of the volume). Returns (manifold, bodies before, bodies
    after). decompose() also returns each closed void's shell (negative volume), so bodies are the positive
    shells, measured by their outer volume, and the dropped ones are cut away: the voids of the kept ones stay."""
    import manifold3d
    solid = [p for p in m.decompose() if p.volume() > 0]
    n = len(solid)
    if mode == "all" or n <= 1:
        return m, n, n
    vols = np.array([p.volume() for p in solid])
    if mode == "largest":
        keep = vols == vols.max()
        keep &= np.cumsum(keep) == 1
    else:
        keep = vols >= min_share / 100.0 * vols.sum()
        if not keep.any():
            keep = np.arange(n) == int(vols.argmax())
    gone = [p for p, k in zip(solid, keep) if not k]
    if not gone:
        return m, n, n
    return m - manifold3d.Manifold.batch_boolean(gone, manifold3d.OpType.Add), n, int(keep.sum())


# ----------------------------------------------------------------------------- the whole cut

@dataclass
class Plan:
    holes: list
    proposed: int
    dropped: int = 0
    shrunk: int = 0
    spaced_out: int = 0


def plan(model: Model, s: Settings, values: dict, progress=None, log=None) -> Plan:
    """Every hole that will be cut, shaped and spaced (no geometry yet but the outlines)."""
    def sub(a, b):
        return (lambda t, msg="": progress(a + (b - a) * t, msg)) if progress else None
    rs = streams(s.seed)
    holes = candidates(model, s, rs, log)
    proposed = len(holes)
    if progress:
        progress(0.1, f"{proposed} candidates")
    holes = place(model, s, holes, rs, sub(0.1, 0.4))
    holes = apply_influence(holes, values, model.lo, model.hi)
    keep = rs["drop"].random(len(holes)) >= s.drop
    dropped = int((~keep).sum())
    holes = [h for h, k in zip(holes, keep) if k]
    if len(holes) > MAX_HOLES:
        raise ValueError(f"{len(holes)} holes is more than {MAX_HOLES}: raise Spacing, lower Count or drop some")
    shape_holes(holes, s, rs)
    before = len(holes)
    holes, shrunk = space(holes, s.gap, min_radius=0.5 * s.size, fixed=(s.size_max - s.size) < 1e-9,
                          progress=sub(0.5, 0.9))
    return Plan(holes, proposed, dropped, shrunk, before - len(holes))
