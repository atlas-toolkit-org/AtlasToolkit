"""One-shot native stripe solver, cancellable and killed with its Atlas worker. No Python ABI dependency."""
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

PROTOCOL = 2
ENV = "ATLAS_TOOLKIT_STRIPES"


def find_backend():
    return find_helper("atlas-stripes-core", ENV, "stripes_exe")


def fingerprint(exe):
    h = hashlib.sha256()
    with Path(exe).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def validate(result):
    import numpy as np
    if result.get("schema") != PROTOCOL or not isinstance(result.get("families"), list) or not result["families"]:
        raise ValueError("unsupported stripe backend result; rebuild the native helper")
    total = 0
    for family in result["families"]:
        vertices = np.asarray(family["vertices"], dtype=float)
        edges = np.asarray(family["edges"], dtype=np.int64)
        if not len(vertices) and not len(edges):
            continue
        if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
            raise ValueError("invalid stripe curve coordinates")
        if not len(edges):
            continue
        if edges.ndim != 2 or edges.shape[1] != 2 or edges.min() < 0 or edges.max() >= len(vertices):
            raise ValueError("invalid stripe curve edge indices")
        if len(vertices) > 2000000 or len(edges) > 2000000:
            raise ValueError("stripe curve budget exceeded; increase spacing")
        total += len(edges)
    if not total:
        raise ValueError("no stripe curves; decrease spacing")
    V = np.asarray(result["mesh_vertices"], dtype=float)
    F = np.asarray(result["mesh_faces"], dtype=np.int64)
    if V.ndim != 2 or V.shape[1] != 3 or not np.isfinite(V).all() or F.ndim != 2 or F.shape[1] != 3:
        raise ValueError("invalid stripe field mesh")
    if F.min() < 0 or F.max() >= len(V):
        raise ValueError("invalid stripe field mesh indices")
    for family in result["families"]:
        phase = np.asarray(family["corner_phase"], float)
        if phase.shape != (len(F), 3) or not np.isfinite(phase).all():
            raise ValueError("invalid stripe phase values")
    return result


def run_native(exe, src, dst, *, spacing, field, rotation, families, seed, check=lambda: None,
               progress=lambda f, m: None):
    args = [str(exe), "--input", str(src), "--output", str(dst), "--spacing", str(spacing),
            "--field", field, "--rotation", str(rotation), "--families", families, "--seed", str(seed)]
    check()
    check_helper(exe, "atlas-stripes-core")
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
            raise RuntimeError(f"stripe solver failed (exit {code}): " + "\n".join(tail))
        with Path(dst).open(encoding="utf-8") as f:
            return validate(json.load(f))
    finally:
        procs.finish(proc)
        thread.join(timeout=2)
        proc.stdout.close()
