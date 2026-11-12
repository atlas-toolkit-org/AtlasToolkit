"""
mesh_ribbon.py - Ribbon Wrap: a flexible band of silk, satin, velvet, cotton or gauze wound around a model, partly
or wholly, its two free ends hanging outside so the scene reads. Pure numpy (igl's AABB for the closest point,
embree for rays); the optional cloth settle runs in Blender (the engine's job, blender/server.py "cloth").

Everything works in the distortion frame (height 2, centred, Z up, front -Y); lengths in the settings are % of the
model's height. The pipeline, per ribbon:

1. a path over the skin: a helix about an axis (Spiral), loops cut by planes (Bands) or a seeded walk (Drape),
   each point the first hit of a ray from outside, so the ribbon rides on the outer skin;
2. a strip of the ribbon's width along it, its width direction across the path in the skin's tangent plane; each
   point of the strip is laid on the skin, a hair above it;
3. tension: the strip is smoothed along its own normal only (so it neither slides nor narrows) and pushed back
   out of the body: it bridges the hollows (eye sockets, under the chin) instead of dipping into them;
4. layers: where the strip passes over an earlier pass (or an earlier ribbon) it is lifted by one thickness, the
   lift spread so the band ramps up over the edge below it;
5. tails: the two ends leave the skin at the first and last contact, bend under gravity, sway, twist about their
   own length, curl at the tip, slide over the body and come to rest on the floor;
6. fabric: lengthwise threads (silk), a woven grain (cotton, gauze), soft ripples strongest at the edges and on
   the tails; then the strip is thickened into a closed solid with rounded (velvet: pillowed) edges, its tips cut
   square, tapered to a point or frayed into threads.

The heavy steps (tension, layers, the cloth settle) run on a coarse strip; the fine strip takes their displacement
by interpolation, so the fabric detail costs nothing to solve.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

H = 2.0                     # canonical model height (the distortion frame)
STYLES = ("helix", "bands", "drape")
BAND_LAYOUTS = ("meridians", "rings", "random")
TIPS = ("cut", "taper", "fray")
MAX_LAYERS = 8


def pct(v) -> float:
    """% of the height -> canonical units."""
    return float(v) / 100.0 * H


def unit(v, axis=-1):
    v = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(v, axis=axis, keepdims=True)
    return v / np.where(n > 1e-12, n, 1.0)


def rotate(v, k, ang):
    """Rodrigues: v turned about the unit axes k by ang (radians); broadcasts over rows."""
    v = np.asarray(v, dtype=np.float64)
    k = np.asarray(k, dtype=np.float64)
    ang = np.asarray(ang, dtype=np.float64)
    c, s = np.cos(ang)[..., None], np.sin(ang)[..., None]
    kv = np.sum(k * v, axis=-1, keepdims=True)
    return v * c + np.cross(k, v) * s + k * kv * (1.0 - c)


def smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


class Noise1D:
    """A smooth seeded wiggle of about unit amplitude: a few sines of rising frequency and random phase."""

    def __init__(self, rng, octaves: int = 4):
        self.f = rng.uniform(0.7, 1.3, octaves) * 2.0 ** np.arange(octaves)
        self.ph = rng.uniform(0.0, 2.0 * math.pi, octaves)
        a = 0.55 ** np.arange(octaves)
        self.a = a / np.sqrt(np.sum(a * a) / 2.0)

    def __call__(self, x):
        x = np.asarray(x, dtype=np.float64)
        return np.sum(self.a * np.sin(x[..., None] * self.f + self.ph), axis=-1)


# ----------------------------------------------------------------------------- the skin

class Surface:
    """Closest points, smooth normals, signed distances and outer hits on a triangle mesh."""

    def __init__(self, V, F):
        import igl
        self.V = np.ascontiguousarray(V, dtype=np.float64)
        self.F = np.ascontiguousarray(F, dtype=np.int64)
        self.tree = igl.AABB()
        self.tree.init(self.V, self.F)
        tri = self.V[self.F]
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        self.area = 0.5 * np.linalg.norm(fn, axis=1)
        self.FN = unit(fn)
        vn = np.zeros_like(self.V)
        for k in range(3):
            np.add.at(vn, self.F[:, k], fn)
        self.VN = unit(vn)
        self.lo, self.hi = self.V.min(0), self.V.max(0)
        self.radius = float(np.linalg.norm(self.hi - self.lo)) / 2.0
        self._caster = None

    def closest(self, P):
        """(closest point, smooth normal there, face) for points (n, 3)."""
        P = np.ascontiguousarray(np.asarray(P, dtype=np.float64).reshape(-1, 3))
        _, I, C = self.tree.squared_distance(self.V, self.F, P)
        I = np.asarray(I, dtype=np.int64)
        C = np.asarray(C, dtype=np.float64)
        f = self.F[I]
        A, B, Cc = self.V[f[:, 0]], self.V[f[:, 1]], self.V[f[:, 2]]
        w = _barycentric(C, A, B, Cc)
        N = unit(w[:, :1] * self.VN[f[:, 0]] + w[:, 1:2] * self.VN[f[:, 1]] + w[:, 2:] * self.VN[f[:, 2]])
        bad = np.linalg.norm(N, axis=1) < 0.5
        N[bad] = self.FN[I[bad]]
        return C, N, I

    def signed(self, P):
        """(signed distance, closest point, outward direction): positive outside. The direction is the one from
        the skin to the point when it is clearly outside, else the skin's normal."""
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        C, N, _ = self.closest(P)
        d = P - C
        dist = np.linalg.norm(d, axis=1)
        side = np.sum(d * N, axis=1)
        sgn = np.where(side >= 0.0, 1.0, -1.0)
        out = np.where(((sgn > 0) & (dist > 1e-9))[:, None], d / np.maximum(dist, 1e-12)[:, None], N)
        return sgn * dist, C, out

    def push_out(self, P, gap):
        """Points inside the body or closer than gap to the skin, moved out to gap along the outward direction.
        gap may be per point."""
        S, C, D = self.signed(P)
        gap = np.broadcast_to(np.asarray(gap, dtype=np.float64), (len(S),))
        inside = S < gap
        out = np.asarray(P, dtype=np.float64).reshape(-1, 3).copy()
        out[inside] = C[inside] + D[inside] * gap[inside, None]
        return out, inside

    def push_along(self, P, dirs, gap, iters: int = 4, cap: float = 0.05 * H):
        """Points inside the body or closer than gap to the skin moved out along their own directions (a strip's
        normal), never sideways: a strip pushed this way tents over a feature instead of bunching up around it."""
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3).copy()
        dirs = np.asarray(dirs, dtype=np.float64).reshape(-1, 3)
        gap = np.broadcast_to(np.asarray(gap, dtype=np.float64), (len(P),))
        moved = np.zeros(len(P), bool)
        for _ in range(iters):
            S, _, D = self.signed(P)
            need = gap - S
            bad = need > 1e-7 * H
            if not bad.any():
                break
            cosang = np.clip(np.sum(dirs[bad] * D[bad], 1), 0.5, 1.0)
            P[bad] += dirs[bad] * np.minimum(need[bad] / cosang, cap)[:, None]
            moved |= bad
        S, C, D = self.signed(P)
        left = S < gap - 1e-6 * H                     # still in (a direction along the skin): out the short way
        P[left] = C[left] + D[left] * gap[left, None]
        return P, moved

    def caster(self):
        if self._caster is None:
            from atlas_toolkit.geom.raycast import Caster
            self._caster = Caster(self.V, self.F)
        return self._caster

    def outer_hits(self, targets, dirs):
        """For each target point and outward direction: the first skin point met by a ray coming from far
        outside, along -dir toward the target (NaN where it misses)."""
        targets = np.asarray(targets, dtype=np.float64).reshape(-1, 3)
        dirs = unit(np.asarray(dirs, dtype=np.float64).reshape(-1, 3))
        far = 2.5 * self.radius + 1.0
        origins = targets + dirs * far
        t, face, u, v = self.caster().intersect(origins, -dirs)
        P = origins - dirs * np.where(np.isfinite(t), t, np.nan)[:, None]
        return P

    def sample(self, n: int, rng):
        """n points spread over the skin by area, with their normals."""
        p = self.area / max(self.area.sum(), 1e-12)
        fi = rng.choice(len(self.F), size=n, p=p)
        r1, r2 = rng.random(n), rng.random(n)
        s = np.sqrt(r1)
        w = np.stack([1 - s, s * (1 - r2), s * r2], 1)
        tri = self.V[self.F[fi]]
        return np.einsum("nk,nkd->nd", w, tri), self.FN[fi]


