"""Photos as inputs: loading (EXIF-rotated RGB), working-size resampling, and the default inspect for image tools."""
from __future__ import annotations

from pathlib import Path

import numpy as np

PREVIEW_PX = 1400


def load_rgb(path: Path, max_side: int | None = None) -> np.ndarray:
    """uint8 H×W×3, EXIF orientation applied, long side at most `max_side`."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    if max_side and max(im.size) > max_side:
        s = max_side / max(im.size)
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    return np.asarray(im, dtype=np.uint8)


def thumbnail(arr: np.ndarray, max_side: int = PREVIEW_PX) -> np.ndarray:
    from PIL import Image
    h, w = arr.shape[:2]
    if max(h, w) <= max_side:
        return arr
    s = max_side / max(h, w)
    mode = "L" if arr.ndim == 2 else None
    im = Image.fromarray(arr, mode=mode).resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    return np.asarray(im)


def view(title: str, arr: np.ndarray, rect: tuple | None = None, sheet: tuple | None = None) -> dict:
    """A raster preview view: `rect` (x, y, w, h) in mm on the `sheet` (W, H); without them, the picture alone."""
    a = thumbnail(arr)
    h, w = arr.shape[:2]
    if rect is None:
        s = 297.0 / max(h, w)
        rect = (0.0, 0.0, w * s, h * s)
    return {"title": title, "kind": "image", "image": np.ascontiguousarray(a), "rect": tuple(map(float, rect)),
            "sheet": tuple(map(float, sheet)) if sheet else None}


def inspect(path: Path):
    from PIL import Image
    from ..core.tool import InputInfo
    with Image.open(path) as im:
        w, h = im.size
        mode = im.mode
    rgb = load_rgb(path, PREVIEW_PX)
    summary = f"{w} × {h} px · {w * h / 1e6:.1f} MP · {mode}"
    depth_file = Path(path).with_name(f"{Path(path).stem}_depth.png")
    return InputInfo(str(path), "image", summary,
                     values={"width": w, "height": h, "max_dim": max(w, h), "aspect": w / h,
                             "depth_file": str(depth_file) if depth_file.exists() else ""},
                     preview={"views": [view("Photo", rgb)]})
