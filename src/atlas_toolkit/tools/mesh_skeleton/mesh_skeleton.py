"""
Skeleton Shell: empty a model and keep only a structure on its skin.

The input is read as a surface, never as a volume: nothing has to be closed and nothing is ever built inside, apart from
the frames of a ribbed structure, which reach inward from the skin. The result is one layer of metal that you can see
through to the far side, the way a wire or plate sculpture is, and it is what makes a drawing of it read as depth.

    build(V, F, p, ...)     skin -> one or two structures -> a kept patch of solid skin -> solids -> one mesh or a fused
                            solid. V is in the canonical frame (geom/frame3d: height 2).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from atlas_toolkit.geom import curvature as cv
from atlas_toolkit.geom import influence

from . import layouts as lay
from . import wires as wr
from .skin import Skin, shell


@dataclass
class Report:
    wires: int = 0
    joints: int = 0
    plates: int = 0
    frames: int = 0
    length: float = 0.0
    coverage: float = 0.0           # share of the skin's area that stays metal, for plates and kept skin
    kept: float = 0.0               # share of the skin's area kept solid
    edge: float = 0.0
    fused: bool = False
    pieces: int = 0
    notes: list = field(default_factory=list)


@dataclass
class Built:
    V: np.ndarray
    F: np.ndarray
    report: Report


def pct(value: float, height: float) -> float:
    return float(value) / 100.0 * height


def layer_settings(p, height: float, second: bool = False) -> lay.Layout:
    structure = p["second"] if second else p["structure"]
    size = pct(p["second_size"] if second else p["size"], height)
    return lay.Layout(
        structure=structure, size=size, regularity=float(p["regularity"]),
        straightness=float(p["straightness"]), stretch=float(p["stretch"]), detail=float(p["detail"]),
        missing=float(p["missing"]) / 100.0, seed=int(p["seed"]) + (101 if second else 0),
        flow_field=p["flow_field"], flow_angle=float(p["flow_angle"]), crossing=bool(p["crossing"]),
        strand_length=pct(p["strand_length"], height), min_length=pct(p["min_length"], height),
        wander=float(p["wander"]), feature=float(p["feature"]), flow_smooth=int(p["flow_smooth"]),
        rib_axis=p["rib_axis"], rib_profile=p["rib_profile"],
        stringer_spacing=pct(p["stringer_spacing"], height), stringer_style=p["stringer_style"],
        frame_depth=pct(p["frame_depth"], height), lightening=bool(p["lightening"]), keel=bool(p["keel"]),
        thickness=pct(p["thickness"], height), plate_pattern=p["plate_pattern"], band=pct(p["band"], height),
        rounding=int(p["rounding"]))


def dressing(p, height: float, L: lay.Layout, second: bool, draft: bool, rib_profile: str | None = None) -> wr.Dress:
    wire = pct(p["second_wire"] if second else p["wire"], height)
    profile = rib_profile or (p["second_profile"] if second else p["profile"])
    return wr.Dress(
        wire=wire, profile=profile, flat=float(p["flatness"]),
        sides=min(int(p["sides"]), 6) if draft else int(p["sides"]),
        step=max(wire * 1.2, L.size / (6.0 if draft else 10.0)),
        smooth={"flow": 30, "ribs": 12}.get(L.structure, 5),
        settle={"flow": 8, "ribs": 8}.get(L.structure, 2),
        vary=float(p["wire_vary"]), swell=float(p["swell"]), taper=float(p["taper"]),
        wobble=float(p["wobble"]) * L.size * 0.16, scale=L.size, overrun=float(p["overrun"]) * L.size,
        hubs=True, seed=int(p["seed"]) + (101 if second else 0))


def solid_field(skin: Skin, p, lo, hi, seeds, size: float, rng, stretch: float) -> np.ndarray | None:
    """Negative where the skin stays whole. Cell by cell, so the edge is finished and the feather dissolves it unevenly:
    each cell lets go at its own moment across the feathered zone."""
    if (p.get("influence", "all") or "all") == "all":
        return None
    graph = skin.metric(None, stretch)
    if seeds is None or len(seeds) < 2:
        area_per_seed = 0.866 * size * size
        seeds = skin.relax(int(min(skin.area / area_per_seed, lay.MAX_SEEDS)), rng, None, stretch, iterations=4)
    acts = influence.weights(skin.V[seeds], p, lo, hi)
    solid = acts < rng.random(len(seeds))
    if solid.all():
        return -np.ones(len(skin.V))
    if not solid.any():
        return np.ones(len(skin.V))
    near_solid = skin.nearest(graph, seeds[solid])[0]
    near_open = skin.nearest(graph, seeds[~solid])[0]
    gap = near_solid - near_open
    return np.nan_to_num(gap, posinf=skin.height, neginf=-skin.height)


def clip_wires(wires: list, skin: Skin, field_: np.ndarray) -> list:
    """Cut wires where they enter the kept skin: what is inside is gone, and the cut ends are free."""
    from scipy.spatial import cKDTree
    tree = cKDTree(skin.V)
    out = []
    for w in wires:
        _, nearest = tree.query(w.points, workers=-1)
        free = field_[nearest] >= 0.0
        if free.all():
            out.append(w)
            continue
        if not free.any():
            continue
        edges = np.diff(free.astype(np.int8), prepend=0, append=0)
        starts, stops = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        for a, b in zip(starts, stops):
            if b - a < 2:
                continue
            piece = wr.Wire(w.points[a:b], False, (w.ends[0] if a == 0 else -1,
                                                   w.ends[1] if b == len(free) else -1), w.weight, w.follow)
            piece.normals, piece.radii = w.normals[a:b], w.radii[a:b]
            out.append(piece)
    return out


def fuse(solids: list, report: Report, progress=None) -> tuple[np.ndarray, np.ndarray]:
    import manifold3d as mf
    parts = []
    skipped = 0
    for V, F in solids:
        mesh = mf.Mesh(np.asarray(V, np.float32), np.asarray(F, np.uint32))
        solid = mf.Manifold(mesh)
        if solid.status() == mf.Error.NoError and not solid.is_empty():
            parts.append(solid)
        else:
            skipped += 1
    if not parts:
        raise ValueError("Nothing could be fused. Turn Fuse off, or raise Wire thickness.")
    if progress:
        progress()
    joined = mf.Manifold.batch_boolean(parts, mf.OpType.Add)
    report.pieces = len(joined.decompose())
    if skipped:
        report.notes.append(f"{skipped} solids that were not closed were left out of the fused result")
    mesh = joined.to_mesh()
    return np.asarray(mesh.vert_properties, np.float64)[:, :3], np.asarray(mesh.tri_verts, np.int64)


def build(V, F, p, lo=None, hi=None, progress=None, draft: bool = False) -> Built:
    tick = progress or (lambda *_: None)
    V, F = np.asarray(V, np.float64), np.asarray(F, np.int64)
    lo = V.min(0) if lo is None else np.asarray(lo, np.float64)
    hi = V.max(0) if hi is None else np.asarray(hi, np.float64)
    height = float(hi[2] - lo[2]) or float((hi - lo).max()) or 1.0
    rng = np.random.default_rng(int(p["seed"]))
    report = Report()
    primary = layer_settings(p, height)
    settings = [primary]
    if p["second"] != "none":
        settings.append(layer_settings(p, height, second=True))
    edge = min(lay.wanted_edge(L) for L in settings)
    if p["influence"] != "all":
        edge = min(edge, pct(p["size"], height) / 8.0)
    tick(0.05, "preparing the skin")
    budget = int(p["draft_faces"] if draft else p["working_faces"])
    skin = Skin(V, F, edge, budget, speck=(primary.size * 0.5) ** 2)
    report.edge = skin.edge
    if skin.edge > edge * 1.5:
        report.notes.append(f"the working mesh is coarser than this pattern wants: raise Working faces for cleaner "
                            f"{'cuts' if primary.structure == 'plate' else 'lines'}")
    solids, plate_fields = [], []
    seeds_for_keep = None
    built_layers = []
    for i, L in enumerate(settings):
        share = 0.15 + 0.5 * i / len(settings)
        tick(share, f"building {'the second' if i else 'the first'} structure")
        layer = lay.build(skin, L, np.random.default_rng(L.seed), progress=lambda: tick(share, "tracing strands"))
        report.notes += layer.notes
        if layer.seeds is not None and seeds_for_keep is None:
            seeds_for_keep = layer.seeds
        built_layers.append((L, layer, i > 0))
    keep = solid_field(skin, p, lo, hi, seeds_for_keep, primary.size, rng, primary.stretch)
    tick(0.7, "dressing the wires")
    for L, layer, second in built_layers:
        rib_profile = None
        if L.structure == "ribs":
            rib_profile = {"strap": "strap", "rod": "round", "fin": "fin"}.get(L.rib_profile)
        groups = [(layer.wires, dressing(p, height, L, second, draft, rib_profile)),
                  (layer.stringers, dressing(p, height, L, second, draft))]
        for raw, d in groups:
            done = wr.dress(raw, skin, d)
            if keep is not None:
                done = clip_wires(done, skin, keep)
            if wr.expected_faces(done, d) > int(p["max_faces"]) * 2:
                raise ValueError("The structure exceeds Output faces. Raise Cell size or lower Wire sides.")
            solids += wr.sweep(done, d)
            report.wires += len(done)
            report.length += float(sum(wr.arc_length(w.points, w.closed)[-1] for w in done))
            report.joints += len({n for w in done for n in w.ends if n >= 0})
        report.frames += len(layer.solids)
        solids += layer.solids
        if layer.plate is not None:
            plate_fields.append(layer.plate)
    tick(0.85, "cutting the plates")
    thickness = pct(p["thickness"], height)
    # plates and kept skin are one sheet: a plate runs into the skin it meets without a seam
    sheets = plate_fields + ([keep] if keep is not None else [])
    if sheets:
        pv, pf, pn = skin.clip(np.minimum.reduce(sheets))
        if len(pf):
            solids.append(shell(pv, pf, pn, thickness))
            report.plates = 1
            report.coverage = float(cv.face_normals(pv, pf)[1].sum() / max(skin.area, 1e-12))
    if keep is not None:
        report.kept = float(skin.vertex_areas[keep < 0].sum() / max(skin.area, 1e-12))
        if p["rim"] and report.kept > 0.0:
            d = dressing(p, height, primary, False, draft)
            rim = wr.dress([wr.Wire(P, closed) for P, closed in skin.isolines(keep, 0.0)], skin, d)
            solids += wr.sweep(rim, d)
            report.wires += len(rim)
    if not solids:
        raise ValueError("The structure came out empty. Lower Cell size or Spacing, or Missing wires.")
    faces = sum(len(f) for _, f in solids)
    if faces > int(p["max_faces"]):
        raise ValueError(f"The structure has {faces:,} faces, above Output faces. Raise Cell size, lower Wire sides, or "
                         f"raise the limit.")
    if p["fuse"] and not draft:
        tick(0.9, "fusing the structure")
        report.fused = True
        out_v, out_f = fuse(solids, report, lambda: tick(0.92, "fusing the structure"))
    else:
        out_v, out_f = concatenate(solids)
        if p["fuse"]:
            report.notes.append("the preview does not fuse; Run does")
    return Built(out_v, out_f, report)


def concatenate(solids: list) -> tuple[np.ndarray, np.ndarray]:
    offset, vs, fs = 0, [], []
    for V, F in solids:
        vs.append(V)
        fs.append(np.asarray(F, np.int64) + offset)
        offset += len(V)
    return np.vstack(vs), np.vstack(fs)
