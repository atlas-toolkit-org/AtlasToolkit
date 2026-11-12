"""Mesh analysis for line drawings (numpy only): topology, smoothing, normals, curvature and
its derivatives, view-dependent fields and zero-crossing curves on triangle meshes. From the Blender pieces'
sketch pipeline (projects/blender3d_project/augustus/plotter_lib/sketch_mesh.py), unchanged: Sketch Hatching
draws its suggestive contours, ridges, valleys and creases with it, and steers its hatching by its curvature.

Curvature follows Rusinkiewicz, "Estimating Curvatures and Their Derivatives on Triangle
Meshes" (3DPVT 2004), the trimesh2 algorithm vectorised. Suggestive contours follow DeCarlo,
Finkelstein, Rusinkiewicz, Santella, "Suggestive Contours for Conveying Shape" (SIGGRAPH 2003)
as implemented in rtsc. Convex surfaces with outward normals have positive curvature.

Curves are returned as (idx (k, 3) int, bary (k, 3) float, closed): every point is
sum(bary * X[idx]) for any per-vertex array X, so the same curve can be placed on the original
surface while it was found on a smoothed copy with the same topology.
"""
import math

import numpy as np


# ---------------------------------------------------------------- small helpers
def _unit(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n > 0, n, 1.0)


def _dot(a, b):
    return np.einsum("...i,...i->...", a, b)


def _scatter(idx, vals, n):
    """Sum rows of `vals` into `n` bins by `idx` (bincount per column: much faster than add.at)."""
    vals = np.asarray(vals, dtype=np.float64)
    flat = vals.reshape(len(idx), -1)
    out = np.empty((n, flat.shape[1]))
    for c in range(flat.shape[1]):
        out[:, c] = np.bincount(idx, weights=flat[:, c], minlength=n)
    return out.reshape((n,) + vals.shape[1:])


def interpolate(curve, X):
    """Evaluate a per-vertex array X along a curve (idx, bary[, closed])."""
    idx, bary = curve[0], curve[1]
    X = np.asarray(X)
    if X.ndim == 1:
        return (bary * X[idx]).sum(1)
    return (bary[:, :, None] * X[idx]).sum(1)


