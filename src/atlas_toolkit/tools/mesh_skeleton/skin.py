"""
The working surface of Skeleton Shell: an even copy of the model's skin and the operations every structure is made of.

    Skin            decimate what is too heavy, split what is too long (conforming: no T-junctions), drop loose specks
    relax           seeds spread over the skin by Lloyd iterations on its edge graph, with a size field and a stretch
    voronoi         the boundaries between seed cells, as a graph of curves on the surface
    isolines        the level curves of any scalar on the surface
    project         the closest point on the surface, with an interpolated normal
    clip / shell    the part of the skin where a field is negative, thickened inward into a closed plate
    trace_lines     streamlines of a line field per face, walked exactly across the triangles

Everything works in the model's canonical frame (geom/frame3d), so sizes given as % of the height mean the same on any
model. No GUI.
"""
from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components, dijkstra

from atlas_toolkit.geom import curvature as cv
from atlas_toolkit.geom import mesh as gm

MAX_SPLIT_ROUNDS = 8


def split_long_edges(V: np.ndarray, F: np.ndarray, length: float) -> tuple[np.ndarray, np.ndarray]:
    """Split every edge longer than `length`, one round at a time, keeping the mesh conforming."""
    for _ in range(MAX_SPLIT_ROUNDS):
        E, FE, _, _ = cv.edge_topology(F)
        lengths = np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1)
        long = lengths > length
        if not long.any():
            break
        mid = np.full(len(E), -1, np.int64)
        mid[long] = np.arange(long.sum()) + len(V)
        V = np.vstack([V, 0.5 * (V[E[long, 0]] + V[E[long, 1]])])
        marked = long[FE]
        count = marked.sum(1)
        parts = [F[count == 0]]
        # one edge: the corner opposite it stays, two triangles
        rows = np.flatnonzero(count == 1)
        if len(rows):
            j = marked[rows].argmax(1)
            a, b, c = F[rows, j], F[rows, (j + 1) % 3], F[rows, (j + 2) % 3]
            m = mid[FE[rows, j]]
            parts += [np.stack([a, b, m], 1), np.stack([a, m, c], 1)]
        # two edges: the corner opposite the whole edge keeps a triangle, the rest is a quad
        rows = np.flatnonzero(count == 2)
        if len(rows):
            j = (~marked[rows]).argmax(1)       # the unsplit edge is opposite corner j
            a, b, c = F[rows, j], F[rows, (j + 1) % 3], F[rows, (j + 2) % 3]
            # the two split edges run a-b (opposite c) and c-a (opposite b)
            ab, ca = mid[FE[rows, (j + 2) % 3]], mid[FE[rows, (j + 1) % 3]]
            parts += [np.stack([a, ab, ca], 1), np.stack([ab, b, c], 1), np.stack([ab, c, ca], 1)]
        rows = np.flatnonzero(count == 3)
        if len(rows):
            a, b, c = F[rows, 0], F[rows, 1], F[rows, 2]
            bc, ca, ab = mid[FE[rows, 0]], mid[FE[rows, 1]], mid[FE[rows, 2]]
            parts += [np.stack([a, ab, ca], 1), np.stack([ab, b, bc], 1), np.stack([ca, bc, c], 1),
                      np.stack([ab, bc, ca], 1)]
        F = np.vstack(parts)
    return V, F


def chains(count: int, segments: np.ndarray) -> list[tuple[np.ndarray, bool]]:
    """Unbranched runs of a segment graph, as (node indices, closed); runs meet only at nodes of degree other than 2."""
    if not len(segments):
        return []
    neighbours: list[list] = [[] for _ in range(count)]
    for i, (a, b) in enumerate(segments.tolist()):
        neighbours[a].append((b, i))
        neighbours[b].append((a, i))
    used = np.zeros(len(segments), bool)
    out = []

    def walk(start, first, seg):
        run = [start, first]
        used[seg] = True
        previous, here = seg, first
        while len(neighbours[here]) == 2:
            (n0, s0), (n1, s1) = neighbours[here]
            nxt, s = (n1, s1) if s0 == previous else (n0, s0)
            if used[s]:
                break
            used[s] = True
            run.append(nxt)
            previous, here = s, nxt
        return run

    for node, around in enumerate(neighbours):
        if around and len(around) != 2:
            for other, seg in around:
                if not used[seg]:
                    out.append((np.asarray(walk(node, other, seg), np.int64), False))
    for seg in np.flatnonzero(~used).tolist():
        if used[seg]:
            continue
        a, b = segments[seg]
        run = walk(int(a), int(b), seg)
        closed = len(run) > 3 and run[0] == run[-1]
        out.append((np.asarray(run[:-1] if closed else run, np.int64), closed))
    return out


