"""
plotter_vision.py - plotter.vision for the Atlas Toolkit: a 3D model drawn as the edges you can see, through a
perspective camera, hidden lines removed, for the pen plotter.

A reimplementation of Trammell Hudson's plotter.vision (github.com/osresearch/plotter-vision, the web app in
code/Web/plotter-vision) from its method, not its code: the same camera (θ, ψ, radius, look-at, 60°), the same
choice of lines (triangle edges, except where neighbouring faces are coplanar; faces turned away culled), the same
screen-space hidden-line test against the faces that cover each edge, the same red/blue stereo pair. Then:

    edges      one angle decides which edges between faces show: 0.01° is plotter.vision (only exactly coplanar
               faces merge), a few degrees hides the diagonals of flat and gently curved quads, 30°+ leaves the
               feature lines (creases, silhouettes, open borders) of dense scans
    model      optional decimation to a face budget; consistent winding; open meshes hide what is behind them
               from both sides
    hidden     exact intervals instead of clip-and-split: no stray lines, every face occludes, edges piercing faces
               cut where they enter, faces crossing the near plane cut instead of dropped
    paper      the camera looks through the paper's drawing area (WYSIWYG): lines are cut at the frame, in mm
    pens       optional outline pen, 2-3 depth pens (far ones dashed), hidden lines on their own dashed pen
    plotting   pieces joined into long strokes, simplified and ordered (plotter.vision wrote one path of
               nearest-first segments, each edge often twice)

Outputs <name>_vision.svg, signed <desc>plotter-vision</desc>.
"""
from __future__ import annotations

import math
import time

import numpy as np

from atlas_toolkit.geom.svgout import Layer, join_segments, order_polylines, simplify_all, travel

from . import hidden as hl
from atlas_toolkit.geom.camera import NEAR, Camera, Frame, camera_from, frame_for  # noqa: F401
from .model import Model

RED, BLUE = "#ff0000", "#0000ff"          # plotter.vision's download colours
PEN_COLORS = {"middle": "#2a5fe0", "far": "#7a8499", "hidden": "#c9821b"}    # tell the pens apart in Inkscape
DEPTH_NAMES = {1: ["lines"], 2: ["near", "far"], 3: ["near", "middle", "far"]}


def two_sided(model: Model, sides: str) -> bool:
    return sides == "two" or (sides == "auto" and not model.watertight)


# ----------------------------------------------------------------------------- one camera

