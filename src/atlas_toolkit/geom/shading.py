"""
Ray-cast shading of a 3D model on the paper grid, shared by the 3D drawing tools (Sketch Hatching, Stipple): the
Blender pieces' sketch tracer (projects/blender3d_project/digital_divide_1/plotter_lib/sketch_trace.py) on the
toolkit's camera (geom/camera.py) and embree (geom/raycast.py) instead of Blender's BVH: every ray of a step goes
in one batch instead of one Python call each.

- analyse():  mesh analysis at three scales (contours / lines / forms) and per-vertex ambient occlusion
              (hemisphere rays). Camera-independent: once per model and settings.
- trace():    for one plate: a G-buffer on the paper grid (one primary ray per pixel: hit, face, barycentrics,
              depth, normal), soft shadow rays toward the key light, a depth-discontinuity map, hatching direction
              fields and the feature lines (contours, suggestive contours, ridges and valleys, creases,
              boundaries) with ray-cast visibility, in paper millimetres.

Units: the model is given as the pieces had it, 2 units tall (the drawing tools scale their canonical frame and
camera by 2 / height, which leaves the picture unchanged), so the pieces' tuned lengths and curvature thresholds
(ao_distance 0.18, feature_size 0.03, valley strength 110 ...) keep their meaning on any model.
"""
from __future__ import annotations

import math

import numpy as np

from . import curvature as sm


def _unit(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n > 0, n, 1.0)


# ----------------------------------------------------------------------------- projection

class Projector:
    """Model (2 tall) <-> paper mm through a camera (geom/camera.Camera, scaled to the same units) and the paper's
    drawing area (geom/camera.Frame)."""

    def __init__(self, cam, frame):
        self.cam, self.frame = cam, frame
        self.eye, self.right, self.up, self.back = cam.basis()
        self.ortho = cam.ortho
        self.forward = -self.back

    def to_page(self, P) -> np.ndarray:
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        return self.frame.to_mm(self.cam.project(self.cam.to_camera(P)))

    def screen(self, xy) -> np.ndarray:
        x0, y0, w, h = self.frame.area
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        return np.c_[(xy[:, 0] - (x0 + w / 2.0)) / self.frame.half, -(xy[:, 1] - (y0 + h / 2.0)) / self.frame.half]

    def rays(self, xy):
        """Ray origins and unit directions through paper points xy (n, 2)."""
        return self.cam.rays(self.screen(xy))

    def depth(self, P) -> np.ndarray:
        D = np.asarray(P, dtype=np.float64) - self.eye
        return D @ self.forward if self.ortho else np.linalg.norm(D, axis=1)

    def view_dirs(self, P) -> np.ndarray:
        """Unit vectors from the surface toward the eye."""
        if self.ortho:
            return np.broadcast_to(self.back, np.shape(P)).copy()
        return _unit(self.eye - np.asarray(P, dtype=np.float64))

    def view_point(self) -> np.ndarray:
        """Where view-dependent fields look from (far along the view axis for orthographic)."""
        return self.eye + self.back * 1e5 if self.ortho else self.eye

    def mm_per_world(self, P, eps: float = 1e-3) -> np.ndarray:
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        return np.linalg.norm(self.to_page(P + eps * self.right) - self.to_page(P), axis=1) / eps

    def page_dirs(self, P, T, eps: float = 1e-3):
        """Paper directions (unit, (n, 2)) of tangents T at points P, and the foreshortening."""
        d = self.to_page(P + eps * T) - self.to_page(P)
        n = np.linalg.norm(d, axis=1)
        return d / np.where(n > 0, n, 1.0)[:, None], n / eps

    def light(self, azimuth_deg: float, elevation_deg: float) -> np.ndarray:
        """Toward a light given relative to the camera: azimuth about its up axis (negative = from the viewer's
        left), elevation up (the pieces' camera-relative key light)."""
        az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
        return _unit(math.cos(el) * (math.sin(az) * self.right + math.cos(az) * self.back) + math.sin(el) * self.up)

    def bounds(self, V) -> tuple:
        """Paper bounds of points V (x0, y0, x1, y1) mm."""
        P = np.asarray(V, dtype=np.float64)
        if not self.ortho:
            P = P[self.cam.to_camera(P)[:, 2] > 1e-6]
        xy = self.to_page(P)
        return float(xy[:, 0].min()), float(xy[:, 1].min()), float(xy[:, 0].max()), float(xy[:, 1].max())


