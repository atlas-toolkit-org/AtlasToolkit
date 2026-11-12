"""
The console companion of the windowed app, built as "atlas-toolkit.exe" next to "Atlas Toolkit.exe" (Windows only).

A windowed exe cannot hold a terminal: cmd and PowerShell do not wait for it and its exit code is lost. This small
console program is what the shell waits for: it starts the app with the same arguments, reads its output through
pipes (which the app always writes to) and passes it on as it arrives, then returns the app's exit code.
Redirection (> out.txt, | findstr) works as with any console program.

    atlas-toolkit --version            the command line, in this terminal (PowerShell: .\atlas-toolkit --version)
    atlas-toolkit                      no arguments: opens the app and returns at once

Standard library only, so the file stays small.
"""
import os
import subprocess
import sys
import threading
from pathlib import Path

APP = "Atlas Toolkit.exe"


def pump(src, dst) -> None:
    """Copy bytes as they arrive, so progress lines show live."""
    fd = src.fileno()
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        try:
            dst.write(chunk)
            dst.flush()
        except (OSError, ValueError):     # the reader went away (| more closed): keep draining
            pass


def main() -> int:
    here = Path(sys.executable).resolve().parent
    app = here / APP
    if not app.is_file():
        print(f"{APP} was not found next to {Path(sys.executable).name} (in {here}).", file=sys.stderr)
        return 2
    args = sys.argv[1:]
    if not args:
        subprocess.Popen([str(app)], close_fds=True)
        return 0
    old_cp = 0
    if sys.platform == "win32" and sys.stdout.isatty():
        import ctypes
        old_cp = ctypes.windll.kernel32.GetConsoleOutputCP()
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)      # the app writes UTF-8 (names, arrows)
    try:
        return _run(app, args)
    finally:
        if old_cp:                                            # leave the terminal as it was
            ctypes.windll.kernel32.SetConsoleOutputCP(old_cp)


def _run(app: Path, args: list) -> int:
    env = dict(os.environ, PYINSTALLER_SUPPRESS_SPLASH_SCREEN="1")
    proc = subprocess.Popen([str(app), *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    threads = [threading.Thread(target=pump, args=(proc.stdout, sys.stdout.buffer), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, sys.stderr.buffer), daemon=True)]
    for t in threads:
        t.start()
    try:
        code = proc.wait()
    except KeyboardInterrupt:                 # the one-file app is two processes (and maybe workers): end them all
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        return 130
    for t in threads:
        t.join(timeout=2)
    return code


if __name__ == "__main__":
    sys.exit(main())
