"""One tool's presets side by side: a contact sheet and how far apart they look, to author and review pack presets.

    python scripts/dev/preset_sheet.py --tool stipple [--models david,venus] [--presets "Soft,Solid"]
                                       [--draft drafts/stipple.json] [--only-draft] [--views contour,vision]
                                       [--jobs 3] [--tag x]

Every preset (from the private packs, plus the candidates in --draft) runs through the real command line on the
reference models. What the sheet shows depends on the tool:

    drawing tools (mesh to SVG, image)   the drawing itself
    mesh tools                           the result drawn by Contour Lines and by Plotter Vision (flat angle 25°):
                                         horizontal slices hide horizontal strata, so both views
    SVG tools                            the result on a Contour Lines drawing of the model (Shape Cutter: its
                                         own sample, which has a layer named "shape")

A draft file is a JSON list of {"name", "tip", "values": {the keys the preset sets}}. Separation: 1 - SSIM between
the drawings of every pair of presets (blurred 1 mm, measured where either has ink), averaged over models and views.
A new preset should sit at least as far from its nearest neighbour as the existing presets do (the median), or it
is a near-duplicate. Writes build/qa/preset_sheets/<tool>/: sheet<tag>.png and separation<tag>.md.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from atlas_toolkit import devpaths  # noqa: E402
from atlas_toolkit.core import presets as P  # noqa: E402
from atlas_toolkit.core.registry import registry  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import readme_gallery as gallery  # noqa: E402

MODELS = {"david": gallery.DAVID, "venus": gallery.VENUS,
          "v17": ("3d_models_db", "head_model", "v17_head.stl"),
          "julia": ("3d_models_db", "Julia-fractal", "julia_20k_v2.stl"),
          "v17s": ("3d_models_db", "head_model", "v17_head_decim7.5K-v2.stl")}     # light: for tools that multiply faces
PSI = {"david": -125.0, "venus": 155.0, "v17": 148.0, "julia": 150.0, "v17s": 148.0}
DRAWN_BY = {"contour": ("contour-lines", "Classic", ()),
            "vision": ("plotter-vision", "Clean", ("--flat-angle", 25))}
PANEL = (360, 460)
MESH_OUT = (".stl", ".obj", ".ply")


def label_font(size: int = 15):
    try:
        return ImageFont.truetype(str(devpaths.DEV_FONT), size) if devpaths.DEV_FONT else ImageFont.load_default()
    except OSError:
        return ImageFont.load_default()


def candidates(tool, args) -> list[dict]:
    """[{name, values (explicit keys), tip, new}] in sheet order: the pack's presets, then the draft's."""
    out = []
    if not args.only_draft:
        wanted = [n.strip() for n in args.presets.split(",") if n.strip()]
        raws = devpaths.preset_raws(tool)
        for name in wanted or list(raws):
            if name not in raws:
                raise SystemExit(f"{tool.id} has no pack preset {name!r}")
            tags = devpaths._dev_presets()[tool.id][name].tags
            out.append({"name": name, "values": raws[name], "new": False, "tags": list(tags)})
    if args.draft:
        for d in json.loads(Path(args.draft).read_text(encoding="utf-8")):
            out.append({"name": d["name"], "values": d["values"], "new": True, "tags": d.get("tags", [])})
    return out


def preset_file(tool, cand: dict, folder: Path) -> Path:
    values = dict(tool.defaults())
    values.update(cand["values"])
    p = P.Preset(cand["name"], tool.id, P.snapshot(tool, values, explicit=cand["values"]),
                 tool_version=tool.version, explicit=tuple(cand["values"]))
    return P.write_preset_file(folder / "preset", [p])


def svg_input(model: str, root: Path) -> Path:
    """A Contour Lines drawing of the model, made once: what the SVG tools work on (smooth curves, many of them
    touching, as the plots these tools clean up usually are)."""
    folder = root / "_inputs" / f"{model}_contours"
    if not folder.exists():
        gallery.run_tool("contour-lines", gallery.sample(MODELS[model]), folder, "Classic",
                         gallery.camera_args("contour-lines", gallery.THETA, PSI[model]))
    return next(folder.glob("*.svg"))


def source_for(tool, model: str, root: Path) -> Path:
    if tool.category == "svg":
        if tool.id == "svg-shape-cutter":
            from atlas_toolkit.tools.svg_shape_cutter.engine import make_sample
            (root / "_inputs").mkdir(parents=True, exist_ok=True)
            return make_sample(root / "_inputs")
        return svg_input(model, root)
    if tool.category in ("image", "image2mesh"):
        return gallery.sample(gallery.PHOTO)
    return gallery.sample(MODELS[model])


def run_one(tool, cand: dict, model: str, source: Path, views: list, root: Path, draw_timeout: int) -> dict:
    """Run one preset on one model; returns {view: the SVG drawn, or error text; "seconds": s}. The SVGs become
    images later, on the main thread: Qt's SVG renderer is not for many threads at once."""
    slug = P.slug(cand["name"]) + ("-new" if cand["new"] else "")
    folder = root / model / slug
    folder.mkdir(parents=True, exist_ok=True)
    preset = preset_file(tool, cand, folder)
    out = folder / "out"
    started = time.perf_counter()
    result: dict = {}
    try:
        args = ["--preset-file", preset, *gallery.camera_args(tool.id, gallery.THETA, PSI[model])]
        code, log = gallery.execute([sys.executable, "-m", "atlas_toolkit", "run", tool.id,
                                     source, "--out-dir", out, "--force", *args],
                                    gallery.child_env())
        result["seconds"] = time.perf_counter() - started
        made = sorted(p for p in out.glob("*") if p.suffix.lower() in (".svg", *MESH_OUT)) if out.exists() else []
        if code or not made:
            raise RuntimeError(gallery.reason(gallery.clean_log(log)))
        svgs = [p for p in made if p.suffix.lower() == ".svg"]
        if svgs:                                        # a drawing tool, or an SVG tool
            result["drawing"] = svgs[0]
            return result
        mesh = max(made, key=lambda p: p.stat().st_size)
        for view in views:
            drawer, look, extra = DRAWN_BY[view]
            vfolder = folder / view
            code, log = gallery.execute([sys.executable, "-m", "atlas_toolkit", "run", drawer, mesh, "--out-dir",
                                         vfolder, "--force", "--preset", look, *extra,
                                         *gallery.camera_args(drawer, gallery.THETA, PSI[model])],
                                        gallery.child_env(), timeout=draw_timeout)
            svgs = sorted(vfolder.glob("*.svg")) if vfolder.exists() else []
            result[view] = (svgs[0] if not code and svgs
                            else f"{view} failed: {gallery.reason(gallery.clean_log(log))}")
    except Exception as e:  # noqa: BLE001 - one failed preset must not stop the sheet
        result.setdefault("seconds", time.perf_counter() - started)
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def gray(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.float32) / 255.0


