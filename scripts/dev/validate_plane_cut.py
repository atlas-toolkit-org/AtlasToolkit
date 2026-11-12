"""Repeatable real-model geometry checks and native OpenGL screenshots; writes only to --output."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401
WORKSPACE = studio_workspace()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=qa_dir("plane_cut_validation"))
    ap.add_argument("--model", type=Path, default=WORKSPACE / "3d_models_db" / "head_model" / "v17_head.stl")
    ap.add_argument("--snapshots", action="store_true")
    args = ap.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(folder / "validation-settings")
    os.environ["LOCALAPPDATA"] = str(folder / "validation-local")
    os.environ["TMP"] = os.environ["TEMP"] = str(folder / "tmp")
    (folder / "tmp").mkdir(exist_ok=True)
    from atlas_toolkit.geom import mesh as gm
    from atlas_toolkit.tools.plane_cut import TOOL
    from atlas_toolkit.tools.plane_cut.plane_cut import cut
    from atlas_toolkit.version import __version__
    mesh = gm.load_mesh(args.model)
    rows = []
    for fill in ("none", "minimal", "remeshed", "fixed"):
        start = time.perf_counter()
        values = TOOL.values(dict(cut_type="slice", fill=fill, cap_size=float(mesh.extents.max())/25,
                                  rotation=(17, 23, 0)))
        parts, _, notes = cut(mesh, values)
        if mesh.is_watertight and fill != "none":
            assert all(m.is_watertight for _, m in parts)
            assert abs(sum(m.volume for _, m in parts)-mesh.volume) < abs(mesh.volume)*1e-6
        for side, part in parts:
            gm.save_mesh(part, folder/f"validation-{fill}-{side}.stl", TOOL)
        rows.append(dict(fill=fill, seconds=time.perf_counter()-start, pieces=len(parts), warnings=notes,
                         faces=[len(m.faces) for _, m in parts], closed=[bool(m.is_watertight) for _, m in parts]))
    report = dict(app_version=__version__, source=str(args.model), source_faces=len(mesh.faces),
                  source_closed=bool(mesh.is_watertight), cases=rows)
    (folder / "geometry-validation.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2),flush=True)
    if args.snapshots:
        snapshots(args.model,folder)


def snapshots(source,folder):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from atlas_toolkit.core.history import History
    from atlas_toolkit.core.registry import registry
    from atlas_toolkit.core.settings import AppSettings
    from atlas_toolkit.ui.bridge import JobBridge
    from atlas_toolkit.ui.context import AppContext
    from atlas_toolkit.ui.shell import MainWindow
    from atlas_toolkit.ui.theme import load_fonts
    app=QApplication.instance() or QApplication([])
    load_fonts()
    ctx=AppContext(registry(),AppSettings(),JobBridge(),History())
    ctx.set_theme("dark")
    win=MainWindow(ctx)
    win.resize(1440,960)
    page=win.open_tool("plane-cut",[str(source)],values={"rotation":(17.,23.,0.),"fill":"minimal"})
    page.store.out_mode="folder"
    page.out_dir=str(folder / "exports")
    page.out_folder.setChecked(True)
    win.show()
    stage={"phase":0,"ticks":0}
    def tick():
        stage["ticks"]+=1
        if stage["ticks"]>500:
            raise RuntimeError("GUI validation timed out")
        if not page.idle() or not page.runnable():
            QTimer.singleShot(100,tick)
            return
        viewer=page.viewer
        if stage["phase"]==0:
            viewer.set_mode("move")
            stage["phase"]=1
        elif stage["phase"]==1:
            win.grab().save(str(folder/"placement-dark.png"))
            viewer.expand()
            viewer.dialog.showMaximized()
            stage["phase"]=2
        elif stage["phase"]==2:
            viewer.dialog.grab().save(str(folder/"expanded-move.png"))
            viewer.set_mode("rotate")
            stage["phase"]=3
        elif stage["phase"]==3:
            viewer.dialog.grab().save(str(folder/"expanded-rotate.png"))
            viewer.dialog.accept()
            ctx.set_theme("light")
            stage["phase"]=4
        elif stage["phase"]==4:
            win.grab().save(str(folder/"placement-light.png"))
            page.debounce.stop()
            page._apply_settings()
            page.preview_selected()
            stage["phase"]=5
        elif stage["phase"]==5:
            row=page._current_row()
            assert row.result is not None, row.status
            win.grab().save(str(folder/"computed-result.png"))
            win.close()
            return
        QTimer.singleShot(400,tick)
    QTimer.singleShot(600,tick)
    app.exec()
    ctx.bridge.shutdown()


if __name__=="__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
