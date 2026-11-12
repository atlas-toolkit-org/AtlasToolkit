"""
cube_grid_slicer.py - Cut a mesh into an n × n × n grid of closed blocks and push them apart
(from "cube_grid_slicer-v2", solid_grid_slicer v5).

The bounding box is divided into n cells per axis; every cutting plane caps the cut so each block stays closed
(needs a watertight model for clean caps). A block is assigned to its cell by its centroid and moved away from
the grid centre by (cell index - centre) × gap. Unlike v2, a plane only cuts the parts it actually crosses, so
large grids no longer slow down as n⁴.

Shuffle (optional, seeded): a share of the blocks trade cells with each other (anywhere, within their layer or
within their column), then every block is shifted at random by up to a share of a cell, along the chosen axes.

Outputs <name>_grid<n>.stl (signed atlas-toolkit:cube-grid-slicer); optionally every block on its own.
"""
from __future__ import annotations

import numpy as np

from atlas_toolkit.geom.cutting import split


def _slice(parts: list, axis: int, value: float) -> list:
    normal = np.zeros(3)
    normal[axis] = 1.0
    origin = np.zeros(3)
    origin[axis] = value
    out = []
    for m in parts:
        lo, hi = m.bounds[0][axis], m.bounds[1][axis]
        if lo >= value or hi <= value:     # the plane misses this part: keep it whole
            out.append(m)
            continue
        below, above = split(m, origin, normal, cap=True)
        out += [piece for piece in (above, below) if piece is not None]
    return out


def grid(mesh, n: int, gap: float, relative: bool = False) -> tuple:
    """The grid's origin, cell size (per axis) and gap in model units."""
    lo, hi = mesh.bounds
    cell = (hi - lo) / n
    return lo, cell, float(gap) * float(cell.min()) if relative else float(gap)


def slice_grid(mesh, n: int, gap: float, relative: bool = False, progress=None) -> tuple[list, list]:
    """Blocks and their (i, j, k) cells. `gap` is in model units, or a fraction of the smallest cell edge when
    `relative` (v2's 'gap is % of cube', which was really a fraction)."""
    if n < 1:
        raise ValueError("n must be at least 1")
    if mesh.is_empty:
        raise ValueError("the mesh has no faces")
    lo, cell, gap_abs = grid(mesh, n, gap, relative)
    parts = [mesh.copy()]
    total, done = 3 * (n - 1), 0
    for axis in range(3):
        for k in range(1, n):
            parts = _slice(parts, axis, lo[axis] + cell[axis] * k)
            done += 1
            if progress:
                progress(0.05 + 0.8 * done / max(total, 1), f"{'XYZ'[axis]} plane {done} of {total}")
    centre = (n - 1) / 2.0
    cells = []
    for m in parts:
        ijk = np.clip(((m.centroid - lo) / cell + 1e-9).astype(int), 0, n - 1)
        m.apply_translation((ijk - centre) * gap_abs)
        cells.append(tuple(int(x) for x in ijk))
    return parts, cells


def combine(parts: list):
    import trimesh
    return trimesh.util.concatenate(parts)


GROUPS = {"all": lambda c: 0, "layer": lambda c: c[2], "column": lambda c: (c[0], c[1])}
AXES = {"xyz": (1.0, 1.0, 1.0), "xy": (1.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def shuffle(parts: list, cells: list, step, cell, share: float = 0.0, within: str = "all", shift: float = 0.0,
            axes: str = "xyz", seed: int = 0) -> list:
    """Move exploded blocks in place: `share` of them (0..1) trade cells - within a group, each chosen block takes
    the next one's cell in a random cycle, so every one of them moves - then each block shifts by up to `shift`
    (a fraction of the cell edge) along `axes`. `step` is the distance between neighbouring cells once exploded
    (cell + gap). Returns the cells the blocks now sit in."""
    rng = np.random.default_rng(int(seed))
    step = np.asarray(step, dtype=float)
    out = list(cells)
    if share > 0 and len(parts) > 1:
        chosen = [i for i in range(len(parts)) if rng.random() < share]
        groups: dict = {}
        for i in chosen:
            groups.setdefault(GROUPS[within](cells[i]), []).append(i)
        for idx in groups.values():
            if len(idx) < 2:
                continue
            order = [idx[t] for t in rng.permutation(len(idx))]
            for t, i in enumerate(order):
                out[i] = cells[order[(t + 1) % len(order)]]
        for m, a, b in zip(parts, cells, out):
            if a != b:
                m.apply_translation((np.array(b) - np.array(a)) * step)
    if shift > 0:
        mask = np.array(AXES[axes])
        for m in parts:
            m.apply_translation(rng.uniform(-1.0, 1.0, 3) * mask * float(shift) * np.asarray(cell, dtype=float))
    return out