def _barycentric(P, A, B, C):
    v0, v1, v2 = B - A, C - A, P - A
    d00 = np.sum(v0 * v0, 1)
    d01 = np.sum(v0 * v1, 1)
    d11 = np.sum(v1 * v1, 1)
    d20 = np.sum(v2 * v0, 1)
    d21 = np.sum(v2 * v1, 1)
    den = d00 * d11 - d01 * d01
    den = np.where(np.abs(den) > 1e-30, den, 1.0)
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    return np.stack([1 - v - w, v, w], 1)


# ----------------------------------------------------------------------------- axes and paths

AXES = {"z": (0.0, 0.0, 1.0), "y": (0.0, -1.0, 0.0), "x": (1.0, 0.0, 0.0)}


def wrap_axis(name: str, tilt: float, lean: float):
    """The wrap axis (unit) and a basis (e1, e2) of the plane across it: Up, Front or Side, tipped by tilt degrees
    toward the front (about X) and lean degrees toward the side (about Y). e1 points to the front where it can."""
    a = np.asarray(AXES.get(name, AXES["z"]), dtype=np.float64)
    a = rotate(a, np.array([1.0, 0.0, 0.0]), math.radians(tilt))
    a = unit(rotate(a, np.array([0.0, 1.0, 0.0]), math.radians(lean)))
    ref = np.array([0.0, -1.0, 0.0]) if abs(a[1]) < 0.9 else np.array([0.0, 0.0, 1.0])
    e1 = unit(ref - a * np.dot(ref, a))
    e2 = np.cross(a, e1)
    return a, e1, e2


def resample(P, ds: float):
    """A polyline resampled every ds of arc length (both ends kept)."""
    P = np.asarray(P, dtype=np.float64)
    if len(P) < 2:
        return P.copy()
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    L = s[-1]
    if L <= 1e-12:
        return P[:1].copy()
    n = max(2, int(math.ceil(L / ds)) + 1)
    t = np.linspace(0.0, L, n)
    return np.stack([np.interp(t, s, P[:, k]) for k in range(3)], 1)


def fill_misses(P):
    """Rays that missed (NaN) between hits are bridged in a straight line; leading and trailing misses dropped.
    Returns the filled points, or None when fewer than a quarter hit."""
    ok = np.isfinite(P).all(1)
    if ok.sum() < max(4, 0.25 * len(P)):
        return None
    idx = np.nonzero(ok)[0]
    P = P[idx[0]: idx[-1] + 1].copy()
    ok = ok[idx[0]: idx[-1] + 1]
    if not ok.all():
        x = np.arange(len(P))
        for k in range(3):
            P[~ok, k] = np.interp(x[~ok], x[ok], P[ok, k])
    return P


def along_axis_range(V, a, frm: float, to: float):
    h = V @ a
    lo, hi = float(h.min()), float(h.max())
    span = hi - lo
    h0, h1 = lo + span * min(frm, to) / 100.0, lo + span * max(frm, to) / 100.0
    return h0, h1


def profile(surf: Surface, a, e1, e2, h0: float, h1: float, n: int = 160, k: int = 16):
    """The skin's mean radius about the axis at n heights between h0 and h1 (rays from k directions; 0 where
    nothing is met), and the length along that profile from h0 (the meridian's arc length)."""
    hs = np.linspace(h0, h1, n)
    th = np.linspace(0.0, 2.0 * math.pi, k, endpoint=False)
    dirs = np.cos(th)[:, None] * e1 + np.sin(th)[:, None] * e2
    T = np.repeat(np.outer(hs, a), k, axis=0)
    D = np.tile(dirs, (n, 1))
    P = surf.outer_hits(T, D)
    r = np.linalg.norm(P - T, axis=1).reshape(n, k)
    r = np.where(np.isfinite(r), r, np.nan)
    import warnings
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        rad = np.nan_to_num(np.nanmedian(r, axis=1), nan=0.0)
    dm = np.sqrt(np.diff(hs) ** 2 + np.diff(rad) ** 2)
    return hs, rad, np.concatenate([[0.0], np.cumsum(dm)])


