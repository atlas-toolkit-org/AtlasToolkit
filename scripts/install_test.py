#!/usr/bin/env python3
"""
install_test.py - Install a package, prove the installed app works, remove it, and check what is left. Standard library only,
because it runs where nothing else is installed. It changes the machine it runs on, so it asks to be told that is fine:
--container or --throwaway, or a CI runner.

    python3 scripts/install_test.py PACKAGE (--container | --throwaway) [--out DIR] [--soft-gl]

PACKAGE is a .deb, .rpm, .AppImage, .dmg or Windows setup .exe. The same chain everywhere:
  1. install it the way a user would, and find the menu entry, file type and icon
  2. --version, and on Linux every library the bundle lacks is one the package's dependencies brought
  3. self-test --strict, with no display in a container: the command line works on a server
  4. the 3D view draws (X11, and Wayland where the distribution has a compositor)
  5. a job stuck forever, then the window killed hard: nothing it started survives
  6. notices and the GPL source are there; installing over itself still runs
  7. uninstall: nothing left, and the user's own settings untouched

--container: a throwaway container with no desktop. The virtual display and software OpenGL go in after step 3, so that they
cannot hide a dependency the package forgot to declare. Give this script a Python that brings nothing with it, for the same
reason: python-build-standalone, as `uv python install` provides, not the distribution's. An AppImage declares nothing, so
there the system gets what the deb and rpm declare (packaging/linux/nfpm.yaml), which is what any desktop already has.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_frozen_gui import check as check_gl  # noqa: E402

WINDOWS = sys.platform == "win32"
APP_FILE = "Atlas Toolkit.exe"
PROG_ID = "AtlasToolkit.Workflow"
# Qt loads these when it can and carries on when it cannot, so what they lack is not a missing dependency: the GTK theme, the
# framebuffer and direct-to-display platforms, and image formats with an optional codec.
OPTIONAL_FILES = re.compile(r"^(libqgtk3|libqlinuxfb|libqeglfs|libqminimalegl|libqvkkhrdisplay|libqvnc|libqtiff|libqwebp|libQt6EglFS)")
PLUGIN_SPEC = '''from atlas_toolkit.core import Inputs, Output, Tool
TOOL = Tool(id="test-sleepy", name="Sleepy", version="0.1", category="svg", icon="dot", action="Run",
            summary="Starts a child process and never finishes.", inputs=Inputs("svg"), output=Output("{stem}_sleepy.svg", "svg"),
            signature="<desc>test-sleepy</desc>", engine=".engine:run", runs_in="process", preview="svg", params=[])
'''
PLUGIN_ENGINE = '''import os, subprocess, sys, time
from pathlib import Path
from atlas_toolkit.core import procs
def run(ctx, src, dst, p):
    command = ["ping", "-n", "600", "127.0.0.1"] if sys.platform == "win32" else ["sleep", "600"]
    child = procs.spawn(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    report = Path(os.environ["ATLAS_TEST_PIDS"])
    report.with_suffix(".tmp").write_text(f"{os.getpid()} {child.pid} {os.getppid()}", encoding="utf-8")
    os.replace(report.with_suffix(".tmp"), report)
    time.sleep(600)
'''
SAMPLE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm"><path d="M0 0L10 10"/></svg>'


def say(ok: bool, text: str) -> bool:
    print(f"{'ok  ' if ok else 'FAIL'} {text}", flush=True)
    return ok


def running(pid: int) -> bool:
    if WINDOWS:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return k32.WaitForSingleObject(ctypes.c_void_p(handle), 0) != 0
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = Path(f"/proc/{pid}/stat")          # a killed process stays a zombie until reaped: a container may never reap it
    if state.exists():
        return state.read_text(encoding="ascii", errors="replace").rsplit(")", 1)[-1].split()[0] != "Z"
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


# ------------------------------------------------------------------------------------------------- installing
class Windows:
    """Per-user, as the installer defaults to, into the folder a user would get: its name has a space in it."""

    def __init__(self, package: Path):
        self.package = package
        self.folder = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Atlas Toolkit"
        self.shortcut = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Atlas Toolkit.lnk"

    def prepare(self) -> None:
        if self.folder.exists():
            sys.exit(f"{self.folder} already exists: this test does not touch an existing install")

    def install(self) -> Path:
        subprocess.run([str(self.package), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER",
                        "/TASKS=association,path"], check=True, timeout=900)
        return self.folder / APP_FILE

    install_again = install

    @staticmethod
    def _association() -> bool:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.atlasflow") as key:
                ours = winreg.QueryValueEx(key, "")[0] == PROG_ID
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}"))
        except OSError:
            return False
        return ours

    def _on_path(self) -> bool:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value = winreg.QueryValueEx(key, "Path")[0]
        except OSError:
            return False
        return str(self.folder).lower() in [p.strip().lower() for p in value.split(";")]

    def missing_integration(self) -> list:
        return [name for name, there in (("Start Menu shortcut", self.shortcut.exists()), (".atlasflow association", self._association()),
                                         ("PATH entry", self._on_path()), ("console companion", (self.folder / "atlas-toolkit.exe").exists()))
                if not there]

    def uninstall(self) -> None:
        subprocess.run([str(self.folder / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=True, timeout=300)
        deadline = time.monotonic() + 90       # the uninstaller hands over to a copy of itself and returns early,
        while self.leftovers() and time.monotonic() < deadline:     # and it cleans PATH only after removing the folder
            time.sleep(0.5)

    def leftovers(self) -> list:
        left = [str(p) for p in self.folder.rglob("*")] if self.folder.exists() else []
        left += [str(self.shortcut)] if self.shortcut.exists() else []
        left += [".atlasflow association"] if self._association() else []
        return left + (["PATH entry"] if self._on_path() else [])


class Package:
    folder = Path("/opt/atlas-toolkit")
    menu = Path("/usr/share/applications/atlas-toolkit.desktop")
    mime = Path("/usr/share/mime/packages/atlas-toolkit.xml")
    icon = Path("/usr/share/icons/hicolor/256x256/apps/atlas-toolkit.png")
    command = Path("/usr/bin/atlas-toolkit")
    display_packages: tuple = ()

    def __init__(self, package: Path):
        self.package = package

    def run(self, *args, check: bool = True) -> bool:
        done = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=900,
                              env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"))
        if done.returncode and check:
            print(done.stdout[-1500:], done.stderr[-1500:], flush=True)
            sys.exit(f"FAIL {' '.join(args[:4])}: exit code {done.returncode}")
        return done.returncode == 0

    def prepare(self) -> None:
        if self.folder.exists():
            sys.exit(f"{self.folder} already exists: this test does not touch an existing install")

    def missing_integration(self) -> list:
        return [str(p) for p in (self.command, self.menu, self.mime, self.icon) if not p.exists()]

    def leftovers(self) -> list:
        return [str(p) for p in (self.folder, self.command, self.menu, self.mime, self.icon) if p.exists() or p.is_symlink()]


class Deb(Package):
    display_packages = ("xvfb", "libgl1-mesa-dri", "libglx-mesa0", "libegl-mesa0")

    def prepare(self) -> None:
        super().prepare()
        self.run("apt-get", "update", "-qq")

    def install(self) -> Path:
        self.run("apt-get", "install", "-y", "-qq", "--no-install-recommends", str(self.package))
        return self.command

    def install_again(self) -> None:
        self.run("apt-get", "install", "-y", "-qq", "--no-install-recommends", "--reinstall", str(self.package))

    def add(self, *names, check: bool = True) -> bool:
        return self.run("apt-get", "install", "-y", "-qq", "--no-install-recommends", *names, check=check)

    def uninstall(self) -> None:
        self.run("apt-get", "remove", "-y", "-qq", "atlas-toolkit")


class Rpm(Package):
    display_packages = ("xorg-x11-server-Xvfb", "mesa-dri-drivers", "mesa-libEGL", "mesa-libGL")

    def install(self) -> Path:
        self.run("dnf", "install", "-y", "-q", "--setopt=install_weak_deps=False", str(self.package))
        return self.command

    def install_again(self) -> None:
        self.run("dnf", "reinstall", "-y", "-q", str(self.package))

    def add(self, *names, check: bool = True) -> bool:
        return self.run("dnf", "install", "-y", "-q", "--setopt=install_weak_deps=False", *names, check=check)

    def uninstall(self) -> None:
        self.run("dnf", "remove", "-y", "-q", "atlas-toolkit")


def declared_dependencies(kind: str) -> list:
    """What the deb or rpm asks the system for: the one list of what the bundle leaves to the system."""
    text = (Path(__file__).resolve().parents[1] / "packaging" / "linux" / "nfpm.yaml").read_text(encoding="utf-8")
    block = re.search(rf"^  {kind}:\n(?:    #.*\n)?    depends:\n((?:      - .*\n)+)", text, re.M)[1]
    return [line.strip()[2:].split(" (")[0] for line in block.splitlines()]


class AppImage:
    """One file, put wherever the user likes. It declares no dependencies, so `--container` brings what a desktop has."""

    def __init__(self, package: Path, out: Path):
        self.package, self.out = package, out
        self.file = out / "apps" / package.name
        self.folder = out / "squashfs-root"     # what is inside, for the checks that read files
        family = Deb if shutil.which("apt-get") else Rpm
        self.system = family(package)
        self.display_packages = self.system.display_packages
        self.add = self.system.add

    def prepare(self) -> None:
        self.system.prepare()
        if not Path("/dev/fuse").exists():      # a container: what the file itself does when it cannot mount
            os.environ["APPIMAGE_EXTRACT_AND_RUN"] = "1"

    def bring_the_desktop(self) -> None:
        self.add(*declared_dependencies("deb" if isinstance(self.system, Deb) else "rpm"))

    def install(self) -> Path:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.package, self.file)
        self.file.chmod(0o755)
        subprocess.run([str(self.file), "--appimage-extract"], cwd=self.out, check=True, capture_output=True, timeout=600)
        return self.file

    def install_again(self) -> None:
        self.file.unlink()
        shutil.copy2(self.package, self.file)
        self.file.chmod(0o755)

    def missing_integration(self) -> list:
        return [name for name in ("atlas-toolkit.desktop", "atlas-toolkit.png", "AppRun") if not (self.folder / name).exists()]

    def uninstall(self) -> None:
        self.file.unlink()

    def leftovers(self) -> list:
        return [str(self.file)] if self.file.exists() else []


class Dmg:
    """Mount it, copy the app to Applications as Finder does, unmount."""
    app = Path("/Applications/Atlas Toolkit.app")

    def __init__(self, package: Path):
        self.package, self.folder, self.layout = package, self.app, False

    def prepare(self) -> None:
        if self.app.exists():
            sys.exit(f"{self.app} already exists: this test does not touch an existing install")

    def _copy(self) -> None:
        mount = Path(tempfile.mkdtemp(prefix="atlas_dmg_"))
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(self.package)],
                       check=True, capture_output=True, timeout=300)
        try:
            self.layout = (mount / "Applications").is_symlink() and (mount / self.app.name).is_dir()
            subprocess.run(["ditto", str(mount / self.app.name), str(self.app)], check=True, timeout=900)
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=True, capture_output=True, timeout=120)

    def install(self) -> Path:
        self._copy()
        return self.app / "Contents" / "MacOS" / self.app.stem

    def install_again(self) -> None:         # Finder's Replace
        shutil.rmtree(self.app)
        self._copy()

    def missing_integration(self) -> list:
        missing = [] if self.layout else ["the app and a link to Applications, side by side in the disk image"]
        if subprocess.run(["codesign", "--verify", "--deep", "--strict", str(self.app)], capture_output=True).returncode:
            missing.append("a signature that verifies")
        info = plistlib.loads((self.app / "Contents" / "Info.plist").read_bytes())
        if info.get("LSMinimumSystemVersion") != "15.0":
            missing.append("macOS 15 as the minimum")
        if "atlasflow" not in str(info.get("UTExportedTypeDeclarations")):
            missing.append("the .atlasflow file type")
        with tempfile.TemporaryDirectory(prefix="atlas_link_") as folder:     # docs/install/macos.md tells people to do this
            link = Path(folder) / "atlas-toolkit"
            link.symlink_to(self.app / "Contents" / "MacOS" / self.app.stem)
            if subprocess.run([str(link), "--version"], capture_output=True, timeout=120).returncode:
                missing.append("a command-line link that runs")
        return missing

    def uninstall(self) -> None:
        shutil.rmtree(self.app)

    def leftovers(self) -> list:
        return [str(self.app)] if self.app.exists() else []


# ------------------------------------------------------------------------------------------------- checks
def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def unresolved_libraries(folder: Path) -> dict:
    """Ask the system's own loader about every library in the bundle. Run this before anything else gets installed."""
    files = [p for p in folder.rglob("*") if p.is_file() and not p.is_symlink() and is_elf(p)]
    env = dict(os.environ, LD_LIBRARY_PATH=":".join(sorted({str(p.parent) for p in files})))

    def lacking(path: Path) -> list:
        out = subprocess.run(["ldd", str(path)], capture_output=True, text=True, errors="replace", env=env)
        return [(line.split()[0] if "=> not found" in line else line.strip(), path.name)
                for line in (out.stdout + out.stderr).splitlines() if "not found" in line]

    found: dict = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
        for result in pool.map(lacking, [p for p in files if not OPTIONAL_FILES.match(p.name)]):
            for library, who in result:
                found.setdefault(library, who)
    return found


