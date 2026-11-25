"""
Mesh repair for the mesh-to-mesh tools: what is wrong with a model, a *safe tidy* that never moves the visible
surface, and a full repair (hole filling, then resampling only if asked) for the Repair Mesh tool.

Most models that break the solid tools carry one of four kinds of damage:
- debris left by decimation: a pair of back-to-back faces (a sheet with no volume) and loose crumbs of a few faces;
- open holes (scans cropped without a cap, gaps in a scan);
- non-manifold edges and "bow-tie" vertices, where two parts of the surface touch along an edge or at a point;
- faces wound inconsistently, or a whole model inside out.

`tidy` fixes the first, third and fourth without changing the shape and closes holes of a few edges; `repair` adds
real hole filling and, last, resampling (`rebuild`), which closes anything but softens detail.

A tool that needs a closed solid declares `needs="solid"` in its spec and calls `prepare(...)`, which tidies the
input only when it fails and raises `NeedsRepair` (it names the Repair Mesh tool) when it still fails.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.errors import ToolError

REPAIR_TOOL = "mesh-repair"
REPAIR_TOOL_NAME = "Repair Mesh"
REPAIR_TOOL_PLACE = "Mesh → Mesh › Prepare and cut"    # where the tool list has it (test_repair_guidance checks)
CRUMB_SHARE = 0.002          # loose pieces below this share of the largest piece's faces are debris
TIDY_HOLE_EDGES = 8          # tidy closes holes of at most this many edges (pin-holes, a missing triangle or two)


# ----------------------------------------------------------------------------- arrays

def _arrays(mesh):
    return np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces, dtype=np.int64)


def _trimesh(V, F):
    import trimesh
    return trimesh.Trimesh(np.asarray(V, dtype=np.float64), np.asarray(F, dtype=np.int64), process=False)


def _compact(V, F):
    """Drop unreferenced vertices."""
    if not len(F):
        return V[:0], F
    used = np.zeros(len(V), bool)
    used[F.ravel()] = True
    remap = np.cumsum(used) - 1
    return V[used], remap[F]


def _unique_rows(rows):
    """np.unique(rows, axis=0, return_inverse, return_counts) for integer rows, fast (lexsort, no row views)."""
    rows = np.asarray(rows)
    if not len(rows):
        return rows, np.zeros(0, np.int64), np.zeros(0, np.int64)
    order = np.lexsort(rows.T[::-1])
    srt = rows[order]
    new = np.r_[True, np.any(srt[1:] != srt[:-1], axis=1)]
    gid = np.cumsum(new) - 1
    inv = np.empty(len(rows), np.int64)
    inv[order] = gid
    counts = np.bincount(gid)
    return srt[new], inv, counts


def _edges(F):
    """Directed half-edges (a, b), the face of each, and for each the id of its undirected edge (with counts)."""
    he = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    face = np.tile(np.arange(len(F)), 3)
    lo, hi = np.minimum(he[:, 0], he[:, 1]), np.maximum(he[:, 0], he[:, 1])
    key = lo.astype(np.int64) * (int(F.max()) + 1 if len(F) else 1) + hi
    _, eid, count = np.unique(key, return_inverse=True, return_counts=True)
    return he, face, eid.reshape(-1), count


def _n(x, one: str, many: str | None = None) -> str:
    x = int(x)
    return f"{x:,} {one if x == 1 else (many or one + 's')}".replace(",", " ")


def signed_volume(V, F) -> float:
    if not len(F):
        return 0.0
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def _components(F, he=None, face=None, eid=None, count=None):
    """Face components joined through shared edges (any number of faces per edge)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    if not len(F):
        return 0, np.zeros(0, np.int64)
    if he is None:
        he, face, eid, count = _edges(F)
    order = np.argsort(eid, kind="stable")
    e_sorted, f_sorted = eid[order], face[order]
    same = e_sorted[1:] == e_sorted[:-1]
    a, b = f_sorted[:-1][same], f_sorted[1:][same]
    g = coo_matrix((np.ones(len(a)), (a, b)), shape=(len(F), len(F)))
    n, labels = connected_components(g, directed=False)
    return n, labels


def _face_orientation_keys(F):
    """For each face, its vertex set (sorted) and its winding parity, so back-to-back twins can be found."""
    s = np.sort(F, axis=1)
    r = np.argmin(F, axis=1)
    rolled = np.stack([F[np.arange(len(F)), (r + k) % 3] for k in range(3)], axis=1)
    parity = (rolled[:, 1] < rolled[:, 2]).astype(np.int8)        # min first, then which way round
    return s, parity


# ----------------------------------------------------------------------------- diagnosis

