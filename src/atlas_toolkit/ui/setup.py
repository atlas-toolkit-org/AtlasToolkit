"""
The Setup window (the gear button, Ctrl+, or "Setup" in the palette): the app's general options, its optional
components and its folders. A resizable, modeless window with tabs, built like the About window:

    General     theme and artwork at start, interface scale, what opens at start, quitting during a run, preview delay
    Outputs     when an output file exists (overwrite / ask / keep both), where a tool's outputs go until it has its
                own choice, heavy jobs at once, what happens when a run ends
    Components  the optional components (core/runtimes): status, which tools use them, why, how to install
    Presets     preset packs and every preset, yours and the packs' (ui/setup_presets.py)
    Storage     where the app keeps its files (Open buttons, cache size); clear the cache or the history, export,
                import or reset the options

Options take effect as they change and are saved at once (core/prefs, in app.json). Like the About window it
shows nothing particular to this computer, except a default output folder the person chose.
"""
from __future__ import annotations

import shutil
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
                               QTabWidget, QVBoxLayout, QWidget)

from ..core import prefs, runtimes
from ..core.paths import PORTABLE_DIR, cache_dir, config_dir, program_inside, reveal
from ..core.settings import ToolSettings
from ..version import APP_NAME
from .about import (_card, _logo, _page, _scroll, _text, _tile, display_path, folder_size, generic_locations,
                    human_size, portable, runtime_users, setup_commands)
from .theme import icon, mono, sans
from .widgets.common import badge, hint, label, set_badge
from .widgets.flow import FlowLayout
from .widgets.spin import IntSpin


