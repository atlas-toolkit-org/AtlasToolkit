"""
Shared mesh layer for the 3D tools (trimesh 5): loading and cleaning, light preview copies, signed saving and
inspection. Units are the model's own (whatever the STL was authored in); the tools say "model units".

Signed outputs: every mesh a tool writes carries "atlas-toolkit:<tool-id> <version>" in the binary STL header,
as an OBJ "#" comment or a PLY "comment" line, so the Atlas gallery can link a project's meshes to the tool.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path

import numpy as np

PREVIEW_FACES = 80_000
MESH_EXTS = (".stl", ".obj", ".ply", ".glb", ".gltf", ".off")


@dataclass
class MeshData:
    """A light copy for the 3D viewer (picklable, ~1-2 MB)."""
    vertices: np.ndarray          # (N, 3) float32
    faces: np.ndarray             # (M, 3) int32
    faces_total: int              # faces in the full mesh
    bounds: tuple                 # ((x0, y0, z0), (x1, y1, z1)) of the full mesh
    edge_segments: np.ndarray | None = None  # native polygon edges, independent of the display triangle proxy
    native_faces_total: int | None = None
    marks: np.ndarray | None = None          # (N, 2, 3) segments drawn on top in the danger colour (problems)
    open_edges: np.ndarray | None = None     # (N, 2, 3) the full mesh's boundary edges (one face only), for the
                                             # viewer's Open edges overlay; None when not worked out


OPEN_EDGES_MAX_FACES = 3_000_000   # above this the preview skips the open-edge count (memory and time)
OPEN_EDGES_KEEP = 60_000           # segments kept for drawing; the count stays exact


def open_edge_segments(vertices, faces, keep: int = OPEN_EDGES_KEEP):
    """The edges that only one face uses (holes, open shells, cut faces), as (N, 2, 3) float32 segments with N at
    most `keep` (an even sample beyond it), and their full count. Edges are keyed by vertex index, so vertices must
    be merged (load_mesh does)."""
    f = np.asarray(faces, dtype=np.int64)
    if not len(f):
        return np.zeros((0, 2, 3), np.float32), 0
    e = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
    key = e[:, 0] * (int(e.max()) + 1) + e[:, 1]
    order = np.argsort(key, kind="stable")          # one sort; the edges used once, in key order
    sk = key[order]
    single = np.ones(len(sk), bool)
    same = sk[1:] == sk[:-1]
    single[1:] &= ~same
    single[:-1] &= ~same
    lone = e[order[single]]
    n = len(lone)
    if n > keep:
        lone = lone[np.linspace(0, n - 1, keep, dtype=int)]
    return np.asarray(vertices, dtype=np.float32)[lone], n


_REUSE: dict | None = None


@contextmanager
def reuse_loads():
    """Within the block, loading the same unchanged file again gives a copy of the first load instead of reading
    and cleaning it again (inspecting a file and checking it for a tool load it twice; a big scan takes seconds)."""
    global _REUSE
    outer, _REUSE = _REUSE, ({} if _REUSE is None else _REUSE)
    try:
        yield
    finally:
        _REUSE = outer


def load_mesh(path, merge: bool = True):
    """A single Trimesh from any file trimesh reads (scenes are flattened with their transforms), cleaned:
    merged vertices, no degenerate or duplicate faces, no unreferenced vertices."""
    if _REUSE is not None:
        try:
            st = Path(path).stat()
            key = (str(Path(path).resolve()), st.st_size, st.st_mtime_ns, merge)
        except OSError:
            key = None
        if key is not None:
            if key not in _REUSE:
                _REUSE.clear()
                _REUSE[key] = _load_mesh(path, merge)
            return _REUSE[key].copy()
    return _load_mesh(path, merge)


def _load_mesh(path, merge: bool = True):
    if Path(path).suffix.lower() in ('.obj', '.ply', '.off'):
        from .surface import load_surface
        surface = load_surface(path)
        if len(surface.faces) != len(surface.triangles):
            return surface.as_trimesh()
        # Triangle inputs retain the existing cleaning behavior.
        return clean(surface.as_trimesh(), merge)
    import trimesh
    obj = trimesh.load(str(path), force=None, process=False)
    if isinstance(obj, trimesh.Scene):
        if not obj.geometry:
            raise ValueError("the file holds no mesh")
        mesh = obj.to_mesh()
    else:
        mesh = obj
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"not a triangle mesh ({type(mesh).__name__})")
    if len(mesh.faces) == 0:
        raise ValueError("the mesh has no faces")
    return clean(mesh, merge)


FAST_MERGE_MIN = 200_000          # vertices; below it trimesh's own merge is as quick


def _row_hash(u: np.ndarray, primes) -> np.ndarray:
    h = np.zeros(len(u), np.uint64)
    for j in range(u.shape[1]):
        h ^= u[:, j] * np.uint64(primes[j])
        h = (h << np.uint64(29)) | (h >> np.uint64(35))
    return h


def first_rows(q: np.ndarray):
    """Unique rows of an integer (n, k) array (k <= 4) in order of first appearance: (index of each unique row's first
    occurrence, inverse), like trimesh.grouping.unique_rows(keep_order=True) but with one sort of a 64-bit row hash
    instead of two row sorts. Rows that share the hash are checked with a second, independent hash; on a collision it
    returns (None, None) and callers take the slow way."""
    q = np.ascontiguousarray(q, dtype=np.int64)
    if not len(q):
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    u = q.view(np.uint64)
    h = _row_hash(u, (0x9E3779B97F4A7C15, 0xC2B2AE3D27D4EB4F, 0x165667B19E3779F9, 0x27D4EB2F165667C5))
    order = np.argsort(h)
    hs = h[order]
    start = np.empty(len(q), bool)
    start[0] = True
    np.not_equal(hs[1:], hs[:-1], out=start[1:])
    del hs
    inner = np.flatnonzero(~start)
    if len(inner):
        g = _row_hash(u, (0xD6E8FEB86659FD93, 0xA0761D6478BD642F, 0xE7037ED1A0B428DB, 0x8EBC6AF09C88C6E3))[order]
        if (g[inner] != g[inner - 1]).any():
            return None, None
    starts = np.flatnonzero(start)
    firsts = np.minimum.reduceat(order, starts)
    rank = np.argsort(firsts)
    newid = np.empty(len(rank), np.int64)
    newid[rank] = np.arange(len(rank))
    inverse = np.empty(len(q), np.int64)
    inverse[order] = newid[np.cumsum(start) - 1]
    return firsts[rank], inverse


def merge_vertices(mesh) -> None:
    """trimesh's Trimesh.merge_vertices() with its defaults, the same result, about four times faster on large scans.
    Meshes with colours, UVs or vertex normals (which trimesh's merge also compares) and small ones go to trimesh."""
    V = mesh.vertices
    if len(V) < FAST_MERGE_MIN or not len(mesh.faces) or mesh.visual.defined \
            or np.shape(mesh._cache["vertex_normals"]) == np.shape(V):
        mesh.merge_vertices()
        return
    from trimesh import util
    from trimesh.constants import tol
    referenced = np.zeros(len(V), dtype=bool)
    referenced[mesh.faces] = True
    q = (np.asarray(V)[referenced] * (10 ** util.decimal_to_digits(tol.merge))).round().astype(np.int64)
    first, inv = first_rows(q)
    if first is None:                     # pragma: no cover (a 64-bit hash collision)
        mesh.merge_vertices()
        return
    inverse = np.zeros(len(V), dtype=np.int64)
    inverse[referenced] = inv
    mesh.update_vertices(mask=np.nonzero(referenced)[0][first], inverse=inverse)


def unique_faces(mesh) -> np.ndarray:
    """trimesh's Trimesh.unique_faces() (a mask of each face's first copy, whatever its winding), quicker on big
    meshes."""
    F = np.asarray(mesh.faces)
    if len(F) < FAST_MERGE_MIN:
        return mesh.unique_faces()
    first, _ = first_rows(np.sort(F, axis=1))
    if first is None:                     # pragma: no cover (a 64-bit hash collision)
        return mesh.unique_faces()
    mask = np.zeros(len(F), dtype=bool)
    mask[first] = True
    return mask


def clean(mesh, merge: bool = True):
    if merge:
        merge_vertices(mesh)
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(unique_faces(mesh))
    mesh.remove_unreferenced_vertices()
    if not np.isfinite(mesh.vertices).all():
        mesh.remove_infinite_values()
    return mesh


def decimate(vertices: np.ndarray, faces: np.ndarray, max_faces: int = PREVIEW_FACES) -> tuple:
    """Vertex clustering on a shrinking grid until the face count fits (like the Atlas live viewer). Keeps the
    silhouette, loses small detail; only for previews."""
    v = np.asarray(vertices, dtype=np.float64)
    f = np.asarray(faces, dtype=np.int64)
    if len(f) <= max_faces:
        return v.astype(np.float32), f.astype(np.int32)
    lo = v.min(0)
    ext = float(np.ptp(v, axis=0).max()) or 1.0
    res = int(np.sqrt(max_faces / 2.0) * 1.6)
    while res >= 8:
        q = np.floor((v - lo) / (ext / res)).astype(np.int64)
        key = (q[:, 0] * (res + 1) + q[:, 1]) * (res + 1) + q[:, 2]
        uniq, inv = np.unique(key, return_inverse=True)
        inv = inv.reshape(-1)
        nv = np.column_stack([np.bincount(inv, weights=v[:, j], minlength=len(uniq)) for j in range(3)])
        nv /= np.bincount(inv, minlength=len(uniq))[:, None]
        nf = inv[f]
        keep = (nf[:, 0] != nf[:, 1]) & (nf[:, 1] != nf[:, 2]) & (nf[:, 0] != nf[:, 2])
        nf = nf[keep]
        sf = np.sort(nf, axis=1)
        n = np.int64(len(uniq))
        if n < 2_000_000:                                   # one key per face: a 1-D unique, much quicker
            _, first = np.unique((sf[:, 0] * n + sf[:, 1]) * n + sf[:, 2], return_index=True)
        else:                                               # pragma: no cover (previews have far fewer cells)
            _, first = np.unique(sf, axis=0, return_index=True)
        nf = nf[np.sort(first)]
        if len(nf) <= max_faces:
            return nv.astype(np.float32), nf.astype(np.int32)
        # faces grow with the square of the grid: aim straight for the budget (at least the old 0.8 step)
        res = int(res * min(0.8, 0.97 * np.sqrt(max_faces / len(nf))))
    return v.astype(np.float32), f[:max_faces].astype(np.int32)


def simplify_faces(vertices: np.ndarray, faces: np.ndarray, max_faces: int, log=None) -> tuple:
    """A lighter copy that keeps the shape (quadric decimation; vertex clustering if that is unavailable), for
    quick previews of tools whose result depends on the surface. Returns (vertices float64, faces int64)."""
    if len(faces) <= max_faces:
        return np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int64)
    import trimesh
    try:
        m = trimesh.Trimesh(vertices, faces, process=False).simplify_quadric_decimation(face_count=int(max_faces))
        return np.asarray(m.vertices, dtype=np.float64), np.asarray(m.faces, dtype=np.int64)
    except Exception as e:                # pragma: no cover (fast_simplification is bundled)
        if log:
            log(f"decimation fell back to clustering ({type(e).__name__})")
        v, f = decimate(vertices, faces, max_faces)
        return v.astype(np.float64), f.astype(np.int64)


