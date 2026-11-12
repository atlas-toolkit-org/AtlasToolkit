"""Repeatable image-to-mesh and pen-drawing acceptance examples; source files stay unchanged."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
from atlas_toolkit.devpaths import preset_values  # noqa: E402
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401


def render(svg):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise ValueError(f'Invalid SVG: {svg}')
    size = renderer.defaultSize()
    image = QImage(1100, round(1100*size.height()/size.width()), QImage.Format_RGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    path = svg.with_suffix('.png')
    image.save(str(path))
    return path


def contact_sheet(sheets, folder):
    from PIL import Image, ImageDraw, ImageOps
    from atlas_toolkit.tools.image_to_mesh.engine import inspect
    sheet = Image.new('RGB', (1800, len(sheets)*300+45), 'white')
    draw = ImageDraw.Draw(sheet)
    for col, title in enumerate(('Original', 'Prepared heightmap', 'Soft contours', 'Bold contours', 'Triangle network')):
        draw.text((col*360+12, 12), title, fill='black')
    for row, (name, images) in enumerate(sheets):
        for col, path in enumerate(images):
            # Use the real inspector's bit-depth/alpha display mapping, including
            # uint16 PNGs that Pillow's direct RGB conversion would clip to white.
            original = Image.fromarray(inspect(path).preview['views'][0]['image'])
            thumb = ImageOps.contain(original, (340, 260))
            sheet.paste(thumb, (col*360+(360-thumb.width)//2, 45+row*300+(260-thumb.height)//2))
        draw.text((12, 45+row*300+270), name, fill='black')
    sheet.save(folder/'contact-sheet.png')


def main():
    from PIL import Image, ImageDraw, ImageOps
    from atlas_toolkit.core.registry import registry
    from atlas_toolkit.core import workflow as w
    from atlas_toolkit.tools.image_to_mesh import TOOL
    from atlas_toolkit.tools.image_to_mesh.image_to_mesh import read
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=qa_dir('image_mesh_validation'))
    parser.add_argument('--photo', type=Path, help='Optional existing photograph, read only.')
    parser.add_argument('--render-only', action='store_true', help='Rebuild the contact sheet from existing validation outputs.')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication([])
    if args.render_only:
        summary = json.loads((args.out/'validation.json').read_text(encoding='utf-8'))
        sheets = [(name, [args.out/(name+'.png'), args.out/(name+'-prepared.png'),
                         *[Path(row['png']) for row in summary if row['source'] == name]])
                  for name in dict.fromkeys(row['source'] for row in summary)]
        contact_sheet(sheets, args.out)
        return 0
    if args.photo:
        photo = ImageOps.exif_transpose(Image.open(args.photo)).convert('RGB')
        original_hash = hashlib.sha256(args.photo.read_bytes()).hexdigest()
    else:
        from skimage.data import astronaut
        photo = Image.fromarray(astronaut())
        original_hash = None
    photo.thumbnail((256, 256))
    photo.save(args.out/'photograph.png')
    y, x = np.mgrid[0:1:128j, 0:1:160j]
    ramp = np.clip(.08+.65*x+.2*np.sin(y*np.pi*2)*np.sin(x*np.pi), 0, 1)
    Image.fromarray(np.round(ramp*65535).astype('uint16')).save(args.out/'grayscale-ramp.png')
    graphic = np.zeros((128, 160, 3), np.uint8)
    graphic[:] = (20, 20, 40)
    graphic[15:110, 15:145] = (220, 90, 30)
    circle = (x-.5)**2+(y-.5)**2 < .085
    graphic[circle] = (70, 230, 180)
    graphic[45:80, 60:100] = 245
    Image.fromarray(graphic).save(args.out/'graphic.png')
    rgba = np.empty((128, 160, 4), np.uint8)
    rgba[..., :3] = np.repeat((ramp[..., None]*255).astype(np.uint8), 3, axis=2)
    rgba[..., 3] = (((x-.5)**2/.23+(y-.5)**2/.2 < 1) & ((x-.5)**2+(y-.5)**2 > .015))*255
    Image.fromarray(rgba).save(args.out/'transparent.png')
    presets = [('Plotter soft contours', 'image_contours'), ('Plotter bold contours', 'image_contours'),
               ('Plotter triangle network', 'image_facets')]
    reg = registry()
    cache = w.WorkflowCache(args.out/'workflow-cache')
    summary, sheets = [], []
    for source_name in ('photograph', 'grayscale-ramp', 'graphic', 'transparent'):
        src = args.out/(source_name+'.png')
        source_hash = hashlib.sha256(src.read_bytes()).hexdigest()
        prepared = read(src, TOOL.defaults()).metadata['_prepared_rgb']
        prepared_path = args.out/(source_name+'-prepared.png')
        Image.fromarray(prepared).save(prepared_path)
        images = [src, prepared_path]
        for preset, template in presets:
            print('Starting '+source_name+' / '+preset, flush=True)
            started = time.perf_counter()
            wf = w.load(ROOT/('docs/workflows/'+template+'.atlasflow'))
            label = preset.lower().replace(' ', '-')
            for node in wf.nodes.values():
                if node.tool == TOOL.id:
                    preparation = {k: v for k, v in node.values.items() if k in ('denoise',)}
                    node.values = dict(preset_values(TOOL, preset))
                    node.values.update(preparation)
                elif node.type == w.SAVE:
                    node.where, node.folder = 'folder', str(args.out/label)
            def progress(kind, payload):
                if kind == 'progress' and any(word in str(payload) for word in ('simplifying', 'sampling', 'checking saved', 'stitching')):
                    print(str(payload), flush=True)
                elif kind == 'log' and payload[0] == 'warning':
                    print(str(payload), flush=True)
            result = w.run_workflow(wf, src, progress, lambda: False, reg=reg, cache=cache)
            svg = Path(result.outputs[0])
            png = render(svg)
            images.append(png)
            from xml.etree import ElementTree
            tree = ElementTree.parse(svg)
            paths = len(tree.findall('.//{http://www.w3.org/2000/svg}path'))
            if not paths:
                raise ValueError(f'No drawing paths: {svg}')
            row = dict(source=source_name, preset=preset, paths=paths, svg=str(svg), png=str(png),
                       seconds=round(time.perf_counter()-started, 2))
            summary.append(row)
            print(json.dumps(row), flush=True)
        if hashlib.sha256(src.read_bytes()).hexdigest() != source_hash:
            raise RuntimeError('Validation modified a source image.')
        sheets.append((source_name, images))
    contact_sheet(sheets, args.out)
    if args.photo and hashlib.sha256(args.photo.read_bytes()).hexdigest() != original_hash:
        raise RuntimeError('Validation modified the supplied photograph.')
    (args.out/'validation.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    print('Contact sheet: '+str(args.out/'contact-sheet.png'), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
