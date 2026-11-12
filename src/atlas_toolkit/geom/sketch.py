"""
Drawing on the paper grid, shared by the 3D drawing tools (Sketch Hatching, Stipple): polyline utilities, the
silhouette straight from a ray-cast hit mask, blurs that respect the figure's edge, an occupancy raster for
priority clipping, and a raster of pen layers at their real widths (for previews and the ink-coverage check).

From the Blender pieces' sketch pipeline (projects/blender3d_project/*/plotter_lib/sketch_draw.py), unchanged.
G-buffers are dicts from geom/shading.py: "shape" (H, W), "x0", "y0" (mm of the first pixel's corner), "px" (mm per
pixel) and flat per-pixel arrays ("hit", "depth", ...).
"""
import math

import numpy as np

def plen(pl):
    pl = np.asarray(pl)
    return float(np.linalg.norm(np.diff(pl, axis=0), axis=1).sum()) if len(pl) > 1 else 0.0


def resample(pl, step):
    pl = np.asarray(pl, float)
    seg = np.linalg.norm(np.diff(pl, axis=0), axis=1)
    s = np.r_[0.0, np.cumsum(seg)]
    if s[-1] <= 0:
        return pl[:1]
    n = max(2, int(math.ceil(s[-1] / step)) + 1)
    t = np.linspace(0, s[-1], n)
    return np.c_[np.interp(t, s, pl[:, 0]), np.interp(t, s, pl[:, 1])]


def smooth(pl, iters=2, closed=False):
    """3-point moving average; open strokes keep their end points."""
    pl = np.asarray(pl, float).copy()
    if len(pl) < 3:
        return pl
    for _ in range(iters):
        if closed:
            core = pl[:-1]
            core = 0.25 * np.roll(core, 1, 0) + 0.5 * core + 0.25 * np.roll(core, -1, 0)
            pl = np.vstack([core, core[:1]])
        else:
            pl[1:-1] = 0.25 * pl[:-2] + 0.5 * pl[1:-1] + 0.25 * pl[2:]
    return pl


def smooth_mm(pl, sigma, closed=False):
    """Gaussian smoothing along arc length (sigma in mm). Open strokes keep their end points
    (odd reflection at the ends, so straight runs neither shrink nor bend)."""
    pl = np.asarray(pl, float)
    if sigma <= 0 or len(pl) < 3 or plen(pl) < 2 * sigma:
        return pl
    step = sigma / 3.0
    r = resample(pl, step)
    k = int(math.ceil(3 * sigma / step))
    x = np.arange(-k, k + 1) * step
    w = np.exp(-x * x / (2 * sigma * sigma))
    w /= w.sum()
    if closed:
        core = r[:-1]
        pad = np.vstack([core[-k:], core, core[:k]]) if len(core) > k else None
        if pad is None:
            return pl
        out = np.c_[np.convolve(pad[:, 0], w, "valid"), np.convolve(pad[:, 1], w, "valid")]
        return np.vstack([out, out[:1]])
    if len(r) <= k + 1:
        return pl
    head = 2 * r[0] - r[k:0:-1]
    tail = 2 * r[-1] - r[-2:-k - 2:-1]
    pad = np.vstack([head, r, tail])
    out = np.c_[np.convolve(pad[:, 0], w, "valid"), np.convolve(pad[:, 1], w, "valid")]
    out[0], out[-1] = r[0], r[-1]
    return out


def rdp(pl, eps):
    """Ramer-Douglas-Peucker with an explicit stack (vectorised distance per span)."""
    pl = np.asarray(pl, float)
    n = len(pl)
    if n < 3:
        return pl
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        p, q = pl[a], pl[b]
        d = q - p
        L = math.hypot(*d)
        seg = pl[a + 1:b]
        if L > 0:
            dist = np.abs(d[0] * (seg[:, 1] - p[1]) - d[1] * (seg[:, 0] - p[0])) / L
        else:
            dist = np.linalg.norm(seg - p, axis=1)
        i = int(np.argmax(dist))
        if dist[i] > eps:
            m = a + 1 + i
            keep[m] = True
            stack += [(a, m), (m, b)]
    return pl[keep]


def normals2d(pl):
    """Unit left normals per point (central differences)."""
    t = np.gradient(pl, axis=0)
    n = np.linalg.norm(t, axis=1, keepdims=True)
    t = t / np.where(n > 0, n, 1.0)
    return np.c_[-t[:, 1], t[:, 0]]


