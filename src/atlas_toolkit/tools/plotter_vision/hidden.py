"""
Hidden-line removal on screen, the plotter.vision way (every candidate edge is tested against the faces that cover
it on screen, in buckets of a screen grid), solved in closed form and in bulk with numpy.

For an edge A→B on screen, p(t) = A + t (B - A), and a face (a screen triangle):
  - the face covers p(t) for t in one interval: three half-planes, each linear in t;
  - nearness q (1/depth, or far - depth for orthographic) is linear in t along the edge and linear across the face
    on screen, so "the edge is behind the face" is one more linear inequality in t.
The hidden part is the intersection of the four, so each (edge, face) pair gives at most one interval; an edge's
visible pieces are what the union of its intervals leaves of 0..1.

What this changes from plotter.vision's per-triangle clip-and-split (hidden.js):
  - no stray lines or slivers from tolerance tests on intercept points; an edge that pierces a face is cut exactly
    where it goes behind it;
  - faces of every size occlude (plotter.vision skipped faces under 2 px², so dense meshes leaked hidden lines);
  - faces partly behind the camera are cut at the near plane instead of dropped;
  - an edge is never tested against its own faces, and a line lying on another face (coplanar, touching) stays.
"""
from __future__ import annotations

import numpy as np

REL = 2e-6              # an edge must be this much nearer-ratio behind a face to count as hidden
EMPTY = 1e-9            # intervals shorter than this (in t) are nothing
PAIRS = 1_500_000       # candidate pairs per batch (memory: ~200 bytes each)


# ----------------------------------------------------------------------------- near plane

def clip_segments(C0: np.ndarray, C1: np.ndarray, near: float):
    """Camera-space segments cut at depth `near`; returns (C0, C1, keep)."""
    d0, d1 = C0[:, 2], C1[:, 2]
    keep = (d0 >= near) | (d1 >= near)
    a, b = C0.copy(), C1.copy()
    t = (near - d0) / np.where(np.abs(d1 - d0) > 1e-12, d1 - d0, 1e-12)
    cut0 = keep & (d0 < near)
    cut1 = keep & (d1 < near)
    a[cut0] = C0[cut0] + t[cut0, None] * (C1[cut0] - C0[cut0])
    b[cut1] = C0[cut1] + t[cut1, None] * (C1[cut1] - C0[cut1])
    return a, b, keep


def clip_triangles(T: np.ndarray, face: np.ndarray, near: float):
    """Camera-space triangles (k, 3, 3) cut at depth `near`: a triangle with one corner behind becomes two, with two
    corners behind becomes one smaller. Returns (triangles, face ids)."""
    inside = T[:, :, 2] >= near
    n_in = inside.sum(1)
    out_t = [T[n_in == 3]]
    out_f = [face[n_in == 3]]

    def cut(P, Q):
        t = (near - P[:, 2]) / np.where(np.abs(Q[:, 2] - P[:, 2]) > 1e-12, Q[:, 2] - P[:, 2], 1e-12)
        return P + t[:, None] * (Q - P)

    for k in (1, 2):
        sel = np.flatnonzero(n_in == k)
        if not len(sel):
            continue
        tri, ins = T[sel], inside[sel]
        # roll each triangle so the odd corner (the one inside when k == 1, outside when k == 2) comes first
        odd = np.argmax(ins if k == 1 else ~ins, axis=1)
        idx = (odd[:, None] + np.arange(3)[None, :]) % 3
        R = np.take_along_axis(tri, idx[:, :, None], axis=1)
        P0, P1, P2 = R[:, 0], R[:, 1], R[:, 2]
        if k == 1:                       # P0 in front: one triangle P0, P0→P1 cut, P0→P2 cut
            out_t.append(np.stack([P0, cut(P0, P1), cut(P0, P2)], axis=1))
            out_f.append(face[sel])
        else:                            # P0 behind: quad P0→P1 cut, P1, P2, P2→P0 cut, as two triangles
            a, b = cut(P0, P1), cut(P2, P0)
            out_t += [np.stack([a, P1, P2], axis=1), np.stack([a, P2, b], axis=1)]
            out_f += [face[sel], face[sel]]
    return np.concatenate(out_t), np.concatenate(out_f)


