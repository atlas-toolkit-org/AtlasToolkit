"""Surface Texture acceptance: does every case finish, how long does it take, and does the texture show in drawings?

Run with Atlas Python:
    scripts/dev/validate_surface_texture.py [--out build/qa/surface-texture] [--quick] [--resume]
        [--models ellipsoid,bust,head] [--mappings triplanar,cylindrical,surface] [--reliefs 0.4,1,2]
        [--drawings contour-lines,sketch-hatching,plotter-vision] [--presets] [--benchmark]

Models: the tool's sample ellipsoid, the 15,000-face David bust from the studio workspace (devpaths; skipped without
it) and the bundled catalogue David head (about 260,000 faces). Each runs every mapping at every relief with the
tool's defaults; --presets adds the official presets on the bust when the private packs are configured.

Per case: success or the error, faces, time per phase (prepare, refine, sample, protect), achieved relief, share of
the displacement restrained, budget residual, and each drawing tool's SVG of the original and of the textured model
from the same camera (outputs carry the source's frame, so the views match). The visibility score is the share of
the textured drawing's ink farther than a pen width (1 px at 840 px across A3) from any ink of the
original drawing: 0 when the texture adds nothing a pen would draw. acceptance.json is rewritten after every case; contact sheets and summary.md at the end.
"""
from pathlib import Path
import argparse
import ctypes
import gzip
import json
import os
import shutil
import subprocess
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from atlas_toolkit.devpaths import DEV_FONT, preset_names, preset_packs, qa_dir, workspace_path  # noqa: E402

DRAWINGS=('contour-lines','sketch-hatching','plotter-vision')


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


def ink(png):
    from PIL import Image
    return np.asarray(Image.open(png).convert('L'))<160


def visibility(original_png,textured_png,radius=1):
    """Share of the textured drawing's ink farther than `radius` px from the original's ink, and the ink ratio."""
    from scipy.ndimage import distance_transform_edt
    a,b=ink(original_png),ink(textured_png)
    if a.shape!=b.shape or not b.any():
        return 0.,0.
    far=distance_transform_edt(~a)>radius
    return float((b&far).sum()/b.sum()),float(b.sum()/max(a.sum(),1))


