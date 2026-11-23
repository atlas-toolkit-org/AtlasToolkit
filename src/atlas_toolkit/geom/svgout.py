"""
Writing new plotter SVGs, for tools that draw from scratch (the Hatch Maker; future generators):

- sheet in millimetres (width="297mm" + viewBox in mm), as the studio's plotter SVGs are;
- one Inkscape layer per pen, strokes only, round caps and joins; or, for a tool that draws with several pens, the
  way its "Save pens as" setting (core/params.pen_output_param) asks: write_pens();
- signed first with a desc element holding the tool id (Atlas reads the head of the file), settings in <metadata>;
- helpers to join segments into polylines, to order polylines so the pen travels less, and to cut them into dashes.

Dashes are always real geometry (dashed()), never a stroke-dasharray: plotter software draws a dasharray solid, and
a dashed pen merged with another would lose it.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .svg import INKSCAPE_NS, SVG_NS, fmt, write_run_metadata

ET.register_namespace("", SVG_NS)
ET.register_namespace("inkscape", INKSCAPE_NS)

PEN_OUTPUT_MODES = ("layers", "groups", "files", "merged")     # core/params.PEN_OUTPUTS, in the same order
MERGED_LABEL = "drawing"


@dataclass
class Layer:
    label: str
    polylines: list = field(default_factory=list)   # [[(x, y), ...], ...] in mm
    color: str = "#000000"
    width: float = 0.3                              # mm
    paths: list | None = None                       # optional ready path data ("M … C …"), one per polyline, for a
                                                    # tool that fits curves (FlowRep); None writes the polylines

    def path_data(self, precision: int = 3) -> list[str]:
        if self.paths is not None:
            return [d for d in self.paths if d]
        return [d for d in (polyline_d(pl, precision) for pl in self.polylines) if d]


def polyline_d(pl, precision: int = 3) -> str:
    if len(pl) < 2:
        return ""
    pts = [f"{fmt(x, precision)},{fmt(y, precision)}" for x, y in pl]
    return f"M {pts[0]} L {' '.join(pts[1:])}"


def slug(label: str) -> str:
    """A pen's label as a file-name and id part: "square hatching" -> "square-hatching"."""
    return re.sub(r"[^a-z0-9]+", "-", str(label).lower()).strip("-") or "pen"


def _style(layer: Layer) -> str:
    return (f"fill:none;stroke:{layer.color};stroke-width:{fmt(layer.width, 3)};"
            "stroke-linecap:round;stroke-linejoin:round")


def _root(width: float, height: float, tool, values, extra: dict | None = None) -> ET.Element:
    root = ET.Element(f"{{{SVG_NS}}}svg", {"width": f"{fmt(width, 3)}mm", "height": f"{fmt(height, 3)}mm",
                                           "viewBox": f"0 0 {fmt(width, 3)} {fmt(height, 3)}"})
    root.text = "\n  "
    if tool is not None:
        desc = ET.SubElement(root, f"{{{SVG_NS}}}desc")
        desc.text = tool.id
        desc.tail = "\n  "
        write_run_metadata(root, tool, values or {})
    if extra:                       # a tool's own record (FlowRep's network), as JSON in a metadata element of its own
        import json
        md = ET.Element(f"{{{SVG_NS}}}metadata")
        md.text = json.dumps(extra, ensure_ascii=False, default=str)
        md.tail = "\n  "
        root.insert(len([c for c in root if c.tag in (f"{{{SVG_NS}}}desc", f"{{{SVG_NS}}}metadata")]), md)
    return root


def _paths(parent: ET.Element, layer: Layer, precision: int, indent: str) -> None:
    parent.text = indent
    for d in layer.path_data(precision):
        ET.SubElement(parent, f"{{{SVG_NS}}}path", {"d": d}).tail = indent
    if len(parent):
        parent[-1].tail = indent[:-2]


