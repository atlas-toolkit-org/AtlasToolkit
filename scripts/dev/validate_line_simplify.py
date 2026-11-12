"""Reproducible line-art performance/visual acceptance; never writes beside an input.

    .venv/Scripts/python scripts/validate_line_simplify.py --out PATH [--vertices 100000] [--source drawing.svg]
"""
import argparse
import copy
import json
import math
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from atlas_toolkit.devpaths import preset_names, preset_values  # noqa: E402
from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.core.selftest import run_tool
from atlas_toolkit.core import workflow as W
from atlas_toolkit.geom.svg import load
from atlas_toolkit.tools.svg_simplify import TOOL
from atlas_toolkit.tools.svg_simplify.engine import make_sample, run
from atlas_toolkit.tools.svg_simplify.svg_simplify import Options, simplify_tree
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--vertices', type=int, default=100000)
    ap.add_argument('--source', type=Path, action='append', default=[])
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    sample = make_sample(a.out)
    reports = []
    for preset in preset_names(TOOL):
        dst = a.out / (preset.lower().replace(' ', '_') + '.svg')
        result = run(JobContext(lambda *args: None, lambda: False), sample, dst, preset_values(TOOL, preset))
        (a.out / (dst.stem + '.txt')).write_text(result.report, encoding='utf-8')
    # Many independent flowing strokes: representative dense line art, deterministic, 100k vertices.
    rows, count = 100, max(3, a.vertices // 100)
    body = []
    for row in range(rows):
        points = ' '.join(f'{k*180/(count-1):.8f},{row*2 + .6*math.sin(k*8*math.pi/(count-1)):.8f}'
                          for k in range(count))
        body.append(f'<polyline points="{points}"/>')
    synthetic = ET.ElementTree(ET.fromstring('<svg xmlns="http://www.w3.org/2000/svg" width="210mm" '
                       'height="297mm" viewBox="0 0 210 297"><g fill="none" stroke="black">' + ''.join(body) + '</g></svg>'))
    sources = [('synthetic', synthetic)] + [(p.name, load(p)) for p in a.source]
    for name, original in sources:
        for method in ('rdp', 'vw'):
            start = time.perf_counter()
            st = simplify_tree(copy.deepcopy(original), Options(method=method))
            reports.append(dict(source=name, method=method, seconds=round(time.perf_counter()-start, 4),
                                vertices_in=st.vertices_in, vertices_out=st.vertices_out,
                                removed_percent=round(100*(1-st.vertices_out/max(1,st.vertices_in)), 3),
                                max_span_error_mm=st.deviation, changed_elements=st.changed, notes=st.notes))
    # Real contract worker, followed by a workflow/cache check with the packaged recipe.
    worker = a.out / 'worker'
    worker.mkdir(exist_ok=True)
    ok, message = run_tool(registry().get(TOOL.id), worker, in_worker=True)
    if not ok:
        raise RuntimeError(message)
    wf = W.load(Path(__file__).resolve().parents[2] / 'docs/workflows/line_simplify.atlasflow')
    wf.saves()[0].where, wf.saves()[0].folder = 'folder', str(a.out / 'workflow')
    cache = W.WorkflowCache(a.out / 'workflow-cache')
    events = []
    result = W.run_workflow(wf, sample, lambda *args: events.append(args), lambda: False, reg=registry(), cache=cache)
    if not result.outputs or b'svg-simplify' not in Path(result.outputs[0]).read_bytes():
        raise RuntimeError('Workflow did not publish signed output')
    again = []
    W.run_workflow(wf, sample, lambda *args: again.append(args), lambda: False, reg=registry(), cache=cache)
    if not any('cached' in str(e) for e in again):
        raise RuntimeError('Workflow did not reuse its cache')
    (a.out / 'benchmark.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
    print(json.dumps(reports, indent=2))
    # Static scientific/acceptance image, drawn from the actual output SVGs.
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from atlas_toolkit.ui.theme import load_fonts, sans
    app = QGuiApplication.instance() or QGuiApplication([])
    load_fonts()
    img = QImage(1600, 980, QImage.Format.Format_RGB32)
    img.fill(QColor('#ffffff'))
    painter = QPainter(img)
    painter.setFont(sans(22))
    for i, (name, path) in enumerate([('Original', sample), ('Faithful · 0.05 mm', a.out/'faithful.svg'),
                                    ('Organic · 0.1 mm', a.out/'organic.svg'), ('Stronger · 0.25 mm', a.out/'stronger.svg')]):
        x, y = (i % 2) * 800, (i // 2) * 490
        painter.setPen(QColor('#172238'))
        painter.drawText(QRectF(x+30, y+20, 730, 40), name)
        renderer = QSvgRenderer(str(path))
        if not renderer.isValid():
            raise RuntimeError(f'Invalid SVG: {path}')
        renderer.render(painter, QRectF(x+30, y+75, 735, 400))
    painter.end()
    img.save(str(a.out / 'comparison.png'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
