import _isolate  # first: settings and caches stay in a temporary folder
import unittest
from dataclasses import replace

import numpy as np
import trimesh

from flowrep3d import Camera,FlowRepOptions,RenderOptions,extract_network,from_arrays,render_network
from flowrep3d.flowlines import trim_edges
from flowrep3d.network import open_ends
from flowrep3d.render import _close_gaps,_contours,_join_strokes,_smooth_polyline
from .helpers import cylinder,grid,sphere,torus


def opts(**kw):
    return FlowRepOptions(cache_dir=None,local_passes=0,regularize_iterations=0,clustering_passes=2,**kw)


def icosphere(subdivisions=4,noise=0.0,seed=7):
    s=trimesh.creation.icosphere(subdivisions=subdivisions)
    V=np.array(s.vertices)
    if noise:
        V*=1+noise*np.random.default_rng(seed).standard_normal((len(V),1))
    return from_arrays(V,s.faces)


class TrimTests(unittest.TestCase):
    def test_isolated_short_creases_are_dropped_long_and_boundary_ones_kept(self):
        # a long ridge across a grid (it reaches the boundary) and one raised vertex (a star of four creases)
        def height(x,y):
            return 0.8*abs(x)+(0.1 if abs(x-2/3)<1e-9 and abs(y)<1e-9 else 0.)
        mesh=grid(13,height)
        mesh.metadata["preparation"]="preserved"
        report={}
        kept=trim_edges(mesh,20.,min_edges=6,report=report)
        everything=trim_edges(mesh,20.,min_edges=0)
        boundary=mesh.topology.edge_faces[:,1]<0
        self.assertGreater(int((everything&~boundary).sum()),int((kept&~boundary).sum()))
        self.assertEqual(report["crease_edges_short"],4)       # the star of four creases round the bump
        ridge=np.abs(mesh.vertices[mesh.topology.edges][:,:,0]).max(1)<1e-9
        self.assertTrue(kept[ridge].all())

    def test_remeshed_smooth_folds_are_not_creases(self):
        # a remeshed sphere: big quads can meet at a sharp angle, but the reference surface is smooth there
        from flowrep3d import prepare_mesh
        mesh=prepare_mesh(icosphere(4),FlowRepOptions(target_faces=100,cache_dir=None))
        report={}
        trims=trim_edges(mesh,25.,4,report)
        self.assertEqual(int(trims.sum()),0)
        self.assertEqual(report["crease_edges"],0)


class EndTests(unittest.TestCase):
    def test_no_open_ended_flowlines_within_the_bound(self):
        for mesh in (torus(),sphere()):
            net=extract_network(mesh,opts())
            d=net.diagnostics
            self.assertLessEqual(d["max_error_deg"],20+1e-8)
            self.assertLessEqual(d["dangling_ends_after"],d["dangling_ends_before"])
            self.assertEqual(d["dangling_ends_after"],0)
            degree={}
            for c in net.curves:
                for a,b in {tuple(sorted((int(a),int(b)))) for a,b in zip(c[:-1],c[1:])}:
                    degree[a]=degree.get(a,0)+1; degree[b]=degree.get(b,0)+1
            for c,kind in zip(net.curves,net.kinds):
                if kind=="flowline" and c[0]!=c[-1]:
                    self.assertGreater(degree[int(c[0])],1); self.assertGreater(degree[int(c[-1])],1)


