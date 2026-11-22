"""GUI-free height sampling, budgeted conforming refinement and local relief protection; welded geometry throughout.

Refinement splits edges at shared midpoints (conforming and planar): first to the sampling length, then where the
sampled height error exceeds Detail tolerance, a share of the relief, worst first and never below a floor tied to the
element. It never fails on Maximum output faces: the last pass takes the worst faces that fit and says how much of the
surface met the tolerance. Displacement follows smoothed normals, and protection restrains the relief only where it
would flip a triangle or push one piece of skin through another, fading into the surroundings.
"""
from pathlib import Path
import hashlib
import json
import os
import tempfile
import time
import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates

ASSETS=Path(__file__).with_name('assets')
MEMORY_FACES=3_500_000     # about 768 bytes of working memory a face: arrays, sampling and protection
BATCH=65536                # points sampled at a time
NORMAL_RADIUS=.25          # displacement normals are smoothed over this share of an element


def catalog():
    return json.loads((ASSETS/'catalog.json').read_text(encoding='utf-8'))


def texture_path(p):
    if p['source']=='custom':
        if not p['heightmap']:
            raise ValueError('Choose a custom grayscale PNG or TIFF heightmap.')
        path=Path(p['heightmap'])
        if path.suffix.lower() not in ('.png','.tif','.tiff') or not path.is_file():
            raise ValueError('The custom heightmap must be an existing PNG or TIFF file.')
        return path,None
    entry=next(t for t in catalog()['textures'] if t['id']==p['texture'])
    path=ASSETS/entry['file']
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:
        raise ValueError('The bundled heightmap is missing or damaged. Reinstall Atlas.')
    return path,entry


def read_height(path):
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(path) as image:
            if image.width*image.height>16777216:
                raise ValueError('Heightmaps are limited to 16 million pixels. Resize this image first.')
            image.load()
            mode=image.mode
            a=np.array(image)
    except (UnidentifiedImageError,OSError) as error:
        raise ValueError('Cannot decode this heightmap as PNG/TIFF.') from error
    alpha=None
    if a.ndim==3:
        if a.shape[2] in (2,4):
            alpha=a[...,-1]
            a=a[...,:-1]
        if a.shape[2]==3 and not (np.array_equal(a[...,0],a[...,1]) and np.array_equal(a[...,0],a[...,2])):
            raise ValueError('Use a grayscale heightmap; a colour photograph is not a calibrated heightmap.')
        a=a[...,0]
    if a.ndim!=2 or min(a.shape)<2:
        raise ValueError('Heightmaps need at least two rows and columns.')
    if np.issubdtype(a.dtype,np.floating):
        scale=1.
    elif mode.startswith('I'):
        scale=65535.
    else:
        scale=float(np.iinfo(a.dtype).max) if a.dtype!=np.bool_ else 1.
    a=a.astype(np.float64)/scale
    alpha=np.ones_like(a) if alpha is None else alpha.astype(float)/scale
    if not np.isfinite(a).all() or a.min()<0 or a.max()>1 or not np.isfinite(alpha).all():
        raise ValueError('Height values must be finite and between black and white (float TIFF: 0..1).')
    return a,np.clip(alpha,0,1)


def prepare(p,ctx):
    from .backend import cache_root
    path,entry=texture_path(p)
    if path.stat().st_size>256*1024**2:
        raise ValueError('Heightmap files are limited to 256 MiB. Resize or compress this image first.')
    raw=path.read_bytes()
    elements=float(entry['elements']) if entry else 1.
    key=hashlib.sha256(raw+json.dumps([1,elements,p['suppression'],p['contrast'],p['crispness'],p['invert_height']]).encode()).hexdigest()
    cache=cache_root(); cache.mkdir(parents=True,exist_ok=True)
    cached=cache/('height-'+key+'.npz')
    if cached.exists():
        try:
            with np.load(cached,allow_pickle=False) as data:
                return data['height'],data['alpha'],elements,True
        except (ValueError,OSError,KeyError,EOFError):
            pass
    a,alpha=read_height(path)
    ctx.check_cancel()
    sigma=float(p['suppression'])*min(a.shape)/elements*.065
    if sigma>0:
        a=gaussian_filter(a*alpha,sigma,mode='wrap')/np.maximum(gaussian_filter(alpha,sigma,mode='wrap'),1e-12)
    if p['invert_height']:
        a=1-a
    a=np.clip((a-.5)*float(p['contrast'])+.5,0,1)
    a=(1-float(p['crispness']))*a+float(p['crispness'])*(a*a*(3-2*a))
    ctx.check_cancel()
    with tempfile.NamedTemporaryFile(dir=cache,suffix='.npz',delete=False) as staged:
        staging=Path(staged.name)
        np.savez_compressed(staged,height=a,alpha=alpha)
    try:
        os.replace(staging,cached)
    finally:
        staging.unlink(missing_ok=True)
    return a,alpha,elements,False



