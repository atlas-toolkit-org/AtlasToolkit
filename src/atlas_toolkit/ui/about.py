"""
The About window (the ⓘ button, or "About" in the palette): a resizable, modeless window with tabs.

    About      what the toolkit is; version, system and runtimes; copy diagnostics
    Tools      every tool with its icon, version, input → output and summary, grouped and filterable
    Command line  the commands and options of core/cli, with examples built from the installed tools
    Shortcuts  the keyboard
    Changelog  CHANGELOG.md, one release at a time (shipped inside the exe as a package resource)
    Credits    who made it and the open-source parts it is built on

Every tab scrolls; the window has minimise and maximise buttons and a size grip. Setting things up (options,
optional components, the app's folders and cache) is the Setup window's (ui/setup.py), which uses the helpers here.

The window shows nothing particular to the computer it runs on (no resolved paths, user names, drives or operating
system): folders are named generically, and a found program by where it is relative to the app's folder or a
standard folder (display_path: <app>\\.venv-ml\\…, %ProgramFiles%\\Blender Foundation\\…, "bundled with the app").
Copy diagnostics, which the person asks for to report a problem, is the one place that gives the real paths.
tests/test_gui.py checks this.
"""
from __future__ import annotations

import os
import platform
import re
import sys
from pathlib import Path

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QSize, Qt, qVersion
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                               QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..core import runtimes
from .keys import shown
from ..core.paths import PORTABLE_DIR, app_home, cache_dir, config_dir, frozen, ml_runtime_dir, resource
from ..version import APP_NAME, COPYRIGHT, HOMEPAGE, LICENSE_ID, SOURCE_URL, SUPPORT_URL, __version__
from .theme import icon, label_font, logo_icon, make_logo, mono, sans
from .widgets.common import badge, hairline, hint, label

TAGLINE = "Pen-plotter drawing, terrain modelling and image reliefs from SVGs, meshes, DEMs and images"
STUDIO = "sci.co.plot"

CREDITS = [   # (name, role, detail)
    (STUDIO, "Concept, design and direction",
     "The idea and design of the toolkit: what each tool draws and how it should look and behave."),
    ("Claude Code", "Development · Anthropic",
     "AI coding assistant: implementation, tests, the build and this interface."),
    ("ChatGPT", "Development · OpenAI",
     "AI assistant: early scripts and algorithms several tools grew from, FlowRep Sketch, Surface Stripes, Surface Texture, Dilate, Developable Surfaces, Remesh/Decimate, Raster DEM to Mesh and Image to Mesh."),
]

BUILT_WITH = [   # (project, licence)
    ("xatlas / Surface Texture helper", "MIT; pinned source; no Python ABI dependency"),
    ("ambientCG and TextureCan heightmaps", "CC0-1.0; twelve prepared offline textures; provenance bundled"),
    ("Qt for Python (PySide6)", "LGPL-3.0"), ("NumPy", "BSD-3-Clause"), ("SciPy", "BSD-3-Clause"),
    ("trimesh", "MIT"), ("Shapely", "BSD-3-Clause"), ("manifold3d", "Apache-2.0"), ("pyqtgraph", "MIT"),
    ("Instant Meshes (PyNanoInstantMeshes)", "BSD-3-Clause"), ("NetworkX", "BSD-3-Clause"),
    ("Geometry Central", "MIT"), ("Eigen", "MPL-2.0; bundled notices"), ("libigl", "MPL-2.0"),
    ("Developable Surfaces helper", "GPL-2.0-or-later; Kopp LGPL-2.1-or-later; source bundled"),
    ("nlohmann JSON", "MIT"),
    ("Polygon Mesh Processing Library (PMP)", "MIT; double-precision remesh helper"),
    ("Rasterio / GDAL / PROJ", "BSD-3-Clause / MIT; bundled raster drivers, coordinate data and notices"),
    ("pydelatin / hmm", "MIT; error-controlled adaptive terrain triangulation; bundled notices"),
    ("Geist and Geist Mono fonts", "SIL OFL 1.1"),
    ("Python", "PSF-2.0"),
    ("PyInstaller bootloader", "GPL-2.0-or-later, with an exception for bundled programs"),
]

