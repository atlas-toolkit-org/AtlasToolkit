"""Atlas adapter for non-geographic image reliefs."""
from pathlib import Path
import numpy as np
from atlas_toolkit.core import InputInfo
from atlas_toolkit.geom.image import view
from . import TOOL
from .image_to_mesh import decode, read


def inspect(path):
    samples, alpha, info = decode(path)
    height, width = samples.shape[:2]
    stride = max(1, int(np.ceil(max(samples.shape[:2])/1024)))
    samples, alpha = samples[::stride, ::stride], alpha[::stride, ::stride]
    rgb = np.nan_to_num(np.clip(samples.astype(float)/info['native_white'], 0., 1.))
    if rgb.shape[2] == 1:
        rgb = np.repeat(rgb, 3, axis=2)
    rows, cols = np.indices(alpha.shape)
    checker = np.where(((rows//12+cols//12) % 2)[..., None], .84, .94)
    rgb = ((rgb*alpha[..., None]+checker*(1-alpha[..., None]))*255).astype(np.uint8)
    metadata = dict(info, source_rows=height, source_columns=width)
    summary = f"{width} × {height} · {info['mode']} · {info['bit_depth']}-bit · non-geographic image"
    return InputInfo(str(path), 'image', summary, metadata, {'views': [view('Original', rgb)]})


def prepared_views(raster, p):
    prepared = view('Prepared heightmap', raster.metadata['_prepared_rgb'])
    prepared['status'] = f"Black 0 · white {p['relief_height']*p['z_factor']:g} mm"
    return [prepared]


def run(ctx, src, dst, p):
    from atlas_toolkit.geom.heightfield.engine import run as build
    return build(ctx, src, dst, p, TOOL, read, mesh_title='Relief mesh', extra_views=prepared_views)


def make_sample(folder):
    from PIL import Image
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[-1:1:40j, -1:1:48j]
    gray = np.clip(.15+.7*np.exp(-3*(x*x+y*y))+.1*x, 0, 1)
    path = folder/'image_relief.png'
    Image.fromarray(np.round(gray*65535).astype(np.uint16)).save(path)
    return path
