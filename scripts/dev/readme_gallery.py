"""
The README's images: one two-panel picture per tool, the hero strip, the logo and the app screenshot.

Every picture comes from the real command line, on the reference samples in the studio workspace (see devpaths):
a tool's output is rendered as clay in Blender on the left, and drawn by a drawing tool on the right.

    python scripts/dev/readme_gallery.py --list
    python scripts/dev/readme_gallery.py --only mesh-melt,dpt-hatching     # into build/qa/readme_gallery
    python scripts/dev/readme_gallery.py --sheet                           # a contact sheet of what is there
    python scripts/dev/readme_gallery.py --extras                          # logo, hero strip, screenshot
    python scripts/dev/readme_gallery.py --publish                         # copy the finished images to docs/images/readme

A step counts as done only if it ended cleanly: it works in a staging folder that replaces the real one on success, so
an interrupted or failed step leaves nothing that looks finished. A second run does only what is missing or failed, and
--status shows where each tool stands. --redraw composes a tool again from its outputs; --force starts it from nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import trimesh  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from atlas_toolkit import devpaths  # noqa: E402
from atlas_toolkit.core import procs  # noqa: E402
from atlas_toolkit.geom.blender import find_blender  # noqa: E402

HERE = Path(__file__).resolve().parent
QA = devpaths.qa_dir("readme_gallery")
PUBLISHED = devpaths.REPO / "docs" / "images" / "readme"

VENUS = ("3d_models_db", "venus_milo.stl")
DAVID = ("3d_models_db", "david_15k_reshape.stl")
PHOTO = ("imgs_db", "viana", "ponte_crop_resize.jpg")
DEM = ("code", "R", "generators", "3d_DEM", "dem_vc.tif")

PAPER = (247, 245, 240)
INK = (24, 30, 44)
RULE = (226, 222, 214)
MUTED = (112, 112, 112)
PANEL_W, PANEL_H, FOOT = 500, 640, 40
TOOL_TIMEOUT = 15 * 60          # seconds: no tool, render or drawing may hold up the batch for longer
DRAW_TIMEOUT = 5 * 60
DRAW_FACES = 600_000            # heavier outputs are refused before drawing: lighten the recipe instead
PROGRESS = re.compile(r"^\s*\S.*:\s+\d+(\.\d+)?%")
TOO_SLOW = "timed out"
FAILED = re.compile(r"\bERROR\b|^Traceback|error:", re.M)
THETA = 78.0                    # slightly from above
PSI = {VENUS: 155.0, DAVID: -125.0}     # a front three-quarter view of each sample


@dataclass
class Draw:
    """How the right-hand panel is drawn."""
    tool: str = "contour-lines"
    preset: str = "Classic"
    args: tuple = ()
    theta: float = THETA
    psi: float | None = None    # None: the sample's own front three-quarter


def contour(preset="Classic", *args, **cam) -> Draw:
    return Draw("contour-lines", preset, args, **cam)


def vision(preset="Clean", *args, **cam) -> Draw:
    return Draw("plotter-vision", preset, args, **cam)


def edges(angle=25.0, **cam) -> Draw:
    """Rims, fragments and strands: Plotter Vision with mesh noise left out."""
    return Draw("plotter-vision", "Clean", ("--flat-angle", str(angle)), **cam)


def sketch(preset="Classic", *args, **cam) -> Draw:
    return Draw("sketch-hatching", preset, args, **cam)


def hatch(preset="Engraving", *args, **cam) -> Draw:
    return Draw("stl-hatch-maker", preset, args, **cam)


@dataclass
class Recipe:
    tool: str
    sample: tuple
    preset: str | None = None
    args: tuple = ()
    draw: Draw | None = field(default_factory=contour)     # None: the tool draws by itself
    second: tuple | None = None                            # a second model, for the flag in args[-1]


DRAWN = {   # the tools that draw: the left panel shows their input
    "contour-lines", "plotter-vision", "sketch-hatching", "stl-hatch-maker", "stipple", "flowrep-sketch", "dpt-hatching",
}

RECIPES = [
    # Draw from 3D
    Recipe("contour-lines", DAVID, "Classic", draw=None),
    Recipe("plotter-vision", VENUS, "Classic", draw=None),
    Recipe("sketch-hatching", DAVID, "Classic", draw=None),
    Recipe("stl-hatch-maker", VENUS, "Engraving", draw=None),
    Recipe("stipple", DAVID, "Soft", draw=None),
    Recipe("flowrep-sketch", VENUS, "Clean", draw=None),
    # Draw from photos
    Recipe("dpt-hatching", PHOTO, "Engraving", draw=None),
    # From image to model
    Recipe("image-to-mesh", PHOTO, "Plotter soft contours", draw=edges(theta=45.0, psi=170.0)),
    Recipe("raster-dem-to-mesh", DEM, "Plotter contours", ("--z-factor", 4, "--pitch", 0.5),
           draw=contour("Classic", "--spacing-mm", 1.0, theta=45.0, psi=165.0)),
    # Prepare and cut
    Recipe("remesh-decimate", VENUS, "Flowing quad grid", draw=vision("Remeshed network")),
    Recipe("plane-cut", DAVID, "Closed cut", draw=sketch()),
    Recipe("mesh-mirror", VENUS, "Mirror a copy", draw=edges()),
    # Deform
    Recipe("mesh-bend", VENUS, "Sculptural arc", draw=hatch()),
    Recipe("mesh-twist", DAVID, "Single twist", draw=hatch()),
    Recipe("mesh-flow", VENUS, "Windblown Figure", draw=hatch()),
    Recipe("mesh-melt", DAVID, "Melting head", draw=sketch()),
    Recipe("mesh-dilate", VENUS, "Rotund Bronze", draw=sketch()),
    Recipe("mesh-compress", DAVID, "Hydraulic Press", draw=sketch()),
    Recipe("mesh-motion", VENUS, "Wind-carved bronze", draw=edges()),
    Recipe("mesh-developable", DAVID, "Sculpture", draw=edges()),
    # Surface and relief
    Recipe("mesh-texture", VENUS, "Cobble Mosaic", ("--element-size", 9, "--relief", 1.2, "--max-faces", 500000),
           draw=hatch()),
    Recipe("mesh-stripes", DAVID, "Flowing bands", draw=edges()),
    Recipe("mesh-growth", VENUS, "Coral Ridges", draw=hatch()),
    Recipe("mesh-ribbon", DAVID, "Mummy bandage", draw=sketch()),
    # Carve and fill
    Recipe("mesh-holes", VENUS, "Swiss Cheese", draw=hatch()),
    Recipe("mesh-rods", DAVID, "Sea Urchin", draw=edges()),
    Recipe("mesh-stairs", VENUS, "Turning Labyrinth", draw=edges(psi=115.0)),
    Recipe("mesh-crumble", VENUS, "Mid-Collapse", draw=edges()),
    Recipe("mesh-skeleton", DAVID, "Wire Dancer", draw=edges()),
    Recipe("mesh-porous", VENUS, "Porous Marble", draw=sketch()),
    Recipe("pipe-fill", DAVID, "Plotter organic", draw=edges()),
    Recipe("voxelize-fill", VENUS, "Cutaway sculpture", draw=edges()),
    Recipe("voxelizer", DAVID, "Medium", draw=edges()),
    # Fragment and separate
    Recipe("mesh-shatter", VENUS, "Burst", draw=sketch()),
    # One step, not the preset's three: 60,000 loose triangles that can be seen, rather than 960,000 that cannot.
    Recipe("mesh-disintegrate", DAVID, "Strong", ("--layout", "final", "--steps", 1), draw=sketch()),
    Recipe("mesh-surface-eject", VENUS, "Floating Tesserae", draw=hatch()),
    Recipe("mesh-peel", DAVID, "Marble Blossom", draw=sketch()),
    Recipe("mesh-strata", VENUS, "Floating Marble", draw=sketch()),
    Recipe("cube-grid-slicer", DAVID, "3 × 3", draw=edges()),
    # Multiply and combine
    Recipe("cubic-replicator", VENUS, "Turning crowd", ("--repeats", 3, 3, 1), draw=edges()),
    Recipe("form-replicator", DAVID, "Radiant Star", ("--source-faces", 3000), draw=hatch()),
    Recipe("mesh-fusion", VENUS, "Halfway Hybrid", ("--resolution", 160, "--max-faces", 200000, "--target-file"),
           second=DAVID, draw=hatch()),
    Recipe("mesh-dual", DAVID, "Merging Twins", ("--resolution", 160, "--max-faces", 200000, "--second"), second=VENUS, draw=hatch()),
]

SVG_TOOLS = ("svg-overlap-cleaner", "svg-smooth", "svg-simplify", "svg-shape-cutter", "svg-shape-outline")
SHOT_PATH_ROW = (300, 811, 500, 829)    # the 1440 x 900 --snapshot of Sketch Hatching: its report's output row
HERO = ("sketch-hatching", "mesh-twist", "mesh-peel", "dpt-hatching")


def sample(parts: tuple) -> Path:
    path = devpaths.workspace_path(*parts)
    if path is None or not path.is_file():
        sys.exit(f"Missing sample {'/'.join(parts)}: set the workspace in dev.local.toml.")
    return path


def child_env() -> dict:
    """Runs here leave the real profile alone: settings and history go to a folder of this script's own."""
    profile = QA / "profile"
    profile.mkdir(exist_ok=True)
    # The app ships with no presets: the recipes' --preset names come from the private packs (dev.local.toml).
    return procs.child_env(ATLAS_TOOLKIT_HOME=str(profile), **devpaths.preset_dirs_env())


