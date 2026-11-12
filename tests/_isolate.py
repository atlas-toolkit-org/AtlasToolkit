"""
Import this first in every test module (tests/test_portability.py checks it). Settings, presets, history and caches
then go to a temporary folder on every OS, and Qt runs without a display unless the environment chose a platform.

CI chooses the real plugin (xcb under Xvfb, cocoa), so that the 3D views really draw. Offscreen they simply do not,
as long as Qt cannot see a display: next to one, Linux's offscreen plugin finds X11 and gets an OpenGL that fails.
"""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

VAR = "ATLAS_TOOLKIT_HOME"       # core/paths.py keeps everything in this folder
HELPERS = ("texture", "stripes", "remesh", "developable")
REQUIRE_HELPERS = os.environ.get("ATLAS_TEST_REQUIRE_HELPERS") == "1"    # CI: a green run means the helpers really ran

if VAR not in os.environ:        # a worker process inherits its parent's folder
    os.environ[VAR] = tempfile.mkdtemp(prefix="atlas_tests_")
    atexit.register(shutil.rmtree, os.environ[VAR], ignore_errors=True)
    if sys.platform != "win32":  # libraries' own caches (fontconfig, Qt) stay out of the real home too
        os.environ["HOME"] = os.environ[VAR]
ROOT = os.environ[VAR]
# Preset packs a developer points the app at must not leak into tests: tests that want the private packs load them
# through tests/_presets.py, and every other test sees only what it installs itself.
os.environ.pop("ATLAS_TOOLKIT_PRESET_DIRS", None)
if os.environ.setdefault("QT_QPA_PLATFORM", "offscreen") == "offscreen":
    os.environ.pop("DISPLAY", None)
    os.environ.pop("WAYLAND_DISPLAY", None)


def _running(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(0x00100000, False, pid)            # SYNCHRONIZE
        if not handle:
            return False
        try:
            return k32.WaitForSingleObject(ctypes.c_void_p(handle), 0) != 0
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A killed process stays a zombie until something reaps it, and a container may have nothing that does.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def process_gone(pid: int, seconds: float = 20.0) -> bool:
    """True once process `pid` has ended, within `seconds`."""
    deadline = time.monotonic() + seconds
    while _running(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def needs_helper(found, why: str):
    """Decorate tests that need a native helper. A fresh checkout has none, so they skip; where CI requires the helpers,
    a missing one is an error instead of a quiet skip."""
    if REQUIRE_HELPERS and not found:
        raise RuntimeError(f"{why}, and ATLAS_TEST_REQUIRE_HELPERS is set")
    return unittest.skipUnless(found, why)


def _forget_paths() -> None:
    paths = sys.modules.get("atlas_toolkit.core.paths")
    if paths is not None:
        paths._base.cache_clear()


def fresh_home():
    """Start from an empty profile, for a test class that must not see another's settings. Call the result to go back."""
    before = os.environ[VAR]
    os.environ[VAR] = tempfile.mkdtemp(dir=ROOT)
    _forget_paths()

    def leave() -> None:
        os.environ[VAR] = before
        _forget_paths()
    return leave
