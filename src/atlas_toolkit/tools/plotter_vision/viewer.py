"""
The Vision tab: plotter.vision's way of aiming the camera, inside the toolkit (the tool spec's `viewer`).

The paper is drawn with its drawing area framed: the camera looks through that frame, so what is inside it is what
Draw writes (lines outside stay faint). Moving works as on plotter.vision:

    drag                orbit (θ, ψ), at plotter.vision's speed
    Shift-drag / right  move the look-at point (in the picture's plane; plotter.vision moved it along x and z)
    wheel               distance (Ctrl+wheel: field of view)
    1 2 3 4 5           front, side, back, other side, top
    arrows              turn and come closer, like plotter.vision's
    F / double-click    fit the model in the frame          R  plotter.vision's start view
    T                   red/blue stereo on/off

While the camera moves every candidate edge is drawn (in the accent colour, with an XYZ marker at the look-at
point); once it rests, hidden lines are removed in a background thread and the visible lines fill in from the middle
out, as plotter.vision did. The camera goes back into the settings when you let go, so Draw uses exactly this view.
"""
from __future__ import annotations

import math
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QWidget

from atlas_toolkit.core.params import Params
from atlas_toolkit.ui.theme import mono, qcolor
from atlas_toolkit.ui.widgets.plot_view import BLUEPRINT_BG, BLUEPRINT_GRID, blueprint_color

from . import model as pm
from . import plotter_vision as pv
from .camera import NEAR, Camera, frame_for, wrap

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
DRAG_LINES = 60_000                                # while moving, at most this many candidate edges are drawn
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


def _same_camera(a: Camera, b: Camera) -> bool:
    return (abs(a.theta - b.theta) < 1e-6 and abs(a.psi - b.psi) < 1e-6 and abs(a.radius - b.radius) < 1e-6
            and np.allclose(a.lookat, b.lookat, atol=1e-6) and abs(a.fov - b.fov) < 1e-6 and a.ortho == b.ortho)


