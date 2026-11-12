"""
mesh_rods.py - Rods: add tubes to a closed model: straight rods, hollow pipes, drooping cables or spikes, placed on a
lattice, radially, at random or over the surface, running through it or growing out of it.

The mirror of Holes (tools/mesh_holes): the same placement (lattice, radial, random, surface), profiles (circle,
n-sided polygon, pillow, blob), irregularity streams, spacing and sweeps, reused from that module; here the solids
are added instead of taken away, in the model's canonical frame (geom/frame3d: up = Z, front = -Y, 2 tall):

    tubes  = (∪ rods ∪ ∪ collars) − ∪ pipe bores
    result = model ∪ tubes                          (pipe bores plugged at the skin)
           = (model ∪ rods ∪ collars) − ∪ bores     (bores open: cut through the model as well)

A rod is first a line with a direction and where it crosses the skin:
    through   it enters and leaves the model and sticks out at both ends by Reach (each end drawn on its own)
    outward   it is rooted a little inside (Root depth) and grows out of the skin by Reach

Shapes along it (a polyline of rings, carried by Holes' sweep):
    rod       its profile, tapering to End size outside the model
    pipe      the rod minus a bore (Wall: share of the radius); the bore runs past the ends, so they are open
    cable     a path from the skin that wanders (Irregularity), waves, swells and droops under gravity
    spike     full width inside, narrowing to a point outside (a thorn's slight curve)
Ends: flat, round, pointed or flared (a bell over the last few radii). Collars: a short wider sleeve of the same
profile where a rod crosses the skin.

Spacing keeps only the parts outside the model apart (the chord inside is hidden by the union).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from atlas_toolkit.geom import influence
from atlas_toolkit.tools.mesh_holes import mesh_holes as mh
from atlas_toolkit.tools.mesh_holes.mesh_holes import Model, unit

FAMILIES = ("rod", "pipe", "cable", "spike")
EXTENDS = ("through", "outward")
LAYOUTS = mh.LAYOUTS
TIPS = ("flat", "round", "pointed", "flared")
MAX_RODS = 4000
SPIKE_CURVE = 1.4                 # a spike's width falls as (1 - t) ** SPIKE_CURVE: a thorn, not a cone
DROOP_RATE = 4.0                  # gravity at Droop 1, radians per canonical unit (the model is 2 tall): a level
                                  # cable turns down within about a fifth of the height, whatever its length


# ----------------------------------------------------------------------------- settings

@dataclass
class Settings:
    """Canonical lengths; the attribute names Holes' placement reads are the same."""
    family: str
    extend: str
    layout: str
    size: float
    size_max: float
    size_mix: str
    reach: float
    reach_max: float
    root: float
    tip: str
    flare: float
    wall: float
    bore: str
    gap: float
    axes: str
    spacing: float
    grid: str
    weave: bool
    both_sides: bool
    turn: tuple
    shift: tuple
    centre_mode: str
    centre: tuple
    spokes: int
    spiral: float
    count: int
    aim: str
    profile: str
    sides: int
    roundness: float
    lobes: int
    lumpiness: float
    aspect: float
    roll: float
    twist: float                  # radians per canonical unit
    taper: float
    droop: float
    waviness: float
    wavelength: float
    swell: float
    collars: bool
    collar_size: float
    collar_length: float
    chaos: float
    drop: float
    jitter_pos: float
    jitter_dir: float
    jitter_shape: float
    segments: int
    seed: int

    @classmethod
    def from_values(cls, v: dict, height: float, segments: int) -> "Settings":
        pct = height / 100.0
        s0, s1 = sorted((float(v["size"]), float(v["size_max"])))
        r0, r1 = sorted((float(v["reach"]), float(v["reach_max"])))
        return cls(
            family=v["family"], extend=v["extend"], layout=v["layout"], size=s0 * pct, size_max=s1 * pct,
            size_mix=v["size_mix"], reach=r0 * pct, reach_max=r1 * pct, root=float(v["root"]) * pct, tip=v["tip"],
            flare=float(v["flare"]), wall=float(v["wall"]), bore=v["bore"], gap=float(v["gap"]) * pct,
            axes=v["axes"], spacing=float(v["spacing"]) * pct, grid=v["grid"], weave=bool(v["weave"]),
            both_sides=bool(v["both_sides"]), turn=tuple(float(a) for a in v["lattice_turn"]),
            shift=tuple(float(a) for a in v["lattice_shift"]), centre_mode=v["centre_mode"],
            centre=tuple(float(a) for a in v["radial_centre"]), spokes=int(v["spokes"]),
            spiral=math.radians(float(v["spiral"])), count=int(v["count"]), aim=v["aim"], profile=v["profile"],
            sides=int(v["sides"]), roundness=float(v["roundness"]), lobes=int(v["lobes"]),
            lumpiness=float(v["lumpiness"]), aspect=float(v["aspect"]), roll=math.radians(float(v["roll"])),
            twist=math.radians(float(v["twist"])) / (10.0 * pct), taper=float(v["taper"]),
            droop=float(v["droop"]), waviness=float(v["waviness"]) * pct,
            wavelength=max(float(v["wavelength"]) * pct, 1e-6), swell=float(v["swell"]),
            collars=bool(v["collars"]), collar_size=float(v["collar_size"]),
            collar_length=float(v["collar_length"]) * pct, chaos=float(v["chaos"]), drop=float(v["drop"]) / 100.0,
            jitter_pos=float(v["jitter_pos"]), jitter_dir=float(v["jitter_dir"]),
            jitter_shape=float(v["jitter_shape"]), segments=int(segments), seed=int(v["seed"]))

    @property
    def c_pos(self) -> float:
        return self.chaos * self.jitter_pos

    @property
    def c_dir(self) -> float:
        return self.chaos * self.jitter_dir

    @property
    def c_shape(self) -> float:
        return min(1.0, self.chaos * self.jitter_shape)


