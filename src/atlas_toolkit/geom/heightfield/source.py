"""Heightfield data and cached mask geometry, independent of geographic interpretation."""
from dataclasses import dataclass
from collections import OrderedDict
from threading import RLock
import numpy as np

_LOCK = RLock()
_FOOTPRINTS = OrderedDict()

@dataclass
class Raster:
    heights: np.ndarray
    valid: np.ndarray
    width: float
    height: float
    geographic: bool
    metadata: dict


def outline(raster):
    if '_outline_wkb' not in raster.metadata:
        return footprint_polygon(raster.valid,raster.width,raster.height)
    if not hasattr(raster,'_outline'):
        from shapely import from_wkb
        raster._outline=from_wkb(bytes.fromhex(raster.metadata['_outline_wkb']))
    return raster._outline


def footprint_polygon(mask, width, height):
    import hashlib
    key = (mask.shape,width,height,hashlib.blake2b(np.ascontiguousarray(mask).tobytes(),digest_size=16).digest())
    with _LOCK:
        if key in _FOOTPRINTS:
            _FOOTPRINTS.move_to_end(key)
            return _FOOTPRINTS[key]
    from rasterio.features import shapes
    from rasterio.transform import from_bounds
    from shapely.geometry import shape
    from shapely.ops import unary_union
    tf = from_bounds(-width/2, -height/2, width/2, height/2, mask.shape[1], mask.shape[0])
    polygons = [shape(g) for g, v in shapes(mask.astype(np.uint8), mask=mask, transform=tf) if v]
    result = unary_union(polygons)
    if result.is_empty or not result.is_valid:
        raise ValueError('The raster mask has a crossed or touching footprint. Fill data gaps or clean the source mask.')
    with _LOCK:
        _FOOTPRINTS[key] = result
        if len(_FOOTPRINTS)>16:
            _FOOTPRINTS.popitem(last=False)
    return result
