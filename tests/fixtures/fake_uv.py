"""
A stand-in for uv in tests/test_depth_runtime.py: the commands the depth installer runs, done in seconds with no download.

    python install VER            a fake managed Python (a few files) in UV_PYTHON_INSTALL_DIR
    venv ... DIR                  a real venv of the Python running this script (so the probe can run in it)
    pip install --python PY ...   stub packages for each name asked: torch, numpy, pillow, safetensors, transformers ...

FAKE_UV_FAIL="<word>:<output>" makes the first command containing <word> print <output> and fail.
FAKE_UV_SLOW="<word>" makes that command sleep (for cancelling). FAKE_TORCH_CUDA="1" makes the torch stub a CUDA build
on an sm_120 card; FAKE_TORCH_CUDA="broken" a CUDA build that cannot see the card.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
line = " ".join(args)

fail = os.environ.get("FAKE_UV_FAIL", "")
if fail:
    word, _, output = fail.partition(":")
    if word in line:
        print(output)
        sys.exit(2)
once = os.environ.get("FAKE_UV_FAIL_ONCE", "")       # the same, the first time only (a download that broke off)
if once:
    word, _, output = once.partition(":")
    marker = Path(os.environ["UV_CACHE_DIR"]) / "failed-once"
    if word in line and not marker.exists():
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("x", encoding="utf-8")
        print(output)
        sys.exit(2)
slow = os.environ.get("FAKE_UV_SLOW", "")
if slow and slow in line:
    for _ in range(600):
        time.sleep(0.1)

if args[:2] == ["python", "install"]:
    home = Path(os.environ["UV_PYTHON_INSTALL_DIR"]) / f"cpython-{args[2]}-fake"
    home.mkdir(parents=True, exist_ok=True)
    (home / "lib.bin").write_bytes(b"x" * 200_000)
    print(f"Installed Python {args[2]}")
    sys.exit(0)

if args[0] == "venv":
    target = args[-1]
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", target], check=True)
    print(f"Creating virtual environment at: {target}")
    sys.exit(0)

if args[:2] == ["pip", "install"]:
    py = args[args.index("--python") + 1]
    site = subprocess.run([py, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"], capture_output=True,
                          text=True, check=True).stdout.strip()
    cache = Path(os.environ["UV_CACHE_DIR"])
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"wheel-{len(list(cache.iterdir()))}.bin").write_bytes(b"x" * 100_000)
    skip = {"--python", "--index-url", "--exclude-newer"}
    names, it = [], iter(args[2:])
    for a in it:
        if a in skip:
            next(it, None)
            continue
        names.append(re.split(r"[=<>]", a)[0].lower())
    stubs = {"pillow": "PIL", "huggingface_hub": "huggingface_hub"}
    for name in names:
        version = next((a.split("==")[1] for a in args if a.lower().startswith(name + "==")), "1.0")
        pkg = Path(site) / stubs.get(name, name)
        pkg.mkdir(parents=True, exist_ok=True)
        if name == "torch":
            mode = os.environ.get("FAKE_TORCH_CUDA", "")
            cuda = "13.0" if mode else None
            (pkg / "__init__.py").write_text(f'''
__version__ = "{version}+{"cu130" if cuda else "cpu"}"
class version:
    cuda = {cuda!r}
class cuda:
    @staticmethod
    def is_available(): return {mode == "1"}
    @staticmethod
    def get_arch_list(): return ["sm_75", "sm_120"]
    @staticmethod
    def get_device_name(i): return "Fake RTX"
    @staticmethod
    def get_device_capability(i): return (12, 0)
class _T:
    def __init__(self, v): self.v = v
    def reshape(self, *a): return self
    def __matmul__(self, o): return self
    def cpu(self): return self
def arange(n, device=None): return _T(n)
def allclose(a, b): return True
''', encoding="utf-8")
        else:
            (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    print(f"Installed {len(names)} packages")
    sys.exit(0)

print(f"fake uv: unknown command {line}")
sys.exit(1)
