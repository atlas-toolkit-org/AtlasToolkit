"""
svg_shape_cutter.py - Clip a drawing to a shape: keep the linework inside (or outside) a closed outline.

The shape is an element or layer named "shape" (id or Inkscape label, as in the first Shape Cutter), a chosen
layer, or the closed outlines of another SVG laid over this one in millimetres. Everything else is clipped, or
only the element/layer named "hatch" when there is one. All of the shape's islands and holes count (even-odd, as
SVG fills them). Elements entirely kept stay exactly as written (curves included); elements entirely cut are
removed; the rest become paths holding the clipped strokes, merged end to end so the pen lifts only where it must.
Group transforms, ids, styles and other layers are kept.

Writes <name>_cutout.svg, signed <desc>svg-shape-cutter</desc> so Atlas links it here.
Needs shapely 2.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import shapely
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import linemerge

from atlas_toolkit.geom.svg import (BASIC_SHAPES, INKSCAPE_NS, SHAPES, SVG_NS, apply, collect, load, local, mat_inv,
                                    mm_per_unit, path_data, shape_subpaths)

TOOL = "svg-shape-cutter"
GEOMETRY_ATTRS = ("x1", "y1", "x2", "y2", "points", "x", "y", "width", "height", "rx", "ry", "cx", "cy", "r")


@dataclass
class Options:
    shape_source: str = "named"      # "named" | "layer" | "file"
    shape_layer: str = ""            # with "layer": the top-level group's label
    shape_file: str = ""             # with "file": an SVG whose closed outlines are the shape
    keep: str = "inside"             # "inside" | "outside"
    keep_shape: bool = False         # keep the shape's own outline in the output
    merge: bool = True               # join clipped segments end to end
    min_length: float = 0.05         # mm: clipped bits shorter than this are dropped
    flatten: float = 0.02            # mm: curve flattening for clipping
    precision: int = 4


@dataclass
class Stats:
    shape_elements: int = 0
    shape_parts: int = 0             # islands of the mask
    shape_holes: int = 0
    clipped: int = 0                 # elements partly kept
    removed: int = 0                 # elements entirely cut
    untouched: int = 0               # elements entirely kept
    strokes_in: int = 0
    strokes_out: int = 0
    ink_in: float = 0.0              # mm, of the clipped elements
    ink_out: float = 0.0
    mask: list = field(default_factory=list)   # mask rings in root units (previews)
    notes: list = field(default_factory=list)

    def report(self, name: str) -> str:
        lines = [name,
                 f"  shape      {self.shape_elements} element(s) -> {self.shape_parts} part(s), {self.shape_holes} hole(s)",
                 f"  elements   {self.untouched} kept whole, {self.clipped} clipped, {self.removed} removed",
                 f"  strokes    {self.strokes_in} -> {self.strokes_out}",
                 f"  ink        {self.ink_in:.1f} -> {self.ink_out:.1f} mm"]
        lines += [f"  note       {n}" for n in self.notes]
        return "\n".join(lines)


class ShapeError(ValueError):
    pass


def _label(el: ET.Element) -> str:
    return (el.get(f"{{{INKSCAPE_NS}}}label") or el.get("id") or "").strip()


def _named(root: ET.Element, name: str) -> ET.Element | None:
    for el in root.iter():
        if local(el.tag) in SHAPES | BASIC_SHAPES | {"g"} and _label(el).lower() == name:
            return el
    return None


def _within(el: ET.Element, items: list) -> list:
    inside = {id(e) for e in el.iter()}
    return [it for it in items if id(it.el) in inside]


def _rings(items: list, flat: float, mpu: float) -> list:
    rings = []
    for it in items:
        scale = math.sqrt(abs(it.ctm[0] * it.ctm[3] - it.ctm[1] * it.ctm[2])) or 1.0
        subs, _ = shape_subpaths(it.el, it.tag, flat / mpu / scale)
        for pts, _closed in subs:
            pts = [apply(it.ctm, p) for p in pts]
            if len(pts) >= 3:
                rings.append(pts)
    return rings


def build_mask(rings: list) -> shapely.Geometry:
    """Even-odd union of closed rings (an outline inside another is a hole), repaired when self-intersecting."""
    mask = None
    for pts in rings:
        poly = shapely.make_valid(Polygon(pts))
        poly = shapely.union_all([g for g in shapely.get_parts(poly) if g.geom_type in ("Polygon", "MultiPolygon")]) \
            if poly.geom_type == "GeometryCollection" else poly
        if poly.is_empty or poly.area <= 0:
            continue
        mask = poly if mask is None else mask.symmetric_difference(poly)
    if mask is None or mask.is_empty:
        raise ShapeError("the shape has no closed area")
    return mask


def _lines(geom) -> list:
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    if geom.geom_type in ("MultiLineString", "GeometryCollection"):
        return [g for part in shapely.get_parts(geom) for g in _lines(part)]
    return []


def cut_tree(tree: ET.ElementTree, opts: Options, progress=None, log=None) -> Stats:
    root = tree.getroot()
    st = Stats()
    mpu = mm_per_unit(root)
    items, _ = collect(root, SHAPES | BASIC_SHAPES)
    shape_items: list = []
    hatch_items: list | None = None
    rings: list = []
    if opts.shape_source == "file":
        if not opts.shape_file or not Path(opts.shape_file).exists():
            raise ShapeError("choose the SVG file that holds the shape")
        other = load(Path(opts.shape_file)).getroot()
        ompu = mm_per_unit(other)
        oitems, _ = collect(other, SHAPES | BASIC_SHAPES)
        rings = [[(x * ompu / mpu, y * ompu / mpu) for x, y in r] for r in _rings(oitems, opts.flatten, ompu)]
        st.shape_elements = len(oitems)
    else:
        if opts.shape_source == "layer":
            layer = next((g for g in root if local(g.tag) == "g" and _label(g) == opts.shape_layer), None)
            if layer is None:
                raise ShapeError(f"no layer named {opts.shape_layer!r}")
            shape_items = _within(layer, items)
        else:
            el = _named(root, "shape")
            if el is not None:
                shape_items = _within(el, items)
            else:   # the first Shape Cutter's fallback: the first path is the shape
                first = next((it for it in items if it.tag == "path"), None)
                if first is None:
                    raise ShapeError('no element or layer named "shape", and no path to use instead')
                shape_items = [first]
                st.notes.append(f'no element named "shape": used the first path ({first.el.get("id") or "no id"})')
        rings = _rings(shape_items, opts.flatten, mpu)
        st.shape_elements = len(shape_items)
        hatch = _named(root, "hatch")
        if hatch is not None:
            hatch_items = _within(hatch, items)
    mask = build_mask(rings)
    for poly in shapely.get_parts(mask):
        if poly.geom_type == "Polygon":
            st.shape_parts += 1
            st.shape_holes += len(poly.interiors)
            st.mask.append(list(poly.exterior.coords))
            st.mask += [list(r.coords) for r in poly.interiors]
    shape_ids = {id(it.el) for it in shape_items}
    targets = [it for it in (hatch_items if hatch_items is not None else items) if id(it.el) not in shape_ids]
    shapely.prepare(mask)
    min_len = opts.min_length / mpu
    inside = opts.keep == "inside"
    for n, it in enumerate(targets):
        if progress is not None:
            progress(n, len(targets))
        inv = mat_inv(it.ctm)
        if inv is None:
            continue
        scale = math.sqrt(abs(it.ctm[0] * it.ctm[3] - it.ctm[1] * it.ctm[2])) or 1.0
        try:
            subs, _ = shape_subpaths(it.el, it.tag, opts.flatten / mpu / scale)
        except ValueError as e:
            if log:
                log(f"skipped {it.tag} {it.el.get('id') or ''}: {e}")
            continue
        lines = [LineString([apply(it.ctm, p) for p in pts]) for pts, _ in subs if len(pts) > 1]
        lines = [ln for ln in lines if ln.length > 0]
        if not lines:
            continue
        geom = MultiLineString(lines) if len(lines) > 1 else lines[0]
        st.strokes_in += len(lines)
        st.ink_in += geom.length * mpu
        if shapely.contains(mask, geom) if inside else shapely.disjoint(mask, geom):
            st.untouched += 1
            st.strokes_out += len(lines)
            st.ink_out += geom.length * mpu
            continue
        if shapely.disjoint(mask, geom) if inside else shapely.contains(mask, geom):
            it.parent.remove(it.el)
            st.removed += 1
            continue
        kept = shapely.intersection(geom, mask) if inside else shapely.difference(geom, mask)
        parts = _lines(kept)
        if opts.merge and len(parts) > 1:
            parts = _lines(linemerge(MultiLineString(parts)))
        parts = [ln for ln in parts if ln.length >= min_len]
        if not parts:
            it.parent.remove(it.el)
            st.removed += 1
            continue
        st.clipped += 1
        st.strokes_out += len(parts)
        st.ink_out += sum(ln.length for ln in parts) * mpu
        polys = [([apply(inv, p) for p in ln.coords], False) for ln in parts]
        if it.tag != "path":
            for k in GEOMETRY_ATTRS:
                it.el.attrib.pop(k, None)
            it.el.tag = f"{{{SVG_NS}}}path"
        it.el.set("d", path_data(polys, opts.precision))
    if not opts.keep_shape:
        for it in shape_items:
            try:
                it.parent.remove(it.el)
            except ValueError:
                pass
    return st


def default_output(src: Path) -> Path:
    return src.with_name(f"{src.stem}_cutout.svg")
