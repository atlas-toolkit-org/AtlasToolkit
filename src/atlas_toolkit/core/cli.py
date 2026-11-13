"""
Command line, generated from the tool specs (no tool writes its own parser):

    atlas-toolkit list
    atlas-toolkit describe voxelizer
    atlas-toolkit run voxelizer model.stl --edge auto --shape sphere [--out-dir DIR | -o OUT] [--preset NAME]
    atlas-toolkit run stipple model.stl --preset-file my-look.atlaspreset
    atlas-toolkit run svg-overlap-cleaner *.svg --dry-run
    atlas-toolkit presets list [TOOL]          your presets and installed packs
    atlas-toolkit presets import FILE...       .atlaspreset into your presets; .atlaspack (or a .zip of packs) installs
    atlas-toolkit presets inspect FILE...      what a file holds, who signed it, what importing it would do
    atlas-toolkit self-test
    atlas-toolkit components [list | install KEY | check KEY | remove KEY]   the optional components (core/components)

Runs happen in this process, one input after the other, with progress on stderr.
"""
from __future__ import annotations

import argparse
import glob
import sys
import time
from pathlib import Path

from ..version import __version__
from .history import History, RunRecord
from .io import expand_inputs, output_path
from .job import Job, execute
from . import prefs, runtimes
from .params import ParamError, Section, resolve
from .registry import registry


def build_parser() -> argparse.ArgumentParser:
    reg = registry()
    ap = argparse.ArgumentParser(prog="atlas-toolkit", description="Atlas pen-plotter toolkit.")
    ap.add_argument("--version", action="version", version=f"Atlas Toolkit {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list the tools")
    d = sub.add_parser("describe", help="show a tool's parameters and presets")
    d.add_argument("tool")
    st = sub.add_parser("self-test", help="run every tool on a generated sample input")
    st.add_argument("--keep", action="store_true", help="keep the outputs (prints the folder)")
    st.add_argument("--report", help="also write the results to this text file (for builds and scripts)")
    st.add_argument("--strict", action="store_true",
                    help="what a release must pass: every bundled helper starts, a missing one fails, variants run too")
    st.add_argument("--tools", nargs="*", help="only these tool ids")
    pr = sub.add_parser("presets", help="list your presets and installed preset packs, or load preset files")
    prs = pr.add_subparsers(dest="action", required=True)
    pl = prs.add_parser("list", help="the presets each tool is offered (yours, then each pack's)")
    pl.add_argument("tool", nargs="?", help="only this tool")
    pi = prs.add_parser("import", help="add .atlaspreset files to your presets; install .atlaspack packs (a .zip "
                                       "holding packs or preset files works too)")
    pi.add_argument("files", nargs="+")
    pi.add_argument("--force", action="store_true",
                    help="also install what has warnings (changed, unsigned) and older versions than installed")
    pi.add_argument("--trust", action="store_true",
                    help="trust the publishers who signed these packs from now on (check their key first: inspect)")
    pn = prs.add_parser("inspect", help="show what files hold and what importing them would do, changing nothing")
    pn.add_argument("files", nargs="+")
    from .components import add_parser as add_components
    add_components(sub)
    run = sub.add_parser("run", help="run a tool on files")
    tools = run.add_subparsers(dest="tool", required=True, metavar="TOOL")
    for t in reg.ordered():
        tp = tools.add_parser(t.id, aliases=list(t.aliases), help=t.summary,
                              description=f"{t.name} {t.version}: {t.summary}")
        tp.add_argument("inputs", nargs="+", help="input files or folders (globs allowed)")
        tp.add_argument("-o", "--output", help="output file (single input only)")
        tp.add_argument("--out-dir", help="write outputs into this folder (default: next to each input)")
        tp.add_argument("--format", help="output format for mesh tools: " + " ".join(t.output.formats)
                        if t.output.formats else argparse.SUPPRESS)
        tp.add_argument("--preset", help="start from a preset: its name, 'source:name' (source: mine or a "
                                         "pack's collection) or its uid; 'presets list' shows them")
        tp.add_argument("--preset-file", help="start from the preset for this tool in an .atlaspreset file")
        tp.add_argument("--dry-run", action="store_true", help="analyse and report, write nothing")
        tp.add_argument("--force", action="store_true", help="overwrite existing outputs")
        tp.add_argument("--suffix", action="store_true",
                        help="keep existing outputs: write the new ones with a date-time suffix")
        tp.add_argument("-q", "--quiet", action="store_true")
        for p in t.param_list:
            p.add_cli(tp)
    return ap


