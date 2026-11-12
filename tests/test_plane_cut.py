"""Plane cuts: volumes, topology/holes, tangencies, units, exports, cancellation and the workflow contract."""
import _isolate  # first: settings and caches stay in a temporary folder
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from atlas_toolkit.core.job import Cancelled, JobContext
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.plane_cut import TOOL
from atlas_toolkit.tools.plane_cut.engine import publish, run
from atlas_toolkit.tools.plane_cut.plane_cut import cap_mask, cut, normal_angles, plane, screen_plane


def context(cancelled=lambda: False):
    return JobContext(lambda *_: None, cancelled)


class PlaneGeometryTests(unittest.TestCase):
    def test_both_sides_conserve_volume_and_preserve_input(self):
        for mesh in (trimesh.creation.box(extents=(10, 12, 14)),
                     trimesh.creation.icosphere(subdivisions=2, radius=10),
                     trimesh.creation.torus(major_radius=10, minor_radius=3)):
            original_v, original_f = mesh.vertices.copy(), mesh.faces.copy()
            for angles in ((0, 0, 0), (25, 47, -13), (0, 90, 0)):
                for fill in ("minimal", "remeshed", "fixed"):
                    with self.subTest(faces=len(mesh.faces), angles=angles, fill=fill):
                        p = TOOL.values(dict(cut_type="slice", fill=fill, cap_size=3, rotation=angles))
                        pieces, (origin, normal), _ = cut(mesh, p)
                        self.assertEqual(len(pieces), 2)
                        self.assertAlmostEqual(sum(m.volume for _, m in pieces), mesh.volume, delta=mesh.volume*1e-7)
                        for side, part in pieces:
                            self.assertTrue(part.is_watertight)
                            self.assertTrue(part.is_winding_consistent)
                            distances = (part.vertices-origin) @ normal * (1 if side == "positive" else -1)
                            self.assertGreaterEqual(distances.min(), -1e-7)
                            self.assertTrue(np.isfinite(part.vertices).all())
            np.testing.assert_array_equal(mesh.vertices, original_v)
            np.testing.assert_array_equal(mesh.faces, original_f)

    def test_annular_cap_keeps_the_torus_hole(self):
        mesh = trimesh.creation.torus(major_radius=10, minor_radius=3)
        for fill in ("minimal", "remeshed", "fixed"):
            p = TOOL.values(dict(fill=fill, cap_size=2))
            pieces, (origin, normal), _ = cut(mesh, p)
            part = pieces[0][1]
            caps = part.triangles[cap_mask(part, origin, normal)]
            self.assertGreater(len(caps), 0)
            # A centre-covering cap would fill the torus's hole. All cap centroids stay outside its inner radius.
            self.assertGreater(np.linalg.norm(caps.mean(axis=1)[:, :2], axis=1).min(), 6.9)
            self.assertAlmostEqual(part.volume, mesh.volume/2, delta=mesh.volume*1e-7)

    def test_flip_selects_the_complement_and_no_fill_stays_open(self):
        mesh = trimesh.creation.box(extents=(10, 10, 10))
        for side, volume in (("positive", 300), ("negative", 700)):
            pieces, _, _ = cut(mesh, TOOL.values(dict(side=side, offset=2, fill="minimal")))
            self.assertAlmostEqual(pieces[0][1].volume, volume)
        pieces, _, _ = cut(mesh, TOOL.values(dict(fill="none")))
        self.assertFalse(pieces[0][1].is_watertight)

    def test_world_and_centre_reference_and_normal_offset(self):
        mesh = trimesh.creation.box(extents=(10, 10, 10))
        mesh.apply_translation((100, 200, 300))
        p = TOOL.values(dict(position=(1, 2, 3), rotation=(0, 90, 0), offset=4))
        origin, normal, _ = plane(mesh.bounds, p)
        np.testing.assert_allclose(origin, (105, 202, 303), atol=1e-9)
        np.testing.assert_allclose(normal, (1, 0, 0), atol=1e-9)
        p["reference"] = "world"
        np.testing.assert_allclose(plane(mesh.bounds, p)[0], (5, 2, 3), atol=1e-9)
        for n in ((1,2,3), (0,-1,0), (-1,0,0), (0,0,-1)):
            p["rotation"] = normal_angles(n)
            np.testing.assert_allclose(plane(mesh.bounds,p)[1], np.asarray(n)/np.linalg.norm(n), atol=1e-9)

    def test_outside_tangent_empty_and_coplanar_planes(self):
        mesh = trimesh.creation.box()
        for offset in (-10, -.5):
            parts, _, _ = cut(mesh, TOOL.values(dict(offset=offset, fill="fixed",cap_size=.01)))
            self.assertEqual(len(parts[0][1].faces), len(mesh.faces))
        with self.assertRaisesRegex(ValueError,"empty"):
            cut(mesh, TOOL.values(dict(offset=10)))
        parts, _, notes = cut(mesh, TOOL.values(dict(cut_type="slice", offset=10)))
        self.assertEqual(len(parts),1)
        self.assertTrue(notes)
        # A plane through existing vertices/edges must remain closed and conserve volume.
        for angles in ((45,0,0),(0,45,0),(45,35.26438968,0)):
            parts,_,_=cut(mesh,TOOL.values(dict(cut_type="slice",rotation=angles,fill="minimal")))
            self.assertTrue(all(part.is_watertight for _,part in parts))
            self.assertAlmostEqual(sum(part.volume for _,part in parts),1)

    def test_disconnected_components_and_open_mesh(self):
        a = trimesh.creation.box()
        b = a.copy()
        a.apply_translation((-3,0,0)); b.apply_translation((3,0,0))
        parts,_,_=cut(trimesh.util.concatenate((a,b)),TOOL.values(dict(fill="minimal")))
        self.assertEqual(len(parts[0][1].split()),2)
        mesh = trimesh.creation.icosphere(subdivisions=2)
        mesh.update_faces(np.arange(len(mesh.faces)) != 0)
        parts,_,notes=cut(mesh,TOOL.values(dict(rotation=(15,10,0),fill="minimal")))
        self.assertTrue(parts)
        self.assertTrue(notes)
        sheet=trimesh.Trimesh(vertices=[(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)],faces=[(0,1,2),(0,2,3)])
        parts,_,_=cut(sheet,TOOL.values(dict(cut_type="slice",fill="none")))
        self.assertEqual(len(parts),1)

    def test_screen_stroke_defines_an_infinite_plane_and_rejects_clicks(self):
        unproject=lambda x,y,z: np.array((x,y,z),dtype=float)
        origin, normal=screen_plane(np.array((10.,20.)),np.array((40.,20.)),unproject,(0,0,0))
        np.testing.assert_allclose(origin,(0,20,0))
        self.assertAlmostEqual(abs(normal[1]),1)
        for z in (-100,0,100):
            self.assertAlmostEqual(np.dot(np.array((999,20,z))-origin,normal),0)
        with self.assertRaisesRegex(ValueError,"8 pixels"):
            screen_plane(np.array((10,20)),np.array((11,20)),unproject,(0,0,0))

    def test_separation_is_symmetric_and_only_for_slice(self):
        mesh=trimesh.creation.box(extents=(10,10,10))
        parts,_,_=cut(mesh,TOOL.values(dict(cut_type="slice",separation=4,fill="minimal")))
        self.assertAlmostEqual(parts[0][1].bounds[0,2],2)
        self.assertAlmostEqual(parts[1][1].bounds[1,2],-2)
        self.assertAlmostEqual(sum(m.volume for _,m in parts),1000)