def helix_paths(surf: Surface, v: dict, w: float, ds: float, rng) -> list:
    """Spiral (a bandage): a helix about the wrap axis between the span's ends, `passes` times (up, then back down on a
    steeper pitch, so the return passes cross the first in X's). The pitch is measured along the skin's profile,
    not the axis, so the turns spread out where the form rounds over (a crown, a chin); the wrap stops where a
    turn would be narrower than the ribbon."""
    a, e1, e2 = wrap_axis(v["axis"], v["tilt"], v["lean"])
    h0, h1 = along_axis_range(surf.V, a, v["span_from"], v["span_to"])
    hs, rad, m = profile(surf, a, e1, e2, h0, h1)
    ok = rad > 0.7 * w
    if not ok.any():
        return []
    i0, i1 = int(np.argmax(ok)), int(len(ok) - 1 - np.argmax(ok[::-1]))
    m0, m1 = m[i0] + 0.5 * w, m[i1] - 0.5 * w
    if m1 - m0 < 0.05 * w:
        m1 = m0 + 0.05 * w
    pitch = max(w * (1.0 - float(v["overlap"]) / 100.0), 0.05 * w)
    hand = -1.0 if v["handed"] == "left" else 1.0
    nh, nt = Noise1D(rng), Noise1D(rng)
    theta = math.radians(float(v["start_angle"]))
    ths, ms = [], []
    passes = int(v["passes"])
    for k in range(passes):
        ma, mb = (m0, m1) if k % 2 == 0 else (m1, m0)
        pk = pitch * (1.0 if k == 0 else float(v["return_pitch"]))
        turns = abs(mb - ma) / pk
        mm = np.linspace(ma, mb, max(8, int(turns * 64)))
        rr = np.interp(mm, m, rad)
        dth = 2.0 * math.pi * np.abs(np.diff(mm)) / pk                     # turns per profile length
        # sample so consecutive points are ~ds/2 apart round the turn
        steps = np.maximum(1, np.ceil(dth * np.maximum(rr[:-1], 1e-3) / (0.5 * ds))).astype(int)
        mm_f = np.concatenate([np.linspace(mm[i], mm[i + 1], steps[i], endpoint=False) for i in range(len(steps))]
                              + [mm[-1:]])
        th = theta + hand * 2.0 * math.pi * np.abs(mm_f - ma) / pk
        if k:
            th, mm_f = th[1:], mm_f[1:]
        ths.append(th)
        ms.append(mm_f)
        theta = float(th[-1])
    th = np.concatenate(ths)
    mm = np.concatenate(ms)
    wob = float(v["wobble"])
    if wob > 0:
        cyc = th / (2.0 * math.pi)
        mm = mm + wob * 0.45 * w * nh(cyc * 1.7)
        th = th + wob * 0.25 * nt(cyc * 0.9)
    hh = np.interp(mm, m, hs)
    dirs = np.cos(th)[:, None] * e1 + np.sin(th)[:, None] * e2
    P = surf.outer_hits(np.outer(hh, a), dirs)
    P = fill_misses(P)
    return [] if P is None else [P]


def _loop(surf, c, u, vv, phi0: float, lap: float, ds: float):
    rad = surf.radius
    n = int(np.clip(2.6 * math.pi * rad / (0.5 * ds), 96, 4000))
    phi = phi0 + np.linspace(0.0, 2.0 * math.pi * 1.3, n)
    dirs = np.cos(phi)[:, None] * u + np.sin(phi)[:, None] * vv
    P = surf.outer_hits(np.broadcast_to(c, dirs.shape), dirs)
    full = phi - phi0 <= 2.0 * math.pi
    ok = np.isfinite(P).all(1)
    if ok[full].mean() < 0.6:
        return None
    P = fill_misses(P)
    if P is None:
        return None
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    n_full = int(full.sum())
    L = s[min(n_full, len(s) - 1)] + lap
    return P[s <= L]


def band_paths(surf: Surface, v: dict, w: float, ds: float, rng) -> list:
    """Bands: closed loops where planes through the model meet its outer skin, each running a little past its
    start so its end lies over it, the two tails leaving from there (the knot). Meridians share the wrap axis
    (2 = a parcel's cross), rings stand across it at heights through the span, random planes are seeded."""
    a, e1, e2 = wrap_axis(v["axis"], v["tilt"], v["lean"])
    count = int(v["count"])
    phi0 = math.radians(float(v["start_angle"]))
    lap = 1.5 * w
    c = (surf.lo + surf.hi) / 2.0
    out = []
    layout = v["band_layout"]
    if layout == "rings":
        h0, h1 = along_axis_range(surf.V, a, v["span_from"], v["span_to"])
        hs = [0.5 * (h0 + h1)] if count == 1 else list(np.linspace(h0 + 0.5 * w, h1 - 0.5 * w, count))
        c_a = c - a * np.dot(c, a)
        for i, h in enumerate(hs):
            P = _loop(surf, c_a + a * h, e1, e2, phi0 + 0.7 * i, lap, ds)
            if P is not None:
                out.append(P)
        return out
    for i in range(count):
        if layout == "meridians":
            n = rotate(e1, a, math.pi * i / count + math.radians(float(v["band_turn"])))
            u = unit(np.cross(a, n))
            vv = a
        else:
            n = unit(rng.normal(size=3))
            u = unit(np.cross(n, a)) if abs(np.dot(n, a)) < 0.95 else unit(np.cross(n, e1))
            vv = np.cross(n, u)
        P = _loop(surf, c, u, vv, phi0 + (0.0 if layout == "meridians" else rng.uniform(0, 2 * math.pi)), lap, ds)
        if P is not None:
            out.append(P)
    return out


def drape_paths(surf: Surface, v: dict, w: float, ds: float, gap: float, rng) -> list:
    """Drape: a seeded walk over the skin, turning by a smooth noise, drawn around the wrap axis by Around and
    kept inside the span; it crosses itself freely (the layers sort that out)."""
    a, e1, e2 = wrap_axis(v["axis"], v["tilt"], v["lean"])
    h0, h1 = along_axis_range(surf.V, a, v["span_from"], v["span_to"])
    total = float(v["length"]) * H
    steps = max(8, int(total / ds))
    curvy = float(v["curviness"])
    around = float(v["around"])
    up = np.array([0.0, 0.0, 1.0])
    out = []
    for r in range(int(v["count"])):
        noise = Noise1D(rng)
        P0, N0 = surf.sample(64, rng)
        hh = P0 @ a
        inside = (hh > h0 + w) & (hh < h1 - w)
        k = int(np.nonzero(inside)[0][0]) if inside.any() else int(np.argmin(np.abs(hh - 0.5 * (h0 + h1))))
        p = P0[k] + N0[k] * gap
        n = N0[k]
        d = unit(np.cross(a, p - a * np.dot(p, a)))
        if np.linalg.norm(d) < 0.5:
            d = e1
        d = unit(rotate(d, n, rng.uniform(-0.6, 0.6)))
        pts = [p]
        for i in range(steps):
            s = i * ds / max(w, 1e-6)
            turn = curvy * 0.55 * noise(s * 0.12) * ds / max(w, 1e-6)
            d = rotate(d, n, turn)
            az = np.cross(a, p - a * np.dot(p, a))
            az = unit(az - n * np.dot(az, n))
            if np.dot(az, d) < 0:
                az = -az
            d = unit(d + around * 0.12 * az)
            if n[2] < -0.3:                        # under a cut base or a chin: climb back round
                upt = up - n * np.dot(up, n)
                d = unit(d + 0.6 * unit(upt))
            h = float(np.dot(p, a))
            if h > h1 - 0.5 * w:
                d = unit(d - a * 0.25 * min(1.0, (h - h1 + 0.5 * w) / w + 0.2))
            elif h < h0 + 0.5 * w:
                d = unit(d + a * 0.25 * min(1.0, (h0 + 0.5 * w - h) / w + 0.2))
            q = p + d * ds
            C, N, _ = surf.closest(q[None])
            q = C[0] + N[0] * gap
            n = N[0]
            step = q - p
            if np.linalg.norm(step) > 1e-9:
                d = unit(step - n * np.dot(step, n))
            p = q
            pts.append(p)
        out.append(np.asarray(pts))
    return out


