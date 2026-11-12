"""
The studio design system for Qt: tokens (synced from studio/design by scripts/sync_design.py), Geist fonts,
line icons, and a stylesheet generated for the dark (default) and light themes.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer

HERE = Path(__file__).resolve().parent
_RGBA = re.compile(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)")

# Toolkit glyphs not in the studio set, same grid and stroke rules (24x24, 1.5, round caps).
EXTRA_ICONS = {
    "skeleton-shell": '<path d="M12 3a4 4 0 0 1 4 4c0 1.8-1 3-2 4l3 2 1 8H6l1-8 3-2c-1-1-2-2.2-2-4a4 4 0 0 1 4-4z"/><path d="m12 3-2 4 2 4 2-4zM7 13l3 4 2-6 2 6 3-4M6 21l4-4 2 4 2-4 4 4"/>',
    "crumble": '<path d="M4 21V5l5-2 2 4-2 3 2 3-2 3 1 5M4 21h16"/><path d="m14 7 3-1 1 3-3 1zM13 13l3 1-1 3-3-1zM18 15l2 1-1 2-2-1z"/>',
    "stairs": '<path d="M3 21v-4h4v-4h4V9h4V5h6M3 21h18V5"/><path d="M7 21v-4M11 21v-8M15 21V9"/>',
    "motion-sculpture": '<path d="M5 4h5l9 2-9 1H4m0 3h7l10 2-11 1H3m2 3h5l9 2-10 1H5M7 3 5 21"/>',
    "line-smooth": '<path d="M3 6 6 3l3 6 3-6 3 6 3-6 3 3M3 19c3-10 6-10 9-4s6 6 9-4"/><path d="m10 11 2 2 2-2"/>',
    "line-simplify": '<path d="m3 8 3-3 3 3 3-3 3 3 3-3 3 3M3 18c5-6 13-6 18 0"/><circle cx="3" cy="18" r="1"/><circle cx="21" cy="18" r="1"/>',
    "surface-eject": '<path d="M3 12v8h12v-8h-4v4H7v-4zM11 3h6v6h-6zM14 12v-1m3 3 4-4m-4 0h4v4"/>',
    "bend-spine": '<path d="M5 21V10a7 7 0 0 1 14 0v2M9 21V10a3 3 0 0 1 6 0v2M5 17h4M5 13h4M6 7l3 2M11 3v4M17 5l-3 3M15 12h4"/><path d="m17 16 2-4 2 4"/>',
    "surface-texture": '<path d="M3 7l3-4 3 4-3 4zM9 7l3-4 3 4-3 4zM15 7l3-4 3 4-3 4zM3 15l3-4 3 4-3 4zM9 15l3-4 3 4-3 4zM15 15l3-4 3 4-3 4z"/>',
    "pipe-fill": '<path d="M3 5h12a3 3 0 0 1 0 6H8a3 3 0 0 0 0 6h13M7 3v5M17 14v7"/><path d="M3 8h11a.5.5 0 0 1 0 1H8a5 5 0 0 0 0 10h13"/>',
    "voxel-fill": '<path d="m3 8 9-5 9 5v9l-9 5-9-5zM3 8l9 5 9-5M12 13v9M7.5 5.5l9 5v9M3 12.5l9 5 9-5M7.5 10.5v9M16.5 5.5l-9 5"/>',
    "image-to-mesh": '<rect x="3" y="3" width="18" height="8" rx="1"/><circle cx="7" cy="6" r="1"/><path d="m10 10 4-4 6 4M3 17l9 5 9-5-9-5-9 5Zm0 0 9-1 9 1m-9-5v10"/>',
    "raster-dem-to-mesh": '<path d="m3 16 9 5 9-5v-3l-9 5-9-5zM3 13l5-7 4 4 3-6 6 9-9 5zM8 6l1 7 3-3m0 0 3 5 0-11"/>',
    "remesh-decimate": '<path d="m3 5 7-2 4 5-4 5-7-2zM3 5l7 8M10 3v10m-7-2 7-8M16 5l5 3-2 7-6 1-3-3m4-5 7 0-8 8 6-1M13 16l2 5 4-6"/>',
    "mirror-plane": '<path d="M12 3v18" stroke-dasharray="2 2"/><path d="M3 6h5v12H3l3-6zM21 6h-5v12h5l-3-6z"/>',
    "plane-cut": '<path d="m3 14 7-5 11 2-7 5zM10 9V3m4 13v5M5 5l14 14"/><path d="m16 17 3 2-1-3"/>',
    "developable": '<path d="M3 17 7 5l5 4 5-4 4 12-5 3-4-4-4 4-5-3ZM7 5l1 15m4-11v7m5-11-1 15M3 17l9-1 9 1"/>',
    "scissors": '<circle cx="6" cy="7" r="2.5"/><circle cx="6" cy="17" r="2.5"/><path d="M8.2 8.3 20 17M8.2 15.7 20 7"/>',
    "hatch": '<path d="M4 20 20 4M4 14 14 4M10 20 20 10M4 8l4-4M16 20l4-4"/>',
    "shatter": '<path d="m12 3 3 6-3 3-4-2zM15 9l5 1-2 6-6-4zM12 12l-4-2-4 5 6 4zM12 12l6 4-4 5-4-2z"/>',
    "voxel": '<rect x="4" y="12" width="7" height="7" rx="1"/><rect x="13" y="12" width="7" height="7" rx="1"/>'
             '<rect x="8.5" y="4" width="7" height="7" rx="1"/>',
    "replicate": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" '
                 'rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><path d="M17 14v6M14 17h6"/>',
    "rosette": '<circle cx="12" cy="12" r="1.8"/><path d="M12 10.2C9.5 8 9.5 5 12 3c2.5 2 2.5 5 0 7.2zM13.8 12c2.2-2.5 5.2-2.5 7.2 0'
               '-2 2.5-5 2.5-7.2 0zM12 13.8c2.5 2.2 2.5 5.2 0 7.2-2.5-2-2.5-5 0-7.2zM10.2 12C8 14.5 5 14.5 3 12c2-2.5 5-2.5 7.2 0z"/>',
    "slice": '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 12h16M12 3v18" stroke-dasharray="2 2.2"/>',
    "dice": '<rect x="4" y="4" width="16" height="16" rx="3.5"/><circle cx="9" cy="9" r="1"/><circle cx="15" cy="15" '
            'r="1"/><circle cx="15" cy="9" r="1"/><circle cx="9" cy="15" r="1"/>',
    "play": '<path d="M7 5v14l11-7z"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "reset": '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4.5h4.5"/>',
    "save": '<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
    "trash": '<path d="M4.5 7h15M10 7V4.5h4V7M6.5 7l1 13h9l1-13"/>',
    "send": '<path d="M4 12h13M13 7l5 5-5 5"/><path d="M20 5v14"/>',
    "log": '<path d="M5 6h14M5 10h14M5 14h9M5 18h6"/>',
    "warn": '<path d="M12 4 21 19H3z"/><path d="M12 10v4M12 16.5v.5"/>',
    "gear": '<path d="M18.6 10 21.1 10.4V13.6L18.6 14 18.1 15.2 19.5 17.3 17.3 19.5 15.2 18.1 14 18.6 13.6 21.1H10.4'
            'L10 18.6 8.8 18.1 6.7 19.5 4.5 17.3 5.9 15.2 5.4 14 2.9 13.6V10.4L5.4 10 5.9 8.8 4.5 6.7 6.7 4.5 8.8 5.9'
            ' 10 5.4 10.4 2.9H13.6L14 5.4 15.2 5.9 17.3 4.5 19.5 6.7 18.1 8.8Z"/><circle cx="12" cy="12" r="3"/>',
    "sliders": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    # 3D tools (Contour Lines, Twist, Melt, Stipple, Sketch Hatching)
    "contours": '<path d="M7 5.5c2.5-1.4 7.5-1.4 10 0M5 9.5c3.5-1.8 10.5-1.8 14 0M4.5 13.5c4-2 11-2 15 0'
                'M5.5 17.5c3.5-1.7 9.5-1.7 13 0"/>',
    "twist": '<path d="M8 3c0 5 8 4.5 8 9s-8 4-8 9M16 3c0 5-8 4.5-8 9s8 4 8 9"/>',
    "melt": '<path d="M5.5 10a6.5 6 0 0 1 13 0v3.5c0 1.2-1.6 1.2-1.6 0v-1c0-.9-1.4-.9-1.4 0v4.5c0 1.3-2 1.3-2 0'
            'v-2.5c0-.9-1.4-.9-1.4 0v1.5c0 1.2-1.6 1.2-1.6 0V12c0-.9-1.4-.9-1.4 0v.5c0 1-1.6 1-1.6 0z"/>'
            '<path d="M4 20.5h16"/>',
    "stipple": '<circle cx="7" cy="7" r=".8"/><circle cx="12" cy="5.5" r=".8"/><circle cx="16.5" cy="8" r=".8"/>'
               '<circle cx="9" cy="11" r=".8"/><circle cx="14" cy="12" r=".8"/><circle cx="6" cy="15" r=".8"/>'
               '<circle cx="11" cy="16" r=".8"/><circle cx="17" cy="15.5" r=".8"/><circle cx="14" cy="19" r=".8"/>',
    "surface-stripes": '<path d="M5 20c-3-5-2-12 2-16M10 21C6 15 7 8 12 3M15 21c-4-6-3-12 2-17M20 20c-3-5-2-10 1-13"/>',
    "strata": '<path d="M4 4.5h10.5M6.5 8.5h13M3.5 12.5h12M7 16.5h12.5M5 20.5h11"/>',
    "flow": '<path d="M3 7c3-2.5 6 2 9 0s4.5-2 9-.5M3 12.5c3-2.5 6 2 9 0 2-1.4 3.5-1.6 5-1"/>'
            '<path d="M17 11.5a3 3 0 1 1-2.6 4.6M3 18c3-2.5 6 2 9 0"/>',
    "porous": '<path d="M12 3.5c4.7 0 8 3.4 8 8.3 0 4.6-3.3 8.7-8 8.7s-8-4.1-8-8.7c0-4.9 3.3-8.3 8-8.3z"/>'
              '<circle cx="9" cy="9" r="1.6"/><circle cx="14.8" cy="10.2" r="2"/><circle cx="10.2" cy="14.6" r="1.8"/>'
              '<circle cx="15.2" cy="15.8" r="1"/>',
    "holes": '<rect x="3.5" y="3.5" width="17" height="17" rx="2.5"/><circle cx="8.5" cy="8.5" r="2.2"/>'
             '<circle cx="15.5" cy="8.5" r="2.2"/><circle cx="8.5" cy="15.5" r="2.2"/><circle cx="15.5" cy="15.5" r="2.2"/>',
    "flowrep-sketch": '<path d="M5 7c3-4 11-4 14 0s-3 13-7 14S2 11 5 7z"/><path d="M5 7c2 3 12 3 14 0M4 12c4 3 12 3 '
                      '16 0M7 4c-1 5 1 13 5 17M16 4c1 5-1 13-4 17"/>',
    "rods": '<circle cx="12" cy="12" r="5.5"/><path d="M2.5 7.5 21.5 16.5M5 20.5 19 3.5"/>',
    "peel": '<path d="M12 21v-7.5"/><path d="M12 13.5C6.5 13.5 4 9.5 4.5 4c4.5.5 7.5 4 7.5 9.5z"/>'
            '<path d="M12 13.5c5.5 0 8-4 7.5-9.5-4.5.5-7.5 4-7.5 9.5z"/><path d="M8 9.5c1.3 1 2.4 2.2 4 4"/>',
    "sketch": '<path d="M4 20l1-4 11-11 3 3-11 11z"/><path d="M14 7l3 3M3.5 20.5c3-.5 5-1 8 0"/>',
    "fusion": '<path d="M8.5 6.5c1.6 0 2.6 1.4 3.5 2.6.9-1.2 1.9-2.6 3.5-2.6a5.5 5.5 0 1 1 0 11c-1.6 0-2.6-1.4-3.5-2.6'
              '-.9 1.2-1.9 2.6-3.5 2.6a5.5 5.5 0 1 1 0-11z"/><path d="M12 9.1v5.8"/>',
    "growth": '<path d="M4 6c2.5-2 5 2 8 0s5.5-2 8 0M4 12c2.5-2 4.5 1.5 6.5 0M4 18c2.5-2 5 2 8 0s5.5-2 8 0"/>'
              '<path d="M14.5 12c1.5-1.2 3.5-1.2 5.5 0"/><circle cx="12.5" cy="12" r="1"/>',
    "ribbon": '<circle cx="12" cy="11" r="6.5"/><path d="M5.8 8.6c3.4 2.6 9 2.6 12.4 0M5.8 13.4c3.4 2.6 9 2.6 12.4 0"/>'
              '<path d="M15.5 16.6c.6 2 1.8 3.2 3.5 3.9M17.3 15.6c1.2 1.4 2.6 2 4.2 2"/>',
    "dual": '<path d="M9 4.5a4 4 0 0 0-4 4v7a4 4 0 0 0 4 4"/><path d="M15 4.5a4 4 0 0 1 4 4v7a4 4 0 0 1-4 4"/>'
            '<path d="M9 4.5c2 0 3 1.6 3 3.5v8c0 1.9 1 3.5 3 3.5M15 4.5c-2 0-3 1.6-3 3.5"/>',
    "dilate": '<path d="M7 7c2-3 8-3 10 0s3 8 0 10-8 3-10 0-3-8 0-10z"/>'
              '<path d="M12 8V2m-2 2 2-2 2 2M12 16v6m-2-2 2 2 2-2M8 12H2m2-2-2 2 2 2M16 12h6m-2-2 2 2-2 2"/>',
    "press": '<path d="M3 4.5h18M3 19.5h18M12 1.5v3"/><path d="M6 8.5c-2.5 1.5-2.5 5.5 0 7h12c2.5-1.5 2.5-5.5 0-7z"/>'
             '<path d="M8.5 12c1.2-1 2.3 1 3.5 0s2.3 1 3.5 0"/>',
}


@lru_cache(maxsize=None)
def tokens() -> dict:
    return json.loads((HERE / "tokens.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def icon_paths() -> dict:
    d = json.loads((HERE / "icons.json").read_text(encoding="utf-8"))
    d.update(EXTRA_ICONS)
    return d


def qcolor(value: str) -> QColor:
    m = _RGBA.fullmatch(value.strip())
    if m:
        r, g, b, a = m.groups()
        return QColor(int(float(r)), int(float(g)), int(float(b)), int(round(float(a if a else 1) * 255)))
    return QColor(value)


class Theme:
    def __init__(self, name: str = "dark"):
        self.name = name if name in tokens()["themes"] else "dark"
        self.t = dict(tokens()["themes"][self.name])

    @property
    def dark(self) -> bool:
        return self.name == "dark"

    def __getitem__(self, key: str) -> str:
        return self.t[key]

    def q(self, key: str) -> QColor:
        return qcolor(self.t[key])


_FONTS_LOADED = False


def load_fonts() -> None:
    global _FONTS_LOADED
    if _FONTS_LOADED:
        return
    for ttf in sorted((HERE / "fonts").glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(ttf))
    _FONTS_LOADED = True


def _weight(w) -> QFont.Weight:
    return w if isinstance(w, QFont.Weight) else QFont.Weight(int(w))


def mono(px: float = 12, weight=QFont.Normal) -> QFont:
    f = QFont("Geist Mono")
    f.setStyleHint(QFont.Monospace)
    f.setPixelSize(int(round(px)))
    f.setWeight(_weight(weight))
    return f


def sans(px: float = 13, weight=QFont.Normal) -> QFont:
    f = QFont("Geist")
    f.setPixelSize(int(round(px)))
    f.setWeight(_weight(weight))
    return f


def label_font(px: float = 10.5) -> QFont:
    """.ds-label: Geist Mono, uppercase, 0.14em tracking (letter-spacing lives on the font in Qt)."""
    f = mono(px, QFont.Medium)
    f.setLetterSpacing(QFont.AbsoluteSpacing, px * 0.14)
    return f


@lru_cache(maxsize=512)
def _icon_pixmap(name: str, color: str, size: int, stroke: float, dpr: float) -> QPixmap:
    body = icon_paths().get(name) or icon_paths()["file"]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    r = QSvgRenderer(QByteArray(svg.encode()))
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str | QColor = "#A9B6CF", size: int = 18, stroke: float = 1.5,
         active: str | QColor | None = None, disabled: str | QColor | None = None) -> QIcon:
    """A studio line icon tinted with `color` (and optional colours for the checked/active and disabled states)."""
    from PySide6.QtWidgets import QApplication
    dpr = QApplication.instance().devicePixelRatio() if QApplication.instance() else 1.0
    c = color.name(QColor.HexArgb) if isinstance(color, QColor) else color
    ic = QIcon(_icon_pixmap(name, c, size, stroke, max(dpr, 2.0)))
    if active is not None:
        a = active.name(QColor.HexArgb) if isinstance(active, QColor) else active
        pm = _icon_pixmap(name, a, size, stroke, max(dpr, 2.0))
        ic.addPixmap(pm, QIcon.Normal, QIcon.On)
        ic.addPixmap(pm, QIcon.Active, QIcon.Off)
    if disabled is not None:
        d = disabled.name(QColor.HexArgb) if isinstance(disabled, QColor) else disabled
        ic.addPixmap(_icon_pixmap(name, d, size, stroke, max(dpr, 2.0)), QIcon.Disabled)
    return ic


def make_logo(size: int = 256, theme: Theme | None = None) -> QPixmap:
    """The app icon: navy tile, registration-mark corners, the studio 'logo' glyph in cyan and white."""
    th = theme or Theme("dark")
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#0F1729"))
    p.drawRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 13 * s, 13 * s)
    p.setPen(QPen(QColor("#3D7BFF"), 2.6 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    for x, y, dx, dy in ((9, 9, 1, 1), (55, 9, -1, 1), (9, 55, 1, -1), (55, 55, -1, -1)):
        p.drawLine(QPointF(x * s, y * s), QPointF((x + 7 * dx) * s, y * s))
        p.drawLine(QPointF(x * s, y * s), QPointF(x * s, (y + 7 * dy) * s))
    # the studio "logo" glyph: an A of three plotted points
    pen = QPen(QColor("#E8EEF9"), 3.4 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    a, b, c = QPointF(32 * s, 17 * s), QPointF(18 * s, 46 * s), QPointF(46 * s, 46 * s)
    p.drawLine(b, a)
    p.drawLine(a, c)
    p.setPen(QPen(QColor("#5BD8FF"), 3.4 * s, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(24.2 * s, 35 * s), QPointF(39.8 * s, 35 * s))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#5BD8FF"))
    for pt in (a, b, c):
        p.drawEllipse(pt, 3.1 * s, 3.1 * s)
    p.end()
    return pm


def logo_icon() -> QIcon:
    ic = QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(make_logo(sz))
    return ic


ICON_SIZE = QSize(18, 18)
