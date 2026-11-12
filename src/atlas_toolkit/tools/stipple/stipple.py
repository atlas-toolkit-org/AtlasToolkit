"""
stipple.py - pointillism for pen plotters: a model's shading, ray-cast on the paper grid (geom/shading.py), becomes
blue-noise dots - dense where the form turns away from the light, sparse in the half-tones, none in the light (the
paper is the light). The sphere of Digital divide (projects/blender3d_project/digital_divide_1: plotter_lib/
stipple.py and dd_runner.sphere_tone), for any model, with cast shadows and ambient occlusion.

    tone      wrap lighting (soft terminator), a bounce from below, optional ambient occlusion and cast shadows,
              and edge darkening (rim_dark) so the dots build the silhouette instead of a line
    density   rho = rho_max · ((darkness - void) / (1 - void))^gamma, from the spacing wanted in the darkest place; a
              rim term adds a few dots where the lit silhouette would vanish; low-frequency noise breaks the
              half-tones into patches
    sampling  variable-radius dart throwing (random sequential adsorption): candidates drawn with probability ~ rho,
              each kept if no dot lies within r = K / sqrt(rho); at saturation RSA packs ~0.70 / r² dots, so K = 0.84
              gives a dot density ~ rho. The budget is a hard cap (every dot is a pen lift).
    dots      a tiny closed polygon per dot: a plotter makes a dot by touching down, and a closed polygon survives
              every path tool (a zero-length move is dropped by most)
"""
from __future__ import annotations

import math

import numpy as np

from atlas_toolkit.geom.shading import smoothstep

K_RSA = 0.84


