"""
stl_hatch_maker.py - Turn a 3D model into a pen-plotter drawing: hatching that shades its form, its outline and
its creases, seen from any angle, with hidden lines removed (from "STL 3D Hatch Maker" v2.1, v3, C-v4 and v5).

    scene      the model seen by an orthographic camera and laid out on paper in mm      (scene.py)
    styles     seven ways to hatch, from v5's surface strokes to v2.1's isophotes       (styles.py)
    lines      true silhouettes (or v5's grazing band) and creases, cut by visibility
    layers     one pen per feature, strokes joined end to end and ordered to cut pen travel

What changed from v5: every style works again (v5 only ran two); seeding is a real Poisson disk (v5's geodesic
sampler marked nearly every vertex); visibility is one batch of rays for everything; the drawing is fitted to a
paper size in millimetres and the right way up (v5 wrote 800 px, upside down); strokes are joined into polylines
(v5 wrote one <path> per segment); randomness is seeded; the view is the 3D viewer's.

Outputs <name>_hatch.svg, signed <desc>stl-hatch-maker</desc> so Atlas links them here.
"""
from __future__ import annotations

import numpy as np

from atlas_toolkit.geom.svgout import Layer, join_segments, order_polylines, simplify_all, travel

from .scene import Scene, crease_edges, edge_polylines, silhouette_edges
from .styles import STYLES


def render(mesh, p, progress=None, log=None) -> dict:
    """Layers (paper mm) and numbers for one model and one set of parameters."""
    say = progress or (lambda *_: None)
    say(0.05, "setting up the view")
    scene = Scene(mesh, p.azimuth, p.elevation, p.roll, p.paper, p.orientation, p.margin, p.light)
    rng = np.random.default_rng(int(p.seed))
    layers = []
    fn = STYLES.get(p.style, (None, None))[1]
    if fn is not None:
        say(0.15, f"hatching: {STYLES[p.style][0].lower()}")
        hatch = fn(scene, p, rng, progress)
        layers.append(Layer("hatching", hatch, "#000000", p.hatch_width))
    if p.silhouettes:
        say(0.65, "outlines")
        edges = silhouette_edges(scene, p.silhouette_method, p.grazing)
        layers.append(Layer("silhouettes", scene.clip_polylines(edge_polylines(scene, edges), p.vis_step),
                            "#000000", p.outline_width))
    if p.creases:
        say(0.75, "creases")
        layers.append(Layer("creases", scene.clip_polylines(edge_polylines(scene, crease_edges(scene,
                                                                                               p.crease_angle)),
                                                              p.vis_step), "#000000", p.crease_width))
    say(0.82, "joining strokes")
    raw_travel, final_travel = 0.0, 0.0
    for layer in layers:
        pls = [pl for pl in join_segments(layer.polylines, 1e-4) if len(pl) >= 2]
        pls = [pl for pl in simplify_all(pls, p.simplify) if len(pl) >= 2]
        raw_travel += travel(pls)
        if p.optimise:
            pls = order_polylines(pls)
        final_travel += travel(pls)
        layer.polylines = pls
    if p.one_layer and len(layers) > 1:
        merged = [pl for layer in layers for pl in layer.polylines]
        layers = [Layer("drawing", order_polylines(merged) if p.optimise else merged, "#000000", p.hatch_width)]
    if log:
        log(f"{scene.rays:,} visibility rays".replace(",", " "))
    return {"layers": [l for l in layers if l.polylines], "sheet": scene.sheet, "scene": scene,
            "travel_before": raw_travel, "travel_after": final_travel}
