"""
mesh_strata.py - Strata Sculpt: cut a model into an ordered stack of capped slabs along one direction and dislocate
them as a stack: the slabs thin into wafers, float apart, sway in a wave, turn into a spiral staircase and, seeded,
slip out of line.

Everything happens in the model's canonical frame (geom/frame3d: up = Z, front = -Y, centred, 2 units tall), so
lengths are % of the height and settings carry over between models.

The stack. A unit normal n (up, front to back or side to side, tilted by up to 80°) and the model's extent along it,
split into N layers of equal pitch. Layer k is centred at c_k; it keeps a share r_k of its pitch around c_k, the rest
is cut away (planar, capped cuts: geom/cutting.partition), so every slab of a watertight model is closed and toroidal
sections keep their holes.

Each layer k moves rigidly, scaled by its own amount u_k = amount × w_k (w_k: the influence weight at the layer's rest
centroid, geom/influence), with a_k its place in the stack from the anchor (base: 0..1, centre: -½..½, top: -1..0):

    thickness   r_k = 1 - u_k (1 - retained)              full partition at u = 0, the chosen wafers at u = 1
    lift        D_k = Σ_{j<=k} sep · pitch · (u_{j-1} + u_j) / 2, less the anchor layer's D   (along n)
    wave        A u_k sin(2π waves s_k + φ) along the heading (planar), or A u_k (cos, sin)(…) around n (orbit)
    turn        T u_k a_k about the stack axis (through the model's centre, along n)
    dislocation seeded per layer, a share of layers only: a sideways shift, a spin about the layer's own centre
                and a tilt about a sideways axis through it; the draws are fixed per layer, then scaled by u_k

so amount 0 is the untouched model, a sequence grows smoothly and any stage only depends on its own amount.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from atlas_toolkit.geom import cutting, influence

# stack normal and two sideways axes for each direction (canonical: X across, -Y front, Z up)
DIRECTIONS = {
    "up": (np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])),
    "front": (np.array([0.0, 1.0, 0.0]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0])),
    "side": (np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0])),
}
ANCHOR = {"base": 0.0, "centre": 0.5, "top": 1.0}


def rotation(axis, angle: float) -> np.ndarray:
    """Rodrigues: rotation by `angle` (radians) about the unit `axis`."""
    k = np.asarray(axis, dtype=np.float64)
    k = k / (np.linalg.norm(k) or 1.0)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(angle) * K + (1 - math.cos(angle)) * (K @ K)


def basis(direction: str = "up", tilt: float = 0.0, tilt_toward: float = 0.0) -> tuple:
    """(n, e1, e2): the stack normal tilted by `tilt` degrees toward the heading `tilt_toward` (0 = the first sideways
    axis), and two unit sideways axes, all orthonormal and right-handed (e1 × e2 = n)."""
    n0, a0, b0 = DIRECTIONS[direction]
    h, t = math.radians(tilt_toward), math.radians(tilt)
    toward = math.cos(h) * a0 + math.sin(h) * b0
    n = math.cos(t) * n0 + math.sin(t) * toward
    n /= np.linalg.norm(n)
    e1 = a0 - (a0 @ n) * n
    if np.linalg.norm(e1) < 1e-6:
        e1 = b0 - (b0 @ n) * n
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    return n, e1, e2


@dataclass
class Stack:
    """The layers of a model at rest: normal, sideways axes, extent and pitch along the normal, rest centroids."""
    n: np.ndarray
    e1: np.ndarray
    e2: np.ndarray
    lo: float
    pitch: float
    count: int
    centroids: np.ndarray            # (count, 3) rest centroid of each layer (canonical)

    def centre(self, k) -> np.ndarray:
        return self.lo + (np.asarray(k, dtype=np.float64) + 0.5) * self.pitch

    @property
    def place(self) -> np.ndarray:
        """s_k: 0 for the first layer, 1 for the last."""
        return np.arange(self.count) / max(self.count - 1, 1)


def stack(V, layers: int, direction: str = "up", tilt: float = 0.0, tilt_toward: float = 0.0) -> Stack:
    """Layer the canonical vertices V along the chosen direction. A layer's rest centroid is the mean of the vertices
    in it, or the point of the stack axis at its centre when none are (a layer crossed by long faces only)."""
    V = np.asarray(V, dtype=np.float64)
    layers = int(layers)
    if layers < 1:
        raise ValueError("at least one layer")
    n, e1, e2 = basis(direction, tilt, tilt_toward)
    d = V @ n
    lo, hi = float(d.min()), float(d.max())
    pitch = (hi - lo) / layers or 1.0
    idx = np.clip(((d - lo) / pitch).astype(np.int64), 0, layers - 1)
    cnt = np.bincount(idx, minlength=layers).astype(np.float64)
    sums = np.stack([np.bincount(idx, V[:, i], minlength=layers) for i in range(3)], axis=1)
    axis_pts = np.outer(lo + (np.arange(layers) + 0.5) * pitch, n)
    cent = np.where(cnt[:, None] > 0, sums / np.maximum(cnt, 1)[:, None], axis_pts)
    return Stack(n, e1, e2, lo, pitch, layers, cent)


@dataclass
class Draws:
    """The seeded dice of every layer, fixed for a seed and a layer count (so a sequence never reseeds)."""
    chosen: np.ndarray       # in [0, 1): a layer is dislocated when below the share
    shift_dir: np.ndarray    # sideways angle of the shift
    shift_mag: np.ndarray    # 0..1
    spin: np.ndarray         # -1..1
    tilt_dir: np.ndarray     # sideways angle of the tilt axis
    tilt: np.ndarray         # -1..1


def draws(count: int, seed: int) -> Draws:
    rng = np.random.default_rng(int(seed))
    a = rng.random((6, count))
    return Draws(a[0], a[1] * 2 * math.pi, np.sqrt(a[2]), a[3] * 2 - 1, a[4] * 2 * math.pi, a[5] * 2 - 1)


def layer_amounts(st: Stack, amount: float, values, lo, hi) -> np.ndarray:
    """u_k: the amount, scaled by the influence weight at each layer's rest centroid."""
    return float(amount) * influence.weights(st.centroids, values, lo, hi)


