#!/usr/bin/env python3
"""
svg_overlap_cleaner.py - Remove retraced ink from pen-plotter SVGs so every line is drawn only once.

Paths are read in plotting order. Any stretch of a path that runs over ink an earlier path (or the same path,
earlier) has already drawn - nearly parallel (--angle), within --tol mm, for at least --min-overlap mm - is cut
out, and the path is split there. Crossings and touching ends are never cut, so a path that leaves a shared
line keeps its vertex on that line. Then duplicate and collinear vertices are dropped (Douglas-Peucker with
--simplify mm, never past a turnaround).

Made for relief-app ridgeline SVGs whose flat baselines all lie on one line (drawn ~40 times on top of itself,
tearing the paper), but it works on any SVG made of paths, lines, polylines and polygons. Element order, ids,
styles, groups and transforms are kept; overlaps are only looked for within one pen (top-level group + stroke
colour) unless --across-pens. Curves are flattened only in paths that actually change. AxiDraw <plotdata>
(pause/resume position) is dropped, since node numbers change.

Optional stages, all off by default (the defaults behave exactly as before):
  --join MM            join stroke ends closer than MM (same pen and group; reversing strokes when needed)
  --whole-duplicates   only remove elements whose ink is entirely drawn before; split nothing (replaces svg_dedup)
  --shapes             also read rect, circle and ellipse (written back as paths only when they change)
  --pen-by-width       stroke width is part of the pen, so a bold line drawn twice on purpose is kept

Writes <name>--nooverlap.svg next to the input, signed <desc>svg-overlap-cleaner</desc> (Atlas links it here).

Usage:
  python svg_overlap_cleaner.py drawing.svg
  python svg_overlap_cleaner.py drawing.svg -o clean.svg --tol 0.05 --min-overlap 2 -v
  python svg_overlap_cleaner.py a.svg b.svg --dry-run        report only, write nothing
  python svg_overlap_cleaner.py                              opens a file dialog

Standard library only (Python 3.9+).
"""
from __future__ import annotations

import argparse
import io
import math
import re
import sys
import xml.etree.ElementTree as ET
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

TOOL = "svg-overlap-cleaner"
SVG_NS = "http://www.w3.org/2000/svg"
SKIP = {"defs", "clipPath", "mask", "symbol", "marker", "pattern", "metadata", "title", "desc", "style", "script",
        "text", "image", "use", "foreignObject"}
SHAPES = {"path", "line", "polyline", "polygon"}
BASIC_SHAPES = {"rect", "circle", "ellipse"}   # read only with Options.shapes
GEOMETRY_ATTRS = ("x1", "y1", "x2", "y2", "points", "x", "y", "width", "height", "rx", "ry", "cx", "cy", "r")
UNIT_MM = {"mm": 1.0, "cm": 10.0, "q": 0.25, "in": 25.4, "pt": 25.4 / 72, "pc": 25.4 / 6, "px": 25.4 / 96,
           "": 25.4 / 96}
EPS = 1e-9


@dataclass
class Options:
    tol: float = 0.05           # mm: two strokes closer than this are the same ink
    angle: float = 5.0          # degrees: only nearly parallel strokes can overlap (never crossings)
    min_overlap: float = 2.0    # mm: shorter retraced stretches are left alone (not worth two pen lifts)
    min_piece: float = 0.3      # mm: leftover bits shorter than this, between cuts, are dropped
    simplify: float = 0.01      # mm: Douglas-Peucker tolerance for collinear vertices (0 = only exact duplicates)
    flatten: float = 0.01       # mm: chord tolerance when curves have to be flattened
    across_pens: bool = False   # also remove overlaps between different layers / stroke colours
    precision: int = 4          # decimals written in path data
    verbose: bool = False
    collect_cuts: bool = False  # keep the removed stretches in Stats.cuts (previews)
    mode: str = "retraced"      # "retraced": cut retraced stretches; "duplicates": only drop wholly retraced elements
    join: float = 0.0           # mm: join stroke ends closer than this (0 = off)
    join_reverse: bool = True   # strokes may be reversed to join end to end
    join_scope: str = "pen"     # "pen": across elements of one pen and group; "element": within each element
    shapes: bool = False        # also read rect, circle and ellipse
    pen_by_width: bool = False  # the pen includes the stroke width (protects deliberate bold double lines)


@dataclass
class Row:
    """One changed element (with --verbose)."""
    name: str
    strokes_in: int
    strokes_out: int
    vertices_in: int
    vertices_out: int
    retraced: float             # mm

    def __str__(self) -> str:
        return (f"  {self.name:<14} strokes {self.strokes_in} -> {self.strokes_out}, "
                f"vertices {self.vertices_in} -> {self.vertices_out}, retraced {self.retraced:.1f} mm")


