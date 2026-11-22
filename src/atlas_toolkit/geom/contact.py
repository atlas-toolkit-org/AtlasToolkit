"""
Where the triangles of a mesh cross each other, vectorised. A uniform grid over their bounding boxes finds candidate
pairs (each pair once: in the cell that holds the low corner of the two boxes' overlap), and a plane cut tests them.
Plotter Vision draws the lines where separate parts cut into each other with it; Surface Texture keeps its relief
from pushing one piece of skin through another.

    pairs = crossings(V, F)                  # (k, 2) faces that cross and share no corner
    pairs = crossings(V, F, active=mask)     # only pairs with at least one active face (what moved)
    for a, b in box_pairs(lo, hi): ...       # candidate pairs whose boxes overlap, in chunks

A crossing needs a cut of positive length (a few billionths of the model's size): faces that only touch at a point
or along a line, as refined pieces of neighbouring planes do, do not count. Coplanar faces count when they overlap
with positive area.
"""
from __future__ import annotations

import numpy as np

BIG_CELLS = 4096        # a box spanning more grid cells than this is paired by a direct box test instead
CHUNK = 2_000_000       # candidate pairs tested at a time


def plane_cut(P: np.ndarray, n: np.ndarray, d: np.ndarray):
    """Where triangles P (k, 3, 3) cross the planes n·x = d: two points each, and whether they cross at all."""
    s = np.einsum("kij,kj->ki", P, n) - d[:, None]
    pos = s > 0
    npos = pos.sum(1)
    ok = (npos == 1) | (npos == 2)
    lone = np.argmax(np.where((npos == 1)[:, None], pos, ~pos), axis=1)
    k = np.arange(len(P))
    o1, o2 = (lone + 1) % 3, (lone + 2) % 3
    sl, s1, s2 = s[k, lone], s[k, o1], s[k, o2]
    with np.errstate(divide="ignore", invalid="ignore"):
        t1 = sl / (sl - s1)
        t2 = sl / (sl - s2)
        X1 = P[k, lone] + t1[:, None] * (P[k, o1] - P[k, lone])
        X2 = P[k, lone] + t2[:, None] * (P[k, o2] - P[k, lone])
    return X1, X2, ok & np.isfinite(t1) & np.isfinite(t2)


