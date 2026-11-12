"""
The main window, laid out like the Atlas gallery:

    top bar   brand · Home / Tools / Activity tabs · search & command palette · jobs badge · art mode · theme · setup
              · help
    sidebar   tools by category (cyan bar on the active one), groups fold; full or icons only (]), hidden with [
    page      Home | a tool page | Activity
    footer    status · progress · log drawer toggle
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..core.paths import config_dir, reveal
from ..core.runtimes import RUNTIMES
from ..version import APP_NAME, __version__
from .about import AboutDialog
from .activity import ActivityPage
from .home import HomePage
from .palette import Palette
from .setup import SetupDialog
from .sidebar import Sidebar
from .keys import shown
from .theme import icon, label_font, logo_icon, make_logo, mono, sans
from .theme.qss import stylesheet
from .tool_page import ToolPage
from .workflow import WorkflowPage
from .widgets.common import Toaster, apply_icon, badge, icon_button, label, set_badge

SHORTCUTS = [
    ("/  or  Ctrl+K", "Search tools and actions"),
    ("Ctrl+O", "Add files to the current tool"),
    ("Ctrl+Enter", "Run the current tool"),
    ("Esc", "Cancel the current run / close the palette"),
    ("[", "Show or hide the tool list (on Workflows, the step list)"),
    ("]", "Tool list: icons only or names"),
    ("Ctrl+L", "Show or hide the log"),
    ("Ctrl+,", "Setup: theme, outputs, components, storage"),
    ("Up / Down  or  the triangles", "Number field: step  (Shift × 10, Ctrl ÷ 10)"),
    ("Ctrl+1 / Ctrl+2 / Ctrl+3", "Home / Activity / Workflows"),
    ("Tab · Del · P · L · F", "Workflows: add a step · delete · preview · lay out · fit"),
    ("Ctrl+S · Ctrl+Z / Ctrl+Y", "Workflows: save · undo / redo"),
    ("Alt+1 … Alt+9", "Open the n-th tool"),
    ("0  or double-click", "Fit the preview"),
    ("Wheel / drag", "Zoom / pan the preview"),
]


def build_menu_bar(win, bar) -> None:
    """macOS's global menu. People look there for About, Settings and Quit, and Qt moves them to the app menu by their
    roles. Other systems have no menu bar: the window's header holds all of this."""
    def action(menu, text, slot, role=QAction.MenuRole.NoRole, shortcut=None):
        act = QAction(text, win)
        act.setMenuRole(role)
        if shortcut is not None:
            act.setShortcut(shortcut)
        act.triggered.connect(lambda _checked=False: slot())
        menu.addAction(act)
        return act

    app_menu = bar.addMenu("File")
    action(app_menu, f"About {APP_NAME}", lambda: win.show_help("about"), QAction.MenuRole.AboutRole)
    action(app_menu, "Setup…", win.show_setup, QAction.MenuRole.PreferencesRole, "Ctrl+,")
    action(app_menu, f"Quit {APP_NAME}", win.close, QAction.MenuRole.QuitRole, QKeySequence.StandardKey.Quit)
    action(app_menu, "Add Files…", lambda: win._page_call("pick_files"))
    action(app_menu, "Close Window", win.close, shortcut=QKeySequence.StandardKey.Close)
    view = bar.addMenu("View")
    action(view, "Home", win.show_home)
    action(view, "Activity", win.show_activity)
    action(view, "Workflows", win.open_workflow)
    bar.addMenu("Help").addAction(f"{APP_NAME} Help", lambda: win.show_help("about"))


