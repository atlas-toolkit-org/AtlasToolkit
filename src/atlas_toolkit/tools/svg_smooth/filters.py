"""Arc-length signal filters. No point selection, decimation or curve fitting.

The span is the wavelength with 50% amplitude response. Both coordinates share
one operator. Open spans have exact fixed ends; unanchored rings are periodic.
"""
import math

import numpy as np
from scipy.fft import dst, idst, rfft, irfft
from scipy.linalg import solveh_banded


def _gain(k, cutoff, method):
    if method == "whittaker":
        return 1 / (1 + (k / cutoff) ** 2)
    # Paired Laplacian contraction/expansion, lambda=.5, mu=-.5.
    # Evaluate the polynomial in the spectral domain instead of thousands of passes.
    cycles = max(1, math.ceil(math.log(.5) / math.log1p(-.25 * cutoff ** 2)))
    return np.maximum(0., 1 - .25 * k ** 2) ** cycles


def filter_points(p, spacing, span, method, periodic=False):
    """Uniformly sampled 2D curve -> filtered samples, without changing count."""
    n = len(p)
    if n < 3:
        return p.copy()
    cutoff = 2 * math.sin(min(math.pi / 2, math.pi * spacing / span)) ** 2
    if periodic:
        k = 1 - np.cos(2 * np.pi * np.arange(n // 2 + 1) / n)
        return irfft(rfft(p - p[0], axis=0) * _gain(k, cutoff, method)[:, None], n=n, axis=0) + p[0]
    penalty = 1 / (4 * cutoff ** 2)
    if method == "taubin" or penalty > 1e6:
        # DST-I diagonalizes the fixed-end second-difference matrix. It also
        # solves the identical Whittaker objective stably for very large penalties.
        baseline = np.linspace(p[0], p[-1], n)
        k = 1 - np.cos(np.pi * np.arange(1, n - 1) / (n - 1))
        q = baseline.copy()
        q[1:-1] += idst(dst(p[1:-1] - baseline[1:-1], type=1, axis=0, norm="ortho") *
                         _gain(k, cutoff, method)[:, None], type=1, axis=0, norm="ortho")
        return q
    # Exact endpoint elimination in (I + lambda D2.T D2) q = p.
    # Three-band Cholesky, O(n) time/storage, two right-hand sides.
    diag = np.zeros(n)
    diag[:-2] += 1
    diag[1:-1] += 4
    diag[2:] += 1
    off = np.full(n - 1, -4.)
    off[0] = off[-1] = -2.
    band = np.zeros((3, n - 2))
    band[0] = 1 + penalty * diag[1:-1]
    band[1, :n - 3] = penalty * off[1:-1]
    if n > 4:
        band[2, :n - 4] = penalty
    baseline = np.linspace(p[0], p[-1], n)
    rhs = p[1:-1] - baseline[1:-1]
    q = baseline.copy()
    q[1:-1] += solveh_banded(band, rhs, lower=True, check_finite=False)
    return q


def interpolate(t, p, at):
    return np.column_stack([np.interp(at, t, p[:, axis]) for axis in (0, 1)])


def arclength(p):
    return np.r_[0., np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]


def antialias(t, p, grid, periodic=False):
    """Exact box integration of a piecewise-linear signal before uniform sampling.

    Point sampling alone can alias very fine sawteeth into broad false waves.
    Integrals are computed once; evaluation is vectorized O(n + m log n).
    """
    origin = p[0]
    p = p - origin
    dt = np.diff(t)
    slopes = np.diff(p, axis=0) / dt[:, None]
    integrals = np.vstack([np.zeros(2), np.cumsum((p[:-1] + p[1:]) * (dt / 2)[:, None], axis=0)])

    def primitive(at):
        wraps = np.floor((at - t[0]) / (t[-1] - t[0])) if periodic else np.zeros(len(at))
        x = (at - t[0]) % (t[-1] - t[0]) + t[0] if periodic else np.clip(at, t[0], t[-1])
        i = np.clip(np.searchsorted(t, x, side="right") - 1, 0, len(t) - 2)
        d = x - t[i]
        return integrals[i] + p[i] * d[:, None] + .5 * slopes[i] * d[:, None] ** 2 + wraps[:, None] * integrals[-1]

    half = (grid[1] - grid[0]) / 2
    left, right = grid - half, grid + half
    if not periodic:
        left, right = np.maximum(left, t[0]), np.minimum(right, t[-1])
    out = (primitive(right) - primitive(left)) / (right - left)[:, None] + origin
    if not periodic:
        out[0], out[-1] = p[0] + origin, p[-1] + origin
    return out


def corner_locks(p, closed, support, angle):
    """Persistent turns over a physical support, rather than raw sawtooth angles."""
    t = arclength(p)
    locks = np.zeros(len(p), dtype=bool)
    if not closed:
        locks[0] = locks[-1] = True
    if angle >= 180 or len(p) < 3 or not t[-1]:
        return locks
    if angle <= 0:
        locks[:] = True
        return locks
    if closed:
        distance = min(support, t[-1] / 4)
        left = interpolate(t, p, (t - distance) % t[-1])
        right = interpolate(t, p, (t + distance) % t[-1])
    else:
        distance = np.minimum(support, np.minimum(t, t[-1] - t))
        left = interpolate(t, p, t - distance)
        right = interpolate(t, p, t + distance)
    a, b = p - left, right - p
    den = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)
    cos = np.divide(np.sum(a * b, axis=1), den, out=np.ones(len(p)), where=den > 0)
    turns = np.degrees(np.arccos(np.clip(cos, -1, 1)))
    # Only peaks are accents: avoid locking a whole neighbourhood around a corner.
    peaks = (turns >= np.roll(turns, 1)) & (turns >= np.roll(turns, -1))
    locks |= (turns >= angle) & peaks
    if closed:
        locks[0] = locks[-1] = locks[0] or locks[-1]
    return locks


def proposal(p, closed, locks, o, check):
    """Return matched source/target polylines on a common arc-length partition.

    Every source vertex is included in this partition: between matching vertices,
    the same-parameter displacement bound therefore holds continuously, not just
    at a few probes. Filtering uses a uniform grid independent of input density.
    """
    t = arclength(p)
    step = min(o.sample_step, o.span / 8)
    periodic = closed and not np.any(locks)
    if periodic:
        count = max(8, math.ceil(t[-1] / step))
        if count + len(p) > o.point_limit:
            raise ValueError("Smoothing exceeds the sample budget; increase Sample spacing and/or Smoothing span.")
        grid = np.linspace(0., t[-1], count + 1)
        src = antialias(t, p, grid, True)
        target = filter_points(src[:-1], t[-1] / count, o.span, o.method, True)
        target = np.vstack([target, target[0]])
        at = np.union1d(t, grid)
        return interpolate(t, p, at), interpolate(grid, target, at), at
    if closed:
        # Rotate to a real protected corner/contact. Never create a seam anchor.
        start = int(np.flatnonzero(locks[:-1])[0])
        p = np.vstack([p[start:-1], p[:start + 1]])
        locks = np.r_[locks[start:-1], locks[:start + 1]]
        t = arclength(p)
    ids = np.flatnonzero(locks)
    sources, targets, times = [], [], []
    used = 0
    for lo, hi in zip(ids[:-1], ids[1:]):
        check()
        if hi - lo == 1:
            # A segment pinned at both ends is already straight. Preserve it
            # exactly, including full crossing/junction spans, without resampling.
            sources.append(p[lo:hi])
            targets.append(p[lo:hi])
            times.append(t[lo:hi])
            used += 1
            continue
        length = t[hi] - t[lo]
        count = max(2, math.ceil(length / step))
        used += count + hi - lo + 1
        if used > o.point_limit:
            raise ValueError("Smoothing exceeds the sample budget; increase Sample spacing and/or Smoothing span.")
        grid = np.linspace(t[lo], t[hi], count + 1)
        src = antialias(t[lo:hi + 1], p[lo:hi + 1], grid)
        # Translate to local origin for stable band solves far from document zero.
        target = filter_points(src - src[0], length / count, o.span, o.method) + src[0]
        target[0], target[-1] = p[lo], p[hi]
        at = np.union1d(t[lo:hi + 1], grid)
        sources.append(interpolate(t, p, at)[:-1])
        targets.append(interpolate(grid, target, at)[:-1])
        times.append(at[:-1])
    return (np.vstack([*sources, p[-1:]]), np.vstack([*targets, p[-1:]]),
            np.concatenate([*times, t[-1:]]))
