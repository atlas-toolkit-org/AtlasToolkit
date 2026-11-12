"""Release gate: exercise packaged GIS and image readers, placement and drawing workflows."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def check(exe, folder):
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin
    from PIL import Image
    from atlas_toolkit.core import workflow as w
    from atlas_toolkit.geom.surface import load_surface
    from atlas_toolkit.tools.raster_dem_to_mesh.raster_dem_to_mesh import check_mesh
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    exe=Path(exe).resolve()
    console=exe.with_name('atlas-toolkit.exe') if sys.platform=='win32' else exe
    y,x=np.mgrid[-1:1:40j,-1:1:48j]
    hill=100+300*np.exp(-4*(x*x+y*y))+40*x
    geo=folder/'geographic.tif'
    with MemoryFile() as mem:
        with mem.open(driver='GTiff',width=48,height=40,count=1,dtype='float32',crs='EPSG:4326',
                      transform=from_origin(-8.8,41.9,.001,.001)) as ds:
            ds.write(hill.astype('float32'),1)
        geo.write_bytes(mem.read())
    png=folder/'heightmap.png'
    Image.fromarray((hill*100).astype('uint16')).save(png)
    home=folder/'home'
    environment=dict(os.environ,ATLAS_TOOLKIT_HOME=str(home),QT_QPA_PLATFORM='offscreen',PYINSTALLER_SUPPRESS_SPLASH_SCREEN='1')
    if sys.platform!='win32':environment['HOME']=str(home)
    environment.pop('DISPLAY',None);environment.pop('WAYLAND_DISPLAY',None)   # offscreen next to a display gets a failing OpenGL
    for src,tool_id in ((geo,'raster-dem-to-mesh'),(png,'image-to-mesh')):
        dst=folder/(src.stem+'.stl')
        result=subprocess.run([str(console),'run',tool_id,str(src),'-o',str(dst),
                               '--pitch','5','--yaw','35','--plate-fit','full','--force','--quiet'],capture_output=True,text=True,encoding='utf-8',
                              errors='replace',timeout=180,env=environment)
        if result.returncode or not dst.is_file():
            raise RuntimeError(f'Frozen raster check failed for {src.name}: {result.stdout}\n{result.stderr}')
        mesh=load_surface(dst).as_trimesh()
        check_mesh(mesh)
        if mesh.extents[0]>190.001 or mesh.extents[1]>277.001:
            raise RuntimeError('Frozen raster output exceeds its page fit.')
        print(f'ok frozen raster: {src.name}, {len(mesh.faces)} triangles, watertight',flush=True)
    # Exercise the bundled native adaptive dependency and physical scale lock,
    # independently of the regular-grid built-in tool self-test.
    adaptive=folder/'adaptive.stl'
    result=subprocess.run([str(console),'run','raster-dem-to-mesh',str(geo),'-o',str(adaptive),
                           '--sampling','adaptive','--maximum-edge','5','--boundary-error','0',
                           '--plate-fit','custom','--plate-width','180','--plate-height','220',
                           '--lock-scale','--terrain-width','100','--yaw','20','--force','--quiet'],
                          capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180,env=environment)
    if result.returncode or not adaptive.is_file():
        raise RuntimeError(f'Frozen adaptive/locked-plate check failed: {result.stdout}\n{result.stderr}')
    mesh=load_surface(adaptive).as_trimesh()
    check_mesh(mesh)
    np.testing.assert_allclose(mesh.extents[:2],[180,220],atol=2e-5)
    print('ok frozen adaptive TIN and locked custom plate',flush=True)
    config = home/'tools/raster-dem-to-mesh.json'
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({'values': {'yaw': 35., 'pitch': 5., 'plate_fit': 'full'}}), encoding='utf-8')
    shot = folder/'placement.png'
    shot.unlink(missing_ok=True)
    highres=folder/'high-resolution.tif'
    with rasterio.open(highres,'w',driver='GTiff',width=1600,height=1300,count=1,dtype='uint16',
                       crs='EPSG:3763',transform=from_origin(0,100000,20,20),compress='deflate',tiled=True) as ds:
        for start in range(0,1300,256):
            from rasterio.windows import Window
            y,x=np.mgrid[start:min(start+256,1300),0:1600]
            ds.write((100+200*np.exp(-((x-800)**2+(y-650)**2)/200000)).astype('uint16'),1,
                     window=Window(0,start,1600,len(y)))
    result = subprocess.run([str(exe), '--tool', 'raster-dem-to-mesh', str(highres), '--view', 'Placement',
                             '--snapshot', str(shot)], capture_output=True, timeout=180, env=environment)
    if result.returncode or not shot.is_file():
        raise RuntimeError(f'Frozen terrain placement failed: {result.returncode}; {result.stderr!r}')
    print(f'ok frozen terrain placement: {shot}', flush=True)
    flow=w.load(ROOT/'docs/workflows/dem_contours.atlasflow')
    for node in flow.nodes.values():
        if node.tool=='raster-dem-to-mesh':
            node.values.update(pitch=5.,height_mode='target',relief_height=15.,z_factor=1.)
        elif node.tool=='contour-lines':
            node.values['spacing_mm']=4.
        elif node.type==w.SAVE:
            node.where='folder'
            node.folder=str(folder/'workflow-output')
    flow_path=folder/'terrain.atlasflow'
    w.save(flow,flow_path)
    shot=folder/'workflow.png'
    shot.unlink(missing_ok=True)
    result=subprocess.run([str(exe),'--workflow',str(flow_path),str(geo),'--draft','--snapshot',str(shot)],
                          capture_output=True,timeout=180,env=environment)
    entries=list((home/'cache/workflows').rglob('entry.json'))
    if result.returncode or not shot.is_file() or not entries:
        raise RuntimeError(f'Frozen terrain workflow failed: {result.returncode}; {result.stderr!r}')
    print(f'ok frozen terrain workflow: {shot}',flush=True)
    rgba=np.empty((40,48,4),np.uint8)
    rgba[:,:,:3]=np.stack([np.clip(hill/2,0,255),np.clip(hill/3,0,255),np.clip(255-hill/3,0,255)],axis=2).astype(np.uint8)
    rgba[:,:,3]=255
    rgba[10:15,20:25,3]=0
    color=folder/'color.png'
    Image.fromarray(rgba).save(color)
    # Both tools must expose the shared repair policy and preserve alpha/mask
    # islands through signed, printable final exports in the frozen runtime.
    contacts=folder/'contact-image.png'
    contact_rgba=rgba.copy()
    contact_rgba[:,:,:3]=128
    contact_rgba[:,:,3]=0
    contact_rgba[4:36,5:42,3]=255
    contact_rgba[14:19,16:21,3]=0
    contact_rgba[0,0,3]=contact_rgba[1,1,3]=255
    Image.fromarray(contact_rgba).save(contacts)
    dem_contacts=folder/'contact-dem.tif'
    data=np.where(contact_rgba[:,:,3]>0,hill,-9999).astype('float32')
    with rasterio.open(dem_contacts,'w',driver='GTiff',width=48,height=40,count=1,dtype='float32',nodata=-9999,
                       crs='EPSG:3763',transform=from_origin(0,100000,20,20)) as ds:ds.write(data,1)
    for src,tool_id in ((contacts,'image-to-mesh'),(dem_contacts,'raster-dem-to-mesh')):
        output=folder/(tool_id+'-automatic.stl')
        result=subprocess.run([str(console),'run',tool_id,str(src),'-o',str(output),'--pitch','2',
                               '--contact-policy','automatic','--force','--quiet'],capture_output=True,text=True,
                              encoding='utf-8',errors='replace',timeout=180,env=environment)
        if result.returncode:raise RuntimeError(f'Frozen shared contact repair failed: {result.stdout}\n{result.stderr}')
        check_mesh(load_surface(output).as_trimesh())
        print('ok frozen automatic contact repair:',tool_id,flush=True)
    for view_name in ('Placement','Prepared heightmap'):
        shot=folder/('image-'+view_name.replace(' ','-')+'.png')
        result=subprocess.run([str(exe),'--tool','image-to-mesh',str(color),'--draft','--view',view_name,'--snapshot',str(shot)],
                              capture_output=True,timeout=180,env=environment)
        if result.returncode or not shot.is_file():
            raise RuntimeError(f'Frozen image {view_name} failed: {result.returncode}; {result.stderr!r}')
        print(f'ok frozen image {view_name}: {shot}',flush=True)
    for name in ('image_contours','image_facets'):
        flow=w.load(ROOT/('docs/workflows/'+name+'.atlasflow'))
        for node in flow.nodes.values():
            if node.tool=='image-to-mesh':
                node.values.update(pitch=5.,brightness=.05,gamma=1.2)
            elif node.type==w.SAVE:
                node.where='folder'
                node.folder=str(folder/(name+'-output'))
        path=folder/(name+'.atlasflow')
        w.save(flow,path)
        shot=folder/(name+'.png')
        result=subprocess.run([str(exe),'--workflow',str(path),str(color),'--draft','--snapshot',str(shot)],
                              capture_output=True,timeout=180,env=environment)
        if result.returncode or not shot.is_file():
            raise RuntimeError(f'Frozen {name} workflow failed: {result.returncode}; {result.stderr!r}')
        print(f'ok frozen {name} workflow: {shot}',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    if args.out:
        check(args.exe,args.out)
    else:
        with tempfile.TemporaryDirectory(prefix='atlas_frozen_raster_') as folder:
            check(args.exe,folder)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