class PlaneEngineTests(unittest.TestCase):
    def test_preview_writes_nothing_and_all_formats_are_signed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            source=folder/'box.stl'
            trimesh.creation.box().export(source)
            original=source.read_bytes()
            p=TOOL.values(dict(cut_type="slice",export_mode="separate",fill="minimal"))
            result=run(context(),source,None,p)
            self.assertFalse(result.outputs)
            self.assertEqual(list(folder.iterdir()),[source])
            for ext in ("stl","obj","ply"):
                dst=folder/f'cut.{ext}'
                result=run(context(),source,dst,p)
                self.assertEqual(len(result.outputs),2)
                for output in result.outputs:
                    self.assertIn(TOOL.signature,gm.read_signature(output))
                    self.assertTrue(gm.load_mesh(output).is_watertight)
            self.assertEqual(source.read_bytes(),original)
            # A saved cut can be explicitly reopened and cut again, for a sequence of manual trims.
            self.assertTrue(TOOL.inputs.accepts(Path(result.outputs[0])))

    def test_extra_file_collision_never_overwrites_a_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); dst=folder/'cut.stl'; other=folder/'cut_other.stl'
            other.write_bytes(b'existing other')
            outputs=publish([trimesh.creation.box()]*2,dst,context())
            self.assertEqual(other.read_bytes(),b'existing other')
            self.assertEqual(Path(outputs[1]).name,'cut_other-1.stl')

    def test_failure_or_cancel_keeps_previous_output_and_removes_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); dst=folder/'cut.stl';dst.write_bytes(b'old output')
            with patch('atlas_toolkit.tools.plane_cut.engine.os.replace',side_effect=OSError('locked')):
                with self.assertRaises(OSError):
                    publish([trimesh.creation.box()]*2,dst,context())
            self.assertEqual(dst.read_bytes(),b'old output')
            self.assertEqual(list(folder.iterdir()),[dst])
            with self.assertRaises(Cancelled):
                publish([trimesh.creation.box()],dst,context(lambda:True))
            self.assertEqual(dst.read_bytes(),b'old output')
            self.assertEqual(list(folder.iterdir()),[dst])

    def test_workflow_saves_both_files_and_reuses_cache(self):
        from atlas_toolkit.core import workflow as W
        from atlas_toolkit.core.registry import registry
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); src=folder/'box.stl';trimesh.creation.box().export(src)
            reg=registry();wf=W.Workflow(name='Cut model')
            inp=wf.add(W.input_node('mesh'))
            node=wf.add(W.tool_node(reg.get(TOOL.id),values=dict(cut_type='slice',export_mode='separate',fill='minimal')))
            save=wf.add(W.save_node(where='folder',folder=str(folder/'out')))
            wf.connect(inp.id,node.id);wf.connect(node.id,save.id)
            self.assertEqual(W.errors(W.check(wf,reg)),[])
            cache=W.WorkflowCache(folder/'cache')
            for _ in range(2):
                result=W.run_workflow(wf,src,lambda *_:None,lambda:False,reg=reg,cache=cache)
                self.assertEqual(len(result.outputs),2)
                self.assertTrue(all(Path(o).is_file() for o in result.outputs))


if __name__ == '__main__':
    unittest.main()
