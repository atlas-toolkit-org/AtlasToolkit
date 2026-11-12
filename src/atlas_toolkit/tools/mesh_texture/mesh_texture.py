"""GUI-free height sampling and conforming surface refinement; welded geometry throughout."""
from pathlib import Path
import hashlib
import json
import os
import tempfile
import time
import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates

ASSETS=Path(__file__).with_name('assets')


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
    unique,inverse,counts=np.unique(np.sort(f[:,[[0,1],[1,2],[2,0]]],axis=2).reshape(-1,2),axis=0,return_inverse=True,return_counts=True)
    return unique,inverse.reshape(-1,3),counts


def distance_to_vertices(v,e,indices,limit):
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra
    if not len(indices):
        return np.full(len(v),np.inf)
    lengths=np.linalg.norm(v[e[:,1]]-v[e[:,0]],axis=1)
    graph=coo_matrix((np.r_[lengths,lengths],(np.r_[e[:,0],e[:,1]],np.r_[e[:,1],e[:,0]])),shape=(len(v),len(v))).tocsr()
    return dijkstra(graph,directed=False,indices=indices,min_only=True,limit=limit)


class Field:
    def __init__(self,p,v,normals,heightmap,alpha,elements):
        self.p=p; self.heightmap=heightmap; self.alpha=alpha
        self.lo,self.hi=v.min(axis=0),v.max(axis=0)
        self.h=float(self.hi[2]-self.lo[2])
        if self.h<=1e-12:
            self.h=float(np.ptp(v,axis=0).max())
        self.element=float(p['element_size'])/100*self.h
        self.tile=self.element*elements
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

    def evaluate(self,points,normals,uv=None):
        shape=np.asarray(points).shape[:-1]
        points=np.asarray(points).reshape(-1,3)
        normals=np.asarray(normals).reshape(-1,3)
        if self.p['mapping']=='surface':
            h,alpha=self.sample(np.asarray(uv).reshape(-1,2)/self.tile)
        elif self.p['mapping']=='cylindrical':
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
        else:
            blend=np.abs(normals)**4
            blend/=np.maximum(blend.sum(axis=1,keepdims=True),1e-20)
            h=np.zeros(len(points)); alpha=np.zeros(len(points))
            for i,axes in enumerate(((1,2),(0,2),(0,1))):
                value,coverage=self.sample(points[:,axes]/self.tile)
                h+=blend[:,i]*value; alpha+=blend[:,i]*coverage
        if self.p['relief_mode']=='engraved':
            h=-h
        elif self.p['relief_mode']=='balanced':
            h-=.5
        if self.p['mapping']=='cylindrical':
            # Angular coordinates have no unique limit on the axis. Taper the
            # relief at caps/poles so refinement can converge to a continuous
            # field instead of chasing a singularity indefinitely.
            t=np.clip(np.linalg.norm(points[:,self.radial],axis=1)/(self.element*.5),0,1)
            h*=t*t*(3-2*t)
        from atlas_toolkit.geom.influence import weights
        h*=alpha*weights(points,self.p,self.lo,self.hi)*(float(self.p['relief'])/100*self.h)
        return h.reshape(shape)


TEMPLATES={0:((0,1,2),),1:((0,3,2),(3,1,2)),2:((1,4,0),(4,2,0)),
           3:((3,1,4),(0,3,2),(3,4,2)),4:((2,5,1),(5,0,1)),
           5:((5,0,3),(2,5,1),(5,3,1)),6:((4,2,5),(1,4,0),(4,5,0)),
           7:((0,3,5),(3,1,4),(5,4,2),(3,4,5))}


