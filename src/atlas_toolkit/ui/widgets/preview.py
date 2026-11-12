"""
The preview stage of a tool page: a row of view tabs (Before / After / Overlay, Input / Output, Model / Drawing),
a 2D plot view or a 3D mesh view, and a small toolbar. What it shows comes from the data, never from the tool:

    views = [{"title": "Before", "kind": "svg", "drawing": Drawing, "overlays": [Pen, ...]},
             {"title": "Output", "kind": "mesh", "mesh": MeshData}, ...]

Inputs bring their own views (from inspect), results add theirs. A tool may also bring an interactive widget (its
spec's `viewer`), shown as {"title": ..., "kind": "widget", "widget": QWidget}: the stage hosts it, passes it the
theme, the art mode and Fit, and shows its info_text() in the toolbar.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QStackedWidget, QTabBar, QVBoxLayout, QWidget

from ..theme import mono
from .common import apply_icon, icon_button
from .plot_view import PlotView


class PreviewStage(QFrame):
    view_changed = Signal(str)

    def __init__(self, theme):
        super().__init__()
        self.setObjectName("stage")
        self.theme = theme
        self.views: list = []
        self.mesh_view = None
        self.hosted: list = []              # tool widgets (kind "widget") added to the stack
        self.art = "paper"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(10, 2, 8, 0)
        bar.setSpacing(4)
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.currentChanged.connect(self._tab_changed)
        bar.addWidget(self.tabs)
        bar.addStretch(1)
        self.info = QLabel("")
        self.info.setFont(mono(11))
        self.info.setProperty("class", "mono")
        bar.addWidget(self.info)
        self.btn_travel = icon_button("toolpath", "Show pen-up travel", theme, checkable=True, size=16)
        self.btn_travel.toggled.connect(lambda on: self.plot.set_travel(on))
        self.btn_wire = icon_button("grid", "Show the mesh edges", theme, checkable=True, size=16)
        self.btn_wire.toggled.connect(lambda on: self.mesh_view.set_wire(on) if self.mesh_view else None)
        self.btn_wire.setVisible(False)
        self.btn_fit = icon_button("expand", "Fit (0 or double-click)", theme, size=16)
        self.btn_fit.clicked.connect(self.fit)
        bar.addWidget(self.btn_travel)
        bar.addWidget(self.btn_wire)
        bar.addWidget(self.btn_fit)
        lay.addLayout(bar)
        self.stack = QStackedWidget()
        self.plot = PlotView(theme)
        self.plot.zoomed.connect(self._zoom_text)
        self.stack.addWidget(self.plot)
        lay.addWidget(self.stack, 1)
        self.setMinimumHeight(220)

    # ------------------------------------------------------------------ views
    def _ensure_mesh_view(self):
        if self.mesh_view is None:
            from .mesh_view import MeshView
            self.mesh_view = MeshView(self.theme)
            self.stack.addWidget(self.mesh_view)
        return self.mesh_view

    def show_views(self, views: list, prefer: str | None = None) -> None:
        current = self.tabs.tabText(self.tabs.currentIndex()) if self.tabs.count() else None
        self.views = list(views or [])
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for v in self.views:
            self.tabs.addTab(v["title"])
        titles = [v["title"] for v in self.views]
        pick = prefer if prefer in titles else (current if current in titles else (titles[-1] if titles else None))
        self.tabs.setCurrentIndex(titles.index(pick) if pick else -1)
        self.tabs.blockSignals(False)
        self._show_current(keep_view=pick == current)

    def clear(self, message: str = "Nothing to preview yet.") -> None:
        self.views = []
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        self.tabs.blockSignals(False)
        self.stack.setCurrentWidget(self.plot)
        self.plot.show_drawing(None, message=message)
        self.info.setText("")

    def _tab_changed(self, _i: int) -> None:
        self._show_current(keep_view=True)
        self.view_changed.emit(self.tabs.tabText(self.tabs.currentIndex()))

    def _show_current(self, keep_view: bool = False) -> None:
        i = self.tabs.currentIndex()
        if i < 0 or i >= len(self.views):
            self.clear()
            return
        v = self.views[i]
        if v["kind"] == "widget":
            w = v["widget"]
            if w not in self.hosted:
                self.hosted.append(w)
                self.stack.addWidget(w)
                if hasattr(w, "set_theme"):
                    w.set_theme(self.theme)
                if hasattr(w, "set_art"):
                    w.set_art(self.art)
                if hasattr(w, "info_changed"):
                    w.info_changed.connect(self._widget_info)
            self.stack.setCurrentWidget(w)
            self.btn_travel.setVisible(False)
            self.btn_wire.setVisible(False)
            self.info.setText(w.info_text() if hasattr(w, "info_text") else "")
            w.setFocus()
        elif v["kind"] == "mesh":
            mv = self._ensure_mesh_view()
            self.stack.setCurrentWidget(mv)
            mv.show_mesh(v.get("mesh"), keep_view=keep_view, wire=self.btn_wire.isChecked())
            self.btn_travel.setVisible(False)
            self.btn_wire.setVisible(True)
            m = v.get("mesh")
            text = ''
            if m is not None:
                text = (f"{m.native_faces_total:,} polygons · {m.faces_total:,} triangles" if m.native_faces_total is not None
                        else f"{m.faces_total:,} faces")
            if v.get('status'):
                text = f"{v['status']} · {text}"
            self.info.setText(text.replace(',', ' '))
        elif v["kind"] == "image":
            from ...geom.drawing import Drawing
            self.stack.setCurrentWidget(self.plot)
            self.btn_travel.setVisible(False)
            self.btn_wire.setVisible(False)
            x, y, w, h = v["rect"]
            W, H = v.get("sheet") or (x + w, y + h)
            self.plot.show_drawing(Drawing(W, H, (0.0, 0.0, W, H), list(v.get("pens", []))), v.get("overlays"),
                                   keep_view=keep_view, raster=(v["image"], v["rect"]))
            self._zoom_text(self.plot.transform().m11())
        else:
            self.stack.setCurrentWidget(self.plot)
            self.btn_travel.setVisible(True)
            self.btn_wire.setVisible(False)
            self.plot.show_drawing(v.get("drawing"), v.get("overlays"), keep_view=keep_view,
                                   message=v.get("message", ""))
            self._zoom_text(self.plot.transform().m11())

    def _widget_info(self, text: str) -> None:
        if self.stack.currentWidget() is self.sender():
            self.info.setText(text)

    def current_title(self) -> str:
        return self.tabs.tabText(self.tabs.currentIndex()) if self.tabs.count() else ""

    def _zoom_text(self, scale: float) -> None:
        d = self.plot.drawing
        if d is None or self.stack.currentWidget() is not self.plot:
            return
        from ...geom.paper import name_for
        self.info.setText(f"{name_for(d.width, d.height)}   {scale / (96 / 25.4) * 100:.0f}%")
        i = self.tabs.currentIndex()
        if 0 <= i < len(self.views) and self.views[i].get('status'):
            self.info.setText(self.info.text()+' · '+self.views[i]['status'])

    def fit(self) -> None:
        w = self.stack.currentWidget()
        if hasattr(w, "fit"):
            w.fit()

    # ------------------------------------------------------------------ appearance
    def set_theme(self, theme) -> None:
        self.theme = theme
        self.plot.set_theme(theme)
        if self.mesh_view is not None:
            self.mesh_view.set_theme(theme)
        for w in self.hosted:
            if hasattr(w, "set_theme"):
                w.set_theme(theme)
        for b in (self.btn_travel, self.btn_wire, self.btn_fit):
            apply_icon(b, theme)

    def set_art(self, art: str) -> None:
        self.art = art
        self.plot.set_art(art)
        for w in self.hosted:
            if hasattr(w, "set_art"):
                w.set_art(art)
        if self.mesh_view is not None:
            self.mesh_view.set_art(art)