def _open_edges(vertices, faces):
    return open_edge_segments(vertices, faces)[0] if len(faces) <= OPEN_EDGES_MAX_FACES else None


def preview(mesh, max_faces: int = PREVIEW_FACES) -> MeshData:
    from .surface import Surface, from_mesh, polygon_data
    surface = mesh if isinstance(mesh, Surface) else from_mesh(mesh) if polygon_data(mesh) else None
    if surface is not None:
        v, f = decimate(surface.vertices, surface.triangles, max_faces)
        edges = surface.edges
        if len(edges) > 160000:
            edges = edges[np.linspace(0, len(edges)-1, 160000, dtype=int)]
        return MeshData(v, f, len(surface.triangles), tuple(map(tuple, surface.bounds)),
                        surface.vertices[edges].astype(np.float32), len(surface.faces),
                        open_edges=_open_edges(surface.vertices, surface.triangles))
    v, f = decimate(mesh.vertices, mesh.faces, max_faces)
    b = mesh.bounds
    return MeshData(v, f, int(len(mesh.faces)), (tuple(map(float, b[0])), tuple(map(float, b[1]))),
                    open_edges=_open_edges(mesh.vertices, mesh.faces))


def is_closed(faces) -> bool:
    """trimesh's is_watertight (every edge shared by exactly two faces) with one sort of the edge keys instead of a
    row grouping: the same answer, a few times sooner on big scans."""
    F = np.asarray(faces, dtype=np.int64)
    if not len(F):
        return False
    a, b = F.reshape(-1), F[:, [1, 2, 0]].reshape(-1)
    n = np.int64(int(F.max()) + 1)
    key = np.sort(np.minimum(a, b) * n + np.maximum(a, b))
    if len(key) % 2:
        return False
    pairs = key.reshape(-1, 2)
    return bool((pairs[:, 0] == pairs[:, 1]).all() and (pairs[1:, 0] != pairs[:-1, 1]).all())


