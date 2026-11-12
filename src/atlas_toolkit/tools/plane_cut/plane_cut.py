"""Pure plane geometry, clipping and cap refinement; no Qt or file I/O."""
from __future__ import annotations

import numpy as np
from atlas_toolkit.geom.plane import normal_angles, plane, screen_plane  # preserve the original import surface


def cap_mask(mesh, origin, normal):
    eps = max(float(np.ptp(mesh.vertices, axis=0).max()) * 1e-8, 1e-10)
    d = (mesh.vertices - origin) @ normal
    return np.all(np.abs(d[mesh.faces]) < eps, axis=1)


def refine_cap(mesh, origin, normal, target, progress=None, limit=200_000):
    """Split only interior cap edges, conformingly. The surface and cap boundary never move.

    Refining every marked edge on both adjacent triangles prevents T junctions. Boundary edges may exceed the
    target, and a face budget prevents tiny requested sizes from exhausting memory. Returns (mesh, limited).
    """
    import trimesh
    mask = cap_mask(mesh, origin, normal)
    cap, surface = mesh.faces[mask].copy(), mesh.faces[~mask].copy()
    vertices = mesh.vertices.copy()
    limited = False
    for iteration in range(12):
        if progress:
            progress(0.45 + iteration * 0.025, "refining cut fill")
        edges = np.sort(cap[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1)
        unique, inv, counts = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
        lengths = np.linalg.norm(vertices[unique[:, 1]] - vertices[unique[:, 0]], axis=1)
        selected = (counts == 2) & (lengths > target * 1.05)
        if not selected.any():
            break
        if len(cap) + int(np.sum(selected[inv])) > limit:
            limited = True
            break
        mid = np.full(len(unique), -1, dtype=np.int64)
        mid[selected] = np.arange(selected.sum()) + len(vertices)
        vertices = np.vstack((vertices, vertices[unique[selected]].mean(axis=1)))
        mids = mid[inv].reshape(-1, 3)
        refined = []
        for tri, ms in zip(cap, mids):
            count = np.count_nonzero(ms >= 0)
            if not count:
                refined.append(tri)
                continue
            if count == 1:
                k = int(np.flatnonzero(ms >= 0)[0])
                a, b, c = np.roll(tri, -k)
                x = ms[k]
                refined.extend(((a, x, c), (x, b, c)))
            elif count == 2:
                k = (int(np.flatnonzero(ms < 0)[0]) + 1) % 3
                a, b, c = np.roll(tri, -k)
                x, y, _ = np.roll(ms, -k)
                refined.extend(((b, y, x), (a, x, c), (x, y, c)))
            else:
                a, b, c = tri
                x, y, z = ms
                refined.extend(((a, x, z), (x, b, y), (z, y, c), (x, y, z)))
        cap = np.asarray(refined, dtype=np.int64)
    else:
        limited = True
    out = trimesh.Trimesh(vertices, np.vstack((surface, cap)), process=False)
    out.remove_unreferenced_vertices()
    return out, limited


def cut(mesh, p, progress=None):
    """Return [(side-name, mesh)], plane data and warnings. Never mutates the source."""
    from atlas_toolkit.geom.cutting import best_engine
    origin, normal, _ = plane(mesh.bounds, p)
    warnings = []
    if not mesh.is_watertight and p["fill"] != "none":
        warnings.append("Input is open: only closed cut contours can be capped; existing holes remain open.")
    target = p["cap_size"] if p["fill"] == "fixed" else float(np.median(mesh.edges_unique_length))
    sides = [p["side"]]
    if p["cut_type"] == "slice":
        sides.append("negative" if sides[0] == "positive" else "positive")
    pieces = []
    for i, side in enumerate(sides):
        if progress:
            progress(0.12 + i * 0.3, "cutting " + side + " side")
        sign = 1 if side == "positive" else -1
        distances = (mesh.vertices - origin) @ (normal * sign)
        eps = max(float(np.ptp(mesh.vertices, axis=0).max()) * 1e-10, 1e-12)
        if np.max(np.abs(distances)) <= eps and i > 0:
            continue  # A wholly coplanar surface belongs to the primary side, not two duplicate pieces.
        if distances.max() <= eps and distances.min() < -eps:
            continue
        if distances.min() >= -eps:
            part = mesh.copy()
            crossed = False
        else:
            part = mesh.slice_plane(origin, normal * sign, cap=p["fill"] != "none", engine=best_engine())
            crossed = True
        if part is None or len(part.faces) == 0:
            continue
        if crossed and p["fill"] in ("remeshed", "fixed") and np.any(cap_mask(part, origin, normal)):
            part, limited = refine_cap(part, origin, normal, max(target, eps), progress)
            if limited:
                warnings.append("Cap refinement reached its safety limit; use a larger cap edge size for fewer faces.")
        if p["fill"] != "none" and mesh.is_watertight and not part.is_watertight:
            raise ValueError("The cut could not be closed reliably. Try moving the plane slightly, or choose No fill.")
        if p["cut_type"] == "slice" and p["separation"]:
            part.apply_translation(normal * sign * p["separation"] * 0.5)
        pieces.append((side, part))
    if not pieces:
        raise ValueError("The chosen side is empty. Flip the kept side or move the plane through the model.")
    if len(pieces) == 1 and p["cut_type"] == "slice":
        warnings.append("The plane does not split the model into two non-empty sides; only one piece exists.")
    return pieces, (origin, normal), warnings
