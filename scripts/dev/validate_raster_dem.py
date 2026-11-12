"""Repeatable Viana DEM acceptance examples. Never modifies the supplied source files."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401


def main():
    from atlas_toolkit.core.job import JobContext
    from atlas_toolkit.core.registry import registry
    from atlas_toolkit.tools.raster_dem_to_mesh.engine import run
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=studio_workspace()/'code/R/generators/3d_DEM/dem_vc.tif')
    parser.add_argument('--out', type=Path, default=qa_dir('raster_dem_validation'))
    args = parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    reg=registry()
    tool=reg.get('raster-dem-to-mesh')
    summary=[]
    for name, overrides in [
        ('faithful_print',dict(method='faithful',pitch=.5)),
        ('soft_print',dict(method='smooth',pitch=1.,smooth_radius=1.5)),
        ('faceted_print',dict(method='faceted',pitch=1.,target_faces=2500)),
        ('terraced_print',dict(method='terraces',pitch=1.,terrace_step=1.)),
        ('drawing_contours',dict(method='smooth',geometry='surface',pitch=1.,z_factor=3.,paper='A3',orientation='landscape')),
        ('drawing_facets',dict(method='faceted',geometry='surface',pitch=2.,z_factor=2.,target_faces=3000,paper='A3',orientation='landscape')),
    ]:
        started=time.perf_counter()
        values=tool.values(dict(exclude_background=True,background=10.,contact_trim=.02,**overrides))
        dst=args.out/(name+'.stl')
        result=run(JobContext(lambda *a:None,lambda:False),args.input,dst,values)
        row=dict(name=name,triangles=result.stat('faces').value,dimensions_mm=result.data['dimensions_mm'],
                 watertight=result.stat('watertight').value,seconds=round(time.perf_counter()-started,2))
        summary.append(row)
        print(json.dumps(row),flush=True)
        if name.startswith('drawing_'):
            drawing=reg.get('contour-lines' if name.endswith('contours') else 'plotter-vision')
            changes=dict(paper='A3',orientation='landscape',theta=45.,psi=-30.,projection='orthographic',radius=180.,up_axis='+z',front='-y')
            if drawing.id=='plotter-vision':
                changes=dict(paper='A3',orientation='landscape',theta=45.,psi=-30.,projection='orthographic',radius=130.,
                             edge_mode='mesh',flat_angle=.01,decimate=False)
            params=drawing.values(changes)
            svg=args.out/(name+'.svg')
            drawing.engine_fn()(JobContext(lambda *a:None,lambda:False),dst,svg,params)
    (args.out/'validation.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
