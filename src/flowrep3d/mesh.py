"""Polygon-preserving loading, manifold topology, and cached remeshing."""
from __future__ import annotations

import hashlib
import json
import contextlib
import os
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from ._atlas.curvature import vertex_normals, curvature
from .types import FlowRepError, FlowRepOptions, PolygonMesh, checkpoint


@contextlib.contextmanager
def _native_stdout():
    """Keep the C++ binding's verbose progress out of CLI data and GUI pipes."""
    saved=None
    try:
        if sys.stdout is not None:
            sys.stdout.flush()
        saved=os.dup(1)
    except (OSError,ValueError):
        # Windowed/frozen hosts can have no stdout descriptor.
        yield
        return
    try:
        with open(os.devnull,"w",encoding="utf-8") as sink:
            os.dup2(sink.fileno(),1)
            yield
    finally:
        if saved is not None:
            os.dup2(saved,1)
            os.close(saved)


def unit(x):
    x = np.asarray(x, dtype=float)
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-30)


def triangulate(vertices, faces):
    try:
        from atlas_toolkit.geom.surface import triangulate as shared_triangulate
    except ImportError:
        return _standalone_triangulate(vertices, faces)
    return shared_triangulate(vertices, faces)


def _standalone_triangulate(vertices, faces):
    """Retain the standalone FlowRep reader when used without the Atlas package."""
    import mapbox_earcut
    triangles, owners = [], []
    for fi, face in enumerate(faces):
        face = np.asarray(face,dtype=np.int64)
        if len(face)==3:
            t=face.reshape(1,3)
        else:
            p=vertices[face]
            relative=p-p[0]
            n=np.sum(np.cross(relative,np.roll(relative,-1,axis=0)),axis=0)
            uv=np.delete(p,int(np.argmax(np.abs(n))),axis=1)
            idx=mapbox_earcut.triangulate_float64(np.ascontiguousarray(uv),np.array([len(p)],np.uint32))
            t=face[np.asarray(idx).reshape(-1,3)]
            if len(t) and np.dot(np.cross(vertices[t[0,1]]-vertices[t[0,0]],vertices[t[0,2]]-vertices[t[0,0]]),n)<0:
                t=t[:,::-1]
        triangles.extend(t.tolist());owners.extend([fi]*len(t))
    return np.asarray(triangles,np.int64).reshape(-1,3),np.asarray(owners,np.int64)


