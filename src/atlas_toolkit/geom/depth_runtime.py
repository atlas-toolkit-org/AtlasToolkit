"""
Installs the depth environment for the user: PyTorch and the model libraries in a private Python, in the per-user
`ml-runtime` folder that geom/depth.py already looks in. No Python, terminal or source tree needed; Setup › Components,
`atlas-toolkit components` and scripts/setup_ml.py all run this module. No Qt.

    plan()     what would be installed, why that build, and whether this computer is ready (disk, folder, internet)
    install()  uv (pinned, SHA-256 checked) → Python → a venv in ml-runtime.partial → torch → packages → probe →
               put in place. A half-made environment is never where the app looks; the old one stays until the new
               one has proved itself.
    check()    the probe, and optionally a model run;  remove()  everything it installed

Every version comes from depth_runtime.json. Progress is measured, not guessed: the bytes landing in uv's cache (and
the Python and model folders) against the sizes that file records. GUI and CLI show the same Plan, stages and Result.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

from ..core import gpu as gpus
from ..core import procs
from ..core.paths import cache_dir, display_path, ml_runtime_dir, resource, venv_python

LOCK_PATH = Path(__file__).with_name("depth_runtime.json")
STAMP = "atlas-runtime.json"
SETTING = "ml_python"
ENV = "ATLAS_TOOLKIT_ML_PYTHON"
UV_COMMAND: list | None = None    # tests: a stand-in for uv (a script run by Python); None = the downloaded uv
WIN_PATH_BUDGET = 150       # the longest file inside the environment, measured below its folder (torch's licences)
MARGIN_MB = 500
NETWORK_RETRIES = 2          # a stage whose download broke off runs again this many times before the install fails


class Cancelled(Exception):
    pass


class InstallError(Exception):
    def __init__(self, kind: str, message: str, hint: str = "", stage: str = ""):
        super().__init__(message)
        self.kind, self.message, self.hint, self.stage = kind, message, hint, stage


@lru_cache(maxsize=1)
def lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def os_key() -> str:
    return "win32" if sys.platform == "win32" else "darwin" if sys.platform == "darwin" else "linux"


def platform_key() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64" if machine in ("amd64", "x86_64", "x64") else machine
    return f"{os_key()}-{arch}"


def detect() -> gpus.GpuReport:
    return gpus.detect()


def default_model() -> str:
    """The model Check runs and the installer can download in advance."""
    return lock()["model"]["key"]


def builds() -> list[tuple[str, str, str]]:
    """(key, label, what it is for) for the builds this OS can install, in the order they are tried."""
    return [(k, b["label"], b["about"]) for k, b in lock()["builds"].items() if os_key() in b["platforms"]]


# ----------------------------------------------------------------------------- folders
@dataclass(frozen=True)
class Folders:
    target: Path             # the environment, where the app looks
    base: Path               # uv, its Python and its cache, beside it

    @property
    def partial(self) -> Path:
        return self.target.with_name(self.target.name + ".partial")

    @property
    def old(self) -> Path:
        return self.target.with_name(self.target.name + ".old")

    @property
    def uv_dir(self) -> Path:
        return self.base / f"uv-{lock()['uv']['version']}"

    @property
    def uv(self) -> Path:
        return self.uv_dir / ("uv.exe" if sys.platform == "win32" else "uv")

    @property
    def python(self) -> Path:
        """The pinned standalone Python, unpacked by us: an ordinary interpreter to uv, so uv makes no links for it."""
        return self.base / f"python-{lock()['python_full']}"

    @property
    def python_exe(self) -> Path:
        return self.python / ("python.exe" if sys.platform == "win32" else "bin/python3")

    @property
    def legacy_python(self) -> Path:
        """Where 2.3.0 let `uv python install` work. Its version link is a junction, which hardened Windows refuses to
        follow (os error 448). A 2.3.0 environment runs on it, so it goes only once a new one is in place."""
        return self.base / "python"

    @property
    def cache(self) -> Path:
        return self.base / "cache"

    @property
    def tmp(self) -> Path:
        """uv's temporary files (a large wheel downloads here before unpacking): on the same disk, and measured."""
        return self.base / "tmp"


def folders(target: Path | None = None) -> Folders:
    target = Path(target) if target else ml_runtime_dir()
    return Folders(target, target.with_name(target.name + "-base"))