# ----------------------------------------------------------------------------- rods

@dataclass
class Rod:
    kind: str
    origin: np.ndarray            # the layout's anchor
    axis: np.ndarray              # unit direction (outward rods: away from the skin)
    e1: np.ndarray | None = None  # the lattice's profile normal
    normal: np.ndarray | None = None
    rnd: np.ndarray = field(default_factory=lambda: np.zeros(8))
    radius: float = 0.0
    roll: float = 0.0
    aspect: float = 1.0
    crossings: list = field(default_factory=list)   # (point, unit tangent) where it crosses the skin
    back: np.ndarray | None = None                  # where an open bore leaves the model on the far side
    path: np.ndarray | None = None                  # centre line (k, 3)
    scale: np.ndarray | None = None                 # ring scales along it
    inner: tuple | None = None                      # (path, scale) of a pipe's bore
    free: tuple = (False, True)                     # which ends stick out (start, end)
    skeleton: list = field(default_factory=list)    # the parts outside the model, for spacing
    reach: float = 0.0
    shape: np.ndarray | None = None                 # profile outline (N, 2), area π


def rod_aims(model: Model, s: Settings, P, normals, rng) -> np.ndarray:
    """Holes' aim, turned around where it points inward: out of the surface, away from the centre."""
    D = mh.aim_dirs(model, s, P, normals, rng)
    return -D if s.aim in ("normal", "centre") else D


def candidates(model: Model, s: Settings, rs: dict) -> list[Rod]:
    """Every rod the layout proposes: an anchor and a direction."""
    rng = rs["place"]
    out: list[Rod] = []
    if s.layout == "lattice":
        for o, d, e1, _fam in mh.lattice_lines(model, s):
            out.append(Rod(s.family, o, unit(d), e1=e1))
            if s.extend == "outward" and s.both_sides:
                out.append(Rod(s.family, o, -unit(d), e1=e1))
    elif s.layout == "radial":
        for o, d in mh.radial_spokes(model, s, full_lines=(s.extend == "through")):
            out.append(Rod(s.family, o, unit(d)))
    else:
        if s.layout == "random":
            P, fi = mh.inside_points(model, s.count, rng)
            N = model.face_normals()[fi] if len(fi) else np.zeros((0, 3))
        else:
            P, N = mh.surface_points(model, s.count, rng)
        D = rod_aims(model, s, P, N, rng)
        for p, d, n in zip(P, D, N):
            out.append(Rod(s.family, p, unit(d), normal=n))
    if len(out) > 40 * MAX_RODS:
        raise ValueError(f"the layout proposes {len(out)} rods: raise Spacing or lower Count")
    for r in out:
        r.rnd = rs["jitter"].random(8)
    return out