def _clean_orient(vertices, faces):
    V = np.asarray(vertices, np.float64).reshape(-1, 3)
    if not len(V) or not np.isfinite(V).all():
        raise FlowRepError("mesh vertices must be finite and nonempty")
    # Exact welding preserves deliberately nearby surfaces.
    V, inverse = np.unique(V, axis=0, return_inverse=True)
    cleaned, seen = [], set()
    diag = float(np.linalg.norm(np.ptp(V, axis=0)))
    if diag <= 0:
        raise FlowRepError("mesh has zero extent")
    for f in faces:
        f = np.asarray(f, np.int64)
        if np.any(f < 0) or np.any(f >= len(inverse)):
            raise FlowRepError("face references an invalid vertex")
        f = inverse[f]
        f = f[np.r_[True, f[1:] != f[:-1]]]
        if len(f) and f[0] == f[-1]:
            f = f[:-1]
        if len(np.unique(f)) < 3:
            continue
        if len(np.unique(f)) != len(f):
            raise FlowRepError("polygon repeats a non-consecutive vertex; split the polygon first")
        key = tuple(sorted(map(int, f)))
        if key in seen:
            continue
        p = V[f]
        area = np.linalg.norm(np.sum(np.cross(p - p[0], np.roll(p, -1, axis=0) - p[0]), axis=0))
        if area <= diag * diag * 1e-14:
            continue
        seen.add(key)
        cleaned.append(f)
    if not cleaned:
        raise FlowRepError("mesh has no usable polygon faces")
    used, inv = np.unique(np.concatenate(cleaned), return_inverse=True)
    lengths = [len(f) for f in cleaned]
    cuts = np.cumsum([0] + lengths)
    cleaned = [inv[cuts[i]:cuts[i+1]] for i in range(len(lengths))]
    V = V[used]
    occurrences = defaultdict(list)
    for fi, f in enumerate(cleaned):
        for a, b in zip(f, np.roll(f, -1)):
            occurrences[tuple(sorted((int(a), int(b))))].append((fi, int(a) < int(b)))
    bad = sum(len(o) > 2 for o in occurrences.values())
    if bad:
        raise FlowRepError(f"{bad} non-manifold edges remain; split or repair these edges before extraction")
    # Edge incidence alone misses two surface sheets touching at one vertex.
    # A manifold vertex has one connected link: a cycle, or an open chain.
    links=[[] for _ in V]; incident=[[] for _ in V]
    for fi,f in enumerate(cleaned):
        for j,v in enumerate(f):
            links[int(v)].append((int(f[j-1]),int(f[(j+1)%len(f)])))
            incident[int(v)].append(fi)
    bad=0; extra=[]; split_vertices=0
    for vertex,edges in enumerate(links):
        link=defaultdict(list)
        for a,b in edges:
            link[a].append(b); link[b].append(a)
        if any(len(neighbors)>2 for neighbors in link.values()):
            bad+=1; continue
        unseen=set(link); components=[]
        while unseen:
            stack=[min(unseen)]; visited=set()
            while stack:
                v=stack.pop()
                if v in visited: continue
                visited.add(v); stack.extend(link[v])
            unseen-=visited; components.append(visited)
        if len(components)>1:
            split_vertices+=1
            for component in components[1:]:
                replacement=len(V)+len(extra); extra.append(V[vertex].copy())
                for fi,(previous,following) in zip(incident[vertex],edges):
                    if following in component:
                        f=cleaned[fi]; j=int(np.flatnonzero(f==vertex)[0]); f[j]=replacement
    if bad:
        raise FlowRepError(f"{bad} non-manifold vertices remain; split the touching surface sheets before extraction")
    if extra:
        V=np.concatenate([V,np.asarray(extra)])
        occurrences=defaultdict(list)
        for fi,f in enumerate(cleaned):
            for a,b in zip(f,np.roll(f,-1)):
                occurrences[tuple(sorted((int(a),int(b))))].append((fi,int(a)<int(b)))
    adjacent = [[] for _ in cleaned]
    for o in occurrences.values():
        if len(o) == 2:
            (a, da), (b, db) = o
            adjacent[a].append((b, da == db))
            adjacent[b].append((a, da == db))
    flips = np.full(len(cleaned), -1, np.int8)
    for start in range(len(cleaned)):
        if flips[start] != -1:
            continue
        q, component = deque([start]), []
        flips[start] = 0
        while q:
            a = q.popleft()
            component.append(a)
            for b, difference in adjacent[a]:
                wanted = flips[a] ^ int(difference)
                if flips[b] == -1:
                    flips[b] = wanted
                    q.append(b)
                elif flips[b] != wanted:
                    raise FlowRepError("surface is not consistently orientable")
        for a in component:
            if flips[a]:
                cleaned[a] = cleaned[a][::-1]
        if all(len(adjacent[a]) == len(cleaned[a]) for a in component):
            volume = 0.0
            origin = V[cleaned[component[0]][0]]
            for a in component:
                p = V[cleaned[a]] - origin
                volume += sum(float(np.dot(p[0], np.cross(p[j], p[j+1]))) for j in range(1, len(p)-1))
            if volume < 0:
                for a in component:
                    cleaned[a] = cleaned[a][::-1]
    return V, tuple(cleaned), {"split_touching_vertices":split_vertices,"added_fan_vertices":len(extra)}


def from_arrays(vertices, faces, source="", metadata=None):
    V, polygons, repairs = _clean_orient(vertices, faces)
    T, owner = triangulate(V, polygons)
    return PolygonMesh(V, polygons, T, owner, V.copy(), T.copy(), str(source), (metadata or {})|{"topology_repairs":repairs})