def edges(f):
    """Unique edges (sorted vertex pairs), each face's three edge ids, and how many faces share each edge."""
    s=np.sort(np.asarray(f,np.int64)[:,[[0,1],[1,2],[2,0]]],axis=2).reshape(-1,2)
    if not len(s):
        return np.zeros((0,2),np.int64),np.zeros((0,3),np.int64),np.zeros(0,np.int64)
    # One int64 key per edge: much faster than unique rows.
    key=s[:,0]*(int(s.max())+1)+s[:,1]
    _,index,inverse,counts=np.unique(key,return_index=True,return_inverse=True,return_counts=True)
    return s[index],inverse.reshape(-1,3),counts


class Graph:
    """The welded surface's edges, for distances measured along the skin (feathers, seams, borders)."""

    def __init__(self,v,e):
        from scipy.sparse import coo_matrix
        self.n=len(v)
        lengths=np.linalg.norm(v[e[:,1]]-v[e[:,0]],axis=1)
        self.csr=coo_matrix((np.r_[lengths,lengths],(np.r_[e[:,0],e[:,1]],np.r_[e[:,1],e[:,0]])),
                            shape=(self.n,self.n)).tocsr()

    def distance(self,indices,limit,nearest=False):
        """Distance to the nearest of `indices` (inf beyond `limit`); with nearest=True also which one it is."""
        from scipy.sparse.csgraph import dijkstra
        indices=np.unique(np.asarray(indices,np.int64))
        if not len(indices):
            far=np.full(self.n,np.inf)
            return (far,np.full(self.n,-1)) if nearest else far
        if nearest:
            dist,_,source=dijkstra(self.csr,directed=False,indices=indices,min_only=True,limit=limit,
                                   return_predecessors=True)
            return dist,source
        return dijkstra(self.csr,directed=False,indices=indices,min_only=True,limit=limit)


def smooth_normals(v,f,normals,radius):
    """Vertex normals averaged over about `radius` along the skin (one implicit diffusion step), so relief does not
    follow scan noise or fold in tight creases. Neighbours whose normals are more than 90 degrees apart (the two sides
    of a thin rim) do not mix."""
    from scipy import sparse
    from scipy.sparse.linalg import cg
    normals=np.asarray(normals,float)
    e,_,_=edges(f)
    if radius<=0 or not len(e):
        return normals.copy()
    lengths=np.maximum(np.linalg.norm(v[e[:,1]]-v[e[:,0]],axis=1),radius*1e-6)
    w=np.clip(np.einsum('ij,ij->i',normals[e[:,0]],normals[e[:,1]]),0,1)/lengths**2
    W=sparse.coo_matrix((np.r_[w,w],(np.r_[e[:,0],e[:,1]],np.r_[e[:,1],e[:,0]])),shape=(len(v),len(v))).tocsr()
    t=radius*radius/1.5
    d=np.asarray(W.sum(axis=1)).ravel()
    A=(sparse.diags(1+t*d)-t*W).tocsr()
    M=sparse.diags(1/(1+t*d))
    out=np.empty_like(normals)
    for k in range(3):
        out[:,k],_=cg(A,normals[:,k],x0=normals[:,k],rtol=1e-5,maxiter=300,M=M)
    length=np.linalg.norm(out,axis=1)
    ok=(length>.3)&(np.einsum('ij,ij->i',out,normals)>.3*length)
    out[ok]/=length[ok,None]
    out[~ok]=normals[~ok]
    return out


def smoothstep(t):
    t=np.clip(t,0,1)
    return t*t*(3-2*t)


CAP_FROM,CAP_TO=.55,.85     # |normal · cylinder axis| where cylindrical caps turn into a projection along the axis


