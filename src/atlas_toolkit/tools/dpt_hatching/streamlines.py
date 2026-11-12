"""
Evenly spaced streamlines (Jobard & Lefer 1997) with a spacing that varies over the picture: continuous strokes that
follow a direction field, never closer to each other than about half their local spacing, and only where the tone
asks for ink. This is notebook (b)'s idea done properly: (b) seeded a 2D grid, so every streamline was retraced many
times over (up to ~60x in dark bands) and lines bunched where the field converged.

Geometry is in field pixels; spacing comes in pixels too (the caller converts from mm). Second-order Runge-Kutta
on a bilinearly interpolated doubled-angle field, with the sign kept continuous along each stroke.

The inner loops are plain Python on lists and memoryviews (much faster than numpy scalar indexing for this access
pattern); "is another stroke closer than r?" is one lookup in a distance raster that each accepted stroke updates.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np


class Field:
    """Bilinear lookup of a doubled-angle field (H, W, 2) → unit direction (x, y), as nested lists."""

    def __init__(self, f2):
        self.h, self.w = f2.shape[:2]
        self.c = f2[..., 0].tolist()
        self.s = f2[..., 1].tolist()

    def at(self, x: float, y: float):
        w, h = self.w, self.h
        if x < 0:
            x = 0.0
        elif x > w - 1.001:
            x = w - 1.001
        if y < 0:
            y = 0.0
        elif y > h - 1.001:
            y = h - 1.001
        x0, y0 = int(x), int(y)
        fx, fy = x - x0, y - y0
        c0, c1 = self.c[y0], self.c[y0 + 1]
        s0, s1 = self.s[y0], self.s[y0 + 1]
        a = (1 - fy) * ((1 - fx) * c0[x0] + fx * c0[x0 + 1]) + fy * ((1 - fx) * c1[x0] + fx * c1[x0 + 1])
        b = (1 - fy) * ((1 - fx) * s0[x0] + fx * s0[x0 + 1]) + fy * ((1 - fx) * s1[x0] + fx * s1[x0 + 1])
        ang = 0.5 * math.atan2(b, a)
        return math.cos(ang), math.sin(ang)


FAR = 1.0e6
SEED_SLACK = 0.95     # seeds placed one spacing from a stroke must pass, despite the raster's small underestimates


class Dist:
    """Distance from each field pixel to the nearest point of the strokes accepted so far. Accepted strokes are stamped
    in with numpy; the tracer reads it point by point, bilinearly, through a memoryview (no numpy scalar overhead).
    A point's stamp reaches as far as any query near it can ask (`reach`, px), so lookups are exact up to the
    interpolation error, a small fraction of a pixel at the distances asked about."""

    def __init__(self, reach):
        self.h, self.w = reach.shape
        self.a = np.full(self.h * self.w, FAR, np.float32)
        self.mv = memoryview(self.a)
        self.reach = reach
        self._offsets: dict = {}

    def offsets(self, r: int):
        o = self._offsets.get(r)
        if o is None:
            dy, dx = np.mgrid[-r:r + 1, -r:r + 1]
            keep = dx * dx + dy * dy <= r * r
            o = self._offsets[r] = (dx[keep].astype(np.intp), dy[keep].astype(np.intp))
        return o

    def stamp(self, pts) -> None:
        h, w = self.h, self.w
        P = np.asarray(pts, np.float32)
        xi = np.clip(np.rint(P[:, 0]), 0, w - 1).astype(np.intp)
        yi = np.clip(np.rint(P[:, 1]), 0, h - 1).astype(np.intp)
        R = self.reach[yi, xi]
        for r in np.unique(R):
            sel = R == r
            ox, oy = self.offsets(int(r))
            X = xi[sel, None] + ox
            Y = yi[sel, None] + oy
            ok = (X >= 0) & (X < w) & (Y >= 0) & (Y < h)
            d = np.hypot(X - P[sel, 0:1], Y - P[sel, 1:2], dtype=np.float32)
            np.minimum.at(self.a, (Y * w + X)[ok], d[ok])        # same dtypes on both sides: the fast path

    def at(self, x: float, y: float) -> float:
        w, h = self.w, self.h
        if x < 0:
            x = 0.0
        elif x > w - 1.001:
            x = w - 1.001
        if y < 0:
            y = 0.0
        elif y > h - 1.001:
            y = h - 1.001
        x0, y0 = int(x), int(y)
        fx, fy = x - x0, y - y0
        i = y0 * w + x0
        m = self.mv
        return ((1 - fy) * ((1 - fx) * m[i] + fx * m[i + 1])
                + fy * ((1 - fx) * m[i + w] + fx * m[i + w + 1]))


class Own:
    """The points of the stroke being traced, with their place along it: a stroke stops before it runs into itself
    (closed isophotes), while its own nearby points, the ones just behind it, never count."""

    def __init__(self, cell: float):
        self.cell = cell
        self.cells: dict = {}

    def add(self, x: float, y: float, i: int) -> None:
        self.cells.setdefault((int(x // self.cell), int(y // self.cell)), []).append((x, y, i))

    def near(self, x: float, y: float, r: float, i: int, lag: int) -> bool:
        c = self.cell
        k = int(r // c) + 1
        cx, cy = int(x // c), int(y // c)
        r2 = r * r
        for gx in range(cx - k, cx + k + 1):
            for gy in range(cy - k, cy + k + 1):
                pts = self.cells.get((gx, gy))
                if pts:
                    for px, py, j in pts:
                        if (j - i > lag or i - j > lag) and (px - x) * (px - x) + (py - y) * (py - y) < r2:
                            return True
        return False


def trace_layer(field2, spacing, barrier, *, step: float, max_len: float, min_len: float, turn_deg: float,
                test_ratio: float = 0.5, min_ratio: float = 0.0, rng=None, hand: float = 0.0, order=None,
                seedable=None, cancel=None) -> list:
    """Streamlines over one tone layer.

    field2   (H, W, 2) doubled-angle direction field
    spacing  (H, W) wanted distance between neighbouring strokes, in px; inf (or <= 0) = no ink here
    barrier  (H, W) bool or None: strokes stop on it (occlusion edges)
    order    optional flat indices of candidate seeds, darkest first
    seedable optional (H, W) bool: strokes start only there, though they may run on wherever spacing is finite
    min_ratio strokes shorter than this many times their own spacing are dropped too (they read as ticks)
    Returns [[(x, y), ...], ...] in px.
    """
    from scipy.ndimage import maximum_filter
    h, w = spacing.shape
    fld = Field(field2)
    sp = np.where(np.isfinite(spacing) & (spacing > 0), spacing, 0.0).astype(np.float32)
    finite = sp[sp > 0]
    if finite.size == 0:
        return []
    sp_list = sp.tolist()
    bar = barrier.tolist() if barrier is not None else None
    seed_ok = seedable.tolist() if seedable is not None else None
    # the widest query near a point: a seed asks for its whole spacing (plus the hand jitter), so a stamp reaches the
    # largest spacing found within that distance
    widest = 1.0 + 0.15 * hand
    size = 2 * int(math.ceil(widest * float(finite.max()))) + 1
    reach = (np.ceil(maximum_filter(sp * widest, size=size)) + 2).astype(np.int32)
    dist = Dist(reach)
    near = dist.at
    cos_turn = math.cos(math.radians(turn_deg))
    rand = rng.random if rng is not None else (lambda: 0.5)

    def spacing_at(x, y):
        xi, yi = int(x + 0.5), int(y + 0.5)
        if xi < 0 or yi < 0 or xi >= w or yi >= h:
            return 0.0
        return sp_list[yi][xi]

    def blocked(x, y):
        if bar is None:
            return False
        xi, yi = int(x + 0.5), int(y + 0.5)
        return 0 <= xi < w and 0 <= yi < h and bar[yi][xi]

    def grow(x, y, dx, dy, own, sign, limit):
        """Follow the field from (x, y) heading roughly (dx, dy); points after the seed."""
        pts, length, i = [], 0.0, 0
        while length < limit:
            ux, uy = fld.at(x, y)
            if ux * dx + uy * dy < 0:
                ux, uy = -ux, -uy
            mx, my = x + 0.5 * step * ux, y + 0.5 * step * uy
            vx, vy = fld.at(mx, my)
            if vx * ux + vy * uy < 0:
                vx, vy = -vx, -vy
            if vx * dx + vy * dy < cos_turn:      # turns too sharply
                break
            nx, ny = x + step * vx, y + step * vy
            if nx < 0 or ny < 0 or nx > w - 1 or ny > h - 1:
                break
            d = spacing_at(nx, ny)
            if d <= 0 or blocked(nx, ny):
                break
            r = test_ratio * d
            if near(nx, ny) < r:
                break
            i += sign
            if own.near(nx, ny, r, i, int(r / step) + 2):
                break
            own.add(nx, ny, i)
            pts.append((nx, ny))
            length += step
            x, y, dx, dy = nx, ny, vx, vy
        return pts

    lines: list = []
    queue: deque = deque()
    if order is None:
        order = np.flatnonzero(sp.ravel() > 0)
    cand = iter(order.tolist())
    tries = 0
    while True:
        if cancel is not None and tries % 256 == 0:
            cancel()
        tries += 1
        if queue:
            sx, sy = queue.popleft()
        else:
            nxt = next(cand, None)
            if nxt is None:
                break
            sy, sx = divmod(int(nxt), w)
            sx += rand() - 0.5
            sy += rand() - 0.5
        d = spacing_at(sx, sy)
        if d <= 0 or blocked(sx, sy):
            continue
        if seed_ok is not None:
            xi, yi = int(sx + 0.5), int(sy + 0.5)
            if not (0 <= xi < w and 0 <= yi < h and seed_ok[yi][xi]):
                continue
        if hand:
            d *= 1.0 + hand * 0.3 * (rand() - 0.5)
        if near(sx, sy) < d * SEED_SLACK:
            continue
        own = Own(max(test_ratio * d, 0.5))
        own.add(sx, sy, 0)
        ux, uy = fld.at(sx, sy)
        limit = max_len * 0.5 * (1.0 - hand * 0.5 * rand())
        fwd = grow(sx, sy, ux, uy, own, 1, limit)
        back = grow(sx, sy, -ux, -uy, own, -1, limit)
        pts = back[::-1] + [(sx, sy)] + fwd
        if len(pts) < 2 or (len(pts) - 1) * step < max(min_len, min_ratio * d):
            continue
        dist.stamp(pts)
        lines.append(pts)
        # new seeds beside this stroke, one spacing away on both sides (the stroke family grows outward)
        for i in range(0, len(pts), max(1, int(3 + 2 * rand()))):
            px, py = pts[i]
            j = min(i + 1, len(pts) - 1)
            k = max(i - 1, 0)
            tx, ty = pts[j][0] - pts[k][0], pts[j][1] - pts[k][1]
            tl = math.hypot(tx, ty)
            if tl == 0:
                continue
            dd = spacing_at(px, py)
            if dd <= 0:
                continue
            nx, ny = -ty / tl * dd, tx / tl * dd
            queue.append((px + nx, py + ny))
            queue.append((px - nx, py - ny))
    return lines
