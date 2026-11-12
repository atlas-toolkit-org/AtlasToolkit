#!/usr/bin/env python3
"""
native/build.py - Build Atlas Toolkit's native helpers and install them into the Python package.

    python native/build.py                       fetch pinned deps, build all four helpers, install
    python native/build.py --only remesh,texture build some of them
    python native/build.py --no-fetch            offline: use native/_deps as it is (revisions still checked)
    python native/build.py --check               report the toolchain and dependency state, build nothing
    python native/build.py --clean               remove this platform's build tree first

Helpers: texture (xatlas UV charts), stripes (Geometry Central stripe patterns), remesh (PMP decimate/remesh),
developable (Stein, Grinspun and Crane). Each one is an atlas-<name>-core executable, installed with its
third-party notice into src/atlas_toolkit/{bin,licenses}; developable also gets its GPL corresponding-source
archive. Needs Git, CMake 3.24+, Ninja and a C++17 compiler. On Windows the Visual Studio C++ Build Tools are
found through vswhere (with their bundled CMake and Ninja); nothing is installed and PATH is not changed.
Pinned upstream revisions live in native/deps.lock.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

NATIVE = Path(__file__).resolve().parent
ROOT = NATIVE.parent
DEPS = NATIVE / "_deps"
LOCK = json.loads((NATIVE / "deps.lock.json").read_text(encoding="utf-8"))
PACKAGE = ROOT / "src" / "atlas_toolkit"
HELPERS = ("texture", "stripes", "remesh", "developable")
EXE = ".exe" if sys.platform == "win32" else ""
PRESET = {"win32": "windows-msvc", "darwin": "macos"}.get(sys.platform, "linux")
DEVELOPABLE_SOLVER_REVISION = "67848c18c3b0c6389fef9b165a37fe7db5eed0f3"
XATLAS_REVISION = "f700c7790aaa030e794b52ba7791a05c085faf0c"


def run(args, **kw):
    print(" ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True, **kw)


def git_head(folder: Path) -> str:
    try:
        state = subprocess.run(["git", "-C", str(folder), "rev-parse", "HEAD"], text=True, capture_output=True)
    except OSError:          # no git at all
        return ""
    return state.stdout.strip() if state.returncode == 0 else ""


def needed_deps(helpers) -> list[str]:
    return [name for name, d in LOCK.items() if not name.startswith("_") and set(d["used_by"]) & set(helpers)]


def fetch(names) -> None:
    """Shallow-fetch each dependency at its pinned revision into native/_deps/<name>."""
    for name in names:
        dep, folder = LOCK[name], DEPS / name
        if not folder.exists():
            folder.mkdir(parents=True)
            run(["git", "init", "-q", folder])
            run(["git", "-C", folder, "remote", "add", "origin", dep["url"]])
        if git_head(folder) != dep["revision"]:
            # Never discard edits to the dependency checkouts.
            dirty = subprocess.check_output(["git", "-C", str(folder), "status", "--porcelain"], text=True)
            if dirty.strip():
                raise RuntimeError(f"local edits in {folder}; preserve them before updating")
            run(["git", "-C", folder, "fetch", "--depth", "1", "origin", dep["revision"]])
            run(["git", "-C", folder, "checkout", "-q", "--detach", dep["revision"]])
        if dep.get("submodules"):
            run(["git", "-C", folder, "submodule", "update", "--init", "--recursive", "--depth", "1"])


def verify(names) -> None:
    for name in names:
        actual = git_head(DEPS / name)
        if actual != LOCK[name]["revision"]:
            raise RuntimeError(f"{name} must be at {LOCK[name]['revision']} (found {actual or 'nothing'}); "
                               "run native/build.py without --no-fetch")


def toolchain():
    """CMake, Ninja and the compiler environment, without editing PATH permanently."""
    env = dict(os.environ)
    env["VSLANG"] = "1033"  # Ninja recognises the English /showIncludes prefix.
    cmake = shutil.which("cmake")
    if sys.platform == "win32":
        program_files = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        vswhere = program_files / "Microsoft Visual Studio/Installer/vswhere.exe"
        if not vswhere.is_file():
            raise RuntimeError("C++ build tools missing. Install Visual Studio C++ Build Tools and CMake, then retry.")
        base = subprocess.check_output([str(vswhere), "-latest", "-products", "*", "-requires",
                                        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property",
                                        "installationPath"], text=True).strip()
        if not base:
            raise RuntimeError("the installed Visual Studio lacks the x64 C++ tools")
        dev = Path(base) / "VC/Auxiliary/Build/vcvars64.bat"
        # vcvars64 probes for vswhere on PATH and complains when it is not there; its exit code is what counts.
        # /u: `set` prints UTF-16, so values in any language survive (the console code page would not, and
        # Python's own default decoding depends on PYTHONUTF8).
        result = subprocess.check_output(f'cmd.exe /u /d /s /c ""{dev}" >nul && set"',
                                         stderr=subprocess.DEVNULL).decode("utf-16-le", errors="replace")
        for line in result.splitlines():
            key, sep, value = line.partition("=")
            if sep and key:
                # Windows environment keys are case insensitive; normal dicts are not.
                old = next((k for k in env if k.lower() == key.lower()), key)
                env[old] = value
        cmake_base = Path(base) / "Common7/IDE/CommonExtensions/Microsoft/CMake"
        cmake = cmake or str(cmake_base / "CMake/bin/cmake.exe")
        ninja = cmake_base / "Ninja/ninja.exe"
        path_key = next(k for k in env if k.lower() == "path")
        env[path_key] = str(ninja.parent) + os.pathsep + env[path_key]
    elif not shutil.which("ninja"):
        raise RuntimeError("Ninja missing; install ninja (e.g. apt install ninja-build, brew install ninja)")
    if not cmake or not Path(cmake).is_file():
        raise RuntimeError("CMake missing; install CMake 3.24+ and a C++17 compiler before building")
    version = subprocess.check_output([cmake, "--version"], text=True, env=env).split()[2]
    if tuple(int(x) for x in version.split("-")[0].split(".")[:2]) < (3, 24):
        raise RuntimeError(f"CMake {version} is too old; the helpers need 3.24 or later")
    env["VSLANG"] = "1033"
    return cmake, env


def compiler(env: dict) -> str:
    """Compile and link a small C++17 program the way the helpers are built, and name the compiler that did it. This
    finds what a CMake failure would explain badly, such as Fedora's separate libstdc++-static package."""
    with tempfile.TemporaryDirectory(prefix="atlas_native_check_") as folder:
        source = Path(folder) / "check.cpp"
        source.write_text("#include <string_view>\n#include <thread>\n"
                          "int main() { std::thread t([] {}); t.join(); return 0; }\n", encoding="utf-8")
        if sys.platform == "win32":
            path = next(v for k, v in env.items() if k.lower() == "path")
            cl = shutil.which("cl", path=path)
            if not cl:
                raise RuntimeError("cl.exe missing; install the Visual Studio C++ x64 tools")
            args, name = [cl, "/nologo", "/std:c++17", "/EHsc", "/MT", source.name], None
        else:
            cxx = env.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
            if not cxx:
                raise RuntimeError("C++ compiler missing; install g++ or clang++ (macOS: xcode-select --install)")
            args = [cxx, "-std=c++17", "-pthread", source.name, "-o", "check"]
            if sys.platform.startswith("linux"):
                args += ["-static-libstdc++"]
            name = subprocess.check_output([cxx, "--version"], text=True, env=env).splitlines()[0]
        done = subprocess.run(args, cwd=folder, env=env, capture_output=True, text=True, errors="replace")
        if done.returncode:
            hint = " (Fedora and RHEL: install libstdc++-static)" if "stdc++" in done.stderr + done.stdout else ""
            raise RuntimeError(f"cannot compile C++17{hint}: {(done.stderr or done.stdout).strip()[-300:]}")
        return name or subprocess.run([cl], env=env, capture_output=True, text=True, errors="replace").stderr.splitlines()[0]


