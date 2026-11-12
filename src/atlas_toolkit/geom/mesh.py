"""
Shared mesh layer for the 3D tools (trimesh 5): loading and cleaning, light preview copies, signed saving and
inspection. Units are the model's own (whatever the STL was authored in); the tools say "model units".

Signed outputs: every mesh a tool writes carries "atlas-toolkit:<tool-id> <version>" in the binary STL header,
as an OBJ "#" comment or a PLY "comment" line, so the Atlas gallery can link a project's meshes to the tool.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
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


def load_mesh(path, merge: bool = True):
    """A single Trimesh from any file trimesh reads (scenes are flattened with their transforms), cleaned:
    merged vertices, no degenerate or duplicate faces, no unreferenced vertices."""
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


def clean(mesh, merge: bool = True):
    if merge:
        mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
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
        nv = np.zeros((len(uniq), 3))
        np.add.at(nv, inv, v)
        nv /= np.bincount(inv, minlength=len(uniq))[:, None]
        nf = inv[f]
        keep = (nf[:, 0] != nf[:, 1]) & (nf[:, 1] != nf[:, 2]) & (nf[:, 0] != nf[:, 2])
        nf = nf[keep]
        _, first = np.unique(np.sort(nf, axis=1), axis=0, return_index=True)
        nf = nf[np.sort(first)]
        if len(nf) <= max_faces:
            return nv.astype(np.float32), nf.astype(np.int32)
        res = int(res * 0.8)
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


def preview(mesh, max_faces: int = PREVIEW_FACES) -> MeshData:
    from .surface import Surface, from_mesh, polygon_data
    surface = mesh if isinstance(mesh, Surface) else from_mesh(mesh) if polygon_data(mesh) else None
    if surface is not None:
        tri = surface.as_trimesh()
        v, f = decimate(surface.vertices, surface.triangles, max_faces)
        edges = surface.edges
        if len(edges) > 160000:
            edges = edges[np.linspace(0, len(edges)-1, 160000, dtype=int)]
        return MeshData(v, f, len(surface.triangles), tuple(map(tuple, surface.bounds)),
                        surface.vertices[edges].astype(np.float32), len(surface.faces))
    v, f = decimate(mesh.vertices, mesh.faces, max_faces)
    b = mesh.bounds
    return MeshData(v, f, int(len(mesh.faces)), (tuple(map(float, b[0])), tuple(map(float, b[1]))))


def describe(mesh) -> tuple[str, dict]:
    ext = mesh.extents
    watertight = bool(mesh.is_watertight)
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
