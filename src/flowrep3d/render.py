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
from ._atlas.curvature import edge_topology, face_normals, neighbour_average, vertex_normals
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


def _smooth_polyline(P,iterations,spacing):
    """Resample by arc length, then Taubin lambda/mu smoothing (no shrinkage) with the ends fixed; a closed curve
    (first point repeated last) is smoothed all round. The quad-edge kinks go, the curve's own shape stays."""
    P=np.asarray(P,float)
    if iterations<=0 or len(P)<3: return P
    closed=np.linalg.norm(P[0]-P[-1])<1e-12
    seg=np.linalg.norm(np.diff(P,axis=0),axis=1); at=np.r_[0.0,np.cumsum(seg)]; total=float(at[-1])
    if total<=0: return P
    n=max(3,int(np.ceil(total/max(spacing,1e-12))))
    want=np.linspace(0.0,total,n+1)
    Q=np.stack([np.interp(want,at,P[:,k]) for k in range(3)],1)
    if closed: Q=Q[:-1]
    if len(Q)<3: return P
    for _ in range(int(iterations)):
        for factor in (0.5,-0.53):
            if closed:
                L=(np.roll(Q,1,0)+np.roll(Q,-1,0))/2-Q
            else:
                L=np.zeros_like(Q); L[1:-1]=(Q[:-2]+Q[2:])/2-Q[1:-1]
            Q=Q+factor*L
    return np.vstack([Q,Q[:1]]) if closed else Q


def _contours(V,F,cam,normal_passes=3):
    """Interpolated contours (Hertzmann and Zorin 2000): the zero set of n·(eye − p) over smoothed vertex normals,
    one segment per triangle it crosses, chained through shared edges into long smooth curves. Along real creases
    (a cut base, a box's edges) averaged normals are meaningless, so there the contour is the exact one: the mesh
    edge between a front- and a back-facing face. Open boundaries are contours too.
    Returns [(points, normals)] so visibility can test them just off the surface."""
    E,FE,EF,counts=edge_topology(F)
    eye,_,_,w=cam.basis()
    FN,_=face_normals(V,F)
    front=FN@w>=0 if cam.ortho else (FN*(eye-V[F].mean(1))).sum(1)>=0
    # real creases: sharp edges in clusters long enough not to be sculpt noise
    inner=counts==2
    sharp=np.zeros(len(E),bool)
    sharp[inner]=(FN[EF[inner,0]]*FN[EF[inner,1]]).sum(1)<np.cos(np.radians(SHARP_CONTOUR))
    crease=np.zeros(len(E),bool)
    if sharp.any():
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        ids=np.flatnonzero(sharp); S=E[ids]
        _,label=connected_components(coo_matrix((np.ones(len(ids)),(S[:,0],S[:,1])),shape=(len(V),len(V))),directed=False)
        group=label[S[:,0]]
        length=np.bincount(group,weights=np.linalg.norm(V[S[:,0]]-V[S[:,1]],axis=1))
        crease[ids[length[group]>=SHARP_CONTOUR_SHARE*float(np.ptp(V,axis=0).max())]]=True
    near_crease=np.zeros(len(V),bool); near_crease[E[crease].ravel()]=True
    N=vertex_normals(V,F)
    for _ in range(int(normal_passes)):
        N=N+neighbour_average(N,E,len(V)); N/=np.maximum(np.linalg.norm(N,axis=1,keepdims=True),1e-30)
    if cam.ortho:
        g=N@w
    else:
        D=eye-V; g=(N*D).sum(1)/np.maximum(np.linalg.norm(D,axis=1),1e-30)
    g=np.where(np.abs(g)<1e-12,1e-12,g)
    a,b=E[:,0],E[:,1]
    crossed=np.sign(g[a])!=np.sign(g[b])
    t=np.zeros(len(E)); t[crossed]=g[a[crossed]]/(g[a[crossed]]-g[b[crossed]])
    point=V[a]+t[:,None]*(V[b]-V[a])
    normal=N[a]+t[:,None]*(N[b]-N[a])
    hit=crossed[FE]
    two=(hit.sum(1)==2)&~near_crease[F].any(1)
    segments=np.sort(FE[two][hit[two]].reshape(-1,2),axis=1) if two.any() else np.zeros((0,2),np.int64)
    out=[]
    if len(segments):
        chains,_=graph_chains(segments,np.arange(len(segments)))
        out+=[(point[c],normal[c]) for c in chains]
    flip=np.zeros(len(E),bool); flip[crease]=front[EF[crease,0]]!=front[EF[crease,1]]
    for ids in (np.flatnonzero(flip),np.flatnonzero(counts==1)):
        if len(ids):
            for c in graph_chains(E,ids)[0]:
                out.append((V[c],N[c]))
    return out


