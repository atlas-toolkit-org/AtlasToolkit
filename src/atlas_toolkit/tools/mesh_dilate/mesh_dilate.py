"""Pressure-driven sculptural dilation, independent of the Atlas UI.

The skin uses local edge/bending projections and a prefactored sparse global
solve (Bouaziz et al., Projective Dynamics, 2014). Each closed connected surface
has an independent pressure controller. Open surfaces use an area-equivalent
outward displacement. Contact backtracking is a discrete geometric safeguard,
not a continuous collision guarantee or a calibrated solid FEM simulation.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from atlas_toolkit.geom.influence import smoothstep
from atlas_toolkit.tools.mesh_compress import mesh_compress as mc

AXIS = {"up": 2, "front": 1, "side": 0}


def components(V, F):
    """Vertex and face indices per connected part, without filling holes."""
    from scipy import sparse
    from scipy.sparse.csgraph import connected_components
    E, _ = mc.edges_of(F)
    graph = sparse.coo_matrix((np.ones(len(E)), (E[:, 0], E[:, 1])), shape=(len(V), len(V)))
    _, labels = connected_components(graph, directed=False)
    return [(np.flatnonzero(labels == label), np.flatnonzero(labels[F[:, 0]] == label))
            for label in np.unique(labels[F[:, 0]])]


def areas(V, F):
    face = np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1) * 0.5
    vertex = np.zeros(len(V))
    for k in range(3):
        np.add.at(vertex, F[:, k], face / 3)
    return face, vertex


def boundary_vertices(F):
    edges, counts = np.unique(np.sort(F[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1),
                             axis=0, return_counts=True)
    return np.unique(edges[counts == 1])


def mobility(V, F, influence, anchor, lo, hi):
    """Rest-space influence times the anchor's smooth freedom of movement."""
    w = np.asarray(influence, float).copy()
    height = float(hi[2] - lo[2]) or float(np.max(np.asarray(hi) - lo)) or 1.0
    if anchor == "base":
        w *= smoothstep(((V[:, 2] - lo[2]) / height - 0.02) / 0.05)
    elif anchor == "boundary":
        boundary = boundary_vertices(F)
        if len(boundary):
            from scipy import sparse
            from scipy.sparse.csgraph import dijkstra
            E, _ = mc.edges_of(F)
            lengths = np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1)
            G = sparse.coo_matrix((np.r_[lengths, lengths],
                                  (np.r_[E[:, 0], E[:, 1]], np.r_[E[:, 1], E[:, 0]])),
                                 shape=(len(V), len(V))).tocsr()
            distance = dijkstra(G, indices=boundary, min_only=True)
            w *= smoothstep(distance / (0.05 * height))
            w[boundary] = 0.0
    return np.clip(w, 0, 1)


def lobe_field(V, height, amount=0.0, size=35.0, seed=0):
    """Seeded low-frequency pressure variation; depends on position, not topology."""
    if amount <= 0:
        return np.ones(len(V))
    rng = np.random.default_rng(int(seed))
    directions = rng.normal(size=(4, 3))
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)
    wavelength = max(height * float(size) / 100, 1e-9)
    phase = rng.uniform(-math.pi, math.pi, 4)
    field = np.sin(V @ directions.T * (2 * math.pi / wavelength) + phase).mean(axis=1)
    return np.exp(2.0 * float(amount) * field)


def intersections(V, F, ignore=frozenset(), limit=1, check=None, ignore_faces=frozenset(), active=None, verify=None):
    """Non-adjacent intersecting triangles: R-tree broad phase, libigl exact test.

    Shared-vertex pairs are adjacent and excluded. Existing intersections can be
    ignored by stable face index, so dirty inputs are not treated as new damage.
    """
    import igl
    from rtree import index
    triangles = np.asarray(V, float)[F]
    bounds = np.hstack((triangles.min(axis=1), triangles.max(axis=1)))
    prop = index.Property()
    prop.dimension = 3
    tree = index.Index(((i, tuple(b), None) for i, b in enumerate(bounds)), properties=prop)
    found = set()
    try:
        scan = range(len(F)) if active is None else active
        seen = set()
        for i in scan:
            b = bounds[i]
            if check and i % 512 == 0:
                check()
            candidates = np.fromiter((j for j in tree.intersection(tuple(b))
                                      if (j > i if active is None else j != i)), dtype=np.int64)
            if not len(candidates):
                continue
            shared = (F[candidates, :, None] == F[i, None, :]).any(axis=(1, 2))
            for j in candidates[~shared]:
                pair = (min(int(i), int(j)), max(int(i), int(j)))
                if pair in seen or pair in ignore or (i in ignore_faces and int(j) in ignore_faces):
                    continue
                seen.add(pair)
                a, btri = triangles[i], triangles[j]
                hit, *detail = igl.tri_tri_intersection_test_3d(*(p[None, :] for p in (*a, *btri)))
                if hit and (verify is None or verify(a, btri, detail)):
                    found.add(pair)
                    if len(found) >= limit:
                        return found
    finally:
        tree.close()
    return found


