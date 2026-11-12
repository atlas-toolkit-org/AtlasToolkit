"""Image preparation, calibrated geometry, source safety and all Atlas entry points."""
import _isolate  # first: settings and caches stay in a temporary folder
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image
from atlas_toolkit.core.job import JobContext, Cancelled
from atlas_toolkit.tools.image_to_mesh import TOOL
from atlas_toolkit.tools.image_to_mesh.image_to_mesh import decode, read, prepare, orient
from atlas_toolkit.tools.image_to_mesh.engine import run, make_sample, inspect
from atlas_toolkit.geom.heightfield.mesh import process, check_mesh
from atlas_toolkit.geom.heightfield.source import outline


def context(mode='run', cancelled=lambda: False):
    return JobContext(lambda *a: None, cancelled, mode=mode)


class ImageMeshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='atlas_image_')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = make_sample(self.root)
        self.p = TOOL.values(dict(pitch=5., paper='A5', maximum_edge=5., boundary_error=0.))

    def png(self, data, name='source.png', **kwargs):
        path = self.root/name
        Image.fromarray(data).save(path, **kwargs)
        return path

    def numeric(self, data, name='numeric.tif', **kwargs):
        import rasterio
        from rasterio.transform import from_origin
        path = self.root/name
        bands = data[None] if data.ndim == 2 else np.moveaxis(data, -1, 0)
        with rasterio.open(path, 'w', driver='GTiff', width=bands.shape[2], height=bands.shape[1],
                           count=len(bands), dtype=data.dtype, transform=from_origin(0, 100, 1, 1), **kwargs) as ds:
            ds.write(bands)
        return path

    def test_grayscale_bypasses_color_conversion_and_preserves_16bit(self):
        data = np.arange(120, dtype=np.uint16).reshape(10, 12)*503
        path = self.png(data)
        for method in ('weighted', 'average', 'lightness', 'red', 'green', 'blue'):
            with self.subTest(method=method):
                raster = read(path, TOOL.values(dict(self.p, grayscale=method)))
                np.testing.assert_allclose(raster.heights, data/65535., atol=1e-15)
                self.assertEqual(raster.metadata['bit_depth'], 16)
        self.assertEqual(inspect(path).preview['views'][0]['title'], 'Original')

    def test_grayscale_methods_and_channels(self):
        data = np.array([[[255, 0, 0], [0, 255, 0]], [[0, 0, 255], [255, 255, 255]]], dtype=np.uint8)
        path = self.png(data)
        expected = dict(weighted=[[.2125, .7154], [.0721, 1]], average=[[1/3, 1/3], [1/3, 1]],
                        lightness=[[.5, .5], [.5, 1]], red=[[1, 0], [0, 1]],
                        green=[[0, 1], [0, 1]], blue=[[0, 0], [1, 1]])
        for method, values in expected.items():
            np.testing.assert_allclose(read(path, TOOL.values(dict(self.p, grayscale=method))).heights, values)

    def test_float_precision_ranges_and_geographic_metadata_ignored(self):
        data = np.linspace(.20000001, .80000001, 120).reshape(10, 12)
        path = self.numeric(data, crs='EPSG:3763')
        raster = read(path, self.p)
        np.testing.assert_array_equal(raster.heights, data)
        self.assertFalse(raster.geographic)
        self.assertEqual((raster.width, raster.height), (12., 10.))
        outside = self.numeric(data*100-50, name='outside.tif')
        with self.assertRaisesRegex(ValueError, 'Auto levels'):
            read(outside, self.p)
        stretched = read(outside, TOOL.values(dict(self.p, auto_levels=True)))
        self.assertEqual(stretched.heights.min(), 0.)
        self.assertEqual(stretched.heights.max(), 1.)
        explicit = read(outside, TOOL.values(dict(self.p, input_range='custom', source_black=-50., source_white=50.)))
        np.testing.assert_allclose(explicit.heights, data)

    def test_supported_ordinary_formats_palette_and_bilevel(self):
        rgb = np.arange(360, dtype=np.uint8).reshape(10, 12, 3)
        for ext in ('jpg', 'bmp', 'webp', 'png', 'tif'):
            path = self.png(rgb, 'color.'+ext)
            raster = read(path, self.p)
            self.assertEqual(raster.heights.shape, (10, 12))
            self.assertTrue(np.isfinite(raster.heights).all())
        palette = Image.fromarray(rgb).quantize(colors=16)
        path = self.root/'palette.png'
        palette.save(path)
        self.assertEqual(read(path, self.p).heights.shape, (10, 12))
        Image.fromarray((rgb[..., 0] > 128)).save(self.root/'binary.png')
        self.assertEqual(set(np.unique(read(self.root/'binary.png', self.p).heights)), {0., 1.})

    def test_tonal_pipeline_and_neutral_constant_auto_levels(self):
        values = np.linspace(0, 1, 20).reshape(4, 5)
        samples = values[..., None]
        alpha = np.ones_like(values)
        info = dict(native_white=1.)
        p = TOOL.values(dict(self.p, black_level=.1, white_level=.9, brightness=.05, contrast=1.5, gamma=1.7, invert=True))
        gray, valid, _ = prepare(samples, alpha, info, p)
        levels = np.clip((values-.1)/.8, 0, 1)
        expected = 1-np.clip((levels-.5)*1.5+.55, 0, 1)**1.7
        np.testing.assert_allclose(gray, expected)
        self.assertTrue(valid.all())
        constant = np.full((4, 5, 1), .4)
        neutral, _, _ = prepare(constant, alpha, info, TOOL.values(dict(self.p, auto_levels=True)))
        np.testing.assert_allclose(neutral, .4)
        for change in (dict(black_level=.7, white_level=.3), dict(input_range='custom', source_black=2., source_white=1.)):
            with self.assertRaises(ValueError):
                prepare(samples, alpha, info, TOOL.values(dict(self.p, **change)))

    def test_alpha_mask_compositing_background_and_masked_smoothing(self):
        rgba = np.full((12, 16, 4), 255, np.uint8)
        rgba[3:6, 4:8, 3] = 0
        rgba[3:6, 4:8, :3] = 0
        rgba[8, 10, 3] = 128
        path = self.png(rgba)
        raster = read(path, self.p)
        self.assertFalse(raster.valid[4, 5])
        self.assertTrue(raster.valid[8, 10])
        self.assertEqual(len(outline(raster).interiors), 1)
        excluded = read(path, TOOL.values(dict(self.p, alpha_threshold=.6)))
        self.assertFalse(excluded.valid[8, 10])
        for mode, value in (('white', 1.), ('black', 0.)):
            r = read(path, TOOL.values(dict(self.p, alpha_mode=mode)))
            self.assertTrue(r.valid.all())
            self.assertEqual(r.heights[4, 5], value)
        smooth = read(path, TOOL.values(dict(self.p, denoise=2.)))
        np.testing.assert_allclose(smooth.heights[smooth.valid], 1.)
        with self.assertRaisesRegex(ValueError, 'No valid'):
            read(path, TOOL.values(dict(self.p, exclude_background=True, background=1.)))
        rgba[..., 3] = 0
        with self.assertRaisesRegex(ValueError, 'No valid'):
            read(self.png(rgba, 'empty.png'), self.p)
        samples = np.linspace(20, 30, 20).reshape(4, 5, 1)
        alpha = np.ones((4, 5))
        alpha[:, 0] = 0
        for mode, value in (('white', 1.), ('black', 0.)):
            p = TOOL.values(dict(self.p, auto_levels=True, alpha_mode=mode))
            gray, valid, _ = prepare(samples, alpha, dict(native_white=1.), p)
            np.testing.assert_allclose(gray[:, 0], value)
            self.assertTrue(valid.all())

    def test_exif_orientation_mirroring_and_quarter_turn(self):
        values = np.arange(12, dtype=np.uint8).reshape(3, 4)*20
        exif = Image.Exif()
        exif[274] = 6
        path = self.png(values, exif=exif)
        np.testing.assert_allclose(read(path, self.p).heights, np.rot90(values, -1)/255.)
        p = TOOL.values(dict(self.p, quarter_turns='1', mirror_x=True, mirror_y=True))
        expected = np.rot90(values, -2)[::-1, ::-1]/255.
        np.testing.assert_allclose(read(path, p).heights, expected)
        self.assertEqual((read(path, p).width, read(path, p).height), (4., 3.))
        for orientation in range(1, 9):
            image = Image.fromarray(values)
            image.getexif()[274] = orientation
            from PIL import ImageOps
            np.testing.assert_array_equal(orient(values, orientation), np.asarray(ImageOps.exif_transpose(image)))
        colors = np.arange(36, dtype=np.uint8).reshape(3, 4, 3)*6
        for mode in ('L', 'RGB', 'P', 'CMYK'):
            with self.subTest(tiff_mode=mode):
                image = Image.fromarray(colors).convert(mode)
                expected = np.asarray(image.convert('L' if mode == 'L' else 'RGB'))
                if expected.ndim == 2:
                    expected = expected[..., None]
                path = self.root/(mode+'.tif')
                image.save(path, exif=exif)
                samples, _, _ = decode(path)
                np.testing.assert_array_equal(samples, np.rot90(expected, -1))

    def test_height_calibration_all_methods_and_sampling(self):
        path = self.png(np.full((12, 16), 102, np.uint8))
        for method in ('faithful', 'smooth', 'faceted', 'terraces'):
            for sampling in ('grid', 'adaptive'):
                for geometry in ('surface', 'solid'):
                    with self.subTest(method=method, sampling=sampling, geometry=geometry):
                        p = TOOL.values(dict(self.p, method=method, sampling=sampling, geometry=geometry,
                                             relief_height=10., brightness=.1, terrace_step=.5, target_faces=500))
                        mesh, data = process(read(path, p), p, context())
                        check_mesh(mesh, solid=geometry == 'solid')
                        expected = 5.+(4. if geometry == 'solid' else 0.)
                        self.assertAlmostEqual(mesh.bounds[1, 2], expected, places=5)
                        self.assertAlmostEqual(data['basal_height'], 2. if geometry == 'solid' else 0.)

    def test_black_surface_terraces_and_masked_solids(self):
        black = self.png(np.zeros((12, 16), np.uint8))
        for method in ('faithful', 'smooth', 'terraces'):
            p = TOOL.values(dict(self.p, geometry='surface', method=method))
            mesh, _ = process(read(black, p), p, context())
            np.testing.assert_allclose(mesh.vertices[:, 2], 0.)
        rgba = np.full((12, 16, 4), 200, np.uint8)
        rgba[..., 3] = 255
        rgba[4:7, 5:8, 3] = 0
        for mode in ('expose', 'gaps', 'minimum'):
            p = TOOL.values(dict(self.p, missing=mode, gap_size=100.))
            mesh, _ = process(read(self.png(rgba, 'masked.png'), p), p, context())
            self.assertTrue(mesh.is_volume)

    def test_cache_preparation_keys_and_exact_downsampled_outline(self):
        a = read(self.src, self.p)
        self.assertIs(a, read(self.src, self.p))
        for values in (dict(brightness=.1), dict(gamma=1.2), dict(mirror_x=True), dict(quarter_turns='1')):
            b = read(self.src, TOOL.values(dict(self.p, **values)))
            self.assertIsNot(a, b)
        rgba = np.full((80, 100, 4), 255, np.uint8)
        rgba[30:35, 40:45, 3] = 0
        path = self.png(rgba)
        full, small = read(path, self.p), read(path, self.p, max_size=12)
        self.assertLessEqual(max(small.heights.shape), 12)
        self.assertAlmostEqual(outline(full).area, outline(small).area)
        self.assertEqual(len(outline(small).interiors), 1)

    def test_exports_preview_failure_cancellation_and_source_safety(self):
        from atlas_toolkit.core.registry import registry
        from atlas_toolkit.core.selftest import signed
        from atlas_toolkit.geom.surface import load_surface, read_frame
        registry()
        digest = hashlib.sha256(self.src.read_bytes()).digest()
        for ext in ('.stl', '.obj', '.ply'):
            path = self.root/('export'+ext)
            result = run(context(), self.src, path, self.p)
            saved = load_surface(path).as_trimesh()
            self.assertTrue(signed(TOOL, path))
            self.assertTrue(saved.is_volume)
            self.assertEqual(read_frame(path)['up'], '+z')
            np.testing.assert_allclose(saved.extents, result.data['dimensions_mm'], atol=2e-5)
            self.assertEqual([v['title'] for v in result.preview['views']], ['Prepared heightmap', 'Relief mesh'])
        path.write_bytes(b'existing')
        for mode in ('draft', 'analyse'):
            self.assertFalse(run(context(mode), self.src, path, self.p).outputs)
            self.assertEqual(path.read_bytes(), b'existing')
        with self.assertRaises(Cancelled):
            run(context(cancelled=lambda: True), self.src, path, self.p)
        with patch('atlas_toolkit.geom.heightfield.engine.check_mesh', side_effect=ValueError('export check failed')):
            with self.assertRaises(ValueError):
                run(context(), self.src, path, self.p)
        self.assertEqual(path.read_bytes(), b'existing')
        with self.assertRaisesRegex(ValueError, 'source raster'):
            run(context(), self.src, self.src, self.p)
        self.assertEqual(hashlib.sha256(self.src.read_bytes()).digest(), digest)

    def test_dem_split_and_legacy_workflow_validation(self):
        from atlas_toolkit.tools.raster_dem_to_mesh import TOOL as dem
        from atlas_toolkit.tools.raster_dem_to_mesh.raster import read as read_dem
        from atlas_toolkit.core import workflow as w
        from atlas_toolkit.core.registry import registry
        with self.assertRaisesRegex(ValueError, 'Image to Mesh'):
            dem.values(dict(source_mode='heightmap'))
        with self.assertRaisesRegex(ValueError, 'Image to Mesh'):
            read_dem(self.src, dem.defaults())
        self.assertNotIn('invert', dem.defaults())
        self.assertNotIn('pixel_aspect', dem.defaults())
        self.assertIn(dem.values(dict(source_mode='auto')).source_mode, ('auto', 'gis'))
        from atlas_toolkit.core import presets as P
        self.assertIsNone(P.match(dem, dem.values(dict(yaw=12.)),
                                  [P.Preset('Old heightmap', dem.id, {'source_mode': 'heightmap'})]))
        wf = w.Workflow(name='Legacy heightmap')
        inp = wf.add(w.input_node('image'))
        step = wf.add(w.tool_node(dem))
        step.values['source_mode'] = 'heightmap'
        save = wf.add(w.save_node(where='folder', folder=str(self.root/'out')))
        wf.connect(inp.id, step.id)
        wf.connect(step.id, save.id)
        self.assertTrue(any('Image to Mesh' in i.message for i in w.check(wf, registry())))

    def test_cli_and_workflow_cache_use_preparation(self):
        from atlas_toolkit.core.cli import build_parser
        from atlas_toolkit.core import workflow as w
        from atlas_toolkit.core.registry import registry
        args = build_parser().parse_args(['run', TOOL.id, str(self.src), '--quarter-turns', '1', '--gamma', '1.4', '--mirror-x'])
        values = TOOL.values({p.key: getattr(args, p.key) for p in TOOL.param_list})
        self.assertEqual((values.quarter_turns, values.gamma, values.mirror_x), ('1', 1.4, True))
        wf = w.Workflow(name='Image cache')
        inp = wf.add(w.input_node('image'))
        step = wf.add(w.tool_node(TOOL, values=self.p))
        save = wf.add(w.save_node(where='folder', folder=str(self.root/'out')))
        wf.connect(inp.id, step.id)
        wf.connect(step.id, save.id)
        restored = w.Workflow.from_dict(wf.to_dict())
        self.assertEqual(restored.nodes[step.id].values, step.values)
        cache = w.WorkflowCache(self.root/'cache')
        reg = registry()
        a = w.run_workflow(wf, self.src, lambda *a: None, lambda: False, reg=reg, cache=cache)
        b = w.run_workflow(wf, self.src, lambda *a: None, lambda: False, reg=reg, cache=cache)
        step.values['brightness'] = .1
        c = w.run_workflow(wf, self.src, lambda *a: None, lambda: False, reg=reg, cache=cache)
        self.assertEqual((a.stat('steps').value, b.stat('cached').value, c.stat('steps').value), (1, 1, 1))