class VisionView(QWidget):
    """Plotter Vision's live view. Other tools that draw through the same camera (camera_params) host a subclass
    to aim (tools/plotter_vision/aim.py); these class attributes are what such a subclass changes."""
    title = "Vision"
    info_changed = Signal(str)
    DEFAULTS: dict = {}                  # values for Plotter Vision keys the host tool does not have
    MODEL_KEYS = MODEL_KEYS              # settings that reload the model
    STEREO = True                        # T toggles the red/blue pair
    REDRAW_KEYS = None                   # settings (besides the camera) that recompute the lines; None: any other
    KEYS = "drag orbit · shift-drag move · wheel distance · 1-5 views · F fit · R reset · T stereo"

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
        self.cam = pv.camera_from(self._p(self.values))
        self.cand: list = []            # per eye: (A, B, outline) on screen, nothing hidden
        self.pieces: list = []          # (P0, P1, depth, outline, hidden, eye) batches
        self.state = "empty"            # empty | loading | moving | computing | done | error
        self.message = ""
        self.gen = 0                    # bumped on every change: stale work is dropped
        self.mgen = 0
        self.frac = 0.0
        self.t0 = 0.0
        self.seconds = 0.0
        self.edges = 0
        self.last: QPointF | None = None
        self._cache = None
        self.idle = QTimer(self, singleShot=True, interval=160, timeout=self._compute)
        self.commit = QTimer(self, singleShot=True, interval=350, timeout=self._commit)

    def _p(self, values: dict) -> Params:
        return _params({**self.DEFAULTS, **values})

    def load_model(self, path: str, p):
        """The model for the view (runs in a thread)."""
        return pm.load(path, int(p.target_faces) if p.decimate else 0, bool(p.fix_winding), None, bool(p.crossings))

    def fit_radius(self, p) -> float:
        return pv.fit_radius(self.model, self.cam, frame_for(p.paper, p.orientation, p.margin, (p.get("paper_width", 200.), p.get("paper_height", 200.))))

    # ================================================================== host interface
    def info_text(self) -> str:
        p = self._p(self.values)
        if self.model is None:
            return f"{p.paper} {p.orientation}"
        return f"{p.paper} {p.orientation}   {self.edges:,} edges".replace(",", " ")

    def set_theme(self, theme) -> None:
        self.theme = theme
        self.update()

    def set_art(self, art: str) -> None:
        self.art = art
        self.update()

    def set_input(self, path, info) -> None:
        vals = dict(self.page.values())          # the page restores its settings after building us
        if vals != self.values:
            model_changed = any(self.values.get(k) != vals.get(k) for k in self.MODEL_KEYS)
            self.values = vals
            self.cam = pv.camera_from(self._p(vals))
            if self.path is not None and (model_changed or str(path or "") != self.path):
                self.path = None                 # reload below
            elif self.model is not None:
                self._candidates()
                self._compute_soon()
        path = str(path) if path else None
        if path == self.path:
            return
        self.path = path
        self.model, self.cand, self.pieces = None, [], []
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
            self.cam = pv.camera_from(self._p(values))
            self._load_model()
            return
        cam = pv.camera_from(self._p(values))
        # A camera still on its way to the settings is newer than these values, which were already waiting to arrive.
        ahead = self.commit.isActive() or self.last is not None
        moved = not ahead and not _same_camera(cam, self.cam)
        other = any(old.get(k) != values.get(k) for k in values
                    if k not in CAMERA_KEYS and k not in self.MODEL_KEYS and k not in PAINT_ONLY
                    and (self.REDRAW_KEYS is None or k in self.REDRAW_KEYS))
        if moved:
            self.cam = cam
        if (moved or other) and self.model is not None:
            self._candidates()
            self._compute_soon()
        self.info_changed.emit(self.info_text())
        self.update()

    def fit(self) -> None:
        """Look at the model's centre from a distance that fits it in the frame."""
        if self.model is None:
            return
        p = self._p(self.values)
        self.cam.lookat = (0.0, 0.0, 0.0)
        self.cam.radius = round(self.fit_radius(p), 1)
        self._moved()
        self._settle()

    # ================================================================== work
    def _load_model(self) -> None:
        self.mgen += 1
        mg = self.mgen
        self.gen += 1
        self.model, self.cand, self.pieces = None, [], []
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
        self._candidates()
        self._compute()

    def _cams(self) -> list:
        p = self._p(self.values)
        return [self.cam] if not p.stereo else [self.cam, self.cam.stereo(float(p.eye_sep))]

    def _candidates(self) -> None:
        if self.model is None:
            return
        p = self._p(self.values)
        self.cand = []
        for c in self._cams():
            v = pv.View(self.model, c, float(p.flat_angle), p.sides, occluders=False, edge_mode=p.get('edge_mode', 'features'))
            self.cand.append(v.candidates())
        self.edges = len(self.cand[0][0]) if self.cand else 0
        self._cache = None

    def _compute_soon(self) -> None:
        self.gen += 1                    # whatever is running is stale now
        self.state = "computing"
        self.pieces = []
        self._cache = None
        self.idle.start()

    def _compute(self) -> None:
        if self.model is None:
            return
        self.gen += 1
        g = self.gen
        self.pieces, self._cache = [], None
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
        self.pieces.append(payload)
        self._cache = None
        self.update()

    def _done(self, g: int, err) -> None:
        if g != self.gen:
            return
        if err is not None:
            self.state, self.message = "error", f"{type(err).__name__}: {err}"
        else:
            self.state, self.seconds = "done", time.perf_counter() - self.t0
        self.info_changed.emit(self.info_text())
        self.update()

    # ================================================================== camera
    def _moved(self) -> None:
        """The camera changed under the user's hand: stop any hidden-line work and show the candidates."""
        self.gen += 1
        self.idle.stop()
        self.commit.stop()
        self.state = "moving"
        self.pieces, self._cache = [], None
        self._candidates()
        self.update()

    def _settle(self) -> None:
        self.idle.start()
        self.commit.start()

    def _commit(self) -> None:
        """Write the camera back into the settings (rounded, and the camera takes the rounded values too)."""
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
            frame, s, _, _ = self._layout()
            per_px = c.radius / (c.k * frame.half * s)       # model units per pixel at the look-at distance
            _, u, v, _ = c.basis()
            c.lookat = tuple(np.asarray(c.lookat) - u * dx * per_px + v * dy * per_px)
        else:
            c.psi = wrap(c.psi + dx * DRAG_DEG)
            c.theta = wrap(c.theta - dy * DRAG_DEG)
        self._candidates()
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
            c.radius = float(np.clip(c.radius * (0.9 if k == Qt.Key_Up else 1.1), 1.0, 5000.0))
            self._moved()
            self._settle()
        elif k == Qt.Key_F:
            self.fit()
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

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._cache = None
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
        return (left, bottom, right, top)

    def _to_px(self, S: np.ndarray, frame, s, ox, oy) -> np.ndarray:
        return frame.to_mm(S) * s + np.array([ox, oy])

    def _paths(self, frame, s, ox, oy):
        """[(path, colour, width px, dashed)], cached until the pieces or the layout change."""
        key = (len(self.pieces), self.width(), self.height(), self.art, frame.sheet, frame.area)
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1]
        p = self._p(self.values)
        blue = self.art == "blueprint"

        def col(hexa: str, alpha: float = 1.0) -> QColor:
            c = QColor(hexa)
            c = blueprint_color(c) if blue else c
            c.setAlphaF(alpha)
            return c

        out = []
        if self.pieces:
            P0 = np.concatenate([b[0] for b in self.pieces])
            P1 = np.concatenate([b[1] for b in self.pieces])
            depth = np.concatenate([b[2] for b in self.pieces])
            outl = np.concatenate([b[3] for b in self.pieces]).astype(bool)
            hid = np.concatenate([np.full(len(b[0]), b[4]) for b in self.pieces]).astype(bool)
            eye = np.concatenate([np.full(len(b[0]), b[5]) for b in self.pieces])
            Q0, Q1 = self._to_px(P0, frame, s, ox, oy), self._to_px(P1, frame, s, ox, oy)
            lw = max(float(p.line_width) * s, 0.8)
            ow = max(float(p.outline_width) * s, 1.0) if p.outline_layer else lw
            groups = []
            if p.stereo:
                for ei in (0, 1):
                    groups.append((~hid & (eye == ei), col(STEREO_COLORS[ei], 0.85), lw, False))
            else:
                k = int(p.depth_layers)
                inner = ~hid & ~(outl if p.outline_layer else np.zeros_like(hid))
                if k > 1 and inner.any():
                    lo, hi = float(depth[inner].min()), float(depth[inner].max())
                    band = np.minimum(((depth - lo) / max(hi - lo, 1e-9) * k).astype(int), k - 1)
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
                    out.append((_segments_path(Q0[sel], Q1[sel]), c, w, dash))
        self._cache = (key, out)
        return out

    def paintEvent(self, e) -> None:
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
        # candidates: while moving, and faintly while the hidden lines are worked out
        if self.cand and self.state != "done":
            c = qcolor(th["accent"])
            c.setAlphaF(0.85 if self.state == "moving" else 0.28)
            for ei, (A, B, _o) in enumerate(self.cand):
                if len(A) > DRAG_LINES:
                    step = int(math.ceil(len(A) / DRAG_LINES))
                    A, B = A[::step], B[::step]
                path = _segments_path(self._to_px(A, frame, s, ox, oy), self._to_px(B, frame, s, ox, oy))
                cc = QColor(STEREO_COLORS[ei]) if len(self.cand) > 1 else c
                cc.setAlphaF(c.alphaF())
                self._stroke(qp, path, cc, 1.0, False, inside, outside)
        for path, c, w, dsh in self._paths(frame, s, ox, oy):
            self._stroke(qp, path, c, w, dsh, inside, outside)
        if self.state in ("moving", "computing"):
            self._axes(qp, frame, s, ox, oy)
        self._hud(qp)

    def _stroke(self, qp, path, c: QColor, w: float, dashed: bool, inside: QRegion, outside: QRegion) -> None:
        pen = QPen(c, w, Qt.DashLine if dashed else Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        qp.setBrush(Qt.NoBrush)
        qp.setClipRegion(inside)
        qp.setPen(pen)
        qp.drawPath(path)
        faint = QColor(c)
        faint.setAlphaF(c.alphaF() * 0.22)
        pen.setColor(faint)
        qp.setClipRegion(outside)
        qp.setPen(pen)
        qp.drawPath(path)
        qp.setClipping(False)

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

    def _hud(self, qp) -> None:
        th = self.theme
        qp.setFont(mono(11))
        if self.state == "empty":
            qp.setPen(qcolor(th["text-3"]))
            qp.drawText(self.rect(), Qt.AlignCenter, "Add a model, then aim the camera here.")
            return
        c = self.cam
        look = ", ".join(f"{x:.0f}" for x in c.lookat)
        lines = [f"theta {c.theta:.1f}°   psi {c.psi:.1f}°   r {c.radius:.0f}   look ({look})   "     # as plotter.vision
                 f"{c.fov:.0f}° {'ortho' if c.ortho else 'persp'}"]
        if self.state == "loading":
            lines.append("loading the model…")
        elif self.state == "error":
            lines.append(self.message)
        elif self.state == "moving":
            lines.append(f"{self.edges:,} edges".replace(",", " "))
        elif self.state == "computing":
            lines.append(f"hidden lines {self.frac * 100:.0f}%")
        elif self.model is not None:
            n = sum(len(b[0]) for b in self.pieces if not b[4])
            lines.append(f"{self.model.faces:,} faces · {self.edges:,} edges · {n:,} pieces · "
                         f"{self.seconds:.2f} s".replace(",", " "))
        qp.setPen(qcolor(th["text-2"]))
        for i, t in enumerate(lines):
            qp.drawText(QPointF(12, 20 + 16 * i), t)
        qp.setPen(qcolor(th["text-3"]))
        keys = self.KEYS
        qp.drawText(QPointF(12, self.height() - 10),
                    qp.fontMetrics().elidedText(keys, Qt.ElideRight, max(self.width() - 24, 40)))
