"""Pipe Fill geometry, containment, hollow bores, deterministic routing and Atlas IO."""
import _isolate  # first: settings and caches stay in a temporary folder
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from atlas_toolkit.core.job import Cancelled, JobContext
from atlas_toolkit.core.registry import registry, validate
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.pipe_fill import TOOL
from atlas_toolkit.tools.pipe_fill import pipe_fill as pf
from atlas_toolkit.tools.pipe_fill.engine import run


def context(mode='run', cancelled=lambda: False):
    return JobContext(lambda *_: None, cancelled, mode=mode)


def packed(mesh, **overrides):
    p = TOOL.values(dict(spacing=16., diameter=4., length=24, sides=8, samples=4, **overrides))
    domain = pf.Domain(mesh.vertices, mesh.faces)
    curves, info = pf.pack(domain, p, lambda *_: None, lambda: None)
    result = pf.build_mesh(curves, p.sides, p.section == 'hollow', p.wall/100., p.max_faces, lambda: None)
    return curves, info, result, domain, p


class PipeGeometryTests(unittest.TestCase):
    def test_all_modes_and_topologies_stay_inside_and_separate(self):
        body = trimesh.creation.box(extents=(1.6, 1.4, 2.))
        for mode in ('organic', 'orthogonal'):
            for topology in ('strands', 'loops'):
                for section in ('solid', 'hollow'):
                    with self.subTest(mode=mode, topology=topology, section=section):
                        curves, info, out, domain, p = packed(body, mode=mode, topology=topology, section=section)
                        self.assertTrue(curves)
                        self.assertGreater(info['coverage'], 45)
                        self.assertLess(out.volume, body.volume*.5)
                        self.assertTrue(out.is_watertight)
                        self.assertTrue(out.is_winding_consistent)
                        self.assertGreater(out.volume, 0)
                        self.assertTrue(out.nondegenerate_faces().all())
                        self.assertTrue(np.isfinite(out.vertices).all())
                        self.assertGreaterEqual(domain.distance(out.vertices).min(), p.clearance/50.-1e-7)
                        separation = pf.Separation(p.diameter/100., p.gap/50.)
                        for c in curves:
                            self.assertEqual(c.closed, topology == 'loops')
                            self.assertTrue(separation.valid(c))
                            separation.add(c)

    def test_hollow_open_pipe_has_real_bore_and_annular_rims(self):
        p = np.column_stack((np.zeros(9), np.zeros(9), np.linspace(-1, 1, 9)))
        curve = pf.Curve(p, False, np.full(9, .1))
        solid = pf.sweep(curve, 24, False, .25)
        hollow = pf.sweep(curve, 24, True, .25)
        self.assertTrue(hollow.is_watertight)
        self.assertTrue(hollow.is_winding_consistent)
        self.assertEqual(len(hollow.split()), 1)
        self.assertAlmostEqual(hollow.volume/solid.volume, 1-.75**2, places=8)
        from trimesh.ray.ray_triangle import RayMeshIntersector
        ray = RayMeshIntersector(hollow)
        self.assertFalse(ray.intersects_any([[0, 0, -2]], [[0, 0, 1]])[0])
        self.assertTrue(ray.intersects_any([[.09, 0, -2]], [[0, 0, 1]])[0])

    def test_periodic_loop_sweep_and_thickness_variation(self):
        t = np.arange(96)*2*np.pi/96
        curve = pf.Curve(np.column_stack((.5*np.cos(t), .5*np.sin(t), .1*np.sin(2*t))),
                         True, .035*(1+.2*np.sin(3*t)))
        for hollow in (False, True):
            out = pf.sweep(curve, 16, hollow, .3)
            self.assertTrue(out.is_watertight)
            self.assertTrue(out.is_winding_consistent)
            self.assertGreater(out.volume, 0)
            self.assertTrue(out.nondegenerate_faces().all())
            self.assertEqual(out.euler_number, 0)

    def test_cavities_and_disconnected_bodies_are_respected(self):
        outer = trimesh.creation.box(extents=(2, 2, 2))
        inner = trimesh.creation.box(extents=(.65, .65, .65))
        inner.invert()
        shell = trimesh.util.concatenate((outer, inner))
        curves, info, out, domain, p = packed(shell)
        self.assertLess(domain.distance([[0, 0, 0]])[0], 0)
        self.assertGreater(domain.distance(out.vertices).min(), 0)
        self.assertFalse(np.any(np.max(np.abs(out.vertices), axis=1) < .325))
        a = trimesh.creation.box(extents=(.8, .8, 2))
        b = a.copy()
        a.apply_translation((-.7, 0, 0))
        b.apply_translation((.7, 0, 0))
        _curves, _info, out, _d, _p = packed(trimesh.util.concatenate((a, b)))
        self.assertTrue(np.any(out.vertices[:, 0] < -.3))
        self.assertTrue(np.any(out.vertices[:, 0] > .3))
        self.assertFalse(np.any(np.abs(out.vertices[:, 0]) < .299))

    def test_boundary_certification_rejects_a_segment_across_cavity(self):
        outer = trimesh.creation.box(extents=(2, 2, 2))
        inner = trimesh.creation.box(extents=(.1, .8, .8))
        inner.invert()
        body = trimesh.util.concatenate((outer, inner))
        domain = pf.Domain(body.vertices, body.faces)
        self.assertFalse(domain.segments_inside([[-.5, 0, 0]], [[.5, 0, 0]], .02)[0])
        self.assertTrue(domain.segments_inside([[-.5, .6, 0]], [[.5, .6, 0]], .02)[0])

    def test_seed_is_reproducible_and_changes_the_paths(self):
        body = trimesh.creation.icosphere(subdivisions=2)
        c1, _, m1, _, _ = packed(body, seed=12)
        c2, _, m2, _, _ = packed(body, seed=12)
        _, _, m3, _, _ = packed(body, seed=13)
        np.testing.assert_array_equal(m1.vertices, m2.vertices)
        np.testing.assert_array_equal(m1.faces, m2.faces)
        self.assertNotEqual(m1.identifier_hash, m3.identifier_hash)

    def test_segment_distances_include_crossings_parallel_and_endpoints(self):
        d = pf.segment_distance(np.array([[0,0,0]]*3, float), np.array([[1,0,0]]*3, float),
                                np.array([[.5,-1,0], [0,1,0], [2,0,0]], float),
                                np.array([[.5,1,0], [1,1,0], [3,0,0]], float))
        np.testing.assert_allclose(d, [0, 1, 1])

    def test_preflight_budgets_and_impossible_dimensions(self):
        body = trimesh.creation.box(extents=(2, 2, 2))
        domain = pf.Domain(body.vertices, body.faces)
        for updates, message in ((dict(spacing=2., diameter=5.), 'Spacing'),
                                 (dict(mode='orthogonal', spacing=6., diameter=3., bend=15.), 'Elbows'),
                                 (dict(spacing=2., diameter=.3, max_sites=500), 'site budget'),
                                 (dict(spacing=4., diameter=1., max_faces=20000), 'face budget')):
            with self.subTest(updates=updates), self.assertRaisesRegex(ValueError, message):
                pf.pack(domain, TOOL.values(updates), lambda *_: None, lambda: None)
        with patch.object(pf, 'MAX_GRID', 10), self.assertRaisesRegex(ValueError, 'grid'):
            pf.lattice(domain, .1, .01, 0., np.random.default_rng(1), 10000, lambda *_: None)
        from types import SimpleNamespace
        enormous = SimpleNamespace(v=np.array([[0., 0., 0.], [1e12, 2., 2.]]))
        with patch.object(pf.np, 'arange', side_effect=AssertionError('allocated before preflight')):
            with self.assertRaisesRegex(ValueError, 'grid'):
                pf.lattice(enormous, .1, .01, 0., np.random.default_rng(1), 10000, lambda *_: None)

    def test_organic_and_orthogonal_have_distinct_direction_distributions(self):
        body = trimesh.creation.box(extents=(1.6, 1.6, 2.))
        fractions = []
        for mode, straightness in (('organic', 10.), ('orthogonal', 90.)):
            curves, _, _, _, _ = packed(body, mode=mode, straightness=straightness)
            tangents = np.concatenate([b-a for a, b in map(pf.segments, curves)])
            tangents /= np.linalg.norm(tangents, axis=1)[:, None]
            fractions.append(np.mean(np.abs(tangents).max(axis=1) > .9999))
        self.assertLess(fractions[0], fractions[1]*.7)


class PipeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='atlas_pipes_')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.src = self.folder/'body.stl'
        body = trimesh.creation.box(extents=(16, 14, 20))
        body.apply_translation((100, -50, 220))
        body.export(self.src)
        self.values = TOOL.values(dict(TOOL.test_values, mode='orthogonal'))

    def test_contract_and_signed_exports_keep_coordinates(self):
        self.assertEqual(validate(registry().get(TOOL.id)), [])
        for ext in ('.stl', '.obj', '.ply'):
            with self.subTest(ext=ext):
                dst = self.folder/('filled'+ext)
                result = run(context(), self.src, dst, self.values)
                saved = gm.load_mesh(dst)
                self.assertEqual(result.outputs, [str(dst)])
                self.assertIn(TOOL.signature.encode(), dst.read_bytes()[:8192])
                self.assertTrue(saved.is_watertight)
                self.assertTrue(saved.is_winding_consistent)
                self.assertGreater(saved.volume, 0)
                self.assertTrue(np.all(saved.bounds[0] > [92, -57, 210]))
                self.assertTrue(np.all(saved.bounds[1] < [108, -43, 230]))
                from atlas_toolkit.geom.surface import read_frame
                axes = read_frame(dst)
                self.assertEqual(axes['up'], '+z')
                self.assertEqual(axes['front'], '-y')
                self.assertEqual([v['title'] for v in result.preview['views']], ['Output'])

    def test_preview_equals_final_and_writes_nothing(self):
        dst = self.folder/'out.stl'
        a = run(context('draft'), self.src, dst, self.values)
        self.assertFalse(dst.exists())
        self.assertEqual(a.outputs, [])
        b = run(context(), self.src, dst, self.values)
        av, bv = a.preview['views'][0]['mesh'], b.preview['views'][0]['mesh']
        np.testing.assert_array_equal(av.vertices, bv.vertices)
        np.testing.assert_array_equal(av.faces, bv.faces)

    def test_large_display_proxy_keeps_final_face_count(self):
        p = TOOL.values(dict(spacing=7., diameter=2., sides=24, samples=8))
        result = run(context('analyse'), self.src, None, p)
        view = result.preview['views'][0]
        total = result.stat('faces').value
        self.assertGreater(total, 300000)
        self.assertEqual(view['mesh'].faces_total, total)
        self.assertLess(len(view['mesh'].faces), total)
        self.assertIn('Display proxy', view['status'])

    def test_open_mesh_is_rejected_unless_explicitly_repaired(self):
        body = gm.load_mesh(self.src)
        body.update_faces(np.arange(len(body.faces)) != 0)
        body.export(self.src)
        with self.assertRaisesRegex(ValueError, 'closed'):
            run(context(), self.src, None, self.values)
        result = run(context(), self.src, None, TOOL.values(dict(self.values, repair=True, repair_resolution=48)))
        self.assertTrue(result.data['repaired'])

    def test_cancel_and_failed_publication_preserve_existing_output(self):
        dst = self.folder/'out.stl'
        dst.write_bytes(b'previous file')
        with self.assertRaises(Cancelled):
            run(context(cancelled=lambda: True), self.src, dst, self.values)
        self.assertEqual(dst.read_bytes(), b'previous file')
        with patch('atlas_toolkit.tools.pipe_fill.engine.validate_saved', side_effect=ValueError('bad saved copy')):
            with self.assertRaisesRegex(ValueError, 'saved copy'):
                run(context(), self.src, dst, self.values)
        self.assertEqual(dst.read_bytes(), b'previous file')
        self.assertEqual(list(self.folder.glob('.pipe-fill-*')), [])

    def test_replacing_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'replace'):
            run(context(), self.src, self.src, self.values)

    def test_workflow_draws_and_reuses_cached_pipes(self):
        from atlas_toolkit.core import workflow as w
        reg = registry()
        wf = w.Workflow(name='Pipes for the plotter')
        inp = wf.add(w.input_node('mesh'))
        fill = wf.add(w.tool_node(reg.get(TOOL.id), values=self.values))
        draw = wf.add(w.tool_node(reg.get('plotter-vision'), values=dict(flat_angle=35., crossings=False,
                                                                       decimate=False, min_stroke=.1, paper='A4')))
        save = wf.add(w.save_node(where='folder', folder=str(self.folder/'art')))
        for a, b in zip((inp, fill, draw), (fill, draw, save)):
            wf.connect(a.id, b.id)
        wf = w.Workflow.from_dict(wf.to_dict())
        self.assertEqual(w.errors(w.check(wf, reg)), [])
        cache = w.WorkflowCache(self.folder/'cache')
        events = []
        result = w.run_workflow(wf, self.src, lambda k, p: events.append((k, p)), lambda: False,
                                reg=reg, cache=cache, tool_cache=str(self.folder/'toolcache'))
        self.assertEqual(len(result.outputs), 1)
        svg = Path(result.outputs[0])
        self.assertEqual(svg.suffix, '.svg')
        self.assertIn(b'plotter-vision', svg.read_bytes())
        self.assertIn(b'<path', svg.read_bytes())
        events.clear()
        w.run_workflow(wf, self.src, lambda k, p: events.append((k, p)), lambda: False,
                       reg=reg, cache=cache, tool_cache=str(self.folder/'toolcache'))
        cached = {p[0] for k, p in events if k == 'node' and p[1] == 'cached'}
        self.assertEqual(cached, {fill.id, draw.id})


if __name__ == '__main__':
    unittest.main()
