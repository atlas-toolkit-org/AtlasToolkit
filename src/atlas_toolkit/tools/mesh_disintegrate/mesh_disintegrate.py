"""Disintegrate: midpoint subdivision and independent corner distortion (first ported from a Processing sketch).

Triangle arrays deliberately have no shared vertices: rejoining coincident corners changes the next step's
behavior. Computation and randomness are independent of preview sampling, layout, export format and batch size.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MAX_TOTAL_FACES = 2_000_000
BATCH_FACES = 4096
MAX_STEPS = 8


def face_counts(faces: int, steps: int, budget: int | None = None) -> list[int]:
    """Preflight before allocating any subdivided geometry. Stage 0 counts toward the budget."""
    budget = MAX_TOTAL_FACES if budget is None else budget
    if faces < 1:
        raise ValueError("the mesh has no usable triangles")
    if not 0 <= steps <= MAX_STEPS or int(steps) != steps:
        raise ValueError(f"subdivision steps must be an integer from 0 to {MAX_STEPS}")
    counts = [int(faces) * 4**i for i in range(int(steps) + 1)]
    if sum(counts) > budget:
        safe = [i for i in range(MAX_STEPS + 1) if faces * (4**(i + 1) - 1) // 3 <= budget]
        advice = (f"Largest supported step count: {safe[-1]}." if safe else
                  "Even stage 0 exceeds the budget; use a smaller input mesh.")
        raise ValueError(f"{steps} steps need {sum(counts):,} faces across all stages "
                         f"(budget {budget:,}). {advice} The input was not simplified.")
    return counts


def checked_triangles(triangles) -> np.ndarray:
    a = np.asarray(triangles, dtype=np.float64)
    if a.ndim != 3 or a.shape[1:] != (3, 3) or len(a) == 0:
        raise ValueError("the mesh has no usable triangles")
    # Atlas's binary mesh formats and MeshData use float32. Catch overflow before export or preview.
    if not np.isfinite(a).all() or np.max(np.abs(a)) > np.finfo(np.float32).max:
        raise ValueError("mesh coordinates must be finite and representable in 32-bit mesh formats")
    return a


def subdivide(triangles: np.ndarray) -> np.ndarray:
    """Each (A,B,C) becomes (A,AB,CA), (B,BC,AB), (C,CA,BC), (AB,BC,CA), as in the sketch."""
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    ab, bc, ca = (a + b) * 0.5, (b + c) * 0.5, (c + a) * 0.5
    out = np.empty((len(triangles), 4, 3, 3), dtype=np.float64)
    out[:, 0] = np.stack((a, ab, ca), axis=1)
    out[:, 1] = np.stack((b, bc, ab), axis=1)
    out[:, 2] = np.stack((c, ca, bc), axis=1)
    out[:, 3] = np.stack((ab, bc, ca), axis=1)
    return out.reshape(-1, 3, 3)


def random_directions(rng, shape) -> np.ndarray:
    """Uniform on the sphere (azimuth and cosine of polar angle), as PVector.random3D()."""
    draws = rng.random((*shape, 2))
    z = 2 * draws[..., 0] - 1
    phi = 2 * np.pi * draws[..., 1]
    r = np.sqrt(np.maximum(0, 1 - z * z))
    return np.stack((r * np.cos(phi), r * np.sin(phi), z), axis=-1)


def distort(triangles, amplitude, direction, centre, influence, dir_rng, mag_rng):
    """Return new positions, never mutating the preceding stage or welding adjacent corners."""
    fallback = random_directions(dir_rng, triangles.shape[:2])
    if direction == "random":
        vectors = fallback
    else:
        if direction == "normal":
            normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
            vectors = np.broadcast_to(normals[:, None, :], triangles.shape)
        else:
            vectors = triangles - centre
        lengths = np.linalg.norm(vectors, axis=-1, keepdims=True)
        vectors = np.divide(vectors, lengths, out=fallback, where=lengths > 0)
    magnitudes = mag_rng.uniform(-amplitude, amplitude, size=(*triangles.shape[:2], 1))
    return triangles + vectors * magnitudes * influence


@dataclass
class Fragmentation:
    stages: list[np.ndarray]
    amplitudes: list[float]
    diagonal: float


def generate(mesh, steps=2, strength=1.0, units="percent", growth=1.0, direction="random",
             influence=(1.0, 1.0, 1.0), seed=42, progress=None, batch_faces=BATCH_FACES) -> Fragmentation:
    counts = face_counts(len(mesh.faces), steps)
    if units not in ("percent", "units") or direction not in ("random", "normal", "radial"):
        raise ValueError("unknown distortion units or direction")
    influence = np.asarray(influence, dtype=np.float64)
    if (influence.shape != (3,) or not np.isfinite(influence).all() or
            np.any(influence < 0) or np.any(influence > 1)):
        raise ValueError("XYZ influence must contain three finite values from 0 to 1")
    if not np.isfinite([strength, growth]).all() or strength < 0 or not 0 <= growth <= 3:
        raise ValueError("strength must be finite and nonnegative; growth must be from 0 to 3")
    if batch_faces < 1:
        raise ValueError("batch size must be positive")
    if progress:
        progress(0.0, "preparing stages")
    original = checked_triangles(mesh.triangles).copy()
    lo, hi = original.min(axis=(0, 1)), original.max(axis=(0, 1))
    centre = (lo + hi) * 0.5
    diagonal = float(np.linalg.norm(hi - lo))
    if diagonal <= 0:
        raise ValueError("the mesh has no usable extent")
    base = float(strength) * (diagonal / 100 if units == "percent" else 1)
    stages, amplitudes = [original], [0.0]
    completed, work = 0, sum(counts[1:]) or 1
    for stage in range(1, int(steps) + 1):
        prev = stages[-1]
        amplitude = base * stage**growth
        out = np.empty((len(prev) * 4, 3, 3), dtype=np.float64)
        # Independent direction/magnitude streams make batching and additional stages prefix-stable.
        dir_seq, mag_seq = np.random.SeedSequence([int(seed), stage]).spawn(2)
        dir_rng, mag_rng = np.random.default_rng(dir_seq), np.random.default_rng(mag_seq)
        for start in range(0, len(prev), batch_faces):
            stop = min(len(prev), start + batch_faces)
            part = subdivide(prev[start:stop])
            out[start * 4:stop * 4] = checked_triangles(
                distort(part, amplitude, direction, centre, influence, dir_rng, mag_rng))
            if progress:
                progress((completed + stop * 4) / work, f"stage {stage} of {steps}")
        stages.append(out)
        amplitudes.append(amplitude)
        completed += len(out)
    return Fragmentation(stages, amplitudes, diagonal)


def bounds(triangles) -> np.ndarray:
    return np.stack((triangles.min(axis=(0, 1)), triangles.max(axis=(0, 1))))


def layout_offsets(stages, axis="x", gap=0.0) -> list[np.ndarray]:
    """Original stays in place; successors clear the preceding stage's actual upper bound."""
    if axis not in ("x", "y", "z") or not np.isfinite(gap) or gap < 0:
        raise ValueError("layout needs an X/Y/Z axis and a finite nonnegative gap")
    ax = "xyz".index(axis)
    offsets = [np.zeros(3)]
    upper = bounds(stages[0])[1, ax]
    for stage in stages[1:]:
        b = bounds(stage)
        offset = np.zeros(3)
        offset[ax] = upper + gap - b[0, ax]
        offsets.append(offset)
        upper = b[1, ax] + offset[ax]
    return offsets


