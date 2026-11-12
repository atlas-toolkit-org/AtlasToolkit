"""
sketch_hatching.py - a 3D model drawn like an illustrator's pen sketch: an unbroken outline that thins where the
light falls on it (lost and found), interior contours, suggestive contours, valleys and creases, and evenly spaced
hatching that follows the form, one pass per tone band, crossing in the darks - the sketch style the Blender pieces
developed on Augustus (projects/blender3d_project/augustus: plotter_lib/sketch_draw.py, sketch_runner.py), for any
model, without Blender. The trace (G-buffer, light, shadows, ambient occlusion, feature lines) is geom/shading.py.

    strokes = line_strokes(trace, **lines)            # visible feature lines with roles
    strokes += hatch_strokes(trace, tone, ...)        # streamline hatching (Jobard & Lefer), tone passes
    layers = pen_layers(strokes, pens, style)         # role -> pen, hand styling, order and join per pen

Drawing rules, per pen layer: a minimum stroke length, priority clipping so lines never double up, joins and
ordering only within a layer; hatching keeps 2.5 pen widths between strokes and stops short of bold contours.

The functions below are the pieces' own (unchanged apart from imports); this module adds the settings that wrap
them (complexity, presets, the ink budget).
"""
import array
import json
import math
from collections import deque

import numpy as np

from atlas_toolkit.geom.sketch import (_arclen, _majority, _runs, blur, clip_against, gb_sample,  # noqa: F401
                                       mask_outline, masked_blur, normals2d, plen, rdp, resample, smooth_mm)


ROLE_PRIORITY = {"outline": 0, "inner": 1, "accent": 2, "crease": 3, "inner_short": 4, "detail": 5,
                 "hatch_0": 10, "hatch_1": 11, "hatch_2": 12, "hatch_3": 13}


LINE_DEFAULTS = dict(
    min_len_mm={"outline": 1.5, "inner": 1.5, "inner_short": 1.5, "crease": 2.5, "detail": 3.0, "accent": 2.0},
    outline_min_piece_mm=3.0,   # outline/inner flag flips shorter than this are ignored
    bold_min_len_mm=10.0,       # inner contours shorter than this go to the fine pen
    bold_min_gap=0.04,          # ... and those whose depth gap (world units) is smaller
    suggestive=True,
    suggestive_min_strength=0.0,  # extra strength filter on top of the trace threshold
    suggestive_trim_mm=0.4,     # shorten both ends (they fade out in a drawing)
    creases=True,
    min_gap_mm=0.3,             # lower-priority lines are clipped this close to drawn ones
    smooth_mm={"outline": 0.35, "inner": 0.5, "inner_short": 0.6, "crease": 0.4, "detail": 0.9},
    simplify_mm=0.03,
    probe_mm=0.6,               # crease edges with background this far to one side are outline
    outline_from_mask=True,     # silhouette = boundary of the ray-cast hit mask (unbroken);
                                # object-space contours then only give the inner contours
    valleys=True,               # concave creases: lid creases, lip line, nostrils, ear folds
    valley_min_strength=110.0,  # |k1| (1/world units) of drawn valleys, before attention scaling
    ridges=False,
    ridge_min_strength=160.0,
    # attention (only when the trace has a focus): thresholds x focus_line_scale on the focal
    # region (more, finer lines) and x periphery_line_scale elsewhere (calmer)
    focus_line_scale=0.45,
    periphery_line_scale=1.25,
    focus_min_len_scale=0.5,
    focus_smooth_scale=0.55,
    accent_min_strength=170.0,  # valleys this strong on the focal region become accents
    accent_min_attn=0.5,
    light_weighted_outline=True,  # lost and found: heavier outline on the shadow side
)


def _trim(pts, mm):
    if mm <= 0 or plen(pts) <= 2 * mm + 0.5:
        return pts
    r = resample(pts, 0.1)
    k = int(round(mm / 0.1))
    return r[k:len(r) - k]


def outward_side(pts, gb, probe=0.6):
    """+1 if the stroke's left normal points away from the figure (background or a farther
    surface), else -1 - the side a heavier outline should grow toward."""
    pts = resample(pts, 1.0) if plen(pts) > 2 else np.asarray(pts, float)
    n = normals2d(pts)
    hl, hr = gb_sample(gb, pts + n * probe, "hit"), gb_sample(gb, pts - n * probe, "hit")
    dl, dr = gb_sample(gb, pts + n * probe, "depth"), gb_sample(gb, pts - n * probe, "depth")
    dl = np.where(hl, dl, np.inf)
    dr = np.where(hr, dr, np.inf)
    return 1.0 if (dl > dr).sum() >= (dr > dl).sum() else -1.0


def _outline_weight(pts, side, gb, probe=1.0, light_hi=0.75, light_lo=0.2, floor=0.15, smooth_mm_=4.0):
    """Lost-and-found weight along an outline: ~1 where the surface just inside it is in shadow,
    down to `floor` where it is fully lit (the line thins and almost disappears into the light)."""
    r = resample(pts, 0.5) if plen(pts) > 1 else np.asarray(pts, float)
    inward = -side * normals2d(r)
    q = r + inward * probe
    nl = gb_sample(gb, q, "ndotl_soft" if "ndotl_soft" in gb else "ndotl")
    lit = gb_sample(gb, q, "lit")
    hit = gb_sample(gb, q, "hit")
    light = np.where(hit, np.clip(nl, 0, 1) * lit, 0.0)
    w = np.clip((light_hi - light) / (light_hi - light_lo), floor, 1.0)
    k = max(1, int(smooth_mm_ / 0.5))
    w = np.convolve(np.pad(w, k, mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), "valid")
    return np.c_[_arclen(r), w]