class SetupDialog(QDialog):
    cache_measured = Signal(str)          # from the thread that adds up the cache folder
    TABS = ("general", "outputs", "components", "presets", "storage")

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.theme = ctx.theme
        self.setObjectName("setupDialog")
        self.setWindowTitle(f"Setup · {APP_NAME}")
        self.setWindowIcon(icon("gear", self.theme["highlight"], 32))
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowMinMaxButtonsHint
                            | Qt.WindowCloseButtonHint)
        self.setSizeGripEnabled(True)
        self.setMinimumSize(640, 460)
        self.resize(900, 680)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._header())
        self.tabs = QTabWidget(objectName="aboutTabs")
        self.tabs.setDocumentMode(True)
        ic = lambda name: icon(name, self.theme["text-3"], 16)   # noqa: E731
        self.tabs.addTab(self._general_tab(), ic("sliders"), "General")
        self.tabs.addTab(self._outputs_tab(), ic("save"), "Outputs")
        self.tabs.addTab(self._components_tab(), ic("layers"), "Components")
        self.tabs.addTab(self._presets_tab(), ic("tag"), "Presets")
        self.tabs.addTab(self._storage_tab(), ic("folder"), "Storage")
        self.tabs.setIconSize(QSize(16, 16))
        self.tabs.tabBar().setDrawBase(False)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(self._footer())

    def show_tab(self, key: str) -> None:
        if key in self.TABS:
            self.tabs.setCurrentIndex(self.TABS.index(key))

    # ------------------------------------------------------------------ frame
    def _header(self) -> QWidget:
        bar = QFrame(objectName="aboutHead")
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(28, 20, 24, 18)
        hl.setSpacing(16)
        hl.addWidget(_logo(48))
        col = QVBoxLayout()
        col.setSpacing(3)
        name = QLabel("SETUP")
        f = sans(17, 600)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 17 * 0.16)
        name.setFont(f)
        col.addWidget(name)
        col.addWidget(_text("How the toolkit looks, starts, writes its files and runs; its optional components and "
                            "folders.", "summary"))
        hl.addLayout(col, 1)
        return bar

    def _footer(self) -> QWidget:
        bar = QFrame(objectName="aboutFoot")
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(28, 10, 24, 12)
        foot = _text("Changes apply and are saved as you make them.", "hint", wrap=False)
        foot.setFont(mono(11))
        hl.addWidget(foot, 1)
        close = QPushButton("Close")
        close.setProperty("variant", "primary")
        close.setDefault(True)
        close.clicked.connect(self.close)
        hl.addWidget(close)
        return bar

    # ------------------------------------------------------------------ small parts
    @staticmethod
    def _option(grid: QGridLayout, title: str, tip: str, widget: QWidget) -> None:
        """One option on a card: its name and a line under it on the left, the control on the right."""
        r = grid.rowCount()
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(_text(title, "muted", wrap=False))
        if tip:
            col.addWidget(hint(tip))
        grid.addLayout(col, r, 0)
        grid.addWidget(widget, r, 1, Qt.AlignRight | Qt.AlignVCenter)

    @staticmethod
    def _grid() -> QGridLayout:
        g = QGridLayout()
        g.setHorizontalSpacing(24)
        g.setVerticalSpacing(14)
        g.setColumnStretch(0, 1)
        return g

    def _combo(self, key: str, choices: dict) -> QComboBox:
        c = QComboBox()
        c.setMinimumWidth(240)
        for value, text in choices.items():
            c.addItem(text, value)
        c.setCurrentIndex(max(0, c.findData(self.ctx.pref(key))))
        c.currentIndexChanged.connect(lambda _i, k=key, c=c: self._set(k, c.currentData()))
        return c

    def _check(self, key: str, text: str) -> QCheckBox:
        b = QCheckBox(text)
        b.setChecked(bool(self.ctx.pref(key)))
        b.toggled.connect(lambda on, k=key: self._set(k, bool(on)))
        return b

    def _set(self, key: str, value) -> None:
        self.ctx.set_pref(key, value)
        if key == "theme_start" and value != "last":              # see it now, not only at the next start
            name = self.ctx.start_theme()
            if name != self.ctx.theme.name:
                self.ctx.set_theme(name)                          # rebuilds this window for the theme
        elif key == "art_start" and value != "last" and value != self.ctx.art:
            self.ctx.set_art(value)

    # ------------------------------------------------------------------ General
    def _general_tab(self) -> QWidget:
        w, lay = _page()
        card, cl = _card("Appearance")
        g = self._grid()
        self.theme_combo = self._combo("theme_start", prefs.THEMES)
        self._option(g, "Theme at start", "The sun / moon button in the top bar still switches it any time.",
                     self.theme_combo)
        self._option(g, "Artwork at start", "Previews on paper or blueprint (the paper button in the top bar).",
                     self._combo("art_start", prefs.ARTS))
        scale = QComboBox()
        scale.setMinimumWidth(240)
        for s in prefs.UI_SCALES:
            scale.addItem(f"{s * 100:.0f} %", s)
        scale.setCurrentIndex(max(0, scale.findData(self.ctx.pref("ui_scale"))))
        scale.currentIndexChanged.connect(lambda _i: self._set("ui_scale", scale.currentData()))
        self._option(g, "Interface scale", "Text and controls larger or smaller. Applies the next time the app "
                                           "starts.", scale)
        cl.addLayout(g)
        lay.addWidget(card)

        card, cl = _card("Start and quit")
        g = self._grid()
        self._option(g, "Open at start", "Home, or the tool, workflow or page the app was closed on.",
                     self._combo("start_page", prefs.START_PAGES))
        self._option(g, "Quitting during a run", "", self._check("confirm_quit", "Ask before stopping it"))
        cl.addLayout(g)
        lay.addWidget(card)

        card, cl = _card("Preview")
        g = self._grid()
        delay = IntSpin()
        delay.setRange(*prefs.RANGES["preview_delay_ms"])
        delay.setSingleStep(50)
        delay.setSuffix(" ms")
        delay.setValue(self.ctx.pref("preview_delay_ms"))
        delay.valueChanged.connect(lambda v: self._set("preview_delay_ms", int(v)))
        self.delay_spin = delay
        self._option(g, "Preview delay", "The pause after a setting changes before the preview updates: longer "
                                         "for heavy tools on a slow computer.", delay)
        cl.addLayout(g)
        lay.addWidget(card)
        lay.addStretch(1)
        return _scroll(w)

    # ------------------------------------------------------------------ Outputs
    def _outputs_tab(self) -> QWidget:
        w, lay = _page()
        card, cl = _card("When an output file exists")
        cl.addWidget(hint("For every tool and workflow. Inputs are never overwritten."))
        example = f"sample_contours_{prefs.stamp()}.svg"
        tips = {"overwrite": "The new file replaces the old one. Quickest when you tune a design.",
                "ask": "Lists the files that exist before a run; you choose to overwrite them or not.",
                "suffix": f"The old file stays; the new one gets the run's date and time, e.g. <b>{example}</b>. "
                          "For random variations, new seeds and designs you want to compare."}
        self.existing = QButtonGroup(self)
        current = self.ctx.pref("on_existing")
        for value, text in prefs.ON_EXISTING.items():
            b = QRadioButton(text)
            b.setChecked(value == current)
            b.toggled.connect(lambda on, v=value: on and self._set("on_existing", v))
            self.existing.addButton(b)
            b._value = value
            cl.addWidget(b)
            t = hint(tips[value])
            t.setContentsMargins(24, 0, 0, 6)
            cl.addWidget(t)
        lay.addWidget(card)

        card, cl = _card("Where outputs go")
        cl.addWidget(hint("For a tool you have not run yet. Once you run it, each tool remembers its own choice "
                          "(on its page, under Output)."))
        self.out_next = QRadioButton("Next to each input")
        self.out_folder = QRadioButton("In one folder")
        grp = QButtonGroup(self)
        grp.addButton(self.out_next)
        grp.addButton(self.out_folder)
        folder_mode = self.ctx.pref("default_out_mode") == "folder"
        (self.out_folder if folder_mode else self.out_next).setChecked(True)
        cl.addWidget(self.out_next)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.out_folder)
        self.out_dir = QLineEdit(self.ctx.pref("default_out_dir"), readOnly=True, objectName="pathField")
        self.out_dir.setPlaceholderText("no folder chosen")
        row.addWidget(self.out_dir, 1)
        choose = QPushButton("Choose…")
        choose.setIcon(icon("folder", self.theme["text-2"], 14))
        choose.clicked.connect(lambda: self.choose_out_dir())
        row.addWidget(choose)
        cl.addLayout(row)
        self.out_next.toggled.connect(lambda on: on and self._set("default_out_mode", "next"))
        self.out_folder.toggled.connect(self._folder_mode)
        lay.addWidget(card)

        card, cl = _card("Running")
        g = self._grid()
        workers = IntSpin()
        workers.setRange(1, prefs.max_workers())
        workers.setValue(min(self.ctx.pref("workers"), prefs.max_workers()))
        workers.valueChanged.connect(lambda v: self._set("workers", int(v)))
        self.workers_spin = workers
        self._option(g, "Heavy jobs at once",
                     "Mesh and hatching runs, each in its own worker process. More finishes a batch sooner, "
                     f"but each one takes its own memory. 1 is the safest; up to {prefs.max_workers()} here.",
                     workers)
        cl.addLayout(g)
        lay.addWidget(card)

        card, cl = _card("When a run ends")
        cl.addWidget(self._check("reveal_after_run", "Show the first output in its folder"))
        cl.addWidget(self._check("finish_alert", "Flash the app in the taskbar when it is in the background"))
        cl.addWidget(self._check("finish_sound", "Play a sound"))
        lay.addWidget(card)
        lay.addStretch(1)
        return _scroll(w)

    def _folder_mode(self, on: bool) -> None:
        if not on:
            return
        if not self.ctx.pref("default_out_dir") and not self.choose_out_dir():
            self.out_next.setChecked(True)                       # no folder, no folder mode
            return
        self._set("default_out_mode", "folder")

    def choose_out_dir(self, path: str | None = None) -> bool:
        """Pick the default output folder (a dialog, or `path`); True when one was chosen."""
        if path is None:
            path = QFileDialog.getExistingDirectory(self, "Default output folder", self.ctx.pref("default_out_dir"))
        if not path:
            return False
        self.ctx.set_pref("default_out_dir", str(path))
        self.out_dir.setText(str(path))
        if not self.out_folder.isChecked():
            self.out_folder.setChecked(True)
        else:
            self._set("default_out_mode", "folder")
        return True

    # ------------------------------------------------------------------ Components
    def _components_tab(self) -> QWidget:
        w, lay = _page()
        lay.addWidget(_text(
            "The toolkit is self-contained: every tool runs without installing anything else. A few tools can do "
            "more with an optional component installed on this computer. Install one only when you use those "
            "tools; the app finds it by itself, and you can always point to it with <b>Locate</b>.", "summary"))
        self.rt_status: dict = {}
        for key, rt in runtimes.RUNTIMES.items():
            lay.addWidget(self._runtime_card(key, rt))
        lay.addStretch(1)
        return _scroll(w)

    def _runtime_card(self, key: str, rt) -> QWidget:
        card, cl = _card()
        cl.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(_text(rt.label, "h2", wrap=False))
        state = badge("")
        top.addWidget(state)
        top.addStretch(1)
        cl.addLayout(top)
        buttons = FlowLayout()                    # under the name, wrapping: long labels never widen the window
        locate = QPushButton(f"Locate {rt.label}…")
        locate.setIcon(icon("folder", self.theme["text-2"], 14))
        locate.clicked.connect(lambda _=False, k=key: self.locate(k))
        buttons.addWidget(locate)
        if rt.url:
            get = QPushButton(rt.get or f"Get {rt.label}")
            get.setIcon(icon("external", self.theme["text-2"], 14))
            get.setToolTip(rt.url)
            get.clicked.connect(lambda _=False, u=rt.url: self._open_url(u))
            buttons.addWidget(get)
        cl.addLayout(buttons)
        where = _text("", "muted")
        cl.addWidget(where)
        self.rt_status[key] = (state, where)
        self._refresh_runtime(key)

        need, can = runtime_users(self.ctx.registry, key)
        if need or can:
            cl.addWidget(label("When you need it"))
            for t in need:
                cl.addWidget(self._use_row(t, f"<b>{t.name}</b> needs it: the tool waits for it before it runs."))
            for t, what in can:
                cl.addWidget(self._use_row(t, f"<b>{t.name}</b> (optional): {what}."))
        if rt.why:
            cl.addWidget(label("Why"))
            cl.addWidget(_text(rt.why, "muted"))
        if rt.steps:
            cl.addWidget(label("How to install"))
            for i, step in enumerate(rt.steps, 1):
                row = QHBoxLayout()
                row.setSpacing(10)
                n = QLabel(str(i), objectName="stepNo")
                n.setFont(mono(11, QFont.Medium))
                n.setAlignment(Qt.AlignCenter)
                n.setFixedSize(22, 22)
                row.addWidget(n, 0, Qt.AlignTop)
                row.addWidget(_text(step, "muted"), 1)
                cl.addLayout(row)
        if rt.commands:
            text = setup_commands(rt)
            box = QPlainTextEdit(text, readOnly=True, objectName="commands")
            box.setFont(mono(11.5))
            box.setLineWrapMode(QPlainTextEdit.NoWrap)
            box.setFixedHeight(box.fontMetrics().lineSpacing() * (text.count("\n") + 1) + 30)
            cl.addWidget(box)
            copy = QPushButton("Copy commands")
            copy.setIcon(icon("copy", self.theme["text-2"], 14))
            copy.clicked.connect(lambda _=False, t=text, b=copy: (QGuiApplication.clipboard().setText(t),
                                                                  b.setText("Copied")))
            cl.addWidget(copy, 0, Qt.AlignLeft)
        if rt.notes:
            cl.addWidget(label("Good to know"))
            for note in rt.notes:
                row = QHBoxLayout()
                row.setSpacing(10)
                dot = QLabel("·", objectName="noteDot")
                dot.setFixedWidth(10)
                row.addWidget(dot, 0, Qt.AlignTop)
                row.addWidget(_text(note, "muted"), 1)
                cl.addLayout(row)
        return card

    def _use_row(self, tool, text: str) -> QWidget:
        row = QWidget(objectName="transparent")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)
        hl.addWidget(_tile(tool.icon, self.theme, 28), 0, Qt.AlignTop)
        hl.addWidget(_text(text, "muted"), 1)
        return row

    def _refresh_runtime(self, key: str) -> None:
        state, where = self.rt_status[key]
        found = runtimes.find(key)
        set_badge(state, "found" if found else "not installed", "success" if found else "")
        rt = runtimes.get(key)
        where.setText(f"Found: {display_path(found)}" if found else
                      f"Not found. Install it as below, or point to it with Locate {rt.label}.")
        where.setToolTip("" if not found else
                         "<app> is the folder of Atlas Toolkit; " +
                         ("%…% names a standard Windows folder." if sys.platform == "win32" else "~ is your home folder."))

    def locate(self, key: str, path: str | None = None) -> None:
        """Point at a runtime's program (a file dialog, or `path`); remembered in the app settings."""
        rt = runtimes.get(key)
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, f"Locate {rt.label}", "", rt.file_filter)
        if path:
            self.ctx.settings.set(rt.setting, program_inside(str(path)))
            self.ctx.settings.save()
            self._refresh_runtime(key)
            self.ctx.refresh_runtimes()

    def _open_url(self, url: str) -> None:
        from ..core.paths import open_url
        open_url(url)

    # ------------------------------------------------------------------ Storage
    def _storage_tab(self) -> QWidget:
        w, lay = _page()
        card, cl = _card("Where your files live")
        mode = (f"Portable mode: everything lives in the <b>{PORTABLE_DIR}</b> folder next to the app."
                if portable() else
                "Per user, in your profile. Put a folder named "
                f"<b>{PORTABLE_DIR}</b> next to the app to keep everything there instead (portable mode).")
        cl.addWidget(hint(mode))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        for r, (what, where, path) in enumerate(generic_locations(self.ctx)):
            grid.addWidget(_text(what, "muted", wrap=False), r, 0)
            p = QLineEdit(where, readOnly=True, objectName="pathField")
            p.setFont(mono(11.5))
            p.setCursorPosition(0)
            grid.addWidget(p, r, 1)
            if what == "Cache":                                    # its size, added up off the UI thread
                self.cache_size = _text("measuring…", "mono", wrap=False)
                self.cache_size.setToolTip("The cache: depth maps, 3D networks, stripe curves, workflow steps. "
                                           "Safe to delete; it fills again as tools need it.")
                grid.addWidget(self.cache_size, r, 2)
                self.cache_measured.connect(self.cache_size.setText)
                self._measure_cache(path)
            b = QPushButton("Open")
            b.setProperty("variant", "ghost")
            b.setIcon(icon("folder", self.theme["text-2"], 14))
            b.clicked.connect(lambda _=False, p=path: reveal(Path(p)))
            grid.addWidget(b, r, 3)
        grid.setColumnStretch(1, 1)
        cl.addLayout(grid)
        lay.addWidget(card)

        card, cl = _card("Housekeeping")
        g = self._grid()
        for title, tip, text, ic, fn in (
                ("Cache", "Intermediate files of every tool and workflow. They are made again when needed.",
                 "Clear cache", "trash", self.clear_cache),
                ("History", "The Activity page's past runs and the recent files list. Your outputs stay.",
                 "Clear history", "trash", self.clear_history),
                ("Settings", "Options, every tool's last settings and your own presets, as one file: to keep or "
                             "to move to another computer (preset packs are not included).", "Export…", "save",
                 self.export_settings),
                ("", "Take the settings of an exported file (tools already open take them at the next start).",
                 "Import…", "folder", self.import_settings),
                ("Options", "Every option on this window back to how the app comes. Tool settings, presets and "
                            "located components stay.", "Reset options", "reset", self.reset_options)):
            b = QPushButton(text)
            b.setIcon(icon(ic, self.theme["text-2"], 14))
            b.setMinimumWidth(150)
            b.clicked.connect(lambda _=False, fn=fn: fn())
            self._option(g, title, tip, b)
        cl.addLayout(g)
        lay.addWidget(card)
        lay.addStretch(1)
        return _scroll(w)

    def _presets_tab(self) -> QWidget:
        from .setup_presets import PresetsPanel
        w, lay = _page()
        self.presets_panel = PresetsPanel(self.ctx)
        lay.addWidget(self.presets_panel, 1)
        return _scroll(w)

    def _measure_cache(self, folder: Path) -> None:
        def work():
            size, files = folder_size(folder)
            try:
                self.cache_measured.emit(f"{human_size(size)} · {files:,} file{'s' if files != 1 else ''}"
                                         .replace(",", " ") if files else "empty")
            except RuntimeError:                                  # the window closed meanwhile
                pass
        threading.Thread(target=work, daemon=True).start()

    def _confirm(self, text: str, ask: bool) -> bool:
        return not ask or QMessageBox.question(self, "Setup", text) == QMessageBox.Yes

    def clear_cache(self, ask: bool = True) -> bool:
        if self.ctx.bridge.busy():
            self.ctx.toast("Wait for the runs to finish (or cancel them) before clearing the cache.", "warning")
            return False
        if not self._confirm("Delete every file in the cache? Tools make them again when they need them.", ask):
            return False
        root = cache_dir()
        for p in list(root.iterdir()):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
        self.cache_size.setText("measuring…")
        self._measure_cache(root)
        self.ctx.toast("Cache cleared.", "success")
        return True

    def clear_history(self, ask: bool = True) -> bool:
        if not self._confirm("Clear the run history (Activity) and the recent files? Output files stay.", ask):
            return False
        try:
            self.ctx.history.path.unlink(missing_ok=True)
        except OSError as e:
            self.ctx.toast(f"Could not clear the history: {e}", "danger")
            return False
        self.ctx.settings.set("recent", [])
        self.ctx.settings.save()
        self.ctx.history_changed.emit()
        self.ctx.toast("History cleared.", "success")
        return True

    def export_settings(self, path: str | None = None) -> bool:
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export settings", "atlas-toolkit-settings.json",
                                                  "Settings (*.json)")
        if not path:
            return False
        try:
            self.ctx.settings.save()
            n = prefs.export_settings(self.ctx.settings, Path(path), config_dir() / "tools")
        except OSError as e:
            self.ctx.toast(f"Could not export: {e}", "danger")
            return False
        self.ctx.toast(f"Settings exported (options and {n} tool{'s' if n != 1 else ''}).", "success", "Show",
                       lambda: reveal(Path(path)))
        return True

    def import_settings(self, path: str | None = None) -> bool:
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Import settings", "", "Settings (*.json)")
        if not path:
            return False
        try:
            n = prefs.import_settings(self.ctx.settings, Path(path), config_dir() / "tools")
        except (OSError, ValueError) as e:
            self.ctx.toast(f"Could not import: {e}", "danger")
            return False
        for store in self.ctx._tool_settings.values():          # tools not open yet read their file again
            store.data = ToolSettings(store.tool).data
        self.ctx.toast(f"Settings imported (options and {n} tool{'s' if n != 1 else ''}).", "success")
        self._reopen()
        return True

    def reset_options(self, ask: bool = True) -> bool:
        if not self._confirm("Set every option of the Setup window back to its default?", ask):
            return False
        prefs.reset(self.ctx.settings)
        self.ctx.settings.save()
        self.ctx.toast("Options reset.", "success")
        self._reopen()
        return True

    def _reopen(self) -> None:
        """After an import or a reset: tell the app every option changed and show the window afresh."""
        for key in prefs.DEFAULTS:
            self.ctx.prefs_changed.emit(key)
        tab = self.TABS[self.tabs.currentIndex()]
        geo = self.geometry()
        self.close()
        QTimer.singleShot(0, lambda: self.ctx.show_setup(tab).setGeometry(geo))
