"""Sparse signed correlation clustering: GAEC initialization and KL refinement.

The objective maximizes sum(w_ij * [label_i == label_j]). Negative edges
are repulsive; connected-components on positive edges is not this objective.
"""
from __future__ import annotations

import heapq
from collections import defaultdict

import numpy as np

from .types import checkpoint


def alignment_weight(angle, sigma=5.0):
    angle = np.asarray(angle, float)
    return np.where(angle <= 45.0, np.exp(-(angle / sigma)**2),
                    -5.0 * np.exp(-((90.0 - angle) / 15.0)**2))


def aggregate_graph(pairs, weights):
    graph = defaultdict(float)
    for (a, b), w in zip(pairs, weights):
        a, b = int(a), int(b)
        if a != b and np.isfinite(w):
            graph[min(a,b), max(a,b)] += float(w)
    return {e: w for e, w in graph.items() if abs(w) > 1e-14}


def objective(labels, graph):
    return sum(w for (a,b), w in graph.items() if labels[a] == labels[b])


def _gaec(n, graph, cancel=None):
    adjacent = [dict() for _ in range(n)]
    heap = []
    parent = np.arange(n)
    for (a,b), w in graph.items():
        adjacent[a][b] = adjacent[b][a] = w
        if w > 1e-12:
            heapq.heappush(heap, (-w,a,b))
    pops=0
    while heap:
        pops+=1
        if pops%1000==0:
            checkpoint(cancel)
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
    """Allow non-improving intermediate moves; commit only the best positive prefix."""
    current = {v: int(labels[v]) for v in vertices}
    gains, heap = {}, []
    for v in vertices:
        own = current[v]
        gain = sum(w if current.get(u) != own else -w for u,w in adjacency[v].items() if u in current)
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


def correlation_cluster(n, pairs, weights, passes=8, cancel=None):
    graph=aggregate_graph(pairs,weights)
    if n == 0:
        return np.array([],int), {"nodes":0,"arcs":0,"objective":0.0}
    labels=_gaec(n,graph,cancel)
    initial=objective(labels,graph)
    adjacency=[{} for _ in range(n)]
    for (a,b),w in graph.items():
        adjacency[a][b]=w; adjacency[b][a]=w
    completed=0
    for _ in range(passes):
        checkpoint(cancel)
        before=objective(labels,graph)
        # Repulsion inside a cluster can justify splitting off a node.
        next_label=int(labels.max())+1
        for v in range(n):
            internal=sum(w for u,w in adjacency[v].items() if labels[u]==labels[v])
            if internal < -1e-10:
                labels[v]=next_label; next_label+=1
        pairs_now=sorted({tuple(sorted((int(labels[a]),int(labels[b])))) for a,b in graph if labels[a]!=labels[b]})
        groups=defaultdict(set)
        for v,label in enumerate(labels):
            groups[int(label)].add(v)
        for pi,(a,b) in enumerate(pairs_now):
            if pi%100==0:
                checkpoint(cancel)
            A,B=groups[a],groups[b]
            if not A or not B:
                continue
            vertices=sorted(A|B)
            gain=_kl_pair(labels,adjacency,a,b,vertices)
            if gain>1e-10:
                groups[a]={v for v in vertices if labels[v]==a}
                groups[b]={v for v in vertices if labels[v]==b}
        completed+=1
        if objective(labels,graph)<=before+1e-9:
            break
    # Canonical labels depend on the earliest member, not arbitrary solver IDs.
    roots=sorted(set(map(int,labels)),key=lambda l:int(np.flatnonzero(labels==l)[0]))
    mapping={r:i for i,r in enumerate(roots)}
    labels=np.array([mapping[int(l)] for l in labels],np.int64)
    return labels, {"nodes":n,"arcs":len(graph),"clusters":len(roots),"initial_objective":initial,
                    "objective":objective(labels,graph),"refinement_passes":completed}