def box_pairs(lo: np.ndarray, hi: np.ndarray, active: np.ndarray | None = None, chunk: int | None = CHUNK,
              budget: int | None = None, min_cell: float | None = None, big_cells: int | None = BIG_CELLS,
              check=None) -> list:
    """Pairs of boxes (lo, hi: (m, 3)) that overlap, each pair once, as a list of (a, b) index arrays of about
    `chunk` candidates each (None: one array). With `active` (bool, m), only pairs with at least one active box.
    With `budget`, an empty list when the grid holds more candidate pairs than that: the caller gives up instead of
    spending the time (Plotter Vision's crossing lines). A box over more than `big_cells` cells (None: no limit) is
    paired by a direct test, so one huge face does not fill the grid."""
    lo, hi = np.asarray(lo, np.float64), np.asarray(hi, np.float64)
    m = len(lo)
    if m < 2:
        return []
    ext = (hi - lo).max(1)
    size = float((hi.max(0) - lo.min(0)).max())
    cell = max(float(np.median(ext) * 2.0), min_cell if min_cell is not None else 1e-12 * max(size, 1e-300))
    g0 = lo.min(0)
    c0 = np.floor((lo - g0) / cell).astype(np.int64)
    c1 = np.floor((hi - g0) / cell).astype(np.int64)
    dims = c1.max(0) + 1
    span = c1 - c0 + 1
    cnt = span.prod(1)
    big = cnt > big_cells if big_cells is not None else np.zeros(m, bool)
    grid = np.flatnonzero(~big)
    if active is not None:
        grid = _near_active(grid, np.asarray(active, bool), big, c0, c1, span, cnt, dims)
    face, cx, cy, cz = _expand(grid, c0, span, cnt)
    cid = (cz * dims[1] + cy) * dims[0] + cx
    del cx, cy, cz
    if active is not None:
        active = np.asarray(active, bool)
        hot = np.unique(cid[active[face]])
        keep = np.isin(cid, hot)
        face, cid = face[keep], cid[keep]
    o = np.argsort(cid, kind="stable")
    face, cid = face[o], cid[o]
    start = np.flatnonzero(np.r_[True, cid[1:] != cid[:-1]]) if len(cid) else np.zeros(0, np.int64)
    size_ = np.diff(np.r_[start, len(cid)])
    # every pair within a cell: element i with the ones after it in its cell
    end = np.repeat(start + size_, size_)
    after = end - np.arange(len(cid)) - 1
    total = int(after.sum())
    if budget is not None and total > budget:
        return []
    out = []
    pieces = [(0, len(cid))]
    if chunk is not None and total > chunk:
        cuts = np.searchsorted(np.cumsum(after), np.arange(chunk, total, chunk))
        bounds = np.unique(np.r_[0, cuts + 1, len(cid)])
        pieces = list(zip(bounds[:-1], bounds[1:]))
    for s, e in pieces:
        if check:
            check()
        aft = after[s:e]
        if not aft.sum():
            continue
        a = np.repeat(np.arange(s, e), aft)
        b = a + 1 + (np.arange(len(a)) - np.repeat(np.cumsum(aft) - aft, aft))
        fa, fb, cell_of = face[a], face[b], cid[a]
        del a, b
        olo = np.maximum(lo[fa], lo[fb])
        keep = (np.minimum(hi[fa], hi[fb]) >= olo).all(1)
        oc = np.floor((olo - g0) / cell).astype(np.int64)
        keep &= ((oc[:, 2] * dims[1] + oc[:, 1]) * dims[0] + oc[:, 0]) == cell_of        # each pair once
        if active is not None:
            keep &= active[fa] | active[fb]
        out.append((fa[keep], fb[keep]))
    for i in np.flatnonzero(big):
        if check:
            check()
        hit = (lo <= hi[i]).all(1) & (hi >= lo[i]).all(1)
        hit[i] = False
        hit[big & (np.arange(m) < i)] = False          # big-big pairs once
        if active is not None and not active[i]:
            hit &= active
        j = np.flatnonzero(hit)
        if len(j):
            out.append((np.full(len(j), i, np.int64), j))
    return out


