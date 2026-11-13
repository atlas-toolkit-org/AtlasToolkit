"""
Depth maps for tools that read photos: from a DPT-family model on the GPU (through the separate runtime), from a
depth-map file, or a rough guess from tone when neither is available.

The GPU runtime (.venv-ml in the repository, or the per-user ml-runtime folder; made by geom/depth_runtime.py) runs
ml/depth_server.py as a subprocess of
the toolkit's worker; it stays alive between runs so models load once. Results are cached by image content, model
and detail, so changing hatching settings never re-runs the model.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
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


STALL_S = 240      # a download that has not grown for this long has stopped: give up and say so (plain downloads
                   # grow steadily; Hugging Face's own timeouts usually end a broken one sooner, with a clearer error)
QUIET_S = 15       # no new data for this long: the label says it is waiting for the network


class DepthError(RuntimeError):
    pass


class NotDownloaded(DepthError):
    """The model is not in the cache yet, and this run may not download it (a quick preview)."""

    def __init__(self, label: str, total_mb: float, folder: str = ""):
        super().__init__(f"{label} is not downloaded yet (about {gb(total_mb)})")
        self.label, self.total_mb, self.folder = label, total_mb, folder


def gb(mb: float) -> str:
    return f"{mb / 1000:.2f} GB" if mb >= 100 else f"{mb:.0f} MB"


def duration(s: float) -> str:
    s = max(0, int(round(s)))
    return f"{s} s" if s < 60 else f"{s // 60} min {s % 60:02d} s" if s < 3600 else f"{s // 3600} h {s % 3600 // 60:02d} min"


@dataclass
class Download:
    """What the user sees of a model's first-use download: the label for the progress bar, the log lines."""
    model: str = ""
    total_mb: float = 0.0
    done_mb: float = 0.0
    folder: str = ""
    state: str = ""
    grew: float = field(default_factory=time.monotonic)      # when it last grew (for the stall check)

    def update(self, d: dict) -> None:
        if d.get("done_mb", 0) > self.done_mb + 0.05 or d.get("state") != "progress":
            self.grew = time.monotonic()
        self.model = d.get("model", self.model)
        self.total_mb = float(d.get("total_mb", self.total_mb) or 0)
        self.done_mb = float(d.get("done_mb", self.done_mb) or 0)
        self.folder = d.get("folder", self.folder)
        self.state = d.get("state", self.state)

    @property
    def fraction(self) -> float:
        return min(1.0, self.done_mb / self.total_mb) if self.total_mb else 0.0

    def label(self, d: dict) -> str:
        head = f"Downloading {self.model} (first use only): "
        if self.state == "done":
            return f"Downloaded {self.model}"
        text = f"{gb(self.done_mb)} of {gb(self.total_mb)}"
        rate, eta = d.get("rate_mbs") or 0, d.get("eta_s")
        if self.state == "progress" and time.monotonic() - self.grew > QUIET_S:
            return head + text + f" · waiting for the network ({duration(time.monotonic() - self.grew)})"
        if rate > 0:
            text += f" · {rate:.1f} MB/s"
        if eta is not None:
            text += f" · about {duration(eta)} left"
        return head + (text if self.state == "progress" else f"starting, {text}")


DOWNLOAD_ERRORS = [   # (pattern in the error, what to tell the user); the installer's list (depth_runtime) after these
    (r"couldn't connect|ConnectionError|Connect(?:ion)?Timeout|ReadTimeout|Max retries|NameResolution|"
     r"name resolution|getaddrinfo|Network is unreachable|timed out|Connection (?:reset|refused|aborted)|"
     r"IncompleteRead|ChunkedEncodingError|stopped growing",
     "Check the internet connection, a proxy (HTTPS_PROXY) or a firewall, then run it again to download it again."),
    (r"No space left|not enough space|os error 112|errno 28|ENOSPC|disk space",
     "The disk holding the model cache is full. Free some space, then run it again."),
    (r"(?:401|403) Client Error|gated repo|Unauthorized|Forbidden",
     "Hugging Face refused the download. Try again later; if it keeps happening, the model's page may have changed."),
]


def download_failure(dl: Download, error: str) -> str:
    """A readable error for a download that did not finish: what, how far it got, and what to do."""
    hint = next((h for pat, h in DOWNLOAD_ERRORS if re.search(pat, error, re.I)), "")
    if not hint:
        try:
            from .depth_runtime import classify
            kind, h = classify(error)
            hint = h.split(" Try again")[0].split(": what was")[0] + " Then run it again." if kind != "other" else ""
        except Exception:
            pass
    hint = hint or "Run it again to download it again; the log has the details."
    return (f"The {dl.model or 'depth'} model did not finish downloading ({gb(dl.done_mb)} of {gb(dl.total_mb)}, "
            f"into {dl.folder or 'the Hugging Face cache'}). {hint}\n\n"
            f"Details: {error.strip().splitlines()[-1] if error.strip() else 'none'}")


def clear_partials(folder: str, wait: float = 5.0) -> None:
    """After stopping a download: its partial file is never reused (Hugging Face writes each download to a new,
    uniquely named file), so delete it rather than leave a gigabyte behind. On Windows a venv's python.exe is a
    launcher whose child, the real interpreter, holds the file a moment longer: retry while it is locked. The server
    clears anything still left (STALE_S) before the next download."""
    left = list(Path(folder).glob("blobs/*.incomplete")) if folder else []
    end = time.monotonic() + wait
    while left:
        for p in list(left):
            try:
                p.unlink(missing_ok=True)
                left.remove(p)
            except PermissionError:
                pass
            except OSError:
                left.remove(p)
        if left and time.monotonic() > end:
            break
        if left:
            time.sleep(0.2)