@dataclass
class Diagnosis:
    faces: int = 0
    vertices: int = 0
    degenerate: int = 0
    duplicate: int = 0            # extra copies of a face wound the same way
    sheets: int = 0               # faces in back-to-back pairs (no volume)
    boundary_edges: int = 0
    holes: int = 0
    largest_hole: int = 0         # edges in the largest hole
    nonmanifold_edges: int = 0
    bowtie_vertices: int = 0
    flipped_edges: int = 0        # shared edges whose two faces are wound the same way
    pieces: int = 0
    crumbs: int = 0               # pieces below CRUMB_SHARE of the largest
    crumb_faces: int = 0
    volume: float = 0.0
    solid: bool = False           # manifold3d accepts it as a closed, outward solid
    notes: list = field(default_factory=list)

    @property
    def manifold(self) -> bool:
        """A clean surface: openings allowed, nothing else wrong."""
        return not (self.degenerate or self.duplicate or self.sheets or self.nonmanifold_edges
                    or self.bowtie_vertices or self.flipped_edges)

    def ok_for(self, need: str | None) -> bool:
        if need == "solid":
            return self.solid
        if need == "manifold":
            return self.manifold
        return True

    def problems(self) -> list[str]:
        """The problems in plain words, most important first."""
        out = []

        def n(x, one, many):
            return f"{x:,} {one if x == 1 else many}".replace(",", " ")
        if self.sheets:
            out.append(n(self.sheets // 2 or 1, "zero-volume sheet", "zero-volume sheets")
                       + f" ({n(self.sheets, 'face', 'faces')} back to back)")
        if self.crumbs:
            out.append(n(self.crumbs, "loose crumb", "loose crumbs") + f" ({n(self.crumb_faces, 'face', 'faces')})")
        if self.holes:
            out.append(n(self.boundary_edges, "open edge", "open edges") + " in " + n(self.holes, "hole", "holes")
                       + (f" (the largest has {self.largest_hole} edges)" if self.holes > 1 else ""))
        if self.nonmanifold_edges:
            out.append(n(self.nonmanifold_edges, "non-manifold edge", "non-manifold edges")
                       + " (more than two faces meet)")
        if self.bowtie_vertices:
            out.append(n(self.bowtie_vertices, "bow-tie vertex", "bow-tie vertices") + " (parts touch at a point)")
        if self.flipped_edges:
            out.append(n(self.flipped_edges, "edge", "edges") + " between faces wound in opposite directions")
        if self.duplicate:
            out.append(n(self.duplicate, "duplicate face", "duplicate faces"))
        if self.degenerate:
            out.append(n(self.degenerate, "degenerate face", "degenerate faces") + " (no area)")
        if not out and self.volume < 0:
            out.append("the model is inside out (its faces point inward)")
        if not out and not self.solid and self.faces:
            out.append("the surface does not enclose a volume")
        out.extend(self.notes)
        return out

    def summary(self) -> str:
        p = self.problems()
        return "; ".join(p) if p else "a clean closed solid" if self.solid else "a clean surface"


def is_solid(V, F) -> bool:
    """What every solid tool accepts: each edge shared by exactly two faces wound opposite ways (trimesh's
    watertight and winding-consistent tests), a positive volume, and manifold3d's own test."""
    return solid_body(V, F) is not None


def closed_and_consistent(F) -> bool:
    """Every undirected edge used by exactly two faces, and by them in opposite directions (no directed edge twice).
    Two plain sorts of the edge keys, so it stays quick on scans of millions of faces."""
    F = np.asarray(F, dtype=np.int64)
    if len(F) < 4:
        return False
    a = F.reshape(-1)
    b = F[:, [1, 2, 0]].reshape(-1)
    n = np.int64(int(F.max()) + 1)
    directed = np.sort(a * n + b)
    if (directed[1:] == directed[:-1]).any():
        return False
    undirected = np.sort(np.minimum(a, b) * n + np.maximum(a, b))
    pairs = undirected.reshape(-1, 2) if len(undirected) % 2 == 0 else None
    return pairs is not None and bool((pairs[:, 0] == pairs[:, 1]).all()) and bool(
        (pairs[1:, 0] != pairs[:-1, 1]).all())


def open_half_edges(F) -> np.ndarray:
    """The directed edges (a, b) that no other face shares, with one sort of the edge keys (quick on big scans)."""
    F = np.asarray(F, dtype=np.int64)
    if not len(F):
        return np.zeros((0, 2), np.int64)
    he = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    n = np.int64(int(F.max()) + 1)
    key = np.minimum(he[:, 0], he[:, 1]) * n + np.maximum(he[:, 0], he[:, 1])
    order = np.argsort(key)
    sk = key[order]
    lone = np.ones(len(sk), bool)
    same = sk[1:] == sk[:-1]
    lone[1:] &= ~same
    lone[:-1] &= ~same
    return he[order[lone]]


def beyond_tidy(V, F) -> Diagnosis | None:
    """A short diagnosis when the model has an opening larger than the tidy closes (TIDY_HOLE_EDGES): no tidy can
    make it a solid, so a tool can say so at once instead of tidying a large scan for nothing. None otherwise, and
    for an opening that only rims a loose crumb (the tidy drops those): it must span 1% of the model or more."""
    bhe = open_half_edges(F)
    if len(bhe) <= TIDY_HOLE_EDGES:
        return None
    loops = _boundary_loops(np.asarray(F), bhe)
    V = np.asarray(V)
    scale = float(np.ptp(V, axis=0).max()) or 1.0
    big = [lp for lp in loops if len(lp) - (lp[-1] is None) > TIDY_HOLE_EDGES]
    # a wide opening, not the rim of a loose crumb (which the tidy drops): it spans 1% of the model or more
    wide = [lp for lp in big if float(np.ptp(V[[i for i in lp if i is not None]], axis=0).max()) > 0.01 * scale]
    if not wide:
        return None
    largest = max(len(lp) - (lp[-1] is None) for lp in loops)
    return Diagnosis(faces=int(len(F)), vertices=int(len(V)), boundary_edges=int(len(bhe)), holes=len(loops),
                     largest_hole=int(largest))


def solid_body(V, F):
    """is_solid's checks, returning manifold3d's solid (to use rather than build again) or None."""
    if len(F) < 4 or not closed_and_consistent(F) or signed_volume(V, F) <= 0:
        return None
    try:
        import manifold3d as mf
        mesh = mf.Mesh64(vert_properties=np.ascontiguousarray(V, dtype=np.float64),
                         tri_verts=np.ascontiguousarray(F, dtype=np.uint64))
        body = mf.Manifold(mesh)
        ok = body.status() == mf.Error.NoError and not body.is_empty() and body.volume() > 0
        return body if ok else None
    except Exception:          # pragma: no cover (manifold3d is bundled)
        return None


def diagnose(mesh) -> Diagnosis:
    V, F = _arrays(mesh)
    return diagnose_arrays(V, F)


def diagnose_arrays(V, F) -> Diagnosis:
    d = Diagnosis(faces=int(len(F)), vertices=int(len(V)))
    if not len(F):
        return d
    scale = float(np.ptp(V, axis=0).max()) or 1.0
    area2 = np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)
    repeated = (F[:, 0] == F[:, 1]) | (F[:, 1] == F[:, 2]) | (F[:, 0] == F[:, 2])
    d.degenerate = int((repeated | (area2 <= 1e-14 * scale * scale)).sum())
    s, parity = _face_orientation_keys(F)
    _, inv, cnt = _unique_rows(s)
    inv = inv.reshape(-1)
    multi = cnt[inv] > 1
    if multi.any():
        both = np.zeros(len(cnt), bool)
        pos = np.bincount(inv[multi], weights=parity[multi], minlength=len(cnt))
        both[(pos > 0) & (pos < cnt)] = True
        d.sheets = int((multi & both[inv]).sum())
        d.duplicate = int(cnt[(cnt > 1) & ~both].sum() - ((cnt > 1) & ~both).sum())
    he, face, eid, count = _edges(F)
    ec = count[eid]
    boundary = ec == 1
    d.boundary_edges = int(boundary.sum())
    d.nonmanifold_edges = int((count > 2).sum())
    if d.boundary_edges:
        loops = _boundary_loops(F, he[boundary])
        d.holes = len(loops)
        d.largest_hole = max((len(lp) for lp in loops), default=0)
    two = ec == 2
    if two.any():
        # for each edge shared by two faces, the two half-edges must run opposite ways
        e2 = eid[two]
        order = np.argsort(e2, kind="stable")
        a = he[two][order]
        first, second = a[0::2], a[1::2]
        d.flipped_edges = int((first[:, 0] == second[:, 0]).sum())
    if d.nonmanifold_edges == 0:
        try:
            import igl
            d.bowtie_vertices = int((~igl.is_vertex_manifold(np.ascontiguousarray(F, dtype=np.int64)))
                                    [np.unique(F)].sum())
        except Exception:        # pragma: no cover
            pass
    n, labels = _components(F, he, face, eid, count)
    sizes = np.bincount(labels, minlength=n)
    d.pieces = int(n)
    small = sizes < CRUMB_SHARE * sizes.max()
    d.crumbs = int(small.sum())
    d.crumb_faces = int(sizes[small].sum())
    d.volume = signed_volume(V, F)
    d.solid = is_solid(V, F)
    return d


# ----------------------------------------------------------------------------- steps
# Each takes (V, F) and returns (V, F, note or ""), the note saying what changed.

def weld(V, F, tol: float = 0.0):
    """Merge vertices at the same place (within tol model units; 0 = only identical positions)."""
    n0 = len(V)
    if tol > 0:
        key = np.round(V / tol).astype(np.int64)
    else:
        key = V
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    V2 = V[first]
    F2 = inv[F]
    merged = n0 - len(V2)
    return V2, F2, (f"welded {_n(merged, 'duplicate vertex', 'duplicate vertices')}" if merged else "")


