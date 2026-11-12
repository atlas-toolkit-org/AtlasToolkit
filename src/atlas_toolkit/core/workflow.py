"""
Workflows: tools chained into a graph and run as one job per input file.

    Input ──▶ Mesh Shatter ──▶ Hatch Maker ──▶ Overlap Cleaner ──▶ Save
                           └─▶ Hatch Maker (contours) ─────────▶ Save

A workflow is data: nodes (input, tool, save), edges between their ports, and the parameters shown on its panel. It
is stored as JSON (`.atlasflow`) and every step goes through the same `execute()` as a tool on its own, in the worker
process. A tool needs nothing extra to be a step: its main input and output come from `Inputs` and `Output`, and each
`FileRef` parameter is a side port another step can feed.

Intermediates go to a content-addressed cache (cache_dir()/workflows): a step whose tool, settings and inputs did not
change is not run again. Only Save nodes write into the user's folders.

No Qt here: the UI, the worker process, the tests and the self-test use it.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from .job import DEFAULT_INSPECT, Cancelled, Job, execute, inspect_input
from .params import AUTO, Choice, FileRef, ParamError, Seed, resolve_auto
from .prefs import stamped
from .tool import KINDS, Result, Stat

def saved_path(path: str):
    """A path as a workflow saved it, perhaps on another OS: on Linux, Path would keep a Windows path whole as one name."""
    return PureWindowsPath(path) if "\\" in path else Path(path)


def same_file_key(text: str) -> str:
    """Names that are one file on this system's disks: case makes a difference only on Linux."""
    return text if sys.platform.startswith("linux") else text.lower()


FORMAT = "atlas-toolkit.workflow"
FORMAT_VERSION = 1
EXT = ".atlasflow"
MAIN = "in"                          # the main input port of a tool or save node
INPUT, TOOL, SAVE = "input", "tool", "save"
DEFAULT_LIMIT_GB = 5.0
PARTIAL_AGE_S = 6 * 3600       # a step's partial cache folder older than this was left by a killed worker
NAME_FIELDS = ("name", "stem", "workflow", "node")
TERMINAL = ("done", "error", "cancelled")


class WorkflowError(ValueError):
    """A workflow that cannot be built or run: a loop, a missing file, a step that failed."""


class StepFailed(WorkflowError):
    def __init__(self, node: str, label: str, text: str, trace: str = ""):
        super().__init__(f"{label}: {text}")
        self.node, self.label, self.text, self.trace = node, label, text, trace


# ============================================================================ model

@dataclass
class Node:
    id: str
    type: str                                    # input | tool | save
    tool: str = ""                               # tool id (tool nodes)
    tool_version: str = ""                       # version the values were set with
    label: str = ""                              # "" = the tool's name
    values: dict = field(default_factory=dict)   # JSON values (param.to_json), validated by the tool's schema
    format: str = ""                             # output extension for tools with formats ("" = the template's)
    kind: str = "mesh"                           # input nodes: mesh | svg | image
    mode: str = "batch"                          # input nodes: batch (the files of a run) | fixed (one file)
    path: str = ""                               # input nodes in fixed mode
    where: str = "next"                          # save nodes: next (to the batch input) | folder
    folder: str = ""
    name: str = "{name}"                         # save nodes: file name, without extension
    pos: tuple = (0.0, 0.0)

    def to_dict(self) -> dict:
        d = {"id": self.id, "type": self.type, "label": self.label, "pos": [round(float(self.pos[0]), 1),
                                                                            round(float(self.pos[1]), 1)]}
        if self.type == TOOL:
            d.update(tool=self.tool, tool_version=self.tool_version, values=dict(self.values), format=self.format)
        elif self.type == INPUT:
            d.update(kind=self.kind, mode=self.mode, path=self.path)
        else:
            d.update(where=self.where, folder=self.folder, name=self.name)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        n = cls(**known)
        n.pos = tuple(float(x) for x in (d.get("pos") or (0, 0)))[:2]
        n.values = dict(d.get("values") or {})
        return n


@dataclass
class Edge:
    src: str
    dst: str
    port: str = MAIN


@dataclass
class Issue:
    level: str          # error | warning
    message: str
    node: str = ""
    port: str = ""


@dataclass
class Port:
    key: str            # MAIN or a FileRef parameter key
    label: str
    kind: str | None    # svg | mesh | image, None = any
    exts: tuple = ()    # accepted extensions, () = any of the kind's
    side: bool = False  # a FileRef parameter fed by another step


