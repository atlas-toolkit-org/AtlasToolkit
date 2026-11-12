"""Arc-length spine bending with rotation-minimising sections; no GUI or file I/O.

In the bend frame, s is longitudinal and u,v are offsets from the neutral spine.
F(u,v,s) = C(s) + u E0(s) + v E1(s), with C'(s) = T(s), |T| = 1.
Outside the range C extends along the endpoint tangent and the section is rigid.
An inverse rigid transform fixes the anchor section exactly. No displacement blending.
"""
from __future__ import annotations

import math
import numpy as np
from scipy.integrate import cumulative_trapezoid


def basis(axis):
    """Right-handed bend coordinates -> canonical frame."""
    return np.eye(3)[:, {"z": [0, 1, 2], "x": [1, 2, 0], "y": [2, 0, 1]}[axis]]


def profile(t, kind, waves=1.5):
    if kind == "arc":
        return t
    if kind == "soft":
        return t - np.sin(2 * np.pi * t) / (2 * np.pi)
    if kind == "s":
        return np.sin(2 * np.pi * t)
    if kind == "wave":
        raw = np.sin(2 * np.pi * waves * t) * np.sin(np.pi * t) ** 2
        grid = np.linspace(0, 1, 8193)
        peak = np.abs(np.sin(2 * np.pi * waves * grid) * np.sin(np.pi * grid) ** 2).max()
        return raw / max(float(peak), 1e-12)
    raise ValueError(f"Unknown bend profile: {kind}")


class Spine:
    def __init__(self, bounds, p, amount=1., angle_scale=1., samples=2049):
        self.B = basis(p.bend_axis)
        # Cyclic permutations: min/max can be transformed directly.
        self.bounds = np.asarray(bounds, float) @ self.B
        self.height = float(bounds[1, 2] - bounds[0, 2])
        extent = float(self.bounds[1, 2] - self.bounds[0, 2])
        if extent <= 1e-12:
            raise ValueError("The model has no extent along the bend axis. Choose another Bend along axis.")
        if not 0 <= p.bend_from < p.bend_to <= 100:
            raise ValueError("Bend From must be below To (between 0 and 100%).")
        self.start = self.bounds[0, 2] + extent * float(p.bend_from) / 100
        self.length = extent * (float(p.bend_to) - float(p.bend_from)) / 100
        self.neutral = self.bounds.mean(axis=0)[:2] + self.height / 100 * np.array([p.neutral_u, p.neutral_v])
        self.angle = float(p.angle) * amount * angle_scale
        self.roll = float(p.roll) * amount
        self.identity = abs(self.angle) < 1e-12 and abs(self.roll) < 1e-12
        t = np.linspace(0, 1, samples)
        theta = np.radians(self.angle) * profile(t, p.profile, float(p.waves))
        phi = np.radians(float(p.direction) + float(p.sweep) * t)
        T = np.column_stack((np.sin(theta) * np.cos(phi), np.sin(theta) * np.sin(phi), np.cos(theta)))
        E = np.empty_like(T)
        E[0] = [1., 0., 0.]
        for i in range(1, len(t)):
            a, b = T[i - 1], T[i]
            cross = np.cross(a, b)
            c = float(a @ b)
            # Consecutive samples are close even at the largest supported settings.
            n = E[i - 1] + np.cross(cross, E[i - 1]) + np.cross(cross, np.cross(cross, E[i - 1])) / (1 + c)
            n -= b * (n @ b)
            E[i] = n / np.linalg.norm(n)
        V = np.cross(T, E)
        roll = np.radians(self.roll) * (t - np.sin(2 * np.pi * t) / (2 * np.pi))
        N = E * np.cos(roll[:, None]) + V * np.sin(roll[:, None])
        self.t, self.T, self.N = t, T, N
        self.C = cumulative_trapezoid(T, t * self.length, axis=0, initial=0)
        # Differential bound on det(dF); conservative across the entire transverse box.
        dtheta = np.gradient(theta, t * self.length, edge_order=2)
        dphi = math.radians(float(p.sweep)) / self.length
        dT = np.column_stack((dtheta * np.cos(theta) * np.cos(phi) - dphi * np.sin(theta) * np.sin(phi),
                              dtheta * np.cos(theta) * np.sin(phi) + dphi * np.sin(theta) * np.cos(phi),
                              -dtheta * np.sin(theta)))
        k0, k1 = np.einsum("ij,ij->i", dT, N), np.einsum("ij,ij->i", dT, np.cross(T, N))
        uv_lo, uv_hi = self.bounds[:, :2] - self.neutral
        compression = np.maximum(uv_lo[0] * k0, uv_hi[0] * k0) + np.maximum(uv_lo[1] * k1, uv_hi[1] * k1)
        self.min_jacobian = float(1 - compression.max())
        self.anchor_t = {"base": 0., "centre": .5, "tip": 1.}[p.anchor]
        ac, an, av, at = self.sections(np.array([self.start + self.anchor_t * self.length]))
        self.anchor_C = ac[0]
        self.anchor_R = np.column_stack((an[0], av[0], at[0]))
        self.anchor_P = np.array([0., 0., self.start + self.anchor_t * self.length])

    def sections(self, s):
        s = np.asarray(s, float)
        t = np.clip((s - self.start) / self.length, 0, 1)
        def interp(array):
            return np.column_stack([np.interp(t, self.t, array[:, j]) for j in range(3)])
        T = interp(self.T)
        T /= np.linalg.norm(T, axis=1, keepdims=True)
        N = interp(self.N)
        N -= T * np.einsum("ij,ij->i", N, T)[:, None]
        N /= np.linalg.norm(N, axis=1, keepdims=True)
        V = np.cross(T, N)
        C = interp(self.C) + (s - self.start - t * self.length)[:, None] * T
        return C, N, V, T

    def deform(self, points):
        points = np.asarray(points, float)
        if self.identity:
            return points.copy()
        local = points @ self.B
        uv = local[:, :2] - self.neutral
        C, N, V, _ = self.sections(local[:, 2])
        Q = C + uv[:, 0, None] * N + uv[:, 1, None] * V
        Q = (Q - self.anchor_C) @ self.anchor_R + self.anchor_P
        Q[:, :2] += self.neutral
        return Q @ self.B.T

    def guide(self, samples=181):
        s = np.linspace(self.bounds[0, 2], self.bounds[1, 2], samples)
        points = np.column_stack((np.full_like(s, self.neutral[0]), np.full_like(s, self.neutral[1]), s))
        return self.deform(points @ self.B.T)


