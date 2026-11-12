import _isolate  # first: settings and caches stay in a temporary folder
import itertools
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from flowrep3d import (CancelledError,CurveNetwork,FlowRepError,FlowRepOptions,extract_network,
                      from_arrays,load_mesh,load_network,prepare_mesh,save_network)
from flowrep3d.clustering import aggregate_graph,alignment_weight,correlation_cluster,objective
from flowrep3d.flowlines import compute_flowlines,projectivity
from flowrep3d.network import CoverageEvaluator,NetworkState,predict_normal
from .helpers import cylinder,grid,sphere,torus


def opts(**kw):
    return FlowRepOptions(cache_dir=None,local_passes=0,regularize_iterations=0,clustering_passes=2,**kw)


class GeometryTests(unittest.TestCase):
    def test_polygon_obj_and_negative_indices(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"polygon.obj"
            p.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv .5 1.5 0\nv 0 1 0\nf -5 -4 -3 -2 -1\n")
            mesh=load_mesh(p)
            self.assertEqual([len(f) for f in mesh.faces],[5])
            self.assertEqual(len(mesh.triangles),3)
            self.assertEqual(int((mesh.topology.edge_faces[:,1]<0).sum()),5)

    def test_nonmanifold_diagnostic(self):
        with self.assertRaisesRegex(FlowRepError,"non-manifold"):
            from_arrays([[0,0,0],[1,0,0],[0,1,0],[0,-1,0],[0,0,1]],
                        [[0,1,2],[1,0,3],[0,1,4]])

    def test_touching_vertex_fans_are_split_and_reported(self):
        mesh=from_arrays([[0,0,0],[1,0,0],[0,1,0],[-1,0,0],[0,-1,0]],
                         [[0,1,2],[0,3,4]])
        self.assertEqual(mesh.topology.components,2)
        self.assertEqual(len(mesh.vertices),6)
        self.assertEqual(mesh.metadata["topology_repairs"]["split_touching_vertices"],1)

    def test_signed_clustering_respects_repulsion(self):
        pairs=[(0,1),(1,2),(0,2)]
        labels,info=correlation_cluster(3,pairs,[1.,1.,-5.],passes=3)
        self.assertNotEqual(labels[0],labels[2])
        self.assertAlmostEqual(info["objective"],1.)
        self.assertGreater(float(alignment_weight(0)),0)
        self.assertLess(float(alignment_weight(90)),0)

    def test_kl_never_lowers_objective(self):
        rng=np.random.default_rng(7)
        pairs=list(itertools.combinations(range(8),2)); weights=rng.normal(size=len(pairs))
        graph=aggregate_graph(pairs,weights)
        labels,info=correlation_cluster(8,pairs,weights,passes=4)
        self.assertGreaterEqual(objective(labels,graph)+1e-8,info["initial_objective"])
        other,_=correlation_cluster(8,pairs,weights,passes=4)
        np.testing.assert_array_equal(labels,other)

    def test_normal_interpolation_endpoints_and_midpoint(self):
        a=np.array([0.,0.,1.]); b=np.array([0.,1.,0.])
        np.testing.assert_allclose(predict_normal(a,b,0.,10.),a)
        np.testing.assert_allclose(predict_normal(a,b,10.,0.),b)
        np.testing.assert_allclose(predict_normal(a,b,5.,5.),[0,2**-0.5,2**-0.5])

    def test_geodesicity_uses_angle_to_plane(self):
        t=np.linspace(-1,1,31)
        P=np.c_[np.sin(t),np.zeros_like(t),np.cos(t)]
        N=P.copy()
        self.assertLess(projectivity(P,N),1e-5)  # Great-circle arc.
        latitude=np.c_[0.7*np.cos(t),0.7*np.sin(t),np.full_like(t,np.sqrt(1-.7**2))]
        self.assertGreater(projectivity(latitude,latitude),10.)

    def test_planar_patch_requires_no_interior_curves(self):
        mesh=grid()
        network=extract_network(mesh,opts())
        self.assertEqual(network.diagnostics["selected_flowlines"],0)
        self.assertTrue(network.diagnostics["coverage_complete"])
        self.assertTrue(all(k=="trim" for k in network.kinds))

    def test_sharp_quad_box_retains_its_twelve_feature_edges(self):
        V=np.array(list(itertools.product((-1.,1.),repeat=3)))
        F=[[0,1,3,2],[4,6,7,5],[0,4,5,1],[2,3,7,6],[0,2,6,4],[1,5,7,3]]
        net=extract_network(from_arrays(V,F),opts())
        self.assertEqual(net.diagnostics["selected_flowlines"],0)
        self.assertEqual(net.edge_count,12)
        self.assertEqual(net.diagnostics["max_error_deg"],0.)

    def test_face_region_partition_and_holes(self):
        mesh=cylinder()
        flows,trim,_=compute_flowlines(mesh,opts())
        ev=CoverageEvaluator(mesh,flows)
        state=NetworkState(ev,trim,np.zeros(len(flows),bool))
        self.assertEqual(len(state.regions),1)
        region=next(iter(state.regions.values()))
        self.assertEqual(region.boundary_cycles,2)
        self.assertEqual(region.error,90.)

    def test_thin_annulus_is_uncovered_even_without_interior_vertices(self):
        mesh=cylinder(n=12,levels=2)
        flows,trim,_=compute_flowlines(mesh,opts())
        ev=CoverageEvaluator(mesh,flows)
        state=NetworkState(ev,trim,np.zeros(len(flows),bool))
        region=next(iter(state.regions.values()))
        self.assertEqual(region.interior_vertices,0)
        self.assertEqual(region.boundary_cycles,2)
        self.assertEqual(region.error,90.)

    def test_sphere_torus_cylinder_coverage(self):
        for mesh in (sphere(12,6),torus(12,6),cylinder(12,4)):
            with self.subTest(vertices=len(mesh.vertices)):
                before=mesh.vertices.copy()
                network=extract_network(mesh,opts())
                self.assertTrue(network.diagnostics["coverage_complete"])
                self.assertLessEqual(network.diagnostics["max_error_deg"],20.+1e-7)
                self.assertLessEqual(network.diagnostics["selected_flowlines"],network.diagnostics["dense_flowlines"])
                np.testing.assert_array_equal(mesh.vertices,before)

    def test_detailed_and_sparse_descriptiveness_bounds(self):
        for threshold in (10.,30.):
            net=extract_network(sphere(12,6),opts(dmax=threshold))
            self.assertTrue(net.diagnostics["coverage_complete"])
            self.assertLessEqual(net.diagnostics["max_error_deg"],threshold+1e-7)

    def test_disconnected_components(self):
        a=grid(4); b=grid(4)
        V=np.r_[a.vertices,b.vertices+np.array([5,0,1])]
        F=list(a.faces)+[f+len(a.vertices) for f in b.faces]
        network=extract_network(from_arrays(V,F),opts())
        self.assertEqual(network.diagnostics["components"],2)
        self.assertEqual(network.diagnostics["trim_curves"],2)

    def test_cancellation(self):
        with self.assertRaises(CancelledError):
            extract_network(grid(4),opts(),cancel=lambda:True)

    def test_cancellation_during_extraction(self):
        requested=False
        def progress(fraction,message):
            nonlocal requested
            requested=fraction>=0.12
        with self.assertRaises(CancelledError):
            extract_network(sphere(12,6),opts(),progress,cancel=lambda:requested)

    def test_fixed_mesh_reproducibility_and_cache(self):
        mesh=torus(10,6)
        a=extract_network(mesh,opts()); b=extract_network(mesh,opts())
        np.testing.assert_array_equal(a.vertices,b.vertices)
        self.assertEqual([c.tolist() for c in a.curves],[c.tolist() for c in b.curves])
        with tempfile.TemporaryDirectory() as tmp:
            options=replace(opts(),cache_dir=tmp)
            one=extract_network(mesh,options); two=extract_network(mesh,options)
            self.assertFalse(one.diagnostics["cache_hit"])
            self.assertTrue(two.diagnostics["cache_hit"])

    def test_remesh_binding_and_cache(self):
        import trimesh
        primitive=trimesh.creation.icosphere(subdivisions=2)
        mesh=from_arrays(primitive.vertices,primitive.faces)
        with tempfile.TemporaryDirectory() as tmp:
            options=FlowRepOptions(target_faces=150,cache_dir=tmp)
            a=prepare_mesh(mesh,options); b=prepare_mesh(mesh,options)
            self.assertGreater(a.metadata["quad_fraction"],0.8)
            self.assertTrue(b.metadata["preparation_cached"])
            np.testing.assert_array_equal(a.vertices,b.vertices)
            np.testing.assert_array_equal(a.reference_vertices,mesh.reference_vertices)

    def test_network_formats_and_author_chains(self):
        net=CurveNetwork([[0,0,0],[1,0,0],[1,1,0]],[[0,1,2]],["trim"],
                         [np.array([4,5])],[2],{"max_error_deg":12.},{"source_mesh":"example.obj"})
        with tempfile.TemporaryDirectory() as tmp:
            for ext in ("json","npz","obj"):
                p=Path(tmp)/("network."+ext); save_network(net,p); loaded=load_network(p)
                np.testing.assert_allclose(loaded.vertices,net.vertices)
                np.testing.assert_array_equal(loaded.curves[0],net.curves[0])
            p=Path(tmp)/"author.obj"
            p.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nl 2 1\nl 3 2\n# chain 1 2\n")
            self.assertEqual(load_network(p).curves[0].tolist(),[0,1,2])

    def test_normalized_author_reference_registration(self):
        from flowrep3d.benchmark import register_reference
        V=np.array(list(itertools.product((-1.,1.),repeat=3)))+np.array([10.,20.,30.])
        F=[[0,1,3,2],[4,6,7,5],[0,4,5,1],[2,3,7,6],[0,2,6,4],[1,5,7,3]]
        mesh=from_arrays(V,F)
        # Use surface edges, all exactly on the box, in normalized units.
        E=mesh.topology.edges
        normalized=(mesh.vertices-np.array([10.,20.,30.]))/np.sqrt(12.)
        original=CurveNetwork(normalized,[e for e in E],metadata={"imported":True})
        registered=register_reference(original,mesh)
        np.testing.assert_allclose(registered.vertices,mesh.vertices,atol=1e-6)
        self.assertTrue(registered.metadata["reference_registration"]["applied"])
        np.testing.assert_allclose(original.vertices,normalized)
        self.assertIs(register_reference(registered,mesh),registered)

    def test_network_schema_validation(self):
        with self.assertRaises(FlowRepError):
            CurveNetwork([[0,0,0]],[[0,2]])
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"bad.json"; p.write_text('{"schema":"atlas-flowrep-network","version":99,"vertices":[],"curves":[]}')
            with self.assertRaisesRegex(FlowRepError,"schema"):
                load_network(p)

    def test_large_translation_does_not_destroy_polygon_triangulation(self):
        mesh=grid(4)
        translated=from_arrays(mesh.vertices+np.array([1e9,-1e9,1e9]),mesh.faces)
        self.assertEqual(len(translated.triangles),len(mesh.triangles))
        np.testing.assert_allclose(translated.topology.normals,mesh.topology.normals,atol=1e-6)

    def test_regularization_rechecks_coverage_and_surface_bound(self):
        mesh=sphere(12,6)
        network=extract_network(mesh,replace(opts(),regularize_iterations=2))
        self.assertLessEqual(network.diagnostics["max_error_deg"],20+1e-8)
        regularization=network.diagnostics["regularization"]
        self.assertLessEqual(regularization["surface_max_distance"],regularization["surface_bound"]+1e-8)

    def test_remeshing_preserves_large_coordinate_offsets(self):
        import trimesh
        p=trimesh.creation.icosphere(subdivisions=1)
        offset=np.array([1e6,-2e6,3e6])
        mesh=from_arrays(p.vertices+offset,p.faces)
        prepared=prepare_mesh(mesh,FlowRepOptions(target_faces=150,cache_dir=None))
        self.assertLess(float(np.linalg.norm(prepared.vertices.mean(0)-offset)),0.2)
        self.assertGreater(float(np.ptp(prepared.vertices,axis=0).min()),1.5)


if __name__=="__main__":
    unittest.main()

