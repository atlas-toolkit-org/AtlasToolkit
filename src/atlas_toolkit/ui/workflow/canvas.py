"""
The workflow canvas: steps as cards on a dotted plane, ports as dots coloured by kind (mesh cyan, SVG blue, image
violet), edges as arrows.

    drag a card                 move it (a rubber band selects several)
    drag from an output dot     connect it to an input dot; ports that cannot take it fade while you drag
    drag an input's arrow away  unplug it, or move it to another input
    let go on empty canvas      pick a step to add there, already connected
    wheel · middle/Space-drag   zoom · pan          F fit · L lay out · Tab add · Del delete · P preview

The canvas only draws and reports: the page owns the workflow and rebuilds the canvas after each change.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QGraphicsScene, QGraphicsView

from ..theme import icon, label_font, mono, qcolor, sans
from ..widgets.common import draw_reg_marks

NODE_W = 224.0
HEAD_H = 58.0
ROW_H = 22.0
PORT_R = 5.5
GRID = 24.0
MIME = "application/x-atlas-workflow-entry"
KIND_TONE = {"mesh": "highlight", "svg": "accent", "image": "secondary"}
CHIP_TONE = {"accent": "accent-text", "live": "highlight", "success": "success", "warning": "warning",
             "danger": "danger", "": "text-3"}


@dataclass
class Card:
    """What a node shows; the page fills it in from the workflow, the registry and the run state."""
    id: str
    title: str
    sub: str = ""
    icon: str = "process"
    tone: str = "highlight"                       # icon colour token
    ports: list = field(default_factory=list)     # [(key, label, kind, side)] inputs
    out_kind: str | None = None                   # kind handed on; None with has_output False (Save)
    has_output: bool = True
    chip: str = ""
    chip_tone: str = ""
    level: str = ""                               # "", warning, error (from Check)
    missing: bool = False                         # the tool is not installed
    progress: float = -1.0                        # 0..1 while running


def kind_color(theme, kind) -> QColor:
    return qcolor(theme[KIND_TONE.get(kind, "text-3")])


class PortItem(QGraphicsItem):
    def __init__(self, node: "NodeItem", key: str, kind, output: bool, side: bool = False):
        super().__init__(node)
        self.node, self.key, self.kind, self.output, self.side = node, key, kind, output, side
        self.hover = self.dim = self.connected = False
        self.setAcceptHoverEvents(True)
        self.setZValue(3)
        self.setCursor(Qt.CrossCursor)

    def boundingRect(self) -> QRectF:
        r = PORT_R + 6
        return QRectF(-r, -r, 2 * r, 2 * r)

    def shape(self) -> QPainterPath:
        p = QPainterPath()
        p.addEllipse(self.boundingRect())
        return p

    def paint(self, p, _opt, _w=None):
        th = self.node.canvas.theme
        col = kind_color(th, self.kind)
        if self.dim:
            col.setAlphaF(0.22)
        r = PORT_R + (1.5 if self.hover and not self.dim else 0)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(col, 1.6))
        p.setBrush(col if self.connected else qcolor(th["bg-1"]))
        p.drawEllipse(QPointF(0, 0), r, r)

    def center(self) -> QPointF:
        return self.mapToScene(QPointF(0, 0))

    def hoverEnterEvent(self, e):
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.update()


class NodeItem(QGraphicsObject):
    def __init__(self, canvas: "WorkflowView", card: Card):
        super().__init__()
        self.canvas = canvas
        self.card = card
        self.edges: list = []
        self.ports_in: dict[str, PortItem] = {}
        self.port_out: PortItem | None = None
        self.hover = False
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
                      | QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(1)
        self._build_ports()

    # ------------------------------------------------------------------ geometry
    def sides(self) -> list:
        return [p for p in self.card.ports if p[3]]

    def height(self) -> float:
        n = len(self.sides())
        return HEAD_H + (ROW_H * n + 6 if n else 0)

    def _build_ports(self) -> None:
        for it in list(self.ports_in.values()) + ([self.port_out] if self.port_out else []):
            it.setParentItem(None)
            if it.scene() is not None:
                it.scene().removeItem(it)
        self.ports_in, self.port_out = {}, None
        row = 0
        for key, _label, kind, side in self.card.ports:
            it = PortItem(self, key, kind, False, side)
            it.setPos(0, HEAD_H / 2 if not side else HEAD_H + ROW_H * (row + 0.5) + 2)
            row += bool(side)
            self.ports_in[key] = it
        if self.card.has_output:
            self.port_out = PortItem(self, "out", self.card.out_kind, True)
            self.port_out.setPos(NODE_W, HEAD_H / 2)

    def set_card(self, card: Card) -> None:
        reshape = [p[:4] for p in card.ports] != [p[:4] for p in self.card.ports] or \
            card.has_output != self.card.has_output or card.out_kind != self.card.out_kind
        self.prepareGeometryChange()
        self.card = card
        if reshape:
            connected = {k for k, it in self.ports_in.items() if it.connected}
            self._build_ports()
            for k in connected & set(self.ports_in):
                self.ports_in[k].connected = True
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(-14, -18, NODE_W + 28, self.height() + 32)

    def shape(self) -> QPainterPath:
        p = QPainterPath()
        p.addRoundedRect(QRectF(0, 0, NODE_W, self.height()), 10, 10)
        return p

    # ------------------------------------------------------------------ painting
    def paint(self, p, _opt, _w=None):
        th = self.canvas.theme
        c = self.card
        h = self.height()
        r = QRectF(0, 0, NODE_W, h)
        p.setRenderHint(QPainter.Antialiasing)
        sel = self.isSelected()
        border = qcolor(th["line-3"] if (self.hover or sel) else th["line-2"])
        width = 1.0
        if c.level == "error":
            border, width = qcolor(th["danger"]), 1.6
        elif c.level == "warning":
            border, width = qcolor(th["warning"]), 1.3
        elif sel:
            border = qcolor(th["accent"])
        p.setPen(QPen(border, width))
        p.setBrush(qcolor(th["bg-2"] if sel else th["bg-1"]))
        p.drawRoundedRect(r, 10, 10)
        if c.missing:
            p.setBrush(QBrush(qcolor(th["line-2"]), Qt.BDiagPattern))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 9, 9)
        # icon, title, subtitle
        pm = icon(c.icon, th[c.tone] if not c.missing else th["text-4"], 18, 1.6).pixmap(18, 18)
        p.drawPixmap(QPointF(14, 11), pm)
        tf = sans(13.5, 500)
        p.setFont(tf)
        p.setPen(qcolor(th["text"] if not c.missing else th["text-3"]))
        tw = NODE_W - 50
        p.drawText(QRectF(40, 8, tw, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(tf).elidedText(c.title, Qt.ElideRight, tw))
        sf = mono(10.5)
        p.setFont(sf)
        p.setPen(qcolor(th["text-3"]))
        p.drawText(QRectF(40, 30, tw, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(sf).elidedText(c.sub, Qt.ElideRight, tw))
        sides = self.sides()
        if sides:
            p.setPen(QPen(qcolor(th["line"]), 1))
            p.drawLine(QPointF(10, HEAD_H), QPointF(NODE_W - 10, HEAD_H))
            p.setPen(qcolor(th["text-2"]))
            for i, (_key, label, _kind, _side) in enumerate(sides):
                p.drawText(QRectF(16, HEAD_H + 2 + ROW_H * i, NODE_W - 32, ROW_H), Qt.AlignLeft | Qt.AlignVCenter,
                           QFontMetricsF(sf).elidedText(label, Qt.ElideRight, NODE_W - 32))
        if 0.0 <= c.progress < 1.0:           # a thin progress line along the bottom edge
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(th["highlight"]))
            p.drawRoundedRect(QRectF(10, h - 4, max(2.0, (NODE_W - 20) * c.progress), 2), 1, 1)
        if c.chip:
            cf = label_font(9)
            p.setFont(cf)
            fm = QFontMetricsF(cf)
            w = fm.horizontalAdvance(c.chip.upper()) + 16
            chip = QRectF(NODE_W - w - 12, -9, w, 18)
            tone = qcolor(th[CHIP_TONE.get(c.chip_tone, "text-3")])
            p.setBrush(qcolor(th["bg-1"]))
            p.setPen(QPen(tone, 1))
            p.drawRoundedRect(chip, 5, 5)
            p.setPen(tone)
            p.drawText(chip, Qt.AlignCenter, c.chip.upper())
        if sel:
            draw_reg_marks(p, r, qcolor(th["highlight"]), arm=10, inset=-6)

    # ------------------------------------------------------------------ events
    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for e in self.edges:
                e.update_path()
        return super().itemChange(change, value)

    def hoverEnterEvent(self, e):
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.update()

    def mouseDoubleClickEvent(self, e):
        self.canvas.activated.emit(self.card.id)


class EdgeItem(QGraphicsPathItem):
    def __init__(self, canvas: "WorkflowView", src: NodeItem, dst: NodeItem, port: str):
        super().__init__()
        self.canvas, self.src, self.dst, self.port = canvas, src, dst, port
        self.hover = False
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setZValue(0)
        src.edges.append(self)
        dst.edges.append(self)
        self.update_path()

    def ends(self) -> tuple[QPointF, QPointF]:
        a = self.src.port_out.center() if self.src.port_out else self.src.mapToScene(QPointF(NODE_W, HEAD_H / 2))
        tp = self.dst.ports_in.get(self.port)
        b = tp.center() if tp else self.dst.mapToScene(QPointF(0, HEAD_H / 2))
        return a, b

    def update_path(self) -> None:
        a, b = self.ends()
        self.setPath(curve(a, QPointF(b.x() - PORT_R - 5, b.y())))

    def shape(self) -> QPainterPath:
        s = QPainterPathStroker()
        s.setWidth(12)
        return s.createStroke(self.path())

    def paint(self, p, _opt, _w=None):
        th = self.canvas.theme
        col = kind_color(th, self.src.card.out_kind)
        sel = self.isSelected()
        if sel:
            col = qcolor(th["highlight"])
        elif not self.hover:
            col.setAlphaF(0.75)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(col, 2.4 if (sel or self.hover) else 1.6, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawPath(self.path())
        _a, b = self.ends()
        tip = QPointF(b.x() - PORT_R - 1.5, b.y())
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([tip, QPointF(tip.x() - 8, tip.y() - 4.5), QPointF(tip.x() - 8, tip.y() + 4.5)]))

    def hoverEnterEvent(self, e):
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.update()


def curve(a: QPointF, b: QPointF) -> QPainterPath:
    dx = max(48.0, abs(b.x() - a.x()) * 0.5)
    path = QPainterPath(a)
    path.cubicTo(QPointF(a.x() + dx, a.y()), QPointF(b.x() - dx, b.y()), b)
    return path


class WorkflowView(QGraphicsView):
    selected = Signal(object)                     # node id or None
    connect_requested = Signal(str, str, str)     # src, dst, port
    rewire_requested = Signal(str, str, str, str, str)   # src, old dst, old port, new dst, new port
    unplug_requested = Signal(str, str)           # dst, port
    delete_requested = Signal(list, list)         # node ids, [(dst, port)]
    moved = Signal(dict)                          # id -> (x, y), after a drag
    entry_dropped = Signal(str, QPointF)          # library entry, scene position
    quick_add = Signal(QPointF, object)           # scene position, node to connect from (or None)
    activated = Signal(str)                       # double-click on a card
    context = Signal(object, QPoint, QPointF)     # node id or None, global position, scene position
    action = Signal(str)                          # layout, duplicate, preview
    files_dropped = Signal(list)

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.scene_ = QGraphicsScene(self)
        self.scene_.setSceneRect(QRectF(-6000, -6000, 12000, 12000))
        self.setScene(self.scene_)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.nodes: dict[str, NodeItem] = {}
        self.edge_items: list[EdgeItem] = []
        self.can_connect = lambda src, dst, port: True
        self._drag = None          # connecting: {"src": id, "anchor": QPointF, "rewire": (dst, port) | None}
        self._temp: QGraphicsPathItem | None = None
        self._pan = None
        self._space = False
        self._press_pos: dict = {}
        self._rebuilding = False
        self.scene_.selectionChanged.connect(self._selection_changed)

    # ------------------------------------------------------------------ content
    def rebuild(self, cards: list[Card], positions: dict, edges: list, select: str | None = None) -> None:
        """Replace everything: cards with their positions, edges as (src, dst, port)."""
        self._rebuilding = True
        try:
            self.scene_.clear()
            self.nodes, self.edge_items, self._temp = {}, [], None
            for c in cards:
                it = NodeItem(self, c)
                x, y = positions.get(c.id, (0.0, 0.0))
                it.setPos(QPointF(x, y))
                self.scene_.addItem(it)
                self.nodes[c.id] = it
            for src, dst, port in edges:
                if src in self.nodes and dst in self.nodes:
                    e = EdgeItem(self, self.nodes[src], self.nodes[dst], port)
                    self.scene_.addItem(e)
                    self.edge_items.append(e)
                    if port in self.nodes[dst].ports_in:
                        self.nodes[dst].ports_in[port].connected = True
                    if self.nodes[src].port_out:
                        self.nodes[src].port_out.connected = True
            if select in self.nodes:
                self.nodes[select].setSelected(True)
        finally:
            self._rebuilding = False
        self.viewport().update()

    def update_card(self, card: Card) -> None:
        it = self.nodes.get(card.id)
        if it is not None:
            it.set_card(card)
            for e in it.edges:
                e.update_path()

    def select(self, node_id: str | None) -> None:
        self._rebuilding = True                # one selection signal, for the final state
        try:
            self.scene_.clearSelection()
            if node_id in self.nodes:
                self.nodes[node_id].setSelected(True)
                self.ensureVisible(self.nodes[node_id], 60, 60)
        finally:
            self._rebuilding = False
        self.selected.emit(self.selected_id())

    def selected_id(self) -> str | None:
        for it in self.scene_.selectedItems():
            if isinstance(it, NodeItem):
                return it.card.id
        return None

    def _selection_changed(self) -> None:
        if not self._rebuilding:
            self.selected.emit(self.selected_id())

    def center_pos(self) -> QPointF:
        return self.mapToScene(self.viewport().rect().center())

    def fit(self) -> None:
        if not self.nodes:
            self.resetTransform()
            self.centerOn(QPointF(NODE_W, 0))
            return
        r = QRectF()
        for it in self.nodes.values():
            r = r.united(it.sceneBoundingRect())
        self.fitInView(r.adjusted(-36, -40, 36, 40), Qt.KeepAspectRatio)
        s = self.transform().m11()
        if s > 1.1:
            self.scale(1.1 / s, 1.1 / s)
            self.centerOn(r.center())

    def set_theme(self, theme) -> None:
        self.theme = theme
        self.viewport().update()
        self.scene_.update()

    # ------------------------------------------------------------------ painting
    def drawBackground(self, p, rect):
        th = self.theme
        p.fillRect(rect, qcolor(th["bg"]))
        step = GRID * (2 if self.transform().m11() < 0.55 else 1)
        dot = qcolor(th["line-2"])
        p.setPen(QPen(dot, 1.6 / max(self.transform().m11(), 0.3), Qt.SolidLine, Qt.RoundCap))
        x0 = int(rect.left() // step) * step
        y0 = int(rect.top() // step) * step
        pts = []
        y = y0
        while y < rect.bottom():
            x = x0
            while x < rect.right():
                pts.append(QPointF(x, y))
                x += step
            y += step
        if len(pts) < 40000:
            p.drawPoints(pts)

    def drawForeground(self, p, rect):
        if self.nodes:
            return
        p.save()
        p.resetTransform()
        vr = QRectF(self.viewport().rect())
        p.setPen(qcolor(self.theme["text-3"]))
        p.setFont(sans(14))
        p.drawText(vr.adjusted(0, -16, 0, -16), Qt.AlignCenter,
                   "Drag tools here from the list, or press Tab to add one.")
        p.setFont(mono(11))
        p.drawText(vr.adjusted(0, 24, 0, 24), Qt.AlignCenter, "INPUT  →  TOOLS  →  SAVE")
        p.restore()

    # ------------------------------------------------------------------ mouse
    def _port_at(self, pos: QPoint) -> PortItem | None:
        for it in self.items(pos):
            if isinstance(it, PortItem):
                return it
        return None

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and self._space):
            self._pan = e.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        if e.button() == Qt.LeftButton:
            port = self._port_at(e.position().toPoint())
            if port is not None:
                self._start_connect(port)
                e.accept()
                return
            self._press_pos = {k: (it.pos().x(), it.pos().y()) for k, it in self.nodes.items()}
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position().toPoint() - self._pan
            self._pan = e.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - d.y())
            return
        if self._drag is not None:
            pos = self.mapToScene(e.position().toPoint())
            port = self._port_at(e.position().toPoint())
            end = port.center() if (port is not None and not port.dim and not port.output) else pos
            self._temp.setPath(curve(self._drag["anchor"], end))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pan is not None:
            self._pan = None
            self.setCursor(Qt.ArrowCursor)
            return
        if self._drag is not None:
            self._finish_connect(e.position().toPoint())
            return
        super().mouseReleaseEvent(e)
        if e.button() == Qt.LeftButton and self._press_pos:
            moved = {k: (round(it.pos().x(), 1), round(it.pos().y(), 1)) for k, it in self.nodes.items()
                     if self._press_pos.get(k) != (it.pos().x(), it.pos().y())}
            self._press_pos = {}
            if moved:
                self.moved.emit(moved)

    def _start_connect(self, port: PortItem) -> None:
        rewire = None
        if port.output:
            src = port.node.card.id
        else:                                  # pick up the arrow plugged into this input, if any
            edge = next((x for x in self.edge_items if x.dst is port.node and x.port == port.key), None)
            if edge is None:
                return
            src = edge.src.card.id
            rewire = (port.node.card.id, port.key)
            edge.hide()
        anchor = self.nodes[src].port_out.center()
        self._drag = {"src": src, "anchor": anchor, "rewire": rewire}
        self._temp = QGraphicsPathItem()
        col = kind_color(self.theme, self.nodes[src].card.out_kind)
        self._temp.setPen(QPen(col, 1.8, Qt.DashLine, Qt.RoundCap))
        self._temp.setZValue(5)
        self.scene_.addItem(self._temp)
        for nid, it in self.nodes.items():     # fade the inputs that cannot take this output
            for key, p in it.ports_in.items():
                p.dim = not ((rewire is not None and (nid, key) == rewire) or self.can_connect(src, nid, key))
                p.update()
            if it.port_out:
                it.port_out.dim = True
                it.port_out.update()

    def _finish_connect(self, pos: QPoint) -> None:
        d, self._drag = self._drag, None
        if self._temp is not None:
            self.scene_.removeItem(self._temp)
            self._temp = None
        for it in self.nodes.values():
            for p in list(it.ports_in.values()) + ([it.port_out] if it.port_out else []):
                p.dim = False
                p.update()
        for x in self.edge_items:
            x.show()
        port = self._port_at(pos)
        target = (port.node.card.id, port.key) if (port is not None and not port.output) else None
        if d["rewire"] is not None:
            if target == d["rewire"]:
                return
            if target is not None and self.can_connect(d["src"], *target):
                self.rewire_requested.emit(d["src"], d["rewire"][0], d["rewire"][1], target[0], target[1])
            else:
                self.unplug_requested.emit(*d["rewire"])
            return
        if target is not None:
            if self.can_connect(d["src"], *target):
                self.connect_requested.emit(d["src"], target[0], target[1])
            return
        if port is None and self.itemAt(pos) is None:
            self.quick_add.emit(self.mapToScene(pos), d["src"])

    def wheelEvent(self, e):
        f = 1.0015 ** e.angleDelta().y()
        s = self.transform().m11()
        f = max(0.25 / s, min(2.5 / s, f))
        self.scale(f, f)

    def contextMenuEvent(self, e):
        it = self.itemAt(e.pos())
        while it is not None and not isinstance(it, NodeItem):
            it = it.parentItem()
        nid = it.card.id if isinstance(it, NodeItem) else None
        if nid is not None and not it.isSelected():
            self.select(nid)
        self.context.emit(nid, e.globalPos(), self.mapToScene(e.pos()))

    # ------------------------------------------------------------------ keys
    def focusNextPrevChild(self, _next: bool) -> bool:
        return False                           # Tab adds a step

    def keyPressEvent(self, e):
        k, mods = e.key(), e.modifiers()
        if k in (Qt.Key_Delete, Qt.Key_Backspace):
            nodes = [it.card.id for it in self.scene_.selectedItems() if isinstance(it, NodeItem)]
            edges = [(it.dst.card.id, it.port) for it in self.scene_.selectedItems() if isinstance(it, EdgeItem)]
            if nodes or edges:
                self.delete_requested.emit(nodes, edges)
            return
        if k == Qt.Key_Space and not e.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.OpenHandCursor)
            return
        if mods & Qt.ControlModifier and k == Qt.Key_D:
            self.action.emit("duplicate")
            return
        if mods & (Qt.ControlModifier | Qt.AltModifier):
            super().keyPressEvent(e)
            return
        if k == Qt.Key_Tab:
            pos = self.mapToScene(self.mapFromGlobal(self.cursor().pos()))
            if not self.viewport().rect().contains(self.mapFromScene(pos)):
                pos = self.center_pos()
            self.quick_add.emit(pos, self.selected_id())
            return
        if k == Qt.Key_F:
            self.fit()
            return
        if k == Qt.Key_L:
            self.action.emit("layout")
            return
        if k == Qt.Key_P:
            self.action.emit("preview")
            return
        if k == Qt.Key_Escape and self.scene_.selectedItems():
            self.scene_.clearSelection()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = False
            self.setCursor(Qt.ArrowCursor)
            return
        super().keyReleaseEvent(e)

    # ------------------------------------------------------------------ drops from the library and the desktop
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasFormat(MIME) or md.hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        md = e.mimeData()
        if md.hasFormat(MIME) or md.hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasFormat(MIME):
            self.entry_dropped.emit(bytes(md.data(MIME)).decode("utf-8"), self.mapToScene(e.position().toPoint()))
            e.acceptProposedAction()
        elif md.hasUrls():
            self.files_dropped.emit([u.toLocalFile() for u in md.urls() if u.isLocalFile()])
            e.acceptProposedAction()
        else:
            super().dropEvent(e)
