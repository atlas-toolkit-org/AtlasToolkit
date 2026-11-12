"""Bounded cubic SVG fitting and plotter-safe stroke-only export."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np

from .io import atomic_text, json_text

SVG_NS="http://www.w3.org/2000/svg"
INKSCAPE_NS="http://www.inkscape.org/namespaces/inkscape"
ET.register_namespace("",SVG_NS); ET.register_namespace("inkscape",INKSCAPE_NS)


def _bezier(control,t):
    t=np.asarray(t,float)[:,None]; s=1-t
    return s**3*control[0]+3*s*s*t*control[1]+3*s*t*t*control[2]+t**3*control[3]


def _derivative(control,t):
    t=np.asarray(t,float)[:,None]; s=1-t
    return 3*(s*s*(control[1]-control[0])+2*s*t*(control[2]-control[1])+t*t*(control[3]-control[2]))


def cubic_error_bound(control,points,t):
    """Convex-hull bound on the entire cubic versus the original piecewise line.

    Restrict the cubic to each source segment's parameter interval, subtract that
    segment's four degree-elevated linear controls, then bound by control norms.
    This bounds both directions under the same continuous correspondence.
    """
    a,b=t[:-1],t[1:]; dt=(b-a)[:,None]/3
    start,end=_bezier(control,a),_bezier(control,b)
    Q=np.stack([start,start+dt*_derivative(control,a),end-dt*_derivative(control,b),end],axis=1)
    P=points[:-1]; D=points[1:]-P
    L=np.stack([P,P+D/3,P+2*D/3,P+D],axis=1)
    bound=np.linalg.norm(Q-L,axis=2).max(axis=1)
    return float(bound.max(initial=0)),bound


def fit_path(points,tolerance=0.05,bounds=None):
    """Return SVG commands, a proven geometric bound, and cubic/line counts."""
    P=np.asarray(points,float)
    P=P[np.r_[True,np.linalg.norm(np.diff(P,axis=0),axis=1)>1e-10]]
    if len(P)<2:
        return "",0.0,0,0
    def fmt(p): return f"{p[0]:.6f},{p[1]:.6f}"
    commands=["M "+fmt(P[0])]; worst=0.0; cubic_count=line_count=0
    stack=[P]
    while stack:
        p=stack.pop()
        d=p[-1]-p[0]; length=float(np.linalg.norm(d))
        if len(p)==2:
            commands.append("L "+fmt(p[-1])); line_count+=1; continue
        if length>1e-12:
            tline=(p-p[0])@d/(length*length)
            deviation=np.linalg.norm(p-(p[0]+np.clip(tline,0,1)[:,None]*d),axis=1)
            if np.all(np.diff(tline)>=-1e-12) and deviation.max()<=tolerance*0.999:
                commands.append("L "+fmt(p[-1])); line_count+=1
                worst=max(worst,float(deviation.max())); continue
        arc=np.r_[0.0,np.cumsum(np.linalg.norm(np.diff(p,axis=0),axis=1))]
        if arc[-1]<=1e-12:
            continue
        t=arc/arc[-1]; s=1-t
        left=p[1]-p[0]; left/=max(np.linalg.norm(left),1e-30)
        right=p[-2]-p[-1]; right/=max(np.linalg.norm(right),1e-30)
        b1,b2=3*s*s*t,3*s*t*t
        design=np.stack([b1[:,None]*left,b2[:,None]*right],axis=-1).reshape(-1,2)
        rhs=p-((s**3+b1)[:,None]*p[0]+(t**3+b2)[:,None]*p[-1])
        alpha=np.linalg.lstsq(design,rhs.ravel(),rcond=None)[0]
        if np.any(alpha<=0) or np.any(alpha>arc[-1]*2):
            alpha[:]=arc[-1]/3
        control=np.array([p[0],p[0]+alpha[0]*left,p[-1]+alpha[1]*right,p[-1]])
        error,per_segment=cubic_error_bound(control,p,t)
        allowed=True
        if bounds is not None:
            x0,y0,x1,y1=bounds
            allowed=bool(np.all(control[:,0]>=x0-1e-9) and np.all(control[:,0]<=x1+1e-9) and
                         np.all(control[:,1]>=y0-1e-9) and np.all(control[:,1]<=y1+1e-9))
        if error<=tolerance*0.999 and allowed:
            commands.append("C "+" ".join(fmt(x) for x in control[1:]))
            cubic_count+=1; worst=max(worst,error)
        else:
            split=max(1,min(len(p)-2,int(np.argmax(per_segment))+1))
            stack.append(p[split:]); stack.append(p[:split+1])
    if np.linalg.norm(P[0]-P[-1])<1e-8:
        commands.append("Z")
    return " ".join(commands),worst,cubic_count,line_count


def build_svg(drawing,extra_metadata=None):
    root=ET.Element(f"{{{SVG_NS}}}svg",{"width":f"{drawing.width:g}mm","height":f"{drawing.height:g}mm",
                                    "viewBox":f"0 0 {drawing.width:g} {drawing.height:g}"})
    ET.SubElement(root,f"{{{SVG_NS}}}desc").text="flowrep-sketch"
    metadata={"tool":"flowrep-sketch","version":"0.1.0","values":drawing.metadata.get("render_options",{}),
              "network":drawing.metadata,"statistics":drawing.stats}
    if extra_metadata: metadata.update(extra_metadata)
    ET.SubElement(root,f"{{{SVG_NS}}}metadata").text=json_text(metadata)
    margin=drawing.metadata.get("render_options",{}).get("margin",0)
    half=drawing.line_width/2
    bounds=tuple(drawing.metadata.get("drawing_bounds_mm",
                 (margin+half,margin+half,drawing.width-margin-half,drawing.height-margin-half)))
    max_bound=0.0; cubics=lines=0
    for layer_name,polylines in drawing.layers.items():
        if not polylines: continue
        attrs={"id":layer_name,f"{{{INKSCAPE_NS}}}groupmode":"layer",f"{{{INKSCAPE_NS}}}label":layer_name.title(),
               "fill":"none","stroke":"#999999" if layer_name=="hidden" else "#000000",
               "stroke-width":f"{drawing.line_width:g}","stroke-linecap":"round","stroke-linejoin":"round"}
        if layer_name=="hidden": attrs["stroke-dasharray"]="1.5 1.5"
        group=ET.SubElement(root,f"{{{SVG_NS}}}g",attrs)
        for i,p in enumerate(polylines):
            d,error,nc,nl=fit_path(p,drawing.tolerance,bounds)
            if d:
                ET.SubElement(group,f"{{{SVG_NS}}}path",{"id":f"{layer_name}-{i+1}","d":d})
                max_bound=max(max_bound,error); cubics+=nc; lines+=nl
    drawing.stats.update(max_fitting_bound_mm=max_bound,cubic_segments=cubics,line_segments=lines)
    metadata["statistics"]=drawing.stats
    root.find(f"{{{SVG_NS}}}metadata").text=json_text(metadata)
    ET.indent(root,space="  ")
    return root


def write_svg(drawing,path,extra_metadata=None):
    root=build_svg(drawing,extra_metadata)
    atomic_text(path,'<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(root,encoding="unicode")+"\n")
    return path

