"""Reflection accuracy, handedness, source preservation, unjoined copies and full contract integration."""
import _isolate  # first: settings and caches stay in a temporary folder
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from atlas_toolkit.core.job import Cancelled, JobContext
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.plane import plane, reflect_points
from atlas_toolkit.tools.mesh_mirror import TOOL
from atlas_toolkit.tools.mesh_mirror.engine import run
from atlas_toolkit.tools.mesh_mirror.mesh_mirror import combine, mirror


def context(cancelled=lambda:False):
    return JobContext(lambda *_:None,cancelled)


class MirrorGeometryTests(unittest.TestCase):
    def setUp(self):
        self.mesh=trimesh.creation.icosphere(subdivisions=2,radius=3)
        self.mesh.apply_scale((.7,1.1,1.5))
        self.mesh.apply_translation((11,-4,7))

    def test_default_plane_produces_an_adjacent_copy(self):
        reflected,(origin,normal)=mirror(self.mesh,TOOL.defaults())
        self.assertAlmostEqual(origin[0],self.mesh.bounds[1,0])
        np.testing.assert_allclose(normal,(1,0,0),atol=1e-12)
        self.assertAlmostEqual(reflected.bounds[0,0],self.mesh.bounds[1,0])
        np.testing.assert_allclose(reflected.vertices[:,0],2*self.mesh.bounds[1,0]-self.mesh.vertices[:,0])
        np.testing.assert_allclose(reflected.vertices[:,1:],self.mesh.vertices[:,1:],atol=1e-12)

    def test_arbitrary_planes_preserve_shape_volume_area_and_winding(self):
        vertices,faces=self.mesh.vertices.copy(),self.mesh.faces.copy()
        for rotation in ((0,0,0),(27,53,-19),(90,0,0),(-90,-45,180)):
            p=TOOL.values(dict(reference='world',position=(2,9,-4),offset=1.25,rotation=rotation))
            reflected,(origin,normal)=mirror(self.mesh,p)
            before=(self.mesh.vertices-origin)@normal
            after=(reflected.vertices-origin)@normal
            np.testing.assert_allclose(after,-before,atol=1e-12)
            np.testing.assert_allclose((reflected.vertices+self.mesh.vertices)*.5,
                                       self.mesh.vertices-before[:,None]*normal,atol=1e-12)
            self.assertAlmostEqual(reflected.area,self.mesh.area,places=9)
            self.assertAlmostEqual(reflected.volume,self.mesh.volume,places=9)
            self.assertTrue(reflected.is_watertight)
            self.assertTrue(reflected.is_winding_consistent)
            expected=self.mesh.face_normals-2*(self.mesh.face_normals@normal)[:,None]*normal
            np.testing.assert_allclose(reflected.face_normals,expected,atol=1e-12)
            np.testing.assert_array_equal(reflected.faces,faces[:,::-1])
        np.testing.assert_array_equal(self.mesh.vertices,vertices)
        np.testing.assert_array_equal(self.mesh.faces,faces)

    def test_reflection_twice_about_a_fixed_plane_is_identity(self):
        p=TOOL.values(dict(reference='world',position=(11,-6,9),rotation=(18,-33,44),offset=2))
        reflected,_=mirror(self.mesh,p)
        restored,_=mirror(reflected,p)
        np.testing.assert_allclose(restored.vertices,self.mesh.vertices,atol=1e-12)
        np.testing.assert_array_equal(restored.faces,self.mesh.faces)

    def test_centre_world_side_references_and_offset(self):
        for reference,expected in [('center',self.mesh.bounds.mean(axis=0)),('world',np.zeros(3)),
                                  ('side',np.r_[self.mesh.bounds[1,0],self.mesh.bounds.mean(axis=0)[1:]])]:
            p=TOOL.values(dict(reference=reference,position=(1,2,3),rotation=(0,90,0),offset=4))
            origin,normal,_=plane(self.mesh.bounds,p)
            np.testing.assert_allclose(origin,expected+[5,2,3],atol=1e-12)
            point=origin+[0,7,-2]
            np.testing.assert_allclose(reflect_points([point],origin,normal)[0],point,atol=1e-12)
        p=TOOL.values(dict(reference='world',rotation=(11,22,33)))
        a,(origin,normal)=mirror(self.mesh,p)
        np.testing.assert_allclose(reflect_points(self.mesh.vertices,origin,-normal),a.vertices,atol=1e-12)

    def test_overlapping_and_touching_copies_are_not_welded_or_unionized(self):
        for reference in ('side','center'):
            reflected,_=mirror(self.mesh,TOOL.values(dict(reference=reference)))
            result=combine(self.mesh,reflected)
            self.assertEqual(len(result.faces),2*len(self.mesh.faces))
            self.assertEqual(len(result.vertices),2*len(self.mesh.vertices))
            np.testing.assert_array_equal(result.vertices[:len(self.mesh.vertices)],self.mesh.vertices)
            self.assertEqual(len(result.split(only_watertight=False)),2)
            self.assertAlmostEqual(result.volume,self.mesh.volume*2,places=9)

    def test_open_and_disconnected_meshes_keep_their_topology(self):
        self.mesh.update_faces(np.arange(len(self.mesh.faces))!=0)
        reflected,_=mirror(self.mesh,TOOL.defaults())
        self.assertFalse(reflected.is_watertight)
        self.assertEqual(len(reflected.faces),len(self.mesh.faces))
        other=self.mesh.copy();other.apply_translation((30,0,0))
        source=trimesh.util.concatenate((self.mesh,other))
        reflected,_=mirror(source,TOOL.defaults())
        self.assertEqual(len(reflected.split(only_watertight=False)),2)
        self.assertEqual(len(reflected.faces),len(source.faces))


class MirrorEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='atlas_mirror_')
        self.folder=Path(self.temp.name)
        self.source=self.folder/'input.stl'
        mesh=trimesh.creation.box(extents=(4,6,8));mesh.apply_translation((10,-2,3))
        mesh.export(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_is_non_writing_and_mirror_only_drops_original(self):
        initial=self.source.read_bytes()
        for mode,factor in [('copy',2),('mirror',1)]:
            result=run(context(),self.source,None,TOOL.values(dict(result_mode=mode)))
            self.assertFalse(result.outputs)
            self.assertEqual(result.preview['views'][0]['mesh'].faces_total,12*factor)
            self.assertEqual(result.stat('copies').value,factor)
        self.assertEqual(list(self.folder.iterdir()),[self.source])
        self.assertEqual(self.source.read_bytes(),initial)

    def test_all_formats_signed_correctly_and_original_file_unchanged(self):
        initial=self.source.read_bytes()
        for extension in ('stl','obj','ply'):
            for mode in ('copy','mirror'):
                dst=self.folder/f'{mode}.{extension}'
                result=run(context(),self.source,dst,TOOL.values(dict(result_mode=mode,export_mode='separate')))
                self.assertEqual(len(result.outputs),2 if mode=='copy' else 1)
                for path in result.outputs:
                    mesh=gm.load_mesh(path)
                    self.assertTrue(mesh.is_watertight)
                    self.assertGreater(mesh.volume,0)
                    self.assertIn(TOOL.signature+' '+TOOL.version,gm.read_signature(path))
                reflected=gm.load_mesh(result.outputs[0])
                self.assertAlmostEqual(reflected.bounds[0,0],12)
        self.assertEqual(self.source.read_bytes(),initial)

    def test_combined_output_retains_twice_the_faces(self):
        result=run(context(),self.source,self.folder/'combined.obj',TOOL.defaults())
        self.assertEqual(len(result.outputs),1)
        raw=trimesh.load(result.outputs[0],process=False)
        self.assertEqual(len(raw.faces),24)
        self.assertEqual(len(raw.vertices),16)

    def test_collision_cancellation_and_publish_failure_preserve_existing_files(self):
        dst=self.folder/'result.stl';other=self.folder/'result_original.stl'
        dst.write_bytes(b'existing primary');other.write_bytes(b'existing extra')
        p=TOOL.values(dict(export_mode='separate'))
        with self.assertRaises(Cancelled):
            run(context(lambda:True),self.source,dst,p)
        with patch('atlas_toolkit.geom.mesh_export.os.replace',side_effect=OSError('locked')):
            with self.assertRaises(OSError):
                run(context(),self.source,dst,p)
        self.assertEqual(dst.read_bytes(),b'existing primary')
        self.assertEqual(other.read_bytes(),b'existing extra')
        self.assertFalse(list(self.folder.glob('.mesh-mirror-*')))
        result=run(context(),self.source,dst,p)
        self.assertEqual(Path(result.outputs[1]).name,'result_original-1.stl')
        self.assertEqual(other.read_bytes(),b'existing extra')

    def test_workflow_cache_save_and_chaining_use_the_mirrored_primary(self):
        from atlas_toolkit.core import workflow as W
        from atlas_toolkit.core.registry import registry
        reg=registry();wf=W.Workflow(name='Mirror model')
        source=wf.add(W.input_node('mesh'))
        node=wf.add(W.tool_node(reg.get(TOOL.id),values=dict(export_mode='separate')))
        save=wf.add(W.save_node(where='folder',folder=str(self.folder/'out')))
        wf.connect(source.id,node.id);wf.connect(node.id,save.id)
        self.assertEqual(W.errors(W.check(wf,reg)),[])
        cache=W.WorkflowCache(self.folder/'cache')
        for _ in range(2):
            result=W.run_workflow(wf,self.source,lambda *_:None,lambda:False,reg=reg,cache=cache)
            self.assertEqual(len(result.outputs),2)
            self.assertTrue(all(Path(path).is_file() for path in result.outputs))
            self.assertAlmostEqual(gm.load_mesh(result.outputs[0]).bounds[0,0],12)
        # A following Plane Cut gets the reflected main mesh, and the reusable contracts need no special ports.
        cut=wf.add(W.tool_node(reg.get('plane-cut'),values=dict(fill='minimal')))
        wf.connect(node.id,cut.id);wf.connect(cut.id,save.id)
        result=W.run_workflow(wf,self.source,lambda *_:None,lambda:False,reg=reg,cache=cache)
        self.assertTrue(gm.load_mesh(result.outputs[0]).is_watertight)
        self.assertGreaterEqual(gm.load_mesh(result.outputs[0]).bounds[0,0],12)


if __name__=='__main__':
    unittest.main()
