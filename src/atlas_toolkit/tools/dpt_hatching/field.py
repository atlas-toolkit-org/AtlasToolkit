"""
Tone and direction fields from a photo and its depth.

Tone T in 0..1 (0 = darkest ink, 1 = paper): CIELab L*, local contrast (CLAHE), a tone curve, optional depth fog.

Directions are axial (a stroke has no arrow), so they are averaged and interpolated as doubled angles
(cos 2φ, sin 2φ). Notebook (d) averaged plain vectors, which cancels where directions flip; notebook (b) swapped the
Sobel axes. The structure tensor gives each pixel a dominant orientation and a coherence (how clear it is); where
depth has no clear orientation (flat walls, sky) the field falls back to the photo's, then to a fixed angle.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import gaussian_filter


def lightness(rgb: np.ndarray) -> np.ndarray:
    from skimage.color import rgb2lab
    return (rgb2lab(rgb.astype(np.float32) / 255.0)[..., 0] / 100.0).astype(np.float32)


def tone(L: np.ndarray, depth: np.ndarray, contrast: float = 0.35, curve: float = 1.0, fog: float = 0.0) -> np.ndarray:
    """0 = darkest .. 1 = paper. `contrast` 0..1 drives CLAHE, `curve` > 1 darkens the mid-tones, `fog` lightens what
    is far (aerial perspective)."""
    t = L.astype(np.float32)
    if contrast > 0:
        from skimage.exposure import equalize_adapthist
        k = max(8, int(max(t.shape) / 8))
        eq = equalize_adapthist(np.clip(t, 0, 1), kernel_size=k, clip_limit=0.004 + 0.03 * contrast)
        t = (1 - contrast) * t + contrast * eq.astype(np.float32)
    t = np.clip(t, 0, 1) ** curve
    if fog > 0:
        t = t + fog * (1.0 - depth) * (1.0 - t)
    return np.clip(t, 0, 1).astype(np.float32)


def structure(img: np.ndarray, sigma_grad: float, sigma_tensor: float):
    """(gradient orientation φ (axial), coherence 0..1, energy) from the smoothed structure tensor.
    Depth jumps (occlusions) stay in on purpose: across a thin part (a beam, a finger) the jump is the strongest
    gradient, and it lays the strokes along the part, as an engraver would. Next to a silhouette it also combs the
    flat background into echo lines; masking the jumps out removes those, but turns strokes on beams into rungs."""
    g = gaussian_filter(img.astype(np.float32), max(sigma_grad, 0.5))
    gy, gx = np.gradient(g)
    jxx = gaussian_filter(gx * gx, sigma_tensor)
    jxy = gaussian_filter(gx * gy, sigma_tensor)
    jyy = gaussian_filter(gy * gy, sigma_tensor)
    phi = 0.5 * np.arctan2(2 * jxy, jxx - jyy)
    tr = jxx + jyy
    coh = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / (tr + 1e-12)
    return phi.astype(np.float32), coh.astype(np.float32), tr.astype(np.float32)


CLEAR_SLOPE = 0.05    # a change of 5% of the full range across the picture is a clear direction


def _weight(coh: np.ndarray, energy: np.ndarray) -> np.ndarray:
    """How much to trust an orientation: coherent, and with a slope above an absolute floor (5% of the full range
    across the picture). Relative to the picture's own strongest slopes, the occlusion edges drowned every gentle
    form."""
    floor = (CLEAR_SLOPE / max(coh.shape)) ** 2
    return np.clip(coh, 0, 1) * np.clip(energy / floor, 0, 1)


def directions(mode: str, depth: np.ndarray, L: np.ndarray, angle_deg: float, smooth_px: float) -> np.ndarray:
    """Doubled-angle field (H, W, 2) = (cos 2φ, sin 2φ) of the stroke direction φ, in image pixels (y down).

    form        along the isophotes of depth: strokes wrap round the forms (engraving)
    cross-form  along the depth gradient: strokes run over the forms
    image       along the photo's edges and textures
    angle       a fixed angle (degrees, counter-clockwise on the paper)
    """
    a0 = -math.radians(angle_deg)                      # paper y points down
    fixed = np.array([math.cos(2 * a0), math.sin(2 * a0)], np.float32)
    if mode == "angle":
        out = np.empty(depth.shape + (2,), np.float32)
        out[...] = fixed
        return out
    phi_i, coh_i, en_i = structure(L, 1.0, max(smooth_px, 1.0))
    wi = _weight(coh_i, en_i)
    vi = np.stack([np.cos(2 * (phi_i + np.pi / 2)), np.sin(2 * (phi_i + np.pi / 2))], -1)   # along edges
    v_fallback = wi[..., None] * vi + (1 - wi[..., None]) * fixed
    if mode == "image":
        v = v_fallback
    else:
        phi_d, coh_d, en_d = structure(depth, 1.0, max(smooth_px, 1.0))
        wd = _weight(coh_d, en_d)
        rot = np.pi / 2 if mode == "form" else 0.0
        vd = np.stack([np.cos(2 * (phi_d + rot)), np.sin(2 * (phi_d + rot))], -1)
        v = wd[..., None] * vd + (1 - wd[..., None]) * v_fallback
    v[..., 0] = gaussian_filter(v[..., 0], max(smooth_px * 0.5, 0.5))
    v[..., 1] = gaussian_filter(v[..., 1], max(smooth_px * 0.5, 0.5))
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return (v / np.maximum(n, 1e-6)).astype(np.float32)


def rotate(field: np.ndarray, deg: float) -> np.ndarray:
    """The same field turned by `deg` degrees (doubled angle turns twice as much)."""
    if not deg:
        return field
    a = -2 * math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    out = np.empty_like(field)
    out[..., 0] = field[..., 0] * c - field[..., 1] * s
    out[..., 1] = field[..., 0] * s + field[..., 1] * c
    return out


def occlusions(depth: np.ndarray, sensitivity: float, sigma: float) -> np.ndarray:
    """Depth discontinuities (thin boolean edges): where one surface passes in front of another."""
    from skimage.feature import canny
    hi = 1.0 - (0.003 + 0.06 * sensitivity)              # keep the strongest few % of gradients
    return canny(depth.astype(np.float64), sigma=max(sigma, 0.5), low_threshold=hi - 0.04 - 0.08 * sensitivity,
                 high_threshold=hi, use_quantiles=True)
