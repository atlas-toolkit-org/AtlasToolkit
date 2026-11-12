"""
mesh_melt.py - what Melt asks Blender to do: the Blender pieces' melt (projects/blender3d_project, skill
plotter-contour-art: melt_distortion.py, ~70 tuned values) reduced to one master knob and a few characters, for any
model. The melt itself runs in Blender (atlas_toolkit/blender/melt_gn.py): Geometry Nodes, a voxel dissolve proxy,
drips grown as tubes along the surface.

Amount. The pieces' four-stage melt (v17: I intact, II first drips, III heavy melt + pool, IV dissolved) is the
trajectory: amount 0 is I, 1/3 is II, 2/3 is III, 1 is IV, every value interpolated in between.
- magnitudes (softening, slump, sags and droops, pool height, drips, dissolve ...) grow from nothing at I;
- shapes (pool flatness, lobes, spread, drip lengths, voxel size ...) hold stage II's values below it.
So a four-stage sequence at amount 1 gives exactly the v17 values (seeded on the rest shape: the same drips grow in
the same places from stage to stage), and any single amount is a point on that path.

Characters multiply the trajectory: sag, pool, drips (how many), drip size, dissolve, softening, face slide.

Any model. The melt works on the model normalised as in the pieces (2 tall, Z up, front -Y), and:
- head off (default): the face terms (face slide, nose droop, jowls, jaw mass, ear droop) are 0 and rivulets may
  run anywhere; on: they scale with the head's own depth and width (the v17 head is 1) and its height band;
- smoothing passes over the mesh (softening, the dissolve's blur, the pool's smoothing) are counted for the v17
  head's density (mean edge 0.01298 of 2): n = n_v17 · (e_v17 / e_model)², so a coarse or a dense scan melts alike.
"""
from __future__ import annotations

import numpy as np

from atlas_toolkit.blender.melt_gn import MELT_DEFAULTS

E_REF = 0.012982            # v17_head.stl: mean edge length, 2 units tall
REF_FRONT = 0.911052        # v17_head.stl: face's reach toward -Y from the box centre (2 tall)
REF_SIDE = 0.725590         # v17_head.stl: half-width
KEYS_U = (1.0 / 3.0, 2.0 / 3.0, 1.0)

# templates/melt_sequence.json of the skill (identical to v17_head/melt_sequence/melt_sequence.json)
COMMON = {"seed": 0, "seed_mode": "rest", "hang_radius": [0.026, 0.058], "run_radius": [0.024, 0.04]}
STAGES = [
    {"soften": 8, "slump": 0.04, "face_slide": 0.1, "protrude": 0.04, "forward_sag": 0.1, "jowl": 0.02,
     "jaw_mass": 0.04, "base_mound": 0.02, "ear_droop": 0.06, "crown_sag": 0.03, "crown_bulge": 0.01,
     "flow_ripples": 0.004, "puddle_height": 0.07, "puddle_spread": 1.6, "puddle_flatness": 3.0,
     "drip_amount": 0.45},
    {"soften": 18, "slump": 0.12, "face_slide": 0.24, "protrude": 0.1, "jaw_mass": 0.11, "base_mound": 0.1,
     "ear_droop": 0.16, "crown_sag": 0.11, "crown_bulge": 0.035, "puddle_height": 0.12,
     "puddle_spread": 1.7, "puddle_flatness": 2.5, "drip_amount": 0.9, "dissolve": 0.15},
    {"soften": 60, "soften_weight": 0.8, "slump": 0.6, "face_slide": 0.1, "protrude": 0.08,
     "forward_sag": 0.0, "jowl": 0.06, "jaw_mass": 0.12, "base_mound": 0.35, "ear_droop": 0.22,
     "crown_sag": 0.3, "crown_bulge": 0.03, "variation": 0.5, "flow_ripples": 0.01,
     "puddle_height": 0.24, "puddle_softness": 0.03, "puddle_spread": 0.8, "puddle_flatness": 1.4,
     "puddle_lobes": 0.4, "puddle_lobe_scale": 0.9, "puddle_smooth": 60,
     "drip_amount": 1.4, "hang_len": [0.2, 0.9], "dissolve": 0.8, "dissolve_voxel": 0.07},
]
MAGNITUDES = ("soften", "slump", "face_slide", "protrude", "forward_sag", "jowl", "jaw_mass", "base_mound",
              "ear_droop", "crown_sag", "crown_bulge", "flow_ripples", "puddle_height", "drip_amount", "dissolve")