def build(helpers, jobs: int, clean: bool) -> Path:
    cmake, env = toolchain()
    tree = NATIVE / "build" / PRESET
    if clean and tree.exists():
        shutil.rmtree(tree)
    env["CMAKE_BUILD_PARALLEL_LEVEL"] = str(jobs)
    options = [f"-DATLAS_NATIVE_{h.upper()}={'ON' if h in helpers else 'OFF'}" for h in HELPERS]
    run([cmake, "--preset", PRESET, f"-DPython3_EXECUTABLE={sys.executable}", *options], cwd=NATIVE, env=env)
    run([cmake, "--build", "--preset", PRESET, "--parallel", jobs], cwd=NATIVE, env=env)
    return tree / "stage" / "bin"


# --- third-party notices, written next to the executables -------------------------------------------------

def _section(title: str, text: str) -> str:
    return "\n\n" + title + "\n" + "=" * len(title) + "\n" + text


def by_name(paths) -> list[Path]:
    """The same order on every OS, so a notice does not change between them: glob's order depends on the disk, and
    sorting Windows paths ignores case."""
    return sorted(paths, key=lambda p: p.name)


def notice_texture() -> str:
    dep = NATIVE / "texture/third_party/xatlas"
    # Hash LF bytes: a Windows checkout has CRLF working copies, and the notice must not change between OSes.
    hashes = "\n".join(f"{p.name}: {hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()}"
                       for p in by_name(dep.iterdir()) if p.is_file())
    licence = (dep / "LICENSE").read_text(encoding="utf-8")
    return f"Atlas Surface Texture helper 0.1.0\nxatlas {XATLAS_REVISION}\n{hashes}\n\n" + licence


