"""One-shot native developable solver, cancellable and killed with its Atlas worker. No Python ABI dependency."""
from __future__ import annotations

from collections import deque
import hashlib
import json
from pathlib import Path
import queue
import subprocess
import threading

from atlas_toolkit.core import procs
from atlas_toolkit.core.runtimes import check_helper, find_helper

PROTOCOL = 1
ENV = "ATLAS_TOOLKIT_DEVELOPABLE"


def find_backend():
    return find_helper("atlas-developable-core", ENV, "developable_exe")


def fingerprint(exe):
    h = hashlib.sha256()
    with Path(exe).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def validate(result):
    import math
    if result.get("schema") != PROTOCOL or result.get("version") != "0.1.0":
        raise ValueError("incompatible developable backend result; rebuild the helper")
    if not isinstance(result.get("levels"), list):
        raise ValueError("invalid developable diagnostics")
    for level in result["levels"]:
        for key in ("initial_energy", "final_energy", "gradient_rms"):
            if not isinstance(level.get(key), (int, float)) or not math.isfinite(level[key]):
                raise ValueError("invalid developable energy diagnostics")
        if level["final_energy"] > level["initial_energy"] + 1e-9:
            raise ValueError("developable energy increased during optimization")
    return result


def run_native(exe, src, settings, dst, report, *, check=lambda: None,
               progress=lambda f, m: None):
    args = [str(exe), "--input", str(src), "--settings", str(settings),
            "--output", str(dst), "--report", str(report)]
    check()
    check_helper(exe, "atlas-developable-core", PROTOCOL)
    proc = procs.spawn(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace")
    lines, tail = queue.Queue(), deque(maxlen=20)

    def pump():
        try:
            for line in proc.stdout:
                lines.put(line.rstrip())
        finally:
            lines.put(None)

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    try:
        while True:
            check()
            try:
                line = lines.get(timeout=0.05)
            except queue.Empty:
                continue
            if line is None:
                break
            if line.startswith("PROGRESS "):
                _, frac, message = line.split(" ", 2)
                progress(float(frac), message)
            elif line:
                tail.append(line)
        code = proc.wait()
        check()
        if code:
            raise RuntimeError(f"developable solver failed (exit {code}): " + "\n".join(tail))
        with Path(report).open(encoding="utf-8") as f:
            return validate(json.load(f))
    finally:
        procs.finish(proc)
        thread.join(timeout=2)
        proc.stdout.close()