def model_light(azimuth_deg: float, elevation_deg: float, rotate=(0.0, 0.0, 0.0)) -> np.ndarray:
    """Toward a light given relative to the model: azimuth about its up axis from its front (negative = toward the
    viewer's left when the model faces the camera), elevation up - in the model's own axes (Up and Front), so the
    light turns with it when the frame adds an extra turn (a bust laid on its side keeps its portrait light)."""
    from .frame3d import euler_xyz
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    v = np.array([math.cos(el) * math.sin(az), -math.cos(el) * math.cos(az), math.sin(el)])
    return _unit(euler_xyz(rotate) @ v)


# ----------------------------------------------------------------------------- analysis (once per model)

ANALYSIS_DEFAULTS = dict(
    contour_smooth=2,      # neighbour-average passes on vertex normals for contours + shading
    line_smooth=24,        # Taubin passes before the curvature used by suggestive contours
    field_smooth=120,      # normal-averaging passes before the curvature that steers hatching
    tone_smooth=80,        # normal-averaging passes for the simplified (form) shading
    crease_angle=55.0,     # degrees, dihedral angle of drawn crease edges
    ao_rays=24,            # hemisphere rays per vertex (0 = no ambient occlusion)
    ao_distance=0.18,      # model units (2 tall)
    ao_smooth=2,
    seed=0,
    focus=(),              # [{"anchor": [x, y, z], "radius": r, "weight": 1}]
    focus_line_smooth=6,   # Taubin passes for the line-scale geometry inside a focus
    focus_field_smooth=30,  # normal averaging for the hatching field inside a focus
    curvature=True,        # False: skip the curvature (tools that only shade)
)


def _hemisphere(n):
    """n cosine-weighted directions around +Z (stratified on a spiral)."""
    i = np.arange(n) + 0.5
    r = np.sqrt(i / n)
    phi = i * math.pi * (3 - math.sqrt(5))
    return np.c_[r * np.cos(phi), r * np.sin(phi), np.sqrt(np.maximum(0, 1 - r * r))]


def _frames(N):
    a = np.where(np.abs(N[:, 0:1]) < 0.9, np.array([[1.0, 0, 0]]), np.array([[0, 1.0, 0]]))
    t = _unit(np.cross(N, a))
    return t, np.cross(N, t)


def light_vectors(light, P):
    """Unit vectors toward the light at points P (n, 3) and its falloff there (n,). `light` is a direction toward
    a distant light (falloff 1), or a spherical emitter {"point", "radius", "falloff", "ref"} whose falloff is
    (ref / d)^k, capped at 1 (ref defaults to 1.5 radius)."""
    P = np.asarray(P, float).reshape(-1, 3)
    if not isinstance(light, dict):
        return np.broadcast_to(_unit(np.asarray(light, float)), P.shape), np.ones(len(P))
    c = np.asarray(light["point"], float)
    v = c - P
    d = np.linalg.norm(v, axis=1)
    ref = float(light.get("ref", 1.5 * light["radius"]))
    fall = np.clip((ref / np.maximum(d, 1e-9)) ** float(light.get("falloff", 0.0)), 0, 1)
    return v / np.maximum(d, 1e-12)[:, None], fall


def vertex_ao(caster, V, N, rays=24, distance=0.18, seed=0, cancel=None):
    """Ambient occlusion per vertex: the share of cosine-weighted hemisphere rays that escape within `distance`
    (randomly rotated per vertex against banding). One batch of rays per direction."""
    rng = np.random.default_rng(seed)
    H = _hemisphere(rays)
    t, b = _frames(N)
    rot = rng.random(len(V)) * 2 * math.pi
    c, s = np.cos(rot)[:, None], np.sin(rot)[:, None]
    t, b = c * t + s * b, -s * t + c * b
    O = V + N * 2e-4
    free = np.zeros(len(V))
    for k in range(rays):
        if cancel is not None:
            cancel()
        D = _unit(t * H[k, 0] + b * H[k, 1] + N * H[k, 2])
        free += ~caster.occluded(O, D, distance)
    return free / rays


def attention(V, focus):
    """Per-vertex attention 0-1: 1 within 0.55 x radius of a focus anchor, fading smoothly to 0 at 1.45 x radius
    (max over the focus points, times each one's weight)."""
    a = np.zeros(len(V))
    for f in focus or ():
        d = np.linalg.norm(V - np.asarray(f["anchor"], float), axis=1)
        r = float(f["radius"])
        x = np.clip((1.45 * r - d) / (0.9 * r), 0, 1)
        a = np.maximum(a, float(f.get("weight", 1.0)) * x * x * (3 - 2 * x))
    return a


def _blend_normals(a, N_focus, N_rest):
    return _unit(a[:, None] * N_focus + (1 - a[:, None]) * N_rest)