def jitter(rods: list[Rod], s: Settings, sizes: np.ndarray, nudge: np.ndarray, height: float) -> None:
    """Sizes and the irregularity: lattice and radial rods slide sideways, directions tilt, profiles turn and
    stretch; a nudge far below a pen's width keeps rods from meeting exactly (see Holes)."""
    regular = s.layout in ("lattice", "radial")
    for i, r in enumerate(rods):
        u = r.rnd
        r.radius = sizes[i] / 2.0 * (1.0 + mh.NUDGE * nudge[i, 3])
        r.origin = r.origin + nudge[i, :3] * mh.NUDGE * height
        if regular and s.c_pos > 0:
            off = (np.array(u[:3]) * 2 - 1) * s.spacing * 0.5 * s.c_pos
            r.origin = r.origin + off - (off @ r.axis) * r.axis
        if s.c_dir > 0 and not (s.layout in ("random", "surface") and s.aim == "random"):
            r.axis = mh.tilt(r.axis, mh.MAX_TILT * s.c_dir * u[3], u[4])
        if s.layout == "surface" and s.extend == "outward" and r.normal is not None and r.axis @ r.normal < 0:
            r.axis = -r.axis                     # an outward rod leaves the skin, whatever the aim's sign
        r.roll = s.roll + s.c_shape * (u[5] * 2 - 1) * math.pi
        r.aspect = s.aspect * math.exp(s.c_shape * (u[6] - 0.5) * 0.9)


def cross(model: Model, rods: list[Rod], s: Settings) -> list[Rod]:
    """Where each rod crosses the skin; rods that miss the model are dropped."""
    if not rods:
        return []
    O = np.array([r.origin for r in rods])
    D = np.array([r.axis for r in rods])
    t_in, t_out = model.chord(O, D)
    out = []
    for i, r in enumerate(rods):
        o, d = r.origin, r.axis
        if s.extend == "through":
            if not (np.isfinite(t_in[i]) and np.isfinite(t_out[i])) or t_out[i] - t_in[i] < 1e-6:
                continue
            e, x = o + d * t_in[i], o + d * t_out[i]
            r.crossings = [(e, -d), (x, d)]
        else:
            if s.layout == "surface":
                x = o
            else:
                if not np.isfinite(t_out[i]) or (s.layout in ("radial", "random") and t_out[i] <= 0):
                    continue
                x = o + d * t_out[i]
            r.crossings = [(x, d)]
        if np.isfinite(t_in[i]):
            r.back = o + d * t_in[i]
        out.append(r)
    return out


# ----------------------------------------------------------------------------- paths

def straight(r: Rod, s: Settings, ext: tuple) -> None:
    """Centre line and ring scales of a straight rod, pipe or spike (ends shaped later)."""
    d = r.axis
    if s.extend == "through":
        (e, _), (x, _) = r.crossings
        a, b = ext
        start = e - d * a
        z_in, z_out = a, a + float(np.linalg.norm(x - e))
        L = z_out + b
        r.free = (True, True)
    else:
        (x, _), = r.crossings
        start = x - d * s.root
        z_in, z_out = 0.0, s.root
        L = s.root + ext[1]
        r.free = (False, True)
    fl = flare_zone(r, s, L - z_out)
    zs = {0.0, z_in, z_out, L}
    zs.update(np.linspace(0, L, max(2, int(math.ceil(abs(s.twist) * L / math.radians(8))) + 1)).tolist())
    if s.family == "spike" or s.taper != 1.0:
        zs.update(np.linspace(z_out, L, 13).tolist())
        if r.free[0]:
            zs.update(np.linspace(0, z_in, 13).tolist())
    if s.tip == "flared" and s.family != "spike":
        zs.update(np.linspace(L - fl, L, 9).tolist())
        if r.free[0]:
            zs.update(np.linspace(0, fl, 9).tolist())
    z = np.array(sorted(zs))
    left = np.clip((z_in - z) / max(z_in, 1e-9), 0, 1) if r.free[0] else np.zeros_like(z)
    right = np.clip((z - z_out) / max(L - z_out, 1e-9), 0, 1)
    t = np.maximum(left, right)                      # 0 at the skin, 1 at a free end
    if s.family == "spike":
        scale = np.maximum((1 - t) ** SPIKE_CURVE, 0.02)
    else:
        scale = 1.0 + (s.taper - 1.0) * t
    r.path = start + d[None, :] * z[:, None]
    r.scale = scale
    r.skeleton = ([np.array([start, start + d * z_in])] if r.free[0] else []) + [np.array([start + d * z_out,
                                                                                          start + d * L])]


