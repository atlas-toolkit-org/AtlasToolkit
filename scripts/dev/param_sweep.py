"""
Parameter study for the 3D tools (dev only, not shipped): how much does each setting change the drawing?

    .venv\\Scripts\\python scripts\\param_sweep.py --tool mesh-twist [--models v17,augustus,julia] [--points 5]
                                                 [--params turn,sway] [--out DIR] [--quick]

For every exposed parameter (camera, paper, frame and quality settings excepted) it runs the tool at --points
values across the parameter's range, the rest at the tool's defaults, on each model, and measures the effect:

- drawing tools: the plotted SVG is rasterised at its pens' real widths (3 px/mm), blurred --blur mm (1: the eye
  at reading distance; 3: tone only; 0.3: up close), and compared with the default drawing, inside the inked area:
  effect = 1 - SSIM, or with --metric ink the share of the ink that moves (tone tools such as Stipple, where SSIM
  hardly sees a change of tone on white paper); the largest effect over the sweep counts;
- mesh tools (Twist, Melt): each result is drawn by Contour Lines through one fixed camera in the input model's
  frame, compared the same way; plus how far the surface moved (mean, 95th percentile, % of the height).

Outputs, in --out (default: a folder in the temp directory; never under projects/, imgs_db/ or 3d_models_db/):
- contact_<tool>_<model>.png: one row per parameter (sorted by effect), one column per value, the default first;
- ranking_<tool>.md and .csv: effect per parameter and model, its spread across models, the declared rank
  (primary = in the form, advanced = behind "Show advanced settings"), and flags:
    "advanced but strong"  an advanced setting that changes the drawing more than the median primary one;
    "primary but weak"     a primary setting under a quarter of the median primary effect;
    "model-dependent"      effects differ a lot between models (a normalisation to look at).
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401

WORKSPACE = studio_workspace()
MODELS = {
    "v17": WORKSPACE / "3d_models_db" / "head_model" / "v17_head.stl",
    "augustus": WORKSPACE / "projects" / "blender3d_project" / "augustus" / "augustus.prima.top.01.mesh160k.stl",
    "julia": WORKSPACE / "3d_models_db" / "Julia-fractal" / "julia_20k_v2.stl",
    "david": WORKSPACE / "3d_models_db" / "david_15k_reshape.stl",
    "venus": WORKSPACE / "3d_models_db" / "venus_milo.stl",
}
SKIP = {"theta", "psi", "radius", "lookat", "fov", "projection", "paper", "orientation", "margin", "up_axis",
        "front", "rotate", "normalise", "reference_mesh", "draft_faces", "px_per_mm", "optimise", "save_stages",
        "layout", "axis", "gap", "seed", "stages", "head_from", "head_to", "line_width", "dot_width",
        "outline_width", "fine_width", "bold_width", "step_mm"}
FORBIDDEN = [WORKSPACE / "projects", WORKSPACE / "imgs_db", WORKSPACE / "3d_models_db"]


def sweep_values(p, n: int) -> list:
    """Values across a parameter's useful range, the default included (first)."""
    from atlas_toolkit.core.params import Bool, Choice, Float, Int
    d = p.validate(None)
    if isinstance(p, Bool):
        return [d, not d]
    if isinstance(p, Choice):
        return [d] + [v for v, _ in p.pairs() if v != d]
    if isinstance(p, (Float, Int)):
        lo = p.min if math.isfinite(p.min) else d - abs(d) - 1
        hi = p.max if math.isfinite(p.max) else d + abs(d) + 1
        if d != 0 and hi > 4 * abs(d) + 1e-9 and hi > d:   # huge ranges: sweep 0..4x the default
            hi = 4 * abs(d)
        if d > 0 and lo < 0:
            lo = 0.0
        vals = list(np.linspace(lo, hi, n))
        if isinstance(p, Int):
            vals = sorted({int(round(v)) for v in vals})
        vals = [v for v in vals if abs(v - d) > 1e-9 * max(1.0, abs(d))]
        return [d] + vals
    return [d]


