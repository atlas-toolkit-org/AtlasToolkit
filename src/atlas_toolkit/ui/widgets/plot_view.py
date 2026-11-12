"""
2D preview of a plotter drawing: the sheet as a paper tile with registration-mark corners, one path per pen at
its real width in mm, optional overlays (cut ink) and pen-up travel. Paper or blueprint art mode, like Atlas.
Wheel zooms around the cursor, drag pans, double-click or 0 fits.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

from ...geom.drawing import Drawing, Pen
from ..theme import qcolor

try:   # pyqtgraph builds a QPainterPath from numpy arrays in one call (binary stream), ~50x faster than a loop
    from pyqtgraph.functions import arrayToQPath
except Exception:   # pragma: no cover - pyqtgraph is a dependency; keep a slow fallback anyway
    arrayToQPath = None

BLUEPRINT_BG = QColor("#0A1430")
BLUEPRINT_GRID = QColor(120, 170, 255, 22)


def pen_path(pen: Pen) -> QPainterPath:
    if len(pen.xy) < 2:
        return QPainterPath()
    x = pen.xy[:, 0].astype(np.float64)
    y = pen.xy[:, 1].astype(np.float64)
    if arrayToQPath is not None:
        return arrayToQPath(x, y, connect=pen.connect.astype(np.int32))
    path = QPainterPath()
    start = True
    for (px, py), c in zip(pen.xy, pen.connect):
        if start:
            path.moveTo(px, py)
        else:
            path.lineTo(px, py)
        start = c == 0
    return path


def travel_path(pen: Pen) -> QPainterPath:
    path = QPainterPath()
    ends = np.flatnonzero(pen.connect[:-1] == 0)
    for i in ends:
        path.moveTo(*pen.xy[i])
        path.lineTo(*pen.xy[i + 1])
    return path


def blueprint_color(c: QColor) -> QColor:
    """Ink on navy: dark inks turn pale blue-white, coloured inks are lifted."""
    if c.lightnessF() < 0.35 and c.saturationF() < 0.4:
        return QColor("#DCE8FF")
    h, s, light, a = c.getHslF()
    return QColor.fromHslF(max(h, 0.0), min(1.0, s), max(light, 0.62), a)


class PlotView(QGraphicsView):
    zoomed = Signal(float)

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.art = "paper"
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.drawing: Drawing | None = None
        self.overlays: list = []
        self.show_travel = False
        self.sheet = QRectF(0, 0, 210, 297)
        self._fitted = True
        self.message = "Nothing to preview yet."

    # ------------------------------------------------------------------ content
    def set_theme(self, theme) -> None:
        self.theme = theme
        self.rebuild()

    def set_art(self, art: str) -> None:
        self.art = art
        self.rebuild()

    def set_travel(self, on: bool) -> None:
        self.show_travel = on
        self.rebuild()

    def show_drawing(self, drawing: Drawing | None, overlays: list | None = None, keep_view: bool = False,
                     message: str = "", raster=None) -> None:
        """`raster`: (uint8 H×W or H×W×3 array, (x, y, w, h) in mm) drawn on the paper under the strokes."""
        same_sheet = (self.drawing is not None and drawing is not None
                      and tuple(self.drawing.view) == tuple(drawing.view))
        self.drawing = drawing
        self.overlays = overlays or []
        self.raster = raster
        self.message = message or "Nothing to preview yet."
        self.rebuild()
        if not (keep_view and same_sheet and not self._fitted):
            self.fit()

    def rebuild(self) -> None:
        sc = self.scene_
        sc.clear()
        d = self.drawing
        if d is None:
            self.viewport().update()
            return
        x, y, w, h = d.view
        self.sheet = QRectF(x, y, w, h)
        pad = max(w, h) * 0.08
        sc.setSceneRect(self.sheet.adjusted(-pad, -pad, pad, pad))
        blue = self.art == "blueprint"
        if getattr(self, "raster", None) is not None:
            arr, (rx, ry, rw, rh) = self.raster
            arr = np.ascontiguousarray(arr)
            fmt = QImage.Format_Grayscale8 if arr.ndim == 2 else QImage.Format_RGB888
            img = QImage(arr.data, arr.shape[1], arr.shape[0], arr.strides[0], fmt).copy()
            pix = QGraphicsPixmapItem(QPixmap.fromImage(img))
            pix.setTransformationMode(Qt.SmoothTransformation)
            pix.setScale(rw / max(arr.shape[1], 1))
            pix.setPos(rx, ry)
            sc.addItem(pix)
        for pen in d.pens:
            item = QGraphicsPathItem(pen_path(pen))
            col = qcolor(pen.color) if pen.color else QColor("#0B1220")
            if blue:
                col = blueprint_color(col)
            qp = QPen(col, max(pen.width, 0.05), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            item.setPen(qp)
            item.setBrush(Qt.NoBrush)
            sc.addItem(item)
            if self.show_travel:
                t = QGraphicsPathItem(travel_path(pen))
                tp = QPen(qcolor(self.theme["highlight"] if blue else self.theme["accent"]), 0)
                tp.setStyle(Qt.DashLine)
                tp.setCosmetic(True)
                t.setPen(tp)
                t.setOpacity(0.7)
                sc.addItem(t)
        for ov in self.overlays:
            item = QGraphicsPathItem(pen_path(ov))
            qp = QPen(qcolor(ov.color), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            qp.setCosmetic(True)   # cut ink stays visible at any zoom
            item.setPen(qp)
            item.setOpacity(0.9)
            sc.addItem(item)
        self.viewport().update()

    # ------------------------------------------------------------------ painting
    def drawBackground(self, p: QPainter, rect: QRectF) -> None:
        th = self.theme
        blue = self.art == "blueprint"
        p.fillRect(rect, qcolor(th["bg-1"]))
        if self.drawing is None:
            return
        paper = BLUEPRINT_BG if blue else qcolor(th["paper"])
        p.fillRect(self.sheet, paper)
        if blue:   # 22 px grid in screen space
            scale = self.transform().m11() or 1.0
            step = 22 / scale
            p.setPen(QPen(BLUEPRINT_GRID, 0))
            xx = self.sheet.left()
            while xx < self.sheet.right():
                p.drawLine(QPointF(xx, self.sheet.top()), QPointF(xx, self.sheet.bottom()))
                xx += step
            yy = self.sheet.top()
            while yy < self.sheet.bottom():
                p.drawLine(QPointF(self.sheet.left(), yy), QPointF(self.sheet.right(), yy))
                yy += step
        elif not th.dark:
            p.setPen(QPen(qcolor(th["line-2"]), 0))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self.sheet)

    def drawForeground(self, p: QPainter, rect: QRectF) -> None:
        scale = self.transform().m11() or 1.0
        if self.drawing is None:
            p.save()
            p.resetTransform()
            p.setPen(qcolor(self.theme["text-3"]))
            p.drawText(self.viewport().rect(), Qt.AlignCenter, self.message)
            p.restore()
            return
        p.save()
        col = qcolor(self.theme["highlight"])
        col.setAlphaF(0.55)
        pen = QPen(col, 1.0)
        pen.setCosmetic(True)
        p.setPen(pen)
        arm, inset = 12 / scale, -9 / scale
        r = self.sheet.adjusted(inset, inset, -inset, -inset)
        for x, y, dx, dy in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                             (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + arm * dx, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + arm * dy))
        p.restore()

    # ------------------------------------------------------------------ navigation
    def fit(self) -> None:
        if self.drawing is None:
            return
        m = 28
        vr = self.viewport().rect().adjusted(m, m, -m, -m)
        if vr.width() <= 0 or vr.height() <= 0:
            return
        s = min(vr.width() / max(self.sheet.width(), 1e-6), vr.height() / max(self.sheet.height(), 1e-6))
        self.resetTransform()
        self.scale(s, s)
        self.centerOn(self.sheet.center())
        self._fitted = True
        self.zoomed.emit(s)

    def zoom_by(self, f: float) -> None:
        cur = self.transform().m11()
        f = max(0.05 / cur, min(f, 4000 / cur))
        self.scale(f, f)
        self._fitted = False
        self.zoomed.emit(self.transform().m11())

    def wheelEvent(self, e) -> None:
        if self.drawing is None:
            return
        self.zoom_by(1.0015 ** e.angleDelta().y())

    def mouseDoubleClickEvent(self, e) -> None:
        self.fit()

    def keyPressEvent(self, e) -> None:
        k = e.key()
        if k == Qt.Key_0:
            self.fit()
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_by(1.25)
        elif k in (Qt.Key_Minus, Qt.Key_Underscore):
            self.zoom_by(0.8)
        else:
            super().keyPressEvent(e)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self._fitted:
            self.fit()
