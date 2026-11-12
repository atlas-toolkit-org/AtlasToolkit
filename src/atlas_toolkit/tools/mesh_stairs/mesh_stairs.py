"""
Stair Sculpt: turn a closed model into a staircase. GUI-free and deterministic; everything is a manifold3d boolean, so
the result is one closed solid.

A staircase is an ordered list of steps: a plan polygon and the height of its top. The three stair types only differ
in the polygons they lay out (rectangles along a line, rectangles around a well or back and forth, sectors around an
axis), and the three modes in what they do with the steps:

    carve   open a cavity in the model and build the stairs inside it
    treads  keep only the model's material inside the steps: the silhouette becomes the outline of the treads
    cut     cut a flight into the body: the steps follow the surface and the model stays as walls

All lengths are canonical (a model 2 tall, centred, front at -Y); the engine converts back to model units.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import manifold3d as mf
import numpy as np

from atlas_toolkit.tools.mesh_porous import mesh_porous as porous

from . import escher

OVERLAP = 0.25          # a step reaches this share of its going under the next one, so the two fuse
SAMPLES = 150_000       # surface samples for the height, radius and section lookups


# ----------------------------------------------------------------------------- solids

def ccw(poly) -> np.ndarray:
    poly = np.asarray(poly, dtype=np.float64)
    x, y = poly[:, 0], poly[:, 1]
    area = np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)
    return poly if area > 0 else poly[::-1]


def prism(poly, z0: float, z1: float) -> mf.Manifold:
    return mf.Manifold.extrude(mf.CrossSection([ccw(poly)]), float(z1 - z0)).translate((0.0, 0.0, float(z0)))


def box(lo, hi) -> mf.Manifold:
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    return mf.Manifold.cube(tuple(hi - lo)).translate(tuple(lo))


def union(parts) -> mf.Manifold:
    parts = [m for m in parts if m is not None and not m.is_empty()]
    return mf.Manifold.batch_boolean(parts, mf.OpType.Add) if parts else mf.Manifold()


def rotation_z(angle: float, origin=(0.0, 0.0)) -> np.ndarray:
    """3x4: turn about Z by `angle` radians, then move the origin."""
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0.0, origin[0]], [s, c, 0.0, origin[1]], [0.0, 0.0, 1.0, 0.0]])


def rect(t0, t1, o0, o1) -> np.ndarray:
    return np.array([[t0, o0], [t1, o0], [t1, o1], [t0, o1]], dtype=np.float64)


def sector(c, r0: float, r1: float, a0: float, a1: float, per_degree: float = 1 / 6) -> np.ndarray:
    """An annular sector around c between angles a0 and a1 (radians)."""
    n = max(2, int(math.degrees(abs(a1 - a0)) * per_degree) + 1)
    a = np.linspace(a0, a1, n)
    d = np.column_stack((np.cos(a), np.sin(a)))
    outer = c + r1 * d
    return np.vstack((outer, c + r0 * d[::-1])) if r0 > 1e-9 else np.vstack((outer, [c]))


def lofted_sector(c0, r0, c1, r1, far, a0, a1, z0, z1) -> mf.Manifold:
    """An annular sector whose axis and inner radius change from its bottom to its top. Neighbouring sectors agree at
    every height, so their inner walls join into one smooth surface."""
    n = max(2, int(math.degrees(abs(a1 - a0)) / 6.0) + 1)
    a = np.linspace(a0, a1, n)
    d = np.column_stack((np.cos(a), np.sin(a)))
    rows = [np.column_stack((np.asarray(c) + r * d, np.full(n, z)))
            for c, r, z in ((c0, r0, z0), (c0, far, z0), (c1, far, z1), (c1, r1, z1))]
    V = np.vstack(rows)
    F = []
    for i in range(n - 1):
        for k in range(4):
            p, q = k * n + i, ((k + 1) % 4) * n + i
            F += [[p, p + 1, q + 1], [p, q + 1, q]]
    for end, flip in ((0, False), (n - 1, True)):
        ring = [end, n + end, 2 * n + end, 3 * n + end]
        tris = [[ring[0], ring[1], ring[2]], [ring[0], ring[2], ring[3]]]
        F += [t[::-1] for t in tris] if flip else tris
    F = np.array(F, dtype=np.uint64)
    tri = V[F.astype(np.int64)]
    if np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() < 0:
        F = F[:, ::-1]
    return mf.Manifold(mf.Mesh64(np.ascontiguousarray(V), np.ascontiguousarray(F)))


# ----------------------------------------------------------------------------- steps

@dataclass
class Step:
    poly: np.ndarray                 # plan outline, canonical or local XY
    z: float                         # height of the tread
    core: np.ndarray                 # a point on the centre line of the flight, under this step
    nose: np.ndarray | None = None   # plan of the lip that overhangs the riser
    rails: list = field(default_factory=list)   # plan strips along the flight's free edges


def transform_steps(steps, matrix) -> list:
    """The steps after a turn and a move about Z (matrix from rotation_z)."""
    R, T = matrix[:2, :2], matrix[:2, 3]

    def xy(q):
        return np.asarray(q, float) @ R.T + T
    return [Step(xy(s.poly), s.z, xy(s.core), None if s.nose is None else xy(s.nose), [xy(r) for r in s.rails])
            for s in steps]


def linear_steps(t0, t1, o0, o1, zs, lip=0.0, rail=0.0) -> list:
    """Equal goings from t0 to t1 between the side lines o0 and o1; zs are the treads' heights."""
    n = len(zs)
    going = (t1 - t0) / max(n, 1)
    sign = 1.0 if going >= 0 else -1.0
    out = []
    for i, z in enumerate(zs):
        a = t0 + i * going
        b = a + going * (1.0 + OVERLAP) if i < n - 1 else a + going
        nose = rect(*sorted((a - sign * lip, a + sign * abs(going) * 0.3)), o0, o1) if lip > 0 else None
        rails = [rect(a, a + going, o0, o0 + rail), rect(a, a + going, o1 - rail, o1)] if rail > 0 else []
        out.append(Step(rect(a, b, o0, o1), float(z), np.array([a + going / 2, (o0 + o1) / 2]), nose, rails))
    return out