def drop_degenerate(V, F):
    if not len(F):
        return V, F, ""
    scale = float(np.ptp(V, axis=0).max()) or 1.0
    repeated = (F[:, 0] == F[:, 1]) | (F[:, 1] == F[:, 2]) | (F[:, 0] == F[:, 2])
    area2 = np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)
    bad = repeated | (area2 <= 1e-14 * scale * scale)
    if not bad.any():
        return V, F, ""
    return V, F[~bad], f"removed {_n(bad.sum(), 'degenerate face')} (no area)"


def drop_duplicate_sheets(V, F):
    """Faces that appear twice: a back-to-back pair is a sheet with no volume (both go); a same-way copy goes."""
    if not len(F):
        return V, F, ""
    s, parity = _face_orientation_keys(F)
    _, inv, cnt = _unique_rows(s)
    inv = inv.reshape(-1)
    if not (cnt > 1).any():
        return V, F, ""
    keep = np.ones(len(F), bool)
    pos = np.bincount(inv, weights=parity, minlength=len(cnt))
    both = (pos > 0) & (pos < cnt) & (cnt > 1)
    sheet = both[inv]
    keep[sheet] = False
    # same-way copies: keep the first of each group
    order = np.argsort(inv, kind="stable")
    g = inv[order]
    later = np.zeros(len(F), bool)
    later[order[1:][g[1:] == g[:-1]]] = True
    copies = later & ~sheet
    keep[copies] = False
    notes = []
    if sheet.any():
        notes.append(f"removed {_n(sheet.sum(), 'back-to-back face')} (zero-volume sheets, often left by decimation)")
    if copies.any():
        notes.append(f"removed {_n(copies.sum(), 'duplicate face')}")
    return V, F[keep], "; ".join(notes)


def drop_pieces(V, F, mode: str = "crumbs", share: float = CRUMB_SHARE):
    """Loose pieces: 'crumbs' drops those below `share` of the largest piece's faces, 'largest' keeps only the
    largest, 'all' keeps everything."""
    if mode == "all" or not len(F):
        return V, F, ""
    n, labels = _components(F)
    if n < 2:
        return V, F, ""
    sizes = np.bincount(labels, minlength=n)
    if mode == "largest":
        drop = np.ones(n, bool)
        drop[np.argmax(sizes)] = False
    else:
        drop = sizes < share * sizes.max()
    if not drop.any():
        return V, F, ""
    gone = drop[labels]
    what = "loose piece" if mode == "largest" else "loose crumb"
    return V, F[~gone], f"removed {_n(drop.sum(), what)} ({_n(gone.sum(), 'face')})"


def split_nonmanifold(V, F):
    """Separate the surface where it is not a 2-manifold, without moving anything: at an edge with more than two
    faces, faces are paired around the edge so that each pair bounds the same solid; at a bow-tie vertex, each fan
    of faces gets its own copy of the vertex. Unpaired faces are left with an open edge (fill or drop them next)."""
    if not len(F):
        return V, F, ""
    he, face, eid, count = _edges(F)
    links = []                                   # (face a, face b, edge id) pairs that stay joined
    ec = count[eid]
    two = ec == 2
    order = np.argsort(eid[two], kind="stable")
    hf = face[two][order]
    links.append(np.stack([hf[0::2], hf[1::2]], axis=1))
    links_e = [eid[two][order][0::2]]
    bad_edges = np.nonzero(count > 2)[0]
    paired = 0
    partner = {}                                 # face -> the face it pairs with across a non-manifold edge
    if len(bad_edges):
        sel = np.nonzero(ec > 2)[0]
        sel = sel[np.argsort(eid[sel], kind="stable")]
        e_ids = eid[sel]
        starts = np.r_[0, np.nonzero(e_ids[1:] != e_ids[:-1])[0] + 1, len(sel)]
        for i in range(len(starts) - 1):
            hs = sel[starts[i]:starts[i + 1]]
            pairs = _pair_around_edge(V, F, he[hs], face[hs])
            for a, b in pairs:
                links.append(np.array([[a, b]]))
                links_e.append(np.array([e_ids[starts[i]]]))
                partner[(int(a), int(b))] = True
                paired += 1
    L = np.concatenate(links) if links else np.zeros((0, 2), np.int64)
    LE = np.concatenate(links_e) if links_e else np.zeros(0, np.int64)
    # union corners that share a vertex across a kept edge
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    ends = np.zeros((len(count), 2), np.int64)
    keys = np.sort(he, axis=1)
    ends[eid] = keys
    rows, cols = [], []
    for k in range(2):
        v = ends[LE, k]
        ca = L[:, 0] * 3 + np.argmax(F[L[:, 0]] == v[:, None], axis=1)
        cb = L[:, 1] * 3 + np.argmax(F[L[:, 1]] == v[:, None], axis=1)
        rows.append(ca)
        cols.append(cb)
    r = np.concatenate(rows) if rows else np.zeros(0, np.int64)
    c = np.concatenate(cols) if cols else np.zeros(0, np.int64)
    n_corners = len(F) * 3
    g = coo_matrix((np.ones(len(r)), (r, c)), shape=(n_corners, n_corners))
    n, label = connected_components(g, directed=False)
    corner_vertex = F.reshape(-1)
    # a new vertex per corner group; groups never mix original vertices
    first = np.full(n, -1, np.int64)
    first[label[::-1]] = corner_vertex[::-1]
    newV = V[first]
    newF = label.reshape(-1, 3)
    split_v = n - len(np.unique(corner_vertex))
    if len(bad_edges):
        newV, newF = _separate_shared_edges(newV, newF, partner)
    if split_v > 0 or len(bad_edges):
        newV = _part_copies(newV, newF)
    notes = []
    if len(bad_edges):
        notes.append(f"separated {_n(len(bad_edges), 'non-manifold edge')}")
    if split_v > 0:
        notes.append(f"split {_n(split_v, 'shared vertex', 'shared vertices')} so each part has its own")
    if not notes:
        return V, F, ""
    return newV, newF, "; ".join(notes)


def _part_copies(V, F, share: float = 1e-3):
    """Vertices at the same place that are different vertices (the copies a split makes) move a hair toward their
    own faces (a share of the way to the middle of those faces), so the welding every loader does cannot join them
    again. Nothing else moves."""
    if not len(V):
        return V
    scale = float(np.ptp(V, axis=0).max()) or 1.0
    _, inv, cnt = _unique_rows(np.round(V / scale * 1e9).astype(np.int64))
    twins = np.nonzero(cnt[inv] > 1)[0]
    if not len(twins):
        return V
    V = V.copy()
    centre = V[F].mean(1)
    sums = np.zeros((len(V), 3))
    np.add.at(sums, F.ravel(), np.repeat(centre, 3, axis=0))
    count = np.bincount(F.ravel(), minlength=len(V))
    have = twins[count[twins] > 0]
    V[have] += share * (sums[have] / count[have, None] - V[have])
    return V


