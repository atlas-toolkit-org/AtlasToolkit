"""Conservative all-pen contact anchoring and swept-region obstacle checks."""
import numpy as np
import shapely
from rtree.index import Index

CHUNK = 1024


def fixed_ends(corners):
    """Stationary segment endpoints, using only Shapely >=2.0 APIs."""
    fixed = np.all(corners[:, :2] == corners[:, 2:], axis=2)
    pts = shapely.points(corners[:, :2].reshape(-1, 2)).reshape(-1, 2)
    empty = shapely.GeometryCollection()
    return shapely.union(np.where(fixed[:, 0], pts[:, 0], empty),
                         np.where(fixed[:, 1], pts[:, 1], empty))


class Guard:
    def __init__(self, strokes, check):
        self.strokes = strokes
        geoms, owners, edges = [], [], []
        for owner, stroke in enumerate(strokes):
            check()
            p = stroke.points
            if len(p) == 1:
                geoms.append(shapely.Point(p[0]))
                owners.append(owner)
                edges.append(0)
            else:
                pairs = np.stack([p[:-1], p[1:]], axis=1)
                segments = shapely.linestrings(pairs)
                dots = np.all(pairs[:, 0] == pairs[:, 1], axis=1)
                # GEOS can report a degenerate LineString as intersecting a
                # polygon yet return an empty intersection. Index pen dots as
                # actual Points so swept-region checks cannot lose them.
                segments[dots] = shapely.points(pairs[dots, 0])
                geoms.extend(segments)
                owners.extend([owner] * len(pairs))
                edges.extend(range(len(pairs)))
        self.original = np.asarray(geoms, dtype=object)
        self.owners, self.edges = np.asarray(owners), np.asarray(edges)
        self.tree = shapely.STRtree(self.original)
        self.accepted, self.index = [], Index()
        # Pin both ends of every original segment that touches nonadjacent ink.
        # The whole original segment then remains straight between fixed anchors.
        for start in range(0, len(geoms), CHUNK):
            check()
            a, b = self.tree.query(self.original[start:start + CHUNK], predicate="intersects")
            a += start
            distinct = self.owners[a] != self.owners[b]
            apart = np.abs(self.edges[a] - self.edges[b]) > 1
            same_closed_seam = np.zeros(len(a), dtype=bool)
            same = np.flatnonzero(~distinct & apart)
            for i in same:
                s = strokes[int(self.owners[a[i]])]
                same_closed_seam[i] = s.closed and abs(self.edges[a[i]] - self.edges[b[i]]) == len(s.points) - 2
            hit = distinct | (apart & ~same_closed_seam)
            for k in np.unique(np.r_[a[hit], b[hit]]):
                s = strokes[int(self.owners[k])]
                edge = int(self.edges[k])
                s.contacts[edge:min(edge + 2, len(s.points))] = True
            if len(a) > 2_000_000:
                raise ValueError("Too many overlapping contacts for safe smoothing; separate coincident linework first.")
        for s in strokes:
            if s.closed and (s.contacts[0] or s.contacts[-1]):
                s.contacts[0] = s.contacts[-1] = True

    def safe(self, owner, p, q, check):
        changed = np.any(p != q, axis=1)
        ids = np.flatnonzero(changed[:-1] | changed[1:])
        # Check motion, not just the final curve: another motif cannot be swept over.
        for start in range(0, len(ids), CHUNK):
            check()
            edges = ids[start:start + CHUNK]
            corners = np.stack([p[edges], p[edges + 1], q[edges], q[edges + 1]], axis=1)
            regions = shapely.convex_hull(shapely.multipoints(corners))
            a, b = self.tree.query(regions, predicate="intersects")
            keep = self.owners[b] != owner
            a, b = a[keep], b[keep]
            if len(a):
                hits = shapely.intersection(regions[a], self.original[b])
                ends = fixed_ends(corners[a])
                if np.any(~shapely.covered_by(hits, ends)):
                    return False
            # Earlier accepted results also constrain subsequent strokes.
            bounds = shapely.bounds(shapely.MultiPoint(corners.reshape(-1, 2)))
            nearby = list(self.index.intersection(bounds))
            for k in nearby:
                hits = shapely.intersection(regions, self.accepted[k])
                active = ~shapely.is_empty(hits)
                if np.any(active):
                    if np.any(~shapely.covered_by(hits[active], fixed_ends(corners[active]))):
                        return False
        return True

    def add(self, q):
        geom = shapely.LineString(q) if len(q) > 1 else shapely.Point(q[0])
        self.index.insert(len(self.accepted), geom.bounds)
        self.accepted.append(geom)