class Field:
    def __init__(self,p,v,normals,heightmap,alpha,elements):
        self.p=p; self.heightmap=heightmap; self.alpha=alpha
        self.lo,self.hi=v.min(axis=0),v.max(axis=0)
        self.h=float(self.hi[2]-self.lo[2])
        if self.h<=1e-12:
            self.h=float(np.ptp(v,axis=0).max())
        self.element=float(p['element_size'])/100*self.h
        self.tile=self.element*elements
        # Relief and the refinement tolerance in model units: the tolerance is a share of the relief, so taller
        # relief does not need more faces.
        self.height=float(p['relief'])/100*self.h
        self.tolerance=max(float(p['detail_tolerance'])/100*self.height,1e-9*self.h)
        angle=np.deg2rad(float(p['texture_rotation']))
        self.rotation=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
        self.axis={'side':0,'front':1,'up':2}[p['cylinder_axis']]
        radial=[i for i in range(3) if i!=self.axis]
        self.radial=radial
        radius=np.linalg.norm(v[:,radial],axis=1)
        self.circumference=2*np.pi*float(np.median(radius))
        # Fit whole approximate motifs, rather than forcing an entire source
        # tile around the circumference (which caps scale on dense textures).
        self.repeats=max(1,round(self.circumference/self.element))/elements

    def sample(self,uv,periodic_u=False):
        uv=np.asarray(uv,float).reshape(-1,2)
        image_aspect=self.heightmap.shape[0]/self.heightmap.shape[1]
        if periodic_u:
            # A rotation in UV space would destroy angular periodicity. Rotate
            # with an integer shear instead, keeping a full revolution seamless.
            uv=uv.copy()
            angle=np.deg2rad(self.p['texture_rotation'])
            slope=np.sin(angle)/max(abs(np.cos(angle)),.125)
            sign=1 if np.cos(angle)>=0 else -1
            uv[:,0]=sign*uv[:,0]+slope*uv[:,1]
            uv[:,1]*=sign/(float(self.p['aspect'])*image_aspect)
        else:
            uv=uv@self.rotation
            uv[:,1]/=float(self.p['aspect'])*image_aspect
        uv+=np.array([self.p['offset_u'],self.p['offset_v']])
        coords=np.vstack((((-uv[:,1])%1)*self.heightmap.shape[0]-.5,(uv[:,0]%1)*self.heightmap.shape[1]-.5))
        value=map_coordinates(self.heightmap,coords,order=1,mode='grid-wrap',prefilter=False)
        alpha=map_coordinates(self.alpha,coords,order=1,mode='grid-wrap',prefilter=False)
        return value,alpha

    def mode(self,h):
        """Raised 0..1, engraved -1..0, balanced -0.5..0.5."""
        if self.p['relief_mode']=='engraved':
            return -h
        if self.p['relief_mode']=='balanced':
            return h-.5
        return h

    def cylinder(self,points):
        r=self.radial
        # +pi/2 starts the Up-axis seam behind the canonical model (+Y).
        angle=np.arctan2(points[:,r[1]],points[:,r[0]])-np.pi/2-np.deg2rad(self.p['seam_rotation'])
        phase=(angle/(2*np.pi))%1
        coords=np.column_stack((phase*self.repeats,points[:,self.axis]/self.tile))
        h,alpha=self.sample(coords,periodic_u=True)
        width=min(.25,max(.1,self.p['seam_feather']/100)*self.element/max(self.circumference,self.element))
        distance=np.minimum(phase,1-phase)
        blend=np.clip(distance/width,0,1)
        blend=.5*(1-blend*blend*(3-2*blend))
        mask=blend>0
        if mask.any():
            other=coords[mask].copy()
            other[:,0]+=np.where(phase[mask]<.5,self.repeats,-self.repeats)
            other_h,other_alpha=self.sample(other,periodic_u=True)
            t=blend[mask]
            coverage=(1-t)*alpha[mask]+t*other_alpha
            h[mask]=((1-t)*h[mask]*alpha[mask]+t*other_h*other_alpha)/np.maximum(coverage,1e-20)
            alpha[mask]=coverage
        return h,alpha

    def evaluate(self,points,normals,uv=None):
        shape=np.asarray(points).shape[:-1]
        points=np.asarray(points,float).reshape(-1,3)
        normals=np.asarray(normals,float).reshape(-1,3)
        if self.p['mapping']=='surface':
            h,alpha=self.sample(np.asarray(uv).reshape(-1,2)/self.tile)
            h=self.mode(h)
        elif self.p['mapping']=='cylindrical':
            h,alpha=self.cylinder(points)
            # Angular coordinates have no unique limit on the axis. Taper the
            # relief at the axis so refinement converges to a continuous field.
            t=smoothstep(np.linalg.norm(points[:,self.radial],axis=1)/(self.element*.5))
            h=self.mode(h)*t
            # Caps (the crown, under the chin) face along the axis, where angles crowd the motifs together: there
            # the texture is projected along the axis instead, blended by the normal.
            unit=np.abs(normals[:,self.axis])/np.maximum(np.linalg.norm(normals,axis=1),1e-20)
            w=smoothstep((unit-CAP_FROM)/(CAP_TO-CAP_FROM))
            cap=np.flatnonzero(w>0)
            if len(cap):
                cap_h,cap_alpha=self.sample(points[cap][:,self.radial]/self.tile)
                wc=w[cap]
                h[cap]=(1-wc)*h[cap]+wc*self.mode(cap_h)
                alpha[cap]=(1-wc)*alpha[cap]+wc*cap_alpha
        else:
            blend=np.abs(normals)**4
            blend/=np.maximum(blend.sum(axis=1,keepdims=True),1e-20)
            h=np.zeros(len(points)); alpha=np.zeros(len(points))
            for i,axes in enumerate(((1,2),(0,2),(0,1))):
                value,coverage=self.sample(points[:,axes]/self.tile)
                h+=blend[:,i]*value; alpha+=blend[:,i]*coverage
            h=self.mode(h)
        from atlas_toolkit.geom.influence import weights
        h*=alpha*weights(points,self.p,self.lo,self.hi)*self.height
        return h.reshape(shape)


