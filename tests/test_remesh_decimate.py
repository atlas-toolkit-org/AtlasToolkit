"""Geometry, native polygon transport, rendering, runtime selection and publication."""
import _isolate  # first: settings and caches stay in a temporary folder
import _presets
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import trimesh

from atlas_toolkit.core.job import JobContext, Cancelled
from atlas_toolkit.core import runtimes
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom.surface import from_arrays, from_mesh, load_surface, save_surface
from atlas_toolkit.tools.remesh_decimate import TOOL
from atlas_toolkit.tools.remesh_decimate.remesh_decimate import process, dual_cells, validate, topology, boundary_error
from atlas_toolkit.tools.remesh_decimate.engine import run


def context(cancel=lambda: False):
    return JobContext(lambda *args: None, cancel)


def cube():
    v = np.array([[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]],float)
    return from_arrays(v, ([0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]))


class RemeshTests(unittest.TestCase):
    def test_surface_methods_are_valid_and_reproducible(self):
        source = from_mesh(trimesh.creation.icosphere(subdivisions=2))
        for method in ('decimate','uniform','adaptive','directional','cells','quads'):
            with self.subTest(method=method):
                values = TOOL.values(dict(method=method,target_faces=180,iterations=2,surface_error=10.,normal_deviation=0.))
                a, data = process(source, values, context())
                validate(a.as_trimesh())
                self.assertEqual(topology(source.as_trimesh()), topology(a.as_trimesh()))
                self.assertGreater(data['samples'], 0)
                self.assertTrue(np.isfinite(a.vertices).all())
                b, _ = process(source, values, context())
                np.testing.assert_allclose(a.vertices,b.vertices,atol=1e-12)
                np.testing.assert_array_equal(a.triangles,b.triangles)

    def test_units_translation_and_disconnected_parts(self):
        m = trimesh.creation.icosphere(subdivisions=2)
        other = m.copy()
        other.apply_translation([4,0,0])
        source = from_mesh(trimesh.util.concatenate([m, other]))
        p = TOOL.values(dict(target_faces=200,surface_error=4.,normal_deviation=0.))
        a, _ = process(source,p,context())
        offset = np.array([1e5,-200,31])
        moved = from_arrays(source.vertices*17+offset, source.faces)
        b, _ = process(moved,p,context())
        np.testing.assert_allclose(a.vertices*17+offset,b.vertices,atol=1e-8)
        self.assertEqual(len(a.as_trimesh().split(only_watertight=False)),2)

    def test_boundary_and_region_protection(self):
        x,y = np.meshgrid(np.linspace(-1,1,12),np.linspace(-1,1,12))
        v = np.column_stack([x.ravel(),y.ravel(),np.zeros(x.size)])
        f=[]
        for j in range(11):
            for i in range(11):
                a=j*12+i
                f.extend([[a,a+1,a+13],[a,a+13,a+12]])
        source=from_arrays(v,np.array(f))
        for method in ('decimate','uniform'):
            p=TOOL.values(dict(method=method,target_faces=100,iterations=2,surface_error=1.))
            out,_=process(source,p,context())
            border=np.flatnonzero((np.abs(v[:,:2])==1).any(1))
            from scipy.spatial import cKDTree
            self.assertLess(cKDTree(out.vertices).query(v[border])[0].max(),1e-8)
        p=TOOL.values(dict(target_faces=50,regional_detail=True,influence='all'))
        out,_=process(source,p,context())
        self.assertEqual(len(out.triangles),len(source.triangles))

    def test_boundary_validation_detects_expanded_coplanar_sheet(self):
        a=from_arrays([[-1,-1,0],[1,-1,0],[1,1,0],[-1,1,0]],[[0,1,2,3]]).as_trimesh()
        b=a.copy();b.vertices*=2
        self.assertGreater(boundary_error(a,b),.9)
        self.assertEqual(boundary_error(a,a),0.)

    def test_dual_cells_tile_sharp_closed_and_open_surfaces(self):
        for m in (trimesh.creation.icosphere(subdivisions=1),cube().as_trimesh()):
            a=dual_cells(m,True,45.,.6,19)
            validate(a.as_trimesh())
            self.assertEqual(topology(m),topology(a.as_trimesh()))
            self.assertTrue(any(len(face)>4 for face in a.faces))
        a=dual_cells(trimesh.creation.icosphere(subdivisions=1),False,45.,.6,19)
        b=dual_cells(trimesh.creation.icosphere(subdivisions=1),False,45.,.6,20)
        self.assertFalse(np.allclose(a.vertices,b.vertices))

    def test_nonmanifold_and_degenerate_fail(self):
        surface=from_arrays([[0,0,0],[1,0,0],[0,1,0],[0,-1,0],[0,0,1]],np.array([[0,1,2],[1,0,3],[0,1,4]]))
        with self.assertRaisesRegex(ValueError,'Non-manifold'):
            process(surface,TOOL.defaults(),context())
        with self.assertRaises(ValueError):
            from_arrays([[0,0,0],[1,0,0],[0,1,0]],[[0,1,8]])

    def test_native_exports_and_flowrep(self):
        from flowrep3d.mesh import load_mesh
        source=cube()
        with tempfile.TemporaryDirectory() as folder:
            for ext in ('.obj','.ply','.stl'):
                path=Path(folder)/('mesh'+ext)
                save_surface(source,path,TOOL)
                self.assertIn('atlas-toolkit:remesh-decimate',gm.read_signature(path))
                output=load_surface(path)
                self.assertEqual(len(output.faces),12 if ext=='.stl' else 6)
                self.assertTrue(output.as_trimesh().is_watertight)
                if ext!='.stl':
                    self.assertEqual(len(load_mesh(path).faces),6)
                np.testing.assert_allclose(output.bounds,source.bounds)

    def test_native_wire_and_visible_edges_have_no_quad_diagonal(self):
        from atlas_toolkit.tools.plotter_vision.model import prepare
        from atlas_toolkit.tools.plotter_vision.plotter_vision import View
        from atlas_toolkit.geom.camera import Camera
        surface=from_arrays([[-1,-1,0],[1,-1,0],[1,1,0],[-1,1,0]],[[0,1,2,3]])
        data=gm.preview(surface)
        self.assertEqual(data.edge_segments.shape,(4,2,3))
        mdl=prepare(surface.as_trimesh(),crossings=False)
        self.assertEqual(np.count_nonzero(mdl.native),4)
        view=View(mdl,Camera(0.,0.,170.),0.,'two',edge_mode='mesh')
        self.assertEqual(len(view.edge_ids),4)

    def test_bent_shared_chain_has_no_four_triangle_edge(self):
        # Both polygons share A-B-C. The A-C chord must never be internal to both.
        source=from_arrays([[0,1,0],[0,0,0],[1,0,0],[1,1,0],[0,0,1]],
                           [[0,1,2,3],[2,1,0,4]])
        import igl
        self.assertTrue(igl.is_edge_manifold(source.triangles)[0])
        self.assertTrue(source.as_trimesh().is_winding_consistent)

    def test_collinear_boundary_corner_and_concave_polygon_roundtrip(self):
        source=from_arrays([[0,0,0],[1,0,0],[2,0,0],[2,2,0],[1,1,0],[0,2,0]],[[0,1,2,3,4,5]])
        self.assertEqual(len(source.triangles),4)
        self.assertEqual(len(np.unique(source.triangles)),6)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'concave.ply'
            save_surface(source,path,TOOL)
            output=load_surface(path)
            self.assertEqual(len(output.faces),1)
            self.assertEqual(len(output.triangles),4)

    def test_frame_metadata_survives_format_change(self):
        from atlas_toolkit.geom.frame3d import resolve_axes
        source=cube()
        source.frame={'up':'+z','front':'-y','rotate':[0.,10.,0.]}
        with tempfile.TemporaryDirectory() as folder:
            for ext in ('.obj','.ply'):
                path=Path(folder)/('oriented'+ext)
                save_surface(source,path,TOOL)
                self.assertEqual(resolve_axes(path,{},source.extents)[:3],('+z','-y',(0.,10.,0.)))
                self.assertEqual(load_surface(path).frame,source.frame)

    def test_already_under_target_keeps_native_polygons(self):
        source=cube()
        output,_=process(source,TOOL.defaults(),context())
        self.assertEqual(len(output.faces),6)
        self.assertEqual(len(output.triangles),12)
        np.testing.assert_array_equal(output.vertices,source.vertices)

    def test_off_inline_header_and_directional_feature_corners(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'inline.off'
            path.write_text('OFF 4 1 0\n0 0 0\n1 0 0\n1 1 0\n0 1 0\n4 0 1 2 3\n')
            self.assertEqual(len(load_surface(path).faces),1)
        from scipy.spatial import cKDTree
        source=trimesh.creation.box()
        p=TOOL.values(dict(method='directional',direction=(1.,.8,.6),target_faces=100,surface_error=2.,iterations=3))
        output,_=process(from_mesh(source),p,context())
        self.assertLess(cKDTree(output.vertices).query(source.vertices)[0].max(),1e-9)

    def test_regional_density_and_mid_native_cancellation(self):
        source=from_mesh(trimesh.creation.icosphere(subdivisions=3))
        p=TOOL.values(dict(method='uniform',target_faces=900,iterations=3,surface_error=10.,
                          regional_detail=True,influence='band',band_from=50.,band_to=100.,feather=10.))
        output,_=process(source,p,context())
        edge=output.edges
        midpoint=output.vertices[edge].mean(1)
        length=np.linalg.norm(output.vertices[edge[:,0]]-output.vertices[edge[:,1]],axis=1)
        self.assertLess(np.median(length[midpoint[:,2]>.5]),np.median(length[midpoint[:,2]<-.5])*.8)
        cancelled=[False]
        def emit(kind,payload):
            if kind=='progress' and payload[0]>.1:
                cancelled[0]=True
        with self.assertRaises(Cancelled):
            process(source,p,JobContext(emit,lambda:cancelled[0]))

    def test_native_mesh_workflow_draw_and_cache(self):
        from atlas_toolkit.core import workflow as W
        from atlas_toolkit.core.registry import registry
        reg=registry()
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            source=folder/'source.stl'; trimesh.creation.icosphere(subdivisions=1).export(source)
            wf=W.Workflow(name='Native cells')
            src=wf.add(W.input_node('mesh'))
            prep=wf.add(W.tool_node(TOOL,values=dict(method='cells',target_faces=100,surface_error=10.,iterations=2)))
            vision=reg.get('plotter-vision')
            draw=wf.add(W.tool_node(vision,values=_presets.values_or_defaults(vision,'Remeshed network')))
            save=wf.add(W.save_node(where='folder',folder=str(folder/'out')))
            wf.connect(src.id,prep.id);wf.connect(prep.id,draw.id);wf.connect(draw.id,save.id)
            cache=W.WorkflowCache(folder/'cache')
            for _ in range(2):
                result=W.run_workflow(wf,source,lambda *a:None,lambda:False,reg=reg,cache=cache,
                                      tool_cache=str(folder/'toolcache'))
                self.assertTrue(result.outputs)
                self.assertTrue(any(Path(path).suffix=='.svg' for path in result.outputs))

    def test_conditional_runtime_and_workflow_check(self):
        from atlas_toolkit.core import workflow as W
        from atlas_toolkit.core.registry import registry
        def found(key):
            return None if key=='blender' else Path('helper.exe')
        with patch.object(runtimes,'find',side_effect=found):
            self.assertFalse(runtimes.missing(TOOL))
            for values in ({'method':'voxel'},{'method':'planar'},{'method':'quads','quad_algorithm':'quadriflow'}):
                self.assertEqual([r.key for r in runtimes.missing(TOOL,TOOL.values(values))],['blender'])
            wf=W.Workflow(name='remesh')
            src=wf.add(W.input_node('mesh'))
            node=wf.add(W.tool_node(TOOL,values={'method':'voxel'}))
            wf.connect(src.id,node.id)
            self.assertTrue(any('Blender' in str(issue) for issue in W.check(wf,registry())))

    def test_dry_run_cancellation_and_failed_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            src=folder/'source.stl'
            trimesh.creation.icosphere(subdivisions=1).export(src)
            dst=folder/'out.obj'
            p=TOOL.values({'target_faces':1000})
            run(JobContext(lambda *a:None,lambda:False,mode='analyse'),src,dst,p)
            self.assertFalse(dst.exists())
            dst.write_text('previous output')
            with self.assertRaises(Cancelled):
                run(context(lambda:True),src,dst,p)
            self.assertEqual(dst.read_text(),'previous output')
            with patch('atlas_toolkit.geom.mesh_export.os.replace',side_effect=OSError('locked')):
                with self.assertRaises(OSError):
                    run(context(),src,dst,p)
            self.assertEqual(dst.read_text(),'previous output')

    @unittest.skipUnless(runtimes.find('blender'),'Blender not installed')
    def test_blender_methods_preserve_native_faces(self):
        from atlas_toolkit.geom import blender as gb
        try:
            for method in ('voxel','planar','quads'):
                p=TOOL.values(dict(method=method,quad_algorithm='quadriflow',target_faces=120,surface_error=10.,voxel_size=8.))
                source=cube() if method=='planar' else from_mesh(trimesh.creation.icosphere(subdivisions=2))
                out,_=process(source,p,context())
                self.assertGreater(len(out.faces),0)
                if method=='planar':
                    self.assertEqual(len(out.faces),6)
        finally:
            gb.shutdown()


if __name__=='__main__':
    unittest.main()
