"""
The model catalogue's generated shapes (core/catalogue.py): primitives, knots, one-sided surfaces, minimal surfaces,
fractals and turned objects, built in code instead of shipped as files. No Qt.

Every generator is deterministic and returns a trimesh.Trimesh in the toolkit's canonical frame (Z up, the front
toward -Y, as geom/frame3d.py), centred on its bounding box with the largest side 100 units. Flat-sided solids are
subdivided evenly so that every shape has enough triangles for the mesh tools (about 20k and up).

    build(name, **params) -> Trimesh        GENERATORS lists the names; params override each one's defaults

Some shapes are surfaces that pass through themselves (Klein bottle, Boy's surface) or solids whose cubes touch along
an edge (Menger sponge): that is what they are, and the drawing tools take them.

The catalogue does not build them on the user's computer: scripts/catalogue.py makes their files at build time, the
simple solids ship in the app and the rest download like any other model.
"""
from __future__ import annotations

import math

import numpy as np

SIZE = 100.0


# ----------------------------------------------------------------------------- helpers
def _trimesh(v, f, fix=True):
    import trimesh
    m = trimesh.Trimesh(np.asarray(v, float), np.asarray(f, np.int64), process=False)
    m.merge_vertices(digits_vertex=7)
    m.update_faces(m.nondegenerate_faces())
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    if fix:
        if m.is_winding_consistent and m.is_watertight:      # built consistently: at most inside out
            if m.volume < 0:
                m.invert()
        else:
            m.fix_normals()
    return m


def _fine(m, edges: int = 60):
    """Flat faces split into small even triangles (about `edges` along the largest side)."""
    import trimesh
    v, f = trimesh.remesh.subdivide_to_size(m.vertices, m.faces, max_edge=float(m.extents.max()) / edges)
    return _trimesh(v, f)


def _grid_faces(nu, nv, wrap_u, wrap_v):
    """Two triangles per cell of an nu × nv vertex grid (index i * nv + j), optionally wrapping either way."""
    iu = np.arange(nu if wrap_u else nu - 1)
    jv = np.arange(nv if wrap_v else nv - 1)
    i, j = np.meshgrid(iu, jv, indexing="ij")
    i, j = i.ravel(), j.ravel()
    i1, j1 = (i + 1) % nu, (j + 1) % nv
    a, b, c, d = i * nv + j, i1 * nv + j, i1 * nv + j1, i * nv + j1
    return np.concatenate([np.stack([a, b, c], 1), np.stack([a, c, d], 1)])


def _parametric(fn, nu, nv, u_range, v_range, wrap_u=False, wrap_v=False, fix=True):
    u = np.linspace(*u_range, nu, endpoint=not wrap_u)
    v = np.linspace(*v_range, nv, endpoint=not wrap_v)
    U, V = np.meshgrid(u, v, indexing="ij")
    P = np.stack(fn(U, V), -1).reshape(-1, 3)
    return _trimesh(P, _grid_faces(nu, nv, wrap_u, wrap_v), fix)


