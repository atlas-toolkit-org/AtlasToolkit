"""Native DP / effective-area reduction with a shared, conservative line-art safety pass.

Candidates are only original vertices. Every shortcut is checked against its ORIGINAL span,
not merely the last surviving neighbours. Swept regions must be empty of other ink; checking
only the new chord would allow swallowing an enclosed motif. Original constraints remain in
the static STRtree; accepted chords also enter a dynamic R-tree. Thus subsequent strokes
cannot cross shortcuts accepted earlier. Crossing/overlap spans are retained rather than noded.
"""
from __future__ import annotations

import bisect
import heapq
import math
import re
from dataclasses import dataclass, field

import numpy as np
import shapely
from rtree.index import Index
from shapely.geometry import LineString, MultiPoint, Polygon

from atlas_toolkit.geom.svg import (BASIC_SHAPES, SHAPES, SVG_NS, apply, collect, length_mm, local,
                                    mat_mul, mm_per_unit, parse_transform, shape_subpaths)
from .curves import FLATTENERS, MAX_POINTS


@dataclass
class Options:
    method: str = "rdp"
    tolerance: float = 0.05
    area_factor: float = 1.
    curves: str = "keep"
    topology: bool = True
    corner_angle: float = 35.
    reversals: bool = True
    length_loss: float = 2.
    area_loss: float = 3.
    flatten_share: float = 20.


@dataclass
class Stroke:
    local: np.ndarray
    points: np.ndarray
    closed: bool
    kept: list = field(default_factory=list)
    offset: int = 0


@dataclass
class Stats:
    vertices_in: int = 0
    vertices_out: int = 0
    strokes: int = 0
    changed: int = 0
    blocked: int = 0
    deviation: float = 0.
    length_in: float = 0.
    length_out: float = 0.
    notes: list = field(default_factory=list)


def physical_matrix(root):
    """Full root viewport metric, including non-uniform preserveAspectRatio=none."""
    vb = [float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", root.get("viewBox", ""))]
    mpu = mm_per_unit(root)
    if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
        sx = (length_mm(root.get("width")) or vb[2] * mpu) / vb[2]
        sy = (length_mm(root.get("height")) or vb[3] * mpu) / vb[3]
        aspect = root.get("preserveAspectRatio", "xMidYMid meet").split()
        if "none" not in aspect:
            sx = sy = max(sx, sy) if "slice" in aspect else min(sx, sy)
        # Translation is immaterial to distances and intersections.
        metric = (sx, 0., 0., sy, 0., 0.)
    else:
        metric = (mpu, 0., 0., mpu, 0., 0.)
    # SVG2 root transforms conceptually wrap the viewport, outside its viewBox mapping.
    return mat_mul(parse_transform(root.get("transform")), metric)


def distances(points, a, b):
    delta = b - a
    den = float(delta @ delta)
    t = ((points - a) @ delta) / den if den else np.zeros(len(points))
    return np.linalg.norm(points - a - np.clip(t, 0., 1.)[:, None] * delta, axis=1), t


def signed_area(p):
    # Translate to avoid cancellation for drawings far from the origin.
    p = p - p[0]
    return float(np.sum(p[:-1, 0] * p[1:, 1] - p[1:, 0] * p[:-1, 1])) / 2


def anchors(p, angle):
    locked = np.zeros(len(p), dtype=bool)
    locked[0] = locked[-1] = True
    if len(p) > 2 and angle < 180:
        a, b = np.diff(p, axis=0)[:-1], np.diff(p, axis=0)[1:]
        den = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)
        cosine = np.divide(np.sum(a * b, axis=1), den, out=np.ones(len(a)), where=den > 0)
        locked[1:-1] = cosine <= math.cos(math.radians(angle))
    if np.array_equal(p[0], p[-1]) and len(p) > 3:
        # Deterministic separated anchors prevent rings collapsing or acquiring a seam shortcut.
        far = int(np.argmax(np.linalg.norm(p - p[0], axis=1)))
        locked[far] = True
        d, _ = distances(p, p[0], p[far])
        locked[int(np.argmax(d))] = True
    return locked