def helix_steps(centre, r_in, r_out, z0, z1, n, turns, start, hand=1.0, lip=0.0, rail=0.0) -> list:
    """n sectors climbing from z0 to z1 through `turns` full turns. centre(z) gives the axis at a height; r_in is one
    radius or one per step."""
    da = hand * 2.0 * math.pi * turns / n
    rise = (z1 - z0) / n
    r_in = np.full(n, float(r_in)) if np.isscalar(r_in) else np.asarray(r_in, float)
    out = []
    for i in range(n):
        a, z = start + i * da, z0 + (i + 1) * rise
        c, r = np.asarray(centre(z), float), float(r_in[i])
        b = a + da * (1.0 + OVERLAP) if i < n - 1 else a + da
        nose = sector(c, r, r_out, a - hand * lip / max((r + min(r_out, r + 0.4)) / 2, 1e-6), a + da * 0.3) if lip > 0 else None
        rails = [sector(c, r_out - rail, r_out, a, a + da)] if rail > 0 else []
        mid = a + da / 2
        core = c + (r + 0.5 * (min(r_out, r + 0.4) - r)) * np.array([math.cos(mid), math.sin(mid)])
        out.append(Step(sector(c, r, r_out, a, b), z, core, nose, rails))
    return out


def quarter_steps(inner, w, z0, z1, n, twists, start_side=0, hand=1.0, extra=0.0, lip=0.0, rail=0.0) -> list:
    """Flights around a rectangular well, one landing at every quarter turn. inner(z) gives the well's half sizes;
    extra widens the flights outward beyond any model. Local coordinates, centred on the well."""
    dirs = [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)]
    legs = twists + 1
    rise = (z1 - z0) / max(n, 1)
    span = [inner(z0 + (k + 0.5) / legs * (z1 - z0)) for k in range(legs)]

    def length(k):
        a, b = span[k]
        d = dirs[(start_side + k) % 4]
        return max(2.0 * (a if abs(d[0]) > 0.5 else b), w * 0.5)
    lengths = np.array([length(k) for k in range(legs)])
    landings = legs - 1
    counts = np.maximum(2, np.round((n - landings) * lengths / lengths.sum()).astype(int))
    out, index = [], 0

    def place(d, t0, t1, o0, o1, z, core_o, nose=None, rails=()):
        d = np.array(d)
        out_n = np.array([d[1], -d[0]])      # on a counter-clockwise walk the outside is on the right

        def world(q):
            q = np.asarray(q)
            return q[:, :1] * d + q[:, 1:2] * out_n
        core = ((t0 + t1) / 2) * d + core_o * out_n
        out.append(Step(world(rect(t0, t1, o0, o1)), z, core, None if nose is None else world(nose),
                        [world(r) for r in rails]))

    for k in range(legs):
        a, b = span[k]
        d = dirs[(start_side + k) % 4]
        half, off = (a, b) if abs(d[0]) > 0.5 else (b, a)     # half the side's run; the well's half size across it
        o0, o1 = off, off + w + extra
        centre_o = off + w / 2
        t0, t1 = -half, half
        going = (t1 - t0) / max(counts[k], 1)
        for i in range(int(counts[k])):
            s = t0 + i * going
            e = s + going * (1.0 + OVERLAP)
            nose = rect(s - lip, s + going * 0.3, o0, o1) if lip > 0 else None
            rails = [rect(s, s + going, off, off + rail), rect(s, s + going, off + w - rail, off + w)] if rail > 0 else []
            index += 1
            place(d, s, e, o0, o1, z0 + index * rise, centre_o, nose, rails)
        if k < legs - 1:                     # the landing sits on the corner beyond this flight
            index += 1
            place(d, t1, t1 + w + extra, o0 - w if extra else o0, o1, z0 + index * rise, centre_o,
                  None, [rect(t1 + w - rail, t1 + w, o0, o1)] if rail > 0 else [])
    if hand < 0:                             # mirror the walk
        for s in out:
            s.poly = s.poly * [-1.0, 1.0]
            s.core = s.core * [-1.0, 1.0]
            s.nose = None if s.nose is None else s.nose * [-1.0, 1.0]
            s.rails = [r * [-1.0, 1.0] for r in s.rails]
    scale = (z1 - z0) / max(index, 1)        # rounding may leave a few steps more or fewer: still reach the top
    for i, s in enumerate(out):
        s.z = z0 + (i + 1) * scale
    return out


def switchback_steps(half, w, gap, z0, z1, n, twists, extra=0.0, lip=0.0, rail=0.0) -> list:
    """Flights side by side, turning back 180° on a landing at each end. Local coordinates: the flights run along X."""
    legs = twists + 1
    per = max(2, (n - twists) // legs)
    total = per * legs + twists
    rise = (z1 - z0) / total
    out, index = [], 0
    lane = (w + gap) / 2
    for k in range(legs):
        sign = 1.0 if k % 2 == 0 else -1.0
        o = -lane if k % 2 == 0 else lane
        o0, o1 = o - w / 2, o + w / 2
        if extra:                            # widen outward only: the lanes must not cover each other
            o0, o1 = (o0 - extra, o1) if k % 2 == 0 else (o0, o1 + extra)
        zs = [z0 + (index + i + 1) * rise for i in range(per)]
        out += linear_steps(-sign * half, sign * half, o0, o1, zs, lip, rail)
        index += per
        if k < legs - 1:
            index += 1
            a, b = sorted((sign * (half - w * 0.1), sign * (half + w + extra)))
            top = lane + w / 2 + extra
            rails = [rect(sign * (half + w) - rail / 2, sign * (half + w) + rail / 2, -top, top)] if rail > 0 else []
            out.append(Step(rect(a, b, -top, top), z0 + index * rise, np.array([sign * (half + w / 2), 0.0]), None, rails))
    return out


# ----------------------------------------------------------------------------- steps to a solid

def flight_solid(steps, style: str, thick: float, tread: float, lip_h: float, floor: float, rail_h: float = 0.0,
                 carriage: float = 0.0) -> mf.Manifold:
    """The solid of an ordered list of steps.

    style: band (every step a block `thick` deep), smooth (a smooth underside below the treads, as carved in stone or
    wood), open (thin treads held by a carriage) or solid (everything down to the floor)."""
    if not steps:
        return mf.Manifold()
    zs = np.array([s.z for s in steps])
    rises = np.abs(np.diff(zs))
    rise = float(np.median(rises)) if len(rises) else thick
    parts = []
    for s in steps:
        if style == "solid":
            below = floor
        elif style == "band":
            below = s.z - max(thick, rise * 1.1)
        elif style == "smooth":
            below = s.z - rise * 1.1
        else:
            below = s.z - max(rise * tread, 1e-3)
        parts.append(prism(s.poly, min(below, s.z - 1e-4), s.z))
        if s.nose is not None:
            parts.append(prism(s.nose, s.z - lip_h, s.z))
        for r in s.rails:
            parts.append(prism(r, s.z - rise * 0.5, s.z + rail_h))
    if style == "smooth":
        for a, b in zip(steps, steps[1:]):
            pts = [np.column_stack((q.poly, np.full(len(q.poly), q.z - h)))
                   for q in (a, b) for h in (rise, rise + thick)]
            parts.append(mf.Manifold.hull_points(np.vstack(pts)))
    elif style == "open" and carriage > 0:
        for a, b in zip(steps, steps[1:]):
            pts = []
            for q in (a, b):
                sq = q.core + np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]]) * carriage
                for h in (rise * tread * 0.5, rise * tread + carriage * 2):
                    pts.append(np.column_stack((sq, np.full(4, q.z - h))))
            parts.append(mf.Manifold.hull_points(np.vstack(pts)))
    return union(parts)


