"""Face-region descriptiveness and transactional FlowRep network simplification."""
from __future__ import annotations

import heapq
from collections import defaultdict, deque
from dataclasses import dataclass
from itertools import combinations

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .flowlines import projectivity
from .mesh import unit
from .types import checkpoint, progress_to


def predict_normal(n1, n2, distance_from_first, distance_from_second):
    """Arc-length interpolation: exactly n1 at the first endpoint and n2 at the second."""
    a=np.asarray(distance_from_first,float)
    b=np.asarray(distance_from_second,float)
    return unit((b[...,None]*n1+a[...,None]*n2)/np.maximum((a+b)[...,None],1e-30))


def percentile90(values):
    """NumPy's default linear quantile without its large small-array dispatch cost."""
    ordered=np.sort(values)
    position=0.9*(len(ordered)-1)
    i=int(position); j=min(i+1,len(ordered)-1)
    return float(ordered[i]+(ordered[j]-ordered[i])*(position-i))


@dataclass
class Region:
    faces: np.ndarray
    vertices: np.ndarray
    error: float
    boundary_cycles: int
    interior_vertices: int
    predicted_vertices: int
    dominance_error: float = 0.0


class CoverageEvaluator:
    def __init__(self, mesh, flows, cancel=None):
        self.mesh,self.flows,self.topo,self.cancel=mesh,flows,mesh.topology,cancel
        self.N=self.topo.normals
        self.positions=mesh.vertices
        self.vertex_flows=[[] for _ in mesh.vertices]
        for fi,f in enumerate(flows):
            for v in set(map(int,f.vertices)):
                self.vertex_flows[v].append(fi)
        self.face_vertices=[np.asarray(f,np.int64) for f in mesh.faces]
        offsets=np.cumsum([0]+[len(f) for f in mesh.faces])
        self.face_halfedges=[np.arange(a,b,dtype=np.int64) for a,b in zip(offsets[:-1],offsets[1:])]
        self._last_blocked=None
        self._anchors=None

    def network_anchors(self,blocked):
        if self._last_blocked is not blocked:
            anchors=np.zeros(len(self.mesh.vertices),bool)
            anchors[self.topo.edges[blocked].ravel()]=True
            self._last_blocked=blocked
            self._anchors=anchors
        return self._anchors

    def partition(self, blocked, faces=None):
        T=self.topo
        if faces is None:
            faces=np.arange(len(self.mesh.faces),dtype=np.int64)
        faces=np.asarray(faces,np.int64)
        if not len(faces):
            return []
        local=np.full(len(self.mesh.faces),-1,np.int64)
        local[faces]=np.arange(len(faces))
        D=T.dual_faces
        valid=(~blocked[T.dual_edges])&(local[D[:,0]]>=0)&(local[D[:,1]]>=0)
        a,b=local[D[valid,0]],local[D[valid,1]]
        graph=coo_matrix((np.ones(len(a),np.int8),(a,b)),shape=(len(faces),len(faces))).tocsr()
        _,labels=connected_components(graph,directed=False)
        order=np.argsort(labels,kind="stable")
        cuts=np.r_[0,np.flatnonzero(np.diff(labels[order]))+1,len(faces)]
        return [faces[order[a:b]] for a,b in zip(cuts[:-1],cuts[1:])]

    def _boundary(self, faces, blocked):
        T=self.topo
        inside=np.zeros(len(self.mesh.faces),bool); inside[faces]=True
        half=np.concatenate([self.face_halfedges[int(f)] for f in faces])
        twin=T.he_twin[half]
        is_boundary=blocked[T.he_edge[half]]|(twin<0)
        valid=twin>=0
        is_boundary[valid]|=~inside[T.he_face[twin[valid]]]
        boundary=half[is_boundary]
        boundary_set=set(map(int,boundary))
        used=set(); cycles=0
        for start in boundary:
            start=int(start)
            if start in used:
                continue
            cycles+=1; h=start
            for _ in range(len(T.he_vertices)+1):
                if h in used:
                    break
                used.add(h)
                nxt=int(T.he_next[h])
                while nxt not in boundary_set:
                    twin=int(T.he_twin[nxt])
                    if twin<0:
                        break
                    nxt=int(T.he_next[twin])
                if nxt not in boundary_set:
                    break
                h=nxt
        vertices=np.unique(np.r_[T.he_vertices[boundary],T.he_vertices[T.he_next[boundary]]])
        return vertices,cycles

    def measure(self, faces, blocked, crossing=None):
        checkpoint(self.cancel)
        vertices=np.unique(np.concatenate([self.face_vertices[int(f)] for f in faces]))
        # Planar regions need no prediction or boundary tracing. Reusing the
        # anchor mask also avoids full-mesh allocation for every tiny patch.
        anchors=self.network_anchors(blocked)
        interior=vertices[~anchors[vertices]]
        fn=self.topo.face_normals[faces]
        mean=unit(fn.sum(0))
        planar=bool(np.all(fn@mean>=np.cos(np.radians(0.75))))
        if planar:
            return Region(faces,vertices,0.0,-1,len(interior),len(interior))
        boundary,cycles=self._boundary(faces,blocked)
        inside=np.zeros(len(self.mesh.vertices),bool); inside[vertices]=True
        anchors=np.zeros(len(inside),bool); anchors[boundary]=True
        interior=vertices[~anchors[vertices]]
        # This topological rule applies even to a one-quad-wide annulus with
        # no interior vertices. Otherwise parallel rings could incorrectly
        # replace a connected descriptive network.
        if cycles!=1:
            return Region(faces,vertices,90.0,cycles,len(interior),0,90.0)
        if not len(interior):
            return Region(faces,vertices,0.0,cycles,0,0)
        candidate=sorted({fi for v in interior for fi in self.vertex_flows[int(v)]})
        error=np.zeros(len(inside)); seen=np.zeros(len(inside),bool)
        cross_error=np.zeros(len(inside)); cross_seen=np.zeros(len(inside),bool)
        for fi in candidate:
            f=self.flows[fi]; ids=f.vertices
            if f.closed:
                base=ids[:-1]
                ai=np.flatnonzero(anchors[base])
                if not len(ai):
                    continue
                first=int(ai[0])
                ids=np.r_[base[first:],base[:first],base[first]]
            ai=np.flatnonzero(anchors[ids])
            if len(ai)<2:
                continue
            P=self.positions[ids]
            arc=np.r_[0.0,np.cumsum(np.linalg.norm(np.diff(P,axis=0),axis=1))]
            for a,b in zip(ai[:-1],ai[1:]):
                if b<=a+1:
                    continue
                loc=np.arange(a+1,b)
                vv=ids[loc]
                keep=inside[vv]&~anchors[vv]
                loc,vv=loc[keep],vv[keep]
                if not len(vv):
                    continue
                prediction=predict_normal(self.N[ids[a]],self.N[ids[b]],arc[loc]-arc[a],arc[b]-arc[loc])
                angles=np.degrees(np.arccos(np.clip((prediction*self.N[vv]).sum(1),-1,1)))
                np.maximum.at(error,vv,angles); seen[vv]=True
                if crossing is not None and fi in crossing:
                    np.maximum.at(cross_error,vv,angles); cross_seen[vv]=True
        sample=interior[seen[interior]]
        # The paper omits isolated unpredicted vertices. A wholly unpredicted patch
        # is explicitly uncovered instead of receiving a fictitious zero error.
        dc=percentile90(error[sample]) if len(sample) else 90.0
        cs=interior[cross_seen[interior]]
        dominance=percentile90(cross_error[cs]) if len(cs) else 0.0
        return Region(faces,vertices,dc,cycles,len(interior),len(sample),dominance)


