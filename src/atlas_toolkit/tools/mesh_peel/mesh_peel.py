"""
mesh_peel.py - Surface Peel / Bloom: cut a model's skin into panels and bend each one away from the body about a
root that stays attached, so a head opens like a flower or sheds curled plates.

All work is in the canonical frame (geom/frame3d: up = Z, front = -Y, height 2). The pieces:

1. Panels (`segment`). Petals: sectors about a bloom axis through a centre, in rows by the angle from the axis' end
   (its pole), rows staggered by half a petal, sector edges wobbled and row edges scalloped into petal tips.
   Scales: Mesh Shatter's seeded geodesic patches (nearest seed along the surface, creases resist). Either way every
   panel is split into connected pieces, slivers are merged into a neighbour, and the stair-stepped seams along
   triangle edges are smoothed into curves (`smooth_seams`).
2. Roots (`panel_frames`). The root is the panel's edge nearest the pole (Peel: the free end lifts away from the
   pole) or farthest from it (Bloom: the tips, which meet at the pole, fall open). Every vertex gets its distance
   along the surface from the root; a spine of frames (tangent T away from the root, outward normal N, lateral B)
   is taken from the bins of that distance.
3. Bend (`bend`). The spine is rebuilt with each step's own turn scaled down by Unroll (the panel straightens out
   of the body's curvature) plus an extra outward turn about B: Open (a hinge turn in the root's transition zone)
   and Curl (spread along the panel, tightening toward the tip). Each vertex rides the two spine frames around it
   (blended rigid moves), so the panel bends smoothly and nothing within the root zone moves at all.
4. Shell (`solidify`): the bent panel thickened inward along its normals, rims closing every open edge.

Randomness (patches, per-panel variation, random order) is seeded and drawn at rest, so the stages of a sequence
are one motion. Outputs <name>_peel.stl, signed atlas-toolkit:mesh-peel.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

AXES = {"+z": (0, 0, 1), "-z": (0, 0, -1), "-y": (0, -1, 0), "+y": (0, 1, 0), "+x": (1, 0, 0), "-x": (-1, 0, 0)}


# ----------------------------------------------------------------------------- small helpers

def smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def ease(a):
    """Panel opening from its share of the amount: smooth in 0..1, linear past 1 (amount > 1 overshoots)."""
    a = np.asarray(a, dtype=np.float64)
    return np.where(a < 1.0, smoothstep(a), a)


def unit(v, fallback=None):
    v = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    if fallback is None:
        return v / np.maximum(n, 1e-300)
    return np.where(n > 1e-12, v / np.maximum(n, 1e-300), fallback)


def axis_basis(a):
    """Two unit vectors across axis a: e1 points to the model's front (-Y) for vertical axes and up otherwise, so a
    petal (not a seam) faces the viewer."""
    a = unit(np.asarray(a, dtype=np.float64))
    ref = np.array([0.0, -1.0, 0.0]) if abs(a[2]) > 0.9 else np.array([0.0, 0.0, 1.0])
    e1 = unit(ref - a * (ref @ a))
    return e1, np.cross(a, e1)


def polar(P, centre, a):
    """(polar angle from the pole, azimuth) of points about axis a through centre."""
    q = np.asarray(P, dtype=np.float64) - centre
    e1, e2 = axis_basis(a)
    z = q @ unit(a)
    x, y = q @ e1, q @ e2
    return np.arctan2(np.hypot(x, y), z), np.arctan2(y, x)


def vertex_normals(V, F):
    """Area-weighted vertex normals (zero where no face touches)."""
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    N = np.zeros_like(V)
    for k in range(3):
        np.add.at(N, F[:, k], fn)
    return unit(N, np.zeros(3))


def face_adjacency(F):
    """Pairs of faces sharing an edge, and that edge's two vertices (manifold edges only)."""
    E = np.sort(F[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    fid = np.repeat(np.arange(len(F)), 3)
    order = np.lexsort((E[:, 1], E[:, 0]))
    E, fid = E[order], fid[order]
    same = np.all(E[1:] == E[:-1], axis=1)
    i = np.flatnonzero(same)
    # an edge in three or more faces gives several pairs; that is fine for labels
    return np.column_stack([fid[i], fid[i + 1]]), E[i]


# ----------------------------------------------------------------------------- panels

def petal_labels(C, centre, a, petals: int, rows: int, stagger: bool, wobble: float, scallop: float,
                 tips_to_pole: bool, offset: float, seed: int):
    """Label per face centroid C: sector about the axis, row by angle from the pole."""
    rng = np.random.default_rng(int(seed))
    theta, phi = polar(C, centre, a)
    n = max(1, int(petals))
    rows = max(1, int(rows))
    sector = 2.0 * np.pi / n
    lo, hi = float(theta.min()), float(theta.max())
    t = (theta - lo) / max(hi - lo, 1e-9)
    ps = rng.uniform(0, 2 * np.pi, 4)
    if wobble > 0 and n > 1:              # wavy sector edges: smooth and periodic in phi
        phi = phi + wobble * 0.35 * sector * (np.sin(3.0 * np.pi * t + phi + ps[0])
                                              + 0.6 * np.sin(5.0 * np.pi * t + 2.0 * phi + ps[1])) / 1.6
    phase = [offset - sector / 2.0 + (r * sector / 2.0 if stagger else 0.0) for r in range(rows + 1)]

    def centre_depth(r):                  # 1 at the middle of row r's petals, 0 on their edges
        return 0.5 * (1.0 - np.cos(n * (phi - phase[r]))) if n > 1 else np.zeros_like(phi)

    amp = scallop * 0.8 / rows
    row = np.zeros(len(C), dtype=np.int64)
    for b in range(1, rows):
        if tips_to_pole:                  # petal tips (row b's middles) reach toward the pole
            edge = b / rows - amp * centre_depth(b)
        else:                             # tips reach away from it (row b-1's middles)
            edge = b / rows + amp * centre_depth(b - 1)
        row += t > edge
    ph = np.array(phase)[row]
    sec = np.floor(np.mod(phi - ph, 2 * np.pi) / sector).astype(np.int64) % n
    return row * n + sec


def scale_labels(V, F, pieces: int, sensitivity: float, seed: int):
    """Mesh Shatter's geodesic patches: farthest seeds, nearest seed along the face graph, creases resist."""
    import trimesh
    from atlas_toolkit.tools.mesh_shatter import mesh_shatter as ms
    m = trimesh.Trimesh(V, F, process=False)
    rng = np.random.default_rng(int(seed))
    graph = ms.adjacency_graph(m, sensitivity)
    k = int(max(1, min(pieces, len(F))))
    seeds = ms.farthest_point_sampling(m.triangles_center, k, rng)
    return ms.labels_from_seeds(graph, seeds)


def majority(labels, pairs, passes: int):
    """A face whose edge neighbours mostly (2 or 3 of them) carry another label takes it: the teeth that growing
    panels over triangles leaves along their borders are filed off."""
    labels = labels.copy()
    n = len(labels)
    x = np.concatenate([pairs[:, 0], pairs[:, 1]])
    y = np.concatenate([pairs[:, 1], pairs[:, 0]])
    o = np.argsort(x, kind="stable")
    x, y = x[o], y[o]
    fill = np.bincount(x, minlength=n)
    rank = np.arange(len(x)) - np.repeat(np.cumsum(fill) - fill, fill)
    keep = rank < 3
    nb = np.full((n, 3), -1, dtype=np.int64)
    nb[x[keep], rank[keep]] = y[keep]
    full = fill == 3
    idx = np.flatnonzero(full)
    for _ in range(int(passes)):
        L = labels[nb[idx]]
        same01 = L[:, 0] == L[:, 1]
        same02 = L[:, 0] == L[:, 2]
        same12 = L[:, 1] == L[:, 2]
        cand = np.where(same01 | same02, L[:, 0], np.where(same12, L[:, 1], labels[idx]))
        change = cand != labels[idx]
        if not change.any():
            break
        labels[idx[change]] = cand[change]
    return labels


def split_components(F, labels, pairs):
    """Relabel so every label is one edge-connected piece."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    keep = labels[pairs[:, 0]] == labels[pairs[:, 1]]
    p = pairs[keep]
    n = len(F)
    g = coo_matrix((np.ones(len(p)), (p[:, 0], p[:, 1])), shape=(n, n))
    _, comp = connected_components(g, directed=False)
    _, out = np.unique(comp, return_inverse=True)
    return out


def merge_small(labels, pairs, areas, min_share: float, wanted: int):
    """Merge panels smaller than min_share of the mean panel (by area) into the neighbour they share most edges
    with, smallest first. Panels with no neighbour (separate parts) stay."""
    labels = labels.copy()
    for _ in range(64):
        uniq, inv = np.unique(labels, return_inverse=True)
        area = np.bincount(inv, weights=areas)
        target = max(area.sum() / max(wanted, 1), 1e-300) * min_share
        small = np.flatnonzero(area < target)
        if not len(small):
            break
        la, lb = inv[pairs[:, 0]], inv[pairs[:, 1]]
        cross = la != lb
        la, lb = la[cross], lb[cross]
        moved = False
        done = set()
        for s in small[np.argsort(area[small])]:
            if s in done:
                continue
            nb = np.concatenate([lb[la == s], la[lb == s]])
            if not len(nb):
                continue
            vals, cnt = np.unique(nb, return_counts=True)
            to = vals[np.argmax(cnt)]
            labels[inv == s] = uniq[to]
            done.update((s, to))
            moved = True
        if not moved:
            break
    _, out = np.unique(labels, return_inverse=True)
    return out


def smooth_seams(V, F, labels, passes: int, edges=None):
    """Seams follow triangle edges and so zig-zag; relax the seam vertices along their seam (each moves halfway to the
    mean of its two seam neighbours, junctions and open borders fixed). Returns new vertices."""
    if passes <= 0:
        return V
    pairs, E = edges if edges is not None else face_adjacency(F)
    seam = E[labels[pairs[:, 0]] != labels[pairs[:, 1]]]
    if not len(seam):
        return V
    seam = np.unique(seam, axis=0)
    deg = np.bincount(seam.ravel(), minlength=len(V))
    # vertices on the model's own open border stay
    Es = np.sort(F[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    u, c = np.unique(Es, axis=0, return_counts=True)
    border = np.zeros(len(V), dtype=bool)
    border[u[c == 1].ravel()] = True
    free = (deg == 2) & ~border
    a, b = seam[:, 0], seam[:, 1]
    # only the faces around seam vertices can turn over: check those against their rest normals
    # the vertices beside a seam relax too (umbrella step), so the triangles make room for the seam's new course
    ring = np.zeros(len(V), dtype=bool)
    ring[F[free[F].any(axis=1)].ravel()] = True
    ring &= (deg == 0) & ~border
    Fn = F[(free | ring)[F].any(axis=1)]

    def normals(X):
        return np.cross(X[Fn[:, 1]] - X[Fn[:, 0]], X[Fn[:, 2]] - X[Fn[:, 0]])

    rest = normals(V)
    rest_len = np.linalg.norm(rest, axis=1)
    Eall = np.unique(np.sort(F[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1), axis=0)
    ea, eb = Eall[:, 0], Eall[:, 1]
    vdeg = np.bincount(Eall.ravel(), minlength=len(V)).astype(np.float64)
    V = V.copy()
    for _ in range(int(passes)):
        acc = np.zeros_like(V)
        np.add.at(acc, a, V[b])
        np.add.at(acc, b, V[a])
        mean = acc / np.maximum(deg, 1)[:, None]
        new = V.copy()
        new[free] += 0.5 * (mean[free] - V[free])
        acc = np.zeros_like(V)
        np.add.at(acc, ea, new[eb])
        np.add.at(acc, eb, new[ea])
        um = acc / np.maximum(vdeg, 1)[:, None]
        new[ring] += 0.5 * (um[ring] - new[ring])
        for _ in range(4):                 # a move that would fold a face over (or flatten it) is taken back
            n = normals(new)
            ok = (n * rest).sum(1) > 0.25 * rest_len * np.linalg.norm(n, axis=1)
            ok &= np.linalg.norm(n, axis=1) > 0.05 * rest_len
            if ok.all():
                break
            back = np.unique(Fn[~ok])
            new[back] = V[back]
        V = new
    return V


@dataclass
class Panels:
    labels: np.ndarray                 # per face, 0..n-1
    count: int
    V: np.ndarray                      # vertices after seam smoothing


def segment(V, F, p, centre, a, offset: float = 0.0, seed_shift: int = 0, progress=None) -> Panels:
    """Panels for settings p (pattern, petals, rows, ...)."""
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    C = V[F].mean(axis=1)
    seed = int(p["seed"]) + seed_shift
    if p["pattern"] == "petals":
        labels = petal_labels(C, centre, a, p["petals"], p["rows"], p["stagger_rows"], p["wobble"], p["scallop"],
                              p["root"] == "bloom", offset, seed)
        wanted = int(p["petals"]) * int(p["rows"])
    else:
        labels = scale_labels(V, F, int(p["pieces"]), float(p["edge_sensitivity"]), seed)
        wanted = int(p["pieces"])
    if progress:
        progress("splitting panels")
    pairs, E = face_adjacency(F)
    labels = majority(labels, pairs, 3)
    labels = split_components(F, labels, pairs)
    areas = 0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)
    labels = merge_small(labels, pairs, areas, 0.12, wanted)
    Vs = smooth_seams(V, F, labels, int(p["seam_smooth"]), (pairs, E))
    return Panels(labels, int(labels.max()) + 1 if len(labels) else 0, Vs)


# ----------------------------------------------------------------------------- one panel's frames and bend

@dataclass
class Panel:
    verts: np.ndarray                  # indices into the layer's vertices
    faces: np.ndarray                  # local faces
    P: np.ndarray                      # rest positions
    s: np.ndarray                      # distance from the root along the surface
    length: float
    S: np.ndarray                      # (K, 3) spine points at bin centres
    R: np.ndarray                      # (K, 3, 3) frames, columns T N B
    ds: float
    normal: np.ndarray                 # mean outward normal
    pole_t: float = 0.0                # 0 at the pole .. 1 far side (activation order)
    weight: float = 1.0                # influence (mean over the panel at rest)
    jitter: dict = field(default_factory=dict)


def _root_distance(P, faces, sources):
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra
    E = np.unique(np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1), axis=0)
    w = np.maximum(np.linalg.norm(P[E[:, 0]] - P[E[:, 1]], axis=1), 1e-12)
    n = len(P)
    g = coo_matrix((np.concatenate([w, w]), (np.concatenate([E[:, 0], E[:, 1]]),
                                            np.concatenate([E[:, 1], E[:, 0]]))), shape=(n, n)).tocsr()
    d = dijkstra(g, directed=False, indices=sources, min_only=True)
    bad = ~np.isfinite(d)
    if bad.any():                       # cannot happen for one connected piece; keep it safe
        d[bad] = np.linalg.norm(P[bad][:, None] - P[sources][None], axis=2).min(1)
    return d


def panel_frames(P, faces, Nv, theta, root: str, max_bins: int = 48) -> Panel | None:
    """Root, distances and spine frames of one panel (P its rest vertices, Nv their outward normals, theta their
    angles from the pole)."""
    if len(faces) == 0 or len(P) < 3:
        return None
    h = -theta if root == "peel" else theta            # higher h: nearer the root
    span = float(h.max() - h.min())
    sources = np.flatnonzero(h >= h.max() - max(0.08 * span, 1e-9))
    s = _root_distance(P, faces, sources)
    L = float(s.max())
    if L <= 1e-9:
        return None
    K = int(np.clip(np.sqrt(len(P)) * 2.0, 6, max_bins))
    ds = L / K
    b = np.minimum((s / ds).astype(np.int64), K - 1)
    cnt = np.bincount(b, minlength=K).astype(np.float64)
    S = np.stack([np.bincount(b, weights=P[:, k], minlength=K) for k in range(3)], 1)
    Nb = np.stack([np.bincount(b, weights=Nv[:, k], minlength=K) for k in range(3)], 1)
    full = cnt > 0
    centres = (np.arange(K) + 0.5) * ds
    S[full] /= cnt[full, None]
    if not full.all():
        for k in range(3):
            S[~full, k] = np.interp(centres[~full], centres[full], S[full, k])
            Nb[~full, k] = np.interp(centres[~full], centres[full], Nb[full, k])
    # the bins' means follow every bump of the surface (a nose, an ear); the spine and its frames must be smooth
    # or their noise would be read as bending (and unrolled)
    sigma = max(1.5, K / 10.0)
    S = _smooth_bins(S, sigma)
    Nb = _smooth_bins(Nb, sigma)
    mean_n = unit(Nv.sum(0)) if np.linalg.norm(Nv.sum(0)) > 1e-12 else np.array([0.0, 0.0, 1.0])
    T = np.gradient(S, axis=0)
    T = unit(T, np.zeros(3))
    for k in range(K):                                 # a still spine (a round patch): fall back to its neighbour
        if not np.any(T[k]):
            T[k] = T[k - 1] if k else unit(P[np.argmax(s)] - P[sources].mean(0))
    N = unit(Nb, mean_n)
    R = np.zeros((K, 3, 3))
    n = N[0] - T[0] * (N[0] @ T[0])
    if np.linalg.norm(n) < 1e-6:
        n = mean_n - T[0] * (mean_n @ T[0])
    n = unit(n) if np.linalg.norm(n) > 1e-9 else unit(np.cross(T[0], [1.0, 0.0, 0.0]) + [0, 0, 1e-9])
    for k in range(K):
        if k:                                          # rotation-minimising: carry N along the turn of T ...
            n = _carry(n, T[k - 1], T[k])
            want = N[k] - T[k] * (N[k] @ T[k])         # ... then let it follow the surface's own twist, gently
            if np.linalg.norm(want) > 1e-6:
                n = unit(n + 0.35 * (unit(want) - n))
        n = unit(n - T[k] * (n @ T[k]))
        b = np.cross(T[k], n)
        R[k] = np.column_stack([T[k], n, b])           # columns T N B
    return Panel(np.empty(0, np.int64), faces, P, s, L, S, R, ds, mean_n)


def _smooth_bins(X, sigma):
    """Gaussian smoothing along the bins (rows of X), edges held by reflection."""
    r = int(math.ceil(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.pad(X, ((r, r), (0, 0)), mode="reflect" if len(X) > r else "edge")
    return np.stack([np.convolve(pad[:, j], k, mode="valid") for j in range(X.shape[1])], 1)


def _carry(n, t0, t1):
    """n turned by the smallest rotation taking t0 to t1 (the double-reflection rule for a single step)."""
    v = np.cross(t0, t1)
    c = float(t0 @ t1)
    if c < -0.999:
        return n
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    Rm = np.eye(3) + vx + vx @ vx / (1.0 + c)
    return Rm @ n


def _rot(axis_local: int, ang):
    """Rotation matrices (n, 3, 3) about a local axis by angles ang (n,)."""
    ang = np.atleast_1d(ang)
    c, s = np.cos(ang), np.sin(ang)
    M = np.zeros((len(ang), 3, 3))
    i, j = [(1, 2), (2, 0), (0, 1)][axis_local]
    M[:, axis_local, axis_local] = 1.0
    M[:, i, i] = c
    M[:, j, j] = c
    M[:, i, j] = -s
    M[:, j, i] = s
    return M


def _scale_rotation(Q, f):
    """Rotations Q (n,3,3) with their angles scaled by f (n,)."""
    from scipy.spatial.transform import Rotation
    rv = Rotation.from_matrix(Q).as_rotvec()
    return Rotation.from_rotvec(rv * np.asarray(f)[:, None]).as_matrix()


def bend(pn: Panel, curl: float, open_angle: float, unroll: float, tighten: float, root_share: float,
         lift: float = 0.0):
    """Bent positions of a panel. Angles in radians, lift in model units; curl > 0 rolls outward."""
    K = len(pn.S)
    L, ds = pn.length, pn.ds
    sk = (np.arange(K) + 0.5) * ds
    s0 = float(np.clip(root_share, 0.0, 0.9)) * L
    blend = max(0.2 * L, 2.0 * ds)
    w = smoothstep((sk - s0) / blend)                  # 0 in the root, 1 past the hinge
    if (curl == 0 and open_angle == 0 and unroll == 0 and lift == 0) or not w.any():
        return pn.P.copy()
    x = sk / L
    g = w * (1.0 + 4.0 * max(tighten, 0.0) * x * x)
    g_sum = g.sum()
    dphi = curl * g / g_sum if g_sum > 0 else np.zeros(K)
    dw = np.diff(w, prepend=0.0)
    dphi = dphi + open_angle * dw / max(w[-1], 1e-9)
    R, S = pn.R, pn.S
    Qloc = np.einsum("kji,kjl->kil", R[:-1], R[1:])   # R_k^T R_{k+1}
    Qs = _scale_rotation(Qloc, 1.0 - np.clip(unroll, 0.0, 1.0) * w[1:])
    Bend = _rot(2, dphi[1:])                           # about B: T turns toward N (outward)
    Half = _rot(2, dphi[1:] * 0.5)
    dloc = np.einsum("kji,kj->ki", R[:-1], S[1:] - S[:-1])
    Rn, Sn = R.copy(), S.copy()
    fixed = True
    for k in range(K - 1):
        if fixed and w[k + 1] == 0.0:
            continue                                   # still in the root: exactly at rest
        fixed = False
        Rn[k + 1] = Rn[k] @ Bend[k] @ Qs[k]
        Sn[k + 1] = Sn[k] + Rn[k] @ (Half[k] @ dloc[k])
    # blended rigid moves of the two bins around each vertex
    A = np.einsum("kij,klj->kil", Rn, R)               # Rn_k R_k^T
    t = Sn - np.einsum("kij,kj->ki", A, S)
    u = pn.s / ds - 0.5
    k0 = np.clip(np.floor(u).astype(np.int64), 0, K - 2) if K > 1 else np.zeros(len(u), np.int64)
    f = np.clip(u - k0, 0.0, 1.0)[:, None] if K > 1 else np.zeros((len(u), 1))
    k1 = np.minimum(k0 + 1, K - 1)
    P = pn.P
    X0 = np.einsum("nij,nj->ni", A[k0], P) + t[k0]
    X1 = np.einsum("nij,nj->ni", A[k1], P) + t[k1]
    X = (1.0 - f) * X0 + f * X1
    rest = (pn.s <= s0) | ((w[k0] == 0.0) & (w[k1] == 0.0))     # the root share stays exactly in place
    X[rest] = P[rest]
    if lift:
        X = X + lift * pn.normal
    return X


# ----------------------------------------------------------------------------- shells

def boundary_edges(F):
    """Directed edges used by exactly one face (in that face's direction)."""
    E = F[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
    key = np.sort(E, axis=1)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    return E[cnt[inv.ravel()] == 1]


def smooth_normals(V, F, passes: int = 3):
    """Vertex normals averaged over their neighbours a few times: a sliver at a panel's rim no longer tips the
    offset of its corner (which would push a spike through the panel's face)."""
    N = vertex_normals(V, F)
    E = np.unique(np.sort(F[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1), axis=0)
    deg = np.bincount(E.ravel(), minlength=len(V)).astype(np.float64)
    for _ in range(int(passes)):
        acc = N.copy()
        np.add.at(acc, E[:, 0], N[E[:, 1]])
        np.add.at(acc, E[:, 1], N[E[:, 0]])
        N = unit(acc / (1.0 + deg)[:, None], np.zeros(3))
    return N


def solidify(V, F, thickness: float):
    """A shell: the surface, a copy offset inward by `thickness` along the vertex normals (reversed), and rims along
    every open edge. A closed surface, or thickness 0, comes back as it is."""
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    if thickness <= 0 or len(F) == 0:
        return V, F
    be = boundary_edges(F)
    if not len(be):
        return V, F
    n = len(V)
    N = smooth_normals(V, F, 3)
    back = V - thickness * N
    a, b = be[:, 0], be[:, 1]
    rim = np.concatenate([np.column_stack([b, a, a + n]), np.column_stack([b, a + n, b + n])])
    return np.vstack([V, back]), np.vstack([F, F[:, ::-1] + n, rim])


# ----------------------------------------------------------------------------- the whole layer

@dataclass
class Layer:
    V: np.ndarray                      # rest vertices (seams smoothed)
    F: np.ndarray
    labels: np.ndarray
    panels: list                       # [Panel], index = label
    scale: float = 1.0


def build_layer(V, F, p, centre, a, weights_rest, index: int, rng_seed: int, progress=None) -> Layer:
    """Panels with frames for one layer (V already scaled for inner layers). weights_rest: influence per vertex."""
    n_pet = max(1, int(p["petals"]))
    offset = (index * math.pi / n_pet) if p["pattern"] == "petals" else 0.0
    pan = segment(V, F, p, centre, a, offset=offset, seed_shift=97 * index, progress=progress)
    Vs = pan.V
    Nv = vertex_normals(Vs, F)
    theta, _ = polar(Vs, centre, a)
    order = np.argsort(pan.labels, kind="stable")
    bounds = np.searchsorted(pan.labels[order], np.arange(pan.count + 1))
    rng = np.random.default_rng(int(rng_seed) + 7919 * index)
    var = float(p["variation"])
    tl, th = float(theta.min()), float(theta.max())
    panels = []
    for k in range(pan.count):
        fsel = order[bounds[k]:bounds[k + 1]]
        Fk = F[fsel]
        verts, local = np.unique(Fk, return_inverse=True)
        faces = local.reshape(-1, 3)
        pn = panel_frames(Vs[verts], faces, Nv[verts], theta[verts], p["root"])
        jit = {"curl": 1.0 + var * float(np.clip(rng.normal(0, 0.45), -0.9, 0.9)),
               "open": 1.0 + var * float(np.clip(rng.normal(0, 0.45), -0.9, 0.9)),
               "delay": float(rng.random())}
        if pn is None:                                 # a sliver too small to bend: it stays
            pn = Panel(verts, faces, Vs[verts], np.zeros(len(verts)), 0.0, np.zeros((0, 3)), np.zeros((0, 3, 3)),
                       1.0, np.array([0.0, 0.0, 1.0]))
        pn.verts = verts
        pn.pole_t = float((theta[verts].mean() - tl) / max(th - tl, 1e-9))
        pn.weight = float(weights_rest[verts].mean()) if weights_rest is not None else 1.0
        pn.jitter = jit
        panels.append(pn)
    return Layer(Vs, F, pan.labels, panels)


def activation(pn: Panel, order: str, stagger: float) -> float:
    """Start time of a panel in 0..1 (times the stagger)."""
    if order == "together" or stagger <= 0:
        return 0.0
    t = {"pole": pn.pole_t, "far": 1.0 - pn.pole_t, "random": pn.jitter.get("delay", 0.0)}.get(order, 0.0)
    return float(np.clip(stagger, 0.0, 0.95)) * t


def panel_amount(pn: Panel, u: float, order: str, stagger: float) -> float:
    """How far a panel is open at amount u (0 shut, 1 fully, more past 1), influence included."""
    if pn.length <= 0 or pn.weight <= 0 or u <= 0:
        return 0.0
    s = float(np.clip(stagger, 0.0, 0.95)) if order != "together" else 0.0
    a = (u - activation(pn, order, stagger)) / (1.0 - s)
    return float(ease(max(a, 0.0))) * pn.weight


def pose_layer(layer: Layer, p, u: float, height: float, layer_amount: float = 1.0):
    """The layer at amount u: (moved panels [(V, F)], resting faces F (indices into layer.V), opening per panel)."""
    moved, still, opened = [], [], []
    for pn in layer.panels:
        a = panel_amount(pn, u * layer_amount, p["order"], p["stagger"])
        opened.append(a)
        if a <= 0:
            still.append(pn.verts[pn.faces])
            continue
        X = bend(pn, math.radians(p["curl"]) * a * pn.jitter["curl"],
                 math.radians(p["open_angle"]) * a * pn.jitter["open"],
                 min(1.0, p["unroll"] * a), p["tighten"], p["root_share"] / 100.0,
                 p["lift"] / 100.0 * height * a)
        moved.append((X, pn.faces))
    F_still = np.vstack(still) if still else np.zeros((0, 3), np.int64)
    return moved, F_still, opened


def compact(V, F):
    """Only the vertices F uses, reindexed."""
    used, inv = np.unique(F, return_inverse=True)
    return V[used], inv.reshape(-1, 3)


def stretch(rest, bent, faces) -> float:
    """99th percentile of edge length ratios after/before (1: rigid)."""
    E = np.unique(np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1), axis=0)
    a = np.linalg.norm(rest[E[:, 0]] - rest[E[:, 1]], axis=1)
    b = np.linalg.norm(bent[E[:, 0]] - bent[E[:, 1]], axis=1)
    ok = a > 1e-12
    return float(np.percentile(b[ok] / a[ok], 99)) if ok.any() else 1.0