class MainWindow(QMainWindow):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.pages: dict[str, ToolPage] = {}
        self.nav: dict[str, QPushButton] = {}
        self.current_tool: str | None = None
        self.about: AboutDialog | None = None
        self.setup: SetupDialog | None = None
        self._start_page = ctx.settings.get("last_page", "home")   # before show_home() below overwrites it
        self._sidebar_on = bool(ctx.settings.get("sidebar_on", True))
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(logo_icon())
        self.setAcceptDrops(True)
        self.resize(1440, 900)
        self.setMinimumSize(1180, 700)
        ctx.toast = self.toast
        ctx.open_tool = self.open_tool
        ctx.open_workflow = self.open_workflow
        ctx.show_help = self.show_help
        ctx.show_setup = self.show_setup
        ctx.run_finished = self.run_finished
        ctx.status = self.set_status
        ctx.log = self.log
        self.toaster = None
        self._build()
        self.toaster = Toaster(self.centralWidget())
        ctx.bridge.activity.connect(self._activity)
        ctx.bridge.progress.connect(self._progress)
        ctx.theme_changed.connect(self._retheme)
        ctx.art_changed.connect(self._reart)
        ctx.runtimes_changed.connect(self._runtimes_changed)
        ctx.prefs_changed.connect(self._pref_changed)
        self._shortcuts()
        self._retheme(ctx.theme)
        self.show_home()
        geo = ctx.settings.get("geometry")
        if geo:
            try:
                self.restoreGeometry(bytes.fromhex(geo))
            except ValueError:
                pass

    # ================================================================== layout
    def _build(self) -> None:
        root = QWidget(objectName="root")
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.setCentralWidget(root)
        lay.addWidget(self._topbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = Sidebar(self.ctx)
        self.sidebar.open_tool.connect(self.open_tool)
        self.nav = self.sidebar.nav
        body.addWidget(self.sidebar)
        self.vsplit = QSplitter(Qt.Vertical)
        self.vsplit.setChildrenCollapsible(True)
        self.vsplit.setHandleWidth(1)
        self.stack = QStackedWidget()
        self.home = HomePage(self.ctx)
        self.activity = ActivityPage(self.ctx)
        self.workflows = WorkflowPage(self.ctx)
        self.workflows.saved_changed.connect(self._palette_entries)
        self.workflows.saved_changed.connect(self.home.refresh_workflows)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.activity)
        self.stack.addWidget(self.workflows)
        self.vsplit.addWidget(self.stack)
        self.logbox = QPlainTextEdit(readOnly=True)
        self.logbox.setMaximumBlockCount(10000)
        self.logbox.setPlaceholderText("Messages from every tool appear here.")
        self.vsplit.addWidget(self.logbox)
        self.vsplit.setSizes([800, 0])
        bw = QWidget()
        bw.setLayout(body)
        body.addWidget(self.vsplit, 1)
        lay.addWidget(bw, 1)
        lay.addWidget(self._footer())
        self.palette = Palette(root, self.search, self.ctx.theme)
        self._palette_entries()

    def _topbar(self) -> QWidget:
        bar = QFrame(objectName="topbar")
        bar.setFixedHeight(60)
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(18, 0, 16, 0)
        hl.setSpacing(10)
        self.btn_side = icon_button("sidebar", "Show or hide the tool list  ( [ )", None)
        self.btn_side.clicked.connect(self.toggle_sidebar)
        hl.addWidget(self.btn_side)
        logo = QLabel()
        logo.setPixmap(make_logo(68).scaled(34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        hl.addWidget(logo)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        brand.setContentsMargins(0, 0, 0, 0)
        name = QLabel("ATLAS")
        name.setObjectName("brandName")
        f = sans(13, 600)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 13 * 0.22)
        name.setFont(f)
        sub = QLabel(f"PEN-PLOTTER TOOLKIT · V{__version__}")
        sub.setObjectName("brandSub")
        sub.setFont(label_font(9.5))
        brand.addStretch(1)
        brand.addWidget(name)
        brand.addWidget(sub)
        brand.addStretch(1)
        hl.addLayout(brand)
        hl.addSpacing(22)
        self.tabs = {}
        grp = QButtonGroup(self)
        for key, text, ic in (("home", "Home", "grid"), ("tools", "Tools", "process"),
                              ("workflows", "Workflows", "pipeline"), ("activity", "Activity", "activity")):
            b = QPushButton(text, checkable=True)
            b.setProperty("tab", "true")
            b.setIconSize(QSize(16, 16))
            b._icon_name = ic
            b.setCursor(Qt.PointingHandCursor)
            grp.addButton(b)
            hl.addWidget(b)
            self.tabs[key] = b
        self.tabs["home"].clicked.connect(self.show_home)
        self.tabs["tools"].clicked.connect(lambda: self.open_tool(self.current_tool or self.ctx.registry.ordered()[0].id))
        self.tabs["activity"].clicked.connect(self.show_activity)
        self.tabs["workflows"].clicked.connect(lambda: self.open_workflow())
        hl.addSpacing(16)
        sw = QWidget()
        sl = QHBoxLayout(sw)
        sl.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search tools and actions   ( / )")
        self.search.setMinimumWidth(260)
        self.search.setMaximumWidth(520)
        self.search_icon = QAction(self.search)
        self.search.addAction(self.search_icon, QLineEdit.LeadingPosition)
        sl.addWidget(self.search, 1)
        hl.addWidget(sw, 1)
        hl.addStretch(0)
        self.jobs_badge = badge("idle")
        self.jobs_badge.setToolTip("Jobs running and queued")
        hl.addWidget(self.jobs_badge)
        self.btn_art = icon_button("paper", "Artwork on paper or blueprint", None, checkable=True)
        self.btn_art.setChecked(self.ctx.art == "blueprint")
        self.btn_art.toggled.connect(lambda on: self.ctx.set_art("blueprint" if on else "paper"))
        self.btn_theme = icon_button("moon", "Dark or light theme", None)
        self.btn_theme.clicked.connect(lambda: self.ctx.set_theme("light" if self.ctx.theme.dark else "dark"))
        self.btn_setup = icon_button("gear", shown("Setup: theme, outputs, components, storage  (Ctrl+,)"), None)
        self.btn_setup.clicked.connect(lambda: self.show_setup())
        self.btn_help = icon_button("info", "About: tools, shortcuts, changelog, credits", None)
        self.btn_help.clicked.connect(self.show_help)
        for b in (self.btn_art, self.btn_theme, self.btn_setup, self.btn_help):
            hl.addWidget(b)
        return bar

    def _footer(self) -> QWidget:
        foot = QFrame(objectName="footer")
        foot.setFixedHeight(32)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(18, 0, 12, 0)
        fl.setSpacing(10)
        self.status = QLabel("Ready.", objectName="status")
        self.status.setFont(mono(11.5))
        fl.addWidget(self.status, 1)
        self.progress = QProgressBar(textVisible=False)
        self.progress.setFixedWidth(200)
        self.progress.hide()
        fl.addWidget(self.progress)
        self.btn_log = QPushButton("Log")
        self.btn_log.setProperty("variant", "ghost")
        self.btn_log.setCheckable(True)
        self.btn_log.setIconSize(QSize(14, 14))
        self.btn_log._icon_name = "log"
        self.btn_log.toggled.connect(self.toggle_log)
        fl.addWidget(self.btn_log)
        self.warn_count = 0
        return foot

    def _palette_entries(self) -> None:
        e = []
        for t in self.ctx.registry.ordered():
            needs = self.ctx.needs_short(t)
            e.append((t.name, f"{t.category_title} · {t.io_badge}" + (f" · ⚠ {needs} (not found)" if needs else ""),
                      t.icon, lambda tid=t.id: self.open_tool(tid)))
        e += [
            ("Home", "overview and drop zone", "grid", self.show_home),
            ("Workflows", "chain tools into a pipeline", "pipeline", lambda: self.open_workflow()),
            ("New workflow", "an empty pipeline", "plus", lambda: self.open_workflow().new_workflow()),
            ("Clear workflow cache", "intermediate files of workflow runs", "trash", self.workflows.clear_cache),
            ("Activity", "past runs", "activity", self.show_activity),
            ("Toggle dark / light theme", "appearance", "moon",
             lambda: self.ctx.set_theme("light" if self.ctx.theme.dark else "dark")),
            ("Toggle paper / blueprint", "appearance", "blueprint", lambda: self.btn_art.toggle()),
            ("Show log", "messages from every run", "log", lambda: self.btn_log.setChecked(True)),
            ("Show or hide the tool list", "side bar", "sidebar", self.toggle_sidebar),
            ("Tool list: icons only or names", "side bar", "sidebar", self.toggle_rail),
            ("Fold or unfold every tool group", "side bar", "layers", self._fold_all),
            ("Open plug-ins folder", "add tools without rebuilding", "folder",
             lambda: reveal(config_dir() / "plugins")),
            ("Open settings folder", "settings, presets, history", "folder", lambda: reveal(config_dir())),
            ("Setup", "theme, start-up, outputs, jobs, components, presets, storage", "gear",
             lambda: self.show_setup()),
            ("When an output exists", "overwrite, ask, or keep both (date-time suffix)", "gear",
             lambda: self.show_setup("outputs")),
            ("Clear cache, history or settings", "storage and housekeeping", "trash",
             lambda: self.show_setup("storage")),
            ("Manage presets", "your presets and preset packs: load, save to a file, rename, delete, hide", "tag",
             lambda: self.show_setup("presets")),
            ("Load presets or a preset pack", ".atlaspreset, .atlaspack or a .zip holding them", "folder",
             lambda: self.show_setup("presets").presets_panel.import_files()),
            ("About Atlas Toolkit", "version, system, diagnostics", "info", self.show_help),
            ("Keyboard shortcuts", "help", "info", lambda: self.show_help("shortcuts")),
            ("Changelog", "what's new in each version", "clock", lambda: self.show_help("changelog")),
            ("All tools", "every tool with its icon and summary", "process", lambda: self.show_help("tools")),
            ("Credits", "who made it, open-source parts", "star", lambda: self.show_help("credits")),
            ("Command line", "run tools from a terminal: commands, options, examples", "code",
             lambda: self.show_help("cli")),
            ("Optional components", "set up " + ", ".join(rt.label for rt in RUNTIMES.values()), "layers",
             lambda: self.show_setup("components")),
        ]
        e += [(p.stem, "open workflow", "pipeline", lambda p=p: self.open_workflow(p))
              for p in self.workflows.saved_workflows()[:20]]
        self.palette.set_entries(e)

    def _shortcuts(self) -> None:
        def sc(seq, fn):
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.WindowShortcut)
            s.activated.connect(fn)
            return s
        sc("Ctrl+K", self.palette.open)
        sc("/", self._slash)
        sc("[", self._bracket)
        sc("]", lambda: None if self._typing() else self.toggle_rail())
        sc("Ctrl+L", lambda: self.btn_log.toggle())
        if sys.platform == "darwin":
            build_menu_bar(self, self.menuBar())     # it has the Setup shortcut there: two would cancel each other
        else:
            sc("Ctrl+,", lambda: self.show_setup())
        sc("Ctrl+1", self.show_home)
        sc("Ctrl+2", self.show_activity)
        sc("Ctrl+3", lambda: self.open_workflow())
        sc("Ctrl+O", lambda: self._page_call("pick_files"))
        sc("Ctrl+Return", lambda: self._page_call("run_all"))
        sc("Ctrl+Enter", lambda: self._page_call("run_all"))
        sc("Escape", lambda: self.palette.close_palette() if self.palette.isVisible() else self._page_call("cancel"))
        for i, t in enumerate(self.ctx.registry.ordered()[:9], start=1):
            sc(f"Alt+{i}", lambda tid=t.id: self.open_tool(tid))

    def _typing(self) -> bool:
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QPlainTextEdit)) or (w is not None and w.inherits("QAbstractSpinBox"))

    def _slash(self) -> None:
        if not self._typing():
            self.palette.open()

    def _bracket(self) -> None:
        if not self._typing():
            self.toggle_sidebar()

    def toggle_sidebar(self) -> None:
        """Show or hide the tool list; on Workflows, the step list that takes its place."""
        if self.stack.currentWidget() is self.workflows:
            self.workflows.toggle_library()
            return
        self._sidebar_on = self.sidebar.isHidden()
        self.sidebar.setVisible(self._sidebar_on)
        self.ctx.settings.set("sidebar_on", self._sidebar_on)

    def toggle_rail(self) -> None:
        """Icons only or names, showing the list if it was hidden."""
        if self.stack.currentWidget() is self.workflows:
            self.workflows.toggle_library_rail()
            return
        self.sidebar.set_mode("full" if self.sidebar.rail else "icons")
        if self.sidebar.isHidden():
            self.toggle_sidebar()

    def _fold_all(self) -> None:
        if self.stack.currentWidget() is self.workflows:
            self.workflows.library.fold_all()
        else:
            self.sidebar.fold_all()

    def _page_call(self, name: str) -> None:
        fn = getattr(self.stack.currentWidget(), name, None)
        if callable(fn):
            fn()

    # ================================================================== navigation
    def _select_tab(self, key: str) -> None:
        for k, b in self.tabs.items():
            b.setChecked(k == key)

    def _leave_workflows(self) -> None:
        self.sidebar.setVisible(self._sidebar_on)

    def show_home(self) -> None:
        self._leave_workflows()
        self.stack.setCurrentWidget(self.home)
        self._select_tab("home")
        self.sidebar.set_current(None)
        self.ctx.settings.set("last_page", "home")
        self.set_status("Ready.")

    def show_activity(self) -> None:
        self._leave_workflows()
        self.activity.reload()
        self.stack.setCurrentWidget(self.activity)
        self._select_tab("activity")
        self.sidebar.set_current(None)
        self.ctx.settings.set("last_page", "activity")

    def page(self, tool_id: str) -> ToolPage:
        if tool_id not in self.pages:
            p = ToolPage(self.ctx.registry.get(tool_id), self.ctx)
            p.send_to.connect(lambda tid, paths: self.open_tool(tid, paths))
            p.set_art(self.ctx.art)
            self.pages[tool_id] = p
            self.stack.addWidget(p)
        return self.pages[tool_id]

    def open_tool(self, tool_id: str, paths: list | None = None, values: dict | None = None) -> ToolPage | None:
        if tool_id not in self.ctx.registry.tools:
            self.toast(f"Unknown tool {tool_id}.", "danger")
            return None
        p = self.page(tool_id)
        self._leave_workflows()
        self.stack.setCurrentWidget(p)
        self.current_tool = tool_id
        self.ctx.settings.set("last_tool", tool_id)
        self.ctx.settings.set("last_page", "tool")
        self._select_tab("tools")
        self.sidebar.set_current(tool_id)
        if paths:
            p.add_paths(paths, values)
        elif values:
            p.form.set_values(p.tool.values(values))
            p._settings_changed()
        self.set_status(p.status_text())
        return p

    def open_workflow(self, path=None, paths: list | None = None) -> WorkflowPage:
        """Show the Workflows page; open a saved workflow and add files to run on, when given."""
        self.sidebar.hide()                      # the page's own step library takes its place
        self.stack.setCurrentWidget(self.workflows)
        self._select_tab("workflows")
        self.sidebar.set_current(None)
        self.ctx.settings.set("last_page", "workflows")
        if path:
            self.workflows.open_path(path)
        if paths:
            self.workflows.add_paths(paths)
        self.set_status(self.workflows.status_text())
        return self.workflows

    def restore_last_page(self) -> None:
        """Setup › General › "Open at start": the tool, workflow or page the app was closed on."""
        page, tool = self._start_page, self.ctx.settings.get("last_tool")
        if page == "tool" and tool in self.ctx.registry.tools:
            self.open_tool(tool)
        elif page == "workflows":
            self.open_workflow()              # the page reopens its last workflow by itself
        elif page == "activity":
            self.show_activity()

    # ================================================================== drops
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def import_preset_files(self, paths: list, ask: bool = True) -> int:
        """Load dropped or opened preset files, packs or .zip files holding them, after showing what they hold
        (ui/preset_install.py); returns how many packs and preset files were installed."""
        from .preset_install import install_files
        plan = install_files(self, self.ctx.presets, paths, self.toast, registry=self.ctx.registry, ask=ask)
        return sum(1 for i in plan.items if i.done) if plan else 0

    def dropEvent(self, e):
        from .app import is_preset_file
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        page = self.stack.currentWidget()
        preset_files = [p for p in paths if is_preset_file(p)]
        if preset_files:
            self.import_preset_files(preset_files)
            paths = [p for p in paths if p not in preset_files]
            if not paths:
                return
        flows = [p for p in paths if p.lower().endswith(".atlasflow")]
        if flows:
            self.open_workflow(flows[0])
        elif page is self.workflows:
            page.add_paths(paths)
        elif isinstance(page, ToolPage):
            page.add_paths(paths)
        else:
            self.home.route(paths)

    # ================================================================== status, jobs, log
    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def _activity(self, running: int, queued: int) -> None:
        if running or queued:
            set_badge(self.jobs_badge, f"● {running} running" + (f" · {queued} queued" if queued else ""), "live")
        else:
            set_badge(self.jobs_badge, "idle", "")
            self.progress.hide()
            page = self.stack.currentWidget()
            if hasattr(page, "status_text"):
                self.set_status(page.status_text())

    def _progress(self, jid: int, frac: float, msg: str) -> None:
        job = self.ctx.bridge.meta.get(jid)
        if job is None or job.mode not in ("run", "draft"):
            return
        self.progress.setRange(0, 1000)
        self.progress.setValue(int(frac * 1000))
        self.progress.show()
        name = Path(job.src).name
        self.set_status(f"{name}: {frac * 100:.0f}%  {msg}")

    def log(self, level: str, msg: str, source: str = "") -> None:
        th = self.ctx.theme
        color = {"error": th["danger"], "warning": th["warning"]}.get(level, th["text-2"])
        stamp = time.strftime("%H:%M:%S")
        safe = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.logbox.appendHtml(f'<span style="color:{th["text-3"]}">{stamp}</span>&nbsp;&nbsp;'
                               f'<span style="color:{color}">{level:<7}</span>&nbsp;&nbsp;'
                               f'<span style="color:{th["text-3"]}">{source}</span>&nbsp;&nbsp;{safe}')
        if level in ("warning", "error"):
            self.warn_count += 1
            self.btn_log.setText(f"Log · {self.warn_count}")

    def toggle_log(self, on: bool) -> None:
        sizes = self.vsplit.sizes()
        total = sum(sizes)
        self.vsplit.setSizes([total - 220, 220] if on else [total, 0])
        if on:
            self.warn_count = 0
            self.btn_log.setText("Log")

    def toast(self, text: str, tone: str = "", action_text: str = "", on_action=None, ms: int = 3200) -> None:
        if self.toaster is not None:
            self.toaster.show(text, self.ctx.theme, tone, action_text, on_action, ms)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toaster is not None:
            self.toaster.layout()

    def run_finished(self, outputs: list) -> None:
        """A Run batch (tool page or workflow) ended: what Setup › Outputs › "When a run ends" asks for."""
        if self.ctx.pref("finish_sound"):
            QApplication.beep()
        if self.ctx.pref("finish_alert") and not self.isActiveWindow():
            QApplication.alert(self)
        if self.ctx.pref("reveal_after_run") and outputs:
            reveal(Path(outputs[0]))

    # ================================================================== help
    def show_help(self, tab: str = "about"):
        """The About window (modeless; one at a time), on `tab`. Its former Setup tab is the Setup window's
        Components."""
        if tab == "setup":
            return self.show_setup("components")
        if self.about is None:
            self.about = AboutDialog(self.ctx, [(shown(keys), shown(what)) for keys, what in SHORTCUTS], self)
            self.about.finished.connect(lambda _r: setattr(self, "about", None))
            self.about.setAttribute(Qt.WA_DeleteOnClose)
        self.about.show_tab(tab)
        self.about.showNormal()
        self.about.raise_()
        self.about.activateWindow()
        return self.about

    def show_setup(self, tab: str = "general") -> SetupDialog:
        """The Setup window (modeless; one at a time), on `tab`."""
        if self.setup is None:
            self.setup = SetupDialog(self.ctx, self)
            self.setup.finished.connect(lambda _r: setattr(self, "setup", None))
            self.setup.setAttribute(Qt.WA_DeleteOnClose)
        self.setup.show_tab(tab)
        self.setup.showNormal()
        self.setup.raise_()
        self.setup.activateWindow()
        return self.setup

    def _pref_changed(self, key: str) -> None:
        if key == "workers":
            self.ctx.bridge.runner.process.set_workers(self.ctx.pref("workers"))
        elif key == "preview_delay_ms":
            for p in self.pages.values():
                p.debounce.setInterval(self.ctx.pref("preview_delay_ms"))

    # ================================================================== appearance
    def _runtimes_changed(self) -> None:
        """A required program was found or lost (located, installed, removed): every list and page updates."""
        self.sidebar.refresh_availability()
        self.home.refresh_availability()
        self.workflows.refresh_availability()
        for p in self.pages.values():
            p._check_runtimes()
        self._palette_entries()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange and self.isActiveWindow():
            self.ctx.refresh_runtimes()              # back from installing Blender, say: look again (cheap)

    def _retheme(self, theme) -> None:
        QApplication.instance().setStyleSheet(stylesheet(theme))
        self.search_icon.setIcon(icon("search", theme["text-3"], 16))
        for b in self.tabs.values():
            b.setIcon(icon(b._icon_name, theme["text-3"], 16, active=theme["text"]))
        self.sidebar.retheme(theme)
        apply_icon(self.btn_side, theme)
        apply_icon(self.btn_theme, theme, "sun" if theme.dark else "moon")
        apply_icon(self.btn_art, theme, "blueprint" if self.ctx.art == "blueprint" else "paper")
        apply_icon(self.btn_setup, theme)
        apply_icon(self.btn_help, theme)
        self.btn_log.setIcon(icon("log", theme["text-2"], 14))
        self.palette.theme = theme
        if self.about is not None:               # its icons and tiles are drawn for one theme: rebuild it
            tab = AboutDialog.TABS[self.about.tabs.currentIndex()]
            geo = self.about.geometry()
            self.about.close()
            self.about = None
            self.show_help(tab).setGeometry(geo)
        if self.setup is not None:               # the same for the Setup window
            tab = SetupDialog.TABS[self.setup.tabs.currentIndex()]
            geo = self.setup.geometry()
            self.setup.close()
            self.setup = None
            self.show_setup(tab).setGeometry(geo)
        self.home.retheme(theme)
        self.activity.retheme(theme)
        for p in self.pages.values():
            p.retheme(theme)
        self.workflows.retheme(theme)

    def _reart(self, art: str) -> None:
        self.btn_art.blockSignals(True)          # set from Setup too: the button follows
        self.btn_art.setChecked(art == "blueprint")
        self.btn_art.blockSignals(False)
        apply_icon(self.btn_art, self.ctx.theme, "blueprint" if art == "blueprint" else "paper")
        for p in self.pages.values():
            p.set_art(art)
        self.workflows.set_art(art)

    def closeEvent(self, e):
        if self.ctx.pref("confirm_quit") and self.ctx.bridge.busy() and (
                self.workflows.busy() or any(p.busy() for p in self.pages.values())):
            if QMessageBox.question(self, APP_NAME, "A run is still going. Stop it and quit?") != QMessageBox.Yes:
                e.ignore()
                return
        self.ctx.settings.set("geometry", bytes(self.saveGeometry()).hex())
        try:
            self.ctx.settings.save()
        except OSError:
            pass
        self.ctx.bridge.shutdown()
        super().closeEvent(e)
