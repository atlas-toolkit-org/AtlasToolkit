"""Camera-independent developable approximation. No GUI imports."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

import numpy as np
from . import backend


def load_input(path):
    from atlas_toolkit.geom import mesh as gm
    mesh = gm.load_mesh(path)
    mesh.merge_vertices(merge_tex=True, merge_norm=True)
    mesh.remove_unreferenced_vertices()
    return mesh


def validate_mesh(mesh):
    import igl
    v, f = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    if not len(f) or not len(v) or not np.isfinite(v).all():
        raise ValueError("Mesh must contain finite vertices and triangles.")
    if f.min() < 0 or f.max() >= len(v) or not mesh.nondegenerate_faces().all():
        raise ValueError("Mesh contains invalid or degenerate triangles; repair it before developing.")
    counts = np.bincount(mesh.edges_unique_inverse)
    if np.any(counts > 2) or not np.all(igl.is_vertex_manifold(np.asarray(f, dtype=np.int64))):
        raise ValueError("Non-manifold mesh: repair edges and bow-tie vertices before developing. Open boundaries are supported.")
    if not mesh.is_winding_consistent:
        raise ValueError("Inconsistent face winding: orient the surface consistently before developing.")
    return mesh


def boundary_info(mesh):
    counts = np.bincount(mesh.edges_unique_inverse)
    edges = mesh.edges_unique[counts == 1]
    if not len(edges):
        return np.empty(0, dtype=int), 0
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    ids = np.unique(edges)
    mapped = np.searchsorted(ids, edges)
    graph = coo_matrix((np.ones(len(edges)), (mapped[:, 0], mapped[:, 1])), shape=(len(ids), len(ids)))
    loops, _ = connected_components(graph, directed=False)
    return ids, loops


def signature(mesh):
    return len(mesh.split(only_watertight=False, repair=False)), mesh.euler_number, boundary_info(mesh)[1]


def prepare(mesh, method, target, check=lambda: None):
    import trimesh
    validate_mesh(mesh)
    if method == "keep":
        return mesh.copy()
    import pynanoinstantmeshes as nim
    check()
    v, f = nim.remesh(np.ascontiguousarray(mesh.vertices, dtype=np.float32),
                      np.ascontiguousarray(mesh.faces, dtype=np.uint32), max(4, target // 2),
                      rosy=6, posy=3, creaseAngle=45., align_to_boundaries=True,
                      extrinsic=True, deterministic=True)
    check()
    # Never merge coincident vertices here: that could weld intentional boundaries.
    out = trimesh.Trimesh(np.array(v, dtype=float), np.array(f, dtype=np.int64), process=False)
    try:
        validate_mesh(out)
        if signature(out) != signature(mesh) or out.area_faces.min() < 5e-13:
            raise ValueError("remeshing changed topology or produced sliver triangles")
        out.metadata['preparation'] = 'field-aligned triangles'
    except ValueError as error:
        # NIM may introduce non-manifold vertices on otherwise valid scans. Never repair by
        # deleting faces or filling openings. A checked edge-collapse copy is a safe alternative.
        import igl
        v, f, _, _ = igl.decimate(np.asarray(mesh.vertices, dtype=float),
                                  np.asarray(mesh.faces, dtype=np.int32), min(target, len(mesh.faces)))
        out = trimesh.Trimesh(v, f, process=False)
        try:
            validate_mesh(out)
            if signature(out) != signature(mesh) or out.area_faces.min() < 5e-13:
                raise ValueError("coarsening changed topology or produced slivers")
        except ValueError as fallback:
            raise ValueError("Surface preparation failed. Increase Initial face target or use Keep input triangulation. "
                             + str(fallback)) from error
        out.metadata['preparation'] = 'checked edge-collapse fallback (field remesh invalid)'
    return out



def develop(mesh, cache, settings, *, check=lambda: None, progress=lambda f, m: None):
    import trimesh
    validate_mesh(mesh)
    exe = backend.find_backend()
    if exe is None:
        raise RuntimeError("Developable backend missing: build it with native/build.py --only developable or locate atlas-developable-core in About > Setup.")
    effective = dict(settings)
    h = hashlib.sha256(b"atlas-developable-2")
    h.update(np.asarray(mesh.vertices, dtype='<f8').tobytes())
    h.update(np.asarray(mesh.faces, dtype='<i8').tobytes())
    h.update(json.dumps(effective, sort_keys=True).encode())
    h.update(backend.fingerprint(exe).encode())
    cache = Path(cache); cache.mkdir(parents=True, exist_ok=True)
    cached = cache/(h.hexdigest()+".npz")
    check()
    if cached.is_file():
        try:
            with np.load(cached, allow_pickle=False) as z:
                out = validate_mesh(trimesh.Trimesh(z['vertices'].copy(), z['faces'].copy(), process=False))
                report = backend.validate(json.loads(str(z['report'])))
            if signature(out) != signature(mesh) or len(out.faces) > effective['max_faces']:
                raise ValueError("cached topology mismatch")
            if (report.get('settings') != effective or report.get('backend') != backend.fingerprint(exe) or
                    not all(np.isfinite(report[k]) for k in ('displacement_rms','displacement_max')) or
                    len(report['levels']) != report['components']*(effective['subdivisions']+1)):
                raise ValueError("cached diagnostics mismatch")
            report['cache_hit'] = True
            return out, report
        except (OSError, ValueError, KeyError, EOFError, TypeError, AttributeError, zipfile.BadZipFile):
            pass
    components = list(mesh.split(only_watertight=False, repair=False))
    if settings['preparation'] == 'remesh' and settings['target_faces'] < 20*len(components):
        raise ValueError("Initial face target is too small for all components; increase it.")
    results, reports = [], []
    total_faces = 0
    with tempfile.TemporaryDirectory(prefix="developable-", dir=cache) as td:
        td = Path(td)
        for i, component in enumerate(components):
            check()
            center = component.bounds.mean(axis=0)
            scale = float(np.linalg.norm(component.extents))
            if not np.isfinite(scale) or scale <= 0:
                raise ValueError("Component has zero extent.")
            normal = trimesh.Trimesh((component.vertices-center)/scale, component.faces.copy(), process=False)
            target = max(20, int(settings['target_faces']*len(component.faces)/len(mesh.faces)))
            base = prepare(normal, settings['preparation'], target, check)
            check()
            required = len(base.faces)*4**settings['subdivisions']
            if total_faces+required > settings['max_faces']:
                raise ValueError("Refinement exceeds Face safety limit. Reduce the initial target or refinement levels.")
            total_faces += required
            progress(i/len(components), base.metadata.get('preparation', 'kept input triangulation'))
            source, output, config, report_path = (td/f"{i}.{ext}" for ext in ('input.obj','output.obj','json','report.json'))
            # Native IO stays in double precision; trimesh's default OBJ precision is insufficient for gradient checks.
            source.write_text(trimesh.exchange.obj.export_obj(base, digits=17, include_normals=False), encoding='utf-8')
            config.write_text(json.dumps(settings), encoding='utf-8')
            report = backend.run_native(exe, source, config, output, report_path, check=check,
                                       progress=lambda f, m: progress((i+f)/len(components), m))
            report['preparation'] = base.metadata.get('preparation', 'kept input triangulation')
            out = trimesh.load_mesh(output, process=False)
            validate_mesh(out)
            if signature(out) != signature(base) or len(out.faces) != required:
                raise ValueError("Native solver changed surface topology unexpectedly.")
            # Subdivision leaves old vertex indices intact. Every boundary, including new vertices, is fixed.
            bid, _ = boundary_info(base)
            if len(bid) and not np.allclose(out.vertices[bid], base.vertices[bid], atol=1e-12, rtol=0):
                raise ValueError("Native solver moved a fixed boundary.")
            out.vertices = out.vertices*scale+center
            results.append(out); reports.append(report)
        out = validate_mesh(trimesh.util.concatenate(results))
        # Report one-way sampled surface displacement in source units, rather than comparing vertex indices.
        ids = np.linspace(0, len(out.vertices)-1, min(2000, len(out.vertices)), dtype=int)
        _, distances, _ = trimesh.proximity.closest_point(mesh, out.vertices[ids])
        report = {'schema': 1, 'version': '0.1.0', 'levels': [dict(l, component=i) for i, r in enumerate(reports) for l in r['levels']],
                  'components': len(components), 'faces': len(out.faces), 'cache_hit': False,
                  'displacement_rms': float(np.sqrt(np.mean(distances**2))), 'displacement_max': float(distances.max()),
                  'preparations': [r['preparation'] for r in reports], 'settings': effective, 'backend': backend.fingerprint(exe)}
        staged = td/'result.npz'
        np.savez_compressed(staged, vertices=out.vertices, faces=out.faces, report=json.dumps(report))
        check(); os.replace(staged, cached)
    return out, report
