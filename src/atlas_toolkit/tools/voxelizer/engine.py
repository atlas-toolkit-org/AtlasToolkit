"""Toolkit adapter for the voxelizer."""
from __future__ import annotations

from pathlib import Path

from atlas_toolkit.core import Stat
from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import voxelizer as vx


def edge_for(m, p) -> float:
    return p.edge if p.size_by == "edge" else float(m.extents.max()) / max(int(p.across), 1)


def run(ctx, src: Path, dst: Path | None, p):
    m = gm.load_mesh(src)
    src_faces = m.copy()
    edge = edge_for(m, p)
    gap = edge * p.gap / 100.0
    ctx.log(f"voxel edge {edge:.5g} units, gap {gap:.4g}")
    out, n = vx.voxelize(m, edge, gap, p.shape, p.fill, p.sphere_detail, p.sections, progress=ctx.progress)
    stats = [Stat("voxels", "Voxels", n), Stat("edge", "Voxel edge", edge, "units", fmt="{:.4g}")]
    report = [f"{src.name}",
              f"  voxels     {p.shape}s, edge {edge:.5g}, gap {gap:.4g} ({p.gap:g}% of the edge)"
              + (", filled" if p.fill else ", surface shell")]
    return gm.result(ctx, TOOL, src_faces, out, dst, stats, report)
