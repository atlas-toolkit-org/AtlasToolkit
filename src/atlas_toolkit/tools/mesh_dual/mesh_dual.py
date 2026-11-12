"""
mesh_dual.py - Dual Fusion: two models fused into one sculpture, in cinematic ways. Two heads approach, kiss, bridge
and melt into one; a face pushes through the other like a membrane; two figures are split and rejoined along a carved
seam, interleaved in slabs, or joined by strands of liquid metal.

Both models are normalised to one height in model A's canonical frame (geom/frame3d: up = Z, front = -Y, A 2 units
tall and centred on its box): B stands on A's base, `ratio` times as tall. Each model's signed distance (negative
inside) is sampled once, on its own padded grid (Porous Forms' libigl sampler), and read through a pose (a rotation
and a translation) wherever a stage needs it, so models can move between stages without new distance passes and keep
their detail however wide the scene is. Every stage then builds its scene field on a grid fitted to that stage and
extracts it at 0.

Notation: a, b the posed fields, u the stage's Fusion amount (0..1), k a smooth radius (polynomial smooth min).

    convergence   apart -> together: the models glide to one centre (eased), a smooth union whose radius grows as
                  they near (necks and bridges before they touch), then, past Melt from, the field blends toward the
                  overlaid hybrid (1 - balance) a + balance b; face-to-face pairs turn to face the viewer as they melt
    emergence     the emerging model(s) push out of a host (model A, a wall or the floor) under a membrane: their field
                  blurred (Gaussian) and inflated by the skin, unioned with the host by the rim radius; Breach tears
                  the membrane open around the leading point, where the sharp form shows
    split         overlaid, divided by a seam surface r (a plane, optionally tilted and wavy) that wipes across with u:
                  min(max(a, -r), max(b, r)) with exact capped sections; styles: soft blend, carved groove, raised
                  suture, or split open (the halves pulled apart along the seam's normal)
    strata        overlaid, cut into slabs along an axis; B fills a share of every slab period that grows with u and
                  is graded across the model: min(max(a, rA), max(b, rB)), planar caps, optional gaps
    bridge        apart, joined by seeded filaments between their facing sides (tubes on sagging, wavering curves,
                  thinnest at the middle), smooth-unioned at the roots; up to u = 0.5 they reach out as tapering
                  tendrils, then join and thicken
    disintegrate  a front sweeps through A: behind it B (or nothing), and in a band behind the front A's cells break
                  off as explicit fragments (cubes or seeded shards) that drift, shrink and tumble with their age
    echo          K moments of the passage from A's place to B's (straight, arc, spiral, twisting), each the morph
                  (1 - t) a + t b at that point, thinner the older, smooth-unioned into one sculpture
    patchwork     seeded Voronoi cells filled from A or B (B's share spreading with u, swept or at random), exact
                  walls only between cells of different models; grout carves every joint, explode moves cells out
    chrysalis     B (with A inflated by a clearance, so it always encloses A) as a shell round the intact A; u opens
                  gyroid or round windows in it, or cracks it into Voronoi plates, swept

Stage 0 (u = 0) is the starting scene and, where it is the models themselves, they are returned exactly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from atlas_toolkit.geom.frame3d import euler_xyz
from atlas_toolkit.tools.mesh_fusion import mesh_fusion as mf
from atlas_toolkit.tools.mesh_porous import mesh_porous as mp

FUSIONS = ("convergence", "emergence", "split", "strata", "bridge", "disintegrate", "echo", "patchwork",
           "chrysalis")
ARRANGEMENTS = ("side", "face", "back", "stacked", "overlaid")
CHUNK = 1_000_000
MAX_STAGE_POINTS = 60_000_000      # a stage's grid; beyond this its spacing grows (and the report says so)
FIELD_PAD = 0.15                   # a model's own grid: air around it, share of its largest side
FACING = {"side": (0.0, 0.0), "face": (90.0, -90.0), "back": (-90.0, 90.0), "stacked": (0.0, 0.0),
          "overlaid": (0.0, 0.0)}   # turns about Z (degrees) of A and B when apart; front is -Y, +90 faces +X


def smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def rot_z(deg: float) -> np.ndarray:
    a = math.radians(deg)
    return np.array([[math.cos(a), -math.sin(a), 0.0], [math.sin(a), math.cos(a), 0.0], [0.0, 0.0, 1.0]])


def smin_var(a, b, k):
    """Smooth minimum with a radius that may vary from point to point (0: the plain minimum)."""
    k = np.maximum(np.asarray(k, dtype=np.float64), 1e-9)
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0.0, 1.0)
    return b + (a - b) * h - k * h * (1.0 - h)


# ----------------------------------------------------------------------------- models and their fields

@dataclass
class ModelField:
    """A model's signed distance on its own grid, in its local frame."""
    grid: mp.Grid
    d: np.ndarray

    @classmethod
    def of_mesh(cls, V, F, resolution: int, band_extra: float = 0.0, progress=None) -> "ModelField":
        V = np.asarray(V, dtype=np.float64)
        lo, hi = V.min(0), V.max(0)
        pad = FIELD_PAD * float((hi - lo).max())
        grid = mp.make_grid(lo - pad, hi + pad, resolution)
        mp.check_size(grid)
        d = mp.signed_distance(V, np.asarray(F, dtype=np.int64), grid, 3.0 * grid.step + band_extra, progress)
        return cls(grid, d)

    def at(self, P, R: np.ndarray, t: np.ndarray) -> np.ndarray:
        """The field at scene points P for the pose scene = R local + t: trilinear inside the grid, the nearest
        sample plus the distance to the grid's box outside it (a lower bound, right in sign)."""
        from scipy import ndimage
        L = (np.asarray(P, dtype=np.float64) - t) @ R
        idx = (L - self.grid.origin) / self.grid.step
        top = np.array(self.grid.shape) - 1
        ic = np.clip(idx, 0, top)
        v = ndimage.map_coordinates(self.d, ic.T, order=1, mode="nearest")
        return v + np.linalg.norm(idx - ic, axis=1) * self.grid.step


