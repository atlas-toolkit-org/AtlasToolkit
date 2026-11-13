#!/usr/bin/env python3
"""
depth_server.py - Monocular depth (DPT family) on the GPU, for the toolkit's Photo Hatching.

Runs with the GPU runtime (.venv-ml or the per-user ml-runtime: torch + transformers), never inside the app: the app starts it
as a subprocess and talks JSON lines over stdin/stdout. Models stay loaded between requests.

    request   {"id": 1, "image": "photo.jpg", "model": "dpt-beit-large-512", "detail": "boost", "out": "d.npy",
               "size": [h, w]}
    progress  {"id": 1, "progress": 0.4, "msg": "..."}
    download  {"id": 1, "progress": 0.0, "download": {"state": "start" | "progress" | "done", "model": label,
               "done_mb": ..., "total_mb": ..., "rate_mbs": ..., "eta_s": ..., "seconds": ..., "folder": "..."}}
               while a model's weights download (first use only), at least every second
    reply     {"id": 1, "ok": true, "out": "d.npy", "device": "...", "runs": [[h, w, seconds], ...], "notes": [...]}
              {"id": 1, "ok": false, "error": "..."}
    cached    {"id": 2, "op": "cached", "model": "dpt-large"} -> {"id": 2, "ok": true, "cached": false, "total_mb": ...}

Depth is relative inverse depth (disparity) scaled to 0..1 by robust percentiles: 1 = nearest. The image keeps its
aspect ratio (the Colab notebooks squashed it to 384 x 384). Detail:
    native   the model's own resolution
    high     long side about 1024 px
    boost    layout from the native pass, fine detail (high frequencies) from the high pass

    python depth_server.py --check [image] [model ...]   run the models and report sizes, times and memory
    python depth_server.py --probe                       one JSON line: versions, device, a GPU sanity check
    python depth_server.py --fetch MODEL                 download a model now (first use would) and load it once
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import traceback
from pathlib import Path

MODELS = {   # key: (Hugging Face id, native long side, size multiple, label)
    "dpt-beit-large-512": ("Intel/dpt-beit-large-512", 512, 32, "MiDaS 3.1 BEiT-L-512"),
    "dpt-large": ("Intel/dpt-large", 384, 32, "DPT-Large (MiDaS 3.0)"),
    "depth-anything-v2-large": ("depth-anything/Depth-Anything-V2-Large-hf", 518, 14, "Depth Anything V2 Large"),
}
SIZE_MB = {   # what each model downloads, about (the weights; the configs are a few kB): when the hub cannot say
    "dpt-beit-large-512": 1380,
    "dpt-large": 1370,
    "depth-anything-v2-large": 1340,
}
WEIGHTS = ("model.safetensors", "pytorch_model.bin")
HIGH = 1024

_OUT = sys.stdout
_SEND = threading.Lock()


def send(obj: dict) -> None:
    with _SEND:                      # the download reporter writes from its own thread
        _OUT.write(json.dumps(obj) + "\n")
        _OUT.flush()


# ----------------------------------------------------------------------------- first-use downloads
def hub_folder(hf: str) -> Path:
    """Where the Hugging Face cache keeps this model (HF_HUB_CACHE, HF_HOME or ~/.cache/huggingface)."""
    try:
        from huggingface_hub import constants
        root = Path(constants.HF_HUB_CACHE)
    except Exception:
        root = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"
    return root / ("models--" + hf.replace("/", "--"))


def is_cached(key: str) -> bool:
    """The model's config and weights are already in the cache: loading it downloads nothing."""
    hf = MODELS[key][0]
    try:
        from huggingface_hub import try_to_load_from_cache
        found = lambda name: isinstance(try_to_load_from_cache(hf, name), str)   # noqa: E731
        return found("config.json") and any(found(w) for w in WEIGHTS)
    except Exception:
        return False


def download_mb(key: str) -> float:
    """The weights' real size from the hub (one small request), or the table's estimate."""
    try:
        from huggingface_hub import get_hf_file_metadata, hf_hub_url
        for name in WEIGHTS:
            try:
                size = get_hf_file_metadata(hf_hub_url(MODELS[key][0], name), timeout=10).size
            except Exception:
                continue
            if size:
                return size / 1e6
    except Exception:
        pass
    return float(SIZE_MB.get(key, 1400))