# ---------------------------------------------------------------- topology
def edge_topology(F):
    """Unique undirected edges E (k, 2); FE (m, 3): edge of each face opposite corner j, i.e.
    (v1, v2), (v2, v0), (v0, v1); EF (k, 2): the (first) two faces of each edge, -1 = boundary;
    counts (k,): faces per edge (> 2 = non-manifold)."""
    F = np.asarray(F, np.int64)
    nv = int(F.max()) + 1
    a, b = F[:, [1, 2, 0]].ravel(), F[:, [2, 0, 1]].ravel()
    key = np.minimum(a, b) * nv + np.maximum(a, b)
    uniq, inv = np.unique(key, return_inverse=True)
    E = np.stack([uniq // nv, uniq % nv], 1)
    FE = inv.reshape(-1, 3)
    order = np.argsort(inv, kind="stable")
    counts = np.bincount(inv, minlength=len(uniq))
    start = np.concatenate([[0], np.cumsum(counts)[:-1]])
    fidx = np.repeat(np.arange(len(F)), 3)[order]
    EF = np.full((len(uniq), 2), -1, np.int64)
    EF[:, 0] = fidx[start]
    two = counts >= 2
    EF[two, 1] = fidx[start[two] + 1]
    return E, FE, EF, counts


def mean_edge_length(V, E):
    return float(np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1).mean())


# ---------------------------------------------------------------- normals, smoothing
def face_normals(V, F):
    """Unit face normals and face areas."""
    c = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    a2 = np.linalg.norm(c, axis=1)
    return c / np.where(a2 > 0, a2, 1.0)[:, None], 0.5 * a2


def vertex_normals(V, F):
    """Unit vertex normals with Max's (1999) corner weights cross(a, b) / (|a|^2 |b|^2): exact for
    points on a sphere, which keeps the curvature estimate accurate on irregular meshes."""
    n = np.zeros_like(V, dtype=np.float64)
    for j in range(3):
        a = V[F[:, (j + 1) % 3]] - V[F[:, j]]
        b = V[F[:, (j + 2) % 3]] - V[F[:, j]]
        w = np.cross(a, b) / np.maximum(_dot(a, a) * _dot(b, b), 1e-300)[:, None]
        n += _scatter(F[:, j], w, len(V))
    return _unit(n)


def neighbour_average(X, E, nv):
    """Uniform average of a per-vertex array over each vertex's edge neighbours."""
    s = _scatter(E[:, 0], X[E[:, 1]], nv) + _scatter(E[:, 1], X[E[:, 0]], nv)
    deg = np.bincount(E[:, 0], minlength=nv) + np.bincount(E[:, 1], minlength=nv)
    deg = np.maximum(deg, 1).astype(np.float64)
    return s / (deg[:, None] if s.ndim == 2 else deg)


def taubin_smooth(V, E, iters, lam=0.5, mu=-0.53):
    """Taubin lambda/mu smoothing: removes small-scale noise and detail without shrinking the
    shape; the topology is unchanged, so fields found on it map back to the original."""
    X = np.array(V, dtype=np.float64)
    for _ in range(int(iters)):
        X += lam * (neighbour_average(X, E, len(X)) - X)
        X += mu * (neighbour_average(X, E, len(X)) - X)
    return X


def smooth_normals(N, E, iters):
    for _ in range(int(iters)):
        N = _unit(0.5 * N + 0.5 * neighbour_average(N, E, len(N)))
    return N


# ---------------------------------------------------------------- curvature
def corner_areas(V, F):
    """Voronoi (Meyer et al.) area of each face corner (m, 3) and per vertex (n,)."""
    e0, e1, e2 = V[F[:, 2]] - V[F[:, 1]], V[F[:, 0]] - V[F[:, 2]], V[F[:, 1]] - V[F[:, 0]]
    area = 0.5 * np.linalg.norm(np.cross(e0, e1), axis=1)
    l2 = np.stack([_dot(e0, e0), _dot(e1, e1), _dot(e2, e2)], 1)
    bcw = np.stack([l2[:, 0] * (l2[:, 1] + l2[:, 2] - l2[:, 0]),
                    l2[:, 1] * (l2[:, 2] + l2[:, 0] - l2[:, 1]),
                    l2[:, 2] * (l2[:, 0] + l2[:, 1] - l2[:, 2])], 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = 0.5 * area / bcw.sum(1)
        ca = np.stack([scale * (bcw[:, 1] + bcw[:, 2]), scale * (bcw[:, 2] + bcw[:, 0]),
                       scale * (bcw[:, 0] + bcw[:, 1])], 1)
        o0 = bcw[:, 0] <= 0
        o1 = ~o0 & (bcw[:, 1] <= 0)
        o2 = ~o0 & ~o1 & (bcw[:, 2] <= 0)
        a1, a2 = -0.25 * l2[:, 2] * area / _dot(e0, e2), -0.25 * l2[:, 1] * area / _dot(e0, e1)
        ca[o0] = np.stack([area - a1 - a2, a1, a2], 1)[o0]
        b2, b0 = -0.25 * l2[:, 0] * area / _dot(e1, e0), -0.25 * l2[:, 2] * area / _dot(e1, e2)
        ca[o1] = np.stack([b0, area - b2 - b0, b2], 1)[o1]
        c0, c1 = -0.25 * l2[:, 1] * area / _dot(e2, e1), -0.25 * l2[:, 0] * area / _dot(e2, e0)
        ca[o2] = np.stack([c0, c1, area - c0 - c1], 1)[o2]
    ca = np.nan_to_num(ca, nan=0.0, posinf=0.0, neginf=0.0)
    pa = sum(np.bincount(F[:, k], weights=ca[:, k], minlength=len(V)) for k in range(3))
    return ca, pa


def _rot_frame(u, v, new_n):
    """Rotate frames (u, v) (rows, unit, perpendicular) so their normal u x v becomes new_n."""
    old_n = np.cross(u, v)
    nd = _dot(old_n, new_n)
    perp = new_n - nd[:, None] * old_n
    dperp = (old_n + new_n) / np.maximum(1.0 + nd, 1e-12)[:, None]
    ru = u - dperp * _dot(u, perp)[:, None]
    rv = v - dperp * _dot(v, perp)[:, None]
    flip = nd <= -1.0 + 1e-9
    ru[flip], rv[flip] = -u[flip], -v[flip]
    return ru, rv


def _proj_curv(ou, ov, ku, kuv, kv, nu, nv):
    """Curvature tensors given in frames (ou, ov), re-expressed in frames (nu, nv)."""
    ru, rv = _rot_frame(nu, nv, np.cross(ou, ov))
    u1, v1, u2, v2 = _dot(ru, ou), _dot(ru, ov), _dot(rv, ou), _dot(rv, ov)
    return (ku * u1 * u1 + kuv * 2 * u1 * v1 + kv * v1 * v1,
            ku * u1 * u2 + kuv * (u1 * v2 + u2 * v1) + kv * v1 * v2,
            ku * u2 * u2 + kuv * 2 * u2 * v2 + kv * v2 * v2)


def _proj_dcurv(ou, ov, dc, nu, nv):
    """Curvature-derivative tensors (4 coefficients) from frames (ou, ov) to (nu, nv)."""
    ru, rv = _rot_frame(nu, nv, np.cross(ou, ov))
    u1, v1, u2, v2 = _dot(ru, ou), _dot(ru, ov), _dot(rv, ou), _dot(rv, ov)
    a, b, c, d = dc
    return (a * u1 ** 3 + b * 3 * u1 * u1 * v1 + c * 3 * u1 * v1 * v1 + d * v1 ** 3,
            a * u1 * u1 * u2 + b * (u1 * u1 * v2 + 2 * u2 * u1 * v1) + c * (u2 * v1 * v1 + 2 * u1 * v1 * v2)
            + d * v1 * v1 * v2,
            a * u1 * u2 * u2 + b * (u2 * u2 * v1 + 2 * u1 * u2 * v2) + c * (u1 * v2 * v2 + 2 * u2 * v2 * v1)
            + d * v1 * v2 * v2,
            a * u2 ** 3 + b * 3 * u2 * u2 * v2 + c * 3 * u2 * v2 * v2 + d * v2 ** 3)


def _face_frames(V, F):
    e = [V[F[:, 2]] - V[F[:, 1]], V[F[:, 0]] - V[F[:, 2]], V[F[:, 1]] - V[F[:, 0]]]
    t = _unit(e[0])
    b = _unit(np.cross(np.cross(e[0], e[1]), t))
    return e, t, b


def _solve(W, m):
    """Batched least-squares solve, skipping singular systems (degenerate faces)."""
    out = np.zeros(m.shape)
    ok = np.abs(np.linalg.det(W)) > 1e-30
    if ok.any():
        out[ok] = np.linalg.solve(W[ok], m[ok][..., None])[..., 0]
    return out


def curvature(V, F, N):
    """Principal curvatures and directions per vertex. Returns dict with k1, k2 (|k1| >= |k2|),
    d1, d2 (unit, tangent, d1 x d2 = N) and the corner/point areas (reused by curvature_derivs)."""
    nv, m = len(V), len(F)
    p1 = np.zeros_like(V)
    for j in range(3):                      # any incident edge, made tangent below
        p1[F[:, j]] = V[F[:, (j + 1) % 3]] - V[F[:, j]]
    p1 = _unit(np.cross(p1, N))
    p2 = np.cross(N, p1)
    e, t, b = _face_frames(V, F)
    w00 = w01 = w22 = m0 = m1 = m2 = 0.0
    for j in range(3):
        u, v = _dot(e[j], t), _dot(e[j], b)
        w00, w01, w22 = w00 + u * u, w01 + u * v, w22 + v * v
        dn = N[F[:, (j + 2) % 3]] - N[F[:, (j + 1) % 3]]
        dnu, dnv = _dot(dn, t), _dot(dn, b)
        m0, m1, m2 = m0 + dnu * u, m1 + dnu * v + dnv * u, m2 + dnv * v
    W = np.zeros((m, 3, 3))
    W[:, 0, 0], W[:, 1, 1], W[:, 2, 2] = w00, w00 + w22, w22
    W[:, 0, 1] = W[:, 1, 0] = W[:, 1, 2] = W[:, 2, 1] = w01
    fe, ff, fg = _solve(W, np.stack([m0, m1, m2], 1)).T
    ca, pa = corner_areas(V, F)
    c1, c12, c2 = np.zeros(nv), np.zeros(nv), np.zeros(nv)
    for j in range(3):
        vj = F[:, j]
        k1, k12, k2 = _proj_curv(t, b, fe, ff, fg, p1[vj], p2[vj])
        wt = ca[:, j] / np.where(pa[vj] > 0, pa[vj], 1.0)
        c1 += np.bincount(vj, weights=wt * k1, minlength=nv)
        c12 += np.bincount(vj, weights=wt * k12, minlength=nv)
        c2 += np.bincount(vj, weights=wt * k2, minlength=nv)
    ru, rv = _rot_frame(p1, p2, N)
    with np.errstate(divide="ignore", invalid="ignore"):
        h = np.where(c12 != 0, 0.5 * (c2 - c1) / np.where(c12 != 0, c12, 1.0), 0.0)
        tt = np.where(h < 0, 1.0 / (h - np.sqrt(1 + h * h)), 1.0 / (h + np.sqrt(1 + h * h)))
    tt = np.where(c12 != 0, tt, 0.0)
    c = 1.0 / np.sqrt(1 + tt * tt)
    s = tt * c
    k1, k2 = c1 - tt * c12, c2 + tt * c12
    d1 = c[:, None] * ru - s[:, None] * rv
    swap = np.abs(k1) < np.abs(k2)
    k1, k2 = np.where(swap, k2, k1), np.where(swap, k1, k2)
    d1[swap] = (s[:, None] * ru + c[:, None] * rv)[swap]
    d1 = _unit(d1)
    d2 = np.cross(N, d1)
    return {"k1": k1, "k2": k2, "d1": d1, "d2": d2, "corner_areas": ca, "point_areas": pa}


def curvature_derivs(V, F, N, curv):
    """Per-vertex curvature derivative tensor (4 coefficients, in the (d1, d2) frame)."""
    nv, m = len(V), len(F)
    k1, k2, d1, d2 = curv["k1"], curv["k2"], curv["d1"], curv["d2"]
    e, t, b = _face_frames(V, F)
    fc = [_proj_curv(d1[F[:, j]], d2[F[:, j]], k1[F[:, j]], 0.0, k2[F[:, j]], t, b) for j in range(3)]
    w00 = w01 = w33 = 0.0
    m0 = m1 = m2 = m3 = 0.0
    for j in range(3):
        prv, nxt = fc[(j + 2) % 3], fc[(j + 1) % 3]
        df0, df1, df2 = prv[0] - nxt[0], prv[1] - nxt[1], prv[2] - nxt[2]
        u, v = _dot(e[j], t), _dot(e[j], b)
        w00, w01, w33 = w00 + u * u, w01 + u * v, w33 + v * v
        m0 = m0 + u * df0
        m1 = m1 + v * df0 + 2 * u * df1
        m2 = m2 + 2 * v * df1 + u * df2
        m3 = m3 + v * df2
    W = np.zeros((m, 4, 4))
    W[:, 0, 0], W[:, 3, 3] = w00, w33
    W[:, 1, 1], W[:, 2, 2] = 2 * w00 + w33, w00 + 2 * w33
    W[:, 0, 1] = W[:, 1, 0] = w01
    W[:, 1, 2] = W[:, 2, 1] = 2 * w01
    W[:, 2, 3] = W[:, 3, 2] = w01
    fdc = _solve(W, np.stack([m0, m1, m2, m3], 1)).T
    ca, pa = curv["corner_areas"], curv["point_areas"]
    dc = np.zeros((nv, 4))
    for j in range(3):
        vj = F[:, j]
        vdc = _proj_dcurv(t, b, fdc, d1[vj], d2[vj])
        wt = ca[:, j] / np.where(pa[vj] > 0, pa[vj], 1.0)
        for c in range(4):
            dc[:, c] += np.bincount(vj, weights=wt * vdc[c], minlength=nv)
    return dc


def view_fields(P, N, curv, dcurv, cam):
    """Per-vertex view-dependent fields for a camera at `cam` (perspective):
    ndotv; kr = radial curvature * sin^2(theta); sc_num / sc_den = DwKr test of suggestive
    contours (rtsc's form: zero crossings of kr with sc_num/sc_den > threshold are drawn)."""
    vd = _unit(np.asarray(cam, float)[None, :] - P)
    ndotv = _dot(vd, N)
    u, v = _dot(vd, curv["d1"]), _dot(vd, curv["d2"])
    u2, v2 = u * u, v * v
    k1, k2 = curv["k1"], curv["k2"]
    kr = k1 * u2 + k2 * v2
    a, b, c, d = dcurv.T
    num = u2 * (u * a + 3 * v * b) + v2 * (3 * u * c + v * d)
    csc2 = 1.0 / np.maximum(u2 + v2, 1e-12)
    num = num * csc2
    tr = (k2 - k1) * u * v * csc2
    num = num - 2 * ndotv * tr * tr
    return {"ndotv": ndotv, "kr": kr, "sc_num": num, "sc_den": ndotv}


# ---------------------------------------------------------------- curves
def _chain(n_seg, k1, k2):
    """Chain segments (key k1[i] -- key k2[i]) into maximal paths. Every segment is used once;
    paths end at keys that do not have exactly two segments (ends, branch points), and what
    remains are closed loops. Returns [(keys, closed)] (closed loops do not repeat the start)."""
    adj = {}
    for i in range(n_seg):
        a, b = k1[i], k2[i]
        if a == b:
            continue
        adj.setdefault(a, []).append((b, i))
        adj.setdefault(b, []).append((a, i))
    used = np.zeros(n_seg, bool)

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

    chains = []
    for key, nbs in adj.items():
        if len(nbs) != 2:
            for nb, seg in nbs:
                if not used[seg]:
                    chains.append((walk(key, nb, seg), False))
    for key, nbs in adj.items():
        for nb, seg in nbs:
            if not used[seg]:
                ch = walk(key, nb, seg)
                closed = len(ch) > 3 and ch[-1] == ch[0]
                chains.append((ch[:-1] if closed else ch, closed))
    return chains


def zero_crossings(F, val, keep=None, face_mask=None, min_points=2):
    """Zero-crossing curves of the per-vertex scalar `val` (marching triangles), chained across
    faces. Optional per-vertex `keep`: only the parts where the interpolated keep >= 0 survive
    (segments are clipped at its linear zero crossing). face_mask (m,) bool limits the faces.
    Returns [(idx (k, 3), bary (k, 3), closed)]."""
    F = np.asarray(F, np.int64)
    nv = int(F.max()) + 1
    val = np.where(val == 0, 1e-30, val)
    s = val[F] > 0
    cand = s.any(1) & ~s.all(1)
    if face_mask is not None:
        cand &= face_mask
    fi = np.nonzero(cand)[0]
    if not len(fi):
        return []
    tri, sv, tv = F[fi], s[fi], val[F[fi]]
    cr = np.stack([sv[:, j] != sv[:, (j + 1) % 3] for j in range(3)], 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        T = np.stack([tv[:, j] / (tv[:, j] - tv[:, (j + 1) % 3]) for j in range(3)], 1)
    rows = np.arange(len(fi))
    first = np.argmax(cr, 1)
    second = 2 - np.argmax(cr[:, ::-1], 1)

    def ends(j):
        a, b = j, (j + 1) % 3
        va, vb = tri[rows, a], tri[rows, b]
        t = T[rows, j]
        bary = np.zeros((len(fi), 3))
        bary[rows, a] = 1 - t
        bary[rows, b] = t
        return np.minimum(va, vb) * nv + np.maximum(va, vb), bary

    key1, bar1 = ends(first)
    key2, bar2 = ends(second)
    seg_rows = rows
    if keep is not None:
        kp = keep[tri]
        q1, q2 = (bar1 * kp).sum(1), (bar2 * kp).sum(1)
        good = (q1 >= 0) | (q2 >= 0)
        clip = good & ((q1 >= 0) != (q2 >= 0))
        with np.errstate(divide="ignore", invalid="ignore"):
            f = np.where(clip, q1 / (q1 - q2), 0.0)
        newb = bar1 + f[:, None] * (bar2 - bar1)
        newk = -(rows + 1)                       # clipped ends are unique chain ends
        c1 = clip & (q1 < 0)                     # first end is dropped
        c2 = clip & (q2 < 0)
        key1, key2 = np.where(c1, newk, key1), np.where(c2, newk, key2)
        bar1[c1], bar2[c2] = newb[c1], newb[c2]
        sel = np.nonzero(good)[0]
        key1, key2, bar1, bar2, seg_rows = key1[sel], key2[sel], bar1[sel], bar2[sel], rows[sel]
    point = {}
    for i, (a, b) in enumerate(zip(key1.tolist(), key2.tolist())):
        if a not in point:
            point[a] = (seg_rows[i], bar1[i])
        if b not in point:
            point[b] = (seg_rows[i], bar2[i])
    curves = []
    for chain, closed in _chain(len(key1), key1.tolist(), key2.tolist()):
        if len(chain) < min_points:
            continue
        idx = np.array([tri[point[k][0]] for k in chain])
        bary = np.array([point[k][1] for k in chain])
        if closed:
            idx, bary = np.vstack([idx, idx[:1]]), np.vstack([bary, bary[:1]])
        curves.append((idx, bary, closed))
    return curves


def edge_crossings(F, FE, E, cross, t):
    """Curves through per-edge crossing points: cross (k,) bool marks edges carrying a point at
    parameter t (k,) from E[:,0] to E[:,1]. A face with two crossing edges gets a segment
    between them; with three, each point connects to the face centre. Chained across faces
    like zero_crossings. Returns [(idx (n, 3), bary (n, 3), closed)]."""
    fc = cross[FE]
    n = fc.sum(1)
    k1, k2 = [], []
    two = np.flatnonzero(n == 2)
    if len(two):
        ce = FE[two][fc[two]].reshape(-1, 2)
        k1 += ce[:, 0].tolist()
        k2 += ce[:, 1].tolist()
    three = np.flatnonzero(n == 3)
    for f in three.tolist():
        for e in FE[f].tolist():
            k1.append(e)
            k2.append(-(f + 1))
    curves = []
    for chain, closed in _chain(len(k1), k1, k2):
        idx, bary = [], []
        for k in chain:
            if k >= 0:
                a, b = E[k]
                idx.append((a, b, b))
                bary.append((1 - t[k], t[k], 0.0))
            else:
                idx.append(tuple(F[-k - 1]))
                bary.append((1 / 3, 1 / 3, 1 / 3))
        idx, bary = np.array(idx, np.int64), np.array(bary)
        if closed:
            idx, bary = np.vstack([idx, idx[:1]]), np.vstack([bary, bary[:1]])
        curves.append((idx, bary, closed))
    return curves


def ridges_valleys(V, F, E, FE, curv, dcurv, valleys=True, threshold=40.0, vertex_mask=None, threshold_scale=None):
    """Ridge (convex crest) or valley (concave groove) lines: where the principal curvature of
    largest magnitude k1 is extremal along its own direction (zero crossing of dk1/dd1, with
    the right flavour of extremum), k1 has the right sign and |k1| > threshold (1/units).
    Ohtake, Belyaev, Seidel 2004 / rtsc. threshold_scale (n,) optionally scales the threshold
    per vertex (e.g. lower where the drawing should be more detailed)."""
    k1, d1 = curv["k1"], curv["d1"]
    e = dcurv[:, 0]
    sgn = -1.0 if valleys else 1.0
    a, b = E[:, 0], E[:, 1]
    ok = (sgn * k1[a] > 0) & (sgn * k1[b] > 0)
    if vertex_mask is not None:
        ok &= vertex_mask[a] & vertex_mask[b]
    align = np.sign(np.einsum("ij,ij->i", d1[a], d1[b]))
    align[align == 0] = 1.0
    ea, eb = e[a], e[b] * align
    cross = ok & (ea * eb <= 0)
    # vectors toward increasing |k1| at each end; the extremum must be a maximum of |k1|
    ta, tb = (sgn * e[a])[:, None] * d1[a], (sgn * e[b])[:, None] * d1[b]
    dv = V[b] - V[a]
    cross &= (np.einsum("ij,ij->i", ta, dv) >= 0) | (np.einsum("ij,ij->i", tb, dv) <= 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.abs(ea) / (np.abs(ea) + np.abs(eb))
    t = np.nan_to_num(t, nan=0.5)
    kk = np.abs((1 - t) * k1[a] + t * k1[b])
    thr = threshold if threshold_scale is None else threshold * ((1 - t) * threshold_scale[a] + t * threshold_scale[b])
    cross &= kk > thr
    return edge_crossings(F, FE, E, cross, t)


def feature_edges(V, F, E, EF, counts, angle_deg):
    """Mesh edges whose dihedral angle exceeds angle_deg (creases) and boundary edges."""
    fn, _ = face_normals(V, F)
    inner = EF[:, 1] >= 0
    other = np.where(inner, EF[:, 1], EF[:, 0])
    cosang = _dot(fn[EF[:, 0]], fn[other])
    crease = inner & (cosang < math.cos(math.radians(angle_deg)))
    return E[crease], E[counts == 1]


def edge_curves(E, min_points=2):
    """Chain mesh edges (k, 2) into vertex polylines, as curves (idx, bary, closed)."""
    if not len(E):
        return []
    out = []
    for chain, closed in _chain(len(E), E[:, 0].tolist(), E[:, 1].tolist()):
        if len(chain) < min_points:
            continue
        v = np.array(chain + chain[:1] if closed else chain)
        idx = np.stack([v, v, v], 1)
        bary = np.zeros((len(v), 3))
        bary[:, 0] = 1.0
        out.append((idx, bary, closed))
    return out
