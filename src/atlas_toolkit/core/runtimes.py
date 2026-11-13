"""
External programs a tool may need: a tool lists their keys in `Tool.requires`, or in `Tool.optional` with what they
add when the tool runs without them. Nothing here names a tool. A runtime says how to find its program, which app
setting holds a path the user picked, how to explain its absence and how to install it (the About window's Setup
tab shows `why`, `steps`, `commands` and `notes`):

- the tool page shows a notice with "Locate …" while a required runtime is missing (Run and Preview wait for it);
- the self-test and the contract tests skip such a tool, saying why, instead of failing;
- the command line refuses to run it, with the same hint.

`find` is a "module:function" returning the program's Path or None. It must be cheap (no process start): the UI
asks on every page build.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import procs
from .paths import resource

WINDOWS = sys.platform == "win32"
HELPERS = ("texture", "stripes", "remesh", "developable")       # the runtimes that ship inside the app
BLENDER_FILE = "blender.exe" if WINDOWS else "Blender.app" if sys.platform == "darwin" else "blender"
PYTHON_FILE = "python.exe" if WINDOWS else "python3"


def _programs(label: str, *stems: str) -> str:
    """A file dialog filter for "Locate …", in this system's terms."""
    if WINDOWS:
        return f"{label} ({' '.join(s + '.exe' for s in stems)});;Programs (*.exe);;All files (*)"
    if sys.platform == "darwin":     # a bundle counts as one file there (paths.program_inside finds the program in it)
        return f"{label} (*.app {' '.join(stems)});;All files (*)"
    return f"{label} ({' '.join(stems)});;All files (*)"


@dataclass(frozen=True)
class Runtime:
    key: str
    label: str                  # "Blender"
    find: str                   # "atlas_toolkit.geom.blender:find_blender" -> Path | None
    setting: str = ""           # app.json key for a path the user picked with "Locate …"
    env: str = ""               # environment variable that wins over everything else
    need: str = ""              # "4.2 or newer"
    hint: str = ""              # one sentence: how to get it working
    url: str = ""               # where to download it
    get: str = ""               # the download button's label ("Get Blender"; default "Get <label>")
    file_filter: str = ""       # file dialog filter for "Locate …"
    why: str = ""               # what it is and why the toolkit uses it (a paragraph; simple HTML)
    steps: tuple = ()           # how to install it, one step each (simple HTML)
    commands: str = ""          # commands to copy, with {home} (the app's folder), {py} (a Python launcher),
                                # {venv_py} (the Python inside the new environment), {mkdir}, {cd}, {torch} and {cpu} for this system
    notes: tuple = ()           # anything else worth knowing: size, downloads, licences (simple HTML)
    installer: str = ""         # a module that installs it for the user (see INSTALLER_API); Setup and the CLI use it

    def locate_fn(self):
        mod, _, attr = self.find.partition(":")
        return getattr(importlib.import_module(mod), attr)

    def installer_module(self):
        return importlib.import_module(self.installer) if self.installer else None


# What an installer module offers (geom/depth_runtime.py is one). Setup › Components and `atlas-toolkit components`
# drive any runtime through these alone:
#   builds() -> [(key, label, about)]        plan(build, with_model, settings=, online=) -> a Plan (summary(), checks,
#   stages(), ok())    install(plan, observer, cancelled, release, settings) -> a Result (ok, lines())
#   check(model, image, observer) -> (ok, info, lines)    remove(release, models) -> (MB freed, removed)
#   status() -> {python, ours, stamp, folder, update, busy}    tidy()    detect() -> what hardware it found
#   default_model() -> the model Check runs (or None)
INSTALLER_API = ("builds", "plan", "install", "check", "remove", "status", "tidy", "detect", "default_model")

