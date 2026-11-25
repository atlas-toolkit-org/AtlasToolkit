import _isolate  # first: settings and caches stay in a temporary folder
import itertools
import unittest
from unittest import mock

import numpy as np
import trimesh

from flowrep3d import FlowRepError, FlowRepOptions, extract_network, from_arrays
from flowrep3d import network as network_module
from flowrep3d.clustering import aggregate_graph, alignment_weight, correlation_cluster, objective
from flowrep3d.flowlines import _angle, _edge_affinity
from flowrep3d.mesh import _general_clean_orient, _uniform_clean_orient, unit
from .helpers import grid, sphere, torus


def opts(**kw):
    return FlowRepOptions(cache_dir=None, local_passes=1, regularize_iterations=0, clustering_passes=2, **kw)


class AffinityTests(unittest.TestCase):
    def test_vectorised_graph_matches_the_per_vertex_loop(self):
        for mesh in (torus(), sphere(), grid(5, lambda x, y: 0.3 * x * x)):
            topo = mesh.topology; V = mesh.vertices; E = topo.edges; N = topo.normals
            pairs, weights = [], []
            for v, incident in enumerate(topo.vertex_edges):
                d = V[E[incident, 1]] - V[E[incident, 0]]
                d = unit(d - (d @ N[v])[:, None] * N[v])
                for i, j in itertools.combinations(range(len(incident)), 2):
                    pairs.append((incident[i], incident[j])); weights.append(float(alignment_weight(_angle(d[i], d[j]))))
            for a, b in topo.opposite:
                pairs.append((a, b)); weights.append(0.05 * np.exp(-(_angle(V[E[a, 1]] - V[E[a, 0]], V[E[b, 1]] - V[E[b, 0]]) / 5) ** 2))
            a, b, w = _edge_affinity(mesh)
            np.testing.assert_array_equal(np.stack([a, b], 1), np.asarray(pairs))
            np.testing.assert_allclose(w, weights, rtol=1e-12, atol=1e-15)


class RefinementTests(unittest.TestCase):
    def graph(self, n=60, seed=3):
        rng = np.random.default_rng(seed)
        pairs = [(i, j) for i, j in itertools.combinations(range(n), 2) if rng.random() < 0.12]
        return pairs, rng.normal(0.3, 1.0, len(pairs))

    def test_windowed_refinement_never_lowers_the_objective(self):
        for seed in range(5):
            pairs, weights = self.graph(seed=seed)
            graph = aggregate_graph(pairs, weights)
            for depth in (None, 1, 2):
                labels, info = correlation_cluster(60, pairs, weights, passes=4, depth=depth)
                self.assertGreaterEqual(objective(labels, graph) + 1e-8, info["initial_objective"])
                self.assertAlmostEqual(objective(labels, graph), info["objective"], places=6)

    def test_window_covering_both_clusters_is_the_full_refinement(self):
        pairs, weights = self.graph(seed=11)
        full, _ = correlation_cluster(60, pairs, weights, passes=4, depth=None)
        wide, _ = correlation_cluster(60, pairs, weights, passes=4, depth=60)
        np.testing.assert_array_equal(full, wide)

    def test_progress_is_reported_and_bounded(self):
        pairs, weights = self.graph(seed=5)
        seen = []
        correlation_cluster(60, pairs, weights, passes=3, progress=lambda f, d: seen.append((f, d)))
        self.assertTrue(seen)
        self.assertTrue(all(0 <= f <= 1 for f, _ in seen))
        self.assertEqual(seen[-1][0], 1.0)


class NoCache(dict):
    def get(self, key, default=None):
        return default


