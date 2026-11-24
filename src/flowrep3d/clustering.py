"""Sparse signed correlation clustering: GAEC initialization and KL refinement.

The objective maximizes sum(w_ij * [label_i == label_j]). Negative edges
are repulsive; connected-components on positive edges is not this objective.

Refinement visits every pair of neighbouring clusters (Kernighan-Lin, as KLj does). It works on a window of the
pair: the nodes where the two clusters touch, grown by `KL_DEPTH` arcs into each. Moves far from the interface
never pay off, and running each pair over both whole clusters made a pass cost the size of a cluster times the
number of its neighbours: on a remeshed head that grew as faces^1.5, hours at 100k faces.
"""
from __future__ import annotations

import heapq

import numpy as np

from .types import checkpoint

KL_DEPTH = 2


def alignment_weight(angle, sigma=5.0):
    angle = np.asarray(angle, float)
    return np.where(angle <= 45.0, np.exp(-(angle / sigma)**2),
                    -5.0 * np.exp(-((90.0 - angle) / 15.0)**2))


def _arcs(pairs, weights):
    """Canonical arrays (i < j, summed duplicates, zero and self arcs dropped), in order of first appearance."""
    P = np.asarray(pairs, np.int64).reshape(-1, 2)
    w = np.asarray(weights, float).reshape(-1)
    keep = (P[:, 0] != P[:, 1]) & np.isfinite(w)
    P, w = P[keep], w[keep]
    i = np.minimum(P[:, 0], P[:, 1]); j = np.maximum(P[:, 0], P[:, 1])
    if not len(i):
        return i, j, w
    key = np.stack([i, j], 1)
    unique, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    total = np.zeros(len(unique))
    np.add.at(total, inverse.reshape(-1), w)        # unbuffered: summed in input order, as the dict did
    order = np.argsort(first, kind="stable")
    unique, total = unique[order], total[order]
    keep = np.abs(total) > 1e-14
    return unique[keep, 0], unique[keep, 1], total[keep]


def aggregate_graph(pairs, weights):
    i, j, w = _arcs(pairs, weights)
    return {(int(a), int(b)): float(c) for a, b, c in zip(i.tolist(), j.tolist(), w.tolist())}


def objective(labels, graph):
    return sum(w for (a,b), w in graph.items() if labels[a] == labels[b])


def _gaec(n, graph, cancel=None, progress=None):
    adjacent = [dict() for _ in range(n)]
    heap = []
    parent = np.arange(n)
    for (a,b), w in graph.items():
        adjacent[a][b] = adjacent[b][a] = w
        if w > 1e-12:
            heapq.heappush(heap, (-w,a,b))
    pops=0; total=max(1,len(heap))
    while heap:
        pops+=1
        if pops%1000==0:
            checkpoint(cancel)
            if progress and pops%20000==0:
                progress(min(1.0,pops/total))
        neg, a, b = heapq.heappop(heap)
        current = adjacent[a].get(b)
        if current is None or current <= 1e-12 or abs(current + neg) > 1e-9 * (1+abs(current)):
            continue
        if b < a:
            a,b = b,a
        parent[b] = a
        adjacent[a].pop(b, None)
        adjacent[b].pop(a, None)
        for other,w in list(adjacent[b].items()):
            adjacent[other].pop(b, None)
            nw = adjacent[a].get(other,0.0) + w
            adjacent[a][other] = adjacent[other][a] = nw
            if nw > 1e-12:
                heapq.heappush(heap, (-nw,min(a,other),max(a,other)))
        adjacent[b].clear()
    for v in range(n):
        root=v
        while parent[root] != root:
            root=int(parent[root])
        while parent[v] != v:
            nxt=int(parent[v]); parent[v]=root; v=nxt
    return parent


def _kl_pair(labels, adjacency, a, b, vertices):
    """Allow non-improving intermediate moves; commit only the best positive prefix. Only `vertices` may move;
    their neighbours outside it keep their labels but still count in the gains."""
    current = {v: int(labels[v]) for v in vertices}
    gains, heap = {}, []
    for v in vertices:
        own = current[v]; gain = 0.0
        for u,w in adjacency[v].items():
            lu = current.get(u)
            if lu is None:
                lu = int(labels[u])
                if lu != a and lu != b:
                    continue
            gain += w if lu != own else -w
        gains[v] = gain
        heapq.heappush(heap,(-gain,v))
    moved, total, best, best_count = set(),0.0,0.0,0
    moves=[]
    while heap:
        ng,v=heapq.heappop(heap)
        if v in moved or abs(gains[v]+ng)>1e-9:
            continue
        old=current[v]; new=b if old==a else a
        current[v]=new; moved.add(v)
        total+=gains[v]; moves.append((v,new))
        if total>best+1e-10:
            best,best_count=total,len(moves)
        for u,w in adjacency[v].items():
            if u in current and u not in moved:
                gains[u] += 2*w if current[u] == old else -2*w
                heapq.heappush(heap,(-gains[u],u))
    if best_count:
        for v,new in moves[:best_count]:
            labels[v]=new
    return best


