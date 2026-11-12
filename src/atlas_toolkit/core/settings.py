"""
Saved settings, as JSON in the config folder (portable, readable, merge-friendly):

    app.json             theme, art mode, last tool, recent files ...
    tools/<tool-id>.json last values, output mode and folder
    presets/             your own presets and hidden pack presets (core/presets.py)

Values are re-validated against the tool's schema on load, so renamed or removed parameters are dropped quietly.
Atlas Toolkit 2.0 kept your presets inside tools/<tool-id>.json; opening a tool's settings moves them into
presets/mine/<tool-id>.atlaspreset once (core/presets.MineStore.adopt_legacy).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .paths import config_dir
from .params import AUTO, resolve


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    """Atomic write: a crash never leaves half a settings file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class AppSettings:
    def __init__(self, folder: Path | None = None):
        self.folder = Path(folder) if folder else config_dir()
        self.path = self.folder / "app.json"
        self.data = _read(self.path)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        _write(self.path, self.data)

    def add_recent(self, path: str, limit: int = 30) -> None:
        rec = [p for p in self.data.get("recent", []) if p != path]
        self.data["recent"] = [path] + rec[: limit - 1]


class ToolSettings:
    def __init__(self, tool, folder: Path | None = None):
        from .presets import MINE, MineStore
        self.tool = tool
        base = Path(folder) if folder else config_dir()
        self.path = base / "tools" / f"{tool.id}.json"
        self.mine = MineStore(base / "presets" / MINE)
        self.data = _read(self.path)
        if not self.path.exists():     # a renamed tool starts from what was saved under its former id
            for old in getattr(tool, "aliases", ()):
                if (self.path.parent / f"{old}.json").exists():
                    self.data = _read(self.path.parent / f"{old}.json")
                    break
        self._adopt_legacy_presets()

    def _adopt_legacy_presets(self) -> None:
        """Presets saved by Atlas Toolkit 2.0 move to the presets store: written there first, then removed here, so
        a crash in between only repeats the (idempotent) move next time."""
        legacy = self.data.get("presets")
        if not legacy:
            self.data.pop("presets", None)
            return
        try:
            self.mine.adopt_legacy(self.tool, legacy)
        except OSError:
            return                     # a read-only profile: keep them where they are
        self.data.pop("presets", None)
        if self.path.exists():
            try:
                self.save()
            except OSError:
                pass

    # ------------------------------------------------------------------ values
    def values(self) -> dict:
        saved = self.data.get("values") or {}
        try:
            v = dict(self.tool.defaults())
            v.update(resolve(self.tool.param_list, saved, partial=True))
            return v
        except ValueError:
            return dict(self.tool.defaults())

    def set_values(self, values: dict) -> None:
        self.data["values"] = {p.key: (values[p.key] if values[p.key] == AUTO else p.to_json(values[p.key]))
                               for p in self.tool.param_list if p.key in values}

    # ------------------------------------------------------------------ output
    @property
    def out_mode(self) -> str:
        return self.data.get("out_mode", "next")      # "next" (beside each input) | "folder"

    @out_mode.setter
    def out_mode(self, v: str) -> None:
        self.data["out_mode"] = v

    @property
    def out_dir(self) -> str:
        return self.data.get("out_dir", "")

    @out_dir.setter
    def out_dir(self, v: str) -> None:
        self.data["out_dir"] = v

    @property
    def out_format(self) -> str:
        return self.data.get("out_format", "")       # alternative extension (meshes), "" = template's

    @out_format.setter
    def out_format(self, v: str) -> None:
        self.data["out_format"] = v

    # ------------------------------------------------------------------ your own presets (kept in presets/mine)
    def user_presets(self) -> dict:
        """{name: values} of your own presets for this tool (core/presets.py holds them)."""
        return {p.name: dict(p.values) for p in self.mine.load(self.tool)}

    def save_preset(self, name: str, values: dict) -> None:
        self.mine.put(self.tool, name, values)

    def delete_preset(self, name: str) -> None:
        for p in self.mine.load(self.tool):
            if p.name == name:
                self.mine.remove(self.tool, p.uid)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        _write(self.path, self.data)