def analyse(V, F, caster, cancel=None, progress=None, **params):
    """Mesh analysis once per model and settings. With `focus` anchors the drawing gets a level-of-detail hierarchy
    like an artist's: around each anchor the line-scale geometry is smoothed less and the hatching field follows
    smaller planes, so features the global, noise-suppressing scale would erase survive."""
    p = {**ANALYSIS_DEFAULTS, **params}
    say = progress or (lambda *_: None)
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    E, FE, EF, counts = sm.edge_topology(F)
    attn = attention(V, p["focus"])
    N0 = sm.vertex_normals(V, F)
    Nc = sm.smooth_normals(N0, E, p["contour_smooth"])
    out = {"V": V, "F": F, "E": E, "FE": FE, "EF": EF, "Nc": Nc, "attn": attn, "params": p,
           "edge_len": sm.mean_edge_length(V, E)}
    if p["curvature"]:
        say(0.1, "curvature of the lines")
        Vs = sm.taubin_smooth(V, E, p["line_smooth"])
        if attn.any():
            Vfine = sm.taubin_smooth(V, E, p["focus_line_smooth"])
            Vs = attn[:, None] * Vfine + (1 - attn[:, None]) * Vs
        Ns = sm.vertex_normals(Vs, F)
        curv = sm.curvature(Vs, F, Ns)
        if cancel is not None:
            cancel()
        dcurv = sm.curvature_derivs(Vs, F, Ns, curv)
        say(0.4, "curvature of the forms")
        if attn.any():
            Nmid = sm.smooth_normals(N0, E, p["focus_field_smooth"])
            Nf = sm.smooth_normals(Nmid, E, max(0, p["field_smooth"] - p["focus_field_smooth"]))
            Nf = _blend_normals(attn, Nmid, Nf)
        else:
            Nf = sm.smooth_normals(N0, E, p["field_smooth"])
        curv_f = sm.curvature(V, F, Nf)
        out.update(Ns=Ns, curv=curv, dcurv=dcurv, Nf=Nf, curv_f=curv_f)
    if cancel is not None:
        cancel()
    say(0.6, "form shading")
    out["Nt"] = sm.smooth_normals(N0, E, p["tone_smooth"])
    out["crease"], out["boundary"] = sm.feature_edges(V, F, E, EF, counts, p["crease_angle"])
    say(0.7, "ambient occlusion")
    if p["ao_rays"] > 0:
        ao = vertex_ao(caster, V, Nc, p["ao_rays"], p["ao_distance"], p["seed"], cancel)
        for _ in range(p["ao_smooth"]):
            ao = 0.5 * ao + 0.5 * sm.neighbour_average(ao, E, len(V))
    else:
        ao = np.ones(len(V))
    out["ao"] = ao
    out["fn"], _ = sm.face_normals(V, F)
    return out


# ----------------------------------------------------------------------------- G-buffer

def gbuffer(an, caster, proj, box_mm, px_per_mm=5.0):
    """One primary ray per pixel over box_mm = (x0, y0, x1, y1) paper mm (pixel centres)."""
    x0, y0, x1, y1 = box_mm
    px = 1.0 / px_per_mm
    W, H = max(1, int(math.ceil((x1 - x0) / px))), max(1, int(math.ceil((y1 - y0) / px)))
    xs, ys = x0 + (np.arange(W) + 0.5) * px, y0 + (np.arange(H) + 0.5) * px
    XX, YY = np.meshgrid(xs, ys)
    xy = np.c_[XX.ravel(), YY.ravel()]
    O, D = proj.rays(xy)
    t, face, u, v = caster.intersect(O, D)
    n = H * W
    hit_i = np.flatnonzero(face >= 0)
    V, F = an["V"], an["F"]
    bary = np.zeros((n, 3))
    bary[hit_i] = np.c_[1 - u[hit_i] - v[hit_i], u[hit_i], v[hit_i]]
    tri = F[face[hit_i]]
    pos = np.zeros((n, 3))
    pos[hit_i] = (bary[hit_i][:, :, None] * V[tri]).sum(1)
    nrm = np.zeros((n, 3))
    nrm[hit_i] = _unit((bary[hit_i][:, :, None] * an["Nc"][tri]).sum(1))
    soft = np.zeros((n, 3))
    soft[hit_i] = _unit((bary[hit_i][:, :, None] * an["Nt"][tri]).sum(1))
    depth = np.full(n, np.inf)
    depth[hit_i] = proj.depth(pos[hit_i])
    view = np.zeros((n, 3))
    view[hit_i] = proj.view_dirs(pos[hit_i])
    attn = np.zeros(n)
    attn[hit_i] = (bary[hit_i] * an["attn"][tri]).sum(1)
    return {"shape": (H, W), "x0": x0, "y0": y0, "px": px, "hit": face >= 0, "face": face, "bary": bary,
            "pos": pos, "normal": nrm, "normal_soft": soft, "depth": depth, "ndotv": (nrm * view).sum(1),
            "hit_i": hit_i, "attn": attn}


