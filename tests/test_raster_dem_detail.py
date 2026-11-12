"""Adaptive error, bounded input, plate locking and large-source regression checks."""
import _isolate  # first: settings and caches stay in a temporary folder
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from atlas_toolkit.core.job import JobContext, Cancelled
from atlas_toolkit.tools.raster_dem_to_mesh import TOOL
from atlas_toolkit.tools.raster_dem_to_mesh.raster import Raster, read, footprint_polygon
from atlas_toolkit.tools.raster_dem_to_mesh.raster_dem_to_mesh import process, layout, check_mesh


def context(mode='run',cancelled=lambda:False):
    return JobContext(lambda *a:None,cancelled,mode=mode)


class DetailTests(unittest.TestCase):
    def field(self,kind='ridge',masked=False):
        y,x = np.mgrid[-1:1:31j,-1:1:41j]
        if kind=='flat':
            h=np.full_like(x,-50.)
        elif kind=='ramp':
            h=100*x+30*y-40
        else:
            h=200*np.exp(-40*x*x)+120*np.exp(-50*((x-.5)**2+(y-.4)**2))-100*np.exp(-50*((x+.4)**2+y*y))
        valid=np.ones(h.shape,bool)
        if masked:
            valid[10:15,14:19]=False
            valid[:,30]=False
        return Raster(h,valid,2000.,1500.,True,dict(source_range=[float(h[valid].min()),float(h[valid].max())]))

    def values(self,**values):
        base=dict(sampling='adaptive',height_mode='target',relief_height=10.,maximum_edge=5.,boundary_error=0.)
        base.update(values)
        return TOOL.values(base)

    def test_adaptive_error_all_shapes_and_output_modes(self):
        for shape in ('flat','ramp','ridge'):
            for geometry in ('surface','solid'):
                with self.subTest(shape=shape,geometry=geometry):
                    p=self.values(geometry=geometry)
                    mesh,data=process(self.field(shape,masked=True),p,context())
                    check_mesh(mesh,solid=geometry=='solid')
                    self.assertLessEqual(data['maximum_error_mm'],p.vertical_error+1e-5)
                    self.assertLessEqual(data['rms_error_mm'],data['maximum_error_mm']+1e-10)

    def test_finer_tolerance_improves_measured_error(self):
        coarse, a=process(self.field(),self.values(vertical_error=.08),context())
        fine,b=process(self.field(),self.values(vertical_error=.01),context())
        self.assertLessEqual(b['maximum_error_mm'],.01001)
        self.assertLess(b['maximum_error_mm'],a['maximum_error_mm'])
        self.assertGreater(len(fine.faces),0)

    def test_maximum_edge_is_conforming(self):
        p=self.values(geometry='surface',maximum_edge=3.)
        mesh,_=process(self.field('flat'),p,context())
        self.assertLessEqual(np.linalg.norm(np.diff(mesh.vertices[mesh.edges_unique,:2],axis=1)[:,0],axis=1).max(),3.00001)
        check_mesh(mesh,False)

    def test_stitched_solid_avoids_volume_boolean(self):
        p=self.values(geometry='solid')
        with patch('atlas_toolkit.tools.raster_dem_to_mesh.raster_dem_to_mesh.solid_union',side_effect=AssertionError('unexpected boolean')):
            mesh,_=process(self.field('ramp'),p,context())
        self.assertTrue(mesh.is_volume)

    def test_locked_scale_survives_plate_changes_and_rotation(self):
        raster=self.field('ramp')
        p=self.values(plate_fit='custom',plate_width=160.,plate_height=200.,lock_scale=True,terrain_width=90.)
        a,da=process(raster,p,context())
        b,db=process(raster,TOOL.values(dict(p,plate_width=180.,plate_height=230.,yaw=35.)),context())
        self.assertAlmostEqual(da['scale'],db['scale'])
        self.assertAlmostEqual(da['relief_height'],db['relief_height'])
        self.assertAlmostEqual(da['base_height'],db['base_height'])
        self.assertAlmostEqual(a.extents[2],b.extents[2])
        shape=footprint_polygon(raster.valid,raster.width,raster.height)
        with self.assertRaisesRegex(ValueError,'Locked terrain'):
            layout(shape,TOOL.values(dict(p,terrain_width=300.)))
        with self.assertRaisesRegex(ValueError,'available page'):
            layout(shape,TOOL.values(dict(p,plate_width=300.)))

    def test_triangle_budget_and_memory_preflight(self):
        p=self.values(triangle_budget=1000,maximum_edge=.1)
        with self.assertRaisesRegex(ValueError,'budget'):
            process(self.field('ramp'),p,context())
        self.assertEqual(TOOL.values({'triangle_budget':8000000}).triangle_budget,8000000)
        with patch('atlas_toolkit.tools.raster_dem_to_mesh.resources.available_memory',return_value=1024):
            with self.assertRaisesRegex(ValueError,'RAM'):
                process(self.field(),self.values(),context())

    def test_cancel_during_adaptive_construction(self):
        ctx=context(cancelled=lambda:True)
        with self.assertRaises(Cancelled):
            process(self.field(),self.values(),ctx)

    def test_draft_contact_preview_does_not_change_final_policy(self):
        raster=self.field()
        raster.valid[10:20,10:20]=np.indices((10,10)).sum(axis=0)%2==0
        p=TOOL.values(dict(pitch=.25,preview_quality='balanced'))
        draft,data=process(raster,p,context('draft'))
        check_mesh(draft)
        self.assertLess(len(draft.faces),120000)
        self.assertEqual(p.contact_trim,0.)
        with self.assertRaisesRegex(ValueError,'point contacts'):
            process(raster,p,context())

    def test_bounded_read_over_25_million_cells_and_exact_statistics(self):
        import rasterio
        from rasterio.windows import Window
        from rasterio.transform import from_origin
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'large.tif'
            with rasterio.open(path,'w',driver='GTiff',width=5001,height=5001,count=1,dtype='uint16',
                               crs='EPSG:3763',transform=from_origin(0,100000,20,20),compress='deflate',tiled=True) as ds:
                for start in range(0,5001,256):
                    block=np.full((min(256,5001-start),5001),17,np.uint16)
                    if start==0:
                        block[0,0]=999
                    ds.write(block,1,window=Window(0,start,5001,len(block)))
            p=TOOL.defaults()
            a=read(path,p,max_size=128)
            self.assertLessEqual(max(a.heights.shape),128)
            self.assertLess(a.heights.nbytes+a.valid.nbytes,200000)
            self.assertEqual(a.metadata['source_range'],[17.,999.])
            with patch('atlas_toolkit.tools.raster_dem_to_mesh.raster._read',side_effect=AssertionError('cache miss')):
                self.assertIs(read(path,p,max_size=128),a)
            self.assertFalse(a.heights.flags.writeable)

    def test_adaptive_export_precision_all_formats(self):
        from atlas_toolkit.tools.raster_dem_to_mesh.engine import make_sample,run
        from atlas_toolkit.geom.surface import load_surface
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            src=make_sample(root)
            for ext in ('stl','obj','ply'):
                result=run(context(),src,root/f'adaptive.{ext}',self.values(yaw=35.,maximum_edge=3.))
                mesh=load_surface(result.outputs[0]).as_trimesh()
                check_mesh(mesh)
                self.assertIn('measured Z error',result.report)

    def test_coarse_grid_retains_native_holes_and_tiny_islands(self):
        valid=np.zeros((101,121),bool)
        valid[5:80,5:90]=True
        valid[30:35,30:35]=False
        valid[90,110]=True
        h=np.full(valid.shape,10.)
        raster=Raster(h,valid,1210.,1010.,True,dict(source_range=[10.,10.]))
        p=TOOL.values(dict(pitch=8.,boundary_error=0.,geometry='surface'))
        mesh,data=process(raster,p,context())
        from shapely import polygons as triangle_polygons,union_all
        coverage=union_all(triangle_polygons(mesh.vertices[mesh.faces,:2]))
        boundary=footprint_polygon(valid,raster.width,raster.height)
        self.assertAlmostEqual(coverage.area,boundary.area*data['scale']**2,places=4)
        self.assertEqual(len(coverage.geoms),2)
        self.assertEqual(sum(len(poly.interiors) for poly in coverage.geoms),1)

    def test_streamed_native_outline_and_cache_invalidation(self):
        import rasterio
        from rasterio.transform import from_origin,Affine
        from atlas_toolkit.tools.raster_dem_to_mesh.raster import outline
        with tempfile.TemporaryDirectory() as folder:
            for crs,tf in [('EPSG:3763',from_origin(0,10000,20,20)),
                           ('EPSG:4326',from_origin(-8.7,41.8,.001,.001)),
                           ('EPSG:3763',Affine.translation(0,10000)@Affine.rotation(20)@Affine.scale(20,-20))]:
                path=Path(folder)/(crs.replace(':','')+str(tf.b)+'.tif')
                data=np.ones((300,1100),np.uint16)
                data[50:70,40:60]=0
                data[290,1095]=0
                with rasterio.open(path,'w',driver='GTiff',width=1100,height=300,count=1,dtype='uint16',crs=crs,transform=tf,nodata=0) as ds:
                    ds.write(data,1)
                p=TOOL.defaults()
                raster=read(path,p,max_size=64)
                shape=outline(raster)
                self.assertTrue(shape.is_valid)
                self.assertEqual(len(shape.interiors),2)
                self.assertLess(max(raster.heights.shape),65)
                old=raster
                with rasterio.open(path,'r+') as ds:
                    data[0,0]=99
                    ds.write(data,1)
                raster=read(path,p,max_size=64)
                self.assertIsNot(raster,old)
                self.assertEqual(raster.metadata['source_range'],[1.,99.])
