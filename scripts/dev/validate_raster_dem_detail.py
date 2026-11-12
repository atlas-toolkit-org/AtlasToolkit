"""Repeatable 1.10.6 source-data, placement and full-resolution mesh acceptance checks."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def render(path,output):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QVector3D
    from atlas_toolkit.geom.surface import load_surface
    from atlas_toolkit.geom.mesh import MeshData
    from atlas_toolkit.ui.theme import Theme,load_fonts,sans
    from atlas_toolkit.ui.widgets.mesh_view import MeshView
    app=QApplication.instance() or QApplication([])
    load_fonts()
    app.setFont(sans())
    mesh=load_surface(path).as_trimesh()
    widget=MeshView(Theme('dark'))
    widget.resize(1200,900)
    widget.show()
    data=MeshData(mesh.vertices.astype('float32'),mesh.faces.astype('int32'),len(mesh.faces),tuple(map(tuple,mesh.bounds)))
    widget.show_mesh(data)
    if widget.view is None:
        raise RuntimeError('OpenGL is required for acceptance renders.')
    widget.view.setCameraPosition(distance=270,azimuth=-55,elevation=35)
    for _ in range(5):
        app.processEvents()
    widget.grab().save(str(output))
    widget.view.opts['center']=QVector3D(0.,0.,float(mesh.bounds[1,2]-1))
    widget.view.setCameraPosition(distance=65,azimuth=-55,elevation=35)
    for _ in range(5):
        app.processEvents()
    widget.grab().save(str(output.with_stem(output.stem+'-closeup')))
    widget.close()


def placement(path,out):
    from PySide6.QtWidgets import QApplication,QWidget
    from atlas_toolkit.tools.raster_dem_to_mesh import TOOL
    from atlas_toolkit.tools.raster_dem_to_mesh.viewer import PlacementView
    from atlas_toolkit.ui.widgets.param_form import ParamForm
    from atlas_toolkit.ui.theme import Theme,load_fonts,sans
    app=QApplication.instance() or QApplication([])
    load_fonts()
    app.setFont(sans())
    class Page(QWidget):
        theme=Theme('dark')
        def __init__(self):
            super().__init__()
            self.form=ParamForm(TOOL,self.theme)
        def values(self):return self.form.values()
        def busy(self):return False
        def _settings_changed(self):pass
    page=Page()
    widget=PlacementView(page)
    widget.resize(1100,950)
    widget.show()
    start=time.perf_counter()
    loaded=[]
    widget.loaded.connect(lambda *args:loaded.append(time.perf_counter()-start))
    widget.set_input(path,None)
    while len(loaded)<2 and time.perf_counter()-start<60:
        app.processEvents()
        widget.timer.stop()
        time.sleep(.002)
    if len(loaded)<2 or widget.data is None:
        raise RuntimeError(f'Placement load failed: {widget.message}')
    timings={}
    widget.lock_terrain(True)
    # Both measured loops include form changes, layout and synchronous repaint.
    for action in ('rotation','resize'):
        samples=[]
        if action=='resize':
            widget.fit_terrain()
            widget.change(dict(lock_scale=True,terrain_width=120.,plate_fit='custom',plate_width=180.,plate_height=200.))
        for i in range(45):
            t=time.perf_counter()
            if action=='rotation':widget.commit((i%20)-10.)
            else:widget.resize_plate(180.+i%5,200.+i%7)
            widget.canvas.repaint()
            app.processEvents()
            widget.timer.stop()
            samples.append((time.perf_counter()-t)*1000)
        timings[action+'_p95_ms']=round(float(np.percentile(samples,95)),3)
    timings.update(first_display_seconds=round(loaded[0],4),prepared_seconds=round(loaded[1],4))
    widget.grab().save(str(out/'placement.png'))
    (out/'placement-performance.json').write_text(json.dumps(timings,indent=2),encoding='utf-8')
    print(json.dumps(timings),flush=True)
    widget.generation+=1
    widget.close()
    return timings


def main():
    from atlas_toolkit.core.job import JobContext
    from atlas_toolkit.devpaths import preset_raw
    from atlas_toolkit.tools.raster_dem_to_mesh import TOOL
    from atlas_toolkit.tools.raster_dem_to_mesh.engine import run
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=qa_dir('dem106_validation'))
    parser.add_argument('--mode',choices=['meshes','ui','renders','all'],default='all')
    parser.add_argument('--sample',choices=['aminho','viana','both'],default='both')
    parser.add_argument('--detail',choices=['standard','detailed','fine','all'],default='all')
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    application=None
    if args.mode in ('renders','all','ui'):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        application=QApplication.instance() or QApplication([])
    sources={'aminho':studio_workspace()/'code/R/generators/3d_DEM/DEM20m_AMinho.tif',
             'viana':studio_workspace()/'code/R/generators/3d_DEM/dem_vc.tif'}
    if args.mode in ('ui','all'):placement(sources['aminho'],args.out)
    summary=[]
    for sample,src in sources.items():
        if args.sample not in ('both',sample):continue
        before=digest(src)
        for name,changes in [('standard',{}),('detailed',preset_raw(TOOL,'Detailed faithful')),('fine',preset_raw(TOOL,'Fine faithful'))]:
            if args.detail not in ('all',name):continue
            dst=args.out/f'{sample}-{name}.stl'
            if args.mode in ('meshes','all'):
                values=TOOL.values(dict(changes,contact_trim=.002,exclude_background=sample=='viana',background=10.))
                start=time.perf_counter()
                result=run(JobContext(lambda kind,data:print(f'{sample}/{name} {data}',flush=True) if kind=='progress' else None,lambda:False),src,dst,values)
                row=dict(sample=sample,detail=name,seconds=round(time.perf_counter()-start,3),
                         export_faces=result.stat('faces').value,values=dict(values),data=result.data)
                if name=='standard':
                    from atlas_toolkit.tools.raster_dem_to_mesh.raster import read
                    from atlas_toolkit.tools.raster_dem_to_mesh.adaptive import errors
                    from atlas_toolkit.geom.surface import load_surface
                    native=read(src,values)
                    top=load_surface(dst).as_trimesh()
                    top.update_faces(top.face_normals[:,2]>1e-10)
                    scale=result.data['scale']
                    top.vertices[:,:2]+=np.asarray(result.data['centre'])*scale
                    reference=(native.heights-native.metadata['source_range'][0])*scale+result.data['base_height']+values.basal_height
                    maximum,rms=errors(top,reference,native.valid,native.width*scale,native.height*scale,JobContext(lambda *a:None,lambda:False))
                    row['comparison_maximum_error_mm']=maximum
                    row['comparison_rms_error_mm']=float(rms)
                summary.append(row)
                (args.out/f'{sample}-{name}.json').write_text(json.dumps(row,indent=2),encoding='utf-8')
                print(result.report,flush=True)
            if args.mode in ('renders','all'):render(dst,dst.with_suffix('.png'))
        if digest(src)!=before:raise RuntimeError('Acceptance source was modified.')
    if summary:(args.out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return 0


if __name__=='__main__':raise SystemExit(main())
