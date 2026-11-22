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

from atlas_toolkit.geom.contact import box_pairs, plane_cut

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


def intersections(V: np.ndarray, F: np.ndarray, FN: np.ndarray, part: np.ndarray, budget: int = 4_000_000):
    """Segments where faces of different parts (connected components) cross: (k, 2, 3) and their face pairs."""
    m = len(F)
    if m == 0 or part.max() == part.min():
        return np.zeros((0, 2, 3)), np.zeros((0, 2), np.int64)
    P = V[F]
    # the grid of geom/contact, as one batch and without its big-face split: the same pairs in the same order
    chunks = box_pairs(P.min(1), P.max(1), chunk=None, budget=budget, min_cell=1e-6, big_cells=None)
    if not chunks:
        return np.zeros((0, 2, 3)), np.zeros((0, 2), np.int64)
    fa, fb = chunks[0]
    keep = part[fa] != part[fb]
    fa, fb = fa[keep], fb[keep]
    if not len(fa):
        return np.zeros((0, 2, 3)), np.zeros((0, 2), np.int64)
    nA, nB = FN[fa], FN[fb]
    dA = np.einsum("ij,ij->i", nA, P[fa, 0])
    dB = np.einsum("ij,ij->i", nB, P[fb, 0])
    A1, A2, okA = plane_cut(P[fa], nB, dB)
    B1, B2, okB = plane_cut(P[fb], nA, dA)
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
_LIGHT: dict = {}                 # light copies for the Aim tabs, in the file's own coordinates
LIGHT_SLACK = 1.25                # quadric decimation may stop short of the budget by this much before clustering


def _remember(cache: dict, key, value, keep: int = 6):
    if len(cache) > keep:
        cache.pop(next(iter(cache)))
    cache[key] = value
    return value


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
    return _remember(_CACHE, key, prepare(load_mesh(path), target_faces, fix_winding, log, crossings, frame))


def light_mesh(path, budget: int):
    """The file as a trimesh of at most about `budget` faces, in its own coordinates, cached by file, size and time.

    Quadric decimation first (it keeps the shape); when it stalls short of the budget (it can stop at two thirds of
    a model with many thin parts), vertex clustering finishes the job, so a light copy is always light. A mesh
    already within the budget comes back as loaded (an OBJ's or PLY's polygons stay)."""
    import trimesh
    from atlas_toolkit.geom.mesh import decimate, load_mesh, simplify_faces
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_size, st.st_mtime_ns, int(budget))
    hit = _LIGHT.get(key)
    if hit is not None:
        return hit
    mesh = load_mesh(path)
    if budget and len(mesh.faces) > budget:
        V, F = simplify_faces(mesh.vertices, mesh.faces, int(budget))
        if len(F) > budget * LIGHT_SLACK:
            V, F = decimate(V, F, int(budget))
        mesh = trimesh.Trimesh(np.asarray(V, dtype=np.float64), np.asarray(F, dtype=np.int64), process=False)
    return _remember(_LIGHT, key, mesh, keep=3)


def load_light(path, budget: int, fix_winding: bool = True, frame=None) -> Model:
    """The Aim tabs' light copy of a file (no crossings): light_mesh(), then placed (`frame`) and prepared. A new
    Up, Front or turn only places the reduced mesh again; the file is not read or decimated twice. The engines use
    load(), so no drawing changes with it."""
    st = os.stat(path)
    key = ("light", os.path.abspath(path), st.st_size, st.st_mtime_ns, int(budget), bool(fix_winding)) + (
        (frame.key(),) if frame is not None else ())
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    return _remember(_CACHE, key, prepare(light_mesh(path, budget), 0, fix_winding, None, False, frame))