def execute(cmd: list, env: dict | None = None, timeout: int = TOOL_TIMEOUT) -> tuple[int, str]:
    """Run a child that dies with this script, and never waits past its time limit."""
    proc = procs.spawn([str(c) for c in cmd], env=env or procs.child_env(), stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    try:
        output, _ = proc.communicate(timeout=timeout)
        return proc.returncode, output
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{TOO_SLOW} ({timeout} s)") from None
    finally:
        procs.finish(proc)


def clean_log(text: str) -> str:
    """The log without its progress bars, which are nearly all of it and hide the error."""
    lines = (line.rstrip() for line in re.split(r"[\r\n]+", text))
    return "\n".join(line for line in lines if line.strip() and not PROGRESS.match(line)) + "\n"


def reason(log: str) -> str:
    lines = log.strip().splitlines()
    errors = [line.strip() for line in lines if FAILED.search(line)]
    return (errors or lines or ["no output"])[-1][:300]


def run_tool(tool: str, source: Path, out_dir: Path, preset: str | None, args: tuple = (),
             timeout: int = TOOL_TIMEOUT) -> None:
    """One command-line run. Its folder appears only when it succeeded; a failure leaves <folder>.failed.txt."""
    staging = out_dir.with_name(out_dir.name + ".staging")
    failed = out_dir.with_name(out_dir.name + ".failed.txt")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    cmd = [sys.executable, "-m", "atlas_toolkit", "run", tool, source, "--out-dir", staging, "--force"]
    if preset:
        if not devpaths.preset_packs():
            sys.exit(devpaths.NO_PRESETS)
        cmd += ["--preset", preset]
    try:
        code, output = execute([*cmd, *args], child_env(), timeout)
    except RuntimeError as error:
        failed.write_text(str(error), encoding="utf-8")
        shutil.rmtree(staging, ignore_errors=True)
        raise
    log = clean_log(output)
    (staging / "log.txt").write_text(log, encoding="utf-8")
    if code or not finished(staging):
        failed.write_text(log, encoding="utf-8")
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError(f"{tool}: {reason(log)}")
    shutil.rmtree(out_dir, ignore_errors=True)
    staging.rename(out_dir)
    failed.unlink(missing_ok=True)


MADE = (".stl", ".obj", ".ply", ".svg")


def finished(folder: Path, suffixes: tuple = MADE) -> bool:
    """A step that ended cleanly: its log says so, and its files are whole."""
    log = folder / "log.txt"
    if not log.is_file() or FAILED.search(log.read_text(encoding="utf-8", errors="replace")):
        return False
    found = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in suffixes and p.stat().st_size]
    for path in found:
        if path.suffix.lower() == ".svg":
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 64))
                if b"</svg>" not in stream.read():
                    return False
    return bool(found)