@dataclass
class Model:
    """A model in its local frame (upright, centred on its box), its full-detail mesh and its field."""
    V: np.ndarray
    F: np.ndarray
    field: ModelField | None = None
    name: str = ""

    @property
    def corners(self) -> np.ndarray:
        lo, hi = self.V.min(0), self.V.max(0)
        return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])

    def at(self, P, pose: "Pose") -> np.ndarray:
        return self.field.at(P, pose.R, pose.t)


@dataclass
class Pose:
    R: np.ndarray
    t: np.ndarray

    def apply(self, V) -> np.ndarray:
        return np.asarray(V, dtype=np.float64) @ self.R.T + self.t

    def shifted(self, d) -> "Pose":
        return Pose(self.R, self.t + np.asarray(d, dtype=np.float64))

    def bounds(self, model: Model) -> tuple[np.ndarray, np.ndarray]:
        C = self.apply(model.corners)
        return C.min(0), C.max(0)


def normalise(V, height: float) -> np.ndarray:
    """Vertices centred on their box and scaled to `height` (Z)."""
    V = np.asarray(V, dtype=np.float64)
    lo, hi = V.min(0), V.max(0)
    h = float(hi[2] - lo[2]) or float((hi - lo).max()) or 1.0
    return (V - (lo + hi) / 2.0) * (height / h)


# ----------------------------------------------------------------------------- arrangement

AXIS_OF = {"side": 0, "face": 0, "back": 0, "stacked": 2, "overlaid": None}


def arrange(A: Model, B: Model, arrangement: str, gap: float, closeness: float = 0.0, turn: float = 1.0,
            b_turn=(0.0, 0.0, 0.0), b_offset=(0.0, 0.0, 0.0)) -> tuple[Pose, Pose]:
    """Poses of A and B. closeness 0: apart as arranged, `gap` between their boxes; 1: overlaid, centred on one
    another on A's base. turn scales the arrangement's facing turns (1 as arranged, 0 both facing the front)."""
    fa, fb = FACING[arrangement]
    RA = rot_z(fa * turn)
    RB = rot_z(fb * turn) @ euler_xyz(b_turn)
    CA, CB = A.corners @ RA.T, B.corners @ RB.T
    loA, hiA, loB, hiB = CA.min(0), CA.max(0), CB.min(0), CB.max(0)
    base = -1.0
    # overlaid: both centred across, standing on the base
    tA1 = np.array([-(loA[0] + hiA[0]) / 2, -(loA[1] + hiA[1]) / 2, base - loA[2]])
    tB1 = np.array([-(loB[0] + hiB[0]) / 2, -(loB[1] + hiB[1]) / 2, base - loB[2]])
    tA0, tB0 = tA1.copy(), tB1.copy()
    axis = AXIS_OF[arrangement]
    if axis == 0:
        tA0[0] = -gap / 2.0 - hiA[0]
        tB0[0] = gap / 2.0 - loB[0]
    elif axis == 2:
        tB0[2] = tA1[2] + hiA[2] + gap - loB[2]
    c = float(closeness)
    tA = (1.0 - c) * tA0 + c * tA1
    tB = (1.0 - c) * tB0 + c * tB1 + np.asarray(b_offset, dtype=np.float64)
    return Pose(RA, tA), Pose(RB, tB)


# ----------------------------------------------------------------------------- grids

def stage_grid(boxes, step: float, pad: float) -> tuple[mp.Grid, bool]:
    """A grid over the union of boxes (+ pad) with spacing `step`, coarsened when it would be too big. Returns
    (grid, coarsened)."""
    lo = np.min([b[0] for b in boxes], axis=0) - pad
    hi = np.max([b[1] for b in boxes], axis=0) + pad
    longest = float((hi - lo).max())
    res = int(math.ceil(longest / step)) + 1
    coarse = False
    grid = mp.make_grid(lo, hi, res)
    while grid.size > MAX_STAGE_POINTS and res > 32:
        res = int(res * 0.9)
        grid = mp.make_grid(lo, hi, res)
        coarse = True
    return grid, coarse


COARSE = 4                         # banded evaluation: a grid this many times coarser first


def _evaluate_all(grid: mp.Grid, fn) -> np.ndarray:
    out = np.empty(grid.size, dtype=np.float32)
    for s in range(0, grid.size, CHUNK):
        idx = np.arange(s, min(s + CHUNK, grid.size))
        out[idx] = fn(grid.points(idx))
    return out.reshape(grid.shape)


def evaluate(grid: mp.Grid, fn, banded: bool = True) -> np.ndarray:
    """fn(points) on every sample; float32, grid.shape. Banded (fields that change no faster than distance): fn
    runs on a grid COARSE times coarser, and again exactly only where that one comes within a coarse cell of 0;
    elsewhere the coarse values are interpolated (right in sign and far from 0, which is all extraction needs)."""
    from scipy import ndimage
    k = COARSE
    n = np.array(grid.shape)
    if not banded or n.min() < 4 * k:
        return _evaluate_all(grid, fn)
    nc = (n - 1 + k - 1) // k + 1
    coarse = mp.Grid(grid.origin, grid.step * k, tuple(int(x) for x in nc))
    Dc = _evaluate_all(coarse, fn)
    axes = [np.arange(m) / k for m in n]
    out = np.empty(tuple(n), dtype=np.float32)
    for i in range(n[0]):                     # upsample slab by slab (memory)
        I, J, K = np.meshgrid([axes[0][i]], axes[1], axes[2], indexing="ij")
        out[i] = ndimage.map_coordinates(Dc, [I.ravel(), J.ravel(), K.ravel()], order=1,
                                         mode="nearest").reshape(n[1], n[2])
    near = np.flatnonzero(np.abs(out.reshape(-1)) < 1.5 * math.sqrt(3.0) * k * grid.step)
    flat = out.reshape(-1)
    for s in range(0, len(near), CHUNK):
        part = near[s:s + CHUNK]
        flat[part] = fn(grid.points(part))
    return out


