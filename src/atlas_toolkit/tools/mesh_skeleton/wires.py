"""
Wires: curves on the skin dressed as metal.

A Wire is a centre line with shared end nodes. `dress` resamples, smooths and straightens the lines, lays them on the
surface and forges them (wobble, overrun, thickness that swells at joints and thins at free ends). `sweep` turns them
into closed tubes, straps or fins whose outer face lies on the model's skin, so the silhouette is kept.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

ICO: dict = {}


@dataclass
class Wire:
    points: np.ndarray
    closed: bool = False
    ends: tuple = (-1, -1)          # shared node ids; -1 is a free end
    weight: float = 1.0
    follow: float = 1.0             # 1 lies on the skin, 0 the straight chord (Straightness)
    normals: np.ndarray = field(default=None, repr=False)
    radii: np.ndarray = field(default=None, repr=False)


@dataclass
class Dress:
    """How a layer's wires look."""
    wire: float                     # diameter or width, model units
    profile: str = "round"          # round | strap | fin
    flat: float = 4.0               # width to thickness of a strap or fin
    sides: int = 6
    step: float = 0.05
    smooth: int = 4
    settle: int = 3                 # passes after laying on the skin: wire rides over small lumps instead of into them
    vary: float = 0.0
    swell: float = 0.0
    taper: float = 0.0
    wobble: float = 0.0             # amplitude, model units
    scale: float = 0.1              # the length of a wobble and of thickness changes
    overrun: float = 0.0            # model units past a joint
    hubs: bool = True
    seed: int = 1


def noise3(P: np.ndarray, scale: float, seed: int, channels: int = 1) -> np.ndarray:
    """Smooth seeded noise of position, about -1..1: two wires through one point agree there, so joints hold."""
    rng = np.random.default_rng(seed + 7919)
    out = np.zeros((len(P), channels))
    for c in range(channels):
        k = rng.normal(size=(7, 3)) / max(scale, 1e-9)
        phase = rng.uniform(0.0, 2.0 * np.pi, 7)
        out[:, c] = np.sin(P @ k.T + phase).sum(1) / 2.2
    return np.clip(out, -1.0, 1.0)[:, 0] if channels == 1 else np.clip(out, -1.0, 1.0)


def arc_length(P: np.ndarray, closed: bool = False) -> np.ndarray:
    Q = np.vstack([P, P[:1]]) if closed else P
    return np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(Q, axis=0), axis=1))]


def resample(P: np.ndarray, step: float, closed: bool = False) -> np.ndarray:
    Q = np.vstack([P, P[:1]]) if closed else P
    s = arc_length(P, closed)
    total = float(s[-1])
    if total <= 0.0:
        return P[:2] if len(P) >= 2 else P
    count = max(3 if closed else 2, int(round(total / step)))
    at = np.linspace(0.0, total, count + (0 if closed else 1), endpoint=not closed)
    keep = np.r_[True, np.diff(s) > 0]
    return np.stack([np.interp(at, s[keep], Q[keep, c]) for c in range(3)], 1)


def laplacian(P: np.ndarray, passes: int, closed: bool) -> np.ndarray:
    """Smooth a line in place of its noise, with its ends pinned."""
    if len(P) < 3:
        return P
    Q = P.copy()
    for _ in range(int(passes)):
        if closed:
            Q = 0.5 * Q + 0.25 * (np.roll(Q, 1, axis=0) + np.roll(Q, -1, axis=0))
        else:
            Q[1:-1] = 0.5 * Q[1:-1] + 0.25 * (Q[:-2] + Q[2:])
    return Q


