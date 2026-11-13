"""
dpt_hatching.py - Photo to pen-plotter hatching, guided by depth from a DPT-family transformer
(from the Colab notebooks DPT_Transformer, DPT_Hatching_generator, hatching_DPT_Large_2 and _3).

    depth      MiDaS 3.1 BEiT-L-512 by default (DPT-Large and Depth Anything V2 Large on request), on the GPU through
               the separate runtime; aspect ratio kept (the notebooks squashed photos to 384 x 384); "boost" detail
               merges a native and a high-resolution pass. Or a depth-map file, or a rough guess without a GPU.
    tone       CIELab L*, local contrast, tone curve, optional depth fog; tones above "paper white" get no ink.
    direction  structure tensor of depth (strokes wrap the form), of the photo, or a fixed angle.
    styles     flow: evenly spaced curved strokes in 1-4 tone layers, each layer crossing the last
               patches: notebook (d)'s superpixel mosaic, fixed      contours: notebook (c)'s iso-depth lines, fixed
    lines      occlusion outlines from depth discontinuities, optional photo edges, each traced once.
    paper      fitted to a sheet in mm, strokes joined, simplified and ordered for the plotter, one layer per pen.

Outputs <name>_photohatch.svg, signed <desc>dpt-hatching</desc> so Atlas links them here.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np

from atlas_toolkit.geom import depth as gdepth
from atlas_toolkit.geom.image import load_rgb
from atlas_toolkit.geom.paper import fit, sheet
from atlas_toolkit.geom.svgout import Layer, join_segments, order_polylines, simplify_all, travel

from . import field as fld
from . import outlines as ol
from . import styles
from .streamlines import trace_layer

HYSTERESIS = 0.05     # a stroke seeds below its layer's tone threshold but keeps going until this much above it
MIN_RATIO = 2.0       # a stroke at least twice as long as its spacing, or it reads as a tick


def layer_angles(cross: float) -> list:
    """Each tone layer crosses the ones before it: +cross, then -cross (lozenges), then square."""
    return [0.0, cross, -cross, 90.0]


def layer_thresholds(white: float, cross_below: float, k: int) -> list:
    """Tone below which layer i is drawn: the first layer covers everything darker than paper white, the crossing
    layers start below `cross_below` and evenly further down."""
    c = min(cross_below, white)
    return [white] + [c * (k - i) / (k - 1) for i in range(1, k)]


def spacing_for(tone, t_k: float, d_dark: float, d_light: float):
    """Stroke spacing for one layer: ink density (1 / spacing) grows linearly from the layer's threshold (spacing
    d_light) to black (d_dark), so the tone a layer adds is linear in the photo's tone. inf where it draws nothing."""
    u = np.clip(1.0 - tone / max(t_k, 1e-6), 0.0, 1.0)
    dens = 1.0 / d_light + (1.0 / d_dark - 1.0 / d_light) * u
    return np.where(tone < t_k, 1.0 / dens, np.inf)


def get_depth(src: Path, rgb_shape, L, p, log, progress=None, draft: bool = False, note=None) -> np.ndarray:
    """The depth map. A model's first use downloads it, with progress (between 5 and 20 % of the run); a quick preview
    never starts that download: it uses the rough depth and says why (`note` gets a line for the report)."""
    h, w = rgb_shape[:2]
    if p.depth_source == "rough":
        log("depth: rough guess from tone (no model)")
        return gdepth.rough(L)
    if p.depth_source == "file":
        f = Path(p.depth_file) if p.depth_file else src.with_name(f"{src.stem}_depth.png")
        if not f.exists():
            raise gdepth.DepthError(f"no depth-map file: {f}")
        log(f"depth from {f.name}")
        return gdepth.from_file(f, (h, w))
    say = (lambda f, m: progress(0.05 + 0.15 * f, m)) if progress else None
    try:
        return gdepth.from_model(src, p.model, p.detail, (h, w), log, progress=say, allow_download=not draft)
    except gdepth.NotDownloaded as e:
        msg = (f"Preview with the rough depth: {e.label} is not downloaded yet (about {gdepth.gb(e.total_mb)}, once). "
               "Hatch (or running a workflow) downloads it, with progress; previews use it from then on.")
        log(msg, "warning")
        if progress:
            progress(0.05, "rough depth until the model is downloaded")
        if note is not None:
            note.append(f"rough (preview: {e.label} not downloaded yet; Hatch downloads it once)")
        return gdepth.rough(L)


