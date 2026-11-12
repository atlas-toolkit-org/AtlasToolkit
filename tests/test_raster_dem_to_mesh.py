"""Physical scaling, masked topology, numeric raster IO and Atlas integration."""
import _isolate  # first: settings and caches stay in a temporary folder
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

from atlas_toolkit.core.job import Cancelled, JobContext
from atlas_toolkit.tools.raster_dem_to_mesh import TOOL
from atlas_toolkit.tools.raster_dem_to_mesh.engine import run, make_sample, input_dependencies
from atlas_toolkit.tools.raster_dem_to_mesh.raster import Raster, read, footprint_polygon
from atlas_toolkit.tools.raster_dem_to_mesh.raster_dem_to_mesh import process, layout, check_mesh


def context(cancelled=lambda: False):
    return JobContext(lambda *a: None, cancelled)


class TerrainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='atlas_dem_')
        self.root = Path(self.tmp.name)
        self.src = make_sample(self.root)
        self.p = TOOL.values(dict(pitch=5., height_mode='target', relief_height=10., target_faces=500))

    def tearDown(self):
        self.tmp.cleanup()

    def field(self, flat=False, masked=False):
        h = np.zeros((10, 20)) if flat else np.tile(np.linspace(-50, 50, 20), (10, 1))
        mask = np.ones(h.shape, bool)
        if masked:
            mask[3:6, 6:9] = False
            mask[:, 15] = False
        return Raster(h, mask, 2000., 1000., True, {'coordinate_units': 'metres'})

    def test_all_methods_and_geometry_modes(self):
        for method in ('faithful', 'smooth', 'faceted', 'terraces'):
            for geometry in ('solid', 'surface'):
                with self.subTest(method=method, geometry=geometry):
                    p = TOOL.values(dict(self.p, method=method, geometry=geometry))
                    raster = read(self.src, p)
                    mesh, data = process(raster, p, context())
                    check_mesh(mesh, geometry == 'solid')
                    self.assertEqual(mesh.is_watertight, geometry == 'solid')
                    self.assertLessEqual(mesh.bounds[1, 0], 95.00001)
                    if geometry == 'solid':
                        self.assertAlmostEqual(mesh.bounds[0, 2], 0., places=6)
                        self.assertGreater(mesh.volume, 0.)
                    if method == 'terraces':
                        vertical = np.abs(mesh.face_normals[:, 2])
                        self.assertTrue(np.all((vertical < 1e-6) | (vertical > 1-1e-6)))

    def test_geographic_scaling_and_separate_heights(self):
        p = TOOL.values(dict(pitch=5., paper='Custom', paper_width=120., paper_height=80., margin=5., padding=5.))
        mesh, data = process(self.field(), p, context())
        self.assertAlmostEqual(data['scale'], .05, places=7)
        self.assertAlmostEqual(mesh.extents[2], 9., places=6)
        p2 = TOOL.values(dict(p, z_factor=3.))
        mesh2, data2 = process(self.field(), p2, context())
        self.assertAlmostEqual(mesh2.extents[2], 19., places=6)
        self.assertEqual(data2['base_height'], 2.)
        self.assertEqual(data2['basal_height'], 2.)
        np.testing.assert_allclose(mesh2.extents[:2], mesh.extents[:2], atol=1e-7)

    def test_target_height_and_percentage_base(self):
        p = TOOL.values(dict(self.p, z_factor=2., base_mode='percent', base_percent=20.))
        mesh, data = process(self.field(), p, context())
        self.assertAlmostEqual(data['relief_height'], 20.)
        self.assertAlmostEqual(data['base_height'], 4.)
        self.assertAlmostEqual(mesh.extents[2], 26.)

    def test_flat_field_and_zero_basal_lift(self):
        for method in ('faithful', 'smooth', 'terraces'):
            p = TOOL.values(dict(self.p, method=method, basal_height=0., base_mode='percent'))
            mesh, data = process(self.field(flat=True), p, context())
            self.assertAlmostEqual(mesh.extents[2], 1.)
            self.assertEqual(data['relief_height'], 0.)

    def test_holes_and_islands_join_through_plate(self):
        mesh, _ = process(self.field(masked=True), self.p, context())
        self.assertTrue(mesh.is_volume)
        self.assertEqual(len(mesh.split(only_watertight=False)), 1)

    def test_rounded_plate_contains_rotated_padding(self):
        for angle in (0., 27., 90.):
            p = TOOL.values(dict(self.p, yaw=angle, corner_radius=15.))
            shape = footprint_polygon(self.field().valid, 2000, 1000)
            data = layout(shape, p)
            self.assertTrue(data['plate'].buffer(1e-6).covers(data['terrain'].buffer(p.padding)))
            mesh, actual = process(self.field(), p, context())
            self.assertLessEqual(mesh.extents[0], 190.000001)
            self.assertLessEqual(mesh.extents[1], 277.000001)

    def test_elevation_rotation_relative_to_fixed_plate_all_methods(self):
        # A rising eastward ramp must turn clockwise, while the same plate stays page-aligned.
        for method in ('faithful', 'smooth', 'faceted', 'terraces'):
            for yaw in (0., 35., -90.):
                with self.subTest(method=method, yaw=yaw):
                    p = TOOL.values(dict(self.p, method=method, yaw=yaw, plate_fit='full', target_faces=100,
                                         paper='Custom', paper_width=120., paper_height=120.))
                    mesh, data = process(self.field(), p, context())
                    self.assertEqual(data['yaw'], yaw)
                    np.testing.assert_allclose(mesh.bounds[:, :2], [[-50., -50.], [50., 50.]], atol=1e-6)
                    keep = (mesh.face_normals[:, 2] > .1) & (mesh.triangles_center[:, 2] > data['base_height']+.1)
                    triangles = mesh.triangles[keep]
                    weights = mesh.area_faces[keep]*mesh.face_normals[keep, 2]
                    # Integrate over triangle areas: boolean/faceted triangulation must not bias the slope.
                    sums = triangles.sum(axis=1)
                    moments = (np.einsum('ni,nj->nij', sums, sums) +
                               np.einsum('nki,nkj->nij', triangles, triangles))/12
                    mean = np.average(triangles.mean(axis=1), axis=0, weights=weights)
                    covariance = np.average(moments, axis=0, weights=weights)-np.outer(mean, mean)
                    gradient = np.linalg.solve(covariance[:2, :2], covariance[:2, 2])
                    expected = [np.cos(np.radians(yaw)), -np.sin(np.radians(yaw))]
                    np.testing.assert_allclose(gradient/np.linalg.norm(gradient), expected, atol=.03)
                    self.assertTrue(data['plate'].buffer(1e-6).covers(data['terrain'].buffer(p.padding)))

    def test_invalid_layout_and_geometry_budget(self):
        for values in ({'padding': 200.}, {'corner_radius': 200.}, {'margin': 140.}, {'pitch': .05}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                process(self.field(), TOOL.values(dict(self.p, **values)), context())

    def test_point_contact_rejected(self):
        import trimesh
        # Two triangles meeting only at one vertex have disconnected incident fans.
        mesh = trimesh.Trimesh([[0,0,0],[1,0,0],[0,1,0],[-1,0,0],[0,-1,0]], [[0,1,2],[0,3,4]], process=False)
        with self.assertRaisesRegex(ValueError, 'point contact'):
            check_mesh(mesh, False)

    def test_explicit_point_contact_clearance(self):
        field = self.field()
        field.valid[:] = False
        field.valid[:5,:10] = True
        field.valid[5:,10:] = True
        # Align the 20 × 10 sampled cells with the supplied mask.
        p = TOOL.values(dict(self.p,paper='Custom',paper_width=120.,paper_height=80.,margin=5.,padding=5.))
        with self.assertRaisesRegex(ValueError,'point contacts'):
            process(field,p,context())
        p=TOOL.values(dict(p,contact_trim=.02))
        mesh,data=process(field,p,context())
        check_mesh(mesh)
        self.assertTrue(mesh.is_volume)

    def test_missing_area_modes(self):
        raster=self.field(masked=True)
        for mode in ('expose','gaps','minimum'):
            p=TOOL.values(dict(self.p,missing=mode,gap_size=100.))
            mesh,data=process(raster,p,context())
            self.assertTrue(mesh.is_volume)
        p=TOOL.values(dict(self.p,footprint='rectangle'))
        mesh,data=process(raster,p,context())
        self.assertTrue(mesh.is_volume)

    def test_export_signatures_frames_and_dimensions(self):
        from atlas_toolkit.geom.surface import load_surface, read_frame
        from atlas_toolkit.core.selftest import signed
        p = TOOL.values(dict(self.p, yaw=35., plate_fit='full'))
        for ext in ('.stl', '.obj', '.ply'):
            dst = self.root/('export'+ext)
            result = run(context(), self.src, dst, p)
            expected, _ = process(read(self.src, p), p, context())
            mesh = load_surface(dst).as_trimesh()
            check_mesh(mesh)
            self.assertTrue(signed(TOOL, dst))
            self.assertEqual(read_frame(dst)['up'], '+z')
            np.testing.assert_allclose(mesh.extents, result.data['dimensions_mm'], atol=2e-5)
            # Format reload must preserve the actual turned terrain, not just its bounding box.
            from scipy.spatial import cKDTree
            distances, _ = cKDTree(mesh.vertices).query(expected.vertices)
            self.assertLess(distances.max(), 2e-5)
            self.assertIn('35° clockwise; plate aligned with page', result.report)

    def test_dry_run_cancellation_and_failure_preserve_destination(self):
        dst = self.root/'existing.stl'
        dst.write_bytes(b'original')
        with self.assertRaises(Cancelled):
            run(context(lambda: True), self.src, dst, self.p)
        self.assertEqual(dst.read_bytes(), b'original')
        result = run(JobContext(lambda *a: None, lambda: False, mode='draft'), self.src, dst, self.p)
        self.assertFalse(result.outputs)
        self.assertEqual(dst.read_bytes(), b'original')
        with self.assertRaises(ValueError):
            run(context(), self.src, dst, TOOL.values(dict(self.p, band=2)))
        self.assertEqual(dst.read_bytes(), b'original')

    def write_raster(self, name, data, crs=None, transform=None, scale=None):
        import rasterio
        from rasterio.io import MemoryFile
        from rasterio.transform import from_origin
        path = self.root/name
        with MemoryFile() as mem:
            with mem.open(driver='GTiff', width=data.shape[1], height=data.shape[0], count=1, dtype=data.dtype,
                          crs=crs, transform=transform or from_origin(0,1000,10,10)) as ds:
                ds.write(data, 1)
                if scale:
                    ds.scales = (scale[0],)
                    ds.offsets = (scale[1],)
            path.write_bytes(mem.read())
        return path

    def test_band_scale_and_offset_and_background(self):
        data = np.arange(200, dtype=np.float32).reshape(10,20)
        path = self.write_raster('scaled.tif', data, 'EPSG:3763', scale=(2.,100.))
        self.assertEqual(read(path, self.p).metadata['source_range'], [100.,498.])
        p = TOOL.values(dict(self.p, exclude_background=True, background=0.))
        self.assertFalse(read(path, p).valid[0,0])
        p = TOOL.values(dict(p, metadata_scale=False))
        self.assertEqual(read(path, p).metadata['source_range'], [1.,199.])
        with self.assertRaisesRegex(ValueError, 'No valid'):
            read(path, TOOL.values(dict(p, background=100., background_tolerance=100.)))

    def test_crs_reprojection_rotated_transform_and_feet(self):
        from rasterio.transform import from_origin, Affine
        data = np.arange(200, dtype=np.float32).reshape(10,20)
        geographic = self.write_raster('degrees.tif', data, 'EPSG:4326', from_origin(-8.7,41.8,.001,.001))
        raster = read(geographic, self.p)
        self.assertTrue(raster.geographic)
        self.assertGreater(raster.width, 1000.)
        self.assertIn('Azimuthal_Equidistant', raster.metadata['working_crs'])
        rotated = self.write_raster('rotated.tif', data, 'EPSG:3763', Affine.translation(0,1000)*Affine.rotation(20)*Affine.scale(10,-10))
        self.assertGreater(read(rotated,self.p).width, 100.)
        feet = self.write_raster('feet.tif', data, 'EPSG:2277', from_origin(1000000,1000000,10,10))
        raster = read(feet, TOOL.values(dict(self.p, elevation_units='ft')))
        self.assertAlmostEqual(raster.width, 200*1200/3937)
        self.assertAlmostEqual(raster.heights.max(), 199*.3048)

    def test_sidecar_dependencies(self):
        import rasterio
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=False):
            with rasterio.open(self.src,'r+') as ds:
                mask = np.ones((ds.height,ds.width),np.uint8)*255
                mask[:2] = 0
                ds.write_mask(mask)
        self.assertIn(self.src.with_suffix('.tif.msk'), input_dependencies(self.src,self.p))

    def test_custom_paper_renderers(self):
        from atlas_toolkit.core.registry import registry
        registry()
        from atlas_toolkit.geom.camera import frame_for
        from atlas_toolkit.tools.contour_lines import TOOL as contour
        from atlas_toolkit.tools.plotter_vision import TOOL as vision
        from atlas_toolkit.geom.mesh import save_mesh
        mesh,_ = process(self.field(),self.p,context())
        src = self.root/'model.stl'
        save_mesh(mesh,src,TOOL)
        for tool in (contour,vision):
            p=tool.values(dict(tool.test_values,paper='Custom',paper_width=123.,paper_height=234.))
            result=tool.engine_fn()(context(),src,None,p)
            drawing=result.preview['views'][0]['drawing']
            self.assertEqual((drawing.width,drawing.height),(123.,234.) if p.orientation=='portrait' else (234.,123.))

    def test_workflow_cache_sidecar_invalidation(self):
        from atlas_toolkit.core import workflow as w
        from atlas_toolkit.core.registry import registry
        reg=registry()
        wf=w.Workflow(name='DEM cache')
        inp=wf.add(w.input_node('image'))
        tool=wf.add(w.tool_node(TOOL,values=dict(self.p)))
        save=wf.add(w.save_node(where='folder',folder=str(self.root/'out')))
        wf.connect(inp.id,tool.id)
        wf.connect(tool.id,save.id)
        cache=w.WorkflowCache(self.root/'cache')
        side=self.root/'dependency.txt'
        side.write_text('first')
        with patch('atlas_toolkit.tools.raster_dem_to_mesh.engine.input_dependencies',return_value=[side]):
            a=w.run_workflow(wf,self.src,lambda *a:None,lambda:False,reg=reg,cache=cache)
            b=w.run_workflow(wf,self.src,lambda *a:None,lambda:False,reg=reg,cache=cache)
            side.write_text('second changed')
            c=w.run_workflow(wf,self.src,lambda *a:None,lambda:False,reg=reg,cache=cache)
        self.assertEqual(a.stat('steps').value,1)
        self.assertEqual(b.stat('cached').value,1)
        self.assertEqual(c.stat('steps').value,1)