@dataclass
class Workflow:
    name: str = "Untitled workflow"
    description: str = ""
    nodes: dict = field(default_factory=dict)    # id -> Node, in creation order
    edges: list = field(default_factory=list)    # [Edge]
    exposed: list = field(default_factory=list)  # [(node id, parameter key)] shown on the workflow panel
    toolkit: str = ""                            # toolkit version that saved it

    # ------------------------------------------------------------------ editing
    def new_id(self, prefix: str = "n") -> str:
        i = 1
        while f"{prefix}{i}" in self.nodes:
            i += 1
        return f"{prefix}{i}"

    def add(self, node: Node) -> Node:
        if not node.id or node.id in self.nodes:
            node.id = self.new_id({"input": "in", "save": "out"}.get(node.type, "n"))
        self.nodes[node.id] = node
        return node

    def remove(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)
        self.edges = [e for e in self.edges if node_id not in (e.src, e.dst)]
        self.exposed = [x for x in self.exposed if x[0] != node_id]

    def connect(self, src: str, dst: str, port: str = MAIN) -> Edge:
        """Join src's output to a port of dst, replacing the edge that was on that port. Loops are refused."""
        if src not in self.nodes or dst not in self.nodes:
            raise WorkflowError("unknown node")
        if src == dst or self.reaches(dst, src):
            raise WorkflowError("That would make a loop: a step cannot feed itself.")
        if self.nodes[dst].type == INPUT:
            raise WorkflowError("An input has no ports to connect to.")
        if self.nodes[src].type == SAVE:
            raise WorkflowError("A Save node is an end point: it has no output.")
        self.disconnect(dst, port)
        e = Edge(src, dst, port)
        self.edges.append(e)
        return e

    def disconnect(self, dst: str, port: str = MAIN) -> None:
        self.edges = [e for e in self.edges if not (e.dst == dst and e.port == port)]

    # ------------------------------------------------------------------ queries
    def edge_into(self, dst: str, port: str = MAIN) -> Edge | None:
        return next((e for e in self.edges if e.dst == dst and e.port == port), None)

    def edges_into(self, dst: str) -> list[Edge]:
        return [e for e in self.edges if e.dst == dst]

    def edges_from(self, src: str) -> list[Edge]:
        return [e for e in self.edges if e.src == src]

    def reaches(self, a: str, b: str) -> bool:
        """True when b is downstream of a (or is a)."""
        seen, todo = set(), [a]
        while todo:
            n = todo.pop()
            if n == b:
                return True
            if n in seen:
                continue
            seen.add(n)
            todo += [e.dst for e in self.edges if e.src == n]
        return False

    def ancestors(self, ids) -> set:
        seen, todo = set(), list(ids)
        while todo:
            n = todo.pop()
            for e in self.edges_into(n):
                if e.src not in seen:
                    seen.add(e.src)
                    todo.append(e.src)
        return seen

    def order(self, among=None) -> list[str]:
        """Topological order (creation order among ready nodes); WorkflowError on a loop."""
        ids = [n for n in self.nodes if among is None or n in among]
        keep = set(ids)
        indeg = {n: 0 for n in ids}
        for e in self.edges:
            if e.src in keep and e.dst in keep:
                indeg[e.dst] += 1
        out, ready = [], [n for n in ids if indeg[n] == 0]
        while ready:
            n = ready.pop(0)
            out.append(n)
            for e in self.edges:
                if e.src == n and e.dst in keep:
                    indeg[e.dst] -= 1
                    if indeg[e.dst] == 0:
                        ready.append(e.dst)
            ready.sort(key=ids.index)
        if len(out) != len(ids):
            raise WorkflowError("The workflow has a loop: a step feeds one of the steps before it.")
        return out

    def batch_inputs(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.type == INPUT and n.mode == "batch"]

    def saves(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.type == SAVE]

    def needed(self, action: str = "run", target: str | None = None) -> set:
        """Nodes a run needs: everything that leads to a Save, or what leads to (and is) the target."""
        if target is None:
            ends = {n.id for n in self.saves()}
            return ends | self.ancestors(ends)
        return self.ancestors([target]) | ({target} if action != "prepare" else set())

    # ------------------------------------------------------------------ files
    def to_dict(self) -> dict:
        from ..version import __version__
        return {"format": FORMAT, "version": FORMAT_VERSION, "name": self.name, "description": self.description,
                "toolkit": __version__, "nodes": [n.to_dict() for n in self.nodes.values()],
                "edges": [{"from": e.src, "to": e.dst, "port": e.port} for e in self.edges],
                "exposed": [{"node": n, "key": k} for n, k in self.exposed]}

    @classmethod
    def from_dict(cls, d: dict) -> "Workflow":
        if d.get("format") != FORMAT:
            raise WorkflowError("Not an Atlas Toolkit workflow.")
        if int(d.get("version", 1)) > FORMAT_VERSION:
            raise WorkflowError("This workflow was saved by a newer Atlas Toolkit.")
        wf = cls(name=d.get("name") or "Untitled workflow", description=d.get("description", ""),
                 toolkit=d.get("toolkit", ""))
        for nd in d.get("nodes", []):
            n = Node.from_dict(nd)
            wf.nodes[n.id] = n
        for ed in d.get("edges", []):
            if ed.get("from") in wf.nodes and ed.get("to") in wf.nodes:
                wf.edges.append(Edge(ed["from"], ed["to"], ed.get("port") or MAIN))
        wf.exposed = [(x["node"], x["key"]) for x in d.get("exposed", []) if x.get("node") in wf.nodes]
        return wf

    def copy(self) -> "Workflow":
        return Workflow.from_dict(json.loads(json.dumps(self.to_dict())))