# ----------------------------------------------------------------------------- stages

@dataclass
class Stage:
    """A stage's result: a field on a grid to extract, or exact meshes (posed vertices, faces) to join."""
    grid: mp.Grid | None = None
    F: np.ndarray | None = None
    exact: list | None = None
    note: str = ""
    coarse: bool = False
    extra: list = field(default_factory=list)   # loose meshes (posed vertices, faces) joined after extraction
    fill: bool = True                            # fill air sealed inside (off where a shell encloses a gap)


def exact(*parts) -> Stage:
    return Stage(exact=[(pose.apply(m.V), m.F) for m, pose in parts], note="the models themselves")


def pct(v, key: str) -> float:
    """A length setting in % of A's height, in canonical units (A is 2 tall)."""
    return float(v[key]) / 100.0 * 2.0


def field_band(v) -> float:
    """How far from each model's surface its distance must be exact (beyond, it is interpolated from a coarser
    grid): what the fusion type reads off the fields, plus 2% h."""
    f = v["fusion"]
    extra = {"convergence": pct(v, "radius") + pct(v, "swell"),
             "emergence": pct(v, "rim") + 3.0 * pct(v, "skin") + pct(v, "skin_thickness"),
             "split": pct(v, "seam_width") + pct(v, "seam_depth") + pct(v, "swell"),
             "strata": 0.0,
             "bridge": pct(v, "strand_width"),
             "disintegrate": 0.0,
             "echo": pct(v, "echo_blend") + pct(v, "thin"),
             "patchwork": 0.0,
             "chrysalis": pct(v, "shell") * 2.0 + pct(v, "clearance")}.get(f, 0.1)
    return 0.04 + extra


def b_offset(v) -> np.ndarray:
    """Move second, % of A's height, in canonical units."""
    return np.asarray(v["b_offset"], dtype=np.float64) / 100.0 * 2.0


# ----------------------------------------------------------------------------- convergence

def convergence(A: Model, B: Model, v, u: float, step: float) -> Stage:
    arrangement = v["arrangement"]
    gap = pct(v, "gap")
    melt = float(v["melt_from"])
    mu = float(smoothstep((u - melt) / max(1.0 - melt, 1e-6))) if u > melt else 0.0
    turning = v.get("turn_front", True) and arrangement in ("face", "back")
    turn = 1.0 - mu if turning else 1.0
    close = float(smoothstep(u))
    pa, pb = arrange(A, B, arrangement, gap, close, turn, v["b_turn"], b_offset(v))
    if u <= 0:
        return exact((A, pa), (B, pb))
    kmax = pct(v, "radius")
    k = kmax * float(smoothstep(min(u / max(melt, 1e-3), 1.0)))
    beta = float(v["balance"])
    swell = pct(v, "swell")

    def fn(P):
        a, b = A.at(P, pa), B.at(P, pb)
        U = mf.smin(a, b, k)
        if mu > 0:
            U = U + mu * ((1.0 - beta) * a + beta * b - U)
        if swell > 0:
            U = U - swell * math.sin(math.pi * min(u, 1.0))
        return U

    grid, coarse = stage_grid([pa.bounds(A), pb.bounds(B)], step, k + swell + 4 * step)
    return Stage(grid, evaluate(grid, fn), coarse=coarse)


# ----------------------------------------------------------------------------- emergence

DIRS = {"model": np.array([0.0, -1.0, 0.0]), "wall": np.array([0.0, -1.0, 0.0]), "floor": np.array([0.0, 0.0, 1.0])}


def box_sdf(P, lo, hi) -> np.ndarray:
    c, h = (np.asarray(lo) + hi) / 2.0, (np.asarray(hi) - lo) / 2.0
    q = np.abs(P - c) - h
    return np.linalg.norm(np.maximum(q, 0.0), axis=1) + np.minimum(q.max(axis=1), 0.0)