class DeclutterTests(unittest.TestCase):
    def test_parallel_neighbours_go_within_the_relaxed_bound(self):
        mesh=torus(24,12)
        plain=extract_network(mesh,opts(dmax=10))
        tidy=extract_network(mesh,opts(dmax=10,relaxed_declutter=True))
        self.assertLessEqual(tidy.diagnostics["selected_flowlines"],plain.diagnostics["selected_flowlines"])
        self.assertLessEqual(tidy.diagnostics["max_error_deg"],12+1e-8)

    def test_removing_a_middle_line_keeps_the_curves_that_cross_it(self):
        from flowrep3d.flowlines import Flowline
        from flowrep3d.network import CoverageEvaluator,NetworkState,_bridged_removal
        n=7; mesh=grid(n)
        lookup={tuple(sorted(map(int,e))):k for k,e in enumerate(mesh.topology.edges)}
        def at(i,j): return int(np.flatnonzero(np.all(np.isclose(mesh.vertices[:,:2],[-1+2*i/(n-1),-1+2*j/(n-1)]),axis=1))[0])
        def flow(points,strand):
            ids=np.array([at(i,j) for i,j in points])
            return Flowline(ids,np.array([lookup[tuple(sorted((int(a),int(b))))] for a,b in zip(ids[:-1],ids[1:])]),strand)
        rows=[flow([(i,j) for i in range(n)],0) for j in (2,3,4)]
        up=flow([(2,j) for j in range(3,7)],1); down=flow([(2,j) for j in range(0,4)],1)      # meet head-on at row 3
        late=flow([(4,j) for j in range(3,7)],1)                    # ends on row 3; continues down only in the mesh
        late.vertices=np.array([at(4,j) for j in range(1,7)]); late.edges=np.array(
            [lookup[tuple(sorted((at(4,j),at(4,j+1))))] for j in range(1,6)])
        flows=rows+[up,down,late]
        state=NetworkState(CoverageEvaluator(mesh,flows),mesh.topology.edge_faces[:,1]<0)
        state.ranges[5]=[2,5]                                       # late is drawn from row 3 up
        counts=(mesh.topology.edge_faces[:,1]<0).astype(np.int64)
        for fi,f in enumerate(flows):
            a,b=state.ranges[fi]; counts[f.edges[a:b]]+=1
        state.counts=counts                                         # assigned whole: degrees follow the counts
        proposal=_bridged_removal(state,1)
        self.assertFalse(proposal.selected[1])
        self.assertTrue(proposal.selected[3] and proposal.selected[4])
        self.assertEqual(list(proposal.ranges[3]),list(state.ranges[3]))   # the head-on pair still meets
        self.assertEqual(list(proposal.ranges[4]),list(state.ranges[4]))
        self.assertEqual(list(proposal.ranges[5]),[1,5])                   # late now reaches row 2

    def test_parallel_pairs_skip_crossing_curves(self):
        from flowrep3d.flowlines import compute_flowlines
        from flowrep3d.network import CoverageEvaluator,NetworkState,parallel_pairs
        mesh=torus(24,12)
        flows,trim,_=compute_flowlines(mesh,opts())
        state=NetworkState(CoverageEvaluator(mesh,flows),trim)
        for a,b in parallel_pairs(state):
            self.assertFalse(set(map(int,state.active_vertices(a)))&set(map(int,state.active_vertices(b))))