def notice_stripes() -> str:
    gc = DEPS / "geometry-central"
    notices = ["Surface Stripes native backend - third-party notices\n",
               f"Geometry Central {LOCK['geometry-central']['revision']}; Eigen 3.4.0.\n",
               "Original method: Knoppel, Crane, Pinkall and Schroder, Stripe Patterns on Surfaces (2015).\n"]
    sources = [("Geometry Central", gc / "LICENSE"),
               ("happly", gc / "deps/happly/LICENSE"),
               ("Keenan Crane reference implementation", NATIVE / "stripes/NOTICE-StripePatterns.txt")]
    sources += [("Eigen " + p.name, p) for p in by_name((DEPS / "eigen").glob("COPYING.*"))]
    for title, path in sources:
        if path.is_file():
            notices.append(_section(title, path.read_text(encoding="utf-8")))
    # nanoflann/nanort ship their notices in their single-header sources.
    for library, path in [("nanoflann", gc / "deps/nanoflann/include/nanoflann.hpp"),
                          ("nanort", gc / "deps/nanort/include/nanort.h")]:
        header = path.read_text(encoding="utf-8")
        notices.append(_section(library, header[:header.find("#ifndef")]))
    return "".join(notices)


def notice_remesh() -> str:
    pmp = DEPS / "pmp"
    notices = ["Atlas Remesh/Decimate helper 0.1.1, PMP " + LOCK["pmp"]["revision"] + " (MIT).\n"
               "Atlas additions: boundary locks, regional density, progress and intermediate size guard.\n",
               (pmp / "LICENSE.txt").read_text(encoding="utf-8")]
    for path in by_name((pmp / "external/eigen-3.4.0").glob("COPYING.*")):
        notices += ["\nEigen " + path.name + "\n", path.read_text(encoding="utf-8")]
    return "\n".join(notices)


