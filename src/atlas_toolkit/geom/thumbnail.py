"""
A clay render of a mesh for the model catalogue's cards (scripts/catalogue.py thumbs), on the CPU with embree
(geom/raycast.py): no OpenGL, so it runs the same on a build machine, in CI and offscreen. No Qt.

    render(V, F, size=384) -> PIL.Image (RGBA, transparent background)

The model is seen from the front and a little to the side and above (Z up, the front toward -Y, as
geom/frame3d.py), in orthographic projection fitted to its silhouette. Shading: a soft key light with shadows, a
fill, a rim, and ambient occlusion, all in a neutral clay that reads on light and dark backgrounds. Supersampled
for clean edges. Deterministic (the AO and shadow rays are seeded).
"""
from __future__ import annotations

import math

import numpy as np

CLAY = np.array([0.86, 0.84, 0.80])


def _unit(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n > 0, n, 1.0)


def _camera(azimuth: float, elevation: float):
    a, e = math.radians(azimuth), math.radians(elevation)
    back = np.array([math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)])   # toward the camera
    right = _unit(np.cross(np.array([0.0, 0.0, 1.0]), back))
    up = np.cross(back, right)
    return back, right, up


def _hemisphere(n, rng):
    u, v = rng.random(n), rng.random(n)
    r, phi = np.sqrt(u), 2 * math.pi * v
    return np.stack([r * np.cos(phi), r * np.sin(phi), np.sqrt(1 - u)], 1)


def render(V, F, size: int = 384, azimuth: float = -28.0, elevation: float = 14.0, supersample: int = 2,
           ao_rays: int = 12, shadow_rays: int = 4, margin: float = 0.06, seed: int = 7):
    from PIL import Image

    from .raycast import Caster
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    caster = Caster(V, F)
    ext = float(np.ptp(V, axis=0).max()) or 1.0
    back, right, up = _camera(azimuth, elevation)

    # fit the silhouette: the vertices' extent across the view
    px, py = V @ right, V @ up
    cx, cy = (px.min() + px.max()) / 2, (py.min() + py.max()) / 2
    span = max(np.ptp(px), np.ptp(py)) * (1 + 2 * margin)
    n = size * supersample
    s = (np.arange(n) + 0.5) / n - 0.5
    gx, gy = np.meshgrid(s * span + cx, -s * span + cy)
    centre = V.mean(0)
    depth = float(np.ptp(V @ back)) + ext
    O = (gx.reshape(-1, 1) * right + gy.reshape(-1, 1) * up
         + (centre @ back + depth) * back)
    D = np.broadcast_to(-back, O.shape)
    t, face, u, v = caster.intersect(O, D)
    hit = np.isfinite(t)
    rgb = np.zeros((n * n, 3))
    if hit.any():
        f = face[hit]
        P = caster.points(f, u[hit], v[hit])
        fn = _unit(np.cross(V[F[f, 1]] - V[F[f, 0]], V[F[f, 2]] - V[F[f, 0]]))
        vn = _vertex_normals(V, F)
        w0, w1, w2 = 1 - u[hit] - v[hit], u[hit], v[hit]
        sn = _unit(w0[:, None] * vn[F[f, 0]] + w1[:, None] * vn[F[f, 1]] + w2[:, None] * vn[F[f, 2]])
        N = np.where((np.einsum("ij,ij->i", sn, fn) > math.cos(math.radians(35)))[:, None], sn, fn)
        N = np.where(((N @ back) < 0)[:, None], -N, N)     # one-sided surfaces: face the eye
        rng = np.random.default_rng(seed)
        eps = ext * 2e-4
        Ps = P + N * eps

        key = _unit(-0.55 * right + 0.62 * back + 0.75 * up)       # upper left, in front
        fill = _unit(0.7 * right + 0.5 * back - 0.1 * up)
        rim = _unit(0.2 * right - 0.9 * back + 0.5 * up)

        # soft shadow toward the key light
        lit = np.zeros(len(P))
        tang = _unit(np.cross(key, up if abs(key @ up) < 0.9 else right))
        bit = np.cross(key, tang)
        for k in range(shadow_rays):
            j = rng.normal(0, 0.06, 2)
            d = _unit(key + j[0] * tang + j[1] * bit)
            lit += ~caster.occluded(Ps, np.broadcast_to(d, Ps.shape), ext * 3)
        lit /= shadow_rays

        # ambient occlusion
        T = _unit(np.cross(N, np.where(np.abs(N[:, 2:3]) < 0.9, [0.0, 0.0, 1.0], [1.0, 0.0, 0.0])))
        B = np.cross(N, T)
        H = _hemisphere(ao_rays, rng)
        free = np.zeros(len(P))
        for k in range(ao_rays):
            d = T * H[k, 0] + B * H[k, 1] + N * H[k, 2]
            free += ~caster.occluded(Ps, d, ext * 0.18)
        ao = 0.35 + 0.65 * (free / ao_rays)

        ndk = np.clip(N @ key, 0, 1)
        wrap = np.clip((N @ key + 0.25) / 1.25, 0, 1)
        light = (0.18 + 0.72 * (0.35 * wrap + 0.65 * ndk * (0.25 + 0.75 * lit))) * ao
        light += 0.22 * np.clip(N @ fill, 0, 1) * ao
        light += 0.28 * np.clip(N @ rim, 0, 1) ** 2
        spec = np.clip(np.einsum("ij,j->i", N, _unit(key + back)), 0, 1) ** 40 * 0.12 * lit
        c = CLAY[None, :] * light[:, None] + spec[:, None]
        rgb[hit] = np.clip(c, 0, 1) ** (1 / 1.15)
    alpha = hit.astype(float)
    img = np.concatenate([rgb, alpha[:, None]], 1).reshape(n, n, 4)
    # premultiply before averaging the supersamples, so the edges do not darken
    img[..., :3] *= img[..., 3:4]
    big = (img * 255).round().astype(np.uint8)
    im = Image.fromarray(big, "RGBa").resize((size, size), Image.LANCZOS)
    return im.convert("RGBA")


def _vertex_normals(V, F):
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])     # area-weighted
    vn = np.zeros_like(V)
    for k in range(3):
        np.add.at(vn, F[:, k], fn)
    return _unit(vn)