def _runs(mask):
    """(start, stop) of True runs in a boolean array."""
    m = np.r_[False, np.asarray(mask, bool), False].astype(np.int8)
    d = np.diff(m)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def _majority(flag, n):
    """Remove flips shorter than n samples (both values)."""
    flag = np.asarray(flag, bool).copy()
    if len(flag) < 2 * n:
        return np.full(len(flag), flag.mean() >= 0.5)
    for val in (True, False):
        for a, b in _runs(flag == val):
            if b - a < n and a > 0 and b < len(flag):
                flag[a:b] = not val
    return flag


def _arclen(pts):
    return np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]


class Occupancy:
    """Boolean raster over the page for O(1) 'is something drawn within r' tests."""

    def __init__(self, page, cell=0.1):
        self.cell = cell
        self.W, self.H = int(math.ceil(page[0] / cell)) + 1, int(math.ceil(page[1] / cell)) + 1
        self.grid = np.zeros((self.H, self.W), bool)
        self._discs = {}

    def _disc(self, r):
        k = int(math.ceil(r / self.cell))
        if k not in self._discs:
            y, x = np.mgrid[-k:k + 1, -k:k + 1]
            m = (x * x + y * y) * self.cell ** 2 <= r * r + 1e-12
            self._discs[k] = (y[m], x[m])
        return self._discs[k]

    def paint(self, pts, radius):
        pts = resample(pts, self.cell * 0.7) if len(pts) > 1 else np.asarray(pts)
        dy, dx = self._disc(radius)
        i = (pts[:, 1] / self.cell).round().astype(np.int64)
        j = (pts[:, 0] / self.cell).round().astype(np.int64)
        I = np.clip(i[:, None] + dy[None, :], 0, self.H - 1)
        J = np.clip(j[:, None] + dx[None, :], 0, self.W - 1)
        self.grid[I, J] = True

    def test(self, pts):
        i = np.clip((pts[:, 1] / self.cell).round().astype(np.int64), 0, self.H - 1)
        j = np.clip((pts[:, 0] / self.cell).round().astype(np.int64), 0, self.W - 1)
        return self.grid[i, j]


def clip_against(strokes, page, gap, min_run=1.5, step=0.15):
    """Process strokes in order; clip each where it runs within `gap` mm of strokes already
    kept for more than `min_run` mm (brief contacts such as T-junctions survive)."""
    occ = Occupancy(page, cell=min(0.1, gap / 3))
    run_n = max(1, int(min_run / step))
    out = []
    for st in strokes:
        pts = resample(st["pts"], step)
        near = occ.test(pts)
        cut = np.zeros(len(pts), bool)
        for a, b in _runs(near):
            if b - a >= run_n:
                cut[a:b] = True
        for a, b in _runs(~cut):
            if b - a >= 2:
                piece = pts[a:b]
                out.append({**st, "pts": piece})
        occ.paint(pts[~cut] if (~cut).any() else pts[:0], gap)
    return out


def _chain_keys(k1, k2):
    """Chain segments (key k1[i] -- key k2[i]) into maximal paths (closed loops flagged)."""
    adj = {}
    for i, (a, b) in enumerate(zip(k1, k2)):
        if a != b:
            adj.setdefault(a, []).append((b, i))
            adj.setdefault(b, []).append((a, i))
    used = np.zeros(len(k1), bool)

    def walk(start, nb, seg):
        chain, cur = [start, nb], nb
        used[seg] = True
        while len(adj[cur]) == 2 and cur != start:
            nxt = [(k, s) for k, s in adj[cur] if not used[s]]
            if not nxt:
                break
            cur, s = nxt[0]
            used[s] = True
            chain.append(cur)
        return chain

    out = []
    for key, nbs in adj.items():
        if len(nbs) != 2:
            for nb, seg in nbs:
                if not used[seg]:
                    out.append((walk(key, nb, seg), False))
    for key, nbs in adj.items():
        for nb, seg in nbs:
            if not used[seg]:
                ch = walk(key, nb, seg)
                out.append((ch, len(ch) > 3 and ch[-1] == ch[0]))
    return out


