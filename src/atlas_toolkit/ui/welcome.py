"""
The welcome window, shown when the app opens on its own (not when it was started with files, a tool or a page to open):

    the plate             a drawing made with Atlas (theme/welcome.svg, scripts/dev/welcome_art.py), on paper or blueprint;
                          a new one for every X.Y version, showing what changed in it
    01 Before you start   the notice that the app is still in development, with feedback on GitHub and support on Ko-fi
    02 What's new         the headlines of the newest releases in CHANGELOG.md; the whole text is in About › Changelog
    03 Tips               three at a time; each opening starts on the next three

"Don't show this window at start" and Setup › General › Welcome window are the same option (core/prefs `show_welcome`).
The search box (and Help on macOS) opens the window at any time.
"""
from __future__ import annotations

import html
import re
from functools import lru_cache

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QScrollArea,
                               QTextBrowser, QVBoxLayout, QWidget)

from ..core.paths import resource
from ..version import APP_NAME, FEEDBACK_URL, HOMEPAGE, PRESETS_URL, SUPPORT_URL, __version__
from .about import changelog_text, releases
from .keys import shown
from .theme import icon, label_font, logo_icon, make_logo, mono, qcolor, sans
from .widgets.common import draw_reg_marks, hairline, icon_button
from .widgets.plot_view import BLUEPRINT_BG, BLUEPRINT_GRID, blueprint_color

SIZE, SMALLEST = (1084, 720), (640, 460)
PLATE_WIDTH, PLATE_FROM = 324, 940       # a window narrower than PLATE_FROM leaves the plate out
TIPS_SHOWN = 3
RELEASES_SHOWN = 2

NOTICE = (   # (bold lead, the rest)
    ("Unexpected issues may appear.", "Tools, settings and results can still change from one version to the next."),
    ("Use it at your own risk.", "Keep copies of your work, and check a drawing before you plot it."),
    ("Some tools can be slow or give strange results.",
     "Try a lighter model or other settings; you can cancel a run at any time."),
    ("Your feedback is essential.", "Tell us on GitHub what broke, what is missing and what you made."),
)

TIPS = (   # (icon, title, text, link's label, link: an address, or "setup:tab", "help:tab" or "workflows" in the app)
    ("catalogue", "Start with a light model",
     "The model catalogue's Light & quick models keep most tools quick. Open it from Home or beside a mesh tool's "
     "inputs.", "", ""),
    ("eye", "Preview, then run", "Preview is a quick draft to judge the effect; Run makes the final result.", "", ""),
    ("tag", "Begin from a preset",
     "The free Basic pack gives each tool a tuned starting point: drop the file on the window to load it.",
     "Get the Basic pack", PRESETS_URL),
    ("pipeline", "Chain tools in a workflow",
     "Reshape a model, draw it and clean the drawing in one go, on as many files as you like.",
     "Open Workflows", "workflows"),
    ("search", "Find anything", "Press Ctrl+K and type: every tool and action, by name.", "", ""),
    ("reset", "Get the settings back",
     "Drop an SVG made by a tool back on that tool to restore the settings that made it.", "", ""),
    ("process", "Lighten heavy models",
     "Remesh/Decimate makes a dense scan lighter, and most tools much quicker on it.", "", ""),
    ("mesh-repair", "Mend a broken model",
     "When a tool asks for a closed solid, Repair Mesh can close the holes and fix the faces.", "", ""),
    ("save", "Keep every version",
     "Outputs can get the date and time in their names, so no run overwrites another.",
     "Open Setup › Outputs", "setup:outputs"),
    ("sliders", "Step numbers faster",
     "On a number field's triangles, hold Shift for ten times the step and Ctrl for a tenth of it.", "", ""),
    ("topbar", "Make room for the drawing", "Press [ to hide the tool list, or move it to the top of the window.",
     "Open Setup › General", "setup:general"),
    ("feedback", "Report a problem well",
     "Copy diagnostics in About gives the version and system details a report needs. It includes your folder names, so "
     "read it before you post it.", "Open About", "help:about"),
)


