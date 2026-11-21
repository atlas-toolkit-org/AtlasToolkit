"""
plotter.vision's camera, and the frame it looks through: shared by every tool that draws a model through a camera
(Plotter Vision, Contour Lines, Sketch Hatching, Stipple), so one aimed view means the same picture in all of them.

The camera orbits a look-at point: θ is measured from +Z (90° = on the horizon, 0° = straight down from above), ψ
turns around Z, and the eye sits at lookat + r·(sin θ sin ψ, sin θ cos ψ, cos θ) with Z up (down when θ < 0). Its
basis follows plotter.vision: w from the look-at point to the eye, u = up × w to the right, v = w × u up.

Units: the model is scaled so its largest side is 100 and centred on the origin, as plotter.vision does, so its
numbers (θ 70°, ψ -150°, r 170 at start) mean the same here.

The frame is the paper's drawing area (paper minus margins). Screen coordinates s are scaled so the frame's long
side spans -1..1: the field of view is the angle across that side (plotter.vision's 60° is close to its own across
a wide window). Orthographic keeps the same scale at the look-at distance, so switching keeps the model's size.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

NEAR = 0.5          # near plane, model units (the model's largest side is 100)


@dataclass
class Camera:
    theta: float = 70.0             # degrees from +Z
    psi: float = -150.0             # degrees around Z
    radius: float = 170.0
    lookat: tuple = (0.0, 0.0, 0.0)
    fov: float = 60.0               # degrees across the frame's long side
    ortho: bool = False
    roll: float = 0.0               # degrees, clockwise on the picture (turn_in_view); camera_from leaves it at 0

    def basis(self):
        th, ps = math.radians(self.theta), math.radians(self.psi)
        look = np.asarray(self.lookat, dtype=float)
        eye = look + self.radius * np.array([math.sin(th) * math.sin(ps), math.sin(th) * math.cos(ps), math.cos(th)])
        up = np.array([0.0, 0.0, -1.0 if th < 0 else 1.0])
        w = eye - look
        w /= np.linalg.norm(w) or 1.0
        u = np.cross(up, w)
        n = np.linalg.norm(u)
        if n < 1e-9:                     # looking straight along Z: any right vector will do
            u = np.array([math.cos(ps), -math.sin(ps), 0.0])
        else:
            u /= n
        v = np.cross(w, u)
        if self.roll:                    # the picture turned clockwise: the same as the model turned about the
            c, s = math.cos(math.radians(self.roll)), math.sin(math.radians(self.roll))     # line of sight
            u, v = u * c + v * s, -u * s + v * c
        return eye, u, v, w

    @property
    def k(self) -> float:
        """Screen units per unit of x/depth: the frame's half long side is tan(fov/2)."""
        return 1.0 / math.tan(math.radians(max(min(self.fov, 170.0), 0.5)) / 2.0)

    def to_camera(self, P: np.ndarray) -> np.ndarray:
        """(..., 3) world -> (..., 3) camera: x right, y up, d = depth in front of the eye (> 0 is visible)."""
        eye, u, v, w = self.basis()
        Q = np.asarray(P, dtype=float) - eye
        return np.stack([Q @ u, Q @ v, -(Q @ w)], axis=-1)

    def project(self, C: np.ndarray) -> np.ndarray:
        """Camera coordinates (..., 3) -> screen (..., 2), long side of the frame = -1..1. Depth must be > 0 for
        perspective (clip at NEAR first)."""
        C = np.asarray(C, dtype=float)
        if self.ortho:
            s = self.k / max(self.radius, 1e-9)
            return C[..., :2] * s
        return C[..., :2] * (self.k / C[..., 2:3])

    def nearness(self, d: np.ndarray, far: float = 0.0) -> np.ndarray:
        """A quantity that is linear across any plane on screen and grows towards the viewer: 1/depth for
        perspective, (far - depth) for orthographic."""
        d = np.asarray(d, dtype=float)
        return 1.0 / d if not self.ortho else (far - d)

    def depth_of(self, q: np.ndarray, far: float = 0.0) -> np.ndarray:
        q = np.asarray(q, dtype=float)
        return 1.0 / q if not self.ortho else (far - q)

    def unproject(self, S: np.ndarray, depth: np.ndarray) -> np.ndarray:
        """Screen points (..., 2) at camera depth (...) back to world points (..., 3)."""
        S = np.asarray(S, dtype=float)
        d = np.asarray(depth, dtype=float)
        eye, u, v, w = self.basis()
        if self.ortho:
            x = S[..., 0:1] * (self.radius / self.k)
            y = S[..., 1:2] * (self.radius / self.k)
        else:
            x = S[..., 0:1] * (d[..., None] / self.k)
            y = S[..., 1:2] * (d[..., None] / self.k)
        return eye + x * u + y * v - d[..., None] * w

    def rays(self, S: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(origins, unit directions) of the rays through screen points (n, 2): from the eye for perspective,
        parallel to the view axis for orthographic (starting in the eye's plane)."""
        S = np.asarray(S, dtype=float).reshape(-1, 2)
        eye, u, v, w = self.basis()
        if self.ortho:
            sc = self.radius / self.k
            o = eye + (S[:, 0:1] * sc) * u + (S[:, 1:2] * sc) * v
            return o, np.tile(-w, (len(S), 1))
        d = (S[:, 0:1] / self.k) * u + (S[:, 1:2] / self.k) * v - w
        d /= np.linalg.norm(d, axis=1, keepdims=True)
        return np.tile(eye, (len(S), 1)), d

    def mm_per_unit(self, frame: "Frame") -> float:
        """Millimetres on paper per model unit at the look-at distance (exact everywhere when orthographic)."""
        return frame.half * self.k / max(self.radius, 1e-9)

    def stereo(self, separation: float) -> "Camera":
        """plotter.vision's second eye: the same camera turned `separation` degrees further round Z."""
        return Camera(self.theta, self.psi + separation, self.radius, self.lookat, self.fov, self.ortho, self.roll)

    def as_values(self) -> dict:
        """The camera as settings, at the settings' own precision (so writing them back changes nothing)."""
        return {"theta": round(self.theta, 1), "psi": round(self.psi, 1), "radius": round(self.radius, 1),
                "lookat": tuple(round(float(x), 2) for x in self.lookat), "fov": round(self.fov, 1)}


def camera_from(p) -> Camera:
    """The camera of a tool's settings (camera_params keys)."""
    look = tuple(float(x) for x in (p.lookat or (0.0, 0.0, 0.0)))
    return Camera(float(p.theta), float(p.psi), float(p.radius), look, float(p.fov), p.projection == "orthographic")


def fit_radius_points(points: np.ndarray, cam: Camera, frame: "Frame", margin: float = 1.04) -> float:
    """The radius at which points seen around the look-at point just fit the frame's short side."""
    P = np.asarray(points, dtype=float).reshape(-1, 3)
    R = float(np.linalg.norm(P - np.asarray(cam.lookat), axis=1).max()) * margin if len(P) else 1.0
    x0, y0, x1, y1 = frame.screen
    tan_short = min(x1, y1) / cam.k                # long side spans -1..1
    if cam.ortho:
        return R / max(tan_short, 1e-6)
    return R / max(math.sin(math.atan(tan_short)), 1e-6)


def wrap(angle: float) -> float:
    """-180..180, as plotter.vision normalises θ and ψ."""
    return (angle + 180.0) % 360.0 - 180.0


@dataclass
class Frame:
    """The paper's drawing area and the screen rectangle it shows."""
    sheet: tuple            # (W, H) mm
    area: tuple             # (x0, y0, w, h) mm: paper minus margins

    @property
    def half(self) -> float:
        return max(self.area[2], self.area[3]) / 2.0          # mm per screen unit

    @property
    def screen(self) -> tuple:
        """(x0, y0, x1, y1) in screen units."""
        hx, hy = self.area[2] / 2.0 / self.half, self.area[3] / 2.0 / self.half
        return (-hx, -hy, hx, hy)

    def to_mm(self, S: np.ndarray) -> np.ndarray:
        S = np.asarray(S, dtype=float)
        x0, y0, w, h = self.area
        return np.stack([x0 + w / 2.0 + S[..., 0] * self.half, y0 + h / 2.0 - S[..., 1] * self.half], axis=-1)


def frame_for(paper: str, orientation: str, margin: float, custom: tuple | None = None) -> Frame:
    from atlas_toolkit.geom.paper import sheet
    W, H = sheet(paper, orientation, custom)
    m = max(0.0, min(margin, min(W, H) / 2.0 - 1.0))
    return Frame((W, H), (m, m, W - 2 * m, H - 2 * m))


def roll_screen(S: np.ndarray, roll: float) -> np.ndarray:
    """Screen points turned by `roll` degrees about the look-at axis, as a camera with roll sees them: the picture's
    right becomes right·cos + up·sin (the Hatch Maker's Roll)."""
    if not roll:
        return np.asarray(S, dtype=float)
    c, s = math.cos(math.radians(roll)), math.sin(math.radians(roll))
    S = np.asarray(S, dtype=float)
    return np.stack([S[..., 0] * c + S[..., 1] * s, -S[..., 0] * s + S[..., 1] * c], axis=-1)


def turn_matrix(cam: Camera, roll: float) -> np.ndarray:
    """The rotation (3x3, for column vectors) that turns the model `roll` degrees about the camera's line of sight,
    clockwise as the picture shows it (Roll in the Aim tab)."""
    if not roll:
        return np.eye(3)
    _eye, u, v, w = cam.basis()
    B = np.stack([u, v, w])                                    # world -> camera
    c, s = math.cos(math.radians(roll)), math.sin(math.radians(roll))
    Z = np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])  # roll_screen, in camera axes
    return B.T @ Z @ B


