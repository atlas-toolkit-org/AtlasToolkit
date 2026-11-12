"""Toolkit adapter for mesh shatter."""
from __future__ import annotations

from pathlib import Path

from atlas_toolkit.core import Stat
from atlas_toolkit.geom import mesh as gm

from . import TOOL
from . import mesh_shatter as ms


def run(ctx, src: Path, dst: Path | None, p):
    import trimesh
    m = gm.load_mesh(src)
    joined = None
    wanted = p.connectors and p.connector_percent > 0
    if wanted:
        from . import connectors as hub
        hub.validate(p)
    shards = ms.shatter(m, p.num_pieces, p.ejection, p.edge_sensitivity, p.jitter, p.perturb, p.seed, ctx.progress)
    if wanted:
        joined = hub.join(m, shards, p, ctx)
    combined = joined.combined if joined else trimesh.util.concatenate(shards)
    extra = []
    if dst is not None and p.separate:
        folder = dst.with_name(f"{src.stem}_shards_{len(shards)}pcs")
        folder.mkdir(parents=True, exist_ok=True)
        pieces = joined.parts if joined else shards
        for i, s in enumerate(pieces):
            ctx.progress(0.85 + 0.1 * i / len(pieces), "saving shards")
            extra.append(str(gm.save_mesh(s, folder / f"{src.stem}_piece_{i:03d}{dst.suffix}", TOOL)))
        if joined:
            extra.append(str(gm.save_mesh(joined.network, folder / f"{src.stem}_connectors{dst.suffix}", TOOL)))
    if len(shards) != p.num_pieces:
        ctx.log(f"{len(shards)} shards (asked for {p.num_pieces})")
    report = [src.name,
              f"  shards     {len(shards)}, ejection {p.ejection:g} × diagonal, jitter {p.jitter:g}, "
              f"perturbation {p.perturb:g}, seed {p.seed}"]
    stats = [Stat("shards", "Shards", len(shards))]
    if joined:
        layout = p.connector_network if p.connector_style == "organic" else "radial"
        report += [f"  connected  {len(joined.connected)} / {len(shards)} shards ({p.connector_percent:g}% asked, "
                   f"{p.connector_selection}); the others float free",
                   f"  connectors {p.connector_style}, {layout}: {joined.limbs} limbs, "
                   f"{joined.branches} / {joined.requested} extra branches",
                   f"  hub        {p.hub_core} at {joined.hub[0]:.4g}, {joined.hub[1]:.4g}, {joined.hub[2]:.4g} "
                   f"({p.hub_position}), radius {joined.hub_radius:.4g}",
                   ("  shards are closed solids; connected ones are fused to the hub" if joined.solid
                    else "  shards are open surfaces; connectors touch them and are not fused")]
        report += [f"  note       {note}" for note in joined.notes]
        stats.append(Stat("connected", "Connected shards", len(joined.connected)))
    outcome = gm.result(ctx, TOOL, m, combined, dst, stats, report, extra)
    if joined:
        outcome.preview["views"].append({"title": "Connectors", "kind": "mesh", "mesh": gm.preview(joined.network)})
    return outcome