class Skin:
    """An even triangle surface with its edge graph, normals and areas."""

    def __init__(self, V, F, edge: float, max_faces: int, speck: float = 0.0):
        V, F = np.asarray(V, np.float64), np.asarray(F, np.int64)
        if len(F) > max_faces:
            V, F = gm.decimate(V, F, max_faces)
            V, F = np.asarray(V, np.float64), np.asarray(F, np.int64)
        _, areas = cv.face_normals(V, F)
        F = F[areas > 1e-14]
        if not len(F):
            raise ValueError("The model has no surface area.")
        total = float(cv.face_normals(V, F)[1].sum())
        # an even triangle of side L has an area of 0.433 L²: never split past the budget
        floor = float(np.sqrt(total / (0.433 * max(max_faces, 100))))
        self.edge = max(float(edge), floor)
        V, F = split_long_edges(V, F, self.edge * 1.4)
        used = np.zeros(len(V), bool)
        used[F.ravel()] = True
        remap = np.cumsum(used) - 1
        V, F = V[used], remap[F]
        E, _, _, _ = cv.edge_topology(F)
        labels = connected_components(self._matrix(len(V), E, np.ones(len(E))), directed=False)[1]
        if speck > 0 and labels.max() > 0:
            face_area = cv.face_normals(V, F)[1]
            by_part = np.bincount(labels[F[:, 0]], weights=face_area, minlength=labels.max() + 1)
            keep = by_part[labels[F[:, 0]]] >= speck
            if keep.any() and not keep.all():
                F = F[keep]
                used = np.zeros(len(V), bool)
                used[F.ravel()] = True
                remap = np.cumsum(used) - 1
                V, F = V[used], remap[F]
        self.V, self.F = V, F
        self.E, self.FE, self.EF, self.counts = cv.edge_topology(F)
        self.lengths = np.maximum(np.linalg.norm(V[self.E[:, 0]] - V[self.E[:, 1]], axis=1), 1e-12)
        self.face_normals, self.face_areas = cv.face_normals(V, F)
        self.N = cv.smooth_normals(cv.vertex_normals(V, F), self.E, 2)
        self.area = float(self.face_areas.sum())
        self.vertex_areas = np.bincount(F.ravel(), weights=np.repeat(self.face_areas / 3.0, 3), minlength=len(V))
        degree = np.bincount(self.E.ravel(), minlength=len(V))
        step = sparse.csr_matrix((np.ones(2 * len(self.E)), (np.r_[self.E[:, 0], self.E[:, 1]],
                                                            np.r_[self.E[:, 1], self.E[:, 0]])),
                                 shape=(len(V), len(V)))
        self.average = sparse.diags(1.0 / np.maximum(degree, 1)) @ step
        self.parts = connected_components(step, directed=False)[1]
        self.lo, self.hi = V.min(0), V.max(0)
        self.height = float(self.hi[2] - self.lo[2]) or 1.0
        self._curvature = None

    @staticmethod
    def _matrix(count, E, weights):
        return sparse.csr_matrix((np.r_[weights, weights], (np.r_[E[:, 0], E[:, 1]], np.r_[E[:, 1], E[:, 0]])),
                                 shape=(count, count))

    # ------------------------------------------------------------------------------------------- fields
    def smooth(self, field: np.ndarray, passes: int, keep: float = 0.5) -> np.ndarray:
        out = np.asarray(field, np.float64)
        for _ in range(int(passes)):
            out = keep * out + (1.0 - keep) * (self.average @ out)
        return out

    def curvature(self) -> dict:
        if self._curvature is None:
            self._curvature = cv.curvature(self.V, self.F, self.N)
        return self._curvature

    def bumpiness(self, passes: int = 12) -> np.ndarray:
        """Where the form turns fast: smoothed |curvature| per vertex, outliers cut."""
        k = np.abs(self.curvature()["k1"])
        k = np.minimum(np.nan_to_num(k), np.percentile(k, 99.0))
        return self.smooth(k, passes)

    # ------------------------------------------------------------------------------------------- cells
    def metric(self, size: np.ndarray | None = None, stretch: float = 1.0) -> sparse.csr_matrix:
        """The edge graph, measured so that cells come out `size` wide and `stretch` times longer upward."""
        w = self.lengths.copy()
        if size is not None:
            w = w * 2.0 / (size[self.E[:, 0]] + size[self.E[:, 1]])
        if stretch != 1.0:
            along = np.abs(self.V[self.E[:, 1], 2] - self.V[self.E[:, 0], 2]) / self.lengths
            w = w * np.sqrt(along ** 2 / stretch + (1.0 - along ** 2) * stretch)
        return self._matrix(len(self.V), self.E, w)

    def nearest(self, graph, seeds: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(distance, predecessor, label): each vertex's nearest seed; label indexes `seeds`, -1 if unreachable."""
        dist, pred, source = dijkstra(graph, directed=True, indices=seeds, min_only=True, return_predecessors=True)
        lookup = np.full(len(self.V), -1, np.int64)
        lookup[seeds] = np.arange(len(seeds))
        labels = np.where(source >= 0, lookup[np.maximum(source, 0)], -1)
        return dist, pred, labels

    def relax(self, count: int, rng, size: np.ndarray | None = None, stretch: float = 1.0,
              iterations: int = 6) -> np.ndarray:
        """Seed vertices spread by weighted Lloyd iterations; every part of the surface gets at least one."""
        density = self.vertex_areas if size is None else self.vertex_areas / size ** 2
        density = np.maximum(density, 0.0) + 1e-18
        count = int(np.clip(count, 1, len(self.V) // 4 or 1))
        seeds = rng.choice(len(self.V), size=count, replace=False, p=density / density.sum())
        for part in np.unique(self.parts):
            if not (self.parts[seeds] == part).any():
                members = np.flatnonzero(self.parts == part)
                seeds = np.r_[seeds, members[np.argmax(density[members])]]
        graph = self.metric(size, stretch)
        for _ in range(int(iterations)):
            _, _, labels = self.nearest(graph, seeds)
            good = labels >= 0
            weight = np.bincount(labels[good], weights=density[good], minlength=len(seeds))
            centre = np.stack([np.bincount(labels[good], weights=density[good] * self.V[good, c],
                                           minlength=len(seeds)) for c in range(3)], 1)
            centre /= np.maximum(weight, 1e-18)[:, None]
            gap = np.full(len(self.V), np.inf)
            gap[good] = np.linalg.norm(self.V[good] - centre[labels[good]], axis=1)
            order = np.lexsort((gap, labels))
            first = order[np.searchsorted(labels[order], np.arange(len(seeds)))]
            moved = np.where(labels[first] == np.arange(len(seeds)), first, seeds)
            seeds = np.unique(moved)
        return seeds

    def _crossings(self, cross: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Curves through the marked edges, at fraction t from E[:, 0] to E[:, 1]. Returns nodes and segments; a face
        crossed on all three edges (three cells meet) gets a node of its own."""
        index = np.full(len(self.E), -1, np.int64)
        index[cross] = np.arange(cross.sum())
        t = np.clip(t[cross], 0.0, 1.0)[:, None]
        points = [self.V[self.E[cross, 0]] * (1.0 - t) + self.V[self.E[cross, 1]] * t]
        marked = cross[self.FE]
        count = marked.sum(1)
        segments = []
        rows = np.flatnonzero(count == 2)
        if len(rows):
            j = (~marked[rows]).argmax(1)
            segments.append(np.stack([index[self.FE[rows, (j + 1) % 3]], index[self.FE[rows, (j + 2) % 3]]], 1))
        rows = np.flatnonzero(count == 3)
        if len(rows):
            ids = index[self.FE[rows]]
            centre = points[0][ids].mean(1)
            base = len(points[0])
            points.append(centre)
            middle = base + np.arange(len(rows))
            for j in range(3):
                segments.append(np.stack([ids[:, j], middle], 1))
        nodes = np.vstack(points) if points[0].size else np.zeros((0, 3))
        return nodes, (np.vstack(segments) if segments else np.zeros((0, 2), np.int64))

    def voronoi(self, labels: np.ndarray, dist: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The boundaries between cells, placed where the two seeds' distances meet."""
        a, b = self.E[:, 0], self.E[:, 1]
        cross = (labels[a] != labels[b]) & (labels[a] >= 0) & (labels[b] >= 0)
        t = (dist[b] - dist[a] + self.lengths) / (2.0 * self.lengths)
        return self._crossings(cross, np.nan_to_num(t, nan=0.5, posinf=0.5, neginf=0.5))

    def isolines(self, field: np.ndarray, level: float) -> list[tuple[np.ndarray, bool]]:
        """Level curves of a per-vertex scalar, as (points, closed)."""
        above = field >= level
        a, b = self.E[:, 0], self.E[:, 1]
        cross = above[a] != above[b]
        if not cross.any():
            return []
        gap = field[b] - field[a]
        t = np.where(np.abs(gap) > 1e-15, (level - field[a]) / np.where(np.abs(gap) > 1e-15, gap, 1.0), 0.5)
        nodes, segments = self._crossings(cross, t)
        return [(nodes[run], closed) for run, closed in chains(len(nodes), segments)]

    # ------------------------------------------------------------------------------------------- surface queries
    def project(self, P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Closest points on the surface and the smooth normals there."""
        import igl
        P = np.asarray(P, np.float64)
        if not len(P):
            return P, P
        _, faces, closest = igl.point_mesh_squared_distance(P, self.V, self.F)
        faces = np.asarray(faces, np.int64).ravel()
        tri = self.F[faces]
        a, b, c = self.V[tri[:, 0]], self.V[tri[:, 1]], self.V[tri[:, 2]]
        v0, v1, v2 = b - a, c - a, closest - a
        d00, d01, d11 = cv._dot(v0, v0), cv._dot(v0, v1), cv._dot(v1, v1)
        d20, d21 = cv._dot(v2, v0), cv._dot(v2, v1)
        det = d00 * d11 - d01 * d01
        det = np.where(np.abs(det) > 1e-30, det, 1.0)
        u, v = (d11 * d20 - d01 * d21) / det, (d00 * d21 - d01 * d20) / det
        w = np.stack([1.0 - u - v, u, v], 1)[:, :, None]
        normals = (w * self.N[tri]).sum(1)
        length = np.linalg.norm(normals, axis=1, keepdims=True)
        normals = np.where(length > 1e-9, normals / np.maximum(length, 1e-9), self.face_normals[faces])
        return np.asarray(closest, np.float64), normals

    # ------------------------------------------------------------------------------------------- plates
    def clip(self, field: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """The part of the skin where `field` < 0, cut along its level curve. Returns V, F and normals."""
        inside = field < 0.0
        a, b = self.E[:, 0], self.E[:, 1]
        cross = inside[a] != inside[b]
        t = np.zeros(len(self.E))
        gap = field[a] - field[b]
        t[cross] = np.clip(field[a][cross] / gap[cross], 1e-3, 1.0 - 1e-3)
        added = np.full(len(self.E), -1, np.int64)
        added[cross] = len(self.V) + np.arange(cross.sum())
        w = t[cross][:, None]
        V = np.vstack([self.V, self.V[a[cross]] * (1.0 - w) + self.V[b[cross]] * w])
        N = np.vstack([self.N, self.N[a[cross]] * (1.0 - w) + self.N[b[cross]] * w])
        N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-9)
        corner = inside[self.F]
        count = corner.sum(1)
        parts = [self.F[count == 3]]
        rows = np.flatnonzero(count == 1)
        if len(rows):
            j = corner[rows].argmax(1)
            ab, ca = added[self.FE[rows, (j + 2) % 3]], added[self.FE[rows, (j + 1) % 3]]
            parts.append(np.stack([self.F[rows, j], ab, ca], 1))
        rows = np.flatnonzero(count == 2)
        if len(rows):
            j = (~corner[rows]).argmax(1)       # corner j is outside
            f1, f2 = self.F[rows, (j + 1) % 3], self.F[rows, (j + 2) % 3]
            ab, ca = added[self.FE[rows, (j + 2) % 3]], added[self.FE[rows, (j + 1) % 3]]
            parts += [np.stack([ab, f1, f2], 1), np.stack([ab, f2, ca], 1)]
        F = np.vstack(parts) if parts else np.zeros((0, 3), np.int64)
        if not len(F):
            return np.zeros((0, 3)), F, np.zeros((0, 3))
        used = np.zeros(len(V), bool)
        used[F.ravel()] = True
        remap = np.cumsum(used) - 1
        return V[used], remap[F], N[used]

    # ------------------------------------------------------------------------------------------- line fields
    def face_tensors(self, tensor: np.ndarray) -> np.ndarray:
        """Per-face (lengthwise, crosswise) directions of a per-vertex 3x3 line tensor: the eigenvectors of its smallest
        and middle eigenvalues within each face's plane."""
        T = tensor[self.F].mean(1)
        T = 0.5 * (T + T.transpose(0, 2, 1))
        n = self.face_normals
        scale = np.trace(T, axis1=1, axis2=2)[:, None, None]
        M = T + (np.abs(scale) * 10.0 + 1e-9) * (n[:, :, None] * n[:, None, :])
        _, vectors = np.linalg.eigh(M)
        return vectors[:, :, :2].transpose(0, 2, 1)     # (faces, 2, 3): ascending eigenvalue

    def form_tensor(self, passes: int) -> np.ndarray:
        """A line tensor from the principal curvatures, large across the direction the surface bends most."""
        c = self.curvature()
        k1, k2 = np.nan_to_num(c["k1"]), np.nan_to_num(c["k2"])
        cap = float(np.percentile(np.abs(k1), 98.0)) or 1.0
        k1, k2 = np.clip(k1, -cap, cap), np.clip(k2, -cap, cap)
        T = (k1 ** 2)[:, None, None] * (c["d1"][:, :, None] * c["d1"][:, None, :]) \
            + (k2 ** 2)[:, None, None] * (c["d2"][:, :, None] * c["d2"][:, None, :])
        return self.smooth_tensor(T, passes)

    def smooth_tensor(self, T: np.ndarray, passes: int) -> np.ndarray:
        shape = T.shape
        flat = T.reshape(len(T), -1)
        for _ in range(int(passes)):
            flat = 0.5 * flat + 0.5 * (self.average @ flat)
        return flat.reshape(shape)

    def walk(self, faces: np.ndarray, start: np.ndarray, direction: np.ndarray, field: np.ndarray, limit: float,
             turn: float = 0.5, progress=None) -> tuple[np.ndarray, np.ndarray]:
        """Walk many streamlines at once through a sign-less per-face direction field, crossing one triangle per round.
        Returns (walker of each point, points), in walking order. `turn`: cos of the sharpest turn allowed at an edge."""
        F, V, n = self.F, self.V, self.face_normals
        if not hasattr(self, "_walk_data"):
            corner = [V[F[:, (j + 1) % 3]] for j in range(3)]
            inward = [cv._unit(np.cross(n, V[F[:, (j + 2) % 3]] - V[F[:, (j + 1) % 3]])) for j in range(3)]
            neighbour = np.full((len(F), 3), -1, np.int64)
            for j in range(3):
                e = self.FE[:, j]
                first, second = self.EF[e, 0], self.EF[e, 1]
                neighbour[:, j] = np.where(first == np.arange(len(F)), second, first)
            self._walk_data = (corner, inward, neighbour)
        corner, inward, neighbour = self._walk_data
        ids = np.arange(len(faces))
        f, p, previous = np.asarray(faces, np.int64).copy(), np.asarray(start, np.float64).copy(), direction.copy()
        travelled, idle = np.zeros(len(f)), np.zeros(len(f), np.int32)
        walkers, points = [ids.copy()], [p.copy()]
        for round_ in range(200_000):
            if not len(f):
                break
            d = field[f]
            sign = np.where(cv._dot(d, previous) < 0.0, -1.0, 1.0)
            d = d * sign[:, None]
            alive = cv._dot(d, previous) >= turn
            times = np.full((len(f), 3), np.inf)
            for j in range(3):
                side = cv._dot(p - corner[j][f], inward[j][f])
                rate = cv._dot(d, inward[j][f])
                going = rate < -1e-12
                times[going, j] = np.maximum(side[going], 0.0) / -rate[going]
            j = times.argmin(1)
            row = np.arange(len(f))
            t = times[row, j]
            alive &= np.isfinite(t)
            t = np.where(np.isfinite(t), t, 0.0)
            p = p + d * t[:, None]
            travelled += t
            idle = np.where(t < 1e-9, idle + 1, 0)
            walkers.append(ids.copy())
            points.append(p.copy())
            nxt = neighbour[f, j]
            alive &= (nxt >= 0) & (travelled < limit) & (idle < 6)
            ids, f, p, previous, travelled, idle = (ids[alive], nxt[alive], p[alive], d[alive], travelled[alive],
                                                    idle[alive])
            if progress is not None and round_ % 64 == 0:
                progress()
        return np.concatenate(walkers), np.vstack(points)


def shell(V: np.ndarray, F: np.ndarray, N: np.ndarray, thickness: float) -> tuple[np.ndarray, np.ndarray]:
    """A surface patch thickened inward (against its normals) into a closed plate with walls along its cut edges."""
    count = len(V)
    outer, inner = V, V - N * thickness
    directed = np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    key = np.minimum(directed[:, 0], directed[:, 1]) * count + np.maximum(directed[:, 0], directed[:, 1])
    unique, inverse, seen = np.unique(key, return_inverse=True, return_counts=True)
    rim = directed[seen[inverse] == 1]
    walls = np.vstack([np.stack([rim[:, 1], rim[:, 0], rim[:, 0] + count], 1),
                       np.stack([rim[:, 1], rim[:, 0] + count, rim[:, 1] + count], 1)]) if len(rim) else \
        np.zeros((0, 3), np.int64)
    return np.vstack([outer, inner]), np.vstack([F, F[:, ::-1] + count, walls])