def turn_in_view(P: np.ndarray, cam: Camera, roll: float) -> np.ndarray:
    """Points of the model turned `roll` degrees about the line of sight through the look-at point (Roll, clockwise on
    the picture). The axis runs through the eye, so the turned model's picture is the picture turned (roll_screen,
    exactly, in perspective too) while what a tool keeps fixed in the world stays: Contour Lines' level slices, a
    light set on the model. Tools that draw only the model's own lines may roll the camera instead (Camera.roll)."""
    P = np.asarray(P, dtype=float)
    if not roll:
        return P
    look = np.asarray(cam.lookat, dtype=float)
    return (P - look) @ turn_matrix(cam, roll).T + look


def fit_view(cam: Camera, points: np.ndarray, frame: Frame, roll: float = 0.0, margin: float = 1.02) -> None:
    """Move the look-at point and the distance (not the angles) so the points' picture fills the frame, centred:
    the Aim tab's F and Always fit. Exact for orthographic; perspective converges in a few steps."""
    P = np.asarray(points, dtype=float).reshape(-1, 3)
    if not len(P):
        return
    _, _, hx, hy = frame.screen
    cam.lookat = tuple(float(a) for a in 0.5 * (P.min(0) + P.max(0)))
    cam.radius = max(fit_radius_points(P, cam, frame, margin), 1.0)      # a safe start: everything in front
    for _ in range(12):
        _eye, u, v, _w = cam.basis()
        C = cam.to_camera(P)
        if not cam.ortho and (C[:, 2] <= NEAR * 2).any():
            cam.radius *= 1.5
            continue
        S = roll_screen(cam.project(C), roll)
        lo, hi = S.min(0), S.max(0)
        cx, cy = (lo + hi) / 2.0
        ratio = max((hi[0] - lo[0]) / 2.0 / hx, (hi[1] - lo[1]) / 2.0 / hy, 1e-9) * margin
        c, s = math.cos(math.radians(roll)), math.sin(math.radians(roll))
        ux, uy = cx * c - cy * s, cx * s + cy * c                # the middle, back in the unrolled picture
        if cam.ortho:
            unit = cam.radius / cam.k                            # model units per screen unit
            cam.lookat = tuple(float(a) for a in np.asarray(cam.lookat) + (ux * u + uy * v) * unit)
            cam.radius *= ratio
            return
        depth = float(np.median(C[:, 2]))
        cam.lookat = tuple(float(a) for a in np.asarray(cam.lookat) + (ux * u + uy * v) * depth / cam.k)
        cam.radius = max(cam.radius + depth * (ratio - 1.0), 1.0)
        if abs(ratio - 1.0) < 2e-3 and abs(cx) < 2e-3 and abs(cy) < 2e-3:
            return
