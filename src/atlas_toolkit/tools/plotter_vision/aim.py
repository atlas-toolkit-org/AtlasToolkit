"""
The Aim tab of the 3D drawing tools (Contour Lines, Sketch Hatching, Stipple): Plotter Vision's live view, showing a
light copy of the model as its outline and creases with hidden lines removed, through the tool's own camera
(camera_params) on the tool's own paper. Drag to orbit, wheel to come closer, F to fit; the camera is written back
into the settings, so what the frame shows is what the tool draws.

The model is placed in the tool's frame (geom/frame3d: its Up, Front and extra turn, and the shared frame of a stage
set), the one the engine draws in, so the numbers mean the same in both.

A tool hosts it with   viewer="atlas_toolkit.tools.plotter_vision.aim:AimView"
"""
from __future__ import annotations

import numpy as np

from atlas_toolkit.core.params import FRAME_KEYS
from atlas_toolkit.geom import frame3d
from atlas_toolkit.geom.camera import fit_radius_points, frame_for

from . import model as pm
from .viewer import MODEL_KEYS, VisionView

AIM_FACES = 20_000


class AimView(VisionView):
    title = "Aim"
    DEFAULTS = dict(decimate=True, target_faces=AIM_FACES, fix_winding=True, crossings=False, flat_angle=35.0,
                    sides="auto", stereo=False, eye_sep=2.0, depth_layers=1, far_dashed=False, outline_layer=False,
                    show_hidden=False, min_piece=0.1, line_width=0.3, outline_width=0.5, min_stroke=0.0,
                    simplify=0.02, optimise=True)
    MODEL_KEYS = MODEL_KEYS + FRAME_KEYS
    STEREO = False
    REDRAW_KEYS = ("paper", "orientation", "margin", "paper_width", "paper_height")
    KEYS = "drag orbit · shift-drag move · wheel distance · 1-5 views · F fit · R reset"

    def load_model(self, path: str, p):
        frame = frame3d.frame_for(path, p)
        self._frame = frame
        return pm.load(path, AIM_FACES, True, None, False, frame=frame)

    def fit_radius(self, p) -> float:
        """Fit the frame's whole box (every stage of a set), not just this file."""
        f = getattr(self, "_frame", None)
        if f is None:
            return super().fit_radius(p)
        lo, hi = f.lo, f.hi
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        return fit_radius_points(corners, self.cam, frame_for(p.paper, p.orientation, p.margin, (p.get("paper_width", 200.), p.get("paper_height", 200.))))
