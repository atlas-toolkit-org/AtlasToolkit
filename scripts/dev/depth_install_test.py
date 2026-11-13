#!/usr/bin/env python3
"""
depth_install_test.py - The depth environment's installer for real: downloads (0.2 to 3 GB plus a 1.3 GB model), so it is
NOT part of the test suite (the name keeps unittest and run_tests.py away from it). Release builds run it on each OS
(.github/workflows/build.yml with `depth: true`, which release.yml sets); a person runs it with --gpu on an NVIDIA machine.
Standard library only, so any Python can drive a frozen app with it.

    python scripts/dev/depth_install_test.py --app "dist/Atlas Toolkit/atlas-toolkit.exe"    the built app's command line
    python scripts/dev/depth_install_test.py --source                                        python -m atlas_toolkit
        [--build auto|cu130|cu126|cpu|mac] [--gpu] [--no-reinstall] [--out DIR] [--keep]

Everything happens in a temporary profile (ATLAS_TOOLKIT_HOME, HF_HOME): nothing of this computer's own setup is used
or changed. The chain, each step reported ok / FAIL:
  1. components list: what this computer has, and the build it would get
  2. the pinned wheels of every build for this OS and Python exist on their indexes (no download)
  3. components install depth --yes, timed and measured against the sizes in depth_runtime.json
  4. components check depth --model …: a real model run (CPU on a runner; --gpu requires CUDA)
  5. Photo Hatching on a generated photo with depth from the model: the drawing exists and is signed
  6. installing over itself swaps the environment (skipped by --no-reinstall)
  7. components remove depth --models: nothing left
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = json.loads((ROOT / "src" / "atlas_toolkit" / "geom" / "depth_runtime.json").read_text(encoding="utf-8"))
OS = "win32" if sys.platform == "win32" else "darwin" if sys.platform == "darwin" else "linux"
results: list = []


def say(ok: bool, text: str) -> bool:
    print(f"{'ok  ' if ok else 'FAIL'} {text}", flush=True)
    results.append(ok)
    return ok


def run(cmd: list, env: dict, timeout: float, echo: bool = True) -> tuple[int, str]:
    print(f"$ {' '.join(str(c) for c in cmd)}", flush=True)
    t0 = time.monotonic()
    proc = subprocess.run([str(c) for c in cmd], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    if echo:
        print("\n".join("    " + line for line in proc.stdout.splitlines()[-60:]), flush=True)
    print(f"    (exit {proc.returncode}, {time.monotonic() - t0:.0f} s)", flush=True)
    return proc.returncode, proc.stdout


def mb(path: Path) -> float:
    """Size on disk; links (uv's short name for its Python is a junction on Windows) are not followed."""
    total = 0
    for folder, _dirs, files in os.walk(path):          # os.walk follows neither symlinks nor junctions
        for name in files:
            f = Path(folder) / name
            if not f.is_symlink():
                total += f.stat().st_size
    return total / 2**20


def photo(path: Path, w: int = 640, h: int = 480) -> Path:
    """A PNG with no library: a lit sphere in front of a shaded wall, enough for a depth model to find a form."""
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            g = 0.3 + 0.4 * x / w - 0.1 * y / h
            dx, dy = (x - 0.62 * w) / (0.25 * h), (y - 0.5 * h) / (0.25 * h)
            if dx * dx + dy * dy < 1:
                g = 0.9 - 0.5 * ((dx + 0.3) ** 2 + (dy + 0.3) ** 2) ** 0.5
            v = max(0, min(255, int(g * 255)))
            row += bytes((v, int(v * 0.9), int(v * 0.8)))
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b""))
    return path


