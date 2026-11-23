"""Atlas Surface Texture adapter: source units, final geometry preview and staged publication."""
from pathlib import Path
import hashlib
import json
import os
import tempfile
import numpy as np
from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import frame3d
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.mesh_export import publish_meshes
from . import TOOL
from . import mesh_texture as mt


def make_sample(folder):
    import trimesh
    path=Path(folder)/'texture ellipsoid.stl'
    m=trimesh.creation.icosphere(subdivisions=3,radius=20.)
    m.vertices*=np.array([.7,.65,1.])
    m.export(path)
    return path


def migrate(values,from_version):
    """Before 0.2.0 the refinement tolerance was a share of the model's height ('tolerance', % h). It is now a share
    of the relief itself ('detail_tolerance'), converted here with the preset's own relief. The face budget belongs
    to the machine (preset_skip), so a preset that still carries one, as 0.1 presets do, no longer sets it."""
    if 'tolerance' in values and 'detail_tolerance' not in values:
        relief=float(values.get('relief') or .4)
        values['detail_tolerance']=float(values.pop('tolerance'))/max(relief,1e-6)*100
    values.pop('max_faces',None)
    return values


def input_dependencies(src,values):
    # One-file releases extract assets to a different _MEI directory on every
    # launch. Content-addressed persistent markers avoid putting those transient
    # paths/mtimes into the shared workflow cache key.
    from .backend import cache_root
    files=[]
    identity={'revision':1}
    if values.get('source')=='custom':
        if values.get('heightmap'):
            files.append(Path(values['heightmap']))
    else:
        path,_=mt.texture_path(values)
        identity['catalog']=hashlib.sha256((mt.ASSETS/'catalog.json').read_bytes()).hexdigest()
        identity['heightmap']=hashlib.sha256(path.read_bytes()).hexdigest()
    if values.get('mapping')=='surface':
        from .backend import find_backend
        path=find_backend()
        if path:
            identity['helper']=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    encoded=json.dumps(identity,sort_keys=True).encode()
    folder=cache_root()/'dependencies'; folder.mkdir(parents=True,exist_ok=True)
    marker=folder/(hashlib.sha256(encoded).hexdigest()+'.json')
    if not marker.exists():
        with tempfile.NamedTemporaryFile(dir=folder,delete=False) as temp:
            staged=Path(temp.name); temp.write(encoded)
        try:
            if not marker.exists():
                os.replace(staged,marker)
        finally:
            staged.unlink(missing_ok=True)
    files.append(marker)
    return files


def run(ctx,src,dst,p):
    src=Path(src)
    if ctx.dry_run:
        dst=None
    if dst is not None and Path(dst).resolve()==src.resolve():
        raise ValueError('The output must not replace the input model.')
    ctx.progress(.01,'Loading surface')
    original=gm.load_mesh(src)
    up,front,rotate,_=frame3d.resolve_axes(src,p,original.extents)
    frame=frame3d.fit_frame(original.vertices,up,front,rotate,mode='height',size=2.)
    result,data=mt.build(frame.apply(original.vertices),original.faces,p,ctx)
    if float(p.relief)==0:
        result=original.copy()
    else:
        result.vertices=frame.inverse(result.vertices)
    closed=original.is_watertight
    expected_faces=len(result.faces)
    def validate_saved(path):
        saved=gm.load_mesh(path)
        if (len(saved.faces)!=expected_faces or not np.isfinite(saved.vertices).all() or
                not saved.nondegenerate_faces().all() or not saved.is_winding_consistent or
                (closed and not saved.is_watertight)):
            raise ValueError('The saved relief lost valid surface topology. Recenter large coordinates or increase texture size / reduce detail.')
    outputs=[]
    ctx.check_cancel()
    if dst is not None:
        from atlas_toolkit.geom.surface import from_mesh
        surface=from_mesh(result)
        surface.frame=frame.frame_values()
        outputs=publish_meshes([surface],dst,ctx,TOOL,validator=validate_saved)
    # Restraint is local (creases, near contacts); say so when it takes a noticeable share of the relief.
    if data['limited']>5:
        ctx.warn(f"Protection restrained {data['limited']:.1f}% of the total displacement, in creases and near contacts; achieved maximum {data['relief']:.3g}% h.")
    if not p.collision_protection:
        ctx.warn('Collision protection is disabled; triangles were kept valid but crossings were not checked.')
    times=data.get('times',{})
    budget=('budget reached: tolerance met on '+f"{min(100*(1-data.get('unmet',0)),99.9):.1f}% of the surface"
            if data.get('budget_reached') else 'tolerance met')
    report=[src.name,f'  texture    {p.texture if p.source=="library" else Path(p.heightmap).name}; {p.relief_mode}; {p.mapping}',
        f'  element    {p.element_size:g}% h; requested relief {p.relief:g}% h; detail tolerance {p.detail_tolerance:g}% of relief',
        f"  achieved   {data['relief']:.4g}% h maximum; {data['limited']:.2f}% total displacement restrained",
        f"  triangles  {len(original.faces):,} -> {len(result.faces):,} (limit {p.max_faces:,}; {budget})",
        f"  cache      UV charts {'hit' if data['uv_cached'] else 'new/not used'}; heightmap {'hit' if data['height_cached'] else 'new/not used'}",
        '  surface    welded geometry; source units and placement preserved',
        '  drawing    A3 / 0.3 mm: Contour Lines or Sketch Hatching; Plotter Vision features without decimation',
        f"  time       {data['seconds']:.3f} s"+(' ('+', '.join(f'{k} {t:.1f} s' for k,t in times.items())+')' if times else '')]
    if p.mapping=='surface':
        report.append(f"  charts     {data.get('charts',0)}; motifs may change direction across chart seams, which cross-fade")
    report.extend('  saved      '+s for s in outputs)
    # Final geometry remains authoritative; a decimated copy is display-only.
    display=result
    if len(result.faces)>250000:
        import trimesh
        v,f=gm.simplify_faces(result.vertices,result.faces,200000,ctx.log)
        display=trimesh.Trimesh(v,f,process=False)
    preview=gm.preview(display,max_faces=len(display.faces))
    preview.faces_total=len(result.faces); preview.bounds=tuple(map(tuple,result.bounds))
    ctx.progress(1.,'Saved' if outputs else 'Previewed')
    return Result(outputs=outputs,data=data,report='\n'.join(report),status='Saved' if outputs else 'Previewed',
        stats=[Stat('faces','Faces',len(result.faces)),Stat('relief','Achieved relief',data['relief'],'% h',fmt='{:.3g}'),
               Stat('limited','Displacement restrained',data['limited'],'%',fmt='{:.1f}')],
        preview={'views':[{'title':'Before','kind':'mesh','mesh':gm.preview(original)},
                          {'title':'After','kind':'mesh','mesh':preview,'status':'Final geometry' if display is result else 'Display proxy · final mesh retained'}],
                 'prefer':'After'})