def verify_contact(a,b,detail):
    """Reject numerical tangent reports whose returned point is outside a triangle.

    Refinement of adjacent original planes can put edge endpoints within a few
    ulps of the other plane. libigl may report a degenerate intersection segment
    even when the projected triangles are separated. Verify the actual contact,
    rather than exempting a whole neighbourhood of potentially real collisions.
    """
    import trimesh
    scale=max(float(np.ptp(np.vstack((a,b)),axis=0).max()),1e-15)
    epsilon=scale*1e-10
    na=np.cross(a[1]-a[0],a[2]-a[0]); na/=max(np.linalg.norm(na),1e-30)
    nb=np.cross(b[1]-b[0],b[2]-b[0]); nb/=max(np.linalg.norm(nb),1e-30)
    da=(b-a[0])@na; db=(a-b[0])@nb
    if any(np.all(d>epsilon) or np.all(d<-epsilon) for d in (da,db)):
        return False
    if np.linalg.norm(np.cross(na,nb))<1e-8 and np.max(np.abs(da))<=epsilon:
        from shapely.geometry import Polygon
        axis=int(np.argmax(np.abs(na)))
        return Polygon(np.delete(a,axis,axis=1)).intersection(Polygon(np.delete(b,axis,axis=1))).area>scale*scale*1e-14
    # Recompute edge/plane contacts instead of trusting libigl's degenerate
    # returned segment, which can contain nonfinite values in tangent cases.
    for tri,other,distance in ((a,b,db),(b,a,da)):
        candidates=[]
        for i in range(3):
            j=(i+1)%3
            if abs(distance[i])<=epsilon and abs(distance[j])<=epsilon:
                candidates.extend((tri[i],tri[j],(tri[i]+tri[j])/2))
            elif ((distance[i]<=epsilon and distance[j]>=-epsilon) or
                  (distance[j]<=epsilon and distance[i]>=-epsilon)):
                denom=distance[i]-distance[j]
                if abs(denom)>1e-30:
                    t=distance[i]/denom
                    if -1e-10<=t<=1+1e-10:
                        candidates.append(tri[i]+np.clip(t,0,1)*(tri[j]-tri[i]))
        if candidates:
            points=np.array(candidates)
            nearest=trimesh.triangles.closest_point(np.broadcast_to(other,(len(points),3,3)),points)
            if np.any(np.linalg.norm(nearest-points,axis=1)<=epsilon):
                return True
    return False


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


