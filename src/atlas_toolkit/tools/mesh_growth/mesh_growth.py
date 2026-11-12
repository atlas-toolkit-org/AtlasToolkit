"""
mesh_growth.py - grow a Gray-Scott reaction-diffusion pattern over a model's surface and raise (or engrave) it as
relief: coral ridges, fingerprint channels, dividing cells, a honeycomb skin.

Two chemicals, u (the substrate) and v (the activator), react and diffuse over the mesh (Pearson 1993, after Turk
1991 for arbitrary surfaces):

    du/dt = Du lap(u) - u v^2 + F (1 - u)
    dv/dt = Dv lap(v) + u v^2 - (F + k) v

with Du = 2 Dv. F (feed) and k (kill) pick the regime (PATTERNS); the diffusion sets the scale. On a plane the
pattern's dominant wavelength is lambda0 sqrt(Du) (lambda0 measured once per regime with Du = 1, see PATTERNS), so
Du = (wavelength / lambda0)^2 gives the wavelength asked for in model units, whatever the mesh density: the mesh is
refined (or simplified) to `detail` vertices per wavelength first.

The Laplacian is the cotangent Laplacian with nonnegative edge weights w_ij (negative cotangent sums of obtuse
pairs clamped to 0) over the lumped barycentric vertex areas A_i, so lap(x)_i = sum_j w_ij (x_j - x_i) / A_i.
Diffusion is semi-implicit, (A + c L) x = A r with c = dt D, and solved with a few Jacobi sweeps starting from r:

    x_i <- (A_i r_i + c sum_j w_ij x_j) / (A_i + c sum_j w_ij)

Each sweep is a weighted average of the reaction update and the neighbours, so the scheme keeps a maximum
principle at any step size, slivers included, and needs no factorisation (one sparse product per sweep). The
reaction is explicit with dt = 1 (bounded: u and v stay in 0..1). Disconnected pieces never exchange chemicals:
the weights only join vertices that share a triangle. Boundaries of open meshes are no-flux (nothing leaves).

Relief: v is smoothed over a fraction of the wavelength, normalised by the last stage's percentiles (so a sequence
grows into the same contrast), shaped by Sharpness and Balance, and pushed along smoothed rest normals by Depth.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# id: (label, F, k, lambda0 = dominant wavelength at Du = 1, Dv = 0.5, measured on a periodic grid)
PATTERNS = {
    "coral": ("Coral", 0.0545, 0.062, 28.6),           # thick branching, meandering ridges
    "fingerprint": ("Fingerprint", 0.037, 0.060, 28.6),  # parallel channels, a labyrinth
    "maze": ("Maze", 0.029, 0.057, 30.7),               # thinner, denser meanders
    "cells": ("Cells", 0.0367, 0.0649, 30.7),           # spots that divide until they tile the surface
    "honeycomb": ("Honeycomb", 0.039, 0.058, 30.7),     # the negative: a web of walls around round pits
    "pebbles": ("Pebbles", 0.030, 0.062, 28.6),         # sparse round spots
    "lichen": ("Lichen", 0.062, 0.0609, 28.6),          # plateaus broken by stripes and holes
}
DU_DV = 2.0
DT = 1.0
SWEEPS = 2                       # Jacobi sweeps per diffusion solve
NO_PATTERN_KILL = 0.075          # kill rate outside "Acts on": v dies out there, the pattern stops at a natural edge
SEED_RADIUS = 0.3                # starting spot radius, x wavelength
SPREAD = 0.5                     # a wave from an origin reaches the far end at this share of the growth time
CAPTURE_EVERY = 100              # progress / cancel check interval (steps)


def pattern_values(name: str, feed: float | None = None, kill: float | None = None) -> tuple[float, float, float]:
    """(F, k, lambda0) of a named regime; "custom" uses feed and kill with the coral lambda0."""
    if name == "custom":
        return float(feed), float(kill), PATTERNS["coral"][3]
    _, F, k, lam = PATTERNS[name]
    return F, k, lam


# ----------------------------------------------------------------------------- the operator

def edges_of(F: np.ndarray) -> np.ndarray:
    return np.unique(np.sort(np.asarray(F)[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1), axis=0)


def cotan_weights(V: np.ndarray, F: np.ndarray, max_weight: float = 50.0):
    """Nonnegative cotangent edge weights and lumped (barycentric) vertex areas.
    Returns (E (m, 2) edges, w (m,), A (n,))."""
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    n = len(V)
    P = V[F]                                             # (f, 3, 3)
    cr = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    dbl = np.linalg.norm(cr, axis=1)                     # 2 x area
    A = np.zeros(n)
    np.add.at(A, F.ravel(), np.repeat(dbl / 6.0, 3))
    ok = dbl > 1e-14 * max(float(dbl.max()) if len(dbl) else 1.0, 1e-300)
    Ii, Jj, Ww = [], [], []
    for c in range(3):                                   # angle at corner c, opposite edge (c+1, c+2)
        a, b = (c + 1) % 3, (c + 2) % 3
        e1, e2 = P[:, a] - P[:, c], P[:, b] - P[:, c]
        cot = np.where(ok, (e1 * e2).sum(1) / np.where(ok, dbl, 1.0), 0.0)
        Ii.append(F[:, a])
        Jj.append(F[:, b])
        Ww.append(0.5 * cot)
    I, J, W = np.concatenate(Ii), np.concatenate(Jj), np.concatenate(Ww)
    lo, hi = np.minimum(I, J), np.maximum(I, J)
    key = lo * n + hi
    uniq, inv = np.unique(key, return_inverse=True)
    w = np.bincount(inv.ravel(), W, minlength=len(uniq))
    E = np.column_stack((uniq // n, uniq % n))
    w = np.clip(w, 0.0, max_weight)
    A = np.maximum(A, 1e-12 * (float(A.mean()) if n else 1.0))
    return E, w, A


def tangent_direction(V: np.ndarray, N: np.ndarray, align: str, up=(0.0, 0.0, 1.0)) -> np.ndarray | None:
    """Per-vertex unit tangent the diffusion favours: around the up axis (rings) or up the surface (vertical)."""
    if align == "none":
        return None
    up = np.asarray(up, dtype=np.float64)
    if align == "rings":
        T = np.cross(up, N)
    elif align == "vertical":
        T = up - (N @ up)[:, None] * N
    else:
        raise ValueError(f"unknown alignment {align!r}")
    ln = np.linalg.norm(T, axis=1, keepdims=True)
    return np.where(ln > 1e-9, T / np.maximum(ln, 1e-12), 0.0)


def anisotropic(V: np.ndarray, E: np.ndarray, w: np.ndarray, T: np.ndarray | None, strength: float) -> np.ndarray:
    """Edge weights scaled by how well each edge follows the tangent field T: (1 - s) + 2 s cos^2 (mean 1 over
    directions, so the wavelength holds). Diffusing faster along T lays the stripes across it."""
    if T is None or strength <= 0:
        return w
    d = V[E[:, 1]] - V[E[:, 0]]
    d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12)
    t = T[E[:, 0]] + T[E[:, 1]]
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
    c2 = (d * t).sum(1) ** 2
    s = float(np.clip(strength, 0.0, 1.0))
    return w * ((1.0 - s) + 2.0 * s * c2)


@dataclass
class Operator:
    """The sparse neighbour sums (W, symmetric, nonnegative), their row sums and the vertex areas."""
    W: object                    # scipy csr (n, n)
    rows: np.ndarray             # sum_j w_ij
    A: np.ndarray

    @classmethod
    def build(cls, n: int, E: np.ndarray, w: np.ndarray, A: np.ndarray, dtype=np.float32) -> "Operator":
        import scipy.sparse as sp
        i = np.concatenate([E[:, 0], E[:, 1]])
        j = np.concatenate([E[:, 1], E[:, 0]])
        W = sp.csr_matrix((np.concatenate([w, w]).astype(dtype), (i, j)), shape=(n, n))
        return cls(W, np.asarray(W.sum(1)).ravel().astype(dtype), np.asarray(A, dtype=dtype))

    def diffuse(self, r: np.ndarray, c, sweeps: int = SWEEPS) -> np.ndarray:
        """Approximately solve (A + c L) x = A r by Jacobi sweeps from r (a convex average: bounded)."""
        Ar = self.A * r
        den = self.A + c * self.rows
        x = r
        for _ in range(sweeps):
            x = (Ar + c * (self.W @ x)) / den
        return x


# ----------------------------------------------------------------------------- the simulation

@dataclass
class Chemistry:
    feed: np.ndarray             # per vertex (or scalar)
    kill: np.ndarray
    du: float                    # Du in model units^2 per step
    dv: float


def chemistry(n: int, pattern: str, wavelength: float, *, feed=None, kill=None, second: str = "none",
              blend: np.ndarray | None = None, active: np.ndarray | None = None,
              ratio: float = DU_DV) -> Chemistry:
    """Per-vertex feed and kill: the pattern, blended toward a second one by `blend` (0..1 per vertex), and pushed
    to a dead regime where `active` (0..1) is low. Diffusion from the wavelength (model units)."""
    F0, k0, lam = pattern_values(pattern, feed, kill)
    Fv, kv = np.full(n, F0), np.full(n, k0)
    if second != "none" and blend is not None:
        F1, k1, lam1 = pattern_values(second, feed, kill)
        b = np.clip(blend, 0.0, 1.0)
        Fv, kv = Fv + (F1 - F0) * b, kv + (k1 - k0) * b
        lam = (lam + lam1) / 2.0
    if active is not None:
        a = np.clip(active, 0.0, 1.0)
        kv = kv + (NO_PATTERN_KILL - kv) * (1.0 - a) * (kv < NO_PATTERN_KILL)
    du = (float(wavelength) / lam) ** 2
    return Chemistry(Fv.astype(np.float32), kv.astype(np.float32), du, du / float(ratio))


@dataclass
class Start:
    """Starting concentrations and the spots planted later (a wave from an origin): {step: vertex indices}."""
    u: np.ndarray
    v: np.ndarray
    spots: int
    plants: dict


def seed_state(V: np.ndarray, A: np.ndarray, wavelength: float, density: float, seed: int, origin: str = "scattered",
               allowed: np.ndarray | None = None, steps: int = 0) -> Start:
    """u = 1, v = 0 plus a faint seeded noise, and `density` spots per 100 wavelength^2 of surface (area-weighted,
    only where `allowed` > 0.5), each a disc of radius SEED_RADIUS x wavelength with u 0.5, v 0.25. Scattered: all
    at step 0. Crown / face / base: each spot is planted when a wave from that end of the model reaches it, the
    farthest at SPREAD x steps, so a colony creeps over the model and still covers it in time."""
    from scipy.spatial import cKDTree
    n = len(V)
    rng = np.random.default_rng(int(seed))
    u = np.ones(n, dtype=np.float32)
    v = rng.uniform(0.0, 0.01, n).astype(np.float32)
    if n == 0:
        return Start(u, v, 0, {})
    p = A * (allowed if allowed is not None else 1.0)
    if p.sum() <= 0:
        p = np.asarray(A, dtype=np.float64).copy()
    count = max(1, min(n, int(round(density / 100.0 * float(p.sum()) / wavelength ** 2))))
    centres = rng.choice(n, size=count, replace=False, p=p / p.sum())
    when = np.zeros(count, dtype=np.int64)
    if origin != "scattered":
        axis = np.asarray({"crown": (0, 0, 1), "face": (0, -1, 0), "base": (0, 0, -1)}[origin], float)
        h = V[centres] @ axis
        tip = V[centres[int(np.argmax(h))]]
        d = np.linalg.norm(V[centres] - tip, axis=1)
        when = np.round(d / max(float(d.max()), 1e-12) * SPREAD * steps).astype(np.int64)
    tree = cKDTree(V)
    r = SEED_RADIUS * wavelength
    plants: dict[int, list] = {}
    for c, t in zip(centres, when):
        idx = np.asarray(tree.query_ball_point(V[c], r), dtype=np.int64)
        if t <= 0:
            u[idx] = 0.5
            v[idx] = 0.25 + rng.uniform(-0.02, 0.02)
        else:
            plants.setdefault(int(t), []).append(idx)
    return Start(u, v, count, {t: np.unique(np.concatenate(ix)) for t, ix in plants.items()})


def simulate(op: Operator, chem: Chemistry, start: Start, steps: int, capture: list[int],
             progress=None, sweeps: int = SWEEPS) -> dict[int, np.ndarray]:
    """Run `steps` steps from `start`; return {step: v copy} for every step in `capture` (0 .. steps). A planted
    spot only takes where the surface is still bare (v < 0.05), so it never breaks a pattern already there."""
    u = start.u.astype(np.float32).copy()
    v = start.v.astype(np.float32).copy()
    want = sorted(set(int(c) for c in capture))
    out: dict[int, np.ndarray] = {}
    if 0 in want:
        out[0] = v.copy()
    Fv, kv = chem.feed, chem.kill
    cu, cv = np.float32(DT * chem.du), np.float32(DT * chem.dv)
    dt = np.float32(DT)
    one = np.float32(1.0)
    for s in range(1, int(steps) + 1):
        uvv = u * v * v
        ru = u + dt * (Fv * (one - u) - uvv)
        rv = v + dt * (uvv - (Fv + kv) * v)
        u = op.diffuse(ru, cu, sweeps)
        v = op.diffuse(rv, cv, sweeps)
        np.clip(u, 0.0, 1.0, out=u)
        np.clip(v, 0.0, 1.0, out=v)
        idx = start.plants.get(s)
        if idx is not None:
            bare = idx[v[idx] < 0.05]
            u[bare] = 0.5
            v[bare] = 0.25
        if s in want:
            out[s] = v.copy()
        if progress is not None and s % CAPTURE_EVERY == 0:
            progress(s / steps)
    return out


# ----------------------------------------------------------------------------- mesh helpers

def surface_area(V: np.ndarray, F: np.ndarray) -> float:
    V = np.asarray(V, dtype=np.float64)
    return float(0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1).sum())


def faces_for_spacing(area: float, spacing: float) -> int:
    """Faces of a roughly equilateral mesh with that edge length covering `area`."""
    return int(area / (math.sqrt(3) / 4.0 * spacing ** 2))


def refine_with(V: np.ndarray, F: np.ndarray, values: np.ndarray | None, max_len: float, budget: int):
    """Flow Warp's conforming edge splitting, carrying per-vertex values (linear at the new midpoints). The values
    ride along as extra coordinates scaled far below the edge lengths, so they never change which edges split.
    Returns (V, F, values or None, passes)."""
    from atlas_toolkit.tools.mesh_flow.mesh_flow import refine
    V = np.asarray(V, dtype=np.float64)
    if values is None:
        V2, F2, passes = refine(V, F, max_len, budget)
        return V2, F2, None, passes
    vals = np.asarray(values, dtype=np.float64).reshape(len(V), -1)
    eps = 1e-9 * max(float(max_len), 1e-12)
    V2, F2, passes = refine(np.hstack([V, vals * eps]), F, max_len, budget)
    return V2[:, :3], F2, (V2[:, 3:] / eps).reshape((len(V2),) + np.shape(values)[1:]), passes


def neighbour_matrix(n: int, F: np.ndarray):
    """Row-normalised uniform adjacency (for smoothing values and normals)."""
    import scipy.sparse as sp
    E = edges_of(F)
    i = np.concatenate([E[:, 0], E[:, 1]])
    j = np.concatenate([E[:, 1], E[:, 0]])
    M = sp.csr_matrix((np.ones(len(i)), (i, j)), shape=(n, n))
    deg = np.asarray(M.sum(1)).ravel()
    return sp.diags(1.0 / np.maximum(deg, 1.0)) @ M, deg > 0


def smooth(values: np.ndarray, S, has: np.ndarray, passes: int, keep: float = 0.5) -> np.ndarray:
    """Laplacian smoothing passes (x <- keep x + (1 - keep) mean of neighbours); isolated vertices keep theirs."""
    x = np.asarray(values, dtype=np.float64)
    for _ in range(int(passes)):
        avg = S @ x
        x = np.where(has.reshape((-1,) + (1,) * (x.ndim - 1)), keep * x + (1.0 - keep) * avg, x)
    return x


def vertex_normals(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    N = np.zeros_like(V, dtype=np.float64)
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])     # area weighted
    for c in range(3):
        np.add.at(N, F[:, c], fn)
    return N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)


def smooth_normals(V: np.ndarray, F: np.ndarray, S, has, passes: int) -> np.ndarray:
    N = smooth(vertex_normals(V, F), S, has, passes, keep=0.0)
    ln = np.linalg.norm(N, axis=1, keepdims=True)
    raw = vertex_normals(V, F)
    return np.where(ln > 1e-6, N / np.maximum(ln, 1e-12), raw)


def transfer(Vs: np.ndarray, vals: np.ndarray, Vt: np.ndarray, spacing: float, k: int = 6) -> np.ndarray:
    """Values from the simulation mesh's vertices to another mesh of the same surface (inverse-distance weights over
    the k nearest)."""
    from scipy.spatial import cKDTree
    k = min(k, len(Vs))
    d, i = cKDTree(Vs).query(Vt, k=k)
    if k == 1:
        d, i = d[:, None], i[:, None]
    w = 1.0 / np.maximum(d, 1e-3 * spacing) ** 2
    w /= w.sum(1, keepdims=True)
    vals = np.asarray(vals, dtype=np.float64)
    return np.einsum("ij,ij...->i...", w, vals[i])


def rim_fade(V: np.ndarray, F: np.ndarray, width: float) -> np.ndarray:
    """1 away from open edges, easing to 0 on them over `width` (normals on a cut rim point anywhere, so the
    relief would fray there)."""
    from scipy.spatial import cKDTree
    E = np.sort(np.asarray(F)[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    uniq, cnt = np.unique(E, axis=0, return_counts=True)
    rim = np.unique(uniq[cnt == 1].ravel())
    if not len(rim) or width <= 0:
        return np.ones(len(V))
    d, _ = cKDTree(V[rim]).query(V)
    t = np.clip(d / width, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


# ----------------------------------------------------------------------------- relief

def levels(v: np.ndarray, weight: np.ndarray | None = None) -> tuple[float, float]:
    """Normalisation levels: the 2nd and 98th percentiles of v (where weight > 0.5)."""
    x = v if weight is None or not (weight > 0.5).any() else v[weight > 0.5]
    lo, hi = np.percentile(x, [2.0, 98.0]) if len(x) else (0.0, 1.0)
    if hi - lo < 1e-6:
        hi = lo + 1e-6
    return float(lo), float(hi)


def shape(v: np.ndarray, lo: float, hi: float, sharpness: float, balance: float) -> np.ndarray:
    """v to 0..1: normalised, its midpoint moved by Balance (+: the raised share grows), its edges steepened by
    Sharpness (0: as grown, 1: near terraces). Exactly 0 below lo and 1 above hi for every setting."""
    t = np.clip((np.asarray(v, dtype=np.float64) - lo) / (hi - lo), 0.0, 1.0)
    mid = float(np.clip(0.5 - 0.4 * balance, 0.05, 0.95))
    # piecewise gamma so t = mid maps to 0.5
    g = math.log(0.5) / math.log(mid)
    t = t ** g
    a = 1.0 + 14.0 * float(np.clip(sharpness, 0.0, 1.0)) ** 1.5
    if a > 1.0001:
        s = 1.0 / (1.0 + np.exp(-a * (t - 0.5)))
        s0, s1 = 1.0 / (1.0 + math.exp(a * 0.5)), 1.0 / (1.0 + math.exp(-a * 0.5))
        t = (s - s0) / (s1 - s0)
    return t


def relief_offsets(t: np.ndarray, mode: str) -> np.ndarray:
    """0..1 pattern to signed offsets in depths: raised 0..1, engraved 0..-1, both -0.5..0.5."""
    if mode == "raised":
        return t
    if mode == "engraved":
        return -t
    if mode == "both":
        return t - 0.5
    raise ValueError(f"unknown relief {mode!r}")
