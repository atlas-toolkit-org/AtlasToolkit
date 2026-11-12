"""
mesh_twist.py - wring a model: a lateral S-sway, an undulating torsion about the up axis and a (spiral) ripple, the
"wave" distortion of the twisting heads (projects/blender3d_project, skill plotter-contour-art:
head_plot_pipeline.build_wave_group, a Geometry Nodes Set Position), computed here with numpy on any model.

The field, per vertex, in the canonical frame of the Blender pieces (geom/frame3d: up = Z, front = -Y, centred on
the bounding box, 2 units tall; z runs -1 (base) .. 1 (top)):

    grow   = 0.35 z + 0.85                                   the sway grows from the base (0.5) to the top (1.2)
    sway   = sin(z·f_s + φ) · A · grow       along +X (across the face)
           + 0.35 · A · cos(z·f_s + φ + 0.7)  along +Y (away from the face, a quarter phase behind)
    turn   = sin(z·f_t + φ) · T               rotation about the vertical axis (radians)
    ripple = sin(z·f_r + θ·lobes) · R · min(r/fade, 1)²   (θ, r: angle and distance around the axis)

f = π · waves over the height. The ripple pushes along the vertex normal in the Blender pieces ("normal"); here it
can also push straight out from the axis ("radial", the default): that depends on position only, so thin parts
(fingers, an arm, the rim of a cuirass) can never tear into spikes, which normal-driven ripples did. With the
"spine" pivot the turn and the ripple go around a smoothed centre line instead of the box's vertical axis, so a part
that leans out (a raised arm) wrings about itself.

A sequence ramps the three amplitudes over its stages along trajectories taken from the v17 twist sequence
(stage I intact, II, III, IV = full), so four stages at amount 1 give exactly those plates' values.
"""
from __future__ import annotations

import math

import numpy as np

GROW_SLOPE, GROW_BASE = 0.35, 0.85          # the Blender pieces' sway growth, base -> top
DEPTH_RATIO, DEPTH_PHASE = 0.35, 0.7        # quarter-phase sway in depth
KEYS_U = (0.0, 1.0 / 3.0, 2.0 / 3.0, 1.0)   # stages I .. IV of a four-stage sequence
TRAJECTORY = {                              # fraction of the full amplitude at each stage (v17 twist sequence)
    "sway": (0.0, 0.3, 0.625, 1.0),         # 0, 0.072, 0.15, 0.24
    "turn": (0.0, 0.30645, 0.6129, 1.0),    # 0, 0.19, 0.38, 0.62 rad
    "ripple": (0.0, 0.31111, 0.62222, 1.0),  # 0, 0.014, 0.028, 0.045
}


def ramp(name: str, u: float) -> float:
    """Fraction of the full amplitude at progress u (0 intact, 1 full; beyond 1 it keeps growing linearly)."""
    if u >= 1.0:
        return float(u)
    return float(np.interp(max(u, 0.0), KEYS_U, TRAJECTORY[name]))


def spine(C: np.ndarray, bins: int = 48, smooth: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """A smoothed centre line: (z samples, centres (n, 2)) from the mean x, y of the vertices in height bands."""
    z = C[:, 2]
    lo, hi = float(z.min()), float(z.max())
    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.searchsorted(edges, z, side="right") - 1, 0, bins - 1)
    cnt = np.bincount(idx, minlength=bins).astype(float)
    sx = np.bincount(idx, C[:, 0], minlength=bins)
    sy = np.bincount(idx, C[:, 1], minlength=bins)
    have = cnt > 0
    zc = (edges[:-1] + edges[1:]) / 2
    cx = np.interp(zc, zc[have], sx[have] / cnt[have])
    cy = np.interp(zc, zc[have], sy[have] / cnt[have])
    k = np.ones(2 * smooth + 1) / (2 * smooth + 1)
    for _ in range(3):
        cx = np.convolve(np.pad(cx, smooth, mode="edge"), k, mode="valid")
        cy = np.convolve(np.pad(cy, smooth, mode="edge"), k, mode="valid")
    return zc, np.column_stack([cx, cy])


