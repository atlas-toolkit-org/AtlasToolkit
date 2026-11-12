"""Sculptural placement and exact union, without GUI or file I/O.

Layout coordinates are XY with a perpendicular Z axis. A plane basis maps these into Atlas's upright model
frame. Every placement is a positive-determinant affine transform, so the source keeps its outward winding.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from atlas_toolkit.geom.frame3d import euler_xyz

MAX_COPIES = 2048
DRAFT_TOTAL_FACES = 200_000
GOLDEN_ANGLE = math.pi * (3 - math.sqrt(5))
RAY_LAYOUTS = ("cross2d", "cross3d", "star2d", "star3d")


def unit(v):
    a = np.asarray(v, dtype=float)
    length = float(np.linalg.norm(a))
    if not np.isfinite(length) or length < 1e-12:
        raise ValueError("the layout has a zero-length direction; increase Radius or Rise per turn")
    return a / length


def perpendicular(v, reference):
    r = np.asarray(reference, dtype=float)
    projected = r - v * (v @ r)
    if np.linalg.norm(projected) < 1e-8:
        r = np.eye(3)[int(np.argmin(np.abs(v)))]
        projected = r - v * (v @ r)
    return unit(projected)


def aim(direction, facing, axis="up"):
    """Map model +Z (or -Y) onto direction, keeping the secondary axis as near facing as possible."""
    d = unit(direction)
    if axis == "front":
        y, z = -d, perpendicular(d, facing)
    else:
        z, y = d, -perpendicular(d, facing)
    return np.column_stack((np.cross(y, z), y, z))


def turn(axis, angle):
    v = unit(axis)
    x, y, z = v
    K = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + math.sin(angle) * K + (1 - math.cos(angle)) * (K @ K)


def plane_basis(plane):
    return {"xy": np.eye(3), "xz": np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]]),
            "yz": np.array([[0, 0, 1], [1, 0, 0], [0, 1, 0]])}[plane]


def directions(symmetry):
    """Unit directions at the vertices of a Platonic solid; no random approximation to spatial symmetry."""
    phi = (1 + math.sqrt(5)) / 2
    if symmetry == "tetrahedron":
        a = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]], float)
    elif symmetry == "octahedron":
        a = np.concatenate((np.eye(3), -np.eye(3)))
    elif symmetry == "cube":
        a = np.array([(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)
    elif symmetry == "icosahedron":
        a = np.array([np.roll((0, s, t * phi), k) for k in range(3) for s in (-1, 1) for t in (-1, 1)])
    elif symmetry == "dodecahedron":
        a = np.concatenate((directions("cube") * math.sqrt(3),
                            [np.roll((0, s / phi, t * phi), k) for k in range(3)
                             for s in (-1, 1) for t in (-1, 1)]))
    else:
        raise ValueError(f"unknown spatial symmetry: {symmetry}")
    return a / np.linalg.norm(a, axis=1)[:, None]


def proposed_count(p):
    layout = p["layout"]
    if layout in RAY_LAYOUTS:
        arms = {"cross2d": 4, "cross3d": 6, "star2d": p["arms"],
                "star3d": {"tetrahedron": 4, "octahedron": 6, "cube": 8,
                           "icosahedron": 12, "dodecahedron": 20}[p["symmetry"]]}[layout]
        n = arms * p["per_arm"] + int(p["centre_copy"])
    else:
        n = p["count"] * (p["rows"] if layout in ("torus", "mobius") else 1)
    if n > MAX_COPIES:
        raise ValueError(f"{n} proposed copies exceeds {MAX_COPIES}; lower Copies per arm, Rows or Copies around / along")
    return n


def curve_samples(fn, n):
    """Equal arc-length samples of a closed analytic curve (no repeated endpoint)."""
    t = np.linspace(0, 2 * math.pi, max(4097, n * 16 + 1))
    pts = fn(t)
    lengths = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]
    if lengths[-1] < 1e-10:
        raise ValueError("Radius must be greater than zero for this layout")
    angles = np.interp(np.arange(n) * lengths[-1] / n, lengths, t)
    points = fn(angles)
    tangents = fn(angles + 1e-5) - fn(angles - 1e-5)
    tangents /= np.linalg.norm(tangents, axis=1)[:, None]
    return points, tangents


def transported_normals(tangents):
    """Rotation-minimising frames, distributing the closed curve's residual roll over the loop."""
    normals = [perpendicular(tangents[0], (0, 0, 1))]
    for a, b in zip(tangents[:-1], tangents[1:]):
        cross = np.cross(a, b)
        angle = math.atan2(float(np.linalg.norm(cross)), float(a @ b))
        normal = turn(cross, angle) @ normals[-1] if np.linalg.norm(cross) > 1e-8 else normals[-1]
        normals.append(perpendicular(b, normal))
    end = normals[-1]
    cross = np.cross(tangents[-1], tangents[0])
    if np.linalg.norm(cross) > 1e-8:
        end = turn(cross, math.atan2(float(np.linalg.norm(cross)), float(tangents[-1] @ tangents[0]))) @ end
    residual = math.atan2(float(tangents[0] @ np.cross(end, normals[0])), float(end @ normals[0]))
    return np.array([turn(t, residual * i / len(normals)) @ n for i, (t, n) in enumerate(zip(tangents, normals))])


