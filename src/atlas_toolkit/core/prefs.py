"""
The app's general options (the Setup window), kept in app.json beside the rest of AppSettings, and the rule for
outputs that already exist. No Qt: the CLI and the tests use it too.

    on_existing        overwrite (no question) | ask | suffix (keep the old file: name_YYYYMMDD-HHMMSS.ext)
    theme_start        last | dark | light | system            the theme the app opens in
    art_start          last | paper | blueprint
    start_page         home | last                             reopen the last tool or workflow
    default_out_mode   next | folder                           for tools that have no output setting saved yet
    default_out_dir    the folder for "folder"
    reveal_after_run   show the first output in its folder when a run ends
    confirm_quit       ask before quitting while a run is going
    workers            heavy jobs at once (worker processes)
    finish_sound       a sound when a run ends
    finish_alert       flash the taskbar when a run ends while the window is in the background
    preview_delay_ms   the pause after a change before the preview updates
    ui_scale           interface scale, applied at the next start

export_settings / import_settings move the options and every tool's settings and presets as one JSON file (the
computer's own entries, such as located programs and the window layout, stay behind).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

ON_EXISTING = {"overwrite": "Overwrite, without asking",
               "ask": "Ask before overwriting",
               "suffix": "Keep both: add the date and time to the new file's name"}
THEMES = {"last": "The last one used", "dark": "Dark", "light": "Light", "system": "Follow the system"}
ARTS = {"last": "The last one used", "paper": "Paper", "blueprint": "Blueprint"}
START_PAGES = {"home": "Home", "last": "The last tool or workflow"}
UI_SCALES = (0.85, 1.0, 1.1, 1.25, 1.5)
MAX_WORKERS = 4

DEFAULTS = {
    "on_existing": "overwrite",
    "theme_start": "last",
    "art_start": "last",
    "start_page": "home",
    "default_out_mode": "next",
    "default_out_dir": "",
    "reveal_after_run": False,
    "confirm_quit": True,
    "workers": 1,
    "finish_sound": False,
    "finish_alert": True,
    "preview_delay_ms": 450,
    "ui_scale": 1.0,
}
CHOICES = {"on_existing": ON_EXISTING, "theme_start": THEMES, "art_start": ARTS, "start_page": START_PAGES,
           "default_out_mode": {"next": "", "folder": ""}}
RANGES = {"workers": (1, MAX_WORKERS), "preview_delay_ms": (100, 3000), "ui_scale": (UI_SCALES[0], UI_SCALES[-1])}
# keys of app.json that are not options: what "Reset options" keeps (window layout, recent files, located programs)
STATE_KEYS = ("theme", "art", "geometry", "sidebar_on", "last_tool", "last_page", "last_workflow", "recent",
              "workflow_dir", "library_collapsed")


def max_workers() -> int:
    return max(1, min(MAX_WORKERS, os.cpu_count() or 1))


def get(settings, key: str):
    """An option from AppSettings, checked: a value of the wrong kind or out of range gives the default."""
    default = DEFAULTS[key]
    v = settings.get(key, default)
    if key in CHOICES:
        return v if v in CHOICES[key] else default
    if isinstance(default, bool):
        return v if isinstance(v, bool) else default
    if isinstance(default, (int, float)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return default
        lo, hi = RANGES.get(key, (v, v))
        return type(default)(min(max(v, lo), hi))
    return v if isinstance(v, type(default)) else default


def reset(settings) -> None:
    """Every option back to its default (state such as the window layout and located programs stays)."""
    for key in DEFAULTS:
        settings.data.pop(key, None)


# ---------------------------------------------------------------------- outputs that already exist
def stamp(t: float | None = None) -> str:
    """The date-and-time suffix of 'Keep both': one per run, so a batch's files share it."""
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(t))


def stamped(path: Path, mark: str) -> Path:
    """`path` with `_<mark>` before its extension; a number follows when that name is taken too."""
    path = Path(path)
    out = path.with_name(f"{path.stem}_{mark}{path.suffix}")
    n = 2
    while out.exists():
        out = path.with_name(f"{path.stem}_{mark}-{n}{path.suffix}")
        n += 1
    return out