def describe(mesh) -> tuple[str, dict]:
    ext = mesh.extents
    watertight = bool(mesh.is_watertight) if len(mesh.faces) < FAST_MERGE_MIN else is_closed(mesh.faces)
    values = {"faces": int(len(mesh.faces)), "vertices": int(len(mesh.vertices)),
              "extents": tuple(float(x) for x in ext), "max_dim": float(ext.max()), "min_dim": float(ext.min()),
              "diag": float(np.linalg.norm(ext)), "watertight": watertight,
              "volume": float(mesh.volume) if watertight else None,
              "bounds": tuple(tuple(float(x) for x in row) for row in mesh.bounds)}
    summary = (f"{values['faces']:,} faces · {ext[0]:.4g} × {ext[1]:.4g} × {ext[2]:.4g} units · "
               f"{'watertight' if watertight else 'open'}").replace(",", " ")
    from .surface import polygon_data
    polygons = polygon_data(mesh)
    if polygons:
        values['native_faces'] = len(polygons['faces'])
        summary = f"{values['native_faces']:,} native polygons · " + summary.replace(' faces', ' triangles', 1)
    return summary, values


def inspect(path: Path):
    """InputInfo for any mesh: size and faces, with a light copy as the 'Input' view."""
    from ..core.tool import InputInfo
    mesh = load_mesh(path)
    summary, values = describe(mesh)
    return InputInfo(str(path), "mesh", summary, values,
                     preview={"views": [{"title": "Input", "kind": "mesh", "mesh": preview(mesh)}]})


