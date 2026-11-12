"""
mesh_flow.py - pull a model through a turbulent current: every vertex is carried along a smooth, deterministic
velocity field by fourth-order Runge-Kutta integration, so the surface stays connected while it is swept into
eddies, a vortex or a streaming wind.

The field, in the canonical frame of the distortion tools (geom/frame3d: up = Z, front = -Y, centred on the bounding
box, 2 units tall), is the sum of three parts:

    current  curl noise (Bridson et al. 2007): the curl of a vector potential made of random plane waves in octaves,
             v = sum_m cos(k_m . x + phi_m) (k_m^ x a_m). Divergence-free by construction, so the current folds and
             shears the surface without bunching it up; scaled so its RMS speed is the travel per unit time.
    vortex   a Lamb-Oseen swirl about an axis through a centre: rigid in the core, falling off as 1/r outside, fading
             along the axis, with an optional inflow toward the axis that winds the swirl into spiral arms.
    pull     a uniform drift in one direction, modulated by "streaks": a scalar noise that only varies across the
             direction of travel, so neighbouring strands are combed out by different lengths.

Each vertex i moves by dx/dt = w_i v(x) for t in 0 .. amount, with w_i the influence weight at its rest position
(the "Acts on" region times the base anchor), so anchored vertices never move and the stages of a sequence are
samples of the same trajectories. The step size depends on the field alone (a fraction of the flow scale: its top
speed over its top velocity gradient), never on the number of stages or the amount, so a stage shared by two
sequences has the same shape. Long edges are split first (conforming, no T-junctions) so the field's eddies have
vertices to bend.

Nothing here claims collision-free or volume-preserving output: strong flows can fold the surface through itself,
which a pen plotter draws happily. stretch() reports how far triangles were pulled out.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

OCTAVES = 4                 # current octaves (each half the wavelength of the last)
MODES = 16                  # plane waves per octave
MIN_OCTAVE_GAIN = 0.05      # octaves weaker than this are skipped
TURBULENCE_GAIN = 0.7       # octave gain at turbulence 1
PEAK_RATIO = 2.5            # top speed of the current / its RMS speed (for the step size)
VISIBLE_GAIN = 0.15         # octaves at least this strong set the detail edges are split to
STREAK_MODES = 12
LAMB_PEAK = 0.638           # max of (1 - exp(-s^2)) / s, at s = 1.121
CHUNK = 120_000             # vertices per field evaluation block

AXES = {"front": (0.0, -1.0, 0.0),      # toward the viewer: + spin = counter-clockwise seen from the front
        "up": (0.0, 0.0, 1.0),          # + spin = counter-clockwise seen from above (as Twist)
        "side": (1.0, 0.0, 0.0)}        # seen from the model's left (+X side)
DIRECTIONS = {"+x": (1.0, 0.0, 0.0), "-x": (-1.0, 0.0, 0.0), "+y": (0.0, 1.0, 0.0), "-y": (0.0, -1.0, 0.0),
              "+z": (0.0, 0.0, 1.0), "-z": (0.0, 0.0, -1.0)}


@dataclass
class Field:
    """A velocity field in canonical units per unit time."""
    K: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))      # current wave vectors (M, 3)
    B: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))      # current amplitudes (M, 3)
    phi: np.ndarray = field(default_factory=lambda: np.zeros(0))
    vortex: dict | None = None
    pull: np.ndarray | None = None                                      # drift velocity (3,)
    Q: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))      # streak wave vectors
    psi: np.ndarray = field(default_factory=lambda: np.zeros(0))
    streak_amp: float = 0.0
    wavelength: float = math.inf                                        # flow scale: 2 pi speed / rate
    speed: float = 0.0                                                  # top speed (estimate)
    rate: float = 0.0                                                   # top velocity gradient (estimate)
    detail: float = math.inf                                            # smallest visible feature it makes

    @property
    def active(self) -> bool:
        return self.speed > 0

    def velocity(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        out = np.empty_like(X)
        for a in range(0, len(X), CHUNK):
            out[a:a + CHUNK] = self._velocity(X[a:a + CHUNK])
        return out

    def _velocity(self, X: np.ndarray) -> np.ndarray:
        v = np.zeros_like(X)
        if len(self.K):
            v += np.cos(X @ self.K.T + self.phi) @ self.B
        vx = self.vortex
        if vx is not None:
            r = X - vx["c"]
            axial = r @ vx["a"]
            rp = r - axial[:, None] * vx["a"]
            rho = np.linalg.norm(rp, axis=1)
            s = rho / vx["R"]
            with np.errstate(invalid="ignore", divide="ignore"):
                prof = np.where(s > 1e-9, (1.0 - np.exp(-s * s)) / s, s) / LAMB_PEAK     # 0 at the axis, peak 1
                radial = np.where(rho[:, None] > 1e-12, rp / rho[:, None], 0.0)
            g = np.exp(-(axial / vx["depth"]) ** 2) * vx["V0"] * prof
            v += np.cross(vx["a"], radial) * g[:, None]
            if vx["inflow"]:
                v -= radial * (g * vx["inflow"])[:, None]
        if self.pull is not None:
            m = 1.0
            if len(self.Q) and self.streak_amp:
                m = 1.0 + self.streak_amp * (np.sin(X @ self.Q.T + self.psi).sum(1))
                m = m[:, None]
            v += self.pull * m
        return v


def _unit(rng, n: int) -> np.ndarray:
    u = rng.normal(size=(n, 3))
    return u / np.linalg.norm(u, axis=1, keepdims=True)


def build_field(*, current: float = 0.3, swirl: float = 0.7, turbulence: float = 0.4, seed: int = 0,
                spin: float = 0.0, radius: float = 0.5, centre=(0.0, 0.0, 0.0), axis: str = "front",
                depth: float = 1.5, inflow: float = 0.0, pull: float = 0.0, direction: str = "+x",
                streaks: float = 0.0) -> Field:
    """A field from canonical settings: current = RMS speed of the curl noise, swirl = wavelength of its largest
    eddies, spin = degrees a point on the vortex core's edge turns per unit time, radius = core radius, depth =
    falloff along the axis in core radii, inflow = share of the swirl speed drawn toward the axis, pull = drift
    speed, streaks = 0..1 modulation of the drift across its direction. All lengths and speeds canonical."""
    rng = np.random.default_rng(int(seed))
    f = Field()
    speed, rate, detail = 0.0, 0.0, math.inf             # top speed and top velocity gradient, summed over the parts
    # draw every random number in a fixed order whatever is active, so one part's settings never reseed another
    curl_dirs = [(_unit(rng, MODES), rng.uniform(0.85, 1.15, MODES), _unit(rng, MODES),
                  rng.uniform(0, 2 * math.pi, MODES)) for _ in range(OCTAVES)]
    streak_dirs = _unit(rng, STREAK_MODES)
    streak_scale = rng.uniform(0.8, 1.25, STREAK_MODES)
    streak_phase = rng.uniform(0, 2 * math.pi, STREAK_MODES)

    if current > 0 and swirl > 0:
        Ks, Bs, Ps, octaves = [], [], [], []
        gain = TURBULENCE_GAIN * float(np.clip(turbulence, 0.0, 1.0))
        for o, (kd, kj, ad, ph) in enumerate(curl_dirs):
            amp = gain ** o if o else 1.0
            if amp < MIN_OCTAVE_GAIN:
                break
            lam = swirl / 2 ** o
            Ks.append(kd * (2 * math.pi / lam * kj)[:, None])
            Bs.append(np.cross(kd, ad) * amp)
            Ps.append(ph)
            octaves.append((amp, lam / 1.15))
        B = np.concatenate(Bs)
        rms = math.sqrt(float((B * B).sum()) / 2.0)
        f.K, f.B, f.phi = np.concatenate(Ks), B * (current / rms), np.concatenate(Ps)
        speed += PEAK_RATIO * current
        norm = math.sqrt(sum(a * a for a, _ in octaves))
        rate += sum(PEAK_RATIO * current * a / norm * 2 * math.pi / lam for a, lam in octaves)
        detail = min(lam for a, lam in octaves if a >= VISIBLE_GAIN or lam == octaves[0][1])

    if spin and radius > 0:
        a = np.asarray(AXES[axis], dtype=np.float64)
        V0 = math.radians(spin) * radius
        f.vortex = dict(c=np.asarray(centre, dtype=np.float64), a=a, R=float(radius), V0=V0,
                        depth=max(float(depth), 0.05) * radius, inflow=float(inflow))
        speed += abs(V0) * (1.0 + abs(inflow))
        rate += abs(V0) * (1.0 + abs(inflow)) / (LAMB_PEAK * radius)       # the core's angular speed
        detail = min(detail, radius * min(1.0, 360.0 / abs(spin)))          # spacing of the spiral arms at the core

    if pull > 0:
        d = np.asarray(DIRECTIONS[direction], dtype=np.float64)
        f.pull = d * pull
        amp = float(np.clip(streaks, 0.0, 1.0))
        if amp > 0 and swirl > 0:
            q = streak_dirs - np.outer(streak_dirs @ d, d)          # across the drift only
            q /= np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-9)
            lam = swirl / 2.0
            f.Q = q * (2 * math.pi / lam * streak_scale)[:, None]
            f.psi = streak_phase
            f.streak_amp = amp * 0.5 / math.sqrt(STREAK_MODES / 2.0)   # RMS of the modulation = amp / 2
            rate += pull * amp * 2 * math.pi / lam
            detail = min(detail, lam)
        speed += pull * (1.0 + amp)

    f.speed, f.rate, f.detail = speed, rate, detail
    f.wavelength = 2 * math.pi * speed / rate if rate > 0 else math.inf
    return f


# ----------------------------------------------------------------------------- integration

def step_size(f: Field, step: float = 0.25) -> float:
    """Time step: per step the flow shears the surface by at most `step` x 2 pi (no point moves more than `step` x
    the flow scale, where the fastest-turning part of the field sets the scale). Depends on the field alone."""
    if not f.active or not math.isfinite(f.wavelength):
        return 1.0
    n = max(4, math.ceil(f.speed / (max(step, 1e-3) * f.wavelength)))
    return 1.0 / n


def _rk4(f: Field, X: np.ndarray, w: np.ndarray, h: float) -> np.ndarray:
    k1 = f.velocity(X) * w
    k2 = f.velocity(X + 0.5 * h * k1) * w
    k3 = f.velocity(X + 0.5 * h * k2) * w
    k4 = f.velocity(X + h * k3) * w
    return X + (h / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)


def advect(f: Field, X0: np.ndarray, w: np.ndarray, times, step: float = 0.25, progress=None) -> list[np.ndarray]:
    """Positions of points X0 (n, 3) at each time in `times` (0 = rest), each point at speed w_i (n,) through the
    field. Whole steps of one fixed size; a stage between grid times ends with a partial step from the last grid
    state, which is not carried on, so every stage depends on its own time only."""
    X0 = np.asarray(X0, dtype=np.float64)
    w = np.asarray(w, dtype=np.float64)
    times = [max(float(t), 0.0) for t in times]
    out: list = [None] * len(times)
    move = w > 0
    if not f.active or not move.any() or not times:
        return [X0.copy() for _ in times]
    h = step_size(f, step)
    X, wm = X0[move].copy(), w[move][:, None]
    order = sorted(range(len(times)), key=lambda i: times[i])
    t_end = times[order[-1]]
    total = max(1, math.floor(t_end / h + 1e-9))
    j = 0
    for i in order:
        t = times[i]
        n = math.floor(t / h + 1e-9)
        while j < n:
            X = _rk4(f, X, wm, h)
            j += 1
            if progress:
                progress(j / total)
        rest = t - j * h
        Y = _rk4(f, X, wm, rest) if rest > 1e-12 else X
        full = X0.copy()
        full[move] = Y
        out[i] = full
    return out


# ----------------------------------------------------------------------------- weights

def anchor_weights(P: np.ndarray, lo, hi, anchor: float, blend: float) -> np.ndarray:
    """1 above the anchor, 0 within `anchor` (% h) of the base, a smoothstep over `blend` (% h) in between."""
    from atlas_toolkit.geom.influence import smoothstep
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    height = float(hi[2] - lo[2]) or 1.0
    hf = (np.asarray(P, float)[:, 2] - lo[2]) / height * 100.0
    if anchor <= 0 and blend <= 0:
        return np.ones(len(hf))
    if blend <= 0:
        return (hf > anchor).astype(np.float64)
    return smoothstep((hf - anchor) / blend)


# ----------------------------------------------------------------------------- refinement

def refine(V: np.ndarray, F: np.ndarray, max_len: float, budget: int, passes: int = 6
           ) -> tuple[np.ndarray, np.ndarray, int]:
    """Split every edge longer than max_len at its midpoint, conformingly (a split edge is split in both its faces,
    so no T-junctions open cracks when the surface bends), until none is left, `passes` runs out or the face
    budget would be passed. Returns (V, F, passes done). Surface Peel (tools/mesh_peel) imports this: keep the
    signature."""
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    done = 0
    for _ in range(passes):
        if len(F) == 0:
            break
        E = F[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
        Es = np.sort(E, axis=1)
        uniq, inv = np.unique(Es, axis=0, return_inverse=True)
        inv = inv.reshape(-1, 3)
        length = np.linalg.norm(V[uniq[:, 0]] - V[uniq[:, 1]], axis=1)
        split = length > max_len
        if not split.any():
            break
        flags = split[inv]                                   # (F, 3): edges v0v1, v1v2, v2v0
        cnt = flags.sum(1)
        grow = int((cnt == 1).sum() + 2 * (cnt == 2).sum() + 3 * (cnt == 3).sum())
        if len(F) + grow > budget:
            break
        mid_id = np.full(len(uniq), -1, dtype=np.int64)
        mid_id[split] = len(V) + np.arange(int(split.sum()))
        V = np.vstack([V, (V[uniq[split, 0]] + V[uniq[split, 1]]) / 2.0])
        M = mid_id[inv]                                      # midpoint of each face edge (-1: not split)
        out = [F[cnt == 0]]
        # three splits: four triangles
        s = cnt == 3
        a, b, c = F[s].T
        m0, m1, m2 = M[s].T
        out += [np.column_stack(t) for t in ((a, m0, m2), (m0, b, m1), (m2, m1, c), (m0, m1, m2))]
        # one or two splits: rotate each face so the pattern starts at edge 0
        for k in (1, 2):
            s = np.flatnonzero(cnt == k)
            if not len(s):
                continue
            fl = flags[s]
            if k == 1:
                r = np.argmax(fl, axis=1)                    # the split edge becomes edge 0
            else:
                r = (np.argmin(fl, axis=1) + 1) % 3          # the unsplit edge becomes edge 2
            idx = (r[:, None] + np.arange(3)) % 3
            Fv = np.take_along_axis(F[s], idx, axis=1)
            Mv = np.take_along_axis(M[s], idx, axis=1)
            a, b, c = Fv.T
            if k == 1:
                m0 = Mv[:, 0]
                out += [np.column_stack((a, m0, c)), np.column_stack((m0, b, c))]
            else:
                m0, m1 = Mv[:, 0], Mv[:, 1]
                out += [np.column_stack((m0, b, m1)), np.column_stack((a, m0, m1)), np.column_stack((a, m1, c))]
        F = np.vstack(out)
        done += 1
    return V, F, done


# ----------------------------------------------------------------------------- diagnostics

def stretch(V0: np.ndarray, V1: np.ndarray, F: np.ndarray) -> dict:
    """How far the surface was pulled: edge length ratios (moved / rest) and degenerate triangles."""
    F = np.asarray(F, dtype=np.int64)
    if not len(F):
        return {"max": 1.0, "p99": 1.0, "over4": 0.0, "degenerate": 0}
    E = np.unique(np.sort(F[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1), axis=0)
    l0 = np.linalg.norm(V0[E[:, 0]] - V0[E[:, 1]], axis=1)
    l1 = np.linalg.norm(V1[E[:, 0]] - V1[E[:, 1]], axis=1)
    ok = l0 > 0
    ratio = l1[ok] / l0[ok] if ok.any() else np.ones(1)

    def areas(V):
        return 0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)
    a0, a1 = areas(V0), areas(V1)
    tiny = 1e-8 * float(a0.mean()) if a0.size else 0.0
    return {"max": float(ratio.max()), "p99": float(np.percentile(ratio, 99)),
            "over4": float((ratio > 4.0).mean() * 100.0),
            "degenerate": int(((a1 <= tiny) & (a0 > tiny)).sum())}


def max_displacement(V0: np.ndarray, V1: np.ndarray) -> float:
    return float(np.linalg.norm(V1 - V0, axis=1).max()) if len(V0) else 0.0


# ----------------------------------------------------------------------------- from the tool's settings

def field_from(p, lo, hi) -> Field:
    """The field of the tool's settings (% of height, degrees, % of size) in canonical units for bounds lo, hi."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    height = float(hi[2] - lo[2]) or 2.0
    pct = height / 100.0
    centre = (lo + hi) / 2.0 + np.asarray(p.vortex_centre, float) / 100.0 * (hi - lo)
    return build_field(current=p.travel * p.current * pct, swirl=p.swirl * pct, turbulence=p.turbulence,
                       seed=p.seed, spin=p.spin, radius=p.vortex_radius * pct, centre=centre, axis=p.vortex_axis,
                       depth=p.vortex_depth, inflow=p.inflow, pull=p.pull * pct, direction=p.pull_dir,
                       streaks=p.streaks)


def weights_from(p, P, lo, hi) -> np.ndarray:
    from atlas_toolkit.geom import influence
    return influence.weights(P, p, lo, hi) * anchor_weights(P, lo, hi, p.anchor, p.anchor_blend)
