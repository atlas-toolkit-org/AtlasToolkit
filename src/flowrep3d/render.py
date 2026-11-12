"""Camera-dependent vector rendering of an independently extracted 3D network."""
from __future__ import annotations

import copy
import time
from dataclasses import asdict

import numpy as np
from shapely import line_merge
from shapely.geometry import GeometryCollection, LineString, box
from shapely.ops import unary_union

from ._atlas.camera import Camera, NEAR, fit_radius_points, frame_for
from ._atlas.frame3d import fit_frame, guess_axes
from ._atlas import raycast
from ._atlas.curvature import edge_topology, face_normals
from .flowlines import graph_chains
from .mesh import load_mesh
from .types import CurveNetwork, Drawing, FlowRepError, PolygonMesh, RenderOptions, checkpoint, progress_to


def _pieces(geometry):
    if geometry.is_empty: return []
    if geometry.geom_type=="LineString": return [np.asarray(geometry.coords)]
    if hasattr(geometry,"geoms"): return [p for g in geometry.geoms for p in _pieces(g)]
    return []


def _split_junctions(network):
    junctions=set(map(int,network.junctions)); result=[]
    for c,kind in zip(network.curves,network.kinds):
        c=np.asarray(c)
        if c[0]==c[-1]:
            hits=[i for i,v in enumerate(c[:-1]) if int(v) in junctions]
            if hits:
                i=hits[0]; base=c[:-1]; c=np.r_[base[i:],base[:i],base[i]]
        cuts=[0]+[i for i,v in enumerate(c[1:-1],1) if int(v) in junctions]+[len(c)-1]
        for a,b in zip(cuts[:-1],cuts[1:]):
            if b>a: result.append((kind,network.vertices[c[a:b+1]]))
    return result


def _clip_3d(P,cam,frame):
    """Clip linear 3D segments against the camera's near plane and paper frustum."""
    C=cam.to_camera(P); x0,y0,x1,y1=frame.screen
    if cam.ortho:
        s=cam.k/cam.radius
        A=np.array([[0,0,1],[s,0,0],[-s,0,0],[0,s,0],[0,-s,0]])
        b=np.array([-NEAR,-x0,x1,-y0,y1])
    else:
        A=np.array([[0,0,1],[cam.k,0,-x0],[-cam.k,0,x1],[0,cam.k,-y0],[0,-cam.k,y1]])
        b=np.array([-NEAR,0,0,0,0])
    pieces=[]; current=[]
    for i in range(len(P)-1):
        ca,cb=C[i],C[i+1]; fa=A@ca+b; delta=A@(cb-ca)
        lo,hi=0.0,1.0
        for f,d in zip(fa,delta):
            if abs(d)<1e-14:
                if f<0: hi=-1; break
            elif d>0: lo=max(lo,-f/d)
            else: hi=min(hi,-f/d)
        if hi<lo:
            if len(current)>1: pieces.append(np.array(current))
            current=[]; continue
        p=P[i]+lo*(P[i+1]-P[i]); q=P[i]+hi*(P[i+1]-P[i])
        if current and np.linalg.norm(current[-1]-p)<1e-8:
            current.append(q)
        else:
            if len(current)>1: pieces.append(np.array(current))
            current=[p,q]
    if len(current)>1: pieces.append(np.array(current))
    return pieces


def _sample(P,cam,frame,step):
    C=cam.to_camera(P); screen=frame.to_mm(cam.project(C))
    lengths=np.linalg.norm(np.diff(screen,axis=0),axis=1); out=[P[0]]
    for i,length in enumerate(lengths):
        ratio=1.0 if cam.ortho else max(C[i,2],C[i+1,2])/max(min(C[i,2],C[i+1,2]),NEAR)
        n=max(1,min(65536,int(np.ceil(length/step*ratio))))
        t=np.arange(1,n+1)/n
        out.extend(P[i]+t[:,None]*(P[i+1]-P[i]))
    return np.array(out)