def heights(field,points,normals,uv=None,ctx=None,batch=BATCH):
    """field.evaluate in batches, checking for cancellation."""
    points=np.asarray(points,float); normals=np.asarray(normals,float)
    out=np.empty(points.shape[:-1])
    flat_out=out.reshape(-1)
    p=points.reshape(-1,3); n=normals.reshape(-1,3)
    u=None if uv is None else np.asarray(uv,float).reshape(-1,2)
    for s in range(0,len(p),batch):
        if ctx is not None:
            ctx.check_cancel()
        flat_out[s:s+batch]=field.evaluate(p[s:s+batch],n[s:s+batch],None if u is None else u[s:s+batch])
    return out


TEMPLATES={0:((0,1,2),),1:((0,3,2),(3,1,2)),2:((1,4,0),(4,2,0)),
           3:((3,1,4),(0,3,2),(3,4,2)),4:((2,5,1),(5,0,1)),
           5:((5,0,3),(2,5,1),(5,3,1)),6:((4,2,5),(1,4,0),(4,5,0)),
           7:((0,3,5),(3,1,4),(5,4,2),(3,4,5))}


def active_faces(v,f,field):
    p=field.p
    if p['influence']=='all' or p['invert_influence']:
        return np.ones(len(f),bool)
    tri=v[f]
    feather=float(p['feather'])/100*field.h
    if p['influence']=='band':
        a,b=sorted((p['band_from'],p['band_to']))
        low=field.lo[2]+a/100*field.h-feather
        high=field.lo[2]+b/100*field.h+feather
        return (tri[:,:,2].max(axis=1)>=low)&(tri[:,:,2].min(axis=1)<=high)
    import trimesh
    centre=(field.lo+field.hi)/2+np.asarray(p['sphere_centre'])/100*(field.hi-field.lo)
    nearest=trimesh.triangles.closest_point(tri,np.broadcast_to(centre,(len(f),3)))
    return np.linalg.norm(nearest-centre,axis=1)<=p['sphere_radius']/100*field.h+feather


class Closure:
    """Longest-edge closure: every face touched by a split also splits its longest edge, so a neighbour of a refined
    face never becomes an ever thinner sliver. Only faces next to newly split edges are revisited."""

    def __init__(self,f,inv,counts,lengths):
        self.inv=inv; self.n=len(f)
        self.longest=inv[np.arange(len(f)),np.argmax(lengths[inv],axis=1)]
        order=np.argsort(inv.ravel(),kind='stable')
        owner=np.repeat(np.arange(len(f)),3)[order]
        start=np.r_[0,np.cumsum(counts)[:-1]]
        self.first=owner[start]
        self.second=np.where(counts>1,owner[np.minimum(start+1,len(owner)-1)],-1)

    def __call__(self,split):
        split=split.copy()
        faces=np.flatnonzero(split[self.inv].any(axis=1))
        while len(faces):
            longest=self.longest[faces]
            new=np.unique(longest[~split[longest]])
            if not len(new):
                break
            split[new]=True
            faces=np.r_[self.first[new],self.second[new]]
            faces=faces[faces>=0]
        return split

    def count(self,split):
        """Faces after splitting: a face with k split edges becomes k+1 faces."""
        return self.n+int(np.count_nonzero(split[self.inv]))

    def fit(self,make,n,budget):
        """The closed split of make(k) for the largest k in 0..n that fits the budget (make(n) is known not to)."""
        best=self(make(0))
        if self.count(best)>budget:
            return np.zeros_like(best)
        lo,hi=0,n
        while hi-lo>1:
            mid=(lo+hi)//2
            trial=self(make(mid))
            if self.count(trial)<=budget:
                lo,best=mid,trial
            else:
                hi=mid
        return best


def _errors(field,v,n,f,uv,vh,ids,e,inv,ctx):
    """Sampled interpolation error of faces `ids`: edge midpoints and centre against the linear surface. With vertex
    heights (triplanar, cylindrical) each edge is sampled once and its midpoint height kept for the split vertex."""
    if vh is not None:
        eid=np.unique(inv[ids])
        a,b=e[eid,0],e[eid,1]
        mn=n[a]+n[b]
        mn/=np.maximum(np.linalg.norm(mn,axis=1,keepdims=True),1e-20)
        hm=heights(field,(v[a]+v[b])/2,mn,ctx=ctx)
        edge_h=np.full(len(e),np.nan); edge_h[eid]=hm
        edge_err=np.zeros(len(e)); edge_err[eid]=np.abs(hm-(vh[a]+vh[b])/2)
        corners=f[ids]
        cn=n[corners].sum(axis=1)
        cn/=np.maximum(np.linalg.norm(cn,axis=1,keepdims=True),1e-20)
        centre=np.abs(heights(field,v[corners].mean(axis=1),cn,ctx=ctx)-vh[corners].mean(axis=1))
        return np.maximum(edge_err[inv[ids]].max(axis=1),centre),edge_h
    err=np.empty(len(ids))
    step=BATCH//8
    for s in range(0,len(ids),step):
        ctx.check_cancel()
        sub=ids[s:s+step]
        points,norm,cuv=v[f[sub]],n[f[sub]],uv[sub]
        h=field.evaluate(points,norm,cuv)
        mid=(points+np.roll(points,-1,axis=1))/2
        mn=(norm+np.roll(norm,-1,axis=1))/2
        mu=(cuv+np.roll(cuv,-1,axis=1))/2
        edge=np.abs(field.evaluate(mid,mn,mu)-(h+np.roll(h,-1,axis=1))/2).max(axis=1)
        centre=np.abs(field.evaluate(points.mean(axis=1),norm.mean(axis=1),cuv.mean(axis=1))-h.mean(axis=1))
        err[s:s+step]=np.maximum(edge,centre)
    return err,None


