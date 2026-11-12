"""
Shared SVG layer. The parsing core (transforms, path data with curves and arcs, units, document-order traversal
with pens) lives in the Overlap Cleaner's standard-library engine and is re-exported here, so every SVG tool
reads drawings the same way. On top of it: reading a drawing for previews, signing outputs for Atlas, and the
run record stored in <metadata> so settings can be restored from an output file.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ..tools.svg_overlap_cleaner.svg_overlap_cleaner import (  # noqa: F401  (re-exported)
    BASIC_SHAPES, EPS, IDENTITY, SHAPES, SKIP, SVG_NS, UNIT_MM, Item, SegmentIndex, apply, collect, dist, fmt, in_svg_ns, load,
    local, mat_inv, mat_mul, mm_per_unit, parse_path_d, parse_transform, path_data, shape_subpaths, simplify,
    style_of)
from .drawing import DEFAULT_COLORS, Drawing, Pen

INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
ATLAS_NS = "urn:atlas-toolkit"
_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_LEN = re.compile(r"\s*([-+]?[\d.eE+-]+)\s*([a-zA-Z%]*)\s*")


def length_mm(value: str | None) -> float | None:
    m = _LEN.fullmatch(value or "")
    if not m or m.group(2) == "%":
        return None
    try:
        return float(m.group(1)) * UNIT_MM.get(m.group(2).lower(), UNIT_MM["px"])
    except ValueError:
        return None


def sheet_of(root: ET.Element) -> tuple[float, float, tuple, float]:
    """(width_mm, height_mm, view_mm (x, y, w, h), mm per user unit)."""
    mpu = mm_per_unit(root)
    vb = [float(v) for v in _NUM.findall(root.get("viewBox") or "")]
    w, h = length_mm(root.get("width")), length_mm(root.get("height"))
    if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
        view = (vb[0] * mpu, vb[1] * mpu, vb[2] * mpu, vb[3] * mpu)
    else:
        view = (0.0, 0.0, (w or 210.0), (h or 297.0))
    return (w or view[2]), (h or view[3]), view, mpu


def read_drawing(src, flatten_mm: float = 0.05, *, shapes: set | None = None) -> Drawing:
    """Polylines of every drawable element, grouped by pen (top-level layer + stroke colour), in mm."""
    tree = src if isinstance(src, ET.ElementTree) else load(src)
    root = tree.getroot()
    width, height, view, mpu = sheet_of(root)
    items, _ = collect(root, SHAPES if shapes is None else shapes)
    groups: dict = {}
    for it in items:
        scale = abs(it.ctm[0] * it.ctm[3] - it.ctm[1] * it.ctm[2]) ** 0.5 or 1.0
        try:
            subs, _curved = shape_subpaths(it.el, it.tag, flatten_mm / mpu / scale)
        except ValueError:
            continue
        polys = groups.setdefault(it.pen, [])
        for pts, _closed in subs:
            polys.append([(x * mpu, y * mpu) for x, y in (apply(it.ctm, p) for p in pts)])
    pens = []
    children = list(root)
    for n, ((layer, stroke), polys) in enumerate(groups.items()):
        label = f"stroke {stroke}" if stroke else "default pen"
        if 0 <= layer < len(children):
            g = children[layer]
            name = g.get(f"{{{INKSCAPE_NS}}}label") or g.get("id")
            if name:
                label = name + (f" · {stroke}" if stroke and stroke not in ("#000", "#000000", "black") else "")
        color = stroke if stroke and stroke not in ("none",) and not stroke.startswith("url(") else \
            DEFAULT_COLORS[n % len(DEFAULT_COLORS)] if len(groups) > 1 else DEFAULT_COLORS[0]
        pens.append(Pen.from_polylines(f"{layer}:{stroke}", label, polys, color))
    return Drawing(width, height, view, pens)


def sign_desc(root: ET.Element, tool_id: str) -> None:
    """A desc element holding the tool id, as the root's first child (Atlas reads it from the first 4 KB)."""
    for child in root:
        if local(child.tag) == "desc" and (child.text or "").strip() == tool_id:
            return
    desc = ET.Element(f"{{{SVG_NS}}}desc")
    desc.text = tool_id
    desc.tail = "\n  "
    root.insert(0, desc)


def write_run_metadata(root: ET.Element, tool, values: dict) -> None:
    """Store the tool, its version and the settings in <metadata>, replacing an earlier record."""
    from ..version import __version__
    ET.register_namespace("atlas", ATLAS_NS)
    for md in [c for c in root if local(c.tag) == "metadata"]:
        for rec in [c for c in md if c.tag == f"{{{ATLAS_NS}}}run"]:
            md.remove(rec)
        if len(md) == 0 and not (md.text or "").strip():
            root.remove(md)
    md = ET.Element(f"{{{SVG_NS}}}metadata")
    rec = ET.SubElement(md, f"{{{ATLAS_NS}}}run", {"tool": tool.id, "version": tool.version, "toolkit": __version__})
    rec.text = json.dumps({k: (list(v) if isinstance(v, tuple) else v) for k, v in values.items()},
                          ensure_ascii=False)
    md.tail = "\n  "
    pos = 1 if len(root) and local(root[0].tag) == "desc" else 0
    root.insert(pos, md)


_RUN = re.compile(rb'<(?:\w+:)?run\b[^>]*\btool="([\w.\-]+)"[^>]*>(.*?)</(?:\w+:)?run>', re.S)


def read_run_metadata(path: Path) -> tuple[str, dict] | None:
    """(tool id, settings) recorded by write_run_metadata, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(256 * 1024)
    except OSError:
        return None
    m = _RUN.search(head)
    if not m:
        return None
    try:
        return m.group(1).decode(), json.loads(m.group(2).decode("utf-8"))
    except ValueError:
        return None


def write_tree(tree: ET.ElementTree, dst: Path) -> None:
    tree.write(dst, encoding="UTF-8", xml_declaration=True)


def inspect(path: Path):
    """InputInfo for any SVG: sheet, pens and ink, with the drawing as the 'Before' view."""
    from ..core.tool import InputInfo
    from .drawing import fmt_length
    from .paper import name_for
    tree = load(Path(path))
    d = read_drawing(tree)
    st = d.stats()
    layers = [p.label for p in d.pens]
    groups = layer_names(tree.getroot())
    summary = (f"{name_for(d.width, d.height)} · {st.pens} pen{'s' if st.pens != 1 else ''} · "
               f"{st.strokes:,} strokes · {fmt_length(st.ink)} of ink").replace(",", " ")
    return InputInfo(str(path), "svg", summary,
                     values={"width": d.width, "height": d.height, "layers": layers, "groups": groups,
                             "strokes": st.strokes,
                             "ink": st.ink, "max_dim": max(d.width, d.height)},
                     preview={"views": [{"title": "Before", "kind": "svg", "drawing": d}]})


def layer_names(root: ET.Element) -> list[str]:
    """Labels of the top-level groups (Inkscape layers), in order: label, else id, else 'group N'."""
    out = []
    for i, child in enumerate(c for c in root if local(c.tag) == "g"):
        out.append(child.get(f"{{{INKSCAPE_NS}}}label") or child.get("id") or f"group {i + 1}")
    return out
