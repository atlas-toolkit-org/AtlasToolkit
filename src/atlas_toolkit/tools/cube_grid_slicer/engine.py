"""Toolkit adapter for the grid slicer."""
from __future__ import annotations

from pathlib import Path

from atlas_toolkit.core import Stat
from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import cube_grid_slicer as cgs


def run(ctx, src: Path, dst: Path | None, p):
    m = gm.load_mesh(src)
    if not m.is_watertight:
        ctx.warn("the model is not watertight: some cut faces may not be capped")
    relative = p.gap_mode == "percent"
    gap = p.gap / 100.0 if relative else p.gap
    parts, cells = cgs.slice_grid(m, p.n, gap, relative, ctx.progress)
    if p.shuffle > 0 or p.shift > 0:
        ctx.progress(0.88, "shuffling")
        _, cell, gap_abs = cgs.grid(m, p.n, gap, relative)
        cells = cgs.shuffle(parts, cells, cell + gap_abs, cell, p.shuffle / 100.0, p.within, p.shift / 100.0,
                            p.shift_axes, p.seed)
    out = cgs.combine(parts)
    extra = []
    if dst is not None and p.separate:
        folder = dst.with_name(f"{src.stem}_grid{p.n}_blocks")
        folder.mkdir(parents=True, exist_ok=True)
        for part, (i, j, k) in zip(parts, cells):
            path = folder / f"{src.stem}_block_{i}_{j}_{k}{dst.suffix}"
            n = 1
            while str(path) in extra:   # a cell can hold several pieces (concave models)
                path = folder / f"{src.stem}_block_{i}_{j}_{k}-{n}{dst.suffix}"
                n += 1
            gm.save_mesh(part, path, TOOL)
            extra.append(str(path))
    gap_text = f"{p.gap:g}% of the smallest cell edge" if relative else f"{p.gap:g} units"
    report = [src.name, f"  grid       {p.n} × {p.n} × {p.n}, gap {gap_text}",
              f"  blocks     {len(parts)} pieces in {len(set(cells))} cells"]
    if p.shuffle > 0 or p.shift > 0:
        within = {"all": "anywhere", "layer": "in their layer", "column": "in their column"}[p.within]
        report.append(f"  shuffle    {p.shuffle:g}% trade places {within}, shift up to {p.shift:g}% of a cell "
                      f"along {' '.join(p.shift_axes.upper())}, seed {p.seed}")
    stats = [Stat("blocks", "Blocks", len(parts))]
    return gm.result(ctx, TOOL, m, out, dst, stats, report, extra)
