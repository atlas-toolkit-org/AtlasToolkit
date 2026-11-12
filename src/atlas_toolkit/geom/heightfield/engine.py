"""Shared heightfield build, reporting and atomic signed mesh publication."""
from pathlib import Path
import numpy as np
from atlas_toolkit.core import Result, Stat
from .mesh import process, check_mesh


def run(ctx, src, dst, p, tool, reader, mesh_title='Terrain mesh', extra_views=None):
    import time
    from atlas_toolkit.geom.mesh import preview
    from atlas_toolkit.geom.surface import from_arrays, load_surface
    from atlas_toolkit.geom.mesh_export import publish_meshes
    t0 = time.perf_counter()
    parent = ctx
    timings = []
    class TimedContext:
        last_fraction = 0.
        def __getattr__(self,name):
            return getattr(parent,name)
        def progress(self,fraction,message=''):
            elapsed = time.perf_counter()-t0
            fraction = max(fraction,self.last_fraction)
            self.last_fraction = fraction
            if message:
                timings.append((round(elapsed,4),message))
            parent.progress(fraction,f'[{elapsed:.1f}s] {message}')
    ctx = TimedContext()
    src = Path(src)
    if dst is not None and src.resolve() == Path(dst).resolve():
        raise ValueError('Mesh output must not replace the source raster.')
    adaptive = p['sampling']=='adaptive' and p['method'] in ('faithful','smooth')
    raster = reader(src, p, ctx,max_size=1024 if ctx.draft else None if adaptive else 6144,
                  exact_outline=p['footprint']=='valid' and p['missing']=='expose')
    ctx.log(f'input: {raster.metadata["source_columns"]} × {raster.metadata["source_rows"]}; '
            f'working grid {raster.valid.shape[1]} × {raster.valid.shape[0]}; elevation units {raster.metadata["elevation_units"]}; '
            f'band scale/offset {raster.metadata.get("source_scale",1):g}/{raster.metadata.get("source_offset",0):g}')
    mesh, data = process(raster, p, ctx)
    surface = from_arrays(mesh.vertices, mesh.faces)
    surface.frame = {'up': '+z', 'front': '-y', 'rotate': [0., 0., 0.]}
    ctx.progress(.9, 'preparing bounded mesh preview')
    display_budget = {'fast':25000,'balanced':100000,'detailed':250000}[p['preview_quality']]
    mesh_preview = preview(mesh,max_faces=display_budget)
    display_status = f'Draft · {p["preview_quality"]}' if ctx.draft else 'Final'
    if len(mesh_preview.faces)<len(mesh.faces):
        display_status += f' · proxy {len(mesh_preview.faces):,}'
    def validate_export(path):
        ctx.progress(.95, 'checking saved mesh precision and topology')
        saved = load_surface(path).as_trimesh()
        check_mesh(saved, solid=p['geometry'] == 'solid',budget=p['triangle_budget'])
        if not np.allclose(saved.bounds, mesh.bounds, atol=2e-5, rtol=1e-6):
            raise ValueError('Export changed physical model dimensions beyond the format tolerance.')
    outputs = publish_meshes([surface], dst, ctx, tool, validator=validate_export) if dst is not None and not ctx.dry_run else []
    dims = mesh.extents
    data.pop('plate', None)
    data.pop('terrain', None)
    data['dimensions_mm'] = dims.tolist()
    data['source'] = {key:value for key,value in raster.metadata.items() if not key.startswith('_')}
    data['timings'] = timings
    report = [src.name, f"  method       {p['method']} / {p['geometry']}",
              f"  source       {raster.metadata['crs_label']}",
              f"  source range {raster.metadata['source_range']} {raster.metadata['elevation_units']}",
              f"  coordinates  centred XY; +Z up; millimetres; plate bottom Z=0",
              f"  XY scale     {data['scale']:.9g} mm per {raster.metadata['coordinate_units']}",
              f"  height mode  {data['height_mode']}; Z-factor {p['z_factor']:g}",
              f"  plate / lift / relief  {data['base_height']:.4g} / {data['basal_height']:.4g} / {data['relief_height']:.4g} mm",
              f"  plate size   {data['plate_width']:.4g} × {data['plate_height']:.4g} mm",
              f"  {'terrain turn' if raster.geographic else 'image turn  '} {data['yaw']:g}° clockwise; plate aligned with page",
              f"  dimensions   {dims[0]:.4g} × {dims[1]:.4g} × {dims[2]:.4g} mm",
              f"  sampling     {data['grid'][0]} × {data['grid'][1]} cells; pitch {data['pitch']:.4g} mm",
              f"  topology     {len(mesh.faces):,} triangles; {'one watertight manifold solid' if p['geometry']=='solid' else 'open drawing surface'}",
              f"  time         {time.perf_counter()-t0:.2f} s"]
    if raster.geographic:
        report.insert(3, f"  source CRS   {raster.metadata['crs']}")
        report.insert(4, f"  working CRS  {raster.metadata['working_crs']}")
    if ctx.draft:
        report.append('  draft        reduced sampling resolution; Run builds the requested final resolution')
        if p['contact_trim']==0:
            report.append('  draft mask   up to 0.002 mm point-contact clearance for preview only; final output uses the explicit setting')
    report.append(f"  detail       {p['sampling']}; export budget {p['triangle_budget']:,} triangles")
    report.append(f"  RAM preflight {data['estimated_peak_bytes']/1024**3:.2f} GiB conservative peak estimate")
    if ctx.draft:
        report.append(f"  preview      {p['preview_quality']}; export pitch {p['pitch']:g} mm; export adaptive tolerance {p['vertical_error']:g} mm")
    if 'maximum_error_mm' in data:
        report.append(f"  measured Z error  max {data['maximum_error_mm']:.5g} mm; RMS {data['rms_error_mm']:.5g} mm")
    report.append(f"  source cell  {raster.width/raster.metadata['source_columns']*data['scale']:.5g} × {raster.height/raster.metadata['source_rows']*data['scale']:.5g} mm")
    if not raster.geographic:
        report.append(f"  heightmap    black 0 mm; white {p['relief_height']*p['z_factor']:g} mm; no post-adjustment stretch")
        report.append(f"  preparation  {raster.metadata.get('preparation', {})}")
    if p['method'] == 'faceted' and len(mesh.faces) > p['target_faces']*1.25:
        ctx.warn('Protected boundaries and geometric constraints take priority over the approximate terrain face target.')
    if outputs:
        report.append('  saved        '+outputs[0])
    ctx.progress(1., 'saved' if outputs else 'previewed')
    ctx.log(f'completed {ctx.mode} in {time.perf_counter()-t0:.2f}s; {len(mesh.faces):,} triangles; dimensions '+ ' × '.join(f'{d:.4g}' for d in dims)+' mm')
    return Result(outputs=outputs, report='\n'.join(report), data=data,
                  stats=[Stat('faces', 'Triangles', len(mesh.faces)), Stat('height', 'Height', dims[2], 'mm', fmt='{:.2f}'),
                         Stat('watertight', 'Watertight', 'Yes' if mesh.is_watertight else 'Drawing surface', fmt='{}'),
                         Stat('size', 'Size', f'{dims[0]:.1f} × {dims[1]:.1f} × {dims[2]:.1f} mm', fmt='{}')],
                  preview={'views': (extra_views(raster, p) if extra_views else []) + [{'title': mesh_title, 'kind': 'mesh', 'mesh': mesh_preview,
                                      'status': display_status}], 'prefer': mesh_title},
                  status='Saved' if outputs else 'Draft' if ctx.draft else 'Previewed')
