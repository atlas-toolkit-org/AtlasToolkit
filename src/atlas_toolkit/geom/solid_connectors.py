"""Closed solid connector primitives and bounded organic sweeps shared by mesh tools.

Extracted from Surface Eject without changing its geometry or precision policy.
"""
import math
import manifold3d as mf
import numpy as np
import trimesh

_CAPS = {}

def to_mesh(body):
    data = body.to_mesh64()
    return trimesh.Trimesh(np.asarray(data.vert_properties)[:, :3], np.asarray(data.tri_verts), process=False)


def basis(normal):
    n = np.array(normal, dtype=float, copy=True)
    n /= np.linalg.norm(n)
    ref = np.eye(3)[np.argmin(np.abs(n))]
    u = np.cross(ref, n)
    u /= np.linalg.norm(u)
    return np.column_stack((u, np.cross(n, u), n))


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n > 0, n, 1.0)


def perpendicular(t) -> np.ndarray:
    """A unit vector perpendicular to unit t, chosen the same way for the same t (so a lattice's profiles line up)."""
    t = np.asarray(t, dtype=np.float64)
    a = np.array([1.0, 0.0, 0.0]) if abs(t[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    return unit(a - (a @ t) * t)


def frames(P) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rotation-minimising frames along a polyline (double reflection): tangents T, normals E1, binormals E2 with
    (E1, E2, T) right-handed."""
    P = np.asarray(P, dtype=np.float64)
    k = len(P)
    T = np.empty((k, 3))
    T[0] = P[1] - P[0]
    T[-1] = P[-1] - P[-2]
    if k > 2:
        T[1:-1] = P[2:] - P[:-2]
    T = unit(T)
    E1 = np.empty((k, 3))
    E1[0] = perpendicular(T[0])
    for i in range(k - 1):
        v1 = P[i + 1] - P[i]
        c1 = v1 @ v1
        if c1 < 1e-24:
            E1[i + 1] = E1[i]
            continue
        rL = E1[i] - (2.0 / c1) * (v1 @ E1[i]) * v1
        tL = T[i] - (2.0 / c1) * (v1 @ T[i]) * v1
        v2 = T[i + 1] - tL
        c2 = v2 @ v2
        E1[i + 1] = rL - (2.0 / c2) * (v2 @ rL) * v2 if c2 > 1e-24 else rL
        E1[i + 1] = unit(E1[i + 1] - (E1[i + 1] @ T[i + 1]) * T[i + 1])
    E2 = np.cross(T, E1)
    return T, E1, E2


def align_angle(t, e1_now, e1_want) -> float:
    """The turn about t that brings the frame normal e1_now onto e1_want (projected across t); 0 when e1_want runs
    along t."""
    w = np.asarray(e1_want, float)
    w = w - (w @ t) * t
    if np.linalg.norm(w) < 1e-6:
        return 0.0
    w = unit(w)
    return math.atan2(float(np.cross(e1_now, w) @ t), float(e1_now @ w))


def cap_triangles(outline) -> np.ndarray:
    import manifold3d
    key = (len(outline), outline.tobytes())
    tri = _CAPS.get(key)
    if tri is None:
        tri = np.asarray(manifold3d.triangulate([np.ascontiguousarray(outline, dtype=np.float64)]), dtype=np.int64)
        if len(_CAPS) > 4096:
            _CAPS.clear()
        _CAPS[key] = tri
    return tri


def signed_volume(V, F) -> float:
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def sweep_mesh(outline, P, scale, turn, e1=None) -> tuple[np.ndarray, np.ndarray]:
    """A closed solid: `outline` (N, 2, CCW) placed at every point of P (k, 3), scaled by scale (k,) and turned by
    turn (k,) radians about the path, with flat caps at both ends. `e1` fixes the first frame's normal (a lattice's
    profiles all face the same way). Returns (V, F) with outward faces."""
    P = np.asarray(P, dtype=np.float64)
    T, E1, E2 = frames(P)
    if e1 is not None:
        turn = np.asarray(turn, float) + align_angle(T[0], E1[0], e1)
    k, n = len(P), len(outline)
    c, s = np.cos(turn), np.sin(turn)
    x, y = outline[:, 0], outline[:, 1]
    # ring i, point j: P + sc*(x cos - y sin) E1 + sc*(x sin + y cos) E2
    X = (x[None, :] * c[:, None] - y[None, :] * s[:, None]) * scale[:, None]
    Y = (x[None, :] * s[:, None] + y[None, :] * c[:, None]) * scale[:, None]
    V = P[:, None, :] + X[..., None] * E1[:, None, :] + Y[..., None] * E2[:, None, :]
    V = V.reshape(-1, 3)
    i = np.arange(k - 1)[:, None] * n
    j = np.arange(n)[None, :]
    jn = (j + 1) % n
    a, b, cc, d = i + j, i + jn, i + n + jn, i + n + j
    side = np.concatenate([np.stack([a, b, cc], -1).reshape(-1, 3), np.stack([a, cc, d], -1).reshape(-1, 3)])
    cap = cap_triangles(outline)
    F = np.concatenate([side, cap[:, ::-1], cap + (k - 1) * n]).astype(np.int64)
    if signed_volume(V, F) < 0:
        F = F[:, ::-1]
    return V, F


def union(solids):
    return mf.Manifold.batch_boolean(list(solids), mf.OpType.Add)


def interior_candidates(mesh, width, eps):
    """Find real interior points, including on concave solids whose mass centroid is outside."""
    count = min(96, max(16, len(mesh.faces)))
    cumulative = np.cumsum(mesh.area_faces)
    ids = np.searchsorted(cumulative, (np.arange(count) + .5) / count * cumulative[-1])
    ids = np.unique(np.r_[ids, np.argsort(-mesh.area_faces, kind="stable")[:48]])
    tri = mesh.triangles[ids]
    k = np.arange(len(ids))
    u, v = (k * .61803398875 + .31) % 1, (k * .41421356237 + .17) % 1
    root = np.sqrt(u)
    sample = np.einsum("ij,ijk->ik", np.c_[1 - root, root * (1 - v), root * v], tri)
    centres = np.r_[tri.mean(1), sample]
    normals = np.tile(mesh.face_normals[ids], (2, 1))
    step = min(width * .28, float(mesh.extents.min()) * .45)
    queries = np.vstack([mesh.center_mass, mesh.bounds.mean(0)] +
                        [centres - normals * step * t for t in (.12, .4, 1.)])
    queries = queries[mesh.contains(queries)]
    if not len(queries):
        raise ValueError("No safe fragment interior was found. Increase sampling depth or reduce fragment size.")
    _, distances, _ = trimesh.proximity.closest_point(mesh, queries)
    keep = distances > eps * 20
    queries, distances = queries[keep], distances[keep]
    if not len(queries):
        raise ValueError("Fragment is too thin for a solid connector. Increase sampling depth.")
    # Prefer a roomy point near the mass centre, but never use that centre without containment testing.
    score = distances - .04 * np.linalg.norm(queries - mesh.center_mass, axis=1)
    best = int(np.argmax(score))
    return queries[best], float(distances[best]), queries, distances


def edge_clearance(cap, points):
    counts = np.bincount(cap.edges_unique_inverse, minlength=len(cap.edges_unique))
    edges = cap.vertices[cap.edges_unique[counts == 1]]
    if not len(edges):
        return np.zeros(len(points))
    start, delta = edges[:, 0], edges[:, 1] - edges[:, 0]
    result = []
    length2 = np.maximum(np.einsum("ij,ij->i", delta, delta), 1e-30)
    for point in points:
        t = np.clip(np.einsum("ij,ij->i", point - start, delta) / length2, 0, 1)
        result.append(np.linalg.norm(point - start - t[:, None] * delta, axis=1).min())
    return np.asarray(result)


def from_arrays(vertices, faces):
    solid = mf.Manifold(mf.Mesh64(np.array(vertices, dtype=np.float64, order="C", copy=True),
                                np.array(faces, dtype=np.uint64, order="C", copy=True)))
    if solid.status() != mf.Error.NoError or solid.is_empty() or solid.volume() <= 0:
        raise ValueError("Connector geometry could not form a valid closed solid.")
    return solid


def beam(a, b, radius, style, segments):
    delta = np.asarray(b) - a
    length = float(np.linalg.norm(delta))
    if style == "bar":
        shape = mf.Manifold.cube([2 * radius, 2 * radius, length]).translate([-radius, -radius, 0.])
    else:
        shape = mf.Manifold.cylinder(length, radius, circular_segments=segments)
    return shape.transform(np.column_stack((basis(delta), np.asarray(a))))


def stabilize(solid, height):
    """Remove sub-format boolean slivers with bounded, topology-preserving native simplification.

    Never repair by leaving holes. Both double and float32 cleaned copies must remain closed, and
    the accepted solid must retain its component count and volume to 1 ppm. This applies only to bridges
    and their fused assembly; original floating body/fragment exports are untouched.
    """
    from atlas_toolkit.geom.mesh import clean

    def safe(candidate):
        mesh = to_mesh(candidate)
        for vertices in (mesh.vertices, mesh.vertices.astype(np.float32)):
            copy = clean(trimesh.Trimesh(np.array(vertices, copy=True), mesh.faces.copy(), process=False))
            if not copy.is_volume or not copy.nondegenerate_faces().all() or len(copy.faces) != len(mesh.faces):
                return False
        return True

    if safe(solid):
        return solid, 0.
    mesh = to_mesh(solid)
    reset = from_arrays(mesh.vertices, mesh.faces)
    components, volume = len(solid.decompose()), solid.volume()
    for tolerance in (height * 1e-8, height * 5e-8, height * 2e-7):
        fixed = reset.simplify(tolerance)
        if (fixed.status() == mf.Error.NoError and len(fixed.decompose()) == components
                and abs(fixed.volume() - volume) <= volume * 1e-6 and safe(fixed)):
            return fixed, tolerance
        # Dense tree unions can leave redundant paired triangles after a precision weld. Accept the
        # actual closed copy only: never return the unsafe pre-clean mesh or delete faces leaving holes.
        allowance = tolerance + np.sqrt(3.) * 1e-8
        if allowance <= height * 2e-7:
            raw = to_mesh(fixed)
            cleaned = clean(trimesh.Trimesh(raw.vertices.copy(), raw.faces.copy(), process=False))
            if cleaned.is_volume:
                repaired = from_arrays(cleaned.vertices, cleaned.faces)
                if (len(repaired.decompose()) == components and abs(repaired.volume() - volume) <= volume * 1e-6
                        and safe(repaired)):
                    return repaired, allowance
    raise ValueError("Connector joints could not be stabilized for reliable export. Reduce branches or increase thickness.")


def curve(a, b, axis_origin, axis, extent, amount, irregularity, phase):
    t = np.linspace(0., 1., 25)
    points = np.asarray(a) + t[:, None] * (np.asarray(b) - a)
    frame = basis(np.asarray(b) - a)
    amplitude = min(extent * amount, np.linalg.norm(np.asarray(b) - a) * .14)
    angle = phase + irregularity * .7 * np.sin(2 * np.pi * t)
    points += (amplitude * np.sin(np.pi * t) ** 2)[:, None] * (
        np.cos(angle)[:, None] * frame[:, 0] + np.sin(angle)[:, None] * frame[:, 1])
    # The same envelope applies to the main stem, every fork, and their end positions.
    axial = np.asarray(axis_origin) + np.outer((points - axis_origin) @ axis, axis)
    lateral = points - axial
    length = np.linalg.norm(lateral, axis=1)
    points = axial + lateral * np.minimum(1., extent / np.maximum(length, 1e-30))[:, None]
    points[0], points[-1] = a, b
    return points


def tube(path, radius, taper, irregularity, phase, segments):
    t = np.linspace(0., 1., len(path))
    radii = radius * (1 - (1 - taper) * t)
    radii *= 1 - .15 * irregularity * np.sin(np.pi * t) ** 2 * (.5 + .5 * np.sin(phase + t * 4 * np.pi))
    angles = np.arange(segments) * 2 * np.pi / segments
    outline = np.c_[np.cos(angles), np.sin(angles)]
    v, f = sweep_mesh(outline, path, radii, np.zeros(len(path)))
    return from_arrays(v, f)