# ----------------------------------------------------------------------------- the strip

@dataclass
class Ribbon:
    """One ribbon's centreline, width direction and row flags, from the start tail's tip to the end tail's."""
    C: np.ndarray                      # (n, 3) centreline
    B: np.ndarray                      # (n, 3) width direction
    wrapped: np.ndarray                # (n,) bool: on the skin (tension and layers act there)
    tail: np.ndarray                   # (n,) 0 on the skin, rising to 1 at a tail's tip
    s: np.ndarray = None               # (n,) arc length
    order: int = 0
    width: float = 0.1
    grid0: np.ndarray = None           # coarse strip as laid (rows, cols, 3)
    grid: np.ndarray = None            # coarse strip after tension, layers, cloth
    layers: np.ndarray = None          # (rows, cols) layer count (smoothed)
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        seg = np.linalg.norm(np.diff(self.C, axis=0), axis=1)
        self.s = np.concatenate([[0.0], np.cumsum(seg)])

    @property
    def length(self) -> float:
        return float(self.s[-1])


def tangents(P):
    T = np.gradient(P, axis=0)
    return unit(T)


def smooth_rows(A, k: int):
    """A moving average of k rows (edges clamped) over the first axis."""
    if k <= 1 or len(A) < 3:
        return A
    from scipy.ndimage import uniform_filter1d
    return uniform_filter1d(A, size=int(k), axis=0, mode="nearest")


def skin_frame(surf: Surface, P, smooth: int):
    """Tangent, skin normal and width direction along a path on the skin (normals smoothed over `smooth` rows)."""
    T = tangents(P)
    _, N, _ = surf.closest(P)
    N = unit(smooth_rows(N, smooth))
    B = unit(np.cross(N, T))
    B = unit(smooth_rows(B, smooth))
    B = unit(B - T * np.sum(B * T, 1, keepdims=True))
    return T, N, B


def grow_tail(surf: Surface, p0, T0, B0, N0, length: float, ds: float, v: dict, rng, floor_z, gap: float):
    """A free end: leaves the skin tilted out by Lift, bends toward the ground (Gravity), sways (soft S-curves),
    twists about its length, slides flat over the body and lies on the floor. Curl winds its last part into a
    corkscrew about that falling spine, like ribbon pulled over a blade, its width along the corkscrew's axis.
    Returns (points, width directions), the first being p0."""
    n = max(2, int(math.ceil(length / ds)))
    lift = math.radians(float(v["tail_lift"]))
    T = unit(T0 * math.cos(lift) + N0 * math.sin(lift))
    B = unit(B0 - T * np.dot(B0, T))
    g = np.array([0.0, 0.0, -1.0])
    grav = float(v["gravity"])
    sway = float(v["sway"])
    twist = 2.0 * math.pi * float(v["twist"]) / max(length, 1e-9)
    noise = Noise1D(rng, 3)
    up = np.array([0.0, 0.0, 1.0])
    pts, Bs, Ts = [np.asarray(p0, float)], [B], [T]
    rate = grav * 9.0 / H                 # 1: turns down within ~10% of the height
    contact = True                        # it leaves from the skin
    for k in range(1, n + 1):
        s = k / n
        T = unit(T + rate * ds * (g - T * np.dot(g, T)))
        if sway > 0:
            T = unit(rotate(T, up, sway * 2.2 * ds / H * 6.0 * noise(s * 3.0)))
        if not contact:
            B = unit(rotate(B, T, twist * ds))
        B = unit(B - T * np.dot(B, T))
        q = pts[-1] + T * ds
        S, C, D = surf.signed(q[None])
        contact = bool(S[0] < gap)
        if contact:                       # sliding over the body: it lies flat on the skin
            q = C[0] + D[0] * gap
            step = q - pts[-1]
            if np.linalg.norm(step) > 1e-9:
                T = unit(step)
            flat = unit(np.cross(D[0], T))
            if np.linalg.norm(flat) > 0.5:
                B = flat if np.dot(flat, B) >= 0 else -flat
        if floor_z is not None and q[2] < floor_z + gap:
            q[2] = floor_z + gap
            if T[2] < 0:
                T = T.copy()
                T[2] = 0.0
                T = unit(T) if np.linalg.norm(T) > 1e-6 else unit(np.cross(B, up))
        B = unit(B - T * np.dot(B, T))
        pts.append(q)
        Bs.append(B)
        Ts.append(T)
    P, Bs, Ts = np.asarray(pts), np.asarray(Bs), np.asarray(Ts)
    curl = float(v["curl"])
    if curl > 0 and len(P) > 4:
        share = 0.25 + 0.5 * curl                       # how much of the tail curls
        s = np.linspace(0.0, 1.0, len(P))
        c = smoothstep((s - (1.0 - share)) / (0.35 * share))
        if c.any():
            w = float(v["width"]) / 100.0 * H
            r = (0.25 + 0.55 * curl) * w * c           # corkscrew radius
            pitch = (2.4 - 1.2 * curl) * w              # spine length per turn
            arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
            phi = 2.0 * math.pi * np.cumsum(c * np.gradient(arc)) / pitch
            n1 = unit(np.cross(Ts, Bs))
            n2 = np.cross(Ts, n1)
            # the coil rests on the skin: its spine is kept a radius (plus the ribbon's half width) off it
            P[1:], _ = surf.push_out(P[1:], gap + r[1:] + 0.5 * w * c[1:])
            if floor_z is not None:
                P[:, 2] = np.maximum(P[:, 2], floor_z + gap + r + 0.5 * w * c)
            P = P + r[:, None] * (np.cos(phi)[:, None] * n1 + np.sin(phi)[:, None] * n2)
            P[1:], _ = surf.push_out(P[1:], gap)
            if floor_z is not None:
                P[:, 2] = np.maximum(P[:, 2], floor_z + gap)
            Tn = tangents(P)
            Bc = unit(Ts - Tn * np.sum(Ts * Tn, 1, keepdims=True))   # width along the corkscrew's axis
            Bc = np.where((np.sum(Bc * Bs, 1) < 0)[:, None], -Bc, Bc)
            Bs = unit(Bs * (1.0 - c)[:, None] + Bc * c[:, None])
    return P, Bs