def _separate_shared_edges(V, F, pairs):
    """Where a folded surface still puts more than two faces on one edge after the vertex split (its two sheets
    share both end vertices), give every pair but the first its own copy of the edge: a vertex at the edge's
    midpoint splits both faces of the pair. Unpaired faces get one each, so they end with an open edge."""
    he, face, eid, count = _edges(F)
    crowded = np.nonzero(count > 2)[0]
    if not len(crowded):
        return V, F
    pair_of = {}
    for a, b in pairs:
        pair_of[a] = b
        pair_of[b] = a
    newV = [V]
    nv = len(V)
    split = {}                                   # face -> (edge a, edge b, new vertex)
    for e in crowded:
        hs = np.nonzero(eid == e)[0]
        fs = [int(f) for f in face[hs]]
        groups, done = [], set()
        for f in fs:
            if f in done:
                continue
            g = [f]
            done.add(f)
            q = pair_of.get(f)
            if q is not None and q in fs and q not in done:
                g.append(q)
                done.add(q)
            groups.append(g)
        groups.sort(key=len, reverse=True)
        a, b = he[hs[0]]
        mid = (V[a] + V[b]) / 2
        for g in groups[1:]:
            # a hair toward the pair's own faces, so welding never joins the copies again
            towards = V[F[g].ravel()].mean(0) - mid
            newV.append((mid + 1e-3 * towards)[None, :])
            for f in g:
                split[f] = (int(a), int(b), nv)
            nv += 1
    F2 = [F]
    drop = np.zeros(len(F), bool)
    for f, (a, b, m) in split.items():
        t = F[f].tolist()
        for k in range(3):
            p, q = t[k], t[(k + 1) % 3]
            if {p, q} == {a, b}:
                r = t[(k + 2) % 3]
                F2.append(np.array([[p, m, r], [m, q, r]], np.int64))
                drop[f] = True
                break
    F2[0] = F[~drop]
    return np.concatenate(newV), np.concatenate(F2)


def _pair_around_edge(V, F, hes, faces):
    """Pair the faces meeting at one non-manifold edge: sort them by angle around the edge and join neighbours that
    enclose the same solid (each pair wound opposite ways along the edge)."""
    u, w = hes[0]
    a, b = V[u], V[w]
    e = b - a
    ln = np.linalg.norm(e)
    if ln == 0:
        return []
    e = e / ln
    ref = None
    items = []
    for (p, q), f in zip(hes, faces):
        third = [x for x in F[f] if x != p and x != q]
        if not third:
            continue
        d = V[third[0]] - a
        d = d - (d @ e) * e
        dn = np.linalg.norm(d)
        if dn == 0:
            continue
        d /= dn
        if ref is None:
            ref = d
            ref2 = np.cross(e, ref)
        ang = np.arctan2(d @ ref2, d @ ref)
        tri = V[F[f]]
        n = np.cross(tri[1] - tri[0], tri[2] - tri[0])
        tangent = np.cross(e, d)                  # the way the angle grows, at this face
        opens_up = float(-n @ tangent) > 0        # its inside lies toward larger angles
        forward = (p, q) == (u, w)
        items.append((ang, f, opens_up, forward))
    items.sort()
    m = len(items)
    if m < 2:
        return []
    # bracket matching around the edge: a face whose inside opens toward larger angles pairs with the next face
    # (going round) whose inside opens back toward it, wound the other way along the edge. Folded or overlapping
    # parts nest like brackets; try every starting face and keep the most pairs.
    best = []
    for start in range(m):
        stack, pairs = [], []
        for k in range(m):
            it = items[(start + k) % m]
            if it[2]:
                stack.append(it)
            else:
                for j in range(len(stack) - 1, -1, -1):
                    if stack[j][3] != it[3]:
                        pairs.append((stack[j][1], it[1]))
                        del stack[j]
                        break
        if len(pairs) > len(best):
            best = pairs
            if 2 * len(best) >= m - (m % 2):
                break
    return best


def fix_winding(V, F):
    """Wind every piece's faces the same way (propagated across shared edges)."""
    if not len(F):
        return V, F, ""
    he, face, eid, count = _edges(F)
    two = count[eid] == 2
    order = np.argsort(eid[two], kind="stable")
    h = he[two][order]
    fa, fb = face[two][order][0::2], face[two][order][1::2]
    flipped = h[0::2, 0] == h[1::2, 0]           # same direction: one of the two must turn
    if not flipped.any():
        return V, F, ""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import breadth_first_order, connected_components
    n = len(F)
    g = coo_matrix((np.ones(len(fa)), (fa, fb)), shape=(n, n)).tocsr()
    g = g + g.T
    rel_keys = np.minimum(fa, fb).astype(np.int64) * n + np.maximum(fa, fb)
    rel = dict(zip(rel_keys.tolist(), flipped.tolist()))
    _ncomp, labels = connected_components(g, directed=False)
    turn = np.zeros(n, bool)
    for root in np.unique(labels, return_index=True)[1]:
        nodes, pred = breadth_first_order(g, int(root), directed=False, return_predecessors=True)
        for v in nodes[1:].tolist():
            p = int(pred[v])
            turn[v] = turn[p] ^ rel[min(p, v) * n + max(p, v)]
    F2 = F.copy()
    F2[turn] = F2[turn][:, ::-1]
    # check: a non-orientable piece (a Klein bottle, a Möbius band) cannot be wound consistently
    he2, _f2, eid2, count2 = _edges(F2)
    t2 = count2[eid2] == 2
    o2 = np.argsort(eid2[t2], kind="stable")
    h2 = he2[t2][o2]
    left = int((h2[0::2, 0] == h2[1::2, 0]).sum())
    note = f"turned {_n(turn.sum(), 'face')} so every piece is wound the same way" if turn.any() else ""
    if left:
        note += ("; " if note else "") + (f"{_n(left, 'edge')} cannot agree: the surface is non-orientable "
                                          "(a Möbius band, a Klein bottle), only a rebuild makes it a solid")
    return V, F2, note


def orient_outward(V, F):
    """Turn pieces that enclose a negative volume (inside out) the right way."""
    if not len(F):
        return V, F, ""
    n, labels = _components(F)
    F2 = F.copy()
    turned = 0
    for k in range(n):
        sel = labels == k
        if signed_volume(V, F[sel]) < 0:
            F2[sel] = F2[sel][:, ::-1]
            turned += 1
    if not turned:
        return V, F, ""
    return V, F2, ("turned the model the right way out" if n == 1 else f"turned {turned} inside-out pieces outward")


def _boundary_loops(F, bhe):
    """Loops of open edges, each as vertex ids in the order a filling patch must follow (opposite to the faces').
    An open chain that does not come back to its start ends with None."""
    if not len(bhe):
        return []
    out = {}                                   # the patch runs each open edge backward: b -> a
    for a, b in bhe.tolist():
        out.setdefault(b, []).append(a)
    loops = []
    for start in list(out):
        while out.get(start):
            loop = [start]
            cur = out[start].pop()
            while cur != start:
                loop.append(cur)
                nxt = out.get(cur)
                if not nxt:
                    loop.append(None)
                    break
                cur = nxt.pop()
                if len(loop) > len(bhe) + 1:      # pragma: no cover (safety)
                    loop.append(None)
                    break
            loops.append(loop)
    return loops


