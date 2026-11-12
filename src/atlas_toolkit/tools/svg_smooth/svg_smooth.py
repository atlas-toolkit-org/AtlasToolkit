"""Physical-scale line-art fairing, deliberately independent of Line Simplify.

Geometry is filtered, never selected/decimated. Matched piecewise-linear source
and output partitions give a continuous displacement bound. Guards backtrack a
whole stroke uniformly (no pointwise clipping that would put kinks back in it).
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field

import numpy as np
import shapely

from atlas_toolkit.geom.svg import (BASIC_SHAPES, SHAPES, apply, collect, length_mm, local,
                                    mat_mul, mm_per_unit, parse_transform, shape_subpaths, style_of)
# Reuse only bounded SVG curve parsing, never a simplification operation.
from ..svg_simplify.curves import FLATTENERS
from .filters import arclength, corner_locks, proposal
from .safety import Guard

MAX_POINTS = 2_000_000


@dataclass
class Options:
    method: str = "whittaker"
    span: float = 1.0
    strength: float = 100.
    deviation: float = .15
    corner_angle: float = 65.
    corner_support: float = 1.
    topology: bool = True
    area_change: float = 3.
    length_change: float = 25.
    curves: str = "keep"
    sample_step: float = .08
    curve_error: float = .005
    filled: bool = False
    point_limit: int = field(default=MAX_POINTS, init=False, repr=False)


@dataclass
class Stroke:
    local: np.ndarray
    points: np.ndarray
    closed: bool
    contacts: np.ndarray
    output: np.ndarray | None = None


@dataclass
class Stats:
    strokes: int = 0
    smoothed: int = 0
    changed: int = 0
    limited: int = 0
    kept: int = 0
    vertices_in: int = 0
    vertices_out: int = 0
    displacement: float = 0.
    length_in: float = 0.
    length_out: float = 0.
    blend_sum: float = 0.
    notes: list = field(default_factory=list)


def physical_matrix(root):
    """Sheet metric, including anisotropic viewports and the SVG2 root transform."""
    vb = [float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", root.get("viewBox", ""))]
    mpu = mm_per_unit(root)
    sx = sy = mpu
    if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
        sx = (length_mm(root.get("width")) or vb[2] * mpu) / vb[2]
        sy = (length_mm(root.get("height")) or vb[3] * mpu) / vb[3]
        aspect = root.get("preserveAspectRatio", "xMidYMid meet").split()
        if "none" not in aspect:
            sx = sy = max(sx, sy) if "slice" in aspect else min(sx, sy)
    return mat_mul((sx, 0., 0., sy, 0., 0.), parse_transform(root.get("transform")))


def area(p):
    p = p - p[0]
    return float(np.sum(p[:-1, 0] * p[1:, 1] - p[1:, 0] * p[:-1, 1])) / 2


def _validate(o):
    for key in ("span", "strength", "deviation", "corner_angle", "corner_support", "area_change",
                "length_change", "sample_step", "curve_error"):
        value = getattr(o, key)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{key} must be finite and non-negative.")
    if min(o.span, o.corner_support, o.sample_step, o.curve_error) <= 0:
        raise ValueError("Smoothing span, corner support and sampling spacings must be positive.")
    if o.strength > 100 or o.corner_angle > 180 or o.area_change > 100 or o.length_change > 100:
        raise ValueError("Strength and preservation percentages must be within range.")
    if o.method not in ("whittaker", "taubin") or o.curves not in ("keep", "smooth"):
        raise ValueError("Unknown smoothing algorithm or curve mode.")
    if o.curves == "smooth" and o.deviation and o.curve_error >= o.deviation:
        raise ValueError("Curve sampling error must be smaller than Maximum displacement.")


def smooth_stroke(s, o, guard, owner, st, check):
    p = s.points
    # Repeated points can encode dots, markers or retraced accents; retain exactly.
    if len(p) < 3 or np.any(np.linalg.norm(np.diff(p, axis=0), axis=1) == 0):
        return p
    original = shapely.LineString(p)
    # Existing self-crossings/retracing are kept rather than inferred or rewired.
    if not original.is_simple:
        st.kept += 1
        return p
    locks = corner_locks(p, s.closed, o.corner_support, o.corner_angle) | s.contacts
    if s.closed:
        locks[0] = locks[-1] = locks[0] or locks[-1]
    if np.all(locks):
        return p
    source, target, _ = proposal(p, s.closed, locks, o, check)
    shift = target - source
    maximum = float(np.linalg.norm(shift, axis=1).max())
    if maximum <= max(1e-12, o.deviation * 1e-10):
        return p
    # The curve approximation consumes part of the physical displacement budget.
    budget = o.deviation - (o.curve_error if o.curves == "smooth" else 0.)
    blend = min(o.strength / 100, max(0., budget) / maximum)
    original_length = float(arclength(p)[-1])
    original_area = area(p) if s.closed else 0.
    for attempt in range(10):
        check()
        q = source + blend * shift
        # Keep exact fixed coordinates, avoiding unnecessary floating-point additions.
        fixed = np.all(source == target, axis=1)
        q[fixed] = source[fixed]
        length_ok = abs(float(arclength(q)[-1]) - original_length) <= original_length * o.length_change / 100 + 1e-12
        area_ok = True
        if s.closed:
            a = area(q)
            area_ok = original_area * a > 0 and abs(a - original_area) <= abs(original_area) * o.area_change / 100 + 1e-12
        safe = length_ok and area_ok
        if safe and o.topology:
            safe = shapely.LineString(q).is_simple and guard.safe(owner, source, q, check)
        if safe:
            st.smoothed += 1
            st.blend_sum += blend * 100
            st.displacement = max(st.displacement, blend * maximum)
            st.limited += blend + 1e-10 < o.strength / 100
            return q
        blend *= .5
    st.limited += 1
    st.kept += 1
    return p


def _coords(p):
    return " ".join(f"{float(x)!r},{float(y)!r}" for x, y in p)


def _local_points(s, matrix):
    # Invert only for changed points; exact anchors retain their original local spelling/value.
    linear = np.array([[matrix[0], matrix[2]], [matrix[1], matrix[3]]])
    q = np.linalg.solve(linear, (s.output - matrix[4:6]).T).T
    originals = {tuple(world): point for world, point in zip(s.points, s.local)}
    for i, point in enumerate(s.output):
        anchored = originals.get(tuple(point))
        if anchored is not None:
            q[i] = anchored
    return q


def smooth_tree(tree, o: Options, progress=lambda f, m="": None):
    _validate(o)
    emit, last = progress, [0., ""]

    def progress(f, message=""):
        # Empty messages still check cancellation in JobContext without flooding
        # the UI/worker pipe with tens of thousands of identical progress events.
        now = time.monotonic()
        show = message != last[1] or now - last[0] >= .05 or f >= 1
        if show:
            last[:] = [now, message]
        emit(f, message if show else "")

    st = Stats()
    if not o.strength or not o.deviation:
        st.notes.append("Zero strength/displacement: source geometry retained exactly.")
        return st
    root = tree.getroot()
    parents = {child: parent for parent in root.iter() for child in parent}
    unsupported = {"use", "text", "textPath", "foreignObject", "style", "script", "animate", "animateMotion", "animateTransform"}
    if any(local(e.tag) in unsupported or (e is not root and local(e.tag) == "svg") for e in root.iter()):
        st.notes.append("Geometry retained: expand instances/text/CSS/animation/nested viewports before smoothing.")
        return st
    items, plotdata = collect(root, SHAPES | BASIC_SHAPES)
    metric, strokes, records = physical_matrix(root), [], []
    skipped = 0
    for n, it in enumerate(items):
        progress(.02 + .18 * n / max(1, len(items)), "Reading line art in millimetres")
        ctm = mat_mul(metric, it.ctm)
        linear = np.array([[ctm[0], ctm[2]], [ctm[1], ctm[3]]])
        if not np.isfinite(ctm).all() or abs(np.linalg.det(linear)) < 1e-18:
            raise ValueError("Collapsed or non-finite SVG transform; expand transforms before smoothing.")
        scale = float(np.linalg.svd(linear, compute_uv=False)[0])
        subs, curved = shape_subpaths(it.el, it.tag, o.curve_error / scale, flatteners=FLATTENERS)
        eligible = it.tag in SHAPES and (not curved or o.curves == "smooth")
        inherited, ancestors = {}, []
        el = it.el
        while el is not None:
            ancestors.append(el)
            el = parents.get(el)
        for el in reversed(ancestors):
            inherited.update(el.attrib)
            inherited.update(style_of(el))
            attrs = " ".join(f"{local(k)}:{v}" for k, v in el.attrib.items())
            if re.search(r"marker[\s:-]|stroke-dasharray|path-effect|pathLength|clip-path|filter|mask", attrs):
                eligible = False
        if not o.filled and it.tag in ("path", "polyline", "polygon") and inherited.get("fill", "black") != "none":
            eligible = False
        current = []
        for points, closed in subs:
            if not points:
                continue
            points = np.asarray(points, dtype=float)
            world = points @ linear.T + np.asarray(ctm[4:6])
            if not np.isfinite(world).all():
                raise ValueError("Non-finite SVG coordinates.")
            stroke = Stroke(points, world, closed, np.zeros(len(points), dtype=bool))
            current.append((len(strokes), stroke))
            strokes.append(stroke)
            st.vertices_in += len(points)
            if st.vertices_in > MAX_POINTS:
                raise ValueError("Drawing exceeds 2,000,000 sampled vertices; increase Curve sampling error.")
        if not eligible:
            skipped += len(current)
        records.append((it, ctm, current, eligible))
    st.strokes = len(strokes)
    progress(.21, "Anchoring crossings and junctions")
    guard = Guard(strokes, lambda: progress(.22, "Indexing all-pen contacts")) if o.topology else None
    allocated = 0
    for n, (it, ctm, current, eligible) in enumerate(records):
        check = lambda n=n: progress(.25 + .7 * n / max(1, len(records)), "Smoothing with preservation limits")
        check()
        changed = False
        for owner, s in current:
            o.point_limit = MAX_POINTS - allocated
            s.output = smooth_stroke(s, o, guard, owner, st, check) if eligible else s.points
            allocated += len(s.output)
            if allocated > MAX_POINTS:
                raise ValueError("Smoothing exceeds 2,000,000 output samples; increase Sample spacing.")
            different = s.output is not s.points
            changed |= different
            st.length_in += float(arclength(s.points)[-1])
            st.length_out += float(arclength(s.output)[-1])
            st.vertices_out += len(s.output)
            if guard and different:
                guard.add(s.output)
        if changed:
            st.changed += 1
            if it.tag == "path":
                pieces = []
                for _, s in current:
                    p = _local_points(s, ctm) if s.output is not s.points else s.local
                    if s.closed and len(p) > 1 and np.array_equal(p[0], p[-1]):
                        p = p[:-1]
                    pieces.append("M " + _coords(p[:1]) + (" L " + _coords(p[1:]) if len(p) > 1 else "") + (" Z" if s.closed else ""))
                it.el.set("d", " ".join(pieces))
            else:
                s = current[0][1]
                p = _local_points(s, ctm)
                if s.closed and np.array_equal(p[0], p[-1]):
                    p = p[:-1]
                it.el.set("points", _coords(p))
    if st.changed:
        for parent, el in plotdata:
            parent.remove(el)
    st.notes.append("Filtering moves geometry; no simplification or vertex-reduction pass. SVG output uses dense straight segments.")
    st.notes.append(f"{skipped} strokes kept by curve/shape/style policy; {st.kept} retained by preservation checks.")
    st.notes.append("Topology checks use sampled curves, across all pens; existing self-crossing strokes are retained.")
    progress(1., "Ready")
    return st
