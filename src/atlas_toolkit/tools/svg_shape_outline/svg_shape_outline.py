"""
svg_shape_outline.py - Draw the outline of a drawing: one closed shape around its lines (from SVG_Shape_outline_v1).

Three ways to find it:

    envelope   follows the strokes: every line is widened by half the gap, the result is filled and shrunk back, so
               gaps narrower than `gap` mm are bridged and the outline sits on the outermost strokes. Several separate
               parts stay separate; enclosed empty areas become holes only if asked.
    concave    a concave hull of points sampled along every stroke (shapely), `concavity` 0 = convex, 1 = tightest.
               Scale-free, unlike v1's alpha (which also worked backwards to its label).
    convex     the convex hull.

Then: keep every island or only the largest, drop slivers, grow or shrink by a margin in mm, simplify.

All drawable elements count (paths with curves, lines, polylines, polygons, rects, circles, ellipses), with their
group transforms, in mm whatever the SVG units are (v1 read only paths and polygons, ignored transforms and wrote
no viewBox, so outlines of mm drawings came out at the wrong size). The outline is written on the same sheet as the
input, alone or as a new layer of the drawing, so it lines up with it (and with the Shape Cutter's "another SVG").

Outputs <name>_outline.svg, signed <desc>svg-shape-outline</desc> so Atlas links them here. Needs shapely 2.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPoint, Polygon

from atlas_toolkit.geom.svg import (BASIC_SHAPES, INKSCAPE_NS, SHAPES, SVG_NS, apply, collect, fmt, local,
                                    mm_per_unit, path_data, shape_subpaths)

TOOL = "svg-shape-outline"
ET.register_namespace("inkscape", INKSCAPE_NS)


@dataclass
class Options:
    method: str = "envelope"        # envelope | concave | convex
    layer: str = ""                 # "" = every layer, else a top-level group's label
    gap: float = 3.0                # mm, envelope: gaps narrower than this are bridged
    concavity: float = 0.6          # concave: 0 = convex hull .. 1 = tightest
    step: float = 1.0               # mm, concave/convex: strokes are sampled this often
    islands: str = "all"            # all | largest
    holes: bool = False             # keep enclosed empty areas as holes
    min_area: float = 4.0           # mm², smaller islands and holes are dropped
    offset: float = 0.0             # mm, grow (+) or shrink (-) the outline
    smooth: float = 0.05            # mm, simplification of the outline
    output: str = "only"            # only (the outline alone, same sheet) | layer (added to the drawing)
    color: str = "#000000"
    width: float = 0.35             # mm, outline stroke
    flatten: float = 0.05           # mm, curve flattening
    precision: int = 3


@dataclass
class Stats:
    elements: int = 0
    strokes: int = 0
    parts: int = 0
    holes: int = 0
    length: float = 0.0             # mm, outline length
    area: float = 0.0               # mm²
    rings: list = field(default_factory=list)   # outline rings, root user units (previews)

    def report(self, name: str, method: str) -> str:
        return "\n".join([
            name,
            f"  read       {self.elements} elements, {self.strokes} strokes",
            f"  outline    {method}: {self.parts} part(s), {self.holes} hole(s)",
            f"  length     {self.length:.1f} mm, enclosing {self.area / 100:.1f} cm²"])


class OutlineError(ValueError):
    pass


def _label(el: ET.Element) -> str:
    return (el.get(f"{{{INKSCAPE_NS}}}label") or el.get("id") or "").strip()


def strokes(root: ET.Element, layer: str, flatten_mm: float) -> tuple[list, int]:
    """Every stroke of the chosen layer (or all) as (k, 2) arrays in root user units, and the element count."""
    mpu = mm_per_unit(root)
    items, _ = collect(root, SHAPES | BASIC_SHAPES)
    if layer:
        groups = [g for g in root if local(g.tag) == "g"]
        names = [_label(g) or f"group {i + 1}" for i, g in enumerate(groups)]
        if layer not in names:
            raise OutlineError(f"no layer named {layer!r}")
        inside = {id(e) for e in groups[names.index(layer)].iter()}
        items = [it for it in items if id(it.el) in inside]
    out = []
    for it in items:
        scale = math.sqrt(abs(it.ctm[0] * it.ctm[3] - it.ctm[1] * it.ctm[2])) or 1.0
        try:
            subs, _ = shape_subpaths(it.el, it.tag, flatten_mm / mpu / scale)
        except ValueError:
            continue
        for pts, _closed in subs:
            if len(pts) >= 2:
                out.append(np.array([apply(it.ctm, p) for p in pts], dtype=float))
    return out, len(items)


def _sample(lines: list, step: float) -> np.ndarray:
    pts = []
    for pl in lines:
        seg = np.diff(pl, axis=0)
        n = np.maximum(1, np.ceil(np.hypot(seg[:, 0], seg[:, 1]) / max(step, 1e-9)).astype(int))
        pts.append(pl[:1])
        for a, s, k in zip(pl[:-1], seg, n):
            pts.append(a + (np.arange(1, k + 1) / k)[:, None] * s)
    return np.unique(np.round(np.concatenate(pts), 6), axis=0)


def _polygons(g) -> list:
    return [p for p in shapely.get_parts(g) if p.geom_type == "Polygon" and not p.is_empty]


def outline(lines: list, o: Options, mpu: float, progress=None):
    """The outline as a (Multi)Polygon in root user units."""
    if not lines:
        raise OutlineError("nothing to outline: no strokes found")
    u = 1.0 / mpu                                         # user units per mm
    min_area = o.min_area * u * u
    if o.method == "envelope":
        r = max(o.gap, 0.01) / 2 * u
        geoms = np.array([LineString(pl) for pl in lines], dtype=object)
        if progress:
            progress(0.3, "widening strokes")
        fat = shapely.union_all(shapely.buffer(geoms, r, quad_segs=6))
        if progress:
            progress(0.6, "closing gaps")
        parts = []
        for poly in _polygons(fat):
            # fill what the strokes enclose and shrink back, so the edge lies on the outermost strokes; a kept hole
            # (an empty area the strokes enclose) is cut out with its edge on the strokes around it
            solid = Polygon(poly.exterior).buffer(-r, quad_segs=6)
            holes = [Polygon(h) for h in poly.interiors] if o.holes else []
            holes = [h for h in holes if h.area >= min_area]
            if holes and not solid.is_empty:
                solid = solid.difference(shapely.union_all(holes).buffer(r, quad_segs=6))
            parts.append(solid)
        shape = shapely.union_all(parts)
        if shape.is_empty:   # open strokes enclosing nothing: keep their widened silhouette
            shape = shapely.union_all([Polygon(p.exterior) for p in _polygons(fat)])
    else:
        pts = _sample(lines, o.step * u)
        if len(pts) < 3:
            raise OutlineError("too few points for a hull")
        mp = MultiPoint(pts)
        shape = mp.convex_hull if o.method == "convex" else \
            shapely.concave_hull(mp, ratio=1.0 - min(max(o.concavity, 0.0), 1.0), allow_holes=o.holes)
    polys = _polygons(shape)
    polys =[Polygon(p.exterior, [h for h in p.interiors if Polygon(h).area >= min_area] if o.holes else [])
             for p in polys if p.area >= min_area]
    if not polys:
        raise OutlineError("the outline is empty (lower the minimum area or widen the gap)")
    if o.islands == "largest":
        polys = [max(polys, key=lambda p: p.area)]
    shape = shapely.union_all(polys)
    if o.offset:
        shape = shape.buffer(o.offset * u, quad_segs=8, join_style="round")
        if shape.is_empty:
            raise OutlineError("the inward offset removed the whole outline")
    if o.smooth > 0:
        shape = shape.simplify(o.smooth * u, preserve_topology=True)
    return shape


def outline_tree(tree: ET.ElementTree, o: Options, progress=None) -> tuple[ET.ElementTree, Stats]:
    """The output document (the input with an outline layer, or the outline alone on the same sheet)."""
    root = tree.getroot()
    mpu = mm_per_unit(root)
    lines, n = strokes(root, o.layer, o.flatten)
    st = Stats(elements=n, strokes=len(lines))
    shape = outline(lines, o, mpu, progress)
    rings = []
    for p in _polygons(shape):
        st.parts += 1
        st.holes += len(p.interiors)
        rings.append(list(p.exterior.coords))
        rings += [list(h.coords) for h in p.interiors]
    st.rings = rings
    st.length = float(shape.length) * mpu
    st.area = float(shape.area) * mpu * mpu
    if o.output == "only":
        new = ET.Element(f"{{{SVG_NS}}}svg", {k: v for k, v in root.attrib.items()
                                              if k in ("width", "height", "viewBox", "preserveAspectRatio")})
        new.text = "\n  "
        out = ET.ElementTree(new)
        target = new
    else:
        out, target = tree, root
    g = ET.SubElement(target, f"{{{SVG_NS}}}g", {
        f"{{{INKSCAPE_NS}}}groupmode": "layer", f"{{{INKSCAPE_NS}}}label": "outline", "id": "outline",
        "style": f"fill:none;stroke:{o.color};stroke-width:{fmt(o.width / mpu, 4)};stroke-linecap:round;"
                 "stroke-linejoin:round"})
    g.text = "\n    "
    p = ET.SubElement(g, f"{{{SVG_NS}}}path", {"d": path_data([(r_, True) for r_ in rings], o.precision)})
    p.tail = "\n  "
    g.tail = "\n"
    return out, st