def load(path) -> Workflow:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise WorkflowError(f"Could not read {Path(path).name}: {e}") from None
    return Workflow.from_dict(data)


_NAMES: dict = {}       # path -> (mtime, size, name)


def saved_name(path) -> str:
    """The name stored in a saved workflow (what the user called it), or its file name when it has none or cannot
    be read. Cached by modification time, so listing many workflows reads each file once."""
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return path.stem
    hit = _NAMES.get(str(path))
    if hit and hit[:2] == (st.st_mtime_ns, st.st_size):
        return hit[2]
    try:
        name = json.loads(path.read_text(encoding="utf-8")).get("name")
    except (OSError, ValueError, AttributeError):
        name = None
    name = str(name).strip() if isinstance(name, str) and name.strip() else path.stem
    _NAMES[str(path)] = (st.st_mtime_ns, st.st_size, name)
    return name


def save(wf: Workflow, path) -> Path:
    from .settings import _write
    path = Path(path)
    if path.suffix.lower() != EXT:
        path = path.with_name(path.name + EXT)
    _write(path, wf.to_dict())
    return path


# ============================================================================ nodes and tools

def jsonable(tool, values: dict) -> dict:
    return {p.key: p.to_json(values[p.key]) for p in tool.param_list if p.key in values}


def tool_node(tool, pos=(0.0, 0.0), values: dict | None = None) -> Node:
    """A new step for `tool`, starting from its defaults (its default preset)."""
    v = tool.values(values) if values else tool.defaults()
    return Node("", TOOL, tool=tool.id, tool_version=tool.version, values=jsonable(tool, v), pos=tuple(pos))


def input_node(kind: str = "mesh", pos=(0.0, 0.0), mode: str = "batch", path: str = "") -> Node:
    return Node("", INPUT, kind=kind, mode=mode, path=path, pos=tuple(pos))


def save_node(pos=(0.0, 0.0), where: str = "next", folder: str = "", name: str = "{name}") -> Node:
    return Node("", SAVE, where=where, folder=folder, name=name, pos=tuple(pos))


def label_of(node: Node, reg=None) -> str:
    if node.label:
        return node.label
    if node.type == TOOL:
        t = reg.tools.get(node.tool) if reg is not None else None
        return t.name if t else node.tool
    if node.type == INPUT:
        return f"{KINDS.get(node.kind, ('', node.kind))[1].capitalize()} input"
    return "Save"


def _kind_of_exts(exts) -> str | None:
    for k, (kexts, _badge) in KINDS.items():
        if set(exts) & set(kexts):
            return k
    return None


def ports(node: Node, reg) -> list[Port]:
    if node.type == INPUT:
        return []
    if node.type == SAVE:
        return [Port(MAIN, "File", None)]
    tool = reg.tools.get(node.tool)
    if tool is None:
        return [Port(MAIN, "Input", None)]
    out = [Port(MAIN, "Input", tool.inputs.kind, tuple(tool.inputs.exts))]
    for p in tool.param_list:
        if isinstance(p, FileRef):
            out.append(Port(p.key, p.label, _kind_of_exts(p.exts), tuple(p.exts), side=True))
    return out


def output_kind(node: Node, reg) -> str | None:
    if node.type == INPUT:
        return node.kind
    if node.type == TOOL:
        t = reg.tools.get(node.tool)
        return t.output.kind if t else None
    return None


def output_ext(node: Node, reg) -> str | None:
    """The extension a node hands on, when it is known before running (None for batch inputs: any of the kind)."""
    if node.type == INPUT:
        return Path(node.path).suffix.lower() or None if node.mode == "fixed" else None
    if node.type == TOOL:
        t = reg.tools.get(node.tool)
        if t is None:
            return None
        return node.format if (node.format and node.format in t.output.formats) else t.output.ext
    return None


def compatible(port: Port, kind: str | None, ext: str | None) -> bool:
    """Can a node giving (kind, ext) feed this port?"""
    if ext and port.exts:
        return ext in port.exts
    if port.kind is None or kind is None:
        return True
    return port.kind == kind


def can_feed(wf: Workflow, src: str, dst: str, port: str, reg) -> bool:
    s, d = wf.nodes[src], wf.nodes[dst]
    if s.type == SAVE or d.type == INPUT or src == dst or wf.reaches(dst, src):
        return False
    p = next((x for x in ports(d, reg) if x.key == port), None)
    return p is not None and compatible(p, output_kind(s, reg), output_ext(s, reg))


def node_values(node: Node, tool):
    """The step's validated values (raises ParamError)."""
    return tool.values(node.values)


def seeds(wf: Workflow, reg) -> list[tuple[str, str]]:
    """(step id, parameter key) of every Seed in the steps a run needs: what a new variation draws again."""
    out = []
    for nid in sorted(wf.needed()):
        n = wf.nodes[nid]
        tool = reg.tools.get(n.tool) if n.type == TOOL else None
        if tool is not None:
            out += [(nid, p.key) for p in tool.param_list if isinstance(p, Seed)]
    return out