def render(src: Path, p, progress=None, log=None, draft: bool = False, cancel=None) -> dict:
    say = progress or (lambda *_: None)
    log = log or (lambda *_: None)
    t = {}
    t0 = time.perf_counter()
    work = int(min(p.work_px, 900) if draft else p.work_px)
    rgb = load_rgb(src, work)
    h, w = rgb.shape[:2]
    L = fld.lightness(rgb)
    say(0.05, "depth")
    depth_note: list = []
    depth = get_depth(Path(src), rgb.shape, L, p, log, progress=progress, draft=draft, note=depth_note)
    t["depth"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    say(0.25, "tone and direction")
    tone = fld.tone(L, depth, p.contrast, p.curve, p.fog)
    W, H = sheet(p.paper, p.orientation)
    s, tx, ty = fit((0.0, 0.0, float(w), float(h)), p.paper, p.orientation, p.margin)    # mm per px
    field2 = fld.directions(p.direction, depth, L, p.angle, p.field_smooth / s)
    need_bar = p.occlusions or (p.style == "flow" and p.stop_at_edges) or p.style == "contours" or p.add_contours
    barrier = fld.occlusions(depth, p.occlusion_sens, 1.5) if need_bar else None
    t["fields"] = time.perf_counter() - t1

    t2 = time.perf_counter()
    k = int(p.layers)
    thresholds = layer_thresholds(p.white, p.cross_below, k)
    angles = layer_angles(p.cross_angle)[:k]
    d_dark, d_light = p.spacing_dark / s, p.spacing_light / s          # px

    def spacing_px(tv, tk):
        return spacing_for(tv, tk, d_dark, d_light)

    rng = np.random.default_rng(int(p.seed))
    hatch_layers: list = []
    if p.style == "flow":
        from scipy.ndimage import gaussian_filter
        # strokes cannot show tone detail finer than their own spacing: hatch a tone smoothed at that scale (fine in
        # the darks, broad in the lights), so a textured surface (cobbles, skin, foliage) gets long strokes instead
        # of dashes and ticks cut at every flicker
        fine, broad = gaussian_filter(tone, 0.5 * d_dark), gaussian_filter(tone, 0.5 * d_light)
        tone_h = fine + (broad - fine) * np.clip(fine / max(p.white, 1e-6), 0.0, 1.0)
        stride = max(1, int(round(d_dark * 0.7)))
        yy, xx = np.mgrid[0:h:stride, 0:w:stride]
        cand = (yy * w + xx).ravel()
        cand = cand[np.argsort(tone_h.ravel()[cand], kind="stable")]         # darkest first
        for i, (tk, da) in enumerate(zip(thresholds, angles)):
            say(0.3 + 0.4 * i / k, f"hatching layer {i + 1} of {k}")
            sp = spacing_px(tone_h, tk + HYSTERESIS).astype(np.float32)
            seedable = tone_h < tk
            lines = trace_layer(fld.rotate(field2, da), sp, barrier if p.stop_at_edges else None,
                                step=max(p.step / s, 0.3), max_len=p.stroke_len / s, min_len=p.min_len / s,
                                min_ratio=MIN_RATIO, turn_deg=p.turn, rng=rng, hand=p.hand,
                                order=cand[seedable.ravel()[cand]],
                                seedable=seedable, cancel=cancel)
            hatch_layers.append(lines)
    elif p.style == "patches":
        from skimage.color import rgb2lab
        lab = rgb2lab(rgb.astype(np.float32) / 255.0)
        hatch_layers = styles.patches(lab, depth, tone, field2, n_segments=p.patches, compactness=p.compactness,
                                      depth_weight=p.depth_weight, layer_t=thresholds, layer_angles=angles,
                                      spacing_px=spacing_px, white=p.white,
                                      progress=lambda f: say(0.3 + 0.4 * f, "patches"))
    elif p.style == "contours":
        hatch_layers = [styles.contours(depth, levels=p.levels, mode=p.level_mode, bias=p.bias, far_cut=p.far_cut,
                                        smooth_px=p.field_smooth / s * 0.5, barrier=barrier)]
    contour_layer = []
    if p.add_contours and p.style != "contours":
        contour_layer = styles.contours(depth, levels=p.levels, mode=p.level_mode, bias=p.bias, far_cut=p.far_cut,
                                        smooth_px=p.field_smooth / s * 0.5, barrier=barrier)
    t["hatching"] = time.perf_counter() - t2

    t3 = time.perf_counter()
    say(0.75, "outlines")
    outline_lines = []
    if p.occlusions and barrier is not None:
        outline_lines += ol.trace(barrier)
    if p.photo_edges:
        outline_lines += ol.trace(ol.photo_edges(L, p.photo_sens, 1.2))
    min_px = p.edge_min / s
    outline_lines = [pl for pl in outline_lines if _length(pl) >= min_px]
    t["outlines"] = time.perf_counter() - t3

    t4 = time.perf_counter()
    say(0.85, "joining and ordering strokes")

    def to_mm(lines):
        return [[(s * x + tx, s * y + ty) for x, y in pl] for pl in lines]

    layers = []
    names = ["hatching", "cross-hatching", "counter-hatching", "square hatching"]
    if p.style == "contours":
        names = ["contours"]
    if p.layer_per_direction or p.style == "contours":
        for i, lines in enumerate(hatch_layers):
            layers.append(Layer(names[i] if i < len(names) else f"hatching {i + 1}", to_mm(lines), "#000000",
                                p.hatch_width))
    else:
        layers.append(Layer("hatching", to_mm([pl for lines in hatch_layers for pl in lines]), "#000000",
                            p.hatch_width))
    if contour_layer:
        layers.append(Layer("contours", to_mm(contour_layer), "#000000", p.contour_width))
    if outline_lines:
        layers.append(Layer("outlines", to_mm(outline_lines), "#000000", p.outline_width))
    before = after = 0.0
    for layer in layers:
        pls = [pl for pl in join_segments(layer.polylines, 1e-3) if len(pl) >= 2]
        pls = [pl for pl in simplify_all(pls, p.simplify) if len(pl) >= 2]
        before += travel(pls)
        if p.optimise:
            pls = order_polylines(pls, start=(0.0, 0.0))
        after += travel(pls)
        layer.polylines = pls
    t["ordering"] = time.perf_counter() - t4
    return {"layers": [l for l in layers if l.polylines], "sheet": (W, H), "rect": (tx, ty, w * s, h * s),
            "rgb": rgb, "depth": depth, "tone": tone, "timings": t, "travel": (before, after), "mm_per_px": s,
            "size": (w, h), "depth_note": depth_note[0] if depth_note else ""}


def _length(pl) -> float:
    return sum(math.dist(a, b) for a, b in zip(pl, pl[1:]))