def fill_holes(V, F, max_edges: int | None = None, shape: str = "auto"):
    """Close open loops of edges. Holes with more than max_edges edges are left (None = fill all). 'flat' fills
    each hole across its best plane, 'smooth' with a relaxed membrane that follows the surrounding surface,
    'auto' picks flat for flat holes."""
    if not len(F):
        return V, F, ""
    he, face, eid, count = _edges(F)
    bhe = he[count[eid] == 1]
    if not len(bhe):
        return V, F, ""
    loops = _boundary_loops(F, bhe)
    newV = [V]
    newF = [F]
    nv = len(V)
    filled = left = skipped = 0
    for loop in loops:
        if loop[-1] is None:                       # open chain (a vertex with several open edges): not a loop
            skipped += 1
            continue
        if len(loop) < 3:
            skipped += 1
            continue
        if max_edges is not None and len(loop) > max_edges:
            left += 1
            continue
        idx = np.asarray(loop, np.int64)
        tris, extra = _patch(V, idx, shape, nv)
        if tris is None:
            skipped += 1
            continue
        if len(extra):
            newV.append(extra)
            nv += len(extra)
        newF.append(tris)
        filled += 1
    if not filled:
        note = ""
    else:
        note = f"filled {filled} hole{'s' if filled != 1 else ''}"
    if left:
        note += (", " if note else "") + f"left {left} larger hole{'s' if left != 1 else ''} open"
    if skipped:
        note += (", " if note else "") + f"{skipped} open edge chain{'s' if skipped != 1 else ''} could not be filled"
    return np.concatenate(newV), np.concatenate(newF), note


def boundary_loops(half_edges):
    """Loops of open half-edges (a, b) of any polygon mesh, each in the order a patch must follow; an open chain
    that does not come back to its start ends with None."""
    return _boundary_loops(None, np.asarray(half_edges, np.int64).reshape(-1, 2))


def fill_loop(V, idx, shape: str = "auto", base: int | None = None):
    """Triangles closing one loop of vertex ids (patch order, as boundary_loops gives) and the new vertices they
    use, numbered from base (default len(V)); (None, None) when the loop cannot be closed."""
    return _patch(np.asarray(V, float), np.asarray(idx, np.int64), shape, len(V) if base is None else base)


def _patch(V, idx, shape, base):
    """Triangles closing one loop (vertex ids `idx`, in patch order) and any new vertices (numbered from base).
    Small holes are triangulated straight across (earcut); larger ones get graded rings of new vertices, so the
    patch's triangles are about as large as the edges around it (long slivers break the tools that use the result)."""
    P = V[idx]
    n = len(idx)
    if n == 3:
        return idx[None, :].copy(), np.zeros((0, 3))
    c = P.mean(0)
    Q = P - c
    try:
        _, _sv, vt = np.linalg.svd(Q, full_matrices=False)
    except np.linalg.LinAlgError:
        return None, None
    normal = vt[2]
    size = float(np.linalg.norm(np.ptp(P, axis=0))) or 1.0
    flatness = float(np.abs(Q @ normal).max()) / size
    flat = shape == "flat" or (shape == "auto" and flatness < 0.04)
    if n <= SMALL_HOLE:
        tris = _earcut(Q, vt, idx)
        if tris is not None:
            return tris, np.zeros((0, 3))
    return _rings(V, idx, base, plane=(c, normal) if flat else None)


SMALL_HOLE = 12      # holes of up to this many edges are triangulated straight across


def _earcut(Q, vt, idx):
    import mapbox_earcut as earcut
    xy = np.ascontiguousarray(Q @ vt[:2].T, dtype=np.float64)
    # earcut wants a simple ring; check the projection does not fold the loop over itself
    area = 0.5 * np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1])
    if abs(area) < 1e-12:
        return None
    try:
        t = earcut.triangulate_float64(xy, np.array([len(xy)], dtype=np.uint32)).reshape(-1, 3).astype(np.int64)
    except Exception:          # pragma: no cover
        return None
    if len(t) != len(xy) - 2:
        return None
    u, w = xy[t[:, 1]] - xy[t[:, 0]], xy[t[:, 2]] - xy[t[:, 0]]
    tri_area = 0.5 * np.abs(u[:, 0] * w[:, 1] - u[:, 1] * w[:, 0]).sum()
    if tri_area > abs(area) * 1.001:              # the projected loop crosses itself
        return None
    return _match_loop_winding(idx[t], idx)


def _match_loop_winding(tris, idx):
    nxt = {int(a): int(b) for a, b in zip(idx, np.roll(idx, -1))}
    votes = 0
    for t in tris:
        for k in range(3):
            a, b = int(t[k]), int(t[(k + 1) % 3])
            if nxt.get(a) == b:
                votes += 1
            elif nxt.get(b) == a:
                votes -= 1
    return tris if votes >= 0 else tris[:, ::-1].copy()


def _resample_loop(P, m):
    """m points evenly spaced along the closed polyline P (by arc length), starting at P[0]."""
    nxt = np.roll(P, -1, axis=0)
    seg = np.linalg.norm(nxt - P, axis=1)
    total = float(seg.sum()) or 1.0
    at = np.concatenate([[0.0], np.cumsum(seg)])
    want = np.arange(m) * total / m
    k = np.clip(np.searchsorted(at, want, side="right") - 1, 0, len(P) - 1)
    t = ((want - at[k]) / np.where(seg[k] > 0, seg[k], 1.0))[:, None]
    return P[k] + (nxt[k] - P[k]) * t


def _zip(A, B):
    """Triangles joining two closed loops of vertex ids (A outer, B inner, same direction, any lengths): each step
    advances along whichever loop is behind, by its share of the way round."""
    a, b = len(A), len(B)
    out = []
    i = j = 0
    while i < a or j < b:
        if j >= b or (i < a and (i + 1) / a <= (j + 1) / b):
            out.append((A[i % a], A[(i + 1) % a], B[j % b]))
            i += 1
        else:
            out.append((A[i % a], B[(j + 1) % b], B[j % b]))
            j += 1
    return out


def _rings(V, idx, base, plane=None):
    """A cap of graded rings: each ring nearer the centre has fewer vertices, so the triangles stay about the size
    of the hole's edges; then the new vertices relax (umbrella) with the loop fixed. With plane=(point, normal) the
    cap is flat, otherwise a membrane that follows the surface around the hole."""
    from scipy.sparse import coo_matrix
    P = V[idx]
    n = len(idx)
    c = P.mean(0)
    h = float(np.linalg.norm(np.roll(P, -1, axis=0) - P, axis=1).mean()) or 1.0
    radius = float(np.linalg.norm(P - c, axis=1).mean())
    rings = int(np.clip(round(radius / h) - 1, 1, 60))
    X = [P]
    loops = [np.arange(n)]
    nxt = n
    for k in range(1, rings + 1):
        t = k / (rings + 1)
        m = max(3, int(round(n * (1 - t))))
        X.append(_resample_loop(P + (c - P) * t, m))
        loops.append(np.arange(nxt, nxt + m))
        nxt += m
    X.append(c[None, :])
    centre = nxt
    tris = []
    for k in range(rings):
        tris += _zip(loops[k], loops[k + 1])
    last = loops[-1]
    tris += [(last[i], last[(i + 1) % len(last)], centre) for i in range(len(last))]
    T = np.asarray(tris, dtype=np.int64)
    X = np.concatenate(X)
    m = len(X)
    E = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    E = np.concatenate([E, E[:, ::-1]])
    A = coo_matrix((np.ones(len(E)), (E[:, 0], E[:, 1])), shape=(m, m)).tocsr()
    A.data[:] = 1.0
    deg = np.asarray(A.sum(1)).ravel()
    free = np.arange(m) >= n
    for _ in range(80):
        X[free] = (A @ X)[free] / deg[free, None]
        if plane is not None:
            p0, nn = plane
            X[free] -= ((X[free] - p0) @ nn)[:, None] * nn[None, :]
    glob = np.concatenate([idx, base + np.arange(m - n)])
    return _match_loop_winding(glob[T], idx), X[n:]


