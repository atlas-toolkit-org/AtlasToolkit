"""Pure reflection: retain topology and correct handedness without cutting or welding."""
from __future__ import annotations

import numpy as np

from atlas_toolkit.geom.plane import plane, reflect_points


def mirror(mesh, p):
    """Reflect a copy. Reversing triangle order compensates for the reflection's negative determinant."""
    origin, normal, _ = plane(mesh.bounds, p)
    reflected = mesh.copy()
    reflected.vertices = reflect_points(mesh.vertices, origin, normal)
    reflected.faces = np.asarray(mesh.faces)[:, ::-1].copy()
    return reflected, (origin, normal)


def combine(original, reflected):
    """Keep both sets of indexed vertices and faces, including when they touch or overlap."""
    import trimesh
    return trimesh.util.concatenate((original, reflected))