def _shadow_origins(an, gb, todo):
    fn = an["fn"][gb["face"][todo]]
    fn = np.where(((fn * gb["normal"][todo]).sum(1) < 0)[:, None], -fn, fn)
    return gb["pos"][todo] + fn * 4e-4 + gb["normal"][todo] * 4e-4


def _point_shadows(an, caster, gb, light, samples, seed, subset):
    """shadows() for a spherical emitter: rays aimed at jittered points of its disk (radius x shadow_size, smaller
    = harsher), stopped at its surface (it must not shadow itself), times the falloff."""
    rng = np.random.default_rng(seed)
    hit_i = gb["hit_i"] if subset is None else np.asarray(subset, np.int64)
    n = len(gb["face"])
    Lv, fall = light_vectors(light, gb["pos"][hit_i])
    ndotl = np.zeros(n)
    ndotl[hit_i] = (gb["normal"][hit_i] * Lv).sum(1)
    facing = ndotl[hit_i] > 0
    if "normal_soft" in gb:
        facing |= (gb["normal_soft"][hit_i] * Lv).sum(1) > 0
    todo, fall = hit_i[facing], fall[facing]
    lit = np.zeros(n)
    if not len(todo):
        return lit, ndotl
    O = _shadow_origins(an, gb, todo)
    c = np.asarray(light["point"], float)
    R = float(light["radius"])
    r = R * float(light.get("shadow_size", 1.0))
    t, b = _frames(_unit(c - O))
    free = np.zeros(len(todo))
    for k in range(samples):
        ang = rng.random(len(todo)) * 2 * math.pi
        rad = r * np.sqrt((k + rng.random(len(todo))) / samples)
        q = c + rad[:, None] * (np.cos(ang)[:, None] * t + np.sin(ang)[:, None] * b)
        v = q - O
        D = v / np.linalg.norm(v, axis=1)[:, None]
        w = c - O
        along = (w * D).sum(1)
        perp2 = np.maximum((w * w).sum(1) - along * along, 0)
        cap = along - np.sqrt(np.maximum(R * R - perp2, 0)) - 0.01 * R
        free += np.where(cap > 0, ~caster.occluded(O, D, np.maximum(cap, 0.0)), True)
    lit[todo] = free / samples * fall
    return lit, ndotl


def shadows(an, caster, gb, light_dir, samples=4, softness_deg=3.0, seed=1, subset=None):
    """Share of `samples` jittered shadow rays toward the light that escape, per pixel (only where the surface
    faces the light; others are 0); and n·l. light_dir may also be a spherical emitter (see light_vectors)."""
    if isinstance(light_dir, dict):
        return _point_shadows(an, caster, gb, light_dir, samples, seed, subset)
    rng = np.random.default_rng(seed)
    L = _unit(np.asarray(light_dir, float))
    hit_i = gb["hit_i"] if subset is None else np.asarray(subset, np.int64)
    ndotl = np.zeros(len(gb["face"]))
    ndotl[hit_i] = gb["normal"][hit_i] @ L
    facing = ndotl[hit_i] > 0
    if "normal_soft" in gb:                 # the simplified shading may face the light too
        facing |= gb["normal_soft"][hit_i] @ L > 0
    todo = hit_i[facing]
    lit = np.zeros(len(gb["face"]))
    if not len(todo):
        return lit, ndotl
    if samples <= 0:
        lit[todo] = 1.0
        return lit, ndotl
    O = _shadow_origins(an, gb, todo)
    t, b = _frames(L[None, :])
    r = math.tan(math.radians(softness_deg))
    free = np.zeros(len(todo))
    far = 1e4
    for k in range(samples):
        ang = rng.random(len(todo)) * 2 * math.pi
        rad = r * np.sqrt((k + rng.random(len(todo))) / samples)
        D = _unit(L[None, :] + rad[:, None] * (np.cos(ang)[:, None] * t + np.sin(ang)[:, None] * b))
        free += ~caster.occluded(O, D, far)
    lit[todo] = free / samples
    return lit, ndotl