def cable_branch(start, d0, length: float, radius: float, s: Settings, rng, phase: float, side0=None):
    """A cable from the skin. Its heading is steered, step by step, by three turns: a wander (a smooth random turn,
    by Irregularity), a wave (the heading swings to and fro about a side direction carried along the cable, so the
    path weaves with an amplitude of Waviness / 2) and droop (gravity pulls it toward −Z, more the more it runs
    level). Their sum is clamped so a bend is never tighter than the cable at its widest (swell, a lumpy profile):
    the tube cannot fold, whatever the settings. Returns (points, swell)."""
    wide = radius * (1.0 + 0.6 * s.swell) * 1.3
    ds = float(np.clip(0.5 * radius, length / 400.0, length / 12.0))
    k = max(3, int(math.ceil(length / ds)) + 1)
    ds = length / (k - 1)
    max_turn = ds / (1.6 * wide)
    wander = s.c_dir * 0.9 * max_turn
    g = s.droop * DROOP_RATE                         # radians per unit length when the cable runs level
    kk = 2 * math.pi / s.wavelength
    swing = s.waviness / 2.0 * kk                    # heading amplitude of a wave of that sideways amplitude
    down = np.array([0.0, 0.0, -1.0])
    d = unit(d0)
    side = mh.perpendicular(d) if side0 is None else unit(np.asarray(side0, float) - (np.asarray(side0, float) @ d) * d)
    P = np.empty((k, 3))
    P[0] = start
    omega = np.zeros(3)
    noise = rng.normal(size=(k, 3))
    h = rng.uniform(0, 2 * math.pi)
    fall = np.array([math.cos(h), math.sin(h), 0.0])  # which way it tips over when it starts pointing up
    for i in range(1, k):
        turn = np.zeros(3)
        if wander > 0:
            omega = 0.85 * omega + 0.15 * noise[i] * wander * 3.0
            turn += np.cross(omega - (omega @ d) * d, d)
        if swing > 0:
            turn += side * (swing * kk * math.cos(kk * (i - 0.5) * ds + phase) * ds)
        if g > 0:
            # gravity bends a cable by how level it runs; one pointing up would balance there, so it tips over
            upright = max(0.0, -(down @ d))
            pull = (down - (down @ d) * d) + 0.6 * upright * (fall - (fall @ d) * d)
            turn += pull * g * ds
        ang = float(np.linalg.norm(turn))
        if ang > max_turn:
            turn *= max_turn / ang
        d = unit(d + turn)
        side = unit(side - (side @ d) * d)           # carried along without twisting
        P[i] = P[i - 1] + d * ds
    svals = np.arange(k) * ds
    swell = np.ones(k)
    if s.swell > 0:
        reg = np.sin(2 * math.pi * svals / s.wavelength)
        ph = rng.uniform(0, 2 * math.pi, 3)
        rnd = sum(np.sin(2 * math.pi * svals / (s.wavelength * f) + p) for f, p in zip((0.7, 1.3, 2.1), ph)) / 2.0
        ease = np.clip(svals / (2 * radius), 0, 1)       # the cable leaves the skin at its own width
        swell = 1.0 + s.swell * 0.6 * ease * ((1 - s.c_shape) * reg + s.c_shape * np.clip(rnd, -1, 1))
    return P, swell


def cable(r: Rod, s: Settings, ext: tuple, rng) -> None:
    phase = 2 * math.pi * r.rnd[7] * s.c_pos
    if s.extend == "through":
        (e, _), (x, _) = r.crossings
        A, sa = cable_branch(e, -r.axis, ext[0], r.radius, s, rng, phase)
        B, sb = cable_branch(x, r.axis, ext[1], r.radius, s, rng, phase)
        m = max(2, int(math.ceil(np.linalg.norm(x - e) / max(r.radius, 1e-9))))
        mid = e + (x - e) * np.linspace(0, 1, m + 1)[1:-1, None]
        r.path = np.concatenate([A[::-1], mid, B])
        r.scale = np.concatenate([sa[::-1], np.ones(len(mid)), sb])
        tA, tB = np.linspace(1, 0, len(A)), np.linspace(0, 1, len(B))
        t = np.concatenate([tA, np.zeros(len(mid)), tB])
        r.skeleton = [A, B]
        r.free = (True, True)
    else:
        (x, _), = r.crossings
        B, sb = cable_branch(x, r.axis, ext[1], r.radius, s, rng, phase)
        root = x - r.axis * s.root
        r.path = np.concatenate([[root], B])
        r.scale = np.concatenate([[1.0], sb])
        t = np.concatenate([[0.0], np.linspace(0, 1, len(B))])
        r.skeleton = [B]
        r.free = (False, True)
    r.scale = r.scale * (1.0 + (s.taper - 1.0) * t)