RESEARCH = [   # (method, used by, reference): published methods implemented or adapted by the tools
    ("Arc-length curvature regularization and compensated spectral filtering", "Line Smooth",
     "Whittaker–Henderson / Eilers (2003), Schmid et al. (2022), Taubin (1995). "
     "Compiled banded Cholesky or equivalent DST/FFT fairing, anti-aliased physical arc-length samples, "
     "fixed endpoints/contact spans, persistent corner support, displacement/length/area limits and "
     "all-pen swept-region checks. Geometry moves without vertex reduction; dense vector SVG output. "
     "See docs/LINE_SMOOTH.md for the adapted transfer functions, sources and sampled-curve limits."),
    ("Solid surface sampling and normal-directed extraction", "Surface Eject",
     "Manifold Split computes the actual extracted model material and matching cavity together. "
     "Original-face provenance supplies area-weighted surface normals and enforces painted masks. "
     "Cube, box, sphere, ellipsoid and cylinder samplers; certified gap to the body and earlier fragments, "
     "strict closed inputs or explicit repair, visible-face painting and combined/separate signed exports. "
     "Optional cylinders, square bars and bounded organic stems/forks use containment-tested fragment interiors "
     "and final cavity interfaces, with embedded joints and a validated boolean-fused assembly. Thickness, taper, "
     "curvature, fork count, split position, spread and maximum extent; separate body/fragments/connectors. "
     "Connect 0–100% of extracted fragments: exact rounded counts, independent seeded random selection or "
     "largest/smallest first by volume. Stable subsets and unchanged ejection; unselected pieces stay floating. "
     "Up to 100 fixed or inclusive random-range extra branches per connected fragment; beginning/middle/end, "
     "random or mixed visible-span origins, up to four generations and a separate branch seed. Dense interior "
     "tip sampling, fitted branch thickness, bounded centreline extent and validated export topology. "
     "Original orientation, units and source detail retained; no Blender or new dependencies."),
    ("Surface cells, line fields and swept wires", "Skeleton Shell",
     "Lloyd-relaxed seeds and multi-source Dijkstra on the model's edge graph give geodesic Voronoi cells and their dual "
     "triangles. Strands walk a smoothed principal-curvature tensor field (Rusinkiewicz, 2004) across the triangles. Ribs "
     "are level curves, and flat frames are cut with Manifold. Seeded; no Blender or new dependencies."),
    ("Fracture by convex cells and position-based rigid bodies", "Crumble",
     "Voronoi or lattice cells cut the model into closed blocks with Manifold booleans, through a k-d tree of boxes. A "
     "small built-in solver drops them: position-based dynamics with small substeps (Müller et al., 2020), hull planes "
     "between blocks and a signed distance grid for the standing body. Seeded; no Blender or new dependencies."),
    ("Constructive solid staircases in a distance-shaped cavity", "Stair Sculpt",
     "Manifold booleans fuse steps, landings and arches with the model. The cavity is a signed distance from the fast "
     "winding number (Barill et al., 2018, through libigl), swept toward the opening and extracted with Lewiner "
     "marching cubes. Seeded ensemble layout on a grid of storeys; no Blender or new dependencies."),
    ("Surface chart parameterization and geometric displacement", "Surface Texture",
     "xatlas (Jonathan Young), MIT: automatic UV charts. Atlas adds welded conforming refinement, "
     "midpoint/centre height-error sampling, seam feathering, cylindrical periodic wrapping and triplanar blending. "
     "CC0 displacement maps from ambientCG / TextureCan are filtered into graphic relief. "
     "Source units, region masks, open borders, swept normal clearance and final contact checks; no Blender required."),
    ("Image heightfields, grayscale conversion and calibrated tone mapping", "Image to Mesh",
     "Pillow EXIF orientation; precision-preserving Rasterio numeric decoding; scikit-image weighted grayscale, "
     "RGB average/lightness and channel selection; brightness, contrast, gamma, levels and mask-aware Gaussian filtering. "
     "Shared error-controlled heightfield triangulation, local manifold-link precision repair, authoritative placement, "
     "scale-aware mask contact handling, contour terraces and validated printable solids. "
     "No Blender or AI depth model is required."),
    ("Numeric DEM heightfields, mask-aware interpolation and contour terraces", "Raster DEM to Mesh",
     "GDAL/Rasterio reprojection; SciPy interpolation and Gaussian filtering; marching squares; "
     "boundary-preserving quadric simplification through PMP; double-precision manifold3d solid union; "
     "Garland and Heckbert (1995), Fast Polygonal Approximation of Terrains and Height Fields, via pydelatin/hmm; "
     "conforming edge refinement, source-sample error verification, link-safe precision repair and stitched terrain solids"),
    ("Quadric error metrics; incremental and adaptive remeshing", "Remesh/Decimate (PMP)",
     "Garland and Heckbert (1997); Botsch and Kobbelt (2004); Dunyach et al. (2013). "
     "Directional metric and barycentric dual-cell constructions adapted for native polygon line art"),
    ("Instant Field-Aligned Meshes / QuadriFlow", "Remesh/Decimate and FlowRep preparation",
     "Jakob, Tarini, Panozzo and Sorkine-Hornung (2015); Huang et al. (2018). "
     "Instant Meshes is bundled; QuadriFlow is provided by optional Blender"),
    ("Developability of Triangle Meshes", "Developable Surfaces (adapted original C++ solver)",
     "Stein, Grinspun and Crane, ACM Transactions on Graphics 37(4), 2018; GPL-2.0-or-later. "
     "Corresponding helper source: bundled licenses/developable-source.zip"),
    ("Stripe Patterns on Surfaces", "Surface Stripes (Geometry Central implementation)",
     "Knöppel, Crane, Pinkall and Schröder, ACM Transactions on Graphics 34(4), 2015; "
     "curve extraction and Geometry Central integration by David Jourdan"),
    ("Projective Dynamics", "Compress and Dilate (quasi-static, with pressure and contacts)",
     "Bouaziz, Martin, Liu, Kavan and Pauly, ACM Transactions on Graphics 33(4), 2014"),
    ("FlowRep: descriptive curve networks", "FlowRep Sketch",
     "Gori, Sheffer, Vining, Rosales, Carr and Ju, ACM Transactions on Graphics 36(4), 2017"),
    ("Distance and effective-area line simplification", "Line Simplify",
     "Douglas–Peucker (1973), via GEOS; Visvalingam–Whyatt (1993), monotone min-heap. "
     "Original-span distance, corner, gesture, signed-area and indexed swept-region constraints."),
]

