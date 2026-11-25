"""Atlas process-worker adapter. Dry previews write no output."""
from __future__ import annotations

import os
import time
from pathlib import Path

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.core.paths import cache_dir
from atlas_toolkit.geom.camera import camera_from,frame_for
from atlas_toolkit.geom.drawing import Drawing as AtlasDrawing,Pen,plot_tiles,saved_lines
from atlas_toolkit.geom.svgout import Layer,arrange,dashed,pen_mode,write_pens

from . import TOOL

HIDDEN_DASH=(1.5,1.5)        # mm on, mm off: the dasharray FlowRep's hidden layer had, now as geometry
HIDDEN_COLOR="#999999"


def fit_curves(drawing,layers):
    """Each stroke as bounded cubics (flowrep3d.svg.fit_path) inside the drawing's area, with the fitting's numbers in
    the drawing's stats, as flowrep3d's own SVG has them."""
    import numpy as np
    from flowrep3d.svg import fit_path
    margin=drawing.metadata.get("render_options",{}).get("margin",0)
    half=drawing.line_width/2
    bounds=tuple(drawing.metadata.get("drawing_bounds_mm",
                 (margin+half,margin+half,drawing.width-margin-half,drawing.height-margin-half)))
    worst=0.0; cubics=lines=0
    for layer in layers:
        layer.paths=[]
        for pl in layer.polylines:
            d,error,nc,nl=fit_path(np.asarray(pl,float),drawing.tolerance,bounds)
            layer.paths.append(d); worst=max(worst,error); cubics+=nc; lines+=nl
    drawing.stats.update(max_fitting_bound_mm=worst,cubic_segments=cubics,line_segments=lines)


def pen_layers(drawing,mode):
    """One pen per FlowRep layer (features, silhouette, flowlines, hidden) as Save pens as will save them. Hidden
    curves are dashed as geometry (a stroke-dasharray plots solid); a merge keeps FlowRep's own stroke order."""
    return arrange([Layer(name,dashed(lines,*HIDDEN_DASH) if name=="hidden" else list(lines),
                          HIDDEN_COLOR if name=="hidden" else "#000000",drawing.line_width)
                    for name,lines in drawing.layers.items() if len(lines)],mode)


