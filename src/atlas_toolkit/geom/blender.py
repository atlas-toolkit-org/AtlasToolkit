"""
The Blender runtime: the installed Blender, run headless as a warm worker for tools whose work needs it (Melt: its
Geometry Nodes melt, voxel dissolve and drip tubes). No venv: Blender brings its own Python and numpy; the toolkit
ships only the worker scripts (atlas_toolkit/blender/, real files in the exe, as ml/depth_server.py).

    path = find_blender()                    # ATLAS_TOOLKIT_BLENDER, app setting blender_exe, PATH, Program Files
    reply = client(log).request({"op": "melt", ...}, on_progress=..., poll=ctx.check_cancel)

The server (blender/server.py) starts once per toolkit worker process (~2-8 s) and stays up between runs. Requests
and replies are JSON lines tagged on stdout (Blender's own prints are sent to stderr; anything untagged is kept as
"noise" for error messages); meshes travel as .npz files in the cache.

Cancel: the client polls the job's cancel flag while it waits, asks the server to stop between steps, and kills
Blender if it does not answer at once. On Windows Blender also runs in a job object that kills it when the
toolkit's worker dies (the app kills the worker 1.5 s after Cancel), and the server exits by itself when its stdin
closes.
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..core import procs
from ..core.paths import cache_dir, program_inside, resource

ENV = "ATLAS_TOOLKIT_BLENDER"
SETTING = "blender_exe"
MIN_VERSION = (4, 2)
PROTOCOL = 1
TAG = "\x1eATLAS1 "


class BlenderError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- finding Blender

def _version_of(path: Path) -> tuple:
    bundle = next((p.name for p in path.parents if p.suffix == ".app"), "")      # /Applications/Blender 4.5.app/Contents/MacOS
    m = re.search(r"(\d+)\.(\d+)", bundle or path.parent.name) or re.search(r"(\d+)\.(\d+)", str(path))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def candidates() -> list:
    """Every blender executable found, best first (the explicit choices, then the newest install)."""
    exe = "blender.exe" if sys.platform == "win32" else "blender"
    out = []
    if os.environ.get(ENV):
        out.append(Path(program_inside(os.environ[ENV])))
    try:
        from ..core.settings import AppSettings
        chosen = AppSettings().get(SETTING)          # read fresh: the UI may have just located it
        if chosen:
            out.append(Path(program_inside(chosen)))
    except Exception:
        pass
    found = shutil.which("blender")
    if found:
        out.append(Path(found))
    installs = []
    if sys.platform == "win32":
        for base in {os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"}:
            b = Path(base)
            installs += list(b.glob("Blender Foundation/Blender*/" + exe)) + list(b.glob("Blender*/" + exe))
    elif sys.platform == "darwin":          # pragma: no cover
        # An app started from Finder has a minimal PATH, so `which` above rarely helps there.
        for base in (Path("/Applications"), Path.home() / "Applications"):
            installs += list(base.glob("Blender*.app/Contents/MacOS/Blender"))
    else:                                    # pragma: no cover
        home = Path.home()
        installs += [Path("/usr/bin/blender"), Path("/usr/local/bin/blender"), Path("/snap/bin/blender"),
                     Path("/var/lib/flatpak/exports/bin/org.blender.Blender"),
                     home / ".local/share/flatpak/exports/bin/org.blender.Blender"]
        for base in (Path("/opt"), home, home / "Applications", home / ".local/opt"):     # unpacked tarballs
            installs += list(base.glob("blender*/blender"))
    out += sorted(installs, key=_version_of, reverse=True)
    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(str(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def find_blender() -> Path | None:
    """The Blender to use, or None. Cheap (no process is started): the version is checked when it starts."""
    for p in candidates():
        if p.is_file():
            return p
    return None


def server_script() -> Path:
    return resource("blender", "server.py")


def _clean_env() -> dict:
    env = {k: v for k, v in procs.child_env().items() if k not in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP")}
    env.update(PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1")
    return env


# ----------------------------------------------------------------------------- the client

class BlenderClient:
    """One Blender server process; requests are answered in order."""

    def __init__(self, exe: Path | None = None, argv: list | None = None, startup_timeout: float = 180.0):
        if argv is None:
            if exe is None:
                raise BlenderError("Blender was not found")
            argv = [str(exe), "-b", "--factory-startup", "-noaudio", "--python-exit-code", "1",
                    "--python", str(server_script()), "--", "--protocol", str(PROTOCOL)]
        self.exe = exe
        self.proc = procs.spawn(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", bufsize=1, env=_clean_env())
        self.lines: queue.Queue = queue.Queue()
        self.errors: list = []
        self.noise: list = []
        self.next_id = 0
        self._pump_thread = threading.Thread(target=self._pump, daemon=True)
        self._drain_thread = threading.Thread(target=self._drain, daemon=True)
        self._pump_thread.start()
        self._drain_thread.start()
        hello = self._read(startup_timeout)
        if not hello.get("ready"):
            self.kill()
            raise BlenderError(f"Blender did not start the toolkit's worker: {hello.get('error', '')}\n"
                               f"{self.tail()}".strip())
        if int(hello.get("protocol", 0)) != PROTOCOL:
            self.kill()
            raise BlenderError(f"the Blender worker speaks protocol {hello.get('protocol')}, expected {PROTOCOL}")
        self.version = tuple(hello.get("version", (0, 0, 0)))
        self.version_string = hello.get("blender", "?")
        self.startup = hello.get("startup_s", 0.0)
        if self.version[:2] < MIN_VERSION:
            self.kill()
            raise BlenderError(f"Blender {self.version_string} is too old: Melt needs {MIN_VERSION[0]}.{MIN_VERSION[1]} "
                               "or newer (tested with 5.2)")
        if hello.get("missing_nodes"):
            self.kill()
            raise BlenderError(f"Blender {self.version_string} lacks nodes the melt needs: "
                               f"{', '.join(hello['missing_nodes'])} (tested with 5.2)")

    # ------------------------------------------------------------------ pipes
    def _pump(self) -> None:
        for line in self.proc.stdout:
            i = line.find(TAG)
            if i >= 0:
                try:
                    self.lines.put(json.loads(line[i + len(TAG):]))
                    continue
                except ValueError:
                    pass
            if line.strip():
                self.noise.append(line.rstrip())
                del self.noise[:-40]
        self.lines.put(None)

    def _drain(self) -> None:
        for line in self.proc.stderr:
            if line.strip():
                self.errors.append(line.rstrip())
                del self.errors[:-40]

    def tail(self, n: int = 10) -> str:
        return "\n".join((self.errors + self.noise)[-n:])

    def _read(self, timeout: float, poll=None) -> dict:
        t0 = time.monotonic()
        while True:
            try:
                msg = self.lines.get(timeout=0.1)
            except queue.Empty:
                if poll is not None:
                    poll()
                if time.monotonic() - t0 > timeout:
                    raise BlenderError("Blender did not answer in time") from None
                continue
            if msg is None:
                try:
                    code = self.proc.wait(2)
                except subprocess.TimeoutExpired:
                    code = None
                raise BlenderError(f"Blender stopped{f' (exit code {code})' if code is not None else ''}:\n"
                                   f"{self.tail()}".strip())
            return msg

    def alive(self) -> bool:
        return self.proc.poll() is None

    # ------------------------------------------------------------------ requests
    def _send(self, msg: dict) -> None:
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise BlenderError(f"Blender is not running ({e}):\n{self.tail()}".strip()) from None

    def request(self, req: dict, on_progress=None, poll=None, timeout: float = 1800.0) -> dict:
        """Send one request; progress messages go to on_progress(fraction, text); poll() is called while waiting
        and may raise (a job's cancel): the request is then cancelled and the exception re-raised. `timeout` is the
        longest silence (no progress, no reply) before Blender counts as stuck."""
        self.next_id += 1
        rid = self.next_id
        self._send(dict(req, id=rid))
        try:
            while True:
                msg = self._read(timeout, poll)
                if poll is not None:            # a busy stream of progress must not starve the cancel check
                    poll()
                if msg.get("id") != rid:
                    continue
                if "progress" in msg:
                    if on_progress:
                        on_progress(float(msg.get("progress", 0.0)), msg.get("msg", ""))
                    continue
                if msg.get("cancelled"):
                    from ..core.job import Cancelled
                    raise Cancelled()
                if not msg.get("ok"):
                    raise BlenderError(f"{msg.get('error', 'Blender failed')}\n{msg.get('trace', '')}".strip())
                return msg
        except BlenderError:
            raise
        except BaseException:                 # cancelled (or interrupted) while Blender works
            self.cancel(rid)
            raise

    def cancel(self, rid: int, grace: float = 0.4) -> None:
        """Ask the server to stop request rid; kill Blender if it does not confirm within `grace` seconds."""
        try:
            self._send({"cancel": rid})
        except BlenderError:
            return
        t0 = time.monotonic()
        while time.monotonic() - t0 < grace:
            try:
                msg = self.lines.get(timeout=0.05)
            except queue.Empty:
                continue
            if msg is None:
                return
            if msg.get("id") == rid and ("ok" in msg):
                return
        self.kill()

    def kill(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self._send({"quit": True})
                self.proc.wait(3)
        except Exception:
            self.kill()
        finally:
            self.proc.wait()
            self._pump_thread.join(timeout=2)
            self._drain_thread.join(timeout=2)
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            procs.release(self.proc)


_CLIENT: BlenderClient | None = None


def client(log=None) -> BlenderClient:
    """The worker's Blender, started on first use and restarted when it died."""
    global _CLIENT
    if _CLIENT is None or not _CLIENT.alive():
        exe = find_blender()
        if exe is None:
            from ..core.runtimes import BLENDER_FILE
            raise BlenderError("Blender was not found. Install Blender 4.2 or newer (blender.org), or point to "
                               f"{BLENDER_FILE} with “Locate Blender…” on the tool's page (or set {ENV}).")
        if log:
            log(f"starting Blender ({exe}) in the background")
        t0 = time.perf_counter()
        _CLIENT = BlenderClient(exe)
        if log:
            log(f"Blender {_CLIENT.version_string} ready in {time.perf_counter() - t0:.1f} s")
        _sweep()
    return _CLIENT


def shutdown() -> None:
    global _CLIENT
    if _CLIENT is not None:
        _CLIENT.close()
        _CLIENT = None


atexit.register(shutdown)


def scratch() -> Path:
    """Folder for the meshes exchanged with Blender."""
    d = cache_dir() / "blender"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sweep(max_age_s: float = 86400.0) -> None:
    """Remove exchange files older than a day (left by a killed run)."""
    now = time.time()
    for p in scratch().glob("*.npz"):
        try:
            if now - p.stat().st_mtime > max_age_s:
                p.unlink()
        except OSError:
            pass
