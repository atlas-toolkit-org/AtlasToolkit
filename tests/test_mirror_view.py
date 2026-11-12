"""The shared plane editor as hosted by Mirror, using actual Qt mouse gestures and settings forms."""
import _isolate  # first: settings and caches stay in a temporary folder
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import numpy as np
import trimesh
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from atlas_toolkit.core.tool import InputInfo
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.plane import plane
from atlas_toolkit.tools.mesh_mirror import TOOL
from atlas_toolkit.tools.mesh_mirror.viewer import MirrorView
from atlas_toolkit.ui.theme import Theme
from atlas_toolkit.ui.widgets.param_form import ParamForm
from atlas_toolkit.ui.widgets.plane_editor import PlaneEditor


class _Page(QWidget):
    def __init__(self):
        super().__init__()
        self.tool,self.theme=TOOL,Theme('dark')
        self.form=ParamForm(TOOL,self.theme);self.form.set_values(TOOL.defaults())
        self.debounce=QTimer(self)
        self.viewer=MirrorView(self)
        QVBoxLayout(self).addWidget(self.viewer)

    def values(self):
        return self.form.values()

    def _settings_changed(self):
        pass


class MirrorEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='atlas_mirror_view_')
        cls.leave_home = _isolate.fresh_home()
        cls.app=QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        cls.leave_home()
        cls.temp.cleanup()

    def setUp(self):
        self.page=_Page();self.view=self.page.viewer
        self.mesh=trimesh.creation.box(extents=(10,12,14))
        self.mesh.apply_translation((2,-3,4))
        info=InputInfo('test.stl','mesh',preview={'views':[{'kind':'mesh','mesh':gm.preview(self.mesh)}]})
        self.view.set_input('test.stl',info)
        self.page.resize(1000,750);self.page.layout().activate();self.view.root.activate()
        self.view.layout_editor.activate();self.view.canvas.resize(700,450)

    def tearDown(self):
        if self.view.dialog: self.view.dialog.reject()
        self.page.close();self.page.deleteLater();self.app.processEvents()

    def drag(self,start,end):
        for event,pos,button,buttons in [(QEvent.MouseButtonPress,start,Qt.LeftButton,Qt.LeftButton),
                                         (QEvent.MouseMove,end,Qt.NoButton,Qt.LeftButton),
                                         (QEvent.MouseButtonRelease,end,Qt.LeftButton,Qt.NoButton)]:
            self.app.sendEvent(self.view.canvas,QMouseEvent(event,QPointF(*pos),QPointF(*pos),button,buttons,Qt.NoModifier))

    def test_shared_controls_initial_placement_and_opposite_side(self):
        self.assertIsInstance(self.view,PlaneEditor)
        before=self.view.frame()[0].copy()
        self.assertAlmostEqual(before[0],self.mesh.bounds[1,0])
        self.view.flip()
        np.testing.assert_allclose(self.view.frame()[0],2*self.mesh.bounds.mean(axis=0)-before,atol=1e-6)
        self.assertEqual(self.page.values()['rotation'],(0,90,0))
        self.view.undo()
        np.testing.assert_allclose(self.view.frame()[0],before,atol=1e-6)
        self.view.commit({'rotation':(10,20,30)})
        np.testing.assert_allclose(self.view.frame()[0],before,atol=1e-6) # rotate about the existing side pivot
        self.view.center()
        np.testing.assert_allclose(self.view.frame()[0],self.mesh.bounds.mean(axis=0),atol=1e-6)

    def test_draw_move_rotate_and_normal_drag(self):
        for ortho in (False,True):
            self.view.ortho.setChecked(ortho)
            start,end=np.array((170.,150.)),np.array((480.,300.))
            self.view.set_mode('draw');self.drag(start,end)
            origin,normal,_=plane(self.mesh.bounds,self.page.values())
            for point in (start,end):
                for z in (-1,1):
                    self.assertAlmostEqual(np.dot(self.view.canvas.unproject(*point,z)-origin,normal),0,delta=.02)
        arrow=next(h for h in self.view.canvas.handles() if h[1]==0)
        start=arrow[2][0]*.1+arrow[2][-1]*.9
        previous=self.page.values()['position']
        self.drag(start,start+(arrow[2][-1]-arrow[2][0])*.25)
        self.assertGreater(abs(self.page.values()['position'][0]-previous[0]),.1)
        n=next(h for h in self.view.canvas.handles() if h[0]=='normal')
        self.drag(n[2][-1],n[2][-1]+(n[2][-1]-n[2][0])*.2)
        self.assertGreater(abs(self.page.values()['offset']),.1)
        self.view.set_mode('rotate')
        ring=next(h for h in self.view.canvas.handles() if h[1]==0)
        previous=self.page.values()['rotation']
        self.drag(ring[2][8],ring[2][16])
        self.assertGreater(np.linalg.norm(np.array(self.page.values()['rotation'])-previous),1)

    def test_modes_shader_geometry_fit_and_expanded_accept_cancel(self):
        self.view.commit({'result_mode':'mirror'})
        self.assertTrue(self.view.ghost.isEnabled())
        self.view.ghost.setChecked(False)
        self.assertFalse(self.view.mesh_items[0].visible())
        self.assertTrue(self.view.mesh_items[1].visible())
        self.assertEqual(self.view.mesh_items[1].shader().uniformData['reflect_mesh'],[1.])
        snapshot=self.page.values()
        self.view.expand();self.assertIsNotNone(self.view.dialog)
        self.view.commit({'position':(7,8,9)})
        self.assertEqual(self.view.dialog_form.values()['position'],(7,8,9))
        self.view.dialog.reject();self.assertEqual(self.page.values(),snapshot)
        self.assertIs(self.view.editor.parent(),self.view)
        self.view.expand();self.view.commit({'offset':2});self.view.dialog.accept()
        self.assertEqual(self.page.values()['offset'],2)
        self.view.set_theme(Theme('light'))
        self.view.fit()
        self.view.set_input(None,None)
        self.assertIsNone(self.view.bounds)


if __name__=='__main__':
    unittest.main()