OPTIONAL_PARTS = [   # installed separately (Setup › Components), never shipped with the app: (project, licence)
    ("Blender", "GPL-3.0-or-later"), ("PyTorch", "BSD-3-Clause"), ("Hugging Face Transformers", "Apache-2.0"),
    ("Depth models (MiDaS, DPT, Depth Anything)", "see Setup"),
]


def runtime_users(reg, key: str) -> tuple[list, list]:
    """The tools that need runtime `key`, and (tool, what it adds) for those that can use it."""
    need = [t for t in reg.ordered() if key in t.requires]
    can = [(t, what) for t in reg.ordered() for k, what in t.optional if k == key]
    return need, can


CLI_COMMANDS = [   # (command, what it does), after core/cli.build_parser
    ("list", "every tool with its id, version and input → output"),
    ("describe TOOL", "a tool's settings as --flags, with ranges, choices and help; its presets and output name"),
    ("run TOOL FILES… [options]", "run a tool on files, folders or wildcards (*.svg), one file after the other"),
    ("self-test", "run every tool on a generated sample: a quick check of the installation"),
    ("--help", "help for the app, or for one tool: run TOOL --help"),
    ("--version", "the toolkit's version"),
]

CLI_OPTIONS = [   # (option, what it does) for run
    ("--out-dir DIR", "write the outputs into this folder (default: next to each input)"),
    ("-o, --output FILE", "the output file, for a single input"),
    ("--preset NAME", "start from one of the tool's presets; any --setting given as well wins over it"),
    ("--SETTING VALUE", "any setting that describe lists, e.g. --spacing 2; a value that starts with a minus "
                        "needs = (--front=-y)"),
    ("--format EXT", "the output format of a mesh tool (stl, obj, ply …)"),
    ("--dry-run", "analyse and report, write nothing"),
    ("--force", "overwrite outputs that exist (otherwise those files are skipped)"),
    ("--suffix", "keep outputs that exist: write the new ones as name_YYYYMMDD-HHMMSS"),
    ("-q, --quiet", "no progress"),
]


def cli_program() -> str:
    """How to start the command line, never a path: "atlas-toolkit" for the installed app on every OS (the console companion
    on Windows, a package's command on Linux, the link docs/install/macos.md makes), an AppImage's own file, or the package
    when running from source."""
    if not frozen():
        return "python -m atlas_toolkit"
    return f'"./{Path(os.environ["APPIMAGE"]).name}"' if os.environ.get("APPIMAGE") else "atlas-toolkit"


def cli_examples(reg) -> str:
    """A few commands with real tool ids, chosen from the installed tools (nothing here names a tool)."""
    prog = cli_program()
    tools = reg.ordered()
    svg = next((t for t in tools if t.inputs.kind == "svg"), None)
    mesh = next((t for t in tools if t.inputs.kind == "mesh" and not t.requires), None)
    out = [f"{prog} list"]
    if svg:
        out += [f"{prog} describe {svg.id}",
                f"{prog} run {svg.id} drawing.svg --out-dir plots",
                f'{prog} run {svg.id} "{os.path.join("plots", "*.svg")}" --dry-run']
    if mesh:
        out.append(f"{prog} run {mesh.id} model.stl --preset-file my-look.atlaspreset --out-dir out")
    out.append(f"{prog} self-test")
    return "\n".join(out)


def setup_commands(rt) -> str:
    """A runtime's install commands for this kind of system: the Python launcher and venv Python filled in, and
    {home} the per-user runtime folder named by its variable (%LOCALAPPDATA%\\AtlasToolkit, ~/…), never this
    computer's path."""
    win = sys.platform == "win32"
    home = display_path(ml_runtime_dir().parent)
    if not win and home.startswith("~"):          # the commands quote it, and a quoted ~ does not expand
        home = "$HOME" + home[1:]
    mac = sys.platform == "darwin"       # PyTorch has no CUDA builds for macOS, and PyPI's build runs on it as it is
    return rt.commands.format(home=home, mkdir="mkdir" if win else "mkdir -p", cd="pushd" if win else "cd",
                              py="py -3.12" if win else "python3.12",
                              venv_py=r"ml-runtime\Scripts\python" if win else "ml-runtime/bin/python",
                              torch="torch" if mac else "torch --index-url https://download.pytorch.org/whl/cu130",
                              cpu="" if mac else "\n\n# CPU only: --index-url https://download.pytorch.org/whl/cpu")


