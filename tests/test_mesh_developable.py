"""Numerical and integration checks for the headless research solver."""
import _isolate  # first: settings and caches stay in a temporary folder
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import trimesh
from atlas_toolkit.core.registry import registry
from atlas_toolkit.core.job import JobContext, Cancelled
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.mesh_developable import backend, TOOL
from atlas_toolkit.tools.mesh_developable import mesh_developable as alg
from atlas_toolkit.tools.mesh_developable.engine import run, make_sample


@_isolate.needs_helper(backend.find_backend(), 'developable helper missing')
class DevelopableTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='atlas_developable_test_')
        self.root=Path(self.tmp.name)
        self.mesh=trimesh.creation.icosphere(subdivisions=1)
        self.mesh.vertices *= [0.75,0.9,1.3]
        self.settings=dict(TOOL.values(TOOL.test_values))
    def tearDown(self):
        self.tmp.cleanup()
    def native(self, mesh, **settings):
        src=self.root/'native.obj'; config=self.root/'settings.json';out=self.root/'output.obj';report=self.root/'report.json'
        src.write_text(trimesh.exchange.obj.export_obj(mesh,digits=17,include_normals=False),encoding='utf-8')
        config.write_text(json.dumps(dict(self.settings,**settings)),encoding='utf-8')
        data=backend.run_native(backend.find_backend(),src,config,out,report)
        return trimesh.load_mesh(out,process=False),data
    def test_analytic_gradients(self):
        # Directional derivatives on a perturbed, non-symmetric ellipsoid away from eigenvalue ties.
        rng=np.random.default_rng(23)
        self.mesh.vertices += rng.normal(0,0.015,self.mesh.vertices.shape)
        d=rng.normal(size=self.mesh.vertices.shape);d/=np.linalg.norm(d)
        for kind in ('covariance','max-covariance','combinatorial'):
            with self.subTest(kind=kind):
                _,r=self.native(self.mesh,energy=kind,evaluate_only=True)
                g=np.asarray(r['gradient']);eps=1e-6
                plus=self.mesh.copy();plus.vertices += eps*d
                minus=self.mesh.copy();minus.vertices -= eps*d
                _,a=self.native(plus,energy=kind,evaluate_only=True)
                _,b=self.native(minus,energy=kind,evaluate_only=True)
                fd=(a['energy']-b['energy'])/(2*eps)
                self.assertAlmostEqual(fd,float(np.sum(g*d)),delta=1e-4*max(1,abs(fd)))
    def test_plane_and_cylinder_are_developable(self):
        for cylinder in (False,True):
            v=np.array([[x,y,0.] for y in range(3) for x in range(4)],float)
            if cylinder:
                v=np.array([[np.cos(x*.3),np.sin(x*.3),y] for y in range(3) for x in range(4)])
            f=[]
            for y in range(2):
                for x in range(3):
                    a=y*4+x;f.extend([[a,a+1,a+4],[a+1,a+5,a+4]])
            m=trimesh.Trimesh(v,f,process=False)
            for energy in ('covariance','max-covariance','combinatorial'):
                with self.subTest(cylinder=cylinder,energy=energy):
                    _,r=self.native(m,energy=energy,evaluate_only=True)
                    self.assertLess(abs(r['energy']),1e-10)
                    self.assertTrue(np.isfinite(r['gradient']).all())
    def test_energy_decreases_and_solves_do_not_share_state(self):
        for energy in ('covariance','max-covariance','combinatorial'):
            a,r=self.native(self.mesh,energy=energy,iterations=30)
            self.assertLess(r['levels'][0]['final_energy'],r['levels'][0]['initial_energy'])
            self.native(trimesh.creation.icosphere(subdivisions=0),energy=energy)
            b,_=self.native(self.mesh,energy=energy,iterations=30)
            np.testing.assert_array_equal(a.vertices,b.vertices)
    def test_subdivision_fixes_old_and_new_boundaries(self):
        m=self.mesh.copy();m.update_faces(m.triangles_center[:,2]>-0.3);m.remove_unreferenced_vertices()
        ids,_=alg.boundary_info(m)
        out,r=self.native(m,subdivisions=1,iterations=10)
        np.testing.assert_allclose(out.vertices[ids],m.vertices[ids],atol=1e-14)
        edges=m.edges_unique[np.bincount(m.edges_unique_inverse)==1]
        expected=np.vstack([m.vertices[ids],m.vertices[edges].mean(axis=1)])
        from scipy.spatial import cKDTree
        actual=out.vertices[alg.boundary_info(out)[0]]
        self.assertLess(cKDTree(actual).query(expected)[0].max(),1e-13)
        self.assertEqual(alg.signature(out),alg.signature(m))
    def test_budget_invalid_inputs_and_missing_helper(self):
        with self.assertRaisesRegex(RuntimeError,'budget'):
            self.native(self.mesh,subdivisions=4,max_faces=1000)
        bad=self.mesh.copy();bad.vertices[0]=np.nan
        with self.assertRaisesRegex(ValueError,'finite'):
            alg.validate_mesh(bad)
        a=trimesh.Trimesh([[0,0,0],[1,0,0],[0,1,0],[-1,0,0],[0,-1,0]],[[0,1,2],[0,3,4]],process=False)
        with self.assertRaisesRegex(ValueError,'Non-manifold'):
            alg.validate_mesh(a)
        with patch.dict(os.environ,{backend.ENV:str(self.root/'missing.exe')}):
            with self.assertRaisesRegex(RuntimeError,'missing'):
                alg.develop(self.mesh,self.root,self.settings)
        with self.assertRaisesRegex(ValueError,'incompatible'):
            backend.validate({'schema':99})
    def test_components_units_cache_and_corruption(self):
        a=self.mesh.copy();a.vertices=a.vertices*70+[500,-200,30]
        b=self.mesh.copy();b.vertices=b.vertices*3+[-20,10,5]
        source=trimesh.util.concatenate([a,b]);cache=self.root/'cache'
        out,r=alg.develop(source,cache,self.settings)
        self.assertEqual(alg.signature(source),alg.signature(out))
        for original,component in zip(source.split(repair=False),out.split(repair=False)):
            self.assertLess(np.linalg.norm(original.centroid-component.centroid),np.linalg.norm(original.extents)*.1)
        again,r2=alg.develop(source,cache,self.settings)
        self.assertTrue(r2['cache_hit']);np.testing.assert_array_equal(out.vertices,again.vertices)
        next(cache.glob('*.npz')).write_bytes(b'corrupt')
        _,r3=alg.develop(source,cache,self.settings);self.assertFalse(r3['cache_hit'])
    def test_remesh_preserves_open_and_closed_topology(self):
        for opened in (False,True):
            m=self.mesh.copy()
            if opened:
                m.update_faces(m.triangles_center[:,2]>-.3);m.remove_unreferenced_vertices()
            out=alg.prepare(m,'remesh',200)
            self.assertEqual(alg.signature(m),alg.signature(out))
    def test_cancel_dry_run_and_signed_formats(self):
        src=make_sample(self.root)
        ctx=JobContext(lambda *a:None,lambda:False,cache_dir=str(self.root/'cache'))
        tool=registry().get('mesh-developable');p=tool.values(tool.test_values)
        for ext in ('.stl','.obj','.ply'):
            dst=self.root/('signed'+ext)
            r=run(ctx,src,dst,p)
            self.assertIn('atlas-toolkit:mesh-developable',gm.read_signature(dst))
            self.assertEqual(r.outputs,[str(dst)])
        dst=self.root/'not-written.stl'
        dry=JobContext(lambda *a:None,lambda:False,mode='analyse',cache_dir=str(self.root/'cache'))
        run(dry,src,dst,p);self.assertFalse(dst.exists())
        before=dst.read_bytes() if dst.exists() else None
        cancelled=JobContext(lambda *a:None,lambda:True,cache_dir=str(self.root/'cache'))
        with self.assertRaises(Cancelled):run(cancelled,src,dst,p)
        self.assertEqual(dst.read_bytes() if dst.exists() else None,before)
    def test_invalid_field_remesh_uses_validated_fallback(self):
        with patch('pynanoinstantmeshes.remesh', return_value=(np.zeros((3,3)),np.array([[0,1,2]],dtype=np.uint32))):
            out=alg.prepare(self.mesh,'remesh',60)
            self.assertEqual(alg.signature(out),alg.signature(self.mesh))
            self.assertIn('fallback',out.metadata['preparation'])

    def test_workflow_examples_roundtrip_run_branch_and_cache(self):
        from atlas_toolkit.core import workflow as W
        reg=registry()
        for style in ('rulings','contours'):
            wf=W.load(Path(__file__).resolve().parents[1]/'docs/workflows'/('developable_'+style+'.atlasflow'))
            for node in wf.nodes.values():
                if node.type==W.TOOL and node.tool==TOOL.id:
                    node.values.update(self.settings)
                if node.type==W.SAVE:
                    node.where='folder';node.folder=str(self.root/style)
            self.assertFalse([i for i in W.check(wf,reg) if i.level=='error'])
            saved=W.save(wf,self.root/('roundtrip_'+style+'.atlasflow'));wf=W.load(saved)
            source=make_sample(self.root);cache=W.WorkflowCache(self.root/('wf-cache-'+style))
            events=[]
            r=W.run_workflow(wf,source,lambda k,p:events.append((k,p)),lambda:False,reg=reg,
                             cache=cache,tool_cache=str(self.root/'tool-cache'))
            self.assertEqual(len(r.outputs),2)
            self.assertEqual({Path(x).suffix for x in r.outputs},{'.svg','.stl'})
            events.clear()
            W.run_workflow(wf,source,lambda k,p:events.append((k,p)),lambda:False,reg=reg,
                           cache=cache,tool_cache=str(self.root/'tool-cache'))
            self.assertTrue(any(k=='node' and p[1]=='cached' for k,p in events))

    def test_native_cancellation_and_failed_output_are_safe(self):
        src=self.root/'cancel.obj';self.mesh.export(src)
        config=self.root/'settings.json';config.write_text(json.dumps(dict(self.settings,iterations=10000)))
        checks=[0]
        def check():
            checks[0]+=1
            if checks[0]>2:raise Cancelled()
        with self.assertRaises(Cancelled):
            backend.run_native(backend.find_backend(),src,config,self.root/'out.obj',self.root/'report.json',check=check)
        config.write_text('{invalid')
        with self.assertRaisesRegex(RuntimeError,'solver failed'):
            backend.run_native(backend.find_backend(),src,config,self.root/'out.obj',self.root/'report.json')

if __name__=='__main__':unittest.main()
