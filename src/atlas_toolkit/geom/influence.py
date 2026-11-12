"""
Where a mesh tool acts (core/params.influence_params): a weight in 0..1 for points of the model's canonical frame
(geom/frame3d: up = Z, front = -Y, centred on the bounding box).

    all      1 everywhere
    band     1 between two heights (% of the height from the base), fading to 0 over the feather
    sphere   1 inside a sphere (centre in % of the model's size from its centre, radius in % of its height),
             fading to 0 over the feather

Invert swaps acting and resting parts. The feather is a smoothstep, so motion fades in without a crease. Tools
evaluate the weights on the model at rest, so the stages of a sequence share them.
"""
from __future__ import annotations

import numpy as np


def smoothstep(t) -> np.ndarray:
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _fade(outside, feather: float) -> np.ndarray:
    """1 where `outside` <= 0, falling to 0 at `feather` (a hard edge when feather is 0)."""
    outside = np.asarray(outside, dtype=np.float64)
    if feather <= 0:
        return (outside <= 0).astype(np.float64)
    return 1.0 - smoothstep(outside / feather)


def weights(P, values, lo, hi) -> np.ndarray:
    """Weights of points P (n, 3), canonical, for the influence settings in `values`; lo, hi: the model's canonical
    bounds at rest."""
    P = np.atleast_2d(np.asarray(P, dtype=np.float64))
    lo, hi = np.asarray(lo, dtype=np.float64), np.asarray(hi, dtype=np.float64)
    mode = values.get("influence", "all") or "all"
    if mode == "all":
        return np.ones(len(P))
    height = float(hi[2] - lo[2]) or 1.0
    feather = float(values.get("feather", 0.0)) / 100.0 * height
    if mode == "band":
        a, b = sorted((float(values.get("band_from", 0.0)), float(values.get("band_to", 100.0))))
        z0, z1 = lo[2] + a / 100.0 * height, lo[2] + b / 100.0 * height
        w = _fade(np.maximum(z0 - P[:, 2], P[:, 2] - z1), feather)
    elif mode == "sphere":
        centre = (lo + hi) / 2.0 + np.asarray(values.get("sphere_centre", (0, 0, 0)), float) / 100.0 * (hi - lo)
        radius = float(values.get("sphere_radius", 30.0)) / 100.0 * height
        w = _fade(np.linalg.norm(P - centre, axis=1) - radius, feather)
    else:
        raise ValueError(f"unknown influence {mode!r}")
    return 1.0 - w if values.get("invert_influence") else w


def describe(values) -> str:
    mode = values.get("influence", "all") or "all"
    if mode == "all":
        return "the whole model"
    inv = "everywhere but " if values.get("invert_influence") else ""
    if mode == "band":
        a, b = sorted((float(values.get("band_from", 0.0)), float(values.get("band_to", 100.0))))
        text = f"a band from {a:g}% to {b:g}% of the height"
    else:
        c = values.get("sphere_centre", (0, 0, 0))
        text = (f"a sphere of radius {float(values.get('sphere_radius', 30)):g}% h at "
                f"({', '.join(f'{x:g}' for x in c)})% of the size")
    return f"{inv}{text}, feather {float(values.get('feather', 0)):g}% h"
