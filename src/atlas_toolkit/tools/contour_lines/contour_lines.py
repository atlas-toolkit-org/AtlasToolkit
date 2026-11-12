"""
contour_lines.py - a 3D model drawn as horizontal slices, seen through a camera with the hidden parts removed: the
contour style of the Blender pieces in projects/blender3d_project (skill plotter-contour-art: head_plot_pipeline
_slice_heights, _slice, trace_contours, layout_strokes), generalised to any model and made fast.

    heights   planes every `spacing` mm on paper (at the look-at point), on a grid anchored at the frame's floor, so
              every stage of a sequence (one frame for the set, geom/frame3d) gets its lines at the same heights
    slicing   vectorised marching triangles; segment ends are keyed by the mesh edge they lie on, so neighbouring
              triangles join exactly, chained by sorting the keys (no per-plane dictionaries)
    hidden    every sample of every contour in one batch of embree rays from the eye, each switch from seen to
              hidden bisected (geom/raycast) - the Blender pieces cast one BVH ray per point from Python
    plotting  short hidden gaps drawn straight through, converging lines tapered off (min_gap) instead of pooling
              ink at grazing silhouettes, simplified, ordered nearest-first and chained (same contour within
              join_same, any within join_any), fragments dropped

All model coordinates are canonical (largest side 100, Z up); paper in mm.
"""
from __future__ import annotations

import math

import numpy as np

from atlas_toolkit.geom import raycast as rc
from atlas_toolkit.geom.camera import NEAR
from atlas_toolkit.geom.svgout import order_indices, simplify_all


# ----------------------------------------------------------------------------- planes

def slice_heights(lo: float, hi: float, spacing: float, origin: float, offset: float = 0.0) -> np.ndarray:
    """Heights origin + (k + offset)·spacing strictly inside (lo, hi): a grid shared by every model that uses
    the same origin and spacing."""
    if spacing <= 0 or hi <= lo:
        return np.zeros(0)
    k0 = math.floor((lo - origin) / spacing - offset) - 1
    k1 = math.ceil((hi - origin) / spacing - offset) + 1
    h = origin + (np.arange(k0, k1 + 1) + offset) * spacing
    return h[(h > lo) & (h < hi)]


def slice_mesh(V: np.ndarray, F: np.ndarray, heights, cancel=None) -> list:
    """Cut the mesh with horizontal planes. Returns [(points (k, 3), closed, height index)], each contour once."""
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    nv = len(V)
    z = V[:, 2].copy()
    heights = np.asarray(heights, dtype=np.float64)
    if not len(heights) or not len(F):
        return []
    span = float(np.ptp(z)) or 1.0
    # a vertex exactly on a plane would give a zero-length crossing: nudge such planes a hair
    for i, d in enumerate(heights):
        if np.any(np.abs(z - d) < 1e-12 * span):
            heights[i] = d + 1e-7 * span
    tz = z[F]
    tmin, tmax = tz.min(1), tz.max(1)
    order = np.argsort(tmin)
    tmin_sorted = tmin[order]
    keys_a, keys_b, pa, pb, hid = [], [], [], [], []
    pairs = ((0, 1), (1, 2), (2, 0))
    for hi_, d in enumerate(heights):
        if cancel is not None and hi_ % 16 == 0:
            cancel()
        cand = order[:np.searchsorted(tmin_sorted, d, side="left")]
        sel = cand[tmax[cand] > d]
        if not len(sel):
            continue
        tri = F[sel]
        above = z[tri] > d
        pts, keys, cross = [], [], []
        for i, j in pairs:
            a, b = tri[:, i], tri[:, j]
            za, zb = z[a], z[b]
            den = np.where(za != zb, zb - za, 1.0)
            t = np.where(za != zb, (d - za) / den, 0.5)
            pts.append(V[a] + (V[b] - V[a]) * t[:, None])
            keys.append(np.minimum(a, b) * nv + np.maximum(a, b))
            cross.append(above[:, i] != above[:, j])
        cross = np.stack(cross, 1)
        ok = cross.sum(1) == 2
        if not ok.any():
            continue
        cross = cross[ok]
        P = np.stack(pts, 1)[ok]
        K = np.stack(keys, 1)[ok]
        rows = np.arange(len(K))
        first = np.argmax(cross, 1)
        second = 2 - np.argmax(cross[:, ::-1], 1)
        keys_a.append(K[rows, first])
        keys_b.append(K[rows, second])
        pa.append(P[rows, first])
        pb.append(P[rows, second])
        hid.append(np.full(len(K), hi_, dtype=np.int64))
    if not keys_a:
        return []
    return _chain(np.concatenate(keys_a), np.concatenate(keys_b), np.concatenate(pa), np.concatenate(pb),
                  np.concatenate(hid), nv)