def download_env() -> dict:
    """How models download: plain HTTPS, not Hugging Face's Xet transfer. Xet gathers chunks out of sight and writes
    the file in bursts (a minute or more with nothing to show), while plain downloads grow the file as the bytes
    arrive, so the progress bar is honest, and an interrupted one resumes from its partial file. A user's own
    HF_HUB_DISABLE_XET wins."""
    return {} if "HF_HUB_DISABLE_XET" in os.environ else {"HF_HUB_DISABLE_XET": "1"}


def runtime_candidates() -> list[Path]:
    """Where the GPU runtime's Python may be, in order: $ATLAS_TOOLKIT_ML_PYTHON, the app setting 'ml_python', a
    .venv-ml next to the exe, the repository's .venv-ml (an exe published to <repo>/dist, or running from source),
    then the per-user runtime the installer makes (geom/depth_runtime.py)."""
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
                              TRANSFORMERS_VERBOSITY="error", **download_env())
        self.proc = procs.spawn([str(python), "-u", str(server_script())], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                bufsize=1, env=env)
        self.lines: queue.Queue = queue.Queue()
        self.errors: list = []
        threading.Thread(target=self._pump, args=(self.proc.stdout, self.lines), daemon=True).start()
        threading.Thread(target=self._drain, daemon=True).start()
        self.next_id = 0
        self.download: Download | None = None
        hello = self._read(timeout=240)
        if not hello.get("ready"):
            raise DepthError(f"the GPU runtime did not start: {hello.get('error') or self._stderr()}")
        self.device = hello.get("device", "?")

    @staticmethod
    def _pump(stream, q):
        try:
            for line in stream:
                q.put(line)
        except (OSError, ValueError):
            pass
        q.put(None)
        stream.close()

    def _drain(self):
        try:
            for line in self.proc.stderr:
                self.errors.append(line.rstrip())
                del self.errors[:-40]
        except (OSError, ValueError):
            pass
        self.proc.stderr.close()

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

    def request(self, req: dict, on_progress=None, timeout: float = 1800, on_download=None) -> dict:
        """Send one request and wait for its reply. `on_progress(msg)` hears the server's lines; `on_download(dl, d)`
        hears a first-use download (a Download, and the server's message). If either raises (a cancel), or the
        download stops growing, the server is stopped: a model download must not go on behind the user's back."""
        self.next_id += 1
        req = dict(req, id=self.next_id)
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        self.download = None
        try:
            while True:
                msg = self._read(timeout)
                if msg.get("id") != self.next_id:
                    continue
                if "download" in msg:
                    if self.download is None:
                        self.download = Download()
                    self.download.update(msg["download"])
                    if time.monotonic() - self.download.grew > STALL_S:
                        raise DepthError(download_failure(
                            self.download, f"no data arrived for {duration(STALL_S)}: the download stopped growing"))
                    if on_download:
                        on_download(self.download, msg["download"])
                    continue
                if "progress" in msg:
                    if on_progress:
                        on_progress(msg.get("msg", ""))
                    continue
                if not msg.get("ok"):
                    error = msg.get("error", "depth failed")
                    failed, self.download = self.download, None      # answered: the server is idle again
                    if failed is not None and failed.state != "done":
                        raise DepthError(download_failure(failed, error))
                    raise DepthError(error)
                return msg
        except BaseException:
            if self.download is not None and self.download.state != "done":
                self.kill()                    # cancelled or stalled mid-download: stop it, and drop its partial file
                clear_partials(self.download.folder)
            raise

    def cached(self, model: str) -> dict:
        """{"cached": bool, "model": label, "total_mb": about, "folder": where} without downloading anything."""
        return self.request({"op": "cached", "model": model}, timeout=60)

    def kill(self) -> None:
        procs.finish(self.proc)
        self._close_stdin()

    def _close_stdin(self) -> None:
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass

    def close(self) -> None:
        try:
            self.proc.stdin.write(json.dumps({"quit": True}) + "\n")
            self.proc.stdin.flush()
            self.proc.wait(5)
        except Exception:
            self.proc.kill()
        finally:
            procs.finish(self.proc)
            self._close_stdin()


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


def from_model(image: Path, model: str, detail: str, size: tuple[int, int], log=None, progress=None,
               allow_download: bool = True) -> np.ndarray:
    """Depth (h, w) float32 0..1 (1 = nearest) for the image resized to `size`, cached.

    A model's first use downloads it (about 1.3 GB): `progress(fraction, label)` then follows the download (and may
    raise to cancel it), `log` gets where it goes, how long it took and that it will not download again. With
    `allow_download` False a model that is not downloaded yet raises NotDownloaded instead (quick previews)."""
    out = cache_dir() / "depth" / f"{image_key(image)}-{model}-{detail}-{size[0]}x{size[1]}.npy"
    if out.exists():
        if log:
            log(f"depth from the cache ({model}, {detail})")
        return np.load(out)
    c = client()
    if not allow_download:
        state = c.cached(model)
        if not state.get("cached"):
            raise NotDownloaded(state.get("model", model), state.get("total_mb", 1400), state.get("folder", ""))

    def on_download(dl: Download, d: dict) -> None:
        if d.get("state") == "start" and log:
            log(f"{dl.model} is not on this computer yet: downloading it once (about {gb(dl.total_mb)}) into "
                f"{dl.folder}. Later runs use the saved copy.")
        if d.get("state") == "done" and log:
            log(f"Downloaded {dl.model} ({gb(dl.total_mb)} in {duration(d.get('seconds', 0))}). It is saved in "
                f"{dl.folder} and will not download again.")
        if progress:
            progress(dl.fraction, dl.label(d))

    info = c.request({"image": str(image), "model": model, "detail": detail, "out": str(out),
                      "size": list(size)}, on_progress=log, on_download=on_download)
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