class NetworkState:
    def __init__(self, evaluator, trim, selected=None):
        self.ev=evaluator; self.flows=evaluator.flows; self.trim=np.asarray(trim,bool)
        self.selected=np.ones(len(self.flows),bool) if selected is None else np.array(selected,bool)
        self.ranges=np.array([[0,len(f.edges)] for f in self.flows],np.int64).reshape(-1,2)
        self.counts=self.trim.astype(np.int64)
        for fi in np.flatnonzero(self.selected):
            self.counts[self.flows[fi].edges]+=1
        self.trim_vertices=np.zeros(len(evaluator.mesh.vertices),bool)
        self.trim_vertices[evaluator.topo.edges[self.trim].ravel()]=True
        self.labels=np.full(len(evaluator.mesh.faces),-1,int)
        self.regions={}
        self.next_id=0
        self.projectivity_cache={}
        self.rebuild()

    @property
    def blocked(self):
        return self.counts>0

    @property
    def max_error(self):
        return max((r.error for r in self.regions.values()),default=0.0)

    def rebuild(self):
        self.regions={}
        blocked=self.blocked
        for faces in self.ev.partition(blocked):
            rid=self.next_id; self.next_id+=1
            self.labels[faces]=rid
            self.regions[rid]=self.ev.measure(faces,blocked)

    def active_vertices(self, fi, ranges=None):
        a,b=(self.ranges if ranges is None else ranges)[fi]
        return self.flows[fi].vertices[a:b+1]

    def propose(self, remove=(), add=(), shorten=True):
        selected=self.selected.copy(); ranges=self.ranges.copy(); counts=self.counts.copy()
        E=self.ev.topo.edges
        degree=np.bincount(E[counts>0].ravel(),minlength=len(self.ev.mesh.vertices)).astype(int)
        changed=set()
        queue=deque(); queued=set()
        def enqueue_vertices(ids):
            for v in ids:
                for f in self.ev.vertex_flows[int(v)]:
                    if selected[f] and f not in queued:
                        queue.append(f); queued.add(f)
        def update(fi,a,b):
            olda,oldb=ranges[fi]
            old=self.flows[fi].edges[olda:oldb] if selected[fi] else np.array([],int)
            new=self.flows[fi].edges[a:b]
            removed=np.setdiff1d(old,new,assume_unique=True)
            added=np.setdiff1d(new,old,assume_unique=True)
            for es,sign in ((removed,-1),(added,1)):
                before=counts[es]>0
                counts[es]+=sign
                after=counts[es]>0
                delta=after.astype(int)-before.astype(int)
                if len(es):
                    np.add.at(degree,E[es].ravel(),np.repeat(delta,2))
                changed.update(map(int,es))
            selected[fi]=b>a; ranges[fi]=[a,b]
            if len(removed):
                enqueue_vertices(E[removed].ravel())
        for fi in remove:
            if selected[fi]:
                update(int(fi),0,0)
        for fi in add:
            if not selected[fi]:
                update(int(fi),0,len(self.flows[fi].edges))
        if shorten:
            if not remove and not add:
                queue.extend(map(int,np.flatnonzero(selected)))
            while queue:
                fi=queue.popleft(); queued.discard(fi)
                if not selected[fi] or self.flows[fi].closed:
                    continue
                a,b=ranges[fi]; ids=self.flows[fi].vertices
                olda,oldb=a,b
                if not self.trim_vertices[ids[a]] and degree[ids[a]]<3:
                    candidates=np.flatnonzero(self.trim_vertices[ids[a:b+1]]|(degree[ids[a:b+1]]>=3))
                    if not len(candidates):
                        update(fi,0,0); continue
                    a+=int(candidates[0])
                if b>a and not self.trim_vertices[ids[b]] and degree[ids[b]]<3:
                    candidates=np.flatnonzero(self.trim_vertices[ids[a:b+1]]|(degree[ids[a:b+1]]>=3))
                    if not len(candidates):
                        update(fi,0,0); continue
                    b=a+int(candidates[-1])
                if a!=olda or b!=oldb:
                    update(fi,a,b)
        return Proposal(selected,ranges,counts,np.array(sorted(changed),int))

    def assess(self, proposal, crossing=None):
        if not len(proposal.changed_edges):
            return [],[],0.0
        EF=self.ev.topo.edge_faces[proposal.changed_edges].ravel()
        affected=sorted(set(map(int,self.labels[EF[EF>=0]])))
        faces=np.concatenate([self.regions[r].faces for r in affected])
        blocked=proposal.counts>0
        regions=[self.ev.measure(f,blocked,crossing) for f in self.ev.partition(blocked,faces)]
        dominance=max((r.dominance_error for r in regions),default=0.0)
        return affected,regions,dominance

    def commit(self, proposal, affected, regions):
        self.selected,self.ranges,self.counts=proposal.selected,proposal.ranges,proposal.counts
        for rid in affected:
            del self.regions[rid]
        for region in regions:
            rid=self.next_id; self.next_id+=1
            self.labels[region.faces]=rid; self.regions[rid]=region


