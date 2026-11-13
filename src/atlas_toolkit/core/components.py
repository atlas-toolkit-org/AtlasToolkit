"""
`atlas-toolkit components`: the optional components (core/runtimes) from the command line, as Setup › Components shows
them. A runtime with an installer module (Runtime.installer) can be installed, checked and removed here too, on any OS,
with or without a display. Nothing here names a runtime.

    atlas-toolkit components [list]
    atlas-toolkit components install KEY [--build B] [--with-model] [--yes]
    atlas-toolkit components check KEY [--model M] [image]
    atlas-toolkit components remove KEY [--models] [--yes]
"""
from __future__ import annotations

import sys
import threading
import time

from . import runtimes
from .paths import display_path

EXIT_FAILED, EXIT_REFUSED, EXIT_CANCELLED = 1, 2, 130
MARK = {"ok": "ok  ", "info": "info", "warn": "WARN", "fail": "FAIL"}


def add_parser(sub) -> None:
    c = sub.add_parser("components", help="the optional components: what is found, and install, check or remove them",
                       description="The optional components some tools use. Those with an installer can be installed, "
                                   "checked and removed here, as in Setup › Components.")
    cs = c.add_subparsers(dest="action")
    cs.add_parser("list", help="each component: found or not, where, and what this computer has for it")
    ci = cs.add_parser("install", help="install a component (shows what it needs first, then asks)")
    ci.add_argument("key", help="the component: " + ", ".join(k for k, r in runtimes.RUNTIMES.items() if r.installer))
    ci.add_argument("--build", default="auto", help="which build to install (default: the one detected for this "
                                                   "computer; 'components list' shows them)")
    ci.add_argument("--with-model", action="store_true", help="also download the default model now")
    ci.add_argument("--yes", "-y", action="store_true", help="do not ask before starting")
    ck = cs.add_parser("check", help="prove an installed component works; --model also runs one (downloads it once)")
    ck.add_argument("key")
    ck.add_argument("--model", help="a model to run, e.g. dpt-beit-large-512")
    ck.add_argument("image", nargs="?", help="a photo to run it on (default: a synthetic scene)")
    cr = cs.add_parser("remove", help="delete what the installer put on this computer")
    cr.add_argument("key")
    cr.add_argument("--models", action="store_true", help="also delete the downloaded models")
    cr.add_argument("--yes", "-y", action="store_true", help="do not ask first")


def _installer(key: str):
    rt = runtimes.RUNTIMES.get(key)
    if rt is None or not rt.installer:
        have = ", ".join(k for k, r in runtimes.RUNTIMES.items() if r.installer)
        print(f"no installer for {key!r}: {have} can be installed here", file=sys.stderr)
        return None
    return rt.installer_module()


def _ask(question: str) -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        print(f"{question} (no terminal to answer: pass --yes)", file=sys.stderr)
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def command(args) -> int:
    if args.action in (None, "list"):
        return list_components()
    inst = _installer(args.key)
    if inst is None:
        return EXIT_REFUSED
    if args.action == "install":
        return install(inst, args)
    if args.action == "check":
        ok, _, lines = inst.check(args.model, args.image, _Printer(verbose=True))
        for line in lines:
            print(line)
        return 0 if ok else EXIT_FAILED
    return remove(inst, args)


def list_components() -> int:
    for key, rt in runtimes.RUNTIMES.items():
        found = runtimes.find(key)
        print(f"{key:<12} {rt.label}: " + (f"found, {found}" if found else "not found"))
        inst = rt.installer_module()
        if inst is None:
            continue
        st = inst.status()
        if st["ours"] and st["stamp"]:
            s = st["stamp"]
            print(f"{'':12} installed by Atlas on {s.get('date', '?')[:10]}: {s.get('label')} · torch {s.get('torch')} · "
                  f"{s.get('device')}" + ("  (an update is available: install again)" if st["update"] else ""))
        report = inst.detect()
        print(f"{'':12} this computer: {report.summary()}")
        plan = inst.plan("auto", online=False, report=report)
        print(f"{'':12} would install: {plan.label}. {plan.choice.reason}")
        if plan.choice.advice:
            print(f"{'':12} {plan.choice.advice}")
        print(f"{'':12} builds: " + "; ".join(f"{k} = {label}" for k, label, _ in inst.builds()))
    return 0