def run(ctx,src,dst,p):
    from flowrep3d import FlowRepOptions,RenderOptions,extract_network,load_mesh,render_network

    # the toolkit's own cache (per user, or the portable folder); FLOWREP_CACHE overrides it
    t0=time.perf_counter()
    cache=Path(os.environ.get("FLOWREP_CACHE") or cache_dir()/"flowrep")
    opts=FlowRepOptions(dmax=float(p.dmax),prepare=p.prepare,target_faces=min(int(p.target_faces),1500) if ctx.draft else int(p.target_faces),
                        crease_angle=float(p.crease_angle),regularize_iterations=min(1,int(p.regularize)) if ctx.draft else int(p.regularize),
                        local_passes=0 if ctx.draft else 2,clustering_passes=2 if ctx.draft else 8,cache_dir=str(cache),
                        relaxed_declutter=bool(p.declutter),min_trim_edges=int(p.min_trim_edges))
    mesh=load_mesh(src)
    # The Aim tab's placement, exactly: Up and Front come from geom/frame3d like every drawing tool's (a frame stored
    # in the file, its stage set, then a guess), not from flowrep3d's own guess, which ignores a stored frame.
    import numpy as np
    from atlas_toolkit.geom import frame3d
    up,front,rotate,_source=frame3d.resolve_axes(src,p,np.ptp(np.asarray(mesh.reference_vertices),axis=0))
    net=extract_network(mesh,opts,lambda f,m:ctx.progress(f*0.78,m),ctx.check_cancel)
    options=RenderOptions(paper=p.paper,orientation=p.orientation,margin=float(p.margin),
                          line_width=float(p.line_width),tolerance=float(p.tolerance),
                          min_length=float(p.min_length),silhouette=bool(p.silhouette),hidden=bool(p.show_hidden),
                          up=up,front=front,rotate=tuple(rotate),fit_camera=False,
                          sample_step=0.7 if ctx.draft else 0.35,smoothing=float(p.smoothing),
                          silhouette_style=p.silhouette_style)
    # Roll turns the model about the line of sight; FlowRep draws only the model's own lines, so turning the camera
    # gives the same picture and keeps the network (cached for the mesh as it is in the file)
    cam=camera_from(p); cam.roll=float(p.get("roll",0.0) or 0.0)
    drawing=render_network(mesh,net,cam,frame_for(p.paper,p.orientation,float(p.margin)),options,
                           lambda f,m:ctx.progress(0.78+0.20*f,m),ctx.check_cancel)
    mode=pen_mode(p)
    layers=pen_layers(drawing,mode)
    outputs=[]
    if dst is not None:
        fit_curves(drawing,layers)
        extra={"network":drawing.metadata,"statistics":drawing.stats}
        outputs=[str(f) for f in write_pens(dst,drawing.width,drawing.height,layers,mode,TOOL,dict(p),extra=extra)]
    pens=[Pen.from_polylines(l.label,l.label.title(),l.polylines,l.color,l.width) for l in layers]
    preview=AtlasDrawing(drawing.width,drawing.height,(0.,0.,drawing.width,drawing.height),pens)
    error=net.diagnostics["max_error_deg"]
    repairs=net.diagnostics.get("remesh_repairs") or {}
    rebuilt=int(repairs.get("removed_faces",0))+int(repairs.get("dropped_polygons",0))
    healed=", ".join(part for part in (
        f"{repairs['holes_filled']} hole{'s' if repairs['holes_filled']!=1 else ''} filled" if repairs.get("holes_filled") else "",
        f"{rebuilt} broken face{'s' if rebuilt!=1 else ''} rebuilt" if rebuilt else "",
        f"{repairs['welded_vertices']} duplicate vertices welded" if repairs.get("welded_vertices") else "",
        f"remeshed at {repairs['remesh_target']} after {repairs['remesh_attempts']} tries" if repairs.get("remesh_attempts",1)>1 else "",
    ) if part)
    report=(f"{Path(src).name}\n"
            f"  3D network: {len(net.curves)} curves, {net.edge_count} edges\n"
            +(f"  Remesh repairs: {healed}\n" if healed else "")+
            f"  Descriptiveness: {error:.2f}° / {opts.dmax:g}°\n"
            f"  Drawing: {drawing.stats['strokes']} strokes, {drawing.stats['ink_mm']:.0f} mm ink\n"
            +"".join(f"  Pen: {l.label}, {len(l.polylines)} strokes, {l.width:g} mm\n" for l in layers)+
            f"  Network cache: {'hit' if net.diagnostics.get('cache_hit') else 'computed'}\n"
            f"  Visibility: {drawing.stats['ray_backend']}\n"
            "  Method: an independent implementation of FlowRep (Gori et al., ACM TOG 2017)."
            +"".join("\n"+line for line in saved_lines(outputs)))
    ctx.progress(1.0,"Saved" if outputs else "Previewed")
    return Result(outputs=outputs,
                  stats=[Stat("curves","Curves",len(net.curves)),Stat("strokes","Strokes",drawing.stats["strokes"]),
                         # the plot tiles every drawing tool shows, from the drawing as the preview has it, and how
                         # faithful the network is: its worst region error against the tool's limit
                         *plot_tiles(preview.stats(),time.perf_counter()-t0,
                                     extra=[Stat("error","Region error",error,"°",fmt="{:.2f}",
                                                 tone="warning" if error>float(p.dmax) else "")])],
                  report=report,
                  preview={"views":[{"title":"Drawing","kind":"svg","drawing":preview}],"prefer":"Drawing"},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"network":net.diagnostics,"render":drawing.stats})
