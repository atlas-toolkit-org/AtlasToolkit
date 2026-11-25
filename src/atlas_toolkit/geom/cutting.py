"""
Plane cuts that keep solids closed: shared by Grid Slicer (three families of axis planes) and Strata Sculpt (one
family of parallel planes, any direction).

Every cut is capped (trimesh triangulates the cross-section), so the pieces of a watertight model
are closed solids; where that triangulation leaves a gap (a plane grazing a vertex, slivers of an earlier cap), the
cut is made again by manifold3d, which keeps a solid closed. A cut that closes is never changed by this.
An open model still cuts, but its caps can be missing where the section is not a closed loop.
A plane only cuts the pieces it actually crosses.

Caps are triangulated by trimesh's default engine (earcut) unless `engine` says otherwise. Earcut drops the hole of a
ring-shaped section (a torus cut across), so tools that cut rings pass `best_engine()` (manifold3d, bundled).
"""
from __future__ import annotations

import bisect

import numpy as np


def best_engine() -> str | None:
    """The cap triangulation that keeps holes: "manifold" when manifold3d is installed, else trimesh's default."""
    try:
        import manifold3d  # noqa: F401
        return "manifold"
    except ImportError:              # pragma: no cover (bundled)
        return None


def split(mesh, origin, normal, cap: bool = True, engine: str | None = None) -> tuple:
    """(below, above) the plane through `origin` with `normal`; either is None when empty."""
    from .repair import closed_and_consistent
    normal = np.asarray(normal, dtype=np.float64)
    out, failure = [], None
    try:
        for side in (-normal, normal):
            piece = mesh.slice_plane(origin, side, cap=cap, engine=engine)
            out.append(piece if piece is not None and not piece.is_empty else None)
    except Exception as e:               # a cross-section the triangulation refuses
        failure = e
    if cap and (failure is not None or not all(p is None or closed_and_consistent(p.faces) for p in out)):
        native = solid_split(mesh, origin, normal)
        if native is not None:
            return native
    if failure is not None:
        raise failure
    return out[0], out[1]


def solid_split(mesh, origin, normal) -> tuple | None:
    """`split` by manifold3d in double precision, which keeps a closed solid closed: (below, above), or None when
    the mesh is not a solid it accepts or the pieces do not add up to it."""
    import trimesh
    from .repair import closed_and_consistent
    if not closed_and_consistent(mesh.faces):
        return None
    try:
        import manifold3d as mf
    except ImportError:                  # pragma: no cover (bundled)
        return None
    body = mf.Manifold(mf.Mesh64(np.array(mesh.vertices, dtype=np.float64, order="C"),
                                 np.array(mesh.faces, dtype=np.uint64, order="C")))
    if body.status() != mf.Error.NoError or body.is_empty():
        return None
    n = np.asarray(normal, dtype=np.float64)
    n = n / np.linalg.norm(n)
    above, below = body.split_by_plane(n.tolist(), float(np.dot(np.asarray(origin, dtype=np.float64), n)))
    if abs(above.volume() + below.volume() - mesh.volume) > 1e-6 * abs(mesh.volume):
        return None
    out = []
    for piece in (below, above):
        if piece.is_empty():
            out.append(None)
            continue
        data = piece.to_mesh64()
        part = trimesh.Trimesh(np.array(data.vert_properties)[:, :3], np.array(data.tri_verts).astype(np.int64),
                               process=False)
        if not closed_and_consistent(part.faces):
            return None
        out.append(part)
    return out[0], out[1]


def solid_pieces(mesh, samples: int = 24) -> list[tuple[int, object]]:
    """The connected pieces of a closed mesh as solids, [(index among its shells, piece)]. A shell that is inside out
    is the wall of a hollow (a bubble within one layer, a void a layer closes off), not material of its own: it goes
    with the smallest shell that holds it and has the volume for it, so both can be moved as one. One that nothing
    holds bounds no material and is left out. Without hollows these are simply the shells."""
    import trimesh
    shells = list(mesh.split(only_watertight=False))
    walls = [s for s in shells if s.is_watertight and s.volume < 0]
    if not walls:
        return list(enumerate(shells))
    solids = {i: [s] for i, s in enumerate(shells) if not (s.is_watertight and s.volume < 0)}
    room = {i: float(group[0].volume) for i, group in solids.items()}
    for wall in sorted(walls, key=lambda s: s.volume):
        points = _hollow_points(wall, samples)
        lo, hi = points.min(axis=0), points.max(axis=0)
        holders = [i for i, group in solids.items()
                   if room[i] > -wall.volume and (group[0].bounds[0] <= hi).all() and (group[0].bounds[1] >= lo).all()
                   and np.mean(winding(group[0], points) > 0.5) > 0.5]
        if holders:
            owner = min(holders, key=lambda i: solids[i][0].volume)
            solids[owner].append(wall)
            room[owner] += float(wall.volume)
    return [(i, trimesh.util.concatenate(group) if len(group) > 1 else group[0]) for i, group in solids.items()]