def line_strokes(trace, **params):
    """Visible runs of the traced feature lines as strokes {"pts", "role", "kind"}. With a focus
    in the trace, interior-line thresholds, minimum lengths and smoothing follow the attention:
    rigorous detail on the focal region, calmer periphery."""
    p = {**LINE_DEFAULTS, **params}
    step = trace["params"]["step_mm"]
    gb = trace["gbuffer"]
    has_focus = bool(np.any(gb.get("attn", np.zeros(1)) > 0))
    f_lo, f_hi = (p["periphery_line_scale"], p["focus_line_scale"]) if has_focus else (1.0, 1.0)
    out = []
    if p["outline_from_mask"]:
        for xy, closed in mask_outline(gb):
            out.append({"pts": xy, "role": "outline", "kind": "silhouette", "closed": closed, "attn": 0.0})
    for ln in trace["lines"]:
        kind, xy, vis = ln["kind"], ln["xy"], ln["vis"]
        if kind == "suggestive" and not p["suggestive"]:
            continue
        if kind in ("crease", "boundary") and not p["creases"]:
            continue
        if kind == "valley" and not p["valleys"] or kind == "ridge" and not p["ridges"]:
            continue
        la = ln.get("attn", np.zeros(len(xy)))
        scale = (1 - la) * f_lo + la * f_hi
        ok = vis.copy()
        if kind == "suggestive" and p["suggestive_min_strength"] > 0:
            ok &= ln["strength"] >= p["suggestive_min_strength"] * scale
        if kind == "valley":
            ok &= ln["strength"] >= p["valley_min_strength"] * scale
        if kind == "ridge":
            ok &= ln["strength"] >= p["ridge_min_strength"] * scale
        runs = _runs(ok)
        if ln["closed"] and len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == len(ok):
            # a closed curve whose visible part wraps around the start
            a0, b0 = runs.pop(0)
            a1, b1 = runs.pop()
            runs.append((a1, b0 + len(ok)))
        for a, b in runs:
            idx = np.arange(a, b) % len(ok)
            if len(idx) < 2:
                continue
            pts = xy[idx]
            a_run = float(np.median(la[idx]))
            if kind in ("valley", "ridge"):
                accent = (kind == "valley" and has_focus and a_run >= p["accent_min_attn"] and
                          float(np.median(ln["strength"][idx])) >= p["accent_min_strength"] * float(np.median(scale[idx])))
                out.append({"pts": pts, "role": "accent" if accent else "detail", "kind": kind, "attn": a_run})
                continue
            if kind == "contour":
                flag = _majority(ln["outline"][idx], max(2, int(p["outline_min_piece_mm"] / step)))
                gap = ln["gap"][idx]
                for c, d in _runs(flag) + _runs(~flag):
                    piece = pts[max(0, c - 1):d + 1] if c > 0 else pts[c:d + 1]
                    if len(piece) < 2 or (flag[c] and p["outline_from_mask"]):
                        continue
                    if flag[c]:
                        role = "outline"
                    else:
                        g = np.median(np.where(np.isfinite(gap[c:d]), gap[c:d], 1e3))
                        role = "inner" if (plen(piece) >= p["bold_min_len_mm"] and g >= p["bold_min_gap"]) \
                            else "inner_short"
                    out.append({"pts": piece, "role": role, "kind": kind, "attn": a_run})
            elif kind == "suggestive":
                out.append({"pts": _trim(pts, p["suggestive_trim_mm"]), "role": "detail", "kind": kind, "attn": a_run})
            else:
                # sharp edges (scan cuts): outline where one side is background
                n = normals2d(pts)
                bg = ~(gb_sample(gb, pts + n * p["probe_mm"], "hit") & gb_sample(gb, pts - n * p["probe_mm"], "hit"))
                flag = _majority(bg, max(2, int(p["outline_min_piece_mm"] / step)))
                for c, d in _runs(flag) + _runs(~flag):
                    piece = pts[max(0, c - 1):d + 1] if c > 0 else pts[c:d + 1]
                    if len(piece) >= 2 and not (flag[c] and p["outline_from_mask"]):
                        out.append({"pts": piece, "role": "outline" if flag[c] else "crease", "kind": kind,
                                    "attn": a_run})
    # smooth + simplify (crisper and shorter allowed where attention is high), drop short pieces,
    # then priority clipping
    res = []
    sig = p["smooth_mm"] if isinstance(p["smooth_mm"], dict) else {}

    def min_len(st):
        a = st.get("attn", 0.0) if has_focus else 0.0
        return p["min_len_mm"].get(st["role"], 1.5) * ((1 - a) + a * p["focus_min_len_scale"])

    for st in out:
        a = st.get("attn", 0.0) if has_focus else 0.0
        s = sig.get(st["role"], p["smooth_mm"] if not isinstance(p["smooth_mm"], dict) else 0.5)
        s *= (1 - a) + a * p["focus_smooth_scale"]
        pts = rdp(smooth_mm(st["pts"], s, closed=st.get("closed", False)), p["simplify_mm"])
        if plen(pts) >= min_len(st):
            res.append({**st, "pts": pts})
    res.sort(key=lambda s: (ROLE_PRIORITY.get(s["role"], 50), -plen(s["pts"])))
    res = clip_against(res, trace["page"], p["min_gap_mm"])
    final = []
    for st in res:
        pts = rdp(st["pts"], p["simplify_mm"])
        if plen(pts) >= min_len(st):
            st = {**st, "pts": pts}
            if st["role"] in ("outline", "inner"):
                st["side"] = outward_side(pts, gb)
                if st["role"] == "outline" and p["light_weighted_outline"]:
                    st["weight"] = _outline_weight(pts, st["side"], gb)
            final.append(st)
    return final