class View:
    """What one camera sees of a model: the edges worth drawing, on screen, and the faces that may hide them."""

    def __init__(self, model: Model, cam: Camera, flat_angle: float, sides: str = "auto", occluders: bool = True,
                 back_edges: bool = False, edge_mode: str = 'features'):
        """back_edges: also take the edges of faces turned away, for their hidden parts only (the dashed back
        edges of a technical drawing); their visible parts never show, so the drawing itself is unchanged."""
        self.model, self.cam = model, cam
        V, F, FN = model.V, model.F, model.FN
        eye, u, v, w = cam.basis()
        if cam.ortho:
            front = FN @ w > 0
        else:
            front = np.einsum("ij,ij->i", FN, eye - V[F[:, 0]]) > 0
        both = two_sided(model, sides)
        f1, f2 = model.EF[:, 0], model.EF[:, 1]
        border = f2 < 0
        fr1 = front[f1]
        fr2 = np.where(border, False, front[np.maximum(f2, 0)])
        seen1 = fr1 | both
        seen2 = ~border & (fr2 | both)
        silhouette = ~border & (fr1 != fr2)
        native = model.native if model.native is not None else np.ones(len(model.E), bool)
        selected = native & ((model.EA > flat_angle) if edge_mode == 'features' else True)
        crease = ~border & seen1 & seen2 & (fr1 == fr2) & selected
        draw = silhouette | crease | (border & seen1) | (model.many & (seen1 | seen2))
        back = np.zeros_like(draw)
        if back_edges and not both:
            back = ~draw & (border | model.many | (~border & selected))
        self.edge_ids = np.flatnonzero(draw | back)
        nx = len(model.X)
        self.back = np.r_[back[self.edge_ids], np.zeros(nx, bool)]
        self.outline = np.r_[(silhouette | border)[self.edge_ids], np.zeros(nx, bool)]
        self.own = np.concatenate([model.EF[self.edge_ids], model.XF])
        self.front_faces = int(front.sum())
        occ = np.arange(len(F)) if both else np.flatnonzero(front)
        self.two_sided = both

        C = cam.to_camera(V)
        self.far = float(C[:, 2].max()) + 1.0
        E = model.E[self.edge_ids]
        Ca, Cb = C[E[:, 0]], C[E[:, 1]]
        if nx:                                   # where parts cut into each other
            Ca = np.concatenate([Ca, cam.to_camera(model.X[:, 0])])
            Cb = np.concatenate([Cb, cam.to_camera(model.X[:, 1])])
        Ca, Cb, keep = hl.clip_segments(Ca, Cb, NEAR)
        self.keep = keep
        Ca, Cb = Ca[keep], Cb[keep]
        self.outline, self.own, self.back = self.outline[keep], self.own[keep], self.back[keep]
        self.A, self.B = cam.project(Ca), cam.project(Cb)
        self.qa, self.qb = cam.nearness(Ca[:, 2], self.far), cam.nearness(Cb[:, 2], self.far)
        if not occluders:                         # just the candidates (the live view while dragging)
            self.T, self.TQ, self.tface = np.zeros((0, 3, 2)), np.zeros((0, 3)), np.zeros(0, np.int64)
            return
        Tc, self.tface = hl.clip_triangles(C[F[occ]], occ, NEAR)
        self.T = cam.project(Tc.reshape(-1, 3)).reshape(-1, 3, 2)
        self.TQ = cam.nearness(Tc[:, :, 2], self.far)

    @property
    def edges(self) -> int:
        return len(self.A)

    def candidates(self):
        """Every selected edge on screen, nothing hidden yet (what plotter.vision draws while you drag)."""
        front = ~self.back
        return self.A[front], self.B[front], self.outline[front]

    def pieces(self, region, hidden: bool = False, progress=None, cancel=None, middle_first: bool = False,
               batch_pairs: int = hl.PAIRS):
        """Yields (P0, P1, depth, outline, is_hidden) batches of visible (and hidden) pieces on screen."""
        A, B, qa, qb, ok = hl.clip_to_region(self.A, self.B, self.qa, self.qb, region)
        idx = np.flatnonzero(ok)
        A, B, qa, qb = A[idx], B[idx], qa[idx], qb[idx]
        own, outline, back = self.own[idx], self.outline[idx], self.back[idx]
        order = None
        if middle_first and len(A):
            mid = (A + B) / 2.0
            order = np.argsort(np.hypot(mid[:, 0], mid[:, 1]), kind="stable")
        D, dq = B - A, qb - qa
        for s, t0, t1, hid in hl.solve(A, B, qa, qb, own, self.T, self.TQ, self.tface, region, hidden=hidden,
                                       order=order, batch_pairs=batch_pairs, progress=progress, cancel=cancel):
            if not hid and back.any():                       # back edges only count where hidden
                k = ~back[s]
                s, t0, t1 = s[k], t0[k], t1[k]
            P0 = A[s] + t0[:, None] * D[s]
            P1 = A[s] + t1[:, None] * D[s]
            depth = self.cam.depth_of(qa[s] + 0.5 * (t0 + t1) * dq[s], self.far)
            yield P0, P1, depth, outline[s], hid


def fit_radius(model: Model, cam: Camera, frame: Frame, margin: float = 1.04) -> float:
    """The radius at which the model, seen around the look-at point, just fits the frame's short side."""
    R = float(np.linalg.norm(model.V - np.asarray(cam.lookat), axis=1).max()) * margin
    x0, y0, x1, y1 = frame.screen
    half_short = min(x1, y1)                     # long side spans -1..1
    tan_short = half_short / cam.k
    if cam.ortho:
        return R / max(tan_short, 1e-6)
    return R / max(math.sin(math.atan(tan_short)), 1e-6)


# ----------------------------------------------------------------------------- the drawing

def dashed(polylines: list, dash: float, gap: float) -> list:
    """Polylines cut into dashes (mm)."""
    out = []
    period = dash + gap
    for pl in polylines:
        P = np.asarray(pl, dtype=float)
        if len(P) < 2:
            continue
        seg = np.hypot(*np.diff(P, axis=0).T)
        s = np.r_[0.0, np.cumsum(seg)]
        total = s[-1]
        a = 0.0
        while a < total:
            b = min(a + dash, total)
            ts = np.r_[a, s[(s > a) & (s < b)], b]
            out.append([tuple(map(float, xy)) for xy in zip(np.interp(ts, s, P[:, 0]), np.interp(ts, s, P[:, 1]))])
            a += period
    return out