def column(centre, radius, z0, z1, n_z=16, segments=24) -> mf.Manifold:
    """A round column following the axis centre(z)."""
    a = np.linspace(0.0, 2.0 * math.pi, segments, endpoint=False)
    ring = radius * np.column_stack((np.cos(a), np.sin(a)))
    zs = np.linspace(z0, z1, n_z + 1)
    parts = []
    for za, zb in zip(zs, zs[1:]):
        pts = [np.column_stack((ring + np.asarray(centre(z)), np.full(segments, z))) for z in (za, zb)]
        parts.append(mf.Manifold.hull_points(np.vstack(pts)))
    return union(parts)


# ----------------------------------------------------------------------------- what the model looks like

class Shape:
    """Lookups on a closed model: surface samples and sections. Deterministic: its samples use a fixed seed."""

    def __init__(self, V, F, body: mf.Manifold):
        self.V, self.F, self.body = np.asarray(V, float), np.asarray(F, np.int64), body
        self.lo, self.hi = self.V.min(0), self.V.max(0)
        self.size = self.hi - self.lo
        self.far = float(np.linalg.norm(self.size)) * 2.0
        tri = self.V[self.F]
        area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
        rng = np.random.default_rng(0)
        idx = rng.choice(len(self.F), SAMPLES, p=area / area.sum())
        u, v = rng.random((2, SAMPLES))
        flip = u + v > 1
        u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
        t = tri[idx]
        self.P = t[:, 0] + u[:, None] * (t[:, 1] - t[:, 0]) + v[:, None] * (t[:, 2] - t[:, 0])
        self._axis = None

    def section_centres(self, count=24) -> tuple:
        """(heights, centres): the largest section's centre at each height, smoothed."""
        if self._axis is None:
            import shapely
            zs = np.linspace(self.lo[2], self.hi[2], count + 2)[1:-1]
            cs = []
            for z in zs:
                polys = self.body.slice(float(z)).to_polygons()
                rings = [shapely.Polygon(q) for q in polys if len(q) >= 3]
                rings = [r if r.is_valid else r.buffer(0) for r in rings]
                rings = [r for r in rings if not r.is_empty]
                if rings:
                    big = max(rings, key=lambda r: r.area)
                    cs.append((big.centroid.x, big.centroid.y))
                else:
                    cs.append((np.nan, np.nan))
            cs = np.array(cs)
            ok = np.isfinite(cs[:, 0])
            default = (self.lo[:2] + self.hi[:2]) / 2
            if not ok.any():
                cs[:] = default
            else:
                for j in (0, 1):
                    cs[:, j] = np.interp(zs, zs[ok], cs[ok, j])
            from scipy.ndimage import gaussian_filter1d
            cs = gaussian_filter1d(cs, 1.5, axis=0, mode="nearest")
            self._axis = zs, cs
        return self._axis

    def axis(self, follow: bool):
        """centre(z): the axis of a helix. Following the model keeps it inside a neck or a leaning figure."""
        if not follow:
            c = (self.lo[:2] + self.hi[:2]) / 2
            return lambda z: c
        zs, cs = self.section_centres()

        def centre(z):
            return np.array([np.interp(z, zs, cs[:, 0]), np.interp(z, zs, cs[:, 1])])
        return centre

    def span_at(self, z: float, t, band: float = 0.08) -> tuple:
        """The model's range along the direction t near height z."""
        near = np.abs(self.P[:, 2] - z) < band
        if near.sum() < 20:
            near = np.ones(len(self.P), bool)
        d = self.P[near, :2] @ np.asarray(t)
        return float(d.min()), float(d.max())

    def half_sizes(self, z: float, band: float = 0.08) -> tuple:
        """The section's half sizes along X and Y and its centre, near height z."""
        x = self.span_at(z, (1.0, 0.0), band)
        y = self.span_at(z, (0.0, 1.0), band)
        return (x[1] - x[0]) / 2, (y[1] - y[0]) / 2, np.array([(x[0] + x[1]) / 2, (y[0] + y[1]) / 2])

    def radius_grid(self, centre, angle_bins=180, z_bins=96) -> tuple:
        """The outermost surface radius around the axis, by angle and height, gaps filled and lightly smoothed."""
        from scipy.ndimage import gaussian_filter, maximum_filter
        zs = np.linspace(self.lo[2], self.hi[2], z_bins + 1)
        k = np.clip(np.searchsorted(zs, self.P[:, 2]) - 1, 0, z_bins - 1)
        mid = (zs[:-1] + zs[1:]) / 2
        cs = np.array([centre(z) for z in mid])
        q = self.P[:, :2] - cs[k]
        r = np.hypot(q[:, 0], q[:, 1])
        a = np.arctan2(q[:, 1], q[:, 0]) % (2.0 * math.pi)
        j = np.minimum((a / (2.0 * math.pi) * angle_bins).astype(int), angle_bins - 1)
        grid = np.zeros((angle_bins, z_bins))
        np.maximum.at(grid, (j, k), r)
        for _ in range(3):
            filled = maximum_filter(grid, size=(5, 3), mode=("wrap", "nearest"))
            grid = np.where(grid > 0, grid, filled)
        grid = gaussian_filter(grid, (2.0, 1.0), mode=("wrap", "nearest"))
        return grid, mid

    def height_profile(self, t, o, centre, half_width, bins=240) -> tuple:
        """Along the direction t, the top of the surface inside a channel across o: (positions, heights, reached)."""
        d = self.P[:, :2] @ np.asarray(t)
        side = self.P[:, :2] @ np.asarray(o)
        lo, hi = float(d.min()), float(d.max())
        edges = np.linspace(lo, hi, bins + 1)
        k = np.clip(np.searchsorted(edges, d) - 1, 0, bins - 1)
        inside = np.abs(side - centre) <= half_width
        height = np.full(bins, -np.inf)
        np.maximum.at(height, k[inside], self.P[inside, 2])
        ok = np.isfinite(height)
        if not ok.any():
            raise ValueError("The flight misses the model: change Heading or Width.")
        mids = (edges[:-1] + edges[1:]) / 2
        return mids, np.interp(mids, mids[ok], height[ok]), ok


