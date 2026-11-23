"""Surface topology, calibrated fields, native charts and Atlas adapter regressions."""
import _isolate  # first: settings and caches stay in a temporary folder
import hashlib
import json
from pathlib import Path
import tempfile
import shutil
import time
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
import trimesh

from atlas_toolkit.core import Cancelled, JobContext
from atlas_toolkit.tools.mesh_texture import TOOL, MATERIALS
from atlas_toolkit.tools.mesh_texture import backend, engine
from atlas_toolkit.tools.mesh_texture import mesh_texture as mt


def context():
    return JobContext(lambda *a:None,lambda:False)


class TextureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='atlas-texture-test-')
        self.folder=Path(self.tmp.name)
        self.cache_patch=patch.object(backend,'cache_root',return_value=self.folder/'cache')
        self.cache_patch.start()
        self.mesh=trimesh.creation.icosphere(subdivisions=1)
        self.p=dict(TOOL.values(TOOL.test_values))

    def tearDown(self):
        self.cache_patch.stop()
        self.tmp.cleanup()

    def custom(self,a):
        path=self.folder/'height.png'
        Image.fromarray(a).save(path)
        self.p.update(source='custom',heightmap=str(path),contrast=1.,crispness=0.,suppression=0.)
        return path

    def build(self,**values):
        return mt.build(self.mesh.vertices,self.mesh.faces,dict(self.p,**values),context())

    def test_catalog_has_twelve_real_maps_with_provenance(self):
        cat=mt.catalog()
        self.assertEqual({t['id'] for t in cat['textures']},{k for k,_ in MATERIALS})
        for t in cat['textures']:
            self.assertEqual(hashlib.sha256((mt.ASSETS/t['file']).read_bytes()).hexdigest(),t['sha256'])
            a,alpha=mt.read_height(mt.ASSETS/t['file'])
            self.assertGreater(float(a.std()),.0001)
            self.assertEqual(t['license'],'CC0-1.0')
            self.assertTrue(t['source_sha256'] and t['download_url'] and t['preparation'])

    def test_constant_16bit_calibration_and_modes(self):
        self.custom(np.full((8,8),32768,np.uint16))
        a,_,_,_=mt.prepare(self.p,context())
        np.testing.assert_allclose(a,32768/65535)
        for mode,want in [('raised',.5),('engraved',-.5),('balanced',0.)]:
            p=dict(self.p,relief_mode=mode,mapping='triplanar')
            field=mt.Field(p,self.mesh.vertices,self.mesh.vertex_normals,a,np.ones_like(a),1)
            result=field.evaluate(self.mesh.vertices,self.mesh.vertex_normals)
            np.testing.assert_allclose(result,want*self.p['relief']/100*2,atol=2e-7)

    def test_zero_relief_is_exact_even_without_heightmap_or_backend(self):
        original=self.mesh.vertices.copy()
        with patch.object(backend,'find_backend',return_value=None):
            result,_=self.build(relief=0,source='custom',heightmap='',mapping='surface')
        np.testing.assert_array_equal(result.vertices,original)
        np.testing.assert_array_equal(result.faces,self.mesh.faces)

    def test_three_mappings_preserve_closed_topology_and_are_repeatable(self):
        for mapping in ('surface','cylindrical','triplanar'):
            first,_=self.build(mapping=mapping)
            second,_=self.build(mapping=mapping)
            self.assertTrue(first.is_watertight and first.is_winding_consistent)
            self.assertTrue(first.nondegenerate_faces().all())
            self.assertGreater(len(first.faces),len(self.mesh.faces))
            np.testing.assert_array_equal(first.vertices,second.vertices)
            np.testing.assert_array_equal(first.faces,second.faces)

    def test_native_uv_cache_is_independent_of_relief_and_region(self):
        uv,chart,hit=backend.unwrap(self.mesh.vertices,self.mesh.faces,context())
        self.assertFalse(hit)
        with patch.object(backend.subprocess,'Popen',side_effect=AssertionError('cache missed')):
            uv2,chart2,hit=backend.unwrap(self.mesh.vertices,self.mesh.faces,context())
        self.assertTrue(hit)
        np.testing.assert_array_equal(uv,uv2)
        np.testing.assert_array_equal(chart,chart2)

    def test_native_unwrap_does_not_depend_on_the_models_units(self):
        """xatlas's limits are absolute: a large model asked for an enormous atlas (minutes, then a crash), a tiny one lost faces."""
        sphere=trimesh.creation.icosphere(subdivisions=5)
        started=time.perf_counter()
        for size,offset in ((1000.,5000.),(1.,0.),(1e-3,0.)):
            mesh=trimesh.Trimesh(sphere.vertices*size+offset,sphere.faces,process=False)
            with self.subTest(size=size):
                uv,chart,_=backend.unwrap(mesh.vertices,mesh.faces,context())
                self.assertTrue((chart>=0).all())
                # UVs stay in model units: each triangle keeps its edge lengths, within the charts' small distortion.
                lengths=np.linalg.norm(mesh.vertices[mesh.faces]-mesh.vertices[mesh.faces[:,[1,2,0]]],axis=2)
                flat=np.linalg.norm(uv-uv[:,[1,2,0]],axis=2)
                self.assertAlmostEqual(float(np.median(flat/lengths)),1.,delta=.1)
        self.assertLess(time.perf_counter()-started,60)

    def test_native_unwrap_cancellation_does_not_publish_a_cache(self):
        cancelled=[False]
        def emit(kind,payload):
            if kind=='progress':
                cancelled[0]=True
        ctx=JobContext(emit,lambda:cancelled[0])
        with self.assertRaises(Cancelled):
            backend.unwrap(self.mesh.vertices,self.mesh.faces,ctx)
        self.assertFalse(list((self.folder/'cache').glob('*.npz')))

    def test_triplanar_error_refinement_stays_nondegenerate(self):
        result,_=self.build(texture='rock',mapping='triplanar',element_size=18.,detail_tolerance=20.,max_faces=100000)
        self.assertTrue(result.is_watertight)
        self.assertTrue(result.nondegenerate_faces().all())
        self.assertGreater(result.area_faces.min(),1e-12)

    def test_reversed_closed_source_is_displaced_outward(self):
        self.custom(np.full((8,8),65535,np.uint16))
        self.mesh.invert()
        result,_=self.build(mapping='triplanar')
        moved=result.vertices[:len(self.mesh.vertices)]-self.mesh.vertices
        self.assertTrue(np.all(np.einsum('ij,ij->i',moved,self.mesh.vertices)>0))

    def test_band_and_inverse_do_not_move_excluded_source_vertices(self):
        for inverse in (False,True):
            p=dict(self.p,influence='band',band_from=40.,band_to=60.,feather=4.,invert_influence=inverse)
            result,_=mt.build(self.mesh.vertices,self.mesh.faces,p,context())
            from atlas_toolkit.geom.influence import weights
            fixed=weights(self.mesh.vertices,p,self.mesh.bounds[0],self.mesh.bounds[1])==0
            np.testing.assert_array_equal(result.vertices[:len(self.mesh.vertices)][fixed],self.mesh.vertices[fixed])

    def test_local_sphere_on_coarse_face_is_refined(self):
        self.mesh=trimesh.Trimesh([[-1,-1,0],[1,-1,0],[0,1,0]],[[0,1,2]],process=False)
        result,_=self.build(influence='sphere',sphere_centre=(0,0,0),sphere_radius=30.,feather=5.,preserve_borders=False)
        self.assertGreater(len(result.faces),1)
        self.assertGreater(np.max(np.abs(result.vertices[:,2])),0.)

    def test_open_boundary_and_disconnected_parts(self):
        openmesh=trimesh.Trimesh([[-1,-1,-1],[1,-1,-1],[1,1,1],[-1,1,1]],[[0,1,2],[0,2,3]],process=False)
        self.mesh=openmesh
        result,_=self.build()
        np.testing.assert_array_equal(result.vertices[:4],openmesh.vertices)
        self.assertFalse(result.is_watertight)
        self.mesh=trimesh.util.concatenate([trimesh.creation.icosphere(subdivisions=0),trimesh.creation.icosphere(subdivisions=0).apply_translation([3,0,0])])
        result,_=self.build(element_size=30.)
        self.assertEqual(len(result.split()),2)

    def test_cylindrical_seam_is_periodic_with_rotation(self):
        self.custom(np.tile(np.arange(32,dtype=np.uint16)*2048,(32,1)))
        a,alpha,elems,_=mt.prepare(self.p,context())
        for angle in (0,25,90,180):
            field=mt.Field(dict(self.p,texture_rotation=angle),self.mesh.vertices,self.mesh.vertex_normals,a,alpha,elems)
            points=np.array([[1e-10,.8,.3],[-1e-10,.8,.3]])
            values=field.evaluate(points,np.tile([0,1,0],(2,1)))
            self.assertAlmostEqual(values[0],values[1],places=8)

    def test_cylindrical_large_elements_do_not_plateau_at_a_full_tile(self):
        a,alpha,elems,_=mt.prepare(self.p,context())
        small=mt.Field(dict(self.p,element_size=10.),self.mesh.vertices,self.mesh.vertex_normals,a,alpha,elems)
        large=mt.Field(dict(self.p,element_size=30.),self.mesh.vertices,self.mesh.vertex_normals,a,alpha,elems)
        self.assertLess(large.repeats,small.repeats)
        self.assertLess(large.repeats,1.)

    def test_height_cache_invalidates_on_custom_file_edit(self):
        path=self.custom(np.full((8,8),10000,np.uint16))
        a,_,_,hit=mt.prepare(self.p,context()); self.assertFalse(hit)
        _,_,_,hit=mt.prepare(self.p,context()); self.assertTrue(hit)
        Image.fromarray(np.full((8,8),50000,np.uint16)).save(path)
        b,_,_,hit=mt.prepare(self.p,context()); self.assertFalse(hit)
        self.assertGreater(b.mean(),a.mean())
        deps=engine.input_dependencies(None,self.p)
        self.assertIn(path,deps)

    def test_workflow_dependency_identity_survives_frozen_asset_relocation(self):
        first=engine.input_dependencies(None,self.p)
        from atlas_toolkit.core.workflow import file_key
        original_keys=[file_key(path) for path in first]
        other=self.folder/'relocated'; other.mkdir()
        shutil.copy2(mt.ASSETS/'catalog.json',other/'catalog.json')
        shutil.copy2(mt.ASSETS/'scales.png',other/'scales.png')
        with patch.object(mt,'ASSETS',other):
            second=engine.input_dependencies(None,self.p)
            self.assertEqual([file_key(path) for path in second],original_keys)
            catalog=json.loads((other/'catalog.json').read_text()); catalog['revision']+=1
            (other/'catalog.json').write_text(json.dumps(catalog))
            changed=engine.input_dependencies(None,self.p)
            self.assertNotEqual([file_key(path) for path in changed],original_keys)

    def test_bad_maps_alpha_and_float_tiff(self):
        path=self.custom(np.full((8,8,3),[20,40,80],np.uint8))
        with self.assertRaisesRegex(ValueError,'grayscale'):
            mt.read_height(path)
        path.write_bytes(b'bad image')
        with self.assertRaisesRegex(ValueError,'decode'):
            mt.read_height(path)
        alpha=np.full((8,8,2),255,np.uint8); alpha[:,:,1]=0
        Image.fromarray(alpha,mode='LA').save(path)
        _,a=mt.read_height(path); self.assertEqual(a.max(),0)
        tiff=self.folder/'float.tif'
        Image.fromarray(np.full((8,8),.25,np.float32)).save(tiff)
        h,_=mt.read_height(tiff); np.testing.assert_allclose(h,.25)
        Image.fromarray(np.full((8,8),np.nan,np.float32)).save(tiff)
        with self.assertRaisesRegex(ValueError,'finite'):
            mt.read_height(tiff)

    def test_the_face_budget_is_a_ceiling_not_a_failure(self):
        """0.1 raised as soon as a pass would pass Maximum output faces; now the worst faces that fit are refined."""
        warnings=[]
        def emit(kind,payload):
            if kind=='log' and payload[0]=='warning':
                warnings.append(payload[1])
        result,info=mt.build(self.mesh.vertices,self.mesh.faces,dict(self.p,element_size=.5,max_faces=1000),
                             JobContext(emit,lambda:False))
        self.assertLessEqual(len(result.faces),1000)
        self.assertGreater(len(result.faces),len(self.mesh.faces))
        self.assertTrue(info['budget_reached'])
        self.assertTrue(result.is_watertight)
        self.assertTrue(any('Face budget reached' in w for w in warnings))
        # A source already at the budget is textured on its own vertices.
        result,_=mt.build(self.mesh.vertices,self.mesh.faces,dict(self.p,max_faces=len(self.mesh.faces)),context())
        self.assertEqual(len(result.faces),len(self.mesh.faces))

    def test_cancellation_leaves_no_output(self):
        with self.assertRaises(Cancelled):
            mt.build(self.mesh.vertices,self.mesh.faces,self.p,JobContext(lambda *a:None,lambda:True))

    def test_detail_tolerance_is_a_share_of_the_relief(self):
        """Taller relief needs no more faces: in 0.1 the count grew in step with the relief and broke the budget."""
        self.mesh=trimesh.creation.icosphere(subdivisions=2)
        counts=[len(self.build(texture='rock',mapping='triplanar',element_size=12.,relief=r,max_faces=400000,
                               collision_protection=False)[0].faces) for r in (.4,2.)]
        self.assertEqual(counts[0],counts[1])

    def test_a_hard_step_converges_at_the_floor(self):
        """A discontinuous heightmap never meets the tolerance; refinement stops at element / 24 instead of chasing
        it (0.1: 'did not converge')."""
        step=np.zeros((32,32),np.uint16)
        step[:,16:]=65535
        self.custom(step)
        result,info=self.build(mapping='triplanar',element_size=20.,detail_tolerance=1.,max_faces=2000000,
                               collision_protection=False)
        self.assertFalse(info['budget_reached'])
        element=.2*float(np.ptp(self.mesh.vertices[:,2]))
        # Splits halve edges, so none is shorter than half the floor.
        self.assertGreater(result.edges_unique_length.min(),element/24/2*.5)

    def test_old_tolerance_settings_convert(self):
        from atlas_toolkit.core import presets as P
        from atlas_toolkit.core.registry import registry
        tool=registry().get('mesh-texture')
        self.assertAlmostEqual(tool.values({'tolerance':.04}).detail_tolerance,10.)
        old=P.Preset('Old','mesh-texture',{'tolerance':.08,'relief':.5,'max_faces':400000},tool_version='0.1.1')
        values,_=P.apply(tool,old)
        self.assertAlmostEqual(values.detail_tolerance,16.)
        # The face budget is the machine's, not the preset's: the default without a form, the form's with one.
        self.assertEqual(values.max_faces,tool.defaults()['max_faces'])
        values,_=P.apply(tool,old,current=dict(tool.defaults(),max_faces=250000))
        self.assertEqual(values.max_faces,250000)

    def crease(self):
        """An open V, its two sides 30 degrees apart, welded along the fold, facing into the V."""
        xs=np.linspace(0,1,41)
        ys=np.linspace(-1,1,81)
        X,Y=np.meshgrid(xs,ys)
        half=np.radians(15)
        sides=[np.column_stack((X.ravel()*np.cos(half),s*X.ravel()*np.sin(half),Y.ravel())) for s in (1,-1)]
        nx,ny=len(xs),len(ys)
        faces=[]
        for k in (0,1):
            for j in range(ny-1):
                for i in range(nx-1):
                    a,b=k*nx*ny+j*nx+i,k*nx*ny+j*nx+i+1
                    c,d=b+nx,a+nx
                    faces += [[a,b,c],[a,c,d]] if k==0 else [[a,c,b],[a,d,c]]
        return trimesh.Trimesh(np.vstack(sides),np.array(faces),process=True)

    def test_crease_restraint_is_local(self):
        """Raised relief in a tight V collides near its fold. Protection restrains it there, fading out, and leaves
        the rest at full height (0.1 scaled the whole relief down once contacts were many)."""
        self.custom(np.full((8,8),65535,np.uint16))
        m=self.crease()
        p=dict(self.p,mapping='triplanar',relief=3.,element_size=10.,preserve_borders=False,max_faces=200000)
        result,info=mt.build(m.vertices,m.faces,p,context())
        from atlas_toolkit.geom.contact import crossings
        self.assertEqual(len(crossings(result.vertices,result.faces)),0)
        self.assertGreater(info['limited'],0)
        self.assertLess(info['limited'],50)
        moved=np.linalg.norm(result.vertices[:len(m.vertices)]-m.vertices,axis=1)
        far=np.linalg.norm(m.vertices[:,:2],axis=1)>.5
        self.assertGreater(moved[far].min(),.03*2*.95)

    def test_a_stray_crossing_in_the_source_is_left_out_not_refused(self):
        """A scan with one fold through itself (the catalogue David head has one pair) is textured around it."""
        sliver=trimesh.Trimesh([[0,0,.6],[.15,0,1.4],[0,.15,1.4]],[[0,1,2]])
        self.mesh=trimesh.util.concatenate([trimesh.creation.icosphere(subdivisions=2),sliver])
        warnings=[]
        def emit(kind,payload):
            if kind=='log' and payload[0]=='warning':
                warnings.append(payload[1])
        result,info=mt.build(self.mesh.vertices,self.mesh.faces,dict(self.p,preserve_borders=False),JobContext(emit,lambda:False))
        self.assertTrue(any('intersecting' in w for w in warnings))
        self.assertGreater(info['relief'],0)
        # The fold is still there, and nothing else crosses.
        from atlas_toolkit.geom.contact import crossings
        self.assertTrue(len(crossings(result.vertices,result.faces)))
        moved=np.linalg.norm(result.vertices[:len(self.mesh.vertices)]-self.mesh.vertices,axis=1)
        self.assertEqual(float(moved[-3:].max()),0.)

    def test_protection_off_keeps_triangles_valid_instead_of_failing(self):
        self.custom(np.full((8,8),65535,np.uint16))
        m=self.crease()
        p=dict(self.p,mapping='triplanar',relief=5.,element_size=10.,preserve_borders=False,
               collision_protection=False,max_faces=200000)
        result,_=mt.build(m.vertices,m.faces,p,context())
        self.assertTrue(result.nondegenerate_faces().all())
        self.assertTrue(result.is_winding_consistent)

    def test_chart_seams_cross_fade_instead_of_fading_out(self):
        """Constant relief stays constant across chart seams; 0.1 faded it to zero there."""
        self.custom(np.full((8,8),65535,np.uint16))
        self.mesh=trimesh.creation.icosphere(subdivisions=3)
        result,info=self.build(mapping='surface',relief=1.,element_size=20.,seam_feather=50.)
        self.assertGreater(info['charts'],1)
        moved=np.linalg.norm(result.vertices[:len(self.mesh.vertices)]-self.mesh.vertices,axis=1)
        self.assertGreater(moved.min(),.01*2*.9)

    def test_dense_sources_chart_a_simplified_copy(self):
        mesh=trimesh.creation.icosphere(subdivisions=3)
        with patch.object(backend,'PROXY_FACES',300):
            charts=backend.charts(mesh.vertices,mesh.faces,context())
        self.assertEqual(charts.uv.shape,(len(mesh.faces),3,2))
        self.assertLess(len(charts.proxy_f),len(mesh.faces))
        self.assertTrue((charts.chart>=0).all())
        # Inside a chart each vertex has one UV.
        corner=np.repeat(charts.chart,3)*len(mesh.vertices)+mesh.faces.ravel()
        uv=charts.uv.reshape(-1,2)
        order=np.argsort(corner,kind='stable')
        same=corner[order][1:]==corner[order][:-1]
        np.testing.assert_allclose(uv[order][1:][same],uv[order][:-1][same])
        # A point's UV in its own chart is its corner UV.
        corners=mesh.faces[:5,0]
        np.testing.assert_allclose(charts.locate(mesh.vertices[corners],charts.chart[:5],mesh.vertex_normals[corners]),
                                   charts.uv[:5,0],atol=1e-9)

    def test_cylindrical_caps_project_along_the_axis(self):
        """At the crown the angle crowds the motifs together; caps take a projection along the axis instead, which
        also needs fewer faces."""
        a,alpha,elems,_=mt.prepare(self.p,context())
        field=mt.Field(dict(self.p,element_size=10.),self.mesh.vertices,None,a,alpha,elems)
        top=np.array([[.2,.1,.95],[-.3,.05,.9]])
        planar,coverage=field.sample(top[:,:2]/field.tile)
        np.testing.assert_allclose(field.evaluate(top,np.tile([0,0,1.],(2,1))),planar*coverage*field.height)
        self.mesh=trimesh.creation.icosphere(subdivisions=2)
        p=dict(mapping='cylindrical',element_size=8.,collision_protection=False,max_faces=2000000)
        with_caps=len(self.build(**p)[0].faces)
        with patch.object(mt,'CAP_FROM',2.),patch.object(mt,'CAP_TO',3.):
            without=len(self.build(**p)[0].faces)
        self.assertLess(with_caps,without)

    def test_collision_guard_prevents_crossing_between_close_surfaces(self):
        self.custom(np.full((8,8),65535,np.uint16))
        v=np.array([[-1,-1,-.01],[1,-1,-.01],[0,1,-.01],[-1,-1,.01],[0,1,.01],[1,-1,.01]])
        f=np.array([[0,1,2],[3,4,5]])
        p=dict(self.p,preserve_borders=False,relief=200.,element_size=10000.,tolerance=.2)
        m,info=mt.build(v,f,p,context())
        from atlas_toolkit.tools.mesh_dilate.mesh_dilate import intersections
        self.assertFalse(intersections(m.vertices,m.faces))
        self.assertGreater(info['limited'],0)

    def test_export_units_preview_and_failed_publication(self):
        self.mesh.apply_scale(137.)
        self.mesh.apply_translation([20,-70,34])
        src=self.folder/'source.ply'; self.mesh.export(src)
        for suffix in ('.stl','.obj','.ply'):
            dst=self.folder/('out'+suffix)
            result=engine.run(context(),src,dst,TOOL.values(self.p))
            saved=trimesh.load_mesh(dst)
            self.assertTrue(saved.is_watertight)
            self.assertEqual(len(saved.faces),result.data['faces'])
            self.assertLess(np.max(np.abs(saved.bounds-self.mesh.bounds)),5.)
        dst=self.folder/'old.ply'; dst.write_bytes(b'old')
        with patch('atlas_toolkit.geom.mesh_export.os.replace',side_effect=OSError('locked')):
            with self.assertRaisesRegex(OSError,'locked'):
                engine.run(context(),src,dst,TOOL.values(self.p))
        self.assertEqual(dst.read_bytes(),b'old')
        result=engine.run(context(),src,None,TOOL.values(self.p))
        self.assertFalse(result.outputs)
        self.assertEqual(result.preview['views'][1]['mesh'].faces_total,result.data['faces'])


if __name__=='__main__':
    unittest.main()