def one_output(folder: Path, suffixes: tuple) -> Path:
    found = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in suffixes]
    if not found:
        raise RuntimeError(f"no {suffixes} output in {folder}")
    return max(found, key=lambda p: p.stat().st_size)


def check_weight(mesh: Path) -> None:
    faces = len(trimesh.load(mesh, force="mesh", process=False).faces)
    if faces > DRAW_FACES:
        raise RuntimeError(f"{mesh.name} has {faces:,} faces, over the {DRAW_FACES:,} to draw: lighten its recipe")


def clay(mesh: Path, out: Path, theta: float, psi: float) -> None:
    blender = find_blender()
    if blender is None:
        sys.exit("Blender was not found: the shaded panels need it.")
    staged = out.with_name(out.stem + ".staging.png")
    staged.unlink(missing_ok=True)
    code, output = execute([blender, "-b", "--factory-startup", "-noaudio", "--python-exit-code", "1",
                            "--python", HERE / "readme_gallery_blender.py", "--",
                            mesh, staged, PANEL_W * 2, PANEL_H * 2, theta, psi])
    if code or not staged.is_file():
        raise RuntimeError(f"Blender on {mesh.name}: {reason(clean_log(output))}")
    os.replace(staged, out)


def save_png(image: Image.Image, out: Path) -> None:
    staged = out.with_name(out.stem + ".staging.png")
    image.save(staged)
    os.replace(staged, out)