FACE_TERMS = ("face_slide", "protrude", "forward_sag", "jowl", "jaw_mass", "ear_droop")
PASSES = ("soften", "dissolve_blur", "puddle_smooth")        # blur passes over the model's own mesh
MAX_PASSES = 800


def keyframes() -> list:
    """The full parameter sets of stages II, III, IV."""
    return [{**MELT_DEFAULTS, **COMMON, **st} for st in STAGES]


def _lerp(a, b, t):
    if isinstance(a, (list, tuple)):
        return [float(x) + (float(y) - float(x)) * t for x, y in zip(a, b)]
    if isinstance(a, bool) or isinstance(a, str) or a is None:
        return a if t < 0.5 else b
    return float(a) + (float(b) - float(a)) * t


def trajectory(u: float) -> dict:
    """The pieces' parameters at melt progress u (0 < u <= 1): stage II at 1/3, III at 2/3, IV at 1."""
    ks = keyframes()
    u = min(max(float(u), 0.0), 1.0)
    if u <= KEYS_U[0]:
        t = u / KEYS_U[0]
        out = dict(ks[0])
        for k in MAGNITUDES:
            out[k] = float(ks[0][k]) * t
        return out
    i = 0 if u <= KEYS_U[1] else 1
    t = (u - KEYS_U[i]) / (KEYS_U[i + 1] - KEYS_U[i])
    a, b = ks[i], ks[i + 1]
    return {k: _lerp(a[k], b[k], t) for k in a}


def mean_edge(V: np.ndarray, F: np.ndarray) -> float:
    """Mean length of the mesh's unique edges."""
    F = np.asarray(F, dtype=np.int64)
    e = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
    e = np.unique(e, axis=0)
    return float(np.linalg.norm(V[e[:, 0]] - V[e[:, 1]], axis=1).mean()) if len(e) else E_REF


def head_zones(V: np.ndarray, span=(0.0, 100.0)) -> dict:
    """Where the head is, in the 2-tall frame: its height band and horizontal centre, and how deep and wide it is
    against the v17 head (zone scales; exactly 1 on v17)."""
    z0 = -1.0 + 2.0 * float(span[0]) / 100.0
    z1 = -1.0 + 2.0 * float(span[1]) / 100.0
    if z1 - z0 < 0.05:
        z0, z1 = -1.0, 1.0
    sel = (V[:, 2] >= z0) & (V[:, 2] <= z1)
    H = V[sel] if sel.sum() >= 10 else V
    full = z0 <= -1.0 + 1e-9 and z1 >= 1.0 - 1e-9
    cx = 0.0 if full else float(H[:, 0].min() + H[:, 0].max()) / 2.0
    cy = 0.0 if full else float(H[:, 1].min() + H[:, 1].max()) / 2.0
    front = max(-(float(H[:, 1].min()) - cy), 1e-6)
    side = max(float(np.abs(H[:, 0] - cx).max()), 1e-6)
    return {"zone_front": front / REF_FRONT, "zone_side": side / REF_SIDE, "head_centre": (z0 + z1) / 2.0,
            "head_gain": 2.0 / (z1 - z0), "head_x": cx, "head_y": cy}


