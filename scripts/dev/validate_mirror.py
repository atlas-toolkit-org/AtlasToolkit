"""Real-model reflection checks and native renders for Mirror and the reused Plane Cut controls."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401
WORKSPACE = studio_workspace()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=qa_dir('mirror_validation'))
    parser.add_argument('--snapshots',action='store_true')
    args=parser.parse_args()
    folder=args.output.resolve();folder.mkdir(parents=True,exist_ok=True)
    for key,sub in [('APPDATA','validation-settings'),('LOCALAPPDATA','validation-local'),('TMP','tmp'),('TEMP','tmp')]:
        os.environ[key]=str(folder/sub)
    (folder/'tmp').mkdir(exist_ok=True)
    import numpy as np
    import trimesh
    from atlas_toolkit.geom import mesh as gm
    from atlas_toolkit.tools.mesh_mirror import TOOL
    from atlas_toolkit.tools.mesh_mirror.mesh_mirror import mirror
    from atlas_toolkit.version import __version__
    head=WORKSPACE/'3d_models_db'/'head_model'/'v17_head.stl'
    bunny=Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'Autodesk/Meshmixer/meshmixer/models/bunnyr.obj'
    models=[head]
    if bunny.is_file(): models.append(bunny)
    results=[]
    for source in models:
        mesh=gm.load_mesh(source)
        for reference,rotation in [('side',(0,90,0)),('world',(17,23,31)),('center',(-22,70,11))]:
            start=time.perf_counter()
            p=TOOL.values(dict(reference=reference,rotation=rotation))
            reflected,(origin,normal)=mirror(mesh,p)
            distance_error=float(np.max(np.abs((reflected.vertices-origin)@normal+(mesh.vertices-origin)@normal)))
            assert distance_error<max(float(mesh.extents.max())*1e-10,1e-10)
            assert reflected.is_watertight==mesh.is_watertight
            assert abs(reflected.area-mesh.area)<mesh.area*1e-10
            if mesh.is_watertight: assert abs(reflected.volume-mesh.volume)<abs(mesh.volume)*1e-10
            results.append(dict(source=str(source),faces=len(mesh.faces),reference=reference,rotation=rotation,
                                distance_error=distance_error,closed=bool(reflected.is_watertight),seconds=time.perf_counter()-start))
    # Upright copy of Meshmixer's Y-up sample for a Z-up Atlas screenshot. The installed sample is never changed.
    sample=gm.load_mesh(bunny if bunny.is_file() else head)
    if bunny.is_file(): sample.apply_transform(trimesh.transformations.rotation_matrix(np.pi/2,[1,0,0]))
    gui_source=folder/'mirror-input.stl';sample.export(gui_source)
    report=dict(app_version=__version__,cases=results)
    (folder/'geometry-validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True)
    if args.snapshots: snapshots(gui_source,head,folder)


def snapshots(source,head,folder):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from atlas_toolkit.core.history import History
    from atlas_toolkit.core.registry import registry
    from atlas_toolkit.core.settings import AppSettings
    from atlas_toolkit.ui.bridge import JobBridge
    from atlas_toolkit.ui.context import AppContext
    from atlas_toolkit.ui.shell import MainWindow
    from atlas_toolkit.ui.theme import load_fonts
    app=QApplication.instance() or QApplication([]);load_fonts()
    ctx=AppContext(registry(),AppSettings(),JobBridge(),History());ctx.set_theme('dark')
    win=MainWindow(ctx);win.resize(1440,960)
    page=win.open_tool('mesh-mirror',[str(source)],values=dict(result_mode='copy',reference='side',
                       position=(0,0,0),rotation=(0,90,0),offset=0))
    page.out_dir=str(folder/'exports');page.out_folder.setChecked(True)
    win.show()
    state={'phase':0,'ticks':0,'page':page}
    def tick():
        state['ticks']+=1
        if state['ticks']>500: raise RuntimeError('GUI validation timed out')
        current=state['page']
        if not current.idle() or not current.runnable():
            QTimer.singleShot(100,tick);return
        viewer=current.viewer
        phase=state['phase']
        if phase==0:
            viewer.set_mode('move')
        elif phase==1:
            win.grab().save(str(folder/'placement-dark.png'))
            viewer.expand();viewer.dialog.showMaximized()
        elif phase==2:
            viewer.dialog.grab().save(str(folder/'expanded-move.png'))
            viewer.set_mode('rotate')
        elif phase==3:
            viewer.dialog.grab().save(str(folder/'expanded-rotate.png'))
            viewer.dialog.accept();viewer.commit({'result_mode':'mirror'})
        elif phase==4:
            win.grab().save(str(folder/'mirror-only.png'));ctx.set_theme('light')
        elif phase==5:
            win.grab().save(str(folder/'placement-light.png'))
            ctx.set_theme('dark');viewer.commit({'result_mode':'copy'})
            current.debounce.stop();current._apply_settings();current.preview_selected()
        elif phase==6:
            row=current._current_row();assert row.result is not None,row.status
            win.grab().save(str(folder/'computed-result.png'))
            state['page']=win.open_tool('plane-cut',[str(head)],values=dict(fill='minimal',rotation=(17,23,0)))
        elif phase==7:
            viewer.set_mode('move')
        elif phase==8:
            win.grab().save(str(folder/'plane-cut-after-reuse.png'));win.close();return
        state['phase']+=1
        QTimer.singleShot(450,tick)
    QTimer.singleShot(600,tick)
    app.exec();ctx.bridge.shutdown()


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