def stamp(target: Path | None = None) -> dict | None:
    """What Atlas wrote when it installed the environment there, or None (made by hand, or not there)."""
    try:
        return json.loads((folders(target).target / STAMP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def status() -> dict:
    """For the Components card: the Python the app would use, whether Atlas installed the per-user one, and with what."""
    from . import depth
    found = depth.runtime_python()
    f = folders()
    s = stamp()
    ours = found is not None and s is not None and Path(found) == venv_python(f.target)
    return {"python": found, "ours": ours, "stamp": s if ours else None, "folder": f.target,
            "update": bool(ours and s.get("revision", 0) < lock()["revision"]),
            "busy": f.partial.exists()}


def tidy(f: Folders | None = None) -> None:
    """Remove what an interrupted install or a locked swap left: a half-made environment and an old one."""
    f = f or folders()
    for leftover in (f.partial, f.old):
        if leftover.exists():
            shutil.rmtree(leftover, ignore_errors=True)


# ----------------------------------------------------------------------------- choosing a build
@dataclass
class Choice:
    build: str
    reason: str
    advice: str = ""          # what would allow a better build (a newer driver, a driver at all)
    level: str = "ok"         # "ok", "info" (works, slower), "warn" (a card that could do more)


def _fits(b: dict, g: gpus.Gpu) -> str:
    """'' when build `b` runs on card `g`, else why not."""
    if g.compute and tuple(g.compute) < tuple(b["min_compute"]):
        return f"{b['label']} needs compute capability {b['min_compute'][0]}.{b['min_compute'][1]} or newer"
    if g.compute and tuple(g.compute) > tuple(b["max_compute"]):
        return f"{b['label']} does not support this card's architecture ({g.compute_text})"
    floor = b["min_driver"][os_key()]
    if g.driver_number < floor:
        return f"{b['label']} needs NVIDIA driver {floor:g} or newer"
    return ""


def gpu_builds() -> list[str]:
    return [k for k, b in lock()["builds"].items() if b["gpu"] and os_key() in b["platforms"]]


def cpu_build() -> str:
    return "mac" if os_key() == "darwin" else "cpu"


def choose(report: gpus.GpuReport) -> Choice:
    """The build for this computer, with the reason in words. The probe after installing has the last word."""
    if os_key() == "darwin":
        return Choice("mac", "PyTorch for macOS: the depth models run on the CPU of your Mac.", level="info")
    L = lock()["builds"]
    if report.status == "nvidia":
        g = report.best()
        misses = []
        for key in gpu_builds():
            why = _fits(L[key], g)
            if not why:
                note = f" ({misses[0]}; this one works with yours)" if misses else ""
                return Choice(key, f"Suits {g.name} with driver {g.driver}{note}.",
                              advice=_driver_advice(g) if misses else "")
            misses.append(why)
        return Choice("cpu", f"No GPU build of PyTorch runs on {g.name} with driver {g.driver}: {'; '.join(misses)}.",
                      advice=_driver_advice(g) or "This card is too old for current PyTorch: the CPU build is the one that runs.",
                      level="warn")
    if report.status == "no-driver":
        return Choice("cpu", f"{report.summary()} Without a working driver only the CPU build can run.",
                      advice=f"Install NVIDIA's driver ({gpus.DRIVER_URL}), then press Detect again to get the GPU build.",
                      level="warn")
    return Choice("cpu", "No NVIDIA graphics card: the CPU build works on any computer, only slower. "
                         "An NVIDIA card is optional, but it makes each drawing 10 to 30 times faster.", level="info")


def _driver_advice(g: gpus.Gpu) -> str:
    """The first GPU build that only the driver keeps out: update to its floor to use it."""
    for key in gpu_builds():
        b = lock()["builds"][key]
        why = _fits(b, g)
        if why and "driver" in why:
            return (f"Update the NVIDIA driver to {b['min_driver'][os_key()]:g} or newer ({gpus.DRIVER_URL}) to use "
                    f"{b['label']}; then press Detect again.")
    return ""


def fallbacks(build: str, report: gpus.GpuReport) -> list[str]:
    """What to offer when `build` installed but failed its check: the next GPU builds that fit, then the CPU."""
    keys = gpu_builds()
    later = keys[keys.index(build) + 1:] if build in keys else []
    g = report.best()
    return [k for k in later if g and not _fits(lock()["builds"][k], g)] + ([cpu_build()] if build != cpu_build() else [])


# ----------------------------------------------------------------------------- the plan
@dataclass
class Check:
    level: str               # "ok", "info", "warn", "fail"
    title: str
    text: str


@dataclass
class Plan:
    build: str
    choice: Choice
    report: gpus.GpuReport
    folders: Folders
    with_model: bool = False
    checks: list = field(default_factory=list)
    clear_setting: bool = False
    replaces: str = ""       # what is in the folder now, in words ("" when empty)
    recommended: str = ""    # the build detection chose (the plan's own may be one chosen by hand)

    @property
    def info(self) -> dict:
        return lock()["builds"][self.build]

    @property
    def label(self) -> str:
        return self.info["label"]

    def sizes(self) -> dict:
        """MB: what is downloaded, what stays on disk, and the most the disk needs during the install."""
        L, o = lock(), os_key()
        uv = L["uv"]["files"].get(platform_key(), {}).get("bytes", 0) / 2**20
        download = uv + L["sizes"]["python"]["download"] + self.info["download"][o] + L["sizes"]["packages"]["download"]
        installed = 2.5 * uv + L["sizes"]["python"]["installed"] + self.info["installed"][o] + L["sizes"]["packages"]["installed"]
        model = L["model"]["download"] if self.with_model else 0
        return {"download": download + model, "installed": installed, "model": model,
                "peak": installed + download + model + MARGIN_MB}

    def ok(self) -> bool:
        return not any(c.level == "fail" for c in self.checks)

    def minutes(self) -> tuple[int, int]:
        """A range for a typical home connection (50 to 200 Mbit/s) plus unpacking, in whole minutes."""
        s = self.sizes()
        unpack = s["installed"] / 150 + 20
        return max(1, round((s["download"] / 25 + unpack) / 60)), max(2, round((s["download"] / 6 + 2 * unpack) / 60))

    def stages(self) -> list[tuple[str, str]]:
        L = lock()
        rows = [("uv", "Download the installer tool (uv)"), ("python", f"Get Python {L['python']}"),
                ("venv", "Create the environment"), ("torch", f"Download and install PyTorch {L['torch']}, {self.label}"),
                ("packages", "Install the model libraries"), ("check", "Check it works, then put it in place")]
        return rows + ([("model", f"Download the {L['model']['label']} model")] if self.with_model else [])

    def summary(self) -> list[tuple[str, str]]:
        """(heading, text) rows: what both the dialog and the command line say before anything starts."""
        L, s = lock(), self.sizes()
        lo, hi = self.minutes()
        what = (f"A private Python {L['python']}, PyTorch {L['torch']} ({self.label}) and the model libraries "
                f"({', '.join(p.split('==')[0] for p in L['packages'])}). Nothing outside the folder changes: "
                "no system Python, PATH, registry entry or driver.")
        rows = [("Build", f"{self.label}. {self.choice.reason}"), ("Installs", what),
                ("Download", f"about {gb(s['download'])}" + (f", including the model ({gb(s['model'])})" if s["model"] else "")),
                ("Disk", f"about {gb(s['installed'])} stays; up to {gb(s['peak'])} free space is needed while it installs"),
                ("Time", f"about {lo} to {hi} minutes on a typical home connection; the progress shows the real speed"),
                ("Folder", show(self.folders.target))]
        if self.replaces:
            rows.append(("Replaces", f"{self.replaces}, once the new one has passed its check"))
        if not self.with_model:
            rows.append(("Models", f"Each depth model downloads once, on first use (about {gb(L['model']['download'])} each)."))
        return rows


def gb(mb: float) -> str:
    return f"{mb / 1024:.1f} GB" if mb >= 1000 else f"{mb:.0f} MB"


def duration(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m} min {s} s" if m else f"{s} s"


def show(path: Path) -> str:
    """%LOCALAPPDATA%\\…, ~/…: no user name on screen."""
    return display_path(path)


def plan(build: str = "auto", with_model: bool = False, report: gpus.GpuReport | None = None, settings=None,
         online: bool = True, target: Path | None = None) -> Plan:
    report = report or gpus.detect()
    choice = choose(report)
    if build not in ("auto", "", None) and build != choice.build:
        if build not in [k for k, _, _ in builds()]:
            raise ValueError(f"no build {build!r} here; choose from {', '.join(k for k, _, _ in builds())}")
        choice = Choice(build, f"Chosen by hand (the detected choice was {lock()['builds'][choice.build]['label']}).")
    p = Plan(choice.build, choice, report, folders(target), with_model, recommended=choose(report).build)
    p.checks = [_gpu_check(report, choice), _disk_check(p), *_folder_checks(p), *_conflicts(p, settings)]
    if online:
        p.checks.append(_internet_check(p))
    s = stamp(p.folders.target)
    if venv_python(p.folders.target).exists():
        p.replaces = (f"the environment Atlas installed on {s.get('date', '?')[:10]} ({s.get('label', s.get('build'))})"
                      if s else "the environment made by hand in this folder")
    return p


def _gpu_check(report: gpus.GpuReport, choice: Choice) -> Check:
    text = report.summary() + (f" {choice.advice}" if choice.advice else "")
    if report.status == "nvidia" and choice.level == "ok":
        g = report.best()
        if g.memory_mb and g.memory_mb < 4000:
            return Check("warn", "Graphics card", text + " It has under 4 GB of memory: the large models may not fit.")
        return Check("ok", "Graphics card", text)
    return Check("warn" if choice.level == "warn" else "info", "Graphics card", text)


def _existing(path: Path) -> Path:
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def _disk_check(p: Plan) -> Check:
    need = p.sizes()["peak"]
    try:
        free = shutil.disk_usage(_existing(p.folders.base)).free / 2**20
    except OSError:
        return Check("warn", "Disk space", f"Needs about {gb(need)}; the free space could not be read.")
    drive = p.folders.target.anchor or "/"
    text = f"Needs about {gb(need)} on {drive} while installing ({gb(p.sizes()['installed'])} stays); {gb(free)} free."
    return Check("ok" if free >= need else "fail", "Disk space", text if free >= need else text + " Free some space first.")


def _long_paths() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
            return winreg.QueryValueEx(k, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def _folder_checks(p: Plan) -> list[Check]:
    out = []
    probe_at = _existing(p.folders.base)
    try:
        with tempfile.NamedTemporaryFile(dir=probe_at, prefix=".atlas-write-test"):
            pass
        out.append(Check("ok", "Folder", f"{show(p.folders.target)} (with its tools in {p.folders.base.name} beside it)."))
    except OSError as e:
        out.append(Check("fail", "Folder", f"Cannot write in {show(probe_at)}: {e.strerror or e}."))
    if sys.platform == "win32" and len(str(p.folders.partial)) + WIN_PATH_BUDGET > 259 and not _long_paths():
        out.append(Check("fail", "Folder", "The folder's path is too long for Windows' 260-character limit. Turn on long "
                                           "paths in Windows, or use a shorter folder (portable mode) for the app."))
    return out


def _conflicts(p: Plan, settings) -> list[Check]:
    from . import depth
    out = []
    mine = venv_python(p.folders.target)
    if os.environ.get(ENV):
        out.append(Check("warn", "Another environment", f"The variable {ENV} points to {os.environ[ENV]}, and it wins over "
                                                       "this install. Remove the variable to use the new one."))
    picked = settings.get(SETTING) if settings is not None else None
    if picked and Path(picked) != mine:
        p.clear_setting = True
        out.append(Check("info", "Another environment", f"You located {show(Path(picked))}. Atlas will use the new one "
                                                        "instead once it works (the choice is forgotten)."))
    for cand in dict.fromkeys(depth.runtime_candidates()):
        if cand == mine:
            break
        if cand.exists() and str(cand) not in (os.environ.get(ENV, ""), picked or ""):
            out.append(Check("warn", "Another environment", f"{show(cand)} is found before this folder and will still be "
                                                           "used. Rename or remove it to use the new one."))
    return out


def hosts(p: Plan) -> list[str]:
    out = ["github.com", "pypi.org", "files.pythonhosted.org"]
    if p.info["index"]:
        out.append("download.pytorch.org")
    if p.with_model:
        out.append("huggingface.co")
    return out


def _internet_check(p: Plan) -> Check:
    results: dict = {}

    def reach(host: str) -> None:
        try:
            with open_url(f"https://{host}/", method="HEAD", timeout=6):
                results[host] = ""
        except urlerror.HTTPError:
            results[host] = ""                       # it answered
        except Exception as e:
            results[host] = str(getattr(e, "reason", e))[:120]

    threads = [threading.Thread(target=reach, args=(h,), daemon=True) for h in hosts(p)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(8)
    bad = {h: results.get(h, "no answer") for h in hosts(p) if results.get(h, "no answer")}
    if not bad:
        return Check("ok", "Internet", f"Reached {', '.join(hosts(p))}.")
    first = next(iter(bad.items()))
    return Check("fail", "Internet", f"Cannot reach {', '.join(bad)} ({first[0]}: {first[1]}). Check the connection, a "
                                     "proxy (HTTPS_PROXY) or a firewall, then try again.")


# ----------------------------------------------------------------------------- downloads
CA_FILES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem",
            "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem")


def _contexts():
    """The default context first; a frozen app's OpenSSL may look where the build machine kept its certificates, so then
    certifi's, then the usual system files."""
    yield ssl.create_default_context()
    try:
        import certifi
        yield ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for f in CA_FILES:
        if Path(f).is_file():
            yield ssl.create_default_context(cafile=f)


def open_url(url: str, method: str = "GET", timeout: float = 30):
    last = None
    for ctx in _contexts():
        try:
            req = urlrequest.Request(url, method=method, headers={"User-Agent": "atlas-toolkit-installer"})
            return urlrequest.urlopen(req, timeout=timeout, context=ctx)
        except urlerror.URLError as e:
            if not isinstance(e.reason, ssl.SSLCertVerificationError):
                raise
            last = e
    raise last


# ----------------------------------------------------------------------------- watching progress
class Observer:
    """What an install tells its watcher. The dialog and the command line each implement it."""

    def stage(self, index: int, state: str, note: str = "") -> None:   # state: running, done, failed, skipped
        pass

    def progress(self, done_mb: float, total_mb: float, rate_mbs: float, eta_s: float | None) -> None:
        pass

    def log(self, line: str) -> None:
        pass


def folder_mb(path: Path) -> float:
    total, stack = 0, [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_junction():           # uv links its Python under a short name: count it once
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total / 2**20


class Meter:
    """Overall progress, weighted by each stage's download, with a smoothed rate and time left."""

    def __init__(self, observer: Observer, weights: list[float]):
        self.observer, self.weights = observer, weights
        self.total = sum(weights)
        self.done_before = 0.0
        self.current = 0.0
        self.rate = 0.0
        self.last = (time.monotonic(), 0.0)
        self.lock = threading.Lock()

    def begin(self, index: int) -> None:
        with self.lock:
            self.done_before = sum(self.weights[:index])
            self.current = 0.0

    def fraction(self, f: float, weight: float) -> None:
        self.update(max(0.0, min(f, 0.99)) * weight)

    def update(self, current: float) -> None:
        with self.lock:
            self.current = max(self.current, current)
            done = self.done_before + self.current
            now = time.monotonic()
            dt = now - self.last[0]
            if dt >= 2.0:                     # a rate over a few seconds, smoothed: bursts and pauses do not swing it
                inst = max(0.0, done - self.last[1]) / dt
                self.rate = inst if self.rate == 0 else 0.8 * self.rate + 0.2 * inst
                self.last = (now, done)
            eta = (self.total - done) / self.rate if self.rate > 0.2 else None
            self.observer.progress(done, self.total, self.rate, eta)


class Watch:
    """While a stage runs: measure how much has landed in its folders against what is expected. uv unpacks wheels into
    its cache as they stream in, so the unpacked size measures the download; the high-water mark keeps the bar from
    going back when temporary files are deleted."""

    def __init__(self, meter: Meter, weight: float, folders_: list, expected_mb: float):
        self.meter, self.weight, self.expected = meter, weight, max(expected_mb, 1.0)
        self.folders = [p for p in folders_ if p is not None]
        self.start = sum(folder_mb(p) for p in self.folders)
        self.best = 0.0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop.wait(1.0):
            if self.folders:
                self.best = max(self.best, sum(folder_mb(p) for p in self.folders) - self.start)
                self.meter.fraction(self.best / self.expected, self.weight)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(3)


# ----------------------------------------------------------------------------- running things
ERRORS = [   # (kind, pattern in a program's output, what to tell the user)
    ("disk", r"No space left|not enough space|os error 112|os error 28|ENOSPC",
     "The disk filled up. Free some space and try again: what was downloaded is kept."),
    ("tls", r"certificate|UnknownIssuer|CERTIFICATE_VERIFY_FAILED|invalid peer",
     "A secure connection could not be verified. A company proxy that inspects traffic does this: ask for its "
     "certificate to be installed in the system, or try another network."),
    ("network", r"Failed to (?:fetch|download)|error sending request|dns error|timed out|Connection (?:reset|refused)|"
                r"tcp connect|Network is unreachable|failed to lookup address|getaddrinfo",
     "The download failed. Check the internet connection, a proxy (HTTPS_PROXY) or a firewall, then Try again: what was "
     "downloaded is kept."),
    ("links", r"os error 448|untrusted mount point|minor version link",
     "Windows refused to follow a folder link: a security policy on this computer (common on managed work machines) "
     "does not trust links made by your account. Atlas makes none itself; send the log to whoever looks after this "
     "computer, or point Atlas to an environment made another way with Locate."),
    ("locked", r"os error 32|being used by another process|os error 5\b|Access is denied|PermissionError",
     "A file was locked, often by an antivirus scan or another Atlas window that is drawing. Wait a moment, close "
     "other Atlas windows, then Try again."),
]


def classify(text: str) -> tuple[str, str]:
    for kind, pattern, hint in ERRORS:
        if re.search(pattern, text, re.I):
            return kind, hint
    return "other", "See the details below or the log file. Try again, or choose another build."


def child_env(f: Folders, **extra) -> dict:
    """A clean environment for uv and the new Python: nothing from the user's own Python or uv setup leaks in."""
    env = {k: v for k, v in procs.child_env().items()
           if not k.startswith(("UV_", "PIP_", "CONDA_", "PYTHON")) and k not in ("VIRTUAL_ENV", ENV)}
    env.update(UV_CACHE_DIR=str(f.cache), UV_NO_PROGRESS="1", UV_NO_CONFIG="1",
               UV_PYTHON_DOWNLOADS="never",       # Python comes from us (Folders.python), never from uv's managed store
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               UV_HTTP_TIMEOUT="300", UV_HTTP_RETRIES="6",     # uv's 30 s gives up on a slow line mid-way through a big wheel
               **extra)
    if f.tmp.is_dir():                    # during an install (install() makes it, and removes it at the end)
        env.update(TMP=str(f.tmp), TEMP=str(f.tmp), TMPDIR=str(f.tmp))
    return env


class Runner:
    def __init__(self, observer: Observer, cancelled: threading.Event, logfile):
        self.observer, self.cancelled, self.logfile = observer, cancelled, logfile

    def say(self, line: str) -> None:
        self.observer.log(line)
        if self.logfile:
            self.logfile.write(line + "\n")
            self.logfile.flush()

    def __call__(self, args: list, env: dict, timeout: float = 7200) -> tuple[int, str]:
        self.say("$ " + " ".join(str(a) for a in args))
        proc = procs.spawn([str(a) for a in args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
        tail: list = []

        def read() -> None:
            for line in proc.stdout:
                line = line.rstrip()
                tail.append(line)
                del tail[:-200]
                self.say(line)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        started = time.monotonic()
        try:
            while proc.poll() is None:
                if self.cancelled.is_set():
                    raise Cancelled()
                if time.monotonic() - started > timeout:
                    raise InstallError("other", f"{Path(args[0]).name} took longer than {timeout / 60:.0f} minutes")
                time.sleep(0.2)
            reader.join(5)
            return proc.returncode, "\n".join(tail)
        finally:
            procs.finish(proc)
            reader.join(5)
            proc.stdout.close()


# ----------------------------------------------------------------------------- the install
@dataclass
class Result:
    ok: bool
    build: str = ""
    folder: Path | None = None
    python: Path | None = None
    probe: dict = field(default_factory=dict)
    seconds: float = 0.0
    size_mb: float = 0.0
    log_path: Path | None = None
    error: InstallError | None = None
    cancelled: bool = False
    replaced: bool = False
    previous_kept: bool = False        # a failure left the earlier environment as it was
    fallbacks: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def lines(self) -> list[str]:
        """The result in words, for the dialog's last page and the command line."""
        if self.cancelled:
            return ["Cancelled. What was downloaded has been removed" +
                    ("; your earlier environment is untouched." if self.previous_kept else ".")]
        if not self.ok:
            e = self.error
            out = [f"The install did not finish: {e.message if e else 'unknown error'}"]
            if e and e.hint:
                out.append(e.hint)
            out.append("Nothing was changed: your earlier environment is still in place." if self.previous_kept
                       else "Nothing was installed where the app looks.")
            if self.fallbacks:
                out.append("You can try " + " or ".join(lock()["builds"][k]["label"] for k in self.fallbacks) + " instead.")
            if self.log_path:
                out.append(f"Log: {show(self.log_path)}")
            return out
        p = self.probe
        device = p.get("device", "?")
        cuda = f" (CUDA {p['cuda']})" if p.get("cuda") and p.get("cuda_ok") else ""
        out = [f"Installed. PyTorch {p.get('torch', '?')}{cuda} runs on {device}.",
               f"Folder: {show(self.folder)} ({gb(self.size_mb)}), in {duration(self.seconds)}.",
               "Photo Hatching can now read depth with a model."]
        return out + self.notes + ([f"Log: {show(self.log_path)}"] if self.log_path else [])


def _download(info: dict, tmp: Path, run: Runner, meter: Meter, weight: float, what: str) -> None:
    """Stream a pinned file to `tmp`, its SHA-256 checked; the meter follows the bytes."""
    tmp.parent.mkdir(parents=True, exist_ok=True)
    run.say(f"downloading {info['url']}")
    digest = hashlib.sha256()
    try:
        with open_url(info["url"], timeout=60) as r, tmp.open("wb") as out:
            got = 0
            while chunk := r.read(1 << 16):
                if run.cancelled.is_set():
                    raise Cancelled()
                out.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                meter.fraction(got / info["bytes"], weight)
    except (urlerror.URLError, OSError, TimeoutError) as e:
        kind, hint = classify(str(e))
        raise InstallError("network" if kind == "other" else kind, f"Downloading {what} failed: {e}", hint) from None
    if digest.hexdigest() != info["sha256"]:
        tmp.unlink(missing_ok=True)
        raise InstallError("hash", f"The downloaded {what} is not the expected file (its SHA-256 differs).",
                           "Something between you and GitHub changed the download. Try again later or on another network.")


def _download_python(f: Folders, run: Runner, meter: Meter, weight: float) -> None:
    """The pinned python-build-standalone archive, unpacked into Folders.python. Nothing in it is a link uv made."""
    info = lock()["python_files"].get(platform_key())
    if info is None:
        raise InstallError("other", f"There is no Python download for {platform_key()}.")
    marker = f.python / ".verified"
    if f.python_exe.is_file() and marker.is_file() and marker.read_text(encoding="ascii").strip() == info["sha256"]:
        run.say(f"Python {lock()['python_full']} is already here: {f.python}")
        return
    shutil.rmtree(f.python, ignore_errors=True)
    tmp = f.base / "python-download.tmp"
    _download(info, tmp, run, meter, weight, f"Python {lock()['python_full']}")
    unpack = f.base / "python-unpack.tmp"
    shutil.rmtree(unpack, ignore_errors=True)
    with tarfile.open(tmp) as t:
        t.extractall(unpack, filter="data")          # the archive holds one folder, python/
    (unpack / "python").rename(f.python)
    shutil.rmtree(unpack, ignore_errors=True)
    tmp.unlink(missing_ok=True)
    if not f.python_exe.is_file():
        raise InstallError("other", f"The Python archive has no {f.python_exe.name} where expected.")
    marker.write_text(info["sha256"], encoding="ascii")
    run.say(f"Python {lock()['python_full']} verified (SHA-256) and unpacked into {f.python}")


def _download_uv(f: Folders, run: Runner, meter: Meter, weight: float) -> None:
    info = lock()["uv"]["files"].get(platform_key())
    if info is None:
        raise InstallError("other", f"There is no installer tool for {platform_key()}.")
    marker = f.uv_dir / ".verified"
    if f.uv.is_file() and marker.is_file() and marker.read_text(encoding="ascii").strip() == info["sha256"]:
        run.say(f"uv {lock()['uv']['version']} is already here: {f.uv}")
        return
    f.uv_dir.mkdir(parents=True, exist_ok=True)
    tmp = f.uv_dir / "download.tmp"
    _download(info, tmp, run, meter, weight, "installer tool")
    _unpack_uv(f, info, tmp, run)


def _unpack_uv(f: Folders, info: dict, tmp: Path, run: Runner) -> None:
    marker = f.uv_dir / ".verified"
    name = f.uv.name
    if info["url"].endswith(".zip"):
        with zipfile.ZipFile(tmp) as z:
            member = next(m for m in z.namelist() if m.split("/")[-1] == name)
            f.uv.write_bytes(z.read(member))
    else:
        with tarfile.open(tmp) as t:
            member = next(m for m in t.getmembers() if m.isfile() and m.name.split("/")[-1] == name)
            f.uv.write_bytes(t.extractfile(member).read())
        f.uv.chmod(0o755)
    tmp.unlink(missing_ok=True)
    marker.write_text(info["sha256"], encoding="ascii")
    run.say(f"uv {lock()['uv']['version']} verified (SHA-256) and unpacked")


def _uv(run: Runner, f: Folders, args: list, stage: str, env_extra: dict | None = None) -> None:
    env = child_env(f, **(env_extra or {}))
    uv = UV_COMMAND or [f.uv]
    code, out = run([*uv, *args], env)
    if code == 0:
        return
    kind, hint = classify(out)
    if kind == "tls" and "UV_SYSTEM_CERTS" not in env:
        run.say("retrying with the system's certificate store")
        env = dict(env, UV_SYSTEM_CERTS="1")
        code, out = run([*uv, *args], env)
        if code == 0:
            return
        kind, hint = classify(out)
    for attempt in range(NETWORK_RETRIES):       # a slow or bursty line: what finished downloading stays in the cache
        if kind != "network":
            break
        run.say(f"the download broke off; trying again ({attempt + 1} of {NETWORK_RETRIES}), keeping what arrived")
        code, out = run([*uv, *args], env)
        if code == 0:
            return
        kind, hint = classify(out)
    raise InstallError(kind, f"{stage} failed: {uv_error(out, code)}", hint, stage)


def uv_error(out: str, code: int) -> str:
    """uv's own words for what went wrong: its `error:` line and the deepest cause, not the hint that follows them."""
    lines = [s.strip() for s in out.splitlines() if s.strip()]
    error = next((s for s in lines if s.startswith("error:")), "")
    causes = [s[len("cause:"):].strip() for s in lines if s.startswith("cause:")]
    if error:
        return (error[len("error:"):].strip() + (f" ({causes[-1]})" if causes else ""))[:400]
    return (lines[-1] if lines else f"exit code {code}")[:400]


def probe(python: Path, f: Folders | None = None) -> dict:
    """The new environment's own word: versions, device, a GPU sanity check (depth_server.py --probe)."""
    env = child_env(f or folders())
    try:
        proc = procs.spawn([str(python), "-u", str(server_script()), "--probe"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                           errors="replace", env=env)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    try:
        stdout, stderr = proc.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "the environment did not answer within 5 minutes"}
    finally:
        procs.finish(proc)
    line = next((s for s in reversed(stdout.splitlines()) if s.startswith("{")), "")
    try:
        return json.loads(line)
    except ValueError:
        return {"ok": False, "error": (stderr or stdout).strip()[-400:] or f"exit code {proc.returncode}"}


def server_script() -> Path:
    return resource("ml", "depth_server.py")


def judge(p: dict, plan_: Plan) -> str:
    """'' when the probe proves the build works here, else what failed."""
    if not p.get("ok"):
        return p.get("error", "the environment did not start")
    if not str(p.get("torch", "")).startswith(lock()["torch"]):
        return f"PyTorch {p.get('torch')} is installed, not {lock()['torch']}"
    if plan_.info["gpu"]:
        if not p.get("cuda_ok"):
            return "PyTorch cannot use the NVIDIA card with this build (torch.cuda.is_available() is False)"
        if p.get("sm") and p.get("arch") and p["sm"] not in p["arch"]:
            return f"this build has no code for the card's architecture ({p['sm']}; it has {', '.join(p['arch'])})"
        if not p.get("math_ok"):
            return "a test calculation on the GPU came out wrong"
    return ""


def _clean_downloads(f: Folders) -> None:
    """uv's download cache and temporary files: not needed once the environment is in place."""
    for folder in (f.cache, f.tmp):
        shutil.rmtree(folder, ignore_errors=True)


def _rename(src: Path, dst: Path, run: Runner, seconds: float = 30) -> None:
    """Rename a folder, retrying while Windows says a file in it is in use (antivirus, a running depth server)."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            src.rename(dst)
            return
        except OSError as e:
            if time.monotonic() > deadline:
                raise InstallError("locked", f"Could not move {src.name} into place: {e.strerror or e}",
                                   classify("os error 32")[1], "check") from None
            run.say(f"{src.name} is in use, retrying…")
            time.sleep(1)


def install(p: Plan, observer: Observer | None = None, cancelled: threading.Event | None = None, release=None,
            settings=None) -> Result:
    """Install the plan's build. `release()` is called before the swap, to stop whatever holds the old environment
    (the app's depth server). Never raises for an expected failure: the Result says what happened."""
    observer = observer or Observer()
    cancelled = cancelled or threading.Event()
    f = p.folders
    started = time.monotonic()
    logs = cache_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"depth-install-{datetime.now():%Y%m%d-%H%M%S}.log"
    L, o = lock(), os_key()
    stages = p.stages()
    s = p.sizes()
    uv_mb = L["uv"]["files"].get(platform_key(), {}).get("bytes", 0) / 2**20
    weights = {"uv": uv_mb, "python": L["sizes"]["python"]["download"], "venv": 1.0, "torch": p.info["download"][o],
               "packages": L["sizes"]["packages"]["download"], "check": 15.0, "model": s["model"]}
    meter = Meter(observer, [weights[k] for k, _ in stages])
    had_old = venv_python(f.target).exists()
    result = Result(False, p.build, f.target, log_path=log_path, previous_kept=had_old)
    current = 0
    with log_path.open("w", encoding="utf-8") as logfile:
        run = Runner(observer, cancelled, logfile)
        run.say(f"Atlas Toolkit depth environment install, {datetime.now():%Y-%m-%d %H:%M:%S}, {platform_key()}")
        for head, text in p.summary():
            run.say(f"  {head}: {text}")
        for c in p.checks:
            run.say(f"  [{c.level}] {c.title}: {c.text}")
        try:
            tidy(f)
            f.tmp.mkdir(parents=True, exist_ok=True)
            py = venv_python(f.partial)
            for current, (key, title) in enumerate(stages):
                if cancelled.is_set():
                    raise Cancelled()
                observer.stage(current, "running")
                meter.begin(current)
                run.say(f"--- [{current + 1}/{len(stages)}] {title}")
                w = weights[key]
                if key == "uv":
                    if UV_COMMAND:
                        run.say("using the stand-in uv")
                    else:
                        _download_uv(f, run, meter, w)
                elif key == "python":
                    if UV_COMMAND:
                        run.say("using this Python (stand-in uv)")
                    else:
                        _download_python(f, run, meter, w)
                elif key == "venv":
                    base_py = Path(sys.executable) if UV_COMMAND else f.python_exe
                    _uv(run, f, ["venv", "--relocatable", "--no-managed-python", "--python", str(base_py),
                                 str(f.partial)], title)
                elif key == "torch":
                    index = ["--index-url", p.info["index"]] if p.info["index"] else ["--exclude-newer", L["exclude_newer"]]
                    with Watch(meter, w, [f.cache, f.tmp], p.info["installed"][o]):
                        _uv(run, f, ["pip", "install", "--python", str(py), f"torch=={L['torch']}", *index], title)
                elif key == "packages":
                    pk = L["sizes"]["packages"]
                    with Watch(meter, w, [f.cache, f.tmp], pk["installed"]):
                        _uv(run, f, ["pip", "install", "--python", str(py), "--exclude-newer", L["exclude_newer"],
                                     *L["packages"]], title)
                elif key == "check":
                    result.probe = probe(py, f)
                    run.say("probe: " + json.dumps(result.probe))
                    bad = judge(result.probe, p)
                    if bad:
                        result.fallbacks = fallbacks(p.build, p.report)
                        raise InstallError("gpu" if p.info["gpu"] else "other", f"The check failed: {bad}.",
                                           "The environment was not put in place." +
                                           (" A different build may work on this card." if result.fallbacks else ""), title)
                    (f.partial / STAMP).write_text(json.dumps({
                        "by": "Atlas Toolkit", "revision": L["revision"], "build": p.build, "label": p.label,
                        "torch": result.probe.get("torch"), "python": result.probe.get("python"),
                        "device": result.probe.get("device"), "date": datetime.now().isoformat(timespec="seconds"),
                        "gpu": p.report.summary()}, indent=2), encoding="utf-8")
                    if release:
                        release()
                    if f.target.exists():
                        _rename(f.target, f.old, run)
                    try:
                        _rename(f.partial, f.target, run)
                    except InstallError:
                        if f.old.exists() and not f.target.exists():
                            f.old.rename(f.target)
                        raise
                    result.replaced = had_old
                    shutil.rmtree(f.old, ignore_errors=True)
                    shutil.rmtree(f.legacy_python, ignore_errors=True)    # 2.3.0's, now unused (rmtree unlinks junctions)
                    _clean_downloads(f)
                    if p.clear_setting and settings is not None:
                        settings.set(SETTING, "")
                        settings.save()
                        result.notes.append("The environment you had located is no longer used; it was not deleted.")
                    result.ok, result.previous_kept = True, False
                    result.python = venv_python(f.target)
                elif key == "model":
                    model = L["model"]
                    hub = hf_hub_dir() / ("models--" + server_models()[model["key"]][0].replace("/", "--"))
                    hub.mkdir(parents=True, exist_ok=True)
                    from .depth import download_env      # plain downloads: the folder grows as bytes arrive
                    with Watch(meter, w, [hub], model["download"]):
                        code, out = run([venv_python(f.target), "-u", server_script(), "--fetch", model["key"]],
                                        child_env(f, HF_HUB_DISABLE_PROGRESS_BARS="1", **download_env()))
                    if code != 0:
                        kind, hint = classify(out)
                        result.notes.append(f"The model did not download ({kind}); it will download on first use instead.")
                        observer.stage(current, "failed", "will download on first use")
                        continue
                observer.stage(current, "done")
                meter.update(w)
        except Cancelled:
            run.say("--- cancelled")
            observer.stage(current, "failed", "cancelled")
            result.cancelled = True
            shutil.rmtree(f.partial, ignore_errors=True)
            _clean_downloads(f)
        except InstallError as e:
            run.say(f"--- failed: {e.message}")
            observer.stage(current, "failed", e.kind)
            result.error = e
            if e.kind in ("gpu", "other", "hash"):
                shutil.rmtree(f.partial, ignore_errors=True)
        except Exception as e:      # a bug of ours: still a readable result, and the trace in the log
            import traceback
            run.say(traceback.format_exc())
            observer.stage(current, "failed", "error")
            result.error = InstallError("other", f"{type(e).__name__}: {e}", "This looks like a bug in Atlas: please "
                                        "report it with the log file.")
            shutil.rmtree(f.partial, ignore_errors=True)
        result.seconds = time.monotonic() - started
        if result.ok:
            result.size_mb = folder_mb(f.target) + folder_mb(f.base)
        run.say("--- " + " | ".join(result.lines()))
    return result


# ----------------------------------------------------------------------------- models, check, remove
@lru_cache(maxsize=1)
def server_models() -> dict:
    """depth_server.MODELS, read without importing torch: key → (Hugging Face id, size, multiple, label)."""
    spec = importlib.util.spec_from_file_location("atlas_depth_server_models", server_script())
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(mod.MODELS)


def hf_hub_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    home = Path(os.environ.get("HF_HOME") or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "huggingface")
    return home / "hub"


def model_folders() -> list[Path]:
    hub = hf_hub_dir()
    return [hub / ("models--" + v[0].replace("/", "--")) for v in server_models().values()
            if (hub / ("models--" + v[0].replace("/", "--"))).exists()]


def check(model: str | None = None, image: str | None = None, observer: Observer | None = None,
          cancelled: threading.Event | None = None) -> tuple[bool, dict, list[str]]:
    """(works, the probe, readable lines). With `model`, also run it (downloads it on first use)."""
    from . import depth
    observer = observer or Observer()
    python = depth.runtime_python()
    if python is None:
        return False, {}, ["No depth environment is installed."]
    p = probe(python)
    lines = [f"Python {p.get('python', '?')} at {show(python)}",
             f"PyTorch {p.get('torch', '?')}" + (f", CUDA {p['cuda']}" if p.get("cuda") else ", no CUDA") +
             f", runs on {p.get('device', '?')}"]
    if not p.get("ok"):
        return False, p, lines + [f"It does not work: {p.get('error', '?')}"]
    if p.get("cuda") and not p.get("cuda_ok"):
        lines.append("This is a GPU build, but PyTorch cannot use the NVIDIA card: the depth models would run on the CPU.")
    if p.get("cuda_ok") and p.get("sm") and p["sm"] not in p.get("arch", []):
        return False, p, lines + [f"This build has no code for the card ({p['sm']})."]
    ok = True
    if model:
        run = Runner(observer, cancelled or threading.Event(), None)
        code, _ = run([python, "-u", server_script(), "--check", *([image] if image else []), model],
                      child_env(folders(), HF_HUB_DISABLE_PROGRESS_BARS="1", **depth.download_env()))
        ok = code == 0
        lines.append(f"{server_models()[model][3]}: " + ("works (timings above)." if ok else "FAILED (see above)."))
    return ok, p, lines


def remove(release=None, models: bool = False) -> tuple[float, list[str]]:
    """Delete what install() made (and the downloaded models when asked). (MB freed, what was removed)."""
    if release:
        release()
    f = folders()
    gone, freed = [], 0.0
    targets = [f.target, f.base, f.partial, f.old] + (model_folders() if models else [])
    for path in targets:
        if path.exists():
            size = folder_mb(path)
            shutil.rmtree(path, ignore_errors=True)
            if not path.exists():
                freed += size
                gone.append(show(path))
            else:
                raise InstallError("locked", f"Could not remove {show(path)}: a file in it is in use.",
                                   classify("os error 32")[1])
    return freed, gone
