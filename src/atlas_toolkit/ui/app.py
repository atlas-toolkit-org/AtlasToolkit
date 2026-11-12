"""
Start the desktop app.

    atlas-toolkit [files ...] [--tool ID] [--theme dark|light] [--art paper|blueprint]
                  [--snapshot out.png] [--page home|activity|workflows] [--workflow flow.atlasflow]

--snapshot waits until every job has finished, saves a screenshot of the window and quits (used by tests and to
check the look of a tool page).
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from ..core import prefs
from ..core.history import History
from ..core.paths import data_dir
from ..core.registry import registry
from ..core.settings import AppSettings
from ..version import APP_ID, APP_NAME, __version__
from .bridge import JobBridge
from .context import AppContext
from .theme import load_fonts, logo_icon, sans


def _parse(argv: list) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="atlas-toolkit", add_help=False)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--tool")
    ap.add_argument("--theme", choices=["dark", "light"])
    ap.add_argument("--art", choices=["paper", "blueprint"])
    ap.add_argument("--snapshot")
    ap.add_argument("--page", choices=["home", "activity", "workflows"])
    ap.add_argument("--workflow", help="a .atlasflow to open in the Workflows page (files are its inputs)")
    ap.add_argument("--select", type=int, default=0, help="row to select (snapshot)")
    ap.add_argument("--view", help="preview tab to show (snapshot)")
    ap.add_argument("--draft", action="store_true", help="run Preview on the selected file first (snapshot)")
    a, _unknown = ap.parse_known_args(argv)
    return a


def is_preset_file(path) -> bool:
    """.atlaspreset, .atlaspack, or a .zip that holds them (a downloaded pack): core/presets.py decides."""
    from ..core.presets import holds_presets
    return holds_presets(path)


def open_paths(win, files: list) -> None:
    """Files the system hands over: preset files and packs (or a .zip holding them) load into the preset library
    (after asking); a workflow opens as one, with any other files as its inputs; the rest go to the tool that takes
    them, or Home asks which."""
    presets = [f for f in files if is_preset_file(f)]
    if presets:
        win.import_preset_files(presets)
        files = [f for f in files if f not in presets]
        if not files:
            win.raise_()
            win.activateWindow()
            return
    flows = [f for f in files if f.lower().endswith(".atlasflow")]
    if flows:
        win.open_workflow(flows[0], [f for f in files if f not in flows] or None)
    else:
        win.home.route(files)
    win.raise_()
    win.activateWindow()


class FileOpener(QObject):
    """macOS hands files over with an event, not on the command line: at start (Open With in Finder) and later (a drop on
    the Dock icon). What arrives before the window exists waits for it.

    AppKit also turns file names on the command line into these events. Qt drops those only if they are in its own arguments,
    and the app keeps its arguments to itself, so they would open twice: each is skipped once, and a later drop of the same
    file still counts. Files never open inside the event: at launch it comes from inside AppKit's own callback, and a menu or
    dialog opened there leaves NSApp's run count wrong, which aborts the app when it quits."""

    def __init__(self, on_command_line=()):
        super().__init__()
        self.win = None
        self.waiting: list = []
        self.already_open = {os.path.realpath(f) for f in on_command_line}

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() != QEvent.Type.FileOpen or not ev.file():
            return False
        path = os.path.realpath(ev.file())
        if path in self.already_open:
            self.already_open.discard(path)
            return True
        self.waiting.append(ev.file())
        if self.win is not None:
            QTimer.singleShot(0, self.flush)
        return True

    def attach(self, win) -> None:
        self.win = win
        if self.waiting:
            QTimer.singleShot(0, self.flush)

    def flush(self) -> None:
        files, self.waiting = self.waiting, []
        if files:
            open_paths(self.win, files)


def _close_splash() -> None:
    if "_PYI_SPLASH_IPC" not in os.environ:   # only the one-file build has a splash, and this module complains without one
        return
    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:
        pass


def _match_opengl_to_qt(app) -> None:
    """Before the 3D views import PyOpenGL, which works out its OpenGL by itself and can end up with a different one from
    Qt's. On Linux it picks EGL or GLX from the session type, not from what Qt really uses, so an X11 program in a Wayland
    session would get the wrong one and no 3D view."""
    if sys.platform.startswith("linux") and "PYOPENGL_PLATFORM" not in os.environ:
        os.environ["PYOPENGL_PLATFORM"] = "egl" if app.platformName().startswith("wayland") else "glx"
    elif sys.platform == "win32" and app.platformName() == "windows":
        _follow_qts_software_renderer()


def _follow_qts_software_renderer() -> None:
    """With no usable graphics driver (a virtual machine, some Remote Desktop sessions), Qt draws with its own Mesa,
    opengl32sw.dll. PyOpenGL always loads the system's opengl32.dll, where no context is ever current, so every call it
    makes fails. Creating a context is what makes Qt choose; if it chose Mesa, PyOpenGL starts over on that library."""
    if "OpenGL.GL" in sys.modules:         # too late: its functions are already bound
        return
    import ctypes

    from PySide6.QtGui import QOpenGLContext
    if not QOpenGLContext().create():
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    handle = kernel32.GetModuleHandleW("opengl32sw.dll")
    if not handle:
        return
    from OpenGL import platform
    chosen = platform.PLATFORM
    for name in list(vars(chosen)):        # everything it worked out from the other library
        delattr(chosen, name)
    chosen.GL = chosen.OpenGL = ctypes.WinDLL("opengl32sw.dll", handle=handle)
    chosen.install(vars(platform))


