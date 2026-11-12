"""
Depth maps for tools that read photos: from a DPT-family model on the GPU (through the separate runtime), from a
depth-map file, or a rough guess from tone when neither is available.

The GPU runtime (.venv-ml in the repository, or the per-user ml-runtime folder; made by scripts/setup_ml.py) runs
ml/depth_server.py as a subprocess of
the toolkit's worker; it stays alive between runs so models load once. Results are cached by image content, model
and detail, so changing hatching settings never re-runs the model.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from ..core import procs
from ..core.paths import app_home, cache_dir, frozen, ml_runtime_dir, resource, venv_python

ENV_PYTHON = "ATLAS_TOOLKIT_ML_PYTHON"
MODELS = [   # key, label (kept in step with ml/depth_server.py)
    ("dpt-beit-large-512", "MiDaS 3.1 BEiT-L-512"),
    ("dpt-large", "DPT-Large (notebooks)"),
    ("depth-anything-v2-large", "Depth Anything V2 L (non-commercial)"),
]


class DepthError(RuntimeError):
    pass


def runtime_candidates() -> list[Path]:
    """Where the GPU runtime's Python may be, in order: $ATLAS_TOOLKIT_ML_PYTHON, the app setting 'ml_python', a
    .venv-ml next to the exe, the repository's .venv-ml (an exe published to <repo>/dist, or running from source),
    then the per-user runtime made by `setup_ml.py --user`."""
    cands = []
    if os.environ.get(ENV_PYTHON):
        cands.append(Path(os.environ[ENV_PYTHON]))
    try:
        from ..core.settings import AppSettings
        if AppSettings().get("ml_python"):
            cands.append(Path(AppSettings().get("ml_python")))
    except Exception:
        pass
    home = app_home()
    cands.append(venv_python(home / ".venv-ml"))
    if frozen():
        cands.append(venv_python(home.parent / ".venv-ml"))
    else:
        cands.append(venv_python(Path(__file__).resolve().parents[3] / ".venv-ml"))
    cands.append(venv_python(ml_runtime_dir()))
    return cands


def runtime_python() -> Path | None:
    """The first GPU runtime Python that exists (see runtime_candidates)."""
    return next((c for c in runtime_candidates() if c.exists()), None)


def server_script() -> Path:
    return resource("ml", "depth_server.py")


class DepthClient:
    """One depth server process; requests are answered in order."""

    def __init__(self, python: Path):
        env = procs.child_env(PYTHONIOENCODING="utf-8", HF_HUB_DISABLE_PROGRESS_BARS="1",
                              TRANSFORMERS_VERBOSITY="error")
        self.proc = procs.spawn([str(python), "-u", str(server_script())], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                bufsize=1, env=env)
        self.lines: queue.Queue = queue.Queue()
        self.errors: list = []
        threading.Thread(target=self._pump, args=(self.proc.stdout, self.lines), daemon=True).start()
        threading.Thread(target=self._drain, daemon=True).start()
        self.next_id = 0
        hello = self._read(timeout=240)
        if not hello.get("ready"):
            raise DepthError(f"the GPU runtime did not start: {hello.get('error') or self._stderr()}")
        self.device = hello.get("device", "?")

    @staticmethod
    def _pump(stream, q):
        for line in stream:
            q.put(line)
        q.put(None)

    def _drain(self):
        for line in self.proc.stderr:
            self.errors.append(line.rstrip())
            del self.errors[:-40]

    def _stderr(self) -> str:
        return "\n".join(self.errors[-8:])

    def _read(self, timeout: float) -> dict:
        t0 = time.monotonic()
        while True:
            try:
                line = self.lines.get(timeout=max(0.1, timeout - (time.monotonic() - t0)))
            except queue.Empty:
                raise DepthError("the depth model did not answer in time") from None
            if line is None:
                raise DepthError(f"the GPU runtime stopped:\n{self._stderr()}")
            try:
                return json.loads(line)
            except ValueError:
                continue

    def alive(self) -> bool:
        return self.proc.poll() is None

    def request(self, req: dict, on_progress=None, timeout: float = 1800) -> dict:
        self.next_id += 1
        req = dict(req, id=self.next_id)
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        while True:
            msg = self._read(timeout)
            if msg.get("id") != self.next_id:
                continue
            if "progress" in msg:
                if on_progress:
                    on_progress(msg.get("msg", ""))
                continue
            if not msg.get("ok"):
                raise DepthError(msg.get("error", "depth failed"))
            return msg

    def close(self) -> None:
        try:
            self.proc.stdin.write(json.dumps({"quit": True}) + "\n")
            self.proc.stdin.flush()
            self.proc.wait(5)
        except Exception:
            self.proc.kill()
        finally:
            procs.finish(self.proc)


_CLIENT: DepthClient | None = None


def client() -> DepthClient:
    global _CLIENT
    if _CLIENT is None or not _CLIENT.alive():
        py = runtime_python()
        if py is None:
            raise DepthError("The GPU runtime for depth models is not installed. Set it up as Setup › Components "
                             "explains, or set Depth from to a depth-map file or Rough.")
        _CLIENT = DepthClient(py)
    return _CLIENT


def image_key(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:20]


def from_model(image: Path, model: str, detail: str, size: tuple[int, int], log=None) -> np.ndarray:
    """Depth (h, w) float32 0..1 (1 = nearest) for the image resized to `size`, cached."""
    out = cache_dir() / "depth" / f"{image_key(image)}-{model}-{detail}-{size[0]}x{size[1]}.npy"
    if out.exists():
        if log:
            log(f"depth from the cache ({model}, {detail})")
        return np.load(out)
    info = client().request({"image": str(image), "model": model, "detail": detail, "out": str(out),
                             "size": list(size)}, on_progress=log)
    if log:
        runs = ", ".join(f"{h}×{w} in {s:.2f} s" for h, w, s in info.get("runs", []))
        log(f"depth: {info.get('model')} on {info.get('device')} ({info.get('dtype')}), {runs}"
            + (f", peak {info['peak_gb']} GB" if "peak_gb" in info else ""))
        for n in info.get("notes", []):
            log(n)
    return np.load(out)


def from_file(path: Path, size: tuple[int, int]) -> np.ndarray:
    """A depth map saved as .npy or as a grey PNG/TIFF (8 or 16 bit), white = near, fitted to `size`."""
    from PIL import Image
    path = Path(path)
    if path.suffix.lower() == ".npy":
        d = np.load(path).astype(np.float32)
    else:
        im = Image.open(path)
        d = np.asarray(im.convert("I") if im.mode in ("I;16", "I;16B", "I") else im.convert("L"), dtype=np.float32)
    if d.ndim == 3:
        d = d.mean(axis=2)
    if d.shape != tuple(size):
        d = np.asarray(Image.fromarray(d, mode="F").resize((size[1], size[0]), Image.BICUBIC), dtype=np.float32)
    lo, hi = np.percentile(d, [0.5, 99.5])
    return np.clip((d - lo) / max(hi - lo, 1e-9), 0, 1).astype(np.float32)


def rough(lum: np.ndarray) -> np.ndarray:
    """No model: a guess from a heavily blurred tone (lighter = nearer) and the ground plane (lower = nearer)."""
    from scipy.ndimage import gaussian_filter
    h, w = lum.shape
    soft = gaussian_filter(lum.astype(np.float32), 0.04 * max(h, w))
    ground = np.linspace(0, 1, h, dtype=np.float32)[:, None] * np.ones((1, w), np.float32)
    d = 0.6 * soft + 0.4 * ground
    lo, hi = d.min(), d.max()
    return ((d - lo) / max(hi - lo, 1e-9)).astype(np.float32)


def save_png16(path: Path, d: np.ndarray) -> Path:
    """16-bit grey PNG, white = near: reusable as a depth-map file."""
    from PIL import Image
    Image.fromarray((np.clip(d, 0, 1) * 65535).astype(np.uint16)).save(path)
    return Path(path)
