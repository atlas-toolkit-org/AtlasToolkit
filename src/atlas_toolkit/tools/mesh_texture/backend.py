"""Surface charts for Follow surface: the pinned native xatlas helper (protocol 3), a simplified proxy for dense models
and a cancellable, content-addressed cache.

xatlas keeps every chart's stretch within about 10%, so on a scan it cuts the skin wherever the normals are noisy:
1,124 charts on the 260,000-face catalogue David head, smaller than one texture element, and every seam interrupts the
motifs. Atlas charts a proxy instead: the surface simplified to PROXY_FACES faces and smoothed (Taubin, which keeps
its size), so the charts follow the form rather than the noise (tens of charts, in about a second). Each source face
then takes the chart of the nearest proxy face facing the same way, and each corner the UV of its nearest point on
that chart: inside a chart, UVs stay continuous. `Charts.locate` gives any point's UV in any chart, for the seam
cross-fade.
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import queue
import struct
import subprocess
import tempfile
import threading
import numpy as np

PROTOCOL = 3
PROXY_FACES = 8000        # the charted copy: enough for the form, too coarse for scan noise
PROXY_SMOOTHING = 40      # Taubin iterations on the copy
TRANSFER_REVISION = 2
# Few, large charts (xatlas's defaults: maxCost 2, normalDeviationWeight 2, normalSeamWeight 4, roundness 0.01,
# straightness 6, 1 iteration). Tuned on scripts/dev/validate_surface_texture.py: seams against stretch.
CHART_OPTIONS = dict(max_cost=8.0, normal_deviation_weight=1.0, normal_seam_weight=4.0, roundness_weight=0.01,
                     straightness_weight=6.0, max_iterations=2)


def find_backend():
    from atlas_toolkit.core.runtimes import find_helper
    return find_helper('atlas-texture-core', 'ATLAS_TOOLKIT_TEXTURE', 'texture_exe')


def cache_root():
    from atlas_toolkit.core.paths import cache_dir
    return cache_dir()/'surface-texture-v3'


@dataclass
class Charts:
    uv: np.ndarray            # (F, 3, 2) per face corner, in model units
    chart: np.ndarray         # (F,) chart of each face
    cached: bool
    proxy_v: np.ndarray       # the surface xatlas charted (the source itself when small enough)
    proxy_f: np.ndarray
    proxy_uv: np.ndarray
    proxy_chart: np.ndarray

    def locate(self, points, charts, normals=None):
        """UV of each point (k, 3) in the given chart (k,): projected on that chart's nearest face (facing the same
        way as `normals`, where given)."""
        points = np.asarray(points, float).reshape(-1, 3)
        charts = np.asarray(charts, np.int64).reshape(-1)
        out = np.zeros((len(points), 2))
        for c in np.unique(charts):
            sel = np.flatnonzero(charts == c)
            cand = np.flatnonzero(self.proxy_chart == c)
            if not len(cand):
                continue
            face = _closest(self.proxy_v, self.proxy_f, cand, points[sel], None if normals is None else normals[sel])
            out[sel] = _affine(points[sel], self.proxy_v[self.proxy_f[face]], self.proxy_uv[face])
        return out


def unwrap(vertices, faces, ctx):
    """(uv, chart, cached) for the surface; see `charts`."""
    found = charts(vertices, faces, ctx)
    return found.uv, found.chart, found.cached


def charts(vertices, faces, ctx, options=None):
    exe = find_backend()
    if exe is None:
        raise ValueError('Automatic wrapping needs bundled atlas-texture-core; rebuild it with native/build.py --only texture or choose cylindrical/triplanar wrapping.')
    from atlas_toolkit.core.runtimes import check_helper
    check_helper(exe, 'atlas-texture-core', PROTOCOL)
    options = dict(CHART_OPTIONS, **(options or {}))
    vertices = np.asarray(vertices, dtype='<f8')
    faces = np.asarray(faces, dtype='<u4')
    if len(vertices) > 2000000 or len(faces) > 2000000:
        raise ValueError('Automatic unwrapping supports up to two million source faces/vertices. Use Remesh/Decimate first.')
    identity = json.dumps([TRANSFER_REVISION, PROXY_FACES, PROXY_SMOOTHING, sorted(options.items())]).encode()
    key = hashlib.sha256(vertices.tobytes()+faces.tobytes()+Path(exe).read_bytes()+identity).hexdigest()
    cache = cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    path = cache/(key+'.npz')
    names = ('uv', 'chart', 'proxy_v', 'proxy_f', 'proxy_uv', 'proxy_chart')
    if path.exists():
        try:
            with np.load(path, allow_pickle=False) as data:
                found = {k: data[k] for k in names}
            if found['uv'].shape == (len(faces), 3, 2) and found['chart'].shape == (len(faces),):
                ctx.log('Reusing cached surface charts')
                return Charts(cached=True, **found)
        except (OSError, ValueError, KeyError, EOFError):
            pass
    V = np.asarray(vertices, np.float64)
    F = np.asarray(faces, np.int64)
    pv, pf = _proxy(V, F, ctx)
    puv, pchart = _run(exe, pv, pf, options, ctx)
    valid = pchart >= 0
    if not valid.any():
        raise ValueError('xatlas could not chart this surface; choose triplanar wrapping.')
    # Translation does not affect physical tile size, and avoids unstable chart packing offsets changing texture
    # phase from one part to another.
    lowest = np.full((int(pchart.max())+1, 2), np.inf)
    np.minimum.at(lowest, np.repeat(pchart[valid], 3), puv[valid].reshape(-1, 2))
    puv = puv.copy()
    puv[valid] -= lowest[pchart[valid]][:, None, :]
    ctx.progress(.19, 'Transferring surface charts')
    uv, chart = _transfer(V, F, pv, pf, puv, pchart, ctx)
    found = dict(uv=uv, chart=chart, proxy_v=np.asarray(pv, np.float64), proxy_f=np.asarray(pf, np.int64),
                 proxy_uv=puv, proxy_chart=pchart)
    with tempfile.NamedTemporaryFile(dir=cache, suffix='.npz', delete=False) as staged:
        staging = Path(staged.name)
        np.savez_compressed(staged, **found)
    try:
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)
    return Charts(cached=False, **found)


def _proxy(V, F, ctx):
    """The surface xatlas charts: simplified to PROXY_FACES faces and smoothed, so its charts follow the form."""
    import trimesh
    from atlas_toolkit.geom.mesh import simplify_faces
    if len(F) > PROXY_FACES:
        ctx.progress(.05, 'Simplifying a copy for surface charts')
        pv, pf = simplify_faces(V, F, PROXY_FACES, ctx.log)
    else:
        pv, pf = V.copy(), F.copy()
    if PROXY_SMOOTHING:
        mesh = trimesh.Trimesh(pv, pf, process=False)
        trimesh.smoothing.filter_taubin(mesh, iterations=PROXY_SMOOTHING)
        pv = np.asarray(mesh.vertices, np.float64)
    return pv, np.asarray(pf, np.int64)


def _closest(pv, pf, cand, points, normals=None, k=8, batch=32768):
    """Index (into pf) of the nearest face among `cand` for each point: the k faces with the nearest centres, then the
    exact closest point on each. With `normals`, faces turned away from the point's normal only count when none of
    the k faces is turned towards it (the other side of an ear is not the nearest skin)."""
    import trimesh
    from scipy.spatial import cKDTree
    tri_all = pv[pf[cand]]
    centres = tri_all.mean(axis=1)
    facing = np.cross(tri_all[:, 1]-tri_all[:, 0], tri_all[:, 2]-tri_all[:, 0])
    tree = cKDTree(centres)
    k = min(k, len(cand))
    out = np.empty(len(points), np.int64)
    for s in range(0, len(points), batch):
        p = points[s:s+batch]
        _, j = tree.query(p, k=k)
        j = j.reshape(len(p), k)
        tri = tri_all[j].reshape(-1, 3, 3)
        near = trimesh.triangles.closest_point(tri, np.repeat(p, k, axis=0)).reshape(len(p), k, 3)
        dist = np.linalg.norm(near-p[:, None], axis=2)
        if normals is not None:
            away = np.einsum('qkd,qd->qk', facing[j], normals[s:s+batch]) <= 0
            dist = np.where(away & ~away.all(axis=1, keepdims=True), np.inf, dist)
        best = np.argmin(dist, axis=1)
        out[s:s+batch] = cand[j[np.arange(len(p)), best]]
    return out


def _affine(points, tri, tuv):
    """Points (k, 3) in each triangle's own UV map: barycentric coordinates of their projection on its plane, not
    clamped, so a point just past a chart's edge continues its map."""
    a = tri[:, 0]
    v0, v1, v2 = tri[:, 1]-a, tri[:, 2]-a, points-a
    d00, d01, d11 = (v0*v0).sum(1), (v0*v1).sum(1), (v1*v1).sum(1)
    d20, d21 = (v2*v0).sum(1), (v2*v1).sum(1)
    den = d00*d11-d01*d01
    den[np.abs(den) < 1e-300] = 1.0
    b1 = (d11*d20-d01*d21)/den
    b2 = (d00*d21-d01*d20)/den
    return (1-b1-b2)[:, None]*tuv[:, 0]+b1[:, None]*tuv[:, 1]+b2[:, None]*tuv[:, 2]