def _chain(ka, kb, pa, pb, hid, nv) -> list:
    """Segments sharing an edge key (on the same plane) become polylines: open chains from their free ends first,
    then closed loops."""
    base = np.int64(nv) * np.int64(nv)
    ga = hid * base + ka                         # global keys: plane and mesh edge
    gb = hid * base + kb
    keep = ga != gb
    ga, gb, pa, pb, hid = ga[keep], gb[keep], pa[keep], pb[keep], hid[keep]
    n = len(ga)
    if n == 0:
        return []
    ends = np.concatenate([ga, gb])              # end e of segment s is entry s + e·n
    order_a = np.argsort(ends, kind="stable")
    se = ends[order_a].tolist()
    order = order_a.tolist()
    nbr = [-1] * (2 * n)
    # pair each entry with the next one of the same key (manifold: exactly two); extra copies stay free ends
    m = len(se)
    pos = 0
    while pos < m - 1:
        if se[pos] == se[pos + 1]:
            a, b = order[pos], order[pos + 1]
            nbr[a], nbr[b] = b, a
            pos += 2
            while pos < m and se[pos] == se[pos - 1]:       # a third segment on the same edge: leave it free
                pos += 1
        else:
            pos += 1
    nbr_l = nbr
    seen = [False] * n
    pa_l, pb_l, hid = pa.tolist(), pb.tolist(), hid.tolist()
    out = []

    def walk(s, e):
        """Start at segment s, entering through its end e; returns the end points in order."""
        seq = [pa_l[s] if e == 0 else pb_l[s]]
        cur, ce = s, e
        while True:
            seen[cur] = True
            other = 1 - ce
            seq.append(pb_l[cur] if other == 1 else pa_l[cur])
            nxt = nbr_l[cur + other * n]
            if nxt < 0:
                return seq, False
            ns, ne = nxt % n, nxt // n
            if ns == s:
                return seq, True
            if seen[ns]:
                return seq, False
            cur, ce = ns, ne

    free = [k for k in range(2 * n) if nbr_l[k] < 0]
    for k in free:
        s, e = k % n, k // n
        if not seen[s]:
            seq, closed = walk(s, e)
            if len(seq) >= 2:
                out.append((np.array(seq), False, int(hid[s])))
    for s in range(n):
        if not seen[s]:
            seq, closed = walk(s, 0)
            if len(seq) >= 3:
                out.append((np.array(seq), closed, int(hid[s])))
    out.sort(key=lambda c: c[2])
    return out


# ----------------------------------------------------------------------------- tracing

def resample(P: np.ndarray, step: float) -> np.ndarray:
    """Points no further apart than `step` along the polyline (its own points kept)."""
    seg = np.diff(P, axis=0)
    ln = np.linalg.norm(seg, axis=1)
    n = np.maximum(1, np.ceil(ln / max(step, 1e-12)).astype(int))
    if (n == 1).all():
        return P
    t = np.concatenate([np.arange(k) / k for k in n])
    rep = np.repeat(np.arange(len(seg)), n)
    return np.vstack([P[:-1][rep] + seg[rep] * t[:, None], P[-1:]])