MAX_CONTOUR_LIFT = 1.0     # frame units (the model is 100 across): most a contour is lifted for its visibility test
SHARP_CONTOUR = 60.0        # degrees: on edges sharper than this the contour follows the mesh edge
SHARP_CONTOUR_SHARE = 0.08  # of the model's size: smaller clusters of sharp edges are sculpt noise, not creases


def _sample_with_normals(P,N,cam,frame,step):
    """_sample for a contour, carrying its normals along (the same points, the same interpolation)."""
    C=cam.to_camera(P); screen=frame.to_mm(cam.project(C))
    lengths=np.linalg.norm(np.diff(screen,axis=0),axis=1); out=[P[0]]; normals=[N[0]]
    for i,length in enumerate(lengths):
        ratio=1.0 if cam.ortho else max(C[i,2],C[i+1,2])/max(min(C[i,2],C[i+1,2]),NEAR)
        n=max(1,min(65536,int(np.ceil(length/step*ratio))))
        t=np.arange(1,n+1)/n
        out.extend(P[i]+t[:,None]*(P[i+1]-P[i])); normals.extend(N[i]+t[:,None]*(N[i+1]-N[i]))
    normals=np.array(normals)
    return np.array(out),normals/np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-30)


def _runs(P,visible,closed):
    """A polyline cut into [(visible, points)] where its samples' visibility changes; a closed loop that starts and
    ends in the same state is joined across its start."""
    cuts=np.flatnonzero(visible[1:]!=visible[:-1])+1
    bounds=[0,*cuts.tolist(),len(P)]
    runs=[]
    for a,b in zip(bounds[:-1],bounds[1:]):
        piece=P[max(0,a-1):b]                    # each run starts where the last one ended
        if len(piece)>=2: runs.append((bool(visible[a]),piece))
    if closed and len(runs)>1 and runs[0][0]==runs[-1][0]:
        state,last=runs.pop(); runs[0]=(state,np.vstack([last,runs[0][1][1:]]))
    return runs


def _close_gaps(runs,to_mm,gap):
    """Visibility runs [(visible, points)] of one curve with hidden stretches shorter than `gap` mm on paper, between
    visible ones, drawn after all: rays that graze the surface flicker, and that reads as a broken line."""
    if gap<=0 or len(runs)<3: return runs
    out=[]
    for i,(visible,P) in enumerate(runs):
        if (not visible and out and out[-1][0] and i+1<len(runs) and runs[i+1][0]
                and float(np.linalg.norm(np.diff(to_mm(P),axis=0),axis=1).sum())<gap):
            out[-1]=(True,np.vstack([out[-1][1],P[1:]])); continue
        if visible and out and out[-1][0]:
            out[-1]=(True,np.vstack([out[-1][1],P[1:]])); continue
        out.append((visible,P))
    return out