def invalid_faces(before, after, F):
    a = np.cross(before[F[:, 1]] - before[F[:, 0]], before[F[:, 2]] - before[F[:, 0]])
    b = np.cross(after[F[:, 1]] - after[F[:, 0]], after[F[:, 2]] - after[F[:, 0]])
    aa, bb = np.linalg.norm(a, axis=1), np.linalg.norm(b, axis=1)
    return ((bb <= np.maximum(aa * 1e-5, 1e-18)) |
            (np.einsum("ij,ij->i", a, b) <= -1e-8 * aa * bb))


def valid_faces(before, after, F):
    return bool(np.isfinite(after).all() and not np.any(invalid_faces(before, after, F)))


class ContactGuard:
    def __init__(self, V, F, check=None, verify=None):
        self.F, self.check, self.verify = F, check, verify
        self.existing = intersections(V, F, limit=4096, check=check, verify=verify)
        self.edges, _ = mc.edges_of(F)
        self.dirty_faces = set()
        if self.existing:
            # An existing intersecting contact is a patch, not a stable pair of
            # triangle IDs: refinement and small sliding motions change the pair.
            dirty_vertices = np.unique(F[np.unique(np.array(list(self.existing)))])
            dirty = np.zeros(len(V), bool)
            dirty[dirty_vertices] = True
            self.dirty_faces = set(np.flatnonzero(dirty[F].any(axis=1)).tolist())
        self.limited = 0

    def limit(self, start, proposed):
        if np.array_equal(start, proposed):
            return start.copy(), 1.0
        scale = np.ones(len(start))
        delta = proposed - start
        active = None
        for attempt in range(12):
            if self.check:
                self.check()
            trial = start + scale[:, None] * delta
            if not np.isfinite(trial).all():
                break
            bad = np.flatnonzero(invalid_faces(start, trial, self.F))
            hits = intersections(trial, self.F, ignore=self.existing, check=self.check,
                                 ignore_faces=self.dirty_faces, limit=128, active=active, verify=self.verify)
            if not len(bad) and not hits and active is not None:
                hits = intersections(trial, self.F, ignore=self.existing, check=self.check,
                                     ignore_faces=self.dirty_faces, limit=128, verify=self.verify)
            if not len(bad) and not hits:
                if attempt:
                    self.limited += 1
                return trial, float(scale.mean())
            faces = np.r_[bad, np.array(list(hits), dtype=np.int64).ravel()]
            blocked = np.zeros(len(start))
            blocked[np.unique(self.F[faces])] = 1
            # Restrain contact locally, feathering into the surrounding skin.
            # A close lip or eyelid must not freeze inflation of the entire head.
            for _ in range(4):
                blocked = np.maximum(blocked, 0.75 * mc.neighbours_mean(blocked, self.edges, len(start)))
            scale *= 1 - 0.9 * blocked
            active = np.flatnonzero((blocked[self.F] > 0).any(axis=1))
        # Conservative global fallback after local contact relaxation.
        for attempt in range(1, 10):
            alpha = 0.5 ** attempt
            trial = start + (alpha * scale)[:, None] * delta
            if valid_faces(start, trial, self.F) and not intersections(
                    trial, self.F, ignore=self.existing, ignore_faces=self.dirty_faces, check=self.check, verify=self.verify):
                self.limited += 1
                return trial, float(alpha * scale.mean())
        self.limited += 1
        return start.copy(), 0.0


@dataclass
class Outcome:
    vertices: np.ndarray
    targets: list[float | None]
    volumes0: list[float | None]
    volumes1: list[float | None]
    participation: list[float]
    limited: int
    existing: int
    trajectory: list[np.ndarray]


