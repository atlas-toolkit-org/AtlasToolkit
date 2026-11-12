"""
Batched ray casting for the 3D drawing tools: embree (embreex, bundled with the app) driven directly, with
trimesh's pure-Python intersector as a fallback.

trimesh's own embree wrapper rescales origins and has no maximum ray length, which visibility needs ("is anything
between the eye and this point?"), so the scene is built here: triangles moved to the box centre and scaled so the
largest side is 100 (float32 keeps ~1e-5 of that), queries mapped the same way.

    caster = Caster(V, F)
    t, face, u, v = caster.intersect(origins, dirs)          # t = inf where nothing is hit
    hit = caster.occluded(origins, dirs, tmax)                # anything closer than tmax?
    vis = visible(caster, cam, points, eps)                   # seen from the camera (geom/camera.Camera)?
    runs = split_visible(caster, cam, polylines, closed, eps) # visible / hidden runs, transitions bisected

Visibility of a point that lies on the surface stops the ray `eps` short of it (plus a relative slack that grows
with distance), the method of the Blender pieces (1e-3 of a 2-tall model there).
"""
from __future__ import annotations

import numpy as np

CHUNK = 250_000


class Caster:
    def __init__(self, V, F):
        V = np.asarray(V, dtype=np.float64)
        F = np.asarray(F, dtype=np.int64)
        self.V, self.F = V, F
        lo, hi = V.min(0), V.max(0)
        self.offset = (lo + hi) / 2.0
        ext = float((hi - lo).max())
        self.s = 100.0 / ext if ext > 0 else 1.0
        self.size = ext
        tri = ((V[F] - self.offset) * self.s).astype(np.float32)
        self.rays = 0
        self.backend = "embree"
        try:
            from embreex import rtcore_scene as rtcs
            from embreex.mesh_construction import TriangleMesh
            self.scene = rtcs.EmbreeScene()
            TriangleMesh(self.scene, np.ascontiguousarray(tri))
        except Exception:                       # pragma: no cover (embree is bundled; kept for odd platforms)
            import trimesh
            self.backend = "trimesh"
            self.scene = None
            self.tm = trimesh.Trimesh((V - self.offset) * self.s, F, process=False)

    # ------------------------------------------------------------------ queries
    def _local(self, origins, dirs):
        o = ((np.asarray(origins, dtype=np.float64).reshape(-1, 3) - self.offset) * self.s).astype(np.float32)
        d = np.asarray(dirs, dtype=np.float64).reshape(-1, 3)
        n = np.linalg.norm(d, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return np.ascontiguousarray(o), np.ascontiguousarray((d / n).astype(np.float32))

    def intersect(self, origins, dirs, tmax=None):
        """First hits: (t, face, u, v) per ray; t in model units (inf: no hit), face -1, u/v barycentric (the hit
        point is (1-u-v)·A + u·B + v·C of the face's corners)."""
        o, d = self._local(origins, dirs)
        n = len(o)
        t = np.full(n, np.inf)
        face = np.full(n, -1, dtype=np.int64)
        u = np.zeros(n)
        v = np.zeros(n)
        tm = None if tmax is None else np.broadcast_to(np.asarray(tmax, dtype=np.float64), (n,)) * self.s
        self.rays += n
        for a in range(0, n, CHUNK):
            b = min(n, a + CHUNK)
            if self.backend == "embree":
                dist = None if tm is None else np.ascontiguousarray(tm[a:b].astype(np.float32))
                r = self.scene.run(o[a:b], d[a:b], dists=dist, output=1)
                pid = np.asarray(r["primID"], dtype=np.int64)
                hit = pid >= 0
                t[a:b][hit] = np.asarray(r["tfar"], dtype=np.float64)[hit] / self.s
                face[a:b] = np.where(hit, pid, -1)
                u[a:b] = np.where(hit, r["u"], 0.0)
                v[a:b] = np.where(hit, r["v"], 0.0)
            else:                                # pragma: no cover
                self._fallback(o[a:b], d[a:b], None if tm is None else tm[a:b], t[a:b], face[a:b], u[a:b], v[a:b])
        return t, face, u, v

    def occluded(self, origins, dirs, tmax) -> np.ndarray:
        """True where a ray hits anything before tmax (model units)."""
        o, d = self._local(origins, dirs)
        n = len(o)
        tm = np.broadcast_to(np.asarray(tmax, dtype=np.float64), (n,)) * self.s
        out = np.zeros(n, dtype=bool)
        self.rays += n
        for a in range(0, n, CHUNK):
            b = min(n, a + CHUNK)
            dist = np.ascontiguousarray(np.maximum(tm[a:b], 0.0).astype(np.float32))
            if self.backend == "embree":
                r = self.scene.run(o[a:b], d[a:b], dists=dist, query="OCCLUDED")
                out[a:b] = np.asarray(r) >= 0
            else:                                # pragma: no cover
                t = np.full(b - a, np.inf)
                self._fallback(o[a:b], d[a:b], dist, t, np.full(b - a, -1), np.zeros(b - a), np.zeros(b - a))
                out[a:b] = np.isfinite(t)
        return out

    def _fallback(self, o, d, tm, t, face, u, v):     # pragma: no cover
        loc, ri, fi = self.tm.ray.intersects_location(o.astype(np.float64), d.astype(np.float64),
                                                      multiple_hits=False)
        if not len(ri):
            return
        tt = np.einsum("ij,ij->i", loc - o[ri], d[ri])
        ok = tt > 0
        if tm is not None:
            ok &= tt <= tm[ri]
        ri, fi, tt, loc = ri[ok], fi[ok], tt[ok], loc[ok]
        A, B, C = (self.tm.vertices[self.tm.faces[fi, k]] for k in range(3))
        bary = _barycentric(loc, A, B, C)
        t[ri] = tt / self.s
        face[ri] = fi
        u[ri], v[ri] = bary[:, 1], bary[:, 2]

    def points(self, face, u, v) -> np.ndarray:
        """Hit points (model units) from faces and barycentrics."""
        f = self.F[np.maximum(face, 0)]
        A, B, C = self.V[f[:, 0]], self.V[f[:, 1]], self.V[f[:, 2]]
        return (1 - u - v)[:, None] * A + u[:, None] * B + v[:, None] * C


def _barycentric(P, A, B, C):
    v0, v1, v2 = B - A, C - A, P - A
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
    den = d00 * d11 - d01 * d01
    den[den == 0] = 1.0
    b1 = (d11 * d20 - d01 * d21) / den
    b2 = (d00 * d21 - d01 * d20) / den
    return np.stack([1 - b1 - b2, b1, b2], axis=1)


# ----------------------------------------------------------------------------- visibility from a camera

def visible(caster: Caster, cam, P, eps: float, rel: float = 2e-5) -> np.ndarray:
    """True where nothing hides surface point P (n, 3) from the camera. Points behind the eye are hidden."""
    P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
    if not len(P):
        return np.zeros(0, dtype=bool)
    eye, u, v, w = cam.basis()
    depth = -((P - eye) @ w)
    if cam.ortho:
        span = float(np.linalg.norm(P - eye, axis=1).max()) + caster.size * 2.0 + 1.0
        origins = P + w * span
        dirs = np.broadcast_to(-w, P.shape)
        dist = np.full(len(P), span)
        front = np.ones(len(P), dtype=bool)
    else:
        D = P - eye
        dist = np.linalg.norm(D, axis=1)
        dist[dist == 0] = 1e-12
        origins = np.broadcast_to(eye, P.shape)
        dirs = D / dist[:, None]
        from .camera import NEAR
        front = depth > NEAR
    tmax = np.maximum(dist - eps - rel * dist, 0.0)
    return front & ~caster.occluded(origins, dirs, tmax)


def refine(caster: Caster, cam, A, B, va, eps: float, iters: int = 8) -> np.ndarray:
    """Bisect segments A->B (n, 3) whose ends differ in visibility (va at A): the switching points, all at once."""
    A = np.array(A, dtype=np.float64).reshape(-1, 3)
    B = np.array(B, dtype=np.float64).reshape(-1, 3)
    va = np.asarray(va, dtype=bool)
    for _ in range(iters):
        M = (A + B) / 2.0
        vm = visible(caster, cam, M, eps)
        same = vm == va
        A[same] = M[same]
        B[~same] = M[~same]
    return (A + B) / 2.0


def split_visible(caster: Caster, cam, polylines: list, closed: list | None = None, eps: float = 0.05,
                  iters: int = 8, hidden: bool = True) -> list:
    """Each 3D polyline (k, 3) as runs [(visible, points (m, 3))] in order, the switching points bisected; a closed
    loop that starts and ends in the same state is joined across its start. With hidden=False, only visible runs.

    All samples of all polylines go through one batch of rays, all transitions through one batch per bisection
    step."""
    closed = closed or [False] * len(polylines)
    lens = [len(p) for p in polylines]
    if not polylines or sum(lens) == 0:
        return [[] for _ in polylines]
    P = np.concatenate([np.asarray(p, dtype=np.float64).reshape(-1, 3) for p in polylines])
    vis = visible(caster, cam, P, eps)
    starts = np.cumsum([0] + lens[:-1])
    # transitions inside each polyline (not across polyline boundaries)
    change = np.flatnonzero(vis[1:] != vis[:-1])
    owner_end = np.repeat(np.arange(len(lens)), lens)
    change = change[owner_end[change] == owner_end[change + 1]]
    T = refine(caster, cam, P[change], P[change + 1], vis[change], eps, iters) if len(change) else np.zeros((0, 3))
    tpos = {int(i): T[k] for k, i in enumerate(change)}
    out = []
    for pi, (s0, n) in enumerate(zip(starts, lens)):
        if n == 0:
            out.append([])
            continue
        runs = []
        cur = [P[s0]]
        state = bool(vis[s0])
        for i in range(s0, s0 + n - 1):
            if i in tpos:
                t = tpos[i]
                cur.append(t)
                runs.append((state, np.array(cur)))
                cur = [t]
                state = not state
            cur.append(P[i + 1])
        runs.append((state, np.array(cur)))
        if closed[pi] and len(runs) > 1 and runs[0][0] == runs[-1][0]:
            st, last = runs.pop()
            runs[0] = (st, np.concatenate([last, runs[0][1][1:]]))
        if not hidden:
            runs = [r for r in runs if r[0]]
        out.append([r for r in runs if len(r[1]) >= 2])
    return out
