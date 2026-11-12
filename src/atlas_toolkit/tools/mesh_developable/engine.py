"""Atlas adapter for Developable Surfaces; atlas-toolkit:mesh-developable."""
from pathlib import Path
import os
import tempfile
import time
import numpy as np
from atlas_toolkit.core import Result, Stat
from atlas_toolkit.core.paths import cache_dir
from atlas_toolkit.geom import mesh as gm
from . import TOOL
from .mesh_developable import develop, load_input


def make_sample(folder):
    import trimesh
    path=Path(folder)/'developable ellipsoid.obj'
    mesh=trimesh.creation.icosphere(subdivisions=1, radius=20.)
    mesh.vertices *= [0.7, 0.8, 1.2]
    mesh.export(path)
    return path


def inspect(path):
    from atlas_toolkit.core import InputInfo
    mesh=load_input(path)
    summary,values=gm.describe(mesh)
    return InputInfo(str(path),'mesh',summary,values,
                     preview={'views':[{'title':'Input','kind':'mesh','mesh':gm.preview(mesh)}]})


def run(ctx, src, dst, p):
    src=Path(src)
    if ctx.dry_run:
        dst=None
    if dst is not None and Path(dst).resolve()==src.resolve():
        raise ValueError('The output must not replace the input model.')
    started=time.perf_counter()
    ctx.progress(0.01, 'Loading surface')
    source=load_input(src)
    settings=dict(p)
    if ctx.draft:
        settings.update(target_faces=min(p.target_faces,500), subdivisions=min(p.subdivisions,1), iterations=min(p.iterations,100))
        # Keep-input mode promises no retessellation, so only its refinement and iteration counts are reduced.
    cache=(ctx.cache_dir or cache_dir())/'developable'
    out, data=develop(source,cache,settings,check=ctx.check_cancel,
                      progress=lambda f,m: ctx.progress(0.05+0.85*f,m))
    outputs=[]
    if dst is not None:
        dst=Path(dst);dst.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='.atlas-developable-',suffix=dst.suffix,dir=dst.parent)
        os.close(fd);staged=Path(name)
        try:
            gm.save_mesh(out,staged,TOOL)
            from .mesh_developable import validate_mesh, signature
            reread=validate_mesh(gm.load_mesh(staged))
            if signature(reread)!=signature(out):
                raise ValueError('Output format lost surface topology; choose OBJ or recenter the input coordinates.')
            ctx.check_cancel();os.replace(staged,dst)
        finally:
            staged.unlink(missing_ok=True)
        outputs=[str(dst)]
    reductions=[100*(1-l['final_energy']/l['initial_energy']) for l in data['levels'] if l['initial_energy']>1e-14]
    reduction=float(np.mean(reductions)) if reductions else 0.
    lines=[src.name, f"  {data['components']} component(s); source coordinates and units preserved",
           f"  {len(source.faces)} -> {len(out.faces)} triangles; energy {p.energy}",
           f"  sampled displacement RMS {data['displacement_rms']:.5g}, max {data['displacement_max']:.5g} model units",
           f"  cache {'hit' if data['cache_hit'] else 'miss'}; {time.perf_counter()-started:.1f} s"]
    for l in data['levels']:
        lines.append(f"  component {l['component']+1}, level {l['level']}: energy {l['initial_energy']:.6g} -> "
                     f"{l['final_energy']:.6g}; {l['steps']} steps; {l['status']}")
    if any(l['status']=='line search stalled' for l in data['levels']):
        ctx.warn('Line search stalled at a level; the last valid non-increasing solution was retained. See Report.')
    for preparation in data.get('preparations', []):
        lines.append('  preparation: '+preparation)
        if 'fallback' in preparation:
            ctx.warn('Field remeshing was invalid; used topology-checked edge-collapse coarsening instead.')
    lines += ['  Approximate developability; self-intersections are not prevented.',
              '  Method: Stein, Grinspun and Crane, Developability of Triangle Meshes (2018).',
              '  Draw with Plotter Vision: Developable rulings / Developable creases, decimation off; or Contour Lines.']
    ctx.progress(1.,'Saved' if outputs else 'Previewed')
    return Result(outputs=outputs, stats=[Stat('faces','Faces',len(out.faces),before=len(source.faces)),
                  Stat('reduction','Mean energy drop',reduction,'%',fmt='{:.1f}'),
                  Stat('displacement','Displacement RMS',data['displacement_rms'],'units',fmt='{:.4g}')],
                  report='\n'.join(lines), status='Saved' if outputs else ('Draft' if ctx.draft else 'Previewed'), data=data,
                  preview={'views':[{'title':'Input','kind':'mesh','mesh':gm.preview(source)},
                                    {'title':'Output','kind':'mesh','mesh':gm.preview(out,max_faces=250000)}], 'prefer':'Output'})
