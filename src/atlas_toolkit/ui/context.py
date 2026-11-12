"""Shared application state handed to every page: theme, art mode, jobs, settings, options, history and shell
actions."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication

from ..core import prefs
from ..core.history import History
from ..core.registry import Registry
from ..core.settings import AppSettings, ToolSettings
from .bridge import JobBridge
from .theme import Theme


class AppContext(QObject):
    theme_changed = Signal(object)
    art_changed = Signal(str)
    history_changed = Signal()
    runtimes_changed = Signal()       # a required program was found or lost: lists and pages update
    prefs_changed = Signal(str)       # an option of the Setup window changed (its key)

    def __init__(self, registry: Registry, settings: AppSettings, bridge: JobBridge, history: History,
                 theme: str | None = None, art: str | None = None):
        """`theme` and `art` (the command line's --theme, --art) win over the start-up options."""
        super().__init__()
        self.registry = registry
        self.settings = settings
        self.bridge = bridge
        self.history = history
        self.theme = Theme(theme or self.start_theme())
        self.art = art or self.start_art()
        hints = QGuiApplication.styleHints() if QGuiApplication.instance() else None
        if hints is not None and hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._system_scheme_changed)
        self._tool_settings: dict = {}
        from ..core.presets import PresetLibrary
        # Every preset the app can offer (yours, installed packs); one library, so a preset saved on a tool page shows
        # at once in a workflow step.
        self.presets = PresetLibrary()
        self._lacking: dict | None = None   # tool id -> runtimes it requires that cannot be found
        # set by the shell
        self.toast = lambda text, tone="", action_text="", on_action=None, ms=3200: None
        self.open_tool = lambda tool_id, paths=None, values=None: None
        self.open_workflow = lambda path=None, paths=None: None
        self.status = lambda text: None
        self.log = lambda level, msg, source="": None
        self.show_help = lambda tab="about": None
        self.show_setup = lambda tab="general": None
        self.run_finished = lambda outputs: None      # a Run batch ended: sound, taskbar, reveal (Setup options)

    def manage_presets(self, tool_id: str | None = None, uid: str | None = None) -> None:
        """Setup › Presets, on a tool's presets (a preset bar's "Manage presets and packs…")."""
        dialog = self.show_setup("presets")
        panel = getattr(dialog, "presets_panel", None)
        if panel is not None and tool_id:
            panel.select(tool_id, uid)

    # ------------------------------------------------------------------ options (core/prefs, the Setup window)
    def pref(self, key: str):
        return prefs.get(self.settings, key)

    def set_pref(self, key: str, value) -> None:
        self.settings.set(key, value)
        try:
            self.settings.save()
        except OSError:
            pass
        self.prefs_changed.emit(key)

    @staticmethod
    def system_theme() -> str | None:
        """'dark' or 'light' from the operating system, or None when it does not say."""
        hints = QGuiApplication.styleHints() if QGuiApplication.instance() else None
        scheme = hints.colorScheme() if hints is not None and hasattr(hints, "colorScheme") else None
        return {Qt.ColorScheme.Dark: "dark", Qt.ColorScheme.Light: "light"}.get(scheme)

    def start_theme(self) -> str:
        choice = self.pref("theme_start")
        last = self.settings.get("theme", "dark")
        if choice == "system":
            return self.system_theme() or last
        return last if choice == "last" else choice

    def start_art(self) -> str:
        choice = self.pref("art_start")
        return self.settings.get("art", "paper") if choice == "last" else choice

    def _system_scheme_changed(self, *_args) -> None:
        if self.pref("theme_start") == "system":
            name = self.system_theme()
            if name and name != self.theme.name:
                self.set_theme(name)

    def tool_settings(self, tool) -> ToolSettings:
        """One ToolSettings per tool for the whole app: a tool page and workflow steps share its presets."""
        if tool.id not in self._tool_settings:
            self._tool_settings[tool.id] = ToolSettings(tool)
        return self._tool_settings[tool.id]

    # ------------------------------------------------------------------ external programs (core/runtimes)
    def _scan(self) -> dict:
        from ..core import runtimes
        out = {}
        for t in self.registry.tools.values():
            miss = runtimes.missing(t) if t.requires or t.requires_if else []
            if miss:
                out[t.id] = miss
        return out

    def lacking(self, tool, values=None) -> list:
        """The runtimes `tool` requires that cannot be found, as of the last refresh_runtimes()."""
        if values is not None:
            from ..core import runtimes
            return runtimes.missing(tool, values)
        if self._lacking is None:
            self._lacking = self._scan()
        return self._lacking.get(tool.id, [])

    def available(self, tool) -> bool:
        return not self.lacking(tool)

    def needs_short(self, tool) -> str:
        """'needs Blender' (or '' when nothing is missing): for badges and chips."""
        miss = self.lacking(tool)
        return ("needs " + " and ".join(r.label for r in miss)) if miss else ""

    def needs_text(self, tool) -> str:
        """The warning for tooltips and notices (or '' when nothing is missing)."""
        miss = self.lacking(tool)
        if not miss:
            return ""
        what = " and ".join(f"{r.label} {r.need}".strip() for r in miss)
        return (f"{tool.name} needs {what}, which was not found on this computer, so it cannot run yet. "
                "See Setup › Components to install it, or Locate it on the tool's page.")

    def optional_lacking(self, tool) -> list:
        """(runtime, what it adds) for the optional runtimes of `tool` that cannot be found."""
        from ..core import runtimes
        return [(runtimes.get(k), what) for k, what in tool.optional
                if k in runtimes.RUNTIMES and runtimes.find(k) is None]

    def refresh_runtimes(self) -> bool:
        """Look for the programs again (cheap: no process starts); tell the pages when anything changed."""
        old, self._lacking = self._lacking, self._scan()
        keys = lambda d: {k: [r.key for r in v] for k, v in (d or {}).items()}
        if old is not None and keys(old) == keys(self._lacking):
            return False
        self.runtimes_changed.emit()
        return True

    def set_theme(self, name: str) -> None:
        self.theme = Theme(name)
        self.settings.set("theme", name)
        self.theme_changed.emit(self.theme)

    def set_art(self, art: str) -> None:
        self.art = art
        self.settings.set("art", art)
        self.art_changed.emit(art)