def make_ribbon(surf: Surface, path, v: dict, w: float, ds: float, gap: float, floor_z, rng, order: int) -> Ribbon:
    """The wrapped path on the skin with its two tails, as one centreline."""
    P = resample(path, ds)
    k = max(1, int(round(0.6 * w / ds)))
    if k > 1 and len(P) > 2 * k:                  # no kinks where a ray jumped (chin to neck)
        P = np.concatenate([P[:1], smooth_rows(P, k)[1:-1], P[-1:]])
    C, N, _ = surf.closest(P)
    S, _, D = surf.signed(P)
    lifted = S > gap                              # smoothing bridged a hollow: keep it, the tension does the rest
    P = np.where(lifted[:, None], P, C + N * gap)
    smooth = max(3, int(round(1.5 * w / ds)))
    T, N, B = skin_frame(surf, P, smooth)
    jitter = 1.0 + 0.15 * (rng.random(2) * 2.0 - 1.0) * float(v["tail_vary"])
    ls, le = pct(v["tail_start"]) * jitter[0], pct(v["tail_end"]) * jitter[1]
    parts_C, parts_B, wrapped, tail = [], [], [], []
    if ls > ds:
        Ps, Bs = grow_tail(surf, P[0], -T[0], B[0], N[0], ls, ds, v, rng, floor_z, gap)
        parts_C.append(Ps[:0:-1])
        parts_B.append(Bs[:0:-1])
        m = len(Ps) - 1
        wrapped.append(np.zeros(m, bool))
        tail.append(np.linspace(1.0, 1.0 / m, m))
    parts_C.append(P)
    parts_B.append(B)
    wrapped.append(np.ones(len(P), bool))
    tail.append(np.zeros(len(P)))
    if le > ds:
        Pe, Be = grow_tail(surf, P[-1], T[-1], B[-1], N[-1], le, ds, v, rng, floor_z, gap)
        parts_C.append(Pe[1:])
        parts_B.append(Be[1:])
        m = len(Pe) - 1
        wrapped.append(np.zeros(m, bool))
        tail.append(np.linspace(1.0 / m, 1.0, m))
    rib = Ribbon(np.concatenate(parts_C), unit(np.concatenate(parts_B)), np.concatenate(wrapped),
                 np.concatenate(tail), order=order, width=w)
    rib.meta = {"wrapped_length": float(np.sum(np.linalg.norm(np.diff(P, axis=0), axis=1))),
                "tails": (ls if ls > ds else 0.0, le if le > ds else 0.0)}
    return rib


def width_profile(rib: Ribbon, v: dict) -> np.ndarray:
    """Per row width factor: 1, narrowing to a point over the tip share of each tail when the tip is tapered."""
    f = np.ones(len(rib.C))
    if v["tip"] == "taper":
        share = max(float(v["tip_length"]) / 100.0, 1e-3)
        t = np.clip((rib.tail - (1.0 - share)) / share, 0.0, 1.0)
        f = 1.0 - 0.94 * smoothstep(t) ** 0.8
    return f


def rows_at(rib: Ribbon, s_new):
    """Centreline, width direction, flags interpolated at arc lengths s_new."""
    C = np.stack([np.interp(s_new, rib.s, rib.C[:, k]) for k in range(3)], 1)
    B = unit(np.stack([np.interp(s_new, rib.s, rib.B[:, k]) for k in range(3)], 1))
    T = tangents(C) if len(C) > 1 else np.zeros_like(C)
    B = unit(B - T * np.sum(B * T, 1, keepdims=True))
    wrapped = np.interp(s_new, rib.s, rib.wrapped.astype(float)) > 0.5
    tail = np.interp(s_new, rib.s, rib.tail)
    return C, B, wrapped, tail


def lay_strip(surf: Surface, C, B, wrapped, wf, u, w: float, gap: float):
    """The strip (rows, cols, 3): across the centreline by the width; the wrapped rows shrink-wrapped onto the skin
    along the row's normal (rays from outside, so the band bends across its width with the form without bunching
    up sideways), every point kept out of the body."""
    X = C[:, None, :] + (w * wf)[:, None, None] * u[None, :, None] * B[:, None, :]
    R, M = X.shape[:2]
    T = tangents(C) if R > 1 else np.zeros_like(C)
    N = unit(np.cross(T, B))
    flat = X.reshape(-1, 3)
    on = np.repeat(wrapped, M)
    if on.any():
        dirs = np.repeat(N, M, axis=0)[on]
        hit = surf.outer_hits(flat[on], dirs)
        ok = np.isfinite(hit).all(1)
        # only hits near the strip's own place: a ray that grazes a far feature leaves the point where it is
        near = ok.copy()
        near[ok] = np.linalg.norm(hit[ok] - flat[on][ok], axis=1) < 0.75 * w + 4.0 * gap
        idx = np.nonzero(on)[0][near]
        flat[idx] = hit[near] + dirs[near] * gap
    flat, _ = surf.push_along(flat, np.repeat(N, M, axis=0), gap, cap=0.5 * w)
    return flat.reshape(R, M, 3)


def strip_normals(X, ref=None):
    """Unit normals of a strip (rows, cols, 3), turned to agree with ref (rows, 3) on average, else as computed."""
    dS = np.gradient(X, axis=0) if X.shape[0] > 1 else np.zeros_like(X)
    dU = np.gradient(X, axis=1)
    n = unit(np.cross(dU, dS))
    if ref is not None:
        if np.sum(n.mean(1) * ref) < 0:
            n = -n
    return n