# ----------------------------------------------------------------------------- the stair types inside a box

@dataclass
class Report:
    flights: int = 1
    steps: int = 0
    note: list = field(default_factory=list)
    cavity: float = 0.0


def thickness(p) -> float:
    return p["thickness"] / 100.0 * 2.0


def lip_of(p, going: float) -> float:
    return p["nosing"] / 100.0 * going


def single_stair(p, lo, hi) -> tuple:
    """One staircase of the chosen type filling the box [lo, hi] (local coordinates)."""
    n = int(p["steps"])
    size = hi - lo
    mid = (lo + hi) / 2
    floor = float(lo[2])
    hand = -1.0 if p["climb"] == "reverse" else 1.0
    rail = 0.02 * float(min(size[0], size[1])) if p["parapet"] > 0 else 0.0
    rail_h = p["parapet"] / 100.0 * 2.0
    z0, z1 = float(lo[2]), float(hi[2])
    if p["stair_type"] == "linear":
        w = p["width"] / 100.0 * float(size[1])
        going = float(size[0]) / n
        a, b = (lo[0], hi[0]) if hand > 0 else (hi[0], lo[0])
        zs = z0 + (np.arange(n) + 1) * (z1 - z0) / n
        steps = linear_steps(float(a), float(b), mid[1] - w / 2, mid[1] + w / 2, zs, lip_of(p, going), rail)
        return steps, []
    if p["stair_type"] == "turns":
        half = float(min(size[0], size[1])) / 2
        w = max(half * p["width"] / 100.0, 0.02)
        if p["turn_style"] == "switchback":
            w = min(float(size[1]) * 0.46, w)
            gap = max(0.0, float(size[1]) * p["well"] / 100.0 * 0.5)
            run = max(float(size[0]) / 2 - w, 0.05)
            steps = switchback_steps(run, w, gap, z0, z1, n, int(p["twists"]), 0.0, lip_of(p, 2 * run / max(n, 1)), rail)
        else:
            a = max(float(size[0]) / 2 - w, 0.02)
            b = max(float(size[1]) / 2 - w, 0.02)
            steps = quarter_steps(lambda z: (a, b), w, z0, z1, n, int(p["twists"]), 0, hand, 0.0,
                                  lip_of(p, 2 * (a + b) / max(n, 1)), rail)
        return transform_steps(steps, rotation_z(0.0, mid[:2])), []
    r_out = float(min(size[0], size[1])) / 2
    r_in = r_out * p["well"] / 100.0
    going = 2.0 * math.pi * float(p["turns"]) / n * (r_in + r_out) / 2
    c = mid[:2]
    steps = helix_steps(lambda z: c, r_in, r_out, z0, z1, n, float(p["turns"]), math.radians(p["heading"]), hand,
                        lip_of(p, going), rail)
    extras = [column(lambda z: c, max(r_in * 1.03, 0.012), floor, z1)] if p["core"] else []
    return steps, extras


# ----------------------------------------------------------------------------- mode: whole model as treads

