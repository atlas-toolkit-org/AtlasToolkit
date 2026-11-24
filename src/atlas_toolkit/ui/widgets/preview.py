"""
The preview stage of a tool page: a row of view tabs (Before / After / Overlay, Input / Output, Model / Drawing),
a 2D plot view or a 3D mesh view, and a small toolbar. What it shows comes from the data, never from the tool:

    views = [{"title": "Before", "kind": "svg", "drawing": Drawing, "overlays": [Pen, ...]},
             {"title": "Output", "kind": "mesh", "mesh": MeshData}, ...]

Inputs bring their own views (from inspect), results add theirs. A tool may also bring an interactive widget (its
spec's `viewer`), shown as {"title": ..., "kind": "widget", "widget": QWidget}: the stage hosts it, passes it the
theme, the art mode and Fit, and shows its info_text() in the toolbar.

A widget with `sets_view = True` (an Aim tab, tools/plotter_vision/aim.py) is the one place the drawing's view is
set: its tab carries the aim glyph, a 3D tab beside it says "Inspect only" with a way back, and when the user opens
that 3D tab it turns to the side being aimed at (the widget's input_view() -> (azimuth, elevation) or None). Only
that way round: orbiting the 3D tab never changes the settings.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QStackedWidget, QTabBar, QToolButton,
                               QVBoxLayout, QWidget, QWidgetAction)

from ..theme import icon, mono
from .common import ElidedLabel, Mark, apply_icon, icon_button, label
from .plot_view import PlotView


class StageNote(QFrame):
    """A slim strip under the stage's tabs: an info glyph and a few words, the whole note in its tooltip."""

    def __init__(self, theme):
        super().__init__(objectName="stageNote")
        lay = QHBoxLayout(self)
        self.setFixedHeight(26)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(6)
        self.mark = Mark("info", "warning", "", theme, 14)
        self.text = ElidedLabel("")
        self.text.setProperty("class", "hint")
        lay.addWidget(self.mark)
        lay.addWidget(self.text, 1)
        self.hide()

    def set_note(self, note) -> None:
        self.setVisible(bool(note))
        if note:
            self.text.set_full_text(note[0])
            self.setToolTip(note[1])
            self.mark.set_tip(note[1])

    def mouseReleaseEvent(self, e):
        self.mark.show_tip()