def _frames(P):
    """Rotation-minimising frames along a closed polyline (double reflection), with the closing twist spread out."""
    n = len(P)
    T = np.roll(P, -1, 0) - np.roll(P, 1, 0)
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    a = np.array([0.0, 0.0, 1.0]) if abs(T[0, 2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    R = np.empty_like(P)
    R[0] = np.cross(T[0], a)
    R[0] /= np.linalg.norm(R[0])
    r = R[0]
    for i in range(n):
        k = (i + 1) % n
        v1 = P[k] - P[i]
        c1 = v1 @ v1
        rl = r - (2 / c1) * (v1 @ r) * v1
        tl = T[i] - (2 / c1) * (v1 @ T[i]) * v1
        v2 = T[k] - tl
        c2 = v2 @ v2
        r = rl - (2 / c2) * (v2 @ rl) * v2 if c2 > 1e-20 else rl
        if k:
            R[k] = r
    # r is the frame carried once round, back at the start: turn the frames by the mismatch, a little at each step
    S0 = np.cross(T[0], R[0])
    gap = math.atan2(r @ S0, r @ R[0])
    ang = -gap * np.arange(n) / n
    S = np.cross(T, R)
    R = R * np.cos(ang)[:, None] + S * np.sin(ang)[:, None]
    R /= np.linalg.norm(R, axis=1, keepdims=True)
    return T, R, np.cross(T, R)


def _tube(P, section, twist=0.0, shift=0, scale=None):
    """A closed tube along the closed polyline P. `section` is (m, 2) points of the cross-section (counter-clockwise);
    it turns by `twist` radians along the loop and the last ring joins the first `shift` points along (a twist that
    brings the section onto itself). `scale` (n,) sizes the section along the way."""
    P = np.asarray(P, float)
    n, m = len(P), len(section)
    _, R, S = _frames(P)
    t = np.arange(n) / n * twist
    cs = np.asarray(section, float)
    k = np.ones(n) if scale is None else np.asarray(scale, float)
    x = (cs[None, :, 0] * np.cos(t)[:, None] - cs[None, :, 1] * np.sin(t)[:, None]) * k[:, None]
    y = (cs[None, :, 0] * np.sin(t)[:, None] + cs[None, :, 1] * np.cos(t)[:, None]) * k[:, None]
    V = P[:, None, :] + x[..., None] * R[:, None, :] + y[..., None] * S[:, None, :]
    F = _grid_faces(n, m, True, True)
    if shift:                                            # the seam: ring n-1 joins ring 0 turned by `shift`
        rows, cols = F // m, F % m
        seam = (rows == n - 1).any(1) & (rows == 0).any(1)
        at = seam[:, None] & (rows == 0)
        F[at] = (cols[at] + shift) % m
    return _trimesh(V.reshape(-1, 3), F)


def _circle(r, m):
    a = np.linspace(0, 2 * math.pi, m, endpoint=False)
    return np.stack([r * np.cos(a), r * np.sin(a)], 1)


def _rectangle(w, h, m):
    """m points round a w × h rectangle, evenly spaced, symmetric under a half turn (m even)."""
    per = 2 * (w + h)
    s = (np.arange(m) / m * per + w / 2) % per
    pts = []
    for d in s:
        if d < w:
            pts.append((w / 2 - d, h / 2))
        elif d < w + h:
            pts.append((-w / 2, h / 2 - (d - w)))
        elif d < 2 * w + h:
            pts.append((-w / 2 + (d - w - h), -h / 2))
        else:
            pts.append((w / 2, -h / 2 + (d - 2 * w - h)))
    return np.asarray(pts)


def _lathe(profile, m=256):
    """A solid of revolution about Z from an (r, z) profile that starts and ends on the axis (r = 0)."""
    prof = np.asarray(profile, float)
    inner = prof[1:-1]
    a = np.linspace(0, 2 * math.pi, m, endpoint=False)
    rings = np.stack([inner[:, None, 0] * np.cos(a), inner[:, None, 0] * np.sin(a),
                      np.repeat(inner[:, None, 1], m, 1)], -1).reshape(-1, 3)
    bottom, top = len(rings), len(rings) + 1
    V = np.vstack([rings, [0, 0, prof[0, 1]], [0, 0, prof[-1, 1]]])
    F = [_grid_faces(len(inner), m, False, True)]
    j = np.arange(m)
    F.append(np.stack([np.full(m, bottom), j, (j + 1) % m], 1))
    last = (len(inner) - 1) * m
    F.append(np.stack([np.full(m, top), last + (j + 1) % m, last + j], 1))
    return _trimesh(V, np.vstack(F))


def _smooth_profile(points, sharp=(), iterations=4):
    """Chaikin corner cutting of an (r, z) polyline; the indices in `sharp` (and both ends) stay sharp corners."""
    P = np.asarray(points, float)
    keep = [i in sharp or i in (0, len(P) - 1) for i in range(len(P))]
    for _ in range(iterations):
        out, flags = [], []
        for i in range(len(P) - 1):
            a, b = P[i], P[i + 1]
            if keep[i]:
                out.append(a)
                flags.append(True)
            else:
                out.append(0.75 * a + 0.25 * b)
                flags.append(False)
            if not keep[i + 1]:
                out.append(0.25 * a + 0.75 * b)
                flags.append(False)
        out.append(P[-1])
        flags.append(True)
        P, keep = np.asarray(out), flags
    return P


def _implicit(field, bound, res, box=True):
    """The zero level of `field(x, y, z)` (negative inside) in the cube [-bound, bound]³, closed by the cube."""
    from skimage.measure import marching_cubes
    g = np.linspace(-bound, bound, res)
    X, Y, Z = np.meshgrid(g, g, g, indexing="ij")
    f = field(X, Y, Z)
    if box:
        f = np.maximum(f, np.maximum(np.maximum(abs(X), abs(Y)), abs(Z)) - bound * 0.999)
    f = np.pad(f, 1, constant_values=float(np.abs(f).max()) + 1)
    step = g[1] - g[0]
    v, faces, _, _ = marching_cubes(f, 0.0, spacing=(step, step, step))
    m = _trimesh(v - bound - step, faces[:, ::-1])
    parts = m.split(only_watertight=False)
    if len(parts) > 1:                                   # crumbs where the surface grazes the grid
        import trimesh
        big = max(len(p.faces) for p in parts)
        m = trimesh.util.concatenate([p for p in parts if len(p.faces) >= 0.01 * big])
    return m


def _boolean(meshes, op):
    import trimesh
    return trimesh.boolean.boolean_manifold(meshes, op)


def _box(ext, centre=(0, 0, 0)):
    import trimesh
    return trimesh.creation.box(extents=ext, transform=trimesh.transformations.translation_matrix(centre))


# ----------------------------------------------------------------------------- simple solids
def sphere(subdivisions=6):
    import trimesh
    return trimesh.creation.icosphere(subdivisions=subdivisions)


def cube():
    return _fine(_box((1, 1, 1)), 45)


def _stand(m):
    """A tetrahedron of corners (±1, ±1, ±1) turned to stand on a face, a corner up."""
    import trimesh
    rot = trimesh.geometry.align_vectors(np.array([1.0, 1.0, 1.0]) / 3 ** 0.5, np.array([0.0, 0.0, 1.0]))
    return m.apply_transform(rot)


def _platonic(name):
    import trimesh
    phi = (1 + 5 ** 0.5) / 2
    if name == "tetrahedron":
        v = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]], float)
    elif name == "octahedron":
        v = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], float)
    elif name == "icosahedron":
        v = trimesh.creation.icosahedron().vertices
    else:                                                     # dodecahedron
        v = [[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
        for a in (-1, 1):
            for b in (-1, 1):
                v += [[0, a / phi, b * phi], [a / phi, b * phi, 0], [a * phi, 0, b / phi]]
        v = np.array(v, float)
    return _fine(trimesh.convex.convex_hull(v), 70)


def tetrahedron():
    return _stand(_platonic("tetrahedron"))


def octahedron():
    return _platonic("octahedron")


def dodecahedron():
    return _platonic("dodecahedron")


def icosahedron():
    return _platonic("icosahedron")


def pyramid(height=0.75):
    import trimesh
    v = np.array([[-0.5, -0.5, 0], [0.5, -0.5, 0], [0.5, 0.5, 0], [-0.5, 0.5, 0], [0, 0, height]])
    return _fine(trimesh.convex.convex_hull(v), 70)


def _even(profile, step):
    """An (r, z) polyline with points every `step` along it, its corners kept."""
    out = [np.asarray(profile[0], float)]
    for a, b in zip(profile[:-1], profile[1:]):
        a, b = np.asarray(a, float), np.asarray(b, float)
        k = max(1, int(np.ceil(np.linalg.norm(b - a) / step)))
        out += [a + (b - a) * t for t in np.arange(1, k + 1) / k]
    return np.asarray(out)


def cylinder(m=256):
    return _lathe(_even([(0, -0.5), (0.35, -0.5), (0.35, 0.5), (0, 0.5)], 0.012), m)


def cone(m=256):
    return _lathe(_even([(0, 0), (0.4, 0), (0, 1)], 0.01), m)


def capsule(m=256, n=96):
    a = np.linspace(-math.pi / 2, 0, n)
    lower = [(0.3 * math.cos(t), -0.35 + 0.3 * math.sin(t)) for t in a]
    upper = [(0.3 * math.cos(t), 0.35 + 0.3 * math.sin(t)) for t in -a[::-1]]
    return _lathe(_even(lower + upper, 0.012), m)


def torus(major=1.0, minor=0.38, n=256, m=128):
    def fn(u, v):
        return ((major + minor * np.cos(v)) * np.cos(u), (major + minor * np.cos(v)) * np.sin(u), minor * np.sin(v))
    return _parametric(fn, n, m, (0, 2 * math.pi), (0, 2 * math.pi), True, True)


def rounded_cube(power=8.0, subdivisions=6):
    """A superellipsoid: |x|^p + |y|^p + |z|^p = 1, between a sphere and a cube."""
    m = sphere(subdivisions)
    v = m.vertices
    r = (np.abs(v) ** power).sum(1) ** (1 / power)
    m.vertices = v / r[:, None]
    return _trimesh(m.vertices, m.faces)


def stella_octangula():
    """Kepler's star: two interpenetrating tetrahedra."""
    import trimesh
    a = trimesh.convex.convex_hull(np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]], float))
    b = trimesh.convex.convex_hull(-a.vertices)
    return _stand(_fine(_boolean([a, b], "union"), 90))


