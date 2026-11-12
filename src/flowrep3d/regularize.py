"""Surface-constrained smoothing and compatible planar/linear snapping."""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from .mesh import unit
from .types import checkpoint


def regularize(mesh, curves, trim_vertices, iterations, cancel=None):
    import trimesh
    reference=trimesh.Trimesh(mesh.reference_vertices,mesh.reference_triangles,process=False)
    V=mesh.vertices.copy(); topo=mesh.topology
    adjacency=defaultdict(set)
    for c in curves:
        for a,b in zip(c[:-1],c[1:]):
            adjacency[int(a)].add(int(b)); adjacency[int(b)].add(int(a))
    ids=np.array(sorted(adjacency),int)
    movable=ids[~trim_vertices[ids]]
    mean_length=float(topo.lengths.mean())
    if len(ids):
        # The quad proxy may differ from the original triangle surface. Start
        # pinned trims on that reference too; later rollback then stays bounded.
        V[ids],_,_=trimesh.proximity.closest_point(reference,V[ids])
    for iteration in range(iterations):
        checkpoint(cancel)
        for v in movable:   # Gauss-Seidel: subsequent vertices use the newest locations.
            nb=np.array(sorted(adjacency[int(v)]),int)
            normal=topo.normals[v]
            vectors=V[nb]-V[v]
            tangent=vectors-(vectors@normal)[:,None]*normal
            move=tangent.mean(0)
            if len(nb) in (3,4):
                axis=unit(tangent[0]); other=np.cross(normal,axis)
                angle=np.arctan2(tangent@other,tangent@axis)
                order=np.argsort(angle); theta=angle[order]
                if len(nb)==4:
                    target=np.arange(4)*np.pi/2
                else:
                    # A T junction has 90,90,180-degree gaps, rather than 120-degree gaps.
                    gaps=np.diff(np.r_[theta,theta[0]+2*np.pi])
                    largest=int(np.argmax(gaps))
                    steps=np.full(3,np.pi/2); steps[largest]=np.pi
                    target=np.r_[0.0,np.cumsum(steps[:-1])]
                phase=np.angle(np.exp(1j*(theta-target)).mean())
                expected=(np.cos(target+phase)[:,None]*axis+np.sin(target+phase)[:,None]*other)
                expected*=np.linalg.norm(tangent[order],axis=1)[:,None]
                move=0.5*move+0.5*(tangent[order]-expected).mean(0)
            length=np.linalg.norm(move)
            V[v]+=move*min(0.35,0.15*mean_length/max(length,1e-30))
        if len(movable):
            V[movable],_,_=trimesh.proximity.closest_point(reference,V[movable])
    constraints=defaultdict(list); planar=linear=0
    for c in curves:
        pts=V[c]; centre=pts.mean(0)
        if len(pts)<3:
            continue
        _,_,basis=np.linalg.svd(pts-centre,full_matrices=True)
        if np.max(np.linalg.norm((pts-centre)-((pts-centre)@basis[0])[:,None]*basis[0],axis=1))<0.5*mean_length:
            planes=basis[1:]; linear+=1
        elif np.max(np.abs((pts-centre)@basis[-1]))<0.5*mean_length:
            planes=basis[-1:]; planar+=1
        else:
            continue
        for v in set(map(int,c)):
            if not trim_vertices[v]:
                constraints[v].extend((normal,float(normal@centre)) for normal in planes)
    snapped=V.copy()
    for v,conditions in constraints.items():
        A=np.array([a for a,b in conditions]); b=np.array([b for a,b in conditions])
        point=V[v]+np.linalg.lstsq(A,b-A@V[v],rcond=None)[0]
        if np.max(np.abs(A@point-b))<1e-7*mesh.diagonal and np.linalg.norm(point-V[v])<=0.5*mean_length:
            snapped[v]=point
    if len(ids):
        _,distance,face=trimesh.proximity.closest_point(reference,snapped[ids])
        reject=distance>0.5*mean_length
        snapped[ids[reject]]=V[ids[reject]]
        _,distance,face=trimesh.proximity.closest_point(reference,snapped[ids])
        # Interpolated normals at moved boundary anchors, for the post-smoothing coverage check.
        closest,_,face=trimesh.proximity.closest_point(reference,snapped[ids])
        bary=trimesh.triangles.points_to_barycentric(reference.triangles[face],closest)
        normals=topo.normals.copy()
        from ._atlas.curvature import vertex_normals
        rn=vertex_normals(mesh.reference_vertices,mesh.reference_triangles)
        normals[ids]=unit((rn[mesh.reference_triangles[face]]*bary[:,:,None]).sum(1))
        max_distance=float(distance.max())
    else:
        normals=topo.normals.copy(); max_distance=0.0
    return snapped,normals,{"iterations":iterations,"planar_curve_candidates":planar,"linear_curve_candidates":linear,
                             "surface_max_distance":max_distance,"surface_bound":0.5*mean_length}

