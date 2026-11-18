"""
The model catalogue window (ui/catalogue.py) offscreen: categories, search, picking a model, and every
place it opens from (a mesh tool's Inputs, a mesh file setting, a workflow's inputs and fixed input, Home, Setup ›
Storage). Settings and models go to temporary folders.
"""
import _isolate  # first: settings and caches stay in a temporary folder
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class CatalogueUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="atlas_catalogue_ui_"))
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
        cls.ctx = AppContext(registry(), AppSettings(cls.tmp / "cfg"), JobBridge(), History(cls.tmp / "h.jsonl"))
        cls.ctx.settings.set("catalogue_dir", str(cls.tmp / "Catalogue"))
        cls.win = MainWindow(cls.ctx)

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        cls.ctx.bridge.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def wait(self, cond, timeout=120.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            self.app.processEvents()
            if cond():
                return
            time.sleep(0.02)
        self.fail("timed out")

    def dialog(self, multiple=True):
        from atlas_toolkit.ui.catalogue import CatalogueDialog
        d = CatalogueDialog(self.ctx, multiple=multiple)
        self.addCleanup(d.deleteLater)
        return d

    def pick_category(self, d, key):
        for i in range(d.rail.count()):
            if d.rail.item(i).data(0x0100) == key:
                d.rail.setCurrentRow(i)
                return
        self.fail(key)

    def visible_ids(self, d):
        from atlas_toolkit.ui.catalogue import ROLE_MODEL
        return [d.proxy.index(r, 0).data(ROLE_MODEL).id for r in range(d.proxy.rowCount())]

    def test_categories_search_and_details(self):
        d = self.dialog()
        n_all = d.proxy.rowCount()
        self.assertEqual(n_all, len(d.cat.models()))
        self.pick_category(d, "shapes")
        ids = self.visible_ids(d)
        self.assertIn("torus", ids)
        self.assertTrue(all(d.cat.get(i).category == "shapes" for i in ids))
        d.search.setText("knot")
        self.assertTrue(set(self.visible_ids(d)) >= {"trefoil-knot", "figure-eight-knot"})
        d.search.setText("no such model anywhere")
        self.assertEqual(d.proxy.rowCount(), 0)
        self.assertFalse(d.empty.isHidden())
        d.search.setText("")
        d.show_seg.picked.emit("local")              # nothing fetched yet in this folder
        self.assertEqual(d.proxy.rowCount(), 0)
        d.show_seg.picked.emit("app")
        self.assertTrue(self.visible_ids(d) and all(d.cat.get(i).kind == "bundled" for i in self.visible_ids(d)))
        d.show_seg.picked.emit("download")
        self.assertTrue(self.visible_ids(d) and all(d.cat.get(i).kind == "remote" for i in self.visible_ids(d)))
        d.show_seg.picked.emit("all")
        d.grid.setCurrentIndex(d.proxy.index(0, 0))
        self.assertTrue(d.d_name.text())
        self.assertIn("triangles", d.d_facts.text())

    def test_using_a_model_returns_its_file(self):
        """On a small stand-in catalogue with one starter model (a fresh checkout has no starter files)."""
        from unittest import mock

        import test_catalogue as tc
        files = self.tmp / "app"
        (files / "models").mkdir(parents=True, exist_ok=True)
        gz = tc._packed(files / "models")
        tc._index(files, [{"id": "ball", "name": "Ball", "category": "shapes", "kind": "bundled", "file": gz.name,
                           "sha256": tc.sha(gz), "bytes": gz.stat().st_size, "license": "CC0-1.0", "triangles": 320}])
        with mock.patch("atlas_toolkit.core.catalogue.root", return_value=files):
            d = self.dialog(multiple=False)
            self.pick_category(d, "shapes")
            d.grid.setCurrentIndex(d.proxy.index(0, 0))
            d._use()
            self.wait(lambda: d.result() != 0 or d.paths)
            self.assertEqual(len(d.paths), 1)
            p = Path(d.paths[0])
            self.assertTrue(p.is_file())
            self.assertTrue(p.resolve().is_relative_to((self.tmp / "Catalogue").resolve()))
            self.assertEqual(self.ctx.settings.get("catalogue_recent")[0], "ball")
            d2 = self.dialog()
            self.pick_category(d2, "__recent__")
            self.assertEqual(self.visible_ids(d2), ["ball"])
            self.assertEqual(d2.list_model.state["ball"], "local")

    def test_mesh_tools_offer_the_catalogue_and_svg_tools_do_not(self):
        page = self.win.open_tool("contour-lines")
        self.assertIsNotNone(page.btn_catalogue)
        self.assertIn("catalogue</a>", page.queue.drop.text())
        svg = self.win.open_tool("svg-overlap-cleaner")
        self.assertIsNone(svg.btn_catalogue)

    def test_picked_models_reach_the_tool_queue(self):
        from unittest import mock

        import trimesh

        from atlas_toolkit.core import catalogue as core
        ball = trimesh.creation.icosphere(subdivisions=2)
        path = str(core.write_ply(self.tmp / "picked.ply", ball.vertices, ball.faces))
        page = self.win.open_tool("contour-lines")
        with mock.patch("atlas_toolkit.ui.catalogue.pick_models", return_value=[path]):
            page.pick_catalogue()
        self.assertIn(path, page.rows)

    def test_mesh_file_settings_and_workflows_offer_it(self):
        from atlas_toolkit.ui.widgets.common import QToolButton
        page = self.win.open_tool("mesh-fusion")
        tips = [b.toolTip() for b in page.form.findChildren(QToolButton)]
        self.assertTrue(any("catalogue" in t.lower() for t in tips))
        wf = self.win.open_workflow()
        self.assertTrue(hasattr(wf, "btn_catalogue"))

    def test_setup_storage_shows_the_folder(self):
        from atlas_toolkit.core.paths import display_path
        from atlas_toolkit.ui.setup import SetupDialog
        s = SetupDialog(self.ctx)
        self.addCleanup(s.deleteLater)
        self.assertEqual(s.cat_path.text(), display_path(self.tmp / "Catalogue"))
        self.assertNotIn(str(Path.home()).lower(), s.cat_path.text().lower(), "nothing of this computer shown")
        s._set_catalogue_dir(str(self.tmp / "Elsewhere"))
        self.assertEqual(self.ctx.settings.get("catalogue_dir"), str(self.tmp / "Elsewhere"))
        self.assertEqual(s.cat_path.text(), display_path(self.tmp / "Elsewhere"))
        s._set_catalogue_dir(str(self.tmp / "Catalogue"))


if __name__ == "__main__":
    unittest.main()
