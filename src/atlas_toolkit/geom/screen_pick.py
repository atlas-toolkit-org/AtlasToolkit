"""Which faces of a mesh lie under a brush on screen and are visible there: screen-space picking, no rays, no GUI.

A 3D view renders the mesh once per camera into an offscreen *face-id buffer* (face index + 1 as colour, depth tested,
0 where there is nothing) and hands it here with its depth buffer and matrices. Face centres are grouped once into
the cells of a coarse 3D grid (a few hundred faces each), so neither a new camera nor a brush stroke ever goes over the
whole mesh: a stroke looks at the buffer pixels under the brush and at the faces of the cells its projection reaches.

- every face seen by a buffer pixel inside the brush is picked (large faces, and anything a pixel or bigger);
- a face whose centre projects inside the brush is picked too when that centre is no deeper than the visible surface
  at its pixel, within a tolerance (faces smaller than a pixel, so dense meshes paint solid, not speckled).

Hidden faces stay unpicked. Walls thinner than the tolerance may be picked on both sides.
The same idea as the select-ID buffers of mesh editors such as Blender's edit mode.
"""
from __future__ import annotations

import numpy as np

PER_CELL = 256     # faces per cell of the 3D grid, on average


def encode_ids(count: int) -> np.ndarray:
    """RGBA8 colours, one per face, carrying face index + 1 (0 is the background). Up to 16.7 million faces."""
    if count >= 1 << 24:
        raise ValueError("Too many faces to paint (at most 16 777 215).")
    k = np.arange(1, count + 1, dtype=np.uint32)
    return np.column_stack(((k & 255), (k >> 8) & 255, (k >> 16) & 255, np.full(count, 255, np.uint32))).astype(np.uint8)


def decode_ids(rgba: np.ndarray) -> np.ndarray:
    """Face index per pixel from an RGBA8 image (rows top to bottom); -1 for the background."""
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    packed = rgba.view("<u4")[..., 0] & np.uint32(0xFFFFFF)
    return packed.astype(np.int32) - 1