class _Printer:
    """Shows an install's stages and progress: a line that redraws on a terminal, a line every 10 s in a log."""

    def __init__(self, stages=(), verbose: bool = False):
        self.stages, self.verbose = list(stages), verbose
        self.tty = sys.stdout.isatty()
        self.last = 0.0
        self.width = 0

    def _clear(self) -> None:
        if self.tty and self.width:
            sys.stdout.write("\r" + " " * self.width + "\r")
            self.width = 0

    def stage(self, index: int, state: str, note: str = "") -> None:
        self._clear()
        title = self.stages[index][1] if index < len(self.stages) else ""
        tag = f"[{index + 1}/{len(self.stages)}]"
        if state == "running":
            print(f"{tag} {title} …", flush=True)
        elif state in ("done", "failed", "skipped"):
            print(f"{tag} {state}" + (f" ({note})" if note else ""), flush=True)

    def progress(self, done: float, total: float, rate: float, eta) -> None:
        now = time.monotonic()
        if not self.tty and now - self.last < 10:
            return
        self.last = now
        pct = 100 * done / total if total else 0
        left = f" · about {eta / 60:.0f} min left" if eta and eta > 90 else f" · {eta:.0f} s left" if eta else ""
        text = f"    {pct:5.1f}% · {done / 1024:.2f} of {total / 1024:.2f} GB · {rate:.1f} MB/s{left}"
        if self.tty:
            sys.stdout.write("\r" + text.ljust(self.width))
            self.width = len(text)
            sys.stdout.flush()
        else:
            print(text, flush=True)

    def log(self, line: str) -> None:
        if self.verbose:
            self._clear()
            print(f"    {line}", flush=True)


def install(inst, args) -> int:
    from .settings import AppSettings
    settings = AppSettings()
    try:
        plan = inst.plan(args.build, args.with_model, settings=settings)
    except ValueError as e:
        print(e, file=sys.stderr)
        return EXIT_REFUSED
    print("Ready to install\n")
    for head, text in plan.summary():
        print(f"  {head + ':':<10} {text}")
    print(f"  {'':<10} ({plan.folders.target})\n")
    for c in plan.checks:
        print(f"  {MARK[c.level]}  {c.title}: {c.text}")
    print()
    if not plan.ok():
        print("Not started: fix what is marked FAIL above, then run this again.", file=sys.stderr)
        return EXIT_REFUSED
    if not args.yes and not _ask("Start the install?"):
        return EXIT_REFUSED
    printer = _Printer(plan.stages())
    cancelled = threading.Event()
    done: list = []
    worker = threading.Thread(target=lambda: done.append(inst.install(plan, printer, cancelled, None, settings)), daemon=True)
    worker.start()
    while worker.is_alive():           # Ctrl+C cancels cleanly: the install stops its child and removes what it began
        try:
            worker.join(0.5)
        except KeyboardInterrupt:
            if not cancelled.is_set():
                cancelled.set()
                print("\ncancelling …", file=sys.stderr, flush=True)
    if not done:
        return EXIT_FAILED
    result = done[0]
    printer._clear()
    print()
    for line in result.lines():
        print(line)
    if result.ok and result.folder:
        print(f"({result.folder})")
    return 0 if result.ok else EXIT_CANCELLED if result.cancelled else EXIT_FAILED


def remove(inst, args) -> int:
    st = inst.status()
    print(f"This deletes {display_path(st['folder'])} and the installer's own files beside it"
          + (", and the downloaded models." if args.models else " (downloaded models are kept: --models removes them)."))
    if not args.yes and not _ask("Remove it?"):
        return EXIT_REFUSED
    try:
        freed, gone = inst.remove(None, args.models)
    except Exception as e:      # a file in use: say which
        print(f"not removed: {getattr(e, 'message', e)}", file=sys.stderr)
        return EXIT_FAILED
    for g in gone:
        print(f"removed {g}")
    print(f"freed {freed / 1024:.1f} GB" if gone else "nothing to remove")
    return 0