def solve(V, F, weights, *, inflation=80.0, softness=0.7, roundness=0.6,
          direction="all", axis="up", lobes=0.0, lobe_size=35.0, seed=0,
          height=2.0, closed=None, draft=False, progress=None, check=None):
    """Inflate all parts together, with separate pressure targets and contacts."""
    from scipy import sparse
    from scipy.sparse.linalg import splu
    from scipy.spatial import cKDTree

    V, F = np.asarray(V, float), np.asarray(F, np.int64)
    if not len(F) or not len(V) or not np.isfinite(V).all():
        raise ValueError("Dilate needs a finite, nonempty triangle surface")
    X = V.copy()
    parts = components(V, F)
    E, B = mc.edges_of(F)
    _, area = areas(V, F)
    weights = np.clip(np.asarray(weights, float), 0, 1)
    field = lobe_field(V, height, lobes, lobe_size, seed)
    fixed = weights <= 1e-12
    locked = AXIS[axis] if direction == "radial" else None
    GE, GB = mc._incidence(E, len(V)), mc._incidence(B, len(V))
    L0 = np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1)
    B0 = np.linalg.norm(V[B[:, 0]] - V[B[:, 1]], axis=1)
    edge = float(np.median(L0))
    gain_requested = float(inflation) / 100
    targets, volumes0, participation, open_target = [], [], [], V.copy()
    is_open = np.zeros(len(V), bool)
    for k, (indices, fi) in enumerate(parts):
        part_area = float(area[indices].sum())
        share = float(area[indices] @ weights[indices] / max(part_area, 1e-18))
        participation.append(share)
        field[indices] /= max(float(area[indices] @ field[indices] / max(part_area, 1e-18)), 1e-12)
        volume = mc.volume(V, F[fi])
        reliable = mc.is_closed(F[fi]) and volume > 1e-10 * part_area ** 1.5
        if closed is not None:
            reliable = reliable and bool(closed[k])
        volumes0.append(volume if reliable else None)
        targets.append(volume * (1 + gain_requested * share) if reliable else None)
        if not reliable:
            is_open[indices] = True
            distance = math.sqrt(part_area / (4 * math.pi)) * ((1 + gain_requested) ** (1 / 3) - 1)
            normals = mc.smooth_vectors(mc.vertex_normals(V, F), E, 2 + int(4 * roundness))
            normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
            open_target[indices] += (distance * weights[indices] * field[indices])[:, None] * normals[indices]
    if gain_requested == 0 or not np.any(weights > 1e-12):
        return Outcome(X, targets, volumes0, volumes0.copy(), participation, 0, 0, [])

    skin = 1.4 - 0.9 * float(softness)
    bending = 0.015 + 0.22 * (1 - float(roundness))
    mass = 0.04
    tether = 0.12 * (1 - weights) ** 2 / (weights ** 2 + 0.005)
    tether[fixed] = 1e5
    open_stiffness = is_open.astype(float) * (0.35 + softness)
    A = skin * GE.T @ GE + bending * GB.T @ GB + sparse.diags(mass + tether + open_stiffness)
    lu = splu(sparse.csc_matrix(A))
    guard = ContactGuard(V, F, check)
    pressures = np.zeros(len(parts))
    count, inner = (24, 8) if draft else (48, 12)
    creep = 1 - (1 - 0.95 * softness) ** (1 / count)

    def project(P):
        for k, (indices, _) in enumerate(parts):
            if volumes0[k] is not None and np.all(weights[indices] >= 1 - 1e-12):
                # Nonuniform pressure should change the silhouette, not translate
                # a freely floating part out of the input's frame.
                P[indices] -= np.average(P[indices] - V[indices], axis=0, weights=area[indices])
        P[fixed] = V[fixed]
        if locked is not None:
            P[:, locked] = V[:, locked]
        return P

    def spring_targets(P, pairs, lengths):
        D = P[pairs[:, 0]] - P[pairs[:, 1]]
        return D * (lengths / np.maximum(np.linalg.norm(D, axis=1), 1e-15))[:, None]

    safe = X.copy()
    trajectory = []
    for load in range(1, count + 1):
        u = load / count
        predicted = X.copy()
        fields, gains = [], []
        for k, (indices, fi) in enumerate(parts):
            if targets[k] is None:
                fields.append(None)
                gains.append(0.0)
                continue
            G = mc.volume_gradient(X, F[fi])
            Gs = mc.smooth_vectors(G, E, 2 + int(roundness * 6)) * (weights * field)[:, None]
            Gs[fixed] = 0
            if locked is not None:
                Gs[:, locked] = 0
            response = lu.solve(Gs)
            response[fixed] = 0
            if locked is not None:
                response[:, locked] = 0
            fields.append(Gs)
            gains.append(float((G * response).sum()))
        for iteration in range(inner):
            if check:
                check()
            rhs = skin * (GE.T @ spring_targets(X, E, L0)) + mass * predicted + tether[:, None] * V
            if len(B):
                rhs += bending * (GB.T @ spring_targets(X, B, B0))
            rhs += open_stiffness[:, None] * (V + u * (open_target - V))
            for k, Gs in enumerate(fields):
                if Gs is not None:
                    rhs += pressures[k] * Gs
            Y = project(lu.solve(rhs))
            delta = Y - X
            longest = float(np.linalg.norm(delta, axis=1).max())
            Y = project(X + delta * min(1.0, 0.3 * edge / max(longest, 1e-12)))
            # Repel near non-neighbours; exact triangle checks below catch contacts
            # missed by vertex sampling, including between disconnected parts.
            if iteration % 4 == 3:
                radius = 0.35 * edge
                pairs = cKDTree(Y).query_pairs(radius, output_type="ndarray")
                if len(pairs):
                    rest_distance = np.linalg.norm(V[pairs[:, 0]] - V[pairs[:, 1]], axis=1)
                    pairs = pairs[rest_distance > 1.5 * radius]
                    if len(pairs):
                        D = Y[pairs[:, 1]] - Y[pairs[:, 0]]
                        lengths = np.linalg.norm(D, axis=1)
                        correction = D * ((radius - lengths) / np.maximum(lengths, 1e-12) * 0.5)[:, None]
                        movement = np.zeros_like(Y)
                        np.add.at(movement, pairs[:, 0], -correction)
                        np.add.at(movement, pairs[:, 1], correction)
                        counts = np.maximum(np.bincount(pairs.ravel(), minlength=len(V)), 1)
                        Y = project(Y + movement / counts[:, None] * weights[:, None])
            X = Y
            for k, (_, fi) in enumerate(parts):
                if targets[k] is not None and gains[k] > 1e-18:
                    target = volumes0[k] + u * (targets[k] - volumes0[k])
                    pressures[k] += 0.55 * (target - mc.volume(X, F[fi])) / gains[k]
                    pressures[k] = float(np.clip(pressures[k], -1e3, 1e3))
        alpha = 1.0
        if load % 8 == 0 or load == count:
            X, alpha = guard.limit(safe, X)
            safe = X.copy()
        trajectory.append(X.copy())
        if alpha < 1:
            pressures *= alpha
        L0 += creep * (np.linalg.norm(X[E[:, 0]] - X[E[:, 1]], axis=1) - L0)
        if len(B):
            B0 += creep * roundness * (np.linalg.norm(X[B[:, 0]] - X[B[:, 1]], axis=1) - B0)
        if progress:
            progress(u)
    volumes1 = [mc.volume(X, F[fi]) if volumes0[k] is not None else None for k, (_, fi) in enumerate(parts)]
    return Outcome(X, targets, volumes0, volumes1, participation, guard.limited, len(guard.existing), trajectory)