def _join_strokes(lines,tol=0.05,turn=40.0,reach=0.6):
    """Join strokes whose ends meet (within `tol` mm) and continue each other (they turn by at most `turn` degrees),
    best-aligned pairs first: a flowline crossing a junction, or a contour cut by a crossing, becomes one stroke.
    The plotter lifts the pen less and the drawing reads as long curves."""
    from scipy.spatial import cKDTree
    lines=[np.asarray(p,float) for p in lines if len(p)>=2]
    if len(lines)<2: return lines
    def outward(p,end):
        q=p if end==0 else p[::-1]
        d=np.cumsum(np.linalg.norm(np.diff(q,axis=0),axis=1))
        k=min(len(q)-1,int(np.searchsorted(d,reach))+1)
        v=q[0]-q[k]; n=np.linalg.norm(v)
        return v/n if n>0 else v
    pos=np.array([p[0] for p in lines]+[p[-1] for p in lines])
    dirs=np.array([outward(p,0) for p in lines]+[outward(p,1) for p in lines])
    n=len(lines); limit=-np.cos(np.radians(turn))
    pairs=cKDTree(pos).query_pairs(tol,output_type="ndarray")
    scored=sorted((float(dirs[x]@dirs[y]),int(x),int(y)) for x,y in pairs if x%n!=y%n)
    link={}
    for dot,x,y in scored:
        if dot>limit: break
        if x in link or y in link: continue
        link[x]=y; link[y]=x
    if not link: return lines
    used=np.zeros(n,bool); out=[]
    def walk(i,start_end):
        parts=[]; end_in=start_end
        while True:
            used[i]=True
            p=lines[i] if end_in==0 else lines[i][::-1]
            parts.append(p if not parts else p[1:])
            exit_end=1-end_in; node=exit_end*n+i
            other=link.get(node)
            if other is None: break
            j,e=other%n,other//n
            if used[j]: break
            i,end_in=j,e
        return np.vstack(parts)
    for i in range(n):
        if not used[i] and (i not in link or (n+i) not in link):
            out.append(walk(i,0 if i not in link else 1))
    for i in range(n):
        if not used[i]:
            out.append(walk(i,0))
    return out


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


def _clean_layers(layers,bounds,min_length,optimise,cancel,join=False,snap=0.0,floating=0.0):
    area=box(*bounds); ink=GeometryCollection(); out={}; removed=0
    for name in ("features","silhouette","flowlines","hidden"):
        accepted=[]
        source=layers.get(name,[])
        if join and name!="hidden":
            # join before the short-stroke filter, so a short piece between two junctions survives as part of a curve
            source=_join_strokes(source)
        for j,p in enumerate(source):
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
        if join and name!="hidden":
            accepted=_join_strokes(accepted)
        out[name]=accepted
    if snap>0 or floating>0:
        out=_tidy_free_ends(out,snap,floating)
    if optimise:
        out={name:order_polylines(lines) for name,lines in out.items()}
    return out,removed


