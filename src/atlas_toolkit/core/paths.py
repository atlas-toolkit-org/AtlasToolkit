"""
Where the app keeps its files, per platform, without Qt (the worker process and the CLI use it too).

Portable mode: if a folder named `AtlasToolkit-data` sits next to the exe (or the source checkout), everything
(settings, presets, history, plug-ins, cache) lives in it instead of the user profile. The ATLAS_TOOLKIT_HOME
variable does the same with any folder, on any OS: tests and frozen checks use it to stay out of real profiles.
"""
from __future__ import annotations

import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

APP_DIR_NAME = "AtlasToolkit"
PORTABLE_DIR = "AtlasToolkit-data"
HOME_ENV = "ATLAS_TOOLKIT_HOME"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_home() -> Path:
    """The folder the app sits in: the exe's, or the project folder when running from source. A macOS bundle and an
    AppImage count as one file, so this is the folder beside them: nothing portable may have to live inside them."""
    if frozen():
        if os.environ.get("APPIMAGE"):
            return Path(os.environ["APPIMAGE"]).resolve().parent
        exe = Path(sys.executable).resolve()
        bundle = next((p for p in exe.parents if p.suffix == ".app"), None) if sys.platform == "darwin" else None
        return bundle.parent if bundle else exe.parent
    return Path(__file__).resolve().parents[3]


def resource(*parts: str) -> Path:
    """A file shipped inside the package (fonts, tokens, icons): works from source and from the one-file exe."""
    return Path(__file__).resolve().parents[1].joinpath(*parts)


@lru_cache(maxsize=None)
def _base(kind: str) -> Path:
    chosen = os.environ.get(HOME_ENV)
    portable = Path(chosen) if chosen else app_home() / PORTABLE_DIR
    if chosen or portable.is_dir():
        return portable / kind if kind != "config" else portable
    home = Path.home()
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return {"config": roaming / APP_DIR_NAME, "data": roaming / APP_DIR_NAME,
                "cache": local / APP_DIR_NAME / "cache", "runtime": local / APP_DIR_NAME}[kind]
    if sys.platform == "darwin":
        sup = home / "Library" / "Application Support" / APP_DIR_NAME
        return {"config": sup, "data": sup, "cache": home / "Library" / "Caches" / APP_DIR_NAME,
                "runtime": sup}[kind]
    # The XDG spec treats an empty value as unset.
    xdg = {"config": os.environ.get("XDG_CONFIG_HOME") or home / ".config",
           "data": os.environ.get("XDG_DATA_HOME") or home / ".local" / "share",
           "cache": os.environ.get("XDG_CACHE_HOME") or home / ".cache"}
    return Path(xdg["data" if kind == "runtime" else kind]) / "atlas-toolkit"


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    return _ensure(_base("config"))


def data_dir() -> Path:
    return _ensure(_base("data"))


def cache_dir() -> Path:
    return _ensure(_base("cache"))


def ml_runtime_dir() -> Path:
    """Where the depth environment's installer (geom/depth_runtime.py) puts it: a large, machine-specific environment, so
    never in the roaming profile (%LOCALAPPDATA%\\AtlasToolkit\\ml-runtime on Windows). Not created here."""
    return _base("runtime") / "ml-runtime"


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


def program_inside(path: str) -> str:
    """The program itself when `path` names a macOS bundle: file dialogs there pick the whole Blender.app."""
    bundle = Path(path)
    programs = bundle / "Contents" / "MacOS"
    if bundle.suffix != ".app" or not programs.is_dir():
        return path
    named = programs / bundle.stem
    if named.is_file():
        return str(named)
    found = sorted(p for p in programs.iterdir() if p.is_file())
    return str(found[0]) if found else path


def venv_python(venv: Path) -> Path:
    """The interpreter inside a virtual environment, on this platform."""
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _child_env() -> dict:
    # Not imported at the top: this module stays free of the app's other imports.
    from .procs import child_env
    return child_env()


def reveal(path: Path) -> None:
    """Show a file selected in the system file manager (or open a folder)."""
    path = Path(path)
    if not path.exists():
        path = path.parent
    if sys.platform == "win32":
        if path.is_file():
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            subprocess.Popen(f'explorer "{path}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)], env=_child_env())
    else:
        subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)], env=_child_env())


def open_file(path: Path) -> None:
    """Open a file with its default application."""
    open_url(str(path))


def open_url(target: str) -> None:
    """Open a web address or a file with the system's default application. Always through here, never QDesktopServices:
    on Linux that starts xdg-open and the browser with a frozen app's library path (procs.child_env)."""
    if sys.platform == "win32":
        os.startfile(target)   # noqa: S606 - user-chosen local file, or an address of ours
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target], env=_child_env())
    else:
        subprocess.Popen(["xdg-open", target], env=_child_env())