def resolve(p, u: float, e_model: float = E_REF, zones: dict | None = None, draft: bool = False,
            notes: list | None = None) -> dict:
    """The parameters Blender gets for one stage at progress u, from the tool's settings."""
    q = trajectory(u)
    for k in ("slump", "crown_sag", "base_mound", "crown_bulge"):
        q[k] *= p.sag
    q["puddle_height"] *= p.pool
    q["puddle_flatness"] *= p.pool_flatness
    q["drip_amount"] *= p.drips
    q["hang_radius"] = [r * p.drip_size for r in q["hang_radius"]]
    q["run_radius"] = [r * p.drip_size for r in q["run_radius"]]
    q["hang_len"] = [r * p.drip_length for r in q["hang_len"]]
    q["run_len"] = [r * p.drip_length for r in q["run_len"]]
    q["hang_spacing"] *= p.drip_spacing
    q["run_spacing"] *= p.drip_spacing
    q["dissolve"] = min(q["dissolve"] * p.dissolve, 1.0)
    q["dissolve_voxel"] *= p.dissolve_detail
    q["variation"] = min(q["variation"] * p.irregularity, 1.0)
    q["flow_ripples"] *= p.flow
    q["asymmetry"] = float(p.asymmetry)
    q["seed"] = int(p.seed)
    q["seed_mode"] = p.seed_mode
    q["hang_drips"], q["run_drips"] = bool(p.hang_drips), bool(p.run_drips)
    q["drip_floor"] = 2.0 * p.drip_floor / 100.0 if p.drip_floor > 0 else None
    q["base_flatten"] = 2.0 * p.base_flatten / 100.0
    q["head"] = bool(p.head)
    if p.head:
        for k in ("face_slide", "jowl", "jaw_mass"):
            q[k] *= p.face_slide
        for k in ("protrude", "ear_droop"):
            q[k] *= p.droop
        q.update(zones or {})
    else:
        for k in FACE_TERMS:
            q[k] = 0.0
    density = (E_REF / max(e_model, 1e-9)) ** 2
    for k in PASSES:
        n = float(q[k]) * density * (p.soften ** 2 if k == "soften" else 1.0)
        if n > MAX_PASSES and notes is not None:
            notes.append(f"{k} capped at {MAX_PASSES} passes (asked {n:.0f}: a very dense mesh)")
        q[k] = int(round(min(n, MAX_PASSES)))
    q["dissolve_smooth"] = int(q["dissolve_smooth"])       # on the voxel proxy: set by the voxel size, not the mesh
    q["drip_resolution"] = 8 if draft else int(q["drip_resolution"])
    q["face_dir"] = [0.0, -1.0, 0.0]
    q.pop("subdiv", None)
    return q


T_REF = 0.7455              # v17_head.stl: 25th percentile of the inward thickness, 2 tall


def thickness(V: np.ndarray, F: np.ndarray, samples: int = 4000, seed: int = 1) -> float:
    """How thin the model is: 25th percentile of the distance through it, inward from surface points (2 tall)."""
    import trimesh
    from atlas_toolkit.geom.raycast import Caster
    m = trimesh.Trimesh(V, F, process=False)
    pts, fi = trimesh.sample.sample_surface(m, samples, seed=seed)
    N = m.face_normals[fi]
    t, _, _, _ = Caster(V, F).intersect(pts - N * 1e-4, -N)
    t = t[np.isfinite(t)]
    return float(np.percentile(t, 25)) if len(t) else T_REF


def inspect(path):
    """The usual mesh summary, plus a drip size for thin models (Drip size "auto"): drips scale with how thin the
    model is against the v17 head (Augustus' arm and fingers: about half)."""
    from atlas_toolkit.geom import frame3d
    from atlas_toolkit.geom import mesh as gm
    info = gm.inspect(path)
    mesh = gm.load_mesh(path)
    up, front, rot, _ = frame3d.resolve_axes(path, {}, mesh.extents)
    f = frame3d.fit_frame(mesh.vertices, up, front, rot, mode="height", size=2.0)
    t = thickness(f.apply(mesh.vertices), np.asarray(mesh.faces))
    info.values["thickness"] = t
    info.values["drip_size_auto"] = float(min(max(t / T_REF, 0.35), 1.2))
    info.summary += f" · thickness {t / 2 * 100:.0f}% h"
    return info