@dataclass
class Stats:
    elements: int = 0
    changed: int = 0
    removed_elements: int = 0
    strokes_in: int = 0         # subpaths = pen-down strokes
    strokes_out: int = 0
    vertices_in: int = 0
    vertices_out: int = 0
    ink_in: float = 0.0         # mm
    ink_out: float = 0.0
    retraced: float = 0.0       # mm of ink cut because it was already drawn
    plotdata_removed: bool = False
    rows: list = field(default_factory=list)
    cuts: list = field(default_factory=list)   # removed stretches, root user units (with collect_cuts)
    joined: int = 0             # stroke ends joined (with --join)

    def report(self, name: str) -> str:
        pct = 100.0 * self.retraced / self.ink_in if self.ink_in else 0.0
        lines = [f"{name}",
                 f"  elements   {self.elements} -> {self.elements - self.removed_elements}"
                 f"  ({self.changed} changed, {self.removed_elements} removed: fully retraced)",
                 f"  strokes    {self.strokes_in} -> {self.strokes_out}  (pen-down runs)",
                 f"  vertices   {self.vertices_in} -> {self.vertices_out}",
                 f"  ink        {self.ink_in:.1f} -> {self.ink_out:.1f} mm  ({self.retraced:.1f} mm retraced ink cut,"
                 f" {pct:.1f}%)"]
        if self.joined:
            lines.append(f"  joined     {self.joined} stroke end(s) to the next stroke")
        if self.plotdata_removed:
            lines.append("  dropped the AxiDraw <plotdata> resume block (node numbers changed)")
        return "\n".join(lines)


# ----------------------------------------------------------------------------- geometry helpers

Matrix = tuple  # (a, b, c, d, e, f): x' = a*x + c*y + e, y' = b*x + d*y + f
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_TF = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")


def mat_mul(m: Matrix, n: Matrix) -> Matrix:
    """m after n."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D, a * E + c * F + e, b * E + d * F + f)


def mat_inv(m: Matrix) -> Matrix | None:
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    return (d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det)


def apply(m: Matrix, p: tuple) -> tuple:
    a, b, c, d, e, f = m
    return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)


def parse_transform(s: str | None) -> Matrix:
    m = IDENTITY
    for name, args in _TF.findall(s or ""):
        v = [float(x) for x in _NUM.findall(args)]
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate" and v:
            t = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale" and v:
            t = (v[0], 0.0, 0.0, v[1] if len(v) > 1 else v[0], 0.0, 0.0)
        elif name == "rotate" and v:
            r = math.radians(v[0])
            t = (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0.0, 0.0)
            if len(v) == 3:
                t = mat_mul(mat_mul((1.0, 0.0, 0.0, 1.0, v[1], v[2]), t), (1.0, 0.0, 0.0, 1.0, -v[1], -v[2]))
        elif name == "skewX" and v:
            t = (1.0, 0.0, math.tan(math.radians(v[0])), 1.0, 0.0, 0.0)
        elif name == "skewY" and v:
            t = (1.0, math.tan(math.radians(v[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        m = mat_mul(m, t)
    return m


def dist(p: tuple, q: tuple) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


def seg_dist(p: tuple, a: tuple, b: tuple) -> float:
    """Distance from p to segment ab (not the infinite line, so a turnaround vertex is never 'collinear')."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def simplify(pts: list, tol: float) -> list:
    """Douglas-Peucker on a polyline; drops exact duplicates even with tol = 0."""
    out = [pts[0]]
    for p in pts[1:]:
        if dist(p, out[-1]) > EPS:
            out.append(p)
    if len(out) == 1:
        return [pts[0], pts[-1]] if len(pts) > 1 else out
    if tol <= 0 or len(out) < 3:
        return out
    keep = [False] * len(out)
    keep[0] = keep[-1] = True
    stack = [(0, len(out) - 1)]
    while stack:
        i, j = stack.pop()
        best, idx = tol, -1
        for k in range(i + 1, j):
            d = seg_dist(out[k], out[i], out[j])
            if d > best:
                best, idx = d, k
        if idx >= 0:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    return [p for p, k in zip(out, keep) if k]


# ----------------------------------------------------------------------------- SVG path data -> polylines

_CMD_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
_NUMTOK = re.compile(r"[\s,]*([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)")
_FLAGTOK = re.compile(r"[\s,]*([01])")


def _cubic(p0, p1, p2, p3, tol):
    d = max(math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1]),
            math.hypot(p1[0] - 2 * p2[0] + p3[0], p1[1] - 2 * p2[1] + p3[1]))
    n = max(1, min(1000, math.ceil(math.sqrt(3 * d / (4 * tol))))) if d > 0 else 1
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def _quad(p0, p1, p2, tol):
    d = math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1])
    n = max(1, min(1000, math.ceil(math.sqrt(d / (4 * tol))))) if d > 0 else 1
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]) for t in (i / n for i in range(1, n + 1))]


