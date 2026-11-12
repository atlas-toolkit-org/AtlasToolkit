"""
Outlines as single strokes: occlusion edges from depth (where one surface passes in front of another) and, if
asked, the photo's own edges. Edge pixels are traced as a graph, so every edge is drawn once; notebooks (c) and (d)
ran contour finders around thin edge blobs, which draws each outline twice, side by side.
"""
from __future__ import annotations

import numpy as np


def photo_edges(L: np.ndarray, sensitivity: float, sigma: float) -> np.ndarray:
    from skimage.feature import canny
    hi = 1.0 - (0.01 + 0.12 * sensitivity)
    return canny(L.astype(np.float64), sigma=max(sigma, 0.5), low_threshold=hi - 0.08, high_threshold=hi,
                 use_quantiles=True)


def trace(edges: np.ndarray) -> list:
    """Polylines [(x, y), ...] through thin edge pixels, one per stretch between ends and junctions. Diagonal links
    are only used where no straight neighbour already joins the two pixels (no little triangles)."""
    import networkx as nx
    from ...geom.polylines import trails
    from skimage.morphology import skeletonize
    sk = skeletonize(edges)
    ys, xs = np.nonzero(sk)
    if len(xs) == 0:
        return []
    on = set(zip(ys.tolist(), xs.tolist()))
    g = nx.Graph()
    g.add_nodes_from(on)
    for y, x in on:
        for dy, dx in ((0, 1), (1, 0)):
            if (y + dy, x + dx) in on:
                g.add_edge((y, x), (y + dy, x + dx))
        for dy, dx in ((1, 1), (1, -1)):
            q = (y + dy, x + dx)
            if q in on and (y + dy, x) not in on and (y, x + dx) not in on:
                g.add_edge((y, x), q)
    out = []
    for comp in nx.connected_components(g):
        sub = g.subgraph(comp)
        if sub.number_of_edges() == 0:
            continue
        for path in trails(sub):
            out.append([(float(p[1]), float(p[0])) for p in path])
    return out