# ----------------------------------------------------------------------------- screen region

def clip_to_region(A: np.ndarray, B: np.ndarray, qa: np.ndarray, qb: np.ndarray, region):
    """Liang-Barsky on screen: the part of each segment inside region (x0, y0, x1, y1). Nearness is linear along
    the segment on screen, so it is cut the same way. Returns (A, B, qa, qb, keep)."""
    x0, y0, x1, y1 = region
    D = B - A
    lo = np.zeros(len(A))
    hi = np.ones(len(A))
    ok = np.ones(len(A), bool)
    for p, q in ((-D[:, 0], A[:, 0] - x0), (D[:, 0], x1 - A[:, 0]), (-D[:, 1], A[:, 1] - y0), (D[:, 1], y1 - A[:, 1])):
        par = np.abs(p) < 1e-15
        ok &= ~(par & (q < 0))
        with np.errstate(divide="ignore", invalid="ignore"):
            r = q / p
        lo = np.where(~par & (p < 0), np.maximum(lo, r), lo)
        hi = np.where(~par & (p > 0), np.minimum(hi, r), hi)
    ok &= hi - lo > EMPTY
    A2 = A + lo[:, None] * D
    B2 = A + hi[:, None] * D
    dq = qb - qa
    return A2, B2, qa + lo * dq, qa + hi * dq, ok


# ----------------------------------------------------------------------------- candidate pairs

class Grid:
    """Screen buckets of the occluding faces (plotter.vision used 16 px buckets)."""

    def __init__(self, tx0, ty0, tx1, ty1, region, cell: float):
        self.gx0, self.gy0 = region[0], region[1]
        self.cell = cell
        self.nx = max(1, int(np.ceil((region[2] - region[0]) / cell)))
        self.ny = max(1, int(np.ceil((region[3] - region[1]) / cell)))
        item, cid = self.cover(tx0, ty0, tx1, ty1)
        order = np.argsort(cid, kind="stable")
        self.tri = item[order]
        n = self.nx * self.ny
        self.count = np.bincount(cid, minlength=n)
        self.start = np.r_[0, np.cumsum(self.count)[:-1]]

    def index(self, x, y):
        cx = np.clip(np.floor((x - self.gx0) / self.cell), 0, self.nx - 1).astype(np.int64)
        cy = np.clip(np.floor((y - self.gy0) / self.cell), 0, self.ny - 1).astype(np.int64)
        return cx, cy

    def cover(self, x0, y0, x1, y1):
        """(item, cell id) for every cell each box touches."""
        cx0, cy0 = self.index(x0, y0)
        cx1, cy1 = self.index(x1, y1)
        w, h = cx1 - cx0 + 1, cy1 - cy0 + 1
        cnt = w * h
        item = np.repeat(np.arange(len(x0)), cnt)
        local = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        cx = cx0[item] + local % w[item]
        cy = cy0[item] + local // w[item]
        return item, cy * self.nx + cx


# ----------------------------------------------------------------------------- the solver

