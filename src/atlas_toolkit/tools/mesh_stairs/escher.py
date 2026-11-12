"""
The ensemble of a carved cavity: flights, landings, piers and arches on a seeded grid of storeys, in the manner of an
impossible architecture. A walker climbs from platform to platform, so every flight arrives somewhere.

Local coordinates: U across the opening, W into the model, Z up. The caller clips everything to the cavity.
"""
from __future__ import annotations

import math

import manifold3d as mf
import numpy as np


def arch_hole(u0: float, u1: float, z0: float, z1: float) -> np.ndarray:
    """An arched opening in the (U, Z) plane; the semicircle springs below z1."""
    r = (u1 - u0) / 2
    spring = max(z0 + 1e-3, z1 - r)
    a = np.linspace(0.0, math.pi, 17)
    arc = np.column_stack(((u0 + u1) / 2 + np.cos(a) * r, spring + np.sin(a) * r))
    return np.vstack(([[u0, z0], [u1, z0]], arc))


def standing(poly, holes, a0: float, a1: float, along_w: bool = False) -> mf.Manifold:
    """A wall: `poly` in the plane of a run and Z, `holes` cut from it, between a0 and a1 across the run. The run goes
    along U, or along W when `along_w`."""
    from .mesh_stairs import ccw
    section = mf.CrossSection([ccw(poly)])
    for h in holes:
        section = section - mf.CrossSection([ccw(h)])
    if section.is_empty():
        return mf.Manifold()
    solid = mf.Manifold.extrude(section, float(a1 - a0))
    if along_w:      # section x -> W, section y -> Z, extrusion -> U
        return solid.transform(np.array([[0.0, 0.0, 1.0, a0], [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]))
    # section x -> U, section y -> Z, extrusion -> -W: a proper rotation, so the solid stays outward-facing
    return solid.transform(np.array([[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, -1.0, a1], [0.0, 1.0, 0.0, 0.0]]))


def layout(lo, hi, flights: int) -> tuple:
    """(cells along U, cells along W, storeys) so a flight rises at about 38° through one cell."""
    size = np.maximum(np.asarray(hi) - np.asarray(lo), 1e-3)
    volume = float(np.prod(size))
    # Roughly half of the box is usually outside the cavity, and a flight needs its neighbourhood free.
    storey = (volume / (1.28 * 0.8 * 8.0 * max(flights, 2))) ** (1.0 / 3.0)
    nu = int(max(2, round(size[0] / (1.28 * storey))))
    nw = int(max(1, round(size[1] / (0.8 * storey))))
    nz = int(max(2, round(size[2] / storey)))
    return nu, nw, nz


def walk(nu, nw, nz, flights, kind, rng, inside=None) -> tuple:
    """Platforms {(i, j, k)} and connections [(from, to, form)]. A flight goes to a neighbouring cell one storey up;
    in a turning stair consecutive flights alternate between U and W; a helix climbs two storeys in place. inside(i,
    j, k) says whether a platform there is really in the cavity, so nothing is lost in the walls."""
    if inside is None:
        inside = lambda i, j, k: True   # noqa: E731
    cells = [(i, j, k) for i in range(nu) for j in range(nw) for k in range(nz) if inside(i, j, k)]
    if not cells:
        return set(), []
    platforms, links, crossed = set(), [], set()
    want_u = rng.random() < 0.5
    guard = 0
    start_cells = [cells[int(rng.integers(len(cells)))]]
    while len(links) < flights and guard < 400 * max(flights, 1):
        guard += 1
        here = start_cells[int(rng.integers(len(start_cells)))]
        last_u = None
        while len(links) < flights:
            i, j, k = here
            if (kind == "helix" and k + 2 <= nz and inside(i, j, k + 2) and (i, j, k, "tower") not in crossed
                    and rng.random() < 0.55):
                to = (i, j, k + 2)
                crossed |= {(i, j, k, "tower"), (i, j, k + 1, "tower"), (i, j, k), (i, j, k + 1)}
                links.append((here, to, "helix"))
                platforms |= {here, to}
                here = to
                start_cells.append(here)
                continue
            if k >= nz:
                break
            moves = []
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                a, b = i + di, j + dj
                if not (0 <= a < nu and 0 <= b < nw) or not inside(a, b, k + 1):
                    continue
                along_u = di != 0
                if kind == "turns" and last_u is not None and nw > 1 and along_u == last_u:
                    continue
                if (a, b, k) in crossed or (i, j, k) in crossed:
                    continue
                weight = 3.0 if along_u == want_u else 1.0
                moves.append(((a, b), along_u, weight))
            if not moves:
                break
            weights = np.array([m[2] for m in moves], float)
            (a, b), along_u, _ = moves[int(rng.choice(len(moves), p=weights / weights.sum()))]
            to = (a, b, k + 1)
            crossed |= {(a, b, k), (i, j, k)}
            links.append((here, to, "flight"))
            platforms |= {here, to}
            last_u = along_u
            here = to
            start_cells.append(here)
        want_u = not want_u if rng.random() < 0.25 else want_u
        if guard % 3 == 0:                   # a new ascent, in the emptiest of a few places, so the cavity fills up
            picks = rng.integers(len(cells), size=6)
            used = np.array(sorted(platforms)) if platforms else np.zeros((1, 3))

            def room(c):
                return float(np.min(np.sum(np.abs(used - np.array(c)), axis=1)))
            start_cells.append(max((cells[int(q)] for q in picks), key=room))
    return platforms, links


def build(lo, hi, p, contains=None) -> tuple:
    """(solids, flights built, steps built) for a cavity's box. contains(u, w, z) -> is that point in the cavity."""
    from .mesh_stairs import column, flight_solid, helix_steps, linear_steps, prism, rect
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    flights = int(p["flights"])
    rng = np.random.default_rng(int(p["seed"]))
    kind = p["stair_type"]
    nu, nw, nz = layout(lo, hi, flights)
    if kind == "turns":
        nw = max(nw, 2)
    cu, cw, storey = (hi[0] - lo[0]) / nu, (hi[1] - lo[1]) / nw, (hi[2] - lo[2]) / nz

    def inside(i, j, k):
        if contains is None:
            return True
        u, w, z = lo[0] + (i + 0.5) * cu, lo[1] + (j + 0.5) * cw, lo[2] + k * storey
        room = (0.3 * cu, 0.3 * cw)
        return all(contains(u + a * room[0], w + b * room[1], z + 0.2 * storey) for a in (-1, 1) for b in (-1, 1))

    platforms, links = walk(nu, nw, nz, flights, kind, rng, inside)
    each = max(4, int(p["steps"]) // max(len(links), 1))
    pu, pw = 0.28 * cu, 0.36 * cw
    slab = min(0.14 * storey, 0.04 * (hi[2] - lo[2]))
    pier = 0.11 * min(cu, cw)
    style = p["support"]
    arches = bool(p["arches"])
    thick = max(p["thickness"] / 100.0 * 2.0, slab)
    rail_h = p["parapet"] / 100.0 * 2.0
    solids, count, stepsum = [], 0, 0

    def at(i, j, k):
        return lo[0] + (i + 0.5) * cu, lo[1] + (j + 0.5) * cw, lo[2] + k * storey

    # Every platform stands on piers down to whatever lies below it, with an arcade at its back.
    occupied = {}
    for i, j, k in platforms:
        occupied.setdefault((i, j), []).append(k)
    for (i, j), levels in occupied.items():
        levels.sort()
        for n, k in enumerate(levels):
            if k == 0:
                continue
            u, w, z = at(i, j, k)
            below = lo[2] - storey if n == 0 else at(i, j, levels[n - 1])[2] - slab
            solids.append(prism(rect(u - pu, u + pu, w - pw, w + pw), z - slab, z))
            for su in (-1.0, 1.0):
                for sw in (-1.0, 1.0):
                    a, b = u + su * (pu - pier / 2), w + sw * (pw - pier / 2)
                    solids.append(prism(rect(a - pier / 2, a + pier / 2, b - pier / 2, b + pier / 2), below, z - slab))
            if arches:
                top = z - slab
                below = max(below, top - storey)     # an arcade one storey high; only the piers go further down
                bay = 2 * pu
                n_arch = 1 if bay < 1.3 * (top - below) else 2
                holes = []
                width = bay / n_arch
                for m in range(n_arch):
                    a = u - pu + m * width + width * 0.16
                    b = u - pu + (m + 1) * width - width * 0.16
                    holes.append(arch_hole(a, b, below - 0.01, below + (top - below) * 0.72))
                outline = rect(u - pu, u + pu, below, top + 1e-3)
                solids.append(standing(outline, holes, w + pw - pier, w + pw))

    for here, to, form in links:
        count += 1
        (i0, j0, k0), (i1, j1, k1) = here, to
        u0, w0, z0 = at(i0, j0, k0)
        u1, w1, z1 = at(i1, j1, k1)
        if form == "helix":
            r = 0.46 * min(cu, cw)
            c = np.array([u0, w0])
            turns = max(float(p["turns"]) if float(p["turns"]) <= 2 else 2.0, 0.75)
            n = max(each, 8)
            steps = helix_steps(lambda z: c, r * 0.28, r, z0, z1, n, turns, float(rng.random() * 6.28), 1.0, 0.0,
                                r * 0.05 if rail_h > 0 else 0.0)
            solids.append(flight_solid(steps, "smooth", max(thick * 0.5, storey * 0.05), 0.5, 0.0, lo[2], rail_h))
            solids.append(column(lambda z: c, r * 0.3, z0 - slab, z1, n_z=2, segments=16))
            stepsum += n
            continue
        along_u = i0 != i1
        if along_u:
            sign = 1.0 if u1 > u0 else -1.0
            a, b = u0 + sign * pu, u1 - sign * pu
            half = 0.27 * cw
            o0, o1 = w0 - half, w0 + half
        else:
            sign = 1.0 if w1 > w0 else -1.0
            a, b = w0 + sign * pw, w1 - sign * pw
            half = 0.27 * cu
            o0, o1 = u0 - half, u0 + half
        n = each
        zs = z0 + (np.arange(n) + 1) * (z1 - z0) / n
        lip = p["nosing"] / 100.0 * abs(b - a) / n
        rail = 0.14 * half if rail_h > 0 else 0.0
        steps = linear_steps(a, b, o0, o1, zs, lip, rail)
        if not along_u:     # linear_steps runs along its own first axis: this flight runs along W
            steps = _swap(steps)
        lip_h = 0.25 * (z1 - z0) / n
        solids.append(flight_solid(steps, "band" if style == "open" else style, thick, 0.5, lip_h, lo[2], rail_h))
        stepsum += n
        # The solid under the flight, opened by an arch: stair over arch, the ensemble's signature.
        if arches and style != "open":
            rise = (z1 - z0) / n
            soffit = [(a, z0 - slab), (b, z1 - rise * 1.5)]
            floor = lo[2] - storey if k0 == 0 else z0 - 0.75 * storey
            run0, run1 = sorted((a, b))
            low = min(soffit[0][1], soffit[1][1])
            if low - floor > 0.1 * storey:
                poly = np.array([[a, floor], [b, floor], [b, soffit[1][1]], [a, soffit[0][1]]])
                span = abs(b - a)
                hole = arch_hole(run0 + span * 0.2, run1 - span * 0.2, floor - 0.01,
                                 floor + (low - floor) * 0.9)
                solids.append(standing(poly, [hole], o0 + (o1 - o0) * 0.1, o1 - (o1 - o0) * 0.1, not along_u))
    return solids, count, stepsum


def _swap(steps) -> list:
    """Steps laid out as (run, across) turned into (across, run): a flight along W."""
    from .mesh_stairs import Step

    def swap(q):
        return np.asarray(q)[:, ::-1]
    return [Step(swap(s.poly), s.z, s.core[::-1], None if s.nose is None else swap(s.nose), [swap(r) for r in s.rails])
            for s in steps]
