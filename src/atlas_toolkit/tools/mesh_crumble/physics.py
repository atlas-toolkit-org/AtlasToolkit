"""
physics.py - A small rigid-body solver for falling convex blocks: position-based dynamics with small substeps.

Blocks collide with each other (the sample points of one against the planes of the other's convex hull), with the
standing body (a signed distance grid) and with a floor plane. Each substep moves the blocks, finds the contacts and
corrects the penetrations with static friction; the velocities follow from the corrected positions, so a pile stays
stable without a stiff contact model. Blocks that stop moving go to sleep, so a settled heap costs nothing.

All lengths are canonical (a model 2 tall), so 1 unit behaves like 1 metre. NumPy only, seeded by its inputs alone.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLES = 32            # contact points per block at most
PLANES = 22             # hull planes per block at most
GRAVITY = 9.81
SUBSTEPS_PER_SECOND = 200
ITERATIONS = 2
SOR = 1.35              # over-relaxation of the averaged (Jacobi) corrections
MAX_SPEED = 7.0
SLEEP_SPEED = 0.035
SLEEP_STEPS = 24
WAKE_SPEED = 0.6


@dataclass
class Shape:
    """The collision shape of one block, in its local frame (origin at the centre of mass)."""
    samples: np.ndarray     # (k, 3)
    normals: np.ndarray     # (m, 3)
    offsets: np.ndarray     # (m,)
    radius: float


def collision_shape(points: np.ndarray) -> Shape:
    """A convex hull with a bounded number of planes and sample points, from a block's vertices around its centre."""
    from scipy.spatial import ConvexHull, QhullError
    pts = np.asarray(points, dtype=np.float64)
    radius = float(np.linalg.norm(pts, axis=1).max()) if len(pts) else 0.0
    try:
        hull = ConvexHull(pts)
        corners = pts[hull.vertices]
        if len(corners) > SAMPLES * 2 // 3:         # the farthest corners keep the block's shape
            keep = [int(np.argmax(np.linalg.norm(corners, axis=1)))]
            dist = np.linalg.norm(corners - corners[keep[0]], axis=1)
            for _ in range(SAMPLES * 2 // 3 - 1):
                keep.append(int(np.argmax(dist)))
                dist = np.minimum(dist, np.linalg.norm(corners - corners[keep[-1]], axis=1))
            hull = ConvexHull(corners[keep])
            pts = corners[keep]
            corners = pts[hull.vertices]
    except (QhullError, ValueError):                # flat or tiny: a small box around it stands in
        size = max(radius, 1e-4)
        corners = np.array([[x, y, z] for x in (-size, size) for y in (-size, size) for z in (-size, size)]) * 0.5
        normals = np.vstack([np.eye(3), -np.eye(3)])
        return Shape(corners, normals, np.full(6, size * 0.5), radius=max(radius, size))
    tri = pts[hull.simplices]
    areas = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    order = np.argsort(-areas, kind="stable")
    equations = hull.equations[order]
    _, first = np.unique(np.round(equations, 5), axis=0, return_index=True)
    chosen = np.sort(first)[:PLANES]
    equations = equations[chosen]
    extra = tri[order[:max(0, SAMPLES - len(corners))]].mean(axis=1)      # the largest faces, so edges do not cut in
    return Shape(np.vstack([corners, extra]), equations[:, :3], -equations[:, 3], radius)


class Field:
    """Signed distance on a regular grid, trilinear; no contact outside it."""

    def __init__(self, values: np.ndarray, origin: np.ndarray, step: float):
        self.values, self.origin, self.step = values, np.asarray(origin, float), float(step)
        self.limit = np.array(values.shape) - 2

    def distance(self, P: np.ndarray) -> np.ndarray:
        g = (P - self.origin) / self.step
        i = np.floor(g).astype(np.int64)
        outside = (i < 0).any(axis=1) | (i > self.limit).any(axis=1)
        i = np.clip(i, 0, self.limit)
        t = g - i
        V = self.values
        x, y, z = i[:, 0], i[:, 1], i[:, 2]
        tx, ty, tz = t[:, 0], t[:, 1], t[:, 2]
        c00 = V[x, y, z] * (1 - tx) + V[x + 1, y, z] * tx
        c10 = V[x, y + 1, z] * (1 - tx) + V[x + 1, y + 1, z] * tx
        c01 = V[x, y, z + 1] * (1 - tx) + V[x + 1, y, z + 1] * tx
        c11 = V[x, y + 1, z + 1] * (1 - tx) + V[x + 1, y + 1, z + 1] * tx
        d = (c00 * (1 - ty) + c10 * ty) * (1 - tz) + (c01 * (1 - ty) + c11 * ty) * tz
        return np.where(outside, np.inf, d)

    def normal(self, P: np.ndarray) -> np.ndarray:
        h = self.step
        n = np.empty_like(P)
        for axis in range(3):
            e = np.zeros(3)
            e[axis] = h
            hi, lo = self.distance(P + e), self.distance(P - e)
            n[:, axis] = np.nan_to_num(hi - lo, posinf=0.0, neginf=0.0)
        length = np.linalg.norm(n, axis=1, keepdims=True)
        return n / np.maximum(length, 1e-9)


def quat_matrices(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    R = np.empty((len(q), 3, 3))
    R[:, 0, 0] = 1 - 2 * (y * y + z * z)
    R[:, 0, 1] = 2 * (x * y - w * z)
    R[:, 0, 2] = 2 * (x * z + w * y)
    R[:, 1, 0] = 2 * (x * y + w * z)
    R[:, 1, 1] = 1 - 2 * (x * x + z * z)
    R[:, 1, 2] = 2 * (y * z - w * x)
    R[:, 2, 0] = 2 * (x * z - w * y)
    R[:, 2, 1] = 2 * (y * z + w * x)
    R[:, 2, 2] = 1 - 2 * (x * x + y * y)
    return R


def quat_rotate(q: np.ndarray, theta: np.ndarray) -> np.ndarray:
    """q turned by the small rotation vectors theta, normalised."""
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    a, b, c = theta[:, 0], theta[:, 1], theta[:, 2]
    out = np.stack([w - 0.5 * (a * x + b * y + c * z),
                    x + 0.5 * (a * w + b * z - c * y),
                    y + 0.5 * (b * w + c * x - a * z),
                    z + 0.5 * (c * w + a * y - b * x)], axis=1)
    return out / np.linalg.norm(out, axis=1, keepdims=True)


def rotation_quat(axis: np.ndarray, angle: np.ndarray) -> np.ndarray:
    half = 0.5 * angle
    return np.column_stack([np.cos(half), axis * np.sin(half)[:, None]])


def apply(R, v):
    return np.einsum("nij,nj->ni", R, v)


class World:
    """The blocks, the standing body and the floor. Held blocks do not move until their release time."""

    def __init__(self, shapes: list[Shape], positions, masses, inertias, field: Field | None = None,
                 floor: float | None = None, friction: float = 0.6, gravity=(0.0, 0.0, -GRAVITY)):
        n = len(shapes)
        self.n = n
        self.x = np.array(positions, dtype=np.float64).reshape(n, 3)
        self.q = np.tile([1.0, 0.0, 0.0, 0.0], (n, 1))
        self.v = np.zeros((n, 3))
        self.w = np.zeros((n, 3))
        self.mass = np.asarray(masses, dtype=np.float64)
        self.inertia_inv = np.linalg.inv(np.asarray(inertias, dtype=np.float64).reshape(n, 3, 3))
        self.field, self.floor, self.friction = field, floor, float(friction)
        self.gravity = np.asarray(gravity, dtype=np.float64)
        self.radius = np.array([s.radius for s in shapes])
        ks = max((len(s.samples) for s in shapes), default=1)
        kp = max((len(s.normals) for s in shapes), default=1)
        self.samples = np.zeros((n, ks, 3))
        self.sample_mask = np.zeros((n, ks), bool)
        self.normals = np.zeros((n, kp, 3))
        self.offsets = np.full((n, kp), 1e3)        # an unused plane reads as very far inside, so it is never chosen
        for i, s in enumerate(shapes):
            self.samples[i, :len(s.samples)] = s.samples
            self.sample_mask[i, :len(s.samples)] = True
            self.normals[i, :len(s.normals)] = s.normals
            self.offsets[i, :len(s.offsets)] = s.offsets
        self.released = np.zeros(n, bool)
        self.asleep = np.zeros(n, bool)
        self.still = np.zeros(n, dtype=np.int32)
        self.tolerance = np.zeros((n, ks))          # how far a sample started inside the grid's rounded body
        self.R = quat_matrices(self.q)

    # ------------------------------------------------------------------------- contacts

    def start(self) -> None:
        """Samples that start on the broken face read slightly inside it: that is the grid's rounding, not contact."""
        if self.field is None:
            return
        P = self.x[:, None] + np.einsum("nij,nkj->nki", self.R, self.samples)
        d = self.field.distance(P.reshape(-1, 3)).reshape(P.shape[:2])
        self.tolerance = np.maximum(0.0, -np.nan_to_num(d, posinf=0.0)) * self.sample_mask

    def moving(self) -> np.ndarray:
        return self.released & ~self.asleep

    def block_contacts(self, active: np.ndarray):
        from scipy.spatial import cKDTree
        margin = 0.0
        pairs = cKDTree(self.x).query_pairs(2.0 * self.radius.max() + margin, output_type="ndarray")
        if not len(pairs):
            return None
        a, b = pairs[:, 0], pairs[:, 1]
        near = np.linalg.norm(self.x[a] - self.x[b], axis=1) < self.radius[a] + self.radius[b] + margin
        near &= active[a] | active[b]
        a, b = a[near], b[near]
        if not len(a):
            return None
        order = np.lexsort((b, a))
        a, b = a[order], b[order]
        # moving blocks wake what they strike hard enough
        fast = np.linalg.norm(self.v, axis=1) > WAKE_SPEED
        wake = np.r_[b[fast[a] & self.asleep[b]], a[fast[b] & self.asleep[a]]]
        if len(wake):
            self.asleep[wake] = False
            self.still[wake] = 0
        found = [self._inside(a, b), self._inside(b, a)]
        found = [f for f in found if f is not None]
        if not found:
            return None
        return tuple(np.concatenate([f[i] for f in found]) for i in range(5))

    def _inside(self, a: np.ndarray, b: np.ndarray):
        """Sample points of each a inside the hull of its b: contact points and the plane of least penetration."""
        P = self.x[a][:, None] + np.einsum("nij,nkj->nki", self.R[a], self.samples[a])
        L = np.einsum("nji,nkj->nki", self.R[b], P - self.x[b][:, None])      # into b's frame
        # by the bounding sphere first: most samples of most pairs are nowhere near
        close = (L * L).sum(axis=2) < (self.radius[b, None] ** 2)
        close &= self.sample_mask[a]
        pair, sample = np.nonzero(close)
        if not len(pair):
            return None
        Lp = L[pair, sample]
        dist = np.einsum("nj,nmj->nm", Lp, self.normals[b[pair]]) - self.offsets[b[pair]]
        best = dist.argmax(axis=1)
        sep = dist[np.arange(len(pair)), best]
        hit = sep < 0.0
        if not hit.any():
            return None
        pair, sample, best, sep, Lp = pair[hit], sample[hit], best[hit], sep[hit], Lp[hit]
        nl = self.normals[b[pair], best]
        return a[pair], b[pair], self.samples[a[pair], sample], Lp - sep[:, None] * nl, nl

    def static_contacts(self, active: np.ndarray):
        """Samples of moving blocks against the standing body and the floor: (body, sample, surface point, normal)."""
        idx = np.flatnonzero(active)
        if not len(idx):
            return None
        block, sample = np.nonzero(self.sample_mask[idx])
        block = idx[block]
        S = self.samples[block, sample]
        P = self.x[block] + apply(self.R[block], S)
        bodies, local, points, normals = [], [], [], []
        if self.field is not None:
            d = self.field.distance(P) + self.tolerance[block, sample]
            hit = d < 0.0
            if hit.any():
                n = self.field.normal(P[hit])
                bodies.append(block[hit])
                local.append(S[hit])
                points.append(P[hit] - d[hit, None] * n)
                normals.append(n)
        if self.floor is not None:
            hit = P[:, 2] < self.floor
            if hit.any():
                q = P[hit].copy()
                q[:, 2] = self.floor
                bodies.append(block[hit])
                local.append(S[hit])
                points.append(q)
                normals.append(np.tile([0.0, 0.0, 1.0], (int(hit.sum()), 1)))
        if not bodies:
            return None
        return tuple(np.concatenate(c) for c in (bodies, local, points, normals))

    # ------------------------------------------------------------------------- solving

    def _world_inverse(self, inverse_mass_on: np.ndarray) -> np.ndarray:
        I = np.einsum("nij,njk,nlk->nil", self.R, self.inertia_inv, self.R)
        I[~inverse_mass_on] = 0.0
        return I

    def _solve(self, pairs, statics, h, x_prev, R_prev) -> None:
        on = self.moving()
        inv_m = np.where(on, 1.0 / self.mass, 0.0)
        I = self._world_inverse(on)
        n = self.n
        dx = np.zeros((n, 3))
        dt = np.zeros((n, 3))
        count = np.zeros(n)

        def push(body, delta, angular, weight):
            np.add.at(dx, body, delta)
            np.add.at(dt, body, angular)
            np.add.at(count, body, weight)

        if statics is not None:
            a, sa, q, nrm = statics
            ra = apply(self.R[a], sa)
            pa = self.x[a] + ra
            c = ((q - pa) * nrm).sum(axis=1)
            live = c > 0.0
            if live.any():
                a, sa, q, nrm, ra, pa, c = a[live], sa[live], q[live], nrm[live], ra[live], pa[live], c[live]
                arm = np.cross(ra, nrm)
                turn = apply(I[a], arm)
                w = inv_m[a] + (arm * turn).sum(axis=1)
                lam = c / np.maximum(w, 1e-12)
                push(a, lam[:, None] * nrm * inv_m[a, None], lam[:, None] * turn, 1.0)
                # static friction: cancel the sliding of this substep, up to the normal correction's limit
                slip = pa - (x_prev[a] + apply(R_prev[a], sa))
                slip -= (slip * nrm).sum(axis=1, keepdims=True) * nrm
                length = np.linalg.norm(slip, axis=1)
                moving = length > 1e-9
                if moving.any():
                    t = -slip[moving] / length[moving, None]
                    ar = a[moving]
                    arm = np.cross(ra[moving], t)
                    turn = apply(I[ar], arm)
                    w = inv_m[ar] + (arm * turn).sum(axis=1)
                    limit = self.friction * lam[moving]
                    lt = np.minimum(length[moving] / np.maximum(w, 1e-12), limit)
                    push(ar, lt[:, None] * t * inv_m[ar, None], lt[:, None] * turn, 0.0)
        if pairs is not None:
            a, b, sa, sb, nb = pairs
            ra = apply(self.R[a], sa)
            rb = apply(self.R[b], sb)
            pa, pb = self.x[a] + ra, self.x[b] + rb
            nrm = apply(self.R[b], nb)
            c = ((pb - pa) * nrm).sum(axis=1)
            live = c > 0.0
            if live.any():
                a, b, sa, sb, ra, rb, pa, pb, nrm, c = (u[live] for u in (a, b, sa, sb, ra, rb, pa, pb, nrm, c))
                arm_a, arm_b = np.cross(ra, nrm), np.cross(rb, nrm)
                turn_a, turn_b = apply(I[a], arm_a), apply(I[b], arm_b)
                w = inv_m[a] + inv_m[b] + (arm_a * turn_a).sum(axis=1) + (arm_b * turn_b).sum(axis=1)
                lam = c / np.maximum(w, 1e-12)
                push(a, lam[:, None] * nrm * inv_m[a, None], lam[:, None] * turn_a, 1.0)
                push(b, -lam[:, None] * nrm * inv_m[b, None], -lam[:, None] * turn_b, 1.0)
                slip = (pa - (x_prev[a] + apply(R_prev[a], sa))) - (pb - (x_prev[b] + apply(R_prev[b], sb)))
                slip -= (slip * nrm).sum(axis=1, keepdims=True) * nrm
                length = np.linalg.norm(slip, axis=1)
                moving = length > 1e-9
                if moving.any():
                    t = -slip[moving] / length[moving, None]
                    am, bm = a[moving], b[moving]
                    arm_a, arm_b = np.cross(ra[moving], t), np.cross(rb[moving], t)
                    turn_a, turn_b = apply(I[am], arm_a), apply(I[bm], arm_b)
                    w = inv_m[am] + inv_m[bm] + (arm_a * turn_a).sum(axis=1) + (arm_b * turn_b).sum(axis=1)
                    lt = np.minimum(length[moving] / np.maximum(w, 1e-12), self.friction * lam[moving])
                    push(am, lt[:, None] * t * inv_m[am, None], lt[:, None] * turn_a, 0.0)
                    push(bm, -lt[:, None] * t * inv_m[bm, None], -lt[:, None] * turn_b, 0.0)
        touched = count > 0
        if touched.any():
            scale = SOR / count[touched]
            self.x[touched] += dx[touched] * scale[:, None]
            self.q[touched] = quat_rotate(self.q[touched], dt[touched] * scale[:, None])
            self.R[touched] = quat_matrices(self.q[touched])

    def step(self, h: float) -> None:
        on = self.moving()
        if not on.any():
            return
        self.v[on] += self.gravity * h
        speed = np.linalg.norm(self.v[on], axis=1, keepdims=True)
        self.v[on] *= np.minimum(1.0, MAX_SPEED / np.maximum(speed, 1e-9))
        x_prev, q_prev, R_prev = self.x.copy(), self.q.copy(), self.R.copy()
        self.x[on] += self.v[on] * h
        self.q[on] = quat_rotate(self.q[on], self.w[on] * h)
        self.R[on] = quat_matrices(self.q[on])
        pairs = self.block_contacts(on)
        on = self.moving()              # a strike may have woken some
        statics = self.static_contacts(on)
        if self.field is not None:
            self.tolerance[on] *= 0.97
        for _ in range(ITERATIONS):
            self._solve(pairs, statics, h, x_prev, R_prev)
        self.v[on] = (self.x[on] - x_prev[on]) / h
        # the rotation between the two poses, as an angular velocity
        qp, q = q_prev[on], self.q[on]
        rel_w = q[:, 0] * qp[:, 0] + (q[:, 1:] * qp[:, 1:]).sum(axis=1)
        rel_v = (qp[:, 0, None] * q[:, 1:] - q[:, 0, None] * qp[:, 1:] - np.cross(q[:, 1:], qp[:, 1:]))
        self.w[on] = 2.0 * rel_v * np.where(rel_w < 0.0, -1.0, 1.0)[:, None] / h
        self.w[on] *= 1.0 - 0.6 * h         # air drag on tumbling; contacts do the rest
        self.v[on] *= 1.0 - 0.02 * h
        # rough blocks do not roll: what touches something loses its spin and speed fast, so rubble heaps up
        touching = np.zeros(self.n, bool)
        if pairs is not None:
            touching[pairs[0]] = True
            touching[pairs[1]] = True
        if statics is not None:
            touching[statics[0]] = True
        touching &= on
        grip = min(self.friction * h, 0.2)
        self.w[touching] *= 1.0 - 22.0 * grip
        self.v[touching] *= 1.0 - 3.0 * grip
        # sleep: a block that barely moves for a while has come to rest
        rest = np.linalg.norm(self.v[on], axis=1) + np.linalg.norm(self.w[on], axis=1) * self.radius[on] < SLEEP_SPEED
        self.still[on] = np.where(rest, self.still[on] + 1, 0)
        tired = np.flatnonzero(on)[self.still[on] >= SLEEP_STEPS]
        if len(tired):
            self.asleep[tired] = True
            self.v[tired] = 0.0
            self.w[tired] = 0.0

    def release(self, indices, velocity, spin) -> None:
        self.released[indices] = True
        self.v[indices] = velocity
        self.w[indices] = spin


def run(world: World, release_times: np.ndarray, velocities: np.ndarray, spins: np.ndarray, duration: float,
        run_to_rest: bool, progress=None, limit: float = 12.0) -> dict:
    """Move the world to `duration` seconds; with run_to_rest, on until everything is still (or `limit` seconds)."""
    h = 1.0 / SUBSTEPS_PER_SECOND
    if duration <= 0.0 and not run_to_rest:     # the moment before anything lets go
        return {"seconds": 0.0, "steps": 0, "moving": 0}
    world.start()
    pending = np.argsort(release_times, kind="stable")
    pending = pending[np.isfinite(release_times[pending])]
    cursor = 0
    steps = 0
    end = limit if run_to_rest else duration
    total = max(int(end / h), 1)
    last = float(release_times[pending[-1]]) if len(pending) else 0.0
    for steps in range(total):
        t = steps * h
        while cursor < len(pending) and release_times[pending[cursor]] <= t:
            start = cursor
            while cursor < len(pending) and release_times[pending[cursor]] <= t:
                cursor += 1
            chosen = pending[start:cursor]
            world.release(chosen, velocities[chosen], spins[chosen])
        world.step(h)
        if progress and steps % 16 == 0:
            base = min(steps / total, 1.0)
            progress(base, f"{t:.1f} s: {int(world.moving().sum())} blocks moving")
        if run_to_rest and t >= max(duration, last) and not world.moving().any():
            break
    return {"seconds": (steps + 1) * h, "steps": steps + 1, "moving": int(world.moving().sum())}


def poses(world: World) -> tuple[np.ndarray, np.ndarray]:
    """Rotation matrices and positions of every block."""
    return quat_matrices(world.q), world.x.copy()


def tilt(world: World, indices, axes: np.ndarray, angles: np.ndarray, shift: np.ndarray) -> None:
    """Move held blocks a little before the simulation starts: a block that has worked loose."""
    if not len(indices):
        return
    world.q[indices] = rotation_quat(axes, angles)
    world.x[indices] += shift
    world.R = quat_matrices(world.q)