def discontinuities(gb, proj, ratio=6.0, min_jump_mm=0.6):
    """Pixels where the visible surface jumps in depth (occluding edges) or meets the background. A jump counts
    when the 3D distance between neighbouring hits is far more than a continuous surface would give (pixel
    footprint / |n.v|)."""
    H, W = gb["shape"]
    hit = gb["hit"].reshape(H, W)
    pos = gb["pos"].reshape(H, W, 3)
    nv = np.abs(gb["ndotv"]).reshape(H, W)
    hi = gb["hit_i"]
    foot = np.zeros(H * W)
    if len(hi):
        foot[hi] = gb["px"] / proj.mm_per_world(gb["pos"][hi])
    foot = foot.reshape(H, W)
    edge = np.zeros((H, W), bool)
    for axis in (0, 1):
        a = [slice(None), slice(None)]
        b = [slice(None), slice(None)]
        a[axis], b[axis] = slice(0, -1), slice(1, None)
        a, b = tuple(a), tuple(b)
        both = hit[a] & hit[b]
        dist = np.linalg.norm(pos[a] - pos[b], axis=-1)
        allowed = ratio * np.maximum(foot[a], foot[b]) / np.maximum(np.minimum(nv[a], nv[b]), 0.08)
        jump = both & (dist > np.maximum(allowed, min_jump_mm * np.maximum(foot[a], foot[b]) / gb["px"]))
        border = hit[a] != hit[b]
        e = jump | border
        edge[a] |= e
        edge[b] |= e
    return edge


def _doubled(d2):
    """Unit 2D directions (n, 2) -> doubled-angle vectors (cos 2a, sin 2a): a line field."""
    return np.c_[d2[:, 0] ** 2 - d2[:, 1] ** 2, 2 * d2[:, 0] * d2[:, 1]]


def direction_fields(an, gb, proj, slice_axis=(0.0, 0.0, 1.0)):
    """Per-pixel hatching line fields as doubled-angle vectors scaled by a confidence (0-1): `principal`, the
    max-curvature direction of the form-scale surface (cross-contour: around an arm, across a brow), confidence =
    curvature anisotropy; `slice`, the tangent of sections by planes normal to slice_axis, confidence |n x axis|."""
    H, W = gb["shape"]
    hi = gb["hit_i"]
    fields = {"principal": np.zeros((H, W, 2)), "slice": np.zeros((H, W, 2))}
    if not len(hi) or "curv_f" not in an:
        return fields
    tri = an["F"][gb["face"][hi]]
    bary = gb["bary"][hi]
    P = gb["pos"][hi]
    cf = an["curv_f"]
    d = cf["d1"][tri]                                    # (k, 3, 3) sign-ambiguous directions
    sgn = np.sign((d * d[:, :1]).sum(2))
    sgn[sgn == 0] = 1
    t3 = _unit((bary[:, :, None] * d * sgn[:, :, None]).sum(1))
    k1, k2 = np.abs(cf["k1"][tri]), np.abs(cf["k2"][tri])
    aniso = ((k1 - k2) / np.maximum(k1 + k2, 1e-9) * bary).sum(1)
    d2, _ = proj.page_dirs(P, t3)
    f = np.zeros((H * W, 2))
    f[hi] = _doubled(d2) * np.clip(aniso, 0, 1)[:, None]
    fields["principal"] = f.reshape(H, W, 2)
    n = gb["normal"][hi]
    ax = _unit(np.asarray(slice_axis, float))
    ts = np.cross(n, ax)
    conf = np.linalg.norm(ts, axis=1)
    d2, _ = proj.page_dirs(P, _unit(ts))
    f = np.zeros((H * W, 2))
    f[hi] = _doubled(d2) * conf[:, None]
    fields["slice"] = f.reshape(H, W, 2)
    return fields


# ----------------------------------------------------------------------------- feature lines

TRACE_DEFAULTS = dict(
    px_per_mm=5.0,           # G-buffer resolution on paper
    step_mm=0.3,             # resampling step along feature lines (paper)
    vis_offset_mm=0.12,      # ray target offset off the surface along the normal (paper)
    vis_min_run_mm=0.6,      # visible/hidden runs shorter than this are flipped
    probe_mm=0.6,            # outline test: look this far outside a contour
    feature_size=0.03,       # model units; suggestive threshold is sc_threshold / feature_size^2
    sc_threshold=1.0,
    shadow_samples=4,
    softness_deg=3.0,
    slice_axis=(0.0, 0.0, 1.0),
    discontinuity_ratio=6.0,
    valleys=True,            # concave creases: lid creases, the lip line, nostrils, ear folds
    valley_threshold=40.0,   # |k1| (1/model units) traced; the draw stage filters by strength
    ridges=False,            # convex crests (brow, nose bridge): usually left to the tone
    ridge_threshold=60.0,
    focus_light=None,        # direction of a portrait light used inside the focus
    lines=True,              # False: shading only (Stipple)
)