def treads(shape: Shape, p, report: Report, ctx=None) -> mf.Manifold:
    body = shape.body
    lo, hi = shape.lo, shape.hi
    H = float(hi[2] - lo[2])
    z0 = float(lo[2]) + p["keep_bottom"] / 100.0 * H
    z1 = float(hi[2]) - p["keep_top"] / 100.0 * H
    if z1 - z0 < 0.1 * H:
        raise ValueError("Keep base and Keep top leave too little height for the stairs.")
    F = int(p["flights"])
    n = max(3, int(p["steps"]) // F)
    style, thick, tread, lip_h = p["support"], thickness(p), p["tread"] / 100.0, 0.25 * H / max(n, 1)
    hand = -1.0 if p["climb"] == "reverse" else 1.0
    heading = math.radians(p["heading"])
    parts, core = [], []
    kind = p["stair_type"]
    if kind == "helix":
        centre = shape.axis(p["follow_axis"])
        half = min(shape.size[0], shape.size[1]) / 2
        r_in = half * p["well"] / 100.0
        going = 2.0 * math.pi * float(p["turns"]) / n * half * 0.6
        for f in range(F):
            start = heading + 2.0 * math.pi * f / F
            parts.append(flight_solid(helix_steps(centre, r_in, shape.far, z0, z1, n, float(p["turns"]), start, hand,
                                                  lip_of(p, going)),
                                      style, thick, tread, lip_h, float(lo[2]), carriage=0.012))
        if p["core"] or style == "open":
            core.append(column(centre, max(r_in * 1.03, 0.03), float(lo[2]), float(hi[2])))
    elif kind == "turns":
        hx, hy = shape.size[0] / 2, shape.size[1] / 2
        c = (lo[:2] + hi[:2]) / 2
        a, b = hx * p["well"] / 100.0, hy * p["well"] / 100.0
        wide = max(min(hx - a, hy - b) * p["width"] / 100.0, 0.03)
        if p["turn_style"] == "switchback":
            t = np.array([math.cos(heading), math.sin(heading)])
            run = (shape.span_at((z0 + z1) / 2, t)[1] - shape.span_at((z0 + z1) / 2, t)[0]) / 2
            run = max(run * 0.55, 0.1)
            going = 2 * run * (int(p["twists"]) + 1) / max(n, 1)
            for f in range(F):
                dz = (z1 - z0) * f / F
                steps = switchback_steps(run, wide, 2 * a * 0.25, z0 + dz, z1 + dz - (z1 - z0) * (F - 1) / F, n,
                                         int(p["twists"]), shape.far, lip_of(p, going))
                steps = transform_steps(steps, rotation_z(heading, c))
                parts.append(flight_solid(steps, style, thick, tread, lip_h, float(lo[2]), carriage=0.012))
        else:
            going = 8 * (a + b + wide) * (int(p["twists"]) + 1) / 4 / max(n, 1)
            for f in range(F):
                steps = quarter_steps(lambda z: (a, b), wide, z0, z1, n, int(p["twists"]), f * 4 // F, hand, shape.far,
                                      lip_of(p, going))
                steps = transform_steps(steps, rotation_z(0.0, c))
                parts.append(flight_solid(steps, style, thick, tread, lip_h, float(lo[2]), carriage=0.012))
            if p["core"] or style == "open":
                core.append(prism(rect(c[0] - a * 1.02, c[0] + a * 1.02, c[1] - b * 1.02, c[1] + b * 1.02),
                                  float(lo[2]), float(hi[2])))
    else:
        t = np.array([math.cos(heading), math.sin(heading)])
        o = np.array([-t[1], t[0]])
        span = shape.P[:, :2] @ o
        half_w = (span.max() - span.min()) / 2 * p["width"] / 100.0
        mid_o = (span.max() + span.min()) / 2
        matrix = rotation_z(heading)
        per = (z1 - z0) / F
        per_steps = max(3, n)
        for f in range(F):
            a_z, b_z = z0 + f * per, z0 + (f + 1) * per
            forward = (f % 2 == 0) == (hand > 0)
            # Each end of a flight stops inside the model at that height, so the next flight can land on it.
            ra = shape.span_at(a_z + 0.02, t)
            rb = shape.span_at(b_z - 0.02, t)
            inset = 0.06 * float(shape.size[:2].max())
            t0 = (ra[0] + inset) if forward else (ra[1] - inset)
            t1 = (rb[1] - inset) if forward else (rb[0] + inset)
            if abs(t1 - t0) < 0.1:
                t1 = t0 + (0.1 if forward else -0.1)
            zs = a_z + (np.arange(per_steps) + 1) * per / per_steps
            going = abs(t1 - t0) / per_steps
            steps = linear_steps(t0, t1, mid_o - half_w, mid_o + half_w, zs, lip_of(p, going))
            if f < F - 1:                    # a landing the next flight starts from
                sign = 1.0 if t1 > t0 else -1.0
                a, b = sorted((t1 - sign * going, t1 + sign * max(inset, 2 * going)))
                steps.append(Step(rect(a, b, mid_o - half_w, mid_o + half_w), float(zs[-1]),
                                  np.array([t1, mid_o])))
            parts.append(flight_solid(transform_steps(steps, matrix), style, thick, tread, lip_h, float(lo[2]),
                                      carriage=0.012))
    keep =[box((lo[0] - 1, lo[1] - 1, lo[2] - 1), (hi[0] + 1, hi[1] + 1, z0)) if p["keep_bottom"] > 0 else None,
            box((lo[0] - 1, lo[1] - 1, z1), (hi[0] + 1, hi[1] + 1, hi[2] + 1)) if p["keep_top"] > 0 else None]
    material = union(parts + core + keep)
    report.flights, report.steps = F, n * F
    return body ^ material


# ----------------------------------------------------------------------------- mode: a flight cut into the body

def cut(shape: Shape, p, report: Report, ctx=None) -> mf.Manifold:
    body = shape.body
    H = float(shape.size[2])
    lo, hi = shape.lo, shape.hi
    z_floor = float(lo[2]) + p["keep_bottom"] / 100.0 * H
    z_top = float(hi[2]) - p["keep_top"] / 100.0 * H
    if z_top - z_floor < 0.1 * H:
        raise ValueError("Keep base and Keep top leave too little height for the stairs.")
    F, n = int(p["flights"]), max(3, int(p["steps"]) // int(p["flights"]))
    depth = p["cut_depth"] / 100.0 * H
    hand = -1.0 if p["climb"] == "reverse" else 1.0
    heading = math.radians(p["heading"])
    kind = p["stair_type"]
    cutters, lips = [], []
    lip_h = 0.3 * H / max(n, 1)
    report.flights, report.steps = F, n * F
    if kind == "linear":
        t = np.array([math.cos(heading), math.sin(heading)])
        o = np.array([-t[1], t[0]])
        span = shape.P[:, :2] @ o
        total = (span.max() - span.min()) * p["width"] / 100.0
        mid = (span.max() + span.min()) / 2
        wide = total / (F if F == 1 else F * 1.25)
        for f in range(F):
            oc = mid + (f - (F - 1) / 2) * total / F
            pos, height, reached = shape.height_profile(t, o, oc, wide / 2)
            if hand < 0:
                pos, height, reached = pos[::-1], height[::-1], reached[::-1]
            first = int(np.argmax(reached))
            top = first + int(np.argmax(height[first:]))
            if top - first < 4:
                raise ValueError("The surface does not climb along this heading: change Heading or Climb.")
            from scipy.ndimage import gaussian_filter1d
            target = gaussian_filter1d(height, 2.0, mode="nearest")[first:top + 1] - depth
            target = np.maximum.accumulate(np.clip(target, z_floor, z_top))
            # A straight pitch climbs from the base of the model, through whatever stands in its way.
            line = np.linspace(z_floor, target[-1], len(target))
            target = line + (target - line) * p["follow"] / 100.0
            along = pos[first:top + 1]
            idx = np.linspace(0, len(along) - 1, n + 1)
            zs = np.interp(idx[1:], np.arange(len(along)), target)
            a, b = float(along[0]), float(along[-1])
            going = abs(b - a) / n
            steps = linear_steps(a, b, oc - wide / 2, oc + wide / 2, zs, lip_of(p, going))
            # Cut exactly above each tread: a going of this tread, not its overlap with the next.
            sign = 1.0 if b > a else -1.0
            rows = []
            for i, s in enumerate(steps):
                q = a + sign * i * going
                # The first step opens the flight from outside the model.
                back = q - sign * shape.far if i == 0 else q
                rows.append(prism(rect(*sorted((back, q + sign * going)), oc - wide / 2, oc + wide / 2), s.z,
                                  s.z + _head(p, shape.far)))
                if s.nose is not None:
                    lips.append(prism(s.nose, s.z - lip_h, s.z))
            cutters.append(union(rows).transform(rotation_z(heading)))
        if lips:
            lips = [union(lips).transform(rotation_z(heading))]
    elif kind == "helix":
        centre = shape.axis(p["follow_axis"])
        grid, mids = shape.radius_grid(centre)
        z0, z1 = z_floor, z_top
        rise = (z1 - z0) / n
        turns = float(p["turns"])
        pitch = (z1 - z0) / max(turns * F, 0.25)
        head = _head(p, pitch * 0.6)
        da = hand * 2.0 * math.pi * turns / n
        r_min = min(shape.size[0], shape.size[1]) / 2 * p["well"] / 100.0
        for f in range(F):
            start = heading + 2.0 * math.pi * f / F
            # The back wall's radius depends on the height alone: one smooth wall that reads as a road around a tower.
            # Following the surface step by step gives one fin per step instead.
            from scipy.ndimage import gaussian_filter1d
            typical = gaussian_filter1d(np.median(grid, axis=0), 3.0, mode="nearest")

            def wall(z):
                return max(float(np.interp(z, mids, typical)) - depth, r_min)
            r = np.array([wall(z0 + (i + 1) * rise) for i in range(n)])
            going = abs(da) * float(np.median(r))
            steps = helix_steps(centre, r, shape.far, z0, z1, n, turns, start, hand, lip_of(p, going))
            for i, s in enumerate(steps):
                a0 = start + i * da
                top = s.z + head
                # Reaches back over the lower step: where the axis moves, neighbouring wedges would leave a fin.
                cutters.append(lofted_sector(centre(s.z), wall(s.z), centre(top), wall(top), shape.far, a0 - da * 0.5,
                                             a0 + da, s.z, top))
                if s.nose is not None:
                    lips.append(prism(s.nose, s.z - lip_h, s.z))
    else:
        c = (lo[:2] + hi[:2]) / 2
        twists = int(p["twists"])

        # The section's box is well outside a round section: 0.8 brings the terraces back onto the model. Smoothed
        # along the height, so one wall leads into the next.
        from scipy.ndimage import gaussian_filter1d
        levels = np.linspace(z_floor, z_top, 48)
        boxes = np.array([shape.half_sizes(float(z))[:2] for z in levels])
        boxes = gaussian_filter1d(boxes, 4.0, axis=0, mode="nearest")
        floor_a = min(shape.size[0], shape.size[1]) / 2 * p["well"] / 100.0

        def inner(z):
            return (max(0.8 * float(np.interp(z, levels, boxes[:, 0])) - depth, floor_a),
                    max(0.8 * float(np.interp(z, levels, boxes[:, 1])) - depth, floor_a))
        z0, z1 = z_floor, z_top
        legs_per_turn = 4
        pitch = (z1 - z0) / max((twists + 1) / legs_per_turn * F, 0.25)
        head = _head(p, pitch * 0.6)
        for f in range(F):
            steps = quarter_steps(inner, 0.02, z0, z1, n, twists, f * 4 // F, hand, shape.far,
                                  lip_of(p, 2 * float(np.mean(inner((z0 + z1) / 2))) * (twists + 1) / max(n, 1)))
            steps = transform_steps(steps, rotation_z(0.0, c))
            for s in steps:
                cutters.append(prism(s.poly, s.z, s.z + head))
                if s.nose is not None:
                    lips.append(prism(s.nose, s.z - lip_h, s.z))
    carved = body - union(cutters)
    if lips:
        carved = carved + (union(lips) ^ body)
    return carved


def _head(p, auto: float) -> float:
    """The clear height above a tread in a cut flight. 0 in the setting: `auto`."""
    return p["headroom"] / 100.0 * 2.0 if p["headroom"] > 0 else auto


# ----------------------------------------------------------------------------- mode: carved cavity

def opening_axes(normal) -> np.ndarray:
    """Rows e1, e2, e3: e3 points into the model (against `normal`), e2 is as near to up as it can be."""
    e3 = -np.asarray(normal, float)
    e3 = e3 / np.linalg.norm(e3)
    up = np.array([0.0, 0.0, 1.0]) if abs(e3[2]) < 0.95 else np.array([0.0, 1.0, 0.0])
    e2 = up - (up @ e3) * e3
    e2 /= np.linalg.norm(e2)
    return np.vstack((np.cross(e2, e3), e2, e3))


def auto_normal(p) -> np.ndarray:
    side = p["side"]
    if side == "top":
        return np.array([0.0, 0.0, 1.0])
    a = math.radians({"front": 0.0, "right": 90.0, "back": 180.0, "left": 270.0}[side] + p["side_turn"])
    return np.array([math.sin(a), -math.cos(a), 0.0])


def shape_section(kind: str, w: float, h: float, c) -> mf.CrossSection:
    """The opening's shape, w by h around c."""
    t = np.linspace(0.0, 2.0 * math.pi, 96, endpoint=False)
    if kind == "ellipse":
        poly = np.column_stack((np.cos(t) * w / 2, np.sin(t) * h / 2))
    elif kind == "arch":
        a = np.linspace(0.0, math.pi, 48)
        r = w / 2
        spring = h / 2 - r
        poly = np.vstack(([[-r, -h / 2], [r, -h / 2]], np.column_stack((np.cos(a) * r, spring + np.sin(a) * r))))
    else:
        poly = rect(-w / 2, w / 2, -h / 2, h / 2)
    return mf.CrossSection([ccw(poly + np.asarray(c))])


def painted_section(V_local, F, selected) -> mf.CrossSection:
    """The silhouette of painted faces, in the opening's plane, with soft corners."""
    import shapely
    tri = V_local[np.asarray(F)[np.flatnonzero(selected)]][:, :, :2]
    polys = [shapely.Polygon(t) for t in tri]
    polys = [q for q in polys if q.is_valid and q.area > 1e-12]
    if not polys:
        raise ValueError("The painted area has no extent seen from its own direction: repaint it.")
    r = 0.02
    merged = shapely.union_all([q.buffer(1e-4) for q in polys]).buffer(r).buffer(-2 * r).buffer(r)
    if merged.is_empty:
        raise ValueError("The painted area is too narrow to open: paint a wider area.")
    contours = []
    for part in getattr(merged, "geoms", [merged]):
        contours.append(np.asarray(part.exterior.coords)[:-1])
        contours += [np.asarray(h.coords)[:-1] for h in part.interiors]
    return mf.CrossSection(contours, fillrule=mf.FillRule.EvenOdd)


def carve(shape: Shape, p, report: Report, ctx=None, selected=None) -> mf.Manifold:
    body = shape.body
    H = float(shape.size[2])
    wall = p["wall"] / 100.0 * H
    recess = min(p["recess"] / 100.0 * H, wall * 0.8)
    if selected is not None:
        tri = shape.V[shape.F[selected]]
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]).sum(0)
        if np.linalg.norm(n) < 1e-9:
            raise ValueError("The painted area faces every way at once: paint one side of the model.")
        normal = n / np.linalg.norm(n)
    else:
        normal = auto_normal(p)
    E = opening_axes(normal)
    to_open = np.c_[E, np.zeros(3)]
    from_open = np.c_[E.T, np.zeros(3)]
    V_o = shape.V @ E.T
    body_o = body.transform(to_open)
    sil = mf.CrossSection([q for q in body_o.project().to_polygons()], fillrule=mf.FillRule.Positive)
    if selected is not None:
        section = painted_section(V_o, shape.F, selected) ^ sil.offset(-wall * 0.5)
    else:
        (x0, y0, x1, y1) = sil.bounds()
        w, h = (x1 - x0), (y1 - y0)
        c = ((x0 + x1) / 2 + p["open_x"] / 100.0 * w, (y0 + y1) / 2 + p["open_y"] / 100.0 * h)
        section = shape_section(p["open_shape"], p["open_w"] / 100.0 * w, p["open_h"] / 100.0 * h, c)
        section = section ^ sil.offset(-wall)
    if section.is_empty() or section.area() < 1e-4:
        raise ValueError("The opening falls outside the model or is too small: move or enlarge it.")
    zs = V_o[:, 2]
    tall = mf.Manifold.extrude(section, float(zs.max() - zs.min() + 2.0)).translate((0.0, 0.0, float(zs.min()) - 1.0))
    through = (tall ^ body_o).bounding_box()
    front, back = through[2], through[5]
    stop = front + (back - front) * p["depth"] / 100.0
    if stop - front < wall:
        raise ValueError("The cavity is shallower than its wall: raise Depth or lower Wall.")
    prism_o = mf.Manifold.extrude(section, float(stop - zs.min() + 1.0)).translate((0.0, 0.0, float(zs.min()) - 1.0))

    # The cavity follows the model: everything deeper than the wall, swept out toward the opening.
    (x0, y0, x1, y1) = section.bounds()
    margin = wall * 1.5 + 0.05
    grid_lo = np.array([x0 - margin, y0 - margin, zs.min() - margin])
    grid_hi = np.array([x1 + margin, y1 + margin, min(stop + margin, zs.max() + margin)])
    res = int(p["resolution"] if ctx is None or not ctx.draft else p["draft_resolution"])
    grid = porous.make_grid(grid_lo, grid_hi, res, pad=2)
    porous.check_size(grid)
    if ctx:
        ctx.progress(0.2, "measuring the model's depth")
    sd = porous.signed_distance(V_o, shape.F, grid, band=max(wall, recess) + 0.1,
                                progress=(lambda f, m="": ctx.progress(0.2 + 0.25 * f, m)) if ctx else None)
    from scipy.ndimage import gaussian_filter
    sd = gaussian_filter(sd, 0.6)
    swept = np.minimum.accumulate((sd + wall)[:, :, ::-1], axis=2)[:, :, ::-1]
    cavity_o = _field_solid(swept, grid) ^ prism_o
    if cavity_o.is_empty():
        raise ValueError("Nothing is left to hollow out: lower Wall or enlarge the opening.")
    keep_in = body_o if recess <= 0 else _field_solid(sd + recess, grid)
    if ctx:
        ctx.progress(0.5, "building the stairs")
    cavity = cavity_o.transform(from_open)
    # The stairs are laid out in a frame that turns only about Z, so their treads stay level.
    w_axis = E[2][:2]
    if np.linalg.norm(w_axis) < 0.3:
        w_axis = np.array([0.0, 1.0])
    w_axis = w_axis / np.linalg.norm(w_axis)
    u_axis = np.array([w_axis[1], -w_axis[0]])
    local = np.array([[u_axis[0], u_axis[1], 0.0, 0.0], [w_axis[0], w_axis[1], 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]])
    world = np.array([[u_axis[0], w_axis[0], 0.0, 0.0], [u_axis[1], w_axis[1], 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]])
    b = cavity.transform(local).bounding_box()
    lo, hi = np.array(b[:3]), np.array(b[3:])
    pad = np.array([wall * 0.5, wall * 0.5, wall * 0.5])
    style, thick, tread, lip_h = p["support"], thickness(p), p["tread"] / 100.0, 0.25 * (hi[2] - lo[2]) / max(p["steps"], 1)
    # Set back from the opening, so the surface there does not cut the stair's near side.
    back = hi[1] - lo[1]
    if int(p["flights"]) > 1:
        rings = {}

        def contains(u, w, z):
            import shapely
            level = round(float(z), 3)
            if level not in rings:
                polys = cavity.transform(local).slice(float(z)).to_polygons()
                rings[level] = [shapely.Polygon(q) for q in polys if len(q) >= 3]
                rings[level] = [r if r.is_valid else r.buffer(0) for r in rings[level]]
            pt = shapely.Point(u, w)
            return sum(1 for r in rings[level] if r.contains(pt)) % 2 == 1
        start = lo + np.array([0.0, 0.1 * back, 0.0])
        solids, count, stepsum = escher.build(start - pad, hi + pad, p, contains)
        stairs = union(solids)
        report.flights, report.steps = count, stepsum
        if count < int(p["flights"]):
            report.note.append(f"{count} of {int(p['flights'])} flights fitted in this cavity")
    else:
        steps, extras = single_stair(p, lo + np.array([0.0, 0.2 * back, 0.0]) - pad, hi + pad)
        stairs = union([flight_solid(steps, style, thick, tread, lip_h, float(lo[2] - pad[2]),
                                     p["parapet"] / 100.0 * 2.0, carriage=0.012)] + extras)
        report.flights, report.steps = 1, len(steps)
    stairs = stairs.transform(world)
    built = stairs ^ keep_in.transform(from_open)
    report.cavity = cavity.volume() / max(body.volume(), 1e-9) * 100.0
    return (body - cavity) + built


