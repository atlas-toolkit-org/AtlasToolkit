#!/usr/bin/env python3
"""
package.py - Make the installers for this OS from a folder build, into dist/installers. It never publishes them anywhere.

    python scripts/package.py                build first (scripts/build.py, tests skipped), then package
    python scripts/package.py --skip-build   package what scripts/build.py already made
    python scripts/package.py --only deb     just one format

Windows: an Inno Setup installer, from `scripts/build.py --onedir`. Linux: deb and rpm with nfpm and an AppImage with
appimagetool, in packaging/linux/Dockerfile. macOS: a DMG with dmgbuild. `python scripts/doctor.py` says what is missing, and
docs/PACKAGING.md explains how each one is made and tested.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
OUT = DIST / "installers"
STAGING = ROOT / "build" / "release"
WINDOWS, MACOS = sys.platform == "win32", sys.platform == "darwin"


def version() -> str:
    text = (ROOT / "src" / "atlas_toolkit" / "version.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text)[1]


def find_iscc() -> str:
    found = shutil.which("ISCC")
    if found:
        return found
    for base in (os.environ.get("LOCALAPPDATA", "") and Path(os.environ["LOCALAPPDATA"]) / "Programs",
                 os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base and (Path(base) / "Inno Setup 6" / "ISCC.exe").is_file():
            return str(Path(base) / "Inno Setup 6" / "ISCC.exe")
    sys.exit("Inno Setup is missing: winget install --id JRSoftware.InnoSetup")


def package_windows(ver: str) -> list[Path]:
    source, icon = DIST / "Atlas Toolkit", STAGING / "atlas.ico"
    if not (source / "Atlas Toolkit.exe").is_file() or not icon.is_file():
        sys.exit(f"no folder build in {source}: run scripts/build.py --onedir first")
    subprocess.run([find_iscc(), "/Qp", f"/DVersion={ver}", f"/DSource={source}", f"/DOutput={OUT}", f"/DRoot={ROOT}", f"/DIcon={icon}",
                    str(ROOT / "packaging" / "windows" / "atlas_toolkit.iss")], check=True)
    return [OUT / f"AtlasToolkit-{ver}-windows-x64-setup.exe"]


def package_nfpm(ver: str, kind: str) -> Path:
    nfpm = shutil.which("nfpm")
    if not nfpm:
        sys.exit("nfpm is missing: it comes with packaging/linux/Dockerfile, or see https://nfpm.goreleaser.com/install/")
    name = f"atlas-toolkit_{ver}_amd64.deb" if kind == "deb" else f"atlas-toolkit-{ver}-1.x86_64.rpm"
    text = (ROOT / "packaging" / "linux" / "nfpm.yaml").read_text(encoding="utf-8")   # nfpm does not expand these in every field
    for key, value in {"VERSION": ver, "APP_DIR": DIST / "atlas-toolkit", "ICON_DIR": STAGING / "icons"}.items():
        text = text.replace("${" + key + "}", str(value))
    config = STAGING / "nfpm.yaml"
    config.write_text(text, encoding="utf-8")
    subprocess.run([nfpm, "package", "--config", str(config), "--packager", kind, "--target", str(OUT / name)], cwd=ROOT, check=True)
    return OUT / name


def package_appimage(ver: str) -> Path:
    """The same folder the deb and rpm install, as one file. It runs on the libraries they declare, which any desktop has."""
    tool = shutil.which("appimagetool")
    if not tool:
        sys.exit("appimagetool is missing: it comes with packaging/linux/Dockerfile")
    linux = ROOT / "packaging" / "linux"
    appdir = STAGING / "AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    shutil.copytree(DIST / "atlas-toolkit", appdir / "usr" / "lib" / "atlas-toolkit", symlinks=True)
    for source in (linux / "AppRun", linux / "atlas-toolkit.desktop", STAGING / "icons" / "256x256" / "apps" / "atlas-toolkit.png"):
        shutil.copy2(source, appdir)
    (appdir / "AppRun").chmod(0o755)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, appdir)
    target = OUT / f"AtlasToolkit-{ver}-linux-x86_64.AppImage"
    runtime = os.environ.get("APPIMAGE_RUNTIME")   # pinned in the Dockerfile; without it the tool downloads the latest
    subprocess.run([tool, "--no-appstream", *(["--runtime-file", runtime] if runtime else []), str(appdir), str(target)],
                   env=dict(os.environ, ARCH="x86_64"), check=True)
    return target


def package_linux(ver: str, only: str | None) -> list[Path]:
    if not (DIST / "atlas-toolkit" / "atlas-toolkit").is_file() or not (STAGING / "icons").is_dir():
        sys.exit(f"no build in {DIST / 'atlas-toolkit'}: run scripts/build.py first")
    made = [package_nfpm(ver, kind) for kind in ("deb", "rpm") if only in (None, kind)]
    return made + ([package_appimage(ver)] if only in (None, "appimage") else [])


def package_macos(ver: str) -> list[Path]:
    app = DIST / "Atlas Toolkit.app"
    if not app.is_dir():
        sys.exit(f"no build at {app}: run scripts/build.py first")
    target = OUT / f"AtlasToolkit-{ver}-macos-arm64.dmg"
    subprocess.run([sys.executable, "-m", "dmgbuild", "-s", str(ROOT / "packaging" / "macos" / "dmg_settings.py"), "-D", f"app={app}",
                    "Atlas Toolkit", str(target)], check=True)
    return [target]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-build", action="store_true", help="package what scripts/build.py already made")
    ap.add_argument("--only", choices=("deb", "rpm", "appimage"), help="one Linux format")
    a = ap.parse_args()
    if not a.skip_build:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build.py"), "--skip-tests"] + (["--onedir"] if WINDOWS else []), check=True)
    OUT.mkdir(parents=True, exist_ok=True)
    ver = version()
    made = package_windows(ver) if WINDOWS else package_macos(ver) if MACOS else package_linux(ver, a.only)
    for path in made:
        print(f"made {path}  ({path.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
