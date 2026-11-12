"""
voxelizer.py - Rebuild a mesh from cubes, spheres or cylinders on a voxel grid (from "Voxelizer tool v1").

The surface is voxelized at `edge` (trimesh); with `fill` the inside is filled too (v1 always made a hollow
shell). Every voxel gets the same primitive, shrunk by the gap, built in one vectorised step from a single
template mesh instead of one mesh per voxel, so large grids are fast. Output faces are written as they are
(voxels are not merged), as v1 did.

Outputs <name>_vox.stl (signed atlas-toolkit:voxelizer).
"""
from __future__ import annotations

import numpy as np

MAX_FACES = 40_000_000     # past this the output would not fit in memory / no plotter pipeline wants it


def template(shape: str, size: float, sphere_detail: int = 2, sections: int = 32):
    import trimesh
    if shape == "cube":
        return trimesh.creation.box(extents=[size] * 3)
    if shape == "sphere":
        return trimesh.creation.icosphere(subdivisions=sphere_detail, radius=size / 2)
    if shape == "cylinder":
        return trimesh.creation.cylinder(radius=size / 2, height=size, sections=sections)
    raise ValueError(f"unknown voxel shape {shape!r}")


def voxel_centres(mesh, edge: float, fill: bool = False) -> np.ndarray:
    if not mesh.is_watertight:   # as v1: close small holes so the surface voxelizes cleanly
        mesh.fill_holes()
    vg = mesh.voxelized(pitch=edge)
    if fill:
        vg = vg.fill()
    return np.asarray(vg.points, dtype=np.float64), vg


def voxelize(mesh, edge: float, gap: float = 0.0, shape: str = "cube", fill: bool = False, sphere_detail: int = 2,
             sections: int = 32, progress=None):
    """Returns (mesh, voxel count). `gap` is absolute (model units), smaller than `edge`."""
    import trimesh
    if edge <= 0:
        raise ValueError("the voxel edge must be positive")
    if not 0 <= gap < edge:
        raise ValueError("the gap must be smaller than the voxel edge")
    if progress:
        progress(0.05, "voxelizing")
    pts, vg = voxel_centres(mesh, edge, fill)
    if len(pts) == 0:
        raise ValueError("no voxels: the edge is larger than the model")
    if shape == "cube" and gap == 0.0 and not fill:
        if progress:
            progress(0.6, "building cubes")
        return vg.as_boxes(), len(pts)     # v1's fast path
    tpl = template(shape, edge - gap, sphere_detail, sections)
    n_faces = len(pts) * len(tpl.faces)
    if n_faces > MAX_FACES:
        raise ValueError(f"{len(pts):,} voxels × {len(tpl.faces)} faces = {n_faces:,} faces is too many; "
                         f"use a larger edge or cubes".replace(",", " "))
    if progress:
        progress(0.5, f"placing {len(pts):,} {shape}s".replace(",", " "))
    tv, tf = np.asarray(tpl.vertices), np.asarray(tpl.faces)
    V = (tv[None, :, :] + pts[:, None, :]).reshape(-1, 3)
    F = (tf[None, :, :] + (np.arange(len(pts)) * len(tv))[:, None, None]).reshape(-1, 3)
    return trimesh.Trimesh(vertices=V, faces=F, process=False), len(pts)
