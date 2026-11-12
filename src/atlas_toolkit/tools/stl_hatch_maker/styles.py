"""
Hatching styles. Each takes the scene, the parameters and a seeded generator and returns polylines in paper mm,
already cut by visibility. STYLES maps the style id to (label, function); adding a style is one function plus its
parameters (with visible_if={"style": ...}) in __init__.py.

    streamlines   strokes that follow the surface (v5), seeded by a Poisson disk (C-v4), tone sets the spacing
    screen        straight parallel lines across the picture at an angle (v3 parallel / cross)
    contours      depth contours: the model sliced by planes facing the camera (v3 / C-v4 "contour")
    stipple       dots, denser where darker (v3)
    directional   short strokes along the surface normal, longer where darker (v3)
    regions       tone regions filled with parallel lines, the darkest crossed (v2.1 "fixed crosshatch")
    isophotes     lines of equal tone that follow the form (v2.1 "follow form")
"""
from __future__ import annotations

import math

import numpy as np

from .scene import Scene, densify, runs


# ----------------------------------------------------------------------------- helpers

def tone_spacing(tone: np.ndarray, dark_mm: float, light_mm: float, gamma: float) -> np.ndarray:
    return dark_mm + (light_mm - dark_mm) * np.power(np.clip(tone, 0, 1), gamma)


def poisson_on_paper(points: np.ndarray, radius: np.ndarray, order: np.ndarray) -> list:
    """Greedy Poisson-disk selection on paper: keep a point when no kept point lies within max(r_i, r_j)."""
    if len(order) == 0:
        return []
    cell = max(float(radius.min()), 1e-6)
    rmax = float(radius.max())
    reach = int(math.ceil(rmax / cell))
    grid: dict = {}
    keep = []
    for i in order:
        x, y = points[i]
        r = radius[i]
        cx, cy = int(math.floor(x / cell)), int(math.floor(y / cell))
        ok = True
        for gx in range(cx - reach, cx + reach + 1):
            for gy in range(cy - reach, cy + reach + 1):
                for j in grid.get((gx, gy), ()):
                    px, py = points[j]
                    rr = max(r, radius[j])
                    if (px - x) ** 2 + (py - y) ** 2 < rr * rr:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                break
        if ok:
            keep.append(int(i))
            grid.setdefault((cx, cy), []).append(int(i))
    return keep


def parallel_lines(bounds, spacing: float, angle_deg: float) -> list:
    """Lines at `angle_deg` every `spacing` covering `bounds` (x0, y0, x1, y1), in paper mm."""
    x0, y0, x1, y1 = bounds
    a = math.radians(angle_deg)
    d = np.array([math.cos(a), math.sin(a)])
    nrm = np.array([-d[1], d[0]])
    c = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
    half = math.hypot(x1 - x0, y1 - y0) / 2 + spacing
    out = []
    k = int(half / spacing) + 1
    for i in range(-k, k + 1):
        o = c + nrm * (i * spacing)
        out.append([tuple(o - d * half), tuple(o + d * half)])
    return out


def model_bounds(scene: Scene) -> tuple:
    p = scene.P[scene.VN[:, 2] > -1]
    return float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())


# ----------------------------------------------------------------------------- direction field

def edge_lists(scene: Scene):
    e = scene.tm.edges_unique
    return e[:, 0], e[:, 1]


def principal_dirs(scene: Scene) -> np.ndarray:
    """v5's per-vertex 'k_max': the main axis of the neighbours spread in the tangent plane (PCA), vectorised."""
    V, N = scene.V, scene.VN
    i, j = edge_lists(scene)
    cov = np.zeros((len(V), 3, 3))
    for a, b in ((i, j), (j, i)):
        rel = V[b] - V[a]
        rel -= np.sum(rel * N[a], axis=1)[:, None] * N[a]
        np.add.at(cov, a, rel[:, :, None] * rel[:, None, :])
    _, vecs = np.linalg.eigh(cov)
    d = vecs[:, :, 2]
    return d / (np.linalg.norm(d, axis=1, keepdims=True) + 1e-12)