def _utf8_streams() -> None:
    """Piped or redirected output is UTF-8 (names and arrows), whatever the console code page is."""
    for s in (sys.stdout, sys.stderr):
        if s is not None and hasattr(s, "reconfigure") and not s.isatty():
            s.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list | None = None) -> int:
    _utf8_streams()
    args = build_parser().parse_args(argv)
    reg = registry()
    if args.command == "list":
        for cat, tools in reg.by_category():
            print(cat)
            for t in tools:
                print(f"  {t.id:<24} {t.name} {t.version}  {t.io_badge}  {t.summary}")
        for b in reg.broken:
            print(f"  ! {b.name}: {b.error.splitlines()[0]}")
        return 0
    if args.command == "describe":
        if args.tool not in reg.tools:
            print(f"unknown tool {args.tool!r}: 'list' shows the tool ids", file=sys.stderr)
            return 2
        return describe(reg.get(args.tool))
    if args.command == "presets":
        return presets_command(reg, args)
    if args.command == "components":
        from .components import command
        return command(args)
    if args.command == "self-test":
        from .selftest import run_all
        return run_all(keep=args.keep, tools=args.tools, report=args.report, strict=args.strict)
    return run(reg.get(args.tool), args)


def describe(t) -> int:
    print(f"{t.name} {t.version}  ({t.id})  {t.io_badge}\n{t.summary}\n")
    if t.description:
        print(t.description + "\n")
    print(f"output   {t.output.template}" + (f"   formats: {' '.join(t.output.formats)}" if t.output.formats else ""))
    for ext, note in t.output.format_notes.items():
        print(f"         {ext}: {note}")
    print(f"engine   {t.package}{t.engine}   runs in: {t.runs_in}")
    for line in runtimes.describe(t):
        print(f"requires {line}")
    print()
    for item in t.params:
        group = item.params if isinstance(item, Section) else [item]
        if isinstance(item, Section):
            print(item.title.upper())
        for p in group:
            extra = ""
            if hasattr(p, "min") and p.min not in (None,) and abs(float(p.min)) < 1e9:
                extra = f"  [{p.min} .. {p.max}]"
            if getattr(p, "choices", None):
                extra = "  {" + ", ".join(str(v) for v, _ in p.pairs()) + "}"
            if getattr(p, "suggest", None):
                extra += "  (accepts auto)"
            print(f"  {p.flag:<24} {p.label}: {p.describe(p.validate(None))}{extra}")
            if p.help:
                print(f"  {'':<24} {p.help}")
    groups = _library().groups(t)
    if groups:
        print("\nPRESETS")
        for g in groups:
            print(f"  {g.title}")
            for p in g.presets:
                print(f"    {p.name}" + (f"  — {p.tip}" if p.tip else ""))
    return 0


def _library():
    from .presets import PresetLibrary
    return PresetLibrary()