def reseeded(wf: Workflow, reg, rng=None) -> tuple[Workflow, dict]:
    """A copy of `wf` with a new random value for every seed and every other setting as it is: a new design from
    the same settings. Returns the copy and {step id: {key: seed}}, to report and to repeat it."""
    import random
    rng = rng or random.Random()
    out, drawn = wf.copy(), {}
    for nid, key in seeds(wf, reg):
        value = rng.randint(0, 99999)                  # the range of the inspector's dice
        out.nodes[nid].values[key] = value
        drawn.setdefault(nid, {})[key] = value
    return out, drawn


# ============================================================================ check

def _template_fields(template: str) -> list[str]:
    return re.findall(r"\{(\w*)\}", template)


def check(wf: Workflow, reg) -> list[Issue]:
    """Everything that would stop the workflow (errors) or surprise you (warnings), before it runs."""
    issues: list[Issue] = []

    def err(msg, node="", port=""):
        issues.append(Issue("error", msg, node, port))

    def warn(msg, node="", port=""):
        issues.append(Issue("warning", msg, node, port))

    if not wf.nodes:
        err("The workflow is empty: add an input, a tool and a Save.")
        return issues
    try:
        order = wf.order()
    except WorkflowError as e:
        err(str(e))
        order = list(wf.nodes)
    batch = wf.batch_inputs()
    for n in batch[1:]:
        err("Only one input can take the files of a run; make this one a fixed file.", n.id)
    saves = wf.saves()
    if not saves:
        err("Nothing is saved: add a Save after the step you want on disk.")
    feeds_save = wf.ancestors([n.id for n in saves])
    try:
        from .runtimes import missing
    except ImportError:                          # pragma: no cover - older trees without external runtimes
        def missing(_tool):
            return []

    for nid in order:
        n = wf.nodes[nid]
        name = label_of(n, reg)
        if n.type == INPUT:
            if n.kind not in KINDS:
                err(f"Unknown input kind {n.kind!r}.", nid)
            if n.mode == "fixed":
                if not n.path:
                    err("Choose the file this input stands for.", nid)
                elif not Path(n.path).is_file():
                    err(f"File not found: {n.path}", nid)
            if not wf.edges_from(nid):
                warn("This input is not connected to anything.", nid)
            continue
        if n.type == SAVE:
            if wf.edge_into(nid) is None:
                err("Connect the step whose result this Save writes.", nid, MAIN)
            if n.where == "folder" and not n.folder:
                err("Choose the folder to save in.", nid)
            if n.where == "next" and not batch:
                err("“Next to the input” needs an input that takes the files of a run; choose a folder.", nid)
            bad = [f for f in _template_fields(n.name) if f not in NAME_FIELDS]
            if bad or not n.name.strip():
                err("File name: use {name}, {stem}, {workflow} and {node}"
                    + (f" (not {{{bad[0]}}})." if bad else "; it cannot be empty."), nid)
            if re.search(r'[\\/:*?"<>|]', re.sub(r"\{\w*\}", "", n.name)):
                err("The file name cannot hold \\ / : * ? \" < > |.", nid)
            continue
        tool = reg.tools.get(n.tool)
        if tool is None:
            err(f"The tool {n.tool!r} is not installed here (a plug-in?).", nid)
            continue
        try:
            lacking = missing(tool, node_values(n, tool))
        except ParamError:
            lacking = []  # invalid settings are reported below
        if lacking:
            err(f"{tool.name} needs {', '.join(r.label for r in lacking)}, which was not found on this computer.",
                nid)
        if n.tool_version and n.tool_version != tool.version:
            warn(f"Set up with {tool.name} {n.tool_version}; this is {tool.version}. Check its settings.", nid)
        keys = {p.key for p in tool.param_list}
        gone = sorted(set(n.values) - keys)
        if gone:
            warn(f"Settings {', '.join(gone)} are no longer in {tool.name} and are ignored.", nid)
        vals = None
        try:
            vals = node_values(n, tool)
        except ParamError as e:
            err(f"{name}: {e}", nid)
        if n.format and n.format not in tool.output.formats:
            err(f"{tool.name} cannot write {n.format} files.", nid)
        for port in ports(n, reg):
            e = wf.edge_into(nid, port.key)
            if e is None:
                if not port.side:
                    err("Connect an input to this step.", nid, MAIN)
                elif vals is not None:
                    p = tool.param(port.key)
                    if vals.get(port.key) and p.visible(vals) and not Path(vals[port.key]).is_file():
                        err(f"{p.label}: file not found ({vals[port.key]}).", nid, port.key)
                continue
            src = wf.nodes[e.src]
            kind, ext = output_kind(src, reg), output_ext(src, reg)
            if not compatible(port, kind, ext):
                gives = ext or KINDS.get(kind, ("", kind or "?"))[1]
                takes = " ".join(port.exts) if port.exts else KINDS.get(port.kind, ("", port.kind))[1]
                err(f"{label_of(src, reg)} gives {gives}, but {port.label} takes {takes}.", nid, port.key)
            if port.side and vals is not None and not tool.param(port.key).visible(vals):
                warn(f"{port.label} is connected but not used with these settings.", nid, port.key)
        if vals is not None:
            for p in tool.param_list:
                if isinstance(p, Choice) and p.options_from and p.visible(vals):
                    warn(f"{p.label} depends on this step's input: it is checked when the step runs.", nid)
        if not wf.edges_from(nid):
            warn("This step's result is not saved or used: it will be skipped.", nid)
        elif nid not in feeds_save:
            warn("This step leads to no Save: it will be skipped.", nid)

    seen: dict = {}
    for n, name in planned_names(wf, reg).items():
        node = wf.nodes[n]
        place = same_file_key(node.folder).rstrip("\\/") if node.where == "folder" else "<next>"
        k = (place, same_file_key(name))
        if k in seen:
            err(f"Two Saves write the same file ({name}); change one file name.", n)
        seen[k] = n
    return issues


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error"]