def refill_flat_slivers(V, F, longer_than: float = 0.0, min_faces: int = 16, slender: float = 10.0,
                        angle: float = 0.5):
    """Re-triangulate flat patches made of long slivers, such as the fan that closes a planar cut (trimesh's
    slice_plane caps, many hole fillers), with graded rings about the size of their border edges. The shape does not
    change; only that flat region's triangles do. A tool that subdivides the surface would otherwise crowd hundreds
    of thousands of crumbs where the fan's slivers meet.

    A patch qualifies with at least `min_faces` faces within `angle` degrees of one plane, a median slenderness
    (longest edge² / twice the area; 1.15 for an equilateral triangle) above `slender`, a longest edge above
    `longer_than`, and one simple border loop around a disk. Returns V, F (unreferenced vertices dropped) and how
    many patches were refilled."""
    V = np.asarray(V, np.float64)
    F = np.asarray(F, np.int64)
    if len(F) < min_faces:
        return V, F, 0
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    tri = V[F]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    twice = np.linalg.norm(normal, axis=1)
    unit = normal / np.maximum(twice, 1e-300)[:, None]
    longest = np.linalg.norm(tri - tri[:, [1, 2, 0]], axis=2).max(axis=1)
    slim = longest ** 2 / np.maximum(twice, 1e-300)
    he, face, eid, count = _edges(F)
    # faces on each side of every two-sided edge
    order = np.argsort(eid, kind="stable")
    sid, sface = eid[order], face[order]
    first = np.r_[True, sid[1:] != sid[:-1]]
    a = sface[first]
    b = np.where(count[sid[first]] == 2, sface[np.minimum(np.flatnonzero(first) + 1, len(sface) - 1)], -1)
    two = b >= 0
    a, b = a[two], b[two]
    flat = np.einsum("ij,ij->i", unit[a], unit[b]) > np.cos(np.radians(angle))
    graph = coo_matrix((np.ones(int(flat.sum())), (a[flat], b[flat])), shape=(len(F), len(F)))
    _, label = connected_components(graph, directed=False)
    sizes = np.bincount(label)
    candidates = [g for g in np.flatnonzero(sizes >= min_faces)
                  if np.median(slim[label == g]) > slender and longest[label == g].max() > longer_than]
    if not candidates:
        return V, F, 0
    keep = np.ones(len(F), bool)
    newV, newF, nv, done = [V], [], len(V), 0
    for g in candidates:
        faces = np.flatnonzero(label == g)
        sub = F[faces]
        sub_he = np.concatenate([sub[:, [0, 1]], sub[:, [1, 2]], sub[:, [2, 0]]])
        key = np.minimum(sub_he[:, 0], sub_he[:, 1]) * nv + np.maximum(sub_he[:, 0], sub_he[:, 1])
        _, inverse, uses = np.unique(key, return_inverse=True, return_counts=True)
        border = sub_he[uses[inverse] == 1]
        verts = np.unique(sub)
        inner_edges = int((uses == 2).sum())
        if len(verts) - (inner_edges + len(border)) + len(sub) != 1:      # not a disk
            continue
        nxt = {}
        for s, t in border.tolist():
            if s in nxt:
                nxt = None
                break
            nxt[s] = t
        if not nxt:
            continue
        start = border[0, 0]
        loop = [start]
        while nxt.get(loop[-1], start) != start and len(loop) <= len(border):
            loop.append(nxt[loop[-1]])
        if len(loop) != len(border):                                      # several loops, or a pinched border
            continue
        tris, extra = _fill_flat(V, np.asarray(loop, np.int64), nv, max(longer_than, 0.0))
        if tris is None:
            continue
        keep[faces] = False
        if len(extra):
            newV.append(extra)
            nv += len(extra)
        newF.append(tris)
        done += 1
    if not done:
        return V, F, 0
    V2, F2 = _compact(np.concatenate(newV), np.concatenate([F[keep]] + newF))
    return V2, F2, done


def _fill_flat(V, loop, base, spacing):
    """Triangles over a flat, simply bounded loop of vertex ids: the border points and a grid of new points inside
    (spacing about `spacing`, never finer than the border edges, kept half a step from the border), triangulated
    (Delaunay) and clipped to the outline. (None, None) unless every border edge survives and the result is a disk."""
    import shapely
    from scipy.spatial import Delaunay
    P = V[loop]
    c = P.mean(0)
    try:
        _, _, vt = np.linalg.svd(P - c, full_matrices=False)
    except np.linalg.LinAlgError:
        return None, None
    basis = vt[:2]
    xy = (P - c) @ basis.T
    outline = shapely.Polygon(xy)
    if not outline.is_valid or outline.area <= 0:
        return None, None
    edge = float(np.linalg.norm(np.roll(xy, -1, axis=0) - xy, axis=1).mean())
    step = max(spacing, edge)
    lo, hi = xy.min(0), xy.max(0)
    gx, gy = np.meshgrid(np.arange(lo[0] + step / 2, hi[0], step), np.arange(lo[1] + step / 2, hi[1], step))
    grid = np.column_stack((gx.ravel(), gy.ravel()))
    # every other row shifted half a step: near-equilateral triangles
    grid[:, 0] += (np.round((grid[:, 1] - lo[1]) / step) % 2) * step / 2
    inner = outline.buffer(-0.5 * step)
    grid = grid[shapely.contains_xy(inner, grid[:, 0], grid[:, 1])] if not inner.is_empty else grid[:0]
    pts = np.vstack((xy, grid))
    try:
        tri = Delaunay(pts).simplices
    except Exception:
        return None, None
    centre = pts[tri].mean(1)
    tri = tri[shapely.contains_xy(outline, centre[:, 0], centre[:, 1])]
    u, w = pts[tri[:, 1]] - pts[tri[:, 0]], pts[tri[:, 2]] - pts[tri[:, 0]]
    tri = tri[np.abs(u[:, 0] * w[:, 1] - u[:, 1] * w[:, 0]) > 1e-12 * step * step]
    n = len(loop)
    have = {tuple(sorted(e)) for t in tri.tolist() for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0]))}
    if any(tuple(sorted((i, (i + 1) % n))) not in have for i in range(n)):
        return None, None
    area = 0.5 * np.abs(u[:, 0] * w[:, 1] - u[:, 1] * w[:, 0])
    area = area[np.abs(u[:, 0] * w[:, 1] - u[:, 1] * w[:, 0]) > 1e-12 * step * step]
    if abs(float(area.sum()) - outline.area) > 1e-6 * outline.area:      # overlapping or missing triangles
        return None, None
    glob = np.concatenate([loop, base + np.arange(len(grid))])
    return _match_loop_winding(glob[tri], loop), c + grid @ basis