def _silhouettes(V,F,cam):
    E,_,EF,counts=edge_topology(F); N,_=face_normals(V,F)
    eye,_,_,w=cam.basis()
    direction=np.broadcast_to(w,N.shape) if cam.ortho else eye-V[F].mean(1)
    front=(N*direction).sum(1)>=0
    boundary=EF[:,1]<0; keep=boundary.copy(); valid=~boundary
    keep[valid]=front[EF[valid,0]]!=front[EF[valid,1]]
    curves,_=graph_chains(E,np.flatnonzero(keep))
    return [V[c] for c in curves]


def order_polylines(polylines,start=(0.0,0.0)):
    """Atlas's nearest-endpoint cKDTree ordering, adapted to return arrays."""
    from scipy.spatial import cKDTree
    n=len(polylines)
    if n<2: return polylines
    ends=np.array([p[0] for p in polylines]+[p[-1] for p in polylines])
    tree=cKDTree(ends); used=np.zeros(n,bool); out=[]; pos=np.array(start); k=8
    while len(out)<n:
        kk=min(k,2*n); _,idx=tree.query(pos,k=kk)
        found=next((int(j) for j in np.atleast_1d(idx) if int(j)<2*n and not used[int(j)%n]),None)
        if found is None:
            k=min(2*n,k*4); continue
        i=found%n; used[i]=True
        p=polylines[i][::-1] if found>=n else polylines[i]
        out.append(p); pos=p[-1]; k=8
    return out


def _clean_layers(layers,bounds,min_length,optimise,cancel):
    area=box(*bounds); ink=GeometryCollection(); out={}; removed=0
    for name in ("features","silhouette","flowlines","hidden"):
        accepted=[]
        for j,p in enumerate(layers.get(name,[])):
            if j%100==0: checkpoint(cancel)
            if len(p)<2: continue
            line=LineString(np.round(p,8)).intersection(area); before=line.length
            if not ink.is_empty and name!="hidden":
                line=line.difference(ink)
            if line.geom_type in ("MultiLineString","GeometryCollection"):
                # A point crossing is not duplicate ink. Reconnect the two
                # pieces before the minimum-stroke-length filter runs.
                line=line_merge(line)
            removed+=max(0.0,before-line.length)
            pieces=[q for q in _pieces(line) if LineString(q).length>=min_length]
            accepted.extend(pieces)
            if pieces and name!="hidden": ink=unary_union([ink,*[LineString(q) for q in pieces]])
        out[name]=order_polylines(accepted) if optimise else accepted
    return out,removed