def blur_raster(img: np.ndarray, sigma_px: float) -> np.ndarray:
    from scipy.ndimage import gaussian_filter
    return gaussian_filter(img.astype(np.float32), sigma_px)


def drawing_raster(drawing, px_per_mm=3.0) -> np.ndarray:
    from atlas_toolkit.geom import sketch as sk
    layers = [{"width": pen.width, "strokes": pen.polylines()} for pen in drawing.pens]
    return sk.rasterize(layers, (drawing.width, drawing.height), px_per_mm, aa=1)


BLUR_MM = 1.0
METRIC = "ssim"


def effect(a: np.ndarray, b: np.ndarray, px_per_mm=3.0) -> float:
    """1 - SSIM of the two plots blurred 1 mm, averaged where either has ink (the empty paper around a drawing
    would otherwise dilute every difference)."""
    from skimage.metrics import structural_similarity
    A, B = blur_raster(a, px_per_mm * BLUR_MM), blur_raster(b, px_per_mm * BLUR_MM)
    if METRIC == "ink":       # how much ink moved at the blur's scale, of the ink there is (tone tools)
        return float(np.abs(A - B).sum() / max((1 - A).sum(), (1 - B).sum(), 1e-9))
    _, smap = structural_similarity(A, B, data_range=1.0, full=True)
    region = (A < 0.98) | (B < 0.98)
    if not region.any():
        return 0.0
    return float(1.0 - smap[region].mean())


def ink_box(r: np.ndarray, pad: float = 0.08):
    ys, xs = np.nonzero(r < 0.5)
    if not len(xs):
        return 0, 0, r.shape[1], r.shape[0]
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    px, py = int((x1 - x0) * pad) + 2, int((y1 - y0) * pad) + 2
    return max(0, x0 - px), max(0, y0 - py), min(r.shape[1], x1 + px), min(r.shape[0], y1 + py)


def fit_camera(src: Path, paper="A4", orientation="portrait", margin=15.0) -> dict:
    """A 3/4 view from a little above that fits the model, in its own frame."""
    from atlas_toolkit.geom import frame3d
    from atlas_toolkit.geom.camera import Camera, fit_radius_points, frame_for
    f = frame3d.frame_for(src, {"up_axis": "auto", "front": "auto", "normalise": "model"})
    lo, hi = f.lo, f.hi
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    cam = Camera(75.0, 150.0, 200.0, (0.0, 0.0, 0.0), 36.0)
    r = fit_radius_points(corners, cam, frame_for(paper, orientation, margin), margin=1.02)
    return {"theta": 75.0, "psi": 150.0, "radius": round(r, 1), "lookat": (0.0, 0.0, 0.0), "fov": 36.0,
            "paper": paper, "orientation": orientation, "margin": margin}


def run_tool(tool, src: Path, values: dict, folder: Path, ctx_mode="run"):
    from atlas_toolkit.core.io import output_path
    from atlas_toolkit.core.job import JobContext
    vals = tool.values(values)
    dst = output_path(tool, src, vals, "folder", str(folder)) if ctx_mode == "run" else None
    ctx = JobContext(lambda *_: None, lambda: False, ctx_mode, str(folder / "cache"))
    res = tool.engine_fn()(ctx, src, dst, vals)
    return res, dst


def contours_of(mesh_path: Path, reference: Path, cam: dict, folder: Path):
    """Contour Lines drawing of a mesh in the frame of `reference` (the input model)."""
    from atlas_toolkit.core.registry import registry
    cl = registry().get("contour-lines")
    vals = dict(cam, spacing_mm=2.5, min_len=3.0, normalise="reference", reference_mesh=str(reference))
    res, _ = run_tool(cl, mesh_path, vals, folder, "analyse")
    return res.preview["views"][0]["drawing"]


