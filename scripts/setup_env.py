#!/usr/bin/env python3
"""
setup_env.py - Create the toolkit's own environment in .venv and install it (editable) with the pinned versions, so a
fresh checkout builds the same app on any machine. Standard library only.

    python scripts/setup_env.py            create or update .venv, with uv when it is installed, otherwise pip
    python scripts/setup_env.py --lock     after changing dependencies: refresh uv.lock and requirements.lock.txt

uv.lock is the source of truth: one lock for every supported OS, which fails if a target lacks a wheel.
requirements.lock.txt is exported from it (hashes and platform markers) for machines without uv; --lock needs uv.
Needs Python 3.12 to 3.14 (developed on 3.14). `python scripts/doctor.py` checks everything else the build needs.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
LOCK = ROOT / "requirements.lock.txt"
EXPORT = ["export", "--quiet", "--format", "requirements-txt", "--no-emit-project", "--all-extras", "--all-groups",
          "--locked", "--output-file", LOCK.name]       # relative: uv copies this into the file's header, and it runs in ROOT


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def main() -> int:
    if not (3, 12) <= sys.version_info[:2] <= (3, 14):
        sys.exit(f"Atlas Toolkit needs Python 3.12 to 3.14; this is {sys.version.split()[0]}")
    uv = shutil.which("uv")
    if "--lock" in sys.argv:
        if not uv:
            sys.exit("--lock needs uv: https://docs.astral.sh/uv/getting-started/installation/")
        subprocess.run([uv, "lock"], cwd=ROOT, check=True)
        subprocess.run([uv, *EXPORT], cwd=ROOT, check=True)
        print(f"wrote {ROOT / 'uv.lock'} and {LOCK}")
        return 0
    if uv:
        subprocess.run([uv, "sync", "--locked", "--all-extras"], cwd=ROOT, check=True)
    else:
        if not venv_python().exists():
            print(f"creating {VENV} …")
            venv.EnvBuilder(with_pip=True).create(VENV)
        py = str(venv_python())
        subprocess.run([py, "-m", "pip", "install", "--upgrade", "pip"], check=True)
        subprocess.run([py, "-m", "pip", "install", "--require-hashes", "-r", str(LOCK)], check=True)
        subprocess.run([py, "-m", "pip", "install", "--no-deps", "-e", str(ROOT)], check=True)
    print(f"\nready: {venv_python()} -m atlas_toolkit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
