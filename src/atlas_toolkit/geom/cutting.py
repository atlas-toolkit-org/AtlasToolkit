"""
Plane cuts that keep solids closed: shared by Grid Slicer (three families of axis planes) and Strata Sculpt (one
family of parallel planes, any direction).

Every cut is capped (trimesh triangulates the cross-section), so the pieces of a watertight model
are closed solids. An open model still cuts, but its caps can be missing where the section is not a closed loop.
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
    normal = np.asarray(normal, dtype=np.float64)
    out = []
    for side in (-normal, normal):
        piece = mesh.slice_plane(origin, side, cap=cap, engine=engine)
        out.append(piece if piece is not None and not piece.is_empty else None)
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