def _arc(p1, rx, ry, phi, large, sweep, p2, tol):
    """SVG elliptical arc (endpoint parametrisation, SVG 1.1 F.6.5) as points after p1."""
    if dist(p1, p2) < EPS:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < EPS or ry < EPS:
        return [p2]
    phi = math.radians(phi % 360)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (p1[0] - p2[0]) / 2, (p1[1] - p2[1]) / 2
    x1, y1 = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1 / rx) ** 2 + (y1 / ry) ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp, cyp = coef * rx * y1 / ry, -coef * ry * x1 / rx
    cx = cp * cxp - sp * cyp + (p1[0] + p2[0]) / 2
    cy = sp * cxp + cp * cyp + (p1[1] + p2[1]) / 2

    def ang(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    th1 = ang(1, 0, (x1 - cxp) / rx, (y1 - cyp) / ry)
    dth = ang((x1 - cxp) / rx, (y1 - cyp) / ry, (-x1 - cxp) / rx, (-y1 - cyp) / ry)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    r = max(rx, ry)
    step = 2 * math.acos(1 - tol / r) if tol < r else math.pi / 2
    n = max(1, min(1000, math.ceil(abs(dth) / step)))
    out = []
    for i in range(1, n + 1):
        t = th1 + dth * i / n
        x, y = rx * math.cos(t), ry * math.sin(t)
        out.append((cp * x - sp * y + cx, sp * x + cp * y + cy))
    out[-1] = p2
    return out


def parse_path_d(d: str, tol: float, *, flatteners=None) -> tuple[list, bool]:
    """Path data -> ([(points, closed)], has_curves). Points are in the element's own coordinates.
    Optional (cubic, quadratic, arc) callbacks let consumers choose bounded adaptive flattening;
    None retains the standalone cleaner's original sampling and behaviour.
    """
    subs: list = []
    pts: list = []
    curved = False
    x = y = sx = sy = 0.0
    last_ctrl = None   # (kind, point) for S / T reflection
    cmd = None
    i, n = 0, len(d)

    def finish(closed=False):
        nonlocal pts
        if pts:
            subs.append((pts, closed))
        pts = []

    while i < n:
        ch = d[i]
        if ch.isspace() or ch == ",":
            i += 1
            continue
        if ch.upper() in _CMD_ARGS:
            cmd = ch
            i += 1
            if cmd in "Zz":
                if pts:
                    if dist(pts[-1], pts[0]) > EPS:
                        pts.append(pts[0])
                    x, y = pts[0]
                    finish(closed=True)
                    pts = []
                last_ctrl = None
                continue
        elif cmd is None or cmd in "Zz":
            raise ValueError(f"bad path data near {d[i:i + 20]!r}")
        up, rel = cmd.upper(), cmd.islower()
        args = []
        for k in range(_CMD_ARGS[up]):
            rx_ = _FLAGTOK if up == "A" and k in (3, 4) else _NUMTOK
            m = rx_.match(d, i)
            if not m:
                if k == 0:
                    raise ValueError(f"bad path data near {d[i:i + 20]!r}")
                raise ValueError(f"missing arguments for {cmd!r} near {d[i:i + 20]!r}")
            args.append(float(m.group(1)))
            i = m.end()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if up == "M":
            finish()
            x, y = args[0] + ox, args[1] + oy
            sx, sy = x, y
            pts = [(x, y)]
            cmd = "l" if rel else "L"   # extra pairs after M are line-tos
            last_ctrl = None
            continue
        if not pts:            # drawing command right after Z: starts at the subpath start
            pts = [(x, y)]
        if up == "L":
            x, y = args[0] + ox, args[1] + oy
            pts.append((x, y))
            last_ctrl = None
        elif up == "H":
            x = args[0] + ox
            pts.append((x, y))
            last_ctrl = None
        elif up == "V":
            y = args[0] + oy
            pts.append((x, y))
            last_ctrl = None
        elif up in "CS":
            if up == "C":
                c1 = (args[0] + ox, args[1] + oy)
                c2, e = (args[2] + ox, args[3] + oy), (args[4] + ox, args[5] + oy)
            else:
                c1 = (2 * x - last_ctrl[1][0], 2 * y - last_ctrl[1][1]) if last_ctrl and last_ctrl[0] == "C" else (x, y)
                c2, e = (args[0] + ox, args[1] + oy), (args[2] + ox, args[3] + oy)
            pts += (_cubic if flatteners is None else flatteners[0])((x, y), c1, c2, e, tol)
            x, y = e
            last_ctrl = ("C", c2)
            curved = True
        elif up in "QT":
            if up == "Q":
                c, e = (args[0] + ox, args[1] + oy), (args[2] + ox, args[3] + oy)
            else:
                c = (2 * x - last_ctrl[1][0], 2 * y - last_ctrl[1][1]) if last_ctrl and last_ctrl[0] == "Q" else (x, y)
                e = (args[0] + ox, args[1] + oy)
            pts += (_quad if flatteners is None else flatteners[1])((x, y), c, e, tol)
            x, y = e
            last_ctrl = ("Q", c)
            curved = True
        elif up == "A":
            e = (args[5] + ox, args[6] + oy)
            pts += (_arc if flatteners is None else flatteners[2])(
                (x, y), args[0], args[1], args[2], args[3] != 0, args[4] != 0, e, tol)
            x, y = e
            last_ctrl = None
            curved = True
    finish()
    return subs, curved


def _num(el: ET.Element, k: str, default: float = 0.0) -> float:
    m = _NUM.match((el.get(k) or "").strip())
    return float(m.group(0)) if m else default


def shape_subpaths(el: ET.Element, tag: str, tol: float, *, flatteners=None) -> tuple[list, bool]:
    if tag == "path":
        return parse_path_d(el.get("d") or "", tol, flatteners=flatteners)
    if tag == "rect":
        x, y, w, h = (_num(el, k) for k in ("x", "y", "width", "height"))
        if w <= 0 or h <= 0:
            return [], False
        rx, ry = _num(el, "rx", -1), _num(el, "ry", -1)
        rx, ry = (ry if rx < 0 else rx), (rx if ry < 0 else ry)
        rx, ry = min(max(rx, 0.0), w / 2), min(max(ry, 0.0), h / 2)
        if rx <= 0 or ry <= 0:
            return [([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)], True)], False
        return parse_path_d(f"M {x + rx} {y} H {x + w - rx} A {rx} {ry} 0 0 1 {x + w} {y + ry} V {y + h - ry} "
                            f"A {rx} {ry} 0 0 1 {x + w - rx} {y + h} H {x + rx} A {rx} {ry} 0 0 1 {x} {y + h - ry} "
                            f"V {y + ry} A {rx} {ry} 0 0 1 {x + rx} {y} Z", tol, flatteners=flatteners)
    if tag in ("circle", "ellipse"):
        cx, cy = _num(el, "cx"), _num(el, "cy")
        rx = _num(el, "r") if tag == "circle" else _num(el, "rx")
        ry = _num(el, "r") if tag == "circle" else _num(el, "ry")
        if rx <= 0 or ry <= 0:
            return [], False
        return parse_path_d(f"M {cx + rx} {cy} A {rx} {ry} 0 1 1 {cx - rx} {cy} A {rx} {ry} 0 1 1 {cx + rx} {cy} Z",
                            tol, flatteners=flatteners)
    if tag == "line":
        f = [float(el.get(k) or 0) for k in ("x1", "y1", "x2", "y2")]
        return [([(f[0], f[1]), (f[2], f[3])], False)], False
    v = [float(t) for t in _NUM.findall(el.get("points") or "")]
    pts = list(zip(v[0::2], v[1::2]))
    if tag == "polygon" and len(pts) > 1:
        return [(pts + [pts[0]], True)], False
    return ([(pts, False)] if pts else []), False


