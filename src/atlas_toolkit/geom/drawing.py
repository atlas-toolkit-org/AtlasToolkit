"""
A plotter drawing in millimetres: pens (layers × stroke colour), each a list of polylines in plotting order. This
is what every 2D preview draws and what the plot statistics are computed from.

Polylines are packed per pen into one float32 (N, 2) array plus a `connect` byte array (1: joined to the next
point, 0: pen lift), the layout pyqtgraph's arrayToQPath turns into a QPainterPath quickly, and cheap to pickle
from a worker process.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

DEFAULT_COLORS = ["#0B1220", "#3D7BFF", "#E5484D", "#2E9E6B", "#C9821B", "#8C89F7", "#0E8FBA", "#B3417F"]


@dataclass
class Pen:
    key: str
    label: str
    color: str = "#0B1220"
    width: float = 0.3                     # mm, stroke width for the preview
    xy: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), np.float32))
    connect: np.ndarray = field(default_factory=lambda: np.zeros(0, np.uint8))

    @classmethod
    def from_polylines(cls, key: str, label: str, polys, color: str = "#0B1220", width: float = 0.3) -> "Pen":
        parts, conn = [], []
        for pl in polys:
            a = np.asarray(pl, dtype=np.float32).reshape(-1, 2)
            if len(a) < 2:
                continue
            c = np.ones(len(a), np.uint8)
            c[-1] = 0
            parts.append(a)
            conn.append(c)
        if not parts:
            return cls(key, label, color, width)
        return cls(key, label, color, width, np.concatenate(parts), np.concatenate(conn))

    def polylines(self) -> list[np.ndarray]:
        if len(self.xy) == 0:
            return []
        ends = np.flatnonzero(self.connect == 0) + 1
        return [p for p in np.split(self.xy, ends) if len(p) > 1]

    @property
    def strokes(self) -> int:
        return int(np.count_nonzero(self.connect == 0))

    @property
    def points(self) -> int:
        return int(len(self.xy))


@dataclass
class PlotStats:
    ink: float = 0.0            # mm drawn
    travel: float = 0.0         # mm moved with the pen up (in plotting order, from the first stroke)
    strokes: int = 0            # pen-down runs = pen lifts
    points: int = 0
    pens: int = 0

    def seconds(self, draw_mm_s: float = 25.0, travel_mm_s: float = 60.0, lift_s: float = 0.25) -> float:
        """Rough plot time for a Makelangelo-class plotter."""
        return self.ink / draw_mm_s + self.travel / travel_mm_s + self.strokes * lift_s


@dataclass
class Drawing:
    width: float                 # mm, the sheet (SVG width)
    height: float
    view: tuple                  # (x, y, w, h) in mm: what the viewBox shows, in sheet millimetres
    pens: list = field(default_factory=list)

    def bounds(self) -> tuple | None:
        xs = [p.xy for p in self.pens if len(p.xy)]
        if not xs:
            return None
        a = np.concatenate(xs)
        return float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max())

    def stats(self) -> PlotStats:
        st = PlotStats(pens=len([p for p in self.pens if len(p.xy)]))
        for pen in self.pens:
            if len(pen.xy) < 2:
                continue
            d = np.diff(pen.xy.astype(np.float64), axis=0)
            seg = np.hypot(d[:, 0], d[:, 1])
            drawn = pen.connect[:-1] == 1
            st.ink += float(seg[drawn].sum())
            st.travel += float(seg[~drawn].sum())   # end of one stroke to the start of the next
            st.strokes += pen.strokes
            st.points += pen.points
        return st


def fmt_length(mm: float) -> str:
    return f"{mm / 1000:.2f} m" if mm >= 10000 else f"{mm:,.0f} mm".replace(",", " ")


def fmt_duration(s: float) -> str:
    if s < 90:
        return f"{s:.0f} s"
    m = s / 60
    return f"{m:.0f} min" if m < 90 else f"{int(m // 60)} h {int(m % 60):02d}"


def plot_tiles(st: PlotStats, seconds: float, travel: tuple | None = None, extra=()) -> list:
    """The result tiles every drawing tool shows, in one order: Ink, Pen-up travel (`travel`: the tool's own
    (before, after) when it ordered the strokes, shown as "was …"), Plot time, then at most one or two of the tool's own (`extra`), then
    Computed in. Counts the queue shows as columns (strokes, dots, curves) go in the tool's own stats, before these."""
    from atlas_toolkit.core.tool import Stat
    before, after = travel if travel is not None else (None, st.travel)
    return [Stat("ink", "Ink", fmt_length(st.ink), fmt="{}"),
            Stat("travel", "Pen-up travel", fmt_length(after), fmt="{}",
                 before=fmt_length(before) if before is not None else None),
            Stat("time", "Plot time", fmt_duration(st.seconds()), fmt="{}"),
            *extra,
            Stat("seconds", "Computed in", seconds, "s", fmt="{:.1f}")]


def saved_lines(outputs) -> list[str]:
    """The report's last lines for the files a drawing tool wrote: the drawing, then any one-pen files
    (svgout.write_pens in "files" mode)."""
    outputs = [str(o) for o in outputs or ()]
    if not outputs:
        return []
    return [f"  saved as   {outputs[0]}"] + [f"  pen file   {o}" for o in outputs[1:]]


def polyline_length(pts) -> float:
    return sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))
