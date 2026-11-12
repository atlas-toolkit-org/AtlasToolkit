"""
Qt stylesheet built from the design tokens. Widgets opt in through object names (#topbar, #sidebar, #inspector,
#card ...) and dynamic properties (tab, nav, seg, badge, variant ...), mirroring the ds-* classes of the web
design system (studio/design/components.css). Qt style sheets have no letter-spacing, so the uppercase mono
micro-labels get it from their font (theme.label_font).
"""
from __future__ import annotations

from . import Theme


def stylesheet(t: Theme) -> str:
    c = t.t
    dark = t.dark
    inset = c["bg-2"]                    # fields and tracks sit one step above their panel
    hover = c["bg-3"]
    return f"""
* {{ color: {c['text']}; outline: none; }}
QMainWindow, #root, #page {{ background: {c['bg']}; }}
QWidget#transparent {{ background: transparent; }}
QToolTip {{ background: {c['bg-3']}; color: {c['text']}; border: 1px solid {c['line-3']}; padding: 6px 8px;
           border-radius: 6px; }}

/* ---------------------------------------------------------------- top bar */
#topbar {{ background: {c['bg-1']}; border-bottom: 1px solid {c['line']}; }}
#brandName {{ color: {c['text']}; font-size: 13px; font-weight: 600; }}
#brandSub {{ color: {c['text-3']}; }}
QPushButton[tab="true"] {{ background: transparent; border: none; border-bottom: 2px solid transparent;
    border-radius: 0; padding: 0 12px; min-height: 58px; color: {c['text-3']}; font-weight: 500; }}
QPushButton[tab="true"]:hover {{ color: {c['text']}; }}
QPushButton[tab="true"]:checked {{ color: {c['text']}; background: transparent; border-bottom: 2px solid {c['accent']}; }}
#search {{ background: {inset}; border: 1px solid {c['line-2']}; border-radius: 14px; padding: 0 10px 0 6px;
    min-height: 36px; color: {c['text']}; selection-background-color: {c['accent']}; }}
#search:hover {{ border-color: {c['line-3']}; }}
#search:focus {{ border-color: {c['focus']}; }}
#kbd {{ color: {c['text-3']}; background: {c['bg-3']}; border: 1px solid {c['line-2']};
    border-bottom: 2px solid {c['line-3']}; border-radius: 4px; padding: 0 6px; min-height: 16px; max-height: 18px; }}

/* ---------------------------------------------------------------- side bar and navigation */
#sidebar {{ background: {c['bg-1']}; border-right: 1px solid {c['line']}; }}
#inspector {{ background: {c['bg-1']}; border-left: 1px solid {c['line']}; }}
QLabel[class="label"] {{ color: {c['text-3']}; }}
QLabel[class="hint"] {{ color: {c['text-3']}; font-size: 12px; }}
QLabel[class="muted"] {{ color: {c['text-2']}; }}
QLabel[class="title"] {{ color: {c['text']}; font-size: 26px; font-weight: 300; }}
QLabel[class="h2"] {{ color: {c['text']}; font-size: 16px; font-weight: 500; }}
QLabel[class="summary"] {{ color: {c['text-2']}; font-size: 13.5px; }}
QLabel[class="mono"] {{ color: {c['text-2']}; font-family: "Geist Mono"; font-size: 11.5px; }}
QLabel[class="error"] {{ color: {c['danger']}; }}
QPushButton[nav="true"] {{ background: transparent; border: none; border-left: 2px solid transparent;
    border-radius: 0; text-align: left; padding: 7px 10px 7px 14px; color: {c['text-2']}; min-height: 22px; }}
QPushButton[nav="true"]:hover {{ background: {c['bg-2']}; color: {c['text']}; }}
QPushButton[nav="true"]:checked {{ background: {c['accent-softer']}; color: {c['text']};
    border-left: 2px solid {c['highlight']}; }}
QPushButton[nav="true"][broken="true"] {{ color: {c['danger']}; }}
QPushButton[nav="true"][rail="true"] {{ text-align: center; padding: 8px 0; }}
QPushButton[navgroup="true"] {{ background: transparent; border: none; border-radius: 6px; padding: 0;
    min-height: 34px; }}
QPushButton[navgroup="true"]:hover {{ background: {c['bg-2']}; }}
QPushButton[navgroup="true"] QLabel#navGroupTitle {{ color: {c['text']}; }}
QPushButton[navgroup="true"][active="true"] QLabel#navGroupTitle {{ color: {c['highlight']}; }}
QPushButton[navgroup="true"] QLabel#navGroupCount {{ color: {c['text-3']}; }}

/* ---------------------------------------------------------------- buttons */
QPushButton, QToolButton {{ background: {c['bg-2']}; border: 1px solid {c['line-2']}; border-radius: 8px;
    padding: 6px 12px; color: {c['text']}; font-weight: 500; min-height: 20px; }}
QPushButton:hover, QToolButton:hover {{ background: {hover}; border-color: {c['line-3']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {c['bg-4']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {c['text-4']}; background: {c['bg-1']};
    border-color: {c['line']}; }}
QPushButton:checked, QToolButton:checked {{ background: {c['accent-soft']}; border-color: {c['accent']}; }}
QPushButton[variant="primary"] {{ background: {c['accent']}; border: 1px solid {c['accent']}; color: {c['on-accent']};
    padding: 9px 14px; font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {c['accent-hover']}; border-color: {c['accent-hover']}; }}
QPushButton[variant="primary"]:pressed {{ background: {c['accent-press']}; }}
QPushButton[variant="primary"]:disabled {{ background: {c['bg-3']}; border-color: {c['line']}; color: {c['text-4']}; }}
QPushButton[variant="danger"] {{ color: {c['danger']}; }}
QPushButton[variant="danger"]:hover {{ border-color: {c['danger']}; }}
QPushButton[variant="ghost"], QToolButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; }}
QPushButton[variant="ghost"]:hover, QToolButton[variant="ghost"]:hover {{ background: {c['bg-3']};
    border-color: {c['line-2']}; }}
QPushButton[variant="ghost"]:checked, QToolButton[variant="ghost"]:checked {{ background: {c['accent-soft']};
    border-color: {c['accent']}; }}
QToolButton[variant="icon"] {{ background: transparent; border: 1px solid transparent; border-radius: 8px;
    padding: 4px; min-width: 22px; min-height: 22px; }}
QToolButton[variant="icon"]:hover {{ background: {c['bg-3']}; border-color: {c['line-2']}; }}
QToolButton[variant="icon"]:checked {{ background: {c['accent-soft']}; border-color: {c['accent']}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}

/* segmented control (.ds-seg) */
#seg {{ background: {c['bg']}; border: 1px solid {c['line']}; border-radius: 9px; }}
QPushButton[seg="true"] {{ background: transparent; border: 1px solid transparent; border-radius: 7px;
    padding: 5px 8px; color: {c['text-2']}; font-weight: 500; min-height: 16px; }}
QPushButton[seg="true"]:hover {{ color: {c['text']}; background: {c['bg-2']}; }}
QPushButton[seg="true"]:checked {{ background: {c['bg-3']}; color: {c['text']}; border-color: {c['line-2']}; }}

/* ---------------------------------------------------------------- fields */
QDoubleSpinBox, QSpinBox, QLineEdit, QComboBox {{ background: {inset}; border: 1px solid {c['line-2']};
    border-radius: 7px; padding: 4px 8px; min-height: 20px; color: {c['text']};
    selection-background-color: {c['accent']}; selection-color: {c['on-accent']}; }}
QDoubleSpinBox, QSpinBox {{ font-family: "Geist Mono"; font-size: 12.5px; padding-right: 20px; }}
QDoubleSpinBox:hover, QSpinBox:hover, QLineEdit:hover, QComboBox:hover {{ border-color: {c['line-3']}; }}
QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus, QComboBox:focus {{ border-color: {c['focus']}; }}
QDoubleSpinBox:disabled, QSpinBox:disabled, QLineEdit:disabled, QComboBox:disabled {{ color: {c['text-4']};
    background: {c['bg-1']}; border-color: {c['line']}; }}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button, QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border; width: 18px; border: none; border-left: 1px solid {c['line']}; background: transparent; }}
QDoubleSpinBox::up-button, QSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 6px; }}
QDoubleSpinBox::down-button, QSpinBox::down-button {{ subcontrol-position: bottom right;
    border-bottom-right-radius: 6px; }}
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover, QSpinBox::up-button:hover,
QSpinBox::down-button:hover {{ background: {c['bg-3']}; }}
QDoubleSpinBox::up-button:pressed, QDoubleSpinBox::down-button:pressed, QSpinBox::up-button:pressed,
QSpinBox::down-button:pressed {{ background: {c['accent-soft']}; }}
QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow, QSpinBox::up-arrow, QSpinBox::down-arrow {{
    image: none; width: 0; height: 0; }}
QDoubleSpinBox[auto="true"], QSpinBox[auto="true"] {{ color: {c['highlight']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: none; width: 0; }}
QComboBox QAbstractItemView {{ background: {c['bg-2']}; border: 1px solid {c['line-2']}; padding: 4px;
    selection-background-color: {c['bg-4']}; selection-color: {c['text']}; outline: none; }}
QCheckBox, QRadioButton {{ color: {c['text-2']}; spacing: 8px; }}
QCheckBox:hover, QRadioButton:hover {{ color: {c['text']}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {c['line-3']};
    background: {inset}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {c['bg-1']}; border-color: {c['line']}; }}
QSlider::groove:horizontal {{ height: 4px; background: {c['bg-4']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['text'] if dark else '#FFFFFF'}; border: 2px solid {c['accent']};
    width: 10px; height: 10px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ border-color: {c['highlight']}; }}

/* ---------------------------------------------------------------- inspector sections */
QToolButton[section="true"] {{ background: transparent; border: none; border-radius: 0; padding: 6px 0 4px 0;
    color: {c['text-3']}; text-align: left; font-weight: 500; }}
QToolButton[section="true"]:hover {{ color: {c['text']}; }}
#hairline {{ background: {c['line']}; max-height: 1px; min-height: 1px; border: none; }}

/* ---------------------------------------------------------------- tabs */
QTabWidget::pane {{ border: none; background: transparent; top: 0; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {c['text-3']}; padding: 7px 12px 8px 12px; border: none;
    border-bottom: 2px solid transparent; font-weight: 500; margin-right: 2px; }}
QTabBar::tab:hover {{ color: {c['text']}; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; }}
QTabBar::tab:disabled {{ color: {c['text-4']}; }}

/* ---------------------------------------------------------------- lists, tables, text */
QTreeWidget, QTreeView, QTableView, QListWidget {{ background: {c['bg-1']}; border: 1px solid {c['line']};
    border-radius: 12px; outline: none; alternate-background-color: {c['bg-1']}; }}
QTreeWidget::item, QTreeView::item {{ min-height: 30px; border-bottom: 1px solid {c['line']}; padding-left: 4px; }}
QTreeWidget::item:hover, QTreeView::item:hover, QListWidget::item:hover {{ background: {c['bg-2']}; }}
QTreeWidget::item:selected, QTreeView::item:selected, QListWidget::item:selected {{
    background: {c['accent-soft']}; color: {c['text']}; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
QHeaderView::section {{ background: {c['bg-1']}; color: {c['text-3']}; border: none;
    border-bottom: 1px solid {c['line-2']}; padding: 7px 8px; font-family: "Geist Mono"; font-size: 10.5px; }}
QHeaderView {{ background: transparent; border: none; }}
QPlainTextEdit, QTextBrowser {{ background: {c['bg-1']}; border: 1px solid {c['line']}; border-radius: 12px;
    padding: 8px; font-family: "Geist Mono"; font-size: 12px; color: {c['text-2']};
    selection-background-color: {c['accent']}; selection-color: {c['on-accent']}; }}
QTextBrowser {{ font-family: "Geist"; font-size: 13px; }}

/* ---------------------------------------------------------------- surfaces */
#card {{ background: {c['bg-1']}; border: 1px solid {c['line']}; border-radius: 14px; }}
QLabel[class="h3"] {{ color: {c['text']}; font-size: 14px; font-weight: 500; }}
QDialog#aboutDialog, QDialog#setupDialog {{ background: {c['bg']}; }}
#aboutHead {{ background: {c['bg-1']}; border-bottom: 1px solid {c['line']}; }}
#aboutFoot {{ background: {c['bg-1']}; border-top: 1px solid {c['line']}; }}
#aboutTabs QTabBar {{ background: {c['bg-1']}; }}
#aboutTabs QTabBar::tab {{ padding: 10px 16px 11px 14px; }}
#toolRow {{ background: transparent; border: 1px solid transparent; border-radius: 10px; }}
#toolRow:hover {{ background: {c['bg-1']}; border-color: {c['line']}; }}
#iconTile {{ background: {c['accent-softer']}; border: 1px solid {c['line-2']}; border-radius: 9px; }}
#releaseList {{ background: {c['bg-1']}; border: 1px solid {c['line']}; border-radius: 12px; padding: 6px; }}
#releaseList::item {{ padding: 0; margin: 1px 0; color: {c['text-2']}; }}
#releaseSplit::handle {{ background: transparent; }}
#stepNo {{ background: {c['accent-soft']}; color: {c['accent-text']}; border-radius: 11px; }}
#noteDot {{ color: {c['highlight']}; font-weight: 700; }}
#commands {{ background: {c['bg-2']}; color: {c['text']}; border: 1px solid {c['line-2']}; border-radius: 10px;
    padding: 8px 10px; }}
#pathField {{ background: {c['bg-2']}; border: 1px solid {c['line']}; color: {c['text-2']}; min-height: 26px;
    padding: 0 8px; }}
#releaseList::item:selected {{ background: {c['accent-softer']}; color: {c['text']}; }}
#releaseNotes {{ padding: 14px 18px; color: {c['text-2']}; }}
#card[hover="true"] {{ background: {c['bg-2']}; border-color: {c['line-3']}; }}
#panel {{ background: {c['bg-1']}; border: 1px solid {c['line']}; border-radius: 12px; }}
#stat {{ background: {c['bg-1']}; border: 1px solid {c['line']}; border-radius: 10px; }}
#statValue {{ font-family: "Geist Mono"; font-size: 15px; color: {c['text']}; }}
#statValue[tone="warning"] {{ color: {c['warning']}; }}
#statValue[tone="danger"] {{ color: {c['danger']}; }}
#statValue[tone="success"] {{ color: {c['success']}; }}
#statBefore {{ font-family: "Geist Mono"; font-size: 11px; color: {c['text-3']}; }}
#drop {{ border: 1px dashed {c['line-3']}; border-radius: 12px; color: {c['text-3']}; font-size: 14px;
    background: {c['bg-1']}; }}
#drop[active="true"] {{ border-color: {c['highlight']}; color: {c['highlight']}; background: {c['highlight-soft']}; }}
#stage {{ background: {c['bg-1']}; border: 1px solid {c['line']}; border-radius: 12px; }}
#footer {{ background: {c['bg-1']}; border-top: 1px solid {c['line']}; }}
#status {{ font-family: "Geist Mono"; font-size: 11.5px; color: {c['text-3']}; }}
#toast {{ background: {c['bg-3']}; border: 1px solid {c['line-3']}; border-radius: 14px; }}
#palette {{ background: {c['bg-2']}; border: 1px solid {c['line-3']}; border-radius: 14px; }}
#palette QListWidget {{ background: transparent; border: none; }}
#palette QLineEdit {{ background: {c['bg-1']}; font-size: 15px; min-height: 30px; border-radius: 10px; }}

/* badges (.ds-badge) */
QLabel[badge] {{ font-family: "Geist Mono"; font-size: 10.5px; border-radius: 4px; padding: 1px 6px;
    border: 1px solid {c['line-2']}; color: {c['text-2']}; background: transparent; }}
QLabel[badge="accent"] {{ color: {c['accent-text']}; border-color: {c['accent']}; background: {c['accent-soft']}; }}
QLabel[badge="live"] {{ color: {c['highlight']}; border-color: {c['highlight']}; background: {c['highlight-soft']}; }}
QLabel[badge="success"] {{ color: {c['success']}; border-color: {c['success']}; }}
QLabel[badge="warning"] {{ color: {c['warning']}; border-color: {c['warning']}; }}
QLabel[badge="danger"] {{ color: {c['danger']}; border-color: {c['danger']}; }}

/* ---------------------------------------------------------------- chrome */
QSplitter::handle {{ background: {c['line']}; }}
QSplitter::handle:hover {{ background: {c['line-3']}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['line-2']}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {c['line-3']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['line-2']}; border-radius: 4px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {c['line-3']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QProgressBar {{ background: {c['bg-3']}; border: none; border-radius: 2px; max-height: 4px; min-height: 4px; }}
QProgressBar::chunk {{ background: {c['highlight']}; border-radius: 2px; }}
QMenu {{ background: {c['bg-2']}; border: 1px solid {c['line-2']}; padding: 4px; border-radius: 8px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; color: {c['text']}; }}
QMenu::item:selected {{ background: {c['bg-4']}; }}
QMenu::item:disabled {{ color: {c['text-4']}; }}
QMenu::separator {{ height: 1px; background: {c['line-2']}; margin: 4px 6px; }}
QMenu::icon {{ padding-left: 6px; }}
/* ---------------------------------------------------------------- workflows */
#library {{ background: {c['bg-1']}; border-right: 1px solid {c['line']}; }}
#libraryTree {{ background: transparent; border: none; }}
#libraryTree::item {{ min-height: 28px; border-bottom: none; border-radius: 6px; }}
#borrowBanner {{ background: {c['highlight-soft']}; border: 1px solid {c['highlight']}; border-radius: 10px; }}
#warnBanner {{ background: {c['bg-1']}; border: 1px solid {c['warning']}; border-radius: 10px; }}
#warnBanner QLabel {{ color: {c['text']}; }}
#optNote {{ background: transparent; border: 1px dashed {c['line-2']}; border-radius: 10px; }}
QPushButton[nav="true"][unavailable="true"] {{ color: {c['text-4']}; }}
QPushButton[nav="true"][unavailable="true"]:hover {{ color: {c['text-3']}; }}
#card[unavailable="true"] QLabel, #toolRow[unavailable="true"] QLabel {{ color: {c['text-3']}; }}
#card[unavailable="true"] QLabel[badge="warning"], #toolRow[unavailable="true"] QLabel[badge="warning"] {{
    color: {c['warning']}; }}
#borrowBanner QLabel {{ color: {c['text']}; }}
QLabel[marked="true"] {{ color: {c['highlight']}; }}
QWidget[marked="true"] QCheckBox {{ color: {c['highlight']}; }}
QMessageBox, QDialog {{ background: {c['bg-1']}; }}
QInputDialog QLineEdit {{ min-width: 260px; }}
"""
