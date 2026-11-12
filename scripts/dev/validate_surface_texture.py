"""Real mesh -> A3 SVG acceptance sheets and a matched-resolution Blender comparison.

Run with Atlas Python: scripts/validate_surface_texture.py --out <QA folder> [--quick] [--benchmark]
"""
from pathlib import Path
import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from atlas_toolkit.devpaths import preset_names  # noqa: E402
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401


def peak_mb():
    if os.name!='nt':
        return None
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in (
            'PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
            'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
    c=Counters(); c.cb=ctypes.sizeof(c)
    kernel=ctypes.WinDLL('kernel32'); kernel.GetCurrentProcess.restype=wintypes.HANDLE
    psapi=ctypes.WinDLL('psapi'); psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
    psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(c),c.cb)
    return c.PeakWorkingSetSize/1024**2


def raster(svg):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage,QPainter
    from PySide6.QtSvg import QSvgRenderer
    renderer=QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise ValueError(f'Invalid SVG {svg}')
    size=renderer.defaultSize()
    image=QImage(840,round(840*size.height()/size.width()),QImage.Format_RGB32)
    image.fill(Qt.white)
    painter=QPainter(image); renderer.render(painter); painter.end()
    image.save(str(svg.with_suffix('.png')))
    return svg.with_suffix('.png')


