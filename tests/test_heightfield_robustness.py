"""Regressions for the reported fitting, topology, precision and budget failures."""
import _isolate  # first: settings and caches stay in a temporary folder
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from atlas_toolkit.core.job import JobContext,Job,execute
from atlas_toolkit.geom.heightfield.source import Raster,outline
from atlas_toolkit.geom.heightfield.mesh import layout,process,check_mesh,contact_clearance
from atlas_toolkit.tools.raster_dem_to_mesh import TOOL as DEM
from atlas_toolkit.tools.image_to_mesh import TOOL as IMAGE


def ctx(mode='run'):
    return JobContext(lambda *a:None,lambda:False,mode=mode)


class RobustnessTests(unittest.TestCase):
    def test_dem_native_and_preview_lock_share_authoritative_footprint(self):
        import rasterio
        from rasterio.transform import from_origin
        from atlas_toolkit.tools.raster_dem_to_mesh.raster import read
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'masked.tif'
            y,x=np.mgrid[:650,:900]
            data=np.where(((x-450)/390)**2+((y-320)/280)**2<1,100+x//10,65535).astype('uint16')
            with rasterio.open(path,'w',driver='GTiff',width=900,height=650,count=1,dtype='uint16',nodata=65535,
                               crs='EPSG:3763',transform=from_origin(0,100000,20,20)) as ds:ds.write(data,1)
            native=read(path,DEM.defaults())
            preview=read(path,DEM.defaults(),max_size=128)
            self.assertTrue(outline(native).equals(outline(preview)))
            self.check_locks(native,preview,DEM)

    def check_locks(self,native,preview,tool):
        for yaw in (0.,15.,35.,65.,90.):
            p=tool.values(dict(yaw=yaw,paper='A3',orientation='landscape'))
            fitted=layout(outline(native),p,reference_width=native.width)
            locked=dict(p,lock_scale=True,terrain_width=np.floor(native.width*fitted['scale']*1e6)/1e6)
            a=layout(outline(native),locked,reference_width=native.width)
            b=layout(outline(preview),locked,reference_width=preview.width)
            self.assertAlmostEqual(a['scale'],b['scale'],places=10)

    def test_image_alpha_holes_and_scale_survive_preview_reduction(self):
        from PIL import Image
        from atlas_toolkit.tools.image_to_mesh.image_to_mesh import read
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'alpha.png'
            y,x=np.mgrid[:180,:240]
            valid=((x-110)/100)**2+((y-90)/80)**2<1
            valid[70:90,80:100]=False
            data=np.full((180,240,4),128,np.uint8)
            data[:,:,3]=valid*255
            Image.fromarray(data).save(path)
            native=read(path,IMAGE.defaults())
            preview=read(path,IMAGE.defaults(),max_size=32)
            self.assertTrue(outline(native).equals(outline(preview)))
            self.check_locks(native,preview,IMAGE)

    def test_solid_cleanup_returns_the_validated_mesh(self):
        import trimesh
        from atlas_toolkit.geom.heightfield.solid import export_precision
        mesh=trimesh.creation.icosphere(subdivisions=1)
        a,b=mesh.edges_unique[0]
        mesh.vertices[b]=mesh.vertices[a]+(mesh.vertices[b]-mesh.vertices[a])*1e-15
        with self.assertRaisesRegex(ValueError,'Collapsed'):check_mesh(mesh)
        fixed,changed=export_precision(mesh,ctx())
        self.assertTrue(changed)
        self.assertIsNot(fixed,mesh)
        check_mesh(fixed)

    def test_local_surface_repair_preserves_xy_and_height(self):
        import trimesh
        from atlas_toolkit.geom.heightfield.precision import surface,file_copy
        xy=np.array([[0,0],[1e-15,0],[1,0],[1,1],[0,1],[.5,.5]])
        v=np.column_stack((xy,np.full(len(xy),.25)))
        f=np.array([[i,(i+1)%5,5] for i in range(5)])
        original=trimesh.Trimesh(v,f,process=False)
        fixed=surface(original,ctx())
        check_mesh(fixed,solid=False)
        check_mesh(file_copy(fixed),solid=False)
        np.testing.assert_allclose(fixed.bounds,original.bounds,atol=1e-14)
        self.assertAlmostEqual(fixed.area,1.)
        self.assertTrue(np.all(fixed.vertices[:,2]==.25))

    def test_stable_terrain_does_not_use_a_solid_simplifier(self):
        import trimesh
        from atlas_toolkit.geom.heightfield.precision import surface
        mesh=trimesh.Trimesh([[0,0,1],[1,0,2],[1,1,3],[0,1,2]],[[0,1,2],[0,2,3]],process=False)
        with patch('manifold3d.Manifold',side_effect=AssertionError('surface must not be inferred from a volume')):
            self.assertIs(surface(mesh,ctx()),mesh)

    def test_unrepresentable_island_is_rejected_not_deleted(self):
        import trimesh
        from atlas_toolkit.geom.heightfield.precision import surface
        v=np.array([[0,0,1],[1,0,1],[0,1,1],[2,0,1],[2+1e-15,0,1],[2,1e-15,1]])
        mesh=trimesh.Trimesh(v,[[0,1,2],[3,4,5]],process=False)
        with self.assertRaisesRegex(ValueError,'precision repair'):
            surface(mesh,ctx())

    def test_contact_policy_uses_physical_source_cells(self):
        with self.assertRaisesRegex(ValueError,'0.0135'):
            contact_clearance(DEM.values(dict(contact_trim=.02)),.054,ctx())
        automatic=contact_clearance(DEM.values(dict(contact_policy='automatic')),.054,ctx())
        self.assertGreater(automatic,0)
        self.assertLess(automatic,.054/4)
        with self.assertRaisesRegex(ValueError,'diagonal point contacts'):
            contact_clearance(DEM.defaults(),.054,ctx())
        self.assertGreater(contact_clearance(DEM.defaults(),.054,ctx('draft')),0)

    def test_sparse_mask_budget_is_not_full_rectangle_budget(self):
        valid=np.zeros((100,100),bool)
        valid[5:8,5:8]=True
        valid[90:93,90:93]=True
        field=Raster(np.full(valid.shape,10.),valid,2000.,2000.,True,dict(source_range=[10.,10.]))
        p=DEM.values(dict(pitch=.5,geometry='surface',triangle_budget=10000,boundary_error=0.))
        mesh,data=process(field,p,ctx())
        self.assertLess(len(mesh.faces),p.triangle_budget)
        check_mesh(mesh,solid=False)

    def test_failure_has_elapsed_time_mode_parameters_and_phase(self):
        from atlas_toolkit.tools.raster_dem_to_mesh.engine import make_sample
        events=[]
        with tempfile.TemporaryDirectory() as folder:
            path=make_sample(folder)
            values=DEM.values(dict(band=999))
            execute(DEM,Job(DEM.id,'run',str(path),values=dict(values),id=42),lambda kind,payload:events.append((kind,payload)),lambda:False)
        payload=next(value for kind,value in events if kind=='error')
        self.assertGreater(payload[2]['seconds'],0.)
        self.assertEqual(payload[2]['job_id'],42)
        self.assertEqual(payload[2]['mode'],'run')
        self.assertEqual(payload[2]['values']['band'],999)
        self.assertTrue(payload[2]['phase'])