def emergence(A: Model, B: Model, v, u: float, step: float) -> Stage:
    """Host "model": B pushes out of A's front. Host "wall" / "floor": A and B, side by side, push through a wall
    toward the viewer or rise from the floor."""
    from scipy import ndimage
    host = v["host"]
    d = DIRS[host]
    depth = float(v["depth"]) / 100.0
    inset = 0.02 * 2.0
    if host == "model":
        pa = Pose(np.eye(3), np.zeros(3))
        RB = euler_xyz(v["b_turn"])
        CB = B.corners @ RB.T
        tB = np.array([-(CB[:, 0].min() + CB[:, 0].max()) / 2, 0.0, -(CB[:, 2].min() + CB[:, 2].max()) / 2])
        tB = tB + b_offset(v)
        rest = [(B, Pose(RB, tB))]
        s_host = float((pa.apply(A.V) @ d).max())
    else:
        pa0, pb0 = arrange(A, B, "side", pct(v, "gap"), 0.0, 1.0, v["b_turn"], b_offset(v))
        rest = [(A, pa0), (B, pb0)]
        s_host = 0.0
    # where the emerging models' leading point is at rest, and how far they reach along d
    lead = max(float((p.apply(m.V) @ d).max()) for m, p in rest)
    back = min(float((p.apply(m.V) @ d).min()) for m, p in rest)
    reach = lead - back
    s_lead = s_host - inset + u * (inset + depth * reach)
    poses = [(m, p.shifted(d * (s_lead - lead))) for m, p in rest]
    if host == "model" and u <= 0:
        return exact((A, pa))
    sigma = pct(v, "skin")
    skin = pct(v, "skin_thickness")
    k = pct(v, "rim")
    breach = float(v["breach"])
    # the leading point (the membrane tears there first) and the host's box
    allV = np.concatenate([p.apply(m.V) for m, p in poses])
    P_lead = allV[int(np.argmax(allV @ d))]
    boxes = [p.bounds(m) for m, p in poses]
    if host == "model":
        boxes.append(pa.bounds(A))
        slab = None
    else:
        lo = np.min([b[0] for b in boxes], axis=0)
        hi = np.max([b[1] for b in boxes], axis=0)
        margin = pct(v, "wall_margin")
        thick = pct(v, "wall_thickness")
        slo, shi = lo - margin, hi + margin
        i = int(np.argmax(np.abs(d)))
        sign = float(np.sign(d[i]))                       # d points out of the slab's face, which is at s_host
        face = s_host * sign
        slo[i], shi[i] = (face - thick, face) if sign > 0 else (face, face + thick)
        slab = (slo, shi)
        boxes.append(slab)
    grid, coarse = stage_grid(boxes, step, k + 3.0 * sigma + skin + 4 * step)
    if host == "model":
        E = evaluate(grid, lambda P: np.min([m.at(P, p) for m, p in poses], axis=0))
    else:   # only what is in front of the wall's back (above the floor's underside) can show
        thick = pct(v, "wall_thickness")
        E = evaluate(grid, lambda P: np.maximum(np.min([m.at(P, p) for m, p in poses], axis=0),
                                                -(P @ d - s_host + thick)))
    M = E
    if sigma > 0:
        M = ndimage.gaussian_filter(E, sigma / grid.step, mode="nearest")
    M = M - skin
    if breach > 0:
        rho = breach * reach * 2.0
        feather = max(0.15 * rho, 2.0 * grid.step)
        dist = evaluate(grid, lambda P: np.linalg.norm(P - P_lead, axis=1))
        w = smoothstep((rho - dist) / feather)
        M = M + w * (E - M)
    if host == "model":
        H = evaluate(grid, lambda P: A.at(P, pa))
    else:
        H = evaluate(grid, lambda P: box_sdf(P, slab[0], slab[1]))
    return Stage(grid, mf.smin(H, M, k).astype(np.float32), coarse=coarse)


# ----------------------------------------------------------------------------- split with a seam

SEAMS = {"lr": (1.0, 0.0, 0.0), "fb": (0.0, 1.0, 0.0), "horizontal": (0.0, 0.0, 1.0)}


def seam_normal(v) -> np.ndarray:
    if v["seam"] == "tilted":
        t = math.radians(float(v["tilt"]))
        return np.array([math.cos(t), 0.0, math.sin(t)])
    return np.array(SEAMS[v["seam"]])


def seam_field(P, n, c: float, amp: float, waves: float, height: float = 2.0) -> np.ndarray:
    """Signed distance-like field of the seam: < 0 on B's side. Waves run along the seam (up it for upright seams,
    across for a horizontal one)."""
    r = P @ n - c
    if amp > 0 and waves > 0:
        q = P[:, 0] if abs(n[2]) > 0.9 else P[:, 2]
        kk = 2 * math.pi * waves / height
        r = (r + amp * np.sin(kk * q)) / math.sqrt(1.0 + (amp * kk) ** 2)
    return r


def overlaid(A: Model, B: Model, v) -> tuple[Pose, Pose]:
    return arrange(A, B, "overlaid", 0.0, 1.0, 1.0, v["b_turn"], b_offset(v))


def split(A: Model, B: Model, v, u: float, step: float) -> Stage:
    pa, pb = overlaid(A, B, v)
    if u <= 0:
        return exact((A, pa))
    if u >= 1:
        return exact((B, pb))
    n = seam_normal(v)
    boxes = [pa.bounds(A), pb.bounds(B)]
    C = np.concatenate([pa.apply(A.corners), pb.apply(B.corners)])
    s = C @ n
    margin = 0.02
    c = s.min() - margin + u * (s.max() - s.min() + 2 * margin)
    amp, waves = pct(v, "wave"), float(v["waves"])
    style = v["style"]
    g = pct(v, "seam_width")
    depth = pct(v, "seam_depth")
    swell = pct(v, "swell")
    o = pct(v, "open") / 2.0 if style == "open" else 0.0

    def fn(P):
        if style == "open":
            Pa, Pb = P - o * n, P + o * n
            ra, rb = seam_field(Pa, n, c, amp, waves), seam_field(Pb, n, c, amp, waves)
            return np.minimum(np.maximum(A.at(Pa, pa), -ra), np.maximum(B.at(Pb, pb), rb))
        a, b = A.at(P, pa), B.at(P, pb)
        r = seam_field(P, n, c, amp, waves)
        if style == "blend":
            w = smoothstep(0.5 - r / max(g, 1e-6))
            return a + w * (b - a) - 4.0 * swell * w * (1.0 - w)
        H = np.minimum(np.maximum(a, -r), np.maximum(b, r))
        if style == "groove":
            cut = np.maximum(np.abs(r) - g / 2.0, -(H + depth))
            return mf.smax(H, -cut, g / 4.0)
        if style == "suture":
            ridge = np.maximum(np.abs(r) - g / 2.0, H - depth)
            return mf.smin(H, ridge, g / 4.0)
        return H

    pad = o + g + depth + 4 * step
    if style == "open":
        boxes = [(lo - o, hi + o) for lo, hi in boxes]
    grid, coarse = stage_grid(boxes, step, pad)
    return Stage(grid, evaluate(grid, fn), coarse=coarse)


