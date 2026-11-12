"""
cubic_replicator.py - Tile copies of a mesh on a 3D grid, each optionally flipped and turned at random
(from "STL Cubic Replicator v2").

The step between copies is the model's bounding box plus the spacing; with `rotation_safe` it is the bounding
sphere's diameter instead, so copies turned at random can never touch. Randomness is seeded, so a result can be
made again (v2 used Python's unseeded random). Copies are placed exactly as v2 placed them: centred on the
centroid, flipped, turned about X then Y then Z, moved to the grid cell.

Outputs <name>_replicated_<x>x<y>x<z>.stl (signed atlas-toolkit:cubic-replicator).
"""
from __future__ import annotations

import numpy as np

MAX_FACES = 40_000_000
AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def replicate(mesh, repeats=(1, 1, 1), spacing=(0.0, 0.0, 0.0), rotate=(False, False, False), max_angle=45.0,
              flip=(False, False, False), seed: int = 0, rotation_safe: bool = False, progress=None):
    import trimesh
    from trimesh.transformations import rotation_matrix
    nx, ny, nz = (max(int(r), 1) for r in repeats)
    count = nx * ny * nz
    if count * len(mesh.faces) > MAX_FACES:
        raise ValueError(f"{count} copies × {len(mesh.faces):,} faces is too many; use fewer copies or decimate the "
                         f"model first".replace(",", " "))
    rng = np.random.default_rng(int(seed))
    extents = mesh.bounding_box.extents
    if rotation_safe:
        extents = np.full(3, 2.0 * float(np.linalg.norm(mesh.vertices - mesh.centroid, axis=1).max()))
    offset = extents + np.asarray(spacing, dtype=float)
    centroid = mesh.centroid
    base_v = mesh.vertices - centroid
    faces = mesh.faces
    verts, all_faces = [], []
    done = 0
    for x in range(nx):
        for y in range(ny):
            for z in range(nz):
                M = np.eye(4)
                scale = [(-1.0 if (flip[i] and rng.random() < 0.5) else 1.0) for i in range(3)]
                M = np.diag(scale + [1.0]) @ M
                for i in range(3):
                    if rotate[i]:
                        angle = np.deg2rad(rng.uniform(-max_angle, max_angle))
                        M = rotation_matrix(angle, AXES[i]) @ M
                v = base_v @ M[:3, :3].T + (np.array([x, y, z]) * offset + centroid)
                f = faces[:, ::-1] if np.linalg.det(M[:3, :3]) < 0 else faces   # a mirror keeps normals outward
                all_faces.append(f + sum(len(a) for a in verts))
                verts.append(v)
                done += 1
                if progress and (done % 8 == 0 or done == count):
                    progress(0.1 + 0.8 * done / count, f"copy {done} of {count}")
    return trimesh.Trimesh(vertices=np.concatenate(verts), faces=np.concatenate(all_faces), process=False)


def suggest_spacing(extents) -> tuple:
    """v2's suggestion: 5% of the model's size on each axis."""
    return tuple(float(e) * 0.05 for e in extents)
