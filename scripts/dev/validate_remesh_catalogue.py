"""
Remesh/Decimate across the model catalogue (dev only, not shipped): does every method finish, how fast, and how near
its target?

    .venv\\Scripts\\python scripts\\dev\\validate_remesh_catalogue.py [--models id,id] [--methods decimate,uniform,adaptive]
                                                                 [--max-faces 300000] [--jobs 4] [--timeout 600]

Models are build/catalogue/out/<id>.ply.gz (scripts/catalogue.py prepare). Each run is a process of its own with a
time limit (its helper dies with it). Settings: the tool's defaults, except
- decimate: as is (20 000 faces);
- uniform and adaptive: the model's own face count, Surface-error tolerance --tolerance (3% by default: what these
  methods can hold on a scan at the same density; the 0.1% default stops at once with advice, by design).
Writes build/qa/remesh_catalogue/results.csv and summary.md.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("ATLAS_TOOLKIT_HOME", str(ROOT / "build" / "qa" / "remesh_catalogue" / "home"))
OUT_DIR = ROOT / "build" / "catalogue" / "out"


def case(src: str, method: str, tolerance: float) -> dict:
    """One run in this process: the engine as the app runs it (a dry run, nothing written)."""
    import trimesh
    from atlas_toolkit.core.job import Job, execute
    from atlas_toolkit.core.registry import registry
    tool = registry().get("remesh-decimate")
    faces = len(trimesh.load(src, process=False).faces)
    values = {"method": method}
    if method != "decimate":
        values.update(density="faces", target_faces=faces, surface_error=tolerance)
    values = dict(tool.values(values))
    box, logs = {}, []

    def emit(kind, payload):
        if kind == "log":
            logs.append(payload[1])
        elif kind in ("done", "error"):
            box[kind] = payload
    t0 = time.perf_counter()
    execute(tool, Job("remesh-decimate", "analyse", src, None, values), emit, lambda: False)
    row = {"seconds": round(time.perf_counter() - t0, 1), "faces_in": faces, "target": values["target_faces"]}
    if "done" in box:
        d = box["done"][0].data
        row.update(status="ok", faces_out=d.get("polygons"), attempts=d.get("density_attempts"),
                   error_pct=round(float(d.get("sample_max_percent", 0)), 3))
    else:
        row.update(status="error", message=box.get("error", ("?",))[0].splitlines()[0][:200])
    row["noise_dropped"] = any("scan noise" in m for m in logs)
    return row


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--case":
        print("RESULT " + json.dumps(case(sys.argv[2], sys.argv[3], float(sys.argv[4]))), flush=True)
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--models", default="")
    ap.add_argument("--methods", default="decimate,uniform,adaptive")
    ap.add_argument("--max-faces", type=int, default=300_000, help="uniform/adaptive only on models up to this size")
    ap.add_argument("--tolerance", type=float, default=3.0)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args()
    from atlas_toolkit.devpaths import qa_dir
    out = qa_dir("remesh_catalogue")
    plain = out / "models"
    plain.mkdir(exist_ok=True)
    wanted = {m for m in a.models.split(",") if m}
    jobs = []
    import trimesh
    for gz in sorted(OUT_DIR.glob("*.ply.gz")):
        mid = gz.name[:-7]
        if wanted and mid not in wanted:
            continue
        path = plain / f"{mid}.ply"
        if not path.is_file():
            path.write_bytes(gzip.decompress(gz.read_bytes()))
        faces = len(trimesh.load(io.BytesIO(path.read_bytes()), file_type="ply", process=False).faces)
        for method in a.methods.split(","):
            if method != "decimate" and faces > a.max_faces:
                continue
            jobs.append((mid, method, path))
    print(f"{len(jobs)} runs", flush=True)

    def run(mid, method, path):
        t0 = time.perf_counter()
        try:
            r = subprocess.run([sys.executable, __file__, "--case", str(path), method, str(a.tolerance)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=a.timeout)
            line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT ")), None)
            row = json.loads(line[7:]) if line else {"status": "crash", "message": (r.stderr or r.stdout)[-200:]}
        except subprocess.TimeoutExpired:
            row = {"status": "timeout", "seconds": a.timeout, "message": f"over {a.timeout} s"}
        row.setdefault("seconds", round(time.perf_counter() - t0, 1))
        return {"model": mid, "method": method, **row}

    rows = []
    with ThreadPoolExecutor(a.jobs) as pool:
        futs = [pool.submit(run, *j) for j in jobs]
        for f in as_completed(futs):
            r = f.result()
            rows.append(r)
            ratio = f"{r['faces_out'] / r['target']:.2f}x" if r.get("faces_out") and r.get("target") else ""
            print(f"  {r['status']:8s} {r['method']:9s} {r['seconds']:7.1f}s {ratio:7s} {r['model']:28s} "
                  f"{'noise' if r.get('noise_dropped') else '':6s}{r.get('message', '')[:90]}", flush=True)
    rows.sort(key=lambda r: (r["method"], r["model"]))
    keys = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("model", "method", "status", "seconds"), k))
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    lines = ["# Remesh/Decimate across the catalogue", "",
             f"Tolerance for uniform and adaptive: {a.tolerance}%; time limit {a.timeout} s per run.", "",
             "| Method | Runs | Finished | Median s | Slowest s | Faces / target (median, max) | Noise set aside |",
             "|---|---|---|---|---|---|---|"]
    for method in a.methods.split(","):
        rs = [r for r in rows if r["method"] == method]
        if not rs:
            continue
        ok = [r for r in rs if r["status"] == "ok"]
        secs = [r["seconds"] for r in rs]
        ratios = [r["faces_out"] / r["target"] for r in ok if r.get("faces_out") and r.get("target")]
        lines.append(f"| {method} | {len(rs)} | {len(ok)} ({100 * len(ok) / len(rs):.0f} %) | "
                     f"{statistics.median(secs):.1f} | {max(secs):.1f} | "
                     + (f"{statistics.median(ratios):.2f}, {max(ratios):.2f}" if ratios else "—")
                     + f" | {sum(bool(r.get('noise_dropped')) for r in rs)} |")
    bad = [r for r in rows if r["status"] != "ok"]
    if bad:
        lines += ["", "## Not finished", ""] + [f"- {r['method']} · {r['model']}: {r['status']} "
                                               f"{r.get('message', '')}" for r in bad]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
