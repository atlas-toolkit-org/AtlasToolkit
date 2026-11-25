"""How fast is Shape Crop on big meshes, and is every result sound?

Runs the real tool (inspection, as when a file is added, then a saved run) on generated models of growing size and
on any models you pass:

- closed: an icosphere, a closed solid (the boolean way);
- open: the same with its base cut off and a little noise, like an open scan (the surface clip, or Repair Mesh
  for union and symmetric difference).

For every shape and operation it records the time of each stage (load, solid check, crop, save, preview), the faces
out, and whether the result is what it should be (closed solids watertight, open clips on the shape).

    .venv\\Scripts\\python scripts\\dev\\validate_mesh_crop.py [--sizes 80k,330k,1.3m,5m] [--model FILE ...] [--quick]

Writes build/qa/mesh_crop/timings.csv and timings.md (and the models it generates, under models/).
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from atlas_toolkit.devpaths import qa_dir  # noqa: E402

SUBDIVISIONS = {"20k": 5, "80k": 6, "330k": 7, "1.3m": 8, "5m": 9}
CASES = [("intersect", "box", {}), ("intersect", "sphere", {"size": (110.0, 90.0, 100.0)}),
         ("subtract", "cylinder", {}), ("union", "box", {}), ("xor", "pyramid", {"export_mode": "separate"}),
         ("intersect", "pyramid", {"cut": "open"})]
PLACE = {"position": (3.0, -2.0, 1.0), "rotation": (20.0, 30.0, 10.0), "size": (70.0, 55.0, 80.0)}


def generated(folder: Path, size: str):
    import numpy as np
    import trimesh
    from atlas_toolkit.geom import mesh as gm
    closed = folder / f"sphere_{size}.stl"
    opened = folder / f"scan_{size}.stl"
    if not closed.exists() or not opened.exists():
        s = trimesh.creation.icosphere(subdivisions=SUBDIVISIONS[size], radius=50.0)
        s.apply_scale((1.0, 0.85, 1.25))
        gm.write_stl(closed, s.vertices, s.faces, "validate_mesh_crop")
        V = np.asarray(s.vertices) * (1 + 0.003 * np.sin(np.asarray(s.vertices)[:, [0]] * 0.7))
        z = V[s.faces].mean(1)[:, 2]
        F = s.faces[z > z.min() + 0.1 * np.ptp(z)]
        gm.write_stl(opened, V, F, "validate_mesh_crop")
    return [(f"closed {size}", closed), (f"open {size}", opened)]


def run(tool, src: Path, out: Path, values: dict):
    from atlas_toolkit.core.job import Job, execute
    box = {}
    job = Job(tool.id, "run", str(src), str(out), dict(tool.values(values)))
    t = time.perf_counter()
    execute(tool, job, lambda k, p: box.__setitem__(k, p) if k in ("done", "error") else None, lambda: False)
    wall = time.perf_counter() - t
    if "error" in box:
        return None, str(box["error"][0]).splitlines()[0], wall
    return box["done"][0], "", wall


def check(res, values, bounds) -> str:
    """'ok' or what is wrong with a result: signed; closed results closed (each file); clipped surfaces all on the
    kept side of the shape (STL stores float32, hence the small tolerance)."""
    import numpy as np
    from atlas_toolkit.geom import mesh as gm
    from atlas_toolkit.geom import region as R
    from atlas_toolkit.geom import repair
    for out in res.outputs:
        if not (gm.read_signature(Path(out)) or "").startswith("atlas-toolkit:mesh-crop"):
            return "unsigned"
        m = gm.load_mesh(out)
        V, F = np.asarray(m.vertices), np.asarray(m.faces)
        if res.data["path"] == "closed":
            if not repair.closed_and_consistent(F):
                return f"{Path(out).name} not closed"
            continue
        loc = R.to_local(V, R.frame(bounds, values))
        if values["operation"] == "intersect":
            keep = R.inside(values["shape"], loc * (1 - 1e-5))
        else:
            keep = ~R.inside(values["shape"], loc * (1 + 1e-5))
        if keep.mean() < 1.0:
            return f"{(1 - keep.mean()) * 100:.3f}% of vertices on the wrong side"
    return "ok"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", default="80k,330k,1.3m,5m")
    ap.add_argument("--model", action="append", default=[], help="also run on this model (repeatable)")
    ap.add_argument("--quick", action="store_true", help="two cases per model")
    a = ap.parse_args(argv)
    from atlas_toolkit.core.job import inspect_input
    from atlas_toolkit.core.registry import registry
    from atlas_toolkit.geom import mesh as gm
    tool = registry().get("mesh-crop")
    out = qa_dir("mesh_crop")
    models_dir = out / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    models = []
    for size in [s for s in a.sizes.split(",") if s]:
        models += generated(models_dir, size)
    models += [(Path(p).name, Path(p)) for p in a.model]
    cases = CASES[:2] if a.quick else CASES
    rows = []
    for name, src in models:
        t = time.perf_counter()
        info = inspect_input(tool, str(src))
        inspect_time = time.perf_counter() - t
        faces = info.values.get("faces", 0)
        bounds = gm.load_mesh(src).bounds
        print(f"{name}: {faces:,} faces, inspected in {inspect_time:.2f} s ({info.summary})", flush=True)
        for op, shape, extra in cases:
            values = {"operation": op, "shape": shape, **PLACE, **extra}
            res, err, wall = run(tool, src, out / f"{src.stem}_{op}_{shape}_crop.stl", values)
            if res is None:
                verdict = "Repair Mesh asked" if "Repair Mesh" in err or "closed solid" in err else f"error: {err}"
                times, path, faces_out = {}, "-", 0
            else:
                verdict = check(res, tool.values(values), bounds)
                times, path, faces_out = res.data["times"], res.data["path"], res.data["faces"]
            row = {"model": name, "faces": faces, "inspect": round(inspect_time, 2), "operation": op, "shape": shape,
                   "path": path, "faces out": faces_out, "wall": round(wall, 2),
                   **{k: round(v, 2) for k, v in times.items()}, "check": verdict}
            rows.append(row)
            print(f"  {op:9} {shape:8} {path:6} {wall:6.2f} s  "
                  + " ".join(f"{k} {v:.2f}" for k, v in times.items()) + f"  {verdict}", flush=True)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(out / "timings.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, keys)
        w.writeheader()
        w.writerows(rows)
    lines = ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    lines += ["| " + " | ".join(str(r.get(k, "")) for k in keys) + " |" for r in rows]
    (out / "timings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    bad = [r for r in rows if r["check"] not in ("ok", "Repair Mesh asked")]
    print(f"\n{len(rows)} runs, {len(bad)} problems. Written to {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