def thickness(u: np.ndarray, retained: float) -> np.ndarray:
    """r_k: the share of its pitch each layer keeps (1 at rest, `retained` at full amount)."""
    return np.clip(1.0 - np.asarray(u) * (1.0 - float(retained)), 0.02, 1.0)


def cuts_for(st: Stack, r: np.ndarray, eps: float = 1e-9) -> list[float]:
    """The planes (positions along n) that cut each layer down to its share, without coincident duplicates."""
    out: list[float] = []
    for k in range(st.count):
        c, half = float(st.centre(k)), 0.5 * float(r[k]) * st.pitch
        for v in (c - half, c + half):
            if k == 0 and v <= st.lo + eps or k == st.count - 1 and v >= st.lo + st.count * st.pitch - eps:
                continue        # the model's own ends
            if not out or v > out[-1] + eps * max(1.0, st.pitch):
                out.append(v)
    return out


def classify(st: Stack, r: np.ndarray, mid: float) -> int:
    """The layer a piece centred at `mid` (along n) belongs to, or -1 for a cut-away gap."""
    k = int(np.clip(math.floor((mid - st.lo) / st.pitch), 0, st.count - 1))
    return k if abs(mid - float(st.centre(k))) <= 0.5 * float(r[k]) * st.pitch + 1e-9 else -1


def lifts(st: Stack, u: np.ndarray, separation: float, anchor: str = "base") -> np.ndarray:
    """D_k: how far each layer rises along n, `separation` (share of a pitch) between neighbours that move."""
    g = np.zeros(st.count)
    if st.count > 1:
        g[1:] = float(separation) * st.pitch * (u[:-1] + u[1:]) / 2.0
    D = np.cumsum(g)
    at = ANCHOR[anchor] * (st.count - 1)
    return D - float(np.interp(at, np.arange(st.count), D))


