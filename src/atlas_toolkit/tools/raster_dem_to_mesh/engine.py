"""Atlas adapter: inspect, build, validate and atomically publish signed millimetre meshes."""
from pathlib import Path
import numpy as np
from atlas_toolkit.core import InputInfo, Result, Stat
from . import TOOL
from .raster import read, dependencies, footprint_polygon
from .raster_dem_to_mesh import process, polygons, check_mesh


def input_dependencies(path, values):
    return dependencies(path, values)


def inspect(path):
    from atlas_toolkit.geom.image import view
    try:
        raster = read(path, TOOL.defaults(),max_size=1024,quick=True)
    except ValueError:
        raster = read(path, TOOL.defaults())
    mask = raster.valid
    footprint = footprint_polygon(mask, raster.width, raster.height)
    # Bounded metadata keeps worker transport and the placement editor light.
    tol = max(raster.width, raster.height)/1500
    rings = []
    for poly in polygons(footprint.simplify(tol, preserve_topology=True)):
        rings.append({'outer': np.asarray(poly.exterior.coords).tolist(),
                      'holes': [np.asarray(r.coords).tolist() for r in poly.interiors]})
    step = max(1, int(np.ceil(max(mask.shape)/900)))
    values, valid = raster.heights[::step, ::step], mask[::step, ::step]
    lo, hi = raster.metadata['source_range']
    gray = np.clip((values-lo)/max(hi-lo, 1e-12), 0, 1)
    gray = np.nan_to_num(gray)
    rgb = np.repeat((gray[..., None]*220+25).astype(np.uint8), 3, axis=2)
    rgb[~valid] = 0
    metadata = dict(raster.metadata, width=raster.width, height=raster.height, footprint=rings,
                    geographic=raster.geographic)
    summary = f"{raster.metadata['source_columns']} × {raster.metadata['source_rows']} · {raster.metadata['crs_label']} · preview range {lo:.4g}–{hi:.4g} {raster.metadata['elevation_units']}"
    return InputInfo(str(path), 'image', summary, metadata, {'views': [view('Elevation raster', rgb)]})


def make_sample(folder):
    import rasterio
    from rasterio.transform import from_origin
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/'terrain.tif'
    y, x = np.mgrid[-1:1:40j, -1:1:48j]
    heights = 80 + 240*np.exp(-4*(x*x+y*y)) + 30*x
    heights[(x*x+y*y > .95) | ((x-.3)**2+(y+.2)**2 < .025)] = np.nan
    with rasterio.open(path, 'w', driver='GTiff', width=48, height=40, count=1, dtype='float32',
                       crs='EPSG:3763', transform=from_origin(-60000, 240000, 50, 50), nodata=np.nan) as ds:
        ds.write(heights.astype('float32'), 1)
    return path


def run(ctx, src, dst, p):
    from atlas_toolkit.geom.heightfield.engine import run as build
    return build(ctx, src, dst, p, TOOL, read)