def rebuild(V, F, resolution: int = 192, progress=None):
    """Resample into a closed solid (signed distance by winding number, marching cubes): closes anything, merges
    pieces, softens detail smaller than about 1/resolution of the model."""
    from atlas_toolkit.tools.mesh_holes.engine import closed_copy
    V2, F2 = closed_copy(np.asarray(V, float), np.asarray(F, np.int64), int(resolution), progress)
    return V2, F2, f"rebuilt by resampling at {int(resolution)} (detail finer than 1/{int(resolution)} is softened)"


# ----------------------------------------------------------------------------- pipelines

@dataclass
class Settings:
    goal: str = "solid"                 # solid | manifold
    strategy: str = "auto"              # auto (rebuild only if needed) | repair (never rebuild) | rebuild (always)
    weld_tol: float = 0.0               # model units; 0 = identical positions only
    sheets: bool = True                 # remove duplicate and back-to-back faces
    pieces: str = "crumbs"              # crumbs | largest | all
    crumb_share: float = CRUMB_SHARE
    nonmanifold: bool = True            # separate non-manifold edges and bow-tie vertices
    orient: bool = True                 # consistent winding, outward
    fill: str = "all"                   # all | small | none
    fill_max_edges: int = 200
    fill_shape: str = "auto"            # auto | flat | smooth
    resolution: int = 192


def _run(V, F, steps, log=None):
    notes = []
    for step in steps:
        V, F, note = step(V, F)
        if note:
            notes.append(note)
            if log:
                log(note)
    V, F = _compact(V, F)
    return V, F, notes


def _after_rebuild(V, F, faces_in: int, log=None):
    """Marching cubes leaves loose specks (inner cavities, crumbs), many more faces than the model had, and slivers
    where the surface grazes a grid cell. Drop the specks, bring the faces back to about the model's own count
    (quadric decimation keeps the shape) and tidy what the decimation leaves."""
    from .mesh import simplify_faces
    notes = []
    V, F, note = drop_pieces(V, F, "crumbs")
    if note:
        notes.append(note)
    target = max(int(faces_in), 5000)
    if len(F) > 1.3 * target:
        n0 = len(F)
        V, F = simplify_faces(V, F, target)
        V, F = np.asarray(V, float), np.asarray(F, np.int64)
        notes.append(f"brought the rebuilt surface from {_n(n0, 'face')} back to {len(F):,}".replace(",", " "))
    V, F, more = tidy_arrays(*_compact(V, F))
    notes += more
    V, F, more = _run(V, F, [orient_outward])
    notes += more
    if log:
        for n in notes:
            log(n)
    return V, F, notes


def tidy_arrays(V, F, log=None):
    """The safe pipeline: nothing visible moves. Removes zero-volume sheets, duplicates, degenerate faces and
    crumbs, separates non-manifold edges, winds faces consistently and outward, closes pin-holes."""
    steps = [lambda V, F: weld(V, F), drop_degenerate, drop_duplicate_sheets,
             lambda V, F: drop_pieces(V, F, "crumbs"), split_nonmanifold,
             lambda V, F: drop_pieces(V, F, "crumbs"),
             fix_winding, orient_outward,
             lambda V, F: fill_holes(V, F, TIDY_HOLE_EDGES, "flat"), split_nonmanifold, fix_winding, orient_outward]
    V, F, notes = _run(V, F, steps, log)
    for _ in range(2):                           # filling next to a split edge can need one more round
        if not len(F) or is_solid(V, F) or not notes:
            break
        n = len(F)
        V, F, more = _run(V, F, steps, log)
        notes += more
        if not more and len(F) == n:
            break
    return V, F, notes


def simplified_solid(V, F, max_faces: int, log=None):
    """A copy of a closed solid with about `max_faces` faces that is still a closed solid, or None. Quadric decimation
    keeps the shape but leaves back-to-back sheets and pinched edges where thin parts collapse (Venus de Milo's
    drapery, at any target): the safe tidy separates and removes them (split copies move 0.1 % of the way to the
    faces around them, and loose crumbs go). For tools that must
    work at a lower resolution than a dense input."""
    from .mesh import simplify_faces
    V, F = simplify_faces(V, F, max_faces, log)
    if is_solid(V, F):
        return V, F
    V, F, _ = tidy_arrays(V, F, log)
    return (V, F) if len(F) and is_solid(V, F) else None


def tidy(mesh, log=None):
    V, F = _arrays(mesh)
    V, F, notes = tidy_arrays(V, F, log)
    return _trimesh(V, F), notes


def repair_arrays(V, F, s: Settings | None = None, log=None, progress=None):
    """The full repair (the Repair Mesh tool): returns (V, F, notes, rebuilt)."""
    s = s or Settings()
    if s.strategy == "rebuild":
        V, F, notes = _run(V, F, [lambda V, F: weld(V, F, s.weld_tol), drop_degenerate], log)
        faces_in = len(F)
        V, F, note = rebuild(V, F, s.resolution, progress)
        notes.append(note)
        if log:
            log(note)
        V, F, more = _after_rebuild(V, F, faces_in, log)
        return V, F, notes + more, True
    steps = [lambda V, F: weld(V, F, s.weld_tol), drop_degenerate]
    if s.sheets:
        steps.append(drop_duplicate_sheets)
    steps.append(lambda V, F: drop_pieces(V, F, s.pieces, s.crumb_share))
    if s.nonmanifold:
        steps += [split_nonmanifold, lambda V, F: drop_pieces(V, F, "crumbs" if s.pieces != "all" else "all",
                                                              s.crumb_share)]
    if s.orient:
        steps.append(fix_winding)
    if s.fill != "none":
        limit = None if s.fill == "all" else int(s.fill_max_edges)
        steps.append(lambda V, F: fill_holes(V, F, limit, s.fill_shape))
        if s.nonmanifold:
            steps.append(split_nonmanifold)
    if s.orient:
        steps += [fix_winding, orient_outward]
    V, F, notes = _run(V, F, steps, log)
    # a split after filling can leave a small new opening (an unpaired face): go round again while that helps
    for _ in range(2):
        if not len(F) or (is_solid(V, F) if s.goal == "solid" else diagnose_arrays(V, F).manifold):
            break
        before = len(F)
        V, F, more = _run(V, F, steps, log)
        notes += more
        if not more and len(F) == before:
            break
    rebuilt = False
    if s.strategy == "auto" and s.goal == "solid" and not is_solid(V, F) and len(F):
        faces_in = len(F)
        V, F, note = rebuild(V, F, s.resolution, progress)
        notes.append(note)
        if log:
            log(note)
        V, F, more = _after_rebuild(V, F, faces_in, log)
        notes += more
        rebuilt = True
    return V, F, notes, rebuilt


def repair(mesh, s: Settings | None = None, log=None, progress=None):
    V, F = _arrays(mesh)
    V, F, notes, rebuilt = repair_arrays(V, F, s, log, progress)
    return _trimesh(V, F), notes, rebuilt


# ----------------------------------------------------------------------------- what the tools call

NEED_WORDS = {"solid": "a closed solid (watertight, its faces wound one way and facing out)",
              "manifold": "a clean (manifold) surface: openings are fine, but no edge shared by more than two faces, "
                          "no parts touching at a point, no flipped or degenerate faces"}


