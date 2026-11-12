#!/usr/bin/env python3
"""
setup_ml.py - Create the toolkit's GPU runtime for depth models (Photo Hatching): a Python environment with
PyTorch (CUDA), transformers and the few libraries the depth server needs. It stays outside the exe (~4.5 GB);
the app finds it and runs the depth server with it.

    python scripts/setup_ml.py                 create or update .venv-ml in this checkout (CUDA 13.0 PyTorch)
    python scripts/setup_ml.py --user          ... in the per-user runtime folder instead, found by any installed
                                               exe (%LOCALAPPDATA%\\AtlasToolkit\\ml-runtime on Windows)
    python scripts/setup_ml.py --cuda cu128    another CUDA build (cu128 also supports RTX 50-series)
    python scripts/setup_ml.py --cpu           CPU-only PyTorch (slow; for machines without an NVIDIA GPU)

macOS has no CUDA builds: there PyPI's PyTorch is used whatever the options say. It runs on the CPU unless
ATLAS_DEPTH_DEVICE=mps asks for Apple's GPU, which is untested with these models so far.
    python scripts/setup_ml.py --check [photo] report the device and run each model once (downloads weights)

Model weights (~1.3 GB each) are downloaded on first use into the Hugging Face cache (~/.cache/huggingface).
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ["transformers>=4.46", "numpy", "pillow", "safetensors", "huggingface_hub"]

# The app's own path rules (standard library only), loaded by file so no dependency needs installing first.
_spec = importlib.util.spec_from_file_location("atlas_paths", ROOT / "src" / "atlas_toolkit" / "core" / "paths.py")
paths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(paths)


def target(user: bool) -> Path:
    return paths.ml_runtime_dir() if user else ROOT / ".venv-ml"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", action="store_true", help="use the per-user runtime folder instead of .venv-ml")
    ap.add_argument("--cuda", default="cu130", help="PyTorch CUDA build (cu130, cu128 …)")
    ap.add_argument("--cpu", action="store_true", help="CPU-only PyTorch")
    ap.add_argument("--check", action="store_true", help="test the runtime and every model")
    ap.add_argument("image", nargs="*", help="with --check: a photo to test on (default: a synthetic scene), "
                                             "and/or the model keys to test (default: all)")
    a = ap.parse_args()
    env_dir = target(a.user)
    py = paths.venv_python(env_dir)
    if a.check:
        server = ROOT / "src" / "atlas_toolkit" / "ml" / "depth_server.py"
        return subprocess.call([str(py), str(server), "--check"] + a.image)
    if not py.exists():
        print(f"creating {env_dir} …")
        env_dir.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True).create(env_dir)
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    index = "https://download.pytorch.org/whl/" + ("cpu" if a.cpu else a.cuda)
    subprocess.run([str(py), "-m", "pip", "install", "torch", *([] if sys.platform == "darwin" else ["--index-url", index])],
                   check=True)
    subprocess.run([str(py), "-m", "pip", "install", *PACKAGES], check=True)
    out = subprocess.run([str(py), "-c", "import torch; print(torch.__version__, torch.version.cuda, "
                                         "torch.cuda.is_available() and torch.cuda.get_device_name(0))"],
                         capture_output=True, text=True)
    print(f"\nready: {env_dir}\n  torch, CUDA, device: {out.stdout.strip()}")
    print("next: python scripts/setup_ml.py " + ("--user " if a.user else "")
          + "--check   (downloads and tests the depth models)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
