"""Camera-independent stripe curves and solid, surface-following raised bands. No GUI."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np

from . import backend


def unit(a):
    a = np.asarray(a, float)
    return a / np.maximum(np.linalg.norm(a, axis=-1, keepdims=True), 1e-15)


def precision_mesh(vertices, faces, tolerance, *, preserve_shells=False):
    """Collapse near-coincident Boolean vertices by distance before float32 file readers weld them.

    Decimal rounding alone can leave the three sides of an intersection sliver in different grid cells.
    Proximity clusters remove the entire sliver together; the caller bounds tolerance by band detail.
    """
    import trimesh
    from scipy.spatial import cKDTree
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from atlas_toolkit.geom.mesh import clean
    V, F = np.asarray(vertices, float), np.asarray(faces, np.int64)
    pairs = cKDTree(V).query_pairs(tolerance, output_type="ndarray")
    if preserve_shells and len(pairs):
        # Separate ribbons can approach closer than the cleanup tolerance at branch points. Joining their
        # vertices creates pinched solids; only collapse vertices within the same existing closed shell.
        edges = np.vstack([F[:, :2], F[:, 1:]])
        graph = coo_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(len(V), len(V)))
        _, shells = connected_components(graph, directed=False)
        pairs = pairs[shells[pairs[:, 0]] == shells[pairs[:, 1]]]
    if len(pairs):
        graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(V), len(V)))
        n, labels = connected_components(graph, directed=False)
        merged = np.zeros((n, 3))
        np.add.at(merged, labels, V)
        merged /= np.bincount(labels)[:, None]
        V, F = merged, labels[F]
    return clean(trimesh.Trimesh(V, F, process=False))


def working_mesh(mesh, spacing, budget, check=lambda: None):
    """Conforming refinement; never modifies the source body or invents a new geometric surface."""
    import trimesh
    from atlas_toolkit.geom.mesh import simplify_faces
    from atlas_toolkit.tools.mesh_flow.mesh_flow import refine
    V, F = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    simplified = len(F) > budget
    if simplified:
        V, F = simplify_faces(V, F, max(1000, budget // 2))
    check()
    target = spacing / 3.
    passes = 0
    for _ in range(8):
        check()
        V, F, done = refine(V, F, target, budget, passes=1)
        passes += done
        if not done:
            break
    out = trimesh.Trimesh(V, F, process=False)
    if not out.is_watertight or not out.is_winding_consistent:
        raise ValueError("the working mesh lost manifold topology; increase the Field face budget")
    lengths = out.edges_unique_length
    return out, {"solve_faces": len(F), "refine_passes": passes, "simplified": simplified,
                 "underresolved_edges": int(np.count_nonzero(lengths > target * 1.001)),
                 "max_edge_spacing": float(lengths.max() / spacing)}


def curve_network(mesh, cache, *, spacing, budget, field, rotation, families, seed, check=lambda: None,
                  progress=lambda f, m: None):
    """The key intentionally excludes band width/relief and any downstream camera settings."""
    exe = backend.find_backend()
    if exe is None:
        raise RuntimeError("stripe backend missing: run native/build.py --only stripes with the Atlas Python, "
                           "or locate atlas-stripes-core in About > Setup")
    h = hashlib.sha256()
    h.update(np.asarray(mesh.vertices, dtype="<f8").tobytes())
    h.update(np.asarray(mesh.faces, dtype="<i8").tobytes())
    settings = dict(schema=2, spacing=spacing, budget=budget, field=field, rotation=rotation, families=families,
                    seed=seed, backend=backend.fingerprint(exe))
    h.update(json.dumps(settings, sort_keys=True).encode())
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (h.hexdigest() + ".json")
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            backend.validate(data)
            data["cache_hit"] = True
            return data
        except (ValueError, KeyError, TypeError, OSError):
            pass                      # a corrupt/interrupted cache never makes a job unusable
    work, diagnostics = working_mesh(mesh, spacing, budget, check)
    check()
    with tempfile.TemporaryDirectory(prefix="stripes-", dir=cache) as td:
        src, dst = Path(td)/"surface.obj", Path(td)/"curves.json"
        # Trimesh's OBJ writer retains 17 digits in our normalized coordinate frame.
        import trimesh
        src.write_text(trimesh.exchange.obj.export_obj(work, include_normals=False, digits=17), encoding="utf-8")
        data = backend.run_native(exe, src, dst, spacing=spacing, field=field, rotation=rotation,
                                  families=families, seed=seed, check=check, progress=progress)
        data["working_mesh"] = diagnostics
        data["cache_hit"] = False
        check()
        # Unique temporary name allows concurrent Atlas jobs to share the same cache safely.
        staged = Path(td)/"cache.json"
        staged.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        os.replace(staged, path)
    return data


def paths_from_network(data):
    import networkx as nx
    from atlas_toolkit.geom.polylines import trails
    for family in data["families"]:
        V = np.asarray(family["vertices"], float)
        graph = nx.Graph()
        graph.add_edges_from((int(a), int(b)) for a, b in family["edges"] if a != b)
        for path in trails(graph):
            P = V[path]
            keep = np.r_[True, np.linalg.norm(np.diff(P, axis=0), axis=1) > 1e-10]
            P = P[keep]
            if len(P) >= 2:
                yield int(family["id"]), P


def cut_and_raise(mesh, scalar, relief, max_faces=2000000, *, stripes_only=False, vertex_normals=None):
    """Partition every source triangle at scalar=0 and raise positive patches with shared boundary vertices.

    With stripes_only, gap faces are omitted and reversed band patches close the ribbon bottoms at the skin.
    Otherwise gap faces retain the exact source surface. Integer edge IDs stitch adjacent patches exactly.
    """
    import trimesh
    V = [p.copy() for p in np.asarray(mesh.vertices)]
    normals = [n.copy() for n in np.asarray(mesh.vertex_normals if vertex_normals is None else vertex_normals)]
    scalar = np.asarray(scalar, float)
    F = np.asarray(mesh.faces)
    positive = scalar >= 0.
    homogeneous = (positive[F] == positive[F[:, :1]]).all(axis=1)
    # Store (base vertex ids, band flag); height-dependent indices are assigned once all cuts are known.
    triangles = [tuple(map(int, f)) for f in F[homogeneous]]
    flags = positive[F[homogeneous, 0]].tolist()
    cuts = {}

    def crossing(a, b):
        key = (min(a, b), max(a, b))
        if key in cuts:
            return cuts[key]
        t = float(scalar[a]/(scalar[a]-scalar[b]))
        if t < 1e-10:
            index = a
        elif t > 1.-1e-10:
            index = b
        else:
            index = len(V)
            V.append((1-t)*V[a]+t*V[b])
            normals.append(unit((1-t)*normals[a]+t*normals[b]))
        cuts[key] = index
        return index

    for face in F[~homogeneous]:
        face = list(map(int, face))
        for band in (False, True):
            polygon = []
            for a, b in zip(face, face[1:]+face[:1]):
                ia, ib = bool(positive[a]) == band, bool(positive[b]) == band
                if ia:
                    polygon.append(a)
                if ia != ib:
                    polygon.append(crossing(a, b))
            polygon = list(dict.fromkeys(polygon))
            for k in range(1, len(polygon)-1):
                triangles.append((polygon[0], polygon[k], polygon[k+1]))
                flags.append(band)
    V, normals = np.asarray(V), np.asarray(normals)
    n = len(V)
    flags = np.asarray(flags, dtype=np.int64)
    base_faces = np.asarray(triangles, dtype=np.int64)
    top = base_faces+flags[:, None]*n
    # Every underlying edge has two incident faces. Different heights require a shoulder quad.
    directed = top[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
    edge_flags = np.repeat(flags, 3)
    keys = np.sort(directed % n, axis=1)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    if np.any(counts != 2):
        raise ValueError("stripe patch partition did not close; increase stripe spacing or field resolution")
    heights = np.bincount(inverse, weights=edge_flags)
    border = directed[(edge_flags == 1) & (heights[inverse] == 1)]
    a, b = border.T
    low_a, low_b = a % n, b % n
    walls = np.vstack([np.column_stack([a, low_a, low_b]), np.column_stack([a, low_b, b])])
    band_faces = base_faces[flags.astype(bool)]
    stripe_area = float(trimesh.triangles.area(V[band_faces]).sum())
    out_faces = (np.vstack([band_faces+n, band_faces[:, ::-1], walls]) if stripes_only else
                 np.vstack([top, walls]))
    if not len(band_faces) and stripes_only:
        raise ValueError("no stripe patches remain; adjust spacing or band width")
    if len(out_faces) > max_faces:
        raise ValueError("band face budget exceeded; increase spacing or Band face budget")
    out = trimesh.Trimesh(np.vstack([V, V+normals*relief]), out_faces, process=False)
    out.remove_unreferenced_vertices()
    if not out.is_watertight or not out.is_winding_consistent:
        raise ValueError("raised band patches did not form a closed solid")
    out.metadata.update(stripe_area=stripe_area, surface_area=float(mesh.area))
    return out


def raised_bands(mesh, data, width, relief, spacing, *, stripes_only=False, max_faces=2000000, check=lambda: None,
                 progress=lambda f, m: None):
    """Raise a shared surface partition of the periodic phase; return its solid boundary and diagnostics."""
    import manifold3d as m3d
    import trimesh
    from atlas_toolkit.tools.mesh_flow.mesh_flow import refine
    from atlas_toolkit.tools.mesh_ribbon.mesh_ribbon import Surface
    paths = list(paths_from_network(data))
    V, F = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    is_void = mesh.volume < 0
    if relief == 0:
        if stripes_only:
            raise ValueError("Export stripes only needs a positive Raised height for ribbon thickness")
        from atlas_toolkit.tools.mesh_holes.engine import solid
        return solid(V, F), {"curves": len(paths), "band_faces": 0, "chunks": 0, "underresolved_edges": 0,
                             "is_void": is_void, "stripe_area": 0., "surface_area": float(mesh.area)}
    target = min(width/3., spacing/12.)
    budget = max_faces//4
    if len(F) > budget:
        raise ValueError("source surface exceeds the band face budget; raise the budget or simplify the input")
    for _ in range(10):
        check()
        V, F, done = refine(V, F, target, budget, passes=1)
        if not done:
            break
    surface = trimesh.Trimesh(V, F, process=False)
    unresolved = int(np.count_nonzero(surface.edges_unique_length > target*1.001))
    progress(.25, f"Sampling stripe phase on {len(V)} surface vertices")
    field_V = np.asarray(data["mesh_vertices"], float)
    field_F = np.asarray(data["mesh_faces"], np.int64)
    projection = Surface(field_V, field_F)
    C, _, indices = projection.closest(V)
    bary = trimesh.triangles.points_to_barycentric(field_V[field_F[indices]], C)
    values = []
    for family in data["families"]:
        phase = np.asarray(family["corner_phase"], float)
        values.append(np.cos(np.sum(bary*phase[indices], axis=1)))
    # cos is invariant to the phase's 2pi shifts and sheet conjugations. A shared scalar at each source vertex
    # therefore keeps both sides of every edge consistent, including near branch faces.
    scalar = np.max(values, axis=0)-math.cos(math.pi*width/spacing)
    check()
    progress(.5, "Cutting and raising surface bands")
    normals = None
    if stripes_only:
        # Interpolate the original smooth normals. Recomputing them after subdivision would make each
        # original flat triangle imprint a normal change in the thin ribbons.
        original = Surface(mesh.vertices, mesh.faces)
        _, normals, _ = original.closest(V)
    out = cut_and_raise(surface, scalar, relief, max_faces, stripes_only=stripes_only, vertex_normals=normals)
    if is_void and not stripes_only:
        out.invert()       # positive solid representing the remaining cavity; subtracted by the adapter
    check()
    # The patches are already fused to the body by construction. Manifold validates and simplifies the solid.
    solid = m3d.Manifold(m3d.Mesh64(np.ascontiguousarray(out.vertices, dtype=np.float64),
                                   np.ascontiguousarray(out.faces, dtype=np.uint64)))
    if solid.status() != m3d.Error.NoError or solid.is_empty() or solid.volume() <= 0:
        raise ValueError("raised surface bands failed solid validation; reduce width or relief")
    progress(1., "Solid bands ready")
    return solid, {"curves": len(paths), "band_faces": len(out.faces), "chunks": 0,
                   "underresolved_edges": unresolved, "is_void": is_void and not stripes_only,
                   "stripe_area": out.metadata["stripe_area"], "surface_area": out.metadata["surface_area"]}

