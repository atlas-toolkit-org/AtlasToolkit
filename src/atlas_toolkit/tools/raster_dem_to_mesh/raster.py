"""Precision-preserving local raster input. No GUI imports or intermediate XYZ files."""
from pathlib import Path
import warnings
import numpy as np
from collections import OrderedDict
from threading import RLock

_CACHE = OrderedDict()
_LOCK = RLock()
SOURCE_KEYS = ('source_mode', 'band', 'source_crs', 'metadata_scale', 'elevation_units',
               'exclude_background', 'background', 'background_tolerance')


from atlas_toolkit.geom.heightfield.source import Raster, footprint_polygon, outline


def opened(path):
    import rasterio
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', rasterio.errors.NotGeoreferencedWarning)
        return rasterio.open(path)


def dependencies(path, values=None):
    with opened(path) as ds:
        return [Path(f) for f in ds.files if Path(f).resolve() != Path(path).resolve()]


def read(path, p, ctx=None, max_size=None, quick=False, exact_outline=True):
    path = Path(path).resolve()
    files = [path, *dependencies(path, p)]
    with opened(path) as ds:
        if max_size and max_size>=max(ds.width,ds.height):
            max_size = None
    exact_outline = bool(exact_outline and not quick)
    key = (tuple((str(f), f.stat().st_size, f.stat().st_mtime_ns) for f in files),
           tuple((k, p.get(k)) for k in SOURCE_KEYS), max_size, quick,exact_outline)
    if ctx:
        ctx.check_cancel()
    with _LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            if ctx:
                ctx.progress(.055,'using cached raster preparation')
            return _CACHE[key]
    result = _read(path, p, ctx, max_size, quick,exact_outline)
    result.heights.setflags(write=False)
    result.valid.setflags(write=False)
    with _LOCK:
        _CACHE[key] = result
        while sum(r.heights.nbytes+r.valid.nbytes+len(r.metadata.get('_outline_wkb','')) for r in _CACHE.values()) > 256*1024**2 or len(_CACHE) > 6:
            _CACHE.popitem(last=False)
    return result


