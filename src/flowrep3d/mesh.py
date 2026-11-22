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


def _fill_loop(V, idx):
    """Atlas's hole patcher (earcut, or graded rings for big holes); a centroid fan when used standalone."""
    try:
        from atlas_toolkit.geom.repair import fill_loop
    except ImportError:
        fill_loop = None
    if fill_loop is not None:
        tris, extra = fill_loop(V, idx)
        if tris is not None:
            return [list(map(int, t)) for t in tris], np.asarray(extra, float).reshape(-1, 3)
    centre = len(V)
    return ([[int(idx[i]), int(idx[(i+1) % len(idx)]), centre] for i in range(len(idx))],
            V[idx].mean(0)[None, :])


def _heal_polygons(vertices, faces, reference_vertices=None, reference_triangles=None):
    """Repair the local artefacts Instant Meshes leaves in its quads before the strict checks run.

    Its extraction can weld two corners of a quad, leave near-duplicate vertices, fold a pair of quads onto each
    other or make a fin where four quads share an edge, and it leaves small holes in closed surfaces. Faces
    around such defects are removed, and every hole that is not a real opening of the reference surface is
    patched and laid back onto that surface. Returns (vertices, faces, report)."""
    from scipy.spatial import cKDTree
    V = np.asarray(vertices, np.float64).reshape(-1, 3)
    diag = float(np.linalg.norm(np.ptp(V, axis=0))) if len(V) else 0.0
    report = {"welded_vertices": 0, "dropped_polygons": 0, "removed_faces": 0, "holes_filled": 0, "holes_kept": 0}
    if not len(V) or diag <= 0:
        return V, [np.asarray(f, np.int64) for f in faces], report
    # 1. near-duplicate vertices
    pairs = cKDTree(V).query_pairs(diag * 1e-6, output_type="ndarray")
    if len(pairs):
        graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(V), len(V)))
        count, labels = connected_components(graph, directed=False)
        first = np.full(count, -1, np.int64)
        for v in range(len(V)):
            if first[labels[v]] < 0:
                first[labels[v]] = v
        remap = first[labels]
        report["welded_vertices"] = int(len(V) - count)
    else:
        remap = np.arange(len(V))
    # 2. corners repeated or collapsed
    polys = []
    for f in faces:
        f = [int(remap[int(v)]) for v in f]
        f = [v for i, v in enumerate(f) if v != f[i-1]] if len(f) > 1 else f
        if len(f) < 3 or len(set(f)) != len(f):
            report["dropped_polygons"] += 1
            continue
        polys.append(f)
    # 3. folds: every copy of a face with the same corners goes, both windings
    keys = defaultdict(list)
    for i, f in enumerate(polys):
        keys[tuple(sorted(f))].append(i)
    folded = {i for group in keys.values() if len(group) > 1 for i in group}
    report["dropped_polygons"] += len(folded)
    polys = [f for i, f in enumerate(polys) if i not in folded]
    # tiny faces would be dropped by the strict loader and leave holes; drop them here so the holes get patched
    keep = []
    for f in polys:
        p = V[f]
        area = np.linalg.norm(np.sum(np.cross(p - p[0], np.roll(p, -1, axis=0) - p[0]), axis=0))
        if area > diag * diag * 1e-14:
            keep.append(f)
        else:
            report["dropped_polygons"] += 1
    polys = keep
    # 4. fins and branching vertices: remove the faces around them until none are left
    for _ in range(6):
        edges = defaultdict(int)
        links = defaultdict(lambda: defaultdict(set))
        for f in polys:
            for j, v in enumerate(f):
                a, b = f[j-1], f[(j+1) % len(f)]
                edges[(min(v, b), max(v, b))] += 1
                links[v][a].add(b); links[v][b].add(a)
        bad = {v for (a, b), n in edges.items() if n > 2 for v in (a, b)}
        bad |= {v for v, link in links.items() if any(len(n) > 2 for n in link.values())}
        if not bad:
            break
        before = len(polys)
        polys = [f for f in polys if not bad.intersection(f)]
        report["removed_faces"] += before - len(polys)
    # 5. holes: patch every open loop that is not a real opening of the reference surface
    half = defaultdict(int)
    for f in polys:
        for a, b in zip(f, f[1:] + f[:1]):
            half[(min(a, b), max(a, b))] += 1
    open_he = [(a, b) for f in polys for a, b in zip(f, f[1:] + f[:1]) if half[(min(a, b), max(a, b))] == 1]
    if open_he:
        real = None
        lengths = [np.linalg.norm(V[a] - V[b]) for a, b in open_he]
        h = float(np.mean(lengths)) if lengths else 0.0
        if reference_vertices is not None and reference_triangles is not None and len(reference_triangles):
            T = np.asarray(reference_triangles, np.int64)
            E = np.sort(np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), axis=1)
            unique_edges, counts = np.unique(E, axis=0, return_counts=True)
            rim = np.unique(unique_edges[counts == 1])
            if len(rim):
                real = cKDTree(np.asarray(reference_vertices, float)[rim])
        loops = _open_loops(open_he)
        new_vertices = []
        for loop in loops:
            if loop[-1] is None or len(loop) < 3:
                report["holes_kept"] += 1
                continue
            idx = np.asarray(loop, np.int64)
            if real is not None and float(real.query(V[idx])[0].min()) <= max(h, diag * 1e-6):
                report["holes_kept"] += 1            # an opening of the original surface stays a boundary
                continue
            base = len(V) + sum(len(x) for x in new_vertices)
            tris, extra = _fill_loop(np.concatenate([V, *new_vertices]) if new_vertices else V, idx)
            if len(extra):
                # the patcher numbers new vertices from the end of the array it was given
                new_vertices.append(extra)
            polys.extend(tris)
            report["holes_filled"] += 1
        if new_vertices:
            added = np.concatenate(new_vertices)
            if reference_vertices is not None and reference_triangles is not None and len(reference_triangles):
                import trimesh
                surface = trimesh.Trimesh(np.asarray(reference_vertices, float), np.asarray(reference_triangles),
                                          process=False)
                added, _, _ = trimesh.proximity.closest_point(surface, added)
            V = np.concatenate([V, added])
    return V, [np.asarray(f, np.int64) for f in polys], report