def _field_solid(field_values, grid) -> mf.Manifold:
    """The closed solid where a field is negative. The outermost samples are forced outside, so nothing stays open."""
    f = np.array(field_values, dtype=np.float32, copy=True)
    f[0, :, :] = f[-1, :, :] = 1.0
    f[:, 0, :] = f[:, -1, :] = 1.0
    f[:, :, 0] = f[:, :, -1] = 1.0
    V, F = porous.extract(f, grid)
    if not len(F):
        return mf.Manifold()
    solid = mf.Manifold(mf.Mesh64(np.ascontiguousarray(V, dtype=np.float64), np.ascontiguousarray(F, dtype=np.uint64)))
    if solid.status() != mf.Error.NoError:
        raise ValueError("The cavity could not be closed: change Cavity resolution or Wall.")
    return solid


# ----------------------------------------------------------------------------- after building

def _flip_flat_triangles(P, G, height: float) -> np.ndarray:
    """Remove zero-height triangles from a closed mesh without opening it: a vertex lying on a triangle's long edge gets
    that edge flipped against the neighbour across it. Thin slivers along straight edges are common after booleans, and
    formats and loaders drop them, which would leave a crack."""
    G = np.array(G, dtype=np.int64, copy=True)
    for _ in range(8):
        tri = P[G]
        edges = np.linalg.norm(tri[:, [1, 2, 0]] - tri, axis=2)        # edge i runs from corner i to corner i + 1
        area2 = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
        longest = edges.max(axis=1)
        flat = np.flatnonzero(area2 <= height * np.maximum(longest, 1e-30))
        if not len(flat):
            break
        opposite = {}
        for f, (a, b, c) in enumerate(G):
            opposite[(a, b)] = f
            opposite[(b, c)] = f
            opposite[(c, a)] = f
        touched, alive = set(), np.ones(len(G), bool)
        for f in flat:
            if f in touched:
                continue
            i = int(np.argmax(edges[f]))
            a, b, c = G[f, i], G[f, (i + 1) % 3], G[f, (i + 2) % 3]
            g = opposite.get((b, a))
            if g is None or g == f or g in touched:
                continue
            d = [v for v in G[g] if v != a and v != b]
            if len(d) != 1:
                continue
            d = d[0]
            touched |= {int(f), int(g)}
            if d == c:                       # two faces back to back: a membrane with no thickness
                alive[[f, g]] = False
                continue
            G[f] = (c, a, d)
            G[g] = (c, d, b)
        G = G[alive]
        if not touched:
            break
    return G