def offsets(C: np.ndarray, N: np.ndarray | None, sway: float, turn: float, ripple: float, *,
            sway_freq: float = 3.0, turn_freq: float = 2.2, ripple_freq: float = 20.0, lobes: float = 3.0,
            fade: float = 0.3, phase: float = 0.4, ripple_dir: str = "radial", pivot: str = "centre",
            centre_line=None) -> np.ndarray:
    """Displacement of points C (n, 3) (canonical, 2 tall) for amplitudes sway, turn (radians), ripple (units).
    N: unit vertex normals, needed for ripple_dir "normal"/"smooth"."""
    C = np.asarray(C, dtype=np.float64)
    x, y, z = C[:, 0], C[:, 1], C[:, 2]
    off = np.zeros_like(C)
    if sway:
        grow = GROW_SLOPE * z + GROW_BASE
        ph = z * sway_freq + phase
        off[:, 0] += np.sin(ph) * sway * grow
        off[:, 1] += np.cos(ph + DEPTH_PHASE) * sway * DEPTH_RATIO
    if pivot == "spine":
        zc, cxy = centre_line if centre_line is not None else spine(C)
        px = np.interp(z, zc, cxy[:, 0])
        py = np.interp(z, zc, cxy[:, 1])
    else:
        px = py = np.zeros_like(z)
    rx, ry = x - px, y - py
    if turn:
        ang = np.sin(z * turn_freq + phase) * turn
        ca, sa = np.cos(ang), np.sin(ang)
        off[:, 0] += rx * ca - ry * sa - rx
        off[:, 1] += rx * sa + ry * ca - ry
    if ripple:
        theta = np.arctan2(ry, rx)
        r = np.sin(z * ripple_freq + theta * lobes) * ripple
        rxy = np.hypot(rx, ry)
        if fade > 0:
            f = np.minimum(rxy / fade, 1.0)
            r = r * f * f
        if ripple_dir == "radial":
            with np.errstate(invalid="ignore", divide="ignore"):
                d = np.column_stack([rx / rxy, ry / rxy, np.zeros_like(rx)])
            d[~np.isfinite(d)] = 0.0
        else:
            if N is None:
                raise ValueError("normal ripples need vertex normals")
            d = N
        off += d * r[:, None]
    return off


def smooth_normals(mesh, iterations: int = 8) -> np.ndarray:
    """Vertex normals averaged over neighbours (a gentler direction for normal ripples on noisy scans)."""
    from scipy.sparse import coo_matrix
    N = np.asarray(mesh.vertex_normals, dtype=np.float64)
    E = np.asarray(mesh.edges_unique, dtype=np.int64)
    n = len(N)
    rows = np.r_[E[:, 0], E[:, 1], np.arange(n)]
    cols = np.r_[E[:, 1], E[:, 0], np.arange(n)]
    A = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n)).tocsr()
    deg = np.asarray(A.sum(1)).ravel()
    for _ in range(iterations):
        N = (A @ N) / deg[:, None]
    ln = np.linalg.norm(N, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    return N / ln


def subdivide(V: np.ndarray, F: np.ndarray, levels: int, budget: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Loop subdivision, as many levels as fit the face budget. Returns (V, F, levels done)."""
    import trimesh
    done = 0
    while done < levels and len(F) * 4 <= budget:
        V, F = trimesh.remesh.subdivide_loop(V, F, iterations=1)
        done += 1
    return np.asarray(V, dtype=np.float64), np.asarray(F, dtype=np.int64), done


def max_displacement(off: np.ndarray) -> float:
    return float(np.linalg.norm(off, axis=1).max()) if len(off) else 0.0


def amplitudes(p, u: float) -> dict:
    """The skill-unit amplitudes of a stage at progress u from the tool's settings (% of height, degrees)."""
    return {"sway": 2.0 * p.sway / 100.0 * ramp("sway", u),
            "turn": math.radians(p.turn) * ramp("turn", u),
            "ripple": 2.0 * p.ripple / 100.0 * ramp("ripple", u)}


def shape(p) -> dict:
    return dict(sway_freq=math.pi * p.sway_waves, turn_freq=math.pi * p.turn_waves,
                ripple_freq=math.pi * p.ripple_waves, lobes=float(p.lobes), fade=2.0 * p.fade / 100.0,
                phase=math.radians(p.phase), ripple_dir=p.ripple_dir, pivot=p.pivot)