def _collect(view: View, frame: Frame, p, progress, cancel, hidden: bool):
    """Visible (and hidden) pieces in mm, with depth and outline flags."""
    region = frame.screen
    min_s = max(float(p.min_piece), 0.0) / frame.half
    out = {"P0": [], "P1": [], "depth": [], "outline": [], "hidden": []}
    for P0, P1, depth, outline, hid in view.pieces(region, hidden=hidden, progress=progress, cancel=cancel):
        keep = np.hypot(*(P1 - P0).T) >= min_s
        out["P0"].append(P0[keep])
        out["P1"].append(P1[keep])
        out["depth"].append(depth[keep])
        out["outline"].append(outline[keep])
        out["hidden"].append(np.full(int(keep.sum()), hid))
    if not out["P0"]:
        return {k: np.zeros((0, 2)) if k in ("P0", "P1") else np.zeros(0) for k in out}
    res = {k: np.concatenate(v) for k, v in out.items()}
    res["P0"], res["P1"] = frame.to_mm(res["P0"]), frame.to_mm(res["P1"])
    return res


def _layers_for(pc: dict, p, colour: str | None = None, prefix: str = "") -> list:
    """Pieces -> named groups of segments (mm), before joining."""
    groups: list = []
    vis = ~pc["hidden"].astype(bool)
    segs = np.stack([pc["P0"], pc["P1"]], axis=1)
    outline = pc["outline"].astype(bool) & vis
    inner = vis & ~(outline if p.outline_layer else np.zeros_like(vis))
    k = int(p.depth_layers) if not colour else 1
    names = DEPTH_NAMES[k]
    if k > 1 and inner.any():
        d = pc["depth"][inner]
        lo, hi = float(d.min()), float(d.max())
        band = np.minimum(((pc["depth"] - lo) / max(hi - lo, 1e-9) * k).astype(int), k - 1)
        for i, name in enumerate(names):
            groups.append((prefix + name, segs[inner & (band == i)], colour, p.line_width,
                           bool(p.far_dashed and i == k - 1)))
    else:
        groups.append((prefix + "lines", segs[inner], colour, p.line_width, False))
    if p.outline_layer:
        groups.append((prefix + "outline", segs[outline], colour, p.outline_width, False))
    if p.show_hidden:
        groups.append((prefix + "hidden", segs[~vis], colour, p.line_width, True))
    return groups


def _length(pl) -> float:
    P = np.asarray(pl, dtype=float)
    return float(np.hypot(*np.diff(P, axis=0).T).sum())


def render(model: Model, p, progress=None, log=None, cancel=None) -> dict:
    say = progress or (lambda *_: None)
    log = log or (lambda *_: None)
    t = {}
    t0 = time.perf_counter()
    frame = frame_for(p.paper, p.orientation, p.margin, (p.get("paper_width", 200.), p.get("paper_height", 200.)))
    cam = camera_from(p)
    cams = [(cam, "")] if not p.stereo else [(cam, "red "), (cam.stereo(float(p.eye_sep)), "blue ")]
    groups, info = [], []
    for ci, (c, prefix) in enumerate(cams):
        say(0.05 + 0.4 * ci, "edges and faces" + (f" ({prefix.strip()} eye)" if prefix else ""))
        view = View(model, c, float(p.flat_angle), p.sides, back_edges=bool(p.show_hidden), edge_mode=p.get('edge_mode', 'features'))
        info.append(view)
        span = 0.75 / len(cams)

        def prog(f, base=0.1 + ci * span):
            say(base + span * f, "hidden lines" + (f" ({prefix.strip()} eye)" if prefix else ""))

        pc = _collect(view, frame, p, prog, cancel, bool(p.show_hidden))
        colour = (RED if prefix == "red " else BLUE) if p.stereo else None
        groups += _layers_for(pc, p, colour, prefix)
    t["hidden lines"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    say(0.88, "joining and ordering strokes")
    layers, before, after = [], 0.0, 0.0
    tol = max(1e-4, 0.002 * frame.half / 100.0)
    for name, segs, colour, width, dash in groups:
        if not len(segs):
            continue
        pls = join_segments([[tuple(a), tuple(b)] for a, b in segs.tolist()], tol)
        pls = [pl for pl in simplify_all(pls, float(p.simplify)) if len(pl) >= 2]
        if p.min_stroke > 0 and "outline" not in name:                    # specks of scan noise, not outlines
            pls = [pl for pl in pls if _length(pl) >= p.min_stroke]
        if dash:
            pls = dashed(pls, 1.6, 1.0)
        before += travel(pls)
        if p.optimise:
            pls = order_polylines(pls, start=(0.0, 0.0))
        after += travel(pls)
        layers.append(Layer(name.strip(), pls, colour or PEN_COLORS.get(name.strip(), "#000000"), float(width)))
    t["ordering"] = time.perf_counter() - t1
    v0 = info[0]
    return {"layers": layers, "sheet": frame.sheet, "frame": frame, "camera": cam, "timings": t,
            "travel": (before, after), "edges": v0.edges, "front_faces": v0.front_faces,
            "two_sided": v0.two_sided, "faces": model.faces}
