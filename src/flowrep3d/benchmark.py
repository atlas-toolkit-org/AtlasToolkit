"""Reference comparisons, stage timing, and resumable per-model evaluation."""
from __future__ import annotations

import os
import time
import traceback
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from .io import atomic_text, json_text, load_network, save_network
from .mesh import load_mesh
from .types import checkpoint


def evaluation_signature(src,flow_options,render_options,camera):
    config=asdict(flow_options); config.pop("cache_dir",None)
    request={"revision":"2-annulus-surface-manifold-reference","source":str(Path(src).resolve()),
             "source_sha256":hashlib.sha256(Path(src).read_bytes()).hexdigest(),
             "flow_options":config,"render_options":asdict(render_options),"camera":asdict(camera)}
    return hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()


def sample_network(network,max_points=6000):
    parts=[]; count=max(1,sum(len(c)-1 for c in network.curves))
    for P in network.polylines:
        lengths=np.linalg.norm(np.diff(P,axis=0),axis=1)
        total=float(lengths.sum())
        if total<=0: continue
        n=max(2,int(max_points*(len(P)-1)/count))
        arc=np.r_[0.0,np.cumsum(lengths)]
        u=np.linspace(0,total,n)
        parts.append(np.stack([np.interp(u,arc,P[:,j]) for j in range(3)],axis=1))
    return np.concatenate(parts) if parts else np.zeros((0,3))


def compare_networks(network,reference,diagonal):
    A,B=sample_network(network),sample_network(reference)
    if not len(A) or not len(B):
        return {"comparison_available":False}
    ab=cKDTree(B).query(A)[0]/diagonal
    ba=cKDTree(A).query(B)[0]/diagonal
    return {"comparison_available":True,"reference_curves":len(reference.curves),
            "generated_curves":len(network.curves),"reference_edges":reference.edge_count,
            "generated_edges":network.edge_count,
            "reference_junctions":len(reference.junctions),"generated_junctions":len(network.junctions),
            "chamfer_mean_pct_diagonal":float((ab.mean()+ba.mean())*50),
            "generated_to_reference_p95_pct":float(np.percentile(ab,95)*100),
            "reference_to_generated_p95_pct":float(np.percentile(ba,95)*100)}


def register_reference(reference,mesh):
    """Recover a normalized author's uniform scale/origin, with surface checks.

    Some supplied author outputs (notably the bunny) are centered and normalized
    independently of their input mesh. Never rotate or distort a reference, and
    never accept a transform just because its bounding box happens to match.
    """
    from .types import CurveNetwork
    import trimesh
    used=np.unique(np.concatenate(reference.curves)) if reference.curves else np.array([],int)
    if not len(used): return reference
    V=mesh.reference_vertices; P=reference.vertices[used]
    extent=np.ptp(V,axis=0); other=np.ptp(P,axis=0)
    if np.any(other<=mesh.diagonal*1e-10): return reference
    ratios=extent/other
    centre=(V.min(0)+V.max(0))/2; base=(P.min(0)+P.max(0))/2
    if ratios.max()/max(ratios.min(),1e-30)>1.05: return reference
    if abs(np.log(np.median(ratios)))<0.15 and np.linalg.norm(centre-base)<mesh.diagonal*0.1:
        return reference
    surface=trimesh.Trimesh(V,mesh.reference_triangles,process=False)
    samples=sample_network(reference,1200)
    _,before,_=trimesh.proximity.closest_point(surface,samples)
    if np.percentile(before,95)<mesh.diagonal*0.005: return reference
    initial=float(np.linalg.norm(extent)/np.linalg.norm(other)); scale=initial
    translation=centre-scale*base; pc=samples-samples.mean(0)
    denominator=float((pc*pc).sum())
    for _ in range(16):
        closest,_,_=trimesh.proximity.closest_point(surface,samples*scale+translation)
        new_scale=float((pc*(closest-closest.mean(0))).sum()/max(denominator,1e-30))
        if not initial*0.9<=new_scale<=initial*1.1: return reference
        new_translation=closest.mean(0)-new_scale*samples.mean(0)
        change=abs(scale-new_scale)*np.linalg.norm(other)+np.linalg.norm(translation-new_translation)
        scale,translation=new_scale,new_translation
        if change<mesh.diagonal*1e-8: break
    _,after,_=trimesh.proximity.closest_point(surface,samples*scale+translation)
    if np.percentile(after,95)>mesh.diagonal*0.01 or after.mean()>before.mean()*0.25:
        return reference
    registration={"applied":True,"method":"surface-verified uniform scale and translation; no rotation",
                  "scale":scale,"translation":translation.tolist(),
                  "before_p95_pct_diagonal":float(np.percentile(before,95)/mesh.diagonal*100),
                  "after_p95_pct_diagonal":float(np.percentile(after,95)/mesh.diagonal*100)}
    return CurveNetwork(reference.vertices*scale+translation,reference.curves,reference.kinds,
                        reference.edge_ids,reference.strand_ids,reference.diagnostics,
                        reference.metadata|{"reference_registration":registration})


def find_reference(mesh_path):
    p=Path(mesh_path)
    candidates=[p.parent/"network-regularized.obj",p.parent/"network.obj",p.with_name(p.stem+"_network.obj")]
    return next((q for q in candidates if q.exists()),None)


def peak_process_mb():
    from .metrics import memory_snapshot
    return memory_snapshot().get("peak_working_set_mb")