RUNTIMES: dict[str, Runtime] = {
    "texture": Runtime(
        key="texture", label="Surface Texture backend",
        find="atlas_toolkit.tools.mesh_texture.backend:find_backend", setting="texture_exe",
        env="ATLAS_TOOLKIT_TEXTURE", need="0.1.0 (protocol 1)",
        hint="Atlas bundles atlas-texture-core. From source, run native/build.py --only texture with the Atlas Python.",
        url="https://github.com/jpcy/xatlas", get="Method and source",
        file_filter=_programs("Texture helper", "atlas-texture-core"),
        why="A bundled MIT xatlas helper generates surface-following texture charts. Cylindrical and triplanar mapping run without it. No Blender is needed.",
        steps=("Use the bundled helper, or run <code>native/build.py --only texture</code> with C++ build tools and the Atlas Python.",
               "Choose Locate Surface Texture backend or set <code>ATLAS_TOOLKIT_TEXTURE</code> to a compatible helper."),
        notes=("xatlas: MIT. Twelve CC0 heightmaps from ambientCG and TextureCan are bundled for offline use.",)),
    "remesh": Runtime(
        key="remesh", label="Remesh/Decimate backend",
        find="atlas_toolkit.tools.remesh_decimate.backend:find_backend", setting="remesh_exe",
        env="ATLAS_TOOLKIT_REMESH", need="0.1.1 (protocol 1)",
        hint="Use the bundled atlas-remesh-core, or run native/build.py --only remesh with the Atlas Python.",
        url="https://www.pmp-library.org/remeshing.html", get="Method and source",
        file_filter=_programs("Remesh helper", "atlas-remesh-core"),
        why="A bundled double-precision C++ helper performs constrained quadric decimation and uniform or "
            "curvature-adaptive remeshing using the Polygon Mesh Processing Library. No compiler is needed to run a release.",
        steps=("Use the bundled helper, or run <code>native/build.py --only remesh</code> with the Atlas Python and C++ build tools.",
               "Choose Locate Remesh/Decimate backend or set <code>ATLAS_TOOLKIT_REMESH</code> to another compatible helper."),
        notes=("PMP: MIT; Eigen: MPL-2.0. Pinned source and build instructions are included in the source checkout. "
               "Blender is needed only for voxel reconstruction, planar dissolve and QuadriFlow.",)),
    "developable": Runtime(
        key="developable", label="Developable Surfaces backend",
        find="atlas_toolkit.tools.mesh_developable.backend:find_backend", setting="developable_exe",
        env="ATLAS_TOOLKIT_DEVELOPABLE", need="0.1.0 (protocol 1)",
        hint="Atlas bundles atlas-developable-core. From source, run native/build.py --only developable with the Atlas Python.",
        url="https://github.com/odedstein/DevelopabilityOfTriangleMeshes", get="Research source",
        file_filter=_programs("Developable helper", "atlas-developable-core"),
        why="A separately licensed headless C++ solver evolves triangle meshes toward developable surfaces. "
            "The release bundles it; Blender, MATLAB and a compiler are not needed to run it.",
        steps=("Use the bundled helper, or run <code>native/build.py --only developable</code> with the Atlas Python "
               "and installed C++ tools/CMake. Dependencies are pinned and downloaded locally.",
               "Choose <b>Locate Developable Surfaces backend…</b> to use another build, or set "
               "<code>ATLAS_TOOLKIT_DEVELOPABLE</code> to the executable."),
        notes=("Solver adaptation: GPL-2.0-or-later; Kopp eigensolver: LGPL-2.1-or-later; "
               "libigl/Eigen: MPL-2.0; JSON: MIT. Notices and corresponding source archive ship with Atlas.",)),

    "stripes": Runtime(
        key="stripes", label="Surface Stripes backend",
        find="atlas_toolkit.tools.mesh_stripes.backend:find_backend", setting="stripes_exe",
        env="ATLAS_TOOLKIT_STRIPES", need="0.1 or newer",
        hint="The Atlas release bundles atlas-stripes-core. From source, run the Atlas Python on "
             "native/build.py --only stripes, or locate the helper executable.",
        url="https://geometry-central.net/surface/algorithms/stripes/", get="Algorithm and source",
        file_filter=_programs("Stripe helper", "atlas-stripes-core"),
        why="A small headless C++ helper solves the direction field and stripe pattern using Geometry Central "
            "and Eigen. Atlas turns its curves into raised solid bands. The helper ships with the release; "
            "there is no compiler, OpenGL, or SuiteSparse requirement when using the app.",
        steps=("Use the helper bundled with an Atlas release, or build it from the source checkout.",
               "For a source build: run the Atlas Python on <code>native/build.py --only stripes</code>. "
               "It downloads pinned project-local dependencies and uses installed C++ tools and CMake.",
               "To use another build, choose <b>Locate Surface Stripes backend…</b> or set "
               "<code>ATLAS_TOOLKIT_STRIPES</code> to its executable."),
        notes=("Geometry Central and Eigen are used with the Eigen solver; SuiteSparse is disabled.",
               "Geometry Central omits the paper's special interior interpolation. Atlas refines the "
               "working surface and reports insufficient resolution.")),
    "blender": Runtime(
        key="blender", label="Blender", find="atlas_toolkit.geom.blender:find_blender", setting="blender_exe",
        env="ATLAS_TOOLKIT_BLENDER", need="4.2 or newer",
        hint=f"Install Blender 4.2 or newer (blender.org), or point to {BLENDER_FILE} with “Locate Blender…”. "
             "The toolkit runs it in the background, without a window.",
        url="https://www.blender.org/download/",
        file_filter=_programs("Blender", "blender"),
        why="Blender is a free, open-source 3D suite. A few mesh tools hand it the parts of their work that need its "
            "solvers: Geometry Nodes, the cloth simulation, Complex Solidify. The toolkit starts it in the "
            "background, without a window, once per session (a few seconds), and talks to it through small "
            "scripts of its own. Nothing is installed into Blender and no add-on is needed.",
        steps=("Download Blender <b>4.2 or newer</b> from blender.org: the installer, the portable .zip or a store "
               "version all work.",
               "Restart the toolkit. It looks for Blender in the usual install folders and on the PATH.",
               f"Installed somewhere else? Press <b>Locate Blender…</b> and pick <code>{BLENDER_FILE}</code> (or set "
               "the environment variable <code>ATLAS_TOOLKIT_BLENDER</code> to its path)."),
        notes=("Blender is not part of the toolkit and is not shipped with it; it stays under its own licence "
               "(GPL-3.0-or-later).",
               "When several versions are installed the newest is used, unless you located one.")),
    "depth": Runtime(
        key="depth", label="Depth environment", find="atlas_toolkit.geom.depth:runtime_python",
        setting="ml_python", env="ATLAS_TOOLKIT_ML_PYTHON", need="(Python with PyTorch)",
        hint="Install the depth environment from Setup › Components (one button, nothing else needed), or point to "
             f"an environment's {PYTHON_FILE} with “Locate Depth environment…”.",
        url="https://pytorch.org/get-started/locally/", get="About PyTorch",
        file_filter=_programs("Python", "python", "python3"),
        installer="atlas_toolkit.geom.depth_runtime",
        why="Tools that draw photos read the scene's depth to make strokes wrap around forms. The best depth "
            "comes from neural networks (MiDaS / DPT, Depth Anything) run with PyTorch, ideally on an NVIDIA GPU. "
            "PyTorch and its CUDA libraries are large (0.5 to 5 GB), so they live in a separate Python "
            "environment outside the app (<b>ml-runtime</b> in your local app-data folder); the toolkit starts "
            "it when a drawing needs a model's depth and keeps it running between drawings. Without it these "
            "tools still work, from a "
            "depth-map file you provide or a rough depth guessed from tone.",
        steps=("Press <b>Install…</b>. Atlas looks for an NVIDIA card, picks the PyTorch build that suits it (or the "
               "CPU build), and shows what it will download, the disk space and the time before anything starts. "
               "It needs no Python of your own and changes nothing outside its folder.",
               "Follow the progress; when it ends, the tools find the environment by themselves. <b>Check</b> proves "
               "it still works (and on which device), <b>Remove</b> deletes everything it installed.",
               "Made an environment yourself? Press <b>Locate Depth environment…</b> and pick its "
               f"<code>{PYTHON_FILE}</code> (or set <code>ATLAS_TOOLKIT_ML_PYTHON</code>)."),
        commands="atlas-toolkit components install depth",
        notes=("<b>Model weights download on first use</b>: about 1.3 GB per model, once, into the Hugging Face "
               "cache (<code>~/.cache/huggingface</code>, or <code>HF_HOME</code>). The first drawing with a model "
               "needs an internet connection and takes a few minutes; after that the models load from disk, "
               "offline.",
               "Depths are cached by image, model and detail, so changing hatching settings never re-runs the "
               "model.",
               "On a Mac the models run on the CPU. Apple's GPU is untested with them so far: to try it, set "
               "<code>ATLAS_DEPTH_DEVICE=mps</code>.",
               "Model licences: MiDaS 3.1 BEiT-L-512 (MIT) and DPT-Large (Apache-2.0) allow any use; "
               "<b>Depth Anything V2 Large is for non-commercial use only</b> (CC BY-NC 4.0).",
               "On a server or over SSH, the command line does the same: <code>atlas-toolkit components install "
               "depth</code>, then <code>components check depth</code> (<code>components --help</code> lists the options).",
               "No NVIDIA card? The CPU build works on any computer, only slower. AMD and Intel graphics cards are "
               "not used by these PyTorch builds.")),
}