def tone(gb, L, c) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel tone (H, W), 1 = paper white, and n·l (H, W). L: unit vector toward the key light (model units),
    c: the settings (ambient, key, wrap, bounce, gamma, ao_strength, shadows, rim_dark, rim_dark_lo, rim_dark_hl)."""
    H, W = gb["shape"]
    n = gb["normal"]
    nl_raw = n @ L
    nl = (nl_raw + c["wrap"]) / (1 + c["wrap"])
    lit = gb["lit"] if c["shadows"] else np.ones(len(nl))
    ao = 1.0 - c["ao_strength"] * (1.0 - gb["ao"])
    t = c["ambient"] * ao + c["key"] * np.clip(nl, 0, 1) * lit * np.sqrt(np.clip(ao, 0, 1)) \
        + c["bounce"] * np.clip(-n[:, 2], 0, 1)
    t = np.clip(t, 0, 1) ** c["gamma"]
    if c["rim_dark"] > 0:
        edge = smoothstep(c["rim_dark_lo"], 1.0, 1.0 - np.abs(gb["ndotv"]))
        lo, hi = c["rim_dark_hl"]
        t = np.clip(t - c["rim_dark"] * edge * (1.0 - smoothstep(lo, hi, nl_raw)), 0, 1)
    return np.where(gb["hit"], t, 1.0).reshape(H, W), nl_raw.reshape(H, W)


def _smooth_noise(shape, scale_px, seed):
    """Value noise in [0, 1] with features ~scale_px pixels (bilinear upsampling of a coarse grid)."""
    rng = np.random.default_rng(seed)
    H, W = shape
    gh, gw = max(2, int(math.ceil(H / scale_px)) + 2), max(2, int(math.ceil(W / scale_px)) + 2)
    g = rng.random((gh, gw))
    y = np.arange(H) / scale_px
    x = np.arange(W) / scale_px
    y0, x0 = np.floor(y).astype(int), np.floor(x).astype(int)
    fy, fx = (y - y0)[:, None], (x - x0)[None, :]
    fy, fx = fy * fy * (3 - 2 * fy), fx * fx * (3 - 2 * fx)
    a, b = g[y0][:, x0], g[y0][:, x0 + 1]
    c, d = g[y0 + 1][:, x0], g[y0 + 1][:, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def dot_density(dark, mask, px, min_spacing_mm=0.9, void=0.25, gamma=1.3, rim=None, rim_spacing_mm=3.0,
                noise_amp=0.0, noise_scale_mm=6.0, seed=0):
    """Dots per mm² per pixel (H, W). dark: darkness 0 (light) - 1 (black); mask: where dots may go; rim: optional
    0-1 weight of the silhouette ring for the sparse hint."""
    dark = np.asarray(dark, float)
    f = np.clip((dark - void) / max(1.0 - void, 1e-6), 0, 1) ** gamma
    rho_max = (K_RSA / min_spacing_mm) ** 2
    rho = rho_max * f
    if noise_amp > 0:
        n = _smooth_noise(dark.shape, noise_scale_mm / px, seed)
        rho *= np.clip(1.0 - noise_amp * (2 * n - 1), 0, None)
    if rim is not None and rim_spacing_mm > 0:
        rho_rim = (K_RSA / rim_spacing_mm) ** 2
        rho = np.maximum(rho, rho_rim * np.clip(rim, 0, 1) * (1 - np.clip(f * 4, 0, 1)))
    return np.where(mask, rho, 0.0)


def poisson_dots(rho, x0, y0, px, budget=2500, oversample=14, r_cap_mm=8.0, seed=0, k=K_RSA, cancel=None):
    """Variable-radius blue-noise dots for a density raster (dots / mm²). Returns (n, 2) paper mm, n <= budget.
    Pixel (i, j) covers x0 + j px .. x0 + (j + 1) px (same for y)."""
    rho = np.asarray(rho, float)
    H, W = rho.shape
    flat = rho.ravel()
    total = float(flat.sum() * px * px)
    if total <= 0 or budget <= 0:
        return np.zeros((0, 2))
    rng = np.random.default_rng(seed)
    n_cand = int(min(max(oversample * total, 200), 400000))
    p = flat / flat.sum()
    idx = rng.choice(len(flat), size=n_cand, p=p)
    jx = rng.random(n_cand)
    jy = rng.random(n_cand)
    cx = x0 + (idx % W + jx) * px
    cy = y0 + (idx // W + jy) * px
    r = np.minimum(k / np.sqrt(np.maximum(flat[idx], 1e-12)), r_cap_mm)
    cell = max(float(r.min()), 0.05)
    grid = {}
    out = []
    for c_i, (x, y, rr) in enumerate(zip(cx.tolist(), cy.tolist(), r.tolist())):
        if cancel is not None and c_i % 4096 == 0:
            cancel()
        gi, gj = int(x // cell), int(y // cell)
        n = int(math.ceil(rr / cell))
        ok = True
        r2 = rr * rr
        for a in range(gi - n, gi + n + 1):
            for b in range(gj - n, gj + n + 1):
                for qx, qy in grid.get((a, b), ()):
                    if (qx - x) ** 2 + (qy - y) ** 2 < r2:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                break
        if ok:
            grid.setdefault((gi, gj), []).append((x, y))
            out.append((x, y))
            if len(out) >= budget:
                break
    return np.array(out).reshape(-1, 2)


def dot_paths(pts, radius=0.15, sides=8, phase=0.0):
    """One closed polygon (sides + 1 points, first = last) per dot centre."""
    a = phase + np.linspace(0, 2 * math.pi, sides + 1)
    ring = np.c_[np.cos(a), np.sin(a)] * radius
    ring[-1] = ring[0]
    return [ring + p for p in np.asarray(pts, float)]


def keep_out_mask(strokes, x0, y0, px, shape, clear_mm):
    """Pixels within clear_mm of any stroke (k, 2) polyline (dots stay off other lines)."""
    H, W = shape
    m = np.zeros((H, W), bool)
    r = int(math.ceil(clear_mm / px))
    offs = [(di, dj) for di in range(-r, r + 1) for dj in range(-r, r + 1)
            if (di * di + dj * dj) * px * px <= clear_mm * clear_mm + 1e-9]
    for s in strokes:
        s = np.asarray(s, float)
        if len(s) < 2:
            continue
        seg = np.linalg.norm(np.diff(s, axis=0), axis=1)
        n = np.maximum(1, np.ceil(seg / (px * 0.5)).astype(int))
        pts = np.vstack([s[i] + np.linspace(0, 1, k, endpoint=False)[:, None] * (s[i + 1] - s[i])
                         for i, k in enumerate(n)] + [s[-1:]])
        j = np.floor((pts[:, 0] - x0) / px).astype(int)
        i = np.floor((pts[:, 1] - y0) / px).astype(int)
        for di, dj in offs:
            ii, jj = i + di, j + dj
            ok = (ii >= 0) & (ii < H) & (jj >= 0) & (jj < W)
            m[ii[ok], jj[ok]] = True
    return m