def refine(v,f,n,uv,chart,field,ctx,budget=None):
    """Conforming midpoint refinement within `budget` faces: first to the sampling length (element / 6, High / 10),
    then where the sampled error exceeds the detail tolerance, worst first and never below element / 24 (High / 40).
    It never fails on the budget: the last pass takes the worst faces that fit and stops.

    Returns v, f, n, uv, chart, per-vertex heights (None for surface charts, whose heights belong to face corners),
    each face's source face, and a summary (budget_reached, coarsened, passes, unmet share of the area, residual)."""
    p=field.p
    budget=int(p['max_faces'] if budget is None else budget)
    maximum=field.element/(6 if p['quality']=='standard' else 10)
    floor=field.element/(24 if p['quality']=='standard' else 40)
    tolerance=field.tolerance
    parent=np.arange(len(f))
    fresh=np.ones(len(f),bool); error=np.zeros(len(f)); active=np.zeros(len(f),bool)
    vh=heights(field,v,n,ctx=ctx) if uv is None else None
    info=dict(budget_reached=False,coarsened=False,passes=0,unmet=0.,residual=0.)
    stop=False; want=np.zeros(len(f),bool)
    for iteration in range(64):
        ctx.progress(.22+min(iteration,16)*.02,f'Refining texture detail: {len(f):,} faces')
        e,inv,counts=edges(f)
        lengths=np.linalg.norm(v[e[:,1]]-v[e[:,0]],axis=1)
        edge_h=None
        if fresh.any():
            ids=np.flatnonzero(fresh)
            active[ids]=active_faces(v,f[ids],field)
            error[ids]=0.
            ids=ids[active[ids]]
            if len(ids):
                error[ids],edge_h=_errors(field,v,n,f,uv,vh,ids,e,inv,ctx)
            fresh[:]=False
        want=active&(error>tolerance)&(lengths[inv].max(axis=1)>floor*(1+1e-9))
        if stop:
            break
        required=np.zeros(len(e),bool)
        required[inv[active].ravel()]=True
        must=required&(lengths>maximum*(1+1e-9))
        if not must.any() and not want.any():
            break
        closure=Closure(f,inv,counts,lengths)
        split=closure(must)
        if closure.count(split)>budget:
            # Even the sampling length does not fit: split the longest edges that do, and stop.
            info.update(coarsened=True,budget_reached=True); stop=True
            cand=np.flatnonzero(must); cand=cand[np.argsort(-lengths[cand],kind='stable')]
            def make(k,cand=cand,size=len(e)):
                mask=np.zeros(size,bool); mask[cand[:k]]=True
                return mask
            split=closure.fit(make,len(cand),budget)
        else:
            order=np.flatnonzero(want); order=order[np.argsort(-error[order],kind='stable')]
            # Red-refine an error triangle, rather than repeatedly cutting one edge and creating ever thinner
            # green-transition slivers; but never an edge already below the floor: a long thin sliver (the fan that
            # closes a cut neck) is then cut along its length only, instead of into ever smaller crumbs.
            long_enough=lengths>floor*(1+1e-9)
            full=split.copy(); full[inv[order].ravel()]=True; full&=long_enough|split
            full=closure(full)
            if closure.count(full)<=budget:
                split=full
            else:
                info['budget_reached']=True; stop=True
                def make(k,base=split,order=order,long_enough=long_enough):
                    mask=base.copy(); mask[inv[order[:k]].ravel()]=True
                    return mask&(long_enough|base)
                split=closure.fit(make,len(order),budget)
        if not split.any():
            break
        info['passes']=iteration+1
        # Split: shared edge midpoints keep the surface conforming.
        midpoint=np.full(len(e),-1,np.int64)
        midpoint[split]=np.arange(int(split.sum()))+len(v)
        pair=e[split]
        new_v=v[pair].mean(axis=1)
        new_n=n[pair].sum(axis=1)
        new_n/=np.maximum(np.linalg.norm(new_n,axis=1,keepdims=True),1e-20)
        if vh is not None:
            new_h=edge_h[split].copy() if edge_h is not None else np.full(len(new_v),np.nan)
            missing=np.isnan(new_h)
            if missing.any():
                new_h[missing]=heights(field,new_v[missing],new_n[missing],ctx=ctx)
            vh=np.r_[vh,new_h]
        mids=midpoint[inv]
        pattern=(mids>=0).astype(np.uint8)@np.array([1,2,4],np.uint8)
        corners=np.column_stack((f,mids))
        all_uv=np.concatenate((uv,(uv+np.roll(uv,-1,axis=1))/2),axis=1) if uv is not None else None
        out=dict(f=[],parent=[],fresh=[],error=[],active=[],uv=[],chart=[])
        for code,templates in TEMPLATES.items():
            mask=pattern==code
            count=int(mask.sum())
            if not count:
                continue
            for template in templates:
                out['f'].append(corners[mask][:,template]); out['parent'].append(parent[mask])
                kept=code==0
                out['fresh'].append(np.full(count,not kept))
                out['error'].append(error[mask] if kept else np.zeros(count))
                out['active'].append(active[mask] if kept else np.zeros(count,bool))
                if uv is not None:
                    out['uv'].append(all_uv[mask][:,template,:]); out['chart'].append(chart[mask])
        v=np.vstack((v,new_v)); n=np.vstack((n,new_n)); f=np.vstack(out['f'])
        parent=np.concatenate(out['parent']); fresh=np.concatenate(out['fresh'])
        error=np.concatenate(out['error']); active=np.concatenate(out['active'])
        if uv is not None:
            uv=np.vstack(out['uv']); chart=np.concatenate(out['chart'])
    else:
        ctx.warn('Texture refinement stopped after 64 passes; the relief is sampled as far as it got.')
        want=np.zeros(len(f),bool)
    area=np.linalg.norm(np.cross(v[f[:,1]]-v[f[:,0]],v[f[:,2]]-v[f[:,0]]),axis=1)
    total=float(area[active].sum())
    info['unmet']=float(area[want].sum()/total) if total>0 else 0.
    info['residual']=float(error[active].max()/max(field.height,1e-30)) if active.any() else 0.
    return v,f,n,uv,chart,vh,parent,info