def plan(paths: dict, policy: str, mark: str | None = None) -> tuple[dict, list]:
    """Where each planned output goes under `policy`: ({key: path}, [paths that exist and would be overwritten]).
    With 'suffix' an output that exists gets the run's stamp (and nothing is overwritten)."""
    exists = [p for p in paths.values() if Path(p).exists()]
    if policy != "suffix" or not exists:
        return dict(paths), exists
    mark = mark or stamp()
    return {k: (stamped(p, mark) if Path(p).exists() else p) for k, p in paths.items()}, []


# ---------------------------------------------------------------------- export / import
BUNDLE = "atlas-toolkit-settings"
# app.json keys that belong to this computer or this session: never exported, never imported
LOCAL_KEYS = ("geometry", "recent", "last_workflow", "workflow_dir")


def _local_keys() -> set:
    from .runtimes import RUNTIMES
    return set(LOCAL_KEYS) | {rt.setting for rt in RUNTIMES.values()}


def export_settings(settings, path: Path, tools_dir: Path | None = None) -> int:
    """The options, every tool's settings and your own presets (tools_dir, default beside app.json) as one JSON
    file; returns how many tools. Preset packs are purchases, not settings: they are never included."""
    from .presets import EXT
    from .settings import _read, _write
    tools_dir = Path(tools_dir) if tools_dir else settings.folder / "tools"
    tools = {f.stem: _read(f) for f in sorted(tools_dir.glob("*.json"))}
    mine_dir = tools_dir.parent / "presets" / "mine"
    presets = {f.name[: -len(EXT)]: _read(f) for f in sorted(mine_dir.glob(f"*{EXT}"))} if mine_dir.is_dir() else {}
    local = _local_keys()
    _write(Path(path), {BUNDLE: 1, "app": {k: v for k, v in settings.data.items() if k not in local},
                        "tools": tools, "presets": {k: v for k, v in presets.items() if v}})
    return len(tools)


def import_settings(settings, path: Path, tools_dir: Path | None = None) -> int:
    """Merge an exported file into these settings (saved at once); returns how many tools. ValueError if it is
    not one."""
    from .settings import _read, _write
    data = _read(Path(path))
    if not isinstance(data, dict) or data.get(BUNDLE) != 1:
        raise ValueError(f"{Path(path).name} is not an exported Atlas Toolkit settings file")
    tools_dir = Path(tools_dir) if tools_dir else settings.folder / "tools"
    local = _local_keys()
    settings.data.update({k: v for k, v in (data.get("app") or {}).items() if k not in local})
    settings.save()
    tools = {k: v for k, v in (data.get("tools") or {}).items() if isinstance(v, dict) and Path(k).name == k}
    incoming = {}                                       # tool id -> presets, merged into yours (never replacing them)
    for tool_id, values in tools.items():
        legacy = values.pop("presets", None)            # a bundle from Atlas Toolkit 2.0 kept them here
        if isinstance(legacy, dict) and legacy:
            incoming.setdefault(tool_id, []).append(("legacy", legacy))
        _write(tools_dir / f"{tool_id}.json", values)
    for tool_id, file_data in (data.get("presets") or {}).items():
        if isinstance(file_data, dict) and Path(tool_id).name == tool_id:
            incoming.setdefault(tool_id, []).append(("file", file_data))
    _merge_presets(incoming, tools_dir.parent / "presets" / "mine")
    return len(tools)


def _merge_presets(incoming: dict, mine_dir: Path) -> None:
    from .presets import MineStore, PresetFormatError, parse_preset_data
    from .registry import registry
    if not incoming:
        return
    store, reg = MineStore(mine_dir), registry()
    for tool_id, items in incoming.items():
        tool = reg.tools.get(tool_id)
        if tool is None:
            continue                                    # a tool this installation does not have
        for kind, payload in items:
            if kind == "legacy":
                store.adopt_legacy(tool, payload)
            else:
                try:
                    store.add(tool, parse_preset_data(payload))
                except PresetFormatError:
                    continue
