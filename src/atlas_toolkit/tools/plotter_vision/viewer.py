"""
Plotter Vision's live view, the base of every Aim tab (tools/plotter_vision/aim.py): plotter.vision's way of aiming
the camera, inside the toolkit (the tool spec's `viewer`).

The paper is drawn with its drawing area framed: the camera looks through that frame, so what is inside it is what
Draw writes (lines outside stay faint). Moving works as on plotter.vision:

    drag                orbit (θ, ψ), at plotter.vision's speed
    Shift-drag / right  move the look-at point (in the picture's plane; plotter.vision moved it along x and z)
    wheel               distance (Ctrl+wheel: field of view)
    1 2 3 4 5           front, side, back, other side, top
    arrows              turn and come closer, like plotter.vision's
    F / double-click    fit the model in the frame (its picture, centred and as large as the frame allows)
    A                   always fit: F after every move, until A again, Shift-drag or the wheel
    R                   plotter.vision's start view          T  red/blue stereo on/off
    P / Pause lines     no hidden lines after a move until P again: aim a heavy model over its trace, then draw
                        the lines once, where it landed (the camera still goes into the settings)

While the camera moves the candidate edges are drawn (in the accent colour, with an XYZ marker at the look-at
point); once it rests, hidden lines are removed in a background thread and the visible lines fill in from the middle
out, as plotter.vision did. The camera goes back into the settings when you let go, so Draw uses exactly this view.

Keeping up with the hand (2.4.3; scripts/dev/aim_perf.py measures it):
- every line is drawn with flat caps (_pen): round caps made Qt stroke paths through its outline stroker above 100 %
  display scaling, 1.4 s a frame for 8 600 edges at 175 %, and a paint holds the GIL, so the hidden-line thread
  stopped with it;
- lines are drawn once into a layer (_Layer) that is shown twice, inside the frame and faint outside it; the lines of
  a rest are added to their layer batch by batch, never redrawn whole while they fill in;
- mouse moves, the wheel and the keys only move the camera: the candidate edges are worked out once per frame;
- a moving frame has a time budget: when drawing every candidate takes longer, the outline stays and the rest is
  thinned evenly until the camera rests.
"""
from __future__ import annotations

import math
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QPushButton, QWidget

from atlas_toolkit.core.params import Params
from atlas_toolkit.ui.theme import mono, qcolor
from atlas_toolkit.ui.widgets.plot_view import BLUEPRINT_BG, BLUEPRINT_GRID, blueprint_color

from . import model as pm
from . import plotter_vision as pv
from .camera import NEAR, Camera, fit_view, frame_for, roll_screen, wrap

try:   # numpy -> QPainterPath in one call
    from pyqtgraph.functions import arrayToQPath
except Exception:   # pragma: no cover
    arrayToQPath = None

MODEL_KEYS = ("decimate", "target_faces", "fix_winding", "crossings")
CAMERA_KEYS = ("theta", "psi", "radius", "lookat", "fov", "projection")
PAINT_ONLY = ("line_width", "outline_width", "optimise", "simplify", "min_stroke", "far_dashed")
VIEWS = {Qt.Key_1: (90.0, 0.0), Qt.Key_2: (90.0, 90.0), Qt.Key_3: (90.0, 180.0), Qt.Key_4: (90.0, -90.0),
         Qt.Key_5: (1.0, 1.0)}                     # plotter.vision's 1-5
START = {"theta": 70.0, "psi": -150.0, "radius": 170.0, "lookat": (0.0, 0.0, 0.0), "fov": 60.0}
DRAG_DEG = math.degrees(0.005)                     # plotter.vision: 0.5 px x 0.01 rad
DRAG_LINES = 60_000                                # the first moving frame draws at most this many candidate edges
DRAG_MIN = 4_000                                   # thinning while moving never goes below this many
FRAME_S = 0.033                                    # a moving frame's budget: under it the view keeps up with the hand
FAINT = 0.22                                       # lines outside the frame (Draw leaves them out)
LIVE_PAIRS = 250_000                               # small batches, so the drawing fills in as it goes
DEPTH_COLORS = ["#0B1220", "#2A5FE0", "#6F7D99"]
STEREO_COLORS = ["#E8202A", "#1592E8"]


class _Cancelled(Exception):
    pass


class _Signals(QObject):
    model_ready = Signal(int, object)
    partial = Signal(int, object)
    done = Signal(int, object)


def _params(values: dict) -> Params:
    return Params(values)


def _segments_path(Q0: np.ndarray, Q1: np.ndarray) -> QPainterPath:
    """Disjoint segments (pixels) as one path."""
    n = len(Q0)
    if n == 0:
        return QPainterPath()
    xy = np.empty((2 * n, 2))
    xy[0::2], xy[1::2] = Q0, Q1
    if arrayToQPath is not None:
        connect = np.zeros(2 * n, np.int32)
        connect[0::2] = 1
        return arrayToQPath(np.ascontiguousarray(xy[:, 0]), np.ascontiguousarray(xy[:, 1]), connect=connect)
    path = QPainterPath()
    for a, b in zip(Q0, Q1):
        path.moveTo(*a)
        path.lineTo(*b)
    return path


