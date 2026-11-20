"""
Model profiles against tool results (dev only, not shipped): what the models a tool takes have in common, and
what the ones it refuses have.

    .venv\\Scripts\\python scripts\\dev\\profile_models.py [--results build/qa/repair/results.csv ...]
                                                       [--models DIR ...]

For every model in the given folders (<id>.ply, and <id>_repaired.ply when present) it measures the properties that
decide whether a solid tool can work on it: closed and consistently wound, holes, non-manifold edges and bow-ties,
pieces, needle and tiny triangles, thin walls, self-intersections (sampled), size and density. It joins them with
validate_repair.py's results (tool, model, input, status) and writes build/qa/profile/profiles.csv, joined.csv and
report.md: per tool, the median of each property over the models it took and over those it refused.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from atlas_toolkit.devpaths import qa_dir  # noqa: E402


def self_intersections(V, F, samples: int = 4000, seed: int = 1) -> float:
    """Share of sampled faces that a ray along one of their own edges crosses another face before the edge ends:
    an estimate of how much of the surface passes through itself (folds left by decimation, overlapping scans)."""
    import trimesh
    m = trimesh.Trimesh(V, F, process=False)
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(F), size=min(samples, len(F)), replace=False)
    a, b = V[F[pick, 0]], V[F[pick, 1]]
    d = b - a
    ln = np.linalg.norm(d, axis=1)
    ok = ln > 0
    a, d, ln, pick = a[ok], d[ok] / ln[ok, None], ln[ok], pick[ok]
    start = a + d * (ln[:, None] * 1e-3)
    try:
        tri, rays, locs = m.ray.intersects_id(start, d, multiple_hits=True, return_locations=True)
    except Exception:            # pragma: no cover
        return float("nan")
    if not len(tri):
        return 0.0
    tri, rays = np.asarray(tri), np.asarray(rays)
    own = F[pick[rays]]
    touches = (F[tri][:, :, None] == own[:, None, :]).any(axis=(1, 2))    # shares a vertex with the ray's own face
    dist = np.linalg.norm(np.asarray(locs) - start[rays], axis=1)
    crossing = ~touches & (dist < ln[rays] * 0.998)                        # another face, before the edge ends
    return float(len(np.unique(rays[crossing])) / max(len(pick), 1))


def profile(path: Path) -> dict:
    from atlas_toolkit.geom import mesh as gm
    from atlas_toolkit.geom import repair as R
    m = gm.load_mesh(path)                       # as every tool loads it
    V, F = np.asarray(m.vertices, float), np.asarray(m.faces, np.int64)
    d = R.diagnose_arrays(V, F)
    ext = np.ptp(V, axis=0)
    size = float(ext.max()) or 1.0
    e = np.linalg.norm(V[F[:, [1, 2, 0]]] - V[F], axis=2)
    area = np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1) / 2
    q = 4 * np.sqrt(3) * area / np.maximum(e.max(1), 1e-30) ** 2          # 1 equilateral, 0 needle
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import catalogue as cat
        thin = cat.thinness(V, F)
    except Exception:            # pragma: no cover
        thin = float("nan")
    return {
        "faces": len(F), "solid": int(d.solid), "watertight_wound": int(d.boundary_edges == 0 and d.flipped_edges == 0
                                                                         and d.nonmanifold_edges == 0),
        "holes": d.holes, "open_edges": d.boundary_edges, "largest_hole": d.largest_hole,
        "nonmanifold": d.nonmanifold_edges, "bowties": d.bowtie_vertices, "flipped": d.flipped_edges,
        "pieces": d.pieces, "crumbs": d.crumbs, "sheets": d.sheets,
        "needles_q1e-3": int((q < 1e-3).sum()), "needles_q1e-2": int((q < 1e-2).sum()),
        "min_edge_rel": float(e.min() / size), "median_edge_rel": float(np.median(e) / size),
        "min_area_rel": float(area.min() / size ** 2),
        "thin_share": round(float(thin), 3),
        "self_cross": round(self_intersections(V, F), 4),
        "volume_fill": round(abs(float(d.volume)) / float(np.prod(ext)) if np.prod(ext) > 0 else 0.0, 3),
        "aspect": round(float(ext.max() / max(ext.min(), 1e-12)), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--results", nargs="*", default=[str(ROOT / "build" / "qa" / "repair" / "results.csv")])
    ap.add_argument("--models", nargs="*", default=[str(ROOT / "build" / "qa" / "repair" / "models_run")])
    a = ap.parse_args()
    out = qa_dir("profile")
    profiles = {}
    for folder in a.models:
        for p in sorted(Path(folder).glob("*.ply")):
            label = "repaired" if p.stem.endswith("_repaired") else "as is"
            mid = p.stem[:-9] if label == "repaired" else p.stem
            if (mid, label) in profiles:
                continue
            profiles[(mid, label)] = profile(p)
            print(f"  {mid:28s} {label:9s} " + " ".join(f"{k}={v}" for k, v in profiles[(mid, label)].items()
                                                         if k in ("solid", "needles_q1e-3", "thin_share", "self_cross")),
                  flush=True)
    keys = list(next(iter(profiles.values())))
    with (out / "profiles.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["model", "input", *keys])
        for (mid, label), pr in sorted(profiles.items()):
            w.writerow([mid, label, *[pr[k] for k in keys]])
    rows = []
    for f in a.results:
        with open(f, encoding="utf-8") as fh:
            rows += list(csv.DictReader(fh))
    joined = []
    for r in rows:
        pr = profiles.get((r["model"], r["input"]))
        if pr:
            joined.append({**r, **pr})
    with (out / "joined.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(joined[0]))
        w.writeheader()
        w.writerows(joined)
    lines = ["# What the models a tool takes have, and what the refused ones have", "",
             "Medians per tool: models the tool ran on (ok) against those it refused (fail).", ""]
    show = ["solid", "open_edges", "nonmanifold", "pieces", "needles_q1e-3", "min_edge_rel", "thin_share",
            "self_cross", "volume_fill", "faces"]
    for tool in sorted({r["tool"] for r in joined}):
        rs = [r for r in joined if r["tool"] == tool]
        ok = [r for r in rs if r["status"] == "ok"]
        bad = [r for r in rs if r["status"] != "ok"]
        lines += [f"## {tool}: {len(ok)} ok, {len(bad)} failed", "", "| property | ok | failed |", "|---|---|---|"]
        for k in show:
            def med(group):
                vals = [float(r[k]) for r in group if r[k] not in ("", "nan")]
                return f"{statistics.median(vals):.4g}" if vals else "—"
            lines.append(f"| {k} | {med(ok)} | {med(bad)} |")
        reasons = {}
        for r in bad:
            reasons.setdefault(r["message"][:110], []).append(f"{r['model']} ({r['input']})")
        if reasons:
            lines += ["", "Failures:", ""] + [f"- {m}: {', '.join(v)}" for m, v in reasons.items()]
        lines.append("")
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