def flare_zone(r: Rod, s: Settings, outside: float) -> float:
    return float(min(3.0 * r.radius, 0.45 * max(outside, 1e-9)))


def shape_ends(P, scale, r: Rod, s: Settings) -> tuple[np.ndarray, np.ndarray]:
    """Flared, round or pointed free ends (flat ends need nothing): a bell over the last few radii, a dome or a cone
    added beyond the end."""
    P, scale = np.asarray(P, float), np.asarray(scale, float).copy()
    if s.family == "spike" or s.tip == "flat":
        return P, scale
    arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
    for at_end, free in ((False, r.free[0]), (True, r.free[1])):
        if not free:
            continue
        dist = (arc[-1] - arc) if at_end else arc
        if s.tip == "flared":
            fl = flare_zone(r, s, 0.45 * arc[-1])
            u = np.clip((fl - dist) / max(fl, 1e-9), 0, 1)
            scale *= 1.0 + (s.flare - 1.0) * u * u
            continue
        if at_end:
            end, t, se = P[-1], unit(P[-1] - P[-2]), scale[-1]
        else:
            end, t, se = P[0], unit(P[0] - P[1]), scale[0]
        h = r.radius * se
        if s.tip == "round":
            phi = np.linspace(0, math.pi / 2, 9)[1:]
            add = end + t[None, :] * (h * np.sin(phi))[:, None]
            add_s = se * np.maximum(np.cos(phi), 0.06)
        else:                                        # pointed
            add = end + t[None, :] * (1.6 * h)
            add_s = np.array([0.03])
            add = add[None, :] if add.ndim == 1 else add
        if at_end:
            P, scale = np.concatenate([P, add]), np.concatenate([scale, add_s])
        else:
            P, scale = np.concatenate([add[::-1], P]), np.concatenate([add_s[::-1], scale])
    return P, scale


def bore(r: Rod, s: Settings, body_path, body_scale) -> tuple[np.ndarray, np.ndarray]:
    """A pipe's bore: the body's line (flare included, caps not) times 1 − Wall, carried past every free end so the
    ends are open; with an open bore, back through the model to its far side as well. The bore never keeps a ring
    in the plane of one of the body's end caps: its vertices would lie on that face, and the cut would leave
    pinched edges. Its end rings are moved past the caps instead."""
    P = np.array(body_path, dtype=float)
    sc = np.asarray(body_scale, float) * (1.0 - s.wall)
    over = 1.5 * r.radius * max(float(np.max(body_scale)), 1.0)
    t0, t1 = unit(P[0] - P[1]), unit(P[-1] - P[-2])
    if r.free[0]:
        P[0] = P[0] + t0 * over
    elif s.bore == "open" and r.back is not None:
        P[0] = r.back + t0 * over                     # the root's ring goes: the bore runs on to the far side
    else:
        # plugged: start part way up the root, past the body's buried end cap; the model fills the bore below the
        # skin anyway
        arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
        off = min(max(0.5 * s.root, 0.2 * r.radius), 0.5 * arc[-1])
        i = int(np.searchsorted(arc, off, side="right"))
        f = (off - arc[i - 1]) / max(arc[i] - arc[i - 1], 1e-12)
        start = P[i - 1] + (P[i] - P[i - 1]) * f
        P = np.concatenate([[start], P[i:]])
        sc = np.concatenate([[sc[i - 1] + (sc[i] - sc[i - 1]) * f], sc[i:]])
    if r.free[1]:
        P[-1] = P[-1] + t1 * over
    return P, sc


# ----------------------------------------------------------------------------- planning

@dataclass
class Plan:
    rods: list
    proposed: int
    dropped: int = 0
    shrunk: int = 0
    spaced_out: int = 0


