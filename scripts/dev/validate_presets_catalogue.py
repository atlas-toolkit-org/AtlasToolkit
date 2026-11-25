"""
The official presets on catalogue models (dev only, not shipped): does every preset finish, how fast, and does what it
makes look sound? For reviewing preset packs against the current tools.

    .venv\\Scripts\\python scripts\\dev\\validate_presets_catalogue.py --stage A --models david-head-light [--images]
        [--tools id,id] [--presets "Name,Name"] [--select all|essentials|flagged:<stage>[:<seconds>]]
        [--jobs 3] [--timeout 1800] [--keep-meshes] [--summary-only]

Models are build/catalogue/out/<id>.ply.gz (scripts/catalogue.py prepare). Every case (tool, preset, model) is a
process of its own with a time limit; it dies with its helpers (core/procs). Inputs:
    mesh and mesh-to-SVG tools   the model (Dual Fusion's second model and Shape Fusion's target: SECOND)
    SVG tools                    a Contour Lines "Classic" drawing of the model (Shape Cutter: its own sample)
    image tools                  the README gallery's photo / DEM (with --images; they do not depend on the model)
Each result says how long it took, the last phase reached, the tool's notes, and checks on what it wrote: a mesh's
health (geom/repair.diagnose_arrays: solid, pieces, holes, faces), a drawing's ink, strokes and points.

Results go to build/qa/presets_catalogue/<stage>.jsonl (appended as cases finish, so a run can stop and resume: cases
already there are skipped) and summary-<stage>.md.
"""
from __future__ import annotations