def fmt(v: float, prec: int) -> str:
    s = f"{v:.{prec}f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def path_data(polys: list, prec: int) -> str:
    out = []
    for pts, closed in polys:
        if closed and len(pts) > 2 and dist(pts[0], pts[-1]) < EPS:
            pts = pts[:-1]
        coords = [f"{fmt(x, prec)},{fmt(y, prec)}" for x, y in pts]
        out.append(f"M {coords[0]}" + (f" L {' '.join(coords[1:])}" if len(coords) > 1 else "") + (" Z" if closed else ""))
    return " ".join(out)


# ----------------------------------------------------------------------------- overlap index

class SegmentIndex:
    """Uniform grid of segments (document units). Each segment sits in the 3x3 cells around points every half
    cell along it, so any segment passing within cell/4 of it is a candidate."""

    def __init__(self, cell: float):
        self.cell = cell
        self.grid: dict = defaultdict(list)
        self.segs: list = []   # (ax, ay, ux, uy, length)

    def _cells(self, a: tuple, b: tuple) -> set:
        L = dist(a, b)
        n = max(1, math.ceil(L / (self.cell / 2)))
        keys = set()
        for i in range(n + 1):
            t = i / n
            cx = math.floor((a[0] + t * (b[0] - a[0])) / self.cell)
            cy = math.floor((a[1] + t * (b[1] - a[1])) / self.cell)
            for gx in (cx - 1, cx, cx + 1):
                for gy in (cy - 1, cy, cy + 1):
                    keys.add((gx, gy))
        return keys

    def add(self, a: tuple, b: tuple) -> None:
        L = dist(a, b)
        if L < EPS:
            return
        idx = len(self.segs)
        self.segs.append((a[0], a[1], (b[0] - a[0]) / L, (b[1] - a[1]) / L, L))
        for key in self._cells(a, b):
            self.grid[key].append(idx)

    def near(self, a: tuple, b: tuple) -> set:
        found = set()
        for key in self._cells(a, b):
            found.update(self.grid.get(key, ()))
        return found


def covered_part(a: tuple, b: tuple, L: float, k: tuple, tol: float, sin_max: float) -> tuple | None:
    """t-range of segment ab lying within tol of the kept segment k, measured inside k's own extent (no round end
    caps: a stroke that merely continues or leaves k is not 'on' it). None unless the two are nearly parallel."""
    px, py, ux, uy, Lk = k
    dx, dy = b[0] - a[0], b[1] - a[1]
    if abs(dx * uy - dy * ux) > sin_max * L:
        return None
    rx, ry = a[0] - px, a[1] - py
    lo, hi = 0.0, 1.0
    for alpha, beta, v0, v1 in ((rx * ux + ry * uy, dx * ux + dy * uy, 0.0, Lk),
                                (ry * ux - rx * uy, dy * ux - dx * uy, -tol, tol)):
        if abs(beta) < 1e-12:
            if alpha < v0 - EPS or alpha > v1 + EPS:
                return None
            continue
        t0, t1 = (v0 - alpha) / beta, (v1 - alpha) / beta
        if t0 > t1:
            t0, t1 = t1, t0
        lo, hi = max(lo, t0), min(hi, t1)
        if hi - lo <= EPS:
            return None
    return lo, hi


