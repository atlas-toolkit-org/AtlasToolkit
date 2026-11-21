"""
The Hatch Maker's Aim tab: the light trace every Mesh → SVG tool aims with (tools/plotter_vision/aim.py), driven the
same way (drag, Shift-drag, wheel, 1-5, F, A, R), through the Hatch Maker's camera: the shared orbit camera, always
orthographic, with a fixed field of view (scene.FOV), and Roll turning the picture. The model is placed as the engine
places it (centred, largest side 100: scene.Scene and plotter_vision.model agree), so the frame shows what Draw writes.
"""
from __future__ import annotations

from atlas_toolkit.tools.plotter_vision.aim import PAPER_KEYS, TRACE, LightAim

from .scene import FOV


class HatchAimView(LightAim):
    """The model in the file's own axes, as the engine draws it (LightAim's light copy, placed by no frame)."""
    DEFAULTS = {**TRACE, "fov": FOV, "projection": "orthographic"}
    MODEL_KEYS = ()
    REDRAW_KEYS = PAPER_KEYS + ("roll",)

    def _camera_text(self) -> str:
        c = self.cam
        look = ", ".join(f"{x:.0f}" for x in c.lookat)
        return (f"theta {c.theta:.1f}°   psi {c.psi:.1f}°   r {c.radius:.0f}   look ({look})   ortho   "
                f"roll {self._roll_deg():.1f}°")