@dataclass
class Slot:
    position: np.ndarray
    radial: np.ndarray
    tangent: np.ndarray
    normal: np.ndarray
    fraction: float


@dataclass
class Plan:
    transforms: np.ndarray           # (copies, 3, 4), source (already anchored) -> composition
    proposed: int
    omitted: int


def slots(p, height):
    layout = p["layout"]
    radius = p["inner_radius" if layout in RAY_LAYOUTS else "radius"] * height / 100
    n = p["count"]
    Z = np.array([0., 0., 1.])
    out = []

    def add(pos, radial, tangent, normal, fraction):
        out.append(Slot(np.asarray(pos, float), unit(radial), unit(tangent), unit(normal), float(fraction)))

    if layout in RAY_LAYOUTS:
        if layout in ("cross3d", "star3d"):
            rays = directions("octahedron" if layout == "cross3d" else p["symmetry"])
        else:
            angles = math.pi / 2 + np.arange(4 if layout == "cross2d" else p["arms"]) * (
                2 * math.pi / (4 if layout == "cross2d" else p["arms"]))
            rays = np.column_stack((np.cos(angles), np.sin(angles), np.zeros(len(angles))))
        if p["centre_copy"]:
            add((0, 0, 0), (0, 1, 0), (1, 0, 0), Z, 0)
        for d in rays:
            normal = perpendicular(d, Z)
            for j in range(p["per_arm"]):
                distance = radius + (j + 0.5) * p["spacing"] * height / 100
                add(d * distance, d, d, normal, j / max(p["per_arm"] - 1, 1))
    elif layout in ("clock", "sunflower", "urchin", "helix"):
        if radius <= 0 and layout != "helix":
            raise ValueError("Radius must be greater than zero for this layout")
        for i in range(n):
            f = i / max(n - 1, 1)
            if layout == "clock":
                span = math.radians(p["arc"])
                angle = math.pi / 2 - i * span / (n if p["arc"] == 360 else max(n - 1, 1))
            elif layout == "helix":
                angle = 2 * math.pi * p["turns"] * (f - 0.5)
            else:
                angle = i * GOLDEN_ANGLE
            d = np.array([math.cos(angle), math.sin(angle), 0.])
            tangent = np.array([-d[1], d[0], 0.])
            if layout == "urchin":
                z = 1 - (1 - math.cos(math.radians(p["coverage"]))) * (i + 0.5) / n
                d = np.array([math.sqrt(max(0, 1 - z * z)) * d[0],
                              math.sqrt(max(0, 1 - z * z)) * d[1], z])
                add(radius * d, d, tangent, d, f)
            elif layout == "helix":
                pitch = p["pitch"] * height / 100
                pos = radius * d + Z * pitch * p["turns"] * (f - 0.5)
                add(pos, d, radius * tangent + Z * pitch / (2 * math.pi), d, f)
            else:
                r = radius * math.sqrt((i + 0.5) / n) if layout == "sunflower" else radius
                add(r * d, d, -tangent if layout == "clock" else tangent, Z, f)
    elif layout in ("rosette", "trefoil"):
        if layout == "rosette":
            def curve(t):
                r = radius * (1 + p["lobes"] / 100 * np.cos(p["petals"] * t))
                return np.column_stack((r * np.cos(t), r * np.sin(t), np.zeros_like(t)))
        else:
            def curve(t):
                return radius / 3 * np.column_stack((np.sin(t) + 2 * np.sin(2 * t),
                                                      np.cos(t) - 2 * np.cos(2 * t), -np.sin(3 * t)))
        points, tangents = curve_samples(curve, n)
        normals = np.tile(Z, (n, 1)) if layout == "rosette" else transported_normals(tangents)
        for i, (pos, tangent, normal) in enumerate(zip(points, tangents, normals)):
            add(pos, pos, tangent, normal, i / max(n - 1, 1))
    elif layout in ("torus", "mobius"):
        minor = p["minor_radius"] * height / 100
        if radius <= minor:
            raise ValueError("Radius must be greater than Tube radius / half-width to preserve the central opening")
        for i in range(n):
            u = 2 * math.pi * i / n
            d = np.array([math.cos(u), math.sin(u), 0.])
            tangent = np.array([-d[1], d[0], 0.])
            for j in range(p["rows"]):
                if layout == "torus":
                    v = 2 * math.pi * j / p["rows"]
                    normal = math.cos(v) * d + math.sin(v) * Z
                    add(radius * d + minor * normal, normal, tangent, normal, i / max(n - 1, 1))
                else:
                    w = minor * (2 * j / (p["rows"] - 1) - 1) if p["rows"] > 1 else 0
                    across = math.cos(u / 2) * d + math.sin(u / 2) * Z
                    along = ((radius + w * math.cos(u / 2)) * tangent
                             + w / 2 * (-math.sin(u / 2) * d + math.cos(u / 2) * Z))
                    normal = np.cross(along, across)
                    add(radius * d + w * across, d, along, normal, i / max(n - 1, 1))
    else:
        raise ValueError(f"unknown form layout: {layout}")
    return out