def wheel_exists(build: str) -> tuple[bool, str]:
    """Is the pinned torch wheel for this OS and Python on the build's index? Reads the index page, downloads no wheel."""
    b = LOCK["builds"][build]
    tag = "cp" + LOCK["python"].replace(".", "")
    if not b["index"]:            # PyPI (macOS)
        with urllib.request.urlopen(f"https://pypi.org/pypi/torch/{LOCK['torch']}/json", timeout=60) as r:
            names = [u["filename"] for u in json.load(r)["urls"]]
        want = re.compile(rf"torch-{re.escape(LOCK['torch'])}-{tag}-{tag}-macosx_\d+_\d+_arm64\.whl")
    else:
        with urllib.request.urlopen(f"{b['index']}/torch/", timeout=60) as r:
            names = re.findall(r">([^<]+\.whl)<", r.read().decode("utf-8", "replace"))
        plat = "win_amd64" if OS == "win32" else r"manylinux_\d+_\d+_x86_64"
        want = re.compile(rf"torch-{re.escape(LOCK['torch'])}\+{build}-{tag}-{tag}-{plat}\.whl")
    found = [n for n in names if want.fullmatch(n.replace("%2B", "+"))]
    return bool(found), found[0] if found else f"no {want.pattern} on {b['index'] or 'PyPI'}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    how = ap.add_mutually_exclusive_group(required=True)
    how.add_argument("--app", type=Path, help="the app's command-line program (atlas-toolkit.exe, the AppImage, …)")
    how.add_argument("--source", action="store_true", help="run from this checkout (python -m atlas_toolkit)")
    ap.add_argument("--build", default="auto")
    ap.add_argument("--gpu", action="store_true", help="require the model to run on an NVIDIA GPU (CUDA)")
    ap.add_argument("--no-reinstall", action="store_true", help="skip installing over itself (saves a second download)")
    ap.add_argument("--out", type=Path, help="keep everything here (default: a temporary folder, removed afterwards)")
    ap.add_argument("--keep", action="store_true", help="keep the temporary folder")
    a = ap.parse_args()
    app = [str(a.app.resolve())] if a.app else [sys.executable, "-m", "atlas_toolkit"]
    work = a.out.resolve() if a.out else Path(tempfile.mkdtemp(prefix="atlas_depth_test_"))
    work.mkdir(parents=True, exist_ok=True)
    home = work / "home"
    env = {k: v for k, v in os.environ.items() if k not in ("ATLAS_TOOLKIT_ML_PYTHON", "VIRTUAL_ENV")}
    env.update(ATLAS_TOOLKIT_HOME=str(home), HF_HOME=str(work / "hf"), PYTHONUTF8="1",
               PYINSTALLER_SUPPRESS_SPLASH_SCREEN="1", QT_QPA_PLATFORM=env.get("QT_QPA_PLATFORM", "offscreen"))
    if a.source:
        env["PYTHONPATH"] = str(ROOT / "src")
    target = home / "runtime" / "ml-runtime"
    python = target / ("Scripts/python.exe" if OS == "win32" else "bin/python")
    model = LOCK["model"]["key"]
    try:
        print("--- 1. what this computer has", flush=True)
        code, out = run([*app, "components", "list"], env, 120)
        say(code == 0 and "would install" in out, "components list names a build for this computer")

        print("--- 2. the pinned wheels exist", flush=True)
        for key, b in LOCK["builds"].items():
            if OS in b["platforms"]:
                try:
                    ok, what = wheel_exists(key)
                except OSError as e:
                    ok, what = False, str(e)
                say(ok, f"{key}: {what}")

        print("--- 3. install", flush=True)
        t0 = time.monotonic()
        code, out = run([*app, "components", "install", "depth", "--build", a.build, "--yes"], env, 5400)
        took = time.monotonic() - t0
        say(code == 0 and "Installed." in out and python.exists(), f"installed in {took / 60:.1f} min into {target}")
        stamp = json.loads((target / "atlas-runtime.json").read_text(encoding="utf-8")) if python.exists() else {}
        build = stamp.get("build", "?")
        say(bool(stamp) and stamp.get("revision") == LOCK["revision"], f"stamped: {stamp.get('label')}, torch {stamp.get('torch')}")
        b = LOCK["builds"].get(build, {})
        size, base = mb(target), mb(target.with_name("ml-runtime-base"))
        expected = b.get("installed", {}).get(OS, 0) + LOCK["sizes"]["packages"]["installed"]
        print(f"     sizes: environment {size:.0f} MB (the lock expects about {expected:.0f}), tools {base:.0f} MB "
              f"(python {LOCK['sizes']['python']['installed']} + uv)", flush=True)
        if expected and not (0.6 < size / expected < 1.5):
            print(f"note the lock's size for {build} on {OS} is off by more than 40%: update depth_runtime.json", flush=True)

        env["ATLAS_TOOLKIT_ML_PYTHON"] = str(python)      # this environment, whatever else this computer has
        print("--- 4. a real model run", flush=True)
        code, out = run([*app, "components", "check", "depth", "--model", model], env, 3600)
        say(code == 0 and "works" in out, f"the model {model} runs")
        cuda = bool(re.search(r"device NVIDIA|runs on NVIDIA", out))
        if a.gpu:
            say(cuda, "it runs on the NVIDIA GPU")
        else:
            print(f"note the model ran on {'the NVIDIA GPU' if cuda else 'the CPU'}", flush=True)

        print("--- 5. Photo Hatching with depth from the model", flush=True)
        pic = photo(work / "sphere.png")
        outdir = work / "drawn"
        code, out = run([*app, "run", "dpt-hatching", pic, "--depth-source", "model", "--model", model, "--save-depth",
                         "--out-dir", outdir], env, 1800)
        svg = outdir / "sphere_photohatch.svg"
        signed = svg.exists() and "<desc>dpt-hatching</desc>" in svg.read_text(encoding="utf-8")
        say(code == 0 and signed and (outdir / "sphere_depth.png").exists(), "a signed drawing and its depth map")

        if not a.no_reinstall:
            print("--- 6. installing over itself", flush=True)
            env.pop("ATLAS_TOOLKIT_ML_PYTHON")
            code, out = run([*app, "components", "install", "depth", "--build", build, "--yes"], env, 5400)
            say(code == 0 and "Replaces" in out and python.exists() and not target.with_name("ml-runtime.old").exists(),
                "the new environment replaced the old one")

        print("--- 7. remove", flush=True)
        code, out = run([*app, "components", "remove", "depth", "--models", "--yes"], env, 600)
        left = [p for p in (target, target.with_name("ml-runtime-base"), target.with_name("ml-runtime.partial"))
                if p.exists()]
        say(code == 0 and not left, f"nothing left{': ' + ', '.join(map(str, left)) if left else ''}")
    except subprocess.TimeoutExpired as e:
        say(False, f"timed out: {e.cmd}")
    finally:
        if not (a.out or a.keep):
            shutil.rmtree(work, ignore_errors=True)
    print(f"\n{sum(results)} of {len(results)} passed", flush=True)
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