def build_svg(width: float, height: float, layers: list[Layer], tool=None, values: dict | None = None,
              precision: int = 3, groups: bool = False, extra: dict | None = None) -> ET.ElementTree:
    """The sheet with one Inkscape layer per pen. groups=True: one layer holding a group per pen, so the pens stay
    apart in an editor but plotter software draws them in one pass."""
    root = _root(width, height, tool, values, extra)
    layers = [l for l in layers if l.polylines]
    if groups and layers:
        top = ET.SubElement(root, f"{{{SVG_NS}}}g", {
            f"{{{INKSCAPE_NS}}}groupmode": "layer", f"{{{INKSCAPE_NS}}}label": f"1 {MERGED_LABEL}", "id": "layer1"})
        top.text, top.tail = "\n    ", "\n"
        for n, layer in enumerate(layers, 1):
            g = ET.SubElement(top, f"{{{SVG_NS}}}g", {f"{{{INKSCAPE_NS}}}label": f"{n} {layer.label}",
                                                     "id": f"pen{n}-{slug(layer.label)}", "style": _style(layer)})
            _paths(g, layer, precision, "\n      ")
            g.tail = "\n    "
        top[-1].tail = "\n  "
        return ET.ElementTree(root)
    for n, layer in enumerate(layers, 1):
        g = ET.SubElement(root, f"{{{SVG_NS}}}g", {
            f"{{{INKSCAPE_NS}}}groupmode": "layer", f"{{{INKSCAPE_NS}}}label": f"{n} {layer.label}",
            "id": f"layer{n}", "style": _style(layer)})
        _paths(g, layer, precision, "\n    ")
        g.tail = "\n  "
    return ET.ElementTree(root)


def write_svg(path: Path, width: float, height: float, layers: list[Layer], tool=None, values: dict | None = None,
              precision: int = 3, groups: bool = False, extra: dict | None = None) -> Path:
    tree = build_svg(width, height, layers, tool, values, precision, groups, extra)
    tree.write(path, encoding="UTF-8", xml_declaration=True)
    return Path(path)


# ----------------------------------------------------------------------------- several pens

def pen_mode(values) -> str:
    """The "Save pens as" value in a tool's settings: "layers" for a tool (or older settings) without one."""
    mode = (values or {}).get("pen_output") or "layers"
    if mode not in PEN_OUTPUT_MODES:
        raise ValueError(f"pen_output: {mode!r} is not one of {list(PEN_OUTPUT_MODES)}")
    return mode


def arrange(layers: list[Layer], mode: str, optimise: bool = False) -> list[Layer]:
    """The pens as they will be saved: empty ones left out and, for "merged", every stroke on one pen (the first
    pen's width and colour), ordered together when `optimise`. Engines build their preview and report from this, so
    both show what the file holds. Ready path data (Layer.paths) survives only a merge that keeps the order."""
    layers = [l for l in layers if l.polylines]
    if mode != "merged" or len(layers) < 2:
        return layers
    first = layers[0]
    pls = [pl for l in layers for pl in l.polylines]
    paths = None
    if not optimise and all(l.paths is not None for l in layers):
        paths = [d for l in layers for d in l.paths]
    return [Layer(MERGED_LABEL, order_polylines(pls) if optimise else pls, first.color, first.width, paths)]


def pen_file(path: Path, index: int, label: str) -> Path:
    """Where pen `index` (from 1) of a drawing saved at `path` goes in "files" mode: head_sketch-2-bold.svg."""
    path = Path(path)
    return path.with_name(f"{path.stem}-{index}-{slug(label)}{path.suffix}")


