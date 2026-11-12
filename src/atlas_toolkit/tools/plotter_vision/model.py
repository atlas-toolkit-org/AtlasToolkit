"""
The model as plotter.vision sees it, prepared once per file and settings:

- centred on its bounding box and scaled so its largest side is 100 (plotter.vision's units);
- optionally decimated to a face budget (quadric error, fast_simplification), the step usually done in Blender
  before plotter.vision ("aphrodite--3.5K.stl");
- welded within 1e-6 of its size, so faces that meet share their edges (plotter.vision matched corners on a
  0.01 grid, per triangle and in O(n) buckets);
- with consistent winding when asked: plotter.vision culls faces by winding, so a flipped face became a hole;
- with its topology: every edge once, the faces on each side and the angle between their normals, open borders,
  and edges shared by more than two faces;
- with the lines where separate parts cut into each other (two spheres overlapping, a cone through a plate):
  plotter.vision drew none, so overlapping solids met without a line. They do not depend on the view.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

SIZE = 100.0


@dataclass
class Model:
    V: np.ndarray                 # (n, 3) normalised vertices
    F: np.ndarray                 # (m, 3) faces
    FN: np.ndarray                # (m, 3) unit face normals
    E: np.ndarray                 # (k, 2) every edge once (vertex ids)
    EF: np.ndarray                # (k, 2) its faces; the second is -1 on an open border
    EA: np.ndarray                # (k,) angle between the two faces' normals, degrees (0 on borders)
    many: np.ndarray              # (k,) bool: shared by more than two faces
    centre: np.ndarray = field(default_factory=lambda: np.zeros(3))
    scale: float = 1.0            # normalised = (model - centre) * scale
    faces_in: int = 0             # faces before decimation
    watertight: bool = False
    notes: list = field(default_factory=list)
    X: np.ndarray = field(default_factory=lambda: np.zeros((0, 2, 3)))     # intersection segments
    XF: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), np.int64))  # the two faces of each
    native: np.ndarray | None = None  # actual polygon edges; internal triangles still supply silhouettes

    @property
    def faces(self) -> int:
        return int(len(self.F))


def topology(F: np.ndarray, FN: np.ndarray, nv: int):
    """Unique edges with their (first two) faces and the angle between them."""
    m = len(F)
    e = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
    fid = np.tile(np.arange(m), 3)
    key = e[:, 0].astype(np.int64) * nv + e[:, 1]
    order = np.argsort(key, kind="stable")
    ks = key[order]
    start = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    count = np.diff(np.r_[start, len(ks)])
    E = e[order][start]
    f1 = fid[order][start]
    f2 = np.where(count >= 2, fid[order][np.minimum(start + 1, len(ks) - 1)], -1)
    EF = np.stack([f1, f2], axis=1)
    two = f2 >= 0
    EA = np.zeros(len(E))
    cosang = np.einsum("ij,ij->i", FN[f1[two]], FN[f2[two]])
    EA[two] = np.degrees(np.arccos(np.clip(cosang, -1.0, 1.0)))
    return E, EF, EA, count > 2


def _plane_cut(P: np.ndarray, n: np.ndarray, d: np.ndarray):
    """Where triangles P (k, 3, 3) cross the planes n·x = d: two points each, and whether they cross at all."""
    s = np.einsum("kij,kj->ki", P, n) - d[:, None]
    pos = s > 0
    npos = pos.sum(1)
    ok = (npos == 1) | (npos == 2)
    lone = np.argmax(np.where((npos == 1)[:, None], pos, ~pos), axis=1)
    k = np.arange(len(P))
    o1, o2 = (lone + 1) % 3, (lone + 2) % 3
    sl, s1, s2 = s[k, lone], s[k, o1], s[k, o2]
    with np.errstate(divide="ignore", invalid="ignore"):
        t1 = sl / (sl - s1)
        t2 = sl / (sl - s2)
    X1 = P[k, lone] + t1[:, None] * (P[k, o1] - P[k, lone])
    X2 = P[k, lone] + t2[:, None] * (P[k, o2] - P[k, lone])
    return X1, X2, ok & np.isfinite(t1) & np.isfinite(t2)


def intersections(V: np.ndarray, F: np.ndarray, FN: np.ndarray, part: np.ndarray, budget: int = 4_000_000):
    """Segments where faces of different parts (connected components) cross: (k, 2, 3) and their face pairs."""
    m = len(F)
    if m == 0 or part.max() == part.min():
        return np.zeros((0, 2, 3)), np.zeros((0, 2), np.int64)
    P = V[F]
    lo, hi = P.min(1), P.max(1)
    ext = (hi - lo).max(1)
    cell = float(max(np.median(ext) * 2.0, 1e-6))
    g0 = lo.min(0)
    c0 = np.floor((lo - g0) / cell).astype(np.int64)
    c1 = np.floor((hi - g0) / cell).astype(np.int64)
    dims = c1.max(0) + 1
    span = c1 - c0 + 1
    cnt = span.prod(1)
    face = np.repeat(np.arange(m), cnt)
    local = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    sx, sy = span[face, 0], span[face, 1]
    cx = c0[face, 0] + local % sx
    cy = c0[face, 1] + (local // sx) % sy
    cz = c0[face, 2] + local // (sx * sy)
    cid = (cz * dims[1] + cy) * dims[0] + cx
    o = np.argsort(cid, kind="stable")
    face, cid = face[o], cid[o]
    start = np.flatnonzero(np.r_[True, cid[1:] != cid[:-1]])
    size = np.diff(np.r_[start, len(cid)])
    # every pair within a cell: element i with the ones after it in its cell
    end = np.repeat(start + size, size)
    after = end - np.arange(len(cid)) - 1
    if int(after.sum()) > budget:
        return np.zeros((0, 2, 3)), np.zeros((0, 2), np.int64)
    a = np.repeat(np.arange(len(cid)), after)
    b = a + 1 + (np.arange(len(a)) - np.repeat(np.cumsum(after) - after, after))
    fa, fb, cell_of = face[a], face[b], cid[a]
    keep = part[fa] != part[fb]
    fa, fb, cell_of = fa[keep], fb[keep], cell_of[keep]
    olo = np.maximum(lo[fa], lo[fb])
    keep = (np.minimum(hi[fa], hi[fb]) >= olo).all(1)
    oc = np.floor((olo - g0) / cell).astype(np.int64)
    keep &= ((oc[:, 2] * dims[1] + oc[:, 1]) * dims[0] + oc[:, 0]) == cell_of        # each pair once
    fa, fb = fa[keep], fb[keep]
    if not len(fa):
        return np.zeros((0, 2, 3)), np.zeros((0, 2), np.int64)
    nA, nB = FN[fa], FN[fb]
    dA = np.einsum("ij,ij->i", nA, P[fa, 0])
    dB = np.einsum("ij,ij->i", nB, P[fb, 0])
    A1, A2, okA = _plane_cut(P[fa], nB, dB)
    B1, B2, okB = _plane_cut(P[fb], nA, dA)
    line = np.cross(nA, nB)
    ok = okA & okB & (np.linalg.norm(line, axis=1) > 1e-9)
    a1, a2 = np.einsum("ij,ij->i", A1, line), np.einsum("ij,ij->i", A2, line)
    b1, b2 = np.einsum("ij,ij->i", B1, line), np.einsum("ij,ij->i", B2, line)
    lo_t = np.maximum(np.minimum(a1, a2), np.minimum(b1, b2))
    hi_t = np.minimum(np.maximum(a1, a2), np.maximum(b1, b2))
    ok &= (hi_t - lo_t > 1e-9) & (np.abs(a2 - a1) > 1e-12)
    da = np.where(np.abs(a2 - a1) > 1e-12, a2 - a1, 1.0)
    S0 = A1 + ((lo_t - a1) / da)[:, None] * (A2 - A1)
    S1 = A1 + ((hi_t - a1) / da)[:, None] * (A2 - A1)
    return np.stack([S0, S1], axis=1)[ok], np.stack([fa, fb], axis=1)[ok]


def prepare(mesh, target_faces: int = 0, fix_winding: bool = True, log=None, crossings: bool = True,
            frame=None) -> Model:
    """A Model from a trimesh mesh (not modified). `frame` (geom/frame3d.Frame3D, largest side 100) replaces the
    model's own centring and scale: other tools pass the shared frame of a stage set, and their up axis."""
    import trimesh
    from atlas_toolkit.geom.surface import polygon_data
    polygons = polygon_data(mesh)
    log = log or (lambda *_: None)
    notes = []
    V = np.asarray(mesh.vertices, dtype=np.float64)
    F = np.asarray(mesh.faces, dtype=np.int64)
    faces_in = len(F)
    if frame is None:
        lo, hi = V.min(0), V.max(0)
        centre = (lo + hi) / 2.0
        scale = SIZE / (float((hi - lo).max()) or 1.0)
        m = trimesh.Trimesh((V - centre) * scale, F, process=False)
    else:
        centre, scale = np.asarray(frame.centre, dtype=np.float64), float(frame.scale)
        m = trimesh.Trimesh(frame.apply(V), F, process=False)
    if target_faces and len(m.faces) > target_faces:
        try:
            m = m.simplify_quadric_decimation(face_count=int(target_faces))
            polygons = None
            notes.append(f"decimated {faces_in:,} → {len(m.faces):,} faces".replace(",", " "))
        except Exception as e:              # fast_simplification missing or the mesh refused
            notes.append(f"decimation skipped ({type(e).__name__}: {e})")
    if polygons is None:
        m.merge_vertices(digits_vertex=4)
        m.update_faces(m.nondegenerate_faces())
        m.update_faces(m.unique_faces())
        m.remove_unreferenced_vertices()
    if fix_winding and len(m.faces):
        before = m.face_normals.copy()
        trimesh.repair.fix_winding(m)
        if m.is_watertight:
            trimesh.repair.fix_inversion(m)
        flipped = int((np.einsum("ij,ij->i", before, m.face_normals) < 0).sum())
        if flipped:
            notes.append(f"winding fixed on {flipped:,} faces".replace(",", " "))
    V = np.asarray(m.vertices, dtype=np.float64)
    F = np.asarray(m.faces, dtype=np.int64)
    FN = np.asarray(m.face_normals, dtype=np.float64)
    E, EF, EA, many = topology(F, FN, len(V))
    X, XF = np.zeros((0, 2, 3)), np.zeros((0, 2), np.int64)
    if crossings and len(F):
        two = EF[:, 1] >= 0
        part = trimesh.graph.connected_component_labels(EF[two][:, ::-1], node_count=len(F))
        X, XF = intersections(V, F, FN, part)
        if len(X):
            notes.append(f"{len(X):,} intersection segments where parts cross".replace(",", " "))
    for n in notes:
        log(n)
    native = None
    if polygons is not None:
        owner = polygons['owners']
        native = (EF[:, 1] < 0) | (owner[EF[:, 0]] != owner[np.maximum(EF[:, 1], 0)])
    return Model(V, F, FN, E, EF, EA, many, centre, scale, faces_in, bool(m.is_watertight), notes, X, XF, native)


_CACHE: dict = {}


def load(path, target_faces: int = 0, fix_winding: bool = True, log=None, crossings: bool = True,
         frame=None) -> Model:
    """prepare() for a file, cached by file, size, time and settings (the live view asks on every change)."""
    from atlas_toolkit.geom.mesh import load_mesh
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_size, st.st_mtime_ns, int(target_faces or 0), bool(fix_winding),
           bool(crossings)) + ((frame.key(),) if frame is not None else ())
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    mdl = prepare(load_mesh(path), target_faces, fix_winding, log, crossings, frame)
    if len(_CACHE) > 6:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = mdl
    return mdl