TONE_DEFAULTS = dict(
    ambient=0.32,      # weight of the ambient (AO) light
    key=0.9,           # weight of the key light (Lambert x shadow rays)
    form_shading=0.7,  # 0 = shade with the detailed normals, 1 = with smoothed (form) normals
    ao_strength=1.0,   # 0 = ignore AO, 1 = full AO darkening
    wrap=0.0,          # >0 softens the terminator (wrap lighting)
    gamma=1.0,         # tone curve (>1 darkens mid-tones)
    blur_mm=0.8,       # artists simplify tone: smooth away scan-scale noise
    # inside a focus (attention 1), blended smoothly with the values above:
    focus_form_shading=0.1,  # model the small planes (eye sockets, nose, lips)
    focus_blur_mm=0.3,
    focus_gamma=1.0,         # > 1 deepens the darks of the focus
    focus_key=None,          # key-light weight for the focus light (None = key)
)


def attention_map(gb):
    """Per-pixel attention (H, W), zeros when the trace has no focus."""
    H, W = gb["shape"]
    a = gb.get("attn")
    return np.zeros((H, W)) if a is None else np.asarray(a, float).reshape(H, W)


def tone_map(gb, **params):
    """Per-pixel tone (H, W) on the G-buffer grid, 1 = paper white, 0 = black. Inside a focus
    the shading uses the detailed normals, less blur and (if traced) the portrait light."""
    p = {**TONE_DEFAULTS, **params}
    H, W = gb["shape"]
    hit = gb["hit"].reshape(H, W)
    a = attention_map(gb)
    ao = 1.0 - p["ao_strength"] * (1.0 - gb["ao"].reshape(H, W))
    sqao = np.sqrt(np.clip(ao, 0, 1))
    fs = (1 - a) * p["form_shading"] + a * p["focus_form_shading"]

    def shade(nl, nl_soft, lit, key):
        if nl_soft is not None:
            nl = (1 - fs) * nl + fs * nl_soft
        if p["wrap"] > 0:
            nl = (nl + p["wrap"]) / (1 + p["wrap"])
        return np.clip(p["ambient"] * ao + key * np.clip(nl, 0, 1) * lit * sqao, 0, 1)

    soft = gb["ndotl_soft"].reshape(H, W) if "ndotl_soft" in gb else None
    t = shade(gb["ndotl"].reshape(H, W), soft, gb["lit"].reshape(H, W), p["key"])
    if "ndotl_f" in gb:
        soft_f = gb["ndotl_f_soft"].reshape(H, W) if "ndotl_f_soft" in gb else None
        key_f = p["key"] if p["focus_key"] is None else p["focus_key"]
        t_f = shade(gb["ndotl_f"].reshape(H, W), soft_f, gb["lit_f"].reshape(H, W), key_f)
        t = (1 - a) * t + a * t_f
    t = t ** ((1 - a) * p["gamma"] + a * p["focus_gamma"])
    t_lo = masked_blur(t, hit, p["blur_mm"] / gb["px"])
    if a.any():
        t_hi = masked_blur(t, hit, p["focus_blur_mm"] / gb["px"])
        t_lo = (1 - a) * t_lo + a * t_hi
    return np.where(hit, np.clip(t_lo, 0, 1), 1.0)


def _doubled_angle(theta):
    return np.stack([np.cos(2 * theta), np.sin(2 * theta)], -1)


def _isophote_field(tone, px, blur_px=None):
    """Doubled-angle field along the tone bands, weighted by the (normalised) tone gradient."""
    t = blur(tone, blur_px) / blur(np.ones_like(tone), blur_px) if blur_px else tone
    gy, gx = np.gradient(t)
    g = np.hypot(gx, gy)
    theta = np.arctan2(gx, -gy)
    return _doubled_angle(theta) * (g / max(np.percentile(g[g > 0], 95) if (g > 0).any() else 1.0, 1e-9))[..., None]


def hatch_field(gb, tone, field="principal", slice_weight=0.35, screen_weight=0.08, angle_deg=45.0,
                field_blur_mm=2.0, focus_mix=None, focus_angle_deg=None):
    """Unit hatching directions (H, W, 2) in page coordinates (y down) from the trace's line
    fields (doubled-angle vectors weighted by confidence), blended and smoothed.
    focus_mix {"principal", "slice", "isophote", "screen"} weights: the field used where the
    attention is 1 (a portrait reads best with calm, near-parallel strokes that follow the
    planes), blended with the base field by attention."""
    H, W = gb["shape"]
    hit = gb["hit"].reshape(H, W)
    f = gb["fields"]

    def screen_f(deg):
        a = math.radians(deg)
        return np.broadcast_to(np.array([math.cos(2 * a), -math.sin(2 * a)]), (H, W, 2))   # page y down

    if field == "principal":
        F = f["principal"] + slice_weight * f["slice"]
    elif field == "slice":
        F = f["slice"].copy()
    elif field == "isophote":
        F = _isophote_field(tone, gb["px"])
    else:
        F = np.zeros((H, W, 2))
    F = F + (screen_weight if field != "screen" else 1.0) * screen_f(angle_deg)
    att = attention_map(gb)
    if focus_mix and att.any():
        Ff = np.zeros((H, W, 2))
        for name, w in focus_mix.items():
            if not w:
                continue
            if name == "isophote":
                Ff += w * _isophote_field(tone, gb["px"], 1.0 / gb["px"])
            elif name == "screen":
                Ff += w * screen_f(angle_deg if focus_angle_deg is None else focus_angle_deg)
            else:
                Ff += w * f[name]
        F = (1 - att[..., None]) * F + att[..., None] * Ff
    F = masked_blur(F, hit, field_blur_mm / gb["px"])
    theta = 0.5 * np.arctan2(F[..., 1], F[..., 0])
    return np.stack([np.cos(theta), np.sin(theta)], -1)