def dress(wires: list[Wire], skin, d: Dress) -> list[Wire]:
    """Lay the wires on the surface and give them their shape and thickness."""
    wires = [w for w in wires if len(w.points) >= 2]
    if not wires:
        return []
    rng = np.random.default_rng(d.seed + 311)
    lines = [laplacian(resample(w.points, d.step, w.closed), d.smooth, w.closed) for w in wires]
    sizes = [len(q) for q in lines]
    cut = np.cumsum(sizes)[:-1]
    laid, normals = skin.project(np.vstack(lines))
    laid, normals = np.split(laid, cut), np.split(normals, cut)
    for w, on, n in zip(wires, laid, normals):
        on = laplacian(on, d.settle, w.closed)
        n = laplacian(n, d.settle, w.closed)
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
        if w.follow < 1.0 and not w.closed and len(on) >= 2:
            s = arc_length(on)
            chord = on[0] + (on[-1] - on[0]) * (s / max(s[-1], 1e-12))[:, None]
            on = on * w.follow + chord * (1.0 - w.follow)
        w.points, w.normals = on, n
    for w in wires:
        if d.overrun > 0.0 and not w.closed and len(w.points) >= 2:
            w.points, w.normals = _overrun(w, d.overrun, rng)
    if d.wobble > 0.0:
        for w in wires:
            shift = noise3(w.points, d.scale, d.seed, 3) * d.wobble
            shift -= np.sum(shift * w.normals, axis=1, keepdims=True) * w.normals * 0.5
            w.points = w.points + shift
    for w in wires:
        w.radii = _radii(w, d, rng)
    return wires


def _overrun(w: Wire, amount: float, rng) -> tuple[np.ndarray, np.ndarray]:
    """Rods that run past their joints, like hand-tied wire."""
    P, N = w.points, w.normals
    head = P[0] - P[1]
    tail = P[-1] - P[-2]
    a = amount * rng.uniform(0.35, 1.0) / max(np.linalg.norm(head), 1e-9)
    b = amount * rng.uniform(0.35, 1.0) / max(np.linalg.norm(tail), 1e-9)
    w.ends = (-1, -1)
    return np.vstack([P[0] + head * a, P, P[-1] + tail * b]), np.vstack([N[0], N, N[-1]])


def _radii(w: Wire, d: Dress, rng) -> np.ndarray:
    P = w.points
    r = np.full(len(P), 0.5 * d.wire * w.weight)
    if d.vary > 0.0:
        r *= 1.0 + d.vary * noise3(P, d.scale * 1.5, d.seed + 3)
    if w.closed:
        return np.maximum(r, 1e-6)
    s = arc_length(P)
    total = max(float(s[-1]), 1e-9)
    if d.swell > 0.0:
        reach = min(d.scale * 0.45, total * 0.5)
        for at, node in ((s, w.ends[0]), (total - s, w.ends[1])):
            if node >= 0:
                r *= 1.0 + d.swell * np.exp(-(at / max(reach, 1e-9)) ** 2)
    if d.taper > 0.0:
        reach = max(total * 0.5 * d.taper, 1e-9)
        for at, node in ((s, w.ends[0]), (total - s, w.ends[1])):
            if node < 0:
                r *= np.clip(at / reach, 0.0, 1.0) ** 0.6 * 0.85 + 0.15
    return np.maximum(r, 0.5 * d.wire * 0.04)


def _unit_ring(profile: str, sides: int) -> np.ndarray:
    """A ring around the line as (along the normal, along the binormal) in the unit box, counter-clockwise about it."""
    if profile == "round":
        angle = np.linspace(0.0, 2.0 * np.pi, sides, endpoint=False)
        return np.stack([np.cos(angle), np.sin(angle)], 1)
    return np.array([[1.0, -1.0], [1.0, 1.0], [-1.0, 1.0], [-1.0, -1.0]])


def half_sizes(radii: np.ndarray, d: Dress) -> tuple[np.ndarray, np.ndarray]:
    """Half extents (along the normal, along the binormal) for the profile: a strap lies flat on the skin, a fin stands
    up from it, inward."""
    if d.profile == "strap":
        return radii / max(d.flat, 1.0), radii
    if d.profile == "fin":
        return radii, radii / max(d.flat, 1.0)
    return radii, radii