def displacement(a_path: Path, b_path: Path) -> tuple[float, float]:
    """How far surface b lies from surface a: mean and 95th percentile of the nearest distances from b's vertices,
    % of a's height."""
    from scipy.spatial import cKDTree
    from atlas_toolkit.geom.mesh import load_mesh
    a, b = load_mesh(a_path), load_mesh(b_path)
    h = float(np.ptp(a.vertices[:, 2])) or 1.0
    pts, _ = __import__("trimesh").sample.sample_surface(a, 60_000, seed=0)
    q = b.vertices if len(b.vertices) <= 300_000 else b.vertices[np.random.default_rng(0).choice(len(b.vertices),
                                                                                            300_000, replace=False)]
    d, _ = cKDTree(pts).query(q)
    return float(d.mean() / h * 100), float(np.percentile(d, 95) / h * 100)


def thumb(raster: np.ndarray, size=150):
    from PIL import Image
    img = Image.fromarray((np.clip(raster, 0, 1) * 255).astype(np.uint8))
    img.thumbnail((size, size))
    canvas = Image.new("L", (size, size), 255)
    canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2))
    return canvas


def contact_sheet(rows: list, path: Path, size=150):
    """rows: [(label, effect, [(value text, raster)])]"""
    from PIL import Image, ImageDraw
    cols = max(len(r[2]) for r in rows) if rows else 1
    lw = 230
    W, H = lw + cols * (size + 6), len(rows) * (size + 22) + 10
    sheet = Image.new("L", (W, H), 255)
    dr = ImageDraw.Draw(sheet)
    for i, (label, eff, cells) in enumerate(rows):
        y = 5 + i * (size + 22)
        dr.text((6, y + size // 2 - 8), f"{label}\neffect {eff:.3f}", fill=0)
        ref_box = ink_box(cells[0][1])
        for j, (txt, ras) in enumerate(cells):
            x = lw + j * (size + 6)
            b = ink_box(ras)
            x0, y0 = min(ref_box[0], b[0]), min(ref_box[1], b[1])
            x1, y1 = max(ref_box[2], b[2]), max(ref_box[3], b[3])
            sheet.paste(thumb(ras[y0:y1, x0:x1], size), (x, y))
            dr.text((x + 2, y + size + 2), txt, fill=60)
    sheet.save(path)


def fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.3g}"
    return str(v)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tool", required=True)
    ap.add_argument("--models", default="v17,augustus,julia")
    ap.add_argument("--points", type=int, default=5)
    ap.add_argument("--params", default="", help="comma-separated keys (default: every swept parameter)")
    ap.add_argument("--out", default="")
    ap.add_argument("--quick", action="store_true", help="draft quality (melt: decimated, no subdivision)")
    ap.add_argument("--blur", type=float, default=1.0,
                    help="mm of blur before comparing (1: the eye at reading distance; 3: tone only, e.g. stipple, "
                         "where moving dots would otherwise count)")
    ap.add_argument("--metric", choices=("ssim", "ink"), default="ssim",
                    help="ssim: 1 - SSIM (structure); ink: the share of the ink that moves at the blur's scale (tone: "
                         "SSIM hardly sees a low-contrast change of tone on white paper)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="base values for every run, e.g. --set head=true to sweep the face terms")
    args = ap.parse_args(argv)
    global BLUR_MM, METRIC
    BLUR_MM, METRIC = args.blur, args.metric
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix=f"atlas_sweep_{args.tool}_"))
    tag = ("_" + "_".join(kv.replace("=", "-") for kv in args.set)) if args.set else ""
    out = out.resolve()
    if any(str(out).lower().startswith(str(f).lower()) for f in FORBIDDEN):
        print("refusing to write under projects/, imgs_db/ or 3d_models_db/", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    from atlas_toolkit.core.registry import registry
    tool = registry().get(args.tool)
    is_mesh = tool.output.kind == "mesh"
    keys = [k for k in args.params.split(",") if k] or [p.key for p in tool.param_list if p.key not in SKIP]
    params = [tool.param(k) for k in keys]
    results = {}      # key -> model -> effect
    extra = {}        # key -> model -> (mean, p95) displacement at the largest effect
    for mname in args.models.split(","):
        src = MODELS[mname]
        folder = out / mname
        folder.mkdir(exist_ok=True)
        cam = fit_camera(src)
        base = {} if is_mesh else dict(cam)
        for kv in args.set:
            k, _, v = kv.partition("=")
            base[k] = tool.param(k).validate(v)
        if is_mesh and args.quick:
            base.update(subdiv=0)
        t0 = time.time()

        def render(values: dict):
            if is_mesh:
                res, dst = run_tool(tool, src, dict(base, **values), folder, "run")
                ras = drawing_raster(contours_of(dst, src, cam, folder))
                return ras, dst
            res, _ = run_tool(tool, src, dict(base, **values), folder, "draft" if args.quick else "analyse")
            return drawing_raster(res.preview["views"][0]["drawing"]), None

        ref, ref_path = render({})
        rows = []
        for p in params:
            vals = sweep_values(p, args.points)
            cells = [(f"{fmt(vals[0])} (default)", ref)]
            effs = []
            for v in vals[1:]:
                try:
                    ras, path = render({p.key: v})
                except Exception as e:           # a setting that breaks a model is a finding too
                    print(f"  {mname} {p.key}={v}: {type(e).__name__}: {e}")
                    continue
                e = effect(ref, ras)
                effs.append(e)
                cells.append((fmt(v), ras))
                if is_mesh and path is not None and e >= max(effs):
                    extra.setdefault(p.key, {})[mname] = displacement(src, path)
            eff = max(effs) if effs else 0.0
            results.setdefault(p.key, {})[mname] = eff
            rows.append((p.key + (" (adv)" if p.advanced else ""), eff, cells))
            print(f"{mname:9} {p.key:22} effect {eff:.3f}   ({time.time() - t0:.0f} s)", flush=True)
        rows.sort(key=lambda r: -r[1])
        contact_sheet(rows, out / f"contact_{args.tool}_{mname}{tag}.png")
    # ranking
    models = args.models.split(",")
    prim = [statistics.mean(results[p.key].values()) for p in params if not p.advanced and results.get(p.key)]
    med = statistics.median(prim) if prim else 0.0
    lines = [f"# {tool.name}: parameter effects", "",
             f"Models: {', '.join(models)}; {args.points} points per parameter; effect = "
             + ("1 - SSIM of the plot raster" if args.metric == "ssim" else "the share of the ink that moves, plot raster")
             + f" blurred {args.blur:g} mm, against the default" + (" (drawn by Contour Lines)" if is_mesh else "") + ".", "",
             f"Median primary effect: {med:.3f}.", "",
             "| parameter | rank | " + " | ".join(models) + " | mean | spread | flag |",
             "|---|---|" + "---|" * len(models) + "---|---|---|"]
    rows_csv = []
    for p in sorted(params, key=lambda q: -statistics.mean(results.get(q.key, {"x": 0}).values())):
        r = results.get(p.key, {})
        vals = [r.get(m, float("nan")) for m in models]
        mean = statistics.mean([v for v in vals if not math.isnan(v)] or [0.0])
        spread = (statistics.pstdev(vals) / mean) if mean > 1e-6 and len(vals) > 1 else 0.0
        flags = []
        if p.advanced and mean > med and med > 0:
            flags.append("advanced but strong")
        if not p.advanced and med > 0 and mean < 0.25 * med:
            flags.append("primary but weak")
        if spread > 0.75 and mean > 0.25 * med:
            flags.append("model-dependent")
        disp = extra.get(p.key, {})
        lines.append(f"| {p.key} | {'advanced' if p.advanced else 'primary'} | "
                     + " | ".join(f"{v:.3f}" for v in vals) + f" | {mean:.3f} | {spread:.2f} | {', '.join(flags)} |")
        rows_csv.append([p.key, "advanced" if p.advanced else "primary", *[f"{v:.4f}" for v in vals], f"{mean:.4f}",
                         f"{spread:.3f}", ";".join(flags),
                         ";".join(f"{m}:{a:.2f}/{b:.2f}" for m, (a, b) in disp.items())])
    lines.insert(2, f"Base: {', '.join(args.set)}." if args.set else "")
    (out / f"ranking_{args.tool}{tag}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with open(out / f"ranking_{args.tool}{tag}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["parameter", "rank", *models, "mean", "spread", "flags", "displacement mean/p95 % h"])
        w.writerows(rows_csv)
    print(f"\nwritten to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