class NeedsRepair(ToolError):
    """The input is not what the tool needs, even after the safe tidy. The message says what is wrong and how to fix
    it: the Repair Mesh tool, or the tool's own repair option when it has one."""

    def __init__(self, diagnosis: Diagnosis, need: str = "solid", tool_option: str | None = None,
                 tool_name: str | None = None):
        self.diagnosis = diagnosis
        self.need = need
        self.tool_option = tool_option
        what = NEED_WORDS.get(need, need)
        found = diagnosis.summary()
        who = tool_name or "This tool"
        message = f"{who} needs {what}, and this model is not one: {found}."
        hint = (f"Fix it with {REPAIR_TOOL_NAME} ({REPAIR_TOOL_PLACE}): open this model there, run it with its "
                f"default settings, then use the repaired file here.")
        if tool_option:
            hint += f" Or turn on “{tool_option}” in this tool's settings (it can soften fine detail)."
        super().__init__(message, hint=hint, suggest_tool=REPAIR_TOOL)


def prepare(mesh, need: str = "solid", log=None, tool_option: str | None = None, tool_name: str | None = None,
            raise_error: bool = True):
    """What a tool that needs a solid (or a clean surface) does with its input: nothing when it is fine; otherwise
    the safe tidy (logged); then, if it still fails, NeedsRepair (or (mesh, notes, diagnosis) when raise_error is
    False, so the tool can try its own resampling). Returns (mesh, notes, diagnosis)."""
    V, F = _arrays(mesh)
    ok = is_solid(V, F) if need == "solid" else diagnose_arrays(V, F).manifold
    if ok:
        return mesh, [], None
    V2, F2, notes = tidy_arrays(V, F)
    d = diagnose_arrays(V2, F2)
    if d.ok_for(need):
        if log:
            for n in notes:
                log(f"tidied the input: {n}")
            log(f"Tip: run {REPAIR_TOOL_NAME} on this model once and save it, so every tool takes it as it is.")
        return _trimesh(V2, F2), notes, None
    if raise_error:
        raise NeedsRepair(diagnose_arrays(V, F), need, tool_option, tool_name)
    return mesh, [], diagnose_arrays(V, F)


def tidy_input(ctx, mesh, need: str = "solid", painted: bool = False):
    """The first thing a solid tool does with its input: when it is not what the tool needs, the safe tidy, said in
    the log and at the top of the report. A painted selection is tied to the exact faces, so then nothing changes
    (the tool raises NeedsRepair if it must)."""
    if painted:
        return mesh
    out, notes, _ = prepare(mesh, need, raise_error=False)
    if notes:
        ctx.note("Tidied the input before running (the surface is unchanged): " + "; ".join(notes)
                 + f". To skip this step next time, run {REPAIR_TOOL_NAME} on the model once and use its result.")
    return out


def needs_repair(mesh, tool, option: str | None = None, need: str = "solid", painted: bool = False) -> NeedsRepair:
    """The error a tool raises when its input is still not usable: what is wrong and what to do."""
    e = NeedsRepair(diagnose(mesh), need, option if not painted else None, getattr(tool, "name", None))
    if painted:
        e.hint = (f"Painting is tied to the model's exact faces, so the tool cannot repair it for you. Run "
                  f"{REPAIR_TOOL_NAME} ({REPAIR_TOOL_PLACE}) on this model, save it, then open the repaired file "
                  f"here and paint it again.")
    return e


def check_or_raise(mesh, need: str = "solid", tool_option: str | None = None, tool_name: str | None = None):
    """Raise NeedsRepair when the mesh is not what the tool needs (after its own repair, say)."""
    d = diagnose(mesh)
    if not d.ok_for(need):
        raise NeedsRepair(d, need, tool_option, tool_name)
    return d


def problem_segments(mesh, max_segments: int = 60000) -> np.ndarray:
    """Line segments (N, 2, 3) of the open, non-manifold and wrongly wound edges, to show where the problems are."""
    V, F = _arrays(mesh)
    if not len(F):
        return np.zeros((0, 2, 3), np.float32)
    he, face, eid, count = _edges(F)
    ec = count[eid]
    bad = (ec == 1) | (ec > 2)
    two = ec == 2
    if two.any():
        idx = np.nonzero(two)[0]
        order = idx[np.argsort(eid[idx], kind="stable")]
        a, b = order[0::2], order[1::2]
        same = he[a, 0] == he[b, 0]
        bad[a[same]] = True
    seg = he[bad]
    seg = np.unique(np.sort(seg, axis=1), axis=0)
    if len(seg) > max_segments:
        seg = seg[np.linspace(0, len(seg) - 1, max_segments, dtype=int)]
    return V[seg].astype(np.float32)


def load_raw(path):
    """A mesh as its file has it, only identical vertices merged: geom.mesh.load_mesh drops one face of each
    back-to-back pair, which hides the sheet from the repair (the other face stays, an open fin)."""
    from pathlib import Path

    import trimesh
    from .mesh import load_mesh
    obj = trimesh.load(str(path), force=None, process=False)
    if isinstance(obj, trimesh.Scene):
        obj = obj.to_mesh() if obj.geometry else None
    if not isinstance(obj, trimesh.Trimesh) or not len(obj.faces) or Path(path).suffix.lower() not in (
            ".stl", ".ply", ".obj", ".off", ".glb", ".gltf"):
        return load_mesh(path)
    V, F = _arrays(obj)
    if not np.isfinite(V).all():
        return load_mesh(path)
    V, F, _ = weld(V, F)
    V, F = _compact(V, F)
    return _trimesh(V, F)


def input_check(path, need: str = "solid", mesh=None) -> dict:
    """What a tool that needs a solid (or a clean surface) says about an input as soon as it is added:
    {"state": "ok" | "tidy" | "repair", "short": a few words for the summary line, "text": a sentence}."""
    from .mesh import load_mesh
    m = mesh if mesh is not None else load_mesh(path)
    V, F = _arrays(m)
    ok = is_solid(V, F) if need == "solid" else diagnose_arrays(V, F).manifold
    if ok:
        return {"state": "ok", "short": "", "text": ""}
    quick = beyond_tidy(V, F) if need == "solid" else None
    if quick is not None:                    # an opening the tidy cannot close: say so without tidying
        return {"state": "repair", "short": f"not a closed solid: needs {REPAIR_TOOL_NAME}",
                "text": f"This model is not a closed solid ({quick.summary()}). Run {REPAIR_TOOL_NAME} on it first, "
                        "then use the repaired file here."}
    V2, F2, notes = tidy_arrays(V, F)
    if diagnose_arrays(V2, F2).ok_for(need):
        return {"state": "tidy", "short": "small defects, tidied when run",
                "text": "Small defects (" + "; ".join(notes) + ") are tidied automatically when the tool runs; the "
                        f"surface does not change. {REPAIR_TOOL_NAME} can fix them once for every tool."}
    d = diagnose_arrays(V, F)
    what = "not a closed solid" if need == "solid" else "not a clean surface"
    return {"state": "repair", "short": f"{what}: needs {REPAIR_TOOL_NAME}",
            "text": f"This model is {what} ({d.summary()}). Run {REPAIR_TOOL_NAME} on it first, then use the "
                    "repaired file here."}