import argparse
import ctypes
import gzip
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
QA = ROOT / "build" / "qa" / "presets_catalogue"
os.environ.setdefault("ATLAS_TOOLKIT_HOME", str(QA / "home"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
CATALOGUE = ROOT / "build" / "catalogue" / "out"
SECOND = "venus-de-milo-light"
MESH_EXT = (".stl", ".obj", ".ply", ".off", ".glb", ".3mf")
SECOND_INPUT = {"mesh-dual": "second", "mesh-fusion": "target_file"}
IMAGES = {"dpt-hatching": "photo", "image-to-mesh": "photo", "raster-dem-to-mesh": "dem"}
SLOW_S = 300                       # a case slower than this is flagged for the large models
FACES_MAX = 5_000_000              # a mesh output above this is flagged (heavy to draw, to print, to open)
POINTS_MAX = 3_000_000             # a drawing above this is flagged (slow to open and plot)


# --- the case: one preset on one input, in a process of its own ---------------------------------------------------

def peak_memory_mb() -> float:
    if sys.platform != "win32":
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    c = PMC()
    c.cb = ctypes.sizeof(c)
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.PeakWorkingSetSize / 2**20


def mesh_health(path: Path) -> dict:
    from atlas_toolkit.geom import repair
    d = repair.diagnose(repair.load_raw(path))
    out = {"faces": int(d.faces), "solid": bool(d.solid), "pieces": int(d.pieces), "holes": int(d.holes),
           "nonmanifold": int(d.nonmanifold_edges), "problems": "; ".join(d.problems())[:300]}
    if not d.solid:            # the raw file, as any program reads it; and what Atlas makes of it (its loader cleans)
        import numpy as np

        from atlas_toolkit.geom import mesh as gm
        m = gm.load_mesh(path)
        a = repair.diagnose_arrays(np.asarray(m.vertices, float), np.asarray(m.faces, np.int64))
        out.update(loaded_solid=bool(a.solid), loaded_open=int(a.boundary_edges), loaded_nonmanifold=int(a.nonmanifold_edges),
                   loaded_problems="; ".join(a.problems())[:300])
    return out


def drawing_health(path: Path) -> dict:
    from atlas_toolkit.geom.svg import read_drawing
    st = read_drawing(path).stats()
    return {"ink_m": round(st.ink / 1000, 2), "strokes": st.strokes, "points": st.points, "pens": st.pens,
            "plot_h": round(st.seconds() / 3600, 2)}


def case(tool_id: str, name: str, src: str, out: str, extra: str, keep_meshes: bool) -> dict:
    from atlas_toolkit import devpaths
    from atlas_toolkit.core.io import output_path
    from atlas_toolkit.core.job import Job, execute
    from atlas_toolkit.core.registry import registry
    tool = registry().get(tool_id)
    overrides = json.loads(extra) if extra else {}
    values = dict(devpaths.preset_values(tool, name, **overrides))
    dst = output_path(tool, Path(src), values, "folder", out, "")
    box, phase = {}, ["starting"]
    lock = threading.Lock()

    def emit(kind, payload):
        if kind == "progress":
            with lock:
                phase[0] = f"{payload[0] * 100:.0f}% {payload[1]}"[:120]
        elif kind in ("done", "error", "cancelled"):
            box[kind] = payload
    t0 = time.perf_counter()
    execute(tool, Job(tool_id, "run", src, str(dst), values), emit, lambda: False)
    row = {"seconds": round(time.perf_counter() - t0, 1), "phase": phase[0], "peak_mb": round(peak_memory_mb())}
    if "error" in box:
        text = box["error"][0]
        diag = box["error"][2] if len(box["error"]) > 2 and isinstance(box["error"][2], dict) else {}
        row.update(status="needs_repair" if diag.get("suggest_tool") == "mesh-repair" else "error",
                   message=" ".join(text.split())[:400], phase=diag.get("phase", phase[0]))
        if box["error"][1]:
            row["traceback"] = box["error"][1].strip().splitlines()[-1][:300]
        return row
    res = box["done"][0]
    row.update(status="ok", notes=[" ".join(n.split())[:200] for n in (res.notes or [])][:6],
               stats={s.key: (s.value if isinstance(s.value, (int, float, str)) else str(s.value))
                      for s in (res.stats or [])})
    outs = [Path(o) for o in (res.outputs or []) if Path(o).is_file()]
    row["outputs"] = len(outs)
    checks = []
    for o in outs[:12]:
        try:
            if o.suffix.lower() == ".svg":
                checks.append({"file": o.name, **drawing_health(o)})
            elif o.suffix.lower() in MESH_EXT:
                checks.append({"file": o.name, "mb": round(o.stat().st_size / 2**20, 1), **mesh_health(o)})
        except Exception as e:      # a check that fails is itself a finding
            checks.append({"file": o.name, "check_error": f"{type(e).__name__}: {e}"[:200]})
    row["checks"] = checks
    if not keep_meshes:
        for o in outs:
            if o.suffix.lower() in MESH_EXT:
                o.unlink(missing_ok=True)
    return row


# --- the driver ----------------------------------------------------------------------------------------------------

def unpack(model: str) -> Path:
    folder = QA / "models"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{model}.ply"
    if not path.is_file():
        gz = CATALOGUE / f"{model}.ply.gz"
        if not gz.is_file():
            raise SystemExit(f"No catalogue model {model}: run scripts/catalogue.py prepare.")
        path.write_bytes(gzip.decompress(gz.read_bytes()))
    return path


def model_health(model: str) -> dict:
    cache = QA / "models" / f"{model}.health.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    h = mesh_health(unpack(model))
    stats = CATALOGUE / f"{model}.json"
    if stats.is_file():
        h["thin"] = json.loads(stats.read_text(encoding="utf-8")).get("thin")
    cache.write_text(json.dumps(h), encoding="utf-8")
    return h


def svg_input(model: str) -> Path:
    """A Contour Lines drawing of the model, made once: what the SVG tools work on."""
    folder = QA / "inputs" / f"{model}-contours"
    made = sorted(folder.glob("*.svg")) if folder.exists() else []
    if made:
        return made[0]
    import readme_gallery as gallery
    gallery.run_tool("contour-lines", unpack(model), folder, "Classic")
    return sorted(folder.glob("*.svg"))[0]


def image_input(kind: str) -> Path:
    import readme_gallery as gallery
    return gallery.sample(gallery.PHOTO if kind == "photo" else gallery.DEM)


def plan(a, reg) -> list[dict]:
    from atlas_toolkit import devpaths
    from atlas_toolkit.core import runtimes
    wanted_tools = {t for t in a.tools.split(",") if t}
    wanted_presets = {p.strip() for p in a.presets.split(",") if p.strip()}
    flagged = None
    if a.select.startswith("flagged:"):
        parts = a.select.split(":")
        slow = float(parts[2]) if len(parts) > 2 else SLOW_S     # "not solid" alone is a note on the tool, not a rerun
        flagged = {(r["tool"], r["preset"]) for r in load_rows(parts[1])
                   if set(flags(r, slow)) - {"not solid", "raw not solid"}}
    presets = devpaths._dev_presets()
    models = [m for m in a.models.split(",") if m]
    cases = []
    for tool in reg.ordered():
        if wanted_tools and tool.id not in wanted_tools or tool.id not in presets:
            continue
        if tool.category in ("image", "image2mesh"):
            if not a.images:
                continue
            inputs = [IMAGES.get(tool.id, "photo")]
        else:
            inputs = models
        missing = runtimes.missing(tool, tool.defaults())
        for name, p in presets[tool.id].items():
            if wanted_presets and name not in wanted_presets:
                continue
            if a.select == "essentials" and "essentials" not in p.tags:
                continue
            if flagged is not None and (tool.id, name) not in flagged and "essentials" not in p.tags:
                continue
            for m in inputs:
                cases.append({"tool": tool.id, "preset": name, "model": m, "category": tool.category,
                              "essentials": "essentials" in p.tags,
                              "skip": f"needs {', '.join(r.label for r in missing)}" if missing else ""})
    return cases


def load_rows(stage: str) -> list[dict]:
    path = QA / f"{stage}.jsonl"
    if not path.is_file():
        return []
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[(r["tool"], r["preset"], r["model"])] = r      # a rerun replaces the earlier row
    return list(rows.values())


def flags(r: dict, slow: float = SLOW_S) -> list[str]:
    out = []
    if r.get("status") != "ok":
        out.append(r.get("status", "?"))
        return out
    if r.get("seconds", 0) > slow:
        out.append(f"slow {r['seconds'] / 60:.0f} min")
    if not r.get("outputs"):
        out.append("no output")
    for c in r.get("checks", []):
        if "check_error" in c:
            out.append("unreadable output")
        if "faces" in c:
            if c["faces"] == 0:
                out.append("empty mesh")
            elif c["faces"] > FACES_MAX:
                out.append(f"{c['faces'] / 1e6:.1f}M faces")
            if not c.get("solid") and r.get("input_solid") and r.get("category") == "mesh":
                out.append("raw not solid" if c.get("loaded_solid") else "not solid")
        if "strokes" in c:
            if c["strokes"] == 0:
                out.append("empty drawing")
            elif c["points"] > POINTS_MAX:
                out.append(f"{c['points'] / 1e6:.1f}M points")
    return sorted(set(out))


def is_flagged(r: dict, slow: float = SLOW_S) -> bool:
    return bool(flags(r, slow))


def run_case(c: dict, a, lock) -> dict:
    from atlas_toolkit.core import procs
    out = QA / "runs" / a.stage / c["tool"] / c["model"] / slugify(c["preset"])
    out.mkdir(parents=True, exist_ok=True)
    row = dict(c)
    if c["skip"]:
        row.update(status="skipped", message=c["skip"], seconds=0)
    else:
        try:
            if c["category"] == "svg":
                if c["tool"] == "svg-shape-cutter":
                    from atlas_toolkit.tools.svg_shape_cutter.engine import make_sample
                    (QA / "inputs").mkdir(parents=True, exist_ok=True)
                    src = make_sample(QA / "inputs")
                else:
                    with lock:
                        src = svg_input(c["model"])
            elif c["category"] in ("image", "image2mesh"):
                src = image_input(c["model"])
            else:
                src = unpack(c["model"])
                row["input_solid"] = model_health(c["model"])["solid"]
            extra = {SECOND_INPUT[c["tool"]]: str(unpack(SECOND))} if c["tool"] in SECOND_INPUT else {}
            env = procs.child_env(ATLAS_TOOLKIT_HOME=str(QA / "home"), QT_QPA_PLATFORM="offscreen")
            t0 = time.perf_counter()
            proc = procs.spawn([sys.executable, __file__, "--case", c["tool"], c["preset"], str(src), str(out),
                                json.dumps(extra), "1" if a.keep_meshes else "0"], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace")
            try:
                stdout, stderr = proc.communicate(timeout=a.timeout)
                line = next((x for x in stdout.splitlines() if x.startswith("RESULT ")), None)
                if line:
                    row.update(json.loads(line[7:]))
                else:
                    tail = [x for x in (stderr or stdout).strip().splitlines() if x.strip()]
                    row.update(status="crash", seconds=round(time.perf_counter() - t0, 1),
                               message=(tail[-1] if tail else f"exit {proc.returncode}")[:300])
            except subprocess.TimeoutExpired:
                row.update(status="timeout", seconds=a.timeout, message=f"over {a.timeout / 60:.0f} min")
                (out / "timeout.txt").write_text("", encoding="utf-8")
            finally:
                procs.finish(proc)
        except Exception as e:
            row.update(status="crash", seconds=0, message=f"harness: {type(e).__name__}: {e}"[:300])
    row["flags"] = flags(row)
    row["when"] = time.strftime("%Y-%m-%d %H:%M")
    return row


def slugify(name: str) -> str:
    from atlas_toolkit.core.presets import slug
    return slug(name) or "preset"


def summary(stage: str) -> str:
    rows = sorted(load_rows(stage), key=lambda r: (r["tool"], r["preset"], r["model"]))
    if not rows:
        return f"No results for stage {stage}."
    done = [r for r in rows if r.get("status") != "skipped"]
    models = sorted({r["model"] for r in rows})
    lines = [f"# Presets on catalogue models: stage {stage}", "",
             f"Models: {', '.join(models)}. {len(rows)} cases, {sum(r.get('status') == 'ok' for r in done)} "
             f"finished, {sum(bool(r.get('flags')) for r in done)} flagged, "
             f"{sum(r.get('status') == 'skipped' for r in rows)} skipped.", "",
             "| Tool | Cases | OK | Median s | Slowest | Flagged |", "|---|---|---|---|---|---|"]
    for tool in sorted({r["tool"] for r in rows}):
        rs = [r for r in done if r["tool"] == tool]
        if not rs:
            continue
        secs = [r.get("seconds", 0) for r in rs]
        slowest = max(rs, key=lambda r: r.get("seconds", 0))
        lines.append(f"| {tool} | {len(rs)} | {sum(r.get('status') == 'ok' for r in rs)} | "
                     f"{statistics.median(secs):.0f} | {slowest.get('seconds', 0):.0f} s ({slowest['preset']}) | "
                     f"{sum(bool(r.get('flags')) for r in rs)} |")
    bad = [r for r in done if r.get("flags")]
    if bad:
        lines += ["", "## Flagged", "", "| Tool | Preset | Model | s | Flags | Phase / message |", "|---|---|---|---|---|---|"]
        for r in sorted(bad, key=lambda r: -r.get("seconds", 0)):
            msg = r.get("message") or "; ".join(r.get("notes", []))[:160]
            where = r.get("phase", "") if r.get("status") in ("timeout", "crash") else msg
            lines.append(f"| {r['tool']} | {r['preset']} | {r['model']} | {r.get('seconds', 0):.0f} | "
                         f"{', '.join(r['flags'])} | {str(where).replace('|', '/')[:200]} |")
    slow = sorted(done, key=lambda r: -r.get("seconds", 0))[:25]
    lines += ["", "## Slowest 25", "", "| Tool | Preset | Model | s | Peak MB |", "|---|---|---|---|---|"]
    lines += [f"| {r['tool']} | {r['preset']} | {r['model']} | {r.get('seconds', 0):.0f} | {r.get('peak_mb', '')} |"
              for r in slow]
    skipped = [r for r in rows if r.get("status") == "skipped"]
    if skipped:
        lines += ["", "## Skipped", ""] + sorted({f"- {r['tool']}: {r.get('message', '')}" for r in skipped})
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--case":
        _, _, tool, name, src, out, extra, keep = sys.argv
        print("RESULT " + json.dumps(case(tool, name, src, out, extra, keep == "1"), default=str), flush=True)
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stage", required=True, help="a name for this batch: its results file")
    ap.add_argument("--models", default="david-head-light")
    ap.add_argument("--images", action="store_true", help="also the image tools (on their own sample inputs)")
    ap.add_argument("--tools", default="")
    ap.add_argument("--presets", default="")
    ap.add_argument("--select", default="all", help="all | essentials | flagged:<stage>[:<seconds>] (+ essentials; \"not solid\" alone does not count)")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--keep-meshes", action="store_true")
    ap.add_argument("--rerun", action="store_true", help="run cases that already have a result again")
    ap.add_argument("--summary-only", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    QA.mkdir(parents=True, exist_ok=True)
    if not a.summary_only:
        from atlas_toolkit import devpaths
        from atlas_toolkit.core.registry import registry
        if not devpaths.preset_packs():
            raise SystemExit(devpaths.NO_PRESETS)
        cases = plan(a, registry())
        done = {(r["tool"], r["preset"], r["model"]) for r in load_rows(a.stage)}
        todo = [c for c in cases if a.rerun or (c["tool"], c["preset"], c["model"]) not in done]
        for m in {c["model"] for c in todo if c["category"] in ("mesh", "mesh2svg", "svg")}:
            unpack(m)
        print(f"{len(cases)} cases, {len(todo)} to run, {a.jobs} at a time, limit {a.timeout} s", flush=True)
        lock = threading.Lock()
        results = QA / f"{a.stage}.jsonl"
        # the slowest kinds first, so the long ones overlap with the many quick ones
        order = {"mesh": 0, "mesh2svg": 1, "image2mesh": 2, "image": 3, "svg": 4}
        todo.sort(key=lambda c: (order.get(c["category"], 5), c["tool"], c["preset"]))
        with ThreadPoolExecutor(a.jobs) as pool:
            futs = [pool.submit(run_case, c, a, lock) for c in todo]
            for n, f in enumerate(as_completed(futs), 1):
                r = f.result()
                with lock, results.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(r, default=str) + "\n")
                print(f"[{n}/{len(todo)}] {r['status']:12s} {r.get('seconds', 0):7.1f}s {r['tool']:20s} "
                      f"{r['preset'][:28]:28s} {r['model'][:22]:22s} {', '.join(r['flags'])[:60]}", flush=True)
    text = summary(a.stage)
    (QA / f"summary-{a.stage}.md").write_text(text, encoding="utf-8")
    print("\n" + text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
