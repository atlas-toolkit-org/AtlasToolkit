"""
Entry point for `python -m atlas_toolkit`, the console script and the frozen exe.

    atlas-toolkit                         open the app
    atlas-toolkit drawing.svg model.stl   open the app with these files (also: drop them on the exe)
    atlas-toolkit --tool voxelizer a.stl  open a tool with files
    atlas-toolkit list | describe | run | presets | self-test ...   command line (see core/cli.py)
"""
from __future__ import annotations

import multiprocessing
import sys

COMMANDS = {"list", "describe", "run", "presets", "self-test", "--version", "-h", "--help"}


def _ancestors() -> list[int]:
    """Process ids above this one, nearest first (Windows)."""
    import ctypes
    import os
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k.CreateToolhelp32Snapshot(2, 0)                  # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    parent, e = {}, Entry()
    e.dwSize = ctypes.sizeof(Entry)
    ok = k.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        parent[e.th32ProcessID] = e.th32ParentProcessID
        ok = k.Process32NextW(snap, ctypes.byref(e))
    k.CloseHandle(snap)
    out, pid = [], os.getpid()
    while len(out) < 8 and parent.get(pid) and parent[pid] not in out:
        pid = parent[pid]
        out.append(pid)
    return out


def _attach_console() -> None:
    """The exe is windowed, so command-line output needs somewhere to go:
    - output already redirected (a pipe or a file, as from atlas-toolkit.exe): keep it;
    - started from a terminal: write to that terminal's console. The one-file exe runs as a child of its own
      unpacker, which has no console, so look up the process tree for the nearest ancestor that has one;
    - neither (Explorer): output goes nowhere instead of failing on a missing sys.stdout.
    The shell does not wait for a windowed exe; for the console companion atlas-toolkit.exe it does."""
    import os
    if sys.platform == "win32" and getattr(sys, "frozen", False) and sys.stdout is None:
        try:
            import ctypes
            k = ctypes.windll.kernel32
            if k.AttachConsole(-1) or any(k.AttachConsole(pid) for pid in _ancestors()):
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
                sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
                sys.stdout.write("\n")                       # the prompt came back already: start on a new line
        except OSError:
            pass
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _close_splash() -> None:
    import os
    if "_PYI_SPLASH_IPC" not in os.environ:   # only the one-file build has a splash, and this module complains without one
        return
    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:
        pass


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    from .core.procs import keep_bundled_data
    keep_bundled_data()
    if argv and argv[0] in COMMANDS:
        _close_splash()
        _attach_console()
        from .core.cli import main as cli_main
        return cli_main(argv)
    return gui_main(argv)


def gui_main(argv: list | None = None) -> int:
    from .ui.app import run_app
    return run_app(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    multiprocessing.freeze_support()   # the worker process of the frozen exe starts here
    sys.exit(main())