def _tidy_free_ends(layers,snap,floating,touch=0.35):
    """Loose ends on paper. A stroke end that touches nothing but stops just short of another stroke (within `snap`
    mm, heading towards it) is drawn on to meet it, so a curve that runs behind a contour ends on that contour
    instead of a hair before it. A short open stroke (under `floating` mm) that touches nothing at either end is
    a floating fleck and is left out. Closed loops are kept as they are. The hidden layer is left alone."""
    from scipy.spatial import cKDTree
    names=[n for n in layers if n!="hidden"]
    strokes=[(n,i) for n in names for i in range(len(layers[n]))]
    if len(strokes)<2: return layers
    dense=[]; owner=[]
    for k,(n,i) in enumerate(strokes):
        p=layers[n][i]; seg=np.linalg.norm(np.diff(p,axis=0),axis=1); total=float(seg.sum())
        m=max(2,int(np.ceil(total/0.1))+1); at=np.r_[0.0,np.cumsum(seg)]; u=np.linspace(0,total,m)
        dense.append(np.stack([np.interp(u,at,p[:,0]),np.interp(u,at,p[:,1])],1)); owner.append(np.full(m,k))
    D=np.concatenate(dense); owner=np.concatenate(owner); tree=cKDTree(D)
    def free(k,e):
        return not any(owner[j]!=k for j in tree.query_ball_point(e,touch))
    out={n:list(layers[n]) for n in layers}; drop=set()
    for k,(n,i) in enumerate(strokes):
        p=out[n][i]
        if len(p)<2 or np.linalg.norm(p[0]-p[-1])<touch: continue
        length=float(np.linalg.norm(np.diff(p,axis=0),axis=1).sum())
        ends=[free(k,p[0]),free(k,p[-1])]
        if floating>0 and all(ends) and length<floating:
            drop.add((n,i)); continue
        for end,is_free in enumerate(ends):
            if not is_free or snap<=0: continue
            q=p if end==1 else p[::-1]
            e=q[-1]; back=q[max(0,len(q)-4)]
            t=e-back; t=t/(np.linalg.norm(t) or 1.0)
            best=None
            for j in tree.query_ball_point(e,snap):
                if owner[j]==k: continue
                d=D[j]-e; dist=float(np.linalg.norm(d))
                if dist<=1e-9 or float(d@t)<0.5*dist: continue      # only ahead, within 60 degrees
                if best is None or dist<best[0]: best=(dist,D[j])
            if best is not None:
                q=np.vstack([q,best[1]])
                p=q if end==1 else q[::-1]
        out[n][i]=p
    for n,i in sorted(drop,reverse=True):
        del out[n][i]
    return out


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
    # The network follows quad edges, so its curves kink at every proxy vertex. Smooth each curve between
    # junctions (they stay fixed); the samples are laid back on the surface below.
    segments=[np.linalg.norm(np.diff(network.vertices[c],axis=0),axis=1) for c in network.curves]
    spacing=float(np.mean(np.concatenate(segments)))/2 if segments else 0.0   # a quad-edge kink spans ~4 samples
    iterations=int(round(30*options.smoothing))
    raw=[]
    for kind,P in _split_junctions(display_network):
        P=_smooth_polyline(P,iterations//2 if kind=="trim" else iterations,spacing)
        raw.append(("features" if kind=="trim" else "flowlines",transform.apply(P)))
    contours=[]; lift=0.0
    if options.silhouette:
        if options.silhouette_style=="smooth":
            ER=edge_topology(F)[0]
            # A ray to a contour point grazes the surface, and sculpt noise next to it can hide it. Visibility is
            # tested half an edge off the surface along the normal; the contour is drawn where it lies (drawing the
            # lifted points moved the outline outward by as much, visibly so on coarse models).
            lift=min(0.5*float(np.linalg.norm(V[ER[:,0]]-V[ER[:,1]],axis=1).mean()),MAX_CONTOUR_LIFT)
            contours=_contours(V,F,cam,int(round(2+6*options.smoothing)))
        else:
            raw.extend(("silhouette",P) for P in _silhouettes(V,F,cam))
    def paper_length(P):
        return float(np.linalg.norm(np.diff(frame.to_mm(cam.project(cam.to_camera(P))),axis=0),axis=1).sum())
    curves=[]; kinds=[]; lifted={}
    for kind,P in raw:
        checkpoint(cancel)
        for piece in _clip_3d(P,cam,frame):
            curves.append(_sample(piece,cam,frame,options.sample_step)); kinds.append(kind)
    if contours:
        from scipy.spatial import cKDTree
        for P,N in contours:
            checkpoint(cancel)
            pieces=_clip_3d(P,cam,frame)
            tree=cKDTree(P) if len(pieces)!=1 or len(pieces[0])!=len(P) else None
            for piece in pieces:
                if paper_length(piece)<options.min_contour:
                    continue                 # a contour loop around a bump of sculpt noise
                normals=N if tree is None else N[tree.query(piece)[1]]
                S,SN=_sample_with_normals(piece,normals,cam,frame,options.sample_step)
                lifted[len(curves)]=S+lift*SN; curves.append(S); kinds.append("silhouette")
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
    runs=[None]*len(curves)
    ids=[i for i in range(len(curves)) if i not in lifted]
    if ids:
        found=raycast.split_visible(caster,cam,[curves[i] for i in ids],[np.linalg.norm(curves[i][0]-curves[i][-1])<1e-8 for i in ids],
                                    eps=0.015,iters=12,hidden=True)
        for i,r in zip(ids,found): runs[i]=r
    if lifted:
        order=sorted(lifted)
        flags=raycast.visible(caster,cam,np.concatenate([lifted[i] for i in order]),0.015)
        cursor=0
        for i in order:
            n=len(curves[i]); runs[i]=_runs(curves[i],flags[cursor:cursor+n],np.linalg.norm(curves[i][0]-curves[i][-1])<1e-8)
            cursor+=n
    to_mm=lambda P: frame.to_mm(cam.project(cam.to_camera(P)))
    layers={"features":[],"silhouette":[],"flowlines":[],"hidden":[]}
    for kind,parts in zip(kinds,runs):
        for visible,P in _close_gaps(parts,to_mm,options.gap):
            if visible or options.hidden:
                layers[kind if visible else "hidden"].append(to_mm(P))
    progress_to(progress,0.76,"joining, removing overlap and ordering plotter strokes",cancel)
    W,H=frame.sheet; x,y,w,h=frame.area; half=options.line_width/2
    bounds=(x+half,y+half,x+w-half,y+h-half)
    layers,removed=_clean_layers(layers,bounds,options.min_length,options.optimise,cancel,join=options.join,
                                 snap=options.snap,floating=options.floating)
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