# ----------------------------------------------------------------------------- interleaved strata

STRATA_AXES = {"up": (0.0, 0.0, 1.0), "across": (1.0, 0.0, 0.0), "depth": (0.0, 1.0, 0.0)}


def strata_regions(z, f, period: float):
    """Signed distance to B's part of each slab period (the lower share f of it), negative inside; A's is its
    negation. f is 0..1 per point (0: all A, 1: all B)."""
    ph = np.mod(z, period)
    fP = f * period
    inside = ph < fP
    rB = np.where(inside, -np.minimum(ph, fP - ph), np.minimum(ph - fP, period - ph))
    rB = np.where(f <= 1e-6, period, rB)
    rB = np.where(f >= 1.0 - 1e-6, -period, rB)
    return rB


def strata(A: Model, B: Model, v, u: float, step: float) -> Stage:
    pa, pb = overlaid(A, B, v)
    if u <= 0:
        return exact((A, pa))
    if u >= 1:
        return exact((B, pb))
    n = np.array(STRATA_AXES[v["strata_axis"]])
    boxes = [pa.bounds(A), pb.bounds(B)]
    lo = np.min([b[0] for b in boxes], axis=0)
    hi = np.max([b[1] for b in boxes], axis=0)
    z0, z1 = float(lo @ np.abs(n)), float(hi @ np.abs(n))
    period = (z1 - z0) / max(int(v["slabs"]), 1)
    width = max(float(v["grading"]) / 100.0, 1e-3)
    gap = pct(v, "slab_gap")
    sweep = v["grade_dir"]

    def fn(P):
        pos = mf.sweep_position(P, sweep, lo, hi) if sweep != "none" else np.zeros(len(P))
        f = np.clip((u * (1.0 + width) - pos) / width, 0.0, 1.0) if sweep != "none" else np.full(len(P), u)
        rB = strata_regions(P @ n - z0, f, period)
        rA = -rB
        return np.minimum(np.maximum(A.at(P, pa), rA + gap / 2.0), np.maximum(B.at(P, pb), rB + gap / 2.0))

    grid, coarse = stage_grid(boxes, step, 4 * step)
    return Stage(grid, evaluate(grid, fn), coarse=coarse)


# ----------------------------------------------------------------------------- liquid bridge

STRAND_SAMPLES = 96


def facing_points(V, axis: int, side: float, band: float) -> np.ndarray:
    """Vertices on a model's side facing the other one (side +1: its high end along axis)."""
    c = V[:, axis] * side
    return V[c >= c.max() - band]


def strands(pa_V, pb_V, axis: int, v, u: float, rng) -> tuple[np.ndarray, np.ndarray]:
    """Sample points and radii of the filaments between the two facing sides (A at the low end of the axis).

    Anchors are seeded points across the facing sides (Gaussian about their middle, Spread wide), each joined to
    the opposite point of the other model by a sagging, wavering curve whose tube is thinnest at its middle. Up to
    u = 0.5 the strands reach out from both sides, tips tapering like drips; they meet at 0.5 and thicken to the
    full width at 1."""
    from scipy.spatial import cKDTree
    n = int(v["strands"])
    width = pct(v, "strand_width")
    band = 0.3 * 2.0
    FA, FB = facing_points(pa_V, axis, 1.0, band), facing_points(pb_V, axis, -1.0, band)
    trans = [i for i in range(3) if i != axis]
    lo = np.maximum(FA[:, trans].min(0), FB[:, trans].min(0))
    hi = np.minimum(FA[:, trans].max(0), FB[:, trans].max(0))
    mid, half = (lo + hi) / 2.0, np.maximum((hi - lo) / 2.0, 1e-3)
    T = mid + np.clip(rng.normal(size=(n, 2)) * 0.5 * float(v["spread"]), -1.0, 1.0) * half
    ta, tb = cKDTree(FA[:, trans]), cKDTree(FB[:, trans])
    sag, wob, thin = pct(v, "sag"), pct(v, "wobble"), float(v["thinning"])
    reachout = min(u / 0.5, 1.0)                     # how far along the strands have grown from each side
    thick = 0.5 + 0.5 * smoothstep((u - 0.5) / 0.5) if u > 0.5 else 0.5
    s = np.linspace(0.0, 1.0, STRAND_SAMPLES)
    pts, rad = [], []
    for k in range(n):
        p0 = FA[ta.query(T[k])[1]].copy()
        p1 = FB[tb.query(T[k] + rng.normal(size=2) * 0.15 * half)[1]].copy()
        w = width * rng.uniform(0.6, 1.4) * thick
        p0[axis] -= w                                # roots sunk into the models
        p1[axis] += w
        C = p0 + (p1 - p0) * s[:, None]
        C[:, 2] -= sag * rng.uniform(0.6, 1.4) * 4.0 * s * (1.0 - s) if axis != 2 else 0.0
        side = np.zeros(3)
        side[trans[0]] = 1.0
        C += np.outer(wob * np.sin(math.pi * s * rng.uniform(1.0, 3.0) + rng.uniform(0, 2 * math.pi)) * 4 * s * (1 - s),
                      side)
        r = 0.5 * w * (thin + (1.0 - thin) * np.abs(2.0 * s - 1.0) ** 1.2)
        if reachout < 1.0:                           # still reaching: two tapering tendrils
            e = 0.5 * reachout
            tip = max(0.25 * e, 1e-3)
            taper = np.maximum(smoothstep((e - s) / tip), smoothstep((s - (1.0 - e)) / tip))
            r = r * taper
        keep = r > 1e-4
        pts.append(C[keep])
        rad.append(r[keep])
    if not pts or not sum(len(x) for x in pts):
        return np.zeros((0, 3)), np.zeros(0)
    return np.concatenate(pts), np.concatenate(rad)