class _Errors:
    """What the crash dialog has already said. A failure in a paint comes back with every frame, and dialogs run their own
    event loop: without this they stack up behind each other and the app can never be closed."""
    unattended = False        # a --snapshot run: nobody is there to click, so log it and exit with an error instead
    seen: set = set()
    showing = False


def _excepthook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        (data_dir() / "crash.log").open("a", encoding="utf-8").write(text + "\n")
    except OSError:
        pass
    sys.__stderr__ and sys.__stderr__.write(text)
    repeated = text in _Errors.seen
    _Errors.seen.add(text)
    if QApplication.instance() is None or _Errors.unattended or _Errors.showing or repeated:
        return
    box = QMessageBox(QMessageBox.Critical, APP_NAME, f"Something went wrong: {exc}")
    box.setDetailedText(text)
    _Errors.showing = True
    try:
        box.exec()
    finally:
        _Errors.showing = False


def run_app(argv: list) -> int:
    args = _parse(argv)
    settings = AppSettings()
    scale = prefs.get(settings, "ui_scale")         # Setup › General: read before Qt starts
    if scale != 1.0 and "QT_SCALE_FACTOR" not in os.environ and not args.snapshot:
        os.environ["QT_SCALE_FACTOR"] = f"{scale:g}"
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    _match_opengl_to_qt(app)
    opener = FileOpener(args.files)
    app.installEventFilter(opener)
    app.setDesktopFileName(APP_ID)     # how a Wayland desktop ties the window to atlas-toolkit.desktop and its icon
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Joao Goncalves")
    app.setStyle("Fusion")
    load_fonts()
    font = sans(13)
    font.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(font)
    app.setWindowIcon(logo_icon())
    sys.excepthook = _excepthook
    _Errors.unattended = bool(args.snapshot)

    if args.theme:
        settings.set("theme", args.theme)
    if args.art:
        settings.set("art", args.art)
    bridge = JobBridge(workers=prefs.get(settings, "workers"))
    ctx = AppContext(registry(), settings, bridge, History(), theme=args.theme, art=args.art)

    from .shell import MainWindow   # after QApplication: widgets need it
    win = MainWindow(ctx)
    win.show()
    _close_splash()
    opener.attach(win)
    # warm the worker process in the background so the first heavy run starts quickly
    if any(t.runs_in == "process" for t in ctx.registry.tools.values()):
        QTimer.singleShot(1500, bridge.runner.process.warm_up)

    tool = args.tool
    files = [f for f in args.files if Path(f).exists()]
    preset_files = [f for f in files if is_preset_file(f)]
    if preset_files:                   # double-clicked preset files or packs: load them, then carry on as usual
        QTimer.singleShot(0, lambda: win.import_preset_files(preset_files))
        files = [f for f in files if f not in preset_files]
    flows = [f for f in files if f.lower().endswith(".atlasflow")]
    if args.workflow or flows or args.page == "workflows":
        page = win.open_workflow(args.workflow or (flows[0] if flows else None),
                                 [f for f in files if f not in flows] or None)
        if args.select and page.queue.topLevelItemCount() > args.select:
            page.queue.setCurrentItem(page.queue.topLevelItem(args.select))
        files, tool = [], None
    if files and not tool:
        tools = {t.id for t in ctx.registry.ordered() for f in files if t.inputs.accepts(Path(f))}
        if len(tools) == 1:
            tool = tools.pop()
        elif not args.snapshot:
            QTimer.singleShot(200, lambda: win.home.route(files))
    if tool:
        page = win.open_tool(tool, files)
        if page is not None and args.select and page.queue.topLevelItemCount() > args.select:
            page.queue.setCurrentItem(page.queue.topLevelItem(args.select))
    elif args.page == "activity":
        win.show_activity()
    elif ctx.pref("start_page") == "last" and not (args.files or args.tool or args.page or args.workflow
                                                   or args.snapshot):
        win.restore_last_page()

    if args.snapshot:
        _arm_snapshot(app, win, ctx, Path(args.snapshot), args.view, args.draft)
    code = app.exec()
    bridge.shutdown()
    return code or int(bool(_Errors.unattended and _Errors.seen))


def _arm_snapshot(app, win, ctx, out: Path, view: str | None, draft: bool = False) -> None:
    state = {"quiet": 0, "ticks": 0, "draft": draft}

    def tick():
        state["ticks"] += 1
        idle = not ctx.bridge.busy() and all(p.idle() for p in [*win.pages.values(), win.workflows])
        page = win.stack.currentWidget()
        if idle and state["draft"] and hasattr(page, "preview_selected"):
            state["draft"] = False
            page.preview_selected()
            QTimer.singleShot(250, tick)
            return
        state["quiet"] = state["quiet"] + 1 if idle else 0
        if state["quiet"] >= 4 or state["ticks"] > 600:
            if view:
                page = win.stack.currentWidget()
                stage = getattr(page, "stage", None)
                if stage is not None:
                    titles = [stage.tabs.tabText(i) for i in range(stage.tabs.count())]
                    if view in titles:
                        stage.tabs.setCurrentIndex(titles.index(view))
            QTimer.singleShot(400, lambda: (win.grab().save(str(out)), win.close()))
            return
        QTimer.singleShot(250, tick)

    QTimer.singleShot(800, tick)
