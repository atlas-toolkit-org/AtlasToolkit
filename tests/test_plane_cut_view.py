"""The actual editor widgets: projection, line strokes, gizmos, settings sync, undo and modal restoration."""
import _isolate  # first: settings and caches stay in a temporary folder
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import trimesh
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from atlas_toolkit.core.tool import InputInfo
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.plane_cut import TOOL
from atlas_toolkit.tools.plane_cut.plane_cut import plane
from atlas_toolkit.tools.plane_cut.viewer import PlaneView
from atlas_toolkit.ui.theme import Theme
from atlas_toolkit.ui.widgets.param_form import ParamForm


class _Page(QWidget):
    def __init__(self):
        super().__init__()
        self.tool, self.theme = TOOL, Theme("dark")
        self.form = ParamForm(TOOL, self.theme)
        self.form.set_values(TOOL.defaults())
        self.debounce = QTimer(self)
        self.updates = 0
        self.viewer = PlaneView(self)
        layout=QVBoxLayout(self)
        layout.addWidget(self.viewer)
        self.resize(800,700)

    def values(self):
        return self.form.values()

    def _settings_changed(self):
        self.updates += 1


class PlaneEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Earlier GUI cases can leave queued Qt timers. Any event processing here must also use an isolated
        # settings tree, even though this small test page does not itself create AppSettings or a workflow.
        cls.temp = tempfile.TemporaryDirectory(prefix="atlas_plane_editor_")
        cls.leave_home = _isolate.fresh_home()
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        cls.leave_home()
        cls.temp.cleanup()

    def setUp(self):
        self.page=_Page()
        self.view=self.page.viewer
        self.mesh=trimesh.creation.icosphere(subdivisions=2,radius=10)
        self.info=InputInfo('test.stl','mesh',preview={"views":[{"kind":"mesh","mesh":gm.preview(self.mesh)}]})
        self.view.set_input('test.stl',self.info)
        self.page.resize(900,750)
        # Layout activation gives projection math its real logical viewport dimensions without requiring OpenGL.
        self.page.layout().activate()
        self.view.root.activate()
        self.view.layout_editor.activate()
        self.view.canvas.resize(700,450)

    def tearDown(self):
        if self.view.dialog:
            self.view.dialog.reject()
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()

    def mouse(self,kind,pos,button=Qt.LeftButton,buttons=Qt.LeftButton,modifiers=Qt.NoModifier):
        canvas=self.view.canvas
        event=QMouseEvent(kind,QPointF(*pos),QPointF(*pos),button,buttons,modifiers)
        self.app.sendEvent(canvas,event)

    def drag(self,start,end):
        from PySide6.QtCore import QEvent
        self.mouse(QEvent.MouseButtonPress,start)
        self.mouse(QEvent.MouseMove,end,Qt.NoButton)
        self.mouse(QEvent.MouseButtonRelease,end,buttons=Qt.NoButton)

    def test_drawn_line_contains_both_stroke_rays_in_both_projections(self):
        for ortho in (False,True):
            self.view.ortho.setChecked(ortho)
            canvas=self.view.canvas
            start,end=np.array((180.,160.)),np.array((500.,280.))
            self.view.set_mode('draw')
            self.drag(start,end)
            self.assertEqual(self.view.mode,'move')
            origin,normal,_=plane(self.mesh.bounds,self.page.values())
            for point in (start,end):
                for z in (-1,1):
                    world=canvas.unproject(*point,z)
                    self.assertAlmostEqual(np.dot(world-origin,normal),0,delta=.02)
            self.assertGreater(self.page.updates,0)

    def test_translate_arrow_normal_handle_rotation_ring_and_undo(self):
        self.view.set_mode('move')
        handles=self.view.canvas.handles()
        x=next(h for h in handles if h[1]==0)
        start=x[2][0]*.2+x[2][-1]*.8
        end=start+(x[2][-1]-x[2][0])*.25
        self.drag(start,end)
        self.assertGreater(abs(self.page.values()['position'][0]),.1)
        self.assertAlmostEqual(self.page.values()['position'][1],0)
        self.view.undo()
        self.assertEqual(self.page.values()['position'],(0,0,0))
        n=next(h for h in self.view.canvas.handles() if h[0]=='normal')
        start=n[2][-1]
        self.drag(start,start+(n[2][-1]-n[2][0])*.2)
        self.assertGreater(abs(self.page.values()['offset']),.1)
        self.view.set_mode('rotate')
        ring=next(h for h in self.view.canvas.handles() if h[1]==0)
        self.drag(ring[2][8],ring[2][16])
        self.assertGreater(np.linalg.norm(self.page.values()['rotation']),1)

    def test_numeric_updates_flip_snaps_and_modal_cancel(self):
        self.view.commit({'position':(2,3,4),'rotation':(20,30,40)})
        self.view.flip()
        self.assertEqual(self.page.values()['side'],'negative')
        self.view.align_plane(2)
        np.testing.assert_allclose(plane(self.mesh.bounds,self.page.values())[1],(1,0,0),atol=1e-6)
        snapshot=self.page.values()
        self.view.expand()
        self.assertIsNotNone(self.view.dialog)
        self.view.commit({'position':(9,8,7)})
        self.assertEqual(self.view.dialog_form.values()['position'],(9,8,7))
        self.view.dialog.reject()
        self.assertEqual(self.page.values(),snapshot)
        self.assertIsNone(self.view.dialog)
        self.assertIs(self.view.editor.parent(),self.view)
        self.view.expand()
        self.view.commit({'offset':5})
        self.view.dialog.accept()
        self.assertEqual(self.page.values()['offset'],5)
        self.view.set_theme(Theme('light'))
        self.view.set_input(None,None)
        self.assertIsNone(self.view.bounds)


if __name__=='__main__':
    unittest.main()