def evaluate_model(src,out_dir,flow_options,render_options,camera,progress=None):
    from .extract import extract_network
    from .render import render_network
    from .svg import write_svg
    import trimesh
    src=Path(src); out_dir=Path(out_dir); out_dir.mkdir(parents=True,exist_ok=True)
    start=time.perf_counter()
    result={"source":str(src.resolve()),"model":src.parent.name if src.parent.name!="." else src.stem,
            "evaluation_signature":evaluation_signature(src,flow_options,render_options,camera),
            "configuration":{"flow_options":asdict(flow_options),"render_options":asdict(render_options),
                             "camera":asdict(camera)}}
    try:
        mesh=load_mesh(src)
        extract_start=time.perf_counter()
        net=extract_network(mesh,flow_options,progress)
        extraction_call_seconds=time.perf_counter()-extract_start
        save_network(net,out_dir/"network.npz")
        save_network(net,out_dir/"network.obj")
        d=render_network(mesh,net,camera,options=render_options)
        write_svg(d,out_dir/"drawing.svg")
        # A camera-only redraw consumes the same persistent 3D network.
        repeat_start=time.perf_counter()
        second=render_network(mesh,net,camera,options=render_options)
        warm_seconds=time.perf_counter()-repeat_start
        sample=sample_network(net,2000)
        ref_mesh=trimesh.Trimesh(mesh.reference_vertices,mesh.reference_triangles,process=False)
        _,distance,_=trimesh.proximity.closest_point(ref_mesh,sample) if len(sample) else (None,np.array([0.0]),None)
        result.update(status="ok",extraction=net.diagnostics,render=d.stats,
                      extraction_call_seconds=extraction_call_seconds,
                      camera_only_redraw_seconds=warm_seconds,
                      surface_distance_p95_pct_diagonal=float(np.percentile(distance,95)/mesh.diagonal*100),
                      surface_distance_max_pct_diagonal=float(distance.max()/mesh.diagonal*100))
        reference=find_reference(src)
        if reference:
            author=register_reference(load_network(reference),mesh)
            result["reference_registration"]=author.metadata.get("reference_registration",{"applied":False})
            result["comparison"]=compare_networks(net,author,mesh.diagonal)
            ad=render_network(mesh,author,camera,options=render_options)
            write_svg(ad,out_dir/"author.svg")
        result["files"]={"network":"network.npz","drawing":"drawing.svg","author":"author.svg" if reference else None}
    except Exception as exc:
        result.update(status="error",error=f"{type(exc).__name__}: {exc}",traceback=traceback.format_exc())
    result["elapsed_seconds"]=time.perf_counter()-start
    result["process_peak_working_set_mb"]=peak_process_mb()
    result["memory_measurement"]="cumulative process peak; direct callers may evaluate several models in this process"
    atomic_text(out_dir/"metrics.json",json_text(result)+"\n")
    return result


def evaluate_worker(task):
    """One model per fresh process: bounded memory and independent peak readings."""
    index,src,out_dir,flow_options,render_options,camera=task
    out_dir=Path(out_dir); out_dir.mkdir(parents=True,exist_ok=True)
    with (out_dir/"progress.log").open("w",encoding="utf-8",buffering=1) as log:
        def progress(fraction,message):
            print(f"{int(fraction*100):3d}% {message}",file=log,flush=True)
        result=evaluate_model(src,out_dir,flow_options,render_options,camera,progress)
    result["memory_measurement"]="peak working set of an isolated model worker"
    atomic_text(out_dir/"metrics.json",json_text(result)+"\n")
    return index,result


def write_report(records,out_dir):
    import html
    out_dir=Path(out_dir); out_dir.mkdir(parents=True,exist_ok=True)
    atomic_text(out_dir/"report.json",json_text({"schema":"atlas-flowrep-evaluation","version":1,"models":records})+"\n")
    rows=[]
    for r in records:
        name=html.escape(r["model"]); folder=html.escape(r.get("folder",r["model"]),quote=True)
        if r["status"]=="ok":
            q=r["extraction"]; comparison=r.get("comparison",{})
            text=(f'{q["curve_count"]} curves · max region error {q["max_error_deg"]:.2f}° · '
                  f'{q["timings"]["total"]:.2f}s extraction · {r["render"]["seconds"]:.2f}s rendering')
            if comparison.get("comparison_available"):
                text+=f' · sampled curve distance {comparison["chamfer_mean_pct_diagonal"]:.2f}% of diagonal'
            registration=r.get("reference_registration",{})
            if registration.get("applied"):
                text+=f' · normalized author reference registered at scale {registration["scale"]:.6g} (transform in JSON)'
            pictures=f'<div class="pair"><figure><img src="{folder}/author.svg"><figcaption>Author reference</figcaption></figure><figure><img src="{folder}/drawing.svg"><figcaption>Independent implementation</figcaption></figure></div>'
            if not r.get("files",{}).get("author"):
                pictures=f'<img class="single" src="{folder}/drawing.svg">'
        else:
            text=html.escape(r["error"]); pictures=""
        rows.append(f'<article><h2>{name}</h2><p>{text}</p>{pictures}</article>')
    document='''<!doctype html><html lang="en"><meta charset="utf-8"><title>FlowRep evaluation</title>
<style>body{font:15px system-ui;background:#eef0f3;color:#17202b;max-width:1200px;margin:40px auto;padding:0 24px}
h1{font-size:32px}article{background:white;padding:24px;margin:24px 0;border-radius:12px}.pair{display:flex;gap:12px}
figure{margin:0;flex:1}img{width:100%;height:420px;object-fit:contain;background:white}figcaption{text-align:center;color:#596574}
p{line-height:1.6}.single{height:420px}</style>
<h1>FlowRep → SVG evaluation</h1><p>Identical cameras and renderer for author and generated networks.
The sampled curve distance measures agreement with the reference drawing, not surface reconstruction error.
Peak working set is measured in an isolated worker for parallel CLI evaluations.
Full measurements and failures are in report.json.</p>'''
    atomic_text(out_dir/"report.html",document+"\n".join(rows)+"</html>\n")