def plan(p, height=2.0):
    proposed = proposed_count(p)        # reject multiplicative budgets before allocating slots
    points = slots(p, height)
    basis = plane_basis(p["plane"]) @ turn((0, 0, 1), math.radians(p["phase"]))
    streams = [np.random.default_rng(s) for s in np.random.SeedSequence(int(p["seed"])).spawn(4)]
    positions = streams[0].uniform(-1, 1, (proposed, 3))
    angles = streams[1].uniform(-1, 1, (proposed, 3))
    sizes = streams[2].uniform(-1, 1, proposed)
    keep = streams[3].random(proposed) >= p["omit"] / 100
    transforms = []
    for i, slot in enumerate(points):
        if not keep[i]:
            continue
        if p["orientation"] == "upright" or (p["layout"] in RAY_LAYOUTS and p["centre_copy"] and i == 0):
            rotation = np.eye(3)
        else:
            d = {"radial": slot.radial, "inward": -slot.radial,
                 "tangent": slot.tangent, "normal": slot.normal}[p["orientation"]]
            reference = slot.normal if abs(float(d @ slot.normal)) < 0.95 else slot.tangent
            rotation = aim(basis @ d, basis @ reference, p["aim_axis"])
        axis = (0, 0, 1) if p["aim_axis"] == "up" else (0, -1, 0)
        rotation = rotation @ turn(axis, math.radians(p["roll"] + p["spin"] * slot.fraction))
        rotation = rotation @ euler_xyz(angles[i] * p["angle_jitter"] * p["irregularity"])
        scale = p["copy_size"] / 100 * (1 + (p["end_scale"] / 100 - 1) * slot.fraction)
        scale *= 1 + sizes[i] * p["size_jitter"] / 100 * p["irregularity"]
        matrix = rotation @ np.diag(np.asarray(p["stretch"]) / 100 * scale)
        position = basis @ slot.position + positions[i] * p["position_jitter"] * height / 100 * p["irregularity"]
        transforms.append(np.column_stack((matrix, position)))
    if not transforms:
        raise ValueError("Leave out removed every copy; reduce it or change Seed")
    return Plan(np.asarray(transforms), proposed, proposed - len(transforms))


def check_faces(copies, faces, limit):
    work = copies * faces
    if work > limit:
        raise ValueError(f"{copies} copies × {faces:,} source faces = {work:,}, over the {limit:,} working face limit; "
                         "lower Source detail, Copies per arm, Rows or Copies around / along")


def fuse(source, transforms, face_limit, progress=None):
    """Exact boolean union, evaluated in small batches with cancellation checkpoints between native calls."""
    import manifold3d
    check_faces(len(transforms), source.num_tri(), face_limit)
    pieces = []
    for i, matrix in enumerate(transforms):
        if progress:
            progress(0.10 * i / len(transforms), f"placing copy {i + 1} of {len(transforms)}")
        pieces.append(source.transform(matrix))
    original = len(pieces)
    done = 0
    while len(pieces) > 1:
        merged = []
        for start in range(0, len(pieces), 8):
            if progress:
                progress(0.1 + 0.88 * done / max(original - 1, 1), "fusing touching copies")
            batch = pieces[start:start + 8]
            result = manifold3d.Manifold.batch_boolean(batch, manifold3d.OpType.Add)
            count = result.num_tri()           # evaluate lazy booleans here, between checkpoints
            if result.status() != manifold3d.Error.NoError or not count:
                raise ValueError(f"the solid union failed ({result.status()}); check the source or change the spacing")
            if count > face_limit:
                raise ValueError("the fused mesh exceeds the working face limit; lower Source detail or the copy count")
            merged.append(result)
            done += len(batch) - 1
        pieces = merged
    if progress:
        progress(1.0, "solid union complete")
    return pieces[0]