def as_mesh(triangles):
    """Export triangle soup without trimesh's implicit merging/repair."""
    import trimesh
    return trimesh.Trimesh(vertices=triangles.reshape(-1, 3),
                           faces=np.arange(len(triangles) * 3).reshape(-1, 3), process=False)


def preview(stages, offsets=None, max_faces=None):
    """Sample intact triangles, sharing the display budget fairly so every stage remains visible."""
    from atlas_toolkit.geom.mesh import MeshData, PREVIEW_FACES
    max_faces = PREVIEW_FACES if max_faces is None else max_faces
    offsets = offsets if offsets is not None else [np.zeros(3) for _ in stages]
    quota, remainder = divmod(max_faces, len(stages))
    samples, boxes = [], []
    for i, (stage, offset) in enumerate(zip(stages, offsets)):
        n = min(len(stage), quota + (i < remainder))
        indices = np.linspace(0, len(stage) - 1, n, dtype=np.int64)
        samples.append(stage[indices] + offset)
        boxes.append(bounds(stage) + offset)
    triangles = checked_triangles(np.concatenate(samples))
    b = np.stack((np.min([b[0] for b in boxes], axis=0), np.max([b[1] for b in boxes], axis=0)))
    return MeshData(triangles.reshape(-1, 3).astype(np.float32),
                    np.arange(len(triangles) * 3, dtype=np.int32).reshape(-1, 3),
                    sum(len(s) for s in stages), tuple(tuple(map(float, row)) for row in b))
