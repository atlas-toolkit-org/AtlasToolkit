"""The quicker mesh plumbing big scans go through (2.5.0) gives exactly what the slow way gave: vertex merge and
duplicate faces as trimesh, the solid test as before, open edges in the same order, and a scan with a real opening
reported without a tidy that could not close it."""
import _isolate  # first: settings and caches stay in a temporary folder
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom import repair as R


def noisy(n_faces=150_000, seed=0):
    """A triangle soup with exact and near duplicate vertices and duplicate faces, past the fast-path threshold."""
    rng = np.random.default_rng(seed)
    s = trimesh.creation.icosphere(subdivisions=7, radius=10.0)
    tri = np.asarray(s.vertices)[np.asarray(s.faces)].reshape(-1, 3)     # unmerged, as an STL reads
    tri[:5000] += rng.normal(scale=1e-10, size=(5000, 3))                  # merges at trimesh's 1e-8 tolerance
    tri[5000:6000] += 1e-5                                                # does not
    F = np.arange(len(tri)).reshape(-1, 3)
    F = np.vstack([F, F[rng.integers(0, len(F), 3000)][:, [1, 2, 0]], F[rng.integers(0, len(F), 2000)][:, ::-1]])
    return trimesh.Trimesh(tri, F, process=False)


def old_is_solid(V, F):
    """is_solid as it was before 2.5.0."""
    if len(F) < 4:
        return False
    he, _face, eid, count = R._edges(F)
    if (count != 2).any():
        return False
    order = np.argsort(eid, kind="stable")
    h = he[order]
    if (h[0::2, 0] == h[1::2, 0]).any() or R.signed_volume(V, F) <= 0:
        return False
    return R.solid_body(V, F) is not None


class FastPaths(unittest.TestCase):
    def test_merge_and_unique_faces_match_trimesh(self):
        for seed in (0, 1):
            a, b = noisy(seed=seed), noisy(seed=seed)
            self.assertGreater(len(a.vertices), gm.FAST_MERGE_MIN)
            a.merge_vertices()
            gm.merge_vertices(b)
            np.testing.assert_array_equal(a.vertices, b.vertices)
            np.testing.assert_array_equal(a.faces, b.faces)
            np.testing.assert_array_equal(a.unique_faces(), gm.unique_faces(b))

    def test_first_rows_is_unique_rows_in_order(self):
        rng = np.random.default_rng(3)
        q = rng.integers(0, 50, size=(300_000, 3))
        first, inverse = gm.first_rows(q)
        np.testing.assert_array_equal(q[first][inverse], q)
        _, ref_first = np.unique(q, axis=0, return_index=True)
        np.testing.assert_array_equal(first, np.sort(ref_first))

    def test_solid_test_agrees_with_the_old_one(self):
        s = trimesh.creation.icosphere(subdivisions=4)
        V, F = np.asarray(s.vertices), np.asarray(s.faces)
        cases = {"solid": F, "flipped face": np.vstack([F[:1, ::-1], F[1:]]), "hole": F[1:],
                 "inside out": F[:, ::-1], "doubled face": np.vstack([F, F[:1]]),
                 "two touching": np.vstack([F, F + len(V)])}
        V2 = np.vstack([V, V])
        for name, faces in cases.items():
            verts = V2 if name == "two touching" else V
            self.assertEqual(R.is_solid(verts, faces), old_is_solid(verts, faces), name)

    def test_closed_matches_watertight(self):
        s = trimesh.creation.icosphere(subdivisions=4)
        F = np.asarray(s.faces)
        for faces in (F, F[1:], np.vstack([F, F[:1]]), F[:, ::-1], np.vstack([F, F + 10_000])):
            m = trimesh.Trimesh(np.vstack([s.vertices] * 8), faces, process=False)
            self.assertEqual(gm.is_closed(faces), bool(m.is_watertight))

    def test_open_edges_unchanged(self):
        s = trimesh.creation.icosphere(subdivisions=5)
        F = np.asarray(s.faces)
        F = F[np.asarray(s.vertices)[F].mean(1)[:, 2] > -0.5]
        e = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
        key = e[:, 0] * (int(e.max()) + 1) + e[:, 1]
        _, first, counts = np.unique(key, return_index=True, return_counts=True)
        expected = np.asarray(s.vertices, np.float32)[e[first[counts == 1]]]
        got, n = gm.open_edge_segments(s.vertices, F)
        self.assertEqual(n, len(expected))
        np.testing.assert_array_equal(got, expected)

    def test_a_real_opening_skips_the_tidy(self):
        s = trimesh.creation.icosphere(subdivisions=5, radius=10.0)
        V, F = np.asarray(s.vertices), np.asarray(s.faces)
        base = F[V[F].mean(1)[:, 2] > -8]
        d = R.beyond_tidy(V, base)
        self.assertIsNotNone(d)
        self.assertEqual(d.holes, 1)
        self.assertIn("open edge", d.summary())
        self.assertIsNone(R.beyond_tidy(V, F))                       # closed
        self.assertIsNone(R.beyond_tidy(V, F[1:]))                   # a pin-hole the tidy fills
        # an open crumb far from the body: its rim is small, the tidy drops it
        crumb = trimesh.creation.icosphere(subdivisions=1, radius=0.01)
        cf = np.asarray(crumb.faces)[2:] + len(V)
        V3 = np.vstack([V, np.asarray(crumb.vertices) + 30])
        self.assertIsNone(R.beyond_tidy(V3, np.vstack([F, cf])))

    def test_an_input_is_read_once_while_checked(self):
        d = Path(tempfile.mkdtemp(prefix="atlas_reuse_"))
        p = d / "a.stl"
        s = trimesh.creation.icosphere(subdivisions=3)
        gm.write_stl(p, s.vertices, s.faces)
        with gm.reuse_loads():
            a = gm.load_mesh(p)
            a.vertices[0] = (9, 9, 9)                                 # callers get their own copy
            b = gm.load_mesh(p)
        self.assertFalse(np.allclose(b.vertices[0], (9, 9, 9)))
        self.assertIsNone(gm._REUSE)


if __name__ == "__main__":
    unittest.main()