def notice_developable() -> str:
    here = NATIVE / "developable"
    notices = ["Developable Surfaces native helper 0.1.0\n",
               "Stein, Grinspun and Crane, Developability of Triangle Meshes (2018).\n",
               f"Solver source {DEVELOPABLE_SOLVER_REVISION}; Atlas adaptation is GPL-2.0-or-later.\n"]
    paths = by_name(here.glob("*LICENSE*")) + [here / "GPLv2.txt", here / "LGPLv2.1.txt"]
    paths += by_name((DEPS / "libigl").glob("*LICENSE*")) + by_name((DEPS / "eigen").glob("COPYING.*"))
    paths += [DEPS / "json/LICENSE.MIT"]
    for p in paths:
        if p.is_file():
            notices.append("\n" + p.name + "\n" + p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(notices)


NOTICES = {"texture": ("surface-texture.txt", notice_texture),
           "stripes": ("surface-stripes.txt", notice_stripes),
           "remesh": ("remesh-decimate.txt", notice_remesh),
           "developable": ("developable-surfaces.txt", notice_developable)}


def developable_source_zip(dest: Path) -> None:
    """GPL corresponding source: the helper, the exact dependency headers it compiles and every licence,
    with a hash manifest. Unzipped, `cmake -S atlas_developable -B build` rebuilds it offline."""
    here = NATIVE / "developable"
    files = [(p, "atlas_developable/" + p.relative_to(here).as_posix()) for p in sorted(here.rglob("*"))
             if p.is_file() and not ({"build", "__pycache__"} & set(p.relative_to(here).parts))]
    keep = {"libigl": ("include",), "eigen": ("Eigen", "signature_of_eigen3_matrix_library"),
            "json": ("single_include",)}
    deps = []
    for name, subs in keep.items():
        for p in sorted((DEPS / name).rglob("*")):
            rel = p.relative_to(DEPS / name)
            if not p.is_file() or ".git" in rel.parts:
                continue
            if rel.parts[0] in subs or rel.parts[0].startswith(("LICENSE", "COPYING")):
                deps.append((p, f"deps/{name}/{rel.as_posix()}"))
    manifest = {"solver_revision": DEVELOPABLE_SOLVER_REVISION,
                "dependencies": {k: LOCK[k]["revision"] for k in keep},
                "files": {arc: hashlib.sha256(p.read_bytes()).hexdigest() for p, arc in deps}}
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p, arc in files:
            z.write(p, arc)
        for p, arc in deps:
            z.write(p, "atlas_developable/" + arc)
        z.writestr("atlas_developable/source-manifest.json", json.dumps(manifest, indent=2))


def install(stage: Path, helpers, package: Path) -> None:
    (package / "bin").mkdir(parents=True, exist_ok=True)
    (package / "licenses").mkdir(parents=True, exist_ok=True)
    for h in helpers:
        name = f"atlas-{h}-core{EXE}"
        shutil.copy2(stage / name, package / "bin" / name)
        notice, writer = NOTICES[h]
        (package / "licenses" / notice).write_text(writer(), encoding="utf-8")
        print(f"Installed {package / 'bin' / name}", flush=True)
    if "developable" in helpers:
        developable_source_zip(package / "licenses" / "developable-source.zip")


def check(helpers) -> int:
    ok = True
    try:
        cmake, env = toolchain()
        version = subprocess.check_output([cmake, "--version"], text=True, env=env).splitlines()[0]
        print(f"toolchain: {version} ({cmake}), preset {PRESET}")
        print(f"compiler:  {compiler(env)}")
    except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
        ok = False
        print(f"toolchain: MISSING - {e}")
    for name in needed_deps(helpers):
        actual = git_head(DEPS / name)
        state = "ok" if actual == LOCK[name]["revision"] else ("missing" if not actual else f"at {actual[:12]}")
        ok &= state == "ok"
        print(f"dep {name:17} {LOCK[name]['revision'][:12]}  {state}")
    for h in helpers:
        exe = PACKAGE / "bin" / f"atlas-{h}-core{EXE}"
        state = "not built"
        if exe.is_file():
            try:      # a helper from another OS or CPU is on disk but cannot start
                state = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
            except OSError as e:
                ok, state = False, f"cannot run: {e}"
        print(f"helper {h:12} {state or 'installed'}  {exe}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma-separated helpers: " + ",".join(HELPERS))
    ap.add_argument("--no-fetch", action="store_true", help="use native/_deps offline (revisions still checked)")
    ap.add_argument("--check", action="store_true", help="report toolchain and dependencies, build nothing")
    ap.add_argument("--clean", action="store_true", help="remove this platform's build tree first")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--package", type=Path, default=PACKAGE, help="Python package that receives bin/ and licenses/")
    a = ap.parse_args()
    helpers = [h.strip() for h in a.only.split(",")] if a.only else list(HELPERS)
    unknown = set(helpers) - set(HELPERS)
    if unknown:
        ap.error(f"unknown helper(s): {', '.join(sorted(unknown))}")
    if a.check:
        return check(helpers)
    names = needed_deps(helpers)
    if not a.no_fetch:
        fetch(names)
    verify(names)
    stage = build(helpers, a.jobs, a.clean)
    install(stage, helpers, a.package)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        sys.exit(f"native/build.py: {e}")