@dataclass
class Proposal:
    selected: np.ndarray
    ranges: np.ndarray
    counts: np.ndarray
    changed_edges: np.ndarray


def _compatible_composites(state):
    import networkx as nx
    end=defaultdict(list); V=state.ev.mesh.vertices
    for fi,f in enumerate(state.flows):
        if f.closed:
            continue
        end[int(f.vertices[0])].append((fi,unit(V[f.vertices[1]]-V[f.vertices[0]])))
        end[int(f.vertices[-1])].append((fi,unit(V[f.vertices[-2]]-V[f.vertices[-1]])))
    G=nx.Graph(); compatible={}
    for v,items in sorted(end.items()):
        candidates=sorted((float(da@db),a,b) for (a,da),(b,db) in combinations(items,2) if a!=b and da@db<0)
        paired=set()
        for dot,a,b in candidates:
            if a not in paired and b not in paired:
                G.add_edge(a,b); paired.update((a,b))
                compatible[a,v]=b; compatible[b,v]=a
    groups=[tuple(sorted(c)) for c in nx.connected_components(G) if len(c)>1]
    return sorted(groups),compatible


def _crossing_set(state, group):
    own=set(group)
    return {j for fi in group for v in state.active_vertices(fi) for j in state.ev.vertex_flows[int(v)]
            if j not in own and state.flows[j].strand!=state.flows[fi].strand}


