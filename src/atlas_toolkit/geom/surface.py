"""Native polygon surfaces and their derived triangle view. Coordinates stay in source units."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
from collections import defaultdict
import numpy as np


def face_digest(faces):
    return hashlib.blake2b(np.ascontiguousarray(faces, dtype=np.int64).view(np.uint8), digest_size=16).hexdigest()


def triangulate(vertices, faces):
    """Earcut on the dominant plane, retaining winding and polygon ownership."""
    if isinstance(faces, np.ndarray) and faces.ndim == 2 and faces.shape[1] == 3:
        return np.asarray(faces, np.int64), np.arange(len(faces), dtype=np.int64)
    import mapbox_earcut
    # Two native polygons may share a bent chain of edges. Their common chord cannot
    # be an internal diagonal in both: that would create four incident triangles.
    incidence = defaultdict(set)
    for fi, face in enumerate(faces):
        for a,b in zip(face,np.roll(face,-1)):
            incidence[tuple(sorted((int(a),int(b))))].add(fi)
    forbidden = set()
    for fi, face in enumerate(faces):
        for j,b in enumerate(face):
            a,c = int(face[j-1]),int(face[(j+1)%len(face)])
            shared = incidence[tuple(sorted((a,int(b))))] & incidence[tuple(sorted((int(b),c)))]
            if shared-{fi}:
                forbidden.add(tuple(sorted((a,c))))
    triangles, owners = [], []
    for fi, face in enumerate(faces):
        face = np.asarray(face, np.int64)
        if len(face) == 3:
            t = face.reshape(1, 3)
        elif len(face) == 4:
            candidates = (face[[[0,1,2],[0,2,3]]], face[[[0,1,3],[1,2,3]]])
            def quality(triangles):
                edge = set(map(tuple,np.sort(np.concatenate([triangles[:,[0,1]],triangles[:,[1,2]],triangles[:,[2,0]]]),axis=1)))
                native = {tuple(sorted((int(a),int(b)))) for a,b in zip(face,np.roll(face,-1))}
                if (edge-native) & forbidden:
                    return -1.
                p = vertices[triangles]
                normals = np.cross(p[:,1]-p[:,0],p[:,2]-p[:,0])
                lengths = np.linalg.norm(normals,axis=1)
                alignment = float(normals[0]@normals[1])/max(float(lengths.prod()),1e-30)
                return float(lengths.min())*max(.01,1+alignment)
            t = max(candidates, key=quality)
            if quality(t) <= 1e-24:
                raise ValueError('A quad is collapsed; increase remesh density.')
        else:
            p = vertices[face]
            relative = p - p[0]
            n = np.sum(np.cross(relative, np.roll(relative, -1, axis=0)), axis=0)
            uv = np.delete(relative, int(np.argmax(np.abs(n))), axis=1)
            idx = mapbox_earcut.triangulate_float64(np.ascontiguousarray(uv), np.array([len(p)], np.uint32))
            t = face[np.asarray(idx).reshape(-1, 3)]
            edge_set = set(map(tuple,np.sort(np.concatenate([t[:,[0,1]],t[:,[1,2]],t[:,[2,0]]]),axis=1))) if len(t) else set()
            native = {tuple(sorted((int(a),int(b)))) for a,b in zip(face,np.roll(face,-1))}
            if (edge_set-native) & forbidden:
                t = constrained_ears(face,uv,forbidden)
            if not len(t):
                raise ValueError("A polygon cannot be triangulated; check for a crossed or collapsed face.")
            # Earcut may omit collinear boundary corners. Reinsert them so adjacent polygons
            # still meet at identical edges rather than creating triangle-view T junctions.
            missing = [int(vertex) for vertex in face if vertex not in t]
            for vertex in missing:
                point = np.delete(vertices[vertex]-p[0], int(np.argmax(np.abs(n))))
                inserted = False
                for ti, tri in enumerate(t):
                    coords = np.delete(vertices[tri]-p[0], int(np.argmax(np.abs(n))), axis=1)
                    for j in range(3):
                        a, b, c = int(tri[j]), int(tri[(j+1)%3]), int(tri[(j+2)%3])
                        delta = coords[(j+1)%3]-coords[j]
                        length2 = float(delta@delta)
                        u = float((point-coords[j])@delta)/max(length2, 1e-30)
                        if 1e-10 < u < 1-1e-10 and np.linalg.norm(point-coords[j]-u*delta) <= 1e-10*max(np.sqrt(length2),1e-10):
                            t[ti] = [a,vertex,c]
                            t = np.vstack([t,[vertex,b,c]])
                            inserted = True
                            break
                    if inserted:
                        break
                if not inserted:
                    raise ValueError('A polygon has overlapping corners or a crossed projection; increase remesh density.')
            if len(t) != len(face)-2:
                raise ValueError('A polygon has a crossed projection; increase remesh density.')
            # Use projected signed area, not a single 3D triangle: a nonplanar cell may have a
            # small first ear facing across the aggregate normal even though its loop is valid.
            projected = np.delete(vertices[t] - p[0], int(np.argmax(np.abs(n))), axis=2)
            a, b = projected[:, 1]-projected[:, 0], projected[:, 2]-projected[:, 0]
            got = np.sum(a[:, 0]*b[:, 1]-a[:, 1]*b[:, 0])
            want = np.sum(uv[:, 0]*np.roll(uv[:, 1], -1)-uv[:, 1]*np.roll(uv[:, 0], -1))
            if got*want < 0:
                t = t[:, ::-1]
            edges = np.sort(np.concatenate([t[:,[0,1]],t[:,[1,2]],t[:,[2,0]]]), axis=1)
            unique, counts = np.unique(edges,axis=0,return_counts=True)
            boundary = {tuple(e) for e in unique[counts==1]}
            expected = {tuple(sorted((int(a),int(b)))) for a,b in zip(face,np.roll(face,-1))}
            if counts.max()>2 or boundary!=expected:
                raise ValueError('A polygon triangulation does not preserve its boundary; increase remesh density.')
        triangles.extend(t.tolist())
        owners.extend([fi] * len(t))
    return np.asarray(triangles, np.int64).reshape(-1, 3), np.asarray(owners, np.int64)


def constrained_ears(face, uv, forbidden):
    """Ear clipping with protected shared-chain diagonals; no added points or topology changes."""
    area = np.sum(uv[:,0]*np.roll(uv[:,1],-1)-uv[:,1]*np.roll(uv[:,0],-1))
    sign = 1 if area>=0 else -1
    remaining = list(range(len(face)))
    out=[]
    eps=max(float(np.ptp(uv,axis=0).max())**2*1e-13,1e-30)
    def cross(a,b):
        return a[...,0]*b[...,1]-a[...,1]*b[...,0]
    while len(remaining)>3:
        found=False
        for j,k in enumerate(remaining):
            a,c=remaining[j-1],remaining[(j+1)%len(remaining)]
            if tuple(sorted((int(face[a]),int(face[c])))) in forbidden:
                continue
            if sign*cross(uv[k]-uv[a],uv[c]-uv[k])<=eps:
                continue
            other=[i for i in remaining if i not in (a,k,c)]
            q=uv[other]
            inside=(sign*cross(uv[k]-uv[a],q-uv[a])>=-eps)&(sign*cross(uv[c]-uv[k],q-uv[k])>=-eps)&(sign*cross(uv[a]-uv[c],q-uv[c])>=-eps)
            if inside.any():
                continue
            out.append([int(face[a]),int(face[k]),int(face[c])])
            remaining.pop(j);found=True;break
        if not found:
            raise ValueError('A folded polygon needs smaller cells to retain its shared boundary.')
    out.append([int(face[i]) for i in remaining])
    return np.array(out,np.int64)


@dataclass
class Surface:
    vertices: np.ndarray
    faces: object
    triangles: np.ndarray
    owners: np.ndarray
    frame: dict | None = None

    @property
    def edges(self):
        if isinstance(self.faces, np.ndarray):
            e = np.stack([self.faces, np.roll(self.faces, -1, axis=1)], axis=-1).reshape(-1, 2)
        else:
            e = np.concatenate([np.column_stack([f, np.roll(f, -1)]) for f in self.faces])
        return np.unique(np.sort(e, axis=1), axis=0)

    @property
    def bounds(self):
        return np.stack([self.vertices.min(0), self.vertices.max(0)])

    @property
    def extents(self):
        return np.ptp(self.vertices, axis=0)

    def as_trimesh(self):
        import trimesh
        mesh = trimesh.Trimesh(self.vertices, self.triangles, process=False)
        if self.frame:
            mesh.metadata['atlas_frame'] = self.frame
        if len(self.faces) != len(self.triangles):
            mesh.metadata['atlas_polygons'] = dict(faces=self.faces, owners=self.owners,
                vertices=len(self.vertices), digest=face_digest(mesh.faces))
        return mesh


def from_arrays(vertices, faces):
    v = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    if not len(v) or not np.isfinite(v).all() or not len(faces):
        raise ValueError("Mesh must have finite vertices and nonempty faces.")
    if isinstance(faces, np.ndarray) and faces.ndim == 2 and faces.shape[1] == 3:
        f = np.asarray(faces, np.int64)
    else:
        cleaned = []
        for face in faces:
            f = np.asarray(face, np.int64)
            f = f[np.r_[True, f[1:] != f[:-1]]]
            if len(f) and f[0] == f[-1]:
                f = f[:-1]
            if len(f) < 3 or len(np.unique(f)) != len(f):
                raise ValueError("A polygon repeats vertices or has fewer than three corners.")
            cleaned.append(f)
        f = tuple(cleaned)
    flat = f.ravel() if isinstance(f, np.ndarray) else np.concatenate(f)
    if flat.min() < 0 or flat.max() >= len(v):
        raise ValueError("A face references an invalid vertex.")
    t, owner = triangulate(v, f)
    return Surface(v, f, t, owner)


def polygon_data(mesh):
    data = mesh.metadata.get('atlas_polygons')
    if data and data['vertices'] == len(mesh.vertices) and data['digest'] == face_digest(mesh.faces):
        return data
    return None


def from_mesh(mesh):
    if isinstance(mesh, Surface):
        return mesh
    data = polygon_data(mesh)
    surface = from_arrays(mesh.vertices, data['faces'] if data else np.asarray(mesh.faces))
    surface.frame = mesh.metadata.get('atlas_frame')
    return surface


def read_frame(path):
    if path and Path(path).suffix.lower() == '.stl':
        import re
        try:
            with Path(path).open('rb') as stream:
                match = re.search(rb'atlas-axes:([+-][xyz]),([+-][xyz])', stream.read(80))
            if match:
                return {'up': match[1].decode(), 'front': match[2].decode(), 'rotate': [0., 0., 0.]}
        except OSError:
            pass
        return None
    if not path or Path(path).suffix.lower() not in ('.obj', '.ply'):
        return None
    try:
        with Path(path).open('rb') as stream:
            head = stream.read(4096).decode('utf-8', 'ignore')
        for line in head.splitlines():
            if 'atlas-frame:' in line:
                frame = json.loads(line.split('atlas-frame:', 1)[1])
                axes = ('+x','-x','+y','-y','+z','-z')
                if frame.get('up') in axes and frame.get('front') in axes and len(frame.get('rotate', ())) == 3:
                    return frame
    except (OSError, ValueError, TypeError):
        pass
    return None


def load_surface(path):
    path = Path(path)
    ext = path.suffix.lower()
    if ext == '.stl':
        # Exact float32 welding avoids the large rounded float64 copies in a generic STL
        # merge. Binary STL stores every corner separately: this matters on multi-million-face scans.
        import struct
        with path.open('rb') as stream:
            header = stream.read(84)
            count = struct.unpack('<I',header[80:84])[0] if len(header)==84 else 0
            if count and path.stat().st_size == 84+50*count:
                dtype = np.dtype([('normal','<f4',3),('v','<f4',(3,3)),('attribute','<u2')])
                data = np.fromfile(stream,dtype=dtype,count=count)
                corners = np.ascontiguousarray(data['v'].reshape(-1,3))
                del data
                vertices,inverse = np.unique(corners,axis=0,return_inverse=True)
                del corners
                import trimesh
                from .mesh import clean
                mesh = clean(trimesh.Trimesh(vertices.astype(float),inverse.reshape(-1,3),process=False),merge=False)
                return from_mesh(mesh)
    if ext == '.obj':
        v, f = [], []
        with path.open(encoding='utf-8-sig', errors='replace') as stream:
            for number, line in enumerate(stream, 1):
                tokens = line.split('#', 1)[0].split()
                try:
                    if tokens and tokens[0] == 'v':
                        v.append([float(x) for x in tokens[1:4]])
                    elif tokens and tokens[0] == 'f':
                        ids = [int(x.split('/')[0]) for x in tokens[1:]]
                        if 0 in ids:
                            raise ValueError('OBJ indices start at 1')
                        f.append([x - 1 if x > 0 else len(v) + x for x in ids])
                except (ValueError, IndexError) as e:
                    raise ValueError(f'{path.name}:{number}: malformed OBJ: {e}') from e
        if f and all(len(x) == 3 for x in f):
            f = np.asarray(f, np.int64)
        surface = from_arrays(v, f)
        surface.frame = read_frame(path)
        return surface
    if ext == '.ply':
        from trimesh.exchange.ply import load_ply
        with path.open('rb') as stream:
            data = load_ply(stream, fix_texture=False)
        raw = data['metadata']['_ply_raw']['face']['data']
        if isinstance(raw, dict):
            faces = raw.get('vertex_indices', raw.get('vertex_index'))
        elif raw.dtype.names:
            name = next((k for k in raw.dtype.names if k in ('vertex_indices', 'vertex_index')), raw.dtype.names[0])
            faces = raw[name]
            if faces.dtype.names:
                faces = faces['f1']
        else:
            faces = raw
        surface = from_arrays(data['vertices'], faces)
        surface.frame = read_frame(path)
        return surface
    if ext == '.off':
        from trimesh.exchange.off import load_off
        # OFF native loops are simple whitespace-delimited integer lists.
        with path.open(encoding='utf-8-sig') as stream:
            lines = (line.split('#', 1)[0].strip() for line in stream)
            lines = (line for line in lines if line)
            head = next(lines)
            tokens = head.split()
            if tokens[0] not in ('OFF', 'COFF'):
                raise ValueError('Expected OFF header.')
            nv, nf, *_ = map(int, tokens[1:] or next(lines).split())
            v = [[float(x) for x in next(lines).split()[:3]] for _ in range(nv)]
            f = []
            for _ in range(nf):
                row = next(lines).split()
                size = int(row[0])
                f.append(list(map(int,row[1:1+size])))
        return from_arrays(v, f)
    from .mesh import load_mesh
    return from_mesh(load_mesh(path))


def save_surface(surface, dst, tool):
    from .mesh import signature_text, write_stl
    import struct
    dst = Path(dst)
    sig = signature_text(tool)
    frame = 'atlas-frame:'+json.dumps(surface.frame, separators=(',', ':')) if surface.frame else ''
    if dst.suffix.lower() == '.obj':
        with dst.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write(f'# {sig}\n')
            if frame:
                stream.write('# '+frame+'\n')
            for x, y, z in surface.vertices:
                stream.write(f'v {x:.17g} {y:.17g} {z:.17g}\n')
            for face in surface.faces:
                stream.write('f ' + ' '.join(str(int(i) + 1) for i in face) + '\n')
    elif dst.suffix.lower() == '.ply':
        with dst.open('wb') as stream:
            stream.write((f'ply\nformat binary_little_endian 1.0\ncomment {sig}\n'+('comment '+frame+'\n' if frame else '')+f'element vertex {len(surface.vertices)}\n'
                'property double x\nproperty double y\nproperty double z\n'
                f'element face {len(surface.faces)}\nproperty list uint int vertex_indices\nend_header\n').encode())
            stream.write(np.asarray(surface.vertices, dtype='<f8').tobytes())
            for face in surface.faces:
                stream.write(struct.pack('<I', len(face)))
                stream.write(np.asarray(face, dtype='<i4').tobytes())
    elif dst.suffix.lower() == '.stl':
        axes = ''
        if surface.frame and not any(surface.frame.get('rotate', (0, 0, 0))):
            axes = f" atlas-axes:{surface.frame['up']},{surface.frame['front']}"
        write_stl(dst, surface.vertices, surface.triangles, sig+axes)
    else:
        raise ValueError('Polygon outputs support OBJ, PLY, and triangulated STL.')


def native_edge_mask(mesh, edges, edge_faces=None):
    data = polygon_data(mesh)
    if data is None:
        return np.ones(len(edges), dtype=bool)
    if edge_faces is not None:
        ef = np.asarray(edge_faces)
        return (ef[:, 1] < 0) | (data['owners'][ef[:, 0]] != data['owners'][np.maximum(ef[:, 1], 0)])
    native = from_mesh(mesh).edges
    nv = len(mesh.vertices)
    keys = native[:, 0] * nv + native[:, 1]
    return np.isin(np.sort(edges, axis=1) @ np.array([nv, 1]), keys)