class CacheTests(unittest.TestCase):
    def test_cached_measures_and_assessments_change_nothing(self):
        original_ev, original_state = network_module.CoverageEvaluator.__init__, network_module.NetworkState.__init__
        def ev_init(self, *a, **k):
            original_ev(self, *a, **k); self._cache = NoCache()
        def state_init(self, *a, **k):
            original_state(self, *a, **k); self._assessed = NoCache()
        for mesh in (torus(16, 8), sphere()):
            cached = extract_network(mesh, opts(relaxed_declutter=True))
            with mock.patch.object(network_module.CoverageEvaluator, "__init__", ev_init), \
                 mock.patch.object(network_module.NetworkState, "__init__", state_init):
                plain = extract_network(mesh, opts(relaxed_declutter=True))
            self.assertEqual(len(cached.curves), len(plain.curves))
            for a, b in zip(cached.curves, plain.curves):
                np.testing.assert_array_equal(a, b)
            self.assertEqual(cached.diagnostics["max_error_deg"], plain.diagnostics["max_error_deg"])

    def test_new_positions_clear_the_measure_cache(self):
        mesh = torus()
        from flowrep3d.flowlines import compute_flowlines
        flows, trim, _ = compute_flowlines(mesh, opts())
        state = network_module.NetworkState(network_module.CoverageEvaluator(mesh, flows), trim, np.zeros(len(flows), bool))
        region = max(state.regions.values(), key=lambda r: r.error)
        state.ev.measure(region.faces, state.blocked)
        self.assertTrue(state.ev._cache)
        state.ev.N = unit(state.ev.N + [0.3, 0.0, 0.0])
        self.assertFalse(state.ev._cache)                 # new normals: nothing measured before still holds
        after = state.ev.measure(region.faces, state.blocked)
        fresh = network_module.CoverageEvaluator(mesh, flows); fresh.N = state.ev.N
        expected = fresh._measure(np.asarray(region.faces), after.vertices, state.blocked, None)
        self.assertEqual((after.error, after.boundary_cycles), (expected.error, expected.boundary_cycles))


class LoaderTests(unittest.TestCase):
    def same(self, V, F):
        fast = _uniform_clean_orient(V, F)
        self.assertIsNotNone(fast)
        slow = _general_clean_orient(V, F)
        np.testing.assert_array_equal(fast[0], slow[0])
        self.assertEqual(len(fast[1]), len(slow[1]))
        for a, b in zip(fast[1], slow[1]):
            np.testing.assert_array_equal(a, b)
        self.assertEqual(fast[2], slow[2])

    def test_uniform_meshes_load_exactly_as_face_by_face(self):
        s = trimesh.creation.icosphere(2); V, F = np.array(s.vertices), np.array(s.faces)
        rng = np.random.default_rng(1)
        self.same(V, F)
        self.same(V, np.where(rng.random(len(F))[:, None] < 0.3, F[:, ::-1], F)[rng.permutation(len(F))])
        self.same(V, F[:, ::-1])                                              # inside out: turned back
        self.same(np.vstack([V, V[:3]]), np.vstack([F, F[:20], F[:5, ::-1], [[0, 0, 1], [len(V), 0, 1]]]))
        self.same(np.vstack([V, V + 3]), np.vstack([F, F[:, ::-1] + len(V)]))  # two balls, one inside out
        n = 6; G = np.array([(x, y, 0.1 * x * y) for y in range(n) for x in range(n)], float)
        Q = np.array([[j*n+i, j*n+i+1, (j+1)*n+i+1, (j+1)*n+i] for j in range(n-1) for i in range(n-1)])
        self.same(G, np.where((np.arange(len(Q)) % 2 == 0)[:, None], Q, Q[:, ::-1]))   # open quads, half flipped

    def test_broken_meshes_give_the_same_errors_or_take_the_general_path(self):
        fin = (np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1.]]), np.array([[0, 1, 2], [1, 0, 3], [0, 1, 4]]))
        for V, F in (fin,):
            with self.assertRaises(FlowRepError) as fast:
                _uniform_clean_orient(V, F)
            with self.assertRaises(FlowRepError) as slow:
                _general_clean_orient(V, F)
            self.assertEqual(str(fast.exception), str(slow.exception))
        cone = trimesh.creation.cone(1, 1, sections=8); cv, cf = np.array(cone.vertices), np.array(cone.faces)
        apex = int(np.argmax(cv[:, 2])); other = cv.copy(); other[:, 2] = 2 * cv[apex, 2] - other[:, 2]
        V, F = np.vstack([cv, other]), np.vstack([cf, cf[:, ::-1] + len(cv)])
        self.assertIsNone(_uniform_clean_orient(V, F))                       # touching sheets: split face by face
        self.assertEqual(from_arrays(V, F).metadata["topology_repairs"]["split_touching_vertices"], 1)
        self.assertIsNone(_uniform_clean_orient(V, [list(f) for f in F[:-1]] + [[0, 1, 2, 3]]))  # mixed sizes


class ProgressTests(unittest.TestCase):
    def test_clustering_has_its_own_progress_steps(self):
        seen = []
        extract_network(torus(), opts(), lambda f, m: seen.append((f, m)))
        fractions = [f for f, _ in seen]
        self.assertEqual(fractions, sorted(fractions))
        self.assertTrue(any("clustering" in m and 0.1 < f < 0.25 for f, m in seen))
        self.assertTrue(any("grouping" in m for _, m in seen))


if __name__ == "__main__":
    unittest.main()
