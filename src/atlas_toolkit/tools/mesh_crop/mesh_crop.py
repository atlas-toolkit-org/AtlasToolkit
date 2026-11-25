"""Shape Crop's geometry: booleans with a placed primitive, and an exact surface clip for open models. No Qt, no I/O.

Two ways, chosen by the engine:

* **Closed (solids).** manifold3d intersects, subtracts or unites the model with the shape (geom/region.solid). The
  cut is filled by the shape's own surface, so the result is closed.
* **Open (any surface).** `clip` keeps the model's own faces inside (or outside) the shape and splits the faces that
  cross it, with the new vertices exactly on the shape (geom/region.crossing). Only faces near the shape are looked
  at; the rest are kept or dropped whole. Crossing points are made once per edge, so the result has no cracks.
"""
from __future__ import annotations

import numpy as np

from atlas_toolkit.geom import region as R

NEAR = 0.6          # faces whose unit-space bounds reach the cube [-NEAR, NEAR]^3 are clipped; the rest are whole
REFINE = 0.55       # curved shapes: long edges crossing [-REFINE, REFINE]^3 are split first (never a far face's)
REFINE_EDGE = 1.0 / 32.0     # ... until shorter than this share of the shape's size
REFINE_BUDGET = 4_000_000    # faces added by that refinement, at most


# ------------------------------------------------------------------------------------------------ open clip
def _edge_keys(e, n):
    lo, hi = np.minimum(e[:, 0], e[:, 1]), np.maximum(e[:, 0], e[:, 1])
    return lo * np.int64(n) + hi, lo, hi


def _split(V, L, F, c):
    """Cut faces F along constraint c. Returns V, L (grown by the crossing points) and the faces inside and outside
    it, which share those points."""
    s = R.value(c, L)
    ins = s <= 0
    fin = ins[F]
    n = fin.sum(1)
    whole_in, whole_out = F[n == 3], F[n == 0]
    cut = np.flatnonzero((n == 1) | (n == 2))
    if not len(cut):
        return V, L, whole_in, whole_out
    Fc, Ic = F[cut], fin[cut]
    lone_in = n[cut] == 1
    lone = np.where(lone_in[:, None], Ic, ~Ic)
    k = np.argmax(lone, axis=1)
    T = np.take_along_axis(Fc, (k[:, None] + np.arange(3)) % 3, axis=1)     # lone vertex first, winding kept
    a, b, c_ = T[:, 0], T[:, 1], T[:, 2]
    m = len(T)
    E = np.concatenate([np.column_stack((a, b)), np.column_stack((a, c_))])
    key, lo, hi = _edge_keys(E, len(V))
    _, first, inv = np.unique(key, return_index=True, return_inverse=True)
    e0, e1 = lo[first], hi[first]
    t = R.crossing(c, L[e0], L[e1], s[e0], s[e1])
    ids = np.where(t <= 0.0, e0, np.where(t >= 1.0, e1, -1))
    fresh = ids < 0
    ids[fresh] = len(V) + np.arange(int(fresh.sum()))
    tf = t[fresh][:, None]
    V = np.vstack((V, V[e0[fresh]] + tf * (V[e1[fresh]] - V[e0[fresh]])))
    L = np.vstack((L, L[e0[fresh]] + tf * (L[e1[fresh]] - L[e0[fresh]])))
    inv = inv.reshape(-1)
    pab, pac = ids[inv[:m]], ids[inv[m:]]
    tip = np.column_stack((a, pab, pac))
    quad = np.concatenate([np.column_stack((pab, b, c_)), np.column_stack((pab, c_, pac))])
    quad_in = np.concatenate([~lone_in, ~lone_in])
    parts_in = [whole_in, tip[lone_in], quad[quad_in]]
    parts_out = [whole_out, tip[~lone_in], quad[~quad_in]]
    return V, L, _valid(np.concatenate(parts_in)), _valid(np.concatenate(parts_out))


def _valid(F):
    return F[(F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 2] != F[:, 0])]


def _segment_hits_box(a, b, half):
    """Which segments a -> b cross the cube [-half, half]^3 (slab test)."""
    d = b - a
    with np.errstate(divide="ignore", invalid="ignore"):
        t0 = (-half - a) / d
        t1 = (half - a) / d
    lo = np.where(d == 0, np.where(np.abs(a) <= half, -np.inf, np.inf), np.minimum(t0, t1))
    hi = np.where(d == 0, np.where(np.abs(a) <= half, np.inf, -np.inf), np.maximum(t0, t1))
    enter = np.maximum(lo.max(1), 0.0)
    leave = np.minimum(hi.min(1), 1.0)
    return enter <= leave