HATCH_DEFAULTS = dict(
    field="principal",       # principal (cross-contour) | slice | isophote | screen
    slice_weight=0.35,       # where curvature is isotropic, follow cross-sections instead
    screen_weight=0.08,      # a little of a fixed paper angle everywhere, for calm
    angle_deg=45.0,          # the screen angle (counter-clockwise from +x on the page)
    field_blur_mm=2.0,
    passes=[                 # one pass per tone band; darker = denser; later passes cross
        {"below": 0.62, "spacing_mm": [1.7, 0.9], "angle_offset_deg": 0},
        {"below": 0.38, "spacing_mm": [1.5, 0.85], "angle_offset_deg": 72},
        {"below": 0.17, "spacing_mm": [1.3, 0.8], "angle_offset_deg": -38},
    ],
    dark_tone=0.05,          # tone at which a pass reaches its minimum spacing
    density=1.0,             # > 1 = more lines (spacing / density)
    step_mm=0.3,
    test_ratio=0.6,          # a line stops closer than test_ratio x spacing to another line
    seed_ratio=0.95,         # a new line may start no closer than seed_ratio x spacing
    min_len_mm=2.5,
    max_len_mm=45.0,
    feather=0.05,            # per-line jitter of the tone threshold: staggered line ends
    edge_gap_mm=0.35,        # stop this far from occluding edges and bold contours
    max_turn_deg=28.0,
    pen_ratio=2.5,           # spacing never below pen_ratio x the hatch pen's width
    seed=11,
    # attention (only when the trace has a focus): finer, shorter, more deliberate strokes on the
    # focal region, a little sparser elsewhere - the hierarchy a draughtsman gives a portrait
    focus_spacing=0.6,       # spacing multiplier at attention 1
    periphery_spacing=1.1,   # spacing multiplier at attention 0
    focus_max_len_mm=12.0,
    focus_min_len_mm=1.6,
    focus_mix=None,          # field at attention 1, e.g. {"screen": 0.6, "isophote": 0.4, "principal": 0.3}
    focus_angle_deg=None,    # its screen angle (None = angle_deg)
    focus_cross_spacing=1.0, # spacing multiplier of the crossing passes (>= 2nd) at attention 1:
                             # finer single hatching models the face, cross-hatching stays open
    feature_gap_mm=0.3,      # hatching stops this short of the focal feature lines (eyes, lips)
    cross_min_len_mm=3.0,    # crossing passes (>= 2nd): no shorter strokes (avoids stray 'x' marks)
)


class _Grid:
    """Flat byte raster over a page window (cell mm), shared by numpy (painting) and fast
    Python indexing (tests during tracing)."""

    def __init__(self, box, cell):
        self.x0, self.y0 = box[0], box[1]
        self.cell = cell
        self.W = int(math.ceil((box[2] - box[0]) / cell)) + 1
        self.H = int(math.ceil((box[3] - box[1]) / cell)) + 1
        self.buf = bytearray(self.W * self.H)
        self.np = np.frombuffer(self.buf, dtype=np.uint8).reshape(self.H, self.W)
        self._discs = {}

    def index(self, x, y):
        j = int((x - self.x0) / self.cell)
        i = int((y - self.y0) / self.cell)
        if 0 <= i < self.H and 0 <= j < self.W:
            return i * self.W + j
        return -1

    def disc(self, r):
        k = max(0, int(round(r / self.cell)))
        if k not in self._discs:
            y, x = np.mgrid[-k:k + 1, -k:k + 1]
            m = x * x + y * y <= k * k + 0.25
            self._discs[k] = (y[m], x[m])
        return self._discs[k]

    def paint(self, pts, radii):
        """Mark cells within radii[i] of pts[i] (radii grouped to the cell size)."""
        pts = np.asarray(pts, float)
        if not len(pts):
            return
        radii = np.broadcast_to(np.asarray(radii, float), (len(pts),))
        i = ((pts[:, 1] - self.y0) / self.cell).astype(np.int64)
        j = ((pts[:, 0] - self.x0) / self.cell).astype(np.int64)
        ks = np.round(radii / self.cell).astype(np.int64)
        for k in np.unique(ks):
            sel = ks == k
            dy, dx = self.disc(k * self.cell)
            I = i[sel][:, None] + dy[None, :]
            J = j[sel][:, None] + dx[None, :]
            ok = (I >= 0) & (I < self.H) & (J >= 0) & (J < self.W)
            self.np[I[ok], J[ok]] = 1


def _densify(pl, step):
    return resample(pl, step) if len(pl) > 1 else np.asarray(pl, float)


