"""Styled app screenshots, physical placement frame timing and cached DEM draft timing."""
import json,sys,time
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401
sys.path.insert(0,str(ROOT/'src'))


def main():
    from tests.test_gui import GuiFlowTests
    from atlas_toolkit.core.job import JobContext
    from atlas_toolkit.tools.raster_dem_to_mesh import TOOL
    from atlas_toolkit.tools.raster_dem_to_mesh.engine import run
    out=qa_dir('heightfield111_validation')
    out.mkdir(parents=True,exist_ok=True)
    src=studio_workspace()/'code/R/generators/3d_DEM/DEM20m_AMinho.tif'
    GuiFlowTests.setUpClass()
    test=GuiFlowTests()
    result={}
    try:
        win=test.win
        win.resize(1550,1050)
        win.show()
        for tool_id,path in [('raster-dem-to-mesh',src),('image-to-mesh',out/'alpha-heightfield.png')]:
            tool=test.ctx.registry.get(tool_id)
            values=dict(tool.defaults(),contact_policy='automatic',lock_scale=True,terrain_width=135.,plate_fit='custom',plate_width=185.,plate_height=220.)
            page=win.open_tool(tool_id,[str(path)],values=values)
            page.queue.setCurrentItem(page.rows[str(path.resolve())].item)
            view=page.viewer
            test.wait(lambda:view.authoritative and page.idle() and not view.timer.isActive() and page._current_row().result is not None)
            assert not page._current_row().error,page._current_row().error
            titles=[page.stage.tabs.tabText(i) for i in range(page.stage.tabs.count())]
            page.stage.tabs.setCurrentIndex(titles.index('Placement'))
            for theme in ('dark','light'):
                test.ctx.set_theme(theme)
                for _ in range(3):test.app.processEvents()
                win.grab().save(str(out/(tool_id+'-'+theme+'.png')))
            view.expand()
            view.dialog.resize(1200,950)
            test.app.processEvents()
            view.dialog.grab().save(str(out/(tool_id+'-expanded.png')))
            timings={}
            for action in ('rotation','resize'):
                samples=[]
                for i in range(50):
                    t=time.perf_counter()
                    if action=='rotation':view.commit((i%20)-10.)
                    else:view.resize_plate(185.+i%4,220.+i%5)
                    view.canvas.repaint()
                    test.app.processEvents()
                    view.timer.stop()
                    page.debounce.stop()
                    samples.append((time.perf_counter()-t)*1000)
                timings[action+'_p95_ms']=float(np.percentile(samples,95))
            result[tool_id]=timings
            view.dialog.accept()
            test.app.processEvents()
        values=TOOL.values(dict(contact_policy='automatic'))
        result['dem_drafts_seconds']=[]
        for _ in range(2):
            t=time.perf_counter()
            run(JobContext(lambda *a:None,lambda:False,mode='draft'),src,None,values)
            result['dem_drafts_seconds'].append(time.perf_counter()-t)
        print(json.dumps(result,indent=2),flush=True)
        (out/'ui-performance.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    finally:GuiFlowTests.tearDownClass()
    return 0


if __name__=='__main__':raise SystemExit(main())