class ScreenPicker:
    """Brush picking over one mesh. `set_view` once per camera, then `stroke` as often as the brush moves.

    `centers` are the face centres in the same coordinates the view draws. `tolerance` is in those units.
    """

    def __init__(self, centers, tolerance: float):
        centers = np.asarray(centers, dtype=np.float64).reshape(-1, 3)
        self.tolerance = float(tolerance)
        self.ready = False
        if not len(centers):
            self.xyz, self.order = np.zeros((3, 0), np.float32), np.zeros(0, np.int64)
            self.cell_center, self.cell_radius = np.zeros((0, 3)), 0.
            self.starts = np.zeros(1, np.int64)
            return
        lo, hi = centers.min(0), centers.max(0)
        size = np.maximum(hi - lo, 1e-12)
        # A cubic grid of about len/PER_CELL cells over the bounding box (a surface fills few of them; only the
        # occupied ones are kept).
        step = float((np.prod(size) * PER_CELL / len(centers)) ** (1 / 3))
        step = max(step, float(size.max()) / 1024)
        dims = np.maximum(np.ceil(size / step).astype(np.int64), 1)
        cell = np.minimum(((centers - lo) / step).astype(np.int64), dims - 1)
        keys = (cell[:, 0] * dims[1] + cell[:, 1]) * dims[2] + cell[:, 2]
        self.order = np.argsort(keys, kind="stable")
        keys = keys[self.order]
        first = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
        self.starts = np.r_[first, len(keys)]
        self.cell_center = lo + (cell[self.order[first]] + .5) * step
        self.cell_radius = step * np.sqrt(3) / 2
        self.xyz = np.ascontiguousarray(centers[self.order].T.astype(np.float32))   # sorted by cell, one row per axis

    # ------------------------------------------------------------------ camera
    def set_view(self, projection, view, width: int, height: int, ids, depth) -> None:
        """Matrices are 4x4 (column vectors, OpenGL's). `ids` is (height, width) face index or -1 and `depth` the
        window depth in [0, 1], both with row 0 at the top, in the same pixels `stroke` gets."""
        self.P = np.asarray(projection, dtype=np.float64)
        self.V = np.asarray(view, dtype=np.float64)
        self.M = self.P @ self.V
        self.width, self.height = int(width), int(height)
        self.ids = np.asarray(ids, dtype=np.int32)
        self.window = np.asarray(depth, dtype=np.float32)       # made linear only where a stroke looks
        self.perspective = abs(self.P[3, 2]) > 1e-12
        self.pixel = 2. / (abs(self.P[1, 1]) * max(self.height, 1))   # one pixel's size at unit depth (or anywhere, ortho)
        # Cells to the screen: their centre, and a radius in pixels that covers every face centre in them.
        sx, sy, eye, _ = self._project(self.cell_center.T)
        reach = self.cell_radius / (self.pixel * (np.maximum(eye, 1e-12) if self.perspective else np.ones_like(eye)))
        reach[eye <= self.cell_radius] = np.inf          # around or behind the eye: always looked into
        self.cells = (sx, sy, reach)
        self.ready = True

    def _project(self, xyz):
        """Points (3, n) to pixels, with their distance in front of the eye and whether they are in front at all."""
        x, y, z = xyz
        M = self.M.astype(x.dtype)
        V = self.V.astype(x.dtype)
        w = x * M[3, 0] + y * M[3, 1] + z * M[3, 2] + M[3, 3]
        eye = -(x * V[2, 0] + y * V[2, 1] + z * V[2, 2] + V[2, 3])
        front = w > 1e-9
        w = np.where(front, w, 1.)
        sx = ((x * M[0, 0] + y * M[0, 1] + z * M[0, 2] + M[0, 3]) / w + 1.) * (self.width / 2.)
        sy = (1. - (x * M[1, 0] + y * M[1, 1] + z * M[1, 2] + M[1, 3]) / w) * (self.height / 2.)
        return sx, sy, eye, front

    def _linear(self, window):
        """Window depth [0, 1] to distance in front of the eye, for any OpenGL projection."""
        P = self.P
        z = 2. * np.asarray(window, dtype=np.float64) - 1.
        with np.errstate(divide="ignore", invalid="ignore"):
            eye_z = (P[2, 3] - z * P[3, 3]) / (z * P[3, 2] - P[2, 2])
        return -eye_z

    # ------------------------------------------------------------------ brush
    def stroke(self, path, radius: float) -> np.ndarray:
        """Faces under a brush of `radius` pixels swept along `path`, one or more points (pixels, top-left origin).
        A face may be listed twice (seen by a pixel and by its centre)."""
        if not self.ready:
            return np.zeros(0, np.int64)
        path = np.asarray(path, float).reshape(-1, 2)
        r = float(radius)
        (x_min, y_min), (x_max, y_max) = path.min(0), path.max(0)
        left, right = max(int(np.floor(x_min - r)), 0), min(int(np.ceil(x_max + r)), self.width - 1)
        top, bottom = max(int(np.floor(y_min - r)), 0), min(int(np.ceil(y_max + r)), self.height - 1)
        if left > right or top > bottom:
            return np.zeros(0, np.int64)
        # Pixels: their centres inside the swept circle.
        py, px = np.mgrid[top:bottom + 1, left:right + 1]
        inside = _near_path(px + .5, py + .5, path, r)
        seen = self.ids[top:bottom + 1, left:right + 1][inside].astype(np.int64)
        seen = seen[seen >= 0]
        # Face centres in the cells the brush reaches, then the exact capsule and the depth test.
        sx, sy, reach = self.cells
        hit = np.flatnonzero(_near_path(sx, sy, path, r + reach))
        if not len(hit):
            return seen
        a, b = self.starts[hit], self.starts[hit + 1]
        lengths = b - a
        k = np.repeat(a - np.cumsum(np.r_[0, lengths[:-1]]), lengths) + np.arange(int(lengths.sum()))
        x, y, z = self.xyz[:, k]
        M = self.M.astype(np.float32)
        w = x * M[3, 0] + y * M[3, 1] + z * M[3, 2] + M[3, 3]
        front = w > 1e-9
        w[~front] = 1.
        fx = ((x * M[0, 0] + y * M[0, 1] + z * M[0, 2] + M[0, 3]) / w + 1.) * (self.width / 2.)
        fy = (1. - (x * M[1, 0] + y * M[1, 1] + z * M[1, 2] + M[1, 3]) / w) * (self.height / 2.)
        keep = front & _near_path(fx, fy, path, r)
        keep &= (fx >= 0) & (fx < self.width) & (fy >= 0) & (fy < self.height)
        k, fx, fy = k[keep], fx[keep], fy[keep]
        V = self.V.astype(np.float32)
        x, y, z = self.xyz[:, k]
        eye = -(x * V[2, 0] + y * V[2, 1] + z * V[2, 2] + V[2, 3])
        col = np.minimum(fx.astype(np.int64), self.width - 1)
        row = np.minimum(fy.astype(np.int64), self.height - 1)
        surface = self._linear(self.window[row, col])
        surface[self.ids[row, col] < 0] = np.inf
        slack = self.tolerance + 2. * self.pixel * (np.where(np.isfinite(surface), surface, 0.) if self.perspective
                                                     else 1.)
        visible = eye <= surface + slack
        return np.concatenate((seen, self.order[k[visible]]))


def _near_path(x, y, path, r):
    """Whether points are within `r` of a polyline (or of its one point)."""
    if len(path) == 1:
        return _near_segment(x, y, *path[0], *path[0], r)
    near = _near_segment(x, y, *path[0], *path[1], r)
    for (x0, y0), (x1, y1) in zip(path[1:-1], path[2:]):
        near |= _near_segment(x, y, x0, y0, x1, y1, r)
    return near


def _near_segment(x, y, x0, y0, x1, y1, r):
    """Whether points are within `r` (a number, or one per point) of the segment from (x0, y0) to (x1, y1)."""
    dx, dy = x1 - x0, y1 - y0
    length2 = dx * dx + dy * dy
    if length2 < 1e-12:
        t = 0.
    else:
        t = np.clip(((x - x0) * dx + (y - y0) * dy) / length2, 0., 1.)
    ex, ey = x - (x0 + t * dx), y - (y0 + t * dy)
    return ex * ex + ey * ey <= r * r