def hatch_strokes(trace, tone_cfg=None, obstacles=(), pen_width=0.2, **params):
    """Evenly spaced streamline hatching (Jobard & Lefer 1997, with tone-dependent spacing),
    one pass per tone band. obstacles: strokes [(k, 2)] the hatching keeps edge_gap_mm away
    from (the bold contours). Returns strokes {"pts", "role": "hatch_<pass>"}."""
    p = {**HATCH_DEFAULTS, **params}
    gb = trace["gbuffer"]
    H, W = gb["shape"]
    px, gx0, gy0 = gb["px"], gb["x0"], gb["y0"]
    tone = tone_map(gb, **(tone_cfg or {}))
    field = hatch_field(gb, tone, p["field"], p["slice_weight"], p["screen_weight"], p["angle_deg"],
                        p["field_blur_mm"], p["focus_mix"], p["focus_angle_deg"])
    hit_l = bytes(gb["hit"].astype(np.uint8))
    tone_l = array.array("f", tone.astype(np.float32).ravel().tobytes())
    fx_l = array.array("f", field[..., 0].astype(np.float32).ravel().tobytes())
    fy_l = array.array("f", field[..., 1].astype(np.float32).ravel().tobytes())
    attn = attention_map(gb)
    if attn.any():
        mult = (1 - attn) * p["periphery_spacing"] + attn * p["focus_spacing"]
        mult_x = (1 - attn) * p["periphery_spacing"] + attn * p["focus_cross_spacing"]
    else:
        mult = mult_x = np.ones_like(attn)
    mult_l0 = array.array("f", mult.astype(np.float32).ravel().tobytes())
    mult_lx = array.array("f", mult_x.astype(np.float32).ravel().tobytes())
    attn_l = array.array("f", attn.astype(np.float32).ravel().tobytes())
    box = (gx0, gy0, gx0 + W * px, gy0 + H * px)
    cell = 0.1
    # stop raster: occluding edges + background border + bold contours, grown by the gap
    stop = _Grid(box, cell)
    ei = np.flatnonzero(gb["edges"])
    epts = np.c_[gx0 + (ei % W + 0.5) * px, gy0 + (ei // W + 0.5) * px]
    stop.paint(epts, p["edge_gap_mm"])
    for ob in obstacles:
        gap = p["feature_gap_mm"] if ob.get("feature") else p["edge_gap_mm"]
        stop.paint(_densify(ob["pts"], cell), gap + ob.get("width", 0.4) / 2 + pen_width / 2)
    stop_b = stop.buf
    rng = np.random.default_rng(p["seed"])
    h = p["step_mm"]
    max_turn = math.cos(math.radians(p["max_turn_deg"]))
    min_sp = p["pen_ratio"] * pen_width
    out = []

    def gidx(x, y):
        j = int((x - gx0) / px)
        i = int((y - gy0) / px)
        if 0 <= i < H and 0 <= j < W:
            return i * W + j
        return -1

    for k, ps in enumerate(p["passes"]):
        thr = ps["below"]
        s_hi, s_lo = [max(min_sp, s / p["density"]) for s in ps["spacing_mm"]]
        dark = p["dark_tone"]
        ca, sa = math.cos(math.radians(-ps.get("angle_offset_deg", 0))), math.sin(math.radians(-ps.get("angle_offset_deg", 0)))
        occ_t, occ_s = _Grid(box, cell), _Grid(box, cell)
        bt, bs = occ_t.buf, occ_s.buf
        mult_l = mult_l0 if k == 0 else mult_lx

        def spacing(t, g):
            u = (thr - t) / max(thr - dark, 1e-6)
            u = 0.0 if u < 0 else (1.0 if u > 1 else u)
            return max(min_sp, (s_hi + (s_lo - s_hi) * u) * mult_l[g])

        def direction(x, y, px_dir):
            gi = gidx(x, y)
            if gi < 0 or not hit_l[gi]:
                return None
            dx, dy = fx_l[gi], fy_l[gi]
            dx, dy = dx * ca - dy * sa, dx * sa + dy * ca
            if dx * px_dir[0] + dy * px_dir[1] < 0:
                dx, dy = -dx, -dy
            return dx, dy

        def trace_dir(x, y, d0, line_thr, max_len):
            pts, L = [], 0.0
            d = d0
            while L < max_len:
                d1 = direction(x, y, d)
                if d1 is None:
                    break
                mx, my = x + 0.5 * h * d1[0], y + 0.5 * h * d1[1]
                d2 = direction(mx, my, d1)
                if d2 is None:
                    break
                if d2[0] * d[0] + d2[1] * d[1] < max_turn:
                    break
                nx, ny = x + h * d2[0], y + h * d2[1]
                gi = gidx(nx, ny)
                if gi < 0 or not hit_l[gi]:
                    break
                t = tone_l[gi]
                if t > line_thr:
                    break
                oi = occ_t.index(nx, ny)
                if oi < 0 or bt[oi] or stop_b[oi]:
                    break
                # same-pass neighbours closer than test_ratio x local spacing stop the line
                pts.append((nx, ny))
                x, y, d = nx, ny, d2
                L += h
            return pts

        def try_seed(x, y):
            gi = gidx(x, y)
            if gi < 0 or not hit_l[gi]:
                return None
            t = tone_l[gi]
            if t >= thr:
                return None
            oi = occ_s.index(x, y)
            if oi < 0 or bs[oi] or stop_b[oi]:
                return None
            d0 = direction(x, y, (1.0, 0.0))
            if d0 is None:
                return None
            line_thr = thr + p["feather"] * (2 * rng.random() - 1)
            a_seed = attn_l[gi]
            max_len = ((1 - a_seed) * p["max_len_mm"] + a_seed * p["focus_max_len_mm"]) * (0.7 + 0.3 * rng.random())
            min_len = (1 - a_seed) * p["min_len_mm"] + a_seed * p["focus_min_len_mm"]
            if k > 0:
                min_len = max(min_len, p["cross_min_len_mm"])
            fwd = trace_dir(x, y, d0, line_thr, max_len / 2)
            bwd = trace_dir(x, y, (-d0[0], -d0[1]), line_thr, max_len / 2)
            pts = bwd[::-1] + [(x, y)] + fwd
            if len(pts) < 2 or (len(pts) - 1) * h < min_len:
                occ_s.paint([(x, y)], cell)       # do not retry this spot
                return None
            arr = np.array(pts)
            gi_arr = [gidx(a, b) for a, b in pts]
            sp = np.array([spacing(tone_l[g], g) for g in gi_arr])
            occ_t.paint(arr, p["test_ratio"] * sp)
            occ_s.paint(arr, p["seed_ratio"] * sp)
            out.append({"pts": arr, "role": f"hatch_{k}", "spacing": sp})
            return arr, sp

        # candidate seeds on a lattice over the pass region, darkest first
        lat = max(1, int(max(min_sp, s_lo * float((mult if k == 0 else mult_x).min())) / px))
        ii, jj = np.mgrid[0:H:lat, 0:W:lat]
        gi = (ii * W + jj).ravel()
        tv = tone.ravel()[gi]
        cand = gi[(tv < thr) & gb["hit"][gi]]
        cand = cand[np.argsort(tone.ravel()[cand], kind="stable")]
        queue = deque()
        ci = 0
        while True:
            if queue:
                x, y = queue.popleft()
            elif ci < len(cand):
                g = int(cand[ci])
                ci += 1
                x, y = gx0 + (g % W + 0.5) * px, gy0 + (g // W + 0.5) * px
            else:
                break
            res = try_seed(x, y)
            if res is None:
                continue
            arr, sp = res
            # Jobard-Lefer: new seeds one spacing to each side, every ~spacing along the line
            if len(arr) < 3:
                continue
            nrm = normals2d(arr)
            stride = max(1, int(np.median(sp) / h))
            for idx in range(0, len(arr), stride):
                for sgn in (1.0, -1.0):
                    queue.append((arr[idx, 0] + sgn * nrm[idx, 0] * sp[idx], arr[idx, 1] + sgn * nrm[idx, 1] * sp[idx]))
    for st in out:
        st["pts"] = rdp(smooth_mm(st["pts"], 0.3), 0.04)
    return out


STYLE_DEFAULTS = dict(
    seed=7,
    wobble_mm=0.07,             # low-frequency hand wobble (perpendicular), 0 = off
    wobble_wavelength_mm=18.0,
    wobble_roles=("outline", "inner", "inner_short", "crease", "detail"),
    overshoot_mm=0.0,           # extend free stroke ends along their tangent
    double_roles=("outline",),  # roles drawn with a tapered return pass (heavier line)
    pass_offset_mm=0.16,
    taper_mm=6.0,
    double_min_len_mm=8.0,
    accent_pass_offset_mm=0.11,  # accents (lip line, lid crease): a short tapered return pass
    accent_taper_mm=1.5,
)


def wobble(pts, amp, wavelength, rng, ends_mm=3.0):
    """Smooth perpendicular displacement that fades to zero at both ends."""
    if amp <= 0 or len(pts) < 2:
        return pts
    pts = resample(pts, min(0.5, wavelength / 12))
    s = _arclen(pts)
    L = s[-1]
    ph = rng.random(3) * 2 * math.pi
    f = rng.uniform(0.8, 1.25, 3) / wavelength
    w = (0.6 * np.sin(2 * math.pi * f[0] * s + ph[0]) + 0.3 * np.sin(2 * math.pi * f[1] * 2.3 * s + ph[1])
         + 0.1 * np.sin(2 * math.pi * f[2] * 5.1 * s + ph[2]))
    fade = np.clip(np.minimum(s, L - s) / ends_mm, 0, 1)
    return pts + normals2d(pts) * (amp * w * fade)[:, None]


def double_pass(pts, offset, taper, side=1.0, weight=None):
    """Return path offset to one side with tapered ends, appended to the stroke: the pen goes
    out along the line and comes back beside it, so the line thickens in the middle and stays
    thin at its ends - without an extra pen lift. weight: optional (n, 2) [arc length, 0-1]
    profile scaling the offset (lost and found: thick in shadow, thin in light)."""
    pts = resample(pts, 0.25)
    s = _arclen(pts)
    L = s[-1]
    ramp = np.clip(np.minimum(s, L - s) / max(taper, 1e-6), 0, 1)
    ramp = ramp * ramp * (3 - 2 * ramp)
    if weight is not None and len(weight) > 1:
        ws = weight[:, 0] * (L / max(weight[-1, 0], 1e-9))
        ramp = ramp * np.interp(s, ws, weight[:, 1])
    back = pts + normals2d(pts) * (side * offset * ramp)[:, None]
    return np.vstack([pts, back[::-1][1:]])


def style_strokes(strokes, **params):
    p = {**STYLE_DEFAULTS, **params}
    rng = np.random.default_rng(p["seed"])
    out = []
    for st in strokes:
        pts = np.asarray(st["pts"], float)
        if st["role"] in p["wobble_roles"]:
            pts = wobble(pts, p["wobble_mm"], p["wobble_wavelength_mm"], rng)
        if p["overshoot_mm"] > 0 and st["role"] in ("outline", "inner", "crease") and len(pts) > 1:
            e = p["overshoot_mm"] * rng.uniform(0.4, 1.0, 2)
            t0 = pts[0] - pts[min(3, len(pts) - 1)]
            t1 = pts[-1] - pts[max(-4, -len(pts))]
            t0 /= max(np.linalg.norm(t0), 1e-9)
            t1 /= max(np.linalg.norm(t1), 1e-9)
            pts = np.vstack([pts[0] + t0 * e[0], pts, pts[-1] + t1 * e[1]])
        if st["role"] in p["double_roles"] and plen(pts) >= p["double_min_len_mm"]:
            pts = double_pass(pts, p["pass_offset_mm"], p["taper_mm"], st.get("side", 1.0), st.get("weight"))
        elif st["role"] == "accent" and plen(pts) >= 2 * p["accent_taper_mm"]:
            pts = double_pass(pts, p["accent_pass_offset_mm"], p["accent_taper_mm"], 1.0)
        out.append({**st, "pts": rdp(pts, 0.02)})
    return out


def order_strokes(strokes, reverse_ok=True, join_mm=0.0, start=(0.0, 0.0)):
    """Greedy nearest-neighbour ordering on a grid of stroke ends; a stroke whose start is
    within join_mm of the pen is appended to the current path (no pen lift)."""
    if not strokes:
        return [], 0.0
    ends = [np.asarray(s, float) for s in strokes]
    n = len(ends)
    P0 = np.array([e[0] for e in ends])
    P1 = np.array([e[-1] for e in ends])
    cell = 5.0
    grid = {}
    for i in range(n):
        for k, q in ((0, P0[i]), (1, P1[i])):
            if k == 1 and not reverse_ok:
                continue
            grid.setdefault((int(q[0] // cell), int(q[1] // cell)), []).append((i, k))
    used = np.zeros(n, bool)
    pos = np.asarray(start, float)
    paths, travel = [], 0.0
    remaining = n
    while remaining:
        cx, cy = int(pos[0] // cell), int(pos[1] // cell)
        best, r = None, 0
        while best is None or r <= best[0] / cell + 1:
            cands = []
            for gx in range(cx - r, cx + r + 1):
                for gy in (range(cy - r, cy + r + 1) if abs(gx - cx) == r else (cy - r, cy + r)):
                    for i, k in grid.get((gx, gy), ()):
                        if not used[i]:
                            q = P0[i] if k == 0 else P1[i]
                            cands.append((math.hypot(q[0] - pos[0], q[1] - pos[1]), i, k))
            if cands:
                c = min(cands)
                if best is None or c[0] < best[0]:
                    best = c
            r += 1
            if r > 400:
                break
        d, i, k = best
        used[i] = True
        remaining -= 1
        pl = ends[i] if k == 0 else ends[i][::-1]
        if paths and d <= join_mm:
            paths[-1] = np.vstack([paths[-1], pl])
        else:
            travel += d
            paths.append(pl)
        pos = paths[-1][-1]
    return paths, travel


def pen_layers(strokes, pens, style=None):
    """Assign roles to pens, style, then order/join per pen. Returns layers
    [{"name", "width", "label", "strokes": [(k, 2) arrays], "travel", "roles"}] in plot order."""
    style = style or {}
    by_role = {}
    for i, pen in enumerate(pens):
        for role in pen["roles"]:
            by_role[role] = i
    styled = style_strokes(strokes, **style)
    layers = []
    for i, pen in enumerate(pens):
        mine = [s for s in styled if by_role.get(s["role"], 0) == i]
        min_len = pen.get("min_len_mm", 1.0)
        pls = [s["pts"] for s in mine if plen(s["pts"]) >= min_len]
        if pen.get("direction", "any") == "consistent":
            ang = math.radians(pen.get("direction_deg", 45.0))
            u = np.array([math.cos(ang), -math.sin(ang)])      # page y points down
            pls = [pl if (pl[-1] - pl[0]) @ u >= 0 else pl[::-1] for pl in pls]
            paths, travel = order_strokes(pls, reverse_ok=False, join_mm=0.0)
        else:
            paths, travel = order_strokes(pls, reverse_ok=True, join_mm=pen.get("join_mm", 0.0))
        layers.append({"name": pen["name"], "width": float(pen["width_mm"]), "strokes": paths,
                       "travel": travel, "roles": sorted({s["role"] for s in mine}),
                       "min_len": min_len})
    order = sorted(range(len(layers)), key=lambda k: pens[k].get("order", k))
    layers = [layers[k] for k in order]
    for n, L in enumerate(layers, 1):
        L["label"] = f"{n} {L['name']} {L['width']:.2f}mm"
    return layers


# ---------------------------------------------------------------- the pieces' Augustus settings
# (projects/blender3d_project/augustus/augustus_sketch/augustus_sketch.json: the tuned values of its plates)

AUGUSTUS = {
    "lines": dict(bold_min_len_mm=10, bold_min_gap=0.04, suggestive=True, suggestive_min_strength=2.0,
                  suggestive_trim_mm=0.4, creases=True, min_gap_mm=0.3, simplify_mm=0.03, valleys=True,
                  valley_min_strength=110, focus_line_scale=0.45, periphery_line_scale=1.25, accent_min_strength=170,
                  light_weighted_outline=True),
    "style": dict(seed=7, wobble_mm=0.07, wobble_wavelength_mm=18, overshoot_mm=0.0, double_roles=("outline",),
                  pass_offset_mm=0.16, taper_mm=6),
    "tone": dict(ambient=0.42, key=0.85, form_shading=0.7, ao_strength=0.6, wrap=0.0, gamma=1.0, blur_mm=0.8,
                 focus_form_shading=0.1, focus_blur_mm=0.3, focus_gamma=1.25),
    "hatching": dict(field="principal", slice_weight=0.35, screen_weight=0.08, angle_deg=45, field_blur_mm=2.0,
                     passes=[{"below": 0.55, "spacing_mm": [1.8, 1.0], "angle_offset_deg": 0},
                             {"below": 0.3, "spacing_mm": [1.6, 0.95], "angle_offset_deg": 72},
                             {"below": 0.13, "spacing_mm": [1.4, 0.9], "angle_offset_deg": -38}],
                     dark_tone=0.05, density=1.0, step_mm=0.3, min_len_mm=2.5, max_len_mm=45, feather=0.05,
                     edge_gap_mm=0.35, focus_spacing=0.6, periphery_spacing=1.1, focus_max_len_mm=12,
                     focus_mix={"screen": 1.0, "isophote": 0.25}, focus_angle_deg=45, focus_cross_spacing=1.0,
                     feature_gap_mm=0.3),
    "analysis": dict(contour_smooth=2, line_smooth=24, field_smooth=120, tone_smooth=80, crease_angle=55,
                     ao_rays=24, ao_distance=0.18),
    "trace": dict(px_per_mm=5, step_mm=0.3, feature_size=0.03, sc_threshold=1.0, shadow_samples=4),
}
ENGRAVING_PASSES = [{"below": 0.62, "spacing_mm": [1.7, 0.9], "angle_offset_deg": 0},
                    {"below": 0.38, "spacing_mm": [1.5, 0.85], "angle_offset_deg": 72},
                    {"below": 0.17, "spacing_mm": [1.3, 0.8], "angle_offset_deg": -38}]
FINE_ROLES = ["hatch_0", "hatch_1", "hatch_2", "hatch_3", "detail", "accent", "crease", "inner_short"]
BOLD_ROLES = ["outline", "inner"]


def apply_complexity(pc, c):
    """Scale a plate's drawing config by the master complexity knob: 0 = sparse, 0.5 = as configured, 1 = rich.
    It sets how many interior lines survive (suggestive-contour strength and length), the hatching density and
    how many tone passes are drawn (the pieces' sketch_runner.apply_complexity)."""
    if c is None or abs(c - 0.5) < 1e-9:
        return pc
    pc = json.loads(json.dumps(pc))
    f = 2.0 ** ((0.5 - c) * 2)                       # c = 0 -> x2, c = 1 -> x0.5
    ln = pc["lines"]
    ln["suggestive_min_strength"] = ln.get("suggestive_min_strength", 1.0) * f
    ml = dict(LINE_DEFAULTS["min_len_mm"], **ln.get("min_len_mm", {}))
    ml["detail"] = ml["detail"] * math.sqrt(f)
    ln["min_len_mm"] = ml
    ht = pc["hatching"]
    ht["density"] = ht.get("density", 1.0) * 2.0 ** (c - 0.5)
    passes = ht.get("passes", HATCH_DEFAULTS["passes"])
    if c < 0.5:
        ht["passes"] = passes[:max(1, round(len(passes) * (0.4 + 1.2 * c)))]
    return pc


def plate_strokes(tr, pc, cancel=None):
    """Feature-line strokes and (if enabled) hatching for one plate config. Returns (strokes, number of lines)."""
    strokes = line_strokes(tr, **pc["lines"])
    n_lines = len(strokes)
    if pc["hatching"].get("enabled"):
        if cancel is not None:
            cancel()
        pen_of = {role: pen for pen in pc["pens"] for role in pen["roles"]}
        bold = [{"pts": s["pts"], "width": pen_of.get(s["role"], pc["pens"][0])["width_mm"]}
                for s in strokes if s["role"] in ("outline", "inner")]
        # on the focal region the hatching leaves the feature lines (eyes, lips) readable
        bold += [{"pts": s["pts"], "width": pen_of.get(s["role"], pc["pens"][0])["width_mm"], "feature": True}
                 for s in strokes if s["role"] == "accent" or (s["role"] == "detail" and s.get("attn", 0) > 0.5)]
        hatch_w = pen_of.get("hatch_0", pc["pens"][0])["width_mm"]
        strokes += hatch_strokes(tr, pc["tone"], obstacles=bold, pen_width=hatch_w,
                                 **{k: v for k, v in pc["hatching"].items() if k != "enabled"})
    return strokes, n_lines


def budget_layers(tr, pc, max_ink_m=0.0, max_strokes=0, cancel=None):
    """Strokes -> pen layers; with a budget the hatching gets sparser (larger spacing) until the plate fits, the
    pieces' loop (at most 6 rounds; if the lines alone are over budget there is no hatching)."""
    for _ in range(6):
        strokes, n_lines = plate_strokes(tr, pc, cancel)
        layers = pen_layers(strokes, pc["pens"], pc["style"])
        if not pc["hatching"].get("enabled") or not (max_ink_m or max_strokes):
            break
        hatch_pen = {L["name"] for L in layers if any(r.startswith("hatch") for r in L["roles"])}
        ink_all = sum(plen(pl) for L in layers for pl in L["strokes"]) / 1000.0
        ink_h = sum(plen(s["pts"]) for s in strokes if s["role"].startswith("hatch")) / 1000.0
        n_all = sum(len(L["strokes"]) for L in layers)
        n_h = len(strokes) - n_lines
        factors = []
        if max_ink_m and ink_all > max_ink_m * 1.02:
            factors.append((max_ink_m - (ink_all - ink_h)) / max(ink_h, 1e-6))
        if max_strokes and n_all > max_strokes * 1.02:
            factors.append((max_strokes - (n_all - n_h)) / max(n_h, 1))
        if not factors or not hatch_pen or n_h == 0:
            break
        f = min(factors)
        if f <= 0.05:
            pc["hatching"]["enabled"] = False
            continue
        pc["hatching"]["density"] = pc["hatching"].get("density", 1.0) * f
    return strokes, layers