def dp_candidates(p, locked, tolerance):
    """GEOS native DP, split at protected vertices; map retained coordinates in stroke order."""
    positions = {}
    for i, point in enumerate(p):
        positions.setdefault(tuple(point), []).append(i)
    out = [0]
    ids = np.flatnonzero(locked)
    for start, end in zip(ids[:-1], ids[1:]):
        if end - start == 1:
            out.append(int(end))
            continue
        result = shapely.simplify(LineString(p[start:end + 1]), tolerance, preserve_topology=False)
        prev = int(start)
        for coord in list(result.coords)[1:-1]:
            options = positions[coord]
            prev = options[bisect.bisect_right(options, prev)]
            out.append(prev)
        out.append(int(end))
    return out


def vw_candidates(p, locked, threshold, check):
    """O(n log n) min-heap / linked neighbours, monotone effective triangle areas."""
    n = len(p)
    prev, nxt = list(range(-1, n - 1)), list(range(1, n + 1))
    alive, versions, heap = [True] * n, [0] * n, []
    coords = p.tolist()  # scalar heap updates avoid allocating millions of tiny NumPy arrays

    def push(i, floor=0.):
        if i <= 0 or i >= n - 1 or locked[i] or not alive[i]:
            return
        a, b, c = coords[prev[i]], coords[i], coords[nxt[i]]
        cost = max(floor, abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2)
        versions[i] += 1
        heapq.heappush(heap, (cost, i, versions[i]))

    for i in range(1, n - 1):
        push(i)
    count = 0
    while heap:
        cost, i, version = heapq.heappop(heap)
        if cost > threshold:
            break
        if not alive[i] or version != versions[i]:
            continue
        alive[i] = False
        a, b = prev[i], nxt[i]
        nxt[a], prev[b] = b, a
        push(a, cost)
        push(b, cost)
        count += 1
        if count % 1024 == 0:
            check()
    return [i for i, keep in enumerate(alive) if keep]


class TopologyGuard:
    def __init__(self, strokes, check):
        segments = []
        for s in strokes:
            check()
            s.offset = len(segments)
            segments.extend(zip(s.points[:-1], s.points[1:]))
        if segments:
            coordinates = np.asarray(segments)
            self.original = shapely.linestrings(coordinates)
            # SVG zero-length stroked segments can be visible round-cap stipple dots.
            # GEOS intersects() sees a degenerate line inside a polygon, but intersection()
            # returns EMPTY; represent these constraints as points without changing IDs.
            dots = np.all(coordinates[:, 0] == coordinates[:, 1], axis=1)
            self.original[dots] = shapely.points(coordinates[dots, 0])
        else:
            self.original = np.array([], dtype=object)
        self.tree = shapely.STRtree(self.original)
        self.new = Index()
        self.chords = []

    def safe(self, stroke, i, j):
        points = stroke.points[i:j + 1]
        region = Polygon(points)
        if region.area > 0:
            if not region.is_valid:
                return False
        else:
            if not LineString(points).is_simple:
                return False
            region = LineString(points)
        ends = MultiPoint([points[0], points[-1]])
        ids = self.tree.query(region)
        ids = ids[(ids < stroke.offset + i) | (ids >= stroke.offset + j)]
        if len(ids):
            hits = shapely.intersection(self.original[ids], region)
            # Only exact shortcut endpoints may touch other strokes. No epsilon snapping.
            if np.any(~shapely.is_empty(hits) & ~shapely.covered_by(hits, ends)):
                return False
        for k in self.new.intersection(region.bounds):
            hit = region.intersection(self.chords[k])
            if not hit.is_empty and not ends.covers(hit):
                return False
        return True

    def add(self, a, b):
        chord = LineString([a, b])
        self.new.insert(len(self.chords), chord.bounds)
        self.chords.append(chord)

    def rollback(self, checkpoint):
        while len(self.chords) > checkpoint:
            chord = self.chords.pop()
            self.new.delete(len(self.chords), chord.bounds)