def merge_intervals(ivs: list, gap: float) -> list:
    out: list = []
    for s0, s1 in sorted(ivs):
        if out and s0 <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], s1)
        else:
            out.append([s0, s1])
    return out


def point_at(pts: list, cum: list, s: float) -> tuple:
    j = bisect_left(cum, s)
    if j < len(cum) and abs(cum[j] - s) < EPS:
        return pts[j]
    i = max(0, min(len(pts) - 2, j - 1))
    L = cum[i + 1] - cum[i]
    t = 0.0 if L <= 0 else (s - cum[i]) / L
    return (pts[i][0] + t * (pts[i + 1][0] - pts[i][0]), pts[i][1] + t * (pts[i + 1][1] - pts[i][1]))


def slice_polyline(pts: list, cum: list, s0: float, s1: float) -> list:
    out = [point_at(pts, cum, s0)]
    out += pts[bisect_right(cum, s0 + EPS):bisect_left(cum, s1 - EPS)]
    out.append(point_at(pts, cum, s1))
    return out


def clean_stroke(pts: list, closed: bool, index: SegmentIndex, tol: float, sin_max: float, min_overlap: float,
                 min_piece: float, cuts: list | None = None) -> tuple[list | None, float]:
    """Cut the parts of one subpath that retrace ink in `index` (or itself). Returns (pieces or None when
    unchanged, retraced length in document units). The caller adds the kept pieces to the index. With `cuts`,
    the removed stretches are appended to it."""
    cum = [0.0]
    for p, q in zip(pts, pts[1:]):
        cum.append(cum[-1] + dist(p, q))
    total = cum[-1]
    if total < EPS:
        return None, 0.0
    own = SegmentIndex(index.cell)   # this stroke so far: catches a stroke doubling back on itself
    covered = []
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        L = cum[i + 1] - cum[i]
        if L < EPS:
            continue
        for ix in (index, own):
            for k in ix.near(a, b):
                part = covered_part(a, b, L, ix.segs[k], tol, sin_max)
                if part:
                    covered.append((cum[i] + part[0] * L, cum[i] + part[1] * L))
        own.add(a, b)
    if not covered:
        return None, 0.0
    runs = merge_intervals(covered, min_piece)
    end_min = 2 * tol

    def leftovers(cuts):
        """What is left between and around the cuts. A bit between two cuts goes when shorter than min_piece (both
        its ends touch ink); a free end only when shorter than 2*tol, so lines are never visibly shortened."""
        keep, s = [], 0.0
        for c0, c1 in cuts + [[total, total]]:
            if c0 - s >= (end_min if s == 0.0 or c0 == total else min_piece):
                keep.append((s, c0))
            s = max(s, c1)
        return keep

    if not leftovers(runs):   # retraced from end to end, however short
        if cuts is not None:
            cuts.append(list(pts))
        return [], total
    wrap = (closed and len(runs) > 1 and runs[0][0] <= min_piece and runs[-1][1] >= total - min_piece
            and (runs[0][1] - runs[0][0]) + (runs[-1][1] - runs[-1][0]) >= min_overlap)
    cut = [r for j, r in enumerate(runs) if r[1] - r[0] >= min_overlap or (wrap and j in (0, len(runs) - 1))]
    if not cut:
        return None, 0.0
    keep = leftovers(cut)
    pieces = [slice_polyline(pts, cum, s0, s1) for s0, s1 in keep]
    if cuts is not None:
        s = 0.0
        for s0, s1 in keep + [(total, total)]:
            if s0 - s > EPS:
                cuts.append(slice_polyline(pts, cum, s, s0))
            s = s1
    if closed and len(pieces) > 1 and keep[0][0] == 0.0 and keep[-1][1] == total:
        pieces = [pieces[-1] + pieces[0][1:]] + pieces[1:-1]   # the loop's start is not a real end
    retraced = total - sum(s1 - s0 for s0, s1 in keep)
    return pieces, retraced


# ----------------------------------------------------------------------------- document

def local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def in_svg_ns(tag) -> bool:
    return isinstance(tag, str) and (tag.startswith("{" + SVG_NS + "}") or not tag.startswith("{"))


def style_of(el: ET.Element) -> dict:
    st = {}
    for item in (el.get("style") or "").split(";"):
        if ":" in item:
            k, v = item.split(":", 1)
            st[k.strip()] = v.strip()
    for k in ("stroke", "display", "stroke-width"):
        if el.get(k) is not None and k not in st:
            st[k] = el.get(k).strip()
    return st


def mm_per_unit(root: ET.Element) -> float:
    """Millimetres per root user unit, from width + viewBox (px at 96 dpi when unitless)."""
    m = re.fullmatch(r"\s*([-+]?[\d.eE+-]+)\s*([a-zA-Z%]*)\s*", root.get("width") or "")
    vb = [float(v) for v in _NUM.findall(root.get("viewBox") or "")]
    if m and m.group(2) != "%":
        w_mm = float(m.group(1)) * UNIT_MM.get(m.group(2).lower(), UNIT_MM["px"])
        return w_mm / vb[2] if len(vb) == 4 and vb[2] > 0 else UNIT_MM.get(m.group(2).lower(), UNIT_MM["px"])
    return UNIT_MM["px"]