def refine(v,f,n,uv,chart,field,ctx):
    budget=int(field.p['max_faces'])
    maximum=field.element/(6 if field.p['quality']=='standard' else 10)
    tolerance=float(field.p['tolerance'])/100*field.h
    for iteration in range(24):
        ctx.progress(.22+min(iteration,16)*.02,f'Refining texture detail: {len(f):,} faces')
        e,inv,counts=edges(f)
        active=active_faces(v,f,field)
        required=np.zeros(len(e),bool)
        required[inv[active].ravel()]=True
        edge_lengths=np.linalg.norm(v[e[:,1]]-v[e[:,0]],axis=1)
        split=required&(edge_lengths>maximum*(1+1e-9))
        # Midpoint and centroid error samples avoid spending faces on flat tiles.
        # Batches cap temporary height/coordinate arrays even on dense input scans.
        worst=0.; worst_points=None
        for start in range(0,len(f),8192):
            ctx.check_cancel()
            stop=min(len(f),start+8192)
            ids=np.arange(start,stop)[active[start:stop]]
            if not len(ids):
                continue
            points,norm=v[f[ids]],n[f[ids]]
            corner_uv=uv[ids] if uv is not None else None
            h=field.evaluate(points,norm,corner_uv)
            mid=(points+np.roll(points,-1,axis=1))/2
            mn=(norm+np.roll(norm,-1,axis=1))/2
            mu=(corner_uv+np.roll(corner_uv,-1,axis=1))/2 if uv is not None else None
            error=np.abs(field.evaluate(mid,mn,mu)-(h+np.roll(h,-1,axis=1))/2)
            ce=np.abs(field.evaluate(points.mean(axis=1),norm.mean(axis=1),corner_uv.mean(axis=1) if uv is not None else None)-h.mean(axis=1))
            if float(max(error.max(),ce.max()))>worst:
                worst=float(max(error.max(),ce.max()))
                worst_points=points[np.argmax(np.maximum(error.max(axis=1),ce))].copy()
            marked=(error.max(axis=1)>tolerance)|(ce>tolerance)
            # Red-refine an error triangle, rather than repeatedly cutting one
            # edge and creating ever thinner green-transition slivers.
            split[inv[ids[marked]].ravel()]=True
        if not split.any():
            return v,f,n,uv,chart
        # Longest-edge closure prevents successive green neighbours from
        # acquiring arbitrarily thin slivers along a coarse edge. Every face
        # touched by a split must also split its longest edge.
        longest=inv[np.arange(len(f)),np.argmax(edge_lengths[inv],axis=1)]
        while True:
            ctx.check_cancel()
            missing=split[inv].any(axis=1)&~split[longest]
            if not missing.any():
                break
            split[longest[missing]]=True
        requested=int(len(f)+np.count_nonzero(split[inv]))
        # Conservative accounting for current arrays plus per-face refinement,
        # sampling, topology and collision temporaries, capped at 1.5 GiB.
        if requested>budget or requested*768>1536*1024**2:
            raise ValueError(f'Texture refinement needs at least {requested:,} faces; limit {budget:,}. Increase Element size / Displacement tolerance, choose Standard sampling, or raise Maximum output faces.')
        midpoint=np.full(len(e),-1,np.int64)
        midpoint[split]=np.arange(split.sum())+len(v)
        new_v=v[e[split]].mean(axis=1)
        new_n=n[e[split]].mean(axis=1)
        new_n/=np.maximum(np.linalg.norm(new_n,axis=1,keepdims=True),1e-20)
        mids=midpoint[inv]
        pattern=(mids>=0).astype(np.uint8)@np.array([1,2,4],np.uint8)
        all_vertices=np.column_stack((f,mids))
        all_uv=np.concatenate((uv,(uv+np.roll(uv,-1,axis=1))/2),axis=1) if uv is not None else None
        out_f=[]; out_uv=[]; out_chart=[]
        for code,templates in TEMPLATES.items():
            mask=pattern==code
            for template in templates:
                out_f.append(all_vertices[mask][:,template])
                if uv is not None:
                    out_uv.append(all_uv[mask][:,template,:]); out_chart.append(chart[mask])
        v=np.vstack((v,new_v)); n=np.vstack((n,new_n)); f=np.vstack(out_f)
        uv=np.vstack(out_uv) if uv is not None else None
        chart=np.concatenate(out_chart) if chart is not None else None
    ctx.log(f'Refinement residual {worst}; tolerance {tolerance}; triangle {worst_points}')
    raise ValueError('Texture refinement did not converge; increase displacement tolerance or element size.')


