"""A region of space shaped as a box, sphere, cylinder or pyramid and placed on a model by a frame; no Qt or file I/O.

The unit shape sits centred on the origin inside the cube [-1/2, 1/2]^3 (sizes 1 along its own axes; a cylinder's
axis and a pyramid's apex point along +Z). A 4x4 frame (rotation x sizes, then the centre) places it on the model.
Each shape is an intersection of elementary constraints in its own unit space: half-spaces, a sphere and an infinite
cylinder, each with an exact crossing on any segment (linear or quadratic), so a surface clipped against them has its
new vertices exactly on the shape. Shared by Shape Crop's engine and the shape editor (ui/widgets/shape_editor.py).
"""
from __future__ import annotations

import math

import numpy as np

SHAPES = ("box", "sphere", "cylinder", "pyramid")
CURVED = ("sphere", "cylinder")
KIND_CODE = {"box": 0, "sphere": 1, "cylinder": 2, "pyramid": 3}      # for shaders and compact storage


def model_size(bounds) -> float:
    """The model's largest dimension (what % sizes are a share of)."""
    return float(np.ptp(np.asarray(bounds, dtype=float), axis=0).max()) or 1.0


def sizes(bounds, p) -> np.ndarray:
    """The shape's sizes along its own axes, in model units."""
    s = np.asarray(p["size"], dtype=float)
    return s * model_size(bounds) / 100.0 if p.get("size_unit", "units") == "percent" else s


def frame(bounds, p) -> np.ndarray:
    """The 4x4 matrix that takes the unit shape to the model: XYZ Euler rotation (degrees) times sizes, then the
    centre (`position` from the model's bounds centre or the world origin, as Plane Cut places its plane)."""
    from scipy.spatial.transform import Rotation
    from .plane import reference_origin
    rot = Rotation.from_euler("xyz", p["rotation"], degrees=True).as_matrix()
    m = np.eye(4)
    m[:3, :3] = rot * sizes(bounds, p)               # rot @ diag(sizes)
    m[:3, 3] = reference_origin(bounds, p.get("reference", "center")) + np.asarray(p["position"], dtype=float)
    return m


def to_local(points, m) -> np.ndarray:
    """World points in the unit shape's own coordinates."""
    return (np.asarray(points, dtype=np.float64) - m[:3, 3]) @ np.linalg.inv(m[:3, :3]).T


def to_world(points, m) -> np.ndarray:
    return np.asarray(points, dtype=np.float64) @ m[:3, :3].T + m[:3, 3]


def world_bounds(kind: str, m) -> np.ndarray:
    """The shape's axis-aligned bounds in the model (its unit cube's corners, transformed)."""
    corners = np.array([[x, y, z] for x in (-.5, .5) for y in (-.5, .5) for z in (-.5, .5)])
    if kind == "pyramid":
        corners = np.vstack([corners[[0, 2, 4, 6]], [[0, 0, .5]]])
    w = to_world(corners, m)
    return np.array([w.min(0), w.max(0)])


# ------------------------------------------------------------------------------------------------ constraints
# A constraint is (kind, data): "plane" (n, d) means n.p <= d; "sphere" r means |p| <= r; "cylinder" r means
# x^2 + y^2 <= r. Values are negative inside.

def constraints(kind: str) -> list[tuple]:
    if kind == "box":
        return [("plane", (np.eye(3)[i] * s, .5)) for i in range(3) for s in (1.0, -1.0)]
    if kind == "sphere":
        return [("sphere", .5)]
    if kind == "cylinder":
        return [("plane", (np.array([0., 0., 1.]), .5)), ("plane", (np.array([0., 0., -1.]), .5)),
                ("cylinder", .5)]
    if kind == "pyramid":       # base z = -1/2, apex (0, 0, 1/2): |x|, |y| <= (1/2 - z) / 2
        return [("plane", (np.array([0., 0., -1.]), .5))] + [
            ("plane", (np.array(n, float), .5)) for n in ((2, 0, 1), (-2, 0, 1), (0, 2, 1), (0, -2, 1))]
    raise ValueError(f"unknown shape {kind!r}")


def value(c, local) -> np.ndarray:
    kind, data = c
    if kind == "plane":
        n, d = data
        return local @ n - d
    if kind == "sphere":
        return np.einsum("ij,ij->i", local, local) - data * data
    return local[:, 0] ** 2 + local[:, 1] ** 2 - data * data


