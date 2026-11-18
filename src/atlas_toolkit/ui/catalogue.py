"""
The model catalogue window: ready-made models to try the mesh tools with (core/catalogue.py has the data).

    paths = pick_models(parent, multiple=True)      # [] when cancelled

Categories on the left, cards in the middle, the chosen model on the right: its picture, large, with its credit
and licence. "Use" fetches what is missing (downloads are queued, can be
cancelled and say plainly when there is no connection) and returns the files. Every place where a mesh is chosen
opens it the same way: a tool's Inputs, a mesh file setting, a workflow's inputs and its fixed-input steps, Home.
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import (QAbstractListModel, QModelIndex, QObject, QRectF, QSize, QSortFilterProxyModel, Qt,
                            QTimer, Signal)
from PySide6.QtGui import QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListView,
                               QListWidget, QListWidgetItem, QPushButton, QSizePolicy, QSlider, QSplitter, QStyle,
                               QStyledItemDelegate, QVBoxLayout, QWidget)

from ..core import catalogue as core
from ..core.paths import display_path, open_url, reveal
from .theme import icon, label_font, mono, qcolor, sans
from .widgets.common import Segmented, hint

APP_CONTEXT = None            # the shell's AppContext (set_context); entry points without one pass theirs
ALL, RECENT = "__all__", "__recent__"
ROLE_MODEL = Qt.UserRole + 1
RECENT_MAX = 12


def set_context(ctx) -> None:
    global APP_CONTEXT
    APP_CONTEXT = ctx


def pick_models(parent=None, multiple: bool = True, ctx=None, title: str = "") -> list[str]:
    """Open the catalogue; the chosen models' files once they are on this computer ([] when cancelled)."""
    ctx = ctx or APP_CONTEXT
    if ctx is None:
        return []
    dlg = CatalogueDialog(ctx, parent, multiple=multiple, title=title)
    return dlg.paths if dlg.exec() == QDialog.Accepted else []


