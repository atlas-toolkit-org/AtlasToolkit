# -*- mode: python -*-
"""
The PyInstaller recipe for every OS. scripts/build.py runs it, after drawing the icon and the Windows version resource:

    pyinstaller packaging/atlas_toolkit.spec --distpath DIR --workpath DIR -- --icon FILE [--onefile] [--splash FILE] [--version-file FILE]

The result, under the dist path: Windows, "Atlas Toolkit.exe" (--onefile) or a folder "Atlas Toolkit/"; macOS,
"Atlas Toolkit.app"; Linux, a folder "atlas-toolkit/" with the program of the same name.
"""
import argparse
import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

parser = argparse.ArgumentParser()
parser.add_argument("--icon")
parser.add_argument("--onefile", action="store_true")
parser.add_argument("--splash")
parser.add_argument("--version-file")
options = parser.parse_args()

ROOT = Path(SPECPATH).parent          # noqa: F821 - PyInstaller provides SPECPATH and the classes used below
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
from atlas_toolkit.version import APP_ID, __version__  # noqa: E402

WINDOWS, MACOS = sys.platform == "win32", sys.platform == "darwin"
LINUX = not WINDOWS and not MACOS
NAME = "Atlas Toolkit"
PROGRAM = APP_ID if LINUX else NAME   # a name with no space where people type it on the command line

# Packages with native parts or lazy imports that PyInstaller cannot see by analysis.
COLLECT_ALL = ["manifold3d", "rtree", "mapbox_earcut", "shapely", "rasterio", "pydelatin",
               "pynanoinstantmeshes",    # FlowRep Sketch: Instant Meshes quad remeshing (native)
               "fast_simplification",    # Plotter Vision's decimation, imported lazily by trimesh
               "igl"]                    # Porous Forms: signed distance (libigl, imported lazily)
if importlib.util.find_spec("embreex"):  # optional: fast ray casting, with a fallback where it does not install
    COLLECT_ALL.append("embreex")
COLLECT_SUBMODULES = ["atlas_toolkit", "flowrep3d", "trimesh", "pyqtgraph.opengl", "scipy.sparse.csgraph", "scipy.spatial"]
HIDDEN = ["networkx", "OpenGL.arrays.numpymodule",
          # Photo Hatching imports these lazily (inside functions); naming them makes sure their hooks run
          "skimage.color", "skimage.exposure", "skimage.feature", "skimage.measure", "skimage.morphology",
          "skimage.segmentation"]
# PyOpenGL imports its platform by name at run time. Linux has two: GLX under X11, EGL under Wayland (ui/app.py chooses).
HIDDEN += (["OpenGL.platform.win32"] if WINDOWS else ["OpenGL.platform.darwin"] if MACOS else
           ["OpenGL.platform.glx", "OpenGL.platform.egl"])
EXCLUDE = ["matplotlib", "IPython", "pytest", "PySide6.QtWebEngineCore", "PySide6.QtQuick", "PySide6.QtQml",
           "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf"]
if not options.splash:                # Tk only serves the splash screen
    EXCLUDE += ["tkinter", "_tkinter"]
# Files run by another interpreter, so they must stay real files in the bundle: (source, folder in the bundle)
DATA = [(SRC / "atlas_toolkit" / "ml" / "depth_server.py", "atlas_toolkit/ml"),
        (ROOT / "CHANGELOG.md", "atlas_toolkit"),        # the About window's Changelog tab
        (ROOT / "LICENSE", "atlas_toolkit/licenses"),    # inside the bundle, so every format has them, a dragged .app included
        (ROOT / "COPYRIGHT.md", "atlas_toolkit/licenses"),
        (ROOT / "THIRD_PARTY_NOTICES.md", "atlas_toolkit/licenses"),
        (SRC / "atlas_toolkit/tools/mesh_texture/assets", "atlas_toolkit/tools/mesh_texture/assets")]
# run by Blender's own Python (Melt), so they ship as real files next to the package (geom/blender.server_script)
DATA += [(SRC / "atlas_toolkit" / "blender" / f, "atlas_toolkit/blender")
         for f in ("__init__.py", "server.py", "mesh_io.py", "melt_gn.py", "wave_gn.py", "remesh.py")]
# Linux's graphics stack loads these from the system, and PyInstaller's library path would put an older copy from the build
# system in front: Mesa then cannot load, which aborts the app or blanks its 3D views (docs/dev/multi-os-port.md).
# Every supported system has them. Other libraries stay bundled: some sonames differ between distributions.
FROM_THE_SYSTEM = {"libstdc++.so.6", "libgcc_s.so.1", "libz.so.1", "libzstd.so.1", "libexpat.so.1", "liblzma.so.5"} if LINUX else set()

