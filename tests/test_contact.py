"""geom/contact: vectorised triangle crossings against libigl's exact pair test, local re-tests and coplanar faces."""
import _isolate  # first: settings and caches stay in a temporary folder
import unittest

import numpy as np
import trimesh

from atlas_toolkit.geom import contact
from atlas_toolkit.tools.mesh_dilate.mesh_dilate import intersections


def crumpled(seed, amount=.06):
    """A sphere whose vertices are jittered at random: plenty of crossings between nearby faces."""
    m = trimesh.creation.icosphere(subdivisions=3)
    rng = np.random.default_rng(seed)
    return m.vertices + rng.normal(0, amount, (len(m.vertices), 3)), np.asarray(m.faces)


class ContactTests(unittest.TestCase):
    def test_same_crossings_as_libigl(self):
        for seed in range(3):
            V, F = crumpled(seed)
            ours = {tuple(p) for p in contact.crossings(V, F).tolist()}
            igl = intersections(V, F, limit=10**9)
            self.assertGreater(len(igl), 10)
            self.assertEqual(ours, igl)

    def test_clean_surface_and_small_chunks(self):
        m = trimesh.creation.icosphere(subdivisions=4)
        self.assertEqual(len(contact.crossings(m.vertices, m.faces)), 0)
        V, F = crumpled(4)
        everything = {tuple(p) for p in contact.crossings(V, F).tolist()}
        chunked = {tuple(p) for p in contact.crossings(V, F, chunk=500).tolist()}
        self.assertEqual(everything, chunked)

    def test_active_faces_find_the_pairs_they_are_in(self):
        V, F = crumpled(5)
        pairs = contact.crossings(V, F)
        active = np.zeros(len(F), bool)
        active[np.unique(pairs[:5].ravel())] = True
        local = {tuple(p) for p in contact.crossings(V, F, active=active).tolist()}
        want = {tuple(p) for p in pairs.tolist() if active[p[0]] or active[p[1]]}
        self.assertEqual(local, want)

    def test_touching_is_not_crossing_and_coplanar_overlap_is(self):
        tri = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
        def pair(other):
            return contact.crossings(np.vstack((tri, other)), np.array([[0, 1, 2], [3, 4, 5]]))
        self.assertEqual(len(pair(np.array([[.2, .2, 0], [.3, .2, 1], [.2, .3, 1]]))), 0)       # a corner on it
        self.assertEqual(len(pair(np.array([[.2, .2, -1], [.3, .2, 1], [.2, .3, 1]]))), 1)      # through it
        self.assertEqual(len(pair(np.array([[.2, .2, 0], [1.2, .2, 0], [.2, 1.2, 0]]))), 1)     # coplanar overlap
        self.assertEqual(len(pair(np.array([[1, 0, 0], [2, 0, 0], [1, 1, 0]]) + [.1, 0, 0])), 0)  # coplanar apart
        self.assertEqual(len(pair(tri + [0, 0, .5])), 0)                                        # parallel

    def test_huge_faces_pair_by_direct_test(self):
        """A face over thousands of grid cells (a big flat cap among small faces) is tested directly."""
        V, F = crumpled(6, amount=0)
        cap = np.array([[-3, -3, .2], [3, -3, .2], [0, 3, .2]])
        V = np.vstack((V, cap))
        F = np.vstack((F, [len(V) - 3, len(V) - 2, len(V) - 1]))
        pairs = contact.crossings(V, F)
        self.assertTrue((pairs == len(F) - 1).any())
        self.assertEqual({tuple(p) for p in pairs.tolist()}, intersections(V, F, limit=10**9))

    def test_budget_gives_up(self):
        V, F = crumpled(7)
        P = V[F]
        self.assertEqual(contact.box_pairs(P.min(1), P.max(1), budget=10), [])
        self.assertTrue(contact.box_pairs(P.min(1), P.max(1), budget=10**9))


if __name__ == "__main__":
    unittest.main()