def display_path(path) -> str:
    """Where a file is, said without this computer's particulars: inside the built app as "bundled with the app",
    under the app's folder as <app>/…, under a standard folder by its variable (%ProgramFiles%, %LOCALAPPDATA%,
    %USERPROFILE% … on Windows, ~ elsewhere). Anywhere else only its file name shows."""
    p = Path(path)
    bundle = getattr(sys, "_MEIPASS", None)
    win = sys.platform == "win32"
    roots = []
    if bundle:
        roots.append(("bundled with the app", Path(bundle)))
    roots.append(("<app>", app_home()))
    if win:
        for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData", "LOCALAPPDATA", "APPDATA",
                    "USERPROFILE"):
            if os.environ.get(var):
                roots.append((f"%{var}%", Path(os.environ[var])))
    else:
        roots.append(("~", Path.home()))
    sep = "\\" if win else "/"
    for name, root in sorted(roots, key=lambda r: len(str(r[1])), reverse=True):     # the most specific first
        try:
            rel = p.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            continue
        return name if name == "bundled with the app" else name + sep + sep.join(rel.parts)
    return "…" + sep + p.name


def folder_size(folder: Path) -> tuple[int, int]:
    """(bytes, files) under a folder; unreadable entries are skipped."""
    total = count = 0
    stack = [Path(folder)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        pass
        except OSError:
            pass
    return total, count


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def generic_locations(ctx) -> list[tuple[str, str, Path]]:
    """(what, where in general terms, the real folder for Open) for the folders the app keeps: the real path is
    only opened, never shown."""
    if portable():
        base, cache = f"{PORTABLE_DIR} (next to the app)", f"{PORTABLE_DIR}/cache"
    elif sys.platform == "win32":
        base, cache = "%APPDATA%\\AtlasToolkit", "%LOCALAPPDATA%\\AtlasToolkit\\cache"
    elif sys.platform == "darwin":
        base, cache = "~/Library/Application Support/AtlasToolkit", "~/Library/Caches/AtlasToolkit"
    else:
        base, cache = "~/.config/atlas-toolkit", "~/.cache/atlas-toolkit"
    sep = "\\" if sys.platform == "win32" and not portable() else "/"
    chosen = ctx.settings.get("workflow_dir")
    return [("Settings, presets, history", base, config_dir()),
            ("Workflows", "the folder you chose in Workflows" if chosen else f"{base}{sep}workflows",
             Path(chosen) if chosen else config_dir() / "workflows"),
            ("Plug-ins", f"{base}{sep}plugins", config_dir() / "plugins"),
            ("Cache", cache, cache_dir())]


# ---------------------------------------------------------------------- data
def changelog_text() -> str:
    """CHANGELOG.md: a package resource in the exe, the project's own file from source."""
    for p in (resource("CHANGELOG.md"), app_home() / "CHANGELOG.md"):
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return ""


def releases(text: str) -> list[tuple[str, str, str]]:
    """(version, date, markdown body) for each '## 1.2.3 (2026-10-28)' section, newest first."""
    out = []
    for m in re.finditer(r"^## +(\S+)(?: +\(([^)]*)\))?[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S):
        out.append((m.group(1), m.group(2) or "", m.group(3).strip()))
    return out


def portable() -> bool:
    return (app_home() / PORTABLE_DIR).is_dir()


def diagnostics(ctx) -> str:
    """Plain text to paste into a bug report."""
    reg = ctx.registry
    lines = [
        f"{APP_NAME} {__version__} ({'built' if frozen() else 'source'})",
        f"Python {platform.python_version()} · Qt {qVersion()} · PySide6 {pyside_version}",
        f"{platform.system()} {platform.release()} ({platform.version()}) · {platform.machine()}",
        f"Data: {'portable' if portable() else 'user profile'} · settings {config_dir()} · cache {cache_dir()}",
        f"Plug-in folders: {', '.join(reg.plugin_dirs) or 'none'}",
        *(f"{rt.label}: {runtimes.find(k) or 'not found'}" for k, rt in runtimes.RUNTIMES.items()),
        f"Tools ({len(reg.tools)}): " + ", ".join(f"{t.id} {t.version}" for t in reg.ordered()),
    ]
    if reg.broken:
        lines.append("Broken: " + "; ".join(f"{b.name}: {b.error.splitlines()[0] if b.error else ''}"
                                            for b in reg.broken))
    return "\n".join(lines)


# ---------------------------------------------------------------------- small parts
def _scroll(inner: QWidget) -> QScrollArea:
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    s.setWidget(inner)
    return s


def _page(margins=(28, 22, 28, 24), spacing=16) -> tuple[QWidget, QVBoxLayout]:
    w = QWidget(objectName="transparent")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return w, lay


def _text(text: str, cls: str = "muted", wrap: bool = True, select: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("class", cls)
    lab.setWordWrap(wrap)
    if select:
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lab


def _card(title: str = "") -> tuple[QFrame, QVBoxLayout]:
    f = QFrame(objectName="card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(20, 16, 20, 18)
    lay.setSpacing(10)
    if title:
        lay.addWidget(label(title))
    return f, lay


def _logo(px: int) -> QLabel:
    lab = QLabel()
    lab.setPixmap(make_logo(px * 2).scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    lab.setFixedSize(px, px)
    return lab


def _brand_head(title: str, sub: str) -> QWidget:
    """Logo, title and a line under it: the top of the Changelog and Credits tabs."""
    w = QWidget(objectName="transparent")
    hl = QHBoxLayout(w)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(14)
    hl.addWidget(_logo(44), 0, Qt.AlignTop)
    col = QVBoxLayout()
    col.setSpacing(2)
    t = _text(title, "h2", wrap=False)
    col.addWidget(t)
    col.addWidget(_text(sub, "hint"))
    hl.addLayout(col, 1)
    return w


def _tile(name: str, theme, px: int = 38) -> QLabel:
    """A tool's icon on a rounded tile, as on the sidebar but larger."""
    lab = QLabel(objectName="iconTile")
    lab.setFixedSize(px, px)
    lab.setAlignment(Qt.AlignCenter)
    lab.setPixmap(icon(name, theme["highlight"], 20).pixmap(20, 20))
    return lab


def _kbd(text: str) -> QLabel:
    k = QLabel(text, objectName="kbd")
    k.setFont(mono(11.5))
    k.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return k


# ---------------------------------------------------------------------- the window
class AboutDialog(QDialog):
    TABS = ("about", "tools", "cli", "shortcuts", "changelog", "credits")

    def __init__(self, ctx, shortcuts: list, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.theme = ctx.theme
        self.shortcuts = shortcuts
        self.setObjectName("aboutDialog")
        self.setWindowTitle(f"About {APP_NAME}")
        self.setWindowIcon(logo_icon())
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowMinMaxButtonsHint
                            | Qt.WindowCloseButtonHint)
        self.setSizeGripEnabled(True)
        self.setMinimumSize(640, 460)
        self.resize(940, 680)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._header())
        self.tabs = QTabWidget(objectName="aboutTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._about_tab(), icon("info", self.theme["text-3"], 16), "About")
        self.tabs.addTab(self._tools_tab(), icon("process", self.theme["text-3"], 16), "Tools")
        self.tabs.addTab(self._cli_tab(), icon("code", self.theme["text-3"], 16), "Command line")
        self.tabs.addTab(self._shortcuts_tab(), icon("grid", self.theme["text-3"], 16), "Shortcuts")
        self.tabs.addTab(self._changelog_tab(), logo_icon(), "Changelog")
        self.tabs.addTab(self._credits_tab(), logo_icon(), "Credits")
        self.tabs.setIconSize(QSize(16, 16))
        self.tabs.tabBar().setDrawBase(False)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(self._footer())

    def show_tab(self, key: str) -> None:
        if key in self.TABS:
            self.tabs.setCurrentIndex(self.TABS.index(key))

    # ------------------------------------------------------------------ frame
    def _header(self) -> QWidget:
        bar = QFrame(objectName="aboutHead")
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(28, 20, 24, 18)
        hl.setSpacing(16)
        hl.addWidget(_logo(56))
        col = QVBoxLayout()
        col.setSpacing(3)
        name = QLabel("ATLAS TOOLKIT")
        f = sans(17, 600)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 17 * 0.16)
        name.setFont(f)
        col.addWidget(name)
        col.addWidget(_text(TAGLINE, "summary"))       # wraps, so a narrow window does not cut it off
        chips = QHBoxLayout()
        chips.setSpacing(6)
        for text, tone in ((f"v{__version__}", "accent"), (f"{len(self.ctx.registry.tools)} tools", ""),
                           ("built" if frozen() else "source", ""), ("portable" if portable() else "", "")):
            if text:
                chips.addWidget(badge(text, tone))
        chips.addStretch(1)
        col.addLayout(chips)
        hl.addLayout(col, 1)
        self.btn_copy = QPushButton("Copy diagnostics")
        self.btn_copy.setIcon(icon("copy", self.theme["text-2"], 16))
        self.btn_copy.setToolTip("Version, system, folders and tools as plain text, for a bug report")
        self.btn_copy.clicked.connect(self.copy_diagnostics)
        hl.addWidget(self.btn_copy, 0, Qt.AlignTop)
        return bar

    def _footer(self) -> QWidget:
        bar = QFrame(objectName="aboutFoot")
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(28, 10, 24, 12)
        foot = _text(f"{COPYRIGHT} · {LICENSE_ID} · {APP_NAME} {__version__}", "hint")    # wraps when narrow
        foot.setFont(mono(11))
        hl.addWidget(foot, 1)
        close = QPushButton("Close")
        close.setProperty("variant", "primary")
        close.setDefault(True)
        close.clicked.connect(self.close)
        hl.addWidget(close)
        return bar

    def copy_diagnostics(self) -> None:
        QGuiApplication.clipboard().setText(diagnostics(self.ctx))
        if callable(getattr(self.ctx, "toast", None)):
            self.ctx.toast("Diagnostics copied to the clipboard.", "success")
        self.btn_copy.setText("Copied")

    # ------------------------------------------------------------------ About
    def _about_tab(self) -> QWidget:
        w, lay = _page()
        lay.addWidget(_text(
            "A desktop app for pen-plotter drawing: clean and cut SVGs, draw 3D models as plotter lines "
            "(contours, hatching, stipple, hidden lines removed), turn photos into hatching, and reshape meshes "
            "(replicate, slice, shatter, twist, compress, dilate, melt …) before drawing them. Outputs are plain SVG in millimetres "
            "and standard mesh files, ready for any plotter software. Chain tools in Workflows, or run any of "
            "them from the command line (see <b>Command line</b>).", "summary"))
        lay.addWidget(hint("Each output records the tool and settings that made it: drop a drawing back on its "
                           "tool to restore them. Most tools need nothing else; a few can use optional "
                           "components (see <b>Setup › Components</b>, the gear button)."))

        card, cl = _card("Version")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        rows = [("Version", f"{APP_NAME} {__version__} · {'built app' if frozen() else 'from source'}"),
                ("Built on", f"Python {platform.python_version()} · Qt {qVersion()} · PySide6 {pyside_version}")]
        rows += [(rt.label, f"found · {display_path(runtimes.find(k))}" if runtimes.find(k)
                  else "not installed (optional, see Setup)") for k, rt in runtimes.RUNTIMES.items()]
        for r, (k, v) in enumerate(rows):
            grid.addWidget(_text(k, "muted", wrap=False), r, 0)
            grid.addWidget(_text(v, "mono", select=True), r, 1)
        grid.setColumnStretch(1, 1)
        cl.addLayout(grid)
        lay.addWidget(card)
        lay.addWidget(self._licence_card())
        lay.addStretch(1)
        return _scroll(w)

    def _licence_card(self) -> QWidget:
        card, cl = _card("Licence and support")
        cl.addWidget(_text(
            f"{APP_NAME} is free software under the GNU General Public License, version 3 or later "
            f"({LICENSE_ID}): you may use, study, share and change it. The files it writes from your input are yours. "
            "Bundled components keep their own licences (see <b>Credits</b>). Preset packs are separate and carry "
            "their own licence.", "muted"))
        cl.addWidget(_text(
            f"{APP_NAME} is made independently by {STUDIO}. Donations, preset packs and early-access builds keep it going.",
            "muted"))
        row = QHBoxLayout()
        row.setSpacing(8)
        for text, url in (("Support on Ko-fi", SUPPORT_URL), ("atlas-toolkit.org", HOMEPAGE), ("Source code", SOURCE_URL)):
            b = QPushButton(text)
            b.setToolTip(url)
            b.clicked.connect(lambda _=False, u=url: self._open_url(u))
            row.addWidget(b)
        row.addStretch(1)
        cl.addLayout(row)
        return card

    @staticmethod
    def _open_url(url: str) -> None:
        from ..core.paths import open_url
        open_url(url)

    # ------------------------------------------------------------------ Tools
    def _tools_tab(self) -> QWidget:
        outer = QWidget(objectName="transparent")
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(28, 18, 28, 0)
        ol.setSpacing(0)
        self.tool_filter = QLineEdit()
        self.tool_filter.setPlaceholderText("Filter tools")
        self.tool_filter.setClearButtonEnabled(True)
        self.tool_filter.setMaximumWidth(320)
        ol.addWidget(self.tool_filter)
        w, lay = _page((0, 14, 0, 24), 4)
        self.tool_rows: list[tuple[QWidget, str]] = []
        self.tool_heads: list[tuple[QWidget, list]] = []
        for cat, tools in self.ctx.registry.by_category():
            head = QWidget(objectName="transparent")
            hl = QHBoxLayout(head)
            hl.setContentsMargins(0, 12, 0, 4)
            t = QLabel(cat.upper(), objectName="navGroupTitle")
            f = sans(12, 600)
            f.setLetterSpacing(QFont.AbsoluteSpacing, 1.1)
            t.setFont(f)
            hl.addWidget(t)
            line = hairline()
            hl.addWidget(line, 1)
            hl.setAlignment(line, Qt.AlignVCenter)
            lay.addWidget(head)
            rows = []
            for tool in tools:
                row = self._tool_row(tool)
                lay.addWidget(row)
                hay = f"{tool.name} {tool.id} {tool.summary} {cat} {tool.io_badge}".lower()
                self.tool_rows.append((row, hay))
                rows.append(row)
            self.tool_heads.append((head, rows))
        lay.addStretch(1)
        ol.addWidget(_scroll(w), 1)
        self.tool_filter.textChanged.connect(self._filter_tools)
        return outer

    def _tool_row(self, tool) -> QWidget:
        row = QFrame(objectName="toolRow")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(12, 10, 14, 10)
        hl.setSpacing(14)
        hl.addWidget(_tile(tool.icon, self.theme), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        name = _text(tool.name, "h3", wrap=False)
        top.addWidget(name)
        top.addWidget(badge(f"v{tool.version}"))
        top.addWidget(badge(tool.io_badge, "accent"))
        missing = {r.key for r in self.ctx.lacking(tool)}
        for rt in tool.requires:
            name = runtimes.RUNTIMES[rt].label if rt in runtimes.RUNTIMES else rt
            b = badge(f"needs {name}" + (" · not found" if rt in missing else ""), "warning" if rt in missing else "")
            b.setToolTip(self.ctx.needs_text(tool) if rt in missing else f"{name} was found: the tool can run.")
            top.addWidget(b)
        row.setProperty("unavailable", "true" if missing else "false")
        for rt, what in tool.optional:
            b = badge(f"optional {runtimes.RUNTIMES[rt].label}")
            b.setToolTip(what)
            top.addWidget(b)
        top.addStretch(1)
        col.addLayout(top)
        col.addWidget(_text(tool.summary, "muted"))
        hl.addLayout(col, 1)
        return row

    def _filter_tools(self, text: str) -> None:
        words = text.lower().split()
        for row, hay in self.tool_rows:
            row.setVisible(all(w in hay for w in words))
        for head, rows in self.tool_heads:
            head.setVisible(any(not r.isHidden() for r in rows))

    # ------------------------------------------------------------------ Command line
    def _cli_tab(self) -> QWidget:
        w, lay = _page()
        lay.addWidget(_text(
            "Every tool also runs from a terminal, with the same settings as its window: for batches, scripts and "
            "other software. The commands come from the tools' own definitions, so they cover every tool, "
            "plug-ins included, and stay in step with the app.", "summary"))
        program = f"<span style='font-family:Geist Mono'>{cli_program()}</span>"
        if not frozen():
            how = f"Start it as {program}, in the project's environment."
        elif sys.platform == "win32":
            how = ("Start it as <b>atlas-toolkit</b>: <b>atlas-toolkit.exe</b> is the console companion that sits "
                   "next to <b>Atlas Toolkit.exe</b>. The installer can add it to PATH; otherwise open a terminal in "
                   "that folder, and in PowerShell type <b>.\\atlas-toolkit</b>. The terminal waits for it and gets the "
                   "exit code. Atlas Toolkit.exe takes the same commands, but the terminal does not wait for it.")
        elif sys.platform == "darwin":
            how = (f"Start it as {program}, once you have made a link to the program inside the app, as the "
                   "installation guide shows: <span style='font-family:Geist Mono'>sudo ln -sf \"/Applications/Atlas "
                   "Toolkit.app/Contents/MacOS/Atlas Toolkit\" /usr/local/bin/atlas-toolkit</span>")
        elif os.environ.get("APPIMAGE"):
            how = f"Start it as {program}, in the folder that holds the AppImage."
        else:
            how = f"Start it as {program}. It needs no display, so it also works on a server or over SSH."
        prog = _text(how, "hint", select=True)
        lay.addWidget(prog)
        for title, rows in (("Commands", CLI_COMMANDS), ("Options of run", CLI_OPTIONS)):
            card, cl = _card(title)
            grid = QGridLayout()
            grid.setHorizontalSpacing(22)
            grid.setVerticalSpacing(0)
            r = 0
            for cmd, what in rows:
                if r:
                    grid.addWidget(hairline(), r, 0, 1, 2)
                    r += 1
                cell = QWidget(objectName="transparent")
                hl = QHBoxLayout(cell)
                hl.setContentsMargins(0, 7, 0, 7)
                hl.addWidget(_kbd(cmd))
                hl.addStretch(1)
                grid.addWidget(cell, r, 0)
                grid.addWidget(_text(what, "muted"), r, 1)
                r += 1
            grid.setColumnStretch(1, 1)
            cl.addLayout(grid)
            lay.addWidget(card)

        card, cl = _card("Examples")
        text = cli_examples(self.ctx.registry)
        box = QPlainTextEdit(text, readOnly=True, objectName="commands")
        box.setFont(mono(11.5))
        box.setLineWrapMode(QPlainTextEdit.NoWrap)
        box.setFixedHeight(box.fontMetrics().lineSpacing() * (text.count("\n") + 1) + 30)
        cl.addWidget(box)
        copy = QPushButton("Copy examples")
        copy.setIcon(icon("copy", self.theme["text-2"], 14))
        copy.clicked.connect(lambda _=False, t=text, b=copy: (QGuiApplication.clipboard().setText(t),
                                                              b.setText("Copied")))
        cl.addWidget(copy, 0, Qt.AlignLeft)
        lay.addWidget(card)

        card, cl = _card("Good to know")
        for note in (
                "A run starts from the tool's defaults (or <b>--preset</b>), not from the settings last used in "
                "the window, so the same command always gives the same drawing.",
                "Progress goes to stderr; each file's report and its outputs (lines starting with "
                "<span style='font-family:Geist Mono'>-&gt;</span>) to stdout, for scripts to collect.",
                "Exit code 0 when every file was done, 1 when one failed, 2 for a mistake in the command or a "
                "required component that is missing (see <b>Setup</b>).",
                "Runs are recorded in <b>Activity</b>, like runs from the window.",
                "Workflows run from the app only for now; there is no command for them yet."):
            row = QHBoxLayout()
            row.setSpacing(10)
            dot = QLabel("·", objectName="noteDot")
            dot.setFixedWidth(10)
            row.addWidget(dot, 0, Qt.AlignTop)
            row.addWidget(_text(note, "muted"), 1)
            cl.addLayout(row)
        lay.addWidget(card)
        lay.addStretch(1)
        return _scroll(w)

    # ------------------------------------------------------------------ Shortcuts
    def _shortcuts_tab(self) -> QWidget:
        w, lay = _page()
        card, cl = _card("Keyboard")
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(0)
        r = 0
        for keys, what in self.shortcuts:
            if r:
                grid.addWidget(hairline(), r, 0, 1, 2)
                r += 1
            chips = QHBoxLayout()
            chips.setSpacing(5)
            chips.setContentsMargins(0, 8, 0, 8)
            for i, part in enumerate(re.split(r"\s+(or|·|/)\s+", keys)):
                if i % 2:
                    chips.addWidget(_text(part, "hint", wrap=False))
                else:
                    chips.addWidget(_kbd(part.strip()))
            chips.addStretch(1)
            cell = QWidget(objectName="transparent")
            cell.setLayout(chips)
            grid.addWidget(cell, r, 0)
            grid.addWidget(_text(what, "muted"), r, 1)
            r += 1
        grid.setColumnStretch(1, 1)
        cl.addLayout(grid)
        lay.addWidget(card)
        lay.addWidget(hint(shown("The search box (<b>/</b> or <b>Ctrl+K</b>) also runs every action by name: open a tool, "
                                 "switch theme, show the log, open the settings or plug-ins folder, and more.")))
        lay.addStretch(1)
        return _scroll(w)

    # ------------------------------------------------------------------ Changelog
    def _changelog_tab(self) -> QWidget:
        w, lay = _page((28, 22, 28, 20), 14)
        rel = releases(changelog_text())
        lay.addWidget(_brand_head("What's new",
                                  f"{len(rel)} releases · this is {__version__}" if rel else
                                  "The changelog is not available in this build."))
        split = QSplitter(Qt.Horizontal, objectName="releaseSplit")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        self.releases = QListWidget(objectName="releaseList")
        self.releases.setMinimumWidth(150)
        self.releases.setMaximumWidth(220)
        self.notes = QTextBrowser(objectName="releaseNotes")
        self.notes.setOpenExternalLinks(True)
        for i, (version, date, body) in enumerate(rel):
            it = QListWidgetItem()
            it.setData(Qt.UserRole, (version, date, body))
            it.setToolTip(f"{version} · {date}" if date else version)
            cell = QWidget(objectName="transparent")
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(10, 7, 10, 7)
            cl.setSpacing(1)
            v = QLabel(version + ("   latest" if i == 0 else ""))
            v.setFont(mono(12.5, QFont.Medium))
            v.setProperty("class", "h3")
            cl.addWidget(v)
            d = _text(date, "hint", wrap=False)
            d.setFont(mono(10.5))
            cl.addWidget(d)
            it.setSizeHint(QSize(0, 48))
            self.releases.addItem(it)
            self.releases.setItemWidget(it, cell)
        self.releases.currentItemChanged.connect(self._show_release)
        split.addWidget(self.releases)
        split.addWidget(self.notes)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)
        if rel:
            self.releases.setCurrentRow(0)
        return w

    def _show_release(self, it, _prev=None) -> None:
        if it is None:
            return
        version, date, body = it.data(Qt.UserRole)
        md = f"## {version}\n\n" + (f"*{date}*\n\n" if date else "") + body
        self.notes.setMarkdown(md)
        self.notes.verticalScrollBar().setValue(0)

    # ------------------------------------------------------------------ Credits
    def _credits_tab(self) -> QWidget:
        w, lay = _page()
        lay.addWidget(_brand_head(f"{APP_NAME}", f"Made by {STUDIO} with Claude Code and ChatGPT"))
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, (name, role, detail) in enumerate(CREDITS):
            card, cl = _card()
            cl.setSpacing(4)
            cl.addWidget(_text(name, "h2", wrap=False))
            r = _text(role.upper(), "label")
            r.setFont(label_font(10))
            cl.addWidget(r)
            cl.addSpacing(4)
            cl.addWidget(_text(detail, "muted"))
            cl.addStretch(1)
            grid.addWidget(card, 0, i)
            grid.setColumnStretch(i, 1)           # equal shares, so the cards narrow together with the window
        lay.addLayout(grid)
        card, cl = _card("Built with")
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(6)
        for r, (proj, lic) in enumerate(BUILT_WITH):      # one part a row; both columns wrap, so it narrows
            g.addWidget(_text(proj, "muted"), r, 0, Qt.AlignTop)
            g.addWidget(_text(lic, "mono"), r, 1, Qt.AlignTop)
        g.setColumnStretch(0, 2)
        g.setColumnStretch(1, 3)
        cl.addLayout(g)
        lay.addWidget(card)
        card, cl = _card("Research")
        for method, tool, ref in RESEARCH:
            cl.addWidget(_text(f"<b>{method}</b> ({tool}): {ref}.",
                               "muted"))
        lay.addWidget(card)
        card, cl = _card("Optional, installed separately")
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(6)
        for r, (proj, lic) in enumerate(OPTIONAL_PARTS):
            g.addWidget(_text(proj, "muted"), r, 0, Qt.AlignTop)
            g.addWidget(_text(lic, "mono"), r, 1, Qt.AlignTop)
        g.setColumnStretch(0, 2)
        g.setColumnStretch(1, 3)
        cl.addLayout(g)
        lay.addWidget(card)
        lay.addStretch(1)
        return _scroll(w)