def not_for_everyone(folder: Path) -> list:
    """A package is installed by root and run by everybody else."""
    wrong = []
    for path in [folder, *folder.rglob("*")]:
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        if not mode & 0o004 or (path.is_dir() and not mode & 0o001) or (mode & 0o100 and not mode & 0o001):
            wrong.append(path)
    return wrong


class Display:
    """A virtual X server and, where the distribution has one, a headless Wayland compositor, both with software OpenGL."""

    def __init__(self, installer, out: Path):
        self.installer, self.children = installer, []
        self.runtime = out / "xdg-runtime"
        self.runtime.mkdir(mode=0o700, exist_ok=True)

    def start(self, *command) -> subprocess.Popen:
        child = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(child)
        return child

    def x11(self) -> None:
        self.installer.add(*self.installer.display_packages)
        self.start("Xvfb", ":99", "-screen", "0", "1600x1000x24", "+extension", "GLX")
        time.sleep(3)
        os.environ.update(DISPLAY=":99", QT_QPA_PLATFORM="xcb", LIBGL_ALWAYS_SOFTWARE="1", XDG_RUNTIME_DIR=str(self.runtime))

    def wayland(self) -> bool:
        if not self.installer.add("weston", check=False):
            return False
        for backend in ("headless", "headless-backend.so"):     # how Weston 9 and 10 still name it
            child = self.start("weston", f"--backend={backend}", "--socket=wl-atlas", "--idle-time=0", "--width=1600", "--height=1000")
            time.sleep(4)
            if child.poll() is None:
                os.environ.pop("DISPLAY", None)
                os.environ.update(WAYLAND_DISPLAY="wl-atlas", QT_QPA_PLATFORM="wayland", XDG_SESSION_TYPE="wayland")
                return True
        return False

    def stop(self) -> None:
        for child in self.children:
            child.kill()


