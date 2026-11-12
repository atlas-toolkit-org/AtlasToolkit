"""Mesh-bound masks and feathered paint influence; no Qt dependencies."""
import numpy as np
import trimesh
from atlas_toolkit.tools.mesh_surface_eject.engine import selection_text
from atlas_toolkit.tools.mesh_surface_eject.mesh_surface_eject import encode_selection, decode_selection


def export_safe(mesh, height, ctx):
    """Reuse the existing bounded solid stabilizer for edges below file precision."""
    from atlas_toolkit.tools.mesh_surface_eject.connectors import from_arrays, stabilize
    from atlas_toolkit.tools.mesh_surface_eject.mesh_surface_eject import to_mesh
    ctx.check_cancel()
    try:
        body, precision = stabilize(from_arrays(mesh.vertices, mesh.faces), height)
    except ValueError as exc:
        raise ValueError("Painted surface could not be stabilized for reliable export. Increase Paint feather or reduce motion.") from exc
    ctx.check_cancel()
    return to_mesh(body), precision


class PaintedInfluence:
    """Continuous distance-difference field, zero on every unpainted source face.

    Distances use the complete triangle surfaces, not centroids or viewer proxies.
    All-selected is exactly one. Query in cancellable bounded batches.
    """
    def __init__(self, mesh, selected, feather, ctx):
        selected = np.asarray(selected, bool)
        self.mask = selected.copy()
        self.feather, self.ctx = float(feather), ctx
        self.all_selected = bool(selected.all())
        self.selected = trimesh.Trimesh(mesh.vertices, mesh.faces[selected], process=False)
        self.unselected = trimesh.Trimesh(mesh.vertices, mesh.faces[~selected], process=False)

    def __call__(self, vertices):
        self.ctx.check_cancel()
        if self.all_selected:
            return np.ones(len(vertices))
        weights = []
        for start in range(0, len(vertices), 2048):
            self.ctx.check_cancel()
            points = np.asarray(vertices[start:start+2048])
            ds = trimesh.proximity.closest_point(self.selected, points)[1]
            du = trimesh.proximity.closest_point(self.unselected, points)[1]
            t = np.clip((du-ds-1e-10)/self.feather, 0., 1.)
            weights.append(t*t*(3-2*t))
        return np.concatenate(weights) if weights else np.zeros(0)


def protect(rest, target, faces, ctx):
    """Limit offending vertex neighbourhoods without weakening the whole painted area.

    Return displacement-weighted retained strength, not a global multiplier.
    This guards local orientation; it does not certify distant self-contact.
    """
    original = rest[faces]
    normals = np.cross(original[:, 1]-original[:, 0], original[:, 2]-original[:, 0])
    norm2 = np.einsum("ij,ij->i", normals, normals)
    valid = norm2 > 1e-28
    displacement = target-rest
    sizes = np.linalg.norm(displacement, axis=1)
    scale = np.ones(len(rest))
    for _ in range(64):
        ctx.check_cancel()
        result = rest+displacement*scale[:, None]
        tri = result[faces]
        changed = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
        dot = np.einsum("ij,ij->i", normals, changed)
        bad = valid & (dot <= .02*norm2)
        if not bad.any():
            return result, float(np.dot(sizes, scale)/max(float(sizes.sum()), 1e-30)) if sizes.any() else 1.
        scale[np.unique(faces[bad])] *= .6
    # Rare tightly folded transitions still get a valid final fallback, after
    # preserving as much of the spatially varying solution as possible.
    for _ in range(18):
        ctx.check_cancel()
        scale *= .5
        result = rest+displacement*scale[:, None]
        tri = result[faces]
        changed = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
        if np.all(np.einsum("ij,ij->i", normals, changed)[valid] > .02*norm2[valid]):
            return result, float(np.dot(sizes, scale)/max(float(sizes.sum()), 1e-30)) if sizes.any() else 1.
    return rest.copy(), 0.
