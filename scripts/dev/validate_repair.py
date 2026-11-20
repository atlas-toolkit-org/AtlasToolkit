"""
Repair pass rates (dev only, not shipped): which catalogue models the solid-mesh tools take with their default
settings, as the models are, and after Repair Mesh.

    .venv\\Scripts\\python scripts\\dev\\validate_repair.py [--models id,id] [--max-faces 120000] [--tools a,b]
                                                        [--mode run|draft] [--jobs 4] [--all]

Models come from build/catalogue/out (scripts/catalogue.py prepare). Only models that are not clean solids are
tried unless --all. For each model and tool there are two runs:
- "as is":   the model as the catalogue ships it (the tool's own safe tidy applies);
- "repaired": after Repair Mesh with its default settings.
Writes build/qa/repair/results.csv and summary.md, and prints the summary.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
# settings and caches of the runs stay in build/qa, never in the real profile (workers inherit it)
os.environ.setdefault("ATLAS_TOOLKIT_HOME", str(ROOT / "build" / "qa" / "repair" / "home"))
from atlas_toolkit.devpaths import qa_dir  # noqa: E402

CATALOGUE_OUT = ROOT / "build" / "catalogue" / "out"
TOOLS = ("mesh-motion", "mesh-crumble", "mesh-developable", "pipe-fill", "mesh-stairs", "mesh-surface-eject",
         "mesh-stripes")


def load_gz(path: Path):
    import trimesh
    from atlas_toolkit.geom import mesh as gm
    m = trimesh.load(io.BytesIO(gzip.open(path).read()), file_type="ply", process=False)
    return gm.clean(m)


def run_tool(tool_id: str, src: str, out_dir: str, mode: str) -> tuple[str, str, float]:
    """Run one tool with its defaults; returns (status, message, seconds). status: ok | needs-repair | error."""
    from atlas_toolkit.core.job import Job, execute
    from atlas_toolkit.core.registry import registry
    tool = registry().get(tool_id)
    dst = Path(out_dir) / tool.output.name_for(Path(src), dict(tool.defaults()))
    box = {}
    t0 = time.perf_counter()
    job = Job(tool_id, mode, src, str(dst) if mode == "run" else None, dict(tool.defaults()),
              cache_dir=str(Path(out_dir) / "cache"))
    try:
        execute(tool, job, lambda kind, payload: box.__setitem__(kind, payload) if kind in ("done", "error") else None,
                lambda: False)
    except Exception as e:          # pragma: no cover
        return "error", f"{type(e).__name__}: {e} {traceback.format_exc()[-300:]}", time.perf_counter() - t0
    secs = time.perf_counter() - t0
    if "done" in box:
        return "ok", "", secs
    err = box.get("error", ("no result",))
    diag = err[2] if len(err) > 2 and isinstance(err[2], dict) else {}
    return ("needs-repair" if diag.get("suggest_tool") else "error"), err[0].splitlines()[0][:240], secs


def repair_model(src: Path, dst: Path) -> tuple[bool, list, bool]:
    from atlas_toolkit.core.registry import registry
    from atlas_toolkit.geom import mesh as gm
    from atlas_toolkit.geom import repair as R
    m = load_gz(src) if src.suffix == ".gz" else gm.load_mesh(src)
    out, notes, rebuilt = R.repair(m)
    gm.save_mesh(out, dst, registry().get("mesh-repair") if "mesh-repair" in registry().tools else
                 registry().get("plane-cut"))
    return R.diagnose(out).solid, notes, rebuilt


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--models", default="")
    ap.add_argument("--tools", default=",".join(TOOLS))
    ap.add_argument("--max-faces", type=int, default=120_000)
    ap.add_argument("--mode", default="run", choices=("run", "draft"))
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--all", action="store_true", help="also models that are already clean solids")
    ap.add_argument("--skip-as-is", action="store_true")
    ap.add_argument("--inputs", default="", help="a folder of <id>.ply to use instead of build/catalogue/out "
                    "(e.g. the revision-1 copies an earlier run left in build/qa/repair/models)")
    a = ap.parse_args()

    from atlas_toolkit.geom import repair as R
    out = qa_dir("repair")
    models_dir = out / ("models" if not a.inputs else "models_run")
    models_dir.mkdir(exist_ok=True)
    wanted = {m for m in a.models.split(",") if m}
    tools = [t for t in a.tools.split(",") if t]
    picked = []
    sources = (sorted(p for p in Path(a.inputs).glob("*.ply") if not p.stem.endswith("_repaired")) if a.inputs
               else sorted(CATALOGUE_OUT.glob("*.ply.gz")))
    for gz in sources:
        mid = gz.name[:-7] if gz.suffix == ".gz" else gz.stem
        if wanted and mid not in wanted:
            continue
        if gz.suffix == ".gz":
            m = load_gz(gz)
        else:
            from atlas_toolkit.geom import mesh as gm
            m = gm.clean(R.load_raw(gz))
        if len(m.faces) > a.max_faces and not wanted:
            continue
        d = R.diagnose(m)
        if d.solid and not a.all:
            continue
        plain = models_dir / f"{mid}.ply"
        m.export(plain)
        fixed = models_dir / f"{mid}_repaired.ply"
        t0 = time.perf_counter()
        solid, notes, rebuilt = repair_model(plain, fixed)
        print(f"{mid}: {d.summary()[:90]}  ->  {'solid' if solid else 'NOT SOLID'}"
              f"{' (rebuilt)' if rebuilt else ''} in {time.perf_counter() - t0:.1f}s", flush=True)
        picked.append((mid, plain, fixed, d.summary(), solid, rebuilt))
    print(f"\n{len(picked)} models × {len(tools)} tools, mode {a.mode}\n", flush=True)

    rows = []
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        futs = {}
        for mid, plain, fixed, summary, solid, rebuilt in picked:
            for t in tools:
                runs = [("repaired", fixed)] if a.skip_as_is else [("as is", plain), ("repaired", fixed)]
                for label, path in runs:
                    od = out / "runs" / t / label.replace(" ", "_")
                    od.mkdir(parents=True, exist_ok=True)
                    futs[pool.submit(run_tool, t, str(path), str(od), a.mode)] = (mid, t, label, summary, rebuilt)
        for f in as_completed(futs):
            mid, t, label, summary, rebuilt = futs[f]
            try:
                status, msg, secs = f.result()
            except Exception as e:     # pragma: no cover (a crashed worker)
                status, msg, secs = "error", f"worker: {e}", 0.0
            rows.append({"model": mid, "tool": t, "input": label, "status": status, "seconds": round(secs, 1),
                         "rebuilt": rebuilt, "message": msg, "defects": summary})
            print(f"  {status:<12} {t:<20} {label:<9} {mid}  {msg[:110]}", flush=True)

    rows.sort(key=lambda r: (r["tool"], r["model"], r["input"]))
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["model"])
        w.writeheader()
        w.writerows(rows)
    lines = ["# Repair pass rates", "", f"{len(picked)} catalogue models that are not clean solids, tools at their "
             f"defaults ({a.mode}).", "", "| Tool | As is | After Repair Mesh |", "|---|---|---|"]
    for t in tools:
        def rate(label):
            rs = [r for r in rows if r["tool"] == t and r["input"] == label]
            ok = sum(r["status"] == "ok" for r in rs)
            return f"{ok}/{len(rs)} ({100 * ok / len(rs):.0f} %)" if rs else "—"
        lines.append(f"| {t} | {rate('as is')} | {rate('repaired')} |")
    fails = [r for r in rows if r["status"] != "ok" and r["input"] == "repaired"]
    if fails:
        lines += ["", "## Still failing after Repair Mesh", ""]
        lines += [f"- {r['tool']} · {r['model']}: {r['message']}" for r in fails]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\n{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
