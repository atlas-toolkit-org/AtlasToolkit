"""Paper sizes used in the studio (mm, portrait) and fitting a drawing onto a sheet."""
from __future__ import annotations

PAPERS = {
    "A5": (148.0, 210.0),
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A2": (420.0, 594.0),
    "A1": (594.0, 841.0),
    "30×40": (300.0, 400.0),
    "50×70": (500.0, 700.0),
}


def sheet(paper: str, orientation: str = "portrait", custom: tuple | None = None) -> tuple[float, float]:
    if paper == "Custom":
        import math
        if custom is None or len(custom) != 2 or not all(math.isfinite(float(v)) and float(v) > 0 for v in custom):
            raise ValueError("Custom paper needs positive finite width and height in mm.")
        w, h = map(float, custom)
    else:
        w, h = PAPERS[paper]
    return (h, w) if orientation == "landscape" else (w, h)


def fit(bounds: tuple, paper: str, orientation: str = "portrait", margin: float = 15.0,
        custom: tuple | None = None) -> tuple[float, float, float]:
    """Scale and offset that fit `bounds` (x0, y0, x1, y1) centred inside the sheet minus the margin:
    x_mm = s * x + tx, y_mm = s * y + ty."""
    W, H = sheet(paper, orientation, custom)
    x0, y0, x1, y1 = bounds
    bw, bh = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    aw, ah = max(W - 2 * margin, 1.0), max(H - 2 * margin, 1.0)
    s = min(aw / bw, ah / bh)
    tx = (W - s * bw) / 2 - s * x0
    ty = (H - s * bh) / 2 - s * y0
    return s, tx, ty


def name_for(width_mm: float, height_mm: float, tol: float = 1.5) -> str:
    """'A3 portrait' for a known sheet, else '297 × 420 mm'."""
    for name, (w, h) in PAPERS.items():
        if abs(width_mm - w) <= tol and abs(height_mm - h) <= tol:
            return f"{name} portrait"
        if abs(width_mm - h) <= tol and abs(height_mm - w) <= tol:
            return f"{name} landscape"
    return f"{width_mm:.0f} × {height_mm:.0f} mm"