@dataclass
class Item:
    el: ET.Element
    parent: ET.Element
    tag: str
    ctm: Matrix
    pen: tuple


def collect(root: ET.Element, shapes: set = SHAPES, by_width: bool = False) -> tuple[list, list]:
    """Drawable shapes in document (= plotting) order with their transform and pen, plus <plotdata> elements.
    The pen is (top-level group, stroke colour), plus the stroke width with `by_width`."""
    items, plotdata = [], []

    def walk(el, ctm, stroke, layer, width=None):
        for pos, child in enumerate(list(el)):
            if not in_svg_ns(child.tag):
                continue
            tag = local(child.tag)
            if tag == "plotdata":
                plotdata.append((el, child))
                continue
            if tag in SKIP:
                continue
            st = style_of(child)
            if st.get("display") == "none":
                continue
            m = mat_mul(ctm, parse_transform(child.get("transform")))
            s = st.get("stroke", stroke)
            w = st.get("stroke-width", width)
            if tag in shapes:
                items.append(Item(child, el, tag, m, (layer, s, w) if by_width else (layer, s)))
            elif tag in ("g", "a", "switch", "svg"):
                walk(child, m, s, pos if el is root else layer, w)   # a top-level group is a layer

    walk(root, IDENTITY, None, -1)
    return items, plotdata


@dataclass
class _Rec:
    """One element's result before it is written back."""
    it: Item
    inv: Matrix
    subs: list
    raw: list           # [(points, closed)] in root units
    out: list           # [(points, closed)] after cutting (raw when unchanged)
    changed: bool
    cut_here: float


def clean_tree(tree: ET.ElementTree, opts: Options, progress=None, log=None) -> Stats:
    """Clean `tree` in place. Optional hooks: progress(done, total) per element (it may raise to cancel) and
    log(message) for skipped elements (default: stderr)."""
    root = tree.getroot()
    stats = Stats()
    mpu = mm_per_unit(root)
    tol, min_overlap, min_piece = opts.tol / mpu, opts.min_overlap / mpu, opts.min_piece / mpu
    simp = opts.simplify / mpu
    sin_max = math.sin(math.radians(opts.angle))
    cell = max(4 * tol, 1.0 / mpu)
    indexes: dict = {}
    items, plotdata = collect(root, SHAPES | BASIC_SHAPES if opts.shapes else SHAPES, opts.pen_by_width)
    stats.elements = len(items)
    cuts = stats.cuts if opts.collect_cuts else None
    whole = opts.mode == "duplicates"
    recs: list = []
    for n_done, it in enumerate(items):
        if progress is not None:
            progress(n_done, len(items))
        inv = mat_inv(it.ctm)
        if inv is None:
            continue
        scale = math.sqrt(abs(it.ctm[0] * it.ctm[3] - it.ctm[1] * it.ctm[2])) or 1.0
        try:
            subs, curved = shape_subpaths(it.el, it.tag, opts.flatten / mpu / scale)
        except ValueError as e:
            msg = f"skipped {it.tag} {it.el.get('id') or ''}: {e}"
            if log is not None:
                log(msg)
            else:
                print(f"  ! {msg}", file=sys.stderr)
            continue
        index = indexes.setdefault(None if opts.across_pens else it.pen, SegmentIndex(cell))
        raw, out, cut, slimmed, cut_here = [], [], False, False, 0.0
        for pts_local, closed in subs:
            pts = [apply(it.ctm, p) for p in pts_local]
            raw.append((pts, closed))
            stats.strokes_in += 1
            stats.vertices_in += len(pts)
            stats.ink_in += sum(dist(p, q) for p, q in zip(pts, pts[1:])) * mpu
        if whole:   # an element goes only when every stroke of it was drawn before; nothing is split
            gone = bool(raw) and all(clean_stroke(pts, closed, index, tol, sin_max, min_overlap, min_piece)[0] == []
                                     for pts, closed in raw)
            if gone:
                cut_here = sum(sum(dist(p, q) for p, q in zip(pts, pts[1:])) for pts, _ in raw)
                if cuts is not None:
                    cuts.extend(list(pts) for pts, _ in raw)
            else:
                for pts, _ in raw:
                    for p, q in zip(pts, pts[1:]):
                        index.add(p, q)
            stats.retraced += cut_here * mpu
            recs.append(_Rec(it, inv, subs, raw, [] if gone else raw, gone, cut_here))
            continue
        for pts, closed in raw:
            pieces, retraced = clean_stroke(pts, closed, index, tol, sin_max, min_overlap, min_piece, cuts)
            if pieces is None:
                pieces = [pts]
            else:
                cut, closed = True, False
                cut_here += retraced
            for pc in pieces:
                for p, q in zip(pc, pc[1:]):
                    index.add(p, q)
                slim = simplify(pc, simp)
                slimmed |= len(slim) != len(pc)
                out.append((slim, closed))
        changed = cut or (slimmed and not curved)   # curves stay curves unless the path had to be cut
        if not changed:
            out = raw
        stats.retraced += cut_here * mpu
        recs.append(_Rec(it, inv, subs, raw, out, changed, cut_here))
    if opts.join > 0:
        stats.joined = join_records(recs, opts.join / mpu, opts.join_reverse, opts.join_scope)
    for r in recs:
        it, out = r.it, r.out
        for pts, _ in out:
            stats.strokes_out += 1
            stats.vertices_out += len(pts)
            stats.ink_out += sum(dist(p, q) for p, q in zip(pts, pts[1:])) * mpu
        if not r.changed:
            continue
        stats.changed += 1
        if opts.verbose:
            stats.rows.append(Row(it.el.get("id") or it.tag, len(r.subs), len(out), sum(len(s) for s, _ in r.subs),
                                  sum(len(p) for p, _ in out), r.cut_here * mpu))
        if not out:
            it.parent.remove(it.el)
            stats.removed_elements += 1
            continue
        d = path_data([([apply(r.inv, p) for p in pts], closed) for pts, closed in out], opts.precision)
        if it.tag != "path":
            for k in GEOMETRY_ATTRS:
                it.el.attrib.pop(k, None)
            it.el.tag = f"{{{SVG_NS}}}path"
        it.el.set("d", d)
    for parent, el in plotdata:
        parent.remove(el)
        stats.plotdata_removed = True
    sign(root)
    return stats