def motions(st: Stack, u: np.ndarray, p, dice: Draws) -> list[tuple[np.ndarray, np.ndarray]]:
    """Per layer, the rigid motion (R, t) applied as x -> R x + t (canonical units: the model is 2 tall)."""
    h = 2.0 / 100.0                                  # % of the height -> canonical units
    s = st.place
    a = s - ANCHOR[p["anchor"]]
    D = lifts(st, u, p["separation"] / 100.0, p["anchor"])
    heading = math.radians(p["wave_heading"])
    side = math.cos(heading) * st.e1 + math.sin(heading) * st.e2
    theta = 2 * math.pi * p["waves"] * s + math.radians(p["phase"])
    if p["wave_shape"] == "orbit":
        wave = (np.cos(theta)[:, None] * st.e1 + np.sin(theta)[:, None] * st.e2)
        wave = wave - (math.cos(math.radians(p["phase"])) * st.e1 + math.sin(math.radians(p["phase"])) * st.e2) \
            if p["anchor"] == "base" else wave
    else:
        wave = np.sin(theta)[:, None] * side
    share = p["share"] / 100.0
    out = []
    for k in range(st.count):
        uk = float(u[k])
        if uk == 0.0:
            out.append((np.eye(3), np.zeros(3)))
            continue
        c = st.centroids[k]
        R_turn = rotation(st.n, math.radians(p["turn"]) * uk * a[k])
        R_loc = np.eye(3)
        t_extra = np.zeros(3)
        if dice.chosen[k] < share:
            R_loc = rotation(st.n, math.radians(p["jitter_spin"]) * uk * dice.spin[k])
            tilt_axis = math.cos(dice.tilt_dir[k]) * st.e1 + math.sin(dice.tilt_dir[k]) * st.e2
            R_loc = rotation(tilt_axis, math.radians(p["jitter_tilt"]) * uk * dice.tilt[k]) @ R_loc
            ang = dice.shift_dir[k]
            t_extra = (p["jitter_shift"] * h * uk * dice.shift_mag[k]
                       * (math.cos(ang) * st.e1 + math.sin(ang) * st.e2))
        R = R_turn @ R_loc
        t = R_turn @ (c - R_loc @ c) + D[k] * st.n + p["wave"] * h * uk * wave[k] + t_extra
        out.append((R, t))
    return out


def sculpt(mesh, st: Stack, u: np.ndarray, p, dice: Draws, progress=None, *, ctx=None, connector_plan=None,
           connection_progress=None):
    """The slabs of one stage: cut (capped), moved, combined. `mesh` is canonical. Returns (mesh, info)."""
    import trimesh
    if not np.any(u > 0):
        return mesh.copy(), {"layers": 0, "pieces": 1, "cuts": 0, "moved": 0.0}
    if p.get("connectors"):
        from . import connectors
        return connectors.sculpt(mesh, st, u, p, dice, progress, ctx, connector_plan, connection_progress)
    r = thickness(u, p["retained"] / 100.0)
    cuts = cuts_for(st, r)
    pieces = cutting.partition(mesh, st.n, cuts, cap=True, progress=progress, engine=cutting.best_engine())
    slabs: dict[int, list] = {}
    for _, piece in pieces:
        lo, hi = cutting.span(piece, st.n)
        k = classify(st, r, (lo + hi) / 2.0)
        if k >= 0:
            slabs.setdefault(k, []).append(piece)
    moves = motions(st, u, p, dice)
    parts, moved = [], 0.0
    for k in sorted(slabs):
        R, t = moves[k]
        for piece in slabs[k]:
            V = np.asarray(piece.vertices) @ R.T + t
            moved = max(moved, float(np.linalg.norm(V - piece.vertices, axis=1).max()))
            parts.append(trimesh.Trimesh(V, piece.faces, process=False))
    if not parts:
        raise ValueError("nothing left after cutting: raise the retained thickness")
    out = trimesh.util.concatenate(parts)
    return out, {"layers": len(slabs), "pieces": len(parts), "cuts": len(cuts), "moved": moved,
                 "closed": sum(1 for q in parts if q.is_watertight)}
