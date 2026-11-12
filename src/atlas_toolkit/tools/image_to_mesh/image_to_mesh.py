"""Precision-preserving decoding and deterministic, non-destructive image preparation."""
from collections import OrderedDict
from pathlib import Path
from threading import RLock
import warnings
import numpy as np
from atlas_toolkit.geom.heightfield.source import Raster, footprint_polygon
from atlas_toolkit.geom.heightfield.resources import preflight

SOURCE_KEYS = ('grayscale', 'input_range', 'source_black', 'source_white', 'auto_levels', 'denoise',
               'black_level', 'white_level', 'brightness', 'contrast', 'gamma', 'invert',
               'quarter_turns', 'mirror_x', 'mirror_y', 'pixel_aspect', 'map_width', 'map_height',
               'alpha_mode', 'alpha_threshold', 'exclude_background', 'background', 'background_tolerance')
_CACHE = OrderedDict()
_LOCK = RLock()


def orient(array, orientation):
    """Apply the eight EXIF orientations exactly once, also for numeric TIFF/PNG arrays."""
    if orientation == 2:
        return array[:, ::-1]
    if orientation == 3:
        return array[::-1, ::-1]
    if orientation == 4:
        return array[::-1]
    if orientation == 5:
        return array.swapaxes(0, 1)
    if orientation == 6:
        return np.rot90(array, -1)
    if orientation == 7:
        return array.swapaxes(0, 1)[::-1, ::-1]
    if orientation == 8:
        return np.rot90(array, 1)
    return array


def decode(path, ctx=None):
    """Return native samples, normalized alpha and original bit depth; never apply GIS metadata."""
    from PIL import Image, UnidentifiedImageError
    path = Path(path)
    try:
        image = Image.open(path)
    except UnidentifiedImageError:
        if path.suffix.lower() not in ('.tif', '.tiff'):
            raise
        # Pillow cannot identify all numeric TIFF types (notably float64).
        return decode_numeric(path, ctx=ctx)
    with image:
        if min(image.size) < 2:
            raise ValueError('An image heightfield needs at least two rows and two columns.')
        preflight(image.width * image.height, 0)
        orientation = image.getexif().get(274, 1)
        mode = image.mode
        # Read all TIFF pixels through GDAL: Pillow's TIFF loader may itself
        # transpose pixels while loading, unlike its other format loaders.
        numeric = path.suffix.lower() in ('.tif', '.tiff') or (
            path.suffix.lower() == '.png' and mode not in ('P', 'PA'))
        if ctx:
            ctx.check_cancel()
        if numeric:
            return decode_numeric(path, orientation=orientation, mode=mode, ctx=ctx)
        else:
            converted = image.convert('RGBA')
            rgba = np.asarray(converted)
            samples, alpha, bits = rgba[..., :3], rgba[..., 3], 8
            if mode in ('1', 'L', 'LA'):
                samples = np.asarray(image.convert('L'))[..., None]
    return decoded(samples, alpha, bits, orientation, mode)


def decode_numeric(path, orientation=None, mode='numeric TIFF', ctx=None):
    import rasterio
    from rasterio.enums import ColorInterp
    # Avoid libtiff's automatic CMYK expansion/orientation. Decode its raw
    # channels explicitly, then apply the same EXIF transform as every image.
    dataset_path = 'GTIFF_RAW:'+str(path) if mode == 'CMYK' else path
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', rasterio.errors.NotGeoreferencedWarning)
        with rasterio.open(dataset_path, GEOREF_SOURCES='NONE') as ds:
            if min(ds.width, ds.height) < 2:
                raise ValueError('An image heightfield needs at least two rows and two columns.')
            preflight(ds.width*ds.height, 0)
            if any(np.issubdtype(np.dtype(t), np.complexfloating) for t in ds.dtypes):
                raise ValueError('Complex image samples cannot be used as heights.')
            if ctx:
                ctx.check_cancel()
            # Read raw samples, not NoData masks, scale/offset, CRS or sidecars.
            raw = np.moveaxis(ds.read(), 0, -1)
            interpretations = list(ds.colorinterp)
            alpha_index = interpretations.index(ColorInterp.alpha) if ColorInterp.alpha in interpretations else None
            channels = [i for i in range(ds.count) if i != alpha_index]
            if ColorInterp.palette in interpretations:
                table = ds.colormap(interpretations.index(ColorInterp.palette)+1)
                lut = np.asarray([table.get(i, (0, 0, 0, 255)) for i in range(max(table)+1)], np.uint8)
                rgba = lut[raw[..., 0]]
                samples, alpha, bits = rgba[..., :3], rgba[..., 3], 8
            elif ColorInterp.cyan in interpretations:
                from PIL import Image
                cmyk = np.ascontiguousarray(raw, dtype=np.uint8)
                samples = np.asarray(Image.frombytes('CMYK', (ds.width, ds.height), cmyk.tobytes()).convert('RGB'))
                alpha, bits = None, 8
            else:
                if len(channels) not in (1, 3):
                    raise ValueError('Use a grayscale or RGB image, optionally with alpha; multispectral bands belong in Raster DEM to Mesh.')
                if all(c in interpretations for c in (ColorInterp.red, ColorInterp.green, ColorInterp.blue)):
                    channels = [interpretations.index(c) for c in (ColorInterp.red, ColorInterp.green, ColorInterp.blue)]
                alpha = raw[..., alpha_index] if alpha_index is not None else None
                samples = raw[..., channels]
                bits = int(ds.tags(1, ns='IMAGE_STRUCTURE').get('NBITS', ds.tags(ns='IMAGE_STRUCTURE').get('NBITS', samples.dtype.itemsize*8)))
            if orientation is None:
                orientation = int(ds.tags().get('TIFFTAG_ORIENTATION', 1))
    return decoded(samples, alpha, bits, orientation, mode)