def refine(V, L, F, budget=REFINE_BUDGET, edge=REFINE_EDGE, half=REFINE):
    """Split the edges of F longer than `edge` (in unit-shape space) that cross the cube [-half, half]^3, until none
    is left or the budget is spent: a curved shape then meets the surface where it really does, even on a coarse
    model. Conforming (each edge is split once, for both its faces). Returns V, L, F, limited."""
    added = 0
    for _ in range(16):
        E = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
        key, lo, hi = _edge_keys(E, len(V))
        _, first, inv = np.unique(key, return_index=True, return_inverse=True)
        inv = inv.reshape(-1)
        e0, e1 = lo[first], hi[first]
        long_ = np.linalg.norm(L[e1] - L[e0], axis=1) > edge
        idx = np.flatnonzero(long_)
        if len(idx):
            idx = idx[_segment_hits_box(L[e0[idx]], L[e1[idx]], half)]
        if not len(idx):
            return V, L, F, False
        mark = np.zeros(len(e0), bool)
        mark[idx] = True
        per = mark[inv].reshape(3, -1).T                   # face x (edge 01, 12, 20)
        count = per.sum(1)
        growth = int(count.sum())
        if added + growth > budget:
            return V, L, F, True
        added += growth
        mid = np.full(len(e0), -1, np.int64)
        mid[idx] = len(V) + np.arange(len(idx))
        V = np.vstack((V, (V[e0[idx]] + V[e1[idx]]) * 0.5))
        L = np.vstack((L, (L[e0[idx]] + L[e1[idx]]) * 0.5))
        M = mid[inv].reshape(3, -1).T                      # midpoint of edge 01, 12, 20 (or -1)
        out = [F[count == 0]]
        # one edge: rotate it to 01 -> (a, m, c), (m, b, c)
        sel = np.flatnonzero(count == 1)
        if len(sel):
            r = np.argmax(per[sel], axis=1)
            Fr = np.take_along_axis(F[sel], (r[:, None] + np.arange(3)) % 3, 1)
            m = np.take_along_axis(M[sel], r[:, None], 1)[:, 0]
            out += [np.column_stack((Fr[:, 0], m, Fr[:, 2])), np.column_stack((m, Fr[:, 1], Fr[:, 2]))]
        # two edges: rotate the unmarked one to 20 -> (mab, b, mbc), (a, mab, mbc), (a, mbc, c)
        sel = np.flatnonzero(count == 2)
        if len(sel):
            u = np.argmin(per[sel], axis=1)
            r = (u + 1) % 3
            rot = (r[:, None] + np.arange(3)) % 3
            Fr = np.take_along_axis(F[sel], rot, 1)
            Mr = np.take_along_axis(M[sel], rot, 1)
            a, b, c = Fr.T
            mab, mbc = Mr[:, 0], Mr[:, 1]
            out += [np.column_stack((mab, b, mbc)), np.column_stack((a, mab, mbc)), np.column_stack((a, mbc, c))]
        sel = np.flatnonzero(count == 3)
        if len(sel):
            a, b, c = F[sel].T
            mab, mbc, mca = M[sel].T
            out += [np.column_stack((a, mab, mca)), np.column_stack((mab, b, mbc)),
                    np.column_stack((mca, mbc, c)), np.column_stack((mab, mbc, mca))]
        F = np.concatenate(out)
    return V, L, F, True


def clip(V, F, kind, m, keep="inside"):
    """The model's own surface inside (or outside) the placed shape, split exactly along it. Returns
    (vertices, faces, notes); the faces keep their winding and the result has no unreferenced vertices."""
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    L = R.to_local(V, m)
    notes = []
    tri = L[F]
    near = np.all(tri.max(1) >= -NEAR, axis=1) & np.all(tri.min(1) <= NEAR, axis=1)
    del tri
    far, work = F[~near], F[near]
    if len(work) and kind in R.CURVED:
        V, L, work, limited = refine(V, L, work)
        if limited:
            notes.append("The model's faces near the shape are very large; the cut follows the curve less closely.")
    kept_out = [far] if keep == "outside" else []
    for c in R.constraints(kind):
        if not len(work):
            break
        V, L, inner, outer = _split(V, L, work, c)
        if keep == "outside":
            kept_out.append(outer)
        work = inner
    faces = work if keep == "inside" else (np.concatenate(kept_out) if kept_out else np.zeros((0, 3), np.int64))
    return _compact(V, faces) + (notes,)


def _compact(V, F):
    if not len(F):
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    used = np.zeros(len(V), bool)
    used[F] = True
    remap = np.cumsum(used) - 1
    return V[used], remap[F]


# ------------------------------------------------------------------------------------------------ booleans
def median_edge(V, F, sample: int = 200_000) -> float:
    F = np.asarray(F)
    if len(F) > sample:
        F = F[np.linspace(0, len(F) - 1, sample, dtype=np.int64)]
    if not len(F):
        return 1.0
    return float(np.median(np.linalg.norm(V[F[:, 1]] - V[F[:, 0]], axis=1))) or 1.0


def to_arrays(body):
    data = body.to_mesh64()
    return np.asarray(data.vert_properties, dtype=np.float64)[:, :3], np.asarray(data.tri_verts, dtype=np.int64)


def boolean(body, kind, m, operation, segments):
    """manifold3d's result: [(label, vertices, faces)], one piece, or two for the symmetric difference."""
    shape = R.solid(kind, m, segments)
    if operation == "intersect":
        pieces = [("model", body ^ shape)]
    elif operation == "subtract":
        pieces = [("model", body - shape)]
    elif operation == "union":
        pieces = [("model", body + shape)]
    elif operation == "xor":
        pieces = [("model", body - shape), ("shape", shape - body)]
    else:
        raise ValueError(f"unknown operation {operation!r}")
    out = []
    for label, piece in pieces:
        if piece.is_empty():
            continue
        out.append((label, *to_arrays(piece), float(piece.volume())))
    return out, float(shape.volume())