def svg_coverage(svg: Path, long_side: int = 3400) -> Image.Image:
    """The drawing as ink coverage on a black background (L mode), whatever colours its pens have."""
    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    _ = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(QByteArray(svg.read_bytes()))
    box = renderer.viewBoxF()
    scale = long_side / max(box.width(), box.height())
    w, h = round(box.width() * scale), round(box.height() * scale)
    image = QImage(w, h, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, w, h))
    painter.end()
    data = np.frombuffer(image.constBits(), np.uint8).reshape(h, image.bytesPerLine() // 4, 4)[:h, :w, :3]
    # The sheet is white and pens are saturated, so distance from white is coverage on any pen colour.
    return Image.fromarray(255 - data.min(axis=2).astype(np.uint8), "L")


def ink_panel(coverage: Image.Image, width: int, height: int, pad: int = 26) -> Image.Image:
    box = coverage.point(lambda v: 255 if v > 24 else 0).getbbox()
    if box is None:
        raise RuntimeError("the drawing is empty")
    cropped = coverage.crop(box)
    scale = min((width - 2 * pad) / cropped.width, (height - 2 * pad) / cropped.height)
    size = (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale)))
    fitted = np.asarray(cropped.resize(size, Image.Resampling.LANCZOS), dtype=float) / 255
    fitted = np.clip(fitted, 0, 1) ** 0.72            # thin lines keep their weight after the reduction
    panel = np.full((height, width, 3), PAPER, dtype=float)
    top, left = (height - size[1]) // 2, (width - size[0]) // 2
    area = panel[top:top + size[1], left:left + size[0]]
    area[:] = area * (1 - fitted[..., None]) + np.array(INK, dtype=float) * fitted[..., None]
    return Image.fromarray(panel.astype(np.uint8), "RGB")