# ----------------------------------------------------------------------------- saving

def signature_text(tool) -> str:
    """'atlas-toolkit:<id> <version>' (the tool spec's signature carries the id)."""
    return f"{tool.signature} {tool.version}"


def save_mesh(mesh, dst: Path, tool) -> Path:
    """Write `mesh` in the format of dst's extension, signed for Atlas."""
    from .surface import Surface, polygon_data, from_mesh, save_surface
    dst = Path(dst)
    if isinstance(mesh, Surface) or polygon_data(mesh):
        save_surface(from_mesh(mesh), dst, tool)
        return dst
    ext = dst.suffix.lower()
    sig = signature_text(tool)
    if ext == ".stl":
        write_stl(dst, mesh.vertices, mesh.faces, sig)
    elif ext == ".obj":
        text = mesh.export(file_type="obj", include_normals=False, include_texture=False)
        dst.write_text(f"# {sig}\n{text}", encoding="utf-8")
    elif ext == ".ply":
        data = mesh.export(file_type="ply", encoding="binary")
        head, sep, rest = data.partition(b"\n")              # "ply"
        fmt, sep2, rest2 = rest.partition(b"\n")             # "format binary_little_endian 1.0"
        dst.write_bytes(head + sep + fmt + sep2 + f"comment {sig}\n".encode() + rest2)
    else:
        mesh.export(str(dst))
    return dst


def write_stl(path: Path, vertices: np.ndarray, faces: np.ndarray, header: str = "") -> None:
    """Binary STL with a text header (80 bytes), written with numpy in one go."""
    v = np.asarray(vertices, dtype=np.float64)
    f = np.asarray(faces, dtype=np.int64)
    tri = v[f]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(n, axis=1)
    ln[ln == 0] = 1.0
    n = n / ln[:, None]
    rec = np.zeros(len(f), dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")]))
    rec["n"] = n
    rec["v"] = tri
    hdr = header.encode("ascii", "replace")[:80].ljust(80, b" ")
    with open(path, "wb") as fh:
        fh.write(hdr)
        fh.write(struct.pack("<I", len(f)))
        fh.write(rec.tobytes())


def read_signature(path: Path) -> str | None:
    """The 'atlas-toolkit:<id> <version>' a tool wrote into a mesh, if any."""
    try:
        head = Path(path).read_bytes()[:4096]
    except OSError:
        return None
    i = head.find(b"atlas-toolkit:")
    if i < 0:
        return None
    end = i
    while end < len(head) and head[end] not in b"\r\n\x00" and end - i < 80:
        end += 1
    return head[i:end].decode("ascii", "replace").strip()


def result(ctx, tool, src_mesh, out_mesh, dst, stats: list | None = None, report: list | None = None,
           outputs: list | None = None, title: str = "Output"):
    """The usual Result of a mesh tool: save (signed) when dst is given, an Output view, faces and size stats."""
    from ..core.tool import Result, Stat
    written = list(outputs or [])
    if dst is not None:
        ctx.progress(0.96, "saving")
        save_mesh(out_mesh, dst, tool)
        written.insert(0, str(dst))
    ext = out_mesh.extents
    base = [Stat("faces", "Faces", int(len(out_mesh.faces)), before=int(len(src_mesh.faces))),
            Stat("size", "Size", f"{ext[0]:.4g} × {ext[1]:.4g} × {ext[2]:.4g}", fmt="{}")]
    lines = list(report or [])
    lines += [f"  faces      {len(src_mesh.faces):,} -> {len(out_mesh.faces):,}".replace(",", " "),
              f"  size       {ext[0]:.4g} x {ext[1]:.4g} x {ext[2]:.4g} units",
              f"  watertight {'yes' if out_mesh.is_watertight else 'no'}"]
    if written:
        lines.append(f"  saved as   {written[0]}")
        if len(written) > 1:
            lines.append(f"             + {len(written) - 1} more file(s)")
    ctx.progress(1.0)
    return Result(outputs=written, stats=list(stats or []) + base, report="\n".join(lines),
                  preview={"views": [{"title": title, "kind": "mesh", "mesh": preview(out_mesh)}], "prefer": title},
                  status="Saved" if written else "Previewed")