def profile_env(home: Path) -> dict:
    """The app's real profile logic, pointed at a folder of ours: no ATLAS_TOOLKIT_HOME, which would bypass it."""
    env = {k: v for k, v in os.environ.items() if k != "ATLAS_TOOLKIT_HOME" and not k.startswith("XDG_CONFIG") and not k.startswith("XDG_DATA")}
    env["PYINSTALLER_SUPPRESS_SPLASH_SCREEN"] = "1"
    if WINDOWS:
        env.update(APPDATA=str(home / "Roaming"), LOCALAPPDATA=str(home / "Local"))
    else:
        env["HOME"] = str(home)
    return env


def orphan_check(exe: Path, work: Path, env: dict) -> bool:
    """A job stuck forever, in the installed app. Kill the window hard: the worker and its child must both end."""
    plugin = work / "plugins" / "sleepy"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text(PLUGIN_SPEC, encoding="utf-8")
    (plugin / "engine.py").write_text(PLUGIN_ENGINE, encoding="utf-8")
    sample = work / "sample.svg"
    sample.write_text(SAMPLE_SVG, encoding="utf-8")
    report = work / "pids.txt"
    run_env = dict(env, ATLAS_TOOLKIT_PLUGINS=str(work / "plugins"), ATLAS_TEST_PIDS=str(report))
    host = subprocess.Popen([str(exe), "--tool", "test-sleepy", str(sample), "--draft", "--snapshot", str(work / "x.png")],
                            env=run_env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
    pids = []
    try:
        deadline = time.monotonic() + 120
        while not report.exists() and time.monotonic() < deadline and host.poll() is None:
            time.sleep(0.2)
        if not report.exists():
            host.kill()
            print(host.communicate()[1][-1500:], flush=True)
            return say(False, f"nothing is left running: the stuck job never started (exit code {host.poll()})")
        for _ in range(100):     # Windows refuses a read while the file is being renamed into place
            try:
                pids = [int(p) for p in report.read_text(encoding="utf-8").split()]
                break
            except PermissionError:
                time.sleep(0.1)
        if not running(pids[1]):
            return say(False, "nothing is left running: the job's child was not running to begin with")
        window = pids.pop()   # the worker's parent. Usually `host`, but an AppImage's own process is a parent waiting for it
        if WINDOWS:           # the one way to kill it with no chance to clean up
            subprocess.run(["taskkill", "/F", "/PID", str(window)], capture_output=True)
        else:
            os.kill(window, signal.SIGKILL)
        host.wait(90)
        deadline = time.monotonic() + 30
        while any(running(p) for p in pids) and time.monotonic() < deadline:
            time.sleep(0.2)
        alive = [p for p in pids if running(p)]
        return say(not alive, f"nothing is left running after the window was killed{f': still alive {alive}' if alive else ''}")
    finally:
        host.kill()
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


def files_in(folder: Path) -> list:
    return [p for p in folder.rglob("*") if p.is_file()]


# ------------------------------------------------------------------------------------------------- the chain
def chain(package: Path, out: Path, container: bool, soft_gl: bool) -> int:
    kinds = {".deb": Deb, ".rpm": Rpm, ".exe": Windows, ".dmg": Dmg, ".AppImage": lambda p: AppImage(p, out)}
    if package.suffix not in kinds:
        sys.exit(f"cannot install {package.name}")
    installer = kinds[package.suffix](package)
    installer.prepare()
    if container and isinstance(installer, AppImage):
        installer.bring_the_desktop()
    home = out / "home"
    home.mkdir(parents=True, exist_ok=True)
    results = []

    print(f"--- installing {package.name}", flush=True)
    exe = installer.install()
    if not say(exe.exists(), f"installed: {exe}"):
        return 1
    missing = installer.missing_integration()
    results.append(say(not missing, f"on the desktop{': missing ' + ', '.join(missing) if missing else ''}"))
    version = subprocess.run([str(exe), "--version"], capture_output=True, text=True, errors="replace", env=profile_env(home), timeout=120)
    number = version.stdout.split()[-1] if version.stdout.strip() else "nothing"
    results.append(say(version.returncode == 0 and version.stdout.startswith("Atlas Toolkit") and number in package.name,
                       f"--version says {number}, and the package is named {package.name}"))
    if sys.platform.startswith("linux"):
        lacking = unresolved_libraries(installer.folder)
        results.append(say(not lacking, "the package's dependencies bring every library the bundle lacks"))
        for library, who in sorted(lacking.items()):
            print(f"     {library}  (needed by {who})", flush=True)
        wrong = not_for_everyone(installer.folder)
        results.append(say(not wrong, f"every file can be read and run by other users{f': not {wrong[0]}, and {len(wrong) - 1} more' if wrong else ''}"))

    report = out / "selftest.txt"
    strict = subprocess.run([str(exe), "self-test", "--strict", "--report", str(report)], capture_output=True, text=True,
                            errors="replace", env=profile_env(home), timeout=1500)
    summary = report.read_text(encoding="utf-8").splitlines() if report.exists() else []
    results.append(say(strict.returncode == 0, f"self-test --strict: {sum(s.startswith('ok') for s in summary)} ok, "
                                               f"{sum(s.startswith('skip') for s in summary)} skipped (external programs)"))
    if strict.returncode and not summary:
        print(strict.stderr[-1500:], flush=True)
    for line in (s for s in summary if s.startswith("FAIL")):
        print(f"     {line[:200]}", flush=True)

    display = Display(installer, out) if container else None
    try:
        if display:
            display.x11()
        (out / "gl").mkdir(exist_ok=True)
        print("--- the 3D view" + (" on X11" if display else ""), flush=True)
        results.append(check_gl(exe, out / "gl", soft_gl) == 0)
        started = subprocess.run([str(exe), "--page", "home", "--snapshot", str(out / "home.png")], capture_output=True,
                                 env=profile_env(home), timeout=120)
        results.append(say(started.returncode == 0, "a normal start, with the real profile folders"))
        (out / "orphans").mkdir(exist_ok=True)
        results.append(orphan_check(exe, out / "orphans", profile_env(home)))
        if display:
            if display.wayland():
                print("--- the 3D view on Wayland", flush=True)
                (out / "gl-wayland").mkdir(exist_ok=True)
                results.append(check_gl(exe, out / "gl-wayland", soft_gl) == 0)
            else:
                print("note no Weston on this distribution: Wayland was not tried here", flush=True)
    finally:
        if display:
            display.stop()

    print("--- what ships with it", flush=True)
    notices = ("LICENSE", "THIRD_PARTY_NOTICES.md", "developable-source.zip", "developable-surfaces.txt")
    if sys.platform.startswith("linux"):       # installers always come from the build image, which writes this one
        notices += ("linux-system-libraries.txt",)
    lacks = [n for n in notices if not any(installer.folder.rglob(f"atlas_toolkit/licenses/{n}"))]
    results.append(say(not lacks, f"notices and the GPL source{': missing ' + ', '.join(lacks) if lacks else ''}"))

    print("--- installing over itself", flush=True)
    stale = installer.folder / "_internal" / "left-by-an-older-version.txt"
    if WINDOWS:
        stale.write_text("old", encoding="utf-8")
    installer.install_again()
    again = subprocess.run([str(exe), "--version"], capture_output=True, text=True, errors="replace", env=profile_env(home), timeout=120)
    results.append(say(again.returncode == 0, "it still runs"))
    if WINDOWS:     # the folder holds what one version needs: nothing of an older one may stay mixed in
        results.append(say(not stale.exists(), "an older version's files do not stay behind"))

    profile = files_in(home)
    results.append(say(bool(profile), f"the app keeps its settings in the profile folders: {len(profile)} files"))
    print("--- uninstalling", flush=True)
    installer.uninstall()
    left = installer.leftovers()
    results.append(say(not left, f"nothing left after uninstalling{': ' + ', '.join(left[:5]) if left else ''}"))
    gone = [str(p) for p in profile if not p.exists()]
    results.append(say(not gone, f"the user's own settings are untouched{': lost ' + gone[0] if gone else ''}"))
    print(f"\n{sum(results)} of {len(results)} passed", flush=True)
    return 0 if all(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package", type=Path)
    ap.add_argument("--container", action="store_true", help="a clean container: add a virtual display and software OpenGL")
    ap.add_argument("--throwaway", action="store_true", help="this machine has a desktop and may be changed")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--soft-gl", action="store_true", help="a blank 3D pane is reported, not failed: no GPU and no software GL here")
    a = ap.parse_args()
    if not (a.container or a.throwaway or os.environ.get("CI")):
        sys.exit("This installs and removes software on this machine. Say so with --container or --throwaway.")
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        return chain(a.package.resolve(), a.out.resolve(), a.container, a.soft_gl)
    with tempfile.TemporaryDirectory(prefix="atlas_install_test_", ignore_cleanup_errors=True) as folder:
        return chain(a.package.resolve(), Path(folder), a.container, a.soft_gl)


if __name__ == "__main__":
    sys.exit(main())
