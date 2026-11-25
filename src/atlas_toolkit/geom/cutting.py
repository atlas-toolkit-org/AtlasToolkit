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