class TerrainUiTests(unittest.TestCase):
    def test_placement_rotation_and_theme(self):
        import time
        from types import SimpleNamespace
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication
        from atlas_toolkit.ui.theme import Theme
        from atlas_toolkit.tools.raster_dem_to_mesh.viewer import PlacementView
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(prefix='atlas_dem_ui_') as folder:
            src=make_sample(Path(folder))
            state=dict(TOOL.defaults())
            page=SimpleNamespace(theme=Theme(),values=lambda:dict(state),tool=TOOL,
                                 form=SimpleNamespace(set_values=lambda v:state.update(v)),
                                 ctx=SimpleNamespace(lacking=lambda *a:[]),busy=lambda:False,
                                 _settings_changed=lambda:None,preview_selected=lambda **kw:None,
                                 draft_job=None,cancel_preview=lambda:None,_current_row=lambda:None)
            view=PlacementView(page)
            view.resize(800,600)
            view.show()
            view.set_input(src,None)
            deadline=time.monotonic()+10
            while view.data is None and time.monotonic()<deadline:
                app.processEvents()
                time.sleep(.01)
            self.assertIsNotNone(view.data,view.message)
            view.commit(35.)
            self.assertEqual(state['yaw'],35.)
            self.assertIn('35.0',view.info_text())
            for theme in ('dark','light'):
                view.set_theme(Theme(theme))
                app.processEvents()
                self.assertFalse(view.grab().isNull())
            view.commit(0.)
            self.assertEqual(state['yaw'],0.)
            view.timer.stop()
            view.close()


if __name__ == '__main__':
    unittest.main()