def _open_loops(half_edges):
    """Open half-edges (a, b) as loops in patch order (b to a), each ending in None when it does not close."""
    try:
        from atlas_toolkit.geom.repair import boundary_loops
    except ImportError:
        boundary_loops = None
    if boundary_loops is not None:
        return boundary_loops(half_edges)
    out = defaultdict(list)
    for a, b in half_edges:
        out[b].append(a)
    loops = []
    for start in list(out):
        while out.get(start):
            loop = [start]; current = out[start].pop()
            while current != start:
                loop.append(current)
                following = out.get(current)
                if not following or len(loop) > len(half_edges) + 1:
                    loop.append(None); break
                current = following.pop()
            loops.append(loop)
    return loops


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


REMESH_REVISION = "normalized-purequad-v4-healed"   # changes every remesh cache key (extract.py uses it too)
REMESH_RETRIES = (1.0, 1.04, 0.96)


def _remesh(nim, mesh, options, target):
    """One Instant Meshes run at about `target` quads, healed and loaded; FlowRepError when it stays unusable."""
    # The binding always regular-subdivides its extracted polygons into pure
    # quads. Its nominal count therefore needs a factor-of-four correction.
    origin = (mesh.vertices.min(0) + mesh.vertices.max(0)) / 2
    scale = 100.0 / float(np.ptp(mesh.vertices, axis=0).max())
    with _native_stdout():
        v, f = nim.remesh(np.ascontiguousarray((mesh.vertices - origin) * scale, dtype=np.float32),
                          np.ascontiguousarray(mesh.triangles, dtype=np.uint32),
                          max(25, target // 4), rosy=4, posy=4, creaseAngle=options.crease_angle,
                          align_to_boundaries=True, extrinsic=True, smooth_iter=2, deterministic=True)
    v = np.asarray(v, dtype=np.float64) / scale + origin
    # NIM may encode triangles as quads with a repeated last corner; the healing drops it.
    V, faces, repairs = _heal_polygons(v, np.asarray(f, np.int64), mesh.reference_vertices, mesh.reference_triangles)
    return from_arrays(V, faces, mesh.source), repairs


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
    digest.update(f"nim-0.0.3:{options.target_faces}:{options.crease_angle}:{REMESH_REVISION}".encode())
    key = digest.hexdigest()
    cache = Path(options.cache_dir) / f"remesh-{key}.npz" if options.cache_dir else None
    if cache and cache.exists():
        with np.load(cache, allow_pickle=False) as data:
            sizes = data["face_sizes"]
            faces = np.split(data["face_indices"], np.cumsum(sizes)[:-1])
            result = from_arrays(data["vertices"], faces, mesh.source)
            repairs = json.loads(str(data["repairs"]))
        cached = True
    else:
        if progress:
            progress(0.04, f"remeshing to approximately {options.target_faces} quads")
        result = repairs = None; failure = None
        # Instant Meshes' defects depend on the resolution: when one cannot be healed, a slightly different
        # target usually extracts cleanly.
        for attempt, factor in enumerate(REMESH_RETRIES, 1):
            checkpoint(cancel)
            target = max(100, int(round(options.target_faces * factor)))
            try:
                result, repairs = _remesh(nim, mesh, options, target)
            except FlowRepError as exc:
                failure = exc
                continue
            repairs.update(remesh_attempts=attempt, remesh_target=target)
            break
        if result is None:
            raise FlowRepError(f"Instant Meshes produced a surface FlowRep cannot use at this resolution ({failure}); "
                               "try another Remesh resolution, or run Repair Mesh on the model first")
        cached = False
        if cache:
            from .io import atomic_npz
            atomic_npz(cache, vertices=result.vertices, face_sizes=np.asarray([len(f) for f in result.faces], np.int64),
                       face_indices=np.concatenate(result.faces).astype(np.int64), repairs=np.array(json.dumps(repairs)))
    result.reference_vertices = mesh.reference_vertices
    result.reference_triangles = mesh.reference_triangles
    result.metadata.update(mesh.metadata)
    result.metadata.update(preparation="instant-meshes", preparation_key=key, preparation_cached=cached,
                           target_faces=options.target_faces, remesh_repairs=repairs,
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
