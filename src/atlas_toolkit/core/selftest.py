"""
Self-test: run every tool on a generated sample input, in this process and in a spawned worker process (the way
the app runs heavy tools). It runs after every build against the frozen exe, which catches modules PyInstaller
missed. Exit code 0 means every tool produced a signed output.

--strict is what a release must pass: every bundled helper starts, a tool that lacks one is a failure instead of a skip,
and each tool's test_variants run too. External programs (Blender, the depth runtime) may still be absent.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

from .io import output_path
from .job import Job, execute
from .registry import registry


def make_sample(tool, folder: Path) -> Path:
    if tool.sample:
        return tool.resolve(tool.sample)(folder)
    from . import samples
    return samples.for_kind(tool.inputs.kind, folder)


def signed(tool, path: Path) -> bool:
    if not tool.signature:
        return True
    sig = tool.signature
    if sig.startswith("<desc>"):
        sig = sig[len("<desc>"):-len("</desc>")]
    return sig.encode() in Path(path).read_bytes()[:8192]


def run_tool(tool, folder: Path, in_worker: bool = False, variant: dict | None = None) -> tuple[bool, str]:
    src = make_sample(tool, folder)
    values = tool.values({**tool.test_values, **(variant or {})})
    dst = output_path(tool, src, values)
    job = Job(tool.id, "run", str(src), str(dst), dict(values), cache_dir=str(folder / "cache"))
    box: dict = {}
    if in_worker:
        from .runner import Runner
        import threading
        ev = threading.Event()

        def on_event(_jid, kind, payload):
            if kind in ("done", "error", "cancelled"):
                box[kind] = payload
                ev.set()

        r = Runner(on_event)
        r.submit(job, "process")
        ev.wait(300)
        r.shutdown()
    else:
        execute(tool, job, lambda k, p: box.__setitem__(k, p) if k in ("done", "error", "cancelled") else None,
                lambda: False)
    if "done" not in box:
        err = box.get("error", ("timed out", ""))
        return False, f"{err[0]}\n{err[1]}".strip()
    res = box["done"][0]
    if not res.outputs:
        return False, "no output written"
    missing = [o for o in res.outputs if not Path(o).exists()]
    if missing:
        return False, f"missing outputs {missing}"
    if not signed(tool, Path(res.outputs[0])):
        return False, f"output not signed with {tool.signature}"
    return True, f"{len(res.outputs)} output(s)"


def run_workflow_check(folder: Path) -> tuple[bool, str]:
    """A workflow run in the worker process: sample mesh → Mesh Shatter → Hatch Maker (contours) → Overlap
    Cleaner → Save, with its own cache. Checks the workflow path (runner, cache, chained engines) of the build."""
    import threading
    from . import samples
    from . import workflow as W
    from .runner import Runner
    reg = registry()
    if not all(t in reg.tools for t in ("mesh-shatter", "stl-hatch-maker", "svg-overlap-cleaner")):
        return True, "skipped (sample tools not installed)"
    src = samples.mesh(folder)
    hm = reg.get("stl-hatch-maker")
    wf = W.Workflow(name="self-test")
    nodes = [wf.add(W.input_node("mesh")),
             wf.add(W.tool_node(reg.get("mesh-shatter"), values={"num_pieces": 6})),
             wf.add(W.tool_node(hm, values=dict(hm.test_values, style="contours", levels=12))),
             wf.add(W.tool_node(reg.get("svg-overlap-cleaner"), values={"mode": "duplicates"})),
             wf.add(W.save_node(where="folder", folder=str(folder / "out")))]
    for a, b in zip(nodes, nodes[1:]):
        wf.connect(a.id, b.id)
    problems = W.errors(W.check(wf, reg))
    if problems:
        return False, problems[0].message
    box: dict = {}
    ev = threading.Event()

    def on_event(_jid, kind, payload):
        if kind in ("done", "error", "cancelled"):
            box[kind] = payload
            ev.set()

    r = Runner(on_event)
    r.submit(W.workflow_job(wf, src, cache_root=folder / "cache", tool_cache=str(folder / "toolcache")), "process")
    ev.wait(300)
    r.shutdown()
    if "done" not in box:
        err = box.get("error", ("timed out", ""))
        return False, f"{err[0]}\n{err[1]}".strip()
    out = [Path(o) for o in box["done"][0].outputs]
    if not out or not out[0].exists():
        return False, "no output saved"
    if not signed(reg.get("svg-overlap-cleaner"), out[0]):
        return False, "output not signed"
    return True, f"4 steps → {out[0].name}"


def check_helpers() -> list[tuple[str, bool, str]]:
    """Start every native helper once: (name, it works, its version or what is wrong)."""
    from . import runtimes
    results = []
    for key in runtimes.HELPERS:
        stem = f"atlas-{key}-core"
        exe = runtimes.find(key)
        if exe is None:
            results.append((stem, False, "not found"))
            continue
        try:
            results.append((stem, True, runtimes.check_helper(exe, stem)))
        except (RuntimeError, OSError) as e:
            results.append((stem, False, str(e)))
    return results


def run_all(keep: bool = False, tools: list | None = None, report: str | None = None, strict: bool = False) -> int:
    import platform
    from ..version import __version__
    reg = registry()
    folder = Path(tempfile.mkdtemp(prefix="atlas_selftest_"))
    failures = len(reg.broken)
    lines: list = []

    def say(text: str) -> None:
        lines.append(text)
        print(text)

    say(f"Atlas Toolkit {__version__} self-test · Python {platform.python_version()} · "
        f"{'frozen' if getattr(sys, 'frozen', False) else 'source'} · {len(reg.tools)} tools")
    for b in reg.broken:
        say(f"BROKEN  {b.name}: {b.error.splitlines()[0]}")
    from .runtimes import HELPERS, missing
    if strict:
        for stem, ok, msg in check_helpers():
            failures += not ok
            say(f"{'ok    ' if ok else 'FAIL  '}  {stem:<24}           {msg}")
    for t in reg.ordered():
        if tools and t.id not in tools:
            continue
        variants = [None, *t.test_variants] if strict else [None]
        for number, variant in enumerate(variants):
            name = t.id if variant is None else f"{t.id} {number}"
            lacking = missing(t, t.values({**t.test_values, **(variant or {})}))
            helpers = [r.label for r in lacking if r.key in HELPERS]
            if strict and helpers:     # it should have come with the build
                failures += 1
                say(f"FAIL    {name:<24}           needs {', '.join(helpers)} (not found)")
                continue
            if lacking:            # an external program is not installed here: not a failure of the build
                say(f"skip      {name:<24}           needs {', '.join(r.label for r in lacking)} (not found)")
                continue
            sub = folder / f"{t.id}-{number}"
            sub.mkdir()
            t0 = time.perf_counter()
            try:
                ok, msg = run_tool(t, sub, in_worker=t.runs_in == "process", variant=variant)
            except Exception as e:   # a crash in the harness itself still counts as a failure, not an abort
                ok, msg = False, f"{type(e).__name__}: {e}"
            failures += not ok
            say(f"{'ok    ' if ok else 'FAIL  '}  {name:<24} {time.perf_counter() - t0:6.2f} s  {msg}")
    if not tools or "workflow" in tools:
        t0 = time.perf_counter()
        sub = folder / "workflow"
        sub.mkdir()
        try:
            ok, msg = run_workflow_check(sub)
        except Exception as e:
            ok, msg = False, f"{type(e).__name__}: {e}"
        failures += not ok
        say(f"{'ok    ' if ok else 'FAIL  '}  {'workflow':<24} {time.perf_counter() - t0:6.2f} s  {msg}")
    say(f"\n{'all tools passed' if not failures else f'{failures} failure(s)'}")
    if keep:
        say(f"outputs kept in {folder}")
    else:
        shutil.rmtree(folder, ignore_errors=True)
    if report:
        Path(report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.flush()
    return 1 if failures else 0
