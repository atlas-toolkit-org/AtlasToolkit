import _isolate  # first: settings and caches stay in a temporary folder
import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import numpy as np
import trimesh

from flowrep3d import (Camera,CancelledError,CurveNetwork,RenderOptions,from_arrays,render_network,write_svg)
from flowrep3d.render import _clean_layers,_clip_3d
from flowrep3d.svg import SVG_NS,_bezier,cubic_error_bound,fit_path
from flowrep3d._atlas.camera import frame_for


class RenderTests(unittest.TestCase):
    def setUp(self):
        box=trimesh.creation.box()
        self.mesh=from_arrays(box.vertices,box.faces)
        self.camera=Camera(theta=90,psi=180,ortho=True)
        self.options=RenderOptions(up="+z",front="-y",silhouette=False,min_length=0)
        self.front=np.array([[-.3,-.5,0.],[.3,-.5,0.]])
        self.back=self.front+np.array([0.,1.,0.])

    def test_hidden_line_removal_and_hidden_layer(self):
        net=CurveNetwork(np.r_[self.front,self.back],[[0,1],[2,3]])
        visible=render_network(self.mesh,net,self.camera,options=self.options)
        transparent=render_network(self.mesh,net,self.camera,options=replace(self.options,hidden=True))
        self.assertEqual(len(visible.layers["flowlines"]),1)
        self.assertEqual(len(visible.layers["hidden"]),0)
        self.assertEqual(len(transparent.layers["hidden"]),1)

    def test_duplicate_strokes_draw_once(self):
        net=CurveNetwork(self.front,[[0,1],[0,1]],["trim","flowline"])
        d=render_network(self.mesh,net,self.camera,options=self.options)
        self.assertEqual(len(d.polylines),1)
        self.assertGreater(d.stats["duplicate_length_removed_mm"],1)

    def test_point_intersections_do_not_drop_short_fragments(self):
        layers={"features":[np.array([[0.,1.],[2.,1.]])],
                "flowlines":[np.array([[1.,0.],[1.,2.]])]}
        cleaned,_=_clean_layers(layers,(-1,-1,3,3),1.5,False,None)
        self.assertEqual(len(cleaned["flowlines"]),1)
        self.assertAlmostEqual(np.linalg.norm(cleaned["flowlines"][0][-1]-cleaned["flowlines"][0][0]),2.)

    def test_near_plane_clipping_and_offscreen_segments(self):
        cam=Camera(theta=90,psi=180,radius=1.)
        eye,*_=cam.basis()
        P=np.array([eye+[0,1.,0],eye+[0,3.,0]])
        pieces=_clip_3d(P,cam,frame_for("A4","portrait",15))
        self.assertTrue(pieces)
        for p in pieces: self.assertTrue(np.all(cam.to_camera(p)[:,2]>=0.5-1e-9))
        self.assertFalse(_clip_3d(np.array([eye+[1e6,1,0],eye+[1e6,2,0]]),cam,frame_for("A4","portrait",15)))

    def test_cubic_error_bound_covers_continuous_curve(self):
        control=np.array([[0.,0.],[1.,2.],[2.,2.],[3.,0.]])
        t=np.linspace(0,1,20); P=_bezier(control,t)
        bound,_=cubic_error_bound(control,P,t)
        dense=np.linspace(0,1,5000); cubic=_bezier(control,dense)
        linear=np.stack([np.interp(dense,t,P[:,i]) for i in range(2)],axis=1)
        self.assertLessEqual(float(np.linalg.norm(cubic-linear,axis=1).max()),bound+1e-9)

    def test_fitted_paths_are_bounded_and_anchor_endpoints(self):
        t=np.linspace(0,np.pi,100)
        P=np.c_[20+10*np.cos(t),20+10*np.sin(t)]
        d,bound,nc,nl=fit_path(P,.05,(0,0,40,40))
        self.assertTrue(d.startswith("M 30.000000,20.000000"))
        self.assertIn("10.000000,20.000000",d)
        self.assertLessEqual(bound,.05)
        self.assertGreater(nc,0)

    def test_svg_mm_layers_metadata_margins_and_no_raster(self):
        net=CurveNetwork(self.front,[[0,1]])
        drawing=render_network(self.mesh,net,self.camera,options=self.options)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"drawing.svg"; write_svg(drawing,p)
            root=ET.parse(p).getroot()
            self.assertEqual(root.get("width"),"297mm")
            self.assertEqual(root.find(f"{{{SVG_NS}}}desc").text,"flowrep-sketch")
            self.assertFalse(root.findall(f".//{{{SVG_NS}}}image"))
            self.assertLessEqual(drawing.stats["max_fitting_bound_mm"],.05)
            for points in drawing.polylines:
                self.assertTrue(np.all(points>=15+self.options.line_width/2-1e-8))
                self.assertTrue(np.all(points[:,0]<=297-15-self.options.line_width/2+1e-8))

    def test_camera_changes_do_not_mutate_network(self):
        net=CurveNetwork(self.front,[[0,1]])
        before=net.vertices.copy()
        render_network(self.mesh,net,self.camera,options=self.options)
        render_network(self.mesh,net,replace(self.camera,psi=150),options=self.options)
        np.testing.assert_array_equal(before,net.vertices)

    def test_renderer_cancellation(self):
        with self.assertRaises(CancelledError):
            render_network(self.mesh,CurveNetwork(self.front,[[0,1]]),self.camera,
                           options=self.options,cancel=lambda:True)

    def test_nonfinite_parameters_fail_before_geometry_work(self):
        from flowrep3d import FlowRepError
        with self.assertRaises(FlowRepError):
            RenderOptions(tolerance=float("nan"))
        with self.assertRaises(FlowRepError):
            render_network(self.mesh,CurveNetwork(self.front,[[0,1]]),
                           replace(self.camera,radius=-1),options=self.options)


if __name__=="__main__":
    unittest.main()
