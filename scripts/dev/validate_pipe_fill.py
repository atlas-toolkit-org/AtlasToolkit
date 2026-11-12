"""Repeatable Pipe Fill visual/geometry acceptance. Writes only to --out.

    .venv/Scripts/python scripts/validate_pipe_fill.py --out <validation-folder> [--head <mesh>]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'src'))
from atlas_toolkit.devpaths import preset_names, preset_values  # noqa: E402
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401
from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.tools.pipe_fill import TOOL
from atlas_toolkit.tools.pipe_fill.engine import run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--head', type=Path)
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    cube = out/'cube.stl'
    trimesh.creation.box(extents=(80, 80, 100)).export(cube)
    ellipsoid = out/'ellipsoid.stl'
    body = trimesh.creation.icosphere(subdivisions=3, radius=50)
    body.vertices *= [.78, .64, 1.]
    body.export(ellipsoid)
    torus = out/'torus.stl'
    trimesh.creation.torus(major_radius=30, minor_radius=14).export(torus)
    cases = [(cube, name) for name in preset_names(TOOL)]
    cases += [(ellipsoid, 'Plotter organic'), (ellipsoid, 'Organic loops'),
              (torus, 'Plotter organic')]
    if args.head:
        cases += [(args.head, name) for name in ('Plotter organic', 'Architectural pipes', 'Plotter dense')]
    records = []
    ctx = JobContext(lambda k, v: print(v, flush=True) if k == 'log' else None, lambda: False)
    for src, preset in cases:
        slug = src.stem+'_'+preset.lower().replace(' ', '_')
        dst = out/(slug+'.stl')
        print('Building', slug, flush=True)
        started = time.perf_counter()
        p = preset_values(TOOL, preset)
        try:
            result = run(ctx, src, dst, p)
            (out/(slug+'.txt')).write_text(result.report, encoding='utf-8')
            saved = gm.load_mesh(dst)
            assert saved.is_watertight and saved.is_winding_consistent and saved.volume > 0
            entry = dict(case=slug, seconds=round(time.perf_counter()-started, 3),
                         faces=len(saved.faces), **result.data)
            # Actual Atlas renderer, no separate illustrative rendering pipeline.
            if preset in ('Plotter organic', 'Architectural pipes', 'Organic loops', 'Open bores'):
                drawing_tool = registry().get('plotter-vision')
                dp = drawing_tool.values(dict(flat_angle=35., decimate=False, crossings=False,
                                              min_stroke=.15, paper='A4', theta=70., psi=-145., radius=210.,
                                              outline_layer=False, optimise=True))
                drawing = drawing_tool.resolve(drawing_tool.engine)(ctx, dst, out/(slug+'.svg'), dp)
                entry['drawing_strokes'] = drawing.stat('strokes').value
                from PySide6.QtCore import QByteArray, Qt
                from PySide6.QtGui import QImage, QPainter
                from PySide6.QtSvg import QSvgRenderer
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance() or QApplication([])
                renderer = QSvgRenderer(QByteArray((out/(slug+'.svg')).read_bytes()))
                image = QImage(840, 1188, QImage.Format_ARGB32)
                image.fill(Qt.white)
                painter = QPainter(image)
                renderer.render(painter)
                painter.end()
                image.save(str(out/(slug+'.png')))
            records.append(entry)
            print(json.dumps(entry), flush=True)
        except Exception as exc:
            records.append(dict(case=slug, error=str(exc)))
            print('FAILED', slug, exc, flush=True)
    (out/'acceptance.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    return int(any('error' in r for r in records))


if __name__ == '__main__':
    raise SystemExit(main())
