"""Public extraction entry point and camera-independent content cache."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .flowlines import compute_flowlines, graph_chains
from .io import load_network, save_network
from .mesh import alignment_report, load_mesh, prepare_mesh
from .network import simplify_network
from .metrics import memory_snapshot
from .regularize import regularize
from .types import CurveNetwork, FlowRepError, FlowRepOptions, PolygonMesh, checkpoint, progress_to


def extract_network(mesh: PolygonMesh | str | Path, options=None, progress=None, cancel=None):
    options=options or FlowRepOptions()
    start=time.perf_counter(); times={}; memory={"start":memory_snapshot()}
    if not isinstance(mesh,PolygonMesh):
        mesh=load_mesh(mesh)
    source=str(Path(mesh.source).resolve()) if mesh.source else ""
    original_vertices=len(mesh.vertices); original_faces=len(mesh.faces)
    key=hashlib.sha256()
    key.update(b"atlas-flowrep-0.1.0-extract-2-annulus")
    key.update(mesh.vertices.tobytes())
    for face in mesh.faces:
        key.update(np.asarray([len(face),*face],np.int64).tobytes())
    config=asdict(options); config.pop("cache_dir")
    if options.prepare=="remesh" or (options.prepare=="auto" and sum(len(f)==4 for f in mesh.faces)/len(mesh.faces)<0.5):
        key.update(b"nim-normalized-purequad-v3")
    key.update(json.dumps(config,sort_keys=True).encode())
    digest=key.hexdigest()
    cache=Path(options.cache_dir)/f"network-{digest}.npz" if options.cache_dir else None
    checkpoint(cancel)
    if cache and cache.exists():
        network=load_network(cache)
        network.metadata["source_mesh"]=source
        network.diagnostics["cache_hit"]=True
        progress_to(progress,1.0,"loaded cached 3D curve network",cancel)
        return network
    t=time.perf_counter()
    mesh=prepare_mesh(mesh,options,progress,cancel)
    topo=mesh.topology
    times["prepare"]=time.perf_counter()-t
    memory["after_prepare"]=memory_snapshot()
    progress_to(progress,0.09,"checking curvature alignment",cancel)
    alignment=alignment_report(mesh)
    t=time.perf_counter()
    flows,trim,flow_info=compute_flowlines(mesh,options,progress,cancel)
    times["flowlines"]=time.perf_counter()-t
    memory["after_flowlines"]=memory_snapshot()
    t=time.perf_counter()
    state,info=simplify_network(mesh,flows,trim,options,progress,cancel)
    times["network"]=time.perf_counter()-t
    memory["after_network"]=memory_snapshot()
    if not info["coverage_complete"] and not options.relaxed_declutter:
        raise FlowRepError(f"network coverage is incomplete: {info['max_error_deg']:.2f}° > {options.dmax:g}°; "
                           "increase mesh resolution or remesh with better curvature alignment")
    selected=list(map(int,np.flatnonzero(state.selected)))
    curves=[state.active_vertices(fi).copy() for fi in selected]
    edge_ids=[flows[fi].edges[state.ranges[fi,0]:state.ranges[fi,1]].copy() for fi in selected]
    kinds=["flowline"]*len(curves); strands=[flows[fi].strand for fi in selected]
    tc,te=graph_chains(topo.edges,np.flatnonzero(trim))
    curves+=tc; edge_ids+=te; kinds+=["trim"]*len(tc); strands+=[-1]*len(tc)
    t=time.perf_counter()
    positions=mesh.vertices.copy()
    regularization={"accepted":False,"iterations":0}
    if options.regularize_iterations and curves:
        progress_to(progress,0.88,"regularizing and projecting curves to the source surface",cancel)
        positions,normals,regularization=regularize(mesh,curves,state.trim_vertices,options.regularize_iterations,cancel)
        state.ev.positions=positions; state.ev.N=normals
        measures=[state.ev.measure(r.faces,state.blocked) for r in state.regions.values()]
        after=max((r.error for r in measures),default=0.0)
        bound=options.dmax*1.2 if options.relaxed_declutter else options.dmax
        regularization["post_max_error_deg"]=after
        regularization["accepted"]=after<=bound+1e-8
        if not regularization["accepted"]:
            positions=mesh.vertices.copy()
            regularization["rollback_reason"]="regularization would violate the descriptiveness bound"
        else:
            info["max_error_deg"]=after
            info["region_errors_deg"]=[r.error for r in measures]
            info["coverage_complete"]=after<=options.dmax+1e-8
    times["regularize"]=time.perf_counter()-t
    memory["after_regularize"]=memory_snapshot()
    times["total"]=time.perf_counter()-start
    info.update(flow_info); info.update(alignment)
    info["implementation_revision"]=2
    info.update(input_vertices=original_vertices,input_faces=original_faces,
                prepared_vertices=len(mesh.vertices),prepared_faces=len(mesh.faces),
                components=topo.components,boundary_edges=int((topo.edge_faces[:,1]<0).sum()),
                curve_count=len(curves),trim_curves=len(tc),regularization=regularization,timings=times,
                memory_at_stage_boundaries=memory,cache_hit=False)
    metadata={"source_mesh":source,"source_units":"model units","algorithm":"FlowRep independent reimplementation",
              "paper_doi":"10.1145/3072959.3073639","tool":"flowrep-sketch","tool_version":"0.1.0",
              "options":config,"preparation":mesh.metadata,"content_key":digest}
    network=CurveNetwork(positions,curves,kinds,edge_ids,strands,info,metadata)
    if cache:
        save_network(network,cache)
    progress_to(progress,1.0,f"extracted {len(curves)} curves",cancel)
    return network