def _thin(outline: np.ndarray, budget: int) -> np.ndarray:
    """Which candidates a moving frame draws when there are more than its budget: every outline edge (silhouette,
    border), then the others spread evenly."""
    budget = max(int(budget), 1)
    out, rest = np.flatnonzero(outline), np.flatnonzero(~outline)
    if len(out) >= budget:
        return out[np.linspace(0, len(out) - 1, budget).astype(np.int64)]
    room = budget - len(out)
    if len(rest) > room:
        rest = rest[np.linspace(0, len(rest) - 1, room).astype(np.int64)]
    return np.sort(np.concatenate([out, rest]))


def _pen(colour: QColor, width: float, dashed: bool = False) -> QPen:
    """The pen of every line in the view: flat caps, never round. Above 100 % display scaling Qt's raster engine
    strokes a round-capped path through its outline stroker, 50-100 times slower (1.4 s a frame for 8 600 edges at
    175 %), and a paint holds Python's GIL, so the hidden-line thread waits too. At these widths (under 2 px) the caps
    do not show."""
    return QPen(colour, width, Qt.DashLine if dashed else Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin)


def _device_ratio(qp: QPainter, widget: QWidget) -> float:
    """Pixels per logical pixel of what the painter really paints on (the screen, or the image of a grab)."""
    engine = qp.paintEngine()
    device = engine.paintDevice() if engine is not None else None
    try:
        return float(device.devicePixelRatioF()) if device is not None else float(widget.devicePixelRatioF())
    except Exception:   # pragma: no cover
        return float(widget.devicePixelRatioF())


class _Layer:
    """Lines drawn once into a transparent image at the screen's resolution, then shown twice: inside the frame as they
    are, outside it faint, as Draw leaves them out. That is half the stroking of drawing every path twice, and a layer
    keeps what it holds: hidden-line batches are added as they come, never redrawn whole while they fill in."""

    def __init__(self):
        self.image: QImage | None = None
        self.key = None             # what the layer was drawn for (the owner's choice)
        self.count = 0              # how much of it the owner has drawn (batches)

    def begin(self, width: int, height: int, dpr: float, key=None) -> QPainter:
        """A painter on the emptied layer."""
        w, h = max(int(math.ceil(width * dpr)), 1), max(int(math.ceil(height * dpr)), 1)
        if self.image is None or self.image.width() != w or self.image.height() != h:
            self.image = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        self.image.setDevicePixelRatio(dpr)
        self.image.fill(0)
        self.key, self.count = key, 0
        return self.painter()

    def painter(self) -> QPainter:
        qp = QPainter(self.image)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setBrush(Qt.NoBrush)
        return qp

    def clear(self) -> None:
        self.key, self.count = None, 0

    def show(self, qp: QPainter, inside: QRegion, outside: QRegion) -> None:
        if self.image is None or self.key is None:
            return
        qp.save()
        qp.setClipRegion(inside)
        qp.drawImage(QPointF(0.0, 0.0), self.image)
        qp.setOpacity(FAINT)
        qp.setClipRegion(outside)
        qp.drawImage(QPointF(0.0, 0.0), self.image)
        qp.restore()


def _same_camera(a: Camera, b: Camera) -> bool:
    return (abs(a.theta - b.theta) < 1e-6 and abs(a.psi - b.psi) < 1e-6 and abs(a.radius - b.radius) < 1e-6
            and np.allclose(a.lookat, b.lookat, atol=1e-6) and abs(a.fov - b.fov) < 1e-6 and a.ortho == b.ortho)