# ---------------------------------------------------------------------- the changelog, in headlines
def _plain(md: str) -> str:
    """One line of plain text from a little Markdown."""
    md = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)
    return " ".join(md.replace("**", "").replace("`", "").split())


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text, maxsplit=1)[0]


def highlights(body: str) -> tuple[str, list[tuple[str, str]]]:
    """(opening paragraph, [(headline, first sentence of its text)]) of one release in CHANGELOG.md."""
    intro: list[str] = []
    items: list[list[str]] = []
    for line in body.splitlines():
        if line.startswith("- "):
            items.append([line[2:]])
        elif items:
            items[-1].append(line)
        else:
            intro.append(line)
    out = []
    for item in items:
        text = " ".join(part.strip() for part in item).strip()
        m = re.match(r"\*\*(.+?)\*\*\s*(.*)", text)
        if m:
            out.append((_plain(m[1]).rstrip("."), _first_sentence(_plain(m[2]))))
        else:
            out.append(("", _first_sentence(_plain(text))))
    return _plain(" ".join(intro)), out


def news_html(theme, count: int = RELEASES_SHOWN) -> str:
    """The newest releases, as the What's new text."""
    parts = []
    for i, (version, date, body) in enumerate(releases(changelog_text())[:count]):
        intro, items = highlights(body)
        parts.append(f'<p style="margin:{14 if i else 0}px 0 6px 0"><span style="color:{theme["highlight"]};'
                     f'font-weight:600">{html.escape(version)}</span>&nbsp;&nbsp;<span style="color:{theme["text-3"]}">'
                     f"{html.escape(date)}</span></p>")
        if intro:
            parts.append(f'<p style="margin:0 0 8px 0;color:{theme["text-2"]}">{html.escape(intro)}</p>')
        for lead, text in items:
            head = f'<span style="color:{theme["text"]};font-weight:600">{html.escape(lead)}.</span> ' if lead else ""
            parts.append(f'<p style="margin:0 0 8px 0;color:{theme["text-2"]}">{head}{html.escape(text)}</p>')
    return "".join(parts) or f'<p style="color:{theme["text-3"]}">The changelog is not available in this build.</p>'


# ---------------------------------------------------------------------- parts
@lru_cache(maxsize=1)
def _art_svg() -> str:
    path = resource("ui", "theme", "welcome.svg")
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def plate_info() -> dict:
    """What the plate says about itself (scripts/dev/welcome_art.py): series (the X.Y it was drawn for), title, caption."""
    head = _art_svg().split(">", 1)[0]
    return {k: html.unescape(v) for k, v in re.findall(r'\sdata-(\w+)="([^"]*)"', head)}


