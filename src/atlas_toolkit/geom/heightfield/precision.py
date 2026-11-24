"""Local, link-safe heightfield edge collapses for mesh-file precision.

Never simplify a closed volume and infer its terrain by normal direction: that
can join disconnected surface fans when its walls are removed again.
"""
import numpy as np


def file_copy(mesh):
    import trimesh
    from atlas_toolkit.geom.mesh import clean
    return clean(trimesh.Trimesh(mesh.vertices.astype('float32'),mesh.faces,process=False))


def flip_caps(mesh, height=1e-7, rounds=8):
    """Remove "cap" triangles (a corner lying on the opposite edge, as a file reader sees them: after float32
    rounding, within `height` mm of it) that native simplification of planar terraces leaves, by flipping that edge:
    the cap (a, b, c) and its neighbour (b, a, d) across a-b become (c, a, d) and (c, d, b), the neighbour cut in
    two at c. Closed, consistently wound surfaces stay so. A cap whose flip would duplicate an edge, or a needle
    (two corners together), is left for the caller's check to refuse."""
    import trimesh
    F = np.array(mesh.faces, dtype=np.int64)
    V = np.asarray(mesh.vertices, dtype=np.float64)
    R = V.astype(np.float32).astype(np.float64)
    n = len(V)
    for _ in range(rounds):
        tri = R[F]
        edge = np.stack((tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 1], tri[:, 0]-tri[:, 2]), axis=1)
        length = np.linalg.norm(edge, axis=2)
        area = .5*np.linalg.norm(np.cross(edge[:, 0], -edge[:, 2]), axis=1)
        caps = np.flatnonzero((2*area <= height*length.max(axis=1)) & (length.min(axis=1) > height))
        del tri, edge, area
        if not len(caps):
            break
        # Directed edges as sorted keys (millions of faces: no Python dictionary of them).
        keys = np.concatenate([F[:, k]*n+F[:, (k+1) % 3] for k in range(3)])
        order = np.argsort(keys, kind="stable")
        ranked = keys[order]
        del keys

        def owner(x, y):
            i = np.searchsorted(ranked, x*n+y)
            return int(order[i]) % len(F) if i < len(ranked) and ranked[i] == x*n+y else -1

        touched, made = set(), set()
        for i in map(int, caps):
            if i in touched:
                continue
            k = int(np.argmax(length[i]))
            a, b, c = int(F[i][k]), int(F[i][(k+1) % 3]), int(F[i][(k+2) % 3])
            j = owner(b, a)
            if j < 0 or j in touched or j == i:                     # an open edge, or a face just changed
                continue
            d = int(next(v for v in F[j] if v not in (a, b)))
            if owner(c, d) >= 0 or owner(d, c) >= 0 or (min(c, d), max(c, d)) in made:
                continue
            F[i], F[j] = (c, a, d), (c, d, b)
            touched |= {i, j}
            made.add((min(c, d), max(c, d)))
        if not touched:
            break
    return trimesh.Trimesh(V, F, process=False)


def valid_surface(mesh):
    from .mesh import check_mesh
    check_mesh(mesh,solid=False)
    xy=mesh.vertices[mesh.faces,:2]
    a,b=xy[:,1]-xy[:,0],xy[:,2]-xy[:,0]
    if np.any(a[:,0]*b[:,1]-a[:,1]*b[:,0]<=0):
        raise ValueError('Terrain contains a folded or collapsed XY triangle.')


def collapse_short_edges(mesh,precision,ctx,heightfield=True):
    """Collapse only existing short edges, preserving the manifold link and XY orientation."""
    import trimesh
    from scipy.sparse import coo_matrix
    v,f=np.asarray(mesh.vertices).copy(),np.asarray(mesh.faces).copy()
    edges=mesh.edges_unique
    count=np.bincount(mesh.edges_unique_inverse,minlength=len(edges))
    boundary=np.zeros(len(v),bool)
    boundary[edges[count==1].ravel()]=True
    coordinates=v[:,:2] if heightfield else v
    lengths=np.linalg.norm(coordinates[edges[:,0]]-coordinates[edges[:,1]],axis=1)
    candidates=np.flatnonzero(lengths<precision*2)
    candidates=candidates[np.argsort(lengths[candidates],kind='stable')]
    incidence=coo_matrix((np.ones(f.size,np.uint8),(f.ravel(),np.repeat(np.arange(len(f)),3))),
                         shape=(len(v),len(f))).tocsr()
    stars={}
    parent=np.arange(len(v))
    alive=np.ones(len(f),bool)
    def root(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]]
            i=parent[i]
        return int(i)
    def star(i):
        if i not in stars:
            stars[i]=set(incidence.indices[incidence.indptr[i]:incidence.indptr[i+1]])
        return {int(j) for j in stars[i] if alive[j]}
    collapsed=0
    for number,index in enumerate(candidates):
        if number%128==0:ctx.check_cancel()
        a,b=map(root,edges[index])
        if a==b:continue
        sa,sb=star(a),star(b)
        shared=sa&sb
        if len(shared) not in (1,2):continue
        if len(shared)==2 and boundary[a] and boundary[b]:continue
        na=set(f[list(sa)].ravel())-{a,b}
        nb=set(f[list(sb)].ravel())-{a,b}
        opposite=set(f[list(shared)].ravel())-{a,b}
        if na&nb!=opposite:continue
        orders=[(a,b),(b,a)] if boundary[a]==boundary[b] else [(a,b)] if boundary[a] else [(b,a)]
        for keep,drop in orders:
            remaining=np.array(sorted((sa|sb)-shared),dtype=np.int64)
            # A one-face island has a valid edge link but must not disappear.
            if not len(remaining):continue
            old=f[remaining].copy()
            faces=old.copy()
            faces[faces==drop]=keep
            if heightfield:
                xy=v[faces,:2]
                x,y=xy[:,1]-xy[:,0],xy[:,2]-xy[:,0]
                if np.any(x[:,0]*y[:,1]-x[:,1]*y[:,0]<=1e-14):continue
            else:
                normals=np.cross(v[faces[:,1]]-v[faces[:,0]],v[faces[:,2]]-v[faces[:,0]])
                prior=np.cross(v[old[:,1]]-v[old[:,0]],v[old[:,2]]-v[old[:,0]])
                if np.any(np.linalg.norm(normals,axis=1)<=2e-14) or np.any(np.sum(normals*prior,axis=1)<=0):continue
            f[remaining]=faces
            alive[list(shared)]=False
            parent[drop]=keep
            stars[keep]=sa|sb
            boundary[keep]=boundary[a] or boundary[b]
            collapsed+=1
            break
    result=trimesh.Trimesh(v,f[alive],process=False)
    result.remove_unreferenced_vertices()
    return result,collapsed


def surface(mesh,ctx):
    """Keep the original field when safe; repair only sub-format existing edges otherwise."""
    try:
        valid_surface(mesh)
        valid_surface(file_copy(mesh))
        return mesh
    except ValueError:
        pass
    precision=max(1e-7,float(mesh.extents[:2].max())*2**-20)
    ctx.progress(.55,'repairing sub-format terrain edges without joining boundaries')
    result,collapsed=collapse_short_edges(mesh,precision,ctx)
    try:
        valid_surface(result)
        valid_surface(file_copy(result))
    except ValueError as error:
        raise ValueError('Terrain precision repair could not preserve its boundary topology. '
                         'Increase model size or boundary tolerance; the source raster has not been changed. '+str(error)) from error
    if collapsed:ctx.log(f'precision repair: collapsed {collapsed:,} sub-format edges; protected surface links and XY winding')
    return result
