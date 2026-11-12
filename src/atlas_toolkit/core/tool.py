"""
The tool contract. A tool is a sub-package of atlas_toolkit.tools (or a plug-in folder) whose __init__.py defines

    TOOL = Tool(id=..., name=..., params=[...], engine=".my_tool:run", ...)

and an engine module with a pure function

    def run(ctx: JobContext, src: Path, dst: Path | None, p: Params) -> Result

`dst` is None for a dry run (analyse / draft preview): compute, report, write nothing but the preview cache.
__init__.py must stay light (no numpy, trimesh, Qt): the app reads every spec at start-up, and engines are only
imported in the worker that runs them. See docs/ADDING_A_TOOL.md.
"""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .params import Param, Params, flatten, resolve

CATEGORIES = {   # id: (sidebar heading, order)
    "svg": ("SVG · post-process", 10),
    "mesh2svg": ("Mesh → SVG", 20),
    "mesh": ("Mesh · model prep", 30),
    "image": ("Image → SVG", 40),
    "image2mesh": ("Image → Mesh", 45),
    "other": ("Other", 90),
}

KINDS = {   # input/output kinds: extensions and badge text
    "svg": ((".svg",), "SVG"),
    "mesh": ((".stl", ".obj", ".ply", ".glb", ".gltf", ".off"), "MESH"),
    "image": ((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"), "IMAGE"),
}


@dataclass
class Inputs:
    kind: str = "svg"
    exts: tuple = ()
    multiple: bool = True
    exclude: tuple = ()          # name patterns to skip when a folder is added (earlier outputs)

    def __post_init__(self):
        if not self.exts:
            self.exts = KINDS[self.kind][0]

    def accepts(self, path: Path) -> bool:
        return path.suffix.lower() in self.exts and not any(re.search(x, path.name) for x in self.exclude)


@dataclass
class Output:
    template: str                # "{stem}--nooverlap.svg"; may use parameter keys: "{stem}_grid{n}.stl"
    kind: str = "svg"
    formats: tuple = ()          # alternative extensions offered in the inspector (meshes: .stl .obj .ply)
    format_notes: dict = field(default_factory=dict)  # format-specific user guidance, shared by UI and workflows

    @property
    def ext(self) -> str:
        return Path(self.template).suffix.lower()

    def name_for(self, src: Path, p: dict | None = None, ext: str | None = None) -> str:
        values = {k: (_fmt_name_value(v)) for k, v in (p or {}).items()}
        values["stem"] = src.stem
        name = self.template.format_map(_Missing(values))
        if ext and ext != self.ext:
            name = name[: -len(self.ext)] + ext
        return name


class _Missing(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def _same(a, b) -> bool:
    """Equal settings: floats to 4 decimals (spin boxes round), sequences item by item."""
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        try:
            return round(float(a), 4) == round(float(b), 4)
        except (TypeError, ValueError):
            return False
    return a == b


def _fmt_name_value(v) -> str:
    if isinstance(v, (tuple, list)):
        return "x".join(_fmt_name_value(x) for x in v)
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


@dataclass
class Stat:
    """One number for the stat tiles and the queue columns. `before` makes it a "before → after" stat."""
    key: str
    label: str
    value: Any
    unit: str = ""
    before: Any = None
    tone: str = ""               # "", "warning", "danger", "success", "accent"
    fmt: str = "{:,.0f}"

    def text(self, v=None) -> str:
        v = self.value if v is None else v
        try:
            s = self.fmt.format(v).replace(",", " ")
        except (ValueError, TypeError):
            s = str(v)
        return f"{s} {self.unit}".strip()

    def display(self) -> str:
        return f"{self.text(self.before)} → {self.text()}" if self.before is not None else self.text()


@dataclass
class Result:
    outputs: list = field(default_factory=list)      # written files (str)
    stats: list = field(default_factory=list)        # [Stat]
    report: str = ""                                 # monospace text for the Report tab
    preview: dict | None = None                      # {"kind": "svg" | "mesh", ...} see ui/preview
    changed: bool = True                             # False: nothing to do for this input (not written)
    status: str = ""                                 # short status for the queue ("Ready", "Nothing to cut")
    data: Any = None                                 # tool-specific payload (picklable)

    def stat(self, key: str):
        return next((s for s in self.stats if s.key == key), None)


@dataclass
class InputInfo:
    """What a tool's inspect() learned about an input; shown under the queue and fed to parameter suggestions."""
    path: str
    kind: str
    summary: str = ""                                # one line: "12 840 faces · 210 × 180 × 95 units · watertight"
    values: dict = field(default_factory=dict)       # e.g. {"max_dim": 210.0, "layers": [...]}
    preview: dict | None = None

    def __getattr__(self, k):
        try:
            return self.__dict__["values"][k]
        except KeyError:
            raise AttributeError(k) from None


@dataclass
class Tool:
    id: str                                      # kebab-case, also the SVG <desc> signature for SVG tools
    name: str
    version: str
    category: str                                # key of CATEGORIES
    summary: str                                 # one sentence, shown on cards and under the title
    inputs: Inputs
    output: Output
    engine: str                                  # ".module:function", relative to the tool's package
    params: list = field(default_factory=list)   # Params and Sections (presets are data, never part of a spec:
                                                 # core/presets.py, docs/PRESETS.md)
    signature: str = ""                          # SVG: a desc element holding the id; mesh: "atlas-toolkit:" + id
    inspect: str | None = None                   # ".module:inspect" -> InputInfo
    input_dependencies: str | None = None        # callable(src, values) -> additional input Paths for cache keys
    runs_in: str = "thread"                      # "thread" (light) or "process" (heavy, cancellable by kill)
    preview: str = "svg"                         # "svg" | "mesh" | "none"
    live: bool = False                           # analyse every input as settings change (dry runs)
    columns: tuple = ()                          # Stat keys shown as queue columns
    icon: str = "process"
    action: str = "Run"                          # verb on the primary button: "Clean all", "Run · 3 files"
    description: str = ""                        # longer help (plain text, shown in the help popover)
    tagline: str = ""                            # short header line
    panel: str | None = None                     # ".module:Class", an extra QWidget for the inspector
    panel_after: str = ""                        # title of the section the panel goes under ("" = after the form)
    viewer: str | None = None                    # ".module:Class", an interactive QWidget shown as the first
                                                 # preview tab (e.g. a live camera); see ui/widgets/preview.py
    preset_skip: tuple = ()                      # keys presets neither set nor compare (view state, e.g. a camera)
    preset_inputs: tuple = ()                    # built-in UI presets keep input settings unless explicitly overridden
    migrate: str | None = None                   # ".module:fn" fn(values, from_version) -> values: brings preset
                                                 # values written by an older version up to date (see docs/PRESETS.md)
    legacy: tuple = ()                           # older scripts/exes this tool replaces (for docs and Atlas)
    aliases: tuple = ()                          # former ids of a renamed tool: the CLI, workflows and history still
                                                 # accept them, and the settings saved under them carry over
    sample: str | None = None                    # ".module:make_sample" fn(folder) -> Path, for tests/self-test
    test_values: dict = field(default_factory=dict)   # overrides that keep the self-test quick
    test_variants: tuple = ()                    # more overrides (dicts on top of test_values) a strict self-test and
                                                 # the contract tests also run: a path test_values avoids, such as a helper
    requires: tuple = ()                         # external runtimes (keys of core/runtimes.RUNTIMES), e.g. ("blender",)
    requires_if: tuple = ()                      # (runtime, {parameter: value or tuple of values}), AND within a rule
    optional: tuple = ()                         # runtimes it can use but runs without: (key, what it adds) pairs,
                                                 # e.g. (("blender", "Settle in Blender: a cloth simulation"),)
    package: str = ""                            # filled by the registry

    # ------------------------------------------------------------------ schema
    @property
    def param_list(self) -> list[Param]:
        return flatten(self.params)

    def param(self, key: str) -> Param:
        return next(p for p in self.param_list if p.key == key)

    def defaults(self) -> Params:
        return resolve(self.param_list, {})

    def values(self, overrides: dict | None = None) -> Params:
        """The defaults with `overrides` on top (None values ignored), validated. Presets: core/presets.apply."""
        v = dict(self.defaults())
        v.update({k: x for k, x in (overrides or {}).items() if x is not None})
        return resolve(self.param_list, v)

    # ------------------------------------------------------------------ code
    def resolve(self, ref: str) -> Callable:
        mod, _, attr = ref.partition(":")
        module = importlib.import_module(mod, self.package) if mod.startswith(".") else importlib.import_module(mod)
        return getattr(module, attr)

    def engine_fn(self) -> Callable:
        return self.resolve(self.engine)

    def inspect_fn(self) -> Callable | None:
        return self.resolve(self.inspect) if self.inspect else None

    @property
    def category_title(self) -> str:
        return CATEGORIES.get(self.category, CATEGORIES["other"])[0]

    @property
    def io_badge(self) -> str:
        return f"{KINDS[self.inputs.kind][1]} → {KINDS[self.output.kind][1]}"