def reduce_stroke(s, o, tolerance, guard, stats, check):
    p = s.points
    if len(p) < 3:
        return list(range(len(p)))
    locked = anchors(p, o.corner_angle)
    candidates = (dp_candidates(p, locked, tolerance) if o.method == "rdp" else
                  vw_candidates(p, locked, o.area_factor * tolerance ** 2, check))
    lengths = np.r_[0., np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]
    checkpoint = len(guard.chords) if guard else 0
    out, stack, count = [0], list(reversed(list(zip(candidates[:-1], candidates[1:])))), 0
    while stack:
        i, j = stack.pop()
        count += 1
        if count % 128 == 0:
            check()
        if j - i < 2:
            out.append(j)
            continue
        d, t = distances(p[i:j + 1], p[i], p[j])
        length = lengths[j] - lengths[i]
        chord = float(np.linalg.norm(p[j] - p[i]))
        safe = (float(d.max()) <= tolerance and chord > 0 and
                chord + 1e-12 >= length * (1 - o.length_loss / 100) and
                (not o.reversals or np.all(np.diff(t) >= -1e-12)))
        if safe and guard:
            safe = guard.safe(s, i, j)
        if safe:
            out.append(j)
            if guard:
                guard.add(p[i], p[j])
        else:
            stats.blocked += 1
            # Distance extremum, or balanced subdivision for length/topology constraints.
            k = i + int(np.argmax(d))
            if k <= i or k >= j or float(d.max()) <= tolerance:
                k = (i + j) // 2
            stack.extend([(k, j), (i, k)])
    q = p[out]
    if s.closed or np.array_equal(p[0], p[-1]):
        a, b = signed_area(p), signed_area(q)
        if (len(set(map(tuple, q))) < 3 or a * b <= 0 or
                abs(a - b) > abs(a) * o.area_loss / 100 + 1e-12):
            if guard:
                guard.rollback(checkpoint)
            return list(range(len(p)))
    return out


def _num(x):
    # Round-trip coordinates. Decimal rounding could break endpoints and create contacts.
    return repr(float(x))


def _coords(p):
    return " ".join(f"{_num(x)},{_num(y)}" for x, y in p)