def sheet(folder,records,drawing):
    from PIL import Image,ImageChops,ImageDraw,ImageFont,ImageOps
    shown=[r for r in records if drawing in r.get('drawings',{})]
    if not shown:
        return None
    width,height=330,460
    output=Image.new('RGB',(width*4,height*((len(shown)+3)//4)),'white')
    draw=ImageDraw.Draw(output)
    font=ImageFont.truetype(DEV_FONT,15)
    for i,entry in enumerate(shown):
        source=Image.open(entry['drawings'][drawing]['png']).convert('RGB')
        box=ImageChops.difference(source,Image.new('RGB',source.size,'white')).getbbox()
        if box:
            source=source.crop(box)
        source=ImageOps.contain(source,(width-24,height-85))
        x=(i%4)*width; y=(i//4)*height
        output.paste(source,(x+(width-source.width)//2,y+65))
        draw.text((x+10,y+8),entry['name'],font=font,fill='black')
        score=entry['drawings'][drawing].get('visibility')
        line=f"{entry.get('faces',0):,} faces · {entry.get('seconds',0):.0f} s"
        if score is not None:
            line+=f" · visible {100*score:.0f}%"
        draw.text((x+10,y+28),line,font=font,fill='#555555')
        if entry.get('limited') is not None:
            draw.text((x+10,y+46),f"restrained {entry['limited']:.1f}% · relief {entry.get('relief',0):.2f}% h",font=font,fill='#555555')
    target=folder/(drawing+'_contact.png'); output.save(target)
    return target


def summary(folder,records,drawings):
    head='| case | result | faces | s | refine s | protect s | restrained % | unmet % | '+' | '.join(f'{d} visible %' for d in drawings)+' |'
    lines=['# Surface Texture acceptance','',head,'|'+'---|'*(8+len(drawings))]
    for r in records:
        if r.get('original'):
            continue
        t=r.get('times',{})
        vis=[r.get('drawings',{}).get(d,{}).get('visibility') for d in drawings]
        lines.append(f"| {r['name']} | {r.get('error','ok')[:60]} | {r.get('faces',0):,} | {r.get('seconds',0):.1f} | "
                     f"{t.get('refine',0):.1f} | {t.get('protect',0):.1f} | {r.get('limited',0):.1f} | "
                     f"{100*r.get('unmet',0):.1f} | "+' | '.join('' if v is None else f'{100*v:.0f}' for v in vis)+' |')
    (folder/'summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return folder/'summary.md'


def models(folder,wanted):
    """name -> path of the models to test."""
    from atlas_toolkit.tools.mesh_texture.engine import make_sample
    out={}
    if 'ellipsoid' in wanted:
        out['ellipsoid']=make_sample(folder)
    bust=workspace_path('3d_models_db','david_15k_reshape.stl')
    if 'bust' in wanted:
        if bust and bust.is_file():
            out['bust']=bust
        else:
            print('No studio workspace: the 15k David bust is skipped.',flush=True)
    if 'head' in wanted:
        packed=ROOT/'src/atlas_toolkit/catalogue/models/david-head.ply.gz'
        head=folder/'david-head.ply'
        if packed.is_file() and not head.exists():
            with gzip.open(packed,'rb') as src,head.open('wb') as dst:
                shutil.copyfileobj(src,dst)
        if head.exists():
            out['head']=head
        else:
            print('The catalogue David head is not bundled here: run the build once to fetch the starter set.',flush=True)
    return out


def drawing_values(drawing,dense):
    values=dict(paper='A3',orientation='portrait',line_width=.3,margin=15.,theta=75.,psi=-100.)
    if drawing=='contour-lines':
        values.update(spacing_mm=1.6,min_len=1.,simplify=.025,min_gap=.4)
    elif drawing=='plotter-vision':
        values.update(flat_angle=35. if dense else 12.,decimate=False,outline_layer=False,show_hidden=False,crossings=False)
        if dense:
            values.update(min_piece=.2,min_stroke=.5)
    return values


def draw(reg,ctx,drawings,mesh,folder,key,dense,original=None):
    out={}
    for drawing in drawings:
        tool=reg.get(drawing)
        svg=folder/f'{key}_{drawing}.svg'
        started=time.perf_counter()
        try:
            tool.engine_fn()(ctx,mesh,svg,tool.values(drawing_values(drawing,dense)))
        except Exception as error:      # a drawing failure is a finding, not a crash of the run
            out[drawing]=dict(error=f'{type(error).__name__}: {error}')
            continue
        entry=dict(seconds=time.perf_counter()-started,svg=str(svg),png=str(raster(svg)))
        if original and drawing in original and 'png' in original[drawing]:
            entry['visibility'],entry['ink_ratio']=visibility(original[drawing]['png'],entry['png'])
        out[drawing]=entry
    return out


def benchmark(folder,result,p):
    """NumPy sampling + displacement against Blender's Displace modifier on the same tessellation, UVs and image."""
    from atlas_toolkit.geom import mesh as gm
    from atlas_toolkit.geom.blender import find_blender
    from atlas_toolkit.tools.mesh_texture import mesh_texture as mt
    from atlas_toolkit.core import JobContext
    m=gm.load_mesh(result)
    v=np.array(m.vertices); n=np.array(m.vertex_normals)
    from atlas_toolkit.tools.mesh_texture.engine import TOOL
    settings=dict(TOOL.values(dict(p,mapping='cylindrical',texture_rotation=0,offset_u=0,offset_v=0,aspect=1,relief_mode='raised')))
    a,alpha,elements,_=mt.prepare(settings,JobContext(lambda *a:None,lambda:False))
    field=mt.Field(settings,v,n,a,alpha,elements)
    angle=np.arctan2(v[:,1],v[:,0])-np.pi/2
    uv=np.column_stack(((angle/(2*np.pi)*field.repeats)%1,(v[:,2]/field.tile)%1))
    from PIL import Image
    texture=folder/'benchmark_height.png'
    Image.fromarray(np.round(a*65535).astype(np.uint16)).save(texture)
    started=time.perf_counter()
    sampled,coverage=field.sample(uv)
    native=v+n*(sampled*coverage*field.height)[:,None]
    native_seconds=time.perf_counter()-started
    np.savez(folder/'benchmark_arrays.npz',v=v,f=m.faces,uv=uv)
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
        proc=subprocess.run([str(blender),'--background','--factory-startup','--python',str(script),'--',str(folder),str(field.height)],capture_output=True,text=True,timeout=180)
        report['blender_cold_process_seconds']=time.perf_counter()-started
        if proc.returncode:
            raise RuntimeError(proc.stdout[-2000:]+proc.stderr[-2000:])
        report.update(json.loads((folder/'benchmark_blender.json').read_text()))
        bv=np.load(folder/'benchmark_blender_vertices.npy')
        report['vertex_rms_difference_model_units']=float(np.sqrt(np.mean(np.sum((native-bv)**2,axis=1))))
        report['requested_height_model_units']=field.height
    else:
        report['blender']='not installed'
    (folder/'benchmark.json').write_text(json.dumps(report,indent=2))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out',type=Path,default=None)
    parser.add_argument('--quick',action='store_true',help='ellipsoid and bust, reliefs 0.4 and 1, contour lines and Plotter Vision')
    parser.add_argument('--models',default='ellipsoid,bust,head')
    parser.add_argument('--mappings',default='triplanar,cylindrical,surface')
    parser.add_argument('--reliefs',default='0.4,1,2')
    parser.add_argument('--drawings',default=','.join(DRAWINGS))
    parser.add_argument('--set',action='append',default=[],metavar='KEY=VALUE',help='override a tool value in every case')
    parser.add_argument('--presets',action='store_true',help='also the official presets on the bust')
    parser.add_argument('--benchmark',action='store_true')
    parser.add_argument('--resume',action='store_true',help='keep completed cases in this folder (same inputs and settings only)')
    args=parser.parse_args()
    if args.quick:
        args.models,args.reliefs,args.drawings='ellipsoid,bust','0.4,1','contour-lines,plotter-vision'
    out=(args.out or qa_dir('surface-texture')).resolve(); out.mkdir(parents=True,exist_ok=True)
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtGui import QGuiApplication
    app=QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (QSvgRenderer needs it)
    from atlas_toolkit.core import JobContext
    from atlas_toolkit.core.registry import registry
    from atlas_toolkit.tools.mesh_texture.engine import run
    warnings=[]
    def emit(kind,payload):
        if kind=='log' and payload[0]=='warning':
            warnings.append(payload[1])
    ctx=JobContext(emit,lambda:False)
    reg=registry(); tool=reg.get('mesh-texture')
    drawings=[d for d in args.drawings.split(',') if d]
    overrides={}
    for item in args.set:
        key,value=item.split('=',1)
        overrides[key]=json.loads(value) if value[:1] in '[{"0123456789-.tf' else value
    found=models(out,args.models.split(','))
    cases=[]
    for name,path in found.items():
        cases.append((f'{name} original',name,path,None))
        for mapping in args.mappings.split(','):
            for relief in args.reliefs.split(','):
                cases.append((f'{name} {mapping} {float(relief):g}',name,path,dict(overrides,mapping=mapping,relief=float(relief))))
    if args.presets and 'bust' in found and preset_packs():
        from atlas_toolkit.devpaths import preset_values
        for preset in preset_names(tool):
            cases.append((f'bust {preset}','bust',found['bust'],preset_values(tool,preset)))
    prior={}
    if args.resume and (out/'acceptance.json').exists():
        prior={r['name']:r for r in json.loads((out/'acceptance.json').read_text())}
    records=[]; originals={}
    for name,model,path,values in cases:
        key=name.lower().replace(' ','_').replace('.','_')
        if name in prior and 'error' not in prior[name] and all(Path(d.get('png','-')).exists() for d in prior[name]['drawings'].values()):
            records.append(prior[name])
            if values is None:
                originals[model]=prior[name]['drawings']
            print('Reusing',name,flush=True)
            continue
        dense=model!='ellipsoid'
        info={'name':name,'model':model}
        if values is None:
            from atlas_toolkit.geom.mesh import load_mesh
            info.update(original=True,faces=len(load_mesh(path).faces))
            info['drawings']=draw(reg,ctx,drawings,path,out,key,dense)
            originals[model]=info['drawings']
        else:
            p=tool.values(dict(values))
            dst=out/(key+'.ply')
            warnings.clear()
            started=time.perf_counter()
            try:
                result=run(ctx,path,dst,p)
            except Exception as error:
                info.update(error=f'{type(error).__name__}: {error}',seconds=time.perf_counter()-started)
                records.append(info)
                (out/'acceptance.json').write_text(json.dumps(records,indent=2))
                print(name,'FAILED',info['error'],flush=True)
                continue
            info.update({k:v for k,v in result.data.items() if isinstance(v,(int,float,str,bool,dict,type(None)))},
                        seconds=time.perf_counter()-started,warnings=list(warnings))
            info['drawings']=draw(reg,ctx,drawings,dst,out,key,dense,originals.get(model))
        info['process_peak_mb']=peak_mb()
        records.append(info)
        (out/'acceptance.json').write_text(json.dumps(records,indent=2))
        vis=' '.join(f"{d}={100*info['drawings'][d]['visibility']:.0f}%" for d in drawings
                     if 'visibility' in info.get('drawings',{}).get(d,{}))
        print(name,info.get('faces'),f"{info.get('seconds',0):.1f}s",f"restrained {info.get('limited',0):.1f}%",vis,flush=True)
    for drawing in drawings:
        print(sheet(out,records,drawing),flush=True)
    print(summary(out,records,drawings),flush=True)
    if args.benchmark:
        done=[r for r in records if not r.get('original') and 'error' not in r and r['model']=='ellipsoid']
        if done:
            print(benchmark(out,out/(done[0]['name'].lower().replace(' ','_').replace('.','_')+'.ply'),tool.values({})),flush=True)


if __name__=='__main__':
    main()
