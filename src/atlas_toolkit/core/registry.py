"""
Tool discovery. Built-in tools are the sub-packages of atlas_toolkit.tools. Plug-ins are folders holding tool
packages, listed in ATLAS_TOOLKIT_PLUGINS (os.pathsep-separated) or placed in <config>/plugins. A plug-in that only
imports libraries bundled with the app runs in the frozen exe without a rebuild.

A tool that fails to import does not stop the app: it is listed in `broken` with its error.
"""
from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import os
import pkgutil
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from .tool import CATEGORIES, Tool

ENV_PLUGINS = "ATLAS_TOOLKIT_PLUGINS"
# options every `run <tool>` command already has (core/cli.py); a parameter may not reuse them
RESERVED_KEYS = {"inputs", "output", "out_dir", "format", "preset", "preset_file", "suffix", "dry_run", "force", "quiet", "tool", "command"}


@dataclass
class Broken:
    name: str
    origin: str
    error: str


class ToolTable(dict):
    """{id: Tool} that also answers to a tool's former ids (`Tool.aliases`): lookups resolve them, while iterating
    and listing show current ids only, so a renamed tool keeps its old CLI name, workflows and history."""

    def __init__(self):
        super().__init__()
        self.aliases: dict[str, str] = {}

    def resolve(self, tool_id):
        return tool_id if dict.__contains__(self, tool_id) else self.aliases.get(tool_id, tool_id)

    def __getitem__(self, tool_id):
        return dict.__getitem__(self, self.resolve(tool_id))

    def __contains__(self, tool_id):
        return dict.__contains__(self, self.resolve(tool_id))

    def get(self, tool_id, default=None):
        return dict.get(self, self.resolve(tool_id), default)


class Registry:
    def __init__(self):
        self.tools: ToolTable = ToolTable()
        self.broken: list[Broken] = []
        self.plugin_dirs: list[str] = []

    # ------------------------------------------------------------------ discovery
    def discover(self, plugin_dirs: list | None = None, builtins: bool = True) -> "Registry":
        if builtins:
            from .. import tools as pkg
            for mod in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
                if mod.ispkg and not mod.name.startswith("_"):
                    self._load(f"{pkg.__name__}.{mod.name}", origin=mod.name)
        for d in plugin_dirs if plugin_dirs is not None else default_plugin_dirs():
            self.add_plugin_dir(d)
        return self

    def add_plugin_dir(self, folder) -> None:
        folder = Path(folder)
        if not folder.is_dir():
            return
        self.plugin_dirs.append(str(folder))
        for sub in sorted(folder.iterdir()):
            if sub.is_dir() and (sub / "__init__.py").exists() and not sub.name.startswith(("_", ".")):
                name = f"atlas_toolkit_plugins.{sub.name}"
                try:
                    if "atlas_toolkit_plugins" not in sys.modules:   # namespace parent for relative imports
                        ns = importlib.util.module_from_spec(importlib.machinery.ModuleSpec(
                            "atlas_toolkit_plugins", None, is_package=True))
                        ns.__path__ = []
                        sys.modules["atlas_toolkit_plugins"] = ns
                    sys.modules["atlas_toolkit_plugins"].__path__.append(str(folder))
                    spec = importlib.util.spec_from_file_location(name, sub / "__init__.py",
                                                                  submodule_search_locations=[str(sub)])
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[name] = module
                    spec.loader.exec_module(module)
                    self._register(module, origin=str(sub))
                except Exception as e:
                    self.broken.append(Broken(sub.name, str(sub), f"{type(e).__name__}: {e}\n{traceback.format_exc()}"))

    def _load(self, modname: str, origin: str) -> None:
        try:
            self._register(importlib.import_module(modname), origin)
        except Exception as e:
            self.broken.append(Broken(modname.rsplit(".", 1)[-1], origin,
                                      f"{type(e).__name__}: {e}\n{traceback.format_exc()}"))

    def _register(self, module, origin: str) -> None:
        tool = getattr(module, "TOOL", None)
        if not isinstance(tool, Tool):
            raise TypeError(f"{module.__name__} does not define TOOL = Tool(...)")
        tool.package = module.__name__
        problems = validate(tool)
        if problems:
            raise ValueError("; ".join(problems))
        if tool.id in self.tools:
            raise ValueError(f"duplicate tool id {tool.id!r} ({origin})")
        for old in tool.aliases:
            if old in self.tools:
                raise ValueError(f"alias {old!r} of {tool.id!r} is already a tool id or alias ({origin})")
        self.tools[tool.id] = tool
        self.tools.aliases.update({old: tool.id for old in tool.aliases})

    # ------------------------------------------------------------------ access
    def get(self, tool_id: str) -> Tool:
        return self.tools[tool_id]

    def ordered(self) -> list[Tool]:
        return sorted(self.tools.values(), key=lambda t: (CATEGORIES.get(t.category, CATEGORIES["other"])[1], t.name))

    def by_category(self) -> list[tuple[str, list[Tool]]]:
        out: dict[str, list[Tool]] = {}
        for t in self.ordered():
            out.setdefault(t.category, []).append(t)
        return [(CATEGORIES.get(c, CATEGORIES["other"])[0], ts) for c, ts in out.items()]

    def for_file(self, path: Path) -> list[Tool]:
        return [t for t in self.ordered() if t.inputs.accepts(Path(path))]


def validate(tool: Tool) -> list[str]:
    """Contract checks run at registration (cheap) and, more thoroughly, by tests/test_contract.py."""
    errs = []
    if not tool.id or tool.id != tool.id.lower() or " " in tool.id:
        errs.append(f"id {tool.id!r} must be lower-case kebab-case")
    if tool.category not in CATEGORIES:
        errs.append(f"unknown category {tool.category!r}")
    if not tool.engine or ":" not in tool.engine:
        errs.append("engine must be 'module:function'")
    keys = [p.key for p in tool.param_list]
    if len(keys) != len(set(keys)):
        errs.append("duplicate parameter keys")
    taken = sorted(set(keys) & RESERVED_KEYS)
    if taken:
        errs.append(f"parameter keys {taken} are reserved by the command line (--output, --out-dir ...)")
    if tool.migrate and ":" not in tool.migrate:
        errs.append("migrate must be '.module:function'")
    if tool.runs_in not in ("thread", "process"):
        errs.append("runs_in must be 'thread' or 'process'")
    from .runtimes import RUNTIMES
    unknown = sorted((set(tool.requires) | {k for k, _ in tool.requires_if}) - set(RUNTIMES))
    if unknown:
        errs.append(f"requires unknown runtimes {unknown} (known: {sorted(RUNTIMES)})")
    for key, rule in tool.requires_if:
        if not rule or set(rule) - set(keys):
            errs.append(f"requires_if for {key} must reference known parameters")
    if set(tool.output.format_notes) - (set(tool.output.formats) | {tool.output.ext}):
        errs.append('format_notes must reference offered output formats')
    for item in tool.optional:
        if not (isinstance(item, tuple) and len(item) == 2 and item[0] in RUNTIMES and item[1]):
            errs.append(f"optional entries are (runtime key, what it adds) pairs with known keys, not {item!r}")
    return errs


def default_plugin_dirs() -> list[str]:
    from .paths import config_dir
    dirs = [p for p in os.environ.get(ENV_PLUGINS, "").split(os.pathsep) if p]
    dirs.append(str(config_dir() / "plugins"))
    return dirs


_REGISTRY: Registry | None = None


def registry(plugin_dirs: list | None = None) -> Registry:
    """The process-wide registry, discovered on first use."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = Registry().discover(plugin_dirs)
    return _REGISTRY