class Plate(QWidget):
    """The left side: a plate with registration marks, as the app shows artwork, with the name and a caption."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setFixedWidth(PLATE_WIDTH)
        self.setAccessibleName(f"{APP_NAME} {__version__}")
        self._art: tuple | None = None        # (what it was drawn for, pixmap)

    def _pixmap(self, size, ink) -> QPixmap | None:
        """The drawing at this size, kept: it has too many strokes to draw on every paint."""
        dpr = self.devicePixelRatioF()
        key = (size.width(), size.height(), ink.name(), dpr)
        if self._art is None or self._art[0] != key:
            r = QSvgRenderer(QByteArray(_art_svg().replace("currentColor", ink.name()).encode()))
            if not r.isValid():
                return None
            pm = QPixmap(int(size.width() * dpr), int(size.height() * dpr))
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            box, page = r.viewBoxF(), QRectF(pm.rect())
            k = min(page.width() / box.width(), page.height() / box.height())
            w, h = box.width() * k, box.height() * k
            r.render(p, QRectF((page.width() - w) / 2, (page.height() - h) / 2, w, h))
            p.end()
            pm.setDevicePixelRatio(dpr)
            self._art = (key, pm)
        return self._art[1]

    def paintEvent(self, _e) -> None:
        th, blue = self.ctx.theme, self.ctx.art == "blueprint"
        ink = blueprint_color(QColor(Qt.black)) if blue else qcolor(th["paper-ink"])
        soft, marks = qcolor(ink.name()), qcolor(ink.name())
        soft.setAlphaF(0.66)
        marks.setAlphaF(0.35)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.fillRect(r, BLUEPRINT_BG if blue else qcolor(th["paper"]))
        if blue:
            p.setPen(QPen(BLUEPRINT_GRID, 1))
            for x in range(0, self.width(), 22):
                p.drawLine(x, 0, x, self.height())
            for y in range(0, self.height(), 22):
                p.drawLine(0, y, self.width(), y)
        draw_reg_marks(p, r, marks, arm=14, inset=16)

        logo = make_logo(120)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(QRectF(34, 34, 40, 40), logo, QRectF(logo.rect()))
        p.setPen(ink)
        f = sans(15, 600)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 15 * 0.18)
        p.setFont(f)
        p.drawText(QPointF(86, 52), "ATLAS")
        p.setPen(soft)
        p.setFont(label_font(9.5))
        p.drawText(QPointF(86, 69), f"TOOLKIT · V{__version__}")

        art = QRectF(30, 96, r.width() - 60, r.height() - 96 - 86)
        pm = self._pixmap(art.size().toSize(), ink) if art.height() > 60 else None
        if pm is not None:
            p.drawPixmap(art.topLeft(), pm)
        p.setPen(ink)
        p.setFont(sans(13, 500))
        info = plate_info()
        p.drawText(QPointF(34, r.height() - 54), info.get("title", ""))
        p.setPen(soft)
        p.setFont(sans(11.5))
        p.drawText(QPointF(34, r.height() - 36), info.get("caption", ""))


def _section(number: str, text: str, *buttons) -> QHBoxLayout:
    """A numbered heading with a hairline, as the website's sections have."""
    row = QHBoxLayout()
    row.setSpacing(10)
    for t, cls in ((number, "labelNo"), (text.upper(), "label")):
        lab = QLabel(t)
        lab.setProperty("class", cls)
        lab.setFont(label_font(10.5))
        row.addWidget(lab)
    line = hairline()
    row.addWidget(line, 1)
    row.setAlignment(line, Qt.AlignVCenter)
    for b in buttons:
        row.addWidget(b)
    if buttons:
        row.addStrut(32)                  # headings side by side line up whatever they hold
    return row


def _rich(text: str, cls: str = "muted") -> QLabel:
    lab = QLabel(text)
    lab.setProperty("class", cls)
    lab.setTextFormat(Qt.RichText)
    lab.setWordWrap(True)
    return lab


def _tile(name: str, color: str, px: int, object_name: str = "iconTile") -> QLabel:
    lab = QLabel(objectName=object_name)
    lab.setFixedSize(px, px)
    lab.setAlignment(Qt.AlignCenter)
    glyph = round(px * 0.56)
    lab.setPixmap(icon(name, color, glyph, 1.7).pixmap(glyph, glyph))
    return lab


