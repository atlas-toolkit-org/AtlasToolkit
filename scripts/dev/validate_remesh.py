"""Repeatable native-mesh, SVG and large-scan validation. Outputs only under the chosen validation folder."""
from __future__ import annotations
import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from atlas_toolkit.devpaths import preset_names, preset_values  # noqa: E402
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401


def peak_mb():
    if os.name != 'nt':
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(key,ctypes.c_size_t) for key in
            ('peak','working','quota_peak_paged','quota_paged','quota_peak_nonpaged','quota_nonpaged','page','peak_page')]
    data=Counters(); data.cb=ctypes.sizeof(data)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    psapi=ctypes.WinDLL('psapi',use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(data),data.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return data.peak/(1024*1024)


def one_case(source, folder, preset='Clean preparation'):
    import trimesh
    from atlas_toolkit.tools.remesh_decimate import TOOL
    from atlas_toolkit.tools.remesh_decimate.engine import run
    from atlas_toolkit.core.job import JobContext
    if source=='synthetic-1m':
        source=folder/'sphere-1310720.stl'
        trimesh.creation.icosphere(subdivisions=8).export(source)
    source=Path(source)
    began=time.perf_counter()
    result=run(JobContext(lambda *a:None,lambda:False),source,folder/'benchmark.obj',preset_values(TOOL, preset))
    record={'source':str(source),'preset':preset,'seconds':time.perf_counter()-began,
            'python_peak_mb':peak_mb(),**result.data}
    (folder/'metrics.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record),flush=True)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--source',type=Path)
    ap.add_argument('--benchmark',type=Path,help='Additional large sculpture scan, run in an isolated process')
    ap.add_argument('--case',help=argparse.SUPPRESS)
    args=ap.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    for key,name in (('TEMP','tmp'),('TMP','tmp'),('APPDATA','roaming'),('LOCALAPPDATA','local')):
        path=args.out/name;path.mkdir(exist_ok=True);os.environ[key]=str(path.resolve())
    if args.case:
        one_case(args.case,args.out)
        return
    import trimesh
    from atlas_toolkit.core.job import JobContext
    from atlas_toolkit.tools.remesh_decimate import TOOL
    from atlas_toolkit.tools.remesh_decimate.engine import run
    from atlas_toolkit.tools.plotter_vision import TOOL as VISION
    from atlas_toolkit.tools.plotter_vision.engine import run as draw
    source=args.source
    if source is None:
        source=args.out/'asymmetric-form.stl'
        mesh=trimesh.creation.icosphere(subdivisions=3)
        mesh.vertices*= [0.7,0.8,1.3]
        mesh.export(source)
    records=[]
    for name in preset_names(TOOL):
        began=time.perf_counter()
        path=args.out/(name.lower().replace(' ','-')+'.obj')
        try:
            result=run(JobContext(lambda *a:None,lambda:False),source,path,preset_values(TOOL, name))
            record=dict(preset=name,seconds=time.perf_counter()-began,**result.data)
            if name in ('Bold facets','Flowing quad grid','Organic cells','Directional weave','Even triangle net'):
                svg=path.with_suffix('.svg')
                draw(JobContext(lambda *a:None,lambda:False),path,svg,
                     VISION.values(dict(edge_mode='mesh',decimate=False,theta=75.,psi=-90.,radius=180.,
                                        outline_layer=True,min_piece=.05)))
                inkscape=Path(shutil.which('inkscape') or 'inkscape-not-on-PATH')
                if inkscape.is_file():
                    subprocess.run([str(inkscape),str(svg),'--export-type=png',
                        '--export-width=900','--export-background=white',f'--export-filename={svg.with_suffix(".png")}'],
                        check=True,capture_output=True)
            print(name,'OK',round(record['seconds'],2),'s',record['polygons'],'faces',flush=True)
        except Exception as error:
            record=dict(preset=name,error=str(error))
            print(name,'FAILED',error,flush=True)
        records.append(record)
    (args.out/'presets.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    if args.benchmark:
        for label,source in [('1m','synthetic-1m'),('scan',str(args.benchmark.resolve()))]:
            folder=args.out/('benchmark-'+label);folder.mkdir(exist_ok=True)
            subprocess.run([sys.executable,__file__,'--out',str(folder.resolve()),'--case',source],check=True)
    if any('error' in record for record in records):
        raise SystemExit('Some presets failed. Inspect presets.json before release.')


if __name__=='__main__':
    main()
