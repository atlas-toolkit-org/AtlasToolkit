"""Batch CLI. Geometry extraction and rendering can be run separately."""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
from dataclasses import replace
from pathlib import Path

from . import (__version__, Camera, FlowRepError, FlowRepOptions, RenderOptions, extract_network,
               load_mesh, load_network, render_network, save_network, write_svg)
from .io import atomic_text,json_text

MESH_EXTENSIONS={".obj",".stl",".ply",".glb",".gltf",".off"}


def _mesh_paths(inputs):
    paths=[]
    for value in inputs:
        p=Path(value)
        if p.is_dir():
            paths.extend(q for q in sorted(p.rglob("*")) if q.suffix.lower() in MESH_EXTENSIONS and
                         "network" not in q.stem.lower() and not q.stem.endswith("_flowrep"))
        elif p.is_file():
            paths.append(p)
        else:
            raise FlowRepError(f"input does not exist: {p}")
    if not paths:
        raise FlowRepError("no mesh inputs found")
    # Stable de-duplication avoids processing a file supplied both directly and through a directory.
    return list(dict.fromkeys(p.resolve() for p in paths))


def _flow_arguments(parser):
    parser.add_argument("--preset",choices=("clean","detailed","sparse"),default="clean")
    parser.add_argument("--dmax",type=float,help="descriptiveness threshold in degrees; overrides preset")
    parser.add_argument("--prepare",choices=("auto","preserve","remesh"),default="auto")
    parser.add_argument("--target-faces",type=int,default=6000)
    parser.add_argument("--crease-angle",type=float,default=45.0)
    parser.add_argument("--clustering-passes",type=int,default=8)
    parser.add_argument("--local-passes",type=int,default=2)
    parser.add_argument("--regularize",type=int,default=5,metavar="ITERATIONS")
    parser.add_argument("--relaxed-declutter",action="store_true",help="allow the paper's final 20%% error relaxation")
    parser.add_argument("--cache-dir",default=os.environ.get("FLOWREP_CACHE") or ".cache/flowrep")
    parser.add_argument("--no-cache",action="store_true")


def _render_arguments(parser):
    parser.add_argument("--paper",default="A3",choices=("A5","A4","A3","A2","A1","30×40","50×70"))
    parser.add_argument("--orientation",choices=("portrait","landscape"),default="portrait")
    parser.add_argument("--margin",type=float,default=15.0)
    parser.add_argument("--line-width",type=float,default=0.3)
    parser.add_argument("--tolerance",type=float,default=0.05)
    parser.add_argument("--sample-step",type=float,default=0.35)
    parser.add_argument("--min-length",type=float,default=0.5)
    parser.add_argument("--no-silhouette",action="store_true")
    parser.add_argument("--hidden",action="store_true",help="include hidden curves in a dashed grey layer")
    parser.add_argument("--theta",type=float,default=70.0)
    parser.add_argument("--psi",type=float,default=-150.0)
    parser.add_argument("--radius",type=float,default=170.0)
    parser.add_argument("--lookat",type=float,nargs=3,default=(0.,0.,0.))
    parser.add_argument("--fov",type=float,default=60.)
    parser.add_argument("--projection",choices=("perspective","orthographic"),default="perspective")
    parser.add_argument("--keep-camera-distance",action="store_true")
    parser.add_argument("--up",default="auto",choices=("auto","+x","-x","+y","-y","+z","-z"))
    parser.add_argument("--front",default="auto",choices=("auto","+x","-x","+y","-y","+z","-z"))
    parser.add_argument("--rotate",nargs=3,type=float,default=(0.,0.,0.))
    parser.add_argument("--no-order",action="store_true")


def _flow_options(args):
    threshold=args.dmax if args.dmax is not None else {"clean":20.,"detailed":10.,"sparse":30.}[args.preset]
    return FlowRepOptions(dmax=threshold,prepare=args.prepare,target_faces=args.target_faces,
                          crease_angle=args.crease_angle,clustering_passes=args.clustering_passes,
                          local_passes=args.local_passes,regularize_iterations=args.regularize,
                          relaxed_declutter=args.relaxed_declutter,cache_dir=None if args.no_cache else args.cache_dir)


def _render_options(args):
    return RenderOptions(paper=args.paper,orientation=args.orientation,margin=args.margin,
                         line_width=args.line_width,tolerance=args.tolerance,sample_step=args.sample_step,
                         min_length=args.min_length,silhouette=not args.no_silhouette,hidden=args.hidden,
                         fit_camera=not args.keep_camera_distance,up=args.up,front=args.front,
                         rotate=tuple(args.rotate),optimise=not args.no_order)


def _camera(args):
    return Camera(args.theta,args.psi,args.radius,tuple(args.lookat),args.fov,args.projection=="orthographic")


def _destination(args,src,suffix,count):
    if args.output:
        if count!=1:
            raise FlowRepError("--output names one file; use --out-dir for a batch")
        dst=Path(args.output)
    else:
        dst=Path(args.out_dir)/(src.stem+suffix)
    if dst.resolve()==src.resolve():
        raise FlowRepError("output must not overwrite its input")
    if dst.exists() and not args.force:
        raise FlowRepError(f"output exists: {dst}; use --force to replace it")
    return dst


def _progress(quiet):
    if quiet: return None
    last=[None]
    def callback(fraction,message):
        percent=int(fraction*100)
        if (percent,message)!=last[0]:
            print(f"  {percent:3d}% {message}",file=sys.stderr,flush=True)
            last[0]=(percent,message)
    return callback