def _resample_curve(P, attrs, step):
    """Resample a 3D polyline to ~uniform `step` (model units) with linear attributes."""
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    s = np.r_[0.0, np.cumsum(seg)]
    if s[-1] <= 0:
        return P[:1], {k: v[:1] for k, v in attrs.items()}
    n = max(2, int(math.ceil(s[-1] / step)) + 1)
    t = np.linspace(0, s[-1], n)
    out = np.c_[[np.interp(t, s, P[:, c]) for c in range(3)]].T
    res = {}
    for k, v in attrs.items():
        if v.ndim == 1:
            res[k] = np.interp(t, s, v)
        else:
            res[k] = np.c_[[np.interp(t, s, v[:, c]) for c in range(v.shape[1])]].T
    return out, res


def _clean_runs(vis, min_run):
    """Flip visible/hidden runs shorter than min_run points (ends: shorter than min_run / 2)."""
    vis = vis.copy()
    for _ in range(3):
        change = np.flatnonzero(np.diff(vis.astype(np.int8))) + 1
        bounds = np.r_[0, change, len(vis)]
        flipped = False
        for i in range(len(bounds) - 1):
            a, b = bounds[i], bounds[i + 1]
            end = a == 0 or b == len(vis)
            if len(bounds) > 2 and (b - a) < (min_run / 2 if end else min_run):
                vis[a:b] = ~vis[a:b]
                flipped = True
                break
        if not flipped:
            break
    return vis


def visibility(caster, proj, Q, eps: float = 1e-5) -> np.ndarray:
    """Ray-cast visibility of points Q from the camera (True = nothing in front), one batch."""
    Q = np.asarray(Q, dtype=np.float64).reshape(-1, 3)
    if not len(Q):
        return np.zeros(0, dtype=bool)
    if proj.ortho:
        O = Q + proj.back * 100.0
        return ~caster.occluded(O, np.broadcast_to(proj.forward, Q.shape), 100.0 - eps)
    dv = Q - proj.eye
    L = np.linalg.norm(dv, axis=1)
    D = dv / np.maximum(L, 1e-12)[:, None]
    return ~caster.occluded(np.broadcast_to(proj.eye, Q.shape), D, np.maximum(L - eps, 0.0))


def _sample(gb, xy, key):
    """Nearest-pixel lookup of a per-pixel G-buffer array at paper points xy."""
    H, W = gb["shape"]
    j = np.floor((xy[:, 0] - gb["x0"]) / gb["px"]).astype(np.int64)
    i = np.floor((xy[:, 1] - gb["y0"]) / gb["px"]).astype(np.int64)
    inside = (i >= 0) & (i < H) & (j >= 0) & (j < W)
    arr = gb[key]
    out = np.zeros((len(xy),) + arr.shape[1:], arr.dtype)
    out[inside] = arr[(i * W + j)[inside]]
    return out, inside