def distance(a: Image.Image, b: Image.Image) -> float:
    from skimage.metrics import structural_similarity
    from scipy.ndimage import gaussian_filter
    A, B = gaussian_filter(gray(a), 2.0), gaussian_filter(gray(b), 2.0)
    _, smap = structural_similarity(A, B, data_range=1.0, full=True)
    region = (A < 0.95) | (B < 0.95)
    return float(1.0 - smap[region].mean()) if region.any() else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tool", required=True)
    ap.add_argument("--models", default="david,venus")
    ap.add_argument("--presets", default="", help="only these pack presets (comma-separated)")
    ap.add_argument("--draft", help="a JSON list of candidate presets")
    ap.add_argument("--only-draft", action="store_true", help="leave out the pack's presets")
    ap.add_argument("--views", default="contour,vision", help="how mesh results are drawn")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--tag", default="")
    ap.add_argument("--draw-timeout", type=int, default=gallery.DRAW_TIMEOUT, help="seconds per drawing of a mesh")
    args = ap.parse_args()
    tool = registry().get(args.tool)
    root = devpaths.qa_dir("preset_sheets") / tool.id
    cands = candidates(tool, args)
    models = [m for m in args.models.split(",") if m]
    if tool.category in ("image", "image2mesh") or tool.id == "svg-shape-cutter":
        models = models[:1]                              # one input, whatever the models
    views = [v for v in args.views.split(",") if v]
    jobs = [(c, m) for c in cands for m in models]
    sources = {m: source_for(tool, m, root) for m in models}      # made once, before the runs share them
    with ThreadPoolExecutor(args.jobs) as pool:
        results = list(pool.map(lambda job: run_one(tool, job[0], job[1], sources[job[1]], views, root,
                                                    args.draw_timeout), jobs))
    for r in results:                                              # SVG to image, one at a time
        for k, v in list(r.items()):
            if isinstance(v, Path):
                try:
                    r[k] = gallery.ink_panel(gallery.svg_coverage(v, 2200), *PANEL)
                except Exception as e:  # noqa: BLE001
                    r[k] = f"{k}: {e}"
    by = {(c["name"], m): r for (c, m), r in zip(jobs, results)}

    kinds = ["drawing"] if any("drawing" in r for r in results) else views
    columns = [(m, k) for m in models for k in kinds]
    font, w, h, head = label_font(), PANEL[0], PANEL[1], 40
    per_row = len(columns) * max(1, 4 // len(columns))        # presets wrap so the sheet stays about 4 wide
    cells = [(c, mk) for c in cands for mk in columns]
    rows = (len(cells) + per_row - 1) // per_row
    sheet = Image.new("RGB", (per_row * w, rows * (h + head)), "white")
    pen = ImageDraw.Draw(sheet)
    for i, (c, (m, k)) in enumerate(cells):
        y = (i // per_row) * (h + head)
        r, x = by[(c["name"], m)], (i % per_row) * w
        mark = (" · NEW" if c["new"] else "") + (" · ★" if "essentials" in c["tags"] else "")
        pen.text((x + 8, y + 4), f"{c['name']}{mark}", fill=(200, 40, 40) if c["new"] else (0, 0, 0), font=font)
        pen.text((x + 8, y + 22), f"{m} · {k} · {r.get('seconds', 0):.0f}s", fill=(110, 110, 110), font=font)
        panel = r.get(k)
        if isinstance(panel, Image.Image):
            sheet.paste(panel, (x, y + head))
        else:
            pen.text((x + 8, y + head + 10), str(r.get("error") or panel or "nothing")[:300], fill=(200, 40, 40),
                     font=font)
    out = root / f"sheet{args.tag}.png"
    sheet.save(out)

    # separation: each preset's nearest neighbour, averaged over models and views
    names = [c["name"] for c in cands]
    dist = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            vals = [distance(by[(a, m)][k], by[(b, m)][k]) for m, k in columns
                    if isinstance(by[(a, m)].get(k), Image.Image) and isinstance(by[(b, m)].get(k), Image.Image)]
            if vals:
                dist[(a, b)] = dist[(b, a)] = sum(vals) / len(vals)
    lines = [f"# {tool.name}: separation (1 - SSIM, nearest neighbour)", "", "| Preset | New | Nearest | Distance |",
             "|---|---|---|---|"]
    nearest = {}
    for c in cands:
        others = [(dist[(c["name"], o)], o) for o in names if (c["name"], o) in dist]
        if others:
            d, o = min(others)
            nearest[c["name"]] = d
            lines.append(f"| {c['name']} | {'yes' if c['new'] else ''} | {o} | {d:.3f} |")
        else:
            lines.append(f"| {c['name']} | {'yes' if c['new'] else ''} | — | — |")
    old = [nearest[c["name"]] for c in cands if not c["new"] and c["name"] in nearest]
    if old:
        bar = statistics.median(old)
        close = [c["name"] for c in cands if c["new"] and nearest.get(c["name"], 1) < bar]
        lines += ["", f"Median for the existing presets: {bar:.3f}.",
                  f"New presets closer than that: {', '.join(close) or 'none'}."]
    failed = [f"{c['name']} on {m}: {by[(c['name'], m)]['error']}" for c in cands for m in models
              if "error" in by[(c["name"], m)]]
    lines += ["", "Failures:"] + [f"- {f}" for f in failed] if failed else []
    (root / f"separation{args.tag}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
