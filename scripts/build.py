#!/usr/bin/env python3
"""
build.py - Build Atlas Toolkit for this OS, check it, and publish it to dist/ only if it passes.

    .venv\\Scripts\\python scripts\\build.py              native helpers, tests, build, self-test, publish
    .venv\\Scripts\\python scripts\\build.py --onedir     Windows: a folder instead of one file (starts instantly)
    .venv\\Scripts\\python scripts\\build.py --skip-tests
    .venv\\Scripts\\python scripts\\build.py --skip-native   package the helpers already in src/atlas_toolkit/bin

What it publishes: on Windows "Atlas Toolkit.exe" with a splash screen and its console companion "atlas-toolkit.exe"; on
macOS "Atlas Toolkit.app"; on Linux a folder "atlas-toolkit/". Installers are made from these by scripts/package.py.

Steps: build the C++ helpers (native/build.py, incremental); run the test suite; draw the icons and splash with Qt; write
the Windows version resource; run PyInstaller on packaging/atlas_toolkit.spec (work files in a temporary folder); self-test
every tool, render the frozen GUI and audit the bundle. The result is staged in build/release and published to dist/ only
after the checks pass, replacing what was there in one step.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                       # the repository
SRC = ROOT / "src"
DIST = ROOT / "dist"                      # published app, console companion and helper source archive
NATIVE = ROOT / "native"
HELPERS = ("texture", "stripes", "remesh", "developable")
NAME = "Atlas Toolkit"
CONSOLE_NAME = "atlas-toolkit"          # the console companion (Windows): atlas-toolkit list
STAGING = ROOT / "build" / "release"
SPEC = ROOT / "packaging" / "atlas_toolkit.spec"
WINDOWS, MACOS = sys.platform == "win32", sys.platform == "darwin"
sys.path.insert(0, str(SRC))

from atlas_toolkit.version import APP_ID, __version__  # noqa: E402

def run_tests() -> None:
    print("running the tests …")
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=ROOT)
    if r.returncode:
        sys.exit("tests failed: not building")


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=ROOT, capture_output=True, text=True)
        return out.stdout.strip() + ("+" if dirty.stdout.strip() else "") if out.returncode == 0 else ""
    except OSError:
        return ""


def render_assets(tmp: Path) -> tuple[Path, Path, Path]:
    """The icons (.ico, .icns, and PNGs for Linux's icon theme) and a splash image, drawn with the app's own painter."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter, QPen
    QGuiApplication.instance() or QGuiApplication([])
    from atlas_toolkit.ui.theme import make_logo
    for ttf in sorted((SRC / "atlas_toolkit" / "ui" / "theme" / "fonts").glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(ttf))

    images = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        make_logo(size).save(buf, "PNG")
        images.append((size, bytes(buf.data())))
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        side = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    ico = tmp / "atlas.ico"
    ico.write_bytes(head + entries + blobs)
    shutil.copy2(ico, STAGING / "atlas.ico")      # the Windows installer's own icon (scripts/package.py)

    def png(size: int) -> bytes:
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        make_logo(size).save(buf, "PNG")
        return bytes(buf.data())
    # .icns: a header, then one (type, length, PNG) per size
    icns_body = b"".join(kind + struct.pack(">I", 8 + len(data)) + data for kind, data in
                         ((kind, png(size)) for kind, size in ((b"ic11", 32), (b"ic12", 64), (b"ic07", 128), (b"ic13", 256),
                                                               (b"ic08", 256), (b"ic14", 512), (b"ic09", 512), (b"ic10", 1024))))
    icns = tmp / "atlas.icns"
    icns.write_bytes(b"icns" + struct.pack(">I", 8 + len(icns_body)) + icns_body)
    icons = STAGING / "icons"
    if icons.exists():
        shutil.rmtree(icons)
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        folder = icons / f"{size}x{size}" / "apps"
        folder.mkdir(parents=True)
        (folder / f"{APP_ID}.png").write_bytes(png(size))

    W, H = 560, 300
    img = QImage(W, H, QImage.Format_RGB32)
    img.fill(QColor("#05070D"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(120, 150, 255, 14), 1))
    for x in range(0, W, 24):
        p.drawLine(x, 0, x, H)
    for y in range(0, H, 24):
        p.drawLine(0, y, W, y)
    p.drawPixmap(40, 96, make_logo(96))
    title = QFont("Geist", 30)
    title.setWeight(QFont.Light)
    p.setFont(title)
    p.setPen(QColor("#E8EEF9"))
    p.drawText(QRectF(160, 92, 380, 50), Qt.AlignLeft | Qt.AlignVCenter, "Atlas Toolkit")
    sub = QFont("Geist Mono", 10)
    sub.setLetterSpacing(QFont.AbsoluteSpacing, 1.6)
    p.setFont(sub)
    p.setPen(QColor("#6F7D99"))
    p.drawText(QRectF(162, 142, 380, 24), Qt.AlignLeft | Qt.AlignVCenter, f"PEN-PLOTTER TOOLKIT · V{__version__}")
    p.setPen(QColor("#5BD8FF"))
    p.drawText(QRectF(162, 172, 380, 24), Qt.AlignLeft | Qt.AlignVCenter, "LOADING …")
    p.setPen(QPen(QColor("#5BD8FF"), 1))
    for x, y, dx, dy in ((14, 14, 1, 1), (W - 15, 14, -1, 1), (14, H - 15, 1, -1), (W - 15, H - 15, -1, -1)):
        p.drawLine(x, y, x + 14 * dx, y)
        p.drawLine(x, y, x, y + 14 * dy)
    p.end()
    splash = tmp / "splash.png"
    img.save(str(splash))
    return ico, icns, splash


def version_file(tmp: Path, commit: str) -> Path:
    nums = [int(x) for x in (__version__.split(".") + ["0", "0", "0"])[:4] if x.isdigit()]
    while len(nums) < 4:
        nums.append(0)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple(nums)}, prodvers={tuple(nums)}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'sci.co.plot'),
      StringStruct('FileDescription', 'Atlas Toolkit - pen-plotter toolkit'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'atlas-toolkit'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', '{NAME}'),
      StringStruct('ProductVersion', '{__version__} ({stamp}{" " + commit if commit else ""})')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path = tmp / "version.txt"
    path.write_text(text, encoding="utf-8")
    return path


@contextmanager
def system_dlls_first():
    """Match Windows' loader precedence when PyInstaller resolves DLLs from PATH.

    Poppler/Conda may put an incompatible icuuc.dll before System32 on PATH. Qt uses the Windows ICU ABI,
    so collecting that unrelated ICU makes the frozen GUI fail even though source and mesh self-tests pass.
    This change is confined to the build process and never edits the user's environment.
    """
    original = os.environ.get("PATH")
    try:
        if sys.platform == "win32":
            system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
            os.environ["PATH"] = str(system) + os.pathsep + (original or "")
        yield
    finally:
        if original is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = original


def built_app(onedir: bool) -> Path:
    """The program PyInstaller made in the staging folder."""
    if WINDOWS:
        return STAGING / NAME / f"{NAME}.exe" if onedir else STAGING / f"{NAME}.exe"
    if MACOS:
        return STAGING / f"{NAME}.app" / "Contents" / "MacOS" / NAME
    return STAGING / APP_ID / APP_ID


def build(onedir: bool, splash_ok: bool) -> Path:
    """Build into the staging folder. Windows makes one file unless asked for a folder; the others always a folder."""
    import PyInstaller.__main__
    commit = git_commit()
    one_file = WINDOWS and not onedir
    with tempfile.TemporaryDirectory(prefix="atlas_toolkit_build_") as t:
        tmp = Path(t)
        ico, icns, splash = render_assets(tmp)
        spec_args = ["--icon", str(icns if MACOS else ico)]
        if one_file:
            spec_args.append("--onefile")
            if splash_ok:
                spec_args += ["--splash", str(splash)]
        if WINDOWS:
            spec_args += ["--version-file", str(version_file(tmp, commit))]
        args = [str(SPEC), "--noconfirm", "--clean", "--distpath", str(STAGING), "--workpath", str(tmp / "build"),
                "--", *spec_args]
        print("PyInstaller", " ".join(args))
        with system_dlls_first():
            PyInstaller.__main__.run(args)
        exe = built_app(onedir)
        if WINDOWS:
            build_console(tmp, ico, exe.parent)
    return exe


def build_console(tmp: Path, ico: Path, dest: Path) -> Path:
    """The console companion: a small one-file console exe of scripts/console.py, "atlas-toolkit.exe" next to the
    app, so that `atlas-toolkit list` works in cmd and PowerShell. Its name has no space on purpose: cmd will not
    find a quoted, extensionless "Atlas Toolkit" (a .com of that name did not work)."""
    import PyInstaller.__main__
    out = tmp / "console"
    PyInstaller.__main__.run([str(HERE / "console.py"), "--name", CONSOLE_NAME, "--console", "--onefile",
                              "--noconfirm",
                              "--icon", str(ico), "--distpath", str(out), "--workpath", str(tmp / "console-build"),
                              "--specpath", str(tmp / "console-spec"),
                              "--version-file", str(tmp / "version.txt")])
    con = dest / f"{CONSOLE_NAME}.exe"
    shutil.copy2(out / f"{CONSOLE_NAME}.exe", con)
    return con


def console_test(exe: Path) -> int:
    """The command line streams to the terminal and passes its exit code back: through the companion on Windows, where
    the app itself is windowed, and straight from the program elsewhere."""
    com = exe.with_name(f"{CONSOLE_NAME}.exe") if WINDOWS else exe
    if not com.is_file():
        print(f"FAIL console: {com.name} was not built")
        return 1
    v = subprocess.run([str(com), "--version"], capture_output=True, text=True, encoding="utf-8", timeout=120)
    ls = subprocess.run([str(com), "list"], capture_output=True, text=True, encoding="utf-8", timeout=120)
    bad = subprocess.run([str(com), "describe", "no-such-tool"], capture_output=True, timeout=120)
    if v.returncode or __version__ not in v.stdout or ls.returncode or "→" not in ls.stdout or bad.returncode != 2:
        print(f"FAIL console: --version -> {v.returncode} {v.stdout.strip()!r}; list -> {ls.returncode}; "
              f"bad command -> {bad.returncode}")
        return 1
    print(f"ok console: {com.name} --version -> {v.stdout.strip()}, list and exit codes pass through")
    return 0


def publish(exe: Path) -> Path:
    """Replace the installed app only after checks pass: one atomic step for a file, a swap for a folder or .app."""
    target = DIST / exe.name
    DIST.mkdir(parents=True, exist_ok=True)
    if exe.is_dir():
        retired = DIST / f".atlas-retired-{exe.name}"
        if retired.exists():
            shutil.rmtree(retired)
        if target.exists():
            os.replace(target, retired)
        try:
            os.replace(exe, target)
        except OSError:
            if retired.exists():
                os.replace(retired, target)
            raise
        shutil.rmtree(retired, ignore_errors=True)
        return target
    fd, name = tempfile.mkstemp(prefix=".atlas-release-", suffix=".tmp", dir=DIST)
    os.close(fd)
    pending = Path(name)
    try:
        shutil.copy2(exe, pending)
        os.replace(pending, target)
    finally:
        pending.unlink(missing_ok=True)
    return target


def isolated_env(folder: Path) -> dict:
    """A frozen app under test keeps its settings and caches here, on every OS."""
    env = dict(os.environ, ATLAS_TOOLKIT_HOME=str(folder), PYINSTALLER_SUPPRESS_SPLASH_SCREEN="1")
    if not WINDOWS:
        env["HOME"] = str(folder)
    return env


def gui_smoke_test(exe: Path) -> int:
    """Exercise Qt's actual frozen startup, isolated from the user's settings/history."""
    with tempfile.TemporaryDirectory(prefix="atlas_frozen_gui_") as folder:
        folder = Path(folder)
        shot = folder / "home.png"
        env = isolated_env(folder)
        try:
            result = subprocess.run([str(exe), "--page", "home", "--snapshot", str(shot)], env=env, timeout=60)
        except subprocess.TimeoutExpired:
            print("FAIL frozen GUI: startup timed out; the installed executable was not replaced")
            return 1
        if result.returncode or not shot.is_file():
            print(f"FAIL frozen GUI: exit code {result.returncode}, screenshot present: {shot.is_file()}")
            return 1
        print("ok frozen GUI: Home rendered successfully")
        return 0


def self_test(exe: Path, onedir: bool) -> int:
    with tempfile.TemporaryDirectory(prefix="atlas_frozen_selftest_") as folder:
        report = Path(folder) / "selftest.txt"
        t0 = time.perf_counter()
        r = subprocess.run([str(exe), "self-test", "--strict", "--report", str(report)], timeout=1200,
                           env=isolated_env(Path(folder) / "home"))
        print(f"\nself-test of the built app ({time.perf_counter() - t0:.0f} s, exit code {r.returncode}):")
        print(report.read_text(encoding="utf-8") if report.exists() else "  (no report written)")
    basic = r.returncode or gui_smoke_test(exe) or console_test(exe)
    if basic:
        return basic
    folder_build = onedir or not WINDOWS
    if folder_build:
        audited = STAGING / (f"{NAME}.app" if MACOS else NAME if WINDOWS else APP_ID)
        code = subprocess.run([sys.executable, str(HERE / "audit_bundle.py"), str(audited)]).returncode
        if code:
            return code
    if WINDOWS:         # no driver needed, so this proves the same on any machine: a VM or Remote Desktop still gets a 3D view
        code = subprocess.run([sys.executable, str(HERE / "check_frozen_gui.py"), "--exe", str(exe), "--software"], timeout=300).returncode
        if code:
            return code
    raster_code = subprocess.run([sys.executable, str(HERE / "check_frozen_raster.py"), "--exe", str(exe)], timeout=900).returncode
    if raster_code:
        return raster_code
    return subprocess.run([sys.executable, str(HERE / "check_frozen_dem_code.py"), "--exe", str(exe)], timeout=120).returncode


def notice_system_libraries(folder: Path) -> None:
    """Linux: what PyInstaller took from the build system keeps its licence. The build image's package database knows each
    library's package and licence, so this notice is written with the build and cannot fall behind it."""
    if not shutil.which("rpm"):
        print("note: no rpm here, so no notice for bundled system libraries (release builds use packaging/linux/Dockerfile)")
        return

    def rpm(*args: str) -> str:
        done = subprocess.run(["rpm", *args], capture_output=True, text=True, errors="replace")
        return done.stdout if done.returncode == 0 else ""

    packages: dict = {}
    internal = folder / "_internal"
    for library in sorted(internal.glob("lib*.so*")):
        source = next((d / library.name for d in (Path("/usr/lib64"), Path("/lib64")) if (d / library.name).exists()), None)
        found = rpm("-qf", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{LICENSE}", str(source)) if source and not library.is_symlink() else ""
        if found:
            packages.setdefault(tuple(found.split("\t")), []).append(library.name)
    lines = ["Libraries in this Linux build that came from the build system, AlmaLinux 9, with each package's licence as its",
             "package database records it. The app loads them from this folder, so they can be replaced. AlmaLinux publishes the",
             "source of every package at https://vault.almalinux.org/", ""]
    lines += [f"{name} {version}: {licence}\n    {', '.join(libraries)}" for (name, version, licence), libraries in sorted(packages.items())]
    texts: dict = {}
    for name, _version, _licence in sorted(packages):
        documents = rpm("-qd", name).splitlines()
        for path in rpm("-qL", name).splitlines() + [d for d in documents if Path(d).name.upper().startswith(("COPYING", "LICEN"))]:
            if Path(path).is_file():
                texts.setdefault(Path(path).read_text(encoding="utf-8", errors="replace"), []).append(f"{name}: {Path(path).name}")
    for text, owners in texts.items():
        lines += ["", "=" * 100, "; ".join(owners), "=" * 100, text]
    target = internal / "atlas_toolkit" / "licenses" / "linux-system-libraries.txt"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"notice: {sum(len(v) for v in packages.values())} system libraries from {len(packages)} packages, {len(texts)} licence texts")


def native_helpers(skip: bool) -> None:
    """Build the C++ helpers into src/atlas_toolkit/bin (native/build.py; incremental, offline once _deps is
    fetched). With --skip-native, or without native sources, package what is already there, said out loud."""
    suffix = ".exe" if sys.platform == "win32" else ""
    exes = {h: SRC / "atlas_toolkit" / "bin" / f"atlas-{h}-core{suffix}" for h in HELPERS}
    driver = NATIVE / "build.py"
    if not skip and driver.is_file():
        args = [sys.executable, str(driver)]
        if (NATIVE / "_deps").is_dir():
            args.append("--no-fetch")
        subprocess.run(args, check=True)
    else:
        why = "--skip-native" if skip else "no native/build.py"
        print(f"WARNING: native helpers not rebuilt ({why}); packaging the ones in {exes['texture'].parent}")
    missing = [h for h, exe in exes.items() if not exe.is_file()]
    if missing:
        sys.exit(f"native helper(s) missing: {', '.join(missing)}; run python native/build.py before packaging")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--onedir", action="store_true", help="Windows: a folder instead of one file (macOS and Linux always are)")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--skip-native", action="store_true", help="package the helpers already built, without rebuilding")
    ap.add_argument("--no-splash", action="store_true", help="build without the splash screen")
    ap.add_argument("--no-self-test", action="store_true")
    a = ap.parse_args()
    # Native helper builders read UTF-8 vendored notices with Python's default
    # encoding. Keep their output stable on Windows systems using a legacy ACP.
    os.environ['PYTHONUTF8'] = '1'
    for stream in (sys.stdout, sys.stderr):      # redirected to a file, Windows writes cp1252: no "→" there
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    print(f"Atlas Toolkit {__version__} - Python {platform.python_version()} - {sys.platform}")
    native_helpers(a.skip_native)
    if not a.skip_tests:
        run_tests()
    STAGING.mkdir(parents=True, exist_ok=True)
    exe = build(a.onedir, not a.no_splash)
    if not exe.exists():
        sys.exit(f"build failed: {exe} was not written")
    print(f"\nbuilt {exe}  ({exe.stat().st_size / 1e6:.1f} MB)")
    if not WINDOWS and not MACOS:
        notice_system_libraries(STAGING / APP_ID)
    if not a.no_self_test:
        code = self_test(exe, a.onedir)
        if code:
            sys.exit("the built app failed its self-test")
    for source_name in ("developable-source.zip", "developable-surfaces.txt"):   # GPL: source beside the app
        source = SRC / "atlas_toolkit/licenses" / source_name
        if source.is_file():
            print(f"published {publish(source)}")
    if WINDOWS and not a.onedir:
        com = exe.with_name(f"{CONSOLE_NAME}.exe")
        print(f"published {publish(exe)}")
        if com.is_file():
            print(f"published {publish(com)}")
    else:
        built = STAGING / (f"{NAME}.app" if MACOS else NAME if WINDOWS else APP_ID)
        print(f"published {publish(built)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