def render_network(mesh,network: CurveNetwork,camera=None,paper=None,options=None,progress=None,cancel=None):
    """Render in mm. Paper may be an Atlas Frame; camera an Atlas-compatible Camera."""
    start=time.perf_counter(); options=options or RenderOptions()
    mesh=mesh if isinstance(mesh,PolygonMesh) else load_mesh(mesh)
    cam=copy.copy(camera) if camera is not None else Camera()
    if (not np.isfinite([cam.theta,cam.psi,cam.radius,cam.fov,*cam.lookat]).all()
            or cam.radius<=0 or not 0<cam.fov<180):
        raise FlowRepError("camera must be finite with a positive distance and field of view in (0, 180)")
    frame=paper or frame_for(options.paper,options.orientation,options.margin)
    if min(frame.area[2:])<=options.line_width: raise FlowRepError("paper margins leave no drawable area")
    V0=mesh.reference_vertices
    guessed_up,guessed_front=guess_axes(np.ptp(V0,axis=0),mesh.metadata.get("format","obj"))
    up=guessed_up if options.up=="auto" else options.up
    front=guessed_front if options.front=="auto" else options.front
    transform=fit_frame(V0,up,front,options.rotate,mode="size",size=100)
    V=transform.apply(V0); F=mesh.reference_triangles
    if options.fit_camera: cam.radius=fit_radius_points(V,cam,frame)
    progress_to(progress,0.10,"projecting flowlines and geometric silhouettes",cancel)
    # Plane snapping can move a valid descriptive curve slightly beneath the
    # reference surface. Attach its vertices before visibility testing, otherwise
    # self-occlusion would turn a continuous stroke into false dashes.
    import trimesh
    attached=network.vertices.copy()
    used=np.unique(np.concatenate(network.curves)) if network.curves else np.array([],int)
    if len(used):
        reference=trimesh.Trimesh(V0,F,process=False)
        attached[used],_,_=trimesh.proximity.closest_point(reference,attached[used])
    display_network=CurveNetwork(attached,network.curves,network.kinds)
    raw=[("features" if kind=="trim" else "flowlines",transform.apply(P)) for kind,P in _split_junctions(display_network)]
    if options.silhouette: raw.extend(("silhouette",P) for P in _silhouettes(V,F,cam))
    curves=[]; kinds=[]
    for kind,P in raw:
        checkpoint(cancel)
        for piece in _clip_3d(P,cam,frame):
            curves.append(_sample(piece,cam,frame,options.sample_step)); kinds.append(kind)
    samples=sum(len(p) for p in curves)
    progress_to(progress,0.30,f"checking visibility of {samples} curve samples",cancel)
    # Interpolating between attached vertices can still cut slightly beneath a
    # curved surface. Attach intermediate samples too, retaining the source
    # surface as the authority for visibility and exported curve placement.
    attached_ids=[i for i,kind in enumerate(kinds) if kind!="silhouette"]
    if attached_ids:
        source_samples=transform.inverse(np.concatenate([curves[i] for i in attached_ids]))
        closest,_,_=trimesh.proximity.closest_point(reference,source_samples)
        closest=transform.apply(closest)
        cursor=0
        for i in attached_ids:
            count=len(curves[i]); curves[i]=closest[cursor:cursor+count]; cursor+=count
        checkpoint(cancel)
    caster=raycast.Caster(V,F)
    runs=raycast.split_visible(caster,cam,curves,[np.linalg.norm(p[0]-p[-1])<1e-8 for p in curves],
                              eps=0.015,iters=12,hidden=options.hidden)
    layers={"features":[],"silhouette":[],"flowlines":[],"hidden":[]}
    for kind,parts in zip(kinds,runs):
        for visible,P in parts:
            Q=frame.to_mm(cam.project(cam.to_camera(P)))
            layers[kind if visible else "hidden"].append(Q)
    progress_to(progress,0.76,"removing overlap and ordering plotter strokes",cancel)
    W,H=frame.sheet; x,y,w,h=frame.area; half=options.line_width/2
    bounds=(x+half,y+half,x+w-half,y+h-half)
    layers,removed=_clean_layers(layers,bounds,options.min_length,options.optimise,cancel)
    pls=[p for layer in layers.values() for p in layer]
    ink=sum(float(np.linalg.norm(np.diff(p,axis=0),axis=1).sum()) for p in pls)
    travel=sum(float(np.linalg.norm(a[-1]-b[0])) for a,b in zip(pls[:-1],pls[1:]))
    stats={"strokes":len(pls),"ink_mm":ink,"travel_mm":travel,"visibility_samples":samples,
           "duplicate_length_removed_mm":removed,"ray_backend":caster.backend,"seconds":time.perf_counter()-start}
    metadata={"render_options":asdict(options),"camera":dict(cam.as_values(),projection="orthographic" if cam.ortho else "perspective"),
              "frame":{"up":up,"front":front,"rotate":list(options.rotate),"scale":transform.scale,"centre":transform.centre.tolist()},
              "network":network.metadata,"network_quality":network.diagnostics,
              "source_mesh":mesh.source,"drawing_bounds_mm":bounds}
    progress_to(progress,1.0,f"drawing contains {len(pls)} strokes",cancel)
    return Drawing(W,H,layers,options.line_width,options.tolerance,metadata,stats)