def iso_lines(f, iso=0.5):
    """Marching squares: iso-contours of a 2D array as polylines in (x = column, y = row)
    pixel-centre coordinates; saddles resolved by the cell-centre average."""
    H, W = f.shape
    a, b, c, d = f[:-1, :-1], f[:-1, 1:], f[1:, 1:], f[1:, :-1]     # TL, TR, BR, BL
    A, B, C, D = a > iso, b > iso, c > iso, d > iso
    case = A * 1 + B * 2 + C * 4 + D * 8
    ii, jj = np.nonzero((case > 0) & (case < 15))
    cs = case[ii, jj]

    def hkey(i, j):
        return (i * W + j) * 2

    def vkey(i, j):
        return (i * W + j) * 2 + 1

    # edge crossing points: top h(i,j), right v(i,j+1), bottom h(i+1,j), left v(i,j)
    def pt_h(i, j):
        t = (iso - f[i, j]) / (f[i, j + 1] - f[i, j])
        return np.c_[j + t, i.astype(float)]

    def pt_v(i, j):
        t = (iso - f[i, j]) / (f[i + 1, j] - f[i, j])
        return np.c_[j.astype(float), i + t]

    edges = {0: (lambda i, j: hkey(i, j), pt_h, (0, 0)), 1: (lambda i, j: vkey(i, j + 1), pt_v, (0, 1)),
             2: (lambda i, j: hkey(i + 1, j), pt_h, (1, 0)), 3: (lambda i, j: vkey(i, j), pt_v, (0, 0))}
    pairs = {1: [(3, 0)], 2: [(0, 1)], 3: [(3, 1)], 4: [(1, 2)], 6: [(0, 2)], 7: [(3, 2)], 8: [(2, 3)],
             9: [(0, 2)], 11: [(1, 2)], 12: [(1, 3)], 13: [(0, 1)], 14: [(3, 0)]}
    centre = (a + b + c + d)[ii, jj] / 4 > iso
    k1, k2, pts = [], [], {}
    for case_id in range(1, 15):
        sel = cs == case_id
        if not sel.any():
            continue
        if case_id in (5, 10):
            # saddle: connected through the centre or not
            segs_in = [(0, 1), (2, 3)] if case_id == 5 else [(3, 0), (1, 2)]
            segs_out = [(3, 0), (1, 2)] if case_id == 5 else [(0, 1), (2, 3)]
            groups = [(sel & centre, segs_in), (sel & ~centre, segs_out)]
        else:
            groups = [(sel, pairs[case_id])]
        for g, segs in groups:
            if not g.any():
                continue
            i, j = ii[g], jj[g]
            for e1, e2 in segs:
                for e, keys in ((e1, k1), (e2, k2)):
                    kf, pf, (di, dj) = edges[e]
                    kk = kf(i, j)
                    p = pf(i + di, j + dj)
                    keys.extend(kk.tolist())
                    for key, q in zip(kk.tolist(), p):
                        pts.setdefault(key, q)
    out = []
    for chain, closed in _chain_keys(k1, k2):
        pl = np.array([pts[k] for k in chain])
        out.append((pl, closed))
    return out


def mask_outline(gb, blur_px=0.7, min_len_mm=1.5):
    """The figure's silhouette against the background, straight from the ray-cast hit mask:
    unbroken wherever background meets surface, whatever the geometry (smooth contour, sharp
    scan cut, boundary). Page-mm polylines."""
    H, W = gb["shape"]
    f = np.pad(gb["hit"].reshape(H, W).astype(np.float64), 1)
    if blur_px > 0:
        f = blur(f, blur_px) / blur(np.ones_like(f), blur_px)
    out = []
    for pl, closed in iso_lines(f, 0.5):
        xy = np.c_[gb["x0"] + (pl[:, 0] - 1 + 0.5) * gb["px"], gb["y0"] + (pl[:, 1] - 1 + 0.5) * gb["px"]]
        if closed:
            xy = np.vstack([xy, xy[:1]])
        if plen(xy) >= min_len_mm:
            out.append((xy, closed))
    return out


def gb_sample(gb, xy, key):
    """Nearest-pixel lookup of a per-pixel G-buffer array at page points xy (outside = 0)."""
    H, W = gb["shape"]
    j = np.floor((xy[:, 0] - gb["x0"]) / gb["px"]).astype(np.int64)
    i = np.floor((xy[:, 1] - gb["y0"]) / gb["px"]).astype(np.int64)
    inside = (i >= 0) & (i < H) & (j >= 0) & (j < W)
    arr = np.asarray(gb[key]) if isinstance(key, str) else np.asarray(key)
    if arr.shape[:2] == (H, W):
        arr = arr.reshape((H * W,) + arr.shape[2:])
    out = np.zeros((len(xy),) + arr.shape[1:], arr.dtype)
    out[inside] = arr[(i * W + j)[inside]]
    return out