def load_mesh(path) -> PolygonMesh:
    path = Path(path)
    if path.suffix.lower() in ('.ply', '.off'):
        try:
            from atlas_toolkit.geom.surface import load_surface
        except ImportError:
            pass  # standalone FlowRep keeps its original trimesh fallback
        else:
            surface = load_surface(path)
            return from_arrays(surface.vertices, surface.faces, path, {'format': path.suffix[1:]})
    if path.suffix.lower() == ".obj":
        V, F = [], []
        for number, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
            t = line.split("#", 1)[0].split()
            try:
                if t and t[0] == "v":
                    V.append([float(x) for x in t[1:4]])
                elif t and t[0] == "f":
                    ids = [int(x.split("/")[0]) for x in t[1:]]
                    if 0 in ids:
                        raise ValueError("OBJ indices are 1-based")
                    F.append([x - 1 if x > 0 else len(V) + x for x in ids])
            except (ValueError, IndexError) as exc:
                raise FlowRepError(f"{path.name}:{number}: malformed OBJ: {exc}") from exc
        return from_arrays(V, F, path, {"format": "obj"})
    import trimesh
    obj = trimesh.load(str(path), process=False)
    if isinstance(obj, trimesh.Scene):
        obj = obj.to_mesh()
    if not isinstance(obj, trimesh.Trimesh):
        raise FlowRepError(f"{path.name} does not contain a surface mesh")
    return from_arrays(obj.vertices, obj.faces, path, {"format": path.suffix[1:]})


@dataclass
class Topology:
    edges: np.ndarray
    edge_faces: np.ndarray
    face_edges: tuple
    vertex_edges: list
    vertex_faces: list
    normals: np.ndarray
    face_normals: np.ndarray
    lengths: np.ndarray
    opposite: np.ndarray
    dual_edges: np.ndarray
    dual_faces: np.ndarray
    he_vertices: np.ndarray
    he_next: np.ndarray
    he_twin: np.ndarray
    he_edge: np.ndarray
    he_face: np.ndarray
    components: int


def build_topology(mesh):
    V, F = mesh.vertices, mesh.faces
    edges, ef, lookup, face_edges = [], [], {}, []
    ve, vf = [[] for _ in V], [[] for _ in V]
    hv, hn, he, hf = [], [], [], []
    edge_he = []
    for fi, f in enumerate(F):
        fes = []
        offset = len(hv)
        for j, (a, b) in enumerate(zip(f, np.roll(f, -1))):
            a, b = int(a), int(b)
            key = tuple(sorted((a, b)))
            if key not in lookup:
                ei = len(edges)
                lookup[key] = ei
                edges.append(key)
                ef.append([-1, -1])
                edge_he.append([])
                ve[a].append(ei)
                ve[b].append(ei)
            ei = lookup[key]
            if ef[ei][0] < 0:
                ef[ei][0] = fi
            elif ef[ei][1] < 0:
                ef[ei][1] = fi
            else:
                raise FlowRepError("non-manifold polygon edge")
            fes.append(ei)
            vf[a].append(fi)
            edge_he[ei].append(len(hv))
            hv.append(a)
            hn.append(offset + (j+1) % len(f))
            he.append(ei)
            hf.append(fi)
        face_edges.append(np.asarray(fes, np.int64))
    E, EF = np.asarray(edges, np.int64), np.asarray(ef, np.int64)
    fn = unit(np.array([np.sum(np.cross(V[f] - V[f[0]], np.roll(V[f], -1, axis=0) - V[f[0]]), axis=0) for f in F]))
    normals = vertex_normals(V, mesh.triangles)
    opposite = [(int(fes[j]), int(fes[j+2])) for fes in face_edges if len(fes) == 4 for j in range(2)]
    de = np.flatnonzero(EF[:, 1] >= 0)
    D = EF[de]
    nc = connected_components(coo_matrix((np.ones(len(D)), (D[:,0], D[:,1])), shape=(len(F),len(F))).tocsr(),
                              directed=False, return_labels=False)
    twins = np.full(len(hv), -1, np.int64)
    for hs in edge_he:
        if len(hs) == 2:
            twins[hs[0]], twins[hs[1]] = hs[1], hs[0]
    return Topology(E, EF, tuple(face_edges), ve, vf, normals, fn,
                    np.linalg.norm(V[E[:,1]] - V[E[:,0]], axis=1),
                    np.asarray(opposite, np.int64).reshape(-1,2), de, D,
                    np.asarray(hv), np.asarray(hn), twins, np.asarray(he), np.asarray(hf), int(nc))