def decoded(samples, alpha, bits, orientation, mode):
    dtype = samples.dtype
    if np.issubdtype(dtype, np.unsignedinteger):
        native_white = float(2**bits-1)
    elif dtype == bool:
        native_white = 1.
    elif np.issubdtype(dtype, np.floating):
        native_white = 1.
    else:
        # Signed numeric TIFFs need an explicit range or Auto levels.
        native_white = 1.
    if alpha is None:
        alpha = np.ones(samples.shape[:2], float)
    else:
        alpha = np.asarray(alpha, float)/(float(2**bits-1) if np.issubdtype(dtype, np.integer) else 1.)
        alpha = np.nan_to_num(np.clip(alpha, 0., 1.))
    return (np.ascontiguousarray(orient(samples, orientation)), np.ascontiguousarray(orient(alpha, orientation)),
            dict(mode=mode, dtype=str(dtype), bit_depth=bits, native_white=native_white, exif_orientation=int(orientation)))


def grayscale_channel(a, method):
    if a.shape[2] == 1:
        return a[..., 0]
    if method == 'weighted':
        from skimage.color import rgb2gray
        return rgb2gray(a)
    if method == 'average':
        return a.mean(axis=2)
    if method == 'lightness':
        return (a.min(axis=2)+a.max(axis=2))/2
    return a[..., {'red': 0, 'green': 1, 'blue': 2}[method]]


def prepare(samples, alpha, info, p, ctx=None):
    """The single preparation pipeline used for placement, previews and exported heights."""
    from scipy.ndimage import gaussian_filter
    if ctx:
        ctx.check_cancel()
        ctx.progress(.02, 'preparing image tones and alpha mask')
    a = np.rot90(samples, -int(p['quarter_turns']))
    alpha = np.rot90(alpha, -int(p['quarter_turns']))
    if p['mirror_x']:
        a, alpha = a[:, ::-1], alpha[:, ::-1]
    if p['mirror_y']:
        a, alpha = a[::-1], alpha[::-1]
    valid = np.isfinite(a).all(axis=2)
    a = np.nan_to_num(a.astype(np.float64))
    if p['alpha_mode'] == 'mask':
        valid &= alpha > p['alpha_threshold']
    else:
        black, white = (p['source_black'], p['source_white']) if p['input_range'] == 'custom' else (0., info['native_white'])
        visible = valid & (alpha > 0)
        if p['auto_levels'] and visible.any():
            reference = grayscale_channel(a, p['grayscale'])[visible]
            if reference.max() > reference.min():
                black, white = float(reference.min()), float(reference.max())
        background = white if p['alpha_mode'] == 'white' else black
        a = a*alpha[..., None]+background*(1-alpha[..., None])
        valid |= alpha == 0
    if not valid.any():
        raise ValueError('No valid image pixels remain after transparency masking.')
    gray = grayscale_channel(a, p['grayscale'])
    source_range = [float(gray[valid].min()), float(gray[valid].max())]
    lo, hi = (p['source_black'], p['source_white']) if p['input_range'] == 'custom' else (0., info['native_white'])
    if hi <= lo:
        raise ValueError('Source white value must be greater than source black value.')
    if p['auto_levels']:
        lo, hi = source_range
        # A constant image has no stretchable range; preserve its native level.
        if hi == lo:
            lo, hi = (p['source_black'], p['source_white']) if p['input_range'] == 'custom' else (0., info['native_white'])
    elif p['input_range'] == 'native' and (source_range[0] < -1e-7 or source_range[1] > hi+1e-7):
        raise ValueError('Numeric image values are outside the native 0–1 range. Enable Auto levels or choose a Custom source range.')
    gray = np.clip((gray-lo)/(hi-lo), 0., 1.)
    if p['exclude_background']:
        valid &= np.abs(gray-p['background']) > p['background_tolerance']
    if not valid.any():
        raise ValueError('No valid image pixels remain after background exclusion.')
    if p['denoise'] > 0:
        weights = gaussian_filter(valid.astype(float), p['denoise'])
        gray = gaussian_filter(np.where(valid, gray, 0.), p['denoise'])/np.maximum(weights, 1e-30)
    if p['white_level'] <= p['black_level']:
        raise ValueError('White level must be greater than black level.')
    gray = np.clip((gray-p['black_level'])/(p['white_level']-p['black_level']), 0., 1.)
    if p['contrast'] != 1.:
        gray = (gray-.5)*p['contrast']+.5
    if p['brightness'] != 0.:
        gray = gray+p['brightness']
    gray = np.clip(gray, 0., 1.)
    if p['gamma'] != 1.:
        gray = gray ** p['gamma']
    if p['invert']:
        gray = 1-gray
    gray = np.where(valid, gray, 0.)
    if ctx:
        ctx.check_cancel()
    return gray, valid, source_range


