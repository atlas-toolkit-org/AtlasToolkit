"""
mesh_compress.py - compress a model: squash it between press plates, crush it from around an axis or from every side,
press it into a mould (cube, sphere, cylinder, cone, pyramid, prism, octahedron, icosahedron, dodecahedron) or morph
it onto one, in a single step.

Physical compression is a quasi-static projective dynamics solve (Bouaziz et al. 2014: local spring projections,
one prefactored sparse global solve per iteration, with position-based hard constraints on top) of the surface of a
near-uniform simulation copy of the model, in the canonical frame of the distortion tools
(geom/frame3d: up = Z, front = -Y, centred on the bounding box, 2 units tall):

    skin       edge springs; their rest lengths creep toward the current lengths over the load (viscoplastic flow,
               1 - plasticity of the elastic strain is remembered at the end), so an elastic skin buckles into folds
               and a plastic one spreads smoothly
    bending    "bending springs" between the opposite corners of every pair of triangles: stiff ones give a few broad
               folds, soft ones many fine creases
    volume     one global constraint toward V0 (1 - c (1 - q)), where q is the volume ratio the press itself imposes
               (gap ratio for plates, s^2 or s^3 for radial crushes, mould volume over V0): c = 0 is incompressible
               clay that bulges sideways, c = 1 a crushed can that keeps only what the press leaves
    contact    rigid colliders (plates, a shrinking tube, sphere or mould) as hard projections with Coulomb-like
               friction (tangential motion during the step scaled by 1 - friction): friction barrels a squashed form
    self       vertex-vertex repulsion between parts of the surface that started apart (a cKDTree over the current
               positions), so folds stack instead of passing through each other; approximate, not watertight

The colliders close in over `steps` load steps; each step runs `iterations` constraint passes. Long-wave seeded
imperfections (smoothed normal noise) break the symmetry, as in real buckling. The morph sub-mode is analytic: each
point moves along its ray from the shape's centre onto the shape's surface, keeping its relief.

Shapes are convex gauges: g(u) <= 1 inside, g homogeneous of degree 1, so u / g(u) is the boundary point on the ray
through u, and the smallest scale that encloses the model is max g. Aspect ratios scale the unit shape per axis.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

PHI = (1.0 + math.sqrt(5.0)) / 2.0
SHAPES = ("cube", "sphere", "cylinder", "cone", "pyramid", "prism", "octahedron", "icosahedron", "dodecahedron")
AXES = {"up": (0.0, 0.0, 1.0), "front": (0.0, -1.0, 0.0), "side": (1.0, 0.0, 0.0)}
MOMENTUM = 0.0                  # share of the last step's motion carried into the next (quasi-static: none)
BEND_CREEP = 0.5                # bending springs creep at this share of the skin's rate, so the surface keeps
                                # some memory of its curvature and plastic flow cannot run away into wrinkles


# ----------------------------------------------------------------------------- shapes

def _unit(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def _align(a, b) -> np.ndarray:
    """Rotation matrix turning unit vector a onto unit vector b."""
    a, b = _unit(a), _unit(b)
    v, c = np.cross(a, b), float(a @ b)
    if c < -1 + 1e-12:
        p = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        p = _unit(p - (p @ a) * a)
        return 2 * np.outer(p, p) - np.eye(3)
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K / (1 + c)


def _rot_z(deg: float) -> np.ndarray:
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _polytope_planes(name: str, sides: int = 3) -> np.ndarray:
    """Unit outward normals (n, 3) with offsets 1 (inradius 1 about the origin) or explicit (n, 4) [normal, offset]."""
    if name == "cube":
        N = np.vstack([np.eye(3), -np.eye(3)])
    elif name == "octahedron":
        N = _unit(np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], dtype=float))
    elif name in ("icosahedron", "dodecahedron"):
        def cyclic(points):
            return [r for q in points for r in (q, (q[1], q[2], q[0]), (q[2], q[0], q[1]))]
        ico = cyclic([(0, a, b * PHI) for a in (-1, 1) for b in (-1, 1)])
        dod = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
        dod += cyclic([(0, a / PHI, b * PHI) for a in (-1, 1) for b in (-1, 1)])
        # a dodecahedron's face normals point at an icosahedron's vertices, and the other way round
        N = _unit(np.array(ico if name == "dodecahedron" else dod, dtype=float))
        N = N @ _align(N[np.argmin(N[:, 2])], (0, 0, -1)).T          # stand on a face
    elif name == "pyramid":                                           # square base at z = -1, apex at z = 1
        side = _unit(np.array([[2, 0, 1], [-2, 0, 1], [0, 2, 1], [0, -2, 1]], dtype=float))
        off = side @ np.array([0.0, 0.0, 1.0])                        # planes through the apex
        return np.vstack([np.column_stack([side, off]), [[0, 0, -1, 1]]])
    elif name == "prism":                                             # regular n-gon, inradius 1, |z| <= 1
        a = (np.arange(sides) + 0.5) * 2 * math.pi / sides - math.pi / 2
        N = np.vstack([np.column_stack([np.cos(a), np.sin(a), np.zeros(sides)]), [[0, 0, 1], [0, 0, -1]]])
    else:
        raise ValueError(f"unknown polytope {name!r}")
    return np.column_stack([N, np.ones(len(N))])


@dataclass
class Shape:
    """A convex unit shape about the origin, inside where gauge(u) <= 1."""
    name: str
    planes: np.ndarray | None = None          # polytopes: (n, 4) normal, offset

    def gauge(self, U: np.ndarray) -> np.ndarray:
        U = np.asarray(U, dtype=np.float64)
        r = np.hypot(U[:, 0], U[:, 1])
        if self.planes is not None:
            return (U @ self.planes[:, :3].T / self.planes[:, 3]).max(1)
        if self.name == "sphere":
            return np.linalg.norm(U, axis=1)
        if self.name == "cylinder":
            return np.maximum(r, np.abs(U[:, 2]))
        if self.name == "tube":                                  # endless along z: the radial crush
            return r
        if self.name == "cone":                                  # base r <= 1 at z = -1, apex at z = 1
            return np.maximum(2 * r + U[:, 2], -U[:, 2])
        raise ValueError(f"unknown shape {self.name!r}")

    def project(self, U: np.ndarray, sweeps: int = 2) -> np.ndarray:
        """Points outside moved onto the shape (exact for round shapes, cyclic plane projection for polytopes,
        which converges over the solver's iterations)."""
        U = np.array(U, dtype=np.float64)
        if self.planes is not None:
            N, o = self.planes[:, :3], self.planes[:, 3]
            for _ in range(sweeps):
                for n, d in zip(N, o):
                    s = U @ n - d
                    out = s > 0
                    if out.any():
                        U[out] -= s[out, None] * n
            return U
        r = np.hypot(U[:, 0], U[:, 1])
        if self.name == "sphere":
            n = np.linalg.norm(U, axis=1)
            return U / np.maximum(n, 1.0)[:, None]
        scale = np.where(r > 1.0, 1.0 / np.maximum(r, 1e-12), 1.0)
        if self.name in ("cylinder", "tube"):
            U[:, :2] *= scale[:, None]
            if self.name == "cylinder":
                U[:, 2] = np.clip(U[:, 2], -1.0, 1.0)
            return U
        if self.name == "cone":
            # exact: the closest point of the profile triangle (r, z): apex (0, 1), rim (1, -1), axis foot (0, -1)
            r = np.hypot(U[:, 0], U[:, 1])
            z = U[:, 2]
            out = (2 * r + z > 1.0) | (z < -1.0)
            if out.any():
                ro, zo = r[out], z[out]
                t = np.clip(((ro - 0.0) * 1.0 + (zo - 1.0) * -2.0) / 5.0, 0.0, 1.0)     # along apex -> rim
                sr, sz = t, 1.0 - 2.0 * t
                br, bz = np.clip(ro, 0.0, 1.0), np.full_like(zo, -1.0)                # on the base disc
                side = (sr - ro) ** 2 + (sz - zo) ** 2 <= (br - ro) ** 2 + (bz - zo) ** 2
                nr, nz = np.where(side, sr, br), np.where(side, sz, bz)
                k = np.where(ro > 1e-12, nr / np.maximum(ro, 1e-12), 0.0)
                U[out, 0] *= k
                U[out, 1] *= k
                U[out, 2] = nz
            return U
        raise ValueError(f"unknown shape {self.name!r}")

    def volume(self) -> float:
        round_ = {"sphere": 4 * math.pi / 3, "cylinder": 2 * math.pi, "cone": 2 * math.pi / 3, "tube": math.inf}
        if self.planes is None:
            return round_[self.name]
        from scipy.spatial import ConvexHull, HalfspaceIntersection
        H = np.column_stack([self.planes[:, :3], -self.planes[:, 3]])        # n.x - o <= 0
        return float(ConvexHull(HalfspaceIntersection(H, np.zeros(3)).intersections).volume)

    def bottom(self) -> float:
        """Lowest z of the unit shape (where it sits on a floor)."""
        if self.name == "tube":
            return -math.inf
        if self.planes is not None:
            down = self.planes[:, 2] < -0.999
            if down.any():
                return float(-(self.planes[down, 3] / -self.planes[down, 2]).min())
            # resting on a vertex: probe straight down
            return float(-1.0 / self.gauge(np.array([[0.0, 0.0, -1.0]]))[0])
        return -1.0


def make_shape(name: str, sides: int = 3) -> Shape:
    if name in ("cube", "octahedron", "icosahedron", "dodecahedron", "pyramid", "prism"):
        return Shape(name, _polytope_planes(name, int(sides)))
    if name in ("sphere", "cylinder", "cone", "tube"):
        return Shape(name)
    raise ValueError(f"unknown shape {name!r}; one of {', '.join(SHAPES)}")


@dataclass
class Mould:
    """A shape placed in the canonical frame: x = c + R (s A u)."""
    shape: Shape
    R: np.ndarray                 # shape axes -> canonical
    A: np.ndarray                 # aspect per shape axis, product 1
    c: np.ndarray                 # centre

    def to_unit(self, X: np.ndarray, s: float) -> np.ndarray:
        return ((X - self.c) @ self.R) / (s * self.A)

    def from_unit(self, U: np.ndarray, s: float) -> np.ndarray:
        return (U * (s * self.A)) @ self.R.T + self.c

    def enclosing_scale(self, X: np.ndarray) -> float:
        return float(self.shape.gauge(self.to_unit(X, 1.0)).max())

    def volume(self, s: float) -> float:
        return self.shape.volume() * s ** 3 if self.shape.name != "tube" else math.inf


def mould(shape: str, axis: str = "up", aspect=(1.0, 1.0, 1.0), turn: float = 0.0, sides: int = 3,
          centre=(0.0, 0.0, 0.0)) -> Mould:
    """A shape whose own Z points along `axis`, turned `turn` degrees about it, stretched by `aspect` (width,
    depth, height; normalised to keep its volume)."""
    A = np.maximum(np.asarray(aspect, dtype=np.float64), 1e-3)
    A = A / np.prod(A) ** (1.0 / 3.0)
    R = _align((0, 0, 1), AXES[axis]) @ _rot_z(turn)
    return Mould(make_shape(shape, sides), R, A, np.asarray(centre, dtype=np.float64))


# ----------------------------------------------------------------------------- mesh helpers

def edges_of(F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Unique edges (E, 2) and the bending pairs: opposite corners of the two triangles at each interior edge."""
    F = np.asarray(F, dtype=np.int64)
    half = np.column_stack([F[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), F[:, [2, 0, 1]].reshape(-1)])
    key = np.sort(half[:, :2], axis=1)
    order = np.lexsort((key[:, 1], key[:, 0]))
    key, opp = key[order], half[order, 2]
    first = np.ones(len(key), bool)
    first[1:] = np.any(key[1:] != key[:-1], axis=1)
    E = key[first]
    idx = np.flatnonzero(~first)                                   # later half-edges of a shared edge
    idx = idx[first[idx - 1]]                                      # only the second (skip non-manifold extras)
    B = np.column_stack([opp[idx - 1], opp[idx]]) if len(idx) else np.zeros((0, 2), np.int64)
    return E, B[B[:, 0] != B[:, 1]]


def neighbours_mean(X: np.ndarray, E: np.ndarray, n: int) -> np.ndarray:
    S = np.zeros((n, X.shape[1]) if X.ndim > 1 else n)
    np.add.at(S, E[:, 0], X[E[:, 1]])
    np.add.at(S, E[:, 1], X[E[:, 0]])
    cnt = np.bincount(E.ravel(), minlength=n).astype(np.float64)
    cnt = np.maximum(cnt, 1.0)
    return S / (cnt[:, None] if X.ndim > 1 else cnt)


def smooth_scalar(f: np.ndarray, E: np.ndarray, passes: int) -> np.ndarray:
    for _ in range(passes):
        f = 0.5 * f + 0.5 * neighbours_mean(f, E, len(f))
    return f


def smooth_vectors(X: np.ndarray, E: np.ndarray, passes: int) -> np.ndarray:
    for _ in range(passes):
        X = 0.5 * X + 0.5 * neighbours_mean(X, E, len(X))
    return X


def taubin(X: np.ndarray, E: np.ndarray, passes: int, lam: float = 0.5, mu: float = -0.53) -> np.ndarray:
    for _ in range(passes):
        X = X + lam * (neighbours_mean(X, E, len(X)) - X)
        X = X + mu * (neighbours_mean(X, E, len(X)) - X)
    return X


def vertex_normals(X: np.ndarray, F: np.ndarray) -> np.ndarray:
    fn = np.cross(X[F[:, 1]] - X[F[:, 0]], X[F[:, 2]] - X[F[:, 0]])
    N = np.zeros_like(X)
    for k in range(3):
        np.add.at(N, F[:, k], fn)
    return N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-18)


def volume(X: np.ndarray, F: np.ndarray, c=None) -> float:
    c = X.mean(0) if c is None else c
    a, b, d = X[F[:, 0]] - c, X[F[:, 1]] - c, X[F[:, 2]] - c
    return float(np.einsum("ij,ij->i", a, np.cross(b, d)).sum() / 6.0)


def volume_gradient(X: np.ndarray, F: np.ndarray) -> np.ndarray:
    G = np.zeros_like(X)
    a, b, c = X[F[:, 0]], X[F[:, 1]], X[F[:, 2]]
    np.add.at(G, F[:, 0], np.cross(b, c))
    np.add.at(G, F[:, 1], np.cross(c, a))
    np.add.at(G, F[:, 2], np.cross(a, b))
    return G / 6.0


def _area(V: np.ndarray, F: np.ndarray) -> float:
    return float(0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1).sum())


def remesh(V: np.ndarray, F: np.ndarray, faces: int, log=None) -> tuple[np.ndarray, np.ndarray, float]:
    """Near-uniform triangles at about `faces`, the simulation mesh: shortest-edge collapse to 60% of the count
    (libigl, which keeps a closed manifold closed; quadric decimation for other models), then conforming splits of
    the edges longer than an equilateral triangle's at the full count. Returns (V, F, that edge length)."""
    from atlas_toolkit.geom import mesh as gm
    from atlas_toolkit.tools.mesh_flow.mesh_flow import refine
    L = math.sqrt(4.0 * _area(V, F) / (math.sqrt(3.0) * max(int(faces), 1)))
    keep = int(faces * 0.6)
    if len(F) > keep:
        done = False
        if is_closed(F):
            try:
                import igl
                U, G, _, _ = igl.decimate(np.asarray(V, np.float64), np.asarray(F, np.int64), keep)
                if len(G) and is_closed(G):
                    V, F, done = np.asarray(U, np.float64), np.asarray(G, np.int64), True
            except Exception as e:                       # pragma: no cover (libigl is bundled)
                if log:
                    log(f"libigl decimation failed ({type(e).__name__}); using quadric decimation")
        if not done:
            V, F = gm.simplify_faces(V, F, keep, log)
    V, F, _ = refine(V, F, 1.3 * L, int(faces * 1.5), passes=8)
    return V, F, L


def is_closed(F: np.ndarray) -> bool:
    """Every edge shared by exactly two faces."""
    F = np.asarray(F, dtype=np.int64)
    if not len(F):
        return False
    key = np.sort(F[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    _, counts = np.unique(key, axis=0, return_counts=True)
    return bool((counts == 2).all())


def detail_mesh(V: np.ndarray, F: np.ndarray, edge: float, faces: int, log=None) -> tuple[np.ndarray, np.ndarray]:
    """The model as it is output: simplified to at most `faces`, its long edges split under 0.75 x the simulation
    edge so large flat triangles can follow the folds (within the same budget)."""
    from atlas_toolkit.geom import mesh as gm
    from atlas_toolkit.tools.mesh_flow.mesh_flow import refine
    V, F = gm.simplify_faces(V, F, int(faces), log)
    V, F, _ = refine(V, F, 0.75 * edge, int(faces), passes=6)
    return V, F


def rotations(C0: np.ndarray, C1: np.ndarray, E: np.ndarray) -> np.ndarray:
    """Best rotation of each vertex's one-ring from rest C0 to deformed C1 (Kabsch, Sorkine & Alexa 2007)."""
    n = len(C0)
    d0 = np.vstack([C0[E[:, 1]] - C0[E[:, 0]], C0[E[:, 0]] - C0[E[:, 1]]])
    d1 = np.vstack([C1[E[:, 1]] - C1[E[:, 0]], C1[E[:, 0]] - C1[E[:, 1]]])
    owner = np.concatenate([E[:, 0], E[:, 1]])
    S = np.zeros((n, 3, 3))
    np.add.at(S, owner, d1[:, :, None] * d0[:, None, :])
    U, _, Vt = np.linalg.svd(S + np.eye(3) * 1e-12)
    R = U @ Vt
    flip = np.linalg.det(R) < 0
    if flip.any():
        U[flip, :, 2] *= -1
        R[flip] = U[flip] @ Vt[flip]
    return R


def transfer(P: np.ndarray, NP: np.ndarray, C0: np.ndarray, C1: np.ndarray, N0: np.ndarray, E: np.ndarray,
             k: int = 8) -> np.ndarray:
    """Carry detail points P (normals NP) along the simulation mesh's deformation C0 -> C1: blended rigid motions
    of the k nearest simulation vertices (linear blend skinning), weighted by inverse squared distance and by how
    well their normals agree, so the two sides of a thin part (an ear, a lip) do not pull on each other."""
    from scipy.spatial import cKDTree
    R = rotations(C0, C1, E)
    k = min(k, len(C0))
    d, J = cKDTree(C0).query(P, k=k)
    if k == 1:
        d, J = d[:, None], J[:, None]
    agree = np.clip(np.einsum("ij,ikj->ik", NP, N0[J]), 0.0, 1.0) ** 2 + 1e-3
    w = agree / (d + 1e-9) ** 2
    w /= w.sum(1, keepdims=True)
    out = np.zeros_like(P)
    for a in range(k):
        j = J[:, a]
        out += w[:, a, None] * (C1[j] + np.einsum("nij,nj->ni", R[j], P - C0[j]))
    return out


# ----------------------------------------------------------------------------- the press

@dataclass
class Material:
    compressibility: float = 0.15     # 0 clay (volume kept) .. 1 crushed can (volume follows the press)
    plasticity: float = 0.6           # 0 elastic skin (buckles) .. 1 flowing skin (spreads)
    stiffness: float = 0.5            # bending: 0 fine creases .. 1 broad folds
    friction: float = 0.5             # 0 slides along the press .. 1 sticks
    imperfection: float = 0.3         # seeded long-wave dents that pick where folds form
    seed: int = 0


@dataclass
class Press:
    """Rigid colliders as a function of the load u in 0 .. 1."""
    kind: str                         # plates | floor | radial | iso | mould
    n: np.ndarray | None = None       # plate normal (bottom plate: -n); radial axis
    n_top: np.ndarray | None = None   # top plate normal (tilted)
    h_top0: float = 0.0
    h_top1: float = 0.0
    h_bot0: float = 0.0
    h_bot1: float = 0.0
    mould: Mould | None = None
    s0: float = 1.0
    s1: float = 1.0
    seat: float | None = None         # floor height the mould sits on (None: shrinks about its centre)
    v_ratio: callable = None          # q(u): volume ratio the press imposes

    def mould_at(self, u: float) -> tuple[Mould, float]:
        s = self.s0 + (self.s1 - self.s0) * u
        m = self.mould
        if self.seat is not None:
            c = m.c.copy()
            c[2] = self.seat - s * m.A[2] * m.shape.bottom()
            m = Mould(m.shape, m.R, m.A, c)
        return m, s

    def contact(self, X: np.ndarray, u: float, snap: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
        """X projected onto the press at load u, and which points it moved. Points within `snap` of a plate or
        the mould are put on it too (flat contact faces with no islands for a slicer to find)."""
        Y = X.copy()
        if self.kind in ("plates", "floor"):
            ht = self.h_top0 + (self.h_top1 - self.h_top0) * u
            hb = self.h_bot0 + (self.h_bot1 - self.h_bot0) * u
            s = Y @ self.n_top - ht
            out = s > -snap
            Y[out] -= s[out, None] * self.n_top
            s = hb - Y @ self.n
            low = s > -snap
            Y[low] += s[low, None] * self.n
            return Y, out | low
        m, s = self.mould_at(u)
        U = m.to_unit(Y, s)
        out = m.shape.gauge(U) > 1.0 - snap / max(s * float(m.A.min()), 1e-12)
        if out.any():
            Uo = U[out]
            if snap > 0:                                    # near points onto the boundary, outside ones in
                g = m.shape.gauge(Uo)
                Uo = np.where((g < 1.0)[:, None], Uo / np.maximum(g, 1e-12)[:, None], Uo)
            Y[out] = m.from_unit(m.shape.project(Uo), s)
        return Y, out


def plates(X: np.ndarray, axis: str, strain: float, tilt: float = 0.0, floor: bool = False) -> Press:
    n = np.asarray(AXES[axis], dtype=np.float64)
    if axis == "up":
        hinge = np.array([1.0, 0.0, 0.0])
    else:
        hinge = np.array([0.0, 0.0, 1.0])
    t = math.radians(tilt)
    K = np.array([[0, -hinge[2], hinge[1]], [hinge[2], 0, -hinge[0]], [-hinge[1], hinge[0], 0]])
    n_top = (np.eye(3) + math.sin(t) * K + (1 - math.cos(t)) * K @ K) @ n
    proj, proj_t = X @ n, X @ n_top
    gap = float(proj.max() - proj.min())
    travel = strain * gap
    top0, bot0 = float(proj_t.max()), float(proj.min())
    if floor:
        top1, bot1 = top0 - travel, bot0
    else:
        top1, bot1 = top0 - travel / 2, bot0 + travel / 2
    return Press("floor" if floor else "plates", n=n, n_top=n_top, h_top0=top0, h_top1=top1, h_bot0=bot0,
                 h_bot1=bot1, v_ratio=lambda u: 1.0 - strain * u)


def radial(X: np.ndarray, axis: str | None, strain: float) -> Press:
    """Crush about an axis through the centre (axis given: a tightening tube) or from every side (None)."""
    c = (X.min(0) + X.max(0)) / 2.0
    m = mould("tube" if axis else "sphere", axis or "up", centre=c)
    s0 = m.enclosing_scale(X)
    power = 2 if axis else 3
    return Press("radial" if axis else "iso", n=np.asarray(AXES[axis or "up"], float), mould=m, s0=s0,
                 s1=s0 * (1.0 - strain), v_ratio=lambda u: (1.0 - strain * u) ** power)


def into(X: np.ndarray, m: Mould, size: float, fill: float, v0: float, seat: float | None = None) -> Press:
    """Close the mould in from the scale that encloses the model to the one whose volume is `size` x V0."""
    s_fit = (size * v0 / m.shape.volume()) ** (1.0 / 3.0)
    s0 = m.enclosing_scale(X) if seat is None else _seated_enclosing(X, m, seat)
    s1 = s0 + (min(s_fit, s0) - s0) * fill
    p = Press("mould", mould=m, s0=s0, s1=s1, seat=seat)
    p.v_ratio = lambda u: min(1.0, m.volume(p.s0 + (p.s1 - p.s0) * u) / v0)
    return p


def _seated_enclosing(X: np.ndarray, m: Mould, seat: float) -> float:
    """Smallest scale at which the mould, standing on the floor, holds the model. A convex shape scaled about a
    point of its own nests, so the test is monotonic and bisection finds it."""
    def holds(s):
        mm, _ = Press("mould", mould=m, s0=s, s1=s, seat=seat).mould_at(0.0)
        return mm.enclosing_scale(X) <= s
    lo, hi = 1e-3, 1.0
    while not holds(hi) and hi < 1e6:
        lo, hi = hi, hi * 2.0
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if holds(mid) else (mid, hi)
    return hi * 1.0005


@dataclass
class Outcome:
    X: np.ndarray
    volume0: float
    volume1: float
    contact: float                   # share of the vertices touching the press at the end
    collisions: int                  # self-collision pairs resolved in the last step
    strain_max: float                # largest edge shortening (1 - l / l0)


def _incidence(P: np.ndarray, n: int):
    """Sparse difference operator: row e gives x[P[e, 0]] - x[P[e, 1]]."""
    from scipy import sparse
    m = len(P)
    rows = np.repeat(np.arange(m), 2)
    return sparse.csr_matrix((np.tile([1.0, -1.0], m), (rows, P.ravel())), shape=(m, n))


def solve(V: np.ndarray, F: np.ndarray, press: Press, mat: Material, steps: int = 48, iterations: int = 16,
          collide: bool = True, edge: float | None = None, keep_volume: bool = True, progress=None,
          trace=None) -> Outcome:
    """Quasi-static projective dynamics (Bouaziz et al. 2014): close the press over `steps` load steps; each of
    the `iterations` per step projects every spring onto its rest length (local), solves one prefactored sparse
    system for all positions at once (global), then enforces volume, self-contact and the press as hard
    projections."""
    from scipy import sparse
    from scipy.sparse.linalg import splu
    from scipy.spatial import cKDTree
    X = np.array(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    n = len(X)
    E, B = edges_of(F)
    if edge is None:
        edge = float(np.linalg.norm(X[E[:, 0]] - X[E[:, 1]], axis=1).mean()) if len(E) else 0.01
    # seeded long-wave imperfections along the normals
    if mat.imperfection > 0:
        rng = np.random.default_rng(int(mat.seed))
        f = smooth_scalar(rng.normal(size=n), E, 30)
        f -= f.mean()
        f /= max(float(np.abs(f).max()), 1e-12)
        X = X + vertex_normals(X, F) * (f * mat.imperfection * 0.8 * edge)[:, None]
    rest = X.copy()
    L0 = np.linalg.norm(X[E[:, 0]] - X[E[:, 1]], axis=1)
    Lfix = L0.copy()
    B0 = np.linalg.norm(X[B[:, 0]] - X[B[:, 1]], axis=1) if len(B) else np.zeros(0)
    v0 = volume(X, F)
    closed = keep_volume and abs(v0) > 1e-12          # an open surface has no volume to keep
    w_s = 1.0
    w_b = 0.02 + 0.6 * float(np.clip(mat.stiffness, 0, 1)) ** 1.5
    w_m = 0.02                                        # inertia: ties each iterate to the predicted position
    memory = 1.0 - min(float(np.clip(mat.plasticity, 0, 1)), 0.999)
    creep = 1.0 - memory ** (1.0 / max(steps, 1))     # per step, so the elastic memory left at the end is `memory`
    mu = float(np.clip(mat.friction, 0, 1))
    c = float(np.clip(mat.compressibility, 0, 1))
    radius = 0.9 * edge
    GE, GB = _incidence(E, n), _incidence(B, n) if len(B) else None
    A = w_s * (GE.T @ GE) + w_m * sparse.identity(n)
    if GB is not None:
        A = A + w_b * (GB.T @ GB)
    lu = splu(sparse.csc_matrix(A))
    collisions, touched = 0, np.zeros(n, bool)
    pressure = 0.0
    prev = X.copy()

    def targets(X, P, Lr):
        d = X[P[:, 0]] - X[P[:, 1]]
        L = np.linalg.norm(d, axis=1)
        return d * (Lr / np.maximum(L, 1e-12))[:, None]

    for step in range(1, steps + 1):
        u = step / steps
        start = X.copy()
        Y0 = X + MOMENTUM * (X - prev)
        prev = start
        X = Y0
        target = v0 * (1.0 - c * (1.0 - float(press.v_ratio(u))))
        if closed:
            # how far one unit of pressure moves the volume through the global solve (for the controller below)
            Gs = smooth_vectors(volume_gradient(X, F), E, 4)
            Gs[touched] = 0.0
            gain = float((volume_gradient(X, F) * lu.solve(Gs)).sum())
        for it in range(iterations):
            rhs = w_s * (GE.T @ targets(X, E, L0)) + w_m * Y0
            if GB is not None:
                rhs += w_b * (GB.T @ targets(X, B, B0))
            if closed:
                # internal pressure: a smooth outward force on the free surface, raised or lowered (integral
                # control) until the enclosed volume is the target, so it acts through the global solve instead
                # of being undone by it
                Gs = smooth_vectors(volume_gradient(X, F), E, 4)
                Gs[touched] = 0.0
                rhs += pressure * Gs
            X = lu.solve(rhs)
            if closed and gain > 1e-18:
                pressure += 0.7 * (target - volume(X, F, 0.0)) / gain
            if collide and it % 2 == 1:
                pairs = cKDTree(X).query_pairs(radius, output_type="ndarray")
                collisions = 0
                if len(pairs):
                    pairs = pairs[np.linalg.norm(rest[pairs[:, 0]] - rest[pairs[:, 1]], axis=1) > 2.5 * radius]
                    collisions = len(pairs)
                    if len(pairs):
                        cnt = np.maximum(np.bincount(pairs.ravel(), minlength=n), 1).astype(np.float64)[:, None]
                        d = X[pairs[:, 1]] - X[pairs[:, 0]]
                        L = np.linalg.norm(d, axis=1)
                        corr = ((L - radius) / np.maximum(L, 1e-12) * 0.5)[:, None] * d
                        D = np.zeros_like(X)
                        np.add.at(D, pairs[:, 0], corr)
                        np.add.at(D, pairs[:, 1], -corr)
                        X = X + D / cnt
            Y, hit = press.contact(X, u)
            if mu > 0 and hit.any():
                nrm = Y[hit] - X[hit]
                nn = np.linalg.norm(nrm, axis=1, keepdims=True)
                nrm = np.where(nn > 1e-15, nrm / np.maximum(nn, 1e-15), 0.0)
                dlt = Y[hit] - start[hit]
                Y[hit] -= mu * (dlt - (dlt * nrm).sum(1, keepdims=True) * nrm)
                Y, _ = press.contact(Y, u)
            X = Y
            touched = hit
        # viscoplastic flow: the skin's rest lengths creep toward the shape it was pushed into
        if creep > 0:
            L0 = L0 + creep * (np.linalg.norm(X[E[:, 0]] - X[E[:, 1]], axis=1) - L0)
            if len(B):
                B0 = B0 + BEND_CREEP * creep * (np.linalg.norm(X[B[:, 0]] - X[B[:, 1]], axis=1) - B0)
        if trace:
            trace(step, X)
        if progress:
            progress(step / steps)
    L = np.linalg.norm(X[E[:, 0]] - X[E[:, 1]], axis=1)
    shorten = 1.0 - L / np.maximum(Lfix, 1e-18)
    return Outcome(X, v0, volume(X, F), float(touched.mean()) if n else 0.0, int(collisions),
                   float(shorten.max()) if len(shorten) else 0.0)


# ----------------------------------------------------------------------------- morph (analytic)

def morph(X: np.ndarray, F: np.ndarray, m: Mould, s: float, amount: float, relief: float,
          scale: float = 0.15) -> np.ndarray:
    """Every point slides along its ray from the shape's centre onto the shape at scale s; `relief` keeps that
    much of its height above the model's smoothed surface (features stay raised on the faces of the shape). The
    smoothing is measured in model space (`scale` x the model's height), whatever the mesh density: distances
    from the centre are averaged on a coarse copy and read back by inverse-distance weighting."""
    from scipy.spatial import cKDTree
    X = np.asarray(X, dtype=np.float64)
    d = X - m.c
    r = np.linalg.norm(d, axis=1)
    U = m.to_unit(X, s)
    g = np.maximum(m.shape.gauge(U), 1e-12)
    Xb = m.from_unit(U / g[:, None], s)
    if relief > 0:
        height = float(np.ptp(X[:, 2])) or 1.0
        C, CF, edge = remesh(X, np.asarray(F, np.int64), 3000)
        CE, _ = edges_of(CF)
        rc = smooth_scalar(np.linalg.norm(C - m.c, axis=1), CE, max(2, int((scale * height / edge) ** 2)))
        dist, J = cKDTree(C).query(X, k=6)
        w = 1.0 / (dist + 1e-9) ** 2
        rs = (rc[J] * w).sum(1) / w.sum(1)
        out = d / np.maximum(r, 1e-12)[:, None]
        Xb = Xb + out * (relief * (r - rs))[:, None]
    return X + (Xb - X) * float(amount)


# ----------------------------------------------------------------------------- diagnostics

def displacement(V0: np.ndarray, V1: np.ndarray) -> float:
    return float(np.linalg.norm(V1 - V0, axis=1).max()) if len(V0) else 0.0