def _window(seeds, adjacency, labels, a, b, depth):
    """`seeds` and the nodes of clusters a and b within `depth` arcs of them."""
    window = set(seeds); frontier = list(window)
    for _ in range(depth):
        grown = []
        for v in frontier:
            for u in adjacency[v]:
                if u not in window and (labels[u] == a or labels[u] == b):
                    window.add(u); grown.append(u)
        frontier = grown
    return sorted(window)


def _split_repelled(labels, adjacency, gi, gj, gw):
    """A node whose arcs inside its own cluster sum negative leaves it. Nodes are visited in index order and each
    split changes the sums of the later ones, exactly as a plain loop over all nodes; only candidates are visited."""
    same = labels[gi] == labels[gj]
    internal = np.bincount(gi[same], gw[same], len(labels)) + np.bincount(gj[same], gw[same], len(labels))
    todo = [int(v) for v in np.flatnonzero(internal < -1e-10)]
    heapq.heapify(todo); seen = set(todo)
    next_label = int(labels.max()) + 1
    while todo:
        v = heapq.heappop(todo)
        if sum(w for u,w in adjacency[v].items() if labels[u] == labels[v]) < -1e-10:
            labels[v] = next_label; next_label += 1
            for u in adjacency[v]:
                if u > v and u not in seen:
                    seen.add(u); heapq.heappush(todo, u)


def correlation_cluster(n, pairs, weights, passes=8, cancel=None, progress=None, depth=KL_DEPTH):
    """Labels for n nodes. `progress(fraction, detail)` is told how far it has got; `depth` None refines each pair
    of clusters over both whole clusters (the old, slow behaviour)."""
    gi, gj, gw = _arcs(pairs, weights)
    graph = {(int(a), int(b)): float(c) for a, b, c in zip(gi.tolist(), gj.tolist(), gw.tolist())}
    if n == 0:
        return np.array([],int), {"nodes":0,"arcs":0,"objective":0.0}
    report = (lambda f, detail: progress(f, detail)) if progress else None
    labels=_gaec(n,graph,cancel,(lambda f: report(0.4*f,"merging")) if report else None)
    score = lambda: float(gw[labels[gi]==labels[gj]].sum())
    initial=score()
    adjacency=[{} for _ in range(n)]
    for (a,b),w in graph.items():
        adjacency[a][b]=w; adjacency[b][a]=w
    completed=0
    for p in range(passes):
        checkpoint(cancel)
        before=score()
        _split_repelled(labels,adjacency,gi,gj,gw)
        cut = labels[gi] != labels[gj]
        la, lb = labels[gi[cut]], labels[gj[cut]]
        key = np.stack([np.minimum(la, lb), np.maximum(la, lb)], 1)
        nodes = np.concatenate([gi[cut], gj[cut]]); keys = np.concatenate([key, key])
        order = np.lexsort((nodes, keys[:, 1], keys[:, 0]))
        nodes, keys = nodes[order], keys[order]
        starts = np.r_[0, np.flatnonzero(np.any(np.diff(keys, axis=0) != 0, axis=1)) + 1] if len(keys) else np.zeros(0, int)
        ends = np.r_[starts[1:], len(keys)]
        for pi, (s, e) in enumerate(zip(starts.tolist(), ends.tolist())):
            if pi % 100 == 0:
                checkpoint(cancel)
                if report and pi % 2000 == 0:
                    report(0.4 + 0.6 * (p + pi / max(1, len(starts))) / max(1, passes), f"refining, pass {p+1}")
            a, b = int(keys[s, 0]), int(keys[s, 1])
            seeds = [int(v) for v in np.unique(nodes[s:e]) if labels[v] == a or labels[v] == b]
            if not seeds:
                continue
            if depth is None:
                members = np.flatnonzero((labels == a) | (labels == b))
                if not ((labels[members] == a).any() and (labels[members] == b).any()):
                    continue
                vertices = members.tolist()
            else:
                vertices = _window(seeds, adjacency, labels, a, b, depth)
            _kl_pair(labels,adjacency,a,b,vertices)
        completed+=1
        if score()<=before+1e-9:
            break
    # Canonical labels depend on the earliest member, not arbitrary solver IDs.
    _, first, inverse = np.unique(labels, return_index=True, return_inverse=True)
    rank = np.empty(len(first), np.int64); rank[np.argsort(first, kind="stable")] = np.arange(len(first))
    labels = rank[inverse.reshape(-1)]
    if report:
        report(1.0, "done")
    return labels, {"nodes":n,"arcs":len(graph),"clusters":int(len(first)),"initial_objective":initial,
                    "objective":score(),"refinement_passes":completed,"kl_depth":depth}