def get(key: str) -> Runtime:
    return RUNTIMES[key]


def find_helper(stem: str, env: str, setting: str) -> Path | None:
    """A native helper, in this order: the environment variable (a wrong one stays "not found": it was an explicit
    choice), a path the user picked, the bundled one, the PATH. A picked path that has gone stale falls through, so
    settings imported from another machine cannot disable a bundled helper."""
    chosen = os.environ.get(env)
    if chosen:
        path = Path(chosen).expanduser()
        return path if _can_start(path) else None
    try:
        from .settings import AppSettings
        picked = AppSettings().get(setting)
    except (OSError, ValueError):
        picked = None
    name = stem + (".exe" if WINDOWS else "")
    for path in (Path(picked) if picked else None, resource("bin", name)):
        if path is not None and _can_start(path):
            return path
    found = shutil.which(name)
    return Path(found) if found else None


def _can_start(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


_CHECKED: dict = {}


def check_helper(exe, stem: str, protocol: int | None = None) -> str:
    """Start a helper once with --version before trusting it with a job, and give its version line. A build for another
    OS or CPU, or one speaking another protocol, then gets a clear message instead of a failed job. Not in `find_helper`:
    finding must stay cheap. Remembered per file, so it costs one start per process."""
    path = Path(exe)
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size, protocol)
    if key in _CHECKED:
        return _CHECKED[key]
    advice = "Rebuild it with native/build.py, or point to a compatible one with Locate."
    try:
        proc = procs.spawn([str(path), "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        raise RuntimeError(f"{path.name} cannot start on this system ({e}). {advice}") from None
    try:
        output = proc.communicate(timeout=30)[0].strip()
    except subprocess.TimeoutExpired:
        output = ""
    finally:
        procs.finish(proc)
    line = output.splitlines()[0] if output else ""
    if proc.returncode or not line.startswith(stem + " "):
        raise RuntimeError(f"{path.name} is not a working {stem}: {line or 'it gave no version'}. {advice}")
    if protocol is not None and f"protocol {protocol}" not in line:
        raise RuntimeError(f"{path.name} speaks another protocol than this app (protocol {protocol}): {line}. {advice}")
    _CHECKED[key] = line
    return line


def find(key: str) -> Path | None:
    """The program for runtime `key`, or None. Never raises: a broken finder counts as "not found"."""
    try:
        p = get(key).locate_fn()()
    except Exception:
        return None
    return Path(p) if p else None


def required_keys(tool, values=None):
    values = tool.defaults() if values is None else values
    keys = list(getattr(tool, 'requires', ()))
    for key, rule in getattr(tool, 'requires_if', ()):
        if all(values.get(k) in expected if isinstance(expected, (tuple, list)) else values.get(k) == expected
               for k, expected in rule.items()):
            keys.append(key)
    return tuple(dict.fromkeys(keys))


def missing(tool, values=None) -> list[Runtime]:
    """The runtimes `tool` requires that cannot be found right now."""
    return [get(k) for k in required_keys(tool, values) if k in RUNTIMES and find(k) is None]


def describe(tool) -> list[str]:
    """'Blender 4.2 or newer: C:\\...\\blender.exe' or '... : not found' for each runtime the tool requires."""
    out = []
    for k in getattr(tool, "requires", ()):
        rt = RUNTIMES.get(k)
        if rt is None:
            continue
        p = find(k)
        out.append(f"{rt.label} {rt.need}: {p if p else 'not found. ' + rt.hint}".strip())
    return out
