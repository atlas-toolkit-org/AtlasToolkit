"""Public data contracts. All geometry stays in source units until rendering."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable
import math

import numpy as np

Progress = Callable[[float, str], None]
Cancel = Callable[[], object]


class FlowRepError(ValueError):
    """An actionable geometry or input error."""


class CancelledError(RuntimeError):
    """The caller cancelled a computation."""


def checkpoint(cancel: Cancel | None = None):
    if cancel is not None and cancel():
        raise CancelledError("FlowRep computation cancelled")


def progress_to(callback: Progress | None, value: float, message: str, cancel=None):
    checkpoint(cancel)
    if callback:
        callback(float(value), message)


@dataclass
class FlowRepOptions:
    dmax: float = 20.0
    prepare: str = "auto"       # auto | preserve | remesh
    target_faces: int = 6000
    crease_angle: float = 45.0
    clustering_passes: int = 8
    local_passes: int = 2
    regularize_iterations: int = 5
    relaxed_declutter: bool = False
    max_refinements: int = 8
    cache_dir: str | None = ".cache/flowrep"

    def __post_init__(self):
        if not 0 < self.dmax < 90:
            raise FlowRepError("dmax must be between 0 and 90 degrees")
        if self.prepare not in ("auto", "preserve", "remesh"):
            raise FlowRepError("prepare must be auto, preserve, or remesh")
        if not 100 <= self.target_faces <= 200_000:
            raise FlowRepError("target_faces must be between 100 and 200000")
        if not 0 < self.crease_angle <= 180:
            raise FlowRepError("crease_angle must be in (0, 180]")
        if any(x < 0 for x in (self.clustering_passes, self.local_passes, self.regularize_iterations)):
            raise FlowRepError("iteration counts cannot be negative")
        if self.max_refinements < 1:
            raise FlowRepError("max_refinements must be positive")
        for key in ("target_faces","clustering_passes","local_passes","regularize_iterations","max_refinements"):
            if not isinstance(getattr(self,key),(int,np.integer)):
                raise FlowRepError(f"{key} must be an integer")


@dataclass
class RenderOptions:
    paper: str = "A3"
    orientation: str = "portrait"
    margin: float = 15.0
    line_width: float = 0.3
    tolerance: float = 0.05
    sample_step: float = 0.35
    min_length: float = 0.5
    silhouette: bool = True
    hidden: bool = False
    fit_camera: bool = True
    up: str = "auto"
    front: str = "auto"
    rotate: tuple = (0.0, 0.0, 0.0)
    optimise: bool = True

    def __post_init__(self):
        if not all(math.isfinite(float(v)) for v in (self.margin,self.line_width,self.tolerance,self.sample_step,self.min_length,*self.rotate)):
            raise FlowRepError("rendering parameters must be finite")
        if self.orientation not in ("portrait", "landscape"):
            raise FlowRepError("orientation must be portrait or landscape")
        if self.tolerance <= 0 or self.sample_step <= 0 or self.line_width <= 0:
            raise FlowRepError("tolerance, sample_step, and line_width must be positive")
        if self.margin < 0 or self.min_length < 0:
            raise FlowRepError("margin and min_length cannot be negative")


@dataclass
class PolygonMesh:
    vertices: np.ndarray
    faces: tuple
    triangles: np.ndarray
    triangle_faces: np.ndarray
    reference_vertices: np.ndarray
    reference_triangles: np.ndarray
    source: str = ""
    metadata: dict = field(default_factory=dict)
    _topology: object = field(default=None, repr=False)

    @property
    def diagonal(self):
        return float(np.linalg.norm(np.ptp(self.vertices, axis=0)))

    @property
    def topology(self):
        if self._topology is None:
            from .mesh import build_topology
            self._topology = build_topology(self)
        return self._topology


@dataclass
class CurveNetwork:
    vertices: np.ndarray
    curves: list[np.ndarray]
    kinds: list[str] = field(default_factory=list)
    edge_ids: list[np.ndarray] = field(default_factory=list)
    strand_ids: list[int] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        self.vertices = np.asarray(self.vertices, dtype=np.float64).reshape(-1, 3)
        self.curves = [np.asarray(c, dtype=np.int64) for c in self.curves]
        if not np.isfinite(self.vertices).all():
            raise FlowRepError("network contains non-finite coordinates")
        for c in self.curves:
            if len(c) < 2 or np.any(c < 0) or np.any(c >= len(self.vertices)):
                raise FlowRepError("network contains an invalid curve")
        if not self.kinds:
            self.kinds = ["flowline"] * len(self.curves)
        if len(self.kinds) != len(self.curves):
            raise FlowRepError("network kinds must match curves")
        if self.edge_ids and len(self.edge_ids) != len(self.curves):
            raise FlowRepError("network edge_ids must match curves")
        if self.strand_ids and len(self.strand_ids) != len(self.curves):
            raise FlowRepError("network strand_ids must match curves")

    @property
    def polylines(self):
        return [self.vertices[c] for c in self.curves]

    @property
    def edge_count(self):
        return sum(len(c) - 1 for c in self.curves)

    @property
    def junctions(self):
        edges = {tuple(sorted((int(a), int(b)))) for c in self.curves for a, b in zip(c[:-1], c[1:])}
        degree = np.bincount(np.asarray(list(edges)).ravel(), minlength=len(self.vertices)) if edges else np.zeros(len(self.vertices), int)
        return np.flatnonzero(degree >= 3)


@dataclass
class Drawing:
    width: float
    height: float
    layers: dict[str, list[np.ndarray]]
    line_width: float = 0.3
    tolerance: float = 0.05
    metadata: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    @property
    def polylines(self):
        return [p for lines in self.layers.values() for p in lines]


def options_dict(options):
    return asdict(options)

