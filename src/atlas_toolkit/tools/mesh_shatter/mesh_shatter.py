"""
mesh_shatter.py - Break a mesh's surface into shards and blow them apart (from "Mesh shatter v3").

1. K seed faces by farthest-point sampling of face centroids (seeded).
2. Every face goes to its nearest seed along the surface (multi-source Dijkstra over face adjacency). Crossing a
   sharp edge costs more (edge_sensitivity), so cracks follow the model's creases.
3. Shards move away from the model's centroid by ejection × bbox diagonal, plus random jitter.
4. Optional per-shard perturbation: random turn (up to 120° × strength), shift and mirror.

Changes from v3: trimesh 5 compatible; Dijkstra runs in scipy (C) instead of a Python heap; faces no seed can
reach (separate parts of the model) become shards of their own instead of disappearing; a mirrored shard is no
longer turned inside out (trimesh already reverses the winding of a reflection, v3 reversed it again). With the
same seed and settings, shards match v3's.

Outputs <name>_shattered_<K>pcs.obj (signed atlas-toolkit:mesh-shatter), optionally one file per shard.
"""
from __future__ import annotations

import numpy as np


def bbox_diagonal(mesh) -> float:
    return float(np.linalg.norm(mesh.extents))


def farthest_point_sampling(points: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    n = points.shape[0]
    if k >= n:
        return np.arange(n)
    first = int(rng.integers(0, n))
    selected = [first]
    dists = np.linalg.norm(points - points[first], axis=1)
    for _ in range(1, k):
        i = int(np.argmax(dists))
        selected.append(i)
        dists = np.minimum(dists, np.linalg.norm(points - points[i], axis=1))
    return np.array(selected, dtype=int)


def adjacency_graph(mesh, edge_sensitivity: float = 1.0):
    """Sparse face graph: centroid distance × (1 + sensitivity × dihedral angle / π), as v3."""
    from scipy.sparse import coo_matrix
    n = len(mesh.faces)
    pairs = mesh.face_adjacency
    if pairs is None or len(pairs) == 0:
        return coo_matrix((n, n)).tocsr()
    c = mesh.triangles_center
    w = np.linalg.norm(c[pairs[:, 0]] - c[pairs[:, 1]], axis=1) * (1.0 + edge_sensitivity * mesh.face_adjacency_angles / np.pi)
    w = np.maximum(w, 1e-12)
    rows = np.concatenate([pairs[:, 0], pairs[:, 1]])
    cols = np.concatenate([pairs[:, 1], pairs[:, 0]])
    return coo_matrix((np.concatenate([w, w]), (rows, cols)), shape=(n, n)).tocsr()


def labels_from_seeds(graph, seeds: np.ndarray) -> np.ndarray:
    """Index of the nearest seed for every face; faces no seed reaches get one label per connected part."""
    from scipy.sparse.csgraph import connected_components, dijkstra
    _, _, sources = dijkstra(graph, directed=False, indices=seeds, min_only=True, return_predecessors=True)
    lookup = -np.ones(graph.shape[0], dtype=int)
    lookup[seeds] = np.arange(len(seeds))
    labels = np.where(sources >= 0, lookup[np.maximum(sources, 0)], -1)
    orphan = labels < 0
    if orphan.any():
        _, comp = connected_components(graph, directed=False)
        extra = {c: len(seeds) + i for i, c in enumerate(np.unique(comp[orphan]))}
        labels[orphan] = [extra[c] for c in comp[orphan]]
    return labels


def shards_from_labels(mesh, labels: np.ndarray) -> list:
    import trimesh
    shards = []
    faces = np.arange(len(mesh.faces))
    for k in range(int(labels.max()) + 1):
        sel = faces[labels == k]
        if len(sel) == 0:
            continue
        sub = mesh.submesh([sel], append=True, repair=False)
        if isinstance(sub, list):
            sub = trimesh.util.concatenate(sub)
        sub.remove_unreferenced_vertices()
        shards.append(sub)
    return shards


def segment(mesh, num_pieces: int, edge_sensitivity: float, seed: int) -> list:
    rng = np.random.default_rng(int(seed))
    graph = adjacency_graph(mesh, edge_sensitivity)
    k = int(max(1, min(num_pieces, len(mesh.faces))))
    seeds = farthest_point_sampling(mesh.triangles_center, k, rng)
    shards = shards_from_labels(mesh, labels_from_seeds(graph, seeds))
    return [shards[i] for i in np.argsort(-np.array([s.area for s in shards]), kind="stable")]


def explode(shards: list, centroid, diag: float, ejection: float, jitter: float, rng) -> list:
    base = float(ejection) * (diag if diag > 0 else 1.0)
    jmag = float(jitter) * base
    for s in shards:
        d = s.centroid - centroid
        norm = np.linalg.norm(d)
        if norm < 1e-12:
            d = rng.normal(size=3)
            norm = np.linalg.norm(d)
        j = rng.normal(size=3)
        jn = np.linalg.norm(j)
        j = j / jn * (rng.random() * jmag) if jn > 1e-12 else np.zeros(3)
        s.apply_translation(d / norm * base + j)
    return shards


def perturb(shards: list, strength: float, diag: float, rng) -> list:
    """Random turn about a random axis through the shard, optional mirror about its centroid, small shift.
    Same random draws, in the same order, as v3."""
    from trimesh.transformations import rotation_matrix
    strength = max(0.0, float(strength))
    if strength <= 1e-8:
        return shards
    max_angle = np.deg2rad(120.0) * strength
    max_shift = 0.10 * strength * diag
    mirror_p = 0.5 * strength
    for s in shards:
        c = s.centroid
        axis = rng.normal(size=3)
        an = np.linalg.norm(axis)
        axis = axis / an if an > 1e-12 else np.array([1.0, 0.0, 0.0])
        R = rotation_matrix((rng.random() * 2.0 - 1.0) * max_angle, axis, point=c)
        if rng.random() < mirror_p:
            S = np.eye(4)
            S[int(rng.integers(0, 3))] *= -1.0
            S[3, 3] = 1.0
            T1, T2 = np.eye(4), np.eye(4)
            T1[:3, 3], T2[:3, 3] = -c, c
            s.apply_transform(T2 @ S @ T1)     # trimesh reverses the winding of a reflection itself
        s.apply_transform(R)
        t = rng.normal(size=3)
        tn = np.linalg.norm(t)
        t = t / tn * (rng.random() * max_shift) if tn > 1e-12 else np.zeros(3)
        s.apply_translation(t)
    return shards


def shatter(mesh, num_pieces: int = 25, ejection: float = 0.25, edge_sensitivity: float = 1.0, jitter: float = 0.15,
            perturb_strength: float = 0.20, seed: int = 42, progress=None) -> list:
    """Shards in their final places (v3's pipeline, one generator for explode and perturb as in v3)."""
    if len(mesh.faces) < 2:
        raise ValueError("the mesh has too few faces to shatter")
    if progress:
        progress(0.1, "segmenting")
    shards = segment(mesh, num_pieces, edge_sensitivity, seed)
    rng = np.random.default_rng(int(seed))
    diag = bbox_diagonal(mesh)
    if progress:
        progress(0.7, f"moving {len(shards)} shards")
    shards = explode(shards, mesh.centroid.copy(), diag, ejection, jitter, rng)
    return perturb(shards, perturb_strength, diag, rng)