def parser():
    p=argparse.ArgumentParser(prog="flowrep-svg",description="FlowRep descriptive 3D curves → plotter SVG")
    p.add_argument("--version",action="version",version=__version__)
    sub=p.add_subparsers(dest="command",required=True)
    for name in ("extract","draw","render","benchmark"):
        q=sub.add_parser(name)
        q.add_argument("inputs",nargs="+" if name!="render" else 1)
        q.add_argument("--out-dir",default="output/benchmark" if name=="benchmark" else "output")
        q.add_argument("--quiet",action="store_true")
        if name!="benchmark":
            q.add_argument("-o","--output")
            q.add_argument("--force",action="store_true")
        if name in ("extract","draw","benchmark"): _flow_arguments(q)
        if name in ("render","draw","benchmark"): _render_arguments(q)
        if name=="extract": q.add_argument("--format",choices=("npz","json","obj"),default="npz")
        if name=="render":
            q.add_argument("--mesh",help="source surface, inferred from generated-network metadata when omitted")
            q.add_argument("--register-reference",action="store_true",help="recover a normalized author's scale and origin, verified against the mesh")
        if name=="benchmark":
            q.add_argument("--models",nargs="+",help="only these model directory names or stems")
            q.add_argument("--resume",action="store_true",help="reuse finished metrics from this evaluation directory")
            q.add_argument("--jobs",type=int,default=1,help="independent model workers (default 1)")
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    progress=_progress(args.quiet)
    failures=0
    try:
        if args.command=="render":
            src=Path(args.inputs[0]); net=load_network(src)
            mesh_path=args.mesh or net.metadata.get("source_mesh")
            if not mesh_path: raise FlowRepError("--mesh is required for an imported author network")
            dst=_destination(args,src,".svg",1)
            if dst.resolve()==Path(mesh_path).resolve():
                raise FlowRepError("output must not overwrite the source mesh")
            mesh=load_mesh(mesh_path)
            if args.register_reference:
                from .benchmark import register_reference
                net=register_reference(net,mesh)
            d=render_network(mesh,net,_camera(args),options=_render_options(args),progress=progress)
            write_svg(d,dst); print(dst); return 0
        paths=_mesh_paths(args.inputs)
        if args.command=="benchmark":
            from .benchmark import evaluate_worker,write_report,evaluation_signature
            if args.models:
                paths=[p for p in paths if p.parent.name in args.models or p.stem in args.models]
                if not paths: raise FlowRepError("--models matched no inputs")
            if not 1<=args.jobs<=32:
                raise FlowRepError("--jobs must be between 1 and 32")
            records={}; tasks=[]
            for i,src in enumerate(paths):
                folder=f"{i+1:02d}-{src.stem.replace(' ','-')}"
                out=Path(args.out_dir)/folder; metrics=out/"metrics.json"
                record=None
                if args.resume and metrics.exists():
                    record=json.loads(metrics.read_text(encoding="utf-8"))
                signature=evaluation_signature(src,_flow_options(args),_render_options(args),_camera(args))
                if record is not None and record.get("status")=="ok" and record.get("evaluation_signature")==signature:
                    record["folder"]=folder; records[i]=record
                    print(f'reused: {record["model"]}',flush=True)
                else:
                    tasks.append((i,src,out,_flow_options(args),_render_options(args),_camera(args)))
            write_report([records[i] for i in sorted(records)],args.out_dir)
            if tasks:
                print(f"Evaluating {len(tasks)} model(s) with {args.jobs} worker(s); per-model progress.log files show stages.",
                      file=sys.stderr,flush=True)
                context=multiprocessing.get_context("spawn")
                with context.Pool(processes=args.jobs,maxtasksperchild=1) as pool:
                    for i,record in pool.imap_unordered(evaluate_worker,tasks,chunksize=1):
                        record["folder"]=f"{i+1:02d}-{paths[i].stem.replace(' ','-')}"
                        records[i]=record
                        write_report([records[j] for j in sorted(records)],args.out_dir)
                        print(f'{record["status"]}: {record["model"]} ({record["elapsed_seconds"]:.1f}s)',flush=True)
            failures=sum(r["status"]!="ok" for r in records.values())
            print(Path(args.out_dir)/"report.html")
            return int(bool(failures))
        if args.output and len(paths)>1:
            raise FlowRepError("--output names one file; use --out-dir for a batch")
        # Validate destinations before starting costly extraction.
        destinations=[_destination(args,src,f".network.{args.format}" if args.command=="extract" else "_flowrep.svg",len(paths))
                      for src in paths]
        if len({p.resolve() for p in destinations})!=len(destinations):
            raise FlowRepError("batch contains duplicate stems; use separate output directories")
        for src,dst in zip(paths,destinations):
            try:
                if not args.quiet: print(src,file=sys.stderr,flush=True)
                mesh=load_mesh(src)
                net=extract_network(mesh,_flow_options(args),progress)
                if args.command=="extract":
                    save_network(net,dst)
                else:
                    d=render_network(mesh,net,_camera(args),options=_render_options(args),progress=progress)
                    write_svg(d,dst)
                    save_network(net,dst.with_suffix(".network.npz"))
                print(dst,flush=True)
            except (FlowRepError,OSError) as exc:
                failures+=1; print(f"{src.name}: {exc}",file=sys.stderr,flush=True)
        return int(bool(failures))
    except (FlowRepError,OSError,ValueError) as exc:
        print(f"flowrep-svg: {exc}",file=sys.stderr); return 2
    except KeyboardInterrupt:
        print("FlowRep cancelled",file=sys.stderr); return 130


if __name__=="__main__":
    raise SystemExit(main())