def sweep_one(w: Wire, d: Dress) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One closed wire as (V, F) plus its centre line after the inset."""
    P, N = w.points, w.normals
    closed = w.closed
    T = np.empty_like(P)
    if closed:
        T = np.roll(P, -1, axis=0) - np.roll(P, 1, axis=0)
    else:
        T[1:-1] = P[2:] - P[:-2]
        T[0], T[-1] = P[1] - P[0], P[-1] - P[-2]
    T /= np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-12)
    n = N - np.sum(N * T, axis=1, keepdims=True) * T
    length = np.linalg.norm(n, axis=1, keepdims=True)
    fallback = np.cross(T, [0.0, 0.0, 1.0])
    fallback[np.linalg.norm(fallback, axis=1) < 1e-6] = [1.0, 0.0, 0.0]
    n = np.where(length > 1e-6, n / np.maximum(length, 1e-6), fallback)
    b = np.cross(T, n)
    across_n, across_b = half_sizes(w.radii, d)
    # the outer face touches the skin: the centre sits one half size inward
    centre = P - n * across_n[:, None]
    ring = _unit_ring(d.profile, d.sides)
    count, sides = len(P), len(ring)
    grid = (centre[:, None, :]
            + (ring[:, 0][None, :, None] * across_n[:, None, None]) * n[:, None, :]
            + (ring[:, 1][None, :, None] * across_b[:, None, None]) * b[:, None, :])
    V = grid.reshape(-1, 3)
    i = np.arange(count if closed else count - 1)
    j = np.arange(sides)
    i0, j0 = np.meshgrid(i, j, indexing="ij")
    i1, j1 = (i0 + 1) % count, (j0 + 1) % sides
    a, b_, c, e = i0 * sides + j0, i0 * sides + j1, i1 * sides + j1, i1 * sides + j0
    F = np.vstack([np.stack([a, b_, c], -1).reshape(-1, 3), np.stack([a, c, e], -1).reshape(-1, 3)])
    if not closed:
        first, last = len(V), len(V) + 1
        V = np.vstack([V, centre[0], centre[-1]])
        start, end = np.arange(sides), (count - 1) * sides + np.arange(sides)
        F = np.vstack([F, np.stack([np.full(sides, first), np.roll(start, -1), start], 1),
                       np.stack([np.full(sides, last), end, np.roll(end, -1)], 1)])
    return V, F, centre


def _ico(fine: bool) -> tuple[np.ndarray, np.ndarray]:
    """A joint: as round as the wire, so a smooth rod's joints don't draw as little facets."""
    if fine not in ICO:
        import trimesh
        m = trimesh.creation.icosphere(subdivisions=2 if fine else 1, radius=1.0)
        ICO[fine] = np.asarray(m.vertices, np.float64), np.asarray(m.faces, np.int64)
    return ICO[fine]


def sweep(wires: list[Wire], d: Dress) -> list[tuple[np.ndarray, np.ndarray]]:
    """Every wire and joint as a separate closed solid."""
    solids, joints = [], {}
    for w in wires:
        if len(w.points) < 2:
            continue
        V, F, centre = sweep_one(w, d)
        solids.append((V, F))
        for node, at, radius in ((w.ends[0], centre[0], w.radii[0]), (w.ends[1], centre[-1], w.radii[-1])):
            if node >= 0:
                joints.setdefault(node, []).append((at, radius))
    if d.hubs and d.profile == "round":
        unit_v, unit_f = _ico(d.sides >= 10)
        for around in joints.values():
            if len(around) < 2:
                continue
            at = np.mean([a for a, _ in around], axis=0)
            radius = max(r for _, r in around) * 1.04
            solids.append((unit_v * radius + at, unit_f))
    return solids


def expected_faces(wires: list[Wire], d: Dress) -> int:
    sides = d.sides if d.profile == "round" else 4
    return int(sum(2 * len(w.points) * sides for w in wires))