def size_text(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{max(1, round(n / 1e3))} KB"


def tri_text(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{round(n / 1e3)}k"


# ----------------------------------------------------------------------------- worker news (thread → UI)
def _emit(signal, *args) -> None:
    """From a worker thread: the window may have closed meanwhile, and then nobody needs the news."""
    try:
        signal.emit(*args)
    except RuntimeError:
        pass


class _News(QObject):
    progressed = Signal(str, str, object)        # model id, stage, fraction or None
    fetched = Signal(str, str)                   # model id, path
    failed = Signal(str, str)                    # model id, message
    done = Signal(bool)                          # all fetched (not cancelled, no failure)


# ----------------------------------------------------------------------------- the cards
class ModelList(QAbstractListModel):
    def __init__(self, cat: core.Catalogue):
        super().__init__()
        self.cat = cat
        self.items = cat.models()
        self.state = {m.id: cat.status(m) for m in self.items}      # local | bundled | remote
        self.busy: dict = {}                                        # id → (stage, fraction)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.DisplayRole):
        m = self.items[index.row()]
        if role == Qt.DisplayRole:
            return m.name
        if role == ROLE_MODEL:
            return m
        if role == Qt.ToolTipRole:
            return f"{m.name}\n{m.description}" if m.description else m.name
        return None

    def row_of(self, mid: str) -> int:
        return next((i for i, m in enumerate(self.items) if m.id == mid), -1)

    def refresh(self, mid: str | None = None) -> None:
        if mid is None:
            self.state = {m.id: self.cat.status(m) for m in self.items}
            self.dataChanged.emit(self.index(0), self.index(len(self.items) - 1))
            return
        r = self.row_of(mid)
        if r >= 0:
            self.state[mid] = self.cat.status(self.items[r])
            self.dataChanged.emit(self.index(r), self.index(r))


class ModelFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.category = ALL
        self.query = ""
        self.show = "all"                         # all | app | download | local
        self.recent: list = []

    def set(self, **kw) -> None:
        if hasattr(self, "beginFilterChange"):           # Qt 6.9+: invalidateFilter() is deprecated
            self.beginFilterChange()
            self.__dict__.update(kw)
            self.endFilterChange()
        else:
            self.__dict__.update(kw)
            self.invalidateFilter()
        if self.category == RECENT:
            self.invalidate()
            self.sort(0)

    def filterAcceptsRow(self, row, parent):
        src = self.sourceModel()
        m = src.items[row]
        if self.category == RECENT:
            if m.id not in self.recent:
                return False
        elif self.category != ALL and m.category != self.category:
            return False
        if self.show == "app" and m.kind != "bundled":           # comes with the app, unpacked or not
            return False
        if self.show == "download" and m.kind != "remote":       # from the catalogue's release
            return False
        if self.show == "local" and src.state.get(m.id) != "local":
            return False
        return m.matches(self.query)

    def lessThan(self, a, b):
        if self.category == RECENT:
            ra, rb = (self.recent.index(self.sourceModel().items[i.row()].id) for i in (a, b))
            return ra < rb
        return a.row() < b.row()


class CardDelegate(QStyledItemDelegate):
    """A card: the picture on a soft tile, the name, triangles and what using it takes."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self.d = dialog
        self.cache: dict = {}

    def sizeHint(self, option, index):
        s = self.d.card_size
        return QSize(s + 16, s + 62)

    def pixmap(self, m, px: int) -> QPixmap | None:
        key = (m.id, px)
        if key not in self.cache:
            pm = QPixmap(str(self.d.cat.thumbnail(m)))
            self.cache[key] = None if pm.isNull() else pm.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return self.cache[key]

    def paint(self, p: QPainter, option, index):
        th = self.d.theme
        m = index.data(ROLE_MODEL)
        src = self.d.list_model
        state = src.state.get(m.id, "remote")
        busy = src.busy.get(m.id)
        r = QRectF(option.rect).adjusted(4, 4, -4, -4)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        p.fillPath(path, qcolor(th["bg-3"] if selected else th["bg-2"] if hover else th["bg-1"]))
        p.setPen(QPen(qcolor(th["accent"] if selected else th["line-3"] if hover else th["line"]), 2 if selected else 1))
        p.drawPath(path)
        s = self.d.card_size
        tile = QRectF(r.left() + 4, r.top() + 4, r.width() - 8, s)
        dpr = p.device().devicePixelRatioF() if p.device() else 1.0
        pm = self.pixmap(m, int(s * dpr))
        if pm is not None:
            pm.setDevicePixelRatio(dpr)
            w, h = pm.width() / dpr, pm.height() / dpr
            p.drawPixmap(QRectF(tile.center().x() - w / 2, tile.center().y() - h / 2, w, h), pm, QRectF(pm.rect()))
        # name
        p.setPen(qcolor(th["text"]))
        p.setFont(sans(12.5, 500))
        name_r = QRectF(r.left() + 10, tile.bottom() + 6, r.width() - 20, 18)
        p.drawText(name_r, Qt.AlignLeft | Qt.AlignVCenter, p.fontMetrics().elidedText(m.name, Qt.ElideRight,
                                                                                         int(name_r.width())))
        # meta: triangles · what it takes
        meta_r = QRectF(r.left() + 10, name_r.bottom() + 2, r.width() - 20, 16)
        p.setFont(mono(10.5))
        p.setPen(qcolor(th["text-3"]))
        what = {"local": "on this computer", "bundled": "in the app", "remote": size_text(m.bytes)}[state]
        p.drawText(meta_r, Qt.AlignLeft | Qt.AlignVCenter, f"{tri_text(m.triangles)} △")
        p.setPen(qcolor(th["success"] if state == "local" else th["text-3"]))
        ic_name = {"local": "check", "remote": "download", "bundled": "archive"}[state]
        ic = icon(ic_name, th["success"] if state == "local" else th["text-3"], 12)
        tw = p.fontMetrics().horizontalAdvance(what)
        p.drawText(meta_r, Qt.AlignRight | Qt.AlignVCenter, what)
        ic.paint(p, int(meta_r.right() - tw - 16), int(meta_r.center().y() - 6), 12, 12)
        # progress
        if busy:
            stage, frac = busy
            bar = QRectF(tile.left() + 8, tile.bottom() - 12, tile.width() - 16, 4)
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(th["bg-4"]))
            p.drawRoundedRect(bar, 2, 2)
            p.setBrush(qcolor(th["highlight"]))
            if frac is None:                                  # unknown length: a moving segment
                t = (time.monotonic() * 0.8) % 1.0
                p.drawRoundedRect(QRectF(bar.left() + t * bar.width() * 0.75, bar.top(), bar.width() * 0.25, 4), 2, 2)
            else:
                p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * max(0.02, frac), 4), 2, 2)
            p.setFont(label_font(9))
            p.setPen(qcolor(th["highlight"]))
            p.drawText(QRectF(bar.left(), bar.top() - 16, bar.width(), 14), Qt.AlignLeft, stage.upper())
        p.restore()


class Picture(QWidget):
    """A pixmap drawn to fit its box, whatever the box's size (the card's picture, large)."""

    def __init__(self):
        super().__init__()
        self.pm: QPixmap | None = None
        self.setObjectName("catalogueStage")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_pixmap(self, pm: QPixmap | None) -> None:
        self.pm = None if pm is None or pm.isNull() else pm
        self.update()

    def paintEvent(self, _e):
        if self.pm is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        side = min(self.width(), self.height())
        r = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        p.drawPixmap(r, self.pm, QRectF(self.pm.rect()))


# ----------------------------------------------------------------------------- the window
class CatalogueDialog(QDialog):
    def __init__(self, ctx, parent=None, multiple: bool = True, title: str = ""):
        super().__init__(parent)
        self.ctx = ctx
        self.theme = ctx.theme
        self.multiple = multiple
        self.paths: list[str] = []
        self.cat = core.Catalogue.load(settings=ctx.settings)
        self.view_state = dict(ctx.settings.get("catalogue_view", {}) or {})
        self.card_size = int(min(240, max(112, self.view_state.get("size", 160))))
        self.news = _News()
        self.news.progressed.connect(self._progressed)
        self.news.fetched.connect(self._fetched)
        self.news.failed.connect(self._failed)
        self.news.done.connect(self._done)
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._fetched_paths: dict = {}
        self._errors: list = []
        self.setObjectName("catalogueDialog")
        self.setWindowTitle(title or "Model catalogue")
        self.setWindowIcon(icon("catalogue", self.theme["highlight"], 32))
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowMinMaxButtonsHint
                            | Qt.WindowCloseButtonHint)
        self.setSizeGripEnabled(True)
        self.setMinimumSize(820, 540)
        w, h = self.view_state.get("window", (1180, 760))
        self.resize(int(w), int(h))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._header())
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)
        split.addWidget(self._rail())
        split.addWidget(self._grid())
        split.addWidget(self._details())
        split.setStretchFactor(1, 1)
        split.setSizes([236, 620, 330])
        lay.addWidget(split, 1)
        lay.addWidget(self._footer())
        self._ticker = QTimer(self, interval=60)
        self._ticker.timeout.connect(lambda: self.grid.viewport().update())
        self._restore()

    # ------------------------------------------------------------------ frame
    def _header(self) -> QWidget:
        bar = QFrame(objectName="aboutHead")
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(24, 16, 20, 14)
        hl.setSpacing(14)
        glyph = QLabel()
        glyph.setPixmap(icon("catalogue", self.theme["highlight"], 30).pixmap(30, 30))
        hl.addWidget(glyph)
        col = QVBoxLayout()
        col.setSpacing(2)
        name = QLabel("MODEL CATALOGUE")
        name.setFont(label_font(15))
        col.addWidget(name)
        n = len(self.cat.models())
        sub = hint(f"{n} models to try the tools with: pick a category, then a model.", wrap=False)
        if self.multiple:
            sub.setToolTip("Ctrl or Shift picks several.")
        col.addWidget(sub)
        hl.addLayout(col, 1)
        self.search = QLineEdit(placeholderText="Search names, artists, tags…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", self.theme["text-3"], 16), QLineEdit.LeadingPosition)
        self.search.setMinimumWidth(200)
        self.search.textChanged.connect(lambda t: self._filter(query=t))
        hl.addWidget(self.search)
        self.show_seg = Segmented([("all", "All"), ("app", "In the app"), ("download", "To download"),
                                   ("local", "Local")], columns=4,
                                  tips={"all": "Every model", "app": "The models that come with the app: ready at once",
                                        "download": "The models that download on first use",
                                        "local": "Already in your catalogue folder on this computer"})
        self.show_seg.set_value("all")
        self.show_seg.setFixedWidth(480)
        self.show_seg.picked.connect(lambda v: self._filter(show=v))
        hl.addWidget(self.show_seg)
        return bar

    def _rail(self) -> QWidget:
        self.rail = QListWidget(objectName="catalogueRail")
        self.rail.setIconSize(QSize(20, 20))
        self.rail.setMinimumWidth(228)
        self.rail.setFrameShape(QFrame.NoFrame)
        self.rail.setFont(sans(13))
        rows = [(ALL, "All models", "grid", len(self.cat.models()))]
        rows += [(c.id, c.name, c.icon, len(self.cat.models(c.id))) for c in self.cat.categories]
        rows += [(RECENT, "Recently used", "clock", None)]
        for key, text, ic, count in rows:
            it = QListWidgetItem(icon(ic, self.theme["text-2"], 20, active=self.theme["accent-text"]),
                                 f"{text}" + (f"   {count}" if count is not None else ""))
            it.setData(Qt.UserRole, key)
            it.setSizeHint(QSize(180, 40))
            c = next((c for c in self.cat.categories if c.id == key), None)
            if c is not None and c.blurb:
                it.setToolTip(c.blurb)
            self.rail.addItem(it)
        self.rail.currentItemChanged.connect(self._category_changed)
        return self.rail

    def _grid(self) -> QWidget:
        box = QWidget(objectName="catalogueCentre")
        v = QVBoxLayout(box)
        v.setContentsMargins(16, 12, 12, 8)
        v.setSpacing(8)
        top = QHBoxLayout()
        self.cat_title = QLabel()
        self.cat_title.setFont(sans(17, 600))
        top.addWidget(self.cat_title)
        top.addStretch(1)
        small = QLabel()
        small.setPixmap(icon("zoomout", self.theme["text-3"], 14).pixmap(14, 14))
        top.addWidget(small)
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(112, 240)
        self.size_slider.setValue(self.card_size)
        self.size_slider.setFixedWidth(110)
        self.size_slider.setToolTip("Card size")
        self.size_slider.valueChanged.connect(self._resize_cards)
        top.addWidget(self.size_slider)
        big = QLabel()
        big.setPixmap(icon("zoomin", self.theme["text-3"], 14).pixmap(14, 14))
        top.addWidget(big)
        v.addLayout(top)
        self.cat_blurb = hint("")
        v.addWidget(self.cat_blurb)
        self.list_model = ModelList(self.cat)
        self.proxy = ModelFilter()
        self.proxy.setSourceModel(self.list_model)
        self.proxy.recent = list(self.ctx.settings.get("catalogue_recent", []) or [])
        self.grid = QListView(objectName="catalogueGrid")
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setSpacing(4)
        self.grid.setMouseTracking(True)
        self.grid.setFrameShape(QFrame.NoFrame)
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.grid.verticalScrollBar().setSingleStep(24)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection if self.multiple
                                   else QAbstractItemView.SingleSelection)
        self.delegate = CardDelegate(self)
        self.grid.setItemDelegate(self.delegate)
        self.grid.setModel(self.proxy)
        self.grid.selectionModel().selectionChanged.connect(self._selection_changed)
        self.grid.doubleClicked.connect(lambda _i: self._use())
        v.addWidget(self.grid, 1)
        self.empty = hint("No model matches. Clear the search, or show all models instead of those on this computer.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.hide()
        v.addWidget(self.empty)
        return box

    def _details(self) -> QWidget:
        box = QFrame(objectName="catalogueDetails")
        box.setMinimumWidth(300)
        v = QVBoxLayout(box)
        v.setContentsMargins(16, 14, 18, 14)
        v.setSpacing(8)
        self.big = Picture()
        self.big.setMinimumHeight(260)
        v.addWidget(self.big, 3)
        self.stage_note = hint("")
        self.stage_note.setAlignment(Qt.AlignCenter)
        v.addWidget(self.stage_note)
        self.d_name = QLabel()
        self.d_name.setFont(sans(17, 600))
        self.d_name.setWordWrap(True)
        v.addWidget(self.d_name)
        self.d_desc = QLabel(wordWrap=True)
        self.d_desc.setProperty("class", "summary")
        v.addWidget(self.d_desc)
        self.d_facts = QLabel(wordWrap=True, textFormat=Qt.RichText, openExternalLinks=False)
        self.d_facts.linkActivated.connect(open_url)
        self.d_facts.setFont(sans(12))
        v.addWidget(self.d_facts)
        row = QHBoxLayout()
        self.b_get = QPushButton("Download")
        self.b_get.setIcon(icon("download", self.theme["text-2"], 16))
        self.b_get.clicked.connect(self._get_selected)
        self.b_remove = QPushButton("Remove from this computer")
        self.b_remove.setProperty("variant", "ghost")
        self.b_remove.clicked.connect(self._remove_selected)
        row.addWidget(self.b_get)
        row.addWidget(self.b_remove)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        return box

    def _footer(self) -> QWidget:
        bar = QFrame(objectName="aboutFoot")
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(24, 10, 20, 12)
        hl.setSpacing(8)
        self.status = QLabel()
        self.status.setFont(mono(11))
        self.status.setProperty("class", "hint")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hl.addWidget(self.status, 1)
        folder = QPushButton("Show folder")
        folder.setIcon(icon("folder", self.theme["text-2"], 16))
        folder.setToolTip(f"Models go to {display_path(self.cat.folder)} (Setup › Storage changes it); tools write "
                          "their outputs beside them.")
        folder.clicked.connect(self._show_folder)
        hl.addWidget(folder)
        self.b_cancel = QPushButton("Cancel")
        self.b_cancel.clicked.connect(self._cancel_or_close)
        hl.addWidget(self.b_cancel)
        self.b_use = QPushButton("Use model")
        self.b_use.setProperty("variant", "primary")
        self.b_use.setDefault(True)
        self.b_use.clicked.connect(self._use)
        hl.addWidget(self.b_use)
        return bar

    # ------------------------------------------------------------------ state
    def _restore(self) -> None:
        want = self.view_state.get("category", ALL)
        keys = [self.rail.item(i).data(Qt.UserRole) for i in range(self.rail.count())]
        self.rail.setCurrentRow(keys.index(want) if want in keys else 0)
        last = self.view_state.get("model")
        if last:
            r = self.list_model.row_of(last)
            idx = self.proxy.mapFromSource(self.list_model.index(r)) if r >= 0 else QModelIndex()
            if idx.isValid():
                self.grid.setCurrentIndex(idx)
                self.grid.scrollTo(idx)
        if not self.grid.selectionModel().hasSelection() and self.proxy.rowCount():
            self.grid.setCurrentIndex(self.proxy.index(0, 0))
        self._selection_changed()

    def _save_state(self) -> None:
        sel = self.selected()
        self.view_state.update({"category": self.proxy.category, "size": self.card_size,
                                "window": [self.width(), self.height()],
                                "model": sel[0].id if sel else self.view_state.get("model")})
        self.ctx.settings.set("catalogue_view", self.view_state)
        try:
            self.ctx.settings.save()
        except OSError:
            pass

    def _filter(self, **kw) -> None:
        self.proxy.set(**kw)
        self.empty.setVisible(self.proxy.rowCount() == 0)
        if self.proxy.rowCount() and not self.grid.selectionModel().hasSelection():
            self.grid.setCurrentIndex(self.proxy.index(0, 0))

    def _category_changed(self, cur, _prev=None) -> None:
        if cur is None:
            return
        key = cur.data(Qt.UserRole)
        if key == ALL:
            self.cat_title.setText("All models")
            self.cat_blurb.setText("Every model in the catalogue. Those marked “in the app” are ready at once; "
                                   "the others download once, then stay.")
        elif key == RECENT:
            self.cat_title.setText("Recently used")
            self.cat_blurb.setText("The models you used last, newest first.")
        else:
            c = self.cat.category(key)
            self.cat_title.setText(c.name)
            self.cat_blurb.setText(c.blurb)
        self.grid.clearSelection()
        self._filter(category=key)
        self.grid.scrollToTop()

    def _resize_cards(self, v: int) -> None:
        self.card_size = v
        self.delegate.cache.clear()
        self.grid.doItemsLayout()

    def selected(self) -> list:
        rows = sorted(self.grid.selectionModel().selectedIndexes(), key=lambda i: i.row())
        return [i.data(ROLE_MODEL) for i in rows]

    def _selection_changed(self, *_a) -> None:
        sel = self.selected()
        n = len(sel)
        self.b_use.setEnabled(n > 0 and self._worker is None)
        self.b_use.setText("Use model" if n <= 1 else f"Use {n} models")
        need = [m for m in sel if self.list_model.state.get(m.id) == "remote"]
        total = sum(m.bytes for m in need)
        if self._worker is None:
            self.status.setText(f"{len(need)} to download, {size_text(total)}." if need else
                                ("" if n else "Pick a model."))
        if n != 1:
            self._show_many(sel)
        else:
            self._show(sel[0])

    def _show_many(self, sel) -> None:
        self.big.set_pixmap(None)
        self.stage_note.setText("")
        self.d_name.setText(f"{len(sel)} models" if sel else "")
        self.d_desc.setText(", ".join(m.name for m in sel[:8]) + ("…" if len(sel) > 8 else ""))
        self.d_facts.setText("")
        self.b_get.setVisible(any(self.list_model.state.get(m.id) == "remote" for m in sel))
        self.b_remove.setVisible(False)

    def _show(self, m) -> None:
        state = self.list_model.state.get(m.id)
        self.d_name.setText(m.name)
        self.d_desc.setText(m.description)
        accent = self.theme["accent-text"]
        facts = []
        if m.author:
            facts.append(f"<b>{_esc(m.author)}</b>" + (f", {_esc(m.made)}" if m.made else ""))
        elif m.made:
            facts.append(_esc(m.made))
        if m.generator:
            facts.append("Made by Atlas Toolkit from its equations (CC0, free for anything).")
        elif m.credit:
            facts.append("Scan or model: " + _esc(m.credit))
        lic = m.license
        if m.license_url:
            lic = f'<a href="{m.license_url}" style="color:{accent}">{_esc(m.license)}</a>'
        if not m.generator:
            src = f' · <a href="{m.source_url}" style="color:{accent}">source</a>' if m.source_url else ""
            facts.append(f"Licence: {lic}{src}")
        size = size_text(m.bytes) + " to download" if state == "remote" else ""
        facts.append(f"<span style='font-family:\"Geist Mono\"'>{m.triangles:,} triangles".replace(",", " ")
                     + (f" · {size}" if size else "") + "</span>")
        if m.changes:
            facts.append(f"<span style='color:{self.theme['text-3']}'>Changed: {_esc(m.changes)}</span>")
        self.d_facts.setText("<br>".join(facts))
        self.b_get.setVisible(state == "remote")
        self.b_get.setEnabled(self._worker is None)
        self.b_remove.setVisible(state == "local" and m.kind == "remote")
        # the picture now, the live view once the file is here
        self.big.set_pixmap(QPixmap(str(self.cat.thumbnail(m))))
        self.stage_note.setText({"remote": "Downloads when you use it, then stays on this computer.",
                                 "bundled": "Comes with the app: ready at once.",
                                 "local": "On this computer: ready at once."}.get(state, ""))

    # ------------------------------------------------------------------ fetching
    def _use(self) -> None:
        sel = self.selected()
        if not sel or self._worker is not None:
            return
        if not self.multiple:
            sel = sel[:1]
        self._fetch(sel, then_accept=True)

    def _get_selected(self) -> None:
        sel = [m for m in self.selected() if self.list_model.state.get(m.id) == "remote"]
        if sel:
            self._fetch(sel, then_accept=False)

    def _fetch(self, models: list, then_accept: bool) -> None:
        self._cancel.clear()
        self._errors = []
        self._fetched_paths = {}
        self._then_accept = then_accept
        self._order = [m.id for m in models]
        for m in models:
            if self.list_model.state.get(m.id) != "local":
                self.list_model.busy[m.id] = ("waiting", 0.0)
        self._ticker.start()
        self.b_use.setEnabled(False)
        self.b_get.setEnabled(False)
        self.b_cancel.setText("Stop")

        def work():
            ok = True
            for m in models:
                if self._cancel.is_set():
                    ok = False
                    break
                try:
                    path = self.cat.ensure(
                        m, progress=lambda stage, frac, mid=m.id: _emit(self.news.progressed, mid, stage, frac),
                        cancelled=self._cancel.is_set)
                    _emit(self.news.fetched, m.id, str(path))
                except Exception as e:
                    ok = False
                    _emit(self.news.failed, m.id, _readable(e))
                    if getattr(e, "kind", "") == "cancelled":
                        break
            _emit(self.news.done, ok and not self._cancel.is_set())
        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()
        self.status.setText("Getting the models ready…")

    def _progressed(self, mid: str, stage: str, frac) -> None:
        self.list_model.busy[mid] = (stage, frac)
        m = self.cat.get(mid)
        pct = f" {frac * 100:.0f}%" if frac is not None else ""
        self.status.setText(f"{stage.capitalize()} {m.name}…{pct}")

    def _fetched(self, mid: str, path: str) -> None:
        self.list_model.busy.pop(mid, None)
        self._fetched_paths[mid] = path
        self.list_model.refresh(mid)

    def _failed(self, mid: str, message: str) -> None:
        self.list_model.busy.pop(mid, None)
        self._errors.append((mid, message))
        self.list_model.refresh(mid)

    def _done(self, ok: bool) -> None:
        self._worker = None
        self._ticker.stop()
        self.list_model.busy.clear()
        self.grid.viewport().update()
        self.b_cancel.setText("Cancel")
        if self._errors:
            mid, msg = self._errors[0]
            self.status.setText(f"{self.cat.get(mid).name}: {msg}")
            self.status.setProperty("class", "warning")
        elif not ok:
            self.status.setText("Stopped. What was already fetched stays on this computer.")
        self._selection_changed()
        if ok and not self._errors and self._then_accept:
            self.paths = [self._fetched_paths[i] for i in self._order if i in self._fetched_paths]
            recent = [i for i in self._order] + [i for i in self.proxy.recent if i not in self._order]
            self.ctx.settings.set("catalogue_recent", recent[:RECENT_MAX])
            self._save_state()
            self.accept()

    def _cancel_or_close(self) -> None:
        if self._worker is not None:
            self._cancel.set()
            self.status.setText("Stopping…")
            return
        self.reject()

    def _remove_selected(self) -> None:
        for m in self.selected():
            if self.list_model.state.get(m.id) == "local" and m.kind == "remote":
                self.cat.remove(m)
                self.list_model.refresh(m.id)
        self._selection_changed()

    def _show_folder(self) -> None:
        self.cat.folder.mkdir(parents=True, exist_ok=True)
        reveal(self.cat.folder)

    # ------------------------------------------------------------------ closing
    def done(self, r: int) -> None:
        if self._worker is not None:                 # closing while fetching: stop, then close
            self._cancel.set()
            self._worker.join(5)
            self._worker = None
        if r != QDialog.Accepted:
            self._save_state()
        super().done(r)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _readable(e: Exception) -> str:
    text = str(e) or type(e).__name__
    return text if text.endswith(".") else text + "."


# ----------------------------------------------------------------------------- a button for the entry points
def catalogue_button(theme, text: str = "Catalogue…", tip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setIcon(icon("catalogue", theme["text-2"], 16))
    b.setToolTip(tip or "Pick ready-made models to try the tool with: busts, statues, animals, shapes and more.")
    return b


__all__ = ["CatalogueDialog", "catalogue_button", "pick_models", "set_context"]