def direction_field(scene: Scene, curvature: float = 0.65, smooth: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """C-v4's blend of the principal direction and the isophote direction (n × light), smoothed. Signs are
    aligned before blending and averaging (a direction field has no arrow), which C-v4 missed."""
    N = scene.VN
    iso = np.cross(N, scene.L)
    iso /= np.linalg.norm(iso, axis=1, keepdims=True) + 1e-12
    pdir = principal_dirs(scene) if curvature > 0 else iso
    s = np.sign(np.sum(pdir * iso, axis=1))
    s[s == 0] = 1
    d = curvature * pdir * s[:, None] + (1 - curvature) * iso
    i, j = edge_lists(scene)
    for _ in range(max(int(smooth), 0)):
        sg = np.sign(np.sum(d[i] * d[j], axis=1))
        sg[sg == 0] = 1
        acc = np.zeros_like(d)
        cnt = np.zeros(len(d))
        np.add.at(acc, i, d[j] * sg[:, None])
        np.add.at(acc, j, d[i] * sg[:, None])
        np.add.at(cnt, i, 1)
        np.add.at(cnt, j, 1)
        mean = acc / np.maximum(cnt, 1)[:, None]
        d = 0.5 * (d + np.where(cnt[:, None] > 0, mean, d))
    d -= np.sum(d * N, axis=1)[:, None] * N
    d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-12
    d2 = np.cross(N, d)
    d2 /= np.linalg.norm(d2, axis=1, keepdims=True) + 1e-12
    return d, d2


# ----------------------------------------------------------------------------- styles

def streamlines(scene: Scene, p, rng, progress=None) -> list:
    from scipy.spatial import cKDTree
    d1, d2 = direction_field(scene, p.follow, p.smooth)
    spacing = tone_spacing(scene.vtone, p.spacing_dark, p.spacing_light, p.gamma)   # mm
    tree = cKDTree(scene.V)
    seen = scene.VN[:, 2] > 0.05
    cand = np.flatnonzero(seen)
    if len(cand):
        cand = cand[scene.visible_points(scene.V[cand])]
    out = []
    passes = [(d1, p.highlight)] + ([(d2, p.cross_band)] if p.cross else [])
    for n, (field, band) in enumerate(passes):
        c = cand[scene.vtone[cand] <= band]
        seeds = poisson_on_paper(scene.P, spacing, rng.permutation(c))
        if progress:
            progress(0.25 + 0.3 * n / len(passes), f"tracing {len(seeds)} strokes")
        out += _trace(scene, tree, field, spacing, np.array(seeds, dtype=np.int64), p)
    return out


def _trace(scene: Scene, tree, field, spacing_mm, seeds, p) -> list:
    """Vectorised streamline tracing on the surface, both ways from every seed (v5's rules: a step of a fraction of
    the local spacing, stop when the stroke turns more than the limit, reaches its length or leaves the front)."""
    if len(seeds) == 0:
        return []
    V, N = scene.V, scene.VN
    cos_turn = math.cos(math.radians(p.turn))
    max_len = scene.cam(p.stroke_len) / 2
    sides = []
    for sign in (1.0, -1.0):
        pos = V[seeds].copy()
        idx = seeds.copy()
        prev = field[idx] * sign
        length = np.zeros(len(seeds))
        alive = np.ones(len(seeds), dtype=bool)
        pts = []
        for _ in range(400):
            if not alive.any():
                break
            d = field[idx]
            d *= np.where(np.sum(d * prev, axis=1) < 0, -1.0, 1.0)[:, None]
            alive &= np.sum(d * prev, axis=1) >= cos_turn
            h = scene.cam(np.maximum(spacing_mm[idx], 0.2)) * 0.5
            nxt = pos + d * h[:, None]
            _, k = tree.query(nxt)
            nxt -= np.sum((nxt - V[k]) * N[k], axis=1)[:, None] * N[k]   # back onto the surface
            alive &= N[k, 2] > 0
            length += h
            alive &= length <= max_len
            step = np.where(alive[:, None], nxt, np.nan)
            pts.append(step)
            pos = np.where(alive[:, None], nxt, pos)
            idx = np.where(alive, k, idx)
            prev = np.where(alive[:, None], d, prev)
        sides.append(np.stack(pts, axis=1) if pts else np.zeros((len(seeds), 0, 3)))
    polys = []
    for s in range(len(seeds)):
        fwd = sides[0][s]
        back = sides[1][s]
        fwd = fwd[~np.isnan(fwd[:, 0])]
        back = back[~np.isnan(back[:, 0])][::-1]
        pl = np.concatenate([back, V[seeds[s]][None], fwd])
        if len(pl) >= 2:
            polys.append((pl, len(back)))
    if not polys:
        return []
    allpts = np.concatenate([pl for pl, _ in polys])
    vis = scene.visible_points(allpts)
    out, i = [], 0
    for pl, si in polys:
        v = vis[i:i + len(pl)]
        i += len(pl)
        if not v[si]:
            continue
        a = si
        while a > 0 and v[a - 1]:
            a -= 1
        b = si
        while b < len(pl) - 1 and v[b + 1]:
            b += 1
        if b > a:
            out.append([tuple(q) for q in scene.to_paper(pl[a:b + 1])])
    return out


def screen(scene: Scene, p, rng, progress=None) -> list:
    b = model_bounds(scene)
    lines = parallel_lines(b, p.spacing, p.angle)
    out = scene.clip_on_surface(lines, p.vis_step, keep=lambda f, _: scene.ftone[f] <= p.highlight)
    if p.cross:
        lines = parallel_lines(b, p.spacing, p.angle + 90)
        out += scene.clip_on_surface(lines, p.vis_step, keep=lambda f, _: scene.ftone[f] <= p.cross_band)
    return out


def contours(scene: Scene, p, rng, progress=None) -> list:
    import trimesh
    from atlas_toolkit.geom.svgout import join_segments
    z = scene.V[:, 2][scene.VN[:, 2] > 0] if (scene.VN[:, 2] > 0).any() else scene.V[:, 2]
    levels = np.linspace(z.min(), z.max(), int(p.levels) + 2)[1:-1]
    polys = []
    for n, lev in enumerate(levels):
        segs = trimesh.intersections.mesh_plane(scene.tm, (0, 0, 1), (0, 0, lev))
        if len(segs):
            polys += [np.asarray(pl) for pl in join_segments([[tuple(a), tuple(b)] for a, b in segs], 1e-9)]
        if progress and n % 4 == 0:
            progress(0.25 + 0.3 * n / len(levels), "slicing")
    return scene.clip_polylines(polys, p.vis_step)


def stipple(scene: Scene, p, rng, progress=None) -> list:
    x0, y0, x1, y1 = model_bounds(scene)
    n = int((x1 - x0) * (y1 - y0) / 100.0 * p.dots)
    pts = np.column_stack([rng.uniform(x0, x1, n), rng.uniform(y0, y1, n)])
    face = scene.surface_at(pts)
    hit = face >= 0
    dark = np.zeros(n)
    dark[hit] = 1.0 - scene.ftone[face[hit]]
    keep = hit & (rng.random(n) < np.power(dark, p.gamma))
    r = p.dot_size / 2
    out = []
    for x, y in pts[keep]:
        if p.dot_shape == "circle":
            t = np.linspace(0, 2 * math.pi, 9)
            out.append([(x + r * math.cos(a), y + r * math.sin(a)) for a in t])
        else:
            out.append([(x - r, y), (x + r, y)])
    return out


def directional(scene: Scene, p, rng, progress=None) -> list:
    seen = np.flatnonzero(scene.VN[:, 2] > 0.05)
    if len(seen):
        seen = seen[scene.visible_points(scene.V[seen])]
    seeds = poisson_on_paper(scene.P, np.full(len(scene.P), p.spacing), rng.permutation(seen))
    out = []
    for i in seeds:
        dark = 1.0 - scene.vtone[i]
        if dark < 0.05:
            continue
        nx, ny = scene.VN[i, 0], -scene.VN[i, 1]                # paper y points down
        ln = math.hypot(nx, ny)
        if ln < 1e-6:
            continue
        L = p.stroke_len * dark ** p.gamma
        x, y = scene.P[i]
        out.append([(x, y), (x + nx / ln * L, y + ny / ln * L)])
    return scene.clip_on_surface(out, p.vis_step)


def _visible_faces(scene: Scene) -> np.ndarray:
    tri = scene.V[scene.F]
    c = tri.mean(axis=1)
    vis = np.zeros(len(scene.F), dtype=bool)
    front = np.flatnonzero(scene.front)
    if len(front):
        vis[front] = scene.visible_points(c[front])
    return vis


def regions(scene: Scene, p, rng, progress=None) -> list:
    import shapely
    from shapely.geometry import LineString
    vis = _visible_faces(scene)
    tri2 = scene.P[scene.F]
    out = []
    for thr, angle in ((p.t_light, p.angle), (p.t_dark, p.angle + 90)):
        sel = np.flatnonzero(vis & (scene.ftone < thr))
        if len(sel) == 0:
            continue
        polys = shapely.polygons(np.concatenate([tri2[sel], tri2[sel][:, :1]], axis=1))
        polys = polys[shapely.area(polys) > 1e-9]
        region = shapely.union_all(shapely.make_valid(polys)).buffer(0)
        if region.is_empty:
            continue
        lines = parallel_lines(region.bounds, p.spacing, angle)
        clipped = shapely.intersection(shapely.linestrings(np.array(lines)), region)
        for g in shapely.get_parts(clipped):
            if g.geom_type == "LineString" and g.length > 0:
                out.append(list(g.coords))
            elif g.geom_type == "MultiLineString":
                out += [list(x.coords) for x in g.geoms]
        if progress:
            progress(0.45, "filling tone regions")
    del LineString
    return out


def isophotes(scene: Scene, p, rng, progress=None) -> list:
    from atlas_toolkit.geom.svgout import join_segments
    vis = _visible_faces(scene)
    F = scene.F[vis]
    if len(F) == 0:
        return []
    t = scene.vtone[F]                  # (m, 3)
    P3 = scene.V[F]                     # (m, 3, 3)
    polys = []
    lo, hi = sorted((p.t_dark, p.t_light))
    for lev in np.arange(lo, hi + 1e-9, p.tone_step):
        crosses = []
        for a, b in ((0, 1), (1, 2), (2, 0)):
            ta, tb = t[:, a], t[:, b]
            m = (ta - lev) * (tb - lev) < 0
            r = np.where(m, (lev - ta) / np.where(tb != ta, tb - ta, 1), 0)
            crosses.append((m, P3[:, a] + r[:, None] * (P3[:, b] - P3[:, a])))
        mask = np.stack([c[0] for c in crosses], axis=1)
        two = mask.sum(axis=1) == 2
        if not two.any():
            continue
        pts = np.stack([c[1] for c in crosses], axis=1)[two]     # (k, 3 edges, 3)
        mk = mask[two]
        first = np.argmax(mk, axis=1)
        second = 2 - np.argmax(mk[:, ::-1], axis=1)
        segs = [[tuple(pts[i, first[i]]), tuple(pts[i, second[i]])] for i in range(len(pts))]
        polys += [np.asarray(pl) for pl in join_segments(segs, 1e-9)]
    return scene.clip_polylines(polys, p.vis_step)


STYLES = {
    "none": ("No hatching", None),
    "streamlines": ("Surface strokes", streamlines),
    "screen": ("Straight lines", screen),
    "contours": ("Depth contours", contours),
    "stipple": ("Stipple", stipple),
    "directional": ("Normal strokes", directional),
    "regions": ("Tone regions", regions),
    "isophotes": ("Isophotes", isophotes),
}