def simplify_tree(tree, o: Options, progress=lambda f, m="": None):
    stats = Stats()
    if o.tolerance < 0 or not math.isfinite(o.tolerance):
        raise ValueError("Deviation must be a finite non-negative number.")
    if o.method not in ("rdp", "vw") or o.curves not in ("keep", "flatten"):
        raise ValueError("Unknown simplification or curve method.")
    root = tree.getroot()
    parents = {child: parent for parent in root.iter() for child in parent}
    # Instances and geometry-dependent styling can create unseen obstacles. Ordinary pen CSS
    # is safe because retained elements keep their original tags, classes and attributes.
    geometry_css = re.compile(r"(?:^|[;{])\s*(?:transform(?:-origin|-box)?|d|x|y|cx|cy|r|rx|ry|width|height)\s*:", re.I)
    unsafe_stylesheet = re.compile(r"(?:marker-(?:start|mid|end)|stroke-dasharray)\s*:|@import", re.I)
    unsupported = {"use", "text", "textPath", "foreignObject", "script", "animate", "set", "animateMotion", "animateTransform"}
    if any(local(e.tag) in unsupported or (e is not root and local(e.tag) == "svg") or
           geometry_css.search(re.sub(r"/\*.*?\*/", "", e.get("style", ""), flags=re.S)) or
           (local(e.tag) == "style" and
            (geometry_css.search(re.sub(r"/\*.*?\*/", "", e.text or "", flags=re.S)) or
             unsafe_stylesheet.search(e.text or ""))) for e in root.iter()):
        stats.notes.append("Geometry kept: referenced instances, text, CSS, animation or nested viewports require expansion first.")
        return stats
    items, plotdata = collect(root, SHAPES | BASIC_SHAPES)
    metric, records, strokes = physical_matrix(root), [], []
    flatten = o.tolerance * o.flatten_share / 100 if o.tolerance else 0.01
    flatten = max(flatten, 1e-9)
    total_points = 0
    for n, it in enumerate(items):
        progress(0.05 + 0.2 * n / max(1, len(items)), "Reading line art")
        ctm = mat_mul(metric, it.ctm)
        linear = np.array([[ctm[0], ctm[2]], [ctm[1], ctm[3]]])
        scale = float(np.linalg.svd(linear, compute_uv=False)[0])
        if scale <= 0 or not np.isfinite(linear).all():
            raise ValueError("Non-finite or collapsed SVG transform; expand transforms before simplifying.")
        subs, curved = shape_subpaths(it.el, it.tag, flatten / scale, flatteners=FLATTENERS)
        current = []
        for points, closed in subs:
            if not points:
                continue
            if closed and points[-1] != points[0]:
                points = list(points) + [points[0]]
            # Keep zero-length / duplicate points intact; they may encode pen dots or markers.
            points = np.asarray(points, dtype=float)
            world = np.asarray([apply(ctm, p) for p in points])
            if not np.isfinite(world).all():
                raise ValueError("Non-finite coordinates in SVG.")
            s = Stroke(points, world, closed, list(range(len(points))))
            current.append(s)
            strokes.append(s)
            total_points += len(points)
            if total_points > MAX_POINTS:
                raise ValueError("Drawing exceeds 2,000,000 sampled vertices; keep curves or increase deviation.")
        eligible = (it.tag in SHAPES and (not curved or o.curves == "flatten"))
        # Marker/dash/path-effect semantics depend on original vertices or path length.
        parent = it.el
        while parent is not None:
            attrs = " ".join(f"{local(k)}:{v}" for k, v in parent.attrib.items())
            if re.search(r"marker-|stroke-dasharray|path-effect|pathLength", attrs):
                eligible = False
            parent = parents.get(parent)
        records.append((it, current, eligible, curved))
    stats.vertices_in = total_points
    stats.strokes = len(strokes)
    progress(0.26, "Indexing topology constraints")
    check = lambda: progress(0.3, "Checking simplification constraints")
    guard = TopologyGuard(strokes, check) if o.topology and o.tolerance > 0 else None
    for n, (it, current, eligible, curved) in enumerate(records):
        check = lambda n=n: progress(0.3 + 0.6 * n / max(1, len(records)), "Simplifying line art")
        check()
        changed = False
        for s in current:
            if eligible and o.tolerance > 0:
                budget = o.tolerance - flatten if curved else o.tolerance
                s.kept = reduce_stroke(s, o, budget, guard, stats, check)
                changed |= len(s.kept) < len(s.points)
            q = s.points[s.kept]
            stats.vertices_out += len(q)
            stats.length_in += float(np.linalg.norm(np.diff(s.points, axis=0), axis=1).sum())
            stats.length_out += float(np.linalg.norm(np.diff(q, axis=0), axis=1).sum())
            for i, j in zip(s.kept[:-1], s.kept[1:]):
                if j - i > 1:
                    stats.deviation = max(stats.deviation, float(distances(s.points[i:j + 1], s.points[i], s.points[j])[0].max()))
        # Flattening alone may increase the number of vertices; only rewrite if reduction happened.
        if changed:
            stats.changed += 1
            if it.tag == "path":
                parts = []
                for s in current:
                    p = s.local[s.kept]
                    if s.closed and len(p) > 1 and np.array_equal(p[0], p[-1]):
                        p = p[:-1]
                    parts.append("M " + _coords(p[:1]) + (" L " + _coords(p[1:]) if len(p) > 1 else "") + (" Z" if s.closed else ""))
                it.el.set("d", " ".join(parts))
            else:
                p = current[0].local[current[0].kept]
                if it.tag == "polygon" and np.array_equal(p[0], p[-1]):
                    p = p[:-1]
                it.el.set("points", _coords(p))
        elif eligible:
            # Report actual exported geometry when a curved element was left untouched.
            for s in current:
                stats.vertices_out += len(s.points) - len(s.kept)
                s.kept = list(range(len(s.points)))
    if stats.changed:
        for parent, el in plotdata:
            parent.remove(el)
    if o.curves == "keep":
        stats.notes.append("Original Béziers and arcs retained. Vertex counts describe the sampled line-art representation.")
    stats.notes.append("Topology checks use sampled curves; reduce Curve error budget for close curved details.")
    progress(1., "Ready")
    return stats
