"""Atlas adapter: process, validate, then atomically publish a signed native mesh."""
from pathlib import Path
import time

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom import frame3d
from atlas_toolkit.geom.surface import load_surface
from atlas_toolkit.geom.mesh_export import publish_meshes
from . import TOOL
from .remesh_decimate import process


def inspect(path):
    from atlas_toolkit.core import InputInfo
    surface = load_surface(path)
    summary, values = gm.describe(surface.as_trimesh())
    values['native_faces'] = len(surface.faces)
    return InputInfo(str(path),'mesh',summary,values,
        preview={'views':[{'title':'Input','kind':'mesh','mesh':gm.preview(surface)}]})


def run(ctx, src, dst, p):
    src = Path(src)
    if ctx.dry_run:
        dst = None
    if dst is not None and Path(dst).resolve() == src.resolve():
        raise ValueError('The output must not replace the input model.')
    t0 = time.perf_counter()
    ctx.progress(.01, 'reading native mesh')
    surface = load_surface(src)
    frame = frame3d.frame_for(src, p, V=surface.vertices)
    values = dict(p)
    if ctx.draft:
        values['iterations'] = min(3, values['iterations'])
        if values['density'] == 'faces':
            values['target_faces'] = min(4000, values['target_faces'])
    output, data = process(surface, values, ctx, frame)
    output.frame = {'up':frame.up, 'front':frame.front, 'rotate':list(frame.rotate)}
    ctx.progress(.92, 'preparing bounded previews')
    before_preview, after_preview = gm.preview(surface), gm.preview(output)
    ctx.check_cancel()
    outputs = []
    if dst is not None:
        if Path(dst).suffix.lower() == '.stl' and len(output.faces) != len(output.triangles):
            ctx.warn('STL triangulates native polygons. Use OBJ or PLY to retain the intended edge pattern.')
        outputs = publish_meshes([output], dst, ctx, TOOL)
    reduction = 100*(1-len(output.faces)/len(surface.faces))
    target_text = f" (requested {data['target']:,})" if values['method'] not in ('voxel','planar') else ''
    if values['method'] not in ('voxel', 'planar') and len(output.faces) > data['target']*1.25:
        ctx.warn(f"Requested {data['target']:,} native faces; constraints produced {len(output.faces):,}.")
    report = [src.name, f"  method     {values['method']}",
        f"  native faces  {len(surface.faces):,} -> {len(output.faces):,}"+target_text,
        f"  triangles  {len(output.triangles):,} for processing / visibility", f"  components {data['components']}",
        f"  density validation  {data['density_attempts']} attempt(s); constraints take priority over target",
        f"  sampled surface error  max {data['sample_max']:.6g} units ({data['sample_max_percent']:.4g}% diagonal), RMS {data['sample_rms']:.6g}",
        f"  validation {data['samples']:,} deterministic bidirectional samples; not a certified Hausdorff bound",
        '  coordinates  original model units and placement', f"  time       {time.perf_counter()-t0:.2f} s"]
    if len(output.edges) > 160000:
        report.append('  preview    native wireframe sampled to 160,000 edges; saved topology is complete')
    if ctx.draft:
        report.append('  draft      reduced iterations / face target; run Apply for the final mesh')
    if outputs:
        report.append('  saved as   '+outputs[0])
    ctx.progress(1., 'saved' if outputs else 'previewed')
    return Result(outputs=outputs, stats=[Stat('polygons', 'Native faces', len(output.faces), before=len(surface.faces)),
        Stat('reduction', 'Reduction', reduction, '%', fmt='{:.1f}'), Stat('triangles', 'Triangles', len(output.triangles)),
        Stat('error', 'Sampled max error', data['sample_max_percent'], '% diagonal', fmt='{:.3g}')],
        report='\n'.join(report), data=data,
        preview={'views':[{'title':'Before', 'kind':'mesh', 'mesh':before_preview},
                          {'title':'After', 'kind':'mesh', 'mesh':after_preview}], 'prefer':'After'},
        status='Saved' if outputs else 'Draft' if ctx.draft else 'Previewed')