def trace(V, F, cam, contours: list, step: float, eps: float, iters: int = 8, hidden: bool = True,
          caster=None) -> list:
    """Visible / hidden runs of every contour: [(contour index, visible, points (m, 3))]."""
    polys = [resample(P, step) for P, _, _ in contours]
    if not hidden:
        return [(ci, True, P) for ci, P in enumerate(polys)]
    caster = caster or rc.Caster(V, F)
    runs = rc.split_visible(caster, cam, polys, [c for _, c, _ in contours], eps, iters)
    return [(ci, vis, P) for ci, rs in enumerate(runs) for vis, P in rs]


def to_paper(cam, frame, P: np.ndarray) -> list:
    """3D points -> paper mm, split where the line goes behind the near plane; clipped to the frame's drawing
    area. Returns a list of (k, 2) arrays."""
    C = cam.to_camera(P)
    front = np.ones(len(P), dtype=bool) if cam.ortho else C[:, 2] > NEAR
    out = []
    for a, b in _true_runs(front):
        if b - a < 2:
            continue
        S = cam.project(C[a:b])
        out += clip_rect(frame.to_mm(S), frame.area)
    return out


def _true_runs(mask: np.ndarray) -> list:
    idx = np.flatnonzero(np.diff(np.r_[0, mask.astype(np.int8), 0]))
    return list(zip(idx[0::2], idx[1::2]))


def clip_rect(P: np.ndarray, area) -> list:
    """Cut a polyline (k, 2) to the rectangle (x0, y0, w, h): the pieces inside, exact at the border."""
    x0, y0, w, h = area
    x1, y1 = x0 + w, y0 + h
    inside = (P[:, 0] >= x0) & (P[:, 0] <= x1) & (P[:, 1] >= y0) & (P[:, 1] <= y1)
    if inside.all():
        return [P]
    out, cur = [], []
    for i in range(len(P) - 1):
        a, b = P[i], P[i + 1]
        seg = _clip_segment(a, b, x0, y0, x1, y1)
        if seg is None:
            if len(cur) >= 2:
                out.append(np.array(cur))
            cur = []
            continue
        p, q, cut_end = seg
        if not cur or np.hypot(*(np.asarray(cur[-1]) - p)) > 1e-9:
            if len(cur) >= 2:
                out.append(np.array(cur))
            cur = [p]
        cur.append(q)
        if cut_end:
            if len(cur) >= 2:
                out.append(np.array(cur))
            cur = []
    if len(cur) >= 2:
        out.append(np.array(cur))
    return out


