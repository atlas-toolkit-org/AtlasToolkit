"""
Child processes, one way on every OS: the environment they start with, and that none outlives the job that started it.
No Qt: the worker process and the command line use it too.

A worker process runs the heavy tools, and a tool may start a native helper, Blender or the depth runtime under it.
Cancel has to stop all of them, even a worker stuck inside C++, and so does a crash of the worker or of the window:

- Windows: `spawn` ties each child to the process that started it with a job object, and the system does the rest.
- POSIX: each worker leads its own process group (`lead_process_group`). The runner kills the whole group (`kill_tree`),
  and `watch_parent` takes the group down if the window's process dies without a word.

If a worker and its window die in the same instant, a one-shot helper runs on to its end: nothing is left to notice.
"""
from __future__ import annotations

import multiprocessing
import os
import signal
import subprocess
import sys
import threading

WINDOWS = sys.platform == "win32"
# Inherited settings that would point a frozen app's GIS libraries at another install (QGIS, conda) instead of its own.
FOREIGN_DATA = ("GDAL_DATA", "GDAL_DRIVER_PATH", "PROJ_DATA", "PROJ_LIB")


def child_env(**extra) -> dict:
    """The environment for a program that is not part of this app. A frozen app runs with its own libraries first on
    LD_LIBRARY_PATH, and a child must not load those instead of its own (the bootloader keeps the old value in _ORIG)."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False) and not WINDOWS:
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    env.update(extra)
    return env


def keep_bundled_data() -> None:
    """Once at startup: a frozen app reads its own GDAL and PROJ data, whatever another install left in the environment."""
    if getattr(sys, "frozen", False):
        for name in FOREIGN_DATA:
            os.environ.pop(name, None)


# ----------------------------------------------------------------------------- children
def spawn(args, **kwargs) -> subprocess.Popen:
    """Popen for a helper or an external program: this environment, no console window, tied to this process on Windows.
    Call `finish` (or `release`, once it has exited) when done with it."""
    kwargs.setdefault("env", child_env())
    if WINDOWS:
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    proc = subprocess.Popen(args, **kwargs)
    proc.atlas_job = _tie_to_this_process(proc)
    return proc


def finish(proc: subprocess.Popen) -> None:
    """Stop a child if it still runs, and let go of it."""
    if proc.poll() is None:
        proc.kill()
    proc.wait()
    release(proc)


def release(proc: subprocess.Popen) -> None:
    """Let go of a child's job object (closing it kills the child, so only once the child has exited)."""
    job, proc.atlas_job = getattr(proc, "atlas_job", None), None
    if job:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(wintypes.HANDLE(job))


def _tie_to_this_process(proc) -> object | None:
    """Put the child in a Windows job object that kills it when this process ends (the handle is kept on the child;
    closing it, or dying, kills the child)."""
    if not WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                                                  "OtherOperationCount", "ReadTransferCount",
                                                  "WriteTransferCount", "OtherTransferCount")]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):   # extended limits
        return None
    if not k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(proc._handle))):
        return None
    return job


# ----------------------------------------------------------------------------- workers
def lead_process_group() -> None:
    """In a worker, first thing: become the leader of a new group, which everything it starts then belongs to."""
    if not WINDOWS:
        try:
            os.setsid()
        except OSError:          # already leads one
            pass


def watch_parent() -> None:
    """In a worker: if the process that started it dies, stop at once, with everything started here. A worker would
    otherwise wait for its next job forever."""
    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def wait() -> None:
        parent.join()
        if not WINDOWS and os.getpgrp() == os.getpid():      # never the group of whoever started the app
            try:
                os.killpg(0, signal.SIGKILL)
            except OSError:
                pass
        os._exit(1)

    threading.Thread(target=wait, name="atlas-parent-watch", daemon=True).start()


def kill_tree(worker) -> None:
    """Kill a worker (a multiprocessing.Process) and everything it started, at once. Also right after it has died by
    itself: its helpers may still be running, and the group id is the worker's pid either way."""
    if not WINDOWS:
        try:
            os.killpg(worker.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):    # not a leader yet, or nothing left but zombies
            pass
    worker.kill()