def build(v,f,p,ctx):
    import trimesh
    from . import backend
    started=time.perf_counter()
    v=np.asarray(v,float).copy(); f=np.asarray(f,np.int64).copy()
    if p['relief']==0:
        return trimesh.Trimesh(v,f,process=False),dict(faces=len(f),relief=0.,limited=0.,uv_cached=False,height_cached=False,seconds=0.)
    if len(f)>int(p['max_faces']):
        raise ValueError('The source already exceeds Maximum output faces. Increase the limit or use Remesh/Decimate first.')
    base=trimesh.Trimesh(v,f,process=False)
    if (not np.isfinite(v).all() or not base.nondegenerate_faces().all() or not base.is_winding_consistent):
        raise ValueError('Surface Texture needs a finite, nondegenerate, consistently oriented surface. Repair the model first.')
    e,_,counts=edges(f)
    if (counts>2).any():
        raise ValueError('The source has nonmanifold edges. Repair or remesh it before texturing.')
    if base.is_watertight:
        trimesh.repair.fix_normals(base,multibody=True)
        f=np.asarray(base.faces,np.int64).copy()
    n=np.array(base.vertex_normals)
    heightmap,alpha,elements,height_cached=prepare(p,ctx)
    field=Field(p,v,n,heightmap,alpha,elements)
    uv=chart=None; uv_cached=False
    if p['mapping']=='surface':
        uv,chart,uv_cached=backend.unwrap(v,f,ctx)
    v,f,n,uv,chart=refine(v,f,n,uv,chart,field,ctx)
    ctx.progress(.61,'Sampling welded surface relief')
    values=np.zeros(len(v)); area=np.zeros(len(v))
    for start in range(0,len(f),16384):
        ctx.check_cancel()
        stop=min(len(f),start+16384)
        idx=f[start:stop]
        h=field.evaluate(v[idx],n[idx],uv[start:stop] if uv is not None else None)
        face_area=np.linalg.norm(np.cross(v[idx[:,1]]-v[idx[:,0]],v[idx[:,2]]-v[idx[:,0]]),axis=1)
        np.add.at(values,idx.ravel(),(h*face_area[:,None]).ravel())
        np.add.at(area,idx.ravel(),np.repeat(face_area,3))
    values/=np.maximum(area,1e-30)
    e,inv,counts=edges(f)
    if uv is not None and p['seam_feather']>0:
        # Find actual chart joins, not duplicated geometric vertices.
        low=np.full(len(e),np.iinfo(np.int32).max,np.int64); high=np.full(len(e),-1,np.int64)
        np.minimum.at(low,inv.ravel(),np.repeat(chart,3)); np.maximum.at(high,inv.ravel(),np.repeat(chart,3))
        seams=np.unique(e[(counts==2)&(low!=high)])
        width=p['seam_feather']/100*field.element
        dist=distance_to_vertices(v,e,seams,width)
        t=np.clip(dist/width,0,1); values*=t*t*(3-2*t)
    if p['preserve_borders']:
        border=np.unique(e[counts==1])
        if len(border):
            width=field.element*.25
            dist=distance_to_vertices(v,e,border,width)
            t=np.clip(dist/width,0,1); values*=t*t*(3-2*t)
    proposed=v+n*values[:,None]
    wanted=np.linalg.norm(proposed-v,axis=1)
    ctx.progress(.68,'Checking relief contacts and topology')
    from atlas_toolkit.tools.mesh_dilate.mesh_dilate import ContactGuard, intersections, valid_faces
    if p['collision_protection'] and np.any(values):
        guard=ContactGuard(v,f,ctx.check_cancel,verify=verify_contact)
        if guard.existing:
            raise ValueError('The input surface has intersecting triangles. Repair it before using collision-protected relief.')
        # Limit swept normal segments as well as testing the final triangles.
        # Two facing sheets can otherwise pass completely through each other
        # and end with no intersecting endpoint triangles. Reserve over half
        # each gap for the opposing sheet's own motion.
        from atlas_toolkit.geom.raycast import Caster
        caster=Caster(v,f)
        epsilon=max(float(np.ptp(v,axis=0).max())*2e-7,1e-10)
        bounded=values.copy()
        for start in range(0,len(v),16384):
            ctx.check_cancel()
            ids=np.arange(start,min(len(v),start+16384))
            ids=ids[wanted[ids]>epsilon]
            directions=n[ids]*np.sign(values[ids,None])
            distance,*_=caster.intersect(v[ids]+epsilon*directions,directions)
            clearance=np.maximum(0,(distance+epsilon)*.45)
            bounded[ids]=np.sign(values[ids])*np.minimum(np.abs(values[ids]),clearance)
        output,_=guard.limit(v,v+n*bounded[:,None])
    else:
        output=proposed
    if not valid_faces(v,output,f):
        raise ValueError('Relief creates flipped or collapsed triangles. Enable Collision protection or lower relief height.')
    achieved=np.linalg.norm(output-v,axis=1)
    limited=float(100*(1-achieved.sum()/wanted.sum())) if wanted.sum()>1e-20 else 0.
    result=trimesh.Trimesh(output,f,process=False)
    if base.is_watertight and not result.is_watertight:
        raise ValueError('Texturing lost closed topology; no output was published.')
    return result,dict(faces=len(f),relief=float(achieved.max())/field.h*100,limited=max(0.,limited),
        uv_cached=uv_cached,height_cached=height_cached,seconds=time.perf_counter()-started,
        charts=int(len(np.unique(chart))) if chart is not None else 0,element_size=p['element_size'],
        requested_relief=p['relief'],cylinder_repeats=field.repeats if p['mapping']=='cylindrical' else None)