def folder_files(folder: Path) -> dict:
    """{path: bytes} of the real files in a cache folder (partial *.incomplete ones included, links left out)."""
    out = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            try:
                if not os.path.islink(p):
                    out[p] = os.path.getsize(p)
            except OSError:
                pass
    return out


def folder_mb(folder: Path) -> float:
    return sum(folder_files(folder).values()) / 1e6


STALE_S = 600     # a partial file nobody has written to for this long is left over from a download that was killed


def clear_partials(folder: Path, older_than: float = STALE_S) -> int:
    """Delete the *.incomplete files a killed download left behind: Hugging Face never reuses them (each download
    writes its own uniquely named file), so they only take space. A file still being written (another Atlas window)
    is recent, or locked on Windows, and stays. Returns the bytes freed."""
    freed, now = 0, time.time()
    for p in Path(folder).glob("blobs/*.incomplete"):
        try:
            st = p.stat()
            if now - st.st_mtime >= older_than:
                p.unlink()
                freed += st.st_size
        except OSError:
            pass
    return freed


class DownloadReport:
    """While a model downloads: measure its cache folder against the expected size and report, about twice a second,
    how much has arrived, the rate and the time left. Only files that are new since it began count: Hugging Face
    never resumes, so anything already there is not part of this download."""

    def __init__(self, report, label: str, folder: Path, total_mb: float, every: float = 0.5):
        self.report, self.label, self.folder, self.every = report, label, Path(folder), every
        self.total = max(float(total_mb), 1.0)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.t0 = time.monotonic()
        self.samples: list = []                # (time, MB) over the last few seconds, for the rate
        self.before: set = set()               # files that were there before: not part of this download

    def _msg(self, state: str, **more) -> dict:
        return dict(state=state, model=self.label, total_mb=round(self.total, 1), folder=str(self.folder), **more)

    def arrived(self) -> float:
        return sum(b for p, b in folder_files(self.folder).items() if p not in self.before) / 1e6

    def measure(self) -> dict:
        now, done = time.monotonic(), self.arrived()
        self.samples = [s for s in self.samples if now - s[0] <= 20.0] + [(now, done)]   # files grow in bursts
        (t_a, d_a), (t_b, d_b) = self.samples[0], self.samples[-1]
        rate = max(0.0, (d_b - d_a) / (t_b - t_a)) if t_b - t_a >= 2.0 else 0.0
        done = min(done, self.total * 0.99)    # the estimate can be a little short: never 100 % before it is done
        eta = (self.total - done) / rate if rate > 0.05 else None
        return self._msg("progress", done_mb=round(done, 1), rate_mbs=round(rate, 2),
                         eta_s=None if eta is None else round(eta))

    def _run(self) -> None:
        while not self.stop.wait(self.every):
            try:
                self.report(self.measure())
            except Exception:
                pass

    def __enter__(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        clear_partials(self.folder)
        self.before = set(folder_files(self.folder))
        self.report(self._msg("start", done_mb=0.0))
        self.thread.start()
        return self

    def __exit__(self, exc_type, *_):
        self.stop.set()
        self.thread.join(2)
        if exc_type is None:
            self.report(self._msg("done", done_mb=round(self.total, 1),
                                  seconds=round(time.monotonic() - self.t0, 1)))


def choose_device(torch, wanted: str = "") -> str:
    """CUDA where there is one, otherwise the CPU. Apple's GPU only on request (ATLAS_DEPTH_DEVICE=mps), because these
    models and their precision have not been tested on it yet."""
    if wanted in ("cuda", "mps", "cpu"):
        return wanted
    return "cuda" if torch.cuda.is_available() else "cpu"


class Depth:
    def __init__(self):
        import torch
        self.torch = torch
        self.device = choose_device(torch, os.environ.get("ATLAS_DEPTH_DEVICE", ""))
        self.models: dict = {}
        self.fp32_only: set = set()       # models that gave non-finite values in fp16
        self.no_resize: set = set()       # models that refused a non-native size

    def device_name(self) -> str:
        t = self.torch
        return t.cuda.get_device_name(0) if self.device == "cuda" else "Apple GPU" if self.device == "mps" else "CPU"

    def load(self, key: str, say=None, report=None):
        """The model, loaded once. Its first use downloads it: `report` (a function taking one dict) then hears how
        the download goes (see DownloadReport); `say` gets one line either way."""
        if key in self.models:
            return self.models[key]
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation
        hf, label = MODELS[key][0], MODELS[key][3]
        cached = is_cached(key)
        if say and (cached or report is None):          # a reported download tells its own story
            say(f"loading {label}" if cached else
                f"downloading {label} (about {SIZE_MB.get(key, 1400) / 1000:.1f} GB, first use only) "
                f"into {hub_folder(hf)}")

        def fetch():
            proc = AutoImageProcessor.from_pretrained(hf)
            try:
                model = AutoModelForDepthEstimation.from_pretrained(hf, attn_implementation="sdpa")
            except (ValueError, TypeError):
                model = AutoModelForDepthEstimation.from_pretrained(hf)
            return proc, model

        if cached or report is None:
            proc, model = fetch()
        else:
            with DownloadReport(report, label, hub_folder(hf), download_mb(key)):
                proc, model = fetch()
        model.eval().to(self.device)
        _non_square(model)
        dtype = self.torch.float16 if self.device == "cuda" else self.torch.float32
        model.to(dtype)
        for other in [k for k in self.models if k != key]:   # one model on the GPU at a time
            del self.models[other]
        if self.device == "cuda":
            self.torch.cuda.empty_cache()
        self.models[key] = (proc, model)
        return self.models[key]

    @staticmethod
    def size_for(h: int, w: int, long_side: int, multiple: int) -> tuple[int, int]:
        s = long_side / max(h, w)
        return (max(multiple, int(round(h * s / multiple)) * multiple),
                max(multiple, int(round(w * s / multiple)) * multiple))

    def infer(self, key: str, rgb, size: tuple[int, int]):
        """rgb: float32 HxWx3 0..1 numpy; returns predicted depth (h, w) float32 numpy at the model size."""
        import numpy as np
        t = self.torch
        proc, model = self.load(key)
        mean = np.array(getattr(proc, "image_mean", [0.5, 0.5, 0.5]), dtype=np.float32)
        std = np.array(getattr(proc, "image_std", [0.5, 0.5, 0.5]), dtype=np.float32)
        x = t.from_numpy(((rgb - mean) / std).transpose(2, 0, 1)[None].copy()).to(self.device)
        x = t.nn.functional.interpolate(x, size=size, mode="bicubic", align_corners=False, antialias=True)
        dtype = t.float32 if (key in self.fp32_only or self.device != "cuda") else t.float16
        if dtype == t.float32:
            model.to(t.float32)
        with t.inference_mode():
            pred = model(pixel_values=x.to(dtype)).predicted_depth
        out = pred.float()[0]
        if not bool(t.isfinite(out).all()) and dtype == t.float16:   # fp16 overflow: redo in fp32 from now on
            self.fp32_only.add(key)
            model.to(t.float32)
            with t.inference_mode():
                out = model(pixel_values=x.float()).predicted_depth.float()[0]
        return out.cpu().numpy()

    def depth(self, req: dict, say, report=None) -> dict:
        import numpy as np
        from PIL import Image, ImageOps
        t = self.torch
        key = req["model"]
        if key not in MODELS:
            raise ValueError(f"unknown model {key!r}")
        hf, native, mult, label = MODELS[key]
        img = ImageOps.exif_transpose(Image.open(req["image"])).convert("RGB")
        H, W = req.get("size") or (img.height, img.width)
        rgb = np.asarray(img, dtype=np.float32) / 255.0
        detail = req.get("detail", "native")
        notes, runs = [], []
        self.load(key, say, report)
        if self.device == "cuda":
            t.cuda.reset_peak_memory_stats()

        def run(long_side: int):
            size = self.size_for(rgb.shape[0], rgb.shape[1], long_side, mult)
            t0 = time.perf_counter()
            d = self.infer(key, rgb, size)
            if self.device == "cuda":
                t.cuda.synchronize()
            runs.append([size[0], size[1], round(time.perf_counter() - t0, 3)])
            return d

        say(f"{label}: {detail} detail on {self.device_name()}")
        lo = run(native)
        hi = None
        if detail in ("high", "boost") and max(rgb.shape[:2]) < native:
            notes.append(f"the photo is smaller than {label}'s native {native} px; used native detail")
        elif detail in ("high", "boost") and key not in self.no_resize:
            try:
                hi = run(HIGH)
            except Exception as e:   # a backbone that cannot change resolution
                self.no_resize.add(key)
                notes.append(f"{label} only runs at its native size here ({type(e).__name__}); used native detail")
        elif detail in ("high", "boost"):
            notes.append(f"{label} only runs at its native size here; used native detail")
        lo_full = resize(lo, (H, W))
        if hi is None:
            d = lo_full
        elif detail == "high":
            d = resize(hi, (H, W))
        else:
            hi_full = resize(hi, (H, W))
            sigma = 0.012 * max(H, W)
            # match the high pass to the native one (scale and shift), then take its detail only
            a, b = np.polyfit(hi_full.ravel()[::97], lo_full.ravel()[::97], 1)
            hi_full = a * hi_full + b
            d = blur(lo_full, sigma) + (hi_full - blur(hi_full, sigma))
        lo_p, hi_p = np.percentile(d, [0.5, 99.5])
        d = np.clip((d - lo_p) / max(hi_p - lo_p, 1e-9), 0.0, 1.0).astype(np.float32)
        Path(req["out"]).parent.mkdir(parents=True, exist_ok=True)
        np.save(req["out"], d)
        info = {"ok": True, "out": req["out"], "device": self.device_name(), "model": label, "runs": runs,
                "notes": notes, "dtype": "fp32" if key in self.fp32_only or self.device != "cuda" else "fp16"}
        if self.device == "cuda":
            info["peak_gb"] = round(t.cuda.max_memory_allocated() / 1e9, 2)
        return info


def _non_square(model) -> None:
    """transformers' plain-ViT DPT (Intel/dpt-large) folds its tokens back into a square grid (sqrt of the token count),
    so any other aspect ratio fails, which is why the notebooks squashed photos to 384 x 384. Its position embeddings
    already adapt to any grid: hand the reassemble stage the real grid, read from the input, and it keeps the aspect."""
    neck = getattr(model, "neck", None)
    stage = getattr(neck, "reassemble_stage", None)
    cfg = model.config
    if stage is None or getattr(cfg, "backbone_config", None) is not None or getattr(cfg, "is_hybrid", False):
        return
    grid = {}
    patch = cfg.patch_size
    model.register_forward_pre_hook(
        lambda _m, args, kwargs: grid.update(hw=tuple((kwargs.get("pixel_values", args[0] if args else None)).shape[-2:])),
        with_kwargs=True)
    forward = stage.forward

    def reassemble(hidden_states, patch_height=None, patch_width=None):
        if patch_height is None and "hw" in grid:
            patch_height, patch_width = grid["hw"][0] // patch, grid["hw"][1] // patch
        return forward(hidden_states, patch_height, patch_width)

    stage.forward = reassemble


def resize(a, size):
    import numpy as np
    from PIL import Image
    return np.asarray(Image.fromarray(a.astype(np.float32), mode="F").resize((size[1], size[0]), Image.BICUBIC),
                      dtype=np.float32)


def blur(a, sigma: float):
    """Separable Gaussian with numpy (the runtime has no scipy)."""
    import numpy as np
    r = max(1, int(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.pad(a, r, mode="reflect")
    tmp = np.apply_along_axis(lambda v: np.convolve(v, k, mode="valid"), 1, pad)
    return np.apply_along_axis(lambda v: np.convolve(v, k, mode="valid"), 0, tmp)


def serve() -> int:
    global _OUT
    _OUT = sys.stdout
    sys.stdout = sys.stderr          # library prints must not corrupt the protocol
    try:
        engine = Depth()
    except Exception as e:
        send({"ok": False, "fatal": True, "error": f"{type(e).__name__}: {e}"})
        return 1
    send({"ready": True, "device": engine.device_name(), "torch": engine.torch.__version__})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if req.get("quit"):
            break
        rid = req.get("id")
        try:
            if req.get("op") == "cached":
                key = req.get("model", "")
                if key not in MODELS:
                    raise ValueError(f"unknown model {key!r}")
                send({"id": rid, "ok": True, "cached": is_cached(key), "model": MODELS[key][3],
                      "total_mb": SIZE_MB.get(key, 1400), "folder": str(hub_folder(MODELS[key][0]))})
                continue
            info = engine.depth(req, lambda msg, rid=rid: send({"id": rid, "progress": 0.0, "msg": msg}),
                                lambda d, rid=rid: send({"id": rid, "progress": 0.0, "download": d}))
            info["id"] = rid
            send(info)
        except Exception as e:
            send({"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
    return 0


def check(image: str | None, models=None) -> int:
    import numpy as np
    import tempfile
    from PIL import Image
    engine = Depth()
    t = engine.torch
    print(f"torch {t.__version__}, CUDA {t.version.cuda}, device {engine.device_name()}")
    if image is None:   # a synthetic scene: a sphere in front of a tilted plane
        yy, xx = np.mgrid[0:768, 0:1024] / 768.0
        g = np.clip(0.3 + 0.4 * xx - 0.1 * yy, 0, 1)
        m = (xx - 0.7) ** 2 + (yy - 0.5) ** 2 < 0.09
        g[m] = 0.9 - 0.8 * np.hypot(xx - 0.62, yy - 0.42)[m]
        image = str(Path(tempfile.gettempdir()) / "atlas_depth_check.png")
        Image.fromarray((np.dstack([g, g * 0.9, g * 0.8]) * 255).astype(np.uint8)).save(image)
    ok = True
    for key in models or MODELS:
        for detail in ("native", "high", "boost"):
            out = str(Path(tempfile.gettempdir()) / f"atlas_depth_check_{key}_{detail}.npy")
            try:
                t0 = time.perf_counter()
                info = engine.depth({"image": image, "model": key, "detail": detail, "out": out},
                                    lambda m: print("  ", m))
                d = np.load(out)
                print(f"{key:26} {detail:7} runs {info['runs']}  total {time.perf_counter() - t0:6.2f} s  "
                      f"peak {info.get('peak_gb', 0):.2f} GB  {info['dtype']}  depth {d.shape} "
                      f"{d.min():.2f}..{d.max():.2f}  {' / '.join(info['notes'])}")
            except Exception as e:
                ok = False
                print(f"{key:26} {detail:7} FAILED {type(e).__name__}: {e}")
    return 0 if ok else 1


def probe() -> int:
    """One JSON line about this environment, downloading nothing: what the installer checks before trusting it. PyTorch's
    own word on the GPU counts, not the driver's: the device must be usable, its architecture compiled in, and a small
    product on it must come out right."""
    info: dict = {"python": sys.version.split()[0], "ok": False}
    try:
        import torch
        info.update(torch=torch.__version__, cuda=torch.version.cuda, cuda_ok=bool(torch.cuda.is_available()), arch=[])
        if info["cuda_ok"]:
            info["arch"] = list(torch.cuda.get_arch_list())
            info["device"] = torch.cuda.get_device_name(0)
            major, minor = torch.cuda.get_device_capability(0)
            info["sm"] = f"sm_{major}{minor}"
            a = torch.arange(16.0, device="cuda").reshape(4, 4)
            b = torch.arange(16.0).reshape(4, 4)
            info["math_ok"] = bool(torch.allclose((a @ a).cpu(), b @ b))
        else:
            info["device"] = "CPU"
        import numpy
        import PIL
        import safetensors  # noqa: F401
        import transformers
        info.update(numpy=numpy.__version__, pillow=PIL.__version__, transformers=transformers.__version__, ok=True)
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(info), flush=True)
    return 0 if info["ok"] else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        sys.exit(probe())
    if len(sys.argv) > 2 and sys.argv[1] == "--fetch":       # --fetch model: download it now and load it once
        Depth().load(sys.argv[2], print)
        print(f"ready: {MODELS[sys.argv[2]][3]}", flush=True)
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "--check":      # --check [photo] [model ...]
        rest = sys.argv[2:]
        sys.exit(check(next((a for a in rest if a not in MODELS), None), [a for a in rest if a in MODELS]))
    sys.exit(serve())