datas = [(str(src), dst) for src, dst in DATA] + collect_data_files("atlas_toolkit")
binaries = []
hidden = list(HIDDEN)
for package in COLLECT_ALL:
    d, b, h = collect_all(package)
    datas, binaries, hidden = datas + d, binaries + b, hidden + h
# libigl's wheel also carries modules under other licences, and Atlas imports none of them: CGAL (GPL-3.0), TetGen
# (AGPL-3.0) and Shewchuk's Triangle (not open source, no redistribution in products). Keep them out of every bundle
# (THIRD_PARTY_NOTICES.md; scripts/audit_bundle.py checks).
LEFT_OUT = ("igl.copyleft", "igl.triangle")
EXCLUDE += list(LEFT_OUT)
def _left_out(path):
    p = str(path).replace("\\", "/")
    return any(f"/{m.replace('.', '/')}/" in f"/{p}/" for m in LEFT_OUT)
datas = [e for e in datas if not _left_out(e[0])]
binaries = [e for e in binaries if not _left_out(e[0])]
hidden = [m for m in hidden if not m.startswith(LEFT_OUT)]
for package in COLLECT_SUBMODULES:
    hidden += collect_submodules(package)
if WINDOWS:     # The Windows Rasterio wheel loads its delvewheel DLL directory beside the package: keep that layout.
    raster_package = Path(importlib.util.find_spec("rasterio").origin).parent
    binaries += [(str(dll), "rasterio.libs") for dll in (raster_package.parent / "rasterio.libs").glob("*.dll")]
# Native one-shot geometry helpers are executable files, not Python extensions.
binaries += [(str(p), "atlas_toolkit/bin") for p in (SRC / "atlas_toolkit" / "bin").glob("atlas-*-core*") if p.is_file()]

a = Analysis([str(ROOT / "scripts" / "launcher.py")], pathex=[str(SRC)], binaries=binaries, datas=datas,      # noqa: F821
             hiddenimports=hidden, excludes=EXCLUDE)
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name not in FROM_THE_SYSTEM]
pyz = PYZ(a.pure)                     # noqa: F821
splash = Splash(options.splash, binaries=a.binaries, datas=a.datas) if options.splash else None      # noqa: F821
extras = [splash, splash.binaries] if splash else []
common = dict(name=PROGRAM, console=False, icon=options.icon, version=options.version_file)

if options.onefile:
    EXE(pyz, a.scripts, a.binaries, a.datas, *extras, **common)     # noqa: F821
else:
    exe = EXE(pyz, a.scripts, exclude_binaries=True, **common)      # noqa: F821
    folder = COLLECT(exe, a.binaries, a.datas, name=PROGRAM)        # noqa: F821
    if MACOS:
        BUNDLE(folder, name=f"{NAME}.app", icon=options.icon, bundle_identifier="org.atlastoolkit.app",      # noqa: F821
               version=__version__, info_plist={
                   "CFBundleDisplayName": NAME,
                   "CFBundleShortVersionString": __version__,
                   "LSMinimumSystemVersion": "15.0",
                   "NSHighResolutionCapable": True,
                   "LSApplicationCategoryType": "public.app-category.graphics-design",
                   "UTExportedTypeDeclarations": [{
                       "UTTypeIdentifier": "org.atlastoolkit.workflow", "UTTypeDescription": "Atlas Toolkit workflow",
                       "UTTypeConformsTo": ["public.json"],
                       "UTTypeTagSpecification": {"public.filename-extension": ["atlasflow"]}}, {
                       "UTTypeIdentifier": "org.atlastoolkit.preset", "UTTypeDescription": "Atlas Toolkit presets",
                       "UTTypeConformsTo": ["public.json"],
                       "UTTypeTagSpecification": {"public.filename-extension": ["atlaspreset"]}}, {
                       "UTTypeIdentifier": "org.atlastoolkit.pack", "UTTypeDescription": "Atlas Toolkit preset pack",
                       "UTTypeConformsTo": ["public.zip-archive"],
                       "UTTypeTagSpecification": {"public.filename-extension": ["atlaspack"]}}],
                   "CFBundleDocumentTypes": [
                       {"CFBundleTypeName": "Atlas Toolkit workflow", "CFBundleTypeRole": "Editor",
                        "LSHandlerRank": "Owner", "LSItemContentTypes": ["org.atlastoolkit.workflow"]},
                       {"CFBundleTypeName": "Atlas Toolkit presets", "CFBundleTypeRole": "Viewer",
                        "LSHandlerRank": "Owner",
                        "LSItemContentTypes": ["org.atlastoolkit.preset", "org.atlastoolkit.pack"]},
                       {"CFBundleTypeName": "Drawing, model or image", "CFBundleTypeRole": "Viewer",
                        "LSHandlerRank": "Alternate",
                        "CFBundleTypeExtensions": ["svg", "stl", "obj", "ply", "png", "jpg", "jpeg", "tif", "tiff"]}]})
