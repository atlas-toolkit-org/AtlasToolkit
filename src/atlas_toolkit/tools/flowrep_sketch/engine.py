"""Atlas process-worker adapter. Dry previews write no output."""
from __future__ import annotations

import os
from pathlib import Path

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.core.paths import cache_dir
from atlas_toolkit.geom.camera import camera_from,frame_for
from atlas_toolkit.geom.drawing import Drawing as AtlasDrawing,Pen
from atlas_toolkit.geom.svg import write_run_metadata

from . import TOOL


def run(ctx,src,dst,p):
    from flowrep3d import FlowRepOptions,RenderOptions,extract_network,load_mesh,render_network
    from flowrep3d.svg import build_svg
    from flowrep3d.io import atomic_text
    import xml.etree.ElementTree as ET

    # the toolkit's own cache (per user, or the portable folder); FLOWREP_CACHE overrides it
    cache=Path(os.environ.get("FLOWREP_CACHE") or cache_dir()/"flowrep")
    opts=FlowRepOptions(dmax=float(p.dmax),prepare=p.prepare,target_faces=min(int(p.target_faces),1500) if ctx.draft else int(p.target_faces),
                        crease_angle=float(p.crease_angle),regularize_iterations=min(1,int(p.regularize)) if ctx.draft else int(p.regularize),
                        local_passes=0 if ctx.draft else 2,clustering_passes=2 if ctx.draft else 8,cache_dir=str(cache))
    mesh=load_mesh(src)
    net=extract_network(mesh,opts,lambda f,m:ctx.progress(f*0.78,m),ctx.check_cancel)
    options=RenderOptions(paper=p.paper,orientation=p.orientation,margin=float(p.margin),
                          line_width=float(p.line_width),tolerance=float(p.tolerance),
                          min_length=float(p.min_length),silhouette=bool(p.silhouette),hidden=bool(p.show_hidden),
                          up=p.up_axis,front=p.front,rotate=tuple(p.rotate),fit_camera=False,
                          sample_step=0.7 if ctx.draft else 0.35)
    drawing=render_network(mesh,net,camera_from(p),frame_for(p.paper,p.orientation,float(p.margin)),options,
                           lambda f,m:ctx.progress(0.78+0.20*f,m),ctx.check_cancel)
    outputs=[]
    if dst is not None:
        root=build_svg(drawing)
        write_run_metadata(root,TOOL,dict(p))
        atomic_text(dst,'<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(root,encoding="unicode")+"\n")
        outputs=[str(dst)]
    pens=[Pen.from_polylines(name,name.title(),lines,"#999999" if name=="hidden" else "#000000",drawing.line_width)
          for name,lines in drawing.layers.items() if lines]
    preview=AtlasDrawing(drawing.width,drawing.height,(0.,0.,drawing.width,drawing.height),pens)
    error=net.diagnostics["max_error_deg"]
    report=(f"{Path(src).name}\n"
            f"  3D network: {len(net.curves)} curves, {net.edge_count} edges\n"
            f"  Descriptiveness: {error:.2f}° / {opts.dmax:g}°\n"
            f"  Drawing: {drawing.stats['strokes']} strokes, {drawing.stats['ink_mm']:.0f} mm ink\n"
            f"  Network cache: {'hit' if net.diagnostics.get('cache_hit') else 'computed'}\n"
            f"  Visibility: {drawing.stats['ray_backend']}\n"
            "  Method: an independent implementation of FlowRep (Gori et al., ACM TOG 2017).")
    ctx.progress(1.0,"Saved" if outputs else "Previewed")
    return Result(outputs=outputs,
                  stats=[Stat("curves","Curves",len(net.curves)),Stat("strokes","Strokes",drawing.stats["strokes"]),
                         Stat("error","Region error",error,"°",fmt="{:.2f}")],
                  report=report,
                  preview={"views":[{"title":"Drawing","kind":"svg","drawing":preview}],"prefer":"Drawing"},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"network":net.diagnostics,"render":drawing.stats})