def corner_heights(field,v,n,f,uv,ctx):
    """Per-vertex heights from face-corner samples (surface charts), area-weighted."""
    values=np.zeros(len(v)); area=np.zeros(len(v))
    for start in range(0,len(f),16384):
        ctx.check_cancel()
        stop=min(len(f),start+16384)
        idx=f[start:stop]
        h=field.evaluate(v[idx],n[idx],uv[start:stop])
        face_area=np.linalg.norm(np.cross(v[idx[:,1]]-v[idx[:,0]],v[idx[:,2]]-v[idx[:,0]]),axis=1)
        np.add.at(values,idx.ravel(),(h*face_area[:,None]).ravel())
        np.add.at(area,idx.ravel(),np.repeat(face_area,3))
    return values/np.maximum(area,1e-30)


def blend_seams(v,f,n,chart,values,e,inv,counts,graph,field,charts,ctx):
    """Cross-fade the two charts across each seam instead of fading the relief out: within Seam blend of a seam, a
    vertex also samples the neighbouring chart (its UV from that chart's own map) and the two meet half and half."""
    big=np.iinfo(np.int64).max
    low=np.full(len(e),big,np.int64); high=np.full(len(e),-1,np.int64)
    np.minimum.at(low,inv.ravel(),np.repeat(chart,3)); np.maximum.at(high,inv.ravel(),np.repeat(chart,3))
    seam=(counts==2)&(low!=high)
    if not seam.any():
        return values
    width=float(field.p['seam_feather'])/100*field.element
    dist,source=graph.distance(np.unique(e[seam]),width,nearest=True)
    band=np.flatnonzero(np.isfinite(dist)&(source>=0))
    own=np.empty(len(v),np.int64); own[f.ravel()]=np.repeat(chart,3)
    vlo=np.full(len(v),big,np.int64); vhi=np.full(len(v),-1,np.int64)
    np.minimum.at(vlo,f.ravel(),np.repeat(chart,3)); np.maximum.at(vhi,f.ravel(),np.repeat(chart,3))
    s=source[band]
    other=np.where(vhi[s]!=own[band],vhi[s],vlo[s])
    keep=other!=own[band]
    band,other=band[keep],other[keep]
    if not len(band):
        return values
    ctx.progress(.64,'Blending chart seams')
    uv=charts.locate(v[band],other,n[band])
    h=heights(field,v[band],n[band],uv,ctx=ctx)
    w=.5*(1-smoothstep(dist[band]/max(width,1e-30)))
    values=values.copy()
    values[band]=(1-w)*values[band]+w*h
    return values


