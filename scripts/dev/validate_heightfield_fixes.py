"""Reproduce the logged DEM failures and verify shared image reliefs without changing inputs."""
import argparse,hashlib,json,sys,time
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401


def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    from PIL import Image
    from atlas_toolkit.core.job import JobContext
    from atlas_toolkit.tools.raster_dem_to_mesh import TOOL as DEM
    from atlas_toolkit.tools.image_to_mesh import TOOL as IMAGE
    from atlas_toolkit.geom.surface import load_surface
    from atlas_toolkit.geom.mesh import save_mesh
    from atlas_toolkit.geom.heightfield.mesh import check_mesh
    from atlas_toolkit.core.registry import registry
    registry()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=qa_dir('heightfield111_validation'))
    parser.add_argument('--case',default='all')
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    source=studio_workspace()/'code/R/generators/3d_DEM/DEM20m_AMinho.tif'
    before=digest(source)
    image=args.out/'alpha-heightfield.png'
    y,x=np.mgrid[:960,:1200]
    valid=((x-570)/530)**2+((y-480)/430)**2<1
    valid[300:400,300:400]=False
    valid[600:620,700:730]=False
    rgba=np.empty((960,1200,4),np.uint8)
    rgba[:,:,:3]=np.clip(20+200*np.exp(-((x-600)**2+(y-450)**2)/160000),0,255).astype(np.uint8)[:,:,None]
    rgba[:,:,3]=valid*255
    Image.fromarray(rgba).save(image)
    cases=[('dem-a3-grid',DEM,source,dict(method='smooth',sampling='grid',paper='A3',orientation='landscape',pitch=.2,  # preset-leak: ok (a DEM case; it only resembles an image-to-mesh preset)
            smooth_radius=.6,z_factor=8.,contact_trim=.01,triangle_budget=5000000,geometry='surface'),('stl','obj','ply')),  # preset-leak: ok
           ('dem-a4-grid',DEM,source,dict(pitch=.1,contact_trim=.01,exclude_background=True,background=0.,
            boundary_error=.01,triangle_budget=7000000),('stl',)),
           ('dem-adaptive',DEM,source,dict(sampling='adaptive',contact_policy='automatic'),('stl',)),
           ('image-grid',IMAGE,image,dict(pitch=.35,gamma=1.2,contact_policy='automatic'),('stl','obj','ply')),
           ('image-adaptive',IMAGE,image,dict(sampling='adaptive',maximum_edge=2.,gamma=1.2,contact_policy='automatic',geometry='surface'),('stl',))]
    summary=[]
    for name,tool,src,overrides,formats in cases:
        if args.case not in ('all',name):continue
        p=tool.values(overrides)
        started=time.perf_counter()
        def emit(kind,data):
            if kind in ('progress','log'):print(name,kind,data,flush=True)
        result=tool.engine_fn()(JobContext(emit,lambda:False),src,args.out/(name+'.stl'),p)
        surface=load_surface(result.outputs[0])
        mesh=surface.as_trimesh()
        row=dict(case=name,seconds=round(time.perf_counter()-started,3),faces=len(mesh.faces),dimensions=mesh.extents.tolist(),
                 maximum_error_mm=result.data.get('maximum_error_mm'),formats=list(formats),values=dict(p))
        for fmt in formats:
            path=args.out/(name+'.'+fmt)
            if fmt!='stl':save_mesh(surface,path,tool)
            saved=load_surface(path).as_trimesh()
            check_mesh(saved,solid=p.geometry=='solid',budget=p.triangle_budget)
            np.testing.assert_allclose(saved.bounds,mesh.bounds,atol=2e-5,rtol=1e-6)
        summary.append(row)
        print(json.dumps(row),flush=True)
        (args.out/(name+'.json')).write_text(json.dumps(row,indent=2),encoding='utf-8')
    assert digest(source)==before,'Acceptance source was changed.'
    (args.out/('summary-'+args.case+'.json')).write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return 0


if __name__=='__main__':raise SystemExit(main())