def presets_command(reg, args) -> int:
    from .presets import PresetFormatError
    lib = _library()
    if args.action == "list":
        if args.tool and args.tool not in reg.tools:
            print(f"unknown tool {args.tool!r}: 'list' shows the tool ids", file=sys.stderr)
            return 2
        for pack in lib.packs():
            print(f"pack  {pack.title}  (version {pack.pack_version}, {len(pack.presets)} presets, "
                  f"{pack.status_label.lower()})")
            if pack.signer is not None:
                print(f"      signed by {pack.signer.publisher}, key {pack.signer.fingerprint}")
            for problem in pack.problems:
                print(f"      ! {problem}")
        for path, error in lib.broken:
            print(f"pack  ! {path.name}: {error}")
        tools = [reg.get(args.tool)] if args.tool else reg.ordered()
        for t in tools:
            groups = lib.groups(t)
            if not groups:
                continue
            print(t.id)
            for g in groups:
                for p in g.presets:
                    print(f"  {g.source}:{p.name}" + (f"   [{p.uid}]" if not p.mine else ""))
        return 0
    try:
        plan = lib.plan_install([Path(f) for f in args.files], registry=reg)
    except (PresetFormatError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for item in plan.items:
        print(item.describe())
        if item.pack is not None and item.pack.signer is not None:
            print(f"  key {item.pack.signer.fingerprint}")
        for w in item.warnings:
            print(f"  ! {w}")
    for what, why in plan.errors:
        print(f"{what}: {why}", file=sys.stderr)
    if args.action == "inspect":
        if plan.untrusted:
            print("Publishers this app does not know yet: import with --trust to trust them, after checking the key "
                  "against the one on the publisher's own website.")
        return 2 if plan.errors and not plan.items else 0
    lib.install(plan, accept_warnings=args.force, downgrade=args.force, trust=plan.untrusted if args.trust else (),
                registry=reg)
    for item in plan.items:
        if item.done and item.report is not None:
            r = item.report
            if r.pack is not None:
                print(f"installed {r.pack.title} ({r.pack.status_label.lower()})")
            else:
                print(f"added {len(r.added)} preset(s) from {item.source}, {r.skipped} already there"
                      + (f"; tools not installed: {', '.join(r.unknown_tools)}" if r.unknown_tools else ""))
                for old, new in r.renamed:
                    print(f"  {old!r} saved as {new!r} (the name was taken)")
        elif item.installs and item.warnings:
            print(f"skipped {item.pack.title if item.pack else item.source}: it has warnings (--force installs it)")
    return 2 if plan.errors or any(i.action == "blocked" for i in plan.items) else 0


def _start_values(t, args):
    """The values a run starts from: a preset (--preset or --preset-file) or the tool's defaults; None on error."""
    from .presets import PresetFormatError, apply, read_preset_file
    preset = None
    if getattr(args, "preset_file", None):
        try:
            found = [p for p in read_preset_file(Path(args.preset_file))
                     if p.tool == t.id or p.tool in t.aliases]
        except PresetFormatError as e:
            print(f"error: {e}", file=sys.stderr)
            return None
        if not found:
            print(f"error: {args.preset_file} holds no preset for {t.id}", file=sys.stderr)
            return None
        preset = found[0]
    elif getattr(args, "preset", None):
        hits = _library().find(t, args.preset)
        if not hits:
            print(f"error: no preset {args.preset!r} for {t.id}; 'presets list {t.id}' shows them", file=sys.stderr)
            return None
        if len(hits) > 1:
            print(f"error: {len(hits)} presets are called {args.preset!r}; name one of: "
                  + ", ".join(f"'{p.source}:{p.name}'" for p in hits), file=sys.stderr)
            return None
        preset = hits[0]
    if preset is None:
        return dict(t.defaults())
    values, problems = apply(t, preset)
    for problem in problems:
        print(f"warning: preset {preset.name!r}: {problem}", file=sys.stderr)
    return dict(values)


def run(t, args) -> int:
    base = _start_values(t, args)
    if base is None:
        return 2
    try:
        base.update({p.key: getattr(args, p.key) for p in t.param_list if getattr(args, p.key) is not None})
        values = resolve(t.param_list, base)
    except ParamError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    lacking = runtimes.missing(t, values)
    if lacking:
        for rt in lacking:
            print(f"{t.name} needs {rt.label} {rt.need}, which was not found. {rt.hint}"
                  + (f" Or set {rt.env} to the program." if rt.env else ""), file=sys.stderr)
        return 2
    paths = []
    for pattern in args.inputs:
        paths += glob.glob(pattern) or [pattern]
    inputs = expand_inputs(t, paths)
    if not inputs:
        print(f"no {t.inputs.kind} inputs found", file=sys.stderr)
        return 2
    if args.output and len(inputs) > 1:
        print("-o/--output needs a single input; use --out-dir", file=sys.stderr)
        return 2
    hist, failures = History(), 0
    mark = prefs.stamp()                   # one --suffix stamp for the whole run
    for src in inputs:
        dst = Path(args.output) if args.output else output_path(
            t, src, values, "folder" if args.out_dir else "next", args.out_dir or "", getattr(args, "format", "") or "")
        if not args.dry_run and dst.exists() and not args.force:
            if not args.suffix:
                print(f"skip {src.name}: {dst.name} exists (use --force or --suffix)", file=sys.stderr)
                continue
            dst = prefs.stamped(dst, mark)
        job = Job(t.id, "analyse" if args.dry_run else "run", str(src), str(dst), dict(values))
        box: dict = {}
        t0 = time.perf_counter()

        def emit(kind, payload, name=src.name):
            if kind == "progress" and not args.quiet:
                frac, msg = payload
                print(f"\r  {name}: {frac * 100:5.1f}%  {msg[:60]:<60}", end="", file=sys.stderr, flush=True)
            elif kind == "log" and not args.quiet:
                print(f"\n  [{payload[0]}] {payload[1]}", file=sys.stderr)
            elif kind in ("done", "error", "cancelled"):
                box[kind] = payload

        execute(t, job, emit, lambda: False)
        if not args.quiet:
            print("\r" + " " * 90 + "\r", end="", file=sys.stderr)
        secs = time.perf_counter() - t0
        if "error" in box:
            failures += 1
            print(f"{src.name}: ERROR {box['error'][0]}", file=sys.stderr)
            hist.append(RunRecord(t.id, t.version, "error", str(src), values=_jsonable(values), seconds=secs,
                                  message=box["error"][0], toolkit_version=__version__))
            continue
        res = box["done"][0]
        print(res.report.rstrip() if res.report else f"{src.name}: done")
        if res.outputs:
            for o in res.outputs:
                print(f"  -> {o}")
        if not args.dry_run:
            hist.append(RunRecord(t.id, t.version, "done", str(src), outputs=list(res.outputs),
                                  values=_jsonable(values), seconds=secs, toolkit_version=__version__,
                                  stats={s.key: s.value for s in res.stats}))
    return 1 if failures else 0


def _jsonable(values: dict) -> dict:
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in values.items()}