def clay_panel(png: Path, width: int, height: int) -> Image.Image:
    render = Image.open(png).convert("RGBA")
    # The floor's shadow and tone fade out before the panel's edge, so no line shows against the paper around it.
    h, w = render.height, render.width
    margin = 0.06
    fade_x = np.clip(np.minimum(np.arange(w), np.arange(w)[::-1]) / (w * margin), 0, 1)
    fade_y = np.clip(np.minimum(np.arange(h), np.arange(h)[::-1]) / (h * margin), 0, 1)
    alpha = np.asarray(render.getchannel("A"), dtype=float) * np.outer(fade_y, fade_x)
    render.putalpha(Image.fromarray(alpha.astype(np.uint8), "L"))
    panel = Image.new("RGBA", render.size, (*PAPER, 255))
    panel.alpha_composite(render)
    return panel.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)


def photo_panel(path: Path, width: int, height: int, pad: int = 26) -> Image.Image:
    photo = Image.open(path).convert("RGB")
    photo.thumbnail((width - 2 * pad, height - 2 * pad), Image.Resampling.LANCZOS)
    panel = Image.new("RGB", (width, height), PAPER)
    panel.paste(photo, ((width - photo.width) // 2, (height - photo.height) // 2))
    return panel


def compose(left: Image.Image, right: Image.Image, label_left: str, label_right: str) -> Image.Image:
    sheet = Image.new("RGB", (PANEL_W * 2, PANEL_H + FOOT), PAPER)
    sheet.paste(left, (0, 0))
    sheet.paste(right, (PANEL_W, 0))
    draw = ImageDraw.Draw(sheet)
    draw.line([(PANEL_W, 24), (PANEL_W, PANEL_H + FOOT - 12)], fill=RULE, width=1)
    font = ImageFont.truetype(devpaths.DEV_FONT, 17)
    for x, text in ((PANEL_W // 2, label_left), (PANEL_W + PANEL_W // 2, label_right)):
        draw.text((x, PANEL_H + FOOT // 2 - 4), text, font=font, fill=MUTED, anchor="mm")
    return sheet


def tool_name(tool: str) -> str:
    return {
        "contour-lines": "Contour Lines", "plotter-vision": "Plotter Vision", "sketch-hatching": "Sketch Hatching",
        "stl-hatch-maker": "Hatch Maker", "stipple": "Stipple", "flowrep-sketch": "FlowRep Sketch",
        "dpt-hatching": "Photo Hatching",
    }[tool]


def view_of(recipe: Recipe) -> tuple[float, float]:
    if recipe.draw is None:
        return THETA, PSI.get(recipe.sample, 180.0)
    psi = recipe.draw.psi if recipe.draw.psi is not None else PSI[recipe.sample]
    return recipe.draw.theta, psi


def drawing_folder(recipe: Recipe) -> Path:
    """A drawing already made for the comparison is used as it is; any other has its own folder."""
    draw = recipe.draw
    for candidate in CANDIDATES.values():
        if (draw.tool, draw.preset, draw.args) == (candidate.tool, candidate.preset, candidate.args):
            return QA / recipe.tool / "compare" / draw.tool
    key = hashlib.sha256(repr((draw.tool, draw.preset, draw.args, view_of(recipe))).encode()).hexdigest()[:10]
    return QA / recipe.tool / f"drawing-{key}"


def main_output(recipe: Recipe) -> Path:
    """The tool's own result, made if it is missing or did not finish."""
    work = QA / recipe.tool / "output"
    if not finished(work):
        args = (*recipe.args, *((sample(recipe.second),) if recipe.second else ()))
        own_camera = () if recipe.draw else camera_args(recipe.tool, *view_of(recipe))
        run_tool(recipe.tool, sample(recipe.sample), work, recipe.preset, (*args, *own_camera))
    return one_output(work, (".svg",) if recipe.draw is None else (".stl", ".obj", ".ply"))


def build(recipe: Recipe, force: bool = False) -> Path:
    folder = QA / recipe.tool
    final = folder / f"{recipe.tool}.png"
    if force and folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    made = main_output(recipe)
    theta, psi = view_of(recipe)

    if recipe.draw is None:     # a drawing tool: its input on the left, its drawing on the right
        svg, shaded = made, sample(recipe.sample)
        label_right = f"Drawn by {tool_name(recipe.tool)}" + (f" · {recipe.preset}" if recipe.preset else "")
    else:
        draw_dir = drawing_folder(recipe)
        if not finished(draw_dir, (".svg",)):
            check_weight(made)
            run_tool(recipe.draw.tool, made, draw_dir, recipe.draw.preset,
                     (*camera_args(recipe.draw.tool, theta, psi), *recipe.draw.args), DRAW_TIMEOUT)
        svg, shaded = one_output(draw_dir, (".svg",)), made
        label_right = f"Drawn with {tool_name(recipe.draw.tool)}"

    png = folder / "clay.png"
    photo = recipe.tool == "dpt-hatching"
    if not photo and not png.is_file():
        clay(shaded, png, theta, psi)

    # Nothing to do if this picture was composed from these same parts.
    signature = folder / "composed.txt"
    parts = repr((label_right, str(svg.relative_to(QA)), svg.stat().st_mtime_ns,
                  None if photo else png.stat().st_mtime_ns))
    if final.is_file() and signature.is_file() and signature.read_text(encoding="utf-8") == parts:
        return final

    right = ink_panel(svg_coverage(svg), PANEL_W, PANEL_H)
    if photo:
        left, label = photo_panel(shaded, PANEL_W, PANEL_H), "Photograph"
    else:
        left, label = clay_panel(png, PANEL_W, PANEL_H), "Model" if recipe.draw is None else "Output model"
    save_png(right, folder / "drawing.png")
    save_png(compose(left, right, label, label_right), final)
    signature.write_text(parts, encoding="utf-8")
    return final


def camera_args(tool: str, theta: float, psi: float) -> tuple:
    """One view for every drawing tool, in each tool's own terms. Both samples stand on +Z."""
    if tool == "plotter-vision":
        return ("--theta", theta, "--psi", psi, "--orientation", "portrait")
    if tool == "stl-hatch-maker":   # azimuth 0 looks from +X, -90 from the front
        return (f"--azimuth={wrap_angle(90 - psi)}", f"--elevation={90 - theta}", "--orientation", "portrait")
    if tool in ("contour-lines", "sketch-hatching", "stipple", "flowrep-sketch"):
        return ("--theta", theta, "--psi", psi, "--orientation", "portrait", "--up-axis", "+z")
    return ()


def wrap_angle(degrees: float) -> float:
    return (degrees + 180) % 360 - 180


CANDIDATES = {      # the drawing tools a mesh tool's output is tried with
    "Contour Lines": contour("Classic"),
    "Plotter Vision": edges(25.0),
    "Sketch Hatching": sketch("Classic"),
    "Hatch Maker": Draw("stl-hatch-maker", "Engraving"),
}


def compare(recipe: Recipe) -> Path:
    """The output's clay beside every candidate drawing, to choose the recipe's drawing tool from. A candidate that
    fails is reported and tried again next time, and compare.png only exists once none has. One that ran out of time is
    only noted: it will not get faster, so it is not run again (delete its .failed.txt to try once more)."""
    folder = QA / recipe.tool
    final = folder / "compare.png"
    if final.is_file():
        return final
    folder.mkdir(parents=True, exist_ok=True)
    mesh = main_output(recipe)
    check_weight(mesh)
    theta, psi = view_of(recipe)
    png = folder / "clay.png"
    if not png.is_file():
        clay(mesh, png, theta, psi)

    w, h = 420, 540
    tiles = [("clay", clay_panel(png, w, h))]
    failures = []
    for name, draw in CANDIDATES.items():
        out = folder / "compare" / draw.tool
        note = out.with_name(out.name + ".failed.txt")
        if note.is_file() and TOO_SLOW in note.read_text(encoding="utf-8"):
            tiles.append((f"{name}: too slow", Image.new("RGB", (w, h), PAPER)))
            continue
        try:
            if not finished(out, (".svg",)):
                run_tool(draw.tool, mesh, out, draw.preset, (*camera_args(draw.tool, theta, psi), *draw.args), DRAW_TIMEOUT)
            tiles.append((name, ink_panel(svg_coverage(one_output(out, (".svg",))), w, h, pad=14)))
        except RuntimeError as error:
            if TOO_SLOW in str(error):
                print(f"      {recipe.tool}: {name} is too slow, left out", flush=True)
                tiles.append((f"{name}: too slow", Image.new("RGB", (w, h), PAPER)))
            else:
                failures.append(f"{name}: {error}")
                tiles.append((f"{name} FAILED", Image.new("RGB", (w, h), PAPER)))
    strip = Image.new("RGB", (w * len(tiles), h + 30), "white")
    draw_text = ImageDraw.Draw(strip)
    font = ImageFont.truetype(devpaths.DEV_FONT, 17)
    for i, (name, tile) in enumerate(tiles):
        strip.paste(tile, (i * w, 30))
        draw_text.text((i * w + 6, 6), f"{recipe.tool}: {name}" if i == 0 else name, font=font, fill=(0, 0, 0))
    save_png(strip, folder / "compare_partial.png" if failures else final)
    if failures:
        raise RuntimeError("; ".join(failures))
    (folder / "compare_partial.png").unlink(missing_ok=True)
    return final


def status() -> None:
    """Where each tool stands, and why the unfinished ones stopped."""
    for recipe in RECIPES:
        folder = QA / recipe.tool
        states = ["output" if finished(folder / "output") else "NO OUTPUT"]
        if recipe.draw is not None:
            states.append("compared" if (folder / "compare.png").is_file() else "not compared")
        states.append("picture" if (folder / f"{recipe.tool}.png").is_file() else "no picture")
        print(f"{recipe.tool:22} {', '.join(states)}")
        for note in folder.rglob("*.failed.txt"):
            print(f"{'':22}   {note.relative_to(folder)}: {reason(note.read_text(encoding='utf-8'))}")


def contact_sheet(columns: int = 4, thumb: int = 480) -> Path:
    items = [(r.tool, QA / r.tool / f"{r.tool}.png") for r in RECIPES]
    items = [(t, p) for t, p in items if p.is_file()]
    if not items:
        sys.exit("Nothing rendered yet.")
    rows = (len(items) + columns - 1) // columns
    thumb_h = round(thumb * (PANEL_H + FOOT) / (PANEL_W * 2))
    sheet = Image.new("RGB", (columns * (thumb + 10) + 10, rows * (thumb_h + 34) + 10), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(devpaths.DEV_FONT, 16)
    for i, (tool, path) in enumerate(items):
        x, y = 10 + (i % columns) * (thumb + 10), 10 + (i // columns) * (thumb_h + 34)
        sheet.paste(Image.open(path).resize((thumb, thumb_h), Image.Resampling.LANCZOS), (x, y + 22))
        draw.text((x, y), tool, font=font, fill=(0, 0, 0))
    out = QA / "contact_sheet.png"
    sheet.save(out)
    return out


def extras() -> None:
    from PySide6.QtWidgets import QApplication

    from atlas_toolkit.ui.theme import make_logo

    _ = QApplication.instance() or QApplication([])
    make_logo(256).save(str(QA / "logo.png"), "PNG")

    tiles = []
    for tool in HERO:
        recipe = next(r for r in RECIPES if r.tool == tool)
        svg_dir = QA / tool / "output" if recipe.draw is None else drawing_folder(recipe)
        tiles.append(ink_panel(svg_coverage(one_output(svg_dir, (".svg",))), 500, 600, pad=20))
    hero = Image.new("RGB", (2000, 600), PAPER)
    for i, tile in enumerate(tiles):
        hero.paste(tile, (i * 500, 0))
    hero.save(QA / "hero.png")

    shot = QA / "app.png"
    env = child_env()
    env.pop("QT_QPA_PLATFORM", None)    # the window's 3D views need a real platform
    code, output = execute([sys.executable, "-m", "atlas_toolkit", "--tool", "sketch-hatching", sample(DAVID),
                            "--snapshot", shot, "--draft", "--view", "After"], env)
    if code or not shot.is_file():
        sys.exit(f"The screenshot failed: {reason(clean_log(output))}")
    # The report's last row is the output's full path on this machine: blank it, it does not belong on a public page.
    picture = Image.open(shot).convert("RGB")
    ImageDraw.Draw(picture).rectangle(SHOT_PATH_ROW, fill=picture.getpixel((SHOT_PATH_ROW[0], SHOT_PATH_ROW[1] - 4)))
    picture.save(shot)


def publish() -> None:
    PUBLISHED.mkdir(parents=True, exist_ok=True)
    pairs = [(QA / r.tool / f"{r.tool}.png", f"{r.tool}.webp") for r in RECIPES]
    pairs += [(QA / "hero.png", "hero.webp"), (QA / "app.png", "app.webp")]
    missing = [str(p) for p, _ in pairs if not p.is_file()]
    if missing:
        sys.exit("Not rendered yet:\n" + "\n".join(missing))
    for png, name in pairs:
        Image.open(png).convert("RGB").save(PUBLISHED / name, "WEBP", quality=84, method=6)
    shutil.copy2(QA / "logo.png", PUBLISHED / "logo.png")
    total = sum(p.stat().st_size for p in PUBLISHED.iterdir())
    print(f"{len(pairs) + 1} images, {total / 1e6:.1f} MB in {PUBLISHED}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--status", action="store_true", help="where each tool stands, and why any stopped")
    ap.add_argument("--only", help="comma-separated tool ids")
    ap.add_argument("--force", action="store_true", help="run these again from the start")
    ap.add_argument("--redraw", action="store_true", help="keep each tool's output, draw and compose it again")
    ap.add_argument("--compare", action="store_true", help="try every drawing tool on each output: compare.png")
    ap.add_argument("--sheet", action="store_true", help="write the contact sheet, nothing else")
    ap.add_argument("--extras", action="store_true", help="the logo, hero strip and screenshot, nothing else")
    ap.add_argument("--publish", action="store_true", help="copy finished images to docs/images/readme, nothing else")
    args = ap.parse_args()

    if args.list:
        for r in RECIPES:
            print(f"{r.tool:22} {r.sample[-1]:26} {r.preset or '-':24} {r.draw.tool if r.draw else '(draws)'}")
        return
    if args.status:
        status()
        return
    if args.sheet:
        print(contact_sheet())
        return
    if args.extras:
        extras()
        return
    if args.publish:
        publish()
        return

    only = set(args.only.split(",")) if args.only else None
    failures = []
    for recipe in RECIPES:
        if only and recipe.tool not in only:
            continue
        if args.compare and recipe.draw is None:
            continue
        started = time.time()
        try:
            if args.compare:
                compare(recipe)
            else:
                if args.redraw:
                    for stale in ("clay.png", f"{recipe.tool}.png", "composed.txt"):
                        (QA / recipe.tool / stale).unlink(missing_ok=True)
                build(recipe, args.force)
            print(f"ok    {recipe.tool:22} {time.time() - started:6.0f} s", flush=True)
        except RuntimeError as error:      # one tool's failure must not stop a long batch
            failures.append((recipe.tool, str(error)))
            print(f"FAIL  {recipe.tool:22} {error}", flush=True)
    if not args.compare:
        print(contact_sheet())
    if failures:
        print(f"\n{len(failures)} failed; run again to retry only these:")
        for tool, why in failures:
            print(f"  {tool}: {why}")
        sys.exit(1)


if __name__ == "__main__":
    main()