def winding(mesh, points) -> np.ndarray:
    """How many times a closed mesh goes around each point (1 inside a solid, 0 outside, -1 within an inside-out shell)."""
    out = np.empty(len(points))
    triangles = np.asarray(mesh.triangles, dtype=np.float64)
    for k, q in enumerate(points):
        a, b, c = (triangles - q).transpose(1, 0, 2)
        la, lb, lc = (np.linalg.norm(x, axis=1) for x in (a, b, c))
        out[k] = np.arctan2(np.einsum("ij,ij->i", a, np.cross(b, c)),
                            la * lb * lc + np.einsum("ij,ij->i", a, b) * lc + np.einsum("ij,ij->i", b, c) * la
                            + np.einsum("ij,ij->i", c, a) * lb).sum() / (2 * np.pi)
    return out


def folds_through_itself(mesh, samples: int = 400) -> bool:
    """Whether a closed piece crosses itself so that part of it is inside out: just outside some of its faces it goes
    -1 times around, which never happens around a simple solid. The sign of such a piece's volume says nothing."""
    ids = np.unique(np.linspace(0, len(mesh.faces) - 1, samples).astype(int))
    off = mesh.triangles_center[ids] + mesh.face_normals[ids] * 0.004 * float(mesh.extents.min())
    return bool((winding(mesh, off) < -0.5).any())


def _hollow_points(wall, samples):
    """Points in the hollow an inside-out shell bounds: off its faces, which face into it. The wall itself would not
    do, a layer's cap lies in the plane of the shell that holds it."""
    ids = np.unique(np.linspace(0, len(wall.faces) - 1, samples).astype(int))
    centres, normals = wall.triangles_center[ids], wall.face_normals[ids]
    for step in (0.1, 0.02, 0.004):
        points = centres + normals * step * float(wall.extents.min())
        within = points[winding(wall, points) < -0.5]
        if len(within) >= 3:
            return within
    return centres


def span(mesh, normal) -> tuple[float, float]:
    """The range of a mesh's vertices along `normal`."""
    d = np.asarray(mesh.vertices, dtype=np.float64) @ np.asarray(normal, dtype=np.float64)
    return float(d.min()), float(d.max())


def partition(mesh, normal, cuts, cap: bool = True, progress=None, eps: float = 1e-9,
              engine: str | None = None) -> list[tuple[int, object]]:
    """Cut a mesh at the parallel planes x·normal = c for c in `cuts` (sorted). Returns [(interval, piece)], where
    interval i lies between cuts[i-1] and cuts[i] (0: below the first cut, len(cuts): above the last); every
    interval holds at most one piece (which can have several components).

    The cuts are made by bisection (middle plane first), so every face is cut about log2(len(cuts)) times rather
    than once per plane. `progress(done, total)` is called after every cut; it may raise to cancel."""
    normal = np.asarray(normal, dtype=np.float64)
    normal = normal / np.linalg.norm(normal)
    cuts = [float(c) for c in cuts]
    if any(b < a for a, b in zip(cuts, cuts[1:])):
        raise ValueError("cuts must be sorted")
    out: list[tuple[int, object]] = []
    done = [0]
    todo = [(mesh, 0, len(cuts))]          # a piece and the cuts it may still cross: cuts[i0:i1]
    while todo:
        m, i0, i1 = todo.pop()
        lo, hi = span(m, normal)
        a = bisect.bisect_right(cuts, lo + eps, i0, i1)
        b = bisect.bisect_left(cuts, hi - eps, i0, i1)
        if a >= b:                          # no plane crosses it: it is one interval
            out.append((bisect.bisect_right(cuts, (lo + hi) / 2.0), m))
            continue
        j = (a + b) // 2
        below, above = split(m, normal * cuts[j], normal, cap, engine)
        done[0] += 1
        if progress:
            progress(done[0], len(cuts))
        if above is not None:
            todo.append((above, j + 1, b))
        if below is not None:
            todo.append((below, a, j))
    out.sort(key=lambda t: t[0])
    return out