def prepare_mesh(mesh, options: FlowRepOptions, progress=None, cancel=None):
    checkpoint(cancel)
    quad_fraction = sum(len(f) == 4 for f in mesh.faces) / len(mesh.faces)
    if options.prepare == "preserve" or (options.prepare == "auto" and quad_fraction >= 0.5):
        mesh.metadata["preparation"] = "preserved"
        mesh.metadata["quad_fraction"] = quad_fraction
        return mesh
    import pynanoinstantmeshes as nim
    digest = hashlib.sha256()
    digest.update(mesh.vertices.tobytes())
    digest.update(mesh.triangles.tobytes())
    digest.update(f"nim-0.0.3:{options.target_faces}:{options.crease_angle}:normalized-purequad-v3".encode())
    key = digest.hexdigest()
    cache = Path(options.cache_dir) / f"remesh-{key}.npz" if options.cache_dir else None
    if cache and cache.exists():
        with np.load(cache, allow_pickle=False) as data:
            result = from_arrays(data["vertices"], data["faces"], mesh.source)
        cached = True
    else:
        if progress:
            progress(0.04, f"remeshing to approximately {options.target_faces} quads")
        checkpoint(cancel)
        # The binding always regular-subdivides its extracted polygons into pure
        # quads. Its nominal count therefore needs a factor-of-four correction.
        origin=(mesh.vertices.min(0)+mesh.vertices.max(0))/2
        scale=100.0/float(np.ptp(mesh.vertices,axis=0).max())
        with _native_stdout():
            v, f = nim.remesh(np.ascontiguousarray((mesh.vertices-origin)*scale, dtype=np.float32),
                              np.ascontiguousarray(mesh.triangles, dtype=np.uint32),
                              max(25,options.target_faces//4), rosy=4, posy=4, creaseAngle=options.crease_angle,
                              align_to_boundaries=True, extrinsic=True, smooth_iter=2, deterministic=True)
        checkpoint(cancel)
        v=np.asarray(v,dtype=np.float64)/scale+origin
        # NIM may encode triangles as quads with a repeated last corner.
        result = from_arrays(v, f, mesh.source)
        cached = False
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            if len({len(f) for f in result.faces}) == 1:
                from .io import atomic_npz
                atomic_npz(cache, vertices=result.vertices, faces=np.asarray(result.faces))
    result.reference_vertices = mesh.reference_vertices
    result.reference_triangles = mesh.reference_triangles
    result.metadata.update(mesh.metadata)
    result.metadata.update(preparation="instant-meshes", preparation_key=key, preparation_cached=cached,
                           target_faces=options.target_faces,
                           quad_fraction=sum(len(f) == 4 for f in result.faces) / len(result.faces))
    import trimesh
    ref = trimesh.Trimesh(result.reference_vertices, result.reference_triangles, process=False)
    ids = np.linspace(0, len(result.vertices)-1, min(2000, len(result.vertices)), dtype=int)
    _, distance, _ = trimesh.proximity.closest_point(ref, result.vertices[ids])
    result.metadata["remesh_surface_p95_relative"] = float(np.percentile(distance, 95) / mesh.diagonal)
    return result


def alignment_report(mesh):
    topo = mesh.topology
    try:
        c = curvature(mesh.vertices, mesh.triangles, topo.normals)
        a, b = topo.edges.T
        d = unit(mesh.vertices[b] - mesh.vertices[a])
        n = unit(topo.normals[a] + topo.normals[b])
        d = unit(d - (d*n).sum(1)[:,None]*n)
        p = unit(c["d1"][a] + np.sign((c["d1"][a]*c["d1"][b]).sum(1))[:,None]*c["d1"][b])
        other = unit(np.cross(n,p))
        cosine = np.maximum(np.abs((d*p).sum(1)), np.abs((d*other).sum(1)))
        anis = np.abs(np.abs(c["k1"]) - np.abs(c["k2"])) / np.maximum(np.abs(c["k1"]),1e-12)
        keep = (anis[a] + anis[b]) > 0.4
        angles = np.degrees(np.arccos(np.clip(cosine[keep],0,1)))
        return {"anisotropic_edges": int(keep.sum()), "alignment_median_deg": float(np.median(angles)) if len(angles) else 0.0,
                "alignment_p90_deg": float(np.percentile(angles,90)) if len(angles) else 0.0}
    except (ValueError, np.linalg.LinAlgError):
        return {"alignment_unavailable": True}