def clearance(v,f,n,values,graph,width,ctx):
    """A scale per vertex that keeps its displacement under 45% of the free distance along it (two facing sheets
    each take less than half the gap), feathered along the skin so restrained spots fade instead of pitting."""
    from atlas_toolkit.geom.raycast import Caster
    caster=Caster(v,f)
    eps=max(float(np.ptp(v,axis=0).max())*2e-7,1e-10)
    ratio=np.ones(len(v))
    for start in range(0,len(v),BATCH*4):
        ctx.check_cancel()
        ids=np.arange(start,min(len(v),start+BATCH*4))
        ids=ids[np.abs(values[ids])>eps]
        if not len(ids):
            continue
        dirs=n[ids]*np.sign(values[ids])[:,None]
        origin=v[ids]+eps*dirs
        travelled=np.zeros(len(ids)); free=np.full(len(ids),np.inf); todo=np.arange(len(ids))
        for _ in range(4):
            dist,face,_,_=caster.intersect(origin[todo],dirs[todo])
            hit=np.isfinite(dist)
            # A face at this very vertex is not an obstacle: look past it.
            own=hit&(f[np.maximum(face,0)]==ids[todo,None]).any(axis=1)
            done=hit&~own
            free[todo[done]]=travelled[todo[done]]+dist[done]
            nxt=todo[own]
            if not len(nxt):
                break
            step=dist[own]+eps
            travelled[nxt]+=step; origin[nxt]+=dirs[nxt]*step[:,None]
            todo=nxt
        allowed=np.maximum(0,(free+eps)*.45)
        ratio[ids]=np.minimum(1,allowed/np.abs(values[ids]))
    restrained=np.flatnonzero(ratio<1-1e-9)
    if not len(restrained):
        return ratio
    dist,source=graph.distance(restrained,width,nearest=True)
    near=np.flatnonzero(np.isfinite(dist)&(source>=0))
    fade=np.ones(len(v))
    r=ratio[source[near]]
    fade[near]=r+(1-r)*smoothstep(dist[near]/width)
    return np.minimum(ratio,fade)


def protect(v,f,n,values,field,graph,ctx,collisions,dirty=None):
    """Displaced vertices with no flipped or collapsed triangle and, with `collisions`, no new crossing between
    triangles. Relief is restrained only where it would fail, fading into the surroundings over about a quarter of an
    element; nothing is scaled down globally. Zero relief is the valid source, so the restraint always converges.
    `dirty` faces surround the source's own crossings: pairs of them are left as they were.
    Returns the vertices and the scale applied to each vertex's displacement."""
    from atlas_toolkit.geom.contact import crossings
    from atlas_toolkit.tools.mesh_dilate.mesh_dilate import invalid_faces
    width=max(field.element*.25,1e-9*field.h)
    scale=np.ones(len(v))
    moving=np.abs(values)>0
    if not moving.any():
        return v.copy(),scale
    if collisions:
        ctx.progress(.7,'Measuring clearance along the relief')
        scale=clearance(v,f,n,values,graph,width,ctx)
    # Vertices moved since the last crossing test: only pairs with one of their faces can have started to cross.
    pending=moving.copy()
    for round_ in range(64):
        ctx.progress(.72+min(round_,12)*.01,'Checking relief contacts and topology')
        trial=v+n*(values*scale)[:,None]
        # Flipped and collapsed triangles cost almost nothing to find, and settling them first removes most of the
        # crossings too: the full crossing test then runs on a state with few contacts left.
        problem=f[invalid_faces(v,trial,f)].ravel()
        if not len(problem) and collisions and pending.any():
            hits=crossings(trial,f,active=pending[f].any(axis=1),check=ctx.check_cancel)
            pending[:]=False
            if dirty is not None and len(hits):
                hits=hits[~(dirty[hits[:,0]]&dirty[hits[:,1]])]
            problem=f[hits.ravel()].ravel()
        problem=np.unique(problem)
        if not len(problem):
            return trial,scale
        strength=.6 if round_<4 else .85 if round_<10 else 1.
        radius=width*2.**max(0,round_-16)
        fall=1-smoothstep(graph.distance(problem,radius)/radius)
        new=scale*(1-strength*fall)
        pending|=new<scale
        scale=new
    ctx.warn('Relief protection did not settle; the relief was removed where it still crossed.')
    return v.copy(),np.zeros(len(v))


