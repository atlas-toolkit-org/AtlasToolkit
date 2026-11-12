"""Batched ray casting (geom/raycast): hits, maximum lengths, visibility from a camera, bisected transitions."""
import _isolate  # first: settings and caches stay in a temporary folder
import unittest

import numpy as np
import trimesh

from atlas_toolkit.geom import raycast as rc
from atlas_toolkit.geom.camera import Camera


def sphere(r=30.0, sub=5):
    return trimesh.creation.icosphere(subdivisions=sub, radius=r)


class CasterTests(unittest.TestCase):
    def test_sphere_hits_match_analytic(self):
        m = sphere()
        c = rc.Caster(m.vertices, m.faces)
        rng = np.random.default_rng(3)
        xy = rng.uniform(-20, 20, size=(500, 2))
        o = np.column_stack([xy, np.full(500, 100.0)])
        d = np.tile([0, 0, -1.0], (500, 1))
        t, face, u, v = c.intersect(o, d)
        z = 100.0 - t
        expect = np.sqrt(30.0 ** 2 - (xy ** 2).sum(1))
        np.testing.assert_allclose(z, expect, atol=0.05)          # facets of the icosphere, not float32
        P = c.points(face, u, v)
        np.testing.assert_allclose(P[:, :2], xy, atol=1e-3)
        self.assertTrue((face >= 0).all())

    def test_misses_and_maximum_length(self):
        m = sphere()
        c = rc.Caster(m.vertices, m.faces)
        o = np.array([[0, 0, 100.0], [0, 0, 100.0], [80, 0, 100.0]])
        d = np.tile([0, 0, -1.0], (3, 1))
        t, face, _, _ = c.intersect(o, d, tmax=[50.0, 80.0, 80.0])
        self.assertEqual(face.tolist()[0], -1)
        self.assertAlmostEqual(t[1], 70.0, delta=0.05)
        self.assertEqual(face[2], -1)
        occ = c.occluded(o, d, [69.0, 71.0, 500.0])
        self.assertEqual(occ.tolist(), [False, True, False])


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        plate = trimesh.creation.box(extents=(40, 2, 40))
        plate.apply_translation((0, -20, 0))           # in front of a sphere seen from -Y
        self.ball = sphere(15.0)
        self.scene = trimesh.util.concatenate([plate, self.ball])
        self.caster = rc.Caster(self.scene.vertices, self.scene.faces)
        self.cam = Camera(theta=90.0, psi=180.0, radius=150.0, fov=40.0)     # on -Y, looking at +Y

    def test_front_of_plate_visible_sphere_behind_hidden(self):
        P = np.array([[0, -21.0, 0], [0, -15.0, 0], [0, 15.0, 0], [35, -12.0, 0]])
        vis = rc.visible(self.caster, self.cam, P, eps=0.05)
        self.assertEqual(vis.tolist(), [True, False, False, True])
        for ortho in (True,):
            cam = Camera(theta=90.0, psi=180.0, radius=150.0, fov=40.0, ortho=ortho)
            self.assertEqual(rc.visible(self.caster, cam, P, eps=0.05).tolist(), [True, False, False, True])

    def test_bisection_lands_on_the_plate_edge(self):
        # a line on the sphere's equator at the front, from hidden (behind the plate) to visible beyond its edge
        line = np.array([[x, -12.0, 0.0] for x in np.linspace(5, 34, 60)])
        runs = rc.split_visible(self.caster, self.cam, [line], [False], eps=0.05)[0]
        self.assertEqual([v for v, _ in runs], [False, True])
        switch = runs[1][1][0]
        # the plate's edge x = 20 at y = -21 (its front face) seen from the eye: the ray through it reaches y = -12
        eye = self.cam.basis()[0]
        edge = np.array([20.0, -21.0, 0.0])
        t = (-12.0 - eye[1]) / (edge[1] - eye[1])
        expect_x = eye[0] + t * (edge[0] - eye[0])
        self.assertAlmostEqual(switch[0], expect_x, delta=0.6)

    def test_closed_loop_joins_across_its_start(self):
        a = np.linspace(0, 2 * np.pi, 200)
        loop = np.column_stack([15.0 * np.cos(a), 15.0 * np.sin(a), np.zeros_like(a)])
        loop[-1] = loop[0]
        runs = rc.split_visible(self.caster, Camera(theta=90.0, psi=180.0, radius=150.0, fov=40.0, ortho=True),
                                [loop * 1.0001], [True], eps=0.05)[0]
        states = [v for v, _ in runs]
        self.assertTrue(all(a != b for a, b in zip(states, states[1:])))   # alternating, no two equal neighbours
        self.assertLessEqual(len(runs), 4)


if __name__ == "__main__":
    unittest.main()