# ============================================================================ names

class _Keep(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def slug(text: str) -> str:
    s = re.sub(r"[^\w\-.]+", "_", text.strip(), flags=re.UNICODE).strip("_")
    return s or "workflow"


def save_name(wf: Workflow, node: Node, incoming: str, batch_stem: str, from_label: str) -> str:
    """The file a Save writes: its template filled in, with the incoming file's extension."""
    inc = Path(incoming)
    values = {"name": inc.stem, "stem": batch_stem or inc.stem, "workflow": slug(wf.name), "node": slug(from_label)}
    return node.name.format_map(_Keep(values)).strip() + inc.suffix.lower()


def planned_names(wf: Workflow, reg, batch_name: str = "input") -> dict:
    """Save node -> file name, from the chain of tool templates (AUTO values stay 'auto'); best effort, no files."""
    names: dict = {}
    try:
        order = wf.order()
    except WorkflowError:
        return {}
    chain: dict = {}
    batch_stem = Path(batch_name).stem
    for nid in order:
        n = wf.nodes[nid]
        if n.type == INPUT:
            ext = saved_path(n.path).suffix if n.mode == "fixed" and n.path else KINDS.get(n.kind, ((".dat",), ""))[0][0]
            chain[nid] = saved_path(n.path).name if n.mode == "fixed" and n.path else f"{batch_stem}{ext}"
            continue
        e = wf.edge_into(nid)
        if e is None or e.src not in chain:
            continue
        if n.type == TOOL:
            tool = reg.tools.get(n.tool)
            if tool is None:
                continue
            try:
                vals = node_values(n, tool)
            except ParamError:
                vals = dict(n.values)
            chain[nid] = tool.output.name_for(Path(chain[e.src]), vals, output_ext(n, reg))
        else:
            names[nid] = save_name(wf, n, chain[e.src], batch_stem, label_of(wf.nodes[e.src], reg))
    return names


def save_path(wf: Workflow, node: Node, incoming: str, batch_file: Path | None, from_label: str,
              stamp: str = "") -> Path:
    """Where a Save writes; with `stamp` (Setup's "keep both") the name ends in _<stamp> and overwrites nothing."""
    name = save_name(wf, node, incoming, batch_file.stem if batch_file else "", from_label)
    folder = Path(node.folder) if node.where == "folder" else (batch_file.parent if batch_file else Path.cwd())
    dst = folder / name
    if batch_file is not None and dst.resolve() == Path(batch_file).resolve():   # never write over the input
        dst = dst.with_name(f"{dst.stem}-1{dst.suffix}")
    return stamped(dst, stamp) if stamp else dst


def auto_layout(wf: Workflow, dx: float = 270.0, dy: float = 120.0) -> None:
    """Columns by depth, left to right; each column centred."""
    try:
        order = wf.order()
    except WorkflowError:
        order = list(wf.nodes)
    depth: dict = {}
    for nid in order:
        ins = [depth.get(e.src, 0) for e in wf.edges_into(nid)]
        depth[nid] = (max(ins) + 1) if ins else 0
    cols: dict = {}
    for nid in order:
        cols.setdefault(depth[nid], []).append(nid)
    for c, ids in cols.items():
        for r, nid in enumerate(ids):
            wf.nodes[nid].pos = (c * dx, (r - (len(ids) - 1) / 2) * dy)


# ============================================================================ cache

def file_key(path: Path) -> str:
    p = Path(path).resolve()
    st = p.stat()
    return _hash(f"file|{p}|{st.st_size}|{st.st_mtime_ns}")


def _hash(obj) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


class WorkflowCache:
    """Step outputs by key. An entry is written in a partial folder, then renamed: a killed step never becomes a hit.
    Entries are dropped oldest-used first above the size limit."""

    def __init__(self, root=None, limit_gb: float = DEFAULT_LIMIT_GB):
        if root is None:
            from .paths import cache_dir
            root = cache_dir() / "workflows"
        self.root = Path(root)
        self.limit = int(float(limit_gb) * 1024 ** 3)

    def folder(self, key: str) -> Path:
        return self.root / key[:2] / key

    def lookup(self, key: str) -> dict | None:
        d = self.folder(key)
        meta = d / "entry.json"
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not (d / m.get("main", "")).is_file():
            return None
        try:
            os.utime(meta)
        except OSError:
            pass
        m["dir"] = str(d)
        return m

    def begin(self, key: str) -> Path:
        d = self.root / key[:2] / f"{key}.partial-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        d.mkdir(parents=True)
        return d

    def commit(self, key: str, partial: Path, meta: dict) -> Path:
        (partial / "entry.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        final = self.folder(key)
        if final.exists():                       # an earlier, broken entry (or a twin): the new one wins
            shutil.rmtree(final, ignore_errors=True)
        os.replace(partial, final)
        return final

    def discard(self, partial: Path) -> None:
        shutil.rmtree(partial, ignore_errors=True)

    def prune_partials(self, older_than_s: float = PARTIAL_AGE_S) -> None:
        """Drop what killed steps left. Only old ones: another worker may be writing a fresh one right now."""
        now = time.time()
        for p in self.root.glob("*/*.partial-*"):
            try:
                if now - p.stat().st_mtime < older_than_s:
                    continue
            except OSError:
                continue
            shutil.rmtree(p, ignore_errors=True)

    def entries(self) -> list[tuple[Path, float, int]]:
        out = []
        for meta in self.root.glob("*/*/entry.json"):
            d = meta.parent
            if ".partial-" in d.name:
                continue
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            out.append((d, meta.stat().st_mtime, size))
        return out

    def size(self) -> int:
        return sum(s for _d, _t, s in self.entries())

    def prune(self) -> int:
        """Drop the least recently used entries until the cache is under its limit; returns bytes freed."""
        ents = sorted(self.entries(), key=lambda x: x[1])
        total, freed = sum(s for _d, _t, s in ents), 0
        for d, _t, s in ents:
            if total - freed <= self.limit:
                break
            shutil.rmtree(d, ignore_errors=True)
            freed += s
        return freed

    def clear(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


# ============================================================================ running

@dataclass
class _Product:
    path: Path                  # the file handed on
    key: str                    # its cache key
    kind: str                   # svg | mesh | image
    extras: list = field(default_factory=list)   # other files the step wrote
    root: Path | None = None    # the folder the extras are relative to
    result: Result | None = None
    label: str = ""


def inspect_file(path: Path, kind: str):
    """The default InputInfo for a file of this kind (its summary and preview)."""
    ref = DEFAULT_INSPECT.get(kind)
    if ref is None:
        return None
    mod, _, attr = ref.partition(":")
    return getattr(importlib.import_module(mod), attr)(Path(path))


def run_workflow(wf: Workflow, batch_file, emit, cancelled, *, reg=None, action: str = "run",
                 target: str | None = None, cache: WorkflowCache | None = None, tool_cache: str = "",
                 stamp: str = "") -> Result:
    """Run the workflow on one batch file. action: run (every Save), preview (draft of `target`, nothing saved) or
    prepare (only what `target` needs; returns its input files in data["inputs"]). `stamp` suffixes every Save's
    file name (see save_path).

    Events through emit: progress, log, and node (node id, state, info) with states started, progress, cached,
    done, error. Raises WorkflowError (StepFailed for a failing step) or Cancelled."""
    if reg is None:
        from .registry import registry
        reg = registry()
    cache = cache or WorkflowCache()
    needed = wf.needed(action, target if action != "run" else None)
    problems = [i for i in errors(check(wf, reg)) if (i.node in needed if action != "run" else True)]
    if problems:
        raise WorkflowError("; ".join(i.message for i in problems[:4]))
    order = wf.order(needed)
    batch = Path(batch_file).resolve() if batch_file else None
    if wf.batch_inputs() and batch is None and any(n.id in needed for n in wf.batch_inputs()):
        raise WorkflowError("No input file: add files to run the workflow on.")
    steps = [nid for nid in order if wf.nodes[nid].type == TOOL]
    total = max(1, len(steps))
    products: dict[str, _Product] = {}
    states: dict = {}
    saved: list[str] = []
    reports: list[str] = []
    ran = hits = 0
    last: _Product | None = None
    draft_result: Result | None = None
    t_start = time.perf_counter()

    for nid in order:
        if cancelled():
            raise Cancelled()
        node = wf.nodes[nid]
        label = label_of(node, reg)
        if node.type == INPUT:
            path = batch if node.mode == "batch" else Path(node.path).resolve()
            products[nid] = _Product(path, file_key(path), node.kind, label=label)
            if action == "preview" and nid == target:
                draft_result = _file_result(path, node.kind, label)
            continue
        if node.type == SAVE:
            inc = products[wf.edge_into(nid).src]
            if action == "preview" and nid == target:
                draft_result = inc.result if (inc.result and inc.result.preview) else _file_result(inc.path, inc.kind,
                                                                                                   inc.label)
                continue
            dst = save_path(wf, node, inc.path.name, batch, inc.label, stamp)
            written = _copy_out(inc, dst)
            saved += [str(p) for p in written]
            last = inc
            states[nid] = {"state": "done", "output": str(dst)}
            emit("node", (nid, "done", {"output": str(dst), "files": [str(p) for p in written]}))
            emit("log", ("info", f"saved {dst}"))
            continue

        # ---- a tool step
        tool = reg.get(node.tool)
        index = steps.index(nid)
        up = products[wf.edge_into(nid).src]
        values = node_values(node, tool)
        side_keys = {}
        for e in wf.edges_into(nid):
            if e.port != MAIN:
                values[e.port] = str(products[e.src].path)
                side_keys[e.port] = products[e.src].key
        for p in tool.param_list:            # a file chosen in the step's settings: its contents count too
            if isinstance(p, FileRef) and p.key not in side_keys and values.get(p.key):
                try:
                    side_keys[p.key] = file_key(Path(values[p.key]))
                except OSError:
                    pass
        if any(v == AUTO for v in values.values()):
            values = resolve_auto(tool.param_list, values, inspect_input(tool, str(up.path)))
        fmt = output_ext(node, reg) if node.format else ""
        name = tool.output.name_for(Path(up.path.name), values, fmt or None)   # the chain of tool templates
        key_values = {k: v for k, v in jsonable(tool, values).items() if k not in side_keys}
        if tool.input_dependencies:
            dependencies = tool.resolve(tool.input_dependencies)(up.path, values)
            side_keys["@input-dependencies"] = sorted(file_key(Path(f)) for f in dependencies)
        key = _hash({"tool": tool.id, "version": tool.version, "values": key_values, "fmt": fmt, "in": up.key,
                     "side": side_keys})
        emit_step = _step_emitter(emit, nid, label, index, total)
        is_target = action == "preview" and nid == target

        if is_target and (hit := cache.lookup(key)) is not None:    # the real output, already made
            out = Path(hit["dir"]) / hit["main"]
            res = _file_result(out, tool.output.kind, label, "Output")
            res.report = hit.get("report") or res.report
            states[nid] = {"state": "cached", "key": key, "output": str(out)}
            emit("node", (nid, "cached", {"key": key, "output": str(out), "result": res}))
            draft_result = res
            continue
        if is_target:                            # the draft the preview shows: nothing written, never cached
            emit("node", (nid, "started", {}))
            t0 = time.perf_counter()
            res = _execute(tool, Job(tool.id, "draft", str(up.path), None, dict(values),
                                     cache_dir=str(Path(tool_cache) / tool.id) if tool_cache else ""),
                           emit_step, cancelled, nid, label, emit)
            secs = time.perf_counter() - t0
            states[nid] = {"state": "done", "seconds": secs, "key": key}
            emit("node", (nid, "done", {"result": res, "seconds": secs, "key": key, "draft": True}))
            draft_result = res
            ran += 1
            continue

        hit = cache.lookup(key)
        if hit is not None:
            d = Path(hit["dir"])
            products[nid] = _Product(d / hit["main"], key, tool.output.kind, [d / x for x in hit.get("extras", [])],
                                     d, label=label)
            hits += 1
            states[nid] = {"state": "cached", "key": key, "output": str(d / hit["main"])}
            emit("node", (nid, "cached", {"key": key, "output": str(d / hit["main"]), "report": hit.get("report", "")}))
            emit("progress", ((index + 1) / total, f"{index + 1}/{total} · {label} · from the cache"))
            if hit.get("report"):
                reports.append(f"── {label} (cached)\n{hit['report']}")
            continue

        partial = cache.begin(key)
        dst = partial / name
        emit("node", (nid, "started", {}))
        t0 = time.perf_counter()
        try:
            res = _execute(tool, Job(tool.id, "run", str(up.path), str(dst), dict(values),
                                     cache_dir=str(Path(tool_cache) / tool.id) if tool_cache else str(partial)),
                           emit_step, cancelled, nid, label, emit)
            if not dst.is_file():
                if not res.outputs and tool.output.kind == up.kind:
                    shutil.copy2(up.path, dst)   # nothing to change: the input goes on as it is
                    emit("log", ("info", f"{label}: nothing to change, passed on as it was"))
                else:
                    raise StepFailed(nid, label, "the step wrote no file")
            extras = []
            for o in res.outputs:
                po = Path(o)
                if po.resolve() == dst.resolve():
                    continue
                try:
                    extras.append(str(po.resolve().relative_to(partial.resolve())))
                except ValueError:
                    emit("log", ("warning", f"{label}: {po.name} was written outside the workflow cache"))
            meta = {"tool": tool.id, "tool_version": tool.version, "label": label, "main": name, "extras": extras,
                    "report": res.report, "status": res.status, "created": time.time()}
            final = cache.commit(key, partial, meta)
        except BaseException:
            cache.discard(partial)
            raise
        secs = time.perf_counter() - t0
        res.outputs = [str(final / name)] + [str(final / x) for x in extras]
        res.report = (res.report or "").replace(str(partial), str(final))
        products[nid] = _Product(final / name, key, tool.output.kind, [final / x for x in extras], final, res, label)
        ran += 1
        states[nid] = {"state": "done", "seconds": secs, "key": key, "output": str(final / name)}
        emit("node", (nid, "done", {"result": res, "seconds": secs, "key": key, "output": str(final / name)}))
        if res.report:
            reports.append(f"── {label}\n{res.report}")

    secs = time.perf_counter() - t_start
    if action == "prepare":
        inputs = {}
        for e in wf.edges_into(target):
            if e.src in products:
                inputs[e.port] = str(products[e.src].path)
        return Result(status="Prepared", data={"inputs": inputs, "nodes": states, "seconds": secs})
    if action == "preview":
        res = draft_result or Result(status="Nothing to preview")
        res.data = res.data if isinstance(res.data, dict) else {}
        res.data.update(nodes=states)
        return res
    preview = None
    if last is not None:
        if last.result is not None and last.result.preview:
            preview = last.result.preview
        else:
            try:
                info = inspect_file(Path(saved[0]) if saved else last.path, last.kind)
                preview = info.preview if info else None
            except Exception:             # a preview is a nicety; the files are saved
                preview = None
    stats = [Stat("steps", "Steps run", ran), Stat("cached", "From cache", hits), Stat("saved", "Saved", len(saved))]
    head = f"{wf.name}  ·  {batch.name if batch else 'fixed inputs'}  ·  {secs:.1f} s"
    return Result(outputs=saved, stats=stats, report="\n\n".join([head] + reports), preview=preview,
                  status=f"Saved {len(saved)}" if saved else "Nothing saved",
                  data={"nodes": states, "seconds": secs})


def _file_result(path: Path, kind: str, label: str, title: str = "") -> Result:
    """A file's default inspection as a Result (summary and preview); `title` renames its views."""
    info = inspect_file(path, kind)
    preview = info.preview if info else None
    if preview and title:
        preview = dict(preview, views=[dict(v, title=title) for v in preview.get("views", [])], prefer=title)
    return Result(report=f"{label}\n  {path}\n  {info.summary if info else ''}", preview=preview,
                  status="Input", changed=False)


def _step_emitter(emit, nid: str, label: str, index: int, total: int):
    def inner(kind, payload):
        if kind == "progress":
            frac, msg = payload
            emit("progress", ((index + frac) / total, f"{index + 1}/{total} · {label}" + (f" · {msg}" if msg else "")))
            emit("node", (nid, "progress", {"frac": frac, "msg": msg}))
        elif kind == "log":
            emit("log", (payload[0], f"{label}: {payload[1]}"))
    return inner


def _execute(tool, job: Job, emit_step, cancelled, nid: str, label: str, emit) -> Result:
    box: dict = {}

    def inner(kind, payload):
        if kind in TERMINAL:
            box[kind] = payload
        else:
            emit_step(kind, payload)

    execute(tool, job, inner, cancelled)
    if "cancelled" in box:
        raise Cancelled()
    if "error" in box:
        text, trace = (list(box["error"]) + [""])[:2]
        emit("node", (nid, "error", {"error": text, "trace": trace}))
        raise StepFailed(nid, label, text, trace)
    return box["done"][0]


def _copy_out(p: _Product, dst: Path) -> list[Path]:
    """Copy a step's file to dst, and its extra files (shard folders ...) beside it, keeping their own names."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p.path, dst)
    out = [dst]
    for x in p.extras:
        x = Path(x)
        if not x.is_file() or p.root is None:
            continue
        rel = x.relative_to(p.root)
        target = dst.parent / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(x, target)
        out.append(target)
    return out


def execute_job(job: Job, emit, cancelled, reg=None) -> None:
    """A workflow job (Job.workflow set) in the runner: report through emit like execute() does."""
    spec = job.workflow or {}
    try:
        wf = Workflow.from_dict(spec["workflow"])
        cache = WorkflowCache(spec.get("cache") or None, spec.get("limit_gb", DEFAULT_LIMIT_GB))
        cache.prune_partials()
        res = run_workflow(wf, job.src or None, emit, cancelled, reg=reg, action=spec.get("action", "run"),
                           target=spec.get("target"), cache=cache, tool_cache=job.cache_dir,
                           stamp=spec.get("stamp", ""))
        try:
            cache.prune()
        except OSError:
            pass
        emit("done", (res,))
    except Cancelled:
        emit("cancelled", ())
    except StepFailed as e:
        emit("error", (str(e), e.trace))
    except Exception as e:   # a broken file or a bug: reported for this input, the app goes on
        emit("error", (f"{type(e).__name__}: {e}" if not isinstance(e, WorkflowError) else str(e),
                       traceback.format_exc()))


def workflow_job(wf: Workflow, batch_file=None, action: str = "run", target: str | None = None,
                 cache_root=None, limit_gb: float = DEFAULT_LIMIT_GB, tool_cache: str = "", stamp: str = "") -> Job:
    """The Job that runs `wf` on one batch file in the runner (always the process lane in the app)."""
    return Job("workflow", action, str(batch_file) if batch_file else "", None, {}, cache_dir=tool_cache,
               workflow={"workflow": wf.to_dict(), "action": action, "target": target,
                         "cache": str(cache_root) if cache_root else "", "limit_gb": limit_gb, "stamp": stamp})
