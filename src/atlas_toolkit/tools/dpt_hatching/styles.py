"""
The two other hatching styles, from the notebooks, with their faults fixed.

patches   notebook (d): SLIC superpixels on Lab + depth, each filled with straight lines at its own orientation.
          Fixed: orientation is a weighted doubled-angle mean (no cancelling); spacing follows the patch's tone
          continuously (not 3 classes); every line family shares one global phase, so lines of neighbouring patches
          with the same angle and spacing line up; lines are cut on the label raster, so neighbours share their
          border exactly (no seams, no one-pixel gaps); cross directions start at any tone threshold.
contours  notebook (c): iso-depth lines. Fixed: levels can follow equal areas (depth quantiles) instead of equal
          depth, so they do not bunch up at occluding edges; the bias works as named; contour pieces lying on an
          occlusion edge are dropped; the far background can be skipped.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import gaussian_filter


def runs_on_mask(xs: np.ndarray, ys: np.ndarray, ok: np.ndarray, min_pts: int = 2) -> list:
    """Consecutive stretches of a sampled line where `ok` holds, as [(x, y), ...] lists."""
    out = []
    if len(ok) == 0:
        return out
    edges = np.flatnonzero(np.diff(np.concatenate([[0], ok.astype(np.int8), [0]])))
    for a, b in zip(edges[0::2], edges[1::2]):
        if b - a >= min_pts:
            out.append(list(zip(xs[a:b].tolist(), ys[a:b].tolist())))
    return out


def patches(lab: np.ndarray, depth: np.ndarray, tone: np.ndarray, field2: np.ndarray, *, n_segments: int,
            compactness: float, depth_weight: float, layer_t: list, layer_angles: list, spacing_px, white: float,
            progress=None) -> list:
    """Per tone layer, the lines in pixels: [[polyline, ...] per layer]."""
    from skimage.segmentation import slic
    stack = np.dstack([lab, depth[..., None] * 100.0 * depth_weight])
    # scikit-image (0.19+) rescales the features to [0, 1] before clustering; Lab spans ~100, so without this the
    # compactness swamps colour and depth and the "superpixels" come out as a plain square grid, as they did in
    # notebook (d). Dividing by the range gives compactness its classic SLIC meaning (10-20 for Lab).
    span = max(float(stack.max() - stack.min()), 1e-6)
    labels = slic(stack, n_segments=int(n_segments), compactness=float(compactness) / span, start_label=0,
                  convert2lab=False, channel_axis=-1)
    n = int(labels.max()) + 1
    flat = labels.ravel()
    cnt = np.bincount(flat, minlength=n).astype(np.float64)
    mean_t = np.bincount(flat, weights=tone.ravel(), minlength=n) / np.maximum(cnt, 1)
    c2 = np.bincount(flat, weights=field2[..., 0].ravel(), minlength=n)
    s2 = np.bincount(flat, weights=field2[..., 1].ravel(), minlength=n)
    angle = 0.5 * np.arctan2(s2, c2)                          # doubled-angle mean: no cancelling
    from scipy.ndimage import find_objects
    boxes = find_objects(labels + 1)
    h, w = labels.shape
    out = [[] for _ in layer_t]
    for idx, box in enumerate(boxes):
        if box is None or mean_t[idx] >= white:
            continue
        if progress and idx % 200 == 0:
            progress(idx / n)
        y0, y1, x0, x1 = box[0].start, box[0].stop, box[1].start, box[1].stop
        t = float(mean_t[idx])
        for k, (tk, da) in enumerate(zip(layer_t, layer_angles)):
            if t >= tk:
                continue
            sp = float(spacing_px(t, tk))
            a = float(angle[idx]) - math.radians(da)
            dx, dy = math.cos(a), math.sin(a)
            nx, ny = -dy, dx
            corners = np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1]], float)
            proj = corners @ np.array([nx, ny])
            along = corners @ np.array([dx, dy])
            for j in range(int(math.ceil(proj.min() / sp)), int(math.floor(proj.max() / sp)) + 1):
                off = j * sp                                  # global phase: line j of this family everywhere
                tt = np.arange(along.min(), along.max() + 0.5, 0.5)
                xs, ys = off * nx + tt * dx, off * ny + tt * dy
                inside = (xs >= 0) & (ys >= 0) & (xs <= w - 1) & (ys <= h - 1)
                ok = np.zeros(len(xs), bool)
                xi, yi = np.rint(xs[inside]).astype(int), np.rint(ys[inside]).astype(int)
                ok[inside] = labels[yi, xi] == idx
                out[k] += runs_on_mask(xs, ys, ok, 3)
    return out


def contour_levels(depth: np.ndarray, n: int, mode: str, bias: float, far_cut: float) -> np.ndarray:
    """`n` depth levels. area: equal image area between levels (quantiles). depth: equal depth steps, bunched towards
    the near side for bias > 0 or the far side for bias < 0."""
    lo = max(float(far_cut), float(depth.min()) + 1e-4)
    hi = float(depth.max()) - 1e-4
    u = np.linspace(0, 1, n + 2)[1:-1]
    u = u ** (2.0 ** (-1.5 * bias))
    if mode == "area":
        vals = depth[depth > lo]
        if vals.size < 10:
            return np.array([])
        return np.quantile(vals, u)
    return lo + (hi - lo) * u


def contours(depth: np.ndarray, *, levels: int, mode: str, bias: float, far_cut: float, smooth_px: float,
             barrier=None) -> list:
    from skimage.measure import find_contours
    d = gaussian_filter(depth.astype(np.float32), max(smooth_px, 0.3))
    out = []
    bar = None
    if barrier is not None:
        from scipy.ndimage import binary_dilation
        bar = binary_dilation(barrier, iterations=2)
    for v in contour_levels(d, int(levels), mode, bias, far_cut):
        for c in find_contours(d, float(v)):
            ys, xs = c[:, 0], c[:, 1]
            if bar is None:
                if len(xs) > 2:
                    out.append(list(zip(xs.tolist(), ys.tolist())))
                continue
            ok = ~bar[np.rint(ys).astype(int).clip(0, d.shape[0] - 1), np.rint(xs).astype(int).clip(0, d.shape[1] - 1)]
            out += runs_on_mask(xs, ys, ok, 3)
    return out
