"""
Jobs: one tool run on one input. `execute()` is the single place where a job is turned into an engine call; the
thread runner, the worker process, the CLI and the self-test all go through it.
"""
from __future__ import annotations

import time
import traceback
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .params import Params, resolve_auto
from .tool import InputInfo, Result


class Cancelled(Exception):
    pass


@dataclass
class Job:
    tool_id: str
    mode: str                     # "run" (write dst) | "analyse" (dry run) | "draft" (fast preview) | "inspect"
    src: str
    dst: str | None = None
    values: dict = field(default_factory=dict)
    id: int = 0
    group: str = ""               # queued jobs of the same group are dropped when a newer one is submitted
    cache_dir: str = ""           # where previews are written
    created: float = field(default_factory=time.time)
    workflow: dict | None = None  # a workflow run (core/workflow.py): the workflow, action, target, cache


class JobContext:
    """Handed to engines. Every call is cheap; engines should call check_cancel() inside long loops."""

    def __init__(self, emit: Callable[[str, tuple], None], cancelled: Callable[[], bool], mode: str = "run",
                 cache_dir: str = "", job_id: int = 0):
        self._emit = emit
        self._cancelled = cancelled
        self.mode = mode
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.job_id = job_id
        self._last = 0.0
        self.last_phase='starting'

    @property
    def draft(self) -> bool:
        """True for quick previews: engines may decimate the input or skip slow refinements."""
        return self.mode == "draft"

    @property
    def dry_run(self) -> bool:
        return self.mode in ("analyse", "draft")

    def progress(self, frac: float, msg: str = "") -> None:
        if msg:self.last_phase=msg
        now = time.monotonic()
        if msg or frac >= 1 or now - self._last > 0.05:   # at most ~20 updates a second
            self._last = now
            self._emit("progress", (max(0.0, min(1.0, float(frac))), msg))
        self.check_cancel()

    def log(self, msg: str, level: str = "info") -> None:
        self._emit("log", (level, str(msg)))

    def warn(self, msg: str) -> None:
        self.log(msg, "warning")

    def check_cancel(self) -> None:
        if self._cancelled():
            raise Cancelled()

    def cache_path(self, name: str) -> Path:
        base = self.cache_dir or Path.cwd()
        base.mkdir(parents=True, exist_ok=True)
        return base / f"job{self.job_id}-{name}"


DEFAULT_INSPECT = {"svg": "atlas_toolkit.geom.svg:inspect", "mesh": "atlas_toolkit.geom.mesh:inspect",
                   "image": "atlas_toolkit.geom.image:inspect"}


def inspect_input(tool, src: str) -> InputInfo | None:
    """The tool's own inspect(), else the default for its input kind (SVG drawing, mesh)."""
    fn = tool.inspect_fn()
    if fn is None and tool.inputs.kind in DEFAULT_INSPECT:
        fn = tool.resolve(DEFAULT_INSPECT[tool.inputs.kind])
    return fn(Path(src)) if fn else None


def run_job(job: Job, emit: Callable[[str, tuple], None], cancelled: Callable[[], bool], reg=None) -> None:
    """What the runner's lanes call: a workflow job, or one tool's job (looked up in the registry)."""
    if job.workflow is not None:
        from .workflow import execute_job
        execute_job(job, emit, cancelled, reg)
        return
    if reg is None:
        from .registry import registry
        reg = registry()
    try:
        tool = reg.get(job.tool_id)
    except KeyError:
        emit("error", (f"unknown tool {job.tool_id!r}", ""))
        return
    execute(tool, job, emit, cancelled)


def execute(tool, job: Job, emit: Callable[[str, tuple], None], cancelled: Callable[[], bool]) -> None:
    """Run `job` and report through emit(kind, payload): progress, log, then exactly one of
    done (Result | InputInfo), error (text, traceback) or cancelled."""
    t0=time.perf_counter()
    ctx=None
    heightfield=tool.id in ('raster-dem-to-mesh','image-to-mesh')
    try:
        if job.mode == "inspect":
            emit("done", (inspect_input(tool, job.src),))
            return
        ctx = JobContext(emit, cancelled, job.mode, job.cache_dir, job.id)
        values = Params(job.values)
        if any(v == "auto" for v in values.values()):
            values = resolve_auto(tool.param_list, values, inspect_input(tool, job.src))
            if not heightfield:
                ctx.log("auto: " + ", ".join(f"{k} = {tool.param(k).describe(values[k])}"
                                             for k, v in job.values.items() if v == "auto"))
        if heightfield:
            from atlas_toolkit.version import __version__
            ctx.log(f'job {job.id} · {job.mode} · Atlas {__version__} · {tool.id} {tool.version} · parameters '+json.dumps(dict(values),ensure_ascii=False,default=str))
        t0 = time.perf_counter()
        dst = Path(job.dst) if (job.dst and job.mode == "run") else None
        if dst is not None:
            dst.parent.mkdir(parents=True, exist_ok=True)
        result = tool.engine_fn()(ctx, Path(job.src), dst, values)
        if not isinstance(result, Result):
            raise TypeError(f"{tool.id}: engine returned {type(result).__name__}, expected Result")
        result.data = result.data if result.data is not None else {}
        if isinstance(result.data, dict):
            result.data.setdefault("seconds", time.perf_counter() - t0)
            result.data.setdefault("values", dict(values))
        emit("done", (result,))
    except Cancelled:
        if ctx is not None and heightfield:ctx.log(f'job {job.id} · {job.mode} · cancelled after {time.perf_counter()-t0:.2f}s · {ctx.last_phase}')
        emit("cancelled", ())
    except Exception as e:   # engines may raise anything on bad input; the app reports it per file
        diagnostic={'seconds':time.perf_counter()-t0,'mode':job.mode,'job_id':job.id,'values':dict(job.values),
                    'phase':ctx.last_phase if ctx else 'inspection'}
        if ctx is not None and heightfield:ctx.log(f'job {job.id} · {job.mode} · failed after {diagnostic["seconds"]:.2f}s · {diagnostic["phase"]}', 'warning')
        expected=heightfield and isinstance(e,ValueError) and any(part in str(e).lower() for part in
                   ('locked terrain','custom plate','clearance','diagonal point contacts','budget','paper size','estimated peak ram'))
        emit("error", (f"{type(e).__name__}: {e}", '' if expected else traceback.format_exc(),diagnostic))