def steinmetz(m=256):
    """The tricylinder: what three crossing cylinders share."""
    import trimesh
    c = trimesh.creation.cylinder(radius=1.0, height=3.0, sections=m)
    rx = trimesh.transformations.rotation_matrix(math.pi / 2, (1, 0, 0))
    ry = trimesh.transformations.rotation_matrix(math.pi / 2, (0, 1, 0))
    out = _boolean([c, c.copy().apply_transform(rx), c.copy().apply_transform(ry)], "intersection")
    return _fine(out, 40)


# ----------------------------------------------------------------------------- knots and tubes
def torus_knot(p=2, q=3, n=1200, m=48, radius=0.32):
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    r = 2 + np.cos(q * t)
    P = np.stack([r * np.cos(p * t), r * np.sin(p * t), -np.sin(q * t)], 1)
    return _tube(P, _circle(radius, m))


def figure_eight_knot(n=1400, m=48, radius=0.55):
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    P = np.stack([(2 + np.cos(2 * t)) * np.cos(3 * t), (2 + np.cos(2 * t)) * np.sin(3 * t), np.sin(4 * t)], 1) * 1.6
    return _tube(P, _circle(radius, m))


def mobius_band(n=600, m=96, width=0.7, thickness=0.2):
    """A thick Möbius band: a band that turns half over once round the loop."""
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    P = np.stack([np.cos(t), np.sin(t), np.zeros(n)], 1) * 1.5
    return _tube(P, _rectangle(thickness, width, m), twist=math.pi, shift=m // 2)


def twisted_torus(n=600, m=128, side=0.55, turns=3):
    """A torus of square section that turns `turns` quarter turns round the loop."""
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    P = np.stack([np.cos(t), np.sin(t), np.zeros(n)], 1) * 1.6
    return _tube(P, _rectangle(side, side, m), twist=turns * math.pi / 2, shift=(turns * m // 4) % m)


def seashell(turns=4.2, n=900, m=96):
    """A shell's spiral: a tube that grows as it winds down a cone, closed at both ends."""
    s = np.linspace(0, 2 * math.pi * turns, n)
    g = np.exp(0.14 * s)
    P = np.stack([g * np.cos(s), g * np.sin(s), -1.25 * g], 1)
    _, R, S = _frames_open(P)
    a = np.linspace(0, 2 * math.pi, m, endpoint=False)
    r = 0.62 * g
    V = P[:, None, :] + (r[:, None, None] * (np.cos(a)[None, :, None] * R[:, None, :] +
                                             np.sin(a)[None, :, None] * S[:, None, :]))
    V = V.reshape(-1, 3)
    F = [_grid_faces(n, m, False, True)]
    j = np.arange(m)
    c0, c1 = len(V), len(V) + 1
    V = np.vstack([V, P[0], P[-1]])
    F.append(np.stack([np.full(m, c0), j, (j + 1) % m], 1))
    last = (n - 1) * m
    F.append(np.stack([np.full(m, c1), last + (j + 1) % m, last + j], 1))
    return _trimesh(V, np.vstack(F))


def _frames_open(P):
    T = np.gradient(P, axis=0)
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    R = np.empty_like(P)
    a = np.array([0.0, 0.0, 1.0]) if abs(T[0, 2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    R[0] = np.cross(T[0], a)
    R[0] /= np.linalg.norm(R[0])
    for i in range(len(P) - 1):
        r = R[i] - (R[i] @ T[i + 1]) * T[i + 1]
        R[i + 1] = r / np.linalg.norm(r)
    return T, R, np.cross(T, R)


# ----------------------------------------------------------------------------- one-sided and immersed surfaces
def klein_bottle(n=420, m=160):
    """The Klein bottle as a bottle (one surface, no inside; it passes through itself where the neck enters)."""
    def fn(u, v):
        cu, su, cv, sv = np.cos(u), np.sin(u), np.cos(v), np.sin(v)
        x = -2 / 15 * cu * (3 * cv - 30 * su + 90 * cu ** 4 * su - 60 * cu ** 6 * su + 5 * cu * cv * su)
        y = -1 / 15 * su * (3 * cv - 3 * cu ** 2 * cv - 48 * cu ** 4 * cv + 48 * cu ** 6 * cv - 60 * su
                            + 5 * cu * cv * su - 5 * cu ** 3 * cv * su - 80 * cu ** 5 * cv * su + 80 * cu ** 7 * cv * su)
        z = 2 / 15 * (3 + 5 * cu * su) * sv
        return x, z, -y                                       # stand it up: the bottle's axis along Z
    return _parametric(fn, n, m, (0, math.pi), (0, 2 * math.pi), False, True, fix=False)


def boys_surface(rings=140, around=560):
    """Boy's surface (Bryant–Kusner): the projective plane in space; one surface that passes through itself."""
    r = np.linspace(0, 1, rings + 1)[1:]
    a = np.linspace(0, 2 * math.pi, around, endpoint=False)
    R, A = np.meshgrid(r, a, indexing="ij")
    w = np.concatenate([[0j], (R * np.exp(1j * A)).ravel()])
    d = w ** 6 + 5 ** 0.5 * w ** 3 - 1
    g1 = -1.5 * np.imag(w * (1 - w ** 4) / d)
    g2 = -1.5 * np.real(w * (1 + w ** 4) / d)
    g3 = np.imag((1 + w ** 6) / d) - 0.5
    g = g1 ** 2 + g2 ** 2 + g3 ** 2
    V = np.stack([g1 / g, g2 / g, g3 / g], 1)
    F = [_grid_faces(rings, around, False, True) + 1]
    j = np.arange(around)
    F.append(np.stack([np.zeros(around, int), 1 + j, 1 + (j + 1) % around], 1))
    import trimesh
    m = trimesh.Trimesh(V, np.vstack(F), process=False)
    m.merge_vertices(digits_vertex=5)                        # the rim's opposite points are one point
    m.update_faces(m.nondegenerate_faces())
    m.remove_unreferenced_vertices()
    return m


# ----------------------------------------------------------------------------- implicit surfaces and fractals
def gyroid(cells=1.5, thickness=0.35, res=150):
    """A gyroid sheet (a triply periodic minimal surface, thickened) cut to a cube."""
    b = math.pi * cells
    return _implicit(lambda x, y, z: np.abs(np.sin(x) * np.cos(y) + np.sin(y) * np.cos(z) + np.sin(z) * np.cos(x))
                     - thickness, b, res)


def schwarz_p(cells=1.5, thickness=0.3, res=160):
    """Schwarz's P surface, thickened, cut to a cube."""
    b = math.pi * cells
    return _implicit(lambda x, y, z: np.abs(np.cos(x) + np.cos(y) + np.cos(z)) - thickness, b, res)


def menger_sponge(level=3):
    """Menger's sponge: a cube with the middle of every face punched through, again and again."""
    n = 3 ** level
    idx = np.indices((n, n, n))
    full = np.ones((n, n, n), bool)
    for k in range(level):
        d = (idx // 3 ** k) % 3 == 1
        full &= (d.sum(0) < 2)
    return _voxels(full)


def _voxels(full):
    """The outside faces of a voxel grid (unit cubes) as triangles."""
    n = full.shape[0]
    pad = np.pad(full, 1)
    V, F = [], []
    quads = {0: [(0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1)], 1: [(0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0)],
             2: [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]}
    for axis in range(3):
        for side in (0, 1):
            shift = [0, 0, 0]
            shift[axis] = 1 if side else -1
            nb = np.roll(pad, -1 if side else 1, axis)[1:-1, 1:-1, 1:-1]
            cells = np.argwhere(full & ~nb)
            if not len(cells):
                continue
            base = cells.astype(float)
            if side:
                base[:, axis] += 1
            q = np.array(quads[axis], float)
            corners = base[:, None, :] + q[None]
            k = len(V) and sum(len(x) for x in V)
            V.append(corners.reshape(-1, 3))
            i = k + np.arange(len(cells))[:, None] * 4
            tri = np.concatenate([np.hstack([i, i + 1, i + 2]), np.hstack([i, i + 2, i + 3])])
            F.append(tri)
    return _trimesh(np.vstack(V) - n / 2, np.vstack(F))


def bumpy_sphere(subdivisions=7, amount=0.18):
    """A sphere rippled by spherical harmonics: an organic blob with even detail everywhere."""
    m = sphere(subdivisions)
    v = m.vertices
    theta = np.arccos(np.clip(v[:, 2], -1, 1))
    phi = np.arctan2(v[:, 1], v[:, 0])
    r = 1 + amount * (np.sin(theta) ** 3 * np.cos(3 * phi) * np.cos(2 * theta)
                      + 0.6 * np.sin(theta) ** 5 * np.sin(5 * phi) + 0.4 * np.cos(4 * theta))
    m.vertices = v * r[:, None]
    return _trimesh(m.vertices, m.faces)


# ----------------------------------------------------------------------------- turned objects
def vase(m=256):
    z = np.linspace(0, 1, 160)
    r = 0.16 + 0.13 * np.sin(math.pi * (z * 1.15 + 0.1)) ** 2 + 0.05 * np.cos(math.pi * z * 3.2) - 0.07 * z
    prof = [(0, 0)] + list(zip(r, z)) + [(0, 1)]
    return _lathe(prof, m)


def goblet(m=256):
    pts = [(0, 0), (0.38, 0), (0.38, 0.025), (0.12, 0.06), (0.05, 0.12), (0.045, 0.4), (0.08, 0.46), (0.06, 0.5),
           (0.2, 0.58), (0.34, 0.75), (0.37, 0.98), (0, 0.98)]
    return _lathe(_smooth_profile(pts, sharp=(1, 2, 10, 11)), m)


def chess_pawn(m=256):
    pts = [(0, 0), (0.42, 0), (0.42, 0.06), (0.36, 0.11), (0.38, 0.15), (0.25, 0.2), (0.16, 0.34), (0.12, 0.52),
           (0.24, 0.56), (0.24, 0.6), (0.13, 0.62)]
    a = np.linspace(-math.pi / 2 + 0.55, math.pi / 2, 60)
    ball = [(0.2 * math.cos(t), 0.79 + 0.2 * math.sin(t)) for t in a]
    prof = list(_smooth_profile(pts, sharp=(1, 2)))[:-1] + ball[:-1] + [(0, 0.99)]
    return _lathe(prof, m)


def chess_rook(m=256):
    pts = [(0, 0), (0.42, 0), (0.42, 0.07), (0.34, 0.13), (0.36, 0.17), (0.26, 0.24), (0.22, 0.7), (0.32, 0.74),
           (0.32, 1.0), (0, 1.0)]
    body = _lathe(_smooth_profile(pts, sharp=(1, 2, 7, 8, 9)), m)
    cuts = [_box((0.9, 0.09, 0.16), (0, 0, 0.98)),
            _box((0.09, 0.9, 0.16), (0, 0, 0.98))]
    import trimesh
    rot = trimesh.transformations.rotation_matrix(math.pi / 4, (0, 0, 1))
    cuts += [c.copy().apply_transform(rot) for c in cuts]
    hollow = _lathe([(0, 0.85), (0.22, 0.85), (0.22, 1.1), (0, 1.1)], m)
    out = _boolean([body, _boolean(cuts + [hollow], "union")], "difference")
    return _fine(out, 55)


# ----------------------------------------------------------------------------- registry
GENERATORS = {
    "sphere": sphere, "cube": cube, "tetrahedron": tetrahedron, "octahedron": octahedron,
    "dodecahedron": dodecahedron, "icosahedron": icosahedron, "pyramid": pyramid, "cylinder": cylinder,
    "cone": cone, "capsule": capsule, "torus": torus, "rounded_cube": rounded_cube,
    "stella_octangula": stella_octangula, "steinmetz": steinmetz,
    "torus_knot": torus_knot, "figure_eight_knot": figure_eight_knot, "mobius_band": mobius_band,
    "twisted_torus": twisted_torus, "seashell": seashell, "klein_bottle": klein_bottle,
    "boys_surface": boys_surface, "gyroid": gyroid, "schwarz_p": schwarz_p,
    "menger_sponge": menger_sponge, "bumpy_sphere": bumpy_sphere,
    "vase": vase, "goblet": goblet, "chess_pawn": chess_pawn, "chess_rook": chess_rook,
}


def normalise(mesh, size: float = SIZE):
    """Centred on its bounding box, the largest side `size`."""
    lo, hi = mesh.bounds
    mesh.apply_translation(-(lo + hi) / 2)
    mesh.apply_scale(size / float((hi - lo).max()))
    return mesh


def build(name: str, **params):
    if name not in GENERATORS:
        raise KeyError(f"no shape generator named {name!r}")
    return normalise(GENERATORS[name](**params))