def warp(base, S, SF, inflated):
    """Continuous spatial blend of a small simulation deformation."""
    from scipy.spatial import cKDTree
    E, _ = mc.edges_of(SF)
    # The compact C2 kernel goes to zero at the last neighbour. Unlike a hard
    # k-nearest inverse-distance blend, switching neighbourhoods cannot produce
    # little jumps that fold the triangles of a detailed scan.
    rotations = mc.rotations(S, inflated, E)
    count = min(32, len(S))
    distance, indices = cKDTree(S).query(base, k=count)
    if count == 1:
        distance, indices = distance[:, None], indices[:, None]
    radius = np.maximum(distance[:, -1:] * 1.00001, 1e-12)
    q = np.clip(distance / radius, 0, 1)
    kernel = (1 - q) ** 4 * (4 * q + 1)
    # A single spatial warp carries both sides of a thin feature together.
    # Weighting by the detail mesh's normals makes the warp depend on how its
    # original flat triangles were subdivided, creating folds along old edges.
    blend = kernel
    blend /= np.maximum(blend.sum(axis=1, keepdims=True), 1e-30)
    X = np.zeros_like(base)
    for k in range(count):
        j = indices[:, k]
        X += blend[:, k, None] * (inflated[j] + np.einsum("nij,nj->ni", rotations[j], base - S[j]))
    return X


def detail_base(D, DF, weights, detail):
    ED, _ = mc.edges_of(DF)
    smooth = mc.taubin(D, ED, 4)
    return D + (1 - float(detail)) * (smooth - D) * np.asarray(weights)[:, None]


def transfer_detail(D, DF, S, SF, inflated, weights, *, detail=0.85, direction="all", axis="up"):
    """One small detail transfer; the adapter composes these along the solve."""
    base = detail_base(D, DF, weights, detail)
    X = D + (warp(base, S, SF, inflated) - D) * smoothstep(np.asarray(weights))[:, None]
    fixed = np.asarray(weights) <= 1e-12
    X[fixed] = D[fixed]
    if direction == "radial":
        X[:, AXIS[axis]] = D[:, AXIS[axis]]
    return X