def _hidden(A, D, qa, dq, P, Q, S):
    """For pairs: edge A + tD (nearness qa + t dq) against triangles P (k, 3, 2) with nearness Q (k, 3).
    S = orientation sign of each triangle. Returns (lo, hi): the hidden interval, empty where lo >= hi."""
    lo = np.zeros(len(A))
    hi = np.ones(len(A))
    for i in range(3):
        Pi, Pj = P[:, i], P[:, (i + 1) % 3]
        E = Pj - Pi
        a = S * (E[:, 0] * (A[:, 1] - Pi[:, 1]) - E[:, 1] * (A[:, 0] - Pi[:, 0]))
        b = S * (E[:, 0] * D[:, 1] - E[:, 1] * D[:, 0])
        flat = np.abs(b) <= 1e-15
        with np.errstate(divide="ignore", invalid="ignore"):
            r = -a / b
        lo = np.where(~flat & (b > 0), np.maximum(lo, r), lo)
        hi = np.where(~flat & (b < 0), np.minimum(hi, r), hi)
        hi = np.where(flat & (a < 0), -1.0, hi)
    # the face's nearness on screen: q = α x + β y + γ
    x0, y0 = P[:, 0, 0], P[:, 0, 1]
    e1, e2 = P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
    det = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    dq1, dq2 = Q[:, 1] - Q[:, 0], Q[:, 2] - Q[:, 0]
    alpha = (dq1 * e2[:, 1] - dq2 * e1[:, 1]) / det
    beta = (dq2 * e1[:, 0] - dq1 * e2[:, 0]) / det
    qtA = Q[:, 0] + alpha * (A[:, 0] - x0) + beta * (A[:, 1] - y0)
    dqt = alpha * D[:, 0] + beta * D[:, 1]
    # hidden where qa + t dq < (1 - REL)(qtA + t dqt)
    g0 = qa - (1.0 - REL) * qtA
    g1 = dq - (1.0 - REL) * dqt
    flat = np.abs(g1) <= 1e-18
    with np.errstate(divide="ignore", invalid="ignore"):
        r = -g0 / g1
    hi = np.where(~flat & (g1 > 0), np.minimum(hi, r), hi)
    lo = np.where(~flat & (g1 < 0), np.maximum(lo, r), lo)
    hi = np.where(flat & (g0 >= 0), -1.0, hi)
    return lo, hi


def merge(seg, lo, hi):
    """Union of intervals per segment: (seg, lo, hi) merged, sorted by segment then start."""
    if not len(seg):
        return seg, lo, hi
    order = np.lexsort((lo, seg))
    seg, lo, hi = seg[order], lo[order], hi[order]
    off = seg.astype(np.float64) * 4.0                  # intervals live in 0..1: groups 4 apart never touch
    lo_o, hi_o = lo + off, hi + off
    run = np.maximum.accumulate(hi_o)
    new = np.r_[True, lo_o[1:] > run[:-1] + EMPTY]
    starts = np.flatnonzero(new)
    ends = np.maximum.reduceat(hi_o, starts)
    s = seg[starts]
    return s, lo_o[starts] - s * 4.0, ends - s * 4.0


def complement(n: int, seg, lo, hi, which=None):
    """What merged hidden intervals leave of 0..1, per segment in `which` (default all n)."""
    which = np.arange(n) if which is None else which
    has = np.zeros(n, bool)
    has[seg] = True
    free = which[~has[which]]
    out_s, out_a, out_b = [free], [np.zeros(len(free))], [np.ones(len(free))]
    if len(seg):
        first = np.r_[True, seg[1:] != seg[:-1]]
        last = np.r_[seg[1:] != seg[:-1], True]
        prev_end = np.where(first, 0.0, np.r_[0.0, hi[:-1]])
        out_s += [seg, seg[last]]
        out_a += [prev_end, hi[last]]
        out_b += [lo, np.ones(int(last.sum()))]
    s, a, b = np.concatenate(out_s), np.concatenate(out_a), np.concatenate(out_b)
    keep = b - a > EMPTY
    return s[keep], a[keep], b[keep]


