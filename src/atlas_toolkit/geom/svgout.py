"""
Writing new plotter SVGs, for tools that draw from scratch (the Hatch Maker; future generators):

- sheet in millimetres (width="297mm" + viewBox in mm), as the studio's plotter SVGs are;
- one Inkscape layer per pen, strokes only, round caps and joins;
- signed first with a desc element holding the tool id (Atlas reads the head of the file), settings in <metadata>;
- helpers to join segments into polylines and to order polylines so the pen travels less.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .svg import INKSCAPE_NS, SVG_NS, fmt, write_run_metadata

ET.register_namespace("", SVG_NS)
ET.register_namespace("inkscape", INKSCAPE_NS)


@dataclass
class Layer:
    label: str
    polylines: list = field(default_factory=list)   # [[(x, y), ...], ...] in mm
    color: str = "#000000"
    width: float = 0.3                              # mm


def build_svg(width: float, height: float, layers: list[Layer], tool=None, values: dict | None = None,
              precision: int = 3) -> ET.ElementTree:
    root = ET.Element(f"{{{SVG_NS}}}svg", {"width": f"{fmt(width, 3)}mm", "height": f"{fmt(height, 3)}mm",
                                           "viewBox": f"0 0 {fmt(width, 3)} {fmt(height, 3)}"})
    root.text = "\n  "
    if tool is not None:
        desc = ET.SubElement(root, f"{{{SVG_NS}}}desc")
        desc.text = tool.id
        desc.tail = "\n  "
        write_run_metadata(root, tool, values or {})
    n = 0
    for layer in layers:
        if not layer.polylines:
            continue
        n += 1
        g = ET.SubElement(root, f"{{{SVG_NS}}}g", {
            f"{{{INKSCAPE_NS}}}groupmode": "layer", f"{{{INKSCAPE_NS}}}label": f"{n} {layer.label}",
            "id": f"layer{n}",
            "style": f"fill:none;stroke:{layer.color};stroke-width:{fmt(layer.width, 3)};"
                     "stroke-linecap:round;stroke-linejoin:round"})
        g.text = "\n    "
        for pl in layer.polylines:
            if len(pl) < 2:
                continue
            pts = " ".join(f"{fmt(x, precision)},{fmt(y, precision)}" for x, y in pl)
            p = ET.SubElement(g, f"{{{SVG_NS}}}path", {"d": f"M {pts.split(' ', 1)[0]} L {pts.split(' ', 1)[1]}"
                                                       if " " in pts else f"M {pts}"})
            p.tail = "\n    "
        g.tail = "\n  "
    return ET.ElementTree(root)


def write_svg(path: Path, width: float, height: float, layers: list[Layer], tool=None, values: dict | None = None,
              precision: int = 3) -> Path:
    tree = build_svg(width, height, layers, tool, values, precision)
    tree.write(path, encoding="UTF-8", xml_declaration=True)
    return Path(path)


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


def travel(polylines: list) -> float:
    return sum(math.dist(a[-1], b[0]) for a, b in zip(polylines, polylines[1:]))
