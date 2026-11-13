"""
The app end to end, offscreen: open tools, add files, run (thread and worker-process lanes), preview, switch
theme. Settings and history go to a temporary folder, never the user's.
"""
import _isolate  # first: settings and caches stay in a temporary folder
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class GuiFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="atlas_gui_"))
        cls.leave_home = _isolate.fresh_home()
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from atlas_toolkit.core.history import History
        from atlas_toolkit.core.registry import registry
        from atlas_toolkit.core.settings import AppSettings
        from atlas_toolkit.ui.bridge import JobBridge
        from atlas_toolkit.ui.context import AppContext
        from atlas_toolkit.ui.shell import MainWindow
        from atlas_toolkit.ui.theme import load_fonts
        load_fonts()
        cls.history = History(cls.tmp / "history.jsonl")
        cls.ctx = AppContext(registry(), AppSettings(cls.tmp / "cfg"), JobBridge(), cls.history)
        cls.win = MainWindow(cls.ctx)

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        cls.ctx.bridge.shutdown()
        cls.leave_home()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def wait(self, cond, timeout=180.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            self.app.processEvents()
            if cond():
                return
            time.sleep(0.02)
        self.fail("timed out")

    def folder(self, name: str) -> Path:
        d = self.tmp / name
        d.mkdir(exist_ok=True)
        return d

    def test_svg_tool_analyses_runs_and_records_history(self):
        from atlas_toolkit.core import samples
        src = samples.svg(self.folder("svg"))
        page = self.win.open_tool("svg-overlap-cleaner", [str(src)])
        self.wait(lambda: page.runnable() and page.idle())
        row = page.rows[str(src.resolve())]
        self.assertEqual({v["title"] for v in row.result.preview["views"]}, {"Before", "After", "Cut ink"})
        page.run_all()
        self.wait(lambda: not page.busy() and page.idle())
        out = src.with_name("sample--nooverlap.svg")
        self.assertTrue(out.exists())
        self.assertIn(b"<desc>svg-overlap-cleaner</desc>", out.read_bytes()[:600])
        self.assertTrue(any(r.tool == "svg-overlap-cleaner" and r.status == "done" for r in self.history.read()))

    def test_mesh_tool_runs_in_the_worker_process(self):
        from atlas_toolkit.core import samples
        src = samples.mesh(self.folder("mesh"))
        page = self.win.open_tool("voxelizer", [str(src)])
        self.wait(lambda: page.runnable() and page.idle())
        page.form.set_values({"across": 8})
        page._apply_settings()
        page.run_all()
        self.wait(lambda: not page.busy() and page.idle())
        out = src.with_name("sample_vox.stl")
        self.assertTrue(out.exists())
        self.assertIn(b"atlas-toolkit:voxelizer", out.read_bytes()[:80])

    def test_bend_spine_edits_preview_and_worker_export(self):
        from atlas_toolkit.tools.mesh_bend.engine import make_sample
        src = make_sample(self.folder("bend-spine"))
        page = self.win.open_tool("mesh-bend", [str(src)])
        self.wait(lambda: page.runnable() and page.idle())
        self.assertIsNotNone(page.viewer)
        page.viewer.commit({"angle": 55., "direction": 20., "segments": 8})
        page._apply_settings()
        page.preview_selected()
        self.wait(lambda: page.idle() and page.draft_job is None)
        row = page.rows[str(src.resolve())]
        self.assertIsNotNone(row.result)
        self.assertIn("Output", [v["title"] for v in row.result.preview["views"]])
        out = src.with_name("bend-sample_bend.stl")
        self.assertFalse(out.exists())
        page.run_all()
        self.wait(lambda: not page.busy() and page.idle())
        self.assertTrue(out.exists())
        self.assertIn(b"atlas-toolkit:mesh-bend", out.read_bytes()[:80])
        self.assertEqual(page.values()["angle"], 55.)

    def test_hatch_preview_writes_nothing(self):
        from atlas_toolkit.core import samples
        src = samples.mesh(self.folder("hatch"))
        page = self.win.open_tool("stl-hatch-maker", [str(src)])
        self.wait(lambda: page.runnable() and page.idle())
        page.form.set_values({"spacing_dark": 2.0, "spacing_light": 6.0})
        page.preview_selected()
        self.wait(lambda: page.idle() and page.draft_job is None)
        row = page.rows[str(src.resolve())]
        self.assertEqual(row.result_mode, "draft")
        self.assertEqual(page.stage.current_title(), "Drawing")
        self.assertFalse(list(self.folder("hatch").glob("*_hatch.svg")))   # a preview writes nothing

    def test_image_mesh_preparation_placement_cancel_and_export(self):
        import numpy as np
        from PySide6.QtWidgets import QPushButton
        from atlas_toolkit.tools.image_to_mesh import TOOL
        from atlas_toolkit.tools.image_to_mesh.engine import make_sample
        from atlas_toolkit.geom.surface import load_surface
        src = make_sample(self.folder('image-placement'))
        page = self.win.open_tool(TOOL.id, [str(src)], values=dict(TOOL.defaults(), pitch=5.))
        page.queue.setCurrentItem(page.rows[str(src.resolve())].item)
        self.wait(lambda: page.viewer.data is not None and page.runnable() and page.idle()
                  and not page.viewer.timer.isActive() and page._current_row().result is not None)
        view, row = page.viewer, page._current_row()
        labels = [b.text() for b in view.findChildren(QPushButton)]
        self.assertIn('Reset rotation', labels)
        self.assertNotIn('Reset north', labels)
        self.assertEqual(view.heading_title, 'Image top')
        self.assertEqual(page.stage.current_title(), 'Placement')
        before = row.result.data['source']['source_range']
        view.change(dict(brightness=.1, gamma=1.2, quarter_turns='1', mirror_x=True))
        self.wait(lambda: page.idle() and not view.timer.isActive() and not page.debounce.isActive()
                  and row.result is not None and row.result.data['values']['gamma'] == 1.2)
        self.assertEqual(page.stage.current_title(), 'Placement')
        self.assertNotEqual(row.result.data['source']['source_range'], before)
        self.assertEqual(row.result.data['source']['preparation']['quarter_turns'], '1')
        snapshot = page.values()
        view.expand()
        view.commit(25.)
        view.dialog.reject()
        self.app.processEvents()
        self.assertEqual(page.values(), snapshot)
        view.fit_terrain()
        # Leave room for a locked turn; fitting to the maximum then rotating
        # would correctly report a placement conflict instead of generating a draft.
        view.change({'terrain_width': page.values()['terrain_width']*.6})
        scale = view.data['scale']
        view.commit(20.)
        self.assertAlmostEqual(view.data['scale'], scale)
        view.expand()
        view.commit(10.)
        view.dialog.accept()
        self.app.processEvents()
        self.assertEqual(page.values()['yaw'], 10.)
        self.wait(lambda: page.idle() and not view.timer.isActive() and not page.debounce.isActive())
        self.assertFalse(view.invalid, view.message)
        page.preview_selected()
        self.wait(lambda: page.idle() and page.draft_job is None)
        titles = {v['title'] for v in page.stage.views}
        self.assertTrue({'Original', 'Prepared heightmap', 'Placement', 'Relief mesh'} <= titles, repr(titles))
        self.assertEqual(page.stage.current_title(), 'Relief mesh')
        for theme in ('light', 'dark'):
            self.ctx.set_theme(theme)
            self.app.processEvents()
        page.run_all()
        self.wait(lambda: not page.busy() and page.idle())
        self.assertFalse(row.error, row.error)
        saved = load_surface(row.result.outputs[0]).as_trimesh()
        self.assertTrue(saved.is_volume)
        np.testing.assert_allclose(saved.extents, row.result.data['dimensions_mm'], atol=2e-5)

    def test_dem_round_handle_still_turns_on_a_small_canvas(self):
        """On a small canvas the plate's top square sits almost on the round handle: the nearest one takes the click."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from atlas_toolkit.tools.raster_dem_to_mesh import TOOL
        from atlas_toolkit.tools.raster_dem_to_mesh.engine import make_sample
        self.win.show()
        self.addCleanup(self.win.hide)
        src = make_sample(self.folder('dem-small-canvas'))
        page = self.win.open_tool(TOOL.id, [str(src)], values=dict(TOOL.defaults(), pitch=5., plate_fit='full'))
        page.queue.setCurrentItem(page.rows[str(src.resolve())].item)
        view = page.viewer
        self.wait(lambda: view.data is not None and page.runnable() and page.idle()
                  and not view.timer.isActive() and page._current_row().result is not None)
        canvas = view.canvas
        canvas.resize(300, 340)
        canvas.grab()  # paint the handles at this size
        top = next(point for x, y, point in canvas.plate_handles if (x, y) == (0, 1))
        self.assertLess((canvas.handle-top).manhattanLength(), 14, "the two handles should overlap at this size")
        QTest.mousePress(canvas, Qt.LeftButton, pos=canvas.handle.toPoint())
        self.addCleanup(canvas.releaseMouse)
        self.addCleanup(setattr, canvas, "dragging", False)
        self.assertTrue(canvas.dragging)
        self.assertIsNone(canvas.resize_handle, "the click on the round handle started a plate resize")
        QTest.mouseRelease(canvas, Qt.LeftButton, pos=canvas.handle.toPoint())
        self.assertFalse(canvas.dragging)
        QTest.mousePress(canvas, Qt.LeftButton, pos=top.toPoint())    # and the square is still reachable
        self.assertEqual(canvas.resize_handle, (0, 1))
        QTest.mouseRelease(canvas, Qt.LeftButton, pos=top.toPoint())
        self.wait(lambda: page.idle() and not view.timer.isActive() and not page.debounce.isActive())

    def test_dem_handle_turns_exported_terrain_and_keeps_placement_active(self):
        import math
        import numpy as np
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from atlas_toolkit.tools.raster_dem_to_mesh import TOOL
        from atlas_toolkit.tools.raster_dem_to_mesh.engine import make_sample
        from atlas_toolkit.geom.surface import load_surface
        self.win.resize(1600, 1000)
        self.win.show()
        self.addCleanup(self.win.hide)
        src = make_sample(self.folder('dem-placement'))
        page = self.win.open_tool(TOOL.id, [str(src)], values=dict(TOOL.defaults(), pitch=5., plate_fit='full'))
        page.queue.setCurrentItem(page.rows[str(src.resolve())].item)
        self.wait(lambda: page.viewer.data is not None and page.runnable() and page.idle()
                  and not page.viewer.timer.isActive() and page._current_row().result is not None)
        view, row = page.viewer, page._current_row()
        self.assertEqual(page.stage.current_title(), 'Placement')
        self.assertIsNotNone(view.elevations)
        view.resize(700, 650)
        self.app.processEvents()
        view.canvas.grab()  # paint the handle before clicking it
        canvas = view.canvas
        radius = math.hypot(view.canvas.handle.x()-canvas.width()/2, view.canvas.handle.y()-canvas.height()/2)
        end = QPoint(round(canvas.width()/2+radius*.5), round(canvas.height()/2-radius*math.sqrt(3)/2))
        QTest.mousePress(canvas, Qt.LeftButton, pos=canvas.handle.toPoint())
        self.addCleanup(canvas.releaseMouse)
        self.addCleanup(setattr, canvas, "dragging", False)     # a failure below must not block the next test's previews
        self.assertTrue(canvas.dragging)
        # Sent directly: on a real platform QTest.mouseMove moves the actual cursor, and the event may never arrive.
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        self.app.sendEvent(canvas, QMouseEvent(QEvent.MouseMove, QPointF(end), QPointF(end), Qt.NoButton, Qt.LeftButton,
                                               Qt.NoModifier))
        yaw = page.values()['yaw']
        self.assertAlmostEqual(yaw, 30., delta=.6)
        view._preview()  # even an expiring timer cannot launch a job mid-gesture
        self.assertIsNone(page.draft_job)
        QTest.mouseRelease(canvas, Qt.LeftButton, pos=end)
        self.wait(lambda: page.idle() and not view.timer.isActive() and not page.debounce.isActive()
                  and row.result is not None and row.result.data['yaw'] == yaw)
        self.assertEqual(page.stage.current_title(), 'Placement')
        self.assertEqual(row.result.data['values']['yaw'], yaw)
        view.angle.setValue(-40.)
        self.assertEqual(page.values()['yaw'], -40.)
        self.wait(lambda: page.idle() and not view.timer.isActive() and not page.debounce.isActive()
                  and row.result is not None and row.result.data['yaw'] == -40.)
        self.assertEqual(page.stage.current_title(), 'Placement')
        # An explicit Preview still opens the 3D model, with the same exported angle.
        page.preview_selected()
        explicit_job = page.draft_job
        view._preview()  # a pending automatic tick must not replace an explicit request
        self.assertEqual(page.draft_job, explicit_job)
        self.wait(lambda: page.idle() and page.draft_job is None)
        self.assertEqual(page.stage.current_title(), 'Terrain mesh')
        page.run_all()
        self.wait(lambda: not page.busy() and page.idle())
        self.assertFalse(row.error)
        self.assertEqual(row.result.data['yaw'], -40.)
        saved = load_surface(row.result.outputs[0]).as_trimesh()
        self.assertTrue(saved.is_volume)
        np.testing.assert_allclose(saved.extents[:2], (190., 277.), atol=2e-5)

    def test_replaced_draft_cannot_restore_stale_geometry_or_clear_new_job(self):
        from unittest.mock import patch
        from atlas_toolkit.core import samples, Result
        src = samples.mesh(self.folder('draft-replacement'))
        page = self.win.open_tool('voxelizer', [str(src)])
        page.queue.setCurrentItem(page.rows[str(src.resolve())].item)
        self.wait(lambda: page.runnable() and page.idle())
        row = page._current_row()
        with patch.object(self.ctx.bridge, 'submit', side_effect=[100001, 100002]), patch.object(self.ctx.bridge, 'cancel') as cancel:
            page.preview_selected()
            old_generation = page.gen
            page._settings_changed()
            self.assertIsNone(page.draft_job)
            page.preview_selected(keep_view=True)
            self.assertFalse(page.debounce.isActive())
            self.assertEqual(page.gen, old_generation+1)
            page._on_event(row, 'draft', old_generation, 100001, 'done', (Result(report='stale'),))
            self.assertEqual(page.draft_job, 100002)
            self.assertIsNone(row.result)
            self.assertNotIn(100001, row.jobs)
            page._on_event(row, 'draft', page.gen, 100002, 'done', (Result(report='current'),))
            self.assertIsNone(page.draft_job)
            self.assertEqual(row.result.report, 'current')
            cancel.assert_called_once_with(100001)

    def test_dem_custom_plate_lock_aspect_and_expanded_cancel(self):
        from PySide6.QtWidgets import QDialog
        from atlas_toolkit.tools.raster_dem_to_mesh import TOOL
        from atlas_toolkit.tools.raster_dem_to_mesh.engine import make_sample
        src=make_sample(self.folder('dem-resize'))
        page=self.win.open_tool(TOOL.id,[str(src)],values=dict(TOOL.defaults(),pitch=5.,lock_scale=True,terrain_width=120.,plate_fit='full'))
        page.queue.setCurrentItem(page.rows[str(src.resolve())].item)
        view=page.viewer
        self.wait(lambda: view.data is not None and page.idle() and not view.timer.isActive() and page._current_row().result is not None)
        scale=view.data['scale']
        view.resize_plate(150.,180.)
        self.assertEqual(page.values()['plate_fit'],'custom')
        self.assertTrue(page.values()['lock_scale'])
        self.assertAlmostEqual(view.data['scale'],scale,places=8)
        self.assertAlmostEqual(view.data['plate_width'],150.,places=2)
        self.assertAlmostEqual(view.data['plate_height'],180.,places=2)
        view.change(dict(plate_width=100.,plate_height=180.))
        view.commit(55.)
        self.assertTrue(view.invalid)
        view.resize_plate(190.,240.)
        self.assertFalse(view.invalid,view.message)
        self.assertAlmostEqual(view.data['scale'],scale,places=8)
        view.commit(0.)
        view.resize_plate(150.,180.)
        view.commit(35.)
        self.assertAlmostEqual(view.data['scale'],scale,places=8)
        self.assertFalse(view.invalid,view.message)
        view.aspect.setChecked(True)
        view.resize_plate(150.,200.)
        self.assertAlmostEqual(view.data['plate_width']/view.data['plate_height'],150/180,places=4)
        snapshot=page.values()
        view.expand()
        self.assertIsNotNone(view.dialog)
        view.commit(15.)
        view.dialog.reject()
        self.app.processEvents()
        self.assertEqual(page.values(),snapshot)
        view.expand()
        view.commit(20.)
        view.dialog.accept()
        self.app.processEvents()
        self.assertEqual(page.values()['yaw'],20.)
        self.wait(lambda: page.idle() and not view.timer.isActive() and not page.debounce.isActive())

    def test_heightfield_preflight_and_preset_input_state(self):
        self.use_test_presets()
        for tool_id in ('raster-dem-to-mesh','image-to-mesh'):
            tool=self.ctx.registry.get(tool_id)
            src=tool.resolve(tool.sample)(self.folder('preflight-'+tool_id))
            page=self.win.open_tool(tool_id,[str(src)],values=dict(tool.defaults(),pitch=5.,lock_scale=True,terrain_width=120.,plate_fit='full'))
            page.queue.setCurrentItem(page.rows[str(src.resolve())].item)
            view=page.viewer
            self.wait(lambda: view.authoritative and page.idle() and not view.timer.isActive() and page._current_row().result is not None)
            extra=dict(contact_trim=.001,contact_policy='automatic',exclude_background=True,background=.1)
            if tool_id=='image-to-mesh':extra['gamma']=1.3
            page.form.set_values(extra)
            page.apply_preset('Test contours' if tool_id=='raster-dem-to-mesh' else 'Test soft contours')
            for key,value in extra.items():self.assertEqual(page.values()[key],value)
            page.form.set_values(dict(exclude_background=False,plate_fit='custom',plate_width=60.,plate_height=70.,lock_scale=True,terrain_width=180.))
            page._apply_settings()
            self.wait(lambda:view.authoritative)
            self.assertTrue(view.invalid)
            self.assertFalse(page.btn_run.isEnabled())
            self.assertFalse(page.btn_preview.isEnabled())
            from unittest.mock import patch
            with patch.object(page,'_submit',side_effect=AssertionError('invalid placement must not enter a worker')):
                page.preview_selected()
                page.run_all()
            page.form.set_values(dict(plate_fit='full',lock_scale=False,geometry='surface',pitch=5.))
            page._apply_settings()
            self.wait(lambda:page.idle() and not view.timer.isActive() and not page.debounce.isActive())

    def test_theme_and_art_switch(self):
        self.ctx.set_theme("light")
        self.ctx.set_art("blueprint")
        self.ctx.set_theme("dark")
        self.ctx.set_art("paper")
        self.win.show_activity()
        self.win.show_home()

    def test_disintegrate_preview_run_history_and_themes(self):
        from atlas_toolkit.tools.mesh_disintegrate import TOOL
        src = TOOL.resolve(TOOL.sample)(self.folder("disintegrate"))
        page = self.win.open_tool("mesh-disintegrate", [str(src)])
        self.wait(lambda: page.runnable() and page.idle())
        page.form.set_values({"steps": 2, "separate": True, "direction": "radial"})
        page.preview_selected()
        self.wait(lambda: page.idle() and page.draft_job is None)
        row = page.rows[str(src.resolve())]
        self.assertFalse(row.error)
        self.assertEqual(row.result_mode, "draft")
        self.assertEqual(page.stage.current_title(), "Progression")
        self.assertEqual({v["title"] for v in row.result.preview["views"]},
                         {"Stage 1", "Stage 2", "Progression"})
        self.assertEqual(list(self.folder("disintegrate").iterdir()), [src])
        self.ctx.set_theme("light")
        self.ctx.set_art("blueprint")
        self.ctx.set_theme("dark")
        self.ctx.set_art("paper")
        page.run_all()
        self.wait(lambda: not page.busy() and page.idle())
        self.assertFalse(row.error)
        self.assertEqual(len(row.result.outputs), 4)
        self.assertTrue(all(Path(p).is_file() for p in row.result.outputs))
        record = next(r for r in self.history.read() if r.tool == "mesh-disintegrate")
        self.assertEqual(record.status, "done")
        self.assertEqual(record.values["direction"], "radial")
        self.assertEqual(record.outputs, row.result.outputs)

    def test_surface_texture_preview_run_history_and_about(self):
        from atlas_toolkit.tools.mesh_texture import TOOL, engine
        src = engine.make_sample(self.folder("surface-texture"))
        page = self.win.open_tool(TOOL.id, [str(src)])
        self.wait(lambda: page.runnable() and page.idle())
        self.assertFalse(page.extra.swatch.pixmap().isNull())
        self.assertIn('TextureCan', page.extra.credit.text())
        page.form.set_values(TOOL.test_values)
        page.preview_selected()
        self.wait(lambda: page.idle() and page.draft_job is None)
        row = page.rows[str(src.resolve())]
        self.assertFalse(row.error, row.error)
        self.assertEqual(row.result.outputs, [])
        self.assertEqual(list(src.parent.iterdir()), [src])
        self.assertEqual(row.result.preview['views'][1]['status'], 'Final geometry')
        page.run_all()
        self.wait(lambda: not page.busy() and page.idle())
        self.assertFalse(row.error, row.error)
        self.assertEqual(len(row.result.outputs), 1)
        record = next(r for r in self.history.read() if r.tool == TOOL.id)
        self.assertEqual(record.status, "done")
        d = self.win.show_help("tools")
        d.tool_filter.setText("Surface Texture")
        self.assertEqual(sum(not r.isHidden() for r, _ in d.tool_rows), 1)
        d.close()

    def test_dilate_preview_run_history_and_about(self):
        from atlas_toolkit.tools.mesh_dilate import TOOL, engine
        src = engine.make_sample(self.folder("dilate"))
        page = self.win.open_tool(TOOL.id, [str(src)])
        self.wait(lambda: page.runnable() and page.idle())
        page.form.set_values(dict(TOOL.test_values, inflation=120, direction="radial"))
        page.preview_selected()
        self.wait(lambda: page.idle() and page.draft_job is None)
        row = page.rows[str(src.resolve())]
        self.assertFalse(row.error)
        self.assertEqual(row.result_mode, "draft")
        self.assertEqual(row.result.outputs, [])
        self.assertEqual(list(src.parent.iterdir()), [src])
        page.run_all()
        self.wait(lambda: not page.busy() and page.idle())
        self.assertFalse(row.error)
        self.assertEqual(len(row.result.outputs), 1)
        record = next(r for r in self.history.read() if r.tool == TOOL.id)
        self.assertEqual(record.status, "done")
        self.assertEqual(record.values["inflation"], 120)
        d = self.win.show_help("tools")
        d.tool_filter.setText("dilate")
        self.assertEqual(sum(not r.isHidden() for r, _ in d.tool_rows), 1)
        d.close()

    def test_long_segment_labels_keep_names_and_actions_at_narrow_width(self):
        from atlas_toolkit.ui.widgets.common import Segmented
        control = Segmented([("sketch", "Sketch progression"), ("ink", "Pen & ink")])
        self.addCleanup(control.close)
        control.resize(160, 40)
        control.show()
        self.app.processEvents()
        self.assertFalse(control.grab().isNull())
        self.assertEqual(control.buttons["sketch"].text(), "Sketch progression")
        self.assertEqual(control.buttons["sketch"].toolTip(), "Sketch progression")
        selected = []
        control.picked.connect(selected.append)
        control.buttons["sketch"].click()
        self.assertEqual(selected, ["sketch"])
        control.buttons["ink"].click()
        self.assertEqual(control.value(), "ink")

    def test_tool_list_folds_shows_icons_only_and_hides(self):
        side = self.win.sidebar
        self.win.open_tool("svg-overlap-cleaner")
        cat, head, buttons, _line = next(g for g in side.groups if "svg-overlap-cleaner" in
                                         [k for k, b in side.nav.items() if b in g[2]])
        head.click()                                             # fold: the open tool's header turns active
        self.assertTrue(all(b.isHidden() for b in buttons))
        self.assertEqual(head.property("active"), "true")
        self.assertIn(cat, self.ctx.settings.get("sidebar_collapsed"))
        head.click()
        self.assertFalse(any(b.isHidden() for b in buttons))
        side.fold_all()
        self.assertTrue(all(b.isHidden() for _c, _h, bs, _l in side.groups for b in bs))
        side.fold_all()                                          # all folded: unfold them all
        self.assertEqual(self.ctx.settings.get("sidebar_collapsed"), [])
        self.win.toggle_rail()                                   # icons only: every tool, no names, hairlines
        self.assertTrue(side.rail)
        self.assertEqual(side.width(), side.minimumWidth())
        self.assertTrue(all(b.text() == "" and not b.isHidden() for b in buttons))
        self.assertTrue(head.isHidden())
        self.win.toggle_sidebar()
        self.assertTrue(side.isHidden())
        self.assertFalse(self.ctx.settings.get("sidebar_on"))
        self.win.toggle_rail()                                   # back to names, and shown again
        self.assertFalse(side.isHidden())
        self.assertFalse(side.rail)
        self.assertTrue(buttons[0].text().strip())
        self.assertTrue(self.ctx.settings.get("sidebar_on"))

    def assert_tabs_fit_smallest(self, d):
        """Every tab of a tabbed window fits it at its minimum size: nothing wider than the window, cut off."""
        was = d.tabs.currentIndex()
        d.resize(d.minimumSize())
        for i, key in enumerate(d.TABS):
            d.tabs.setCurrentIndex(i)
            self.app.processEvents()
            page = d.tabs.currentWidget()
            if hasattr(page, "viewport"):
                self.assertLessEqual(page.widget().width(), page.viewport().width(), key)
        d.tabs.setCurrentIndex(was)

    def test_about_window_tabs_changelog_and_diagnostics(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from atlas_toolkit.core.paths import config_dir
        from atlas_toolkit.ui.about import AboutDialog, changelog_text, releases
        from atlas_toolkit.version import __version__
        d = self.win.show_help("changelog")
        self.addCleanup(lambda: self.win.about and self.win.about.close())
        self.assertIs(self.win.show_help("tools"), d)            # one window at a time
        self.assertEqual(d.tabs.count(), len(AboutDialog.TABS))
        flags = d.windowFlags()
        self.assertTrue(flags & Qt.WindowMinimizeButtonHint and flags & Qt.WindowMaximizeButtonHint)
        self.assertTrue(d.isSizeGripEnabled())
        self.assert_tabs_fit_smallest(d)
        self.assertEqual(d.releases.item(0).data(Qt.UserRole)[0], __version__)   # the changelog is current
        # the notes show the newest entry (its own first heading, so the test holds for every later release)
        newest = next(ln for ln in releases(changelog_text())[0][2].splitlines() if ln.strip())
        self.assertIn(newest.replace("**", "").split(" (")[0].strip(), d.notes.toPlainText())
        self.assertEqual(len(d.tool_rows), len(self.ctx.registry.tools))
        d.tool_filter.setText("overlap")
        self.assertEqual(sum(not r.isHidden() for r, _h in d.tool_rows), 1)
        d.copy_diagnostics()
        text = QGuiApplication.clipboard().text()
        self.assertIn(__version__, text)
        self.assertIn(str(config_dir()), text)                    # the real per-user folder, never a fixed path
        self.assertEqual(releases("## 2.0.0 (2027-01-01)\nnew\n## 1.0.0\nold")[1], ("1.0.0", "", "old"))
        self.assertNotIn("setup", AboutDialog.TABS)                       # setting up is the Setup window's
        import shlex
        from atlas_toolkit.core.cli import build_parser
        from atlas_toolkit.ui.about import cli_examples
        for line in cli_examples(self.ctx.registry).splitlines():          # every example is a valid command
            args = shlex.split(line.split("atlas_toolkit", 1)[-1].split(".exe\"", 1)[-1], posix=False)
            build_parser().parse_args([a.strip('"') for a in args])
        self.assertIn("cli", AboutDialog.TABS)
        from PySide6.QtWidgets import QLabel
        words =" ".join(w.text() for w in d.findChildren(QLabel)).lower()
        self.assertNotIn("gallery", words)                                # a standalone app: no studio-only refs
        self.assertNotIn("projects/", words)
        self.ctx.set_theme("light")                              # rebuilt for the theme, on the same tab
        self.assertIsNot(self.win.about, d)
        self.assertEqual(self.win.about.tabs.currentIndex(), AboutDialog.TABS.index("tools"))
        self.ctx.set_theme("dark")
        self.assert_nothing_of_this_computer(self.win.about)

    def test_setup_window_options_components_and_storage(self):
        from atlas_toolkit.core import prefs
        from atlas_toolkit.core.runtimes import RUNTIMES
        from atlas_toolkit.ui.setup import SetupDialog
        saved = dict(self.ctx.settings.data)
        self.addCleanup(lambda: (self.ctx.settings.data.clear(), self.ctx.settings.data.update(saved),
                                 self.ctx.set_theme(saved.get("theme", "dark")),
                                 self.ctx.set_art(saved.get("art", "paper"))))
        d = self.win.show_setup("components")
        self.addCleanup(lambda: self.win.setup and self.win.setup.close())
        self.assertIs(self.win.show_setup("outputs"), d)                    # one window at a time
        self.assertEqual(d.tabs.count(), len(SetupDialog.TABS))
        self.assertIs(self.win.show_help("setup"), d)                       # the former About › Setup
        self.assertEqual(d.tabs.currentIndex(), SetupDialog.TABS.index("components"))
        self.assertEqual(set(d.rt_status), set(RUNTIMES))                   # a card per runtime
        toggle, body = d.rt_details["blender"]                              # its details fold, remembered
        was = "blender" in d.rt_open
        toggle.click()
        self.assertEqual(body.isVisibleTo(d), not was)
        self.assertEqual("blender" in self.ctx.settings.get("setup_open_components"), not was)
        toggle.click()
        self.assertEqual(body.isVisibleTo(d), was)
        self.assert_tabs_fit_smallest(d)
        fake = self.tmp / "blender-here.exe"
        fake.write_text("")
        before = self.ctx.settings.get("blender_exe")
        d.locate("blender", str(fake))                                      # remembered for the runtime's finder
        self.assertEqual(self.ctx.settings.get("blender_exe"), str(fake))
        self.ctx.settings.set("blender_exe", before)
        self.ctx.settings.save()
        d._refresh_runtime("blender")
        self.assert_nothing_of_this_computer(d)

        # options apply as they change: existing outputs, workers, preview delay
        self.assertEqual(self.ctx.pref("on_existing"), "overwrite")
        next(b for b in d.existing.buttons() if b._value == "suffix").setChecked(True)
        self.assertEqual(self.ctx.settings.get("on_existing"), "suffix")
        if prefs.max_workers() > 1:
            d.workers_spin.setValue(2)
            self.assertEqual(len([w for w in self.ctx.bridge.runner.process.workers if not w.retire]), 2)
            d.workers_spin.setValue(1)
            self.assertEqual(len([w for w in self.ctx.bridge.runner.process.workers if not w.retire]), 1)
        page = self.win.open_tool("svg-overlap-cleaner")
        d.delay_spin.setValue(800)
        self.assertEqual(page.debounce.interval(), 800)
        # the theme at start shows at once (the window is rebuilt for it, on the same tab)
        d.show_tab("general")
        d.theme_combo.setCurrentIndex(d.theme_combo.findData("light"))
        self.assertEqual(self.ctx.theme.name, "light")
        d = self.win.setup
        self.assertEqual(d.tabs.currentIndex(), SetupDialog.TABS.index("general"))
        self.assertEqual(self.ctx.start_theme(), "light")

        # keep both: a run over an existing output writes a stamped file next to it
        from atlas_toolkit.core import samples
        src = samples.svg(self.folder("svg-suffix"))
        page = self.win.open_tool("svg-overlap-cleaner", [str(src)])
        self.wait(lambda: page.runnable() and page.idle())
        out = src.with_name("sample--nooverlap.svg")
        out.write_text("<svg/>")
        page.run_all()
        self.wait(lambda: not page.busy() and page.idle())
        self.assertEqual(out.read_text(), "<svg/>")                         # the old file stays
        self.assertEqual(len(list(out.parent.glob("sample--nooverlap_*.svg"))), 1)

        # storage: export, reset and import the options; clear the history
        exported = self.tmp / "exported.json"
        self.assertTrue(d.export_settings(str(exported)))
        self.assertTrue(d.reset_options(ask=False))
        self.assertEqual(self.ctx.pref("on_existing"), "overwrite")
        self.wait(lambda: self.win.setup is not None)
        self.assertTrue(self.win.setup.import_settings(str(exported)))
        self.assertEqual(self.ctx.pref("on_existing"), "suffix")
        self.wait(lambda: self.win.setup is not None)
        self.assertTrue(self.win.setup.clear_history(ask=False))
        self.assertEqual(self.ctx.history.read(), [])
        self.ctx.history.path.parent.mkdir(parents=True, exist_ok=True)

    def test_component_installer_window_and_card(self):
        """Setup › Components › Install…: the ready page, an install against the stand-in uv, the result, the card
        after it. Nothing is downloaded (tests/test_depth_runtime.py covers the installer itself)."""
        import os
        import sys
        from unittest import mock
        from PySide6.QtWidgets import QLabel, QPushButton
        from atlas_toolkit.core import gpu
        from atlas_toolkit.geom import depth_runtime as dr
        target = self.folder("installer") / "ml-runtime"
        fake_uv = Path(__file__).parent / "fixtures" / "fake_uv.py"
        os_key = "darwin" if sys.platform == "darwin" else "linux"
        from atlas_toolkit.core.paths import venv_python
        from atlas_toolkit.geom import depth
        for p in (mock.patch.object(dr, "UV_COMMAND", [sys.executable, str(fake_uv)]),
                  mock.patch.object(depth, "runtime_candidates", return_value=[venv_python(target)]),
                  mock.patch.object(dr, "ml_runtime_dir", return_value=target),
                  mock.patch.object(dr, "os_key", return_value=os_key),
                  mock.patch.object(dr.gpus, "detect", return_value=gpu.GpuReport("none", others=["Intel graphics"])),
                  mock.patch.object(dr, "_internet_check", return_value=dr.Check("ok", "Internet", "Reached them.")),
                  mock.patch.dict(os.environ, {"FAKE_UV_FAIL": "", "FAKE_UV_SLOW": "", "FAKE_TORCH_CUDA": ""})):
            p.start()
            self.addCleanup(p.stop)
        d = self.win.show_setup("components")
        self.addCleanup(lambda: self.win.setup and self.win.setup.close())
        install, check, remove = d.rt_installer["depth"]
        self.assertTrue(install.isVisible() or not d.isVisible())
        d.open_installer("depth")
        from atlas_toolkit.ui import setup as setup_mod
        dlg = setup_mod._INSTALLERS["depth"]
        self.addCleanup(lambda: dlg.running() or dlg.close())
        self.wait(lambda: dlg.plan is not None, 60)
        texts = " ".join(w.text() for w in dlg.findChildren(QLabel))
        for words in ("No NVIDIA graphics card", "Disk space", "Reached them", "minutes", "ml-runtime", "CPU only"):
            self.assertIn(words, texts)
        self.assertNotIn(str(Path.home()), texts, "no machine paths on screen")
        go = next(b for b in dlg.findChildren(QPushButton) if b.text() == "Install")
        self.assertTrue(go.isEnabled())
        self.assertFalse(dlg.grab().isNull())
        go.click()
        self.wait(lambda: dlg.result is not None, 120)
        self.assertTrue(dlg.result.ok, dlg.result.lines())
        texts = " ".join(w.text() for w in dlg.findChildren(QLabel))
        self.assertIn("Installed", texts)
        self.assertTrue(any(b.text() == "Open folder" for b in dlg.findChildren(QPushButton)))
        self.app.processEvents()
        d._refresh_runtime("depth")
        status = d.rt_status["depth"][1].text()
        self.assertIn("Installed by Atlas", status)
        self.assertEqual(install.text(), "Change build…")
        dlg.close()
        freed, gone = dr.remove()
        self.assertFalse(target.exists())

    def test_an_installer_window_outlives_the_setup_window(self):
        """The installer window is the main window's, so closing Setup must not stop it: its progress and its end then
        reach a Setup window that is gone (and a new one, once, when Setup opens again)."""
        import sys
        from unittest import mock
        from PySide6.QtCore import QCoreApplication, QEvent
        from atlas_toolkit.geom import depth_runtime as dr
        from atlas_toolkit.ui import setup as setup_mod
        with mock.patch.object(dr, "plan", side_effect=RuntimeError("no plan in this test")):
            d = self.win.show_setup("components")
            d.open_installer("depth")
            dlg = setup_mod._INSTALLERS["depth"]
            self.addCleanup(lambda: QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete))
            self.addCleanup(lambda: dlg.running() or dlg.close())
            errors = []
            with mock.patch.object(sys, "excepthook", lambda *a: errors.append(a[1])):
                d.close()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.app.processEvents()
                dlg.progress_text.emit("Installing … 5%")
                dlg.changed.emit()
                self.app.processEvents()
                d2 = self.win.show_setup("components")
                d2.open_installer("depth")
                d2.open_installer("depth")
                calls = []
                with mock.patch.object(d2, "_refresh_runtime", side_effect=lambda k: calls.append(k)):
                    dlg.changed.emit()
                self.app.processEvents()
                d2.close()
            self.assertEqual(errors, [])
            self.assertEqual(calls, ["depth"], "one refresh per signal, however often the window was opened")

    def assert_nothing_of_this_computer(self, d):
        """The About window is the same on every computer: no paths, user name or drives of this one."""
        import os
        import re
        import sys
        from PySide6.QtWidgets import QWidget
        from atlas_toolkit.core.paths import app_home
        from atlas_toolkit.ui.about import changelog_text
        shown = []
        for i in range(d.tabs.count()):
            d.tabs.setCurrentIndex(i)
            self.app.processEvents()
            for wdg in d.findChildren(QWidget):
                for attr in ("text", "toPlainText", "toolTip", "placeholderText"):
                    fn = getattr(wdg, attr, None)
                    if callable(fn):
                        try:
                            shown.append(str(fn()))
                        except TypeError:
                            pass
        shown = "\n".join(shown + [changelog_text()]).lower()
        for private in (str(Path.home()), os.environ.get("USERNAME", ""), str(app_home()), sys.executable,
                        str(self.tmp)):
            if private:
                self.assertNotIn(private.lower(), shown)
        self.assertIsNone(re.search(r"\b[a-z]:[\\/]", shown), "a drive path in the About window")

    def test_step_list_folds_and_shows_icons_only(self):
        from atlas_toolkit.ui.workflow.library import GROUP
        page = self.workflow_page("Library")
        lib = page.library
        head = lib.tree.topLevelItem(1)
        group = head.data(0, GROUP)
        lib._clicked(head)
        head = lib.tree.topLevelItem(1)                          # the list is rebuilt
        self.assertFalse(head.isExpanded())
        self.assertIn(group, self.ctx.settings.get("library_collapsed"))
        lib.search.setText(group.split()[0])                     # a search opens every group
        self.assertTrue(all(lib.tree.topLevelItem(i).isExpanded() for i in range(lib.tree.topLevelItemCount())))
        lib.search.clear()
        self.win.toggle_rail()
        self.assertTrue(lib.rail)
        self.app.processEvents()
        self.assertTrue(lib.search.isHidden())
        self.assertFalse(page.grab().isNull())
        self.win.toggle_rail()
        lib._clicked(lib.tree.topLevelItem(1))
        self.assertEqual(self.ctx.settings.get("library_collapsed"), [])
        self.win.toggle_sidebar()
        self.assertTrue(lib.isHidden())
        self.win.toggle_sidebar()
        self.assertFalse(lib.isHidden())
        self.win.show_home()

    def test_home_list_view_folding_and_workflow_names(self):
        import json
        from atlas_toolkit.ui.home import FLOWS, ToolCard, ToolRow
        home = self.win.home
        self.win.show_home()
        from atlas_toolkit.core.paths import config_dir
        folder = Path(self.ctx.settings.get("workflow_dir") or (config_dir() / "workflows"))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "flow-3f2a9c.atlasflow").write_text(json.dumps({"name": "Shattered hatching", "nodes": []}),
                                                       encoding="utf-8")
        home.refresh_workflows()
        names = [it.name for it in home.sections[FLOWS]["items"]]
        self.assertIn("Shattered hatching", names)                         # the stored name, not the file's
        self.assertNotIn("flow-3f2a9c", names)
        home.set_view("list")
        self.assertTrue(home.cards and all(isinstance(w, ToolRow) for w in home.cards))
        self.assertLess(ToolRow(home.sections[FLOWS]["items"][0], self.ctx.theme).height(),
                        ToolCard(home.sections[FLOWS]["items"][0], self.ctx.theme).height() / 2)
        self.assertEqual(self.ctx.settings.get("home_view"), "list")
        cat = next(k for k in home.sections if k != FLOWS)
        home.toggle(cat)
        self.assertTrue(home.sections[cat]["body"].isHidden())
        self.assertIn(cat, self.ctx.settings.get("home_collapsed"))
        home.fold_all()
        self.assertTrue(all(s["body"].isHidden() for s in home.sections.values()))
        home.fold_all()                                                     # all folded: unfold them all
        self.assertFalse(any(s["body"].isHidden() for s in home.sections.values()))
        home.set_view("cards")
        self.assertTrue(all(isinstance(w, ToolCard) for w in home.cards))

    def test_tools_without_their_program_are_flagged_everywhere(self):
        from unittest import mock
        from atlas_toolkit.core import runtimes
        from atlas_toolkit.ui.home import FLOWS
        from atlas_toolkit.ui.workflow.library import NEEDS
        real = runtimes.find
        needs_blender = [t for t in self.ctx.registry.ordered() if "blender" in t.requires]
        self.assertTrue(needs_blender)
        melt = needs_blender[0]
        optional = next((t for t in self.ctx.registry.ordered() if any(k == "blender" for k, _w in t.optional)), None)
        with mock.patch.object(runtimes, "find", lambda key: None if key == "blender" else real(key)):
            self.ctx.refresh_runtimes()
            self.assertIn("Blender", self.ctx.needs_text(melt))
            b = self.win.sidebar.nav[melt.id]                       # faded, with the warning mark and tooltip
            self.assertEqual(b.property("unavailable"), "true")
            self.assertFalse(b._warn.isHidden())
            self.assertIn("Blender", b.toolTip())
            item = next(it for sec in self.win.home.sections.values() if sec is not self.win.home.sections[FLOWS]
                        for it in sec["items"] if it.id == melt.id)
            self.assertEqual(item.needs_short, "needs Blender")
            page = self.win.open_tool(melt.id)                     # the notice, below the tool's name
            self.assertTrue(any(not n.isHidden() for n in page.notices))
            self.assertFalse(page.btn_run.isEnabled() if hasattr(page, "btn_run") else False)
            if optional is not None:
                opt = self.win.open_tool(optional.id)
                self.assertTrue(any(not n.isHidden() for n in opt.opt_notes))
                self.assertFalse(opt.lacking)                      # optional: the tool still runs
            flows = self.workflow_page("Needs Blender")
            nid = flows.add_entry(f"tool:{melt.id}")
            card = flows._card(flows.wf.nodes[nid])
            self.assertEqual((card.level, card.chip), ("warning", "needs Blender"))
            lib_items = [flows.library.tree.topLevelItem(i).child(j) for i in range(flows.library.tree.topLevelItemCount())
                         for j in range(flows.library.tree.topLevelItem(i).childCount())]
            self.assertTrue(any(it.data(0, NEEDS) for it in lib_items))
        self.ctx.refresh_runtimes()                                 # found again: everything clears
        if real("blender"):
            self.assertEqual(self.win.sidebar.nav[melt.id].property("unavailable"), "false")
            self.assertFalse(flows._card(flows.wf.nodes[nid]).chip == "needs Blender")
        self.win.show_home()

    def use_test_presets(self):
        """Offer the made-up test pack (tests/fixtures/presets/test-pack) for the rest of this test."""
        lib = self.ctx.presets
        folder = Path(__file__).resolve().parent / "fixtures" / "presets" / "test-pack"
        lib.extra_dirs.append(folder)
        lib.reload()
        lib.notify(None)

        def done():
            lib.extra_dirs.remove(folder)
            lib.reload()
            lib.notify(None)
        self.addCleanup(done)
        return lib

    def test_setup_presets_tab(self):
        from atlas_toolkit.core import ed25519, preset_keys
        from atlas_toolkit.core import presets as P
        secret = ed25519.new_secret()
        preset_keys.KEYS["gui-test"] = preset_keys.PublisherKey(ed25519.public_key(secret).hex(), "GUI test")
        self.addCleanup(preset_keys.KEYS.pop, "gui-test", None)
        packed = self.folder("setup-presets") / "test-presets.atlaspack"
        packed.write_bytes(P.pack_bytes(Path(__file__).resolve().parent / "fixtures" / "presets" / "test-pack",
                                        ("gui-test", secret)))
        lib = self.ctx.presets
        d = self.win.show_setup("presets")
        self.assertEqual(d.tabs.currentIndex(), d.TABS.index("presets"))
        panel = d.presets_panel
        self.assertEqual(panel.import_files([str(packed)], ask=False), 1)          # installs the signed pack
        pack = lib.installed("test-presets:complete")
        self.addCleanup(lambda: lib.installed("test-presets:complete") and lib.remove_pack(
            lib.installed("test-presets:complete")))
        rows = [panel.packs_view.topLevelItem(i).text(3) for i in range(panel.packs_view.topLevelItemCount())]
        self.assertIn("Official", rows)
        tool = self.ctx.registry.get("svg-overlap-cleaner")
        gentle = next(p for p in lib.pack_presets(tool) if p.name == "Gentle test")
        panel.select(tool.id, gentle.uid)
        self.assertIn("Gentle test", panel.details.text())
        self.assertFalse(panel.btn_export.isEnabled())                              # pack presets: no export
        self.assertTrue(panel.btn_copy.isEnabled() and panel.btn_hide.isEnabled())
        copy = panel.duplicate_selected()[0]
        self.assertTrue(copy.mine)
        panel.select(tool.id, copy.uid)
        renamed = panel.rename_selected("Renamed copy")
        self.assertEqual(renamed.name, "Renamed copy")
        panel.select(tool.id, renamed.uid)
        out = panel.export_selected(str(self.folder("setup-presets") / "mine.atlaspreset"))
        self.assertEqual([p.name for p in P.read_preset_file(out)], ["Renamed copy"])
        # filters: by tool group, type, source, state and search (tips too); a count, and an empty-result row
        from atlas_toolkit.ui.setup_presets import ROLE

        def listed():
            out, stack = [], [panel.view.topLevelItem(i) for i in range(panel.view.topLevelItemCount())]
            while stack:
                it = stack.pop()
                if (it.data(0, ROLE) or ("",))[0] == "preset":
                    out.append(it.text(0))
                stack += [it.child(i) for i in range(it.childCount())]
            return sorted(out)

        everything = listed()
        self.assertEqual(len(everything), 7)                                    # six from the pack, one of mine
        self.assertFalse(panel.btn_clear.isEnabled())
        # each choice says how many it would list; one that would list none is greyed out (one tier installed)
        self.assertEqual(panel.choice("type", "official"), ("Official (6)", True))
        self.assertEqual(panel.choice("type", "mine"), ("Mine (1)", True))
        self.assertEqual(panel.choice("state", "tiers"), ("In several tiers (0)", False))
        self.assertEqual(panel.choice("state", "hidden"), ("Hidden (0)", False))
        self.assertEqual(panel.choice("state", ""), ("Any state (7)", True))
        panel.set_filter("type", "official")
        self.assertEqual(len(listed()), 6)
        self.assertEqual(panel.count.text(), "Showing 6 of 7")
        panel.set_filter("type", "mine")
        self.assertEqual(listed(), ["Renamed copy"])
        panel.set_filter("type", "official")
        self.assertEqual(panel.choice("state", "copied"), ("Copied from a pack (0)", False))   # counts follow
        panel.set_filter("state", "copied")                                     # mine, so not official: none
        self.assertEqual(listed(), [])
        self.assertTrue(panel.view.topLevelItem(0).text(0).startswith("Nothing matches Official + Copied from a pack."))
        panel.clear_filters()
        self.assertEqual(listed(), everything)
        panel.set_filter("state", "copied")
        self.assertEqual(listed(), ["Renamed copy"])
        panel.clear_filters()
        panel.set_filter("source", "test-presets")
        self.assertNotIn("Renamed copy", listed())
        group = next(c for c, tools in self.ctx.registry.by_category() if tool in tools)
        panel.clear_filters()
        panel.set_filter("group", group)
        self.assertIn("Bold test", listed())
        self.assertNotIn("Test features", listed())                             # Plotter Vision: another group
        panel.clear_filters()
        panel.search.setText("bold clean")                                      # words from the tip
        self.assertEqual(listed(), ["Bold test"])
        panel.clear_filters()
        self.assertTrue(panel.btn_clear.isEnabled() is False and listed() == everything)
        # "Delete" on a pack preset hides it; Show hidden lists it again
        panel.select(tool.id, gentle.uid)
        self.assertEqual(panel.delete_selected(ask=False), 1)
        self.assertNotIn(gentle.uid, [p.uid for p in lib.presets(tool)])
        panel.show_hidden.setChecked(True)
        panel.select(tool.id, gentle.uid)
        self.assertEqual(panel.btn_hide.text(), "Show")
        panel.toggle_hidden_selected()
        self.assertIn(gentle.uid, [p.uid for p in lib.presets(tool)])
        panel.show_hidden.setChecked(False)
        panel.select(tool.id, renamed.uid)
        self.assertEqual(panel.delete_selected(ask=False), 1)                       # yours are deleted
        self.assertEqual(lib.mine.load(tool), [])
        # the preset bar's menu opens this tab on the tool
        bar = self.win.open_tool(tool.id).presets
        self.assertIn("Manage presets and packs…", [a.text() for a in bar.build_menu().actions()])
        self.ctx.manage_presets(tool.id, gentle.uid)
        self.assertIn("Gentle test", panel.details.text())
        self.assert_nothing_of_this_computer(d)
        self.assertTrue(panel.remove_pack(pack.key, ask=False))
        self.assertIsNone(lib.installed("test-presets:complete"))
        # a .zip of a pack's folder, signed by a publisher the app does not know: installs, then can be trusted
        import io
        import zipfile
        stranger = P.pack_bytes(Path(__file__).resolve().parent / "fixtures" / "presets" / "test-pack",
                                ("someone-1", ed25519.new_secret()))
        zipped = self.folder("setup-presets") / "Test presets.zip"
        with zipfile.ZipFile(io.BytesIO(stranger)) as src, zipfile.ZipFile(zipped, "w") as dst:
            for name in src.namelist():
                dst.writestr(f"Test presets/{name}", src.read(name))
            dst.writestr("README.txt", "Thanks!")
        self.assertEqual(panel.import_files([str(zipped)], ask=False), 1)
        self.addCleanup(lambda: lib.installed("test-presets:complete") and lib.remove_pack(
            lib.installed("test-presets:complete")))
        row = next(panel.packs_view.topLevelItem(i) for i in range(panel.packs_view.topLevelItemCount())
                   if panel.packs_view.topLevelItem(i).text(0).startswith("Test"))
        self.assertEqual(row.text(3), "Signed, publisher not trusted")
        row.setSelected(True)
        self.assertTrue(panel.btn_trust.isEnabled())
        self.assertTrue(panel.toggle_trust_selected(ask=False))
        self.assertEqual(lib.installed("test-presets:complete").status, "trusted")
        panel.packs_view.topLevelItem(0).setSelected(True)
        self.assertEqual(panel.btn_trust.text(), "Stop trusting publisher…")
        self.assertTrue(panel.toggle_trust_selected(ask=False))
        self.assertEqual(lib.installed("test-presets:complete").status, "signed")
        d.close()
        self.win.show_home()

    def test_presets_drop_down(self):
        from PySide6.QtCore import Qt
        lib = self.use_test_presets()
        tool = self.ctx.registry.get("svg-overlap-cleaner")
        page = self.win.open_tool(tool.id)
        bar = page.presets
        offered = lib.pack_presets(tool)
        texts = [bar.combo.itemText(i) for i in range(bar.combo.count())]
        names = [bar.combo.itemText(i).strip() for i in range(bar.combo.count()) if bar.combo.itemData(i)]
        self.assertEqual(names[-len(offered):], [p.name for p in offered])        # in pack order, under its heading
        self.assertIn("Test Presets", texts)
        self.assertLess(bar.combo.sizeHint().height(), 48)                         # one line, however many
        pick = offered[-1]
        i = bar.combo.findData(pick.uid)
        bar.combo.activated.emit(i)                                                # as if chosen in the list
        self.assertEqual(bar.combo.currentText().strip(), pick.name)
        self.assertEqual(bar.combo.itemData(i, Qt.ToolTipRole) or "", pick.tip)
        self.assertIn("Test Presets", bar.hint.text())
        self.assertAlmostEqual(bar.form.values()["tol"], 0.2)
        # pack presets are not written to files; a copy in My presets is
        self.assertIsNone(bar.save_to_file(str(self.folder("presets") / "pack.atlaspreset")))
        copy = bar.duplicate()
        self.assertTrue(copy.mine and bar.chosen == copy.uid)
        path = bar.save_mine_to_file(str(self.folder("presets") / "mine.atlaspreset"))
        self.assertTrue(path.exists())
        # saved elsewhere (a workflow step): listed here at once, under "My presets"
        lib.save_mine(tool, "Mine", bar.form.values())
        self.assertIn("My presets", [bar.combo.itemText(i) for i in range(bar.combo.count())])
        mine = {p.name: p.uid for p in lib.mine.load(tool)}
        self.assertIn("Mine", mine)
        self.assertTrue(bar.delete_preset(mine["Mine"], ask=False))
        self.assertTrue(bar.delete_preset(copy.uid, ask=False))
        self.assertEqual(lib.mine.load(tool), [])
        report = bar.load_from_file(str(path), ask=False)                          # and back from the file
        self.assertEqual([p.name for p in report.added], [pick.name])
        menu = [a.text() for a in bar.build_menu().actions() if a.text()]
        self.assertIn("Load presets from a file…", menu)
        # a changed setting goes back to the "Custom settings" placeholder
        bar.apply(pick.uid)
        bar.form.set_values({**bar.form.values(), "tol": 0.33})
        bar.sync()
        self.assertEqual((bar.combo.currentIndex(), bar.combo.placeholderText()), (-1, "Custom settings"))
        lib.delete_mine(tool, lib.mine.load(tool)[0].uid)
        # a tool with no presets shows the placeholder too, a hint, and the save, menu and reset icons
        plain = next(t for t in self.ctx.registry.ordered() if not lib.presets(t))
        pp = self.win.open_tool(plain.id).presets
        self.assertTrue(pp.combo.isVisibleTo(pp) and pp.combo.currentIndex() == -1)
        self.assertTrue(pp.btn_save.isVisibleTo(pp) and pp.btn_more.isVisibleTo(pp) and pp.btn_reset.isVisibleTo(pp))
        self.assertIn("No presets yet", pp.hint.text())
        self.win.show_home()

    def test_about_paths_are_relative_to_known_folders(self):
        import os
        import sys
        from atlas_toolkit.core.paths import app_home
        from atlas_toolkit.ui.about import display_path, folder_size, human_size
        sep = "\\" if sys.platform == "win32" else "/"
        self.assertEqual(display_path(app_home() / ".venv-ml" / "python.exe"), f"<app>{sep}.venv-ml{sep}python.exe")
        if sys.platform == "win32" and os.environ.get("ProgramFiles"):
            p = Path(os.environ["ProgramFiles"]) / "Some App" / "app.exe"
            self.assertEqual(display_path(p), "%ProgramFiles%\\Some App\\app.exe")
        self.assertEqual(display_path(Path(self.tmp).anchor + "elsewhere/odd/tool.exe"), f"…{sep}tool.exe")
        (self.tmp / "sizes").mkdir(exist_ok=True)
        (self.tmp / "sizes" / "a.bin").write_bytes(b"x" * 2048)
        self.assertEqual(folder_size(self.tmp / "sizes"), (2048, 1))
        self.assertEqual(human_size(2048), "2.0 KB")

    # ------------------------------------------------------------------ workflows
    def workflow_page(self, name: str):
        from atlas_toolkit.core import workflow as W
        page = self.win.open_workflow()
        page._set_workflow(W.Workflow(name=name), None)
        return page

    def test_workflow_builds_checks_runs_previews_and_reopens(self):
        from atlas_toolkit.core import samples
        src = samples.mesh(self.folder("wf-in"))
        out = self.folder("wf-out")
        page = self.workflow_page("GUI flow")
        self.assertIs(self.win.stack.currentWidget(), page)
        self.assertTrue(self.win.sidebar.isHidden())            # the step library takes its place
        a = page.add_entry("input:mesh")
        s = page.add_entry("tool:mesh-shatter")                 # added next to the selected step, connected
        h = page.add_entry("tool:stl-hatch-maker")
        o = page.add_entry("save")
        self.assertEqual([page.wf.edge_into(n).src for n in (s, h, o)], [a, s, h])
        page.set_node_values(s, {"num_pieces": 6}, partial=True)
        page.set_node_values(h, {"style": "contours", "levels": 10, "draft_faces": 20000}, partial=True)
        page.set_node_attrs(o, "folder", where="folder", folder=str(out))
        page.add_paths([str(src)])
        self.assertFalse(page.btn_run.isEnabled())               # not checked yet
        self.assertTrue(page.check())
        self.assertTrue(page.btn_run.isEnabled())
        page.set_node_values(s, {"num_pieces": 7}, partial=True)
        self.assertFalse(page.btn_run.isEnabled())               # an edit asks for a new Check
        page.undo.undo()
        self.assertEqual(page.wf.nodes[s].values["num_pieces"], 6)
        self.assertTrue(page.check())
        page.run_all()
        self.wait(lambda: not page.busy() and page.idle())
        self.assertTrue((out / "sample_shattered_6pcs_hatch.svg").exists())
        self.assertEqual(page._state(h)["state"], "done")
        rec = next(r for r in self.history.read() if r.tool == "workflow")
        self.assertEqual((rec.status, rec.workflow["name"]), ("done", "GUI flow"))
        self.assertTrue(page.btn_vary.isVisible() or not page.isVisible())   # Shatter has a seed
        self.assertTrue(page.btn_vary.isEnabled())
        seed = page.wf.nodes[s].values.get("seed")
        page.run_variation()                                     # new seeds, a stamped file beside the first
        self.wait(lambda: not page.busy() and page.idle())
        self.assertEqual(page.wf.nodes[s].values.get("seed"), seed)        # the step keeps its own seed
        self.assertTrue(page.checked)
        self.assertTrue(list(out.glob("sample_shattered_6pcs_hatch_2*.svg")))   # (other tests' inputs may run too)
        rec = next(r for r in self.history.read() if r.tool == "workflow")
        self.assertIn(s, rec.workflow["seeds"])
        page.preview_node(h)                                     # a cached step shows its real output
        self.wait(lambda: page.idle())
        self.assertEqual(page.stage.current_title(), "Output")
        path = page.workflow_dir() / "gui-flow.atlasflow"
        self.assertTrue(page._write(path))
        page._set_workflow(type(page.wf)(), None)
        self.assertTrue(page.open_path(path))
        self.assertEqual(len(page.wf.nodes), 4)
        self.assertEqual(len(page.view.nodes), 4)
        self.assertIn(path, page.saved_workflows())
        self.win.show_home()
        self.assertFalse(self.win.sidebar.isHidden())

    def test_workflow_parameters_write_through_to_the_step(self):
        from atlas_toolkit.ui.workflow.inspector import WorkflowPanel
        page = self.workflow_page("Parameters")
        page.add_entry("input:mesh")
        s = page.add_entry("tool:mesh-shatter")
        page.toggle_exposed(s, "num_pieces")
        page.select(None)
        panel = page.inspector_widget
        self.assertIsInstance(panel, WorkflowPanel)
        panel.form.set_values({f"{s}.num_pieces": 33})
        panel._changed()
        self.assertEqual(page.wf.nodes[s].values["num_pieces"], 33)
        self.assertEqual(page.wf.exposed, [(s, "num_pieces")])

    def test_image_workflow_aim_and_legacy_migration_notice(self):
        from PySide6.QtWidgets import QLabel
        from atlas_toolkit.tools.image_to_mesh.engine import make_sample
        src = make_sample(self.folder('wf-image-aim'))
        page = self.workflow_page('Image aim')
        page.add_entry('input:image')
        image = page.add_entry('tool:image-to-mesh')
        page.set_node_values(image, {'pitch': 5.}, partial=True)
        page.add_paths([str(src)])
        tool_page = self.win.page('image-to-mesh')
        page.aim(image)
        self.wait(lambda: page.idle() and tool_page.borrowed is not None)
        tool_page.form.set_values({'gamma': 1.3, 'yaw': 12., 'mirror_x': True})
        tool_page.end_borrow(keep=True)
        self.assertEqual(page.wf.nodes[image].values['gamma'], 1.3)
        self.assertEqual(page.wf.nodes[image].values['yaw'], 12.)
        self.assertTrue(page.wf.nodes[image].values['mirror_x'])
        legacy = page.add_entry('tool:raster-dem-to-mesh')
        page.wf.nodes[legacy].values['source_mode'] = 'heightmap'
        page.select(legacy)
        page._show_inspector()
        labels = [label.text() for label in page.inspector_widget.findChildren(QLabel)]
        self.assertTrue(any('Non-geographic heightmaps moved to Image to Mesh' in text for text in labels))
        self.assertEqual(page.wf.nodes[legacy].values['source_mode'], 'heightmap')
        from types import SimpleNamespace
        from unittest.mock import Mock
        from atlas_toolkit.core import presets as P
        from atlas_toolkit.tools.raster_dem_to_mesh import TOOL as dem
        from atlas_toolkit.ui.widgets.presets import PresetBar
        here = self.folder("legacy-presets")
        lib = P.PresetLibrary(mine_dir=here / "mine", packs_dir=here / "packs", extra_dirs=[],
                              hidden_path=here / "hidden.json")
        lib.mine.add(dem, [P.Preset('Old heightmap', dem.id, {'source_mode': 'heightmap', 'yaw': 40.})])
        state = dict(dem.values(dict(yaw=12.)))
        toast = Mock()
        bar = PresetBar(dem, lib, SimpleNamespace(values=lambda: state, set_values=lambda v: state.update(v)),
                        self.ctx.theme, toast)
        self.addCleanup(bar.deleteLater)
        bar.sync()
        bar.apply('Old heightmap')             # applies what still fits, says what did not
        self.assertIn('Image to Mesh', toast.call_args.args[0])
        self.assertEqual((state['yaw'], state['source_mode']), (40., dem.defaults()['source_mode']))

    def test_aim_in_tool_brings_the_view_back(self):
        from atlas_toolkit.core import samples
        src = samples.mesh(self.folder("wf-aim"))
        page = self.workflow_page("Aim")
        page.add_entry("input:mesh")
        h = page.add_entry("tool:stl-hatch-maker")
        page.add_paths([str(src)])
        tool_page = self.win.page("stl-hatch-maker")
        own = tool_page.values()["azimuth"]
        page.aim(h)
        self.wait(lambda: page.idle() and tool_page.borrowed is not None)
        self.assertIs(self.win.stack.currentWidget(), tool_page)
        self.assertFalse(tool_page.btn_run.isEnabled())
        tool_page.form.set_values({"azimuth": 33.0})
        tool_page.end_borrow(keep=True)
        self.assertIs(self.win.stack.currentWidget(), page)
        self.assertEqual(page.wf.nodes[h].values["azimuth"], 33.0)
        self.assertEqual(tool_page.values()["azimuth"], own)    # the page keeps its own settings
        self.assertIsNone(tool_page.borrowed)
        page.aim(h)                                              # Discard: back, the step unchanged
        self.wait(lambda: page.idle() and tool_page.borrowed is not None)
        tool_page.form.set_values({"azimuth": 120.0})
        tool_page.end_borrow(keep=False)
        self.assertIs(self.win.stack.currentWidget(), page)
        self.assertEqual(page.wf.nodes[h].values["azimuth"], 33.0)


if __name__ == "__main__":
    unittest.main()