def solve(A, B, qa, qb, own, T, TQ, tface, region, *, hidden=False, batch_pairs: int = PAIRS, order=None,
          progress=None, cancel=None):
    """Visible (and optionally hidden) pieces of screen segments A→B under screen triangles T (k, 3, 2).

    own     (n, 2) the faces each segment belongs to (-1 for none): never tested against them
    region  (x0, y0, x1, y1) screen area that matters (segments are already clipped to it)
    order   optional segment order for the batches (the live view goes from the middle out)
    Yields (seg ids, t0, t1, hidden?) per batch of segments, so a caller can show progress.
    """
    n = len(A)
    if n == 0:
        return
    D = B - A
    dq = qb - qa
    sx0, sx1 = np.minimum(A[:, 0], B[:, 0]), np.maximum(A[:, 0], B[:, 0])
    sy0, sy1 = np.minimum(A[:, 1], B[:, 1]), np.maximum(A[:, 1], B[:, 1])
    sqmin = np.minimum(qa, qb)
    if len(T):
        e1, e2 = T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
        det = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
        ok = np.abs(det) > 1e-14                       # faces seen edge-on cover nothing
        T, TQ, tface, det = T[ok], TQ[ok], tface[ok], det[ok]
        TS = np.sign(det)
        tx0, tx1 = T[:, :, 0].min(1), T[:, :, 0].max(1)
        ty0, ty1 = T[:, :, 1].min(1), T[:, :, 1].max(1)
        inr = (tx1 >= region[0]) & (tx0 <= region[2]) & (ty1 >= region[1]) & (ty0 <= region[3])
        T, TQ, tface, TS = T[inr], TQ[inr], tface[inr], TS[inr]
        tx0, tx1, ty0, ty1 = tx0[inr], tx1[inr], ty0[inr], ty1[inr]
        tqmax = TQ.max(1)
    if len(T) == 0:
        seq = np.arange(n) if order is None else order
        yield seq, np.zeros(n), np.ones(n), False
        return
    ext = np.maximum(tx1 - tx0, ty1 - ty0)
    span = max(region[2] - region[0], region[3] - region[1])
    cell = float(np.clip(np.median(ext) * 2.0, span / 1024.0, span / 4.0))
    grid = Grid(tx0, ty0, tx1, ty1, region, cell)
    seq = np.arange(n) if order is None else np.asarray(order)
    rank = np.empty(n, np.int64)
    rank[seq] = np.arange(n)
    si, sc = grid.cover(sx0, sy0, sx1, sy1)
    # sort the (segment, cell) pairs into the batch order, then cut batches at segment boundaries
    o = np.lexsort((sc, rank[si]))
    si, sc = si[o], sc[o]
    cum = np.cumsum(grid.count[sc])
    seg_end = np.flatnonzero(np.r_[si[1:] != si[:-1], True]) + 1      # end index of each segment's run
    bid = cum[seg_end - 1] // max(int(batch_pairs), 1)
    first = np.flatnonzero(np.r_[True, bid[1:] != bid[:-1]])           # first segment of each batch
    bounds = sorted(set([0] + seg_end[first[1:] - 1].tolist() + [len(si)]))
    done_segments = 0
    for bi in range(len(bounds) - 1):
        if cancel is not None:
            cancel()
        a, b = bounds[bi], bounds[bi + 1]
        csi, csc = si[a:b], sc[a:b]
        segs = np.unique(csi)
        rep = grid.count[csc]
        tot = int(rep.sum())
        if tot:
            ps = np.repeat(csi, rep)
            pc = np.repeat(csc, rep)
            local = np.arange(tot) - np.repeat(np.cumsum(rep) - rep, rep)
            pt = grid.tri[grid.start[pc] + local]
            # one test per (segment, face): keep the pair only in the cell holding the corner of their overlap
            ox = np.maximum(sx0[ps], tx0[pt])
            oy = np.maximum(sy0[ps], ty0[pt])
            keep = (np.minimum(sx1[ps], tx1[pt]) >= ox) & (np.minimum(sy1[ps], ty1[pt]) >= oy)
            cx, cy = grid.index(ox, oy)
            keep &= cy * grid.nx + cx == pc
            keep &= sqmin[ps] < (1.0 - REL) * tqmax[pt]                     # the face is nearer somewhere
            keep &= (tface[pt] != own[ps, 0]) & (tface[pt] != own[ps, 1])
            ps, pt = ps[keep], pt[keep]
            lo, hi = _hidden(A[ps], D[ps], qa[ps], dq[ps], T[pt], TQ[pt], TS[pt])
            hid = hi - lo > EMPTY
            ms, ml, mh = merge(ps[hid], lo[hid], hi[hid])
        else:
            ms = ml = mh = np.zeros(0)
            ms = ms.astype(np.int64)
        vs, va, vb = complement(n, ms, ml, mh, segs)
        yield vs, va, vb, False
        if hidden and len(ms):
            yield ms, ml, mh, True
        done_segments += len(segs)
        if progress is not None:
            progress(done_segments / n)
