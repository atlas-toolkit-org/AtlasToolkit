"""
The canonical frame of a 3D model: which way is up, which side is the front, and how big it is.

Every 3D tool works in the same frame, so their settings mean the same thing on any model:

    canonical Z = the model's up axis, canonical -Y = its front (the face of a head looks toward -Y),
    X = Y × Z (right-handed, so faces keep their winding), then an optional extra turn about X, Y, Z (degrees,
    world axes, in that order, like Blender's XYZ Euler).

The origin is the centre of the (rotated) bounding box. Two scales are in use:
- drawing tools: the largest side is 100 (plotter.vision's units, the camera's numbers);
- distortion tools: the height is 2 (the Blender pieces' "units of a 2-tall model", in which their tuned
  distortion values are given). Lengths in the settings are % of the model's height in both.

Auto axes follow the Atlas gallery's rule (studio/gallery/plotter_gallery/library/orient.py): STL/PLY/3MF are Z-up
with the front at -Y, OBJ is Y-up with the front at +Z; a short default up axis next to a clearly taller one means
the model was stored lying down, so the tallest axis is up; flat pieces (reliefs) face the camera with their thin
axis. A stage set written by a toolkit tool carries the frame it was made in (stages.json), which wins over Auto.

Drawing tools give every stage of a set one frame: the bounds are the union of all stages (vertices only, cached),
so the plates share scale, position and slice heights.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

AXIS = {"x": 0, "y": 1, "z": 2}
DRAWING_SIZE = 100.0          # largest side, as plotter.vision
MODEL_HEIGHT = 2.0            # height, as the Blender pieces


def axis_vec(name: str) -> np.ndarray:
    v = np.zeros(3)
    v[AXIS[name[-1]]] = -1.0 if name.startswith("-") else 1.0
    return v


def fmt_of(path) -> str:
    return Path(path).suffix.lower().lstrip(".")


def guess_axes(extent, fmt: str = "stl") -> tuple[str, str]:
    """(up, front) from the bounding-box extent (x, y, z) and the file format, as the Atlas gallery does."""
    e = [max(float(x), 1e-9) for x in extent]
    up, front = ("+y", "+z") if fmt == "obj" else ("+z", "-y")
    tall = int(np.argmax(e))
    if e[AXIS[up[1]]] < 0.5 * e[tall] and tall != AXIS[up[1]]:
        up = "+" + "xyz"[tall]
        others = [i for i in range(3) if i != tall]
        depth = min(others, key=lambda i: e[i])          # the shallower remaining axis is the depth
        front = ("-" if depth == 1 else "+") + "xyz"[depth]
    thin = int(np.argmin(e))
    if e[thin] < 0.25 * max(e):                          # a relief or a tile: seen head-on along its thin axis
        if thin == AXIS[up[1]]:                          # lying flat: the longer other axis is up
            others = [i for i in range(3) if i != thin]
            up = "+" + "xyz"[max(others, key=lambda i: e[i])]
        front = "+" + "xyz"[thin] if thin != 1 else "-y"
    return up, front


def euler_xyz(deg) -> np.ndarray:
    """Rotation about X, then Y, then Z (world axes), degrees: Rz @ Ry @ Rx."""
    ax, ay, az = (math.radians(float(a)) for a in deg)
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rz @ ry @ rx


def euler_from_matrix(R: np.ndarray) -> tuple[float, float, float]:
    """The XYZ angles (degrees) of a rotation matrix: inverse of euler_xyz (for recipes that chain turns)."""
    sy = -float(R[2, 0])
    ay = math.asin(max(-1.0, min(1.0, sy)))
    if abs(sy) < 0.999999:
        ax = math.atan2(R[2, 1], R[2, 2])
        az = math.atan2(R[1, 0], R[0, 0])
    else:
        ax, az = math.atan2(-R[1, 2], R[1, 1]), 0.0
    return tuple(round(math.degrees(a), 6) for a in (ax, ay, az))


def rotation(up: str = "+z", front: str = "-y", rotate=(0.0, 0.0, 0.0)) -> np.ndarray:
    """Model -> canonical rotation (rows are the canonical axes in model coordinates, then the extra turn)."""
    u, f = axis_vec(up), axis_vec(front)
    if abs(float(u @ f)) > 0.5:                          # degenerate pair: pick a perpendicular front
        f = axis_vec("-y") if abs(u[1]) < 0.5 else axis_vec("+z")
    Z, Y = u, -f
    X = np.cross(Y, Z)
    R0 = np.stack([X, Y, Z])
    return euler_xyz(rotate) @ R0


@dataclass(eq=False)
class Frame3D:
    """canonical = (R @ model - centre) * scale. centre is in rotated model units."""
    R: np.ndarray
    centre: np.ndarray
    scale: float
    up: str = "+z"
    front: str = "-y"
    rotate: tuple = (0.0, 0.0, 0.0)
    lo: np.ndarray = None            # canonical bounds of the frame's reference (the model, the set, the reference)
    hi: np.ndarray = None
    note: str = ""

    def apply(self, V) -> np.ndarray:
        return (np.asarray(V, dtype=np.float64) @ self.R.T - self.centre) * self.scale

    def inverse(self, C) -> np.ndarray:
        return (np.asarray(C, dtype=np.float64) / self.scale + self.centre) @ self.R

    def apply_dirs(self, N) -> np.ndarray:
        """Directions (normals) to the canonical frame (rotation only)."""
        return np.asarray(N, dtype=np.float64) @ self.R.T

    @property
    def extent(self) -> np.ndarray:
        return self.hi - self.lo

    @property
    def height(self) -> float:
        """Canonical height of the reference (2 for a distortion frame of one model)."""
        return float(self.extent[2])

    @property
    def floor(self) -> float:
        return float(self.lo[2])

    def pct_h(self, pct: float) -> float:
        """A length given as % of the height, in canonical units."""
        return float(pct) / 100.0 * self.height

    def key(self) -> tuple:
        return (tuple(np.round(self.R, 9).ravel()), tuple(np.round(self.centre, 9)), round(float(self.scale), 12))

    def frame_values(self) -> dict:
        return {"up": self.up, "front": self.front, "rotate": [float(a) for a in self.rotate]}


def make_frame(lo_r, hi_r, up: str, front: str, rotate, R=None, mode: str = "size", size: float = DRAWING_SIZE,
               note: str = "") -> Frame3D:
    """A frame from rotated-model bounds: centred, scaled so the largest side (mode "size") or the height (mode
    "height") is `size`."""
    R = rotation(up, front, rotate) if R is None else R
    lo_r, hi_r = np.asarray(lo_r, float), np.asarray(hi_r, float)
    centre = (lo_r + hi_r) / 2.0
    ext = hi_r - lo_r
    ref = float(ext[2]) if mode == "height" else float(ext.max())
    scale = size / (ref if ref > 0 else (float(ext.max()) or 1.0))
    return Frame3D(R, centre, scale, up, front, tuple(float(a) for a in rotate),
                   (lo_r - centre) * scale, (hi_r - centre) * scale, note)


def fit_frame(V, up: str = "+z", front: str = "-y", rotate=(0.0, 0.0, 0.0), mode: str = "height",
              size: float = MODEL_HEIGHT) -> Frame3D:
    """The frame of one in-memory model (the distortion tools: height 2, centred)."""
    R = rotation(up, front, rotate)
    P = np.asarray(V, dtype=np.float64) @ R.T
    return make_frame(P.min(0), P.max(0), up, front, rotate, R, mode, size)


# ----------------------------------------------------------------------------- axes from settings

def resolve_axes(path, values: dict, extent=None) -> tuple[str, str, tuple, str]:
    """(up, front, rotate, where they came from) for a file and its settings (frame_params keys)."""
    from .stages import stage_set
    up = values.get("up_axis", "auto") or "auto"
    front = values.get("front", "auto") or "auto"
    rot = tuple(float(a) for a in (values.get("rotate") or (0.0, 0.0, 0.0)))
    source = "settings"
    if up == "auto" or front == "auto":
        sset = stage_set(path) if path else None
        made = sset.frame if sset else None
        from .surface import read_frame
        embedded = read_frame(path)
        made = made or embedded
        if made:
            up = made.get("up", "+z") if up == "auto" else up
            front = made.get("front", "-y") if front == "auto" else front
            if not any(rot):
                rot = tuple(float(a) for a in made.get("rotate", (0, 0, 0)))
            source = "its stage set" if sset and sset.frame else "embedded mesh frame"
        else:
            if extent is None:
                extent = model_extent(path)
            g_up, g_front = guess_axes(extent, fmt_of(path) if path else "stl")
            if up == "auto":
                up = g_up
                if front == "auto":
                    front = g_front
                source = "auto"
            if front == "auto":
                front = g_front if abs(float(axis_vec(g_front) @ axis_vec(up))) < 0.5 else _perp_front(up)
                source = "auto"
    return up, front, rot, source


def _perp_front(up: str) -> str:
    return "-y" if AXIS[up[-1]] != 1 else "+z"


# ----------------------------------------------------------------------------- bounds (cached)

_BOUNDS: dict = {}


def _vertices(path) -> np.ndarray:
    from .mesh import load_mesh
    return np.asarray(load_mesh(path).vertices, dtype=np.float64)


def model_extent(path) -> np.ndarray:
    lo, hi = rotated_bounds(path, np.eye(3))
    return hi - lo


def rotated_bounds(path, R: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Bounds of a mesh file's vertices after rotation R, cached by file, size, time and R."""
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_size, st.st_mtime_ns, tuple(np.round(R, 9).ravel()))
    hit = _BOUNDS.get(key)
    if hit is None:
        P = _vertices(path) @ np.asarray(R).T
        hit = (P.min(0), P.max(0))
        if len(_BOUNDS) > 64:
            _BOUNDS.pop(next(iter(_BOUNDS)))
        _BOUNDS[key] = hit
    return hit[0].copy(), hit[1].copy()