def _box_blur(a, r, axis):
    if r <= 0:
        return a
    n = a.shape[axis]
    c = np.cumsum(a, axis=axis, dtype=np.float64)
    pad_shape = list(a.shape)
    pad_shape[axis] = 1
    c = np.concatenate([np.zeros(pad_shape), c], axis=axis)
    hi = np.minimum(np.arange(n) + r + 1, n)
    lo = np.maximum(np.arange(n) - r, 0)
    return np.take(c, hi, axis=axis) - np.take(c, lo, axis=axis)


def blur(a, sigma_px):
    """Gaussian-like blur (3 box passes per axis), sums not normalised at borders."""
    if sigma_px <= 0:
        return a.astype(np.float64)
    r = max(1, int(round((math.sqrt(12 * sigma_px ** 2 / 3 + 1) - 1) / 2)))
    out = a.astype(np.float64)
    for axis in (0, 1):
        for _ in range(3):
            out = _box_blur(out, r, axis)
    return out


def masked_blur(a, mask, sigma_px):
    """Normalised convolution: blur `a` using only pixels where mask is set."""
    m = mask.astype(np.float64)
    if a.ndim == 3:
        num = np.stack([blur(a[..., c] * m, sigma_px) for c in range(a.shape[2])], -1)
        den = blur(m, sigma_px)[..., None]
    else:
        num, den = blur(a * m, sigma_px), blur(m, sigma_px)
    return np.where(den > 1e-9, num / np.where(den > 1e-9, den, 1.0), 0.0)


def rasterize(layers, page, px_per_mm=6.0, crop=None, aa=2):
    """Render pen layers at their real widths: (H, W) float image, 1 = paper, 0 = ink.
    crop = (x0, y0, x1, y1) in mm renders only that window."""
    x0, y0, x1, y1 = crop if crop else (0.0, 0.0, page[0], page[1])
    k = px_per_mm * aa
    W, H = int(math.ceil((x1 - x0) * k)), int(math.ceil((y1 - y0) * k))
    ink = np.zeros((H, W), bool)
    for L in layers:
        r = max(0.5, L["width"] / 2 * k)
        rr = int(math.ceil(r))
        yy, xx = np.mgrid[-rr:rr + 1, -rr:rr + 1]
        m = xx * xx + yy * yy <= r * r
        dy, dx = yy[m], xx[m]
        for pl in L["strokes"]:
            pl = np.asarray(pl, float)
            if crop is not None:
                if (pl[:, 0].max() < x0 - 1 or pl[:, 0].min() > x1 + 1 or pl[:, 1].max() < y0 - 1
                        or pl[:, 1].min() > y1 + 1):
                    continue
            pts = resample(pl, 0.4 / k) if len(pl) > 1 else pl
            i = ((pts[:, 1] - y0) * k).round().astype(np.int64)
            j = ((pts[:, 0] - x0) * k).round().astype(np.int64)
            I, J = i[:, None] + dy[None, :], j[:, None] + dx[None, :]
            ok = (I >= 0) & (I < H) & (J >= 0) & (J < W)
            ink[I[ok], J[ok]] = True
    img = 1.0 - ink.astype(np.float32)
    if aa > 1:
        Hc, Wc = H // aa, W // aa
        img = img[:Hc * aa, :Wc * aa].reshape(Hc, aa, Wc, aa).mean((1, 3))
    return img


def coverage(layers, page, cell_mm=5.0, px_per_mm=10.0) -> float:
    """Largest share of ink in any cell_mm x cell_mm square of the page, with every pen at its real width (the
    pieces kept it under 0.7: denser, the paper cockles and the lines merge into black)."""
    img = rasterize(layers, page, px_per_mm, aa=1)
    ink = img < 0.5
    c = int(round(cell_mm * px_per_mm))
    Hc, Wc = ink.shape[0] // c, ink.shape[1] // c
    if Hc == 0 or Wc == 0:
        return float(ink.mean()) if ink.size else 0.0
    return float(ink[:Hc * c, :Wc * c].reshape(Hc, c, Wc, c).mean((1, 3)).max())