def plan(model: Model, s: Settings, values: dict, progress=None) -> Plan:
    """Every rod that will be added: crossing points, centre lines, outlines and spacing (no solids yet)."""
    rs = mh.streams(s.seed)
    rods = candidates(model, s, rs)
    proposed = len(rods)
    if progress:
        progress(0.1, f"{proposed} candidates")
    n = len(rods)
    sizes = mh.draw_sizes(s, rs["size"].random(n))
    reach_u = rs["size"].random((n, 2))
    nudge = rs["nudge"].random((n, 4)) - 0.5
    jitter(rods, s, sizes, nudge, model.height)
    rods = cross(model, rods, s)
    # influence: weight at the crossings scales width and reach; rods it leaves out go
    weights = []
    if (values.get("influence", "all") or "all") != "all":
        kept = []
        for r in rods:
            w = float(influence.weights(np.array([p for p, _ in r.crossings]), values, model.lo, model.hi).max())
            if w >= 0.05:
                r.radius *= w
                kept.append(r)
                weights.append(w)
        rods = kept
    else:
        weights = [1.0] * len(rods)
    keep = rs["drop"].random(len(rods)) >= s.drop
    dropped = int((~keep).sum())
    rods = [r for r, k in zip(rods, keep) if k]
    weights = [w for w, k in zip(weights, keep) if k]
    if len(rods) > MAX_RODS:
        raise ValueError(f"{len(rods)} rods is more than {MAX_RODS}: raise Spacing, lower Count or leave some out")
    shared = mh.blob_noise(np.random.default_rng(np.random.SeedSequence(s.seed).spawn(1)[0]))
    srng = rs["shape"]
    for i, r in enumerate(rods):
        if progress and i % 50 == 0:
            progress(0.15 + 0.35 * i / max(len(rods), 1), "laying out rods")
        u = reach_u[i]
        ext = tuple((s.reach + (s.reach_max - s.reach) * u) * weights[i])
        if s.family == "cable":
            cable(r, s, ext, rs["worm"])
        else:
            straight(r, s, ext)
        r.shape = mh.profile(s.profile, s.segments, s.sides, s.roundness, s.lobes, s.lumpiness, r.aspect, shared,
                             mh.blob_noise(srng), s.c_shape)
        k = float(np.linalg.norm(r.shape, axis=1).max())
        top = float(np.max(r.scale)) * (s.flare if (s.tip == "flared" and s.family != "spike") else 1.0)
        r.reach = r.radius * k * max(top, 1.0)
    before = len(rods)
    rods, shrunk = mh.space(rods, s.gap, min_radius=0.5 * s.size, fixed=(s.size_max - s.size) < 1e-9,
                            progress=(lambda t, msg="": progress(0.5 + 0.4 * t, "spacing")) if progress else None)
    return Plan(rods, proposed, dropped, shrunk, before - len(rods))


# ----------------------------------------------------------------------------- solids

def sweep(outline, P, scale, turn, e1=None):
    """A Manifold along a polyline (Holes' piecewise sweep: a long cable may cross itself)."""
    return mh.sweep_chunks(outline, P, scale, turn, e1)


def solids(r: Rod, s: Settings) -> tuple[list, list]:
    """(added solids, bores) of one rod: its body and collars, and a pipe's bore."""
    outline = r.shape * r.radius
    body_path, body_scale = r.path, r.scale
    P, sc = shape_ends(body_path, body_scale, r, s)
    arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
    add = [sweep(outline, P, sc, r.roll + s.twist * arc, r.e1)]
    if s.collars:
        for c, t in r.crossings:
            half = s.collar_length / 2.0
            cp = np.array([c - t * half, c + t * half])
            add.append(sweep(outline * s.collar_size, cp, np.ones(2), np.full(2, r.roll), r.e1))
    cut = []
    if s.family == "pipe":
        if s.tip == "flared":
            bp, bs = bore(r, s, P, sc)
        else:
            bp, bs = bore(r, s, body_path, body_scale)
        barc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(bp, axis=0), axis=1))])
        cut.append(sweep(outline, bp, bs, r.roll + s.twist * barc, r.e1))
    return add, cut


def build(model_m, rods: list, s: Settings, progress=None):
    """(result, tubes): the model with the rods added (and open bores cut), and the rods on their own."""
    import manifold3d
    adds, cuts = [], []
    for i, r in enumerate(rods):
        if progress and i % 25 == 0:
            progress(i / max(len(rods), 1), f"building rods ({i} of {len(rods)})")
        a, c = solids(r, s)
        adds += a
        cuts += c
    if not adds:
        return model_m, manifold3d.Manifold()
    added = manifold3d.Manifold.batch_boolean(adds, manifold3d.OpType.Add)
    bores = manifold3d.Manifold.batch_boolean(cuts, manifold3d.OpType.Add) if cuts else None
    tubes = added - bores if bores is not None else added
    if bores is not None and s.bore == "open":
        result = (model_m + added) - bores
    else:
        result = model_m + tubes
    return result, tubes
