"""Face painting: screen-space picking (no GL) and the paint dialog on a large mesh (real GL, skipped without it)."""
import _isolate  # first: settings and caches stay in a temporary folder
import time
import unittest

import numpy as np
import trimesh

from atlas_toolkit.geom.screen_pick import ScreenPicker, decode_ids, encode_ids


def ortho_view(width, height):
    """A view looking down -z at the unit square [0, w] x [0, h] in pixels, depth = -z in [0, 10]."""
    P = np.array([[2. / width, 0, 0, -1.],
                  [0, -2. / height, 0, 1.],        # y down, as screen pixels
                  [0, 0, -2. / 10, -1.],
                  [0, 0, 0, 1.]])
    return P, np.eye(4)


def window_depth(eye):
    """Window depth of a point `eye` units in front of the camera in `ortho_view`."""
    return (eye / 10. * 2. - 1. + 1.) / 2.


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.w, self.h = 200, 100

    def picker(self, centers, ids, depth, tolerance=.05):
        picker = ScreenPicker(np.asarray(centers, float), tolerance)
        P, V = ortho_view(self.w, self.h)
        picker.set_view(P, V, self.w, self.h, ids, depth)
        return picker

    def test_ids_round_trip(self):
        rgba = encode_ids(70000)
        self.assertEqual(rgba.dtype, np.uint8)
        np.testing.assert_array_equal(decode_ids(rgba), np.arange(70000))
        self.assertEqual(decode_ids(np.zeros((1, 4), np.uint8))[0], -1)

    def test_pixels_pick_a_face_larger_than_the_brush(self):
        ids = np.full((self.h, self.w), 0)        # one face covers the whole screen; its centre is far away
        depth = np.full((self.h, self.w), window_depth(5.))
        picker = self.picker([[-500., -500., -5.]], ids, depth)
        np.testing.assert_array_equal(np.unique(picker.stroke([(100, 50)], 5)), [0])

    def test_centres_fill_in_faces_smaller_than_a_pixel_but_not_hidden_ones(self):
        ids = np.full((self.h, self.w), -1)
        ids[:, :] = 0                                         # the visible surface, face 0, at depth 5
        depth = np.full((self.h, self.w), window_depth(5.))
        centers = [[1000., 1000., -5.],                       # face 0, off screen
                   [100.3, 50.2, -5.01],                      # sub-pixel face on the surface: picked
                   [100.6, 50.4, -9.],                        # behind the surface: hidden
                   [140., 50., -5.]]                          # on the surface, outside the brush
        picker = self.picker(centers, ids, depth)
        np.testing.assert_array_equal(np.unique(picker.stroke([(100, 50)], 10)), [0, 1])

    def test_a_swept_stroke_leaves_no_gaps(self):
        ids = np.full((self.h, self.w), -1)
        ids[:, :] = np.arange(self.w)[None, :]                # one face per pixel column
        depth = np.full((self.h, self.w), window_depth(5.))
        picker = self.picker([[x + .5, 50., -5.] for x in range(self.w)], ids, depth)
        picked = np.unique(picker.stroke([(20, 50), (100, 50), (180, 50)], 3))
        np.testing.assert_array_equal(picked, np.arange(17, 183))

    def test_background_and_outside_the_screen_pick_nothing(self):
        ids = np.full((self.h, self.w), -1)
        depth = np.ones((self.h, self.w))
        picker = self.picker([[100., 50., -5.]], ids, depth)
        self.assertEqual(len(picker.stroke([(-50, -50), (-40, -40)], 5)), 0)
        self.assertEqual(len(picker.stroke([(10, 10)], 5)), 0)


class PaintDialogTests(unittest.TestCase):
    """The real dialog on a 330K-face sphere: picks only what faces the camera, and stays quick."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.mesh = trimesh.creation.icosphere(subdivisions=7)

    def dialog(self):
        from atlas_toolkit.ui.theme import Theme
        from atlas_toolkit.ui.widgets.face_paint import SelectionDialog
        dialog = SelectionDialog(self.mesh, self.mesh, np.zeros(len(self.mesh.faces), bool), Theme("dark"))
        self.addCleanup(dialog.close)
        dialog.resize(1000, 700)
        dialog.show()
        for _ in range(5):
            self.app.processEvents()
        if not dialog.canvas.pick_view():
            self.skipTest("no OpenGL drawing here: " + (dialog.canvas.gl_failure or "the view is not valid"))
        return dialog

    def test_dab_picks_visible_faces_quickly(self):
        dialog = self.dialog()
        canvas = dialog.canvas
        x, y = canvas.width() / 2, canvas.height() / 2
        dialog.canvas.setCameraPosition(azimuth=10)          # a new camera: the dab includes the pick pass
        start = time.perf_counter()
        dialog.begin(x, y, False)
        elapsed = time.perf_counter() - start
        picked = np.flatnonzero(dialog.selected)
        self.assertGreater(len(picked), 100)
        self.assertLess(elapsed, 0.5)
        # Every picked face faces the camera, and lies under the brush.
        eye = canvas.cameraPosition()
        eye = np.array([eye.x(), eye.y(), eye.z()])
        centers = self.mesh.triangles_center[picked]
        facing = np.einsum("ij,ij->i", self.mesh.face_normals[picked], eye - centers)
        self.assertTrue((facing > 0).all())
        screen = canvas.project(centers)
        self.assertTrue((np.hypot(screen[:, 0] - x, screen[:, 1] - y) < dialog.radius.value() + 3).all())
        # Undo restores the empty mask.
        dialog.undo()
        self.assertFalse(dialog.selected.any())

    def test_a_stroke_is_painted_in_one_go_per_frame(self):
        dialog = self.dialog()
        canvas = dialog.canvas
        y = canvas.height() / 2
        dialog.begin(200, y, False)
        start = time.perf_counter()
        for i in range(1, 21):
            dialog.queue(200 + 30 * i, y, False)
        dialog.flush()
        dialog.refresh()
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 2.)
        self.assertFalse(dialog.busy())
        # The stroke is continuous: faces under its middle are painted.
        screen = canvas.project(self.mesh.triangles_center[dialog.selected])
        self.assertTrue(((np.abs(screen[:, 0] - 500) < 5) & (np.abs(screen[:, 1] - y) < 5)).any())
        # Erasing the same stroke clears what it painted.
        dialog.begin(200, y, True)
        for i in range(1, 21):
            dialog.queue(200 + 30 * i, y, True)
        dialog.flush()
        self.assertFalse(dialog.selected.any())


if __name__ == "__main__":
    unittest.main()