class RenderQualityTests(unittest.TestCase):
    def test_smooth_contour_of_a_sphere_is_one_true_circle(self):
        mesh=icosphere(4)
        cam=Camera(theta=90,psi=180,ortho=True)
        loops=_contours(mesh.vertices,mesh.triangles,cam,0)
        self.assertEqual(len(loops),1)
        P,N=loops[0]
        self.assertTrue(np.array_equal(P[0],P[-1]))
        _,_,_,w=cam.basis()
        self.assertLess(float(np.abs(P@w).max()),0.03)                       # on the plane facing the camera
        self.assertLess(float(np.abs(np.linalg.norm(P,axis=1)-1).max()),0.01)  # and on the sphere

    def test_smooth_contour_stays_on_a_coarse_model_and_is_drawn_whole(self):
        # a coarse model: the contour must not move outward (it did by half an edge), nor break into dashes
        mesh=icosphere(2)
        net=from_arrays_network()
        cam=Camera(theta=80,psi=-150,radius=6)
        options=RenderOptions(paper="A5",silhouette=True)
        smooth=render_network(mesh,net,cam,options=options)
        edges=render_network(mesh,net,cam,options=replace(options,silhouette_style="mesh",join=False,gap=0))
        S=np.concatenate(smooth.layers["silhouette"]); M=np.concatenate(edges.layers["silhouette"])
        centre=M.mean(0); outer=float(np.linalg.norm(M-centre,axis=1).max())
        self.assertLessEqual(float(np.linalg.norm(S-centre,axis=1).max()),outer+0.05)
        length=sum(float(np.linalg.norm(np.diff(p,axis=0),axis=1).sum()) for p in smooth.layers["silhouette"])
        self.assertEqual(len(smooth.layers["silhouette"]),1)
        self.assertGreater(length,0.9*2*np.pi*float(np.linalg.norm(M-centre,axis=1).mean()))

    def test_sharp_edged_models_keep_their_exact_outline(self):
        box=trimesh.creation.box(); mesh=from_arrays(box.vertices,box.faces)
        cam=Camera(theta=60,psi=-140,radius=6)
        options=RenderOptions(paper="A5",silhouette=True,up="+z")
        smooth=render_network(mesh,from_arrays_network(),cam,options=options)
        edges=render_network(mesh,from_arrays_network(),cam,options=replace(options,silhouette_style="mesh"))
        ink=lambda d: sum(float(np.linalg.norm(np.diff(p,axis=0),axis=1).sum()) for p in d.layers["silhouette"])
        self.assertAlmostEqual(ink(smooth),ink(edges),delta=0.05*ink(edges))

    def test_noisy_model_silhouette_is_a_few_long_strokes(self):
        mesh=icosphere(5,noise=0.004)
        net=from_arrays_network()
        cam=Camera(theta=80,psi=-150,radius=6)
        options=RenderOptions(paper="A5",silhouette=True)
        smooth=render_network(mesh,net,cam,options=options)
        edges=render_network(mesh,net,cam,options=replace(options,silhouette_style="mesh",join=False,gap=0))
        self.assertLessEqual(len(smooth.layers["silhouette"]),3)
        self.assertGreater(len(edges.layers["silhouette"]),len(smooth.layers["silhouette"]))

    def test_strokes_continue_across_junctions_but_not_round_corners(self):
        a,b,c=np.array([[0.,0.],[1.,0.]]),np.array([[1.,0.],[2.,0.]]),np.array([[1.,0.],[1.,1.]])
        joined=_join_strokes([a,b,c])
        self.assertEqual(len(joined),2)
        self.assertTrue(any(len(p)==3 and np.allclose(p[[0,-1]],[[0,0],[2,0]]) or np.allclose(p[[0,-1]],[[2,0],[0,0]])
                            for p in joined))
        square=[np.array([[0.,0.],[1.,0.]]),np.array([[1.,0.],[1.,1.]])]
        self.assertEqual(len(_join_strokes(square)),2)

    def test_short_hidden_gaps_are_drawn(self):
        A=np.array([[0.,0.,0.],[1.,0.,0.]]); B=np.array([[1.,0.,0.],[1.1,0.,0.]]); C=np.array([[1.1,0.,0.],[2.,0.,0.]])
        mm=lambda P: P[:,:2]
        merged=_close_gaps([(True,A),(False,B),(True,C)],mm,0.4)
        self.assertEqual(len(merged),1); self.assertTrue(merged[0][0])
        kept=_close_gaps([(True,A),(False,B),(True,C)],mm,0.05)
        self.assertEqual(len(kept),3)

    def test_curve_smoothing_removes_kinks_and_keeps_ends(self):
        x=np.linspace(0,1,21); zig=np.stack([x,0.02*(-1)**np.arange(21),np.zeros(21)],1)
        smooth=_smooth_polyline(zig,15,0.032)                  # half an edge, as the renderer spaces them
        np.testing.assert_allclose(smooth[[0,-1]],zig[[0,-1]])
        middle=smooth[len(smooth)//4:-len(smooth)//4]        # the pinned ends keep their offset nearby
        self.assertLess(float(np.abs(middle[:,1]).max()),0.002)
        t=np.linspace(0,2*np.pi,41); ring=np.stack([np.cos(t),np.sin(t),0*t],1); ring[-1]=ring[0]
        loop=_smooth_polyline(ring,15,0.1)
        np.testing.assert_allclose(loop[0],loop[-1])
        self.assertLess(float(np.abs(np.linalg.norm(loop[:,:2],axis=1)-1).max()),0.01)   # Taubin: no shrinking


class StripTests(unittest.TestCase):
    """Regions with no interior vertex are judged by their boundary: a stack of rings round one meridian is not a
    descriptive network (it was the trivial optimum that drew ring stacks on bottles, glasses and handles)."""
    def network(self,meridians,n=16,levels=9):
        from flowrep3d.flowlines import Flowline
        from flowrep3d.network import CoverageEvaluator,NetworkState
        mesh=cylinder(n,levels)
        lookup={tuple(sorted(map(int,e))):k for k,e in enumerate(mesh.topology.edges)}
        def at(i,j):
            a=2*np.pi*(i%n)/n
            return int(np.argmin(np.linalg.norm(mesh.vertices-[np.cos(a),np.sin(a),-1+2*j/(levels-1)],axis=1)))
        def flow(points):
            ids=np.array([at(i,j) for i,j in points])
            return Flowline(ids,np.array([lookup[tuple(sorted((int(a),int(b))))] for a,b in zip(ids[:-1],ids[1:])]))
        flows=[flow([(i,j) for i in range(n+1)]) for j in range(1,levels-1)]
        flows+=[flow([(i,j) for j in range(levels)]) for i in range(0,n,n//meridians)]
        state=NetworkState(CoverageEvaluator(mesh,flows),mesh.topology.edge_faces[:,1]<0)
        return state

    def test_one_meridian_does_not_describe_a_ring_stack(self):
        self.assertGreater(self.network(1).max_error,60)

    def test_four_meridians_do(self):
        self.assertLess(self.network(4).max_error,10)


class SpiralTests(unittest.TestCase):
    def test_a_helix_becomes_rings(self):
        from flowrep3d.flowlines import Flowline,_spiral_rings
        n,levels=16,12
        mesh=cylinder(n,levels)
        lookup={tuple(sorted(map(int,e))):k for k,e in enumerate(mesh.topology.edges)}
        def at(i,j):
            a=2*np.pi*(i%n)/n
            return int(np.argmin(np.linalg.norm(mesh.vertices-[np.cos(a),np.sin(a),-1+2*j/(levels-1)],axis=1)))
        points=[]; s=0
        for turn in range(4):                                  # round once, then two quads up: a coarse helix
            j=1+2*turn
            points+=[(s+k,j) for k in range(n)]+[(s+n-1,j+1)]
            s+=n-1
        ids=np.array([at(i,j) for i,j in points]+[at(s,9)])
        helix=Flowline(ids,np.array([lookup[tuple(sorted((int(a),int(b))))] for a,b in zip(ids[:-1],ids[1:])]))
        pieces=_spiral_rings(mesh,[helix])
        rings=[p for p in pieces if p.closed]
        self.assertGreaterEqual(len(rings),2)
        for r in rings:
            self.assertEqual(len(set(map(int,r.vertices[:-1]))),len(r.vertices)-1)   # a simple loop
            self.assertEqual(len(r.edges),len(r.vertices)-1)


class LooseEndTests(unittest.TestCase):
    def test_ends_just_short_of_a_line_meet_it_and_floating_flecks_go(self):
        from flowrep3d.render import _tidy_free_ends
        wall=np.array([[10.,0.],[10.,20.]])
        short=np.array([[0.,5.],[9.5,5.]])                     # stops 0.5 mm before the wall
        far=np.array([[0.,15.],[8.,15.]])                      # 2 mm away: a real gap, kept
        fleck=np.array([[30.,30.],[31.5,30.]])                 # 1.5 mm touching nothing
        loop=np.array([[40.,40.],[41.,40.],[41.,41.],[40.,40.]])
        out=_tidy_free_ends({"flowlines":[short,far,fleck,loop],"silhouette":[wall]},0.8,3.0)
        lines=out["flowlines"]
        self.assertEqual(len(lines),3)
        np.testing.assert_allclose(lines[0][-1],[10.,5.],atol=0.11)
        np.testing.assert_allclose(lines[1][-1],[8.,15.])
        self.assertTrue(any(len(p)==4 for p in lines))


def from_arrays_network():
    from flowrep3d import CurveNetwork
    return CurveNetwork(np.zeros((2,3)),[])


if __name__=="__main__":
    unittest.main()
