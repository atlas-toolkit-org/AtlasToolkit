"""
The Aim tab of the Mesh → SVG tools: Plotter Vision's live view, showing a light copy of the model (at most AIM_FACES
faces) as its outline and creases with hidden lines removed, through the tool's own camera on the tool's own paper.
Every Aim tab is driven the same way: drag to orbit, Shift-drag to move, wheel for distance, 1-5 views, F fits, A
fits after every move (Always fit), P pauses the hidden lines (Pause lines), R resets; the ring round the frame
(or Q / E) rolls the model like a clock hand, and the dial in the corner (or 0) sets it upright again. The camera is written back
into the settings, so what the frame shows is what the tool draws. The Aim tab is the one place a drawing's view is set (`sets_view`): the Input tab only
follows.

    AimView        Contour Lines, Sketch Hatching, Stipple, FlowRep Sketch: the model is placed in the tool's frame
                   (geom/frame3d: its Up, Front and extra turn, and the shared frame of a stage set), the one the
                   engine draws in, so the numbers mean the same in both; F fits the frame's whole box.
    VisionAimView  Plotter Vision: the same light trace in Plotter Vision's own units; X (or the Exact lines
                   button) shows the exact drawing instead, live, as plotter.vision did (slow on big models).

The Hatch Maker's Aim (tools/stl_hatch_maker/aim.py) is a LightAim with an orthographic camera and Roll.

A tool hosts one with   viewer="atlas_toolkit.tools.plotter_vision.aim:AimView"
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt

from atlas_toolkit.core.params import FRAME_KEYS
from atlas_toolkit.geom import frame3d
from atlas_toolkit.ui.widgets.common import tip_html

from . import model as pm
from .viewer import MODEL_KEYS, VisionView

AIM_FACES = 20_000
# The light trace: what every Aim tab draws (and Plotter Vision's Sketch preset).
TRACE = dict(decimate=True, target_faces=AIM_FACES, fix_winding=True, crossings=False, flat_angle=35.0,
             sides="auto", edge_mode="features", stereo=False, eye_sep=2.0, depth_layers=1, far_dashed=False,
             outline_layer=False, show_hidden=False, min_piece=0.1, line_width=0.3, outline_width=0.5,
             min_stroke=0.0, simplify=0.02, optimise=True)
PAPER_KEYS = ("paper", "orientation", "margin", "paper_width", "paper_height")
AIM_KEYS = ("drag orbit · shift-drag move · ring / Q E roll · wheel distance · 1-5 views · F fit · A always fit · "
            "P pause lines · R reset")


class LightAim(VisionView):
    """An Aim tab's light trace: the model reduced to AIM_FACES (model.load_light: always within it, and reduced once
    per file), outline and creases, no stereo."""
    title = "Aim"
    STEREO = False
    REDRAW_KEYS = PAPER_KEYS
    KEYS = AIM_KEYS

    def load_model(self, path: str, p):
        return pm.load_light(path, AIM_FACES, bool(p.fix_winding))

    def stage_note(self) -> tuple[str, str] | None:
        """Said beside the tabs, in view all the time: this is not the drawing yet."""
        return "Simplified view · the drawing has more detail", tip_html(
            "Aim shows a simplified view", f"It traces a light version of the model (up to {AIM_FACES:,} faces) as "
            "outlines and main creases, to stay quick while you turn it. The tool's own hatching, contours or dots are "
            "not shown, and fine detail is left out.", "Use it to set the view and framing, then Preview or Run to see "
            "the drawing.")

    def _mode_text(self) -> str:
        return " · ".join(t for t in ("simplified", super()._mode_text()) if t)


class AimView(LightAim):
    DEFAULTS = dict(TRACE)
    MODEL_KEYS = MODEL_KEYS + FRAME_KEYS

    def load_model(self, path: str, p):
        frame = frame3d.frame_for(path, p)
        self._frame = frame
        return pm.load_light(path, AIM_FACES, True, frame=frame)

    def fit_points(self) -> np.ndarray:
        """The frame's whole box (every stage of a set), so the plates of a sequence fit alike."""
        f = getattr(self, "_frame", None)
        if f is None or f.lo is None:
            return super().fit_points()
        lo, hi = f.lo, f.hi
        return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])


class VisionAimView(LightAim):
    """Plotter Vision's Aim: the light trace by default; Exact lines (E) draws what Draw will write, live."""
    TRACE = {k: v for k, v in TRACE.items() if k != "fix_winding"}   # the tool's own winding fix stays
    MODEL_KEYS = ("fix_winding",)
    EXACT_KEYS = AIM_KEYS + " · T stereo · X simplified view"

    def load_model(self, path: str, p):
        if self.exact:                           # the tool's own decimation and crossings, as Draw has them
            return VisionView.load_model(self, path, p)
        return super().load_model(path, p)

    def __init__(self, page):
        super().__init__(page)
        self.exact = False                       # view state only: never a setting, off whenever the page opens
        self.btn_exact = self._corner_button(
            "Exact lines · X", "X: show the exact drawing (every edge, depth pens, stereo) instead of the simplified view. "
                           "It can be slow on big models; Draw uses your settings either way.", self.set_exact)
        self.KEYS = AIM_KEYS + " · X exact lines"

    def set_exact(self, on: bool) -> None:
        on = bool(on)
        if on == self.exact:
            return
        self.exact = on
        if on:                                   # instance attributes shadow the light trace's
            self.TRACE, self.MODEL_KEYS, self.REDRAW_KEYS = {}, MODEL_KEYS, None
            self.STEREO, self.KEYS = True, self.EXACT_KEYS
        else:
            for k in ("TRACE", "MODEL_KEYS", "REDRAW_KEYS", "STEREO"):
                self.__dict__.pop(k, None)
            self.KEYS = AIM_KEYS + " · X exact lines"
        self.btn_exact.blockSignals(True)
        self.btn_exact.setChecked(on)
        self.btn_exact.blockSignals(False)
        if self.path is not None:
            self._load_model()                   # another face budget (both stay cached in model._CACHE)
        self.info_changed.emit(self.info_text())
        self.update()

    def stage_note(self) -> tuple[str, str] | None:
        if not self.exact:
            text, tip = super().stage_note()
            return text, tip + tip_html("Exact lines (X)", "Shows the whole drawing here instead, which can be slow on "
                                                           "big models.")
        return "Exact lines · can be slow on big models", tip_html(
            "Exact lines", "The drawing as Draw will make it: every edge, depth pens and stereo. On big models each move "
            "can take a while; turn it off (X) for the quick, simplified view.")

    def _mode_text(self) -> str:
        if not self.exact:
            return super()._mode_text()
        return " · ".join(t for t in ("exact lines", VisionView._mode_text(self)) if t)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_X:
            self.set_exact(not self.exact)
            return
        super().keyPressEvent(e)