def row_normals(X, ref=None, smooth: int = 3):
    """One normal per row of a strip (rows, cols, 3), from its centre line and its width, smoothed along the rows
    and broadcast across: steadier than per-point normals where the strip is folded or tiny."""
    M = X.shape[1]
    T = tangents(X[:, M // 2]) if len(X) > 1 else np.zeros((len(X), 3))
    B = unit(X[:, -1] - X[:, 0])
    N = unit(np.cross(T, B))
    if ref is not None and np.sum(N * ref) < 0:
        N = -N
    N = unit(smooth_rows(N, smooth))
    return np.repeat(N[:, None, :], M, axis=1)


def relax(surf: Surface, X, movable, gap, iters: int, ds: float, du: float, progress=None):
    """Tension: every movable point slides toward the average of its neighbours, along the strip's normal only
    (no sliding over the skin, no narrowing), then out of the body again. Bridges hollows; leaves convex skin
    where it is."""
    if iters <= 0 or not movable.any():
        return X
    R, M = X.shape[:2]
    wa = (1.0 / ds ** 2) / (1.0 / ds ** 2 + 1.0 / du ** 2)
    wc = 1.0 - wa
    mov = np.repeat(movable, M).reshape(R, M)
    _, _, Dref = surf.signed(X[:, M // 2])
    for it in range(iters):
        n = row_normals(X, Dref)
        A = X.copy()
        A[1:-1] = 0.5 * (X[:-2] + X[2:])
        Cx = X.copy()
        if M > 2:
            Cx[:, 1:-1] = 0.5 * (X[:, :-2] + X[:, 2:])
            Cx[:, 0] = 2.0 * X[:, 1] - X[:, 2]          # edges: kept in line with the next two columns
            Cx[:, -1] = 2.0 * X[:, -2] - X[:, -3]
        L = wa * (A - X) + wc * (Cx - X)
        dn = np.sum(L * n, axis=2, keepdims=True) * n
        X = np.where(mov[..., None], X + 0.8 * dn, X)
        flat, _ = surf.push_along(X.reshape(-1, 3), n.reshape(-1, 3), gap, iters=2, cap=0.25 * ds)
        X = flat.reshape(R, M, 3)
        if progress is not None and it % 10 == 0:
            progress(it / iters)
    return X


def count_layers(ribbons: list, cell: float, margin_len: float):
    """Layer number of every coarse strip point: one more than the highest earlier ribbon point within a cell of
    it (earlier = an earlier ribbon, or this ribbon more than margin_len back along it). Returns per ribbon
    (rows, cols) integer layers."""
    from scipy.spatial import cKDTree
    pts, owner, row, srow = [], [], [], []
    for r, rib in enumerate(ribbons):
        G = rib.grid
        R, M = G.shape[:2]
        pts.append(G.reshape(-1, 3))
        owner.append(np.full(R * M, r))
        row.append(np.repeat(np.arange(R), M))
        srow.append(np.repeat(rib.s_rows, M))
    P = np.concatenate(pts)
    own = np.concatenate(owner)
    rows = np.concatenate(row)
    srows = np.concatenate(srow)
    tree = cKDTree(P)
    nb = tree.query_ball_point(P, r=cell)
    L = np.zeros(len(P), dtype=np.int64)
    for i in range(len(P)):
        best = -1
        oi, si = own[i], srows[i]
        for j in nb[i]:
            if own[j] < oi or (own[j] == oi and srows[j] < si - margin_len):
                if L[j] > best:
                    best = L[j]
        L[i] = min(best + 1, MAX_LAYERS)
    out, a = [], 0
    for rib in ribbons:
        R, M = rib.grid.shape[:2]
        out.append(L[a: a + R * M].reshape(R, M))
        a += R * M
    return out


def spread_layers(L, rows_ramp: int):
    """Integer layers -> a lift field that ramps up before an edge below and never sits under the count."""
    from scipy.ndimage import gaussian_filter, maximum_filter
    Lf = L.astype(np.float64)
    if rows_ramp > 1:
        Lf = maximum_filter(Lf, size=(2 * rows_ramp + 1, 3), mode="nearest")
        Lf = gaussian_filter(Lf, sigma=(rows_ramp, 0.8), mode="nearest")
    return np.maximum(Lf, L)


# ----------------------------------------------------------------------------- fabric and solid

def fabric_detail(X, n, u, s_rows, wrapped, tail, w, v: dict, rng):
    """Displacement along the normal: lengthwise threads, a woven grain, soft ripples (edges and tails)."""
    R, M = X.shape[:2]
    disp = np.zeros((R, M))
    threads = int(v["threads"])
    if threads > 0 and v["thread_depth"] > 0:
        disp += pct(v["thread_depth"]) * (0.5 - 0.5 * np.cos(2.0 * math.pi * threads * (u + 0.5)))[None, :]
    if v["weave"] > 0:
        lam = max(pct(v["weave_size"]), 1e-4)
        disp += pct(v["weave"]) * (np.sin(2.0 * math.pi * s_rows / lam)[:, None]
                                   * np.sin(2.0 * math.pi * (u * w) / lam)[None, :])
    if v["ripple"] > 0:
        lam = max(pct(v["ripple_length"]), 1e-4)
        ph = rng.uniform(0, 2 * math.pi)
        edge = (2.0 * np.abs(u)) ** 2
        free = np.clip(tail, 0.0, 1.0) > 0
        amp = pct(v["ripple"]) * np.where(free, 1.0, 0.35)
        wave = np.sin(2.0 * math.pi * s_rows / lam + ph)
        disp += amp[:, None] * edge[None, :] * np.where(free, wave, 0.5 + 0.5 * wave)[:, None]
        flutter = np.sin(2.0 * math.pi * s_rows / (2.3 * lam) + 2 * ph) * np.where(free, 0.6, 0.0)
        disp += (pct(v["ripple"]) * flutter)[:, None] * np.ones(M)[None, :]
    return X + n * disp[..., None]


def half_thickness(u, t: float, softness: float):
    """Half the ribbon's thickness across its width: flat with rounded edges (0) to a pillow (1)."""
    prof = np.sqrt(np.clip(1.0 - (2.0 * u) ** 2, 0.0, 1.0))
    return 0.5 * t * np.maximum((1.0 - softness) + softness * prof, 0.12)


def rings_for(X, n, u, t: float, softness: float, arc: int = 3):
    """Closed cross-sections (rows, K, 3) of the thickened strip: top across, an arc round one edge, bottom back,
    an arc round the other."""
    h = half_thickness(u, t, softness)
    top = X + n * h[None, :, None]
    bot = X - n * h[None, :, None]
    e = unit(np.gradient(X, axis=1))
    parts = [top]
    ang = np.linspace(0.0, math.pi, arc + 2)[1:-1]
    re = h[-1]
    parts.append(np.stack([X[:, -1] + re * (math.cos(a) * n[:, -1] + math.sin(a) * e[:, -1]) for a in ang], 1))
    parts.append(bot[:, ::-1])
    le = h[0]
    parts.append(np.stack([X[:, 0] + le * (-math.cos(a) * n[:, 0] - math.sin(a) * e[:, 0]) for a in ang], 1))
    return np.concatenate(parts, axis=1)


def sweep(rings, cap: bool = True):
    """A closed tube through rings (rows, K, 3): quads between rings, fans over the two ends; faces outward."""
    R, K = rings.shape[:2]
    V = rings.reshape(-1, 3)
    i = np.arange(R - 1)[:, None] * K
    j = np.arange(K)[None, :]
    a = i + j
    b = i + (j + 1) % K
    c = a + K
    d = b + K
    F = np.concatenate([np.stack([a, b, d], -1).reshape(-1, 3), np.stack([a, d, c], -1).reshape(-1, 3)])
    if cap and R >= 1:
        c0 = len(V)
        c1 = c0 + 1
        V = np.vstack([V, rings[0].mean(0), rings[-1].mean(0)])
        jj = np.arange(K)
        F0 = np.stack([np.full(K, c0), (jj + 1) % K, jj], 1)
        F1 = np.stack([np.full(K, c1), (R - 1) * K + jj, (R - 1) * K + (jj + 1) % K], 1)
        F = np.concatenate([F, F0, F1])
    if signed_volume(V, F) < 0:
        F = F[:, ::-1]
    return V, F.astype(np.int64)


def signed_volume(V, F) -> float:
    tri = V[F]
    return float(np.sum(np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2]))) / 6.0)


def fray_threads(X, n, B, wf, rows_from: int, tail_dir: int, w: float, t: float, v: dict, rng, ds: float):
    """Loose threads for a frayed tip: the strip's last rows split into strands that spread, droop and end at
    seeded lengths. tail_dir +1 means the tip is the last row, -1 the first. Returns a list of (V, F)."""
    R, M = X.shape[:2]
    if tail_dir < 0:
        X, n, B, wf = X[::-1], n[::-1], B[::-1], wf[::-1]
        rows_from = R - 1 - rows_from
    seg = X[rows_from:]
    m = len(seg)
    if m < 3:
        return []
    k = int(np.clip(round(w / pct(0.55)), 4, 16))
    us = (np.arange(k) + 0.5) / k - 0.5
    radius = max(0.6 * t, 0.22 * w / k)
    out = []
    side = 6
    for j, uj in enumerate(us):
        L = int(max(3, m * rng.uniform(0.45, 1.0)))
        cj = (uj + 0.5) * (M - 1)
        c0, c1 = int(math.floor(cj)), min(int(math.floor(cj)) + 1, M - 1)
        f = cj - c0
        base = (1 - f) * seg[:L, c0] + f * seg[:L, c1]
        nn = unit((1 - f) * n[rows_from:rows_from + L, c0] + f * n[rows_from:rows_from + L, c1])
        tt = np.linspace(0.0, 1.0, L)
        spread = 0.35 * uj * w * tt ** 1.4
        wav = 0.15 * w / k * np.sin(tt * rng.uniform(4, 9) + rng.uniform(0, 6.3))
        P = base + B[rows_from:rows_from + L] * spread[:, None] + nn * wav[:, None]
        P[:, 2] -= 0.25 * L * ds * tt ** 2 * float(v["gravity"])
        T = tangents(P)
        e1 = unit(nn - T * np.sum(nn * T, 1, keepdims=True))
        e2 = np.cross(T, e1)
        r = radius * (1.0 - 0.6 * tt)
        ang = np.linspace(0, 2 * math.pi, side, endpoint=False)
        rings = P[:, None] + r[:, None, None] * (np.cos(ang)[None, :, None] * e1[:, None]
                                                  + np.sin(ang)[None, :, None] * e2[:, None])
        out.append(sweep(rings))
    return out


# ----------------------------------------------------------------------------- the whole wrap

@dataclass
class Plan:
    ribbons: list
    w: float
    gap: float
    t: float
    ds_c: float
    cols_c: int
    floor_z: float | None
    notes: list


def plan(surf: Surface, v: dict, draft: bool = False, progress=None, floor_z=None) -> Plan:
    """Paths, tails and the coarse strips laid on the skin (steps 1, 2 and 5)."""
    rng = np.random.default_rng(int(v["seed"]))
    w = pct(v["width"])
    t = pct(v["thickness"])
    gap = pct(v["gap"]) + 0.5 * t
    ds_c = w / (2.5 if draft else 3.5)
    notes = []
    style = v["style"]
    if style == "helix":
        paths = helix_paths(surf, v, w, ds_c, rng)
    elif style == "bands":
        paths = band_paths(surf, v, w, ds_c, rng)
    else:
        paths = drape_paths(surf, v, w, ds_c, gap, rng)
    if not paths:
        raise ValueError("the ribbon found no skin to wrap: widen the span, or turn the axis toward the model")
    fz = floor_z if v["floor"] else None
    ribbons = []
    for i, P in enumerate(paths):
        if progress:
            progress(i / max(1, len(paths)), f"laying ribbon {i + 1} of {len(paths)}")
        ribbons.append(make_ribbon(surf, P, v, w, ds_c, gap, fz, rng, i))
    cols_c = 5 if draft else 7
    u = np.linspace(-0.5, 0.5, cols_c)
    for rib in ribbons:
        n = max(2, int(math.ceil(rib.length / ds_c)) + 1)
        s_rows = np.linspace(0.0, rib.length, n)
        C, B, wrapped, tail = rows_at(rib, s_rows)
        tmp = Ribbon(C, B, wrapped, tail)
        wf = width_profile(tmp, v)
        rib.s_rows, rib.rows_wrapped, rib.rows_tail, rib.rows_wf = s_rows, wrapped, tail, wf
        rib.grid0 = lay_strip(surf, C, B, wrapped, wf, u, w, gap)
        rib.grid = rib.grid0.copy()
    return Plan(ribbons, w, gap, t, ds_c, cols_c, fz, notes)


def tension_and_layers(surf: Surface, pl: Plan, v: dict, draft: bool = False, progress=None):
    """Steps 3 and 4 on the coarse strips."""
    iters = int(round(float(v["tension"]) ** 1.5 * (120 if draft else 240)))
    du = pl.w / (pl.cols_c - 1)
    n_r = len(pl.ribbons)
    for i, rib in enumerate(pl.ribbons):
        def pr(f, i=i):
            if progress:
                progress((i + f) / n_r * 0.8, f"tension, ribbon {i + 1} of {n_r}")
        rib.grid = relax(surf, rib.grid, rib.rows_wrapped, pl.gap, iters, pl.ds_c, du, pr)
    if progress:
        progress(0.82, "stacking the layers")
    step = pl.t * 1.15 + pct(0.05)
    cell = 0.75 * max(pl.ds_c, du)
    Ls = count_layers(pl.ribbons, cell, margin_len=1.6 * pl.w)
    ramp = max(1, int(round(0.6 * pl.w / pl.ds_c)))
    top = 0
    for rib, L in zip(pl.ribbons, Ls):
        Lf = spread_layers(L, ramp)
        rib.layers = Lf
        top = max(top, int(L.max()) if L.size else 0)
        if Lf.max() > 0:
            _, _, Dref = surf.signed(rib.grid[:, rib.grid.shape[1] // 2])
            n = row_normals(rib.grid, Dref)
            rib.grid = rib.grid + n * (Lf * step)[..., None]
    pl.notes.append(f"up to {top} layer(s) deep" if top else "no overlaps")
    return top


def cloth_mesh(pl: Plan, hold: float):
    """The coarse strips as one sheet for Blender's cloth (V, F, pin weights): the wrapped rows pinned by Hold,
    the tails free, a short ramp between."""
    Vs, Fs, Ws, a = [], [], [], 0
    for rib in pl.ribbons:
        G = rib.grid
        R, M = G.shape[:2]
        idx = np.arange(R * M).reshape(R, M) + a
        q0, q1, q2, q3 = idx[:-1, :-1], idx[1:, :-1], idx[1:, 1:], idx[:-1, 1:]
        Fs.append(np.concatenate([np.stack([q0, q1, q2], -1).reshape(-1, 3),
                                  np.stack([q0, q2, q3], -1).reshape(-1, 3)]))
        wr = rib.rows_wrapped.astype(np.float64)
        from scipy.ndimage import uniform_filter1d
        ramp = np.maximum(wr, uniform_filter1d(wr, size=5, mode="nearest"))
        Ws.append(np.repeat(np.clip(ramp * hold, 0.0, 1.0), M))
        Vs.append(G.reshape(-1, 3))
        a += R * M
    return np.concatenate(Vs), np.concatenate(Fs), np.concatenate(Ws)


def cloth_unpack(pl: Plan, V):
    a = 0
    for rib in pl.ribbons:
        R, M = rib.grid.shape[:2]
        rib.grid = np.asarray(V[a: a + R * M], dtype=np.float64).reshape(R, M, 3)
        a += R * M


def cloth_params(pl: Plan, v: dict, draft: bool) -> dict:
    """What Blender's cloth is asked to do: bending stiffness from the thickness (silk drapes, velvet holds)."""
    tp = float(v["thickness"])
    return {"frames": int(v["cloth_frames"]) // (2 if draft else 1),
            "bending": float(np.clip(0.05 * (tp / 0.25) ** 2 * (1.0 + 2.0 * float(v["softness"])), 0.01, 25.0)),
            "mass": float(np.clip(0.08 + 0.4 * tp, 0.05, 1.0)),
            "distance": float(max(pl.gap, 0.002)),
            "self_collision": bool(v["self_collide"]),
            "pin_stiffness": 8.0,
            "quality": 4 if draft else 6,
            "floor": pl.floor_z}


def finish(surf_full: Surface, pl: Plan, v: dict, draft: bool = False, progress=None):
    """Step 6: fine strips from the coarse ones, fabric detail, closed solids. Returns (pieces [(V, F)], stats)."""
    from scipy.ndimage import map_coordinates
    rng = np.random.default_rng(int(v["seed"]) + 7)
    w, t = pl.w, pl.t
    lam = [w / (4.0 if draft else 7.0)]
    if v["ripple"] > 0:
        lam.append(pct(v["ripple_length"]) / 8.0)
    if v["weave"] > 0:
        lam.append(pct(v["weave_size"]) / 6.0)
    ds_f = max(min(lam), w / 14.0)
    threads = int(v["threads"]) if v["thread_depth"] > 0 else 0
    cols = max(7 if draft else 11, 4 * threads + 1 if not draft else 2 * threads + 1)
    if v["weave"] > 0:
        cols = max(cols, int(math.ceil(6.0 * w / max(pct(v["weave_size"]), 1e-4))) + 1)
    cols = int(min(cols, 81))
    budget = int(v["face_budget"]) * (0.25 if draft else 1.0)
    K = 2 * cols + 6
    total_len = sum(r.length for r in pl.ribbons)
    rows_total = total_len / ds_f
    if rows_total * K * 2 > budget:
        ds_f = total_len * K * 2 / budget
        pl.notes.append(f"the face budget sets the fine step to {100 * ds_f / H:.2f}% h")
    u = np.linspace(-0.5, 0.5, cols)
    uc = (u + 0.5) * (pl.cols_c - 1)
    pieces = []
    n_r = len(pl.ribbons)
    tip = v["tip"]
    share = max(float(v["tip_length"]) / 100.0, 1e-3)
    for ri, rib in enumerate(pl.ribbons):
        if progress:
            progress(ri / n_r, f"fabric, ribbon {ri + 1} of {n_r}")
        n = max(2, int(math.ceil(rib.length / ds_f)) + 1)
        s_f = np.linspace(0.0, rib.length, n)
        C, B, wrapped, tail = rows_at(rib, s_f)
        tmp = Ribbon(C, B, wrapped, tail)
        wf = width_profile(tmp, v)
        ic = np.interp(s_f, rib.s_rows, np.arange(len(rib.s_rows)))
        I, J = np.meshgrid(ic, uc, indexing="ij")
        order = 3 if min(rib.grid.shape[:2]) >= 4 else 1
        X = np.stack([map_coordinates(rib.grid[..., k], [I, J], order=order, mode="nearest") for k in range(3)], -1)
        if v["tip"] == "taper":                      # the coarse strip is too coarse for a sharp point
            mid = X[:, cols // 2: cols // 2 + 1]
            X = mid + (X - mid) * (wf / np.maximum(np.interp(s_f, rib.s_rows, rib.rows_wf), 1e-6))[:, None, None]
        _, _, Dref = surf_full.signed(X[:, cols // 2])
        flat, _ = surf_full.push_along(X.reshape(-1, 3), row_normals(X, Dref, 5).reshape(-1, 3), pl.gap * 0.98,
                                       cap=0.5 * w)
        X = flat.reshape(X.shape)
        nrm = strip_normals(X, Dref)
        X = fabric_detail(X, nrm, u, s_f, wrapped, tail, w, v, rng)
        nrm = strip_normals(X, Dref)
        lo, hi = 0, len(X)
        threads_out = []
        if tip == "fray":
            frayed = tail > (1.0 - share)
            start_tail = (~wrapped) & (np.arange(len(X)) < np.argmax(wrapped)) if wrapped.any() else np.zeros(
                len(X), bool)
            fs = frayed & start_tail
            fe = frayed & ~start_tail
            if fs.any():
                lo = int(np.nonzero(fs)[0].max())
                threads_out += fray_threads(X, nrm, B, wf, lo, -1, w, t, v, rng, ds_f)
            if fe.any():
                hi = int(np.nonzero(fe)[0].min()) + 1
                threads_out += fray_threads(X, nrm, B, wf, hi - 1, +1, w, t, v, rng, ds_f)
        body = slice(lo, hi)
        if hi - lo >= 2:
            ur = u
            rings = rings_for(X[body], nrm[body], ur, t, float(v["softness"]))
            pieces.append(sweep(rings))
        pieces += threads_out
    return pieces


def coverage(surf: Surface, pieces, rng, n: int = 6000) -> float:
    """Share of the skin hidden under ribbon: rays straight out from sample points that meet the ribbon within
    a fifth of the height."""
    if not pieces:
        return 0.0
    from atlas_toolkit.geom.raycast import Caster
    V = np.concatenate([p[0] for p in pieces])
    off = np.cumsum([0] + [len(p[0]) for p in pieces[:-1]])
    F = np.concatenate([p[1] + o for p, o in zip(pieces, off)])
    cast = Caster(V, F)
    P, N = surf.sample(n, rng)
    t, face, _, _ = cast.intersect(P + N * 1e-4, N, tmax=0.2 * H)
    return float(np.mean(np.isfinite(t)))


def join(pieces):
    if not pieces:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    V = np.concatenate([p[0] for p in pieces])
    off = np.cumsum([0] + [len(p[0]) for p in pieces[:-1]])
    F = np.concatenate([p[1] + o for p, o in zip(pieces, off)])
    return V, F