def tubes(P, C, r, tree, k: int = 8) -> np.ndarray:
    """Distance to the union of the spheres (C, r) along the strands (a tube where they are dense)."""
    d, idx = tree.query(P, k=min(k, len(C)))
    if d.ndim == 1:
        d, idx = d[:, None], idx[:, None]
    return (d - r[idx]).min(axis=1)


def bridge(A: Model, B: Model, v, u: float, step: float) -> Stage:
    from scipy.spatial import cKDTree
    arrangement = v["arrangement"] if v["arrangement"] != "overlaid" else "side"
    gap = pct(v, "gap")
    pa, pb = arrange(A, B, arrangement, gap, 0.0, 1.0, v["b_turn"], b_offset(v))
    if u <= 0:
        return exact((A, pa), (B, pb))
    axis = AXIS_OF[arrangement]
    rng = np.random.default_rng(int(v["seed"]))
    C, r = strands(pa.apply(A.V), pb.apply(B.V), axis, v, u, rng)
    root = 0.6 * pct(v, "strand_width")
    tree = cKDTree(C) if len(C) else None

    def fn(P):
        m = np.minimum(A.at(P, pa), B.at(P, pb))
        if tree is None:
            return m
        return mf.smin(m, tubes(P, C, r, tree), root)

    boxes = [pa.bounds(A), pb.bounds(B)]
    if len(C):
        boxes.append((C.min(0) - r.max(), C.max(0) + r.max()))
    grid, coarse = stage_grid(boxes, step, root + 4 * step)
    return Stage(grid, evaluate(grid, fn), coarse=coarse)


# ----------------------------------------------------------------------------- disintegration

def sweep_frame(how: str, lo, hi):
    """(unit direction or None, length) of a sweep: the plane fronts of axis sweeps, None for the radial ones."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    dirs = {"up": (2, 1.0), "down": (2, -1.0), "front": (1, 1.0), "back": (1, -1.0), "left": (0, 1.0),
            "right": (0, -1.0)}
    if how in dirs:
        i, s = dirs[how]
        n = np.zeros(3)
        n[i] = s
        return n, float(hi[i] - lo[i])
    return None, float(np.linalg.norm(hi - lo)) / 2.0


def fragment_templates(shape: str, seed: int, count: int = 8):
    """Unit fragments (about 1 across, centred): a cube, or seeded convex shards."""
    import trimesh
    if shape == "cube":
        m = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        return [(np.asarray(m.vertices), np.asarray(m.faces))]
    rng = np.random.default_rng(int(seed) + 7919)
    out = []
    for _ in range(count):
        pts = rng.uniform(-0.5, 0.5, (9, 3)) * rng.uniform(0.6, 1.3, 3)
        h = trimesh.convex.convex_hull(pts)
        out.append((np.asarray(h.vertices) - h.centroid, np.asarray(h.faces)))
    return out


def random_rotations(rng, n: int, angles) -> np.ndarray:
    """n rotation matrices about random axes by `angles` (radians), Rodrigues."""
    k = rng.normal(size=(n, 3))
    k /= np.linalg.norm(k, axis=1, keepdims=True)
    c, s = np.cos(angles)[:, None, None], np.sin(angles)[:, None, None]
    K = np.zeros((n, 3, 3))
    K[:, 0, 1], K[:, 0, 2], K[:, 1, 2] = -k[:, 2], k[:, 1], -k[:, 0]
    K[:, 1, 0], K[:, 2, 0], K[:, 2, 1] = k[:, 2], -k[:, 1], k[:, 0]
    return np.eye(3)[None] + s * K + (1 - c) * (K @ K)


def disintegrate(A: Model, B: Model, v, u: float, step: float) -> Stage:
    """A front sweeps through the first model: behind it the second shows (or nothing), and in a band behind the
    front the first model's cells break off as fragments that drift, shrink and tumble with their age."""
    pa, pb = overlaid(A, B, v)
    reveal = v["reveal"] == "second"
    if u <= 0:
        return exact((A, pa))
    if u >= 1 and reveal:
        return exact((B, pb))
    boxes = [pa.bounds(A)] + ([pb.bounds(B)] if reveal else [])
    lo = np.min([b[0] for b in boxes], axis=0)
    hi = np.max([b[1] for b in boxes], axis=0)
    how = v["front_dir"]
    width = max(float(v["front_width"]) / 100.0, 0.02)
    f = u * (1.0 + width) if reveal else u                  # the front's position; dust stays in the air at 1
    n, length = sweep_frame(how, lo, hi)

    def passed(P):
        """Signed distance-like: < 0 where the front has passed."""
        pos = mf.sweep_position(P, how, lo, hi)
        return (pos - f) * length

    # fragments from the first model's cells in the band just behind the front
    rng = np.random.default_rng(int(v["seed"]))
    cell = pct(v, "cell")
    alo, ahi = pa.bounds(A)
    axes = [np.arange(alo[i] + cell / 2, ahi[i], cell) for i in range(3)]
    C = np.stack(np.meshgrid(*axes, indexing="ij"), -1).reshape(-1, 3)
    C = C + rng.uniform(-0.3, 0.3, C.shape) * cell
    jit = rng.random(len(C))                                # every cell's own draw, fixed for the sequence
    pos = mf.sweep_position(C, how, lo, hi)
    age = (f - pos) / width                                 # 0 at the front, 1 a band behind it
    inside = A.at(C, pa) < -0.15 * cell
    keep = inside & (age > 0) & (age < 1) & (jit > float(v["survive"]))
    idx = np.flatnonzero(keep)
    budget = int(v["max_fragments"])
    if len(idx) > budget:
        idx = np.sort(rng.choice(idx, budget, replace=False))
    extra = []
    if len(idx):
        g = age[idx]
        sizes = cell * float(v["fragment_size"]) * (1.0 - float(v["shrink"]) * g) * (0.7 + 0.3 * jit[idx])
        drift = pct(v, "drift")
        centre = (lo + hi) / 2.0
        if v["drift_dir"] == "rise":
            D = np.tile([0.0, 0.0, 1.0], (len(idx), 1))
        elif v["drift_dir"] == "scatter":
            D = C[idx] - centre
            D /= np.maximum(np.linalg.norm(D, axis=1, keepdims=True), 1e-9)
        else:                                               # along the sweep, the way the front goes
            D = np.tile(n if n is not None else [0.0, 0.0, 1.0], (len(idx), 1)) if n is not None else \
                (C[idx] - centre) / np.maximum(np.linalg.norm(C[idx] - centre, axis=1, keepdims=True), 1e-9)
        wander = rng.normal(size=(len(idx), 3)) * 0.35
        move = (D + wander) * (drift * g ** 1.6 * (0.6 + 0.8 * jit[idx]))[:, None]
        rots = random_rotations(rng, len(idx), np.radians(float(v["tumble"])) * g * (0.5 + jit[idx]))
        temps = fragment_templates(v["fragment"], int(v["seed"]))
        which = rng.integers(0, len(temps), len(idx))
        Vs, Fs, off = [], [], 0
        for t, (TV, TF) in enumerate(temps):
            sel = np.flatnonzero(which == t)
            if not len(sel):
                continue
            Vt = np.einsum("nij,vj->nvi", rots[sel], TV) * sizes[sel][:, None, None] \
                + (C[idx][sel] + move[sel])[:, None, :]
            nv = len(TV)
            Fs.append((TF[None] + (off + nv * np.arange(len(sel)))[:, None, None]).reshape(-1, 3))
            Vs.append(Vt.reshape(-1, 3))
            off += nv * len(sel)
        extra.append((np.concatenate(Vs), np.concatenate(Fs)))

    def fn(P):
        r = passed(P)
        a = A.at(P, pa)
        if reveal:
            return np.minimum(np.maximum(a, -r), np.maximum(B.at(P, pb), r))
        return np.maximum(a, -r)

    grid, coarse = stage_grid(boxes, step, 4 * step)
    F = evaluate(grid, fn)
    st = Stage(grid, F, coarse=coarse, extra=extra, note=f"{len(idx)} fragments")
    if not (F < 0).any():
        st.grid, st.F = None, None
    return st


