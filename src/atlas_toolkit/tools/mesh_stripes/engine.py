"""Atlas mesh-worker adapter. Preserves source units; preview caches are the only writes in dry runs."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time

import numpy as np

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.core.paths import cache_dir
from atlas_toolkit.geom import frame3d
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom import repair as R
from . import TOOL
from . import mesh_stripes as stripes

FIT_SHARES = (.8, .7, .6, .5, .4, .3, .25)     # of what the surface may take (the rest: splitting)


class OpenBands(ValueError):
    """The banded solid does not stay closed at file precision."""


def separated_pinches(mesh, within, ctx=None):
    """Two band sheets can meet along an edge once float32 welds their vertices (Venus de Milo's drapery): no hole,
    only an edge four faces share, and a coarser simplification made holes of it. Give each sheet its own copy of
    such an edge, as Repair Mesh does (the copies stay apart through every format's reload). They move by a share of the
    faces around them, so the split is refused when that is more than `within`; unchanged otherwise."""
    import trimesh
    V, F, note = R.split_nonmanifold(np.asarray(mesh.vertices, float), np.asarray(mesh.faces, np.int64))
    if not note:
        return mesh
    split = trimesh.Trimesh(V, F, process=False)
    if not (split.is_watertight and split.is_winding_consistent):
        return mesh
    from scipy.spatial import cKDTree
    if cKDTree(np.asarray(mesh.vertices)).query(V)[0].max() > within:
        return mesh
    if ctx:
        ctx.log(f"Stripes: {note}")
    return split


def make_sample(folder):
    import trimesh
    path = Path(folder)/"striped ellipsoid.stl"
    m = trimesh.creation.icosphere(subdivisions=2, radius=20.)
    m.vertices *= [0.75, 0.6, 1.2]
    m.export(path)
    return path


def run(ctx, src, dst, p):
    import trimesh
    from atlas_toolkit.tools.mesh_holes import engine as holes
    from atlas_toolkit.tools.mesh_holes.mesh_holes import from_manifold
    if ctx.dry_run:
        dst = None
    src = Path(src)
    if dst is not None and Path(dst).resolve() == src.resolve():
        raise ValueError("the output must not replace the input model")
    started = time.perf_counter()
    ctx.progress(0.01, "Loading surface")
    stripes_only = bool(p.get("stripes_only", False))
    if stripes_only and float(p.relief) <= 0:
        raise ValueError("Export stripes only needs a positive Raised height for ribbon thickness")
    mesh = R.tidy_input(ctx, gm.load_mesh(src))
    faces_in = len(mesh.faces)
    up, front, rotate, _source = frame3d.resolve_axes(src, p, mesh.extents)
    frame = frame3d.fit_frame(mesh.vertices, up, front, rotate, mode="height", size=2.)
    body = trimesh.Trimesh(frame.apply(mesh.vertices), mesh.faces.copy(), process=False)
    body_solid = holes.solid(body.vertices, body.faces) if body.is_winding_consistent else None
    repaired = body_solid is None
    if repaired:
        if not p.repair:
            raise R.needs_repair(mesh, TOOL, "Repair open mesh")
        resolution = min(int(p.repair_resolution), 112) if ctx.draft else int(p.repair_resolution)
        ctx.warn(f"Closing the input by resampling at {resolution} samples; fine detail can soften.")
        V, F = holes.closed_copy(body.vertices, body.faces, resolution,
                                lambda f, m="": ctx.progress(0.02+f*0.10, "Repair: "+m))
        body = trimesh.Trimesh(V, F, process=True)
        body_solid = holes.solid(body.vertices, body.faces)
        if body_solid is None:
            raise R.needs_repair(mesh, TOOL)
    if body.volume < 0:
        body.invert()
    initial_volume = body_solid.volume()
    spacing = frame.pct_h(p.spacing)
    width, relief = spacing*float(p.band_width)/100., frame.pct_h(p.relief)
    # A coarser field can create thin branching patches that fail float32 solid validation for standalone
    # ribbons. Use the export field for their preview too, sharing the cache and keeping the pattern stable.
    budget = (min(int(p.solve_faces), int(p.draft_faces)) if ctx.draft and not stripes_only else
              int(p.solve_faces))
    cache = Path(os.environ.get("ATLAS_STRIPES_CACHE") or cache_dir()/"stripes")

    def banded(body, lighter):
        components = list(body.split(only_watertight=False))
        results, infos, networks, warnings = [], [], [], []
        for i, component in enumerate(components):
            ctx.check_cancel()
            lo, span = 0.14+0.76*i/len(components), 0.76/len(components)
            network = stripes.curve_network(component, cache, spacing=spacing, budget=budget, field=p.field,
                                            rotation=float(p.rotation), families=p.families, seed=int(p.seed),
                                            check=ctx.check_cancel,
                                            progress=lambda f, m: ctx.progress(lo+span*0.45*f, m))
            d = network["working_mesh"]
            if d["underresolved_edges"]:
                warnings.append(f"Component {i+1}: {d['underresolved_edges']} field edges exceed one third of the spacing; "
                                "the interpolation is approximate. Increase Field face budget for smoother bands.")
            if network.get("curvature_fallback"):
                warnings.append(f"Component {i+1}: curvature was undefined; using the smoothest field.")
            result, info = stripes.raised_bands(component, network, width, relief, spacing,
                                                stripes_only=stripes_only,
                                                max_faces=int(p.max_faces)-sum(q["band_faces"] for q in infos),
                                                longest_first=lighter,
                                                check=ctx.check_cancel,
                                                progress=lambda f, m: ctx.progress(lo+span*(0.45+0.55*f), m))
            results.append(result)
            if info["underresolved_edges"]:
                warnings.append(f"Component {i+1}: the band face budget limits surface sampling; "
                                "increase Band face budget or stripe spacing for finer rims.")
            infos.append(info)
            networks.append(network)
        return components, results, infos, networks, warnings

    def closed_output(results, infos):
        import manifold3d
        outer = [r for r, info in zip(results, infos) if not info["is_void"]]
        voids = [r for r, info in zip(results, infos) if info["is_void"]]
        if not outer:
            raise ValueError("the input contains no outward-oriented body surface")
        result_solid = (outer[0] if len(outer) == 1 else
                        manifold3d.Manifold.batch_boolean(outer, manifold3d.OpType.Add))
        if voids:
            result_solid = result_solid-manifold3d.Manifold.batch_boolean(voids, manifold3d.OpType.Add)
        # Boolean intersections can leave slivers smaller than STL/PLY's float32 precision. Collapse them before
        # export so Atlas's next load (which welds vertices) retains the same closed topology.
        magnitude = float(np.abs(mesh.vertices).max()) or 1.
        tolerance = max(1e-6, magnitude*np.finfo(np.float32).eps*frame.scale*2.)
        if relief > 0 and tolerance > min(width, relief)*0.05:
            raise ValueError("source coordinates are too large for the band detail in float32 mesh formats; "
                             "recenter the model or increase band width/relief")
        # Increase the collapse tolerance only when quantization needs it, bounded to 5% of the finest band detail.
        limit = max(tolerance, min(width, relief if relief > 0 else width)*0.05)
        while True:
            stable = result_solid.simplify(tolerance)
            V, F = from_manifold(stable)
            world = np.asarray(frame.inverse(V), dtype=np.float32).astype(np.float64)
            output = stripes.precision_mesh(world, F, tolerance/frame.scale, preserve_shells=stripes_only)
            if not output.is_watertight and output.is_winding_consistent:
                # a handful of vertices may move by up to 10% of the finest band detail, the whole mesh by only 5%
                output = separated_pinches(output, 0.1*min(width, relief if relief > 0 else width)/frame.scale, ctx)
            if output.is_watertight and output.is_winding_consistent and output.volume > 0:
                return output, stable
            if tolerance >= limit:
                raise OpenBands("the stripe result failed closed-solid validation; increase band width or relief")
            tolerance = min(tolerance*10., limit)

    dense, worked, tries = len(body.faces), None, [None]
    if p.fit and relief > 0:
        allowed = int(p.max_faces)//4
        # A single component over its share is certain to be refused: do not solve its field first.
        tries = [] if dense > allowed and len(body.split(only_watertight=False)) == 1 else [None]
        tries += [int(allowed*share) for share in FIT_SHARES if allowed*share < dense]
    refused = None
    for target in tries:
        lighter = body
        if target is not None:
            ctx.progress(0.13, f"simplifying the model to {target:,} faces so its bands fit the budget")
            reduced = R.simplified_solid(body.vertices, body.faces, target)
            if reduced is None:
                continue
            lighter = trimesh.Trimesh(*reduced, process=False)
            if abs(lighter.volume/initial_volume - 1.) > .02:       # it lost a cavity or a piece, not only detail
                continue
        try:
            components, results, infos, networks, warnings = banded(lighter, target is not None)
            output, result_solid = closed_output(results, infos)
        except stripes.FaceBudget as error:
            refused = error
            continue
        except OpenBands as error:          # two sheets of a lighter copy can weld by chance: another one need not
            if target is None:
                raise
            refused = error
            continue
        break
    else:
        raise refused or ValueError("the model is too dense for the Band face budget and no lighter copy of it stayed "
                                    "a closed solid; raise the budget or simplify the input")
    for warning in warnings:
        ctx.warn(warning)
    if lighter is not body:
        worked = len(lighter.faces)
        initial_volume = float(lighter.volume)
        ctx.note(f"Banded a copy simplified from {dense:,} to {worked:,} faces so that its bands fit the Band face "
                 f"budget ({int(p.max_faces):,}). Simplify to fit; raise the budget to keep more detail.")
    ctx.progress(0.94, "Saving solid model" if dst is not None else "Preparing preview")
    outputs = []
    if dst is not None:
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".atlas-stripes-", suffix=dst.suffix, dir=dst.parent)
        os.close(fd)
        staged = Path(name)
        try:
            gm.save_mesh(output, staged, TOOL)
            reread = gm.load_mesh(staged)
            if not reread.is_watertight or not reread.is_winding_consistent or reread.volume <= 0:
                raise ValueError("the saved stripe mesh lost solid topology; increase stripe width or relief")
            ctx.check_cancel()
            os.replace(staged, dst)
        finally:
            staged.unlink(missing_ok=True)
        outputs = [str(dst)]
    count = sum(q["curves"] for q in infos)
    added = (result_solid.volume()/initial_volume-1.)*100.
    singular = sum(f["singular_faces"] for n in networks for f in n["families"])
    hits = sum(bool(n["cache_hit"]) for n in networks)
    surface_area = sum(q["surface_area"] for q in infos)
    coverage = 100.*sum(q["stripe_area"] for q in infos)/surface_area if surface_area else 0.
    body_note = ("body omitted; closed ribbons only" if stripes_only else
                 "simplified surface retained" if worked else "original surface retained")
    if repaired:
        body_note += "; input repaired by resampling"
    report = [src.name, f"  {count} stripe curves, {len(components)} surface component(s), {singular} singular faces",
              f"  field {p.field}, rotation {p.rotation:g}°, {p.families}; seed {p.seed}",
              f"  spacing {p.spacing:g}% h, width {p.band_width:g}% spacing, relief {p.relief:g}% h",
              f"  {body_note}; source units preserved",
              *([f"  working    simplified to {worked:,} faces to fit the Band face budget"] if worked else []),
              f"  skin coverage: {coverage:.1f}%" + (f"; ribbon thickness {p.relief:g}% h" if stripes_only else ""),
              f"  curve cache: {hits}/{len(networks)} hit(s); {sum(n['working_mesh']['solve_faces'] for n in networks)} field faces",
              f"  solid: {len(output.faces)} faces, " +
              (f"ribbon volume {output.volume:.4g} model units³" if stripes_only else f"volume {added:+.2f}%") +
              f", {time.perf_counter()-started:.1f} s",
              "  Method: Knöppel et al., Stripe Patterns on Surfaces (2015), Geometry Central curve extraction.",
              "  Draw with Plotter Vision: Features, decimation off; or Hatch Maker."]
    ctx.progress(1., "Saved" if outputs else "Previewed")
    # The shared clustering preview erases shallow rims. Keep normal results intact, and use quadric
    # simplification only for unusually large results so the 3D viewer still shows the bands.
    preview_mesh = output
    if len(output.faces) > 500000:
        pv, pf = gm.simplify_faces(output.vertices, output.faces, 500000)
        preview_mesh = trimesh.Trimesh(pv, pf, process=False)
    mesh_preview = gm.preview(preview_mesh, max_faces=500000)
    mesh_preview.faces_total = len(output.faces)
    volume_stat = (Stat("volume", "Ribbon volume", float(output.volume), "units³", fmt="{:.4g}") if stripes_only else
                   Stat("added", "Added volume", added, "%", fmt="{:+.2f}"))
    return Result(outputs=outputs, stats=[Stat("curves", "Curves", count),
                                          Stat("faces", "Faces", len(output.faces), before=faces_in),
                                          Stat("coverage", "Skin coverage", coverage, "%", fmt="{:.1f}"), volume_stat],
                  report="\n".join(report),
                  preview={"views": [{"title": "Output", "kind": "mesh", "mesh": mesh_preview}], "prefer": "Output"},
                  status="Saved" if outputs else ("Draft" if ctx.draft else "Previewed"),
                  data={"components": len(components), "curves": count, "cache_hits": hits,
                        "singular_faces": singular, "repaired": repaired, "frame": frame.frame_values(),
                        "stripes_only": stripes_only, "coverage_pct": coverage,
                        "backend": networks[0]["revision"], "working_meshes": [n["working_mesh"] for n in networks]})
