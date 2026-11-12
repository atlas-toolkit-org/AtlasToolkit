"""Two-stage strand formation and deterministic reliable flowline extraction."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

import numpy as np

from .clustering import alignment_weight, correlation_cluster
from .mesh import unit
from .types import checkpoint, progress_to


@dataclass
class Flowline:
    vertices: np.ndarray
    edges: np.ndarray
    strand: int = -1

    @property
    def closed(self):
        return len(self.vertices)>2 and self.vertices[0]==self.vertices[-1]


def graph_chains(edges, edge_ids, breaks=None):
    """Edge-disjoint trails, stopping at branches and explicitly protected vertices."""
    edge_ids=np.asarray(edge_ids,np.int64)
    adjacent=defaultdict(list)
    for ei in edge_ids:
        a,b=map(int,edges[ei])
        adjacent[a].append(int(ei)); adjacent[b].append(int(ei))
    breaks=set() if breaks is None else set(breaks)
    used=set(); curves=[]; ids=[]
    def walk(start, ei):
        path=[start]; chain=[]
        while ei not in used:
            used.add(ei); chain.append(ei)
            a,b=map(int,edges[ei])
            v=b if path[-1]==a else a
            path.append(v)
            if v==start or v in breaks or len(adjacent[v])!=2:
                break
            choices=[e for e in adjacent[v] if e not in used]
            if not choices:
                break
            ei=choices[0]
        curves.append(np.array(path,np.int64)); ids.append(np.array(chain,np.int64))
    for v in sorted(adjacent):
        if len(adjacent[v])!=2 or v in breaks:
            for ei in sorted(adjacent[v]):
                if ei not in used:
                    walk(v,ei)
    for ei in sorted(map(int,edge_ids)):
        if ei not in used:
            walk(int(edges[ei,0]),ei)
    return curves,ids


def trim_edges(mesh, crease_angle):
    topo=mesh.topology
    EF=topo.edge_faces
    boundary=EF[:,1]<0
    score=np.zeros(len(EF))
    inside=~boundary
    score[inside]=np.degrees(np.arccos(np.clip((topo.face_normals[EF[inside,0]]*topo.face_normals[EF[inside,1]]).sum(1),-1,1)))
    return boundary | (score>=crease_angle)


def projectivity(points, normals, dmax=20.0):
    """Paper §5.2, using actual averages and angle to the osculating plane."""
    P=np.asarray(points,float); N=np.asarray(normals,float)
    closed=len(P)>2 and np.linalg.norm(P[0]-P[-1])<1e-12
    if closed:
        P=P[:-1]; N=N[:-1]
    if len(P)<3:
        return 0.0
    k=min(5,(len(P)-1)//2)
    if k<1:
        return 0.0
    if closed:
        X=np.concatenate([P[-k:],P,P[:k]])
        NN=np.concatenate([N[-k:],N,N[:k]])
        starts=range(len(P))
    else:
        X,NN=P,N; starts=range(len(P)-2*k)
    planarity=[]; geodesicity=[]
    for i in starts:
        a,centre,b=X[i],X[i+k],X[i+2*k]
        base=np.cross(centre-a,b-a)
        size=np.linalg.norm(base)
        if size < 1e-12*max(np.linalg.norm(b-a)**2,1e-20):
            # Straight segments have no unique osculating plane and zero bending cost.
            continue
        base/=size
        ns=np.cross(X[i+1:i+2*k]-a,b-a)
        valid=np.linalg.norm(ns,axis=1)>1e-15
        ns=unit(ns[valid])
        if len(ns):
            planarity.extend(np.degrees(np.arccos(np.clip(np.abs(ns@base),0,1))).tolist())
        geodesicity.append(min(15.0,float(np.degrees(np.arcsin(np.clip(abs(base@NN[i+k]),0,1))))))
    return (float(np.mean(planarity)) if planarity else 0.0)+(dmax/15.0)*(float(np.mean(geodesicity)) if geodesicity else 0.0)


def _angle(a,b):
    return float(np.degrees(np.arccos(np.clip(abs(float(unit(a)@unit(b))),0,1))))


def _tangent(mesh, flow, end):
    ids=flow.vertices if end==flow.vertices[0] else flow.vertices[::-1]
    P=mesh.vertices[ids]
    distances=np.cumsum(np.linalg.norm(np.diff(P,axis=0),axis=1))
    j=min(len(P)-1,int(np.searchsorted(distances,5*mesh.topology.lengths.mean()))+1)
    direction=P[j]-P[0]
    normal=mesh.topology.normals[end]
    return unit(direction-np.dot(direction,normal)*normal)


def _reliable_paths(mesh, edge_labels, keep, dmax, cancel):
    """Cycles first; remaining trees use weighted diameters (longest tree paths)."""
    import networkx as nx
    topo=mesh.topology
    out=[]
    for label in sorted(set(map(int,edge_labels[keep]))):
        checkpoint(cancel)
        edge_ids=np.flatnonzero(keep & (edge_labels==label))
        G=nx.Graph()
        for ei in edge_ids:
            a,b=map(int,topo.edges[ei])
            G.add_edge(a,b,edge=int(ei),weight=float(topo.lengths[ei]))
        while G.number_of_edges():
            components=list(nx.connected_components(G))
            for vertices in sorted(components,key=min):
                sub=G.subgraph(vertices)
                if not sub.number_of_edges():
                    G.remove_nodes_from(list(vertices)); continue
                cycles=nx.cycle_basis(sub,root=min(vertices))
                if cycles:
                    scored=[]
                    for c in cycles:
                        c=np.array(c+[c[0]],np.int64)
                        cost=projectivity(mesh.vertices[c],topo.normals[c],dmax)
                        scored.append((cost,-len(c),tuple(c),c))
                    path=min(scored,key=lambda x:x[:3])[3]
                else:
                    root=min(vertices)
                    distance=nx.single_source_dijkstra_path_length(sub,root,weight="weight")
                    a=max(distance,key=lambda v:(distance[v],-v))
                    distance,paths=nx.single_source_dijkstra(sub,a,weight="weight")
                    b=max(distance,key=lambda v:(distance[v],-v))
                    path=np.asarray(paths[b],np.int64)
                es=np.array([G[a][b]["edge"] for a,b in zip(path[:-1],path[1:])],np.int64)
                out.append(Flowline(path,es,label))
                G.remove_edges_from(zip(path[:-1],path[1:]))
                G.remove_nodes_from([v for v in list(G.nodes) if G.degree[v]==0])
    # A spiral revisits an opposite edge family before closing; expose its turns separately.
    opposite=defaultdict(set)
    for a,b in topo.opposite:
        opposite[int(a)].add(int(b)); opposite[int(b)].add(int(a))
    split=[]
    for flow in out:
        seen=set(); start=0
        for j,ei in enumerate(flow.edges):
            if opposite[int(ei)] & seen and j>start+1:
                split.append(Flowline(flow.vertices[start:j+1],flow.edges[start:j],flow.strand))
                start=j; seen=set()
            seen.add(int(ei))
        if start<len(flow.edges):
            split.append(Flowline(flow.vertices[start:],flow.edges[start:],flow.strand))
    return sorted(split,key=lambda f:(f.strand,int(f.edges[0])))


def compute_flowlines(mesh, options, progress=None, cancel=None, reliable_sigma=7.5):
    topo=mesh.topology; V=mesh.vertices; E=topo.edges; N=topo.normals
    trim=trim_edges(mesh,options.crease_angle)
    pairs=[]; weights=[]
    progress_to(progress,0.12,"building signed edge-affinity graph",cancel)
    for v,incident in enumerate(topo.vertex_edges):
        if v%4000==0: checkpoint(cancel)
        direction=V[E[incident,1]]-V[E[incident,0]]
        direction=unit(direction-(direction@N[v])[:,None]*N[v])
        for i,j in combinations(range(len(incident)),2):
            angle=_angle(direction[i],direction[j])
            pairs.append((incident[i],incident[j])); weights.append(float(alignment_weight(angle)))
    for a,b in topo.opposite:
        angle=_angle(V[E[a,1]]-V[E[a,0]],V[E[b,1]]-V[E[b,0]])
        pairs.append((a,b)); weights.append(0.05*np.exp(-(angle/5)**2))
    labels,first_info=correlation_cluster(len(E),pairs,weights,options.clustering_passes,cancel)
    protected=set(np.flatnonzero(np.array([len(es) for es in topo.vertex_edges])!=4))
    protected.update(map(int,E[trim].ravel()))
    initial=[]
    for label in sorted(set(map(int,labels))):
        ids=np.flatnonzero((labels==label)&~trim)
        curves,es=graph_chains(E,ids,protected)
        initial.extend(Flowline(c,e,label) for c,e in zip(curves,es))
    progress_to(progress,0.28,f"clustering {len(initial)} initial flowlines",cancel)
    owners=np.full(len(E),-1,int)
    at_vertex=defaultdict(list); at_end=defaultdict(list)
    for fi,f in enumerate(initial):
        owners[f.edges]=fi
        for v in set(map(int,f.vertices)):
            at_vertex[v].append(fi)
        if not f.closed:
            at_end[int(f.vertices[0])].append(fi); at_end[int(f.vertices[-1])].append(fi)
    arcs=defaultdict(float)
    for v,flows in at_end.items():
        tangents={fi:_tangent(mesh,initial[fi],v) for fi in flows}
        for a,b in combinations(sorted(set(flows)),2):
            arcs[a,b]+=float(alignment_weight(_angle(tangents[a],tangents[b]),reliable_sigma))
    parallel=defaultdict(float)
    for a,b in topo.opposite:
        fa,fb=owners[a],owners[b]
        if fa<0 or fb<0 or fa==fb: continue
        angle=_angle(V[E[a,1]]-V[E[a,0]],V[E[b,1]]-V[E[b,0]])
        pair=tuple(sorted((int(fa),int(fb))))
        parallel[pair]+=np.exp(-(angle/5)**2)
    for (a,b),s in parallel.items():
        arcs[a,b]+=2*s/max(1,min(len(initial[a].edges),len(initial[b].edges)))
    crossing=set()
    for v,flows in at_vertex.items():
        for a,b in combinations(sorted(set(flows)),2):
            f,g=initial[a],initial[b]
            fa=(v!=f.vertices[0] and v!=f.vertices[-1]) or f.closed
            fb=(v!=g.vertices[0] and v!=g.vertices[-1]) or g.closed
            if fa and fb:
                crossing.add((a,b))
    for pair in crossing:
        arcs[pair]=-25.0
    reliable,second_info=correlation_cluster(len(initial),list(arcs),list(arcs.values()),options.clustering_passes,cancel)
    edge_labels=np.full(len(E),-1,int)
    for fi,f in enumerate(initial):
        edge_labels[f.edges]=reliable[fi]
    progress_to(progress,0.40,"extracting closed loops and long reliable flowlines",cancel)
    flows=_reliable_paths(mesh,edge_labels,~trim,options.dmax,cancel)
    return flows,trim,{"edge_clustering":first_info,"flowline_clustering":second_info,
                       "initial_flowlines":len(initial),"reliable_flowlines":len(flows),
                       "reliable_sigma":reliable_sigma,"trim_edges":int(trim.sum())}
