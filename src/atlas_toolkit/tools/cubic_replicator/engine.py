"""Toolkit adapter for the cubic replicator."""
from __future__ import annotations

from pathlib import Path

from atlas_toolkit.core import Stat
from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import cubic_replicator as cr


def run(ctx, src: Path, dst: Path | None, p):
    m = gm.load_mesh(src)
    ctx.progress(0.05, "copying")
    rot = (p.rot_x, p.rot_y, p.rot_z)
    flip = (p.flip_x, p.flip_y, p.flip_z)
    out = cr.replicate(m, p.repeats, p.spacing, rot, p.max_angle, flip, p.seed, p.rotation_safe, ctx.progress)
    n = p.repeats[0] * p.repeats[1] * p.repeats[2]
    random = [a for a, on in zip("XYZ", rot) if on]
    flips = [a for a, on in zip("XYZ", flip) if on]
    report = [src.name,
              f"  copies     {p.repeats[0]} × {p.repeats[1]} × {p.repeats[2]} = {n}",
              f"  spacing    {p.spacing[0]:.4g}, {p.spacing[1]:.4g}, {p.spacing[2]:.4g} units"
              + (" (bounding sphere)" if p.rotation_safe else ""),
              f"  random     turn {'/'.join(random) or 'none'} up to ±{p.max_angle:g}°, flip {'/'.join(flips) or 'none'},"
              f" seed {p.seed}"]
    return gm.result(ctx, TOOL, m, out, dst, [Stat("copies", "Copies", n)], report)