def feature_lines(an, caster, gb, proj, light_dir, p):
    """Contours, suggestive contours, ridges and valleys, creases and boundaries as paper-mm polylines with per-point
    ray-cast visibility and attributes:
    {"kind", "xy" (k, 2), "vis" (k,), "depth", "ndotl", "strength", "outline", "gap", "closed", "attn"}."""
    V, F = an["V"], an["F"]
    mpw = float(np.median(proj.mm_per_world(V[::97]))) if len(V) else 1.0
    step = p["step_mm"] / max(mpw, 1e-9)
    off = p["vis_offset_mm"] / max(mpw, 1e-9)
    min_run = max(2, int(round(p["vis_min_run_mm"] / p["step_mm"])))
    curves = []
    # occluding contours: n.v = 0 on lightly smoothed normals, placed on the original surface
    ndotv = (an["Nc"] * proj.view_dirs(V)).sum(1)
    curves += [("contour", c, an["Nc"], None) for c in sm.zero_crossings(F, ndotv)]
    if "curv" in an:
        # suggestive contours: zero crossings of kr where DwKr / (n.v) > threshold, front-facing
        vf = sm.view_fields(V, an["Ns"], an["curv"], an["dcurv"], proj.view_point())
        thr = p["sc_threshold"] / p["feature_size"] ** 2
        keep = np.minimum(vf["sc_num"] - thr * vf["sc_den"], (vf["ndotv"] - 0.02) * 1e3)
        front = (vf["ndotv"][F] > 0).any(1)
        strength = vf["sc_num"] / np.where(np.abs(vf["sc_den"]) > 1e-6, vf["sc_den"], 1e-6) * p["feature_size"] ** 2
        curves += [("suggestive", c, an["Ns"], strength)
                   for c in sm.zero_crossings(F, vf["kr"], keep=keep, face_mask=front)]
        absk = np.abs(an["curv"]["k1"])
        for kind, flag, thr_rv, is_valley in (("valley", p["valleys"], p["valley_threshold"], True),
                                              ("ridge", p["ridges"], p["ridge_threshold"], False)):
            if flag:
                curves += [(kind, c, an["Ns"], absk) for c in
                           sm.ridges_valleys(V, F, an["E"], an["FE"], an["curv"], an["dcurv"], valleys=is_valley,
                                             threshold=thr_rv)]
    curves += [("crease", c, an["Nc"], None) for c in sm.edge_curves(an["crease"])]
    curves += [("boundary", c, an["Nc"], None) for c in sm.edge_curves(an["boundary"])]
    attn_v = an.get("attn")
    pending = []
    for kind, c, Nsrc, strength in curves:
        P = sm.interpolate(c, V)
        attrs = {"n": sm.interpolate(c, Nsrc)}
        if strength is not None:
            attrs["strength"] = sm.interpolate(c, strength)
        if attn_v is not None:
            attrs["attn"] = sm.interpolate(c, attn_v)
        P, attrs = _resample_curve(P, attrs, step)
        if len(P) < 2:
            continue
        pending.append((kind, c, P, attrs, _unit(attrs["n"])))
    if not pending:
        return []
    Q = np.concatenate([P + N * off for _, _, P, _, N in pending])
    vis_all = visibility(caster, proj, Q)
    out, i = [], 0
    for kind, c, P, attrs, N in pending:
        vis = _clean_runs(vis_all[i:i + len(P)], min_run)
        i += len(P)
        if not vis.any():
            continue
        xy = proj.to_page(P)
        Lv, _ = light_vectors(light_dir, P)
        rec = {"kind": kind, "xy": xy, "vis": vis, "depth": proj.depth(P), "ndotl": (N * Lv).sum(1),
               "closed": bool(c[2]), "strength": attrs.get("strength", np.ones(len(P))),
               "attn": attrs.get("attn", np.zeros(len(P)))}
        if kind == "contour":
            d2, _ = proj.page_dirs(P, N)
            probe = xy + d2 * p["probe_mm"]
            hit, inside = _sample(gb, probe, "hit")
            dep, _ = _sample(gb, probe, "depth")
            rec["outline"] = ~(hit & inside)
            rec["gap"] = np.where(hit & inside, dep - rec["depth"], np.inf)
        out.append(rec)
    return out


# ----------------------------------------------------------------------------- one plate

def trace(an, caster, proj, box_mm, light_dir, cancel=None, progress=None, **params):
    """G-buffer + shadows + discontinuities + fields (+ feature lines) for one plate."""
    p = {**TRACE_DEFAULTS, **params}
    say = progress or (lambda *_: None)
    pad = 2.0
    box = (box_mm[0] - pad, box_mm[1] - pad, box_mm[2] + pad, box_mm[3] + pad)
    say(0.0, "G-buffer")
    gb = gbuffer(an, caster, proj, box, p["px_per_mm"])
    if cancel is not None:
        cancel()
    say(0.25, "shadows")
    lit, ndotl = shadows(an, caster, gb, light_dir, p["shadow_samples"], p["softness_deg"])
    gb["lit"], gb["ndotl"] = lit, ndotl
    gb["ndotl_soft"] = (gb["normal_soft"] * light_vectors(light_dir, gb["pos"])[0]).sum(1)
    if p["focus_light"] is not None and gb["attn"].any():
        sub = gb["hit_i"][gb["attn"][gb["hit_i"]] > 0.01]
        lit_f, ndotl_f = shadows(an, caster, gb, p["focus_light"], p["shadow_samples"], p["softness_deg"], seed=2,
                                 subset=sub)
        gb["lit_f"], gb["ndotl_f"] = lit_f, ndotl_f
        gb["ndotl_f_soft"] = gb["normal_soft"] @ _unit(np.asarray(p["focus_light"], float))
    hi = gb["hit_i"]
    ao = np.zeros(len(gb["face"]))
    tri = an["F"][gb["face"][hi]]
    ao[hi] = (gb["bary"][hi] * an["ao"][tri]).sum(1)
    gb["ao"] = ao
    say(0.4, "edges and fields")
    gb["edges"] = discontinuities(gb, proj, p["discontinuity_ratio"]).ravel()
    gb["fields"] = direction_fields(an, gb, proj, p["slice_axis"])
    lines = []
    if p["lines"]:
        if cancel is not None:
            cancel()
        say(0.55, "feature lines")
        lines = feature_lines(an, caster, gb, proj, light_dir, p)
    for k in ("pos", "bary", "normal_soft"):          # big and not needed downstream
        gb.pop(k)
    return {"gbuffer": gb, "lines": lines, "box": box_mm, "page": proj.frame.sheet,
            "light_dir": light_dir if isinstance(light_dir, dict) else np.asarray(light_dir, float), "params": p}