# ----------------------------------------------------------------------------- joining

def chain_strokes(strokes: list, tol: float, reverse: bool = True) -> list:
    """Greedy, in plotting order: extend each stroke at its end, then at its start, with the nearest unused stroke
    whose start (or, reversed, its end) lies within `tol`. Returns [(members, points)], members being
    [(stroke index, reversed)] in drawing order. A gap below tol becomes a sub-tolerance step, never a long bridge."""
    n = len(strokes)
    used = [False] * n
    cell = max(2 * tol, EPS)
    grid: dict = defaultdict(list)

    def key(p):
        return math.floor(p[0] / cell), math.floor(p[1] / cell)

    for i, pts in enumerate(strokes):
        grid[key(pts[0])].append((i, 0))
        grid[key(pts[-1])].append((i, 1))

    def nearest(p, want):
        """Closest free end within tol; ties go to the end that needs no reversal, then to the earlier stroke."""
        best, rank = None, None
        kx, ky = key(p)
        for gx in (kx - 1, kx, kx + 1):
            for gy in (ky - 1, ky, ky + 1):
                for j, e in grid.get((gx, gy), ()):
                    if used[j] or (e != want and not reverse):
                        continue
                    d = dist(p, strokes[j][0 if e == 0 else -1])
                    if d > tol:
                        continue
                    r = (round(d, 9), e != want, j)
                    if rank is None or r < rank:
                        best, rank = (j, e), r
        return best

    chains = []
    for i in range(n):
        if used[i]:
            continue
        used[i] = True
        members, pts = [(i, False)], list(strokes[i])
        while True:   # forward: the next stroke's start meets our end
            m = nearest(pts[-1], 0)
            if m is None:
                break
            j, e = m
            used[j] = True
            seg = strokes[j] if e == 0 else strokes[j][::-1]
            pts += seg[1:] if dist(pts[-1], seg[0]) < EPS else seg
            members.append((j, e == 1))
        while True:   # backward: a stroke's end meets our start
            m = nearest(pts[0], 1)
            if m is None:
                break
            j, e = m
            used[j] = True
            seg = strokes[j] if e == 1 else strokes[j][::-1]
            pts = (seg[:-1] if dist(seg[-1], pts[0]) < EPS else seg) + pts
            members.insert(0, (j, e == 0))
        chains.append((members, pts))
    return chains


def join_records(recs: list, tol: float, reverse: bool, scope: str) -> int:
    """Join open strokes end to end within each element, or across the elements of one pen and parent group. A
    chain is written where its first stroke (in plotting order) was; absorbed elements lose those strokes."""
    groups: dict = defaultdict(list)
    for ri, r in enumerate(recs):
        if r.out:
            groups[ri if scope == "element" else (r.it.pen, id(r.it.parent))].append(ri)
    joins = 0
    for ris in groups.values():
        ids, strokes = [], []
        for ri in ris:
            for si, (pts, closed) in enumerate(recs[ri].out):
                if not closed and len(pts) > 1:
                    ids.append((ri, si))
                    strokes.append(pts)
        if len(strokes) < 2:
            continue
        place: dict = {}    # (ri, si) -> chain points to write there, or None to drop
        for members, pts in chain_strokes(strokes, tol, reverse):
            if len(members) < 2:
                continue
            joins += len(members) - 1
            owner = min(ids[j] for j, _ in members)
            for j, _ in members:
                place[ids[j]] = pts if ids[j] == owner else None
        for ri in {k[0] for k in place}:
            r = recs[ri]
            new = []
            for si, item in enumerate(r.out):
                if (ri, si) not in place:
                    new.append(item)
                elif place[(ri, si)] is not None:
                    new.append((place[(ri, si)], False))
            r.out, r.changed = new, True
    return joins