def build(v,f,p,ctx):
    import trimesh
    from . import backend
    started=time.perf_counter(); times={}
    v=np.asarray(v,float).copy(); f=np.asarray(f,np.int64).copy()
    if p['relief']==0:
        return trimesh.Trimesh(v,f,process=False),dict(faces=len(f),relief=0.,limited=0.,uv_cached=False,
            height_cached=False,seconds=0.,budget_reached=False,unmet=0.)
    budget=min(int(p['max_faces']),MEMORY_FACES)
    base=trimesh.Trimesh(v,f,process=False)
    if (not np.isfinite(v).all() or not base.nondegenerate_faces().all() or not base.is_winding_consistent):
        raise ValueError('Surface Texture needs a finite, nondegenerate, consistently oriented surface. Repair the model first.')
    e,_,counts=edges(f)
    if (counts>2).any():
        raise ValueError('The source has nonmanifold edges. Repair or remesh it before texturing.')
    if base.is_watertight:
        trimesh.repair.fix_normals(base,multibody=True)
        f=np.asarray(base.faces,np.int64).copy()
    heightmap,alpha,elements,height_cached=prepare(p,ctx)
    field=Field(p,v,None,heightmap,alpha,elements)
    # A cut closed by a fan of long slivers (trimesh's slice_plane caps, as on the catalogue's cropped busts) would
    # be refined into hundreds of thousands of crumbs where the slivers meet. Re-triangulate such flat patches first;
    # the shape does not change.
    from atlas_toolkit.geom.repair import refill_flat_slivers
    v,f,refilled=refill_flat_slivers(v,f,longer_than=field.element/(6 if p['quality']=='standard' else 10))
    if refilled:
        ctx.log(f'Re-triangulated {refilled} flat patch{"es" if refilled>1 else ""} of long slivers (a capped cut) '
                'before refining; the shape is unchanged.')
        base=trimesh.Trimesh(v,f,process=False)
    collisions=bool(p['collision_protection'])
    dirty_source=None
    if collisions:
        # Midpoint refinement is planar, so the source's own crossings are all there are before displacement. A few
        # (a scan's stray fold) are left as they are, without relief around them; a badly broken surface is refused.
        ctx.progress(.03,'Checking the source surface')
        from atlas_toolkit.geom.contact import crossings
        existing=crossings(v,f,check=ctx.check_cancel)
        if len(existing):
            involved=np.unique(existing)
            if len(involved)>max(64,.01*len(f)):
                raise ValueError(f'The input surface has {len(existing):,} pairs of intersecting triangles. Repair it '
                                 '(Repair Mesh) before using collision-protected relief.')
            touched=np.zeros(len(v),bool); touched[f[involved].ravel()]=True
            dirty_source=touched[f].any(axis=1)
            ctx.warn(f'The source has {len(existing):,} pair{"s" if len(existing)>1 else ""} of intersecting '
                     'triangles; the relief leaves them out. Repair Mesh can remove them.')
    n=smooth_normals(v,f,np.array(base.vertex_normals),field.element*NORMAL_RADIUS)
    uv=chart=charts=None; uv_cached=False
    if p['mapping']=='surface':
        charts=backend.charts(v,f,ctx)
        uv,chart,uv_cached=charts.uv,charts.chart,charts.cached
    times['prepare']=time.perf_counter()-started
    source_faces=len(f)
    mark=time.perf_counter()
    v,f,n,uv,chart,vh,parent,info=refine(v,f,n,uv,chart,field,ctx,budget)
    times['refine']=time.perf_counter()-mark
    if source_faces>=budget:
        ctx.warn(f'The source has {source_faces:,} faces, at or above Maximum output faces ({budget:,}): the relief '
                 'is sampled on its own vertices without refinement. Raise the limit for finer relief.')
    elif info['budget_reached']:
        met=100*(1-info['unmet'])
        ctx.warn(f"Face budget reached at {len(f):,} faces: detail tolerance met on {min(met,99.9):.1f}% of the "
                 'textured surface. Raise Maximum output faces, Element size or Detail tolerance for more.')
    mark=time.perf_counter()
    ctx.progress(.61,'Sampling welded surface relief')
    values=vh.copy() if vh is not None else corner_heights(field,v,n,f,uv,ctx)
    e,inv,counts=edges(f)
    graph=Graph(v,e)
    if uv is not None and p['seam_feather']>0:
        values=blend_seams(v,f,n,chart,values,e,inv,counts,graph,field,charts,ctx)
    if p['preserve_borders']:
        border=np.unique(e[counts==1])
        if len(border):
            width=field.element*.25
            values*=smoothstep(graph.distance(border,width)/width)
    dirty=dirty_source[parent] if dirty_source is not None else None
    if dirty is not None:
        width=field.element*.25
        values*=smoothstep(graph.distance(np.unique(f[dirty]),width)/width)
    times['sample']=time.perf_counter()-mark
    mark=time.perf_counter()
    output,scale=protect(v,f,n,values,field,graph,ctx,collisions,dirty)
    times['protect']=time.perf_counter()-mark
    achieved=np.linalg.norm(output-v,axis=1)
    wanted=np.abs(values)
    limited=float(100*(1-achieved.sum()/wanted.sum())) if wanted.sum()>1e-20 else 0.
    result=trimesh.Trimesh(output,f,process=False)
    if base.is_watertight and not result.is_watertight:
        raise ValueError('Texturing lost closed topology; no output was published.')
    return result,dict(faces=len(f),relief=float(achieved.max())/field.h*100,limited=max(0.,limited),
        uv_cached=uv_cached,height_cached=height_cached,seconds=time.perf_counter()-started,
        charts=int(len(np.unique(chart))) if chart is not None else 0,element_size=p['element_size'],
        requested_relief=p['relief'],cylinder_repeats=field.repeats if p['mapping']=='cylindrical' else None,
        budget_reached=bool(info['budget_reached']),coarsened=bool(info['coarsened']),unmet=info['unmet'],
        passes=info['passes'],restrained_vertices=int(np.count_nonzero(scale<.999)),
        times={k:round(t,3) for k,t in times.items()})