def sheet(folder,records,drawing):
    from PIL import Image,ImageChops,ImageDraw,ImageFont,ImageOps
    width,height=330,440
    output=Image.new('RGB',(width*4,height*((len(records)+3)//4)),'white')
    draw=ImageDraw.Draw(output)
    font=ImageFont.truetype(DEV_FONT,16)
    for i,entry in enumerate(records):
        source=Image.open(entry['drawings'][drawing]['png']).convert('RGB')
        box=ImageChops.difference(source,Image.new('RGB',source.size,'white')).getbbox()
        if box:source=source.crop(box)
        source=ImageOps.contain(source,(width-24,height-65))
        x=(i%4)*width; y=(i//4)*height
        output.paste(source,(x+(width-source.width)//2,y+45))
        draw.text((x+10,y+10),entry['name'],font=font,fill='black')
        draw.text((x+10,y+30),f"{entry['faces']:,} faces / {drawing}",font=font,fill='#555555')
    target=folder/(drawing+'_contact.png'); output.save(target)
    return target


def benchmark(folder,source,result,p):
    from atlas_toolkit.geom import mesh as gm,frame3d
    from atlas_toolkit.geom.blender import find_blender
    from atlas_toolkit.tools.mesh_texture import mesh_texture as mt
    from atlas_toolkit.core import JobContext
    import trimesh
    # A cylindrical mapping on fixed tessellation isolates the same height field
    # evaluation and displacement in NumPy versus Blender's Displace modifier.
    m=gm.load_mesh(result)
    v=np.array(m.vertices); n=np.array(m.vertex_normals)
    lo=v.min(axis=0); span=np.ptp(v,axis=0); height=span[2]
    from atlas_toolkit.tools.mesh_texture.engine import TOOL
    settings=dict(TOOL.values(dict(p,mapping='cylindrical',texture_rotation=0,offset_u=0,offset_v=0,aspect=1,relief_mode='raised')))
    a,alpha,elements,_=mt.prepare(settings,JobContext(lambda *a:None,lambda:False))
    field=mt.Field(settings,v,n,a,alpha,elements)
    angle=np.arctan2(v[:,1],v[:,0])-np.pi/2
    uv=np.column_stack(((angle/(2*np.pi)*field.repeats)%1,(v[:,2]/field.tile)%1))
    from PIL import Image
    # Blender image textures use bottom-left UV origin, matching Atlas sampling.
    texture=folder/'benchmark_height.png'
    Image.fromarray(np.round(a*65535).astype(np.uint16)).save(texture)
    started=time.perf_counter()
    sampled,coverage=field.sample(uv)
    offsets=sampled*coverage*field.h*settings['relief']/100
    native=v+n*offsets[:,None]
    native_seconds=time.perf_counter()-started
    arrays=folder/'benchmark_arrays.npz'
    np.savez(arrays,v=v,f=m.faces,uv=uv)
    script=folder/'benchmark_blender.py'
    script.write_text('''import bpy, numpy as np, sys, time, json
from pathlib import Path
folder=Path(sys.argv[sys.argv.index('--')+1])
a=np.load(folder/'benchmark_arrays.npz'); v=a['v']; f=a['f']; uv=a['uv']
started=time.perf_counter()
mesh=bpy.data.meshes.new('matched'); mesh.from_pydata(v.tolist(),[],f.tolist()); mesh.update()
obj=bpy.data.objects.new('matched',mesh); bpy.context.collection.objects.link(obj)
bpy.context.view_layer.objects.active=obj; obj.select_set(True)
layer=mesh.uv_layers.new(name='UVMap')
for poly in mesh.polygons:
 for li in poly.loop_indices: layer.data[li].uv=uv[mesh.loops[li].vertex_index]
image=bpy.data.images.load(str(folder/'benchmark_height.png')); image.colorspace_settings.name='Non-Color'
tex=bpy.data.textures.new('height',type='IMAGE'); tex.image=image; tex.extension='REPEAT'; tex.use_interpolation=True
modifier=obj.modifiers.new('surface texture','DISPLACE'); modifier.texture=tex; modifier.texture_coords='UV'; modifier.direction='NORMAL'; modifier.mid_level=0
modifier.strength=float(sys.argv[-1])
bpy.ops.object.modifier_apply(modifier=modifier.name)
out=np.empty(len(mesh.vertices)*3); mesh.vertices.foreach_get('co',out)
np.save(folder/'benchmark_blender_vertices.npy',out.reshape(-1,3))
(folder/'benchmark_blender.json').write_text(json.dumps({'blender_seconds':time.perf_counter()-started,'version':bpy.app.version_string}))
''',encoding='utf-8')
    blender=find_blender()
    report={'faces':len(m.faces),'vertices':len(v),'numpy_sampling_displacement_seconds':native_seconds,
            'comparison':'Fixed topology, UVs and height image; no unwrap, refinement, seam/pole taper, region or collision checks in either timed displacement. Blender includes mesh/image/modifier creation; cold process time separately reported.'}
    if blender:
        started=time.perf_counter()
        proc=subprocess.run([str(blender),'--background','--factory-startup','--python',str(script),'--',str(folder),str(field.h*settings['relief']/100)],capture_output=True,text=True,timeout=180)
        report['blender_cold_process_seconds']=time.perf_counter()-started
        if proc.returncode:
            raise RuntimeError(proc.stdout[-2000:]+proc.stderr[-2000:])
        report.update(json.loads((folder/'benchmark_blender.json').read_text()))
        bv=np.load(folder/'benchmark_blender_vertices.npy')
        report['vertex_rms_difference_model_units']=float(np.sqrt(np.mean(np.sum((native-bv)**2,axis=1))))
        report['requested_height_model_units']=field.h*settings['relief']/100
    else:
        report['blender']='not installed'
    (folder/'benchmark.json').write_text(json.dumps(report,indent=2))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--quick',action='store_true')
    parser.add_argument('--benchmark',action='store_true')
    parser.add_argument('--head',type=Path)
    parser.add_argument('--resume',action='store_true',help='Keep already completed cases in this QA folder; use only with unchanged inputs and settings for those cases.')
    args=parser.parse_args(); args.out=args.out.resolve(); args.out.mkdir(parents=True,exist_ok=True)
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtGui import QGuiApplication
    app=QGuiApplication.instance() or QGuiApplication([])
    from atlas_toolkit.core import JobContext
    from atlas_toolkit.core.registry import registry
    from atlas_toolkit.tools.mesh_texture import TOOL
    from atlas_toolkit.tools.mesh_texture.engine import make_sample,run
    ctx=JobContext(lambda *a:None,lambda:False)
    reg=registry(); source=make_sample(args.out)
    records=[]
    prior={}
    if args.resume and (args.out/'acceptance.json').exists():
        prior={record['name']:record for record in json.loads((args.out/'acceptance.json').read_text())}
    presets=preset_names(TOOL)[:12]
    cases=[('Original',source,None)]
    for preset in presets:
        cases.append((preset,source,preset))
    cases.append(('Textured Band',source,'Textured Band'))
    head=args.head or studio_workspace()/'3d_models_db/david_15k_reshape.stl'
    if head.is_file():
        cases.extend([('Bust Original',head,None),('Bust Scales',head,'Graphic Scales'),('Bust Bark',head,'Carved Bark')])
    benchmark_result=None
    for name,src,preset in cases:
        key=name.lower().replace(' ','_')
        if name in prior and all(Path(data['svg']).exists() and Path(data['png']).exists() for data in prior[name]['drawings'].values()):
            records.append(prior[name])
            if preset=='Graphic Scales' and src==source:
                benchmark_result=(src,args.out/(key+'.ply'),TOOL.values(preset=preset))
            print('Reusing completed case:',name,flush=True)
            continue
        dst=src; info={'name':name,'drawings':{}}
        if preset:
            p=TOOL.values(dict(element_size=10.,tolerance=.06) if args.quick else {},preset=preset)
            if src==head:
                p=TOOL.values(dict(p,element_size=max(8.,p.element_size),tolerance=max(.06,p.tolerance),max_faces=600000))
            dst=args.out/(key+'.ply')
            started=time.perf_counter(); result=run(ctx,src,dst,p)
            info.update(result.data,texture_seconds=time.perf_counter()-started)
            started=time.perf_counter(); run(ctx,src,None,p)
            info['repeat_edit_seconds']=time.perf_counter()-started
            if preset=='Graphic Scales' and src==source:benchmark_result=(src,dst,p)
        else:
            from atlas_toolkit.geom.mesh import load_mesh
            info['faces']=len(load_mesh(src).faces)
        for drawing in ('contour-lines','plotter-vision'):
            tool=reg.get(drawing)
            values=dict(paper='A3',orientation='portrait',line_width=.3,margin=15.)
            if drawing=='contour-lines':
                values.update(spacing_mm=1.6,min_len=1.,simplify=.025,min_gap=.4,theta=75.,psi=-100.)
            else:
                values.update(flat_angle=12.,decimate=False,outline_layer=False,show_hidden=False,crossings=False,theta=75.,psi=-100.)
                if src==head:
                    values.update(flat_angle=35.,min_piece=.2,min_stroke=.5)
            svg=args.out/(key+'_'+drawing+'.svg')
            started=time.perf_counter(); drawn=tool.engine_fn()(ctx,dst,svg,tool.values(values))
            info['drawings'][drawing]=dict(seconds=time.perf_counter()-started,svg=str(svg),png=str(raster(svg)))
        info['process_peak_mb']=peak_mb()
        records.append(info)
        (args.out/'acceptance.json').write_text(json.dumps(records,indent=2))
        print(name,info['faces'],info.get('texture_seconds'),flush=True)
    for drawing in ('contour-lines','plotter-vision'):
        print(sheet(args.out,records,drawing),flush=True)
    if args.benchmark and benchmark_result:
        print(benchmark(args.out,*benchmark_result),flush=True)


if __name__=='__main__':
    main()