def sign(root: ET.Element) -> None:
    """<desc>svg-overlap-cleaner</desc> as the root's first child: Atlas reads it from the file head."""
    for child in root:
        if local(child.tag) == "desc" and (child.text or "").strip() == TOOL:
            return
    desc = ET.Element(f"{{{SVG_NS}}}desc")
    desc.text = TOOL
    desc.tail = "\n  "
    root.insert(0, desc)


def load(src) -> ET.ElementTree:
    """Parse keeping comments and every namespace prefix (sodipodi, inkscape ...) as written."""
    for _, (prefix, uri) in ET.iterparse(src, events=("start-ns",)):
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:   # reserved prefixes such as ns0
            pass
    ET.register_namespace("", SVG_NS)
    if hasattr(src, "seek"):
        src.seek(0)
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    return ET.parse(src, parser)


def clean_string(text: str, opts: Options | None = None) -> tuple[str, Stats]:
    tree = load(io.BytesIO(text.encode("utf-8")))
    stats = clean_tree(tree, opts or Options())
    buf = io.BytesIO()
    tree.write(buf, encoding="UTF-8", xml_declaration=True)
    return buf.getvalue().decode("utf-8"), stats


def default_output(src: Path) -> Path:
    return src.with_name(f"{src.stem}--nooverlap.svg")


def clean_file(src: Path, dst: Path | None, opts: Options, dry_run: bool = False) -> Stats:
    tree = load(src)
    stats = clean_tree(tree, opts)
    if not dry_run:
        tree.write(dst or default_output(src), encoding="UTF-8", xml_declaration=True)
    return stats


# ----------------------------------------------------------------------------- CLI

def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Remove retraced (overlapping) ink and collinear vertices from "
                                             "pen-plotter SVGs, splitting paths where needed.")
    ap.add_argument("inputs", nargs="*", type=Path, help="SVG files (none: pick them in a file dialog)")
    ap.add_argument("-o", "--output", type=Path, help="output file (one input only); default: next to the input "
                                                      "with --nooverlap added to the name")
    d = Options()
    ap.add_argument("--tol", type=float, default=d.tol, help=f"mm; strokes closer than this are the same ink "
                                                             f"(default {d.tol})")
    ap.add_argument("--angle", type=float, default=d.angle, help=f"degrees; max angle between overlapping strokes, "
                                                                 f"so crossings are kept (default {d.angle})")
    ap.add_argument("--min-overlap", type=float, default=d.min_overlap,
                    help=f"mm; shorter retraced stretches are kept (default {d.min_overlap})")
    ap.add_argument("--min-piece", type=float, default=d.min_piece,
                    help=f"mm; leftover bits shorter than this are dropped (default {d.min_piece})")
    ap.add_argument("--simplify", type=float, default=d.simplify,
                    help=f"mm; collinear-vertex tolerance, 0 drops only duplicates (default {d.simplify})")
    ap.add_argument("--flatten", type=float, default=d.flatten, help=f"mm; curve flattening tolerance "
                                                                     f"(default {d.flatten})")
    ap.add_argument("--across-pens", action="store_true", help="also remove overlaps between different layers "
                                                                "and stroke colours")
    ap.add_argument("--precision", type=int, default=d.precision, help=f"decimals in path data "
                                                                       f"(default {d.precision})")
    ap.add_argument("--join", type=float, default=d.join, metavar="MM",
                    help="join stroke ends closer than this, within one pen and group (default off)")
    ap.add_argument("--no-join-reverse", dest="join_reverse", action="store_false",
                    help="never reverse a stroke to join it")
    ap.add_argument("--join-scope", choices=["pen", "element"], default=d.join_scope,
                    help="join across the elements of one pen (default) or only within each element")
    ap.add_argument("--whole-duplicates", action="store_true",
                    help="only remove elements whose ink is entirely drawn before; split nothing")
    ap.add_argument("--shapes", action="store_true", help="also read rect, circle and ellipse")
    ap.add_argument("--pen-by-width", action="store_true", help="the stroke width is part of the pen")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("-v", "--verbose", action="store_true", help="one line per changed element")
    a = ap.parse_args(argv)
    inputs = a.inputs or pick_files()
    if not inputs:
        ap.print_help()
        return 1
    if a.output and len(inputs) > 1:
        ap.error("-o works with a single input")
    opts = Options(tol=a.tol, angle=a.angle, min_overlap=a.min_overlap, min_piece=a.min_piece,
                   simplify=a.simplify, flatten=a.flatten, across_pens=a.across_pens, precision=a.precision,
                   verbose=a.verbose, mode="duplicates" if a.whole_duplicates else "retraced", join=a.join,
                   join_reverse=a.join_reverse, join_scope=a.join_scope, shapes=a.shapes, pen_by_width=a.pen_by_width)
    for src in inputs:
        dst = a.output or default_output(src)
        stats = clean_file(src, dst, opts, a.dry_run)
        print(stats.report(src.name))
        for row in stats.rows:
            print(row)
        print("  (dry run, nothing written)" if a.dry_run else f"  -> {dst}")
    return 0


def pick_files() -> list:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return []
    tk.Tk().withdraw()
    return [Path(p) for p in filedialog.askopenfilenames(title="SVG files to clean",
                                                         filetypes=[("SVG", "*.svg"), ("All files", "*.*")])]


if __name__ == "__main__":
    sys.exit(main())
