"""Polyline helpers shared by tools: tracing graphs of segments or pixels into strokes."""
from __future__ import annotations


def trails(g) -> list:
    """Split a graph into simple paths that start and end at junctions or ends (each edge used once)."""
    used = set()
    paths = []
    nodes = sorted(g.nodes, key=lambda n: (g.degree(n) == 2, n))   # start from ends and junctions
    for start in nodes:
        for nb in g.neighbors(start):
            e = frozenset((start, nb))
            if e in used:
                continue
            path = [start, nb]
            used.add(e)
            prev, cur = start, nb
            while g.degree(cur) == 2:
                nxt = next((x for x in g.neighbors(cur) if x != prev), None)
                if nxt is None or frozenset((cur, nxt)) in used:
                    break
                used.add(frozenset((cur, nxt)))
                path.append(nxt)
                prev, cur = cur, nxt
            paths.append(path)
    return paths