def crossing(c, a, b, va, vb) -> np.ndarray:
    """Where each segment a -> b (unit-space points, values va and vb of opposite sign) meets the constraint's
    surface, as t in [0, 1]: exact for planes (linear) and for the sphere and cylinder (the quadratic's root)."""
    kind, data = c
    if kind == "plane":
        return np.clip(va / (va - vb), 0.0, 1.0)
    d = b - a
    if kind == "cylinder":
        a, d = a[:, :2], d[:, :2]
    qa = np.einsum("ij,ij->i", d, d)
    qb = 2.0 * np.einsum("ij,ij->i", a, d)
    qc = va                                          # |a|^2 - r^2
    disc = np.sqrt(np.maximum(qb * qb - 4.0 * qa * qc, 0.0))
    # the stable pair of roots; exactly one lies in [0, 1] because the values change sign along the segment
    q = -0.5 * (qb + np.copysign(disc, qb))
    with np.errstate(divide="ignore", invalid="ignore"):
        t1 = np.where(qa > 0, q / qa, np.nan)
        t2 = np.where(q != 0, qc / q, np.nan)
    t = np.where((t1 >= 0) & (t1 <= 1), t1, t2)
    bad = ~np.isfinite(t)
    if bad.any():                                    # degenerate: fall back to the linear estimate
        t[bad] = va[bad] / (va[bad] - vb[bad])
    return np.clip(t, 0.0, 1.0)


def inside(kind: str, local) -> np.ndarray:
    """Which unit-space points are in the shape (boundary included)."""
    ok = np.ones(len(local), bool)
    for c in constraints(kind):
        ok &= value(c, local) <= 0
    return ok


# ------------------------------------------------------------------------------------------------ drawing
def outline(kind: str, segments: int = 64) -> np.ndarray:
    """The unit shape's wireframe as (N, 2, 3) segments, for overlays: edges, or rings and meridians."""
    t = np.linspace(0, 2 * math.pi, segments + 1)
    ring = np.column_stack((np.cos(t), np.sin(t), np.zeros_like(t))) * .5

    def poly(points):
        return np.stack((points[:-1], points[1:]), axis=1)

    if kind == "box":
        c = np.array([[x, y, z] for x in (-.5, .5) for y in (-.5, .5) for z in (-.5, .5)])
        pairs = [(i, j) for i in range(8) for j in range(i + 1, 8) if bin(i ^ j).count("1") == 1]
        return np.array([(c[i], c[j]) for i, j in pairs])
    if kind == "sphere":
        out = [poly(ring), poly(ring[:, [0, 2, 1]]), poly(ring[:, [2, 0, 1]])]
        return np.concatenate(out)
    if kind == "cylinder":
        top, bottom = ring + [0, 0, .5], ring - [0, 0, .5]
        sides = np.array([(bottom[i], top[i]) for i in range(0, segments, max(segments // 4, 1))])
        return np.concatenate([poly(top), poly(bottom), sides])
    if kind == "pyramid":
        base = np.array([[-.5, -.5, -.5], [.5, -.5, -.5], [.5, .5, -.5], [-.5, .5, -.5]])
        apex = np.array([0, 0, .5])
        return np.array([(base[i], base[(i + 1) % 4]) for i in range(4)] + [(b, apex) for b in base])
    raise ValueError(f"unknown shape {kind!r}")


# ------------------------------------------------------------------------------------------------ solids
def auto_segments(kind: str, m, edge: float) -> int:
    """Segments around a sphere or cylinder so that its facets are about twice the model's edge length."""
    if kind not in CURVED:
        return 4
    s = np.linalg.norm(m[:3, :3], axis=0)
    circumference = math.pi * float(s.max() if kind == "sphere" else max(s[0], s[1]))
    n = circumference / max(2.0 * edge, 1e-12)
    return int(min(max(round(n / 4) * 4, 48), 720))


def solid(kind: str, m, segments: int = 96):
    """The shape as a manifold3d solid, placed by frame m."""
    import manifold3d as mf
    if kind == "box":
        body = mf.Manifold.cube((1.0, 1.0, 1.0), True)
    elif kind == "sphere":
        body = mf.Manifold.sphere(.5, int(segments))
    elif kind == "cylinder":
        body = mf.Manifold.cylinder(1.0, .5, .5, int(segments), True)
    elif kind == "pyramid":
        body = mf.Manifold.hull_points(np.array([[-.5, -.5, -.5], [.5, -.5, -.5], [.5, .5, -.5], [-.5, .5, -.5],
                                                 [0., 0., .5]]))
    else:
        raise ValueError(f"unknown shape {kind!r}")
    body = body.transform(np.ascontiguousarray(m[:3, :4]))
    if kind == "cylinder":
        # its side is long slivers and its caps thin fans, whose bounds overlap a large share of a fine model, so
        # the boolean slowed to seconds per million faces (8 s at 1.3M); near-square facets take a fraction of one
        s = np.linalg.norm(m[:3, :3], axis=0)
        body = body.refine_to_length(1.5 * math.pi * max(s[0], s[1]) / max(int(segments), 3))
    return body


def volume(kind: str, m) -> float:
    """The exact volume of the placed shape."""
    det = abs(float(np.linalg.det(m[:3, :3])))
    unit = {"box": 1.0, "sphere": math.pi / 6.0, "cylinder": math.pi / 4.0, "pyramid": 1.0 / 3.0}[kind]
    return unit * det