# ----------------------------------------------------------------------------- a drawing tool's view

E_SKETCH = 0.004739       # Augustus in the sketch pose, 2 tall: the mesh the pieces' smoothing passes were tuned on
PASS_KEYS = ("contour_smooth", "line_smooth", "field_smooth", "tone_smooth", "focus_line_smooth",
             "focus_field_smooth")
MAX_PASSES = 600


class View3D:
    """A model set up for drawing: canonical frame (geom/frame3d, shared by a stage set), scaled to 2 tall, the tool's
    camera scaled the same way (the picture does not change), the paper, embree, and where the model falls on the
    paper (box, mm)."""

    def __init__(self, src, p, draft: bool = False, draft_faces: int = 150_000, log=None):
        from . import frame3d
        from . import mesh as gm
        from .camera import Camera, camera_from, frame_for
        from .raycast import Caster
        mesh = gm.load_mesh(src)
        from .surface import polygon_data
        polygons = polygon_data(mesh)
        self.polygon_owners = polygons['owners'] if polygons else None
        self.faces_in = len(mesh.faces)
        self.frame = frame3d.frame_for(src, p, V=mesh.vertices)
        V = self.frame.apply(mesh.vertices)
        F = np.asarray(mesh.faces, dtype=np.int64)
        if draft:
            V, F = gm.simplify_faces(V, F, int(draft_faces), log)
            if len(F) != len(mesh.faces):
                self.polygon_owners = None
        self.s2 = 2.0 / max(self.frame.height, 1e-9)
        self.V, self.F = V * self.s2, F
        c = camera_from(p)
        self.cam = Camera(c.theta, c.psi, c.radius * self.s2, tuple(np.asarray(c.lookat) * self.s2), c.fov, c.ortho)
        self.paper = frame_for(p.paper, p.orientation, p.margin)
        self.proj = Projector(self.cam, self.paper)
        self.caster = Caster(self.V, self.F)
        x0, y0, x1, y1 = self.proj.bounds(self.V)
        ax, ay, aw, ah = self.paper.area
        self.box = (max(x0, ax), max(y0, ay), min(x1, ax + aw), min(y1, ay + ah))
        self.empty = self.box[2] <= self.box[0] or self.box[3] <= self.box[1]
        self.edge = sm.mean_edge_length(self.V, sm.edge_topology(self.F)[0]) if len(self.F) else E_SKETCH
        import os
        st = os.stat(src)
        self.key = (str(src), st.st_size, st.st_mtime_ns, self.frame.key(), bool(draft), int(draft_faces), len(self.F))

    def passes(self, params: dict, notes: list | None = None) -> dict:
        """Smoothing pass counts for this mesh: the pieces' counts were tuned on Augustus; a pass spreads over about
        one edge, so n = n_ref · (e_ref / e)² keeps the smoothing distance the same on a coarser or denser mesh."""
        out = dict(params)
        k = (E_SKETCH / max(self.edge, 1e-9)) ** 2
        for key in PASS_KEYS:
            if key in out:
                n = float(out[key]) * k
                if n > MAX_PASSES and notes is not None:
                    notes.append(f"{key} capped at {MAX_PASSES} passes (a very dense mesh)")
                out[key] = int(round(min(n, MAX_PASSES)))
        return out


_ANALYSES: dict = {}


def cached_analysis(view: View3D, params: dict, cancel=None, progress=None) -> dict:
    """analyse() for a view, kept for the next run with the same model and analysis settings (in the worker)."""
    key = (view.key, tuple(sorted((k, repr(v)) for k, v in params.items())))
    hit = _ANALYSES.get(key)
    if hit is None:
        hit = analyse(view.V, view.F, view.caster, cancel, progress, **params)
        if getattr(view, 'polygon_owners', None) is not None:
            owners, ef = view.polygon_owners, hit['EF']
            native = (ef[:, 1] < 0) | (owners[ef[:, 0]] != owners[np.maximum(ef[:, 1], 0)])
            nv = len(view.V)
            keys = hit['E'][native] @ np.array([nv, 1])
            hit['crease'] = hit['crease'][np.isin(np.sort(hit['crease'], axis=1) @ np.array([nv, 1]), keys)]
        while len(_ANALYSES) >= 2:
            _ANALYSES.pop(next(iter(_ANALYSES)))
        _ANALYSES[key] = hit
    return hit


def smoothstep(a, b, x):
    t = np.clip((np.asarray(x, dtype=np.float64) - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)