def write_pens(path: Path, width: float, height: float, layers: list[Layer], mode: str, tool=None,
               values: dict | None = None, precision: int = 3, optimise: bool = False,
               extra: dict | None = None) -> list[Path]:
    """Write a drawing with several pens the way `mode` ("Save pens as") asks; returns every file written, `path`
    first. layers: an Inkscape layer per pen. groups: one layer holding a group per pen. merged: one pen
    (arrange()). files: the whole drawing at `path`, a layer per pen (what the queue and a workflow pass on), and,
    when there are two or more pens, each pen alone on the same sheet beside it (pen_file()), signed and carrying
    the same settings, so the files line up on the plotter. extra: a tool's own record, stored in every file."""
    if mode not in PEN_OUTPUT_MODES:
        raise ValueError(f"pen_output: {mode!r} is not one of {list(PEN_OUTPUT_MODES)}")
    layers = arrange(layers, mode, optimise)
    path = Path(path)
    write_svg(path, width, height, layers, tool, values, precision, groups=mode == "groups", extra=extra)
    written = [path]
    if mode == "files" and len(layers) > 1:
        for n, layer in enumerate(layers, 1):
            written.append(write_svg(pen_file(path, n, layer.label), width, height, [layer], tool, values,
                                     precision, extra=extra))
    return written


# ----------------------------------------------------------------------------- polylines

def join_segments(segments, tol: float = 1e-6) -> list:
    """Chain segments (or short polylines) sharing end points into long polylines (the Overlap Cleaner's
    end-point joiner, reversing pieces as needed)."""
    from ..tools.svg_overlap_cleaner.svg_overlap_cleaner import chain_strokes
    strokes = [[tuple(map(float, p)) for p in s] for s in segments if len(s) >= 2]
    if not strokes:
        return []
    return [pts for _, pts in chain_strokes(strokes, tol, reverse=True)]


def simplify_all(polylines, tol: float) -> list:
    from ..tools.svg_overlap_cleaner.svg_overlap_cleaner import simplify
    return [simplify(list(pl), tol) for pl in polylines] if tol > 0 else list(polylines)


def order_polylines(polylines: list, start=(0.0, 0.0)) -> list:
    """Greedy nearest-neighbour drawing order, reversing a polyline when its far end is closer. Cuts pen-up
    travel a lot for hatching (typically 3-10×) at O(n log n)."""
    return [polylines[i] if not rev else polylines[i][::-1] for i, rev in order_indices(polylines, start)]


def order_indices(polylines: list, start=(0.0, 0.0)) -> list[tuple[int, bool]]:
    """The order of order_polylines as (index, reversed) pairs, for callers that carry data along each stroke."""
    n = len(polylines)
    if n < 2:
        return [(i, False) for i in range(n)]
    from scipy.spatial import cKDTree
    ends = np.array([p[0] for p in polylines] + [p[-1] for p in polylines], dtype=float)
    tree = cKDTree(ends)
    used = np.zeros(n, dtype=bool)
    out = []
    pos = np.asarray(start, dtype=float)
    k = 8
    while len(out) < n:
        found = None
        while found is None:
            kk = min(k, 2 * n)
            _, idx = tree.query(pos, k=kk)
            idx = np.atleast_1d(idx)
            for j in idx:
                if j < 2 * n and not used[j % n]:
                    found = int(j)
                    break
            if found is None:
                if kk >= 2 * n:
                    found = int(next(i for i in range(n) if not used[i]))
                    break
                k *= 4
        i = found % n
        used[i] = True
        rev = found >= n
        out.append((i, rev))
        pos = np.asarray(polylines[i][0] if rev else polylines[i][-1], dtype=float)
        k = 8
    return out


def dashed(polylines: list, dash: float, gap: float) -> list:
    """Polylines cut into dashes (mm): a dashed pen as geometry, which every plotter draws as it is."""
    out = []
    period = dash + gap
    for pl in polylines:
        P = np.asarray(pl, dtype=float)
        if len(P) < 2:
            continue
        seg = np.hypot(*np.diff(P, axis=0).T)
        s = np.r_[0.0, np.cumsum(seg)]
        total = s[-1]
        a = 0.0
        while a < total:
            b = min(a + dash, total)
            ts = np.r_[a, s[(s > a) & (s < b)], b]
            out.append([tuple(map(float, xy)) for xy in zip(np.interp(ts, s, P[:, 0]), np.interp(ts, s, P[:, 1]))])
            a += period
    return out


def travel(polylines: list) -> float:
    return sum(math.dist(a[-1], b[0]) for a, b in zip(polylines, polylines[1:]))