class VisionView(QWidget):
    """Plotter Vision's live view. Tools that draw through a camera host a subclass to aim
    (tools/plotter_vision/aim.py); these class attributes are what such a subclass changes."""
    title = "Aim"
    sets_view = True                     # this view, and no other tab, sets the drawing's view (ui/widgets/preview)
    info_changed = Signal(str)
    DEFAULTS: dict = {}                  # values for Plotter Vision keys the host tool does not have
    TRACE: dict = {}                     # values that win over the host's (the light trace of an Aim tab)
    MODEL_KEYS = MODEL_KEYS              # settings that reload the model
    STEREO = True                        # T toggles the red/blue pair
    REDRAW_KEYS = None                   # settings (besides the camera) that recompute the lines; None: any other
    KEYS = ("drag orbit · shift-drag move · wheel distance · 1-5 views · F fit · A always fit · P pause lines · R reset"
            " · T stereo")
    FIT_POINTS = 50_000                  # at most this many vertices are fitted (F, Always fit)

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.theme = page.theme
        self.art = "paper"
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(200, 160)
        self.sig = _Signals()
        self.sig.model_ready.connect(self._model_ready)
        self.sig.partial.connect(self._partial)
        self.sig.done.connect(self._done)
        self.path: str | None = None
        self.model = None
        self.values = dict(page.values())
        self.cam = self._camera_from_values(self.values)
        self.cand: list = []            # per eye: (A, B, outline) on screen, nothing hidden
        self.pieces: list = []          # (P0, P1, depth, outline, hidden, eye) batches
        self.state = "empty"            # empty | loading | moving | computing | paused | done | error
        self.message = ""
        self.gen = 0                    # bumped on every change: stale work is dropped
        self.mgen = 0
        self.frac = 0.0
        self.t0 = 0.0
        self.seconds = 0.0
        self.edges = 0
        self.shown = 0                  # candidate edges drawn in the last moving frame (thinned when it lags)
        self.last: QPointF | None = None
        self._dirty = False             # the camera moved: candidates are worked out at the next frame
        self._cand_gen = 0
        self._look = 0                  # bumped by a new theme or art: the layers are drawn again
        self._budget = DRAG_LINES       # candidate edges a moving frame may draw (FRAME_S)
        self._live, self._waiting, self._result = _Layer(), _Layer(), _Layer()
        self.idle = QTimer(self, singleShot=True, interval=160, timeout=self._compute)
        self.commit = QTimer(self, singleShot=True, interval=350, timeout=self._commit)
        self.corner: list = []          # checkable buttons in the bottom-right corner, right to left
        self.always_fit = False         # view state only: never a setting, off whenever the page opens
        self.btn_fit = self._corner_button("Always fit · A", "A: fit the model in the frame after every move, as F "
                                           "does. Shift-drag or the wheel turns it off.", self.set_always_fit)
        self.paused = False             # view state only: never a setting, off whenever the page opens
        self.btn_pause = self._corner_button(
            "Pause lines · P", "P: stop drawing the black lines (hidden lines removed) after each move. Turn, move and "
                               "zoom over the trace as long as you like, then resume to draw them once, for the view you "
                               "landed on. The view still goes into the settings.", self.set_paused)

    def _corner_button(self, text: str, tip: str, slot) -> QPushButton:
        """A small checkable button in the strip under the paper (the keys line's font; the theme's colours)."""
        b = QPushButton(text, self)
        b.setFont(mono(11))
        b.setStyleSheet("QPushButton { padding: 2px 8px; min-height: 16px; border-radius: 6px; }")
        b.setCheckable(True)
        b.setFocusPolicy(Qt.NoFocus)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b.toggled.connect(slot)
        b.adjustSize()
        self.corner.append(b)
        return b

    def _p(self, values: dict) -> Params:
        return _params({**self.DEFAULTS, **values, **self.TRACE})

    def _camera_from_values(self, values: dict) -> Camera:
        """The camera the settings describe (a host with another camera model overrides this and _commit)."""
        return pv.camera_from(self._p(values))

    def input_view(self) -> tuple[float, float] | None:
        """The camera's direction as the Input tab's (azimuth, elevation): the 3D viewer's convention, in the file's
        own coordinates, so the Input tab can turn to the side being aimed at."""
        _eye, _u, _v, w = self.cam.basis()
        frame = getattr(self, "_frame", None)
        if frame is not None:                      # canonical = R @ file: back to the file's axes
            w = np.asarray(frame.R, dtype=float).T @ w
        w = w / (np.linalg.norm(w) or 1.0)
        return math.degrees(math.atan2(w[1], w[0])), math.degrees(math.asin(float(np.clip(w[2], -1.0, 1.0))))

    def load_model(self, path: str, p):
        """The model for the view (runs in a thread)."""
        return pm.load(path, int(p.target_faces) if p.decimate else 0, bool(p.fix_winding), None, bool(p.crossings))

    # ================================================================== host interface
    def info_text(self) -> str:
        p = self._p(self.values)
        if self.model is None:
            return f"{p.paper} {p.orientation}"
        return f"{p.paper} {p.orientation}   {self.edges:,} edges".replace(",", " ")

    def set_theme(self, theme) -> None:
        self.theme = theme
        self._look += 1
        self.update()

    def set_art(self, art: str) -> None:
        self.art = art
        self._look += 1
        self.update()

    def set_input(self, path, info) -> None:
        vals = dict(self.page.values())          # the page restores its settings after building us
        if vals != self.values:
            model_changed = any(self.values.get(k) != vals.get(k) for k in self.MODEL_KEYS)
            self.values = vals
            self.cam = self._camera_from_values(vals)
            if self.path is not None and (model_changed or str(path or "") != self.path):
                self.path = None                 # reload below
            elif self.model is not None:
                self._dirty = True
                self._compute_soon()
        path = str(path) if path else None
        if path == self.path:
            return
        self.path = path
        self.model, self.cand = None, []
        self._clear_pieces()
        self.gen += 1
        if path is None:
            self.state = "empty"
            self.update()
            return
        self._load_model()

    def values_changed(self, values: dict) -> None:
        old, self.values = self.values, dict(values)
        if self.path is None:
            self.update()
            return
        if any(old.get(k) != values.get(k) for k in self.MODEL_KEYS):
            self.cam = self._camera_from_values(values)
            self._load_model()
            return
        cam = self._camera_from_values(values)
        # A camera still on its way to the settings is newer than these values, which were already waiting to arrive.
        ahead = self.commit.isActive() or self.last is not None
        moved = not ahead and not _same_camera(cam, self.cam)
        other = any(old.get(k) != values.get(k) for k in values
                    if k not in CAMERA_KEYS and k not in self.MODEL_KEYS and k not in PAINT_ONLY
                    and (self.REDRAW_KEYS is None or k in self.REDRAW_KEYS))
        if moved:
            self.cam = cam
        if (moved or other) and self.model is not None:
            self._dirty = True
            self._compute_soon()
            if self.always_fit:
                self._ensure_candidates()        # fits the camera to the new paper now
                if not _same_camera(self._camera_from_values(values), self.cam):
                    self.commit.start()          # the paper changed under Always fit: the new fit goes back
        self.info_changed.emit(self.info_text())
        self.update()

    def fit(self) -> None:
        """Fit the model's picture in the frame: centred, as large as it goes (the angles stay)."""
        if self.model is None:
            return
        self._fit_cam()
        self._moved()
        self._settle()

    def fit_points(self) -> np.ndarray:
        """What F fits: the model's vertices (a subclass may fit a stage set's whole box instead)."""
        V = self.model.V
        return V[:: int(math.ceil(len(V) / self.FIT_POINTS))] if len(V) > self.FIT_POINTS else V

    def _paper_frame(self, p=None):
        p = p if p is not None else self._p(self.values)
        return frame_for(p.paper, p.orientation, p.margin, (p.get("paper_width", 200.), p.get("paper_height", 200.)))

    def _fit_cam(self) -> None:
        if self.model is not None and len(self.model.V):
            fit_view(self.cam, self.fit_points(), self._paper_frame(), self._roll_deg())

    def _roll_deg(self) -> float:
        """Roll of the picture about the view axis (a host's `roll` setting; 0 for the others)."""
        return float(self.values.get("roll", 0.0) or 0.0)

    def set_always_fit(self, on: bool) -> None:
        on = bool(on)
        if on != self.always_fit:
            self.always_fit = on
            self.btn_fit.blockSignals(True)
            self.btn_fit.setChecked(on)
            self.btn_fit.blockSignals(False)
            if on and self.model is not None:
                self.fit()
            self.update()

    def set_paused(self, on: bool) -> None:
        """Pause lines (P): no hidden lines are worked out after a move, so a heavy model can be aimed over its trace
        without starting that work at every stop; resuming works them out once, for the view as it is. Lines already
        on screen stay until the next move."""
        on = bool(on)
        b = self.btn_pause
        b.blockSignals(True)
        b.setChecked(on)
        b.setText("Resume lines · P" if on else "Pause lines · P")
        b.adjustSize()
        b.blockSignals(False)
        self._place_corner()
        if on == self.paused:
            return
        self.paused = on
        if self.model is not None and self.state in (("computing",) if on else ("paused",)):
            self._compute()                      # paused: stops the work in hand; resumed: draws this view now
        self.update()

    def _framing_by_hand(self) -> None:
        """Shift-drag, the wheel or the arrows frame the view by hand: Always fit lets go."""
        if self.always_fit:
            self.set_always_fit(False)

    # ================================================================== work
    def _load_model(self) -> None:
        self.mgen += 1
        mg = self.mgen
        self.gen += 1
        self.model, self.cand = None, []
        self._clear_pieces()
        self.state = "loading"
        p = self._p(self.values)
        path = self.path

        def work():
            try:
                mdl = self.load_model(path, p)
                self.sig.model_ready.emit(mg, mdl)
            except Exception as e:      # shown on the canvas
                self.sig.model_ready.emit(mg, e)

        threading.Thread(target=work, daemon=True).start()
        self.update()

    def _model_ready(self, mg: int, obj) -> None:
        if mg != self.mgen:
            return
        if isinstance(obj, Exception):
            self.state, self.message = "error", f"{type(obj).__name__}: {obj}"
            self.update()
            return
        self.model = obj
        self._dirty = True
        self._compute()

    def _cams(self) -> list:
        p = self._p(self.values)
        return [self.cam] if not p.stereo else [self.cam, self.cam.stereo(float(p.eye_sep))]

    def _candidates(self) -> None:
        if self.model is None:
            return
        if self.always_fit:
            self._fit_cam()
        p = self._p(self.values)
        self.cand = []
        for c in self._cams():
            v = pv.View(self.model, c, float(p.flat_angle), p.sides, occluders=False, edge_mode=p.get('edge_mode', 'features'))
            self.cand.append(v.candidates())
        self.edges = len(self.cand[0][0]) if self.cand else 0
        self._cand_gen += 1

    def _ensure_candidates(self) -> None:
        """The candidates for the camera as it is now: worked out once per frame, however many moves came in."""
        if self._dirty and self.model is not None:
            self._dirty = False
            self._candidates()

    def _clear_pieces(self) -> None:
        self.pieces = []
        self._result.clear()

    def _compute_soon(self) -> None:
        self.gen += 1                    # whatever is running is stale now
        self._clear_pieces()
        if self.paused:
            self.idle.stop()
            self.state = "paused"
            self.update()
            return
        self.state = "computing"
        self.idle.start()

    def _compute(self) -> None:
        if self.model is None:
            return
        self._ensure_candidates()        # Always fit settles the camera first
        self.gen += 1
        g = self.gen
        self._clear_pieces()
        if self.paused:                  # Pause lines: the trace only, until resumed
            self.state = "paused"
            self.update()
            return
        self.state, self.frac, self.t0 = "computing", 0.0, time.perf_counter()
        p = self._p(self.values)
        cams, model, region = self._cams(), self.model, self._region()
        frame = frame_for(p.paper, p.orientation, p.margin, (p.get("paper_width", 200.), p.get("paper_height", 200.)))
        min_s = max(float(p.min_piece), 0.0) / frame.half

        def check():
            if g != self.gen:
                raise _Cancelled()

        def work():
            try:
                for ei, c in enumerate(cams):
                    view = pv.View(model, c, float(p.flat_angle), p.sides, back_edges=bool(p.show_hidden), edge_mode=p.get('edge_mode', 'features'))

                    def prog(f, ei=ei):
                        self.frac = (ei + f) / len(cams)

                    for P0, P1, depth, outl, hid in view.pieces(region, hidden=bool(p.show_hidden), cancel=check,
                                                                middle_first=True, batch_pairs=LIVE_PAIRS,
                                                                progress=prog):
                        keep = np.hypot(*(P1 - P0).T) >= min_s
                        self.sig.partial.emit(g, (P0[keep], P1[keep], depth[keep], outl[keep], hid, ei))
                self.sig.done.emit(g, None)
            except _Cancelled:
                pass
            except Exception as e:
                self.sig.done.emit(g, e)

        threading.Thread(target=work, daemon=True).start()
        self.update()

    def _partial(self, g: int, payload) -> None:
        if g != self.gen:
            return
        self.pieces.append(payload)      # drawn into the result layer at the next frame, on its own
        self.update()

    def _done(self, g: int, err) -> None:
        if g != self.gen:
            return
        if err is not None:
            self.state, self.message = "error", f"{type(err).__name__}: {err}"
        else:
            self.state, self.seconds = "done", time.perf_counter() - self.t0
            if int(self._p(self.values).depth_layers) > 1:
                self._result.clear()     # depth pens: drawn again with the whole depth range
        self.info_changed.emit(self.info_text())
        self.update()

    # ================================================================== camera
    def _moved(self) -> None:
        """The camera changed under the user's hand: stop any hidden-line work and show the candidates."""
        self.gen += 1
        self.idle.stop()
        self.commit.stop()
        self.state = "moving"
        self._clear_pieces()
        self._dirty = True
        self.update()

    def _settle(self) -> None:
        if self.always_fit:
            self._ensure_candidates()            # the move is over: Always fit frames it now, not at the next frame
        self.idle.start()
        self.commit.start()

    def _commit(self) -> None:
        """Write the camera back into the settings (rounded, and the camera takes the rounded values too)."""
        self._ensure_candidates()        # Always fit settles the camera first
        c = self.cam
        vals = c.as_values()
        c.theta, c.psi, c.radius, c.lookat, c.fov = (vals["theta"], vals["psi"], vals["radius"], vals["lookat"],
                                                    vals["fov"])
        self.values.update(vals)
        self.page.form.set_values(vals)
        self.page._settings_changed()

    def _set_view(self, theta: float, psi: float) -> None:
        self.cam.theta, self.cam.psi = theta, psi
        self._moved()
        self._settle()

    # ================================================================== input
    def mousePressEvent(self, e) -> None:
        self.setFocus()
        if self.model is None:
            return
        self.last = e.position()
        self._moved()

    def mouseMoveEvent(self, e) -> None:
        if self.last is None or self.model is None:
            return
        pos = e.position()
        dx, dy = pos.x() - self.last.x(), pos.y() - self.last.y()
        self.last = pos
        c = self.cam
        pan = bool(e.buttons() & (Qt.RightButton | Qt.MiddleButton)) or bool(e.modifiers() & Qt.ShiftModifier)
        if pan:
            self._framing_by_hand()
            frame, s, _, _ = self._layout()
            per_px = c.radius / (c.k * frame.half * s)       # model units per pixel at the look-at distance
            _, u, v, _ = c.basis()
            c.lookat = tuple(np.asarray(c.lookat) - u * dx * per_px + v * dy * per_px)
        else:
            c.psi = wrap(c.psi + dx * DRAG_DEG)
            c.theta = wrap(c.theta - dy * DRAG_DEG)
        self._dirty = True               # only the camera moves here: the next frame works out what it sees
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self.last is not None:
            self.last = None
            self._settle()

    def mouseDoubleClickEvent(self, e) -> None:
        self.fit()

    def wheelEvent(self, e) -> None:
        if self.model is None:
            return
        notches = e.angleDelta().y() / 120.0
        self._framing_by_hand()
        if e.modifiers() & Qt.ControlModifier:
            self.cam.fov = float(np.clip(self.cam.fov * 0.93 ** notches, 1.0, 150.0))
        else:
            self.cam.radius = float(np.clip(self.cam.radius * 0.9 ** notches, 1.0, 5000.0))
        self._moved()
        self._settle()

    def keyPressEvent(self, e) -> None:
        k = e.key()
        if self.model is None:
            return super().keyPressEvent(e)
        c = self.cam
        if k in VIEWS:
            self._set_view(*VIEWS[k])
        elif k in (Qt.Key_Left, Qt.Key_Right):
            c.psi = wrap(c.psi + (-5.0 if k == Qt.Key_Left else 5.0))
            self._moved()
            self._settle()
        elif k in (Qt.Key_Up, Qt.Key_Down):
            self._framing_by_hand()
            c.radius = float(np.clip(c.radius * (0.9 if k == Qt.Key_Up else 1.1), 1.0, 5000.0))
            self._moved()
            self._settle()
        elif k == Qt.Key_F:
            self.fit()
        elif k == Qt.Key_A:
            self.set_always_fit(not self.always_fit)
        elif k == Qt.Key_P:
            self.set_paused(not self.paused)
        elif k == Qt.Key_R:
            c.theta, c.psi, c.radius, c.lookat, c.fov = (START["theta"], START["psi"], START["radius"],
                                                        START["lookat"], START["fov"])
            self._moved()
            self._settle()
        elif k == Qt.Key_T and self.STEREO:
            self.page.form.set_values({"stereo": not bool(self.values.get("stereo"))})
            self.page._settings_changed()
        else:
            super().keyPressEvent(e)

    def _place_corner(self) -> None:
        """The corner buttons, right to left from the bottom-right corner, under the paper."""
        x = self.width() - 10
        for b in self.corner:
            x -= b.width()
            b.move(x, self.height() - b.height() - 6)
            x -= 6

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place_corner()
        if self.model is not None and self.state in ("done", "computing"):
            self._compute_soon()

    # ================================================================== drawing
    def _layout(self):
        """(frame, px per mm, x offset, y offset): the sheet fitted in the widget."""
        p = self._p(self.values)
        frame = frame_for(p.paper, p.orientation, p.margin, (p.get("paper_width", 200.), p.get("paper_height", 200.)))
        W, H = frame.sheet
        side, top, bottom = 24, 46, 30                 # room for the read-out above and the keys below
        s = max(min((self.width() - 2 * side) / W, (self.height() - top - bottom) / H), 1e-3)
        return frame, s, (self.width() - W * s) / 2.0, top + (self.height() - top - bottom - H * s) / 2.0

    def _region(self):
        """The widget's area in screen units (lines are computed there, faint outside the frame)."""
        frame, s, ox, oy = self._layout()
        x0, y0, w, h = frame.area
        cx, cy = x0 + w / 2.0, y0 + h / 2.0
        left, right = (-ox / s - cx) / frame.half, ((self.width() - ox) / s - cx) / frame.half
        top, bottom = (cy - (-oy / s)) / frame.half, (cy - (self.height() - oy) / s) / frame.half
        if self._roll_deg():                     # turned: anything that could land on screen
            m = max(abs(left), abs(bottom), abs(right), abs(top)) * 1.42
            return (-m, -m, m, m)
        return (left, bottom, right, top)

    def _to_px(self, S: np.ndarray, frame, s, ox, oy) -> np.ndarray:
        return frame.to_mm(roll_screen(S, self._roll_deg())) * s + np.array([ox, oy])

    def _col(self, hexa: str, alpha: float = 1.0) -> QColor:
        c = QColor(hexa)
        c = blueprint_color(c) if self.art == "blueprint" else c
        c.setAlphaF(alpha)
        return c

    def _depth_range(self, p) -> tuple | None:
        """(lo, hi) depth of the pieces the depth pens split, so far (None without depth pens)."""
        if p.stereo or int(p.depth_layers) <= 1 or not self.pieces:
            return None
        inner = [b[2][~b[3].astype(bool)] if p.outline_layer else b[2] for b in self.pieces if not b[4]]
        d = np.concatenate(inner) if inner else np.zeros(0)
        return (float(d.min()), float(d.max())) if len(d) else None

    def _draw_pieces(self, qp: QPainter, batches: list, depth_range, frame, s, ox, oy) -> None:
        """Hidden-line batches with the drawing's pens: depth pens (split over depth_range), the outline pen, the two
        stereo eyes, hidden lines dashed."""
        if not batches:
            return
        p = self._p(self.values)
        P0 = np.concatenate([b[0] for b in batches])
        P1 = np.concatenate([b[1] for b in batches])
        depth = np.concatenate([b[2] for b in batches])
        outl = np.concatenate([b[3] for b in batches]).astype(bool)
        hid = np.concatenate([np.full(len(b[0]), b[4]) for b in batches]).astype(bool)
        eye = np.concatenate([np.full(len(b[0]), b[5]) for b in batches])
        Q0, Q1 = self._to_px(P0, frame, s, ox, oy), self._to_px(P1, frame, s, ox, oy)
        lw = max(float(p.line_width) * s, 0.8)
        ow = max(float(p.outline_width) * s, 1.0) if p.outline_layer else lw
        col = self._col
        groups = []
        if p.stereo:
            for ei in (0, 1):
                groups.append((~hid & (eye == ei), col(STEREO_COLORS[ei], 0.85), lw, False))
        else:
            k = int(p.depth_layers)
            inner = ~hid & ~(outl if p.outline_layer else np.zeros_like(hid))
            if k > 1 and depth_range is not None:
                lo, hi = depth_range
                band = np.clip(((depth - lo) / max(hi - lo, 1e-9) * k).astype(int), 0, k - 1)
                colours = DEPTH_COLORS if k == 3 else [DEPTH_COLORS[0], DEPTH_COLORS[2]]
                for i in range(k):
                    groups.append((inner & (band == i), col(colours[i]), lw, bool(p.far_dashed and i == k - 1)))
            else:
                groups.append((inner, col("#0B1220"), lw, False))
            if p.outline_layer:
                groups.append((~hid & outl, col("#0B1220"), ow, False))
        if p.show_hidden:
            groups.append((hid, col("#C9821B", 0.6), lw, True))
        for sel, c, w, dash in groups:
            if sel.any():
                qp.setPen(_pen(c, w, dash))
                qp.drawPath(_segments_path(Q0[sel], Q1[sel]))

    def _draw_candidates(self, qp: QPainter, alpha: float, frame, s, ox, oy, budget: int | None = None) -> int:
        """The candidate edges in the accent colour (each eye in its own in stereo). With a budget, a frame that has
        more draws the outline and an even share of the rest. Returns how many it drew (of the first eye)."""
        accent = qcolor(self.theme["accent"])
        shown = 0
        for ei, (A, B, O) in enumerate(self.cand):
            if budget is not None and len(A) > budget:
                keep = _thin(np.asarray(O, dtype=bool), budget)
                A, B = A[keep], B[keep]
            c = QColor(STEREO_COLORS[ei]) if len(self.cand) > 1 else QColor(accent)
            c.setAlphaF(alpha)
            qp.setPen(_pen(c, 1.0))
            qp.drawPath(_segments_path(self._to_px(A, frame, s, ox, oy), self._to_px(B, frame, s, ox, oy)))
            if ei == 0:
                shown = len(A)
        return shown

    def _show_result(self, qp: QPainter, inside, outside, look, frame, s, ox, oy, dpr: float) -> None:
        """The visible lines: only the batches that came in since the last frame are drawn into the layer."""
        layer = self._result
        if layer.key != look:                    # another size, screen, paper or look: every batch again
            layer.clear()
        if self.pieces and (layer.key is None or layer.count < len(self.pieces)):
            if layer.key is None:
                lp, first = layer.begin(self.width(), self.height(), dpr, look), 0
            else:
                lp, first = layer.painter(), layer.count
            self._draw_pieces(lp, self.pieces[first:], self._depth_range(self._p(self.values)), frame, s, ox, oy)
            lp.end()
            layer.count = len(self.pieces)
        layer.show(qp, inside, outside)

    def _pace(self, seconds: float) -> None:
        """After a moving frame: fewer candidate edges for the next one when this one took over FRAME_S, more again
        when it took well under it."""
        if seconds > FRAME_S:
            self._budget = max(DRAG_MIN, int(min(self._budget, max(self.shown, DRAG_MIN)) * FRAME_S / seconds * 0.9))
        elif seconds < FRAME_S / 2 and self._budget < self.edges:
            self._budget = int(self._budget * 1.25) + 1

    def paintEvent(self, e) -> None:
        started = time.perf_counter()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        th = self.theme
        blue = self.art == "blueprint"
        qp.fillRect(self.rect(), qcolor(th["bg-1"]))
        frame, s, ox, oy = self._layout()
        W, H = frame.sheet
        sheet = QRectF(ox, oy, W * s, H * s)
        x0, y0, w, h = frame.area
        fr = QRectF(ox + x0 * s, oy + y0 * s, w * s, h * s)
        qp.fillRect(sheet, BLUEPRINT_BG if blue else qcolor(th["paper"]))
        if blue:
            qp.setPen(QPen(BLUEPRINT_GRID, 1))
            for xx in np.arange(sheet.left(), sheet.right(), 22.0):
                qp.drawLine(QPointF(xx, sheet.top()), QPointF(xx, sheet.bottom()))
            for yy in np.arange(sheet.top(), sheet.bottom(), 22.0):
                qp.drawLine(QPointF(sheet.left(), yy), QPointF(sheet.right(), yy))
        # the frame: hairline, with registration marks at its corners
        mark = qcolor(th["highlight"])
        mark.setAlphaF(0.6)
        dash = QPen(mark, 1, Qt.DashLine)
        dash.setDashPattern([2, 4])
        qp.setPen(dash)
        qp.setBrush(Qt.NoBrush)
        qp.drawRect(fr)
        qp.setPen(QPen(mark, 1))
        arm = 12
        for x, y, dx, dy in ((fr.left(), fr.top(), 1, 1), (fr.right(), fr.top(), -1, 1),
                             (fr.left(), fr.bottom(), 1, -1), (fr.right(), fr.bottom(), -1, -1)):
            qp.drawLine(QPointF(x - 6 * dx, y), QPointF(x + arm * dx, y))
            qp.drawLine(QPointF(x, y - 6 * dy), QPointF(x, y + arm * dy))

        if self.state in ("empty", "loading", "error") or self.model is None:
            self._hud(qp)
            return
        inside = QRegion(fr.toAlignedRect())
        outside = QRegion(self.rect()).subtracted(inside)
        dpr = _device_ratio(qp, self)
        look = (self.width(), self.height(), dpr, frame.sheet, frame.area, self._roll_deg(), self._look)
        if self.state == "moving":               # the candidates, as many as keep up with the hand
            self._ensure_candidates()
            lp = self._live.begin(self.width(), self.height(), dpr, look)
            self.shown = self._draw_candidates(lp, 0.85, frame, s, ox, oy, self._budget)
            lp.end()
            self._live.show(qp, inside, outside)
        else:
            if self.state != "done":             # faint while the lines are worked out, full while paused: once a rest
                self._ensure_candidates()
                alpha = 0.85 if self.state == "paused" else 0.28
                key = (look, self._cand_gen, alpha)
                if self._waiting.key != key:
                    lp = self._waiting.begin(self.width(), self.height(), dpr, key)
                    self._draw_candidates(lp, alpha, frame, s, ox, oy)
                    lp.end()
                self._waiting.show(qp, inside, outside)
            self._show_result(qp, inside, outside, look, frame, s, ox, oy, dpr)
        if self.state in ("moving", "computing", "paused"):
            self._axes(qp, frame, s, ox, oy)
        self._hud(qp)
        if self.state == "moving":
            self._pace(time.perf_counter() - started)

    def _axes(self, qp, frame, s, ox, oy) -> None:
        """plotter.vision's XYZ marker at the look-at point."""
        look = np.asarray(self.cam.lookat, dtype=float)
        pts = np.stack([look, look + [8, 0, 0], look + [0, 8, 0], look + [0, 0, 8]])
        C = self.cam.to_camera(pts)
        if (C[:, 2] < NEAR).any():
            return
        P = self._to_px(self.cam.project(C), frame, s, ox, oy)
        for i, hexa in ((1, "#FF4D5A"), (2, "#3DDC84"), (3, "#3D7BFF")):
            qp.setPen(QPen(QColor(hexa), 2.2, Qt.SolidLine, Qt.RoundCap))
            qp.drawLine(QPointF(*P[0]), QPointF(*P[i]))

    def _camera_text(self) -> str:
        c = self.cam
        look = ", ".join(f"{x:.0f}" for x in c.lookat)
        return (f"theta {c.theta:.1f}°   psi {c.psi:.1f}°   r {c.radius:.0f}   look ({look})   "     # as plotter.vision
                f"{c.fov:.0f}° {'ortho' if c.ortho else 'persp'}")

    def _mode_text(self) -> str:
        """What the lines on screen are (an Aim tab's light trace, or the exact drawing), and Always fit."""
        return "always fit" if self.always_fit else ""

    def _hud(self, qp) -> None:
        th = self.theme
        qp.setFont(mono(11))
        if self.state == "empty":
            qp.setPen(qcolor(th["text-3"]))
            qp.drawText(self.rect(), Qt.AlignCenter, "Add a model, then aim the drawing's view here.")
            return
        lines = [self._camera_text()]
        if self.state == "loading":
            lines.append("loading the model…")
        elif self.state == "error":
            lines.append(self.message)
        elif self.state == "moving":
            thinned = f" · {self.shown:,} shown while moving" if 0 < self.shown < self.edges else ""
            lines.append(f"{self.edges:,} edges{thinned}".replace(",", " "))
        elif self.state == "computing":
            lines.append(f"hidden lines {self.frac * 100:.0f}%")
        elif self.state == "paused":
            lines.append(f"lines paused · {self.edges:,} edges · P draws them".replace(",", " "))
        elif self.model is not None:
            n = sum(len(b[0]) for b in self.pieces if not b[4])
            lines.append(f"{self.model.faces:,} faces · {self.edges:,} edges · {n:,} pieces · "
                         f"{self.seconds:.2f} s".replace(",", " "))
        mode = self._mode_text()
        if mode and len(lines) > 1:
            lines[1] = f"{mode} · {lines[1]}"
        qp.setPen(qcolor(th["text-2"]))
        for i, t in enumerate(lines):
            qp.drawText(QPointF(12, 20 + 16 * i), t)
        qp.setPen(qcolor(th["text-3"]))
        keys = self.KEYS
        room = self.width() - 24 - self._corner_width()
        qp.drawText(QPointF(12, self.height() - 10),
                    qp.fontMetrics().elidedText(keys, Qt.ElideRight, max(room, 40)))

    def _corner_width(self) -> int:
        """Width the corner buttons take, so the keys line stops short of them."""
        return sum(b.width() + 6 for b in self.corner)