def frame_for(path, values: dict, size: float = DRAWING_SIZE, mode: str = "size", V=None) -> Frame3D:
    """The frame a drawing tool uses for `path` with these settings (frame_params keys):

    - normalise "model": the file's own bounds;
    - "set": the union of every stage of its set (a lone file: its own bounds);
    - "reference": the bounds of the reference model.

    V: the file's vertices when they are already loaded (saves a read)."""
    from .stages import stage_set
    up, front, rot, source = resolve_axes(path, values, None if V is None else np.ptp(np.asarray(V), axis=0))
    R = rotation(up, front, rot)
    how = values.get("normalise", "model") or "model"
    notes = [f"up {up}, front {front}" + (f", turned {', '.join(f'{a:g}' for a in rot)}°" if any(rot) else "")
             + f" ({source})"]
    lo = hi = None
    if how == "set" and path:
        sset = stage_set(path)
        if sset is not None:
            bounds = [rotated_bounds(f, R) for f in sset.files]
            lo = np.min([b[0] for b in bounds], axis=0)
            hi = np.max([b[1] for b in bounds], axis=0)
            notes.append(f"frame of its stage set ({len(sset.files)} stages"
                         + (", stages.json" if sset.manifest else "") + ")")
    elif how == "reference":
        ref = values.get("reference_mesh") or ""
        if ref and Path(ref).exists():
            lo, hi = rotated_bounds(ref, R)
            notes.append(f"frame of {Path(ref).name}")
        else:
            notes.append("no reference model chosen: its own frame")
    if lo is None:
        if V is not None:
            P = np.asarray(V, dtype=np.float64) @ R.T
            lo, hi = P.min(0), P.max(0)
        else:
            lo, hi = rotated_bounds(path, R)
        if how == "set":
            notes.append("its own frame (not part of a stage set)")
    return make_frame(lo, hi, up, front, rot, R, mode, size, "; ".join(notes))