def _expand(ids, c0, span, cnt):
    """The (box, cell x, y, z) entries of boxes `ids`."""
    count = cnt[ids]
    face = np.repeat(ids, count)
    local = np.arange(int(count.sum())) - np.repeat(np.cumsum(count) - count, count)
    sx, sy = span[face, 0], span[face, 1]
    return face, c0[face, 0] + local % sx, c0[face, 1] + (local // sx) % sy, c0[face, 2] + local // (sx * sy)


def _near_active(grid, active, big, c0, c1, span, cnt, dims, max_cells=16_000_000):
    """The boxes of `grid` that share a cell with an active box: after a local change, only those can pair with it.
    A summed-volume table of the active boxes' cells counts them for every box at once."""
    act = np.flatnonzero(active & ~big)
    if len(act) > len(grid) // 4 or int(np.prod(dims)) > max_cells:
        return grid
    hot = np.zeros(tuple(int(d) for d in dims), bool)
    if len(act):
        _, x, y, z = _expand(act, c0, span, cnt)
        hot[x, y, z] = True
    table = np.zeros(tuple(int(d) + 1 for d in dims), np.int32)
    table[1:, 1:, 1:] = hot.cumsum(0, dtype=np.int32).cumsum(1, dtype=np.int32).cumsum(2, dtype=np.int32)
    del hot
    lo, hi = c0[grid], c1[grid] + 1
    x0, y0, z0 = lo.T
    x1, y1, z1 = hi.T
    inside = (table[x1, y1, z1] - table[x0, y1, z1] - table[x1, y0, z1] - table[x1, y1, z0]
              + table[x0, y0, z1] + table[x0, y1, z0] + table[x1, y0, z0] - table[x0, y0, z0])
    return grid[inside > 0]


def _coplanar_overlap(A: np.ndarray, B: np.ndarray, normal: np.ndarray, tol: float) -> np.ndarray:
    """Whether coplanar triangles A, B (q, 3, 3) overlap by more than `tol` (separating axes, in their plane)."""
    keep = np.ones((len(A), 3), bool)
    keep[np.arange(len(A)), np.argmax(np.abs(normal), axis=1)] = False
    a = A[np.repeat(keep[:, None, :], 3, axis=1)].reshape(len(A), 3, 2)
    b = B[np.repeat(keep[:, None, :], 3, axis=1)].reshape(len(B), 3, 2)
    edges = np.concatenate([a[:, [1, 2, 0]] - a, b[:, [1, 2, 0]] - b], axis=1)          # (q, 6, 2)
    axes = np.stack([-edges[..., 1], edges[..., 0]], axis=-1)
    axes /= np.maximum(np.linalg.norm(axes, axis=-1, keepdims=True), 1e-300)
    pa = np.einsum("qad,qvd->qav", axes, a)
    pb = np.einsum("qad,qvd->qav", axes, b)
    separated = (pa.max(-1) - pb.min(-1) <= tol) | (pb.max(-1) - pa.min(-1) <= tol)
    return ~separated.any(1)


def crossing(P: np.ndarray, FN: np.ndarray, fa: np.ndarray, fb: np.ndarray, tol: float) -> np.ndarray:
    """Which face pairs (fa, fb) of triangles P (m, 3, 3) with unit normals FN cross along a cut longer than tol."""
    if not len(fa):
        return np.zeros(0, bool)
    nA, nB = FN[fa], FN[fb]
    dA = np.einsum("ij,ij->i", nA, P[fa, 0])
    dB = np.einsum("ij,ij->i", nB, P[fb, 0])
    A1, A2, okA = plane_cut(P[fa], nB, dB)
    B1, B2, okB = plane_cut(P[fb], nA, dA)
    line = np.cross(nA, nB)
    sin = np.linalg.norm(line, axis=1)
    flat = sin < 1e-6
    u = line / np.maximum(sin, 1e-300)[:, None]
    with np.errstate(invalid="ignore"):
        a1, a2 = np.einsum("ij,ij->i", A1, u), np.einsum("ij,ij->i", A2, u)
        b1, b2 = np.einsum("ij,ij->i", B1, u), np.einsum("ij,ij->i", B2, u)
        overlap = np.minimum(np.maximum(a1, a2), np.maximum(b1, b2)) - np.maximum(np.minimum(a1, a2), np.minimum(b1, b2))
        hit = ~flat & okA & okB & (overlap > tol)
    if flat.any():
        sel = np.flatnonzero(flat)
        offset = np.abs(np.einsum("ikj,ij->ik", P[fb[sel]], nA[sel]) - dA[sel, None]).max(1)
        sel = sel[offset <= tol]
        if len(sel):
            hit[sel] = _coplanar_overlap(P[fa[sel]], P[fb[sel]], nA[sel], tol)
    return hit


def crossings(V: np.ndarray, F: np.ndarray, active: np.ndarray | None = None, check=None,
              chunk: int | None = CHUNK) -> np.ndarray:
    """Pairs (k, 2) of faces that cross and share no corner. With `active` (bool per face), only pairs where at least
    one face is active: after a local change, the pairs it can have created."""
    V = np.asarray(V, np.float64)
    F = np.asarray(F, np.int64)
    if len(F) < 2:
        return np.zeros((0, 2), np.int64)
    P = V[F]
    lo, hi = P.min(1), P.max(1)
    size = float((hi.max(0) - lo.min(0)).max()) or 1.0
    tol = 1e-9 * size
    normal = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    length = np.linalg.norm(normal, axis=1)
    FN = normal / np.maximum(length, 1e-300)[:, None]
    usable = length > 1e-30 * size * size
    act = usable if active is None else (np.asarray(active, bool) & usable)
    if not act.any():
        return np.zeros((0, 2), np.int64)
    found = []
    for fa, fb in box_pairs(lo, hi, active=act, chunk=chunk, check=check):
        keep = usable[fa] & usable[fb]
        fa, fb = fa[keep], fb[keep]
        shared = (F[fa][:, :, None] == F[fb][:, None, :]).any(axis=(1, 2))
        fa, fb = fa[~shared], fb[~shared]
        hit = crossing(P, FN, fa, fb, tol)
        if hit.any():
            found.append(np.stack([fa[hit], fb[hit]], axis=1))
    if not found:
        return np.zeros((0, 2), np.int64)
    pairs = np.concatenate(found)
    return np.sort(pairs, axis=1)
