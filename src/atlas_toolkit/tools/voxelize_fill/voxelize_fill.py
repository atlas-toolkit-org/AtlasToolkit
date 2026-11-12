"""Volume classification, block packing and boundary extraction; no UI or file writes.

Cell-centre winding occupancy preserves oriented cavities. Artistic filtering is a
separate step. Full cubes, adaptive cubes and runs represent the same occupied
volume at zero gap. No surface-shell fill or per-cell Boolean is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
import math

import numpy as np

from atlas_toolkit.geom.surface import Surface

MAX_CELLS = 8_000_000
MAX_TRIANGLES = 4_000_000
CHUNK = 131_072
CORNERS = np.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                    (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], dtype=np.int64)
# Outward quads: -X, +X, -Y, +Y, -Z, +Z.
QUADS = np.array([(0, 4, 7, 3), (1, 2, 6, 5), (0, 1, 5, 4),
                  (3, 7, 6, 2), (0, 3, 2, 1), (4, 5, 6, 7)], dtype=np.int64)


def notify(progress, value, message):
    if progress:
        progress(value, message)


@dataclass
class Grid:
    origin: np.ndarray             # lower corner, original model units
    pitch: float
    shape: tuple[int, int, int]

    @property
    def size(self):
        return math.prod(self.shape)

    def centres(self, indices):
        return self.origin + (np.asarray(indices) + 0.5) * self.pitch


@dataclass
class Blocks:
    lower: np.ndarray              # lower corner in cell coordinates
    sizes: np.ndarray              # three side lengths in cells
    groups: np.ndarray             # exploded slab ID (zero otherwise)


def make_grid(bounds, pitch):
    bounds = np.asarray(bounds, dtype=float)
    if bounds.shape != (2, 3) or not np.isfinite(bounds).all():
        raise ValueError("The model bounds must be finite.")
    ext = bounds[1] - bounds[0]
    if not np.isfinite(pitch) or pitch <= 0 or np.any(ext <= 0):
        raise ValueError("Cell edge must be positive and the model must have extent on all three axes.")
    # Python integers avoid overflow before the allocation guard. Centre the grid
    # on the model so translation/scale change placement, not cell decisions.
    shape = tuple(max(1, math.ceil(float(e / pitch) - 1e-10)) for e in ext)
    total = math.prod(shape)
    if total > MAX_CELLS:
        raise ValueError(f"The volume needs {total:,} grid cells (limit {MAX_CELLS:,}). "
                         "Lower Voxels across or increase Cell edge.")
    origin = bounds[0] + (ext - np.asarray(shape) * pitch) / 2
    return Grid(origin, float(pitch), shape)


def classify(mesh, grid, policy="infer", progress=None):
    import igl
    import trimesh
    if policy not in ("infer", "closed"):
        raise ValueError("Unknown input policy.")
    if len(mesh.faces) == 0 or not np.isfinite(mesh.vertices).all():
        raise ValueError("The model must have finite vertices and faces.")
    warnings = []
    if not mesh.is_watertight:
        if policy == "closed":
            raise ValueError("Closed mesh only requires a watertight input. Repair the mesh or select Infer scan interior.")
        warnings.append("Open input: the interior is inferred from winding numbers; missing surfaces can change the result.")
    m = mesh
    if not mesh.is_winding_consistent:
        m = mesh.copy()
        trimesh.repair.fix_winding(m)
        warnings.append("Inconsistent adjacent face winding was repaired on a working copy; check inferred scan regions.")
    # Normalisation protects tiny and large source coordinates. Global inversion
    # is harmless under abs(w); relative nested-shell orientation is preserved.
    centre = np.mean(mesh.bounds, axis=0)
    scale = float(mesh.extents.max())
    V = np.ascontiguousarray((m.vertices - centre) / scale, dtype=np.float64)
    F = np.ascontiguousarray(m.faces, dtype=np.int64)
    occupied = np.empty(grid.size, dtype=bool)
    uncertain = 0
    for start in range(0, grid.size, CHUNK):
        stop = min(start + CHUNK, grid.size)
        notify(progress, 0.05 + 0.43 * start / grid.size, "classifying solid interior")
        ijk = np.column_stack(np.unravel_index(np.arange(start, stop), grid.shape))
        P = (grid.origin - centre) / scale + (ijk + 0.5) * (grid.pitch / scale)
        winding = np.abs(np.asarray(igl.fast_winding_number(V, F, np.ascontiguousarray(P))).reshape(-1))
        if not np.isfinite(winding).all():
            raise ValueError("Interior classification returned non-finite winding numbers; check degenerate input faces.")
        occupied[start:stop] = winding > 0.5
        uncertain += int(np.count_nonzero(np.abs(winding - 0.5) < 0.05))
    if uncertain:
        warnings.append(f"{uncertain:,} cell centres are near the winding threshold; inspect thin or open regions.")
    occupied = occupied.reshape(grid.shape)
    if not occupied.any():
        raise ValueError("No interior cell centres were found. Increase Voxels across or check that the source encloses a volume.")
    return occupied, warnings


def present(occupied, grid, p, progress=None):
    """Return filtered occupancy and slab groups, without changing the baseline."""
    from scipy import ndimage
    kept = occupied.copy()
    effect = p["effect"]
    axis = "xyz".index(p["axis"])
    notify(progress, 0.5, "shaping occupied volume")
    if effect == "bands":
        # Explicit exterior padding is essential for an entirely occupied box.
        distance = ndimage.distance_transform_edt(np.pad(occupied, 1))[1:-1, 1:-1, 1:-1]
        band = np.floor(np.maximum(0., distance - 1.) / p["band_cells"]).astype(np.int32)
        kept &= band % 2 == 0
    elif effect == "erode":
        amount = p["erosion"] / 100.
        if amount >= 1.:
            kept[:] = False
        elif amount > 0.:
            rng = np.random.default_rng(p["seed"])
            noise = rng.standard_normal(grid.shape, dtype=np.float32)
            noise = ndimage.gaussian_filter(noise, sigma=2., mode="reflect")
            noise /= max(float(np.max(np.abs(noise))), 1e-12)
            amplitude = 0.3 * p["roughness"] / 100.
            shape = [1, 1, 1]
            shape[axis] = grid.shape[axis]
            front_coordinate = ((np.arange(grid.shape[axis]) + 0.5) / grid.shape[axis]).reshape(shape)
            threshold = (1. + 2 * amplitude) * (1. - amount) - amplitude
            kept &= front_coordinate + amplitude * noise < threshold
    elif effect not in ("full", "explode"):
        raise ValueError("Unknown volume effect.")
    if p["cutaway"]:
        normal = np.asarray(p["cut_normal"], dtype=float)
        norm = float(np.linalg.norm(normal))
        if not np.isfinite(norm) or norm < 1e-12:
            raise ValueError("Plane normal cannot be zero.")
        normal /= norm
        # Grid-relative projections keep the test stable far from world origin.
        extent = np.asarray(grid.shape) * grid.pitch
        corner_projection = (CORNERS * extent) @ normal
        lo, hi = float(corner_projection.min()), float(corner_projection.max())
        threshold = lo + p["cut_position"] / 100. * (hi - lo)
        projection = sum((np.arange(n).reshape(tuple(n if j == a else 1 for j in range(3))) + 0.5)
                         * grid.pitch * normal[a] for a, n in enumerate(grid.shape))
        kept &= projection <= threshold if p["cut_side"] == "lower" else projection >= threshold
    if not kept.any():
        raise ValueError("The presentation removes every voxel. Reduce Erosion or move/flip the cutaway plane.")
    groups = np.zeros(grid.shape, dtype=np.int32)
    if effect == "explode" and p["separation"] > 0.:
        shape = [1, 1, 1]
        shape[axis] = grid.shape[axis]
        groups[:] = (np.arange(grid.shape[axis]) // p["layer_cells"]).reshape(shape)
    groups[~kept] = -1
    return kept, groups


def check_faces(quads):
    triangles = int(quads) * 2
    if triangles > MAX_TRIANGLES:
        raise ValueError(f"The scene needs {triangles:,} triangles (limit {MAX_TRIANGLES:,}). "
                         "Lower Voxels across, choose Adaptive cubes/Directional bars, or use Joined volume.")


def pack(groups, layout="cubes", levels=3, axis=2, progress=None):
    """Pack cells without crossing empty space or exploded-layer boundaries."""
    if layout == "cubes":
        count = int(np.count_nonzero(groups >= 0))
        check_faces(count * 6)
        lower = np.argwhere(groups >= 0)
        return Blocks(lower, np.ones_like(lower), groups[tuple(lower.T)])
    if layout == "runs":
        g = np.moveaxis(groups, axis, -1)
        air = np.full(g.shape[:-1] + (1,), -1, dtype=g.dtype)
        start = (g >= 0) & (g != np.concatenate((air, g[..., :-1]), axis=-1))
        end = (g >= 0) & (g != np.concatenate((g[..., 1:], air), axis=-1))
        check_faces(int(start.sum()) * 6)
        a, b = np.argwhere(start), np.argwhere(end)
        order = [j for j in range(3) if j != axis] + [axis]
        lower = np.empty_like(a)
        lower[:, order] = a
        sizes = np.ones_like(lower)
        sizes[:, axis] = b[:, 2] - a[:, 2] + 1
        return Blocks(lower, sizes, g[tuple(a.T)])
    if layout != "adaptive":
        raise ValueError("Unknown block layout.")
    current = groups
    lowers, sizes, labels = [], [], []
    total = 0
    for level in range(levels + 1):
        notify(progress, 0.56 + 0.08 * level / max(levels, 1), "packing adaptive cubes")
        scale = 2 ** level
        if level == levels:
            emitted = current >= 0
            next_level = None
        else:
            padded = np.pad(current, tuple((0, n % 2) for n in current.shape), constant_values=-1)
            first = padded[::2, ::2, ::2]
            merge = first >= 0
            for offset in product((0, 1), repeat=3):
                merge &= padded[offset[0]::2, offset[1]::2, offset[2]::2] == first
            merged_cells = np.repeat(np.repeat(np.repeat(merge, 2, axis=0), 2, axis=1), 2, axis=2)
            emitted = (current >= 0) & ~merged_cells[tuple(slice(0, n) for n in current.shape)]
            next_level = np.where(merge, first, -1)
        count = int(emitted.sum())
        total += count
        check_faces(total * 6)
        idx = np.argwhere(emitted)
        lowers.append(idx * scale)
        sizes.append(np.full_like(idx, scale))
        labels.append(current[tuple(idx.T)])
        if next_level is None or not np.any(next_level >= 0):
            break
        current = next_level
    return Blocks(np.concatenate(lowers), np.concatenate(sizes), np.concatenate(labels))


def displacements(labels, grid, p):
    out = np.zeros((len(labels), 3), dtype=float)
    if p["effect"] == "explode" and p["separation"] > 0.:
        axis = "xyz".index(p["axis"])
        n = math.ceil(grid.shape[axis] / p["layer_cells"])
        out[:, axis] = (labels - (n - 1) / 2.) * grid.pitch * p["separation"] / 100.
    return out


def surface(vertices, quads):
    """Known planar convex quads; vectorised triangulation avoids per-face Python."""
    triangles = quads[:, [[0, 1, 2], [0, 2, 3]]].reshape(-1, 3)
    return Surface(np.asarray(vertices, dtype=float), quads, triangles,
                   np.repeat(np.arange(len(quads), dtype=np.int64), 2))


def separate_surface(blocks, grid, p, progress=None):
    count = len(blocks.lower)
    check_faces(count * 6)
    # The clearance is based on the finest cell, not on each merged block's size.
    gap = grid.pitch * p["gap"] / 100.
    vertices = np.empty((count * 8, 3), dtype=float)
    quads = np.empty((count * 6, 4), dtype=np.int64)
    for start in range(0, count, 16384):
        stop = min(start + 16384, count)
        notify(progress, 0.65 + 0.13 * start / count, "building solid blocks")
        low = grid.origin + blocks.lower[start:stop] * grid.pitch + gap / 2.
        size = blocks.sizes[start:stop] * grid.pitch - gap
        shift = displacements(blocks.groups[start:stop], grid, p)
        vertices[start * 8:stop * 8] = (low[:, None] + shift[:, None] + CORNERS * size[:, None]).reshape(-1, 3)
        quads[start * 6:stop * 6] = (QUADS + np.arange(start, stop)[:, None, None] * 8).reshape(-1, 4)
    return surface(vertices, quads)


def joined_surface(groups, grid, p, progress=None):
    """Cull shared faces, welding corners only within the same displaced group."""
    descriptors = []
    total = 0
    for face in range(6):
        axis, sign = face // 2, -1 if face % 2 == 0 else 1
        notify(progress, 0.65 + face * 0.015, "extracting occupied boundary")
        neighbour = np.full_like(groups, -1)
        target, source = [slice(None)] * 3, [slice(None)] * 3
        target[axis] = slice(1, None) if sign < 0 else slice(None, -1)
        source[axis] = slice(None, -1) if sign < 0 else slice(1, None)
        neighbour[tuple(target)] = groups[tuple(source)]
        boundary = (groups >= 0) & (groups != neighbour)
        total += int(boundary.sum())
        check_faces(total)
        descriptors.append((face, np.argwhere(boundary)))
    keys = np.empty((total * 4, 4), dtype=np.int64)
    cursor = 0
    for face, idx in descriptors:
        n = len(idx) * 4
        keys[cursor:cursor + n, :3] = (idx[:, None] + CORNERS[QUADS[face]]).reshape(-1, 3)
        keys[cursor:cursor + n, 3] = np.repeat(groups[tuple(idx.T)], 4)
        cursor += n
    notify(progress, 0.76, "welding boundary corners")
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    vertices = grid.origin + unique[:, :3] * grid.pitch + displacements(unique[:, 3], grid, p)
    return surface(vertices, inverse.reshape(-1, 4))


def build(mesh, p, progress=None):
    """Return a native quad Surface, metrics and diagnostics. Input is unchanged."""
    if not len(mesh.vertices) or not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
        raise ValueError("The model must have finite vertices and non-degenerate faces enclosing a volume.")
    pitch = float(p["edge"]) if p["size_by"] == "edge" else float(mesh.extents.max()) / p["across"]
    grid = make_grid(mesh.bounds, pitch)
    notify(progress, 0.03, f"volume grid: {' x '.join(map(str, grid.shape))}")
    occupied, warnings = classify(mesh, grid, p["input_policy"], progress)
    initial = int(occupied.sum())
    kept, groups = present(occupied, grid, p, progress)
    remaining = int(kept.sum())
    blocks = None
    if p["geometry"] == "joined":
        output = joined_surface(groups, grid, p, progress)
    elif p["geometry"] == "separate":
        blocks = pack(groups, p["layout"], p["adaptive_levels"], "xyz".index(p["bar_axis"]), progress)
        output = separate_surface(blocks, grid, p, progress)
        if p["gap"] == 0.:
            warnings.append("Separate blocks touch at zero gap and retain coincident internal faces. "
                            "Use Joined volume for a boundary-only solid or a gap to expose the blocks.")
    else:
        raise ValueError("Unknown geometry mode.")
    output.frame = mesh.metadata.get("atlas_frame")
    metrics = dict(voxels=initial, retained=remaining, removed=initial - remaining,
                   blocks=len(blocks.lower) if blocks is not None else 0,
                   grid=grid.shape, pitch=pitch, occupied_volume=initial * pitch ** 3,
                   retained_volume=remaining * pitch ** 3, quads=len(output.faces),
                   triangles=len(output.triangles), inferred=not mesh.is_watertight)
    return output, metrics, warnings