class PreviewStage(QFrame):
    view_changed = Signal(str)

    def __init__(self, theme, settings=None):
        super().__init__()
        self.setObjectName("stage")
        self.theme = theme
        self.settings = settings            # keeps the 3D Display choice ("mesh_display"), shared by every page
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
        self.btn_inspect = QPushButton("Inspect only · view set in Aim")
        self.btn_inspect.setProperty("variant", "ghost")
        self.btn_inspect.setFont(mono(11))
        self.btn_inspect.setCursor(Qt.PointingHandCursor)
        self.btn_inspect.setToolTip("Orbit this 3D view to look the model over: it does not change the drawing. "
                                    "The drawing's view is set in the Aim tab.")
        self.btn_inspect.clicked.connect(self.show_aim)
        self.btn_inspect.setVisible(False)
        bar.addWidget(self.btn_inspect)
        self.info = QLabel("")
        self.info.setFont(mono(11))
        self.info.setProperty("class", "mono")
        bar.addWidget(self.info)
        self.btn_travel = icon_button("toolpath", "Show pen-up travel", theme, checkable=True, size=16)
        self.btn_travel.toggled.connect(lambda on: self.plot.set_travel(on))
        self.btn_wire = icon_button("display", "Display", theme, size=16)    # the 3D view's Display menu
        self.btn_wire.setPopupMode(QToolButton.InstantPopup)
        self.btn_wire.setMenu(self._display_menu())
        self.btn_wire.setVisible(False)
        self.btn_fit = icon_button("expand", "Fit (0 or double-click)", theme, size=16)
        self.btn_fit.clicked.connect(self.fit)
        bar.addWidget(self.btn_travel)
        bar.addWidget(self.btn_wire)
        bar.addWidget(self.btn_fit)
        lay.addLayout(bar)
        self.note = StageNote(theme)                     # what a hosted view does not show (its stage_note())
        lay.addWidget(self.note)
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
            if self.art != "paper":                  # made on first use: it takes the mode the stage already has
                self.mesh_view.set_art(self.art)
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
        self._mark_aim()
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
        self.btn_inspect.setVisible(False)
        self._note()

    def _note(self) -> None:
        """What the shown widget says about itself (an optional stage_note() -> (a few words, tooltip) or None)."""
        i = self.tabs.currentIndex()
        v = self.views[i] if 0 <= i < len(self.views) else {"kind": ""}
        get = getattr(v["widget"], "stage_note", None) if v["kind"] == "widget" else None
        self.note.set_note(get() if get else None)

    def _tab_changed(self, _i: int) -> None:
        self._show_current(keep_view=True, follow=True)
        self.view_changed.emit(self.tabs.tabText(self.tabs.currentIndex()))

    # ------------------------------------------------------------------ the Aim tab
    def aim_widget(self):
        """The hosted widget that sets the drawing's view, if any."""
        return next((v["widget"] for v in self.views
                     if v["kind"] == "widget" and getattr(v["widget"], "sets_view", False)), None)

    def show_aim(self) -> None:
        for i, v in enumerate(self.views):
            if v["kind"] == "widget" and getattr(v["widget"], "sets_view", False):
                self.tabs.setCurrentIndex(i)
                return

    def _mark_aim(self) -> None:
        for i, v in enumerate(self.views):
            if v["kind"] == "widget" and getattr(v["widget"], "sets_view", False):
                self.tabs.setTabIcon(i, icon("aim", self.theme["text-2"], 14))
                self.tabs.setTabToolTip(i, "Aim sets the drawing's view and framing.")

    def _show_current(self, keep_view: bool = False, follow: bool = False) -> None:
        i = self.tabs.currentIndex()
        if i < 0 or i >= len(self.views):
            self.clear()
            return
        v = self.views[i]
        self._note()
        aim = self.aim_widget()
        self.btn_inspect.setVisible(aim is not None and v["kind"] == "mesh")
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
            mv.display.update(self.display())          # chosen on another page: it holds here too
            mv.show_mesh(v.get("mesh"), keep_view=keep_view)
            angles = aim.input_view() if (follow and aim is not None and hasattr(aim, "input_view")) else None
            if angles is not None:                       # turn to the side being aimed at
                mv.set_camera(*angles)
            self.btn_travel.setVisible(False)
            self.btn_wire.setVisible(True)
            m = v.get("mesh")
            text = ''
            if m is not None:
                text = (f"{m.native_faces_total:,} polygons · {m.faces_total:,} triangles" if m.native_faces_total is not None
                        else f"{m.faces_total:,} faces")
            if v.get('status'):
                text = f"{v['status']} · {text}"
            n = mv.open_edge_count()
            if mv.display["open_edges"] and n is not None:
                text += f" · {self._open_text(n)}"
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

    # ------------------------------------------------------------------ the 3D Display menu
    def display(self) -> dict:
        from .mesh_view import DISPLAY
        saved = self.settings.get("mesh_display") if self.settings is not None else None
        d = dict(DISPLAY)
        if isinstance(saved, dict):
            d.update({k: v for k, v in saved.items() if k in DISPLAY})
        elif self.mesh_view is not None:
            d.update(self.mesh_view.display)
        return d

    def set_display(self, **changes) -> None:
        d = {**self.display(), **changes}
        if self.settings is not None:
            self.settings.set("mesh_display", d)
        if self.mesh_view is not None:
            self.mesh_view.display.update(d)
            if self.stack.currentWidget() is self.mesh_view:
                self._show_current(keep_view=True)   # redraws, and the info line (open edges)

    @staticmethod
    def _open_text(n: int) -> str:
        from ...geom.mesh import OPEN_EDGES_KEEP
        if n == 0:
            return "no open edges"
        return f"{n:,}+ open edges" if n >= OPEN_EDGES_KEEP else f"{n:,} open edge{'s' if n != 1 else ''}"

    def _display_menu(self) -> QMenu:
        """Styles (one at a time) and what is shown with them, as in MeshLab and Meshmixer."""
        from .mesh_view import OPTIONS, STYLES
        m = QMenu(self)
        m.setToolTipsVisible(True)

        def head(text, first=False):                 # the design system's micro-label (sections do not draw)
            if not first:
                m.addSeparator()
            lab = label(text)
            lab.setContentsMargins(14, 8 if first else 4, 14, 2)
            act = QWidgetAction(m)
            act.setDefaultWidget(lab)
            act.setEnabled(False)
            m.addAction(act)

        head("Style", first=True)
        group = QActionGroup(m)
        self._style_actions = {}
        for key, text, tip in STYLES:
            a = m.addAction(text)
            a.setCheckable(True)
            a.setToolTip(tip)
            a.setActionGroup(group)
            a.triggered.connect(lambda _=False, k=key: self.set_display(style=k))
            self._style_actions[key] = a
        head("Show")
        self._option_actions = {}
        for key, text, tip in OPTIONS:
            a = m.addAction(text)
            a.setCheckable(True)
            a.setToolTip(tip)
            a.toggled.connect(lambda on, k=key: self.set_display(**{k: on}))
            self._option_actions[key] = a
        m.aboutToShow.connect(self._sync_display_menu)
        return m

    def _sync_display_menu(self) -> None:
        from .mesh_view import OPTIONS
        d = self.display()
        self._style_actions.get(d["style"], self._style_actions["shaded"]).setChecked(True)
        for key, a in self._option_actions.items():
            a.blockSignals(True)
            a.setChecked(bool(d[key]))
            a.blockSignals(False)
        n = self.mesh_view.open_edge_count() if self.mesh_view is not None else None
        a = self._option_actions["open_edges"]
        tip = dict((k, t) for k, _x, t in OPTIONS)["open_edges"]
        count = "none" if n == 0 else (self._open_text(n).split(" open")[0] if n is not None else "")
        a.setText("Open edges" + (f"   ·  {count}" if count else ""))
        a.setEnabled(n is not None)
        a.setToolTip(tip if n is not None else "Not worked out for this view (very large meshes, or a view a tool "
                                               "drew itself).")
        self.btn_wire.setToolTip("Display: " + self._style_actions[d["style"]].text())

    def _widget_info(self, text: str) -> None:
        if self.stack.currentWidget() is self.sender():
            self.info.setText(text)
            self._note()                             # what it shows may have changed with it

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
        self.note.mark.retheme(theme)
        self._mark_aim()

    def set_art(self, art: str) -> None:
        self.art = art
        self.plot.set_art(art)
        for w in self.hosted:
            if hasattr(w, "set_art"):
                w.set_art(art)
        if self.mesh_view is not None:
            self.mesh_view.set_art(art)
