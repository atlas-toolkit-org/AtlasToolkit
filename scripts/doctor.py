#!/usr/bin/env python3
"""
doctor.py - Is this machine ready to develop and build Atlas Toolkit? Checks what the project needs on this OS and prints
the command that closes each gap. Changes nothing. Standard library only, so it runs before anything is installed.

    python scripts/doctor.py

Exit code 0 when everything required is there. Items marked "optional" only matter for what they name.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS, MACOS = sys.platform == "win32", sys.platform == "darwin"
# The libraries Qt's xcb and wayland plugins load from the system on Linux: Debian/Ubuntu package, RPM package.
LINUX_LIBS = {"libEGL.so.1": ("libegl1", "mesa-libEGL"), "libGL.so.1": ("libgl1", "mesa-libGL"),
              "libOpenGL.so.0": ("libopengl0", "libglvnd-opengl"), "libfontconfig.so.1": ("libfontconfig1", "fontconfig"),
              "libdbus-1.so.3": ("libdbus-1-3", "dbus-libs"), "libxkbcommon.so.0": ("libxkbcommon0", "libxkbcommon"),
              "libxkbcommon-x11.so.0": ("libxkbcommon-x11-0", "libxkbcommon-x11"),
              "libxcb-cursor.so.0": ("libxcb-cursor0", "xcb-util-cursor"), "libxcb-icccm.so.4": ("libxcb-icccm4", "xcb-util-wm"),
              "libxcb-image.so.0": ("libxcb-image0", "xcb-util-image"), "libxcb-keysyms.so.1": ("libxcb-keysyms1", "xcb-util-keysyms"),
              "libxcb-render-util.so.0": ("libxcb-render-util0", "xcb-util-renderutil"),
              "libwayland-cursor.so.0": ("libwayland-cursor0", "libwayland-cursor")}


def install(apt="", dnf="", brew="", winget="") -> str:
    """The command for this OS and package manager."""
    if WINDOWS:
        return f"winget install --id {winget}" if winget else ""
    if MACOS:
        return f"brew install {brew}" if brew else ""
    if shutil.which("apt-get"):
        return f"sudo apt-get install {apt}" if apt else ""
    return f"sudo dnf install {dnf}" if dnf else ""


class Report:
    def __init__(self):
        self.missing = 0

    def line(self, ok: bool, name: str, detail: str = "", fix: str = "", optional: str = "") -> None:
        mark = "ok  " if ok else ("--  " if optional else "MISS")
        self.missing += not ok and not optional
        print(f"{mark} {name:20} {detail}" + (f"  (optional: {optional})" if optional and not ok else ""))
        if not ok and fix:
            print(f"     {'':20} -> {fix}")


def version_of(*cmd: str) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.splitlines()[0].strip() if out.strip() else ""


def native_driver():
    spec = importlib.util.spec_from_file_location("atlas_native_build", ROOT / "native" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_python(r: Report) -> None:
    v = sys.version_info[:2]
    r.line((3, 12) <= v <= (3, 14), "Python", sys.version.split()[0], "install Python 3.12 to 3.14 (uv does it for you)")
    uv = shutil.which("uv")
    r.line(bool(uv), "uv", version_of("uv", "--version") or "not found",
           "https://docs.astral.sh/uv/getting-started/installation/", optional="without it setup_env.py uses pip")
    r.line(bool(shutil.which("git")), "git", version_of("git", "--version") or "not found",
           install("git", "git", "git", "Git.Git"))


def check_venv(r: Report) -> None:
    py = ROOT / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    if not py.exists():
        r.line(False, ".venv", "not created", "python scripts/setup_env.py")
        return
    code = "import numpy, PySide6, trimesh, rasterio, atlas_toolkit; print(atlas_toolkit.__file__)"
    p = subprocess.run([str(py), "-c", code], capture_output=True, text=True, timeout=120)
    r.line(p.returncode == 0, ".venv", version_of(str(py), "--version") if not p.returncode else p.stderr.strip()[-120:],
           "python scripts/setup_env.py")


def check_native(r: Report) -> None:
    driver = native_driver()
    env = None
    try:
        cmake, env = driver.toolchain()
        version = subprocess.run([cmake, "--version"], capture_output=True, text=True, env=env).stdout.splitlines()[0]
        r.line(True, "CMake and Ninja", version)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
        fix = ("Visual Studio Build Tools with the C++ x64 tools" if WINDOWS else
               install("cmake ninja-build", "cmake ninja-build", "cmake ninja"))
        r.line(False, "CMake and Ninja", str(e), fix)
    fix = ("Visual Studio Build Tools with the C++ x64 tools" if WINDOWS else "xcode-select --install" if MACOS else
           install("build-essential", "gcc-c++ make libstdc++-static"))
    try:
        r.line(True, "C++ compiler", driver.compiler(env if env is not None else dict(os.environ)))
    except (RuntimeError, OSError, StopIteration, subprocess.CalledProcessError) as e:
        r.line(False, "C++ compiler", str(e), fix)
    exe = ".exe" if WINDOWS else ""
    built = [h for h in driver.HELPERS if (ROOT / "src/atlas_toolkit/bin" / f"atlas-{h}-core{exe}").is_file()]
    r.line(len(built) == len(driver.HELPERS), "native helpers", f"{len(built)} of {len(driver.HELPERS)} built",
           "python native/build.py")


def check_linux_libraries(r: Report) -> None:
    if not shutil.which("ldconfig") and not Path("/sbin/ldconfig").exists():
        r.line(False, "Qt system libraries", "cannot check: no ldconfig", optional="running the app")
        return
    known = subprocess.run([shutil.which("ldconfig") or "/sbin/ldconfig", "-p"], capture_output=True, text=True).stdout
    lacking = [name for name in LINUX_LIBS if name not in known]
    apt = shutil.which("apt-get") is not None
    packages = " ".join(LINUX_LIBS[name][0 if apt else 1] for name in lacking)
    r.line(not lacking, "Qt system libraries", ", ".join(lacking) or f"all {len(LINUX_LIBS)} found", install(packages, packages))
    r.line(bool(shutil.which("xvfb-run")), "Xvfb", "found" if shutil.which("xvfb-run") else "not found",
           install("xvfb xauth", "xorg-x11-server-Xvfb"), optional="the GUI and OpenGL tests without a display")
    r.line(bool(shutil.which("docker")), "Docker", version_of("docker", "--version") or "not found",
           install("docker.io", "docker"), optional="clean-distribution checks")


def check_installers(r: Report) -> None:
    if WINDOWS:
        places = [os.environ.get("LOCALAPPDATA", "") and Path(os.environ["LOCALAPPDATA"]) / "Programs",
                  os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")]
        ok = bool(shutil.which("ISCC")) or any(p and (Path(p) / "Inno Setup 6/ISCC.exe").is_file() for p in places)
        r.line(ok, "Inno Setup", "found" if ok else "not found", install(winget="JRSoftware.InnoSetup"), optional="the installer")
    elif MACOS:
        ok = bool(shutil.which("dmgbuild")) or (ROOT / ".venv/bin/dmgbuild").is_file()
        r.line(ok, "dmgbuild", "found" if ok else "not found", "uv sync --group build", optional="the DMG")
    else:
        r.line(bool(shutil.which("nfpm")), "nfpm", "found" if shutil.which("nfpm") else "not found",
               "https://nfpm.goreleaser.com/install/", optional="the deb and rpm packages")
        r.line(bool(shutil.which("appimagetool")), "appimagetool", "found" if shutil.which("appimagetool") else "not found",
               "comes with packaging/linux/Dockerfile", optional="the AppImage")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):      # a legacy Windows console must not stop the report
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    print(f"Atlas Toolkit doctor: {sys.platform}, {ROOT}\n")
    r = Report()
    check_python(r)
    check_venv(r)
    check_native(r)
    if not WINDOWS and not MACOS:
        check_linux_libraries(r)
    check_installers(r)
    blender = shutil.which("blender")
    r.line(bool(blender), "Blender", blender or "not on PATH", "https://www.blender.org/download/", optional="Melt and its tests")
    print(f"\n{'ready' if not r.missing else f'{r.missing} required item(s) missing'}")
    return 1 if r.missing else 0


if __name__ == "__main__":
    sys.exit(main())
