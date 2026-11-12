"""Reproduce the twelve CC0 height tiles; network access is build-time only.

Archives stay in --cache; only prepared height PNGs, thumbnails and provenance ship.
"""
from pathlib import Path
import argparse
import hashlib
import io
import json
import re
import sys
import urllib.request
import zipfile

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT/'src/atlas_toolkit/tools/mesh_texture/assets'
# id, title, provider, source id/page, approximate elements across the source tile
SOURCES = [
    ('scales','Reptile scales','TextureCan','114',40),
    ('rock','Fractured rock','ambientCG','Rock030',4),
    ('sand','Sand ripples','TextureCan','215',7),
    ('soil','Soil','ambientCG','Ground048',30),
    ('leaf_veins','Leaf veins','TextureCan','272',5),
    ('leaves','Fallen leaves','TextureCan','243',14),
    ('bricks','Brickwork','ambientCG','Bricks001',6),
    ('bark','Bark','ambientCG','Bark001',10),
    ('leather','Leather grain','ambientCG','Leather001',24),
    ('cracks','Cracked earth','TextureCan','489',12),
    ('cobbles','Cobblestones','ambientCG','PavingStones036',6),
    ('wood','Wood grain','ambientCG','Wood048',40),
]


def fetch(url):
    request=urllib.request.Request(url,headers={'User-Agent':'AtlasToolkit/1.13.1 (CC0 texture preparation)'})
    with urllib.request.urlopen(request,timeout=90) as response:
        return response.read()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,required=True)
    args=parser.parse_args()
    args.cache.mkdir(parents=True,exist_ok=True)
    DEST.mkdir(parents=True,exist_ok=True)
    manifest={'revision':1,'license':'CC0-1.0','textures':[]}
    for key,title,provider,asset,elements in SOURCES:
        page=f'https://ambientcg.com/view?id={asset}' if provider=='ambientCG' else f'https://www.texturecan.com/details/{asset}/'
        archive=args.cache/(key+'.zip')
        metadata=args.cache/(key+'.source.json')
        if not archive.exists():
            html=fetch(page).decode()
            if provider=='ambientCG':
                links=re.findall(r'href="([^"]+_1K-(?:PNG|JPG)\.zip)"',html)
                links.sort(key=lambda s:'PNG' not in s)
            else:
                links=['https://www.texturecan.com'+v for v in re.findall(r'href="([^\"]+_1k_[^\"]+\.zip)"',html)]
            if not links:
                raise RuntimeError(f'No 1K map archive: {page}')
            data=fetch(links[0])
            # Validate archive before caching; failed requests never become valid cache entries.
            zipfile.ZipFile(io.BytesIO(data)).testzip()
            archive.write_bytes(data)
            metadata.write_text(json.dumps({'download_url':links[0]},indent=2))
        with zipfile.ZipFile(archive) as z:
            names=[n for n in z.namelist() if not n.startswith('__MACOSX/') and any(t in n.lower() for t in ('displacement','height')) and n.lower().endswith(('.png','.jpg','.tif','.exr'))]
            if not names:
                raise RuntimeError(f'No displacement map in {archive}: {z.namelist()}')
            names.sort(key=lambda s:(not s.lower().endswith('.png'),s))
            raw=z.read(names[0])
            image=Image.open(io.BytesIO(raw))
            values=np.array(image)
            if values.ndim!=2:
                if not np.array_equal(values[...,0],values[...,1]):
                    raise RuntimeError(f'Non-grayscale displacement: {names[0]}')
                values=values[...,0]
            precision=str(values.dtype)
            values=values.astype(float)/(65535 if values.max()>255 else 255)
        # Use circular low-pass filtering, then a symmetric edge blend for source
        # maps that are only approximately tileable. Keep the 0..1 height calibration.
        values=gaussian_filter(values,1.2,mode='wrap')
        low,high=map(float,np.quantile(values,[.01,.99]))
        levels=[low,high]
        if high-low<1e-8:
            raise RuntimeError(f'Constant source displacement: {key}')
        # Curated artistic tiles expand the source's useful displacement range.
        # Some physically calibrated scans encode all relief near 0.5. User
        # heightmaps are never stretched by the runtime reader.
        values=np.clip((values-low)/(high-low),0,1)
        size=min(768,values.shape[0],values.shape[1])
        values=np.asarray(Image.fromarray(values.astype('float32')).resize((size,size),Image.Resampling.LANCZOS)).copy()
        strip=max(2,round(size*.02))
        for axis in (0,1):
            a=np.moveaxis(values,axis,0)
            for i in range(strip):
                amount=.5*(1-i/strip)**2
                low,high=a[i].copy(),a[-1-i].copy()
                a[i]=(1-amount)*low+amount*high
                a[-1-i]=(1-amount)*high+amount*low
        values=np.clip(values,0,1)
        output=DEST/(key+'.png')
        Image.fromarray(np.round(values*65535).astype('uint16')).save(output)
        dy,dx=np.gradient(values)
        shade=np.clip(.72+5*dx-7*dy,0,1)
        thumb=Image.fromarray((shade*255).astype('uint8')).resize((160,160),Image.Resampling.LANCZOS)
        thumb.save(DEST/(key+'_thumb.png'))
        manifest['textures'].append(dict(id=key,name=title,provider=provider,source_url=page,
            author='Lennart Demes' if provider=='ambientCG' else 'TextureCan',license='CC0-1.0',
            file=output.name,thumbnail=key+'_thumb.png',elements=elements,
            source_map=names[0],source_precision=precision,source_sha256=hashlib.sha256(raw).hexdigest(),
            archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
            preparation=dict(gaussian_sigma_source_px=1.2,levels=levels,resolution=size,seam_blend_fraction=.02),
            **json.loads(metadata.read_text())))
        print(key,names[0],precision,size,flush=True)
    (DEST/'catalog.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
