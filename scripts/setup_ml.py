#!/usr/bin/env python3
"""
setup_ml.py - The depth environment (PyTorch and the model libraries for Photo Hatching) for developers. It runs the
app's own installer (src/atlas_toolkit/geom/depth_runtime.py: pinned uv, its own Python, the build that suits this
computer, checked before it is put in place), the same one behind Setup › Components and `atlas-toolkit components`.
Run it with the dev environment's Python.

    .venv\\Scripts\\python scripts\\setup_ml.py                 .venv-ml in this checkout (found first from source)
    .venv\\Scripts\\python scripts\\setup_ml.py --user          the per-user folder instead, which installed apps find
                                                              (%LOCALAPPDATA%\\AtlasToolkit\\ml-runtime on Windows)
    .venv\\Scripts\\python scripts\\setup_ml.py --build cpu     another build (auto, cu130, cu126, cpu; mac on macOS)
    .venv\\Scripts\\python scripts\\setup_ml.py --check [photo] [model ...]   report the device and run the models

Model weights (~1.3 GB each) are downloaded on first use into the Hugging Face cache (~/.cache/huggingface).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from atlas_toolkit.core.components import _Printer  # noqa: E402
from atlas_toolkit.core.paths import ml_runtime_dir, venv_python  # noqa: E402
from atlas_toolkit.geom import depth_runtime  # noqa: E402


def target(user: bool) -> Path:
    return ml_runtime_dir() if user else ROOT / ".venv-ml"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", action="store_true", help="use the per-user runtime folder instead of .venv-ml")
    ap.add_argument("--build", default="auto", help="PyTorch build: auto (detected), cu130, cu126, cpu (mac on macOS)")
    ap.add_argument("--cpu", action="store_true", help="the same as --build cpu")
    ap.add_argument("--check", action="store_true", help="test the environment and run the models")
    ap.add_argument("image", nargs="*", help="with --check: a photo to test on (default: a synthetic scene), "
                                             "and/or the model keys to test (default: all)")
    a = ap.parse_args()
    env_dir = target(a.user)
    if a.check:
        server = depth_runtime.server_script()
        return subprocess.call([str(venv_python(env_dir)), str(server), "--check"] + a.image)
    build = depth_runtime.cpu_build() if a.cpu else a.build
    plan = depth_runtime.plan(build, target=env_dir)
    for head, text in plan.summary():
        print(f"  {head + ':':<10} {text}")
    for c in plan.checks:
        print(f"  [{c.level}] {c.title}: {c.text}")
    if not plan.ok():
        return 2
    result = depth_runtime.install(plan, _Printer(plan.stages()))
    print()
    print("\n".join(result.lines()))
    if result.ok:
        print("next: setup_ml.py " + ("--user " if a.user else "") + "--check   (downloads and tests the depth models)")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