def _weld(M, F) -> tuple:
    """(P, G, vertices on bad edges): the mesh as a file holds it (float32, merged). P[G] is closed if the last is empty."""
    P, index = np.unique(M.astype(np.float32).astype(np.float64), axis=0, return_inverse=True)
    index = np.asarray(index).ravel()
    G = index[F]
    G = G[(G[:, 0] != G[:, 1]) & (G[:, 1] != G[:, 2]) & (G[:, 2] != G[:, 0])]
    size = float(np.ptp(P, axis=0).max()) or 1.0
    G = _flip_flat_triangles(P, G, size * 1e-7)
    e = np.vstack((G[:, [0, 1]], G[:, [1, 2]], G[:, [2, 0]]))
    pair, counts = np.unique(np.sort(e, axis=1), axis=0, return_counts=True)
    bad = pair[counts != 2].ravel()
    tri = P[G]
    area2 = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    longest = np.linalg.norm(tri[:, [1, 2, 0]] - tri, axis=2).max(axis=1)
    flat = G[area2 <= size * 1e-7 * np.maximum(longest, 1e-30)]     # the flips could not reach these: move them instead
    bad = np.unique(np.r_[bad, flat.ravel()])
    used = np.zeros(len(P), bool)
    used[G] = True
    remap = np.cumsum(used) - 1
    return P[used], remap[G], np.flatnonzero(np.isin(index, bad))


