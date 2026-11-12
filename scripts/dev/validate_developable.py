"""Reproducible mesh/preset and A3 plotter validation; all outputs go to --out."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from atlas_toolkit.devpaths import preset_names, preset_values  # noqa: E402
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401
WORKSPACE = studio_workspace()
from validate_stripes import png

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--models',nargs='+',default=['sphere','bunny','head'])
    ap.add_argument('--presets',nargs='+',default=['Balanced','Sculpture','Broad folds','Clean rulings','Abstract form'])
    ap.add_argument('--resume',action='store_true')
    ap.add_argument('--iterations',type=int,help='Explicit per-level override for shorter preset comparisons')
    args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    from atlas_toolkit.core.job import JobContext
    from atlas_toolkit.core.registry import registry
    reg=registry();tool=reg.get('mesh-developable')
    models={'sphere':ROOT/'native/reference/DevelopabilityOfTriangleMeshes/assets/sphere320.obj',
            'bunny':ROOT/'native/reference/DevelopabilityOfTriangleMeshes/assets/bunny.obj',
            'head':WORKSPACE/'3d_models_db/head_model/v17_head_decim7.5K-v2.stl'}
    records=[];failed=False
    for model in args.models:
        for preset in preset_names(args):
            name=model+'_'+preset.lower().replace(' ','_');report=args.out/(name+'.json')
            if args.resume and report.is_file():
                record=json.loads(report.read_text());records.append(record);print('RESUME',name,flush=True);continue
            print('START',name,flush=True);last=[0.]
            def emit(kind,payload):
                if kind=='progress' and time.monotonic()-last[0]>5:
                    print(name,f'{payload[0]:.0%}',payload[1],flush=True);last[0]=time.monotonic()
                elif kind=='log':print(name,payload,flush=True)
            ctx=JobContext(emit,lambda:False,'run',str(args.out/'cache'))
            start=time.perf_counter()
            try:
                dst=args.out/(name+'_developable.stl')
                values=preset_values(tool, preset)
                if args.iterations is not None:values=tool.values(dict(values,iterations=args.iterations))
                result=tool.engine_fn()(ctx,models[model],dst,values)
                record=dict(model=model,preset=preset,values=dict(values),mesh=str(dst),seconds=time.perf_counter()-start,diagnostics=result.data,report=result.report,renders=[])
                for drawpreset in ('Developable rulings','Developable creases'):
                    draw=reg.get('plotter-vision');svg=args.out/(name+'_'+drawpreset.lower().replace(' ','_')+'.svg')
                    p=draw.values(dict(preset_values(draw, drawpreset),paper='A3',line_width=.2,outline_width=.3))
                    if model=='head':p.update(psi=-100.,theta=80.)
                    drawing=draw.engine_fn()(ctx,dst,svg,p)
                    root=ET.parse(svg).getroot();assert root.get('width').endswith('mm') and root.get('height').endswith('mm')
                    strokes=next((s.value for s in drawing.stats if s.key=='strokes'),0);assert strokes>0
                    png(svg)
                    record['renders'].append(dict(preset=drawpreset,svg=str(svg),strokes=strokes))
                report.write_text(json.dumps(record,indent=2),encoding='utf-8')
                records.append(record);print('DONE',name,f"{record['seconds']:.1f}s",flush=True)
            except Exception as e:
                import traceback
                traceback.print_exc();records.append(dict(model=model,preset=preset,error=str(e)));failed=True
            (args.out/'validation.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    return int(failed)
if __name__=='__main__':raise SystemExit(main())