def preview_rgb(heights, valid):
    """Absolute grayscale on a checkerboard; masked RGB never leaks into the preview."""
    rows, cols = np.indices(valid.shape)
    checker = np.where(((rows//12+cols//12) % 2)[..., None], 215, 240)
    return np.where(valid[..., None], np.clip(heights[..., None]*255, 0, 255), checker).repeat(3, axis=2).astype(np.uint8)


def read(path, p, ctx=None, max_size=None, quick=False, exact_outline=True):
    """Prepare at native precision; bound only the mesh working grid and display arrays."""
    path = Path(path).resolve()
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns, tuple((k, p[k]) for k in SOURCE_KEYS), max_size, exact_outline)
    if ctx:
        ctx.check_cancel()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached
    samples, alpha, info = decode(path, ctx)
    heights, valid, raw_range = prepare(samples, alpha, info, p, ctx)
    rows, cols = heights.shape
    width, height = (float(cols), float(rows)) if p['pixel_aspect'] else (p['map_width'], p['map_height'])
    metadata = dict(info, source_rows=rows, source_columns=cols, bands=samples.shape[2],
                    source_range=[float(heights[valid].min()), float(heights[valid].max())],
                    raw_range=raw_range, height_reference='unit', geographic=False,
                    coordinate_units='image aspect units', elevation_units='normalized intensity',
                    crs_label='Non-geographic image', crs='', working_crs='',
                    source_cell_size=[width/cols, height/rows], statistics_exact=True,
                    preparation={k: p[k] for k in SOURCE_KEYS})
    stride = max(1, int(np.ceil(max(rows, cols)/1024)))
    metadata['_prepared_rgb'] = preview_rgb(heights[::stride, ::stride], valid[::stride, ::stride])
    if max_size and max(rows, cols) > max_size:
        if exact_outline:
            from atlas_toolkit.geom.heightfield.mesh import contact_vertices
            metadata['_outline_wkb'] = footprint_polygon(valid, width, height).wkb_hex
            contacts = contact_vertices(valid)
            metadata['_contacts_xy'] = [(col/cols*width-width/2, height/2-row/rows*height) for row, col in contacts]
        from skimage.transform import resize_local_mean
        ratio = max_size/max(rows, cols)
        shape = (max(2, round(rows*ratio)), max(2, round(cols*ratio)))
        weights = resize_local_mean(valid.astype(float), shape)
        heights = resize_local_mean(heights, shape)/np.maximum(weights, 1e-30)
        valid = weights > 0
    metadata.update(rows=heights.shape[0], columns=heights.shape[1], valid_cells=int(valid.sum()),
                    resolution_limited=heights.shape != (rows, cols))
    result = Raster(heights, valid, width, height, False, metadata)
    result.heights.setflags(write=False)
    result.valid.setflags(write=False)
    with _LOCK:
        _CACHE[key] = result
        while len(_CACHE) > 6 or sum(r.heights.nbytes+r.valid.nbytes+len(r.metadata.get('_outline_wkb', ''))+r.metadata['_prepared_rgb'].nbytes for r in _CACHE.values()) > 256*1024**2:
            _CACHE.popitem(last=False)
    return result