def _read(path, p, ctx=None, max_size=None, quick=False,exact_outline=True):
    import rasterio
    from rasterio.crs import CRS
    from rasterio.warp import calculate_default_transform, reproject, transform
    from rasterio.enums import Resampling, ColorInterp
    def progress(f, text):
        if ctx:
            ctx.progress(f, text)
    with opened(path) as ds:
        if not 1 <= int(p['band']) <= ds.count:
            raise ValueError(f"Elevation band {p['band']} does not exist; this raster has {ds.count} band(s).")
        if ds.width < 2 or ds.height < 2:
            raise ValueError('A height raster needs at least two rows and two columns.')
        from .resources import preflight
        requested = ds.width*ds.height if not max_size else min(ds.width*ds.height,max_size**2)
        preflight(requested,0)
        crs = CRS.from_user_input(p['source_crs']) if p.get('source_crs', '').strip() else ds.crs
        if p.get('source_mode') == 'heightmap':
            raise ValueError('Non-geographic heightmaps moved to Image to Mesh (image-to-mesh).')
        geo = True
        if geo and crs is None:
            raise ValueError('GIS DEM needs a CRS. Set CRS override for geographic data, or use Image to Mesh (image-to-mesh) for a non-geographic image.')
        if np.issubdtype(np.dtype(ds.dtypes[p['band']-1]), np.complexfloating):
            raise ValueError('Complex raster bands cannot be interpreted as elevations.')
        progress(.02, 'reading numeric elevations and masks')
        from rasterio.windows import Window
        from scipy.ndimage import map_coordinates
        ratio = min(1., max_size/max(ds.width, ds.height)) if max_size else 1.
        rows, cols = max(2, round(ds.height*ratio)), max(2, round(ds.width*ratio))
        raw, valid = np.zeros((rows, cols)), np.zeros((rows, cols), bool)
        yy, xx = (np.arange(rows)+.5)*ds.height/rows-.5, (np.arange(cols)+.5)*ds.width/cols-.5
        minimum, maximum = np.inf, -np.inf
        counts = np.zeros((rows,cols),np.uint32) if ratio<1 and not quick else None
        outline_parts, contact_points, outline_vertices = [],[],0
        if quick:
            raw = ds.read(int(p['band']),out_shape=(rows,cols),resampling=Resampling.nearest).astype(float)
            valid = (ds.read_masks(int(p['band']),out_shape=(rows,cols))!=0)&np.isfinite(raw)
            if p['exclude_background']:
                valid &= np.abs(raw-p['background'])>p['background_tolerance']
            if valid.any():
                minimum,maximum = float(raw[valid].min()),float(raw[valid].max())
        # Tiles keep memory bounded even for very wide source rasters. Halo
        # cells make bilinear samples continuous across all tile boundaries.
        for y in range(0, 0 if quick else ds.height, 256):
            end = min(ds.height, y+256)
            lo, hi = max(0,y-1), min(ds.height,end+1)
            selected = np.flatnonzero((yy >= y-.5) & (yy < end-.5))
            for x in range(0,ds.width,1024):
                if ctx:
                    ctx.check_cancel()
                endx = min(ds.width,x+1024)
                left,right = max(0,x-1),min(ds.width,endx+1)
                window = Window(left,lo,right-left,hi-lo)
                block = ds.read(int(p['band']),window=window).astype(np.float64)
                ok = (ds.read_masks(int(p['band']),window=window)!=0)&np.isfinite(block)
                if p['exclude_background']:
                    ok &= np.abs(block-p['background'])>p['background_tolerance']
                if ok.any():
                    minimum,maximum = min(minimum,float(block[ok].min())),max(maximum,float(block[ok].max()))
                core = ok[y-lo:end-lo,x-left:endx-left]
                if exact_outline and core.any():
                    from rasterio.features import shapes
                    from affine import Affine
                    from shapely.geometry import shape
                    from shapely import get_num_coordinates
                    for geojson,value in shapes(core.astype('uint8'),mask=core,transform=Affine.translation(x,y)):
                        poly=shape(geojson)
                        outline_vertices += int(get_num_coordinates(poly))
                        if outline_vertices>2000000:
                            raise ValueError('Raster outline exceeds two million boundary vertices. Fill small NoData gaps or simplify the mask in GIS.')
                        outline_parts.append(poly)
                    a,b,c,d = ok[:-1,:-1],ok[:-1,1:],ok[1:,:-1],ok[1:,1:]
                    corners = np.argwhere((a&d&~b&~c)|(b&c&~a&~d))+1
                    for cy,cx in corners:
                        px,py = int(cx)+left,int(cy)+lo
                        if x<=px<endx and y<=py<end:
                            contact_points.append((px,py))
                columns = np.flatnonzero((xx>=x-.5)&(xx<endx-.5))
                if ratio<1:
                    # Area averaging uses all valid native samples, so tiny islands
                    # cannot disappear merely because a point sample missed them.
                    ry = np.minimum(rows-1,((np.arange(y,end)+.5)*rows/ds.height).astype(int))
                    cx = np.minimum(cols-1,((np.arange(x,endx)+.5)*cols/ds.width).astype(int))
                    nr,nc = ry[-1]-ry[0]+1,cx[-1]-cx[0]+1
                    indices = (ry[:,None]-ry[0])*nc+cx[None,:]-cx[0]
                    sums = np.bincount(indices[core],weights=block[y-lo:end-lo,x-left:endx-left][core],minlength=nr*nc).reshape(nr,nc)
                    n = np.bincount(indices[core],minlength=nr*nc).reshape(nr,nc)
                    raw[ry[0]:ry[-1]+1,cx[0]:cx[-1]+1] += sums
                    counts[ry[0]:ry[-1]+1,cx[0]:cx[-1]+1] += n.astype(np.uint32)
                elif len(selected) and len(columns):
                    target = np.ix_(selected,columns)
                    if ratio==1.:
                        raw[target] = block[y-lo:end-lo,x-left:endx-left]
                        valid[target] = ok[y-lo:end-lo,x-left:endx-left]
                    else:
                        gy,gx = np.meshgrid(yy[selected]-lo,xx[columns]-left,indexing='ij')
                        weights = map_coordinates(ok.astype(float),[gy,gx],order=1,mode='nearest',prefilter=False)
                        raw[target] = map_coordinates(np.where(ok,block,0.),[gy,gx],order=1,mode='nearest',prefilter=False)/np.maximum(weights,1e-30)
                        valid[target] = map_coordinates(ok.astype(np.uint8),[gy,gx],order=0,mode='nearest',prefilter=False)>0
            progress(.02+.035*end/ds.height, f'reading elevations and statistics {end:,}/{ds.height:,} rows')
        if counts is not None:
            valid = counts>0
            raw /= np.maximum(counts,1)
        if not valid.any():
            raise ValueError('No valid elevations remain after applying masks and background exclusion.')
        values = raw
        if p['metadata_scale']:
            values = values * ds.scales[p['band']-1] + ds.offsets[p['band']-1]
        unit = p['elevation_units']
        native_unit = (ds.units[p['band']-1] or '').lower()
        if unit == 'auto':
            unit = 'us-ft' if 'survey' in native_unit else 'ft' if native_unit == 'ft' or 'foot' in native_unit or 'feet' in native_unit else 'm'
        if geo:
            values *= {'m': 1., 'ft': .3048, 'us-ft': 1200/3937}[unit]
        valid &= np.isfinite(values)
        if not valid.any():
            raise ValueError('Band scale, offset or unit conversion left no finite elevations.')
        extremes = np.array([minimum, maximum])
        if p['metadata_scale']:
            extremes = extremes*ds.scales[p['band']-1]+ds.offsets[p['band']-1]
        if geo:
            extremes *= {'m': 1., 'ft': .3048, 'us-ft': 1200/3937}[unit]
        source_range = [float(extremes.min()), float(extremes.max())]
        tf = ds.transform @ ds.transform.scale(ds.width/cols,ds.height/rows)
        target = crs
        if geo and (crs.is_geographic or abs(tf.b) > 1e-12 or abs(tf.d) > 1e-12 or tf.a <= 0 or tf.e >= 0):
            if crs.is_geographic:
                x, y = transform(crs, CRS.from_epsg(4326), [(ds.bounds.left+ds.bounds.right)/2],
                                 [(ds.bounds.bottom+ds.bounds.top)/2])
                target = CRS.from_string(f'+proj=aeqd +lat_0={y[0]} +lon_0={x[0]} +datum=WGS84 +units=m')
            source_tf = tf
            tf, native_w, native_h = calculate_default_transform(crs, target, ds.width, ds.height, *ds.bounds)
            target_ratio = min(1.,max_size/max(native_w,native_h)) if max_size else 1.
            w,h = max(2,round(native_w*target_ratio)),max(2,round(native_h*target_ratio))
            tf = tf @ tf.scale(native_w/w,native_h/h)
            preflight(w*h,0)
            progress(.04, 'reprojecting to a north-up metric grid')
            src = np.where(valid, values, np.nan)
            out = np.full((h, w), np.nan)
            mask = np.zeros((h, w), np.uint8)
            reproject(src, out, src_transform=source_tf, src_crs=crs, dst_transform=tf, dst_crs=target,
                      src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)
            reproject(valid.astype(np.uint8), mask, src_transform=source_tf, src_crs=crs,
                      dst_transform=tf, dst_crs=target, resampling=Resampling.nearest)
            values, valid = out, (mask != 0) & np.isfinite(out)
        rows, cols = values.shape
        if not valid.any():
            raise ValueError('Reprojection left no valid elevations.')
        if geo:
            factor = target.linear_units_factor[1]
            width, height = abs(tf.a)*cols*factor, abs(tf.e)*rows*factor
        else:
            width, height = (float(ds.width), float(ds.height)) if p['pixel_aspect'] else (p['map_width'], p['map_height'])
        if not np.isfinite([width, height]).all() or min(width, height) <= 0:
            raise ValueError('Raster has invalid physical dimensions or transform.')
        metadata = dict(source_rows=ds.height, source_columns=ds.width, rows=rows, columns=cols,
                        bands=ds.count, crs=str(crs) if crs else 'Unreferenced heightmap',
                        crs_label=':'.join(crs.to_authority()) if crs and crs.to_authority() else 'GIS DEM' if geo else 'Heightmap',
                        working_crs=str(target) if target else '', elevation_units=unit if geo else 'heightmap values',
                        source_range=source_range, source_scale=ds.scales[p['band']-1],
                        source_offset=ds.offsets[p['band']-1], valid_cells=int(valid.sum()),
                        coordinate_units='metres' if geo else 'heightmap aspect units')
        metadata['resolution_limited'] = ratio < 1.
        metadata['statistics_exact'] = not quick
        metadata['source_cell_size'] = [width/ds.width, height/ds.height]
        if outline_parts:
            progress(.056,'merging authoritative source footprint')
            from shapely.ops import unary_union, transform as map_geometry
            source_transform = ds.transform
            if geo:
                centre_x,centre_y = tf @ (cols/2,rows/2)
            def coordinates(xs,ys):
                xs,ys = np.asarray(xs),np.asarray(ys)
                if not geo:
                    return xs/ds.width*width-width/2,height/2-ys/ds.height*height
                px=source_transform.a*xs+source_transform.b*ys+source_transform.c
                py=source_transform.d*xs+source_transform.e*ys+source_transform.f
                if target!=crs:
                    px,py=transform(crs,target,px,py)
                return (np.asarray(px)-centre_x)*factor,(np.asarray(py)-centre_y)*factor
            exact = map_geometry(coordinates,unary_union(outline_parts))
            if not exact.is_valid:
                raise ValueError('The projected source outline is invalid. Check the CRS or clean the source mask.')
            metadata['_outline_wkb'] = exact.wkb_hex
            if contact_points:
                px,py=coordinates(*np.asarray(contact_points).T)
                metadata['_contacts_xy']=np.column_stack((px,py)).tolist()
            else:
                metadata['_contacts_xy']=[]
            # Nearest-neighbour warp masks can extend across the true source
            # boundary. Reference samples must belong to the canonical domain.
            if target!=crs or abs(source_transform.b)>1e-12 or abs(source_transform.d)>1e-12:
                from rasterio.features import rasterize
                from rasterio.transform import from_bounds
                valid &= rasterize([(exact,1)],out_shape=valid.shape,
                    transform=from_bounds(-width/2,-height/2,width/2,height/2,cols,rows),dtype='uint8')!=0
                if not valid.any():raise ValueError('No target-grid samples remain inside the source footprint.')
                metadata['valid_cells']=int(valid.sum())
            if ctx:ctx.check_cancel()
        return Raster(values, valid, width, height, geo, metadata)