def _transfer(V, F, pv, pf, puv, pchart, ctx):
    """Charts and corner UVs of the source faces from the proxy's: a face takes the chart of the proxy face nearest its
    centre, a corner the UV of its nearest point on that chart (one UV per vertex and chart)."""
    import trimesh
    valid = np.flatnonzero(pchart >= 0)
    mesh = trimesh.Trimesh(V, F, process=False)
    nearest = _closest(pv, pf, valid, V[F].mean(axis=1), np.asarray(mesh.face_normals))
    chart = pchart[nearest].astype(np.int64)
    span = int(chart.max())+1
    keys, inverse = np.unique(F.ravel()*span+np.repeat(chart, 3), return_inverse=True)
    vertex, owner = keys//span, keys % span
    normals = np.asarray(mesh.vertex_normals)
    corner = np.zeros((len(keys), 2))
    for c in np.unique(owner):
        ctx.check_cancel()
        sel = np.flatnonzero(owner == c)
        cand = valid[pchart[valid] == c]
        face = _closest(pv, pf, cand, V[vertex[sel]], normals[vertex[sel]])
        corner[sel] = _affine(V[vertex[sel]], pv[pf[face]], puv[face])
    return corner[inverse.ravel()].reshape(-1, 3, 2), chart


def _run(exe, vertices, faces, options, ctx):
    """The helper's per-corner UVs and chart ids for this surface."""
    from atlas_toolkit.core import procs
    vertices = np.asarray(vertices, dtype='<f8')
    faces = np.asarray(faces, dtype='<u4')
    with tempfile.TemporaryDirectory(prefix='atlas-texture-uv-') as tmp:
        src, dst = Path(tmp)/'in.bin', Path(tmp)/'out.bin'
        with src.open('wb') as stream:
            stream.write(struct.pack('<III', PROTOCOL, len(vertices), len(faces)))
            stream.write(struct.pack('<5fI', options['max_cost'], options['normal_deviation_weight'],
                                     options['normal_seam_weight'], options['roundness_weight'],
                                     options['straightness_weight'], int(options['max_iterations'])))
            stream.write(vertices.tobytes()); stream.write(faces.tobytes())
        proc = procs.spawn([str(exe), str(src), str(dst)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        lines = queue.Queue()
        def pump():
            for line in proc.stdout:
                lines.put(line.strip())
        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        tail = []
        try:
            while proc.poll() is None:
                ctx.check_cancel()
                try:
                    line = lines.get(timeout=.1)
                except queue.Empty:
                    continue
                tail.append(line)
                tail = tail[-10:]
                if line.startswith('PROGRESS '):
                    _, phase, percent = line.split()
                    ctx.progress(.06+.13*(min(int(phase), 3)+int(percent)/100)/4, 'Unwrapping surface charts')
            reader.join(timeout=2)
            ctx.check_cancel()
            if proc.returncode or not dst.exists():
                raise ValueError('xatlas unwrap failed: '+'; '.join(tail))
            with dst.open('rb') as stream:
                protocol, nv, nf = struct.unpack('<III', stream.read(12))
                if protocol != PROTOCOL or nf != len(faces) or nv > len(faces)*3:
                    raise ValueError('Invalid xatlas output counts')
                mapping = np.frombuffer(stream.read(nv*4), dtype='<u4')
                coords = np.frombuffer(stream.read(nv*16), dtype='<f8').reshape(nv, 2)
                indices = np.frombuffer(stream.read(nf*12), dtype='<u4').reshape(nf, 3)
                ids = np.frombuffer(stream.read(nv*4), dtype='<i4')
            if len(ids) != nv or (indices >= nv).any() or not np.array_equal(mapping[indices], faces) or not np.isfinite(coords).all():
                raise ValueError('xatlas output changed the source face mapping')
            return coords[indices].copy(), ids[indices[:, 0]].astype(np.int64)
        finally:
            procs.finish(proc)
            proc.stdout.close()