def protected_spine(bounds, p, amount, ctx=None, samples=2049):
    """Reduce bending only, retaining requested roll; deterministic conservative local fold guard."""
    scale = 1.
    for attempt in range(32):
        if ctx:
            ctx.check_cancel()
        spine = Spine(bounds, p, amount, scale, samples)
        if not p.fold_guard or spine.min_jacobian >= .15 or abs(spine.angle) < 1e-10:
            return spine, scale
        # Gradual reduction handles non-monotone compression for large spatial turns.
        scale *= min(.9, .82 / max(1 - spine.min_jacobian, .85))
    raise ValueError("Unable to protect the inner curve. Extend the bend range or reduce Angle / Plane sweep.")


# Conforming red/green edge templates; all neighbours share the same midpoint index.
TEMPLATES = {0: ((0, 1, 2),), 1: ((0, 3, 2), (3, 1, 2)), 2: ((1, 4, 0), (4, 2, 0)),
             4: ((2, 5, 1), (5, 0, 1)), 3: ((1, 4, 3), (0, 3, 2), (3, 4, 2)),
             6: ((2, 5, 4), (1, 4, 0), (4, 5, 0)), 5: ((0, 3, 5), (2, 5, 1), (5, 3, 1)),
             7: ((0, 3, 5), (3, 1, 4), (5, 4, 2), (3, 4, 5))}


def refine(vertices, faces, spine, segments, budget, ctx):
    """Split only where longitudinal edges cross the bend, with longest-edge closure.

    Linear subdivision preserves the exact input surface, boundaries and winding.
    No preview simplification and no Loop smoothing of scanned relief.
    """
    v, f = np.asarray(vertices, float), np.asarray(faces, np.int64)
    max_span = spine.length / segments
    for iteration in range(28):
        ctx.progress(.1 + min(iteration, 20) * .015, f"Refining bend: {len(f):,} faces")
        raw = np.stack((f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]), axis=1)
        edges, inv = np.unique(np.sort(raw.reshape(-1, 2), axis=1), axis=0, return_inverse=True)
        inv = inv.reshape(-1, 3)
        s = np.clip((v @ spine.B)[:, 2], spine.start, spine.start + spine.length)
        split = np.abs(s[edges[:, 1]] - s[edges[:, 0]]) > max_span * (1 + 1e-9)
        if not split.any():
            return v, f
        length2 = np.sum((v[edges[:, 1]] - v[edges[:, 0]]) ** 2, axis=1)
        longest = inv[np.arange(len(f)), np.argmax(length2[inv], axis=1)]
        while True:
            ctx.check_cancel()
            missing = split[inv].any(axis=1) & ~split[longest]
            if not missing.any():
                break
            split[longest[missing]] = True
        requested = int(len(f) + np.count_nonzero(split[inv]))
        if requested > budget or requested * 768 > 1536 * 1024 ** 2:
            raise ValueError(f"Bend refinement needs at least {requested:,} faces; limit {budget:,}. "
                             "Lower Bend resolution, simplify the input, or increase Maximum faces.")
        midpoint = np.full(len(edges), -1, np.int64)
        midpoint[split] = np.arange(split.sum()) + len(v)
        mids = midpoint[inv]
        code = (mids >= 0).astype(np.uint8) @ np.array([1, 2, 4], np.uint8)
        corners = np.column_stack((f, mids))
        parts = [corners[code == k][:, template] for k, templates in TEMPLATES.items() for template in templates]
        v = np.vstack((v, v[edges[split]].mean(axis=1)))
        f = np.vstack(parts)
    raise ValueError("Bend refinement did not converge. Reduce Bend resolution or simplify the source.")