def _geometric_cost(state, group, compatible):
    group=set(group); V=state.ev.positions; N=state.ev.N
    costs=[]; lengths=[]; endpoints=[]
    for fi in sorted(group):
        ids=state.active_vertices(fi)
        if len(ids)<2:
            continue
        key=(int(fi),*map(int,state.ranges[fi]))
        if key not in state.projectivity_cache:
            state.projectivity_cache[key]=(projectivity(V[ids],N[ids],state.dmax),
                                           float(np.linalg.norm(np.diff(V[ids],axis=0),axis=1).sum()))
        pure_cost,length=state.projectivity_cache[key]
        costs.append(pure_cost); lengths.append(length)
        if ids[0]!=ids[-1]:
            for v in (int(ids[0]),int(ids[-1])):
                other=compatible.get((fi,v))
                if other in group:
                    continue
                if len(group)==1 and other is not None and state.selected[other]:
                    continue
                endpoints.append(30.0 if state.trim_vertices[v] else 60.0)
    return float(np.average(costs,weights=np.maximum(lengths,1e-30)))+sum(endpoints) if costs else 0.0


def simplify_network(mesh, flows, trim, options, progress=None, cancel=None):
    ev=CoverageEvaluator(mesh,flows,cancel)
    state=NetworkState(ev,trim,np.zeros(len(flows),bool)); state.dmax=options.dmax
    initial_error=state.max_error
    refinement=0
    while state.max_error>options.dmax+1e-8 and refinement<options.max_refinements:
        checkpoint(cancel)
        bad={rid for rid,r in state.regions.items() if r.error>options.dmax}
        add=[]
        for fi,f in enumerate(flows):
            if state.selected[fi]:
                continue
            incident=ev.topo.edge_faces[f.edges].ravel()
            if any(int(state.labels[v]) in bad for v in incident if v>=0):
                add.append(fi)
        if not add:
            break
        # Joint insertion also handles non-separating loops on surfaces of positive genus.
        proposal=state.propose(add=add,shorten=False)
        affected,regions,_=state.assess(proposal)
        state.commit(proposal,affected,regions); refinement+=1
    dense_count=int(state.selected.sum()); dense_error=state.max_error
    groups,compatible=_compatible_composites(state)
    candidates=[(i,) for i in range(len(flows))]+groups
    by_flow=[[] for _ in flows]
    for ci,g in enumerate(candidates):
        for fi in g:
            by_flow[fi].append(ci)
    heap=[]; revisions=np.zeros(len(candidates),int)
    evaluated=0; accepted=0; rejected=0
    def evaluate(ci):
        nonlocal evaluated
        group=tuple(f for f in candidates[ci] if state.selected[f])
        if not group or (len(candidates[ci])>1 and len(group)<2):
            return None
        proposal=state.propose(remove=group)
        affected,regions,D=state.assess(proposal,_crossing_set(state,group))
        cost=_geometric_cost(state,group,compatible)+360.0-D
        evaluated+=1
        return cost,proposal,affected,regions
    def queue(ci):
        revisions[ci]+=1
        value=evaluate(ci)
        if value is not None:
            heapq.heappush(heap,(-value[0],ci,int(revisions[ci])))
    for ci in range(len(candidates)):
        if ci%100==0:
            progress_to(progress,0.46+0.07*ci/max(1,len(candidates)),f"scoring curves {ci}/{len(candidates)}",cancel)
        queue(ci)
    while heap:
        checkpoint(cancel)
        _,ci,revision=heapq.heappop(heap)
        if revision!=revisions[ci]:
            continue
        value=evaluate(ci)
        if value is None:
            continue
        _,proposal,affected,regions=value
        if any(r.error>options.dmax+1e-8 for r in regions):
            rejected+=1; continue
        if np.array_equal(proposal.selected,state.selected) and np.array_equal(proposal.ranges,state.ranges):
            continue
        state.commit(proposal,affected,regions); accepted+=1
        near={fi for r in regions for v in r.vertices for fi in ev.vertex_flows[int(v)] if state.selected[fi]}
        updates={k for fi in near for k in by_flow[fi]}
        for k in sorted(updates):
            queue(k)
        if accepted%10==0:
            fraction=1-float(state.selected.sum())/max(1,dense_count)
            progress_to(progress,0.54+0.24*fraction,f"simplified to {int(state.selected.sum())} flowlines",cancel)
    # Local replacement by the nearest available parallel representative.
    owner=np.full(len(ev.topo.edges),-1,int)
    for fi,f in enumerate(flows): owner[f.edges]=fi
    neighbors=defaultdict(lambda:defaultdict(int))
    for a,b in ev.topo.opposite:
        fa,fb=int(owner[a]),int(owner[b])
        if fa>=0 and fb>=0 and fa!=fb and flows[fa].strand==flows[fb].strand:
            neighbors[fa][fb]+=1; neighbors[fb][fa]+=1
    def energy():
        total=0.0
        for fi in np.flatnonzero(state.selected):
            proposal=state.propose(remove=(int(fi),))
            _,_,D=state.assess(proposal,_crossing_set(state,(int(fi),)))
            total+=_geometric_cost(state,(int(fi),),compatible)+360.0-D
        return total
    swaps=0
    for iteration in range(options.local_passes):
        progress_to(progress,0.80+0.03*iteration/max(1,options.local_passes),"optimizing neighboring curve representatives",cancel)
        baseline=energy()
        changed=False
        for fi in list(map(int,np.flatnonzero(state.selected))):
            if not state.selected[fi]:
                continue
            for fj,_ in sorted(neighbors[fi].items(),key=lambda p:(-p[1],p[0]))[:2]:
                if state.selected[fj]:
                    continue
                proposal=state.propose(remove=(fi,),add=(fj,))
                affected,regions,_=state.assess(proposal)
                if not proposal.selected[fj] or any(r.error>options.dmax+1e-8 for r in regions):
                    continue
                # Evaluate the full context-dependent objective before committing a substitution.
                old=(state.selected,state.ranges,state.counts,state.labels.copy(),state.regions.copy(),state.next_id)
                state.commit(proposal,affected,regions)
                candidate=energy()
                if candidate<baseline-1e-7:
                    baseline=candidate; swaps+=1; changed=True; break
                state.selected,state.ranges,state.counts,state.labels,state.regions,state.next_id=old
        if not changed:
            break
    relaxed=0
    if options.relaxed_declutter:
        for fi in list(map(int,np.flatnonzero(state.selected))):
            # Only parallel neighbors within two average edges are candidates for the paper's relaxation.
            for fj in neighbors[fi]:
                if not state.selected[fj]:
                    continue
                from scipy.spatial import cKDTree
                distance=cKDTree(mesh.vertices[state.active_vertices(fi)]).query(mesh.vertices[state.active_vertices(fj)])[0]
                if float(np.median(distance))>2*ev.topo.lengths.mean():
                    continue
                proposal=state.propose(remove=(fi,))
                affected,regions,_=state.assess(proposal)
                bound=options.dmax*1.2
                if all(r.error<=bound+1e-8 for r in regions):
                    state.commit(proposal,affected,regions); relaxed+=1; break
    diagnostics={"initial_max_error_deg":initial_error,"dense_flowlines":dense_count,"dense_max_error_deg":dense_error,
                 "refinement_rounds":refinement,"selected_flowlines":int(state.selected.sum()),
                 "region_count":len(state.regions),"max_error_deg":state.max_error,
                 "coverage_complete":state.max_error<=options.dmax+1e-8,
                 "removals":accepted,"rejected_removals":rejected,"candidate_evaluations":evaluated,
                 "local_substitutions":swaps,"relaxed_removals":relaxed,
                 "unpredicted_interior_vertices":sum(r.interior_vertices-r.predicted_vertices for r in state.regions.values()),
                 "region_errors_deg":[r.error for r in state.regions.values()]}
    return state,diagnostics