# ----------------------------------------------------------------------------- motion echo

def echo(A: Model, B: Model, v, u: float, step: float) -> Stage:
    """Echoes of the form as it travels from the first model's place to the second's, morphing on the way, joined
    by a smooth union: a sculpture of continuity. The trail reaches the second model at Fusion 1."""
    arrangement = v["arrangement"] if v["arrangement"] != "overlaid" else "side"
    gap = pct(v, "gap")
    p0a, p0b = arrange(A, B, arrangement, gap, 0.0, 1.0, v["b_turn"], b_offset(v))
    if u <= 0:
        return exact((A, p0a))
    oa, ob = overlaid(A, B, v)                              # both centred on one point, to morph in place
    start = p0a.t - oa.t                                    # where the trail starts and ends, as moves
    end = p0b.t - ob.t
    K = max(int(v["echoes"]), 2)
    path = v["path"]
    lift = pct(v, "lift")
    twist = float(v["twist"])
    thin = pct(v, "thin")
    k_join = pct(v, "echo_blend")
    axis = end - start
    L = float(np.linalg.norm(axis)) or 1.0
    ax = axis / L
    side = np.cross(ax, [0.0, 0.0, 1.0])
    if np.linalg.norm(side) < 1e-6:
        side = np.array([1.0, 0.0, 0.0])
    side /= np.linalg.norm(side)
    up = np.cross(side, ax)
    echoes = []
    for k in range(K):
        t = u * k / (K - 1)
        c = start + t * axis
        if path == "arc":
            c = c + up * lift * math.sin(math.pi * t)
        elif path == "spiral":
            ang = 2 * math.pi * t * float(v["turns"])
            c = c + (up * (math.cos(ang) - 1.0) + side * math.sin(ang)) * lift
        R = rot_z(twist * t)
        pa = Pose(R @ oa.R, R @ oa.t + c)
        pb = Pose(R @ ob.R, R @ ob.t + c)
        fade = thin * (1.0 - k / (K - 1)) ** 1.5             # the oldest echoes thinnest
        echoes.append((t, pa, pb, fade))
    boxes = []
    for t, pa, pb, fade in echoes:
        boxes += [pa.bounds(A), pb.bounds(B)]

    def fn(P):
        out = None
        for t, pa, pb, fade in echoes:
            e = (1.0 - t) * A.at(P, pa) + t * B.at(P, pb) + fade
            out = e if out is None else mf.smin(out, e, k_join)
        return out

    grid, coarse = stage_grid(boxes, step, k_join + 4 * step)
    return Stage(grid, evaluate(grid, fn), coarse=coarse)


# ----------------------------------------------------------------------------- cellular patchwork