def closed_arrays(solid: mf.Manifold, to_model) -> tuple:
    """(V, F, distance moved), V in the model's units, of a solid that stays closed once saved, in the float32 of an STL.

    Steps often touch along an exact edge. Manifold keeps the two sheets apart as separate vertices in one place, which
    is sound until a file format merges them into an edge shared by four faces. So those copies retract into their own
    solids, by far less than a pen line (2e-5 is 1/100000 of the model's height), and the contact opens."""
    import trimesh

    from atlas_toolkit.geom.mesh import clean
    data = solid.simplify(2e-6).to_mesh64()
    V = np.asarray(data.vert_properties, dtype=np.float64)[:, :3].copy()
    F = np.asarray(data.tri_verts, dtype=np.int64).copy()
    moved, bad = 0.0, np.zeros(0, dtype=np.int64)
    for by in (0.0, 2e-5, 2e-5, 2e-5, 5e-5, 5e-5, 5e-5, 1e-4, 1e-4, 2e-4, 2e-4, 4e-4):
        if by:
            tri = V[F]
            face = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            normals = np.zeros_like(V)
            for j in range(3):
                np.add.at(normals, F[:, j], face)
            length = np.linalg.norm(normals, axis=1, keepdims=True)
            # A vertex with no clear inside still has to move, or it would stay where it is on every pass.
            normals = np.divide(normals, length, out=np.full_like(normals, 0.5774), where=length > 1e-14)
            V[bad] -= normals[bad] * by
            moved = max(moved, by)
        P, G, bad = _weld(to_model(V), F)
        if not len(bad):
            check = clean(trimesh.Trimesh(P.copy(), G.copy(), process=False))
            if check.is_volume and len(check.faces) == len(G):
                return P, G, moved
    raise ValueError("The staircase has edges too fine to export reliably: lower Steps or raise Thickness.")


def tidy(result: mf.Manifold, keep_fragments: bool, min_share: float = 2e-3) -> tuple:
    """(solid, pieces kept, pieces removed): by default one connected solid, with whatever the clipping left floating
    removed. Kept fragments still lose the crumbs."""
    pieces = result.decompose()
    if len(pieces) <= 1:
        return result, len(pieces), 0
    pieces = sorted(pieces, key=lambda m: -m.volume())
    if not keep_fragments:
        return pieces[0], 1, len(pieces) - 1
    total = sum(m.volume() for m in pieces)
    kept = [m for m in pieces if m.volume() >= total * min_share]
    return union(kept), len(kept), len(pieces) - len(kept)
