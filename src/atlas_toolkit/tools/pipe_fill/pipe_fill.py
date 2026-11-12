"""Finite-radius volumetric path packing and transported-frame tube sweeps.

Independent Atlas implementation; not the Repulsive Curves tangent-point solver.
All distances in the caller's model frame. No GUI, file IO, or unseeded randomness.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

MAX_GRID = 750000
MAX_SAMPLES = 240000


@dataclass
class Curve:
    points: np.ndarray
    closed: bool
    radii: np.ndarray | None = None


class Domain:
    """Reusable exact triangle distances and cached approximate winding signs.

    Distances positive inside. Consistent closed oriented input is required by
    the engine. Taking abs of winding supports a globally reversed mesh while
    respecting oppositely oriented nested cavity shells.
    """
    def __init__(self, vertices, faces, check=lambda: None):
        import igl
        self.v = np.ascontiguousarray(vertices, dtype=np.float64)
        self.f = np.ascontiguousarray(faces, dtype=np.int64)
        self.check = check
        self.tree = igl.AABB()
        self.tree.init(self.v, self.f)
        self.wind = igl.FastWindingNumberBVH()
        self.wind.init(self.v, self.f)

    def distance(self, points):
        p = np.ascontiguousarray(points, dtype=np.float64)
        result = np.empty(len(p))
        for start in range(0, len(p), 12000):
            self.check()
            q = p[start:start+12000]
            d = np.sqrt(np.maximum(0., self.tree.squared_distance(self.v, self.f, q)[0]))
            inside = np.abs(self.wind.winding_number(q)) > .5
            result[start:start+len(q)] = np.where(inside, d, -d)
        return result

    def segments_inside(self, a, b, clearance, depth=7):
        """Conservatively certify capsules using the distance field's Lipschitz bound.

        A midpoint farther from the surface than clearance + half segment length
        certifies the whole segment. Subdivide unresolved intervals; reject any
        still unresolved at the limit rather than letting a thin wall be crossed.
        """
        a, b = np.asarray(a, float), np.asarray(b, float)
        count = len(a)
        okay = np.ones(count, dtype=bool)
        ids = np.arange(count)
        for level in range(depth+1):
            if not len(ids):
                break
            self.check()
            mid = (a+b)*.5
            d = self.distance(mid)
            half = np.linalg.norm(b-a, axis=1)*.5
            bad = d < clearance + 1e-10
            okay[ids[bad]] = False
            uncertain = (~bad) & (d < clearance+half+1e-10) & okay[ids]
            if level == depth:
                okay[ids[uncertain]] = False
                break
            aa, bb, mm, ii = a[uncertain], b[uncertain], mid[uncertain], ids[uncertain]
            a, b = np.concatenate((aa, mm)), np.concatenate((mm, bb))
            ids = np.concatenate((ii, ii))
        return okay


def lattice(domain, spacing, radius, clearance, rng, max_sites, progress):
    lo, hi = domain.v.min(0), domain.v.max(0)
    phase = rng.uniform(-.22, .22, 3)*spacing
    # Check counts before even allocating axis arrays: a very wide, flat model
    # with a manually selected short height can otherwise allocate a huge axis.
    shape = tuple(max(0, math.ceil((h-l-spacing*.35)/spacing)) for l, h in zip(lo, hi))
    total = math.prod(shape)
    if total > MAX_GRID:
        raise ValueError(f"Packing grid would have {total:,} sites (limit {MAX_GRID:,}); increase Spacing.")
    if not total:
        raise ValueError("No packing sites fit. Reduce Spacing or Outer diameter.")
    axes = [l+spacing*.35+np.arange(n)*spacing+d for l, n, d in zip(lo, shape, phase)]
    ijk = np.indices(shape).reshape(3, -1).T
    points = np.stack([axes[k][ijk[:, k]] for k in range(3)], axis=1)
    d = domain.distance(points)
    keep = d > radius+clearance+spacing*.015
    points, ijk = points[keep], ijk[keep]
    if len(points) > max_sites:
        raise ValueError(f"{len(points):,} interior sites exceed Packing site budget ({max_sites:,}); "
                         "increase Spacing or the budget.")
    if not len(points):
        raise ValueError("No interior sites fit the tube diameter and clearance. Reduce Outer diameter or Spacing.")
    progress(.35, f"Classified {len(points):,} interior sites")
    lookup = {tuple(q): i for i, q in enumerate(ijk)}
    edges = []
    for i, q in enumerate(ijk):
        if i % 2000 == 0:
            domain.check()
        for axis in range(3):
            n = q.copy()
            n[axis] += 1
            j = lookup.get(tuple(n))
            if j is not None:
                edges.append((i, j))
    adj = [set() for _ in points]
    if edges:
        edges = np.asarray(edges, dtype=int)
        inside = domain.segments_inside(points[edges[:, 0]], points[edges[:, 1]], radius+clearance)
        for a, b in edges[inside]:
            adj[a].add(int(b))
            adj[b].add(int(a))
    progress(1., "Certified interior routing edges")
    return points, ijk, adj, lookup


def strand_routes(points, adj, rng, length, straightness, check):
    available = np.ones(len(points), bool)
    order = np.lexsort((rng.random(len(points)), [len(a) for a in adj]))
    paths = []
    for start in order:
        if not available[start]:
            continue
        check()
        path = [int(start)]
        available[start] = False
        for _ in range(length):
            choices = [n for n in sorted(adj[path[-1]]) if available[n]]
            if not choices:
                break
            scores = rng.random(len(choices))
            if len(path) > 1:
                old = points[path[-1]]-points[path[-2]]
                forward = np.array([np.dot(points[n]-points[path[-1]], old) > 0 for n in choices])
                scores += forward*straightness*3.
            # Favor entering constrained sites, reducing orphaned boundary nodes.
            scores -= .13*np.array([sum(available[n] for n in adj[c]) for c in choices])
            chosen = choices[int(np.argmax(scores))]
            available[chosen] = False
            path.append(chosen)
        if len(path) >= 3:
            paths.append(path)
    return paths


def loop_routes(ijk, adj, lookup, rng, length, check):
    """Disjoint lattice plaquettes, spliced into longer non-branching 3D cycles."""
    n = len(ijk)
    owner = np.full(n, -1, int)
    link = [set() for _ in range(n)]
    parent, sizes = [], []

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in rng.permutation(n):
        if owner[a] >= 0:
            continue
        check()
        planes = [(0, 1), (0, 2), (1, 2)]
        rng.shuffle(planes)
        for x, y in planes:
            u, v = np.eye(3, dtype=int)[[x, y]]
            q = ijk[a]
            b, c, d = (lookup.get(tuple(q+t), -1) for t in (u, u+v, v))
            cycle = [int(a), b, c, d]
            if min(cycle) < 0 or any(owner[i] >= 0 for i in cycle):
                continue
            if any(j not in adj[i] for i, j in zip(cycle, cycle[1:]+cycle[:1])):
                continue
            label = len(parent)
            parent.append(label)
            sizes.append(4)
            for i, j in zip(cycle, cycle[1:]+cycle[:1]):
                owner[i] = label
                link[i].add(j)
                link[j].add(i)
            break
    # Pair parallel edges across lattice faces. Removing each and adding the two
    # cross-edges merges two cycles without intersections or branch vertices.
    directions = np.concatenate((np.eye(3, dtype=int), -np.eye(3, dtype=int)))
    for a in rng.permutation(n):
        check()
        if owner[a] < 0:
            continue
        for b in sorted(link[a]):
            if b < a:
                continue
            shifts = directions[rng.permutation(6)]
            for shift in shifts:
                c = lookup.get(tuple(ijk[a]+shift), -1)
                d = lookup.get(tuple(ijk[b]+shift), -1)
                if c < 0 or d < 0 or owner[c] < 0 or d not in link[c]:
                    continue
                ra, rc = root(owner[a]), root(owner[c])
                if ra == rc or sizes[ra]+sizes[rc] > length:
                    continue
                if c not in adj[a] or d not in adj[b]:
                    continue
                link[a].remove(b)
                link[b].remove(a)
                link[c].remove(d)
                link[d].remove(c)
                for i, j in ((a, c), (b, d)):
                    link[i].add(j)
                    link[j].add(i)
                parent[rc] = ra
                sizes[ra] += sizes[rc]
                break
    visited = set()
    paths = []
    for start in range(n):
        if not link[start] or start in visited:
            continue
        path, old, now = [], -1, start
        while now not in visited:
            path.append(now)
            visited.add(now)
            nxt = min(j for j in link[now] if j != old)
            old, now = now, nxt
        if now != start:
            raise RuntimeError("Cycle routing produced a non-cycle")
        paths.append(path)
    return paths


def subdivide(points, step, closed):
    points = np.asarray(points, float)
    out = []
    for a, b in zip(points, np.roll(points, -1, axis=0)):
        if not closed and len(out) and np.array_equal(a, points[-1]):
            break
        count = max(1, int(np.ceil(np.linalg.norm(b-a)/step)))
        out.extend(a+(b-a)*np.arange(count)[:, None]/count)
    if not closed:
        out.append(points[-1])
    return np.asarray(out)


def rounded_path(points, radius, step, closed):
    """Exact circular elbows on axis-aligned input, sampled as a polygonal curve."""
    out = []
    n = len(points)
    for i, v in enumerate(points):
        if not closed and i in (0, n-1):
            out.append(v)
            continue
        prev, nxt = points[(i-1) % n], points[(i+1) % n]
        u, w = v-prev, nxt-v
        lu, lw = np.linalg.norm(u), np.linalg.norm(w)
        u, w = u/lu, w/lw
        if np.dot(u, w) > .99:
            out.append(v)
            continue
        r = min(radius, .48*lu, .48*lw)
        center = v-u*r+w*r
        t = np.linspace(0, np.pi/2, max(4, int(np.ceil(np.pi*r/(2*step)))+1))
        out.extend(center+(-np.cos(t)[:, None]*w+np.sin(t)[:, None]*u)*r)
    return subdivide(np.asarray(out), step, closed)


def segments(curve):
    p = curve.points
    return (p, np.roll(p, -1, axis=0)) if curve.closed else (p[:-1], p[1:])


def segment_distance(a, b, c, d):
    """Vectorized finite-segment distances, including parallel and endpoint cases."""
    u, v, w = b-a, d-c, a-c
    aa = np.einsum('ij,ij->i', u, u)
    bb = np.einsum('ij,ij->i', u, v)
    cc = np.einsum('ij,ij->i', v, v)
    dd = np.einsum('ij,ij->i', u, w)
    ee = np.einsum('ij,ij->i', v, w)
    denom = aa*cc-bb*bb
    safe = np.maximum(denom, 1e-30)
    s, t = (bb*ee-cc*dd)/safe, (aa*ee-bb*dd)/safe

    def point_seg(p, x, y):
        z = y-x
        q = np.clip(np.einsum('ij,ij->i', p-x, z)/np.maximum(np.einsum('ij,ij->i', z, z), 1e-30), 0., 1.)
        return np.linalg.norm(p-x-q[:, None]*z, axis=1)

    result = np.minimum.reduce([point_seg(a, c, d), point_seg(b, c, d),
                                point_seg(c, a, b), point_seg(d, a, b)])
    valid = (denom > 1e-20) & (s >= 0) & (s <= 1) & (t >= 0) & (t <= 1)
    interior = np.linalg.norm(w+s[:, None]*u-t[:, None]*v, axis=1)
    return np.where(valid, np.minimum(result, interior), result)


class Separation:
    """Broad-phase segment-midpoint tree followed by exact finite-segment tests."""
    def __init__(self, radius, gap):
        self.distance = radius*2+gap
        self.radius = radius
        self.a = np.empty((0, 3))
        self.b = np.empty((0, 3))
        self.tree = None
        self.half = 0.

    def add(self, curve):
        from scipy.spatial import cKDTree
        a, b = segments(curve)
        self.a, self.b = np.concatenate((self.a, a)), np.concatenate((self.b, b))
        self.tree = cKDTree((self.a+self.b)*.5)
        self.half = np.linalg.norm(self.b-self.a, axis=1).max()*.5

    def valid(self, curve):
        from scipy.spatial import cKDTree
        a, b = segments(curve)
        lengths = np.linalg.norm(b-a, axis=1)
        mid = (a+b)*.5
        half = float(lengths.max())*.5
        tree = cKDTree(mid)
        # Adjacent samples are part of the same swept tube. Check remote arcs
        # exactly; bound local folds separately with the circumcircle radius.
        p = curve.points
        if len(p) > 3:
            x, y, z = (np.roll(p, 1, axis=0), p, np.roll(p, -1, axis=0)) if curve.closed else (p[:-2], p[1:-1], p[2:])
            cross = np.linalg.norm(np.cross(y-x, z-y), axis=1)
            r = np.linalg.norm(y-x, axis=1)*np.linalg.norm(z-y, axis=1)*np.linalg.norm(z-x, axis=1)/np.maximum(2*cross, 1e-30)
            if np.any(r < self.radius*1.15):
                return False
        pairs = tree.query_pairs(self.distance+2*half, output_type='ndarray')
        if len(pairs):
            s = np.cumsum(lengths)-lengths*.5
            arc = np.abs(s[pairs[:, 0]]-s[pairs[:, 1]])
            if curve.closed:
                arc = np.minimum(arc, lengths.sum()-arc)
            arc -= (lengths[pairs[:, 0]]+lengths[pairs[:, 1]])*.5
            remote = arc > max(np.pi*self.radius*1.2, self.distance*1.5)
            pairs = pairs[remote]
            if len(pairs):
                i, j = pairs.T
                if np.any(segment_distance(a[i], b[i], a[j], b[j]) < self.distance-1e-9):
                    return False
        if self.tree is not None:
            near = self.tree.query_ball_point(mid, self.distance+half+self.half)
            rows = np.repeat(np.arange(len(mid)), [len(n) for n in near])
            if len(rows):
                cols = np.concatenate([np.asarray(n, int) for n in near])
                for start in range(0, len(rows), 50000):
                    i, j = rows[start:start+50000], cols[start:start+50000]
                    if np.any(segment_distance(a[i], b[i], self.a[j], self.b[j]) < self.distance-1e-9):
                        return False
        return True

    def repel(self, points, spacing):
        """Bounded local separation force used by the organic relaxation step."""
        if self.tree is None:
            return np.zeros_like(points)
        d, idx = self.tree.query(points)
        v = points-(self.a[idx]+self.b[idx])*.5
        force = np.clip((self.distance*1.8-d)/(self.distance*1.8), 0., 1.)
        return v/np.maximum(d[:, None], 1e-12)*force[:, None]*spacing*.12


def relax(points, closed, flow, spacing, rng, separation, samples):
    from scipy.ndimage import gaussian_filter1d
    mode = 'wrap' if closed else 'nearest'
    smooth = gaussian_filter1d(points, samples*1.6, axis=0, mode=mode)
    noise = gaussian_filter1d(rng.normal(size=points.shape), samples*2., axis=0, mode=mode)
    norm = np.sqrt(np.mean(noise**2)) or 1.
    noise = np.clip(noise/norm, -2., 2.)*spacing*.32
    target = smooth + noise + separation.repel(smooth, spacing)
    if not closed:
        # Preserve both ends while allowing the interior to relax.
        ramp = np.minimum(np.arange(len(points)), np.arange(len(points))[::-1])/(samples*2.)
        target = points+(target-points)*np.minimum(ramp, 1.)[:, None]
    return points+(target-points)*flow


def pack(domain, values, progress, check):
    p = values
    spacing = float(p['spacing'])/50.
    nominal = float(p['diameter'])/100.
    variation = float(p['variation'])/100.
    radius = nominal*(1+variation)
    gap, clearance = float(p['gap'])/50., float(p['clearance'])/50.
    if spacing < 2*radius+gap+spacing*.08:
        raise ValueError("Spacing must exceed the largest Outer diameter plus Minimum gap by at least 8% "
                         "to allow rounded turns. Increase Spacing or reduce diameter/variation/gap.")
    bend = spacing*(float(p['bend'])/100. if p['mode'] == 'orthogonal' else .44)
    if bend < radius*1.4:
        raise ValueError("Elbows are too tight for the tube thickness. Increase Spacing or Elbow radius, "
                         "or reduce Outer diameter/Thickness variation.")
    rng = np.random.default_rng(int(p['seed']))
    points, ijk, adj, lookup = lattice(domain, spacing, radius, clearance, rng, int(p['max_sites']),
                                      lambda f, m: progress(.25*f, m))
    check()
    routes = (loop_routes(ijk, adj, lookup, rng, int(p['length']), check) if p['topology'] == 'loops'
              else strand_routes(points, adj, rng, int(p['length']), float(p['straightness'])/100., check))
    progress(.28, f"Routed {len(routes):,} paths")
    # Fail early before constructing curve arrays that cannot fit the mesh budget.
    estimated = sum(len(r) for r in routes)*int(p['samples'])*2*int(p['sides'])
    if p['section'] == 'hollow':
        estimated *= 2
    if estimated > int(p['max_faces'])*1.8:
        raise ValueError(f"Estimated pipe geometry exceeds Output face budget ({estimated:,} faces before "
                         "corner refinement). Increase Spacing or the budget, or reduce sides/samples.")
    closed = p['topology'] == 'loops'
    separation = Separation(radius, gap)
    curves, used, rejected = [], 0, 0
    relaxation = {str(a): 0 for a in (1., .6, .3, 0.)}
    samples_total = 0
    for i, route in enumerate(routes):
        check()
        base = rounded_path(points[route], bend, spacing/int(p['samples']), closed)
        desired = (relax(base, closed, float(p['organic'])/100., spacing, rng, separation, int(p['samples']))
                   if p['mode'] == 'organic' else base)
        accepted = None
        for alpha in (1., .6, .3, 0.):
            check()
            candidate = Curve(base+(desired-base)*alpha, closed)
            if not separation.valid(candidate):
                continue
            a, b = segments(candidate)
            if not np.all(domain.segments_inside(a, b, radius+clearance)):
                continue
            accepted = candidate
            relaxation[str(alpha)] += 1
            break
        if accepted is None:
            rejected += 1
            continue
        distances = np.linalg.norm(np.diff(accepted.points, axis=0), axis=1)
        s = np.r_[0., np.cumsum(distances)]
        total = s[-1] + (np.linalg.norm(accepted.points[-1]-accepted.points[0]) if closed else 0.)
        phase = rng.uniform(0., 2*np.pi)
        wave = np.sin(2*np.pi*s/max(total, 1e-12)*2+phase)
        accepted.radii = nominal*(1+variation*wave)
        samples_total += len(accepted.points)
        if samples_total > MAX_SAMPLES:
            raise ValueError("Curve sampling exceeds the safety budget; increase Spacing or reduce Curve samples.")
        curves.append(accepted)
        separation.add(accepted)
        used += len(route)
        progress(.3+.7*(i+1)/max(1, len(routes)), f"Packing path {i+1}/{len(routes)}")
    if not curves:
        raise ValueError("No valid paths fit these settings. Reduce Outer diameter and Boundary clearance, "
                         "reduce Spacing for thin forms, or try Independent strands.")
    return curves, dict(sites=len(points), used=used, rejected=rejected, routed=len(routes),
                        coverage=100.*used/len(points), radius=radius, spacing=spacing,
                        relaxation=relaxation)


def transported_frames(points, closed):
    p = np.asarray(points)
    edges = np.diff(np.vstack((p, p[0])) if closed else p, axis=0)
    edges /= np.linalg.norm(edges, axis=1)[:, None]
    tangent = (np.roll(edges, 1, axis=0)+edges if closed else
               np.vstack((edges[0], edges[:-1]+edges[1:], edges[-1])))
    tangent /= np.linalg.norm(tangent, axis=1)[:, None]
    normals = np.empty_like(p)
    axis = np.eye(3)[np.argmin(np.abs(tangent[0]))]
    normals[0] = np.cross(tangent[0], axis)
    normals[0] /= np.linalg.norm(normals[0])

    def transport(normal, a, b):
        cross = np.cross(a, b)
        cosine = np.dot(a, b)
        if cosine < -.99999:
            raise ValueError("A path folds back on itself; reduce diameter or increase spacing.")
        q = normal+np.cross(cross, normal)+np.cross(cross, np.cross(cross, normal))/max(1+cosine, 1e-12)
        q -= b*np.dot(q, b)
        return q/np.linalg.norm(q)

    for i in range(1, len(p)):
        normals[i] = transport(normals[i-1], tangent[i-1], tangent[i])
    if closed:
        end = transport(normals[-1], tangent[-1], tangent[0])
        angle = np.arctan2(np.dot(tangent[0], np.cross(end, normals[0])), np.dot(end, normals[0]))
        # Spread holonomy over every ring, including the closing interval.
        arc = np.r_[0., np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]
        total = arc[-1]+np.linalg.norm(p[-1]-p[0])
        theta = angle*arc/total
        normals = normals*np.cos(theta)[:, None]+np.cross(tangent, normals)*np.sin(theta)[:, None]
    return normals, np.cross(tangent, normals)


def sweep(curve, sides, hollow, wall):
    import trimesh
    p, radii = curve.points, curve.radii
    u, v = transported_frames(p, curve.closed)
    theta = np.arange(sides)*2*np.pi/sides
    circle = u[:, None]*np.cos(theta)[None, :, None]+v[:, None]*np.sin(theta)[None, :, None]
    outer = p[:, None]+circle*radii[:, None, None]
    n = len(p)
    vertices = [outer.reshape(-1, 3)]
    faces = []
    pairs = n if curve.closed else n-1
    for i in range(pairs):
        j = (i+1) % n
        for k in range(sides):
            a, b = i*sides+k, i*sides+(k+1) % sides
            c, d = j*sides+k, j*sides+(k+1) % sides
            faces.extend(((a, b, d), (a, d, c)))
    if hollow:
        vertices.append((p[:, None]+circle*(radii*(1-wall))[:, None, None]).reshape(-1, 3))
        offset = n*sides
        faces.extend(tuple(offset+x for x in reversed(f)) for f in list(faces))
        if not curve.closed:
            for k in range(sides):
                a, b = k, (k+1) % sides
                faces.extend(((a, offset+b, b), (a, offset+a, offset+b)))
                a, b = (n-1)*sides+k, (n-1)*sides+(k+1) % sides
                faces.extend(((a, b, offset+b), (a, offset+b, offset+a)))
    elif not curve.closed:
        vertices.append(np.array([p[0], p[-1]]))
        for k in range(sides):
            faces.extend(((n*sides, (k+1) % sides, k),
                          (n*sides+1, (n-1)*sides+k, (n-1)*sides+(k+1) % sides)))
    return trimesh.Trimesh(np.concatenate(vertices), np.asarray(faces, np.int64), process=False)


def build_mesh(curves, sides, hollow, wall, budget, check):
    import trimesh
    estimated = sum((len(c.points) if c.closed else len(c.points)-1)*sides*2*(2 if hollow else 1)
                    +(0 if c.closed else sides*(4 if hollow else 2)) for c in curves)
    if estimated > budget:
        raise ValueError(f"Pipes need {estimated:,} faces, above Output face budget ({budget:,}). "
                         "Increase Spacing or the budget, or reduce Tube sides / Curve samples.")
    meshes = []
    for curve in curves:
        check()
        meshes.append(sweep(curve, sides, hollow, wall))
    result = trimesh.util.concatenate(meshes)
    if (not np.isfinite(result.vertices).all() or not result.is_watertight or
            not result.is_winding_consistent or result.volume <= 0 or
            not result.nondegenerate_faces().all()):
        raise ValueError("Pipe geometry failed closed-shell validation; increase Spacing or reduce Outer diameter.")
    return result