def patchwork(A: Model, B: Model, v, u: float, step: float) -> Stage:
    """Seeded Voronoi cells, each filled from one model or the other; the second model's cells spread with Fusion
    (across the model when swept). Grout carves the joints, Explode pushes the cells out from the centre."""
    from scipy.spatial import cKDTree
    pa, pb = overlaid(A, B, v)
    if u <= 0:
        return exact((A, pa))
    if u >= 1 and float(v["grout"]) <= 0 and float(v["explode"]) <= 0:
        return exact((B, pb))
    boxes = [pa.bounds(A), pb.bounds(B)]
    lo = np.min([b[0] for b in boxes], axis=0)
    hi = np.max([b[1] for b in boxes], axis=0)
    cell = pct(v, "cell_size")
    seeds = mp.voronoi_seeds(lo / cell, hi / cell, int(v["seed"])) * cell
    rng = np.random.default_rng(int(v["seed"]) + 1)
    draw = rng.random(len(seeds))
    how = v["patch_dir"]
    width = max(float(v["patch_width"]) / 100.0, 1e-3)
    t = mf.progress_at(u, mf.sweep_position(seeds, how, lo, hi), width) if how != "none" else np.full(len(seeds), u)
    isB = draw < t
    centre = (lo + hi) / 2.0
    disp = (seeds - centre) * float(v["explode"]) / 100.0
    moved = cKDTree(seeds + disp)
    tree = cKDTree(seeds)
    grout = pct(v, "grout")
    separate = grout > 0 or float(v["explode"]) > 0
    NB = 8

    def fn(P):
        _, i = moved.query(P)
        Q = P - disp[i]
        d, j = tree.query(Q, k=NB)
        si = seeds[i]
        sj = seeds[j]
        dd = np.linalg.norm(sj - si[:, None, :], axis=2)
        bis = (d ** 2 - np.linalg.norm(Q - si, axis=1)[:, None] ** 2) / (2.0 * np.maximum(dd, 1e-12))
        bis = np.where(j == i[:, None], np.inf, bis)
        if separate:
            wall = bis.min(axis=1) - grout / 2.0
        else:                                               # only the joints between the two models' cells
            wall = np.where(isB[j] != isB[i][:, None], bis, np.inf).min(axis=1)
            wall = np.minimum(wall, 4.0 * cell)
        a, b = A.at(Q, pa), B.at(Q, pb)
        content = np.where(isB[i], b, a)
        return np.maximum(content, -wall)

    pad = float(np.abs(disp).max()) if separate else 0.0
    grid, coarse = stage_grid([(lo - pad, hi + pad)], step, 4 * step)
    return Stage(grid, evaluate(grid, fn), coarse=coarse, note=f"{int(isB.sum())} of {len(seeds)} cells from "
                                                                   f"the second model")


# ----------------------------------------------------------------------------- chrysalis shell

def chrysalis(A: Model, B: Model, v, u: float, step: float) -> Stage:
    """The second model as a shell around the first, intact inside; Fusion opens windows in it (gyroid, round
    windows) or cracks it into plates, from where the sweep starts. The shell follows the second model and, where
    that is too small to hold the first one, the first one inflated by Clearance, so it always encloses it."""
    pa = arrange(A, B, "overlaid", 0.0, 1.0, 1.0, v["b_turn"], b_offset(v))[0]
    RB = euler_xyz(v["b_turn"])
    CB = B.corners @ RB.T
    pb = Pose(RB, -(CB.min(0) + CB.max(0)) / 2.0 + b_offset(v))          # centred on the first model's centre
    boxes = [pa.bounds(A), pb.bounds(B)]
    lo = np.min([b[0] for b in boxes], axis=0)
    hi = np.max([b[1] for b in boxes], axis=0)
    shell = pct(v, "shell")
    rib = pct(v, "rib")
    cell = pct(v, "pattern_cell")
    pattern = v["shell_pattern"]
    how = v["open_dir"]
    width = max(float(v["open_width"]) / 100.0, 1e-3)
    centre = (lo + hi) / 2.0
    tree = None
    if pattern == "cracks":
        from scipy.spatial import cKDTree
        tree = cKDTree(mp.voronoi_seeds((lo - cell) / cell, (hi + cell) / cell, int(v["seed"])))

    clear = pct(v, "clearance")

    def fn(P):
        a = A.at(P, pa)
        o = mf.smin(B.at(P, pb), a - clear - shell, clear + shell)   # the outer form always clears the inner
        sh = np.maximum(o, -(o + shell))
        t = mf.progress_at(u, mf.sweep_position(P, how, lo, hi), width) if how != "none" else u
        if pattern == "cracks":
            d, _ = tree.query(P / cell, k=2)
            wall = (d[:, 1] - d[:, 0]) / 2.0 * cell             # distance to the nearest crack line
            open_ = t * rib                                     # cracks widen to Rib
            sh = np.maximum(sh, open_ / 2.0 - wall)
        else:
            name = "gyroid" if pattern == "gyroid" else "primitive"
            Q = (P - centre) / cell
            f = mp.tpms_field(name, "sheet", Q, np.full(len(P), cell))
            fill = cell * 0.6
            h = rib / 2.0 + (1.0 - t) ** 2 * (fill - rib / 2.0)
            sh = np.maximum(sh, f - h)
        return np.minimum(a, sh)

    grid, coarse = stage_grid(boxes, step, clear + 2 * shell + 4 * step)
    return Stage(grid, evaluate(grid, fn), coarse=coarse, fill=False)


BUILD = {"convergence": convergence, "emergence": emergence, "split": split, "strata": strata, "bridge": bridge,
         "disintegrate": disintegrate, "echo": echo, "patchwork": patchwork, "chrysalis": chrysalis}