# ---------------------------------------------------------------------- the window
class WelcomeDialog(QDialog):
    def __init__(self, ctx, parent=None, tip_page: int | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.theme = ctx.theme
        self.setObjectName("welcomeDialog")
        self.setWindowTitle(f"Welcome · {APP_NAME}")
        self.setWindowIcon(logo_icon())
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowCloseButtonHint)
        self.tip_pages = -(-len(TIPS) // TIPS_SHOWN)
        if tip_page is None:                              # each opening starts on the next tips
            last = ctx.settings.get("welcome_tip")
            tip_page = last + 1 if isinstance(last, int) and not isinstance(last, bool) else 0
        self.tip_page = tip_page % self.tip_pages
        self._fit_screen(parent)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.setSizeConstraint(QLayout.SetNoConstraint)      # the plate gives way in a narrow window (resizeEvent)
        self.plate = Plate(ctx)
        outer.addWidget(self.plate)
        self.plate.setVisible(self.width() >= PLATE_FROM)
        right = QVBoxLayout()
        right.setSpacing(0)
        outer.addLayout(right, 1)
        body = QWidget(objectName="transparent")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(32, 26, 32, 22)
        lay.setSpacing(12)
        title = QLabel(f"Welcome to {APP_NAME}")
        title.setFont(sans(28, 300))
        lay.addWidget(title)
        lay.addSpacing(2)
        lay.addLayout(_section("01", "Before you start"))
        lay.addWidget(self._notice())
        lay.addSpacing(6)
        cols = QHBoxLayout()
        cols.setSpacing(26)
        cols.addLayout(self._news(), 1)
        cols.addLayout(self._tips(), 1)
        lay.addLayout(cols, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(body)
        right.addWidget(self.scroll, 1)
        right.addWidget(self._footer())
        self._show_tips()
        self.ok.setFocus()

    def _fit_screen(self, parent) -> None:
        """At its size, or as much of it as the screen has room for (the right side then scrolls)."""
        screen = (parent.screen() if parent is not None else None) or QGuiApplication.primaryScreen()
        w, h = SIZE
        if screen is not None:
            room = screen.availableGeometry()
            w, h = min(w, int(room.width() * 0.94)), min(h, int(room.height() * 0.88))
        self.setMinimumSize(min(SMALLEST[0], w), min(SMALLEST[1], h))
        self.resize(w, h)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.plate.setVisible(e.size().width() >= PLATE_FROM)

    # ------------------------------------------------------------------ 01
    def _notice(self) -> QWidget:
        box = QFrame(objectName="welcomeNotice")
        hl = QHBoxLayout(box)
        hl.setContentsMargins(18, 16, 20, 16)
        hl.setSpacing(16)
        hl.addWidget(_tile("warn", self.theme["warning"], 48, "warnTile"), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(5)
        self.notice_title = _rich(f"Atlas v{__version__} is still under development", "h2")
        col.addWidget(self.notice_title)
        col.addSpacing(2)
        strong = self.theme["text"]
        for lead, rest in NOTICE:
            row = QHBoxLayout()
            row.setSpacing(9)
            row.addWidget(QLabel("·", objectName="warnDot"), 0, Qt.AlignTop)
            row.addWidget(_rich(f'<span style="color:{strong};font-weight:600">{lead}</span> {rest}'), 1)
            col.addLayout(row)
        col.addSpacing(7)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_feedback = self._link("Leave feedback on GitHub", "feedback", FEEDBACK_URL)
        self.btn_support = self._link("Support on Ko-fi", "heart", SUPPORT_URL)
        for b in (self.btn_feedback, self.btn_support):
            buttons.addWidget(b)
        buttons.addStretch(1)
        col.addLayout(buttons)
        hl.addLayout(col, 1)
        return box

    def _link(self, text: str, glyph: str, target: str) -> QPushButton:
        b = QPushButton(text)
        b.setIcon(icon(glyph, self.theme["warning"], 16))
        b.setToolTip(target)
        b.setCursor(Qt.PointingHandCursor)
        b.setAutoDefault(False)
        b.clicked.connect(lambda: self.follow(target))
        return b

    # ------------------------------------------------------------------ 02
    def _news(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(10)
        more = _rich(f'<a href="help:changelog" style="color:{self.theme["accent-text"]}">Full changelog</a>', "hint")
        more.setWordWrap(False)
        more.linkActivated.connect(self.follow)
        col.addLayout(_section("02", "What's new", more))
        self.news = QTextBrowser(objectName="welcomeNews")
        self.news.document().setDocumentMargin(0)
        self.news.setHtml(news_html(self.theme))
        self.news.setMinimumHeight(170)
        col.addWidget(self.news, 1)
        return col

    # ------------------------------------------------------------------ 03
    def _tips(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(10)
        self.tip_prev = icon_button("left", "Previous tips", self.theme, size=14)
        self.tip_next = icon_button("right", "More tips", self.theme, size=14)
        self.tip_prev.clicked.connect(lambda: self._turn(-1))
        self.tip_next.clicked.connect(lambda: self._turn(1))
        self.tip_count = QLabel()
        self.tip_count.setProperty("class", "hint")
        self.tip_count.setFont(mono(11))
        col.addLayout(_section("03", "Tips", self.tip_prev, self.tip_count, self.tip_next))
        self.tip_box = QWidget(objectName="transparent")
        self.tip_lay = QVBoxLayout(self.tip_box)
        self.tip_lay.setContentsMargins(0, 2, 0, 0)
        self.tip_lay.setSpacing(14)
        col.addWidget(self.tip_box, 1)
        return col

    def _turn(self, by: int) -> None:
        self.tip_page = (self.tip_page + by) % self.tip_pages
        self._show_tips()

    def _show_tips(self) -> None:
        while self.tip_lay.count():
            w = self.tip_lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        first = self.tip_page * TIPS_SHOWN
        for tip in TIPS[first:first + TIPS_SHOWN]:
            self.tip_lay.addWidget(self._tip(*tip))
        self.tip_lay.addStretch(1)
        self.tip_count.setText(f"{self.tip_page + 1} / {self.tip_pages}")
        self.ctx.settings.set("welcome_tip", self.tip_page)

    def _tip(self, glyph: str, title: str, text: str, link: str, target: str) -> QWidget:
        w = QWidget(objectName="transparent")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(12)
        hl.addWidget(_tile(glyph, self.theme["highlight"], 34), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        head = QLabel(title)
        head.setProperty("class", "h3")
        col.addWidget(head)
        body = html.escape(shown(text), quote=False)
        if link:
            body += f' <a href="{target}" style="color:{self.theme["accent-text"]}">{html.escape(link)}</a>'
        lab = _rich(body)
        lab.linkActivated.connect(self.follow)
        col.addWidget(lab)
        hl.addLayout(col, 1)
        return w

    # ------------------------------------------------------------------ foot
    def _footer(self) -> QWidget:
        bar = QFrame(objectName="aboutFoot")
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(32, 10, 24, 12)
        hl.setSpacing(10)
        self.hide_box = QCheckBox("Don't show this window at start")
        self.hide_box.setToolTip("Setup › General › Welcome window shows it again")
        self.hide_box.setChecked(not self.ctx.pref("show_welcome"))
        self.hide_box.toggled.connect(lambda off: self.ctx.set_pref("show_welcome", not off))
        hl.addWidget(self.hide_box)
        hl.addStretch(1)
        site = QPushButton("atlas-toolkit.org")
        site.setProperty("variant", "ghost")
        site.setAutoDefault(False)
        site.clicked.connect(lambda: self.follow(HOMEPAGE))
        hl.addWidget(site)
        self.ok = QPushButton("Start")
        self.ok.setProperty("variant", "primary")
        self.ok.setMinimumWidth(110)
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.close)
        hl.addWidget(self.ok)
        return bar

    def show_pref(self) -> None:
        """The option was changed in Setup: the box follows."""
        self.hide_box.blockSignals(True)
        self.hide_box.setChecked(not self.ctx.pref("show_welcome"))
        self.hide_box.blockSignals(False)

    def follow(self, target: str) -> None:
        """A link: an address opens in the browser; anything else is a place in the app, which takes over."""
        if target.startswith("https://"):
            from ..core.paths import open_url
            open_url(target)
            return
        place, _, tab = target.partition(":")
        self.close()
        if place == "setup":
            self.ctx.show_setup(tab)
        elif place == "help":
            self.ctx.show_help(tab)
        elif place == "workflows":
            self.ctx.open_workflow()