def _clip_segment(a, b, x0, y0, x1, y1):
    """Liang-Barsky: the part of a->b inside the box, and whether b itself was cut off."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return None
            t0 = max(t0, r)
        else:
            if r < t0:
                return None
            t1 = min(t1, r)
    if t1 - t0 <= 1e-12:
        return None
    return (np.array([a[0] + t0 * dx, a[1] + t0 * dy]), np.array([a[0] + t1 * dx, a[1] + t1 * dy]), t1 < 1.0)


# ----------------------------------------------------------------------------- plotting

def length(P) -> float:
    P = np.asarray(P, dtype=float)
    return float(np.hypot(*np.diff(P, axis=0).T).sum()) if len(P) > 1 else 0.0


def bridge(runs_mm: list, gap: float) -> list:
    """runs_mm: [(contour, visible, (k, 2) mm)] in contour order. A hidden stretch shorter than `gap` between two
    visible ones is drawn as a straight chord: one stroke instead of two pen lifts. Returns visible strokes
    [(contour, (k, 2))]."""
    out = []
    i, n = 0, len(runs_mm)
    while i < n:
        ci, vis, P = runs_mm[i]
        if not vis:
            i += 1
            continue
        cur = [P]
        j = i + 1
        while j + 1 < n and runs_mm[j][0] == ci and not runs_mm[j][1] and runs_mm[j + 1][0] == ci \
                and runs_mm[j + 1][1] and gap > 0 and length(runs_mm[j][2]) < gap:
            cur.append(runs_mm[j + 1][2])
            j += 2
        out.append((ci, np.vstack(cur)))
        i = j
    return out


def taper(strokes: list, gap: float, min_run: float = 3.0, step: float = 0.15, cancel=None) -> list:
    """Clip each stroke where it runs within `gap` mm of strokes already kept for more than `min_run` mm, so
    contours converging at grazing silhouettes taper off instead of merging into solid ink; brief contacts stay
    (the Blender pieces' _cull_dense). strokes: [(contour, (k, 2))] in drawing priority."""
    if gap <= 0:
        return strokes
    grid: dict = {}
    g2 = gap * gap
    run_n = max(1, int(min_run / step))
    out = []
    for si, (cid, P) in enumerate(strokes):
        if cancel is not None and si % 64 == 0:
            cancel()
        pts = _dense(np.asarray(P, dtype=float), step)
        cx = np.floor(pts[:, 0] / gap).astype(np.int64)
        cy = np.floor(pts[:, 1] / gap).astype(np.int64)
        near = np.zeros(len(pts), dtype=bool)
        if grid:
            for k, (x, y, gx, gy) in enumerate(zip(pts[:, 0].tolist(), pts[:, 1].tolist(), cx.tolist(),
                                                   cy.tolist())):
                hit = False
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        cell = grid.get((gx + dx, gy + dy))
                        if cell is not None and any((qx - x) ** 2 + (qy - y) ** 2 < g2 for qx, qy in cell):
                            hit = True
                            break
                    if hit:
                        break
                near[k] = hit
        cut = np.zeros(len(pts), dtype=bool)
        for a, b in _true_runs(near):
            if b - a >= run_n:
                cut[a:b] = True
        for a, b in _true_runs(~cut):
            if b - a >= 2:
                piece = pts[a:b]
                out.append((cid, piece))
                for (x, y), gx, gy in zip(piece.tolist(), cx[a:b].tolist(), cy[a:b].tolist()):
                    grid.setdefault((gx, gy), []).append((x, y))
    return out


def _dense(P: np.ndarray, step: float) -> np.ndarray:
    if len(P) < 2:
        return P
    return resample(P, step)


def chain(strokes: list, join_same: float, join_any: float, simplify: float, min_len: float,
          optimise: bool = True) -> tuple[list, float, float]:
    """Simplify, order nearest-first and chain strokes whose ends meet (same contour within join_same, any
    within join_any); drop what is shorter than min_len. Returns (polylines [(k, 2)], travel before, after)."""
    if not strokes:
        return [], 0.0, 0.0
    cids = [c for c, _ in strokes]
    pls = simplify_all([[tuple(p) for p in P.tolist()] for _, P in strokes], simplify)
    before = sum(math.dist(a[-1], b[0]) for a, b in zip(pls, pls[1:]))
    order = order_indices(pls) if optimise else [(i, False) for i in range(len(pls))]
    out, last_c = [], None
    for i, rev in order:
        pl = pls[i][::-1] if rev else pls[i]
        if out:
            d = math.dist(out[-1][-1], pl[0])
            if d < join_any or (cids[i] == last_c and d < join_same):
                out[-1] = out[-1] + (pl[1:] if d < 1e-9 else pl)
                last_c = cids[i]
                continue
        out.append(list(pl))
        last_c = cids[i]
    out = [pl for pl in out if len(pl) >= 2 and length(pl) >= min_len]
    after = sum(math.dist(a[-1], b[0]) for a, b in zip(out, out[1:]))
    return out, before, after


def near_misses(polylines: list, pen: float) -> int:
    """Pen-up moves shorter than the pen width (could have been joined): the skill verifier's warning."""
    return sum(1 for a, b in zip(polylines, polylines[1:]) if 1e-9 < math.dist(a[-1], b[0]) < pen)
