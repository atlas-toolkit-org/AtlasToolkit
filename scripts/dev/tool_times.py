r"""
How long does each mesh tool take at its defaults? Which tools' specs say `slow` rests on this (dev only).

    .venv\Scripts\python scripts\dev\tool_times.py model.ply [model2.ply ...] [--tool id ...] [--limit 600]
                                                   [--src FOLDER]

Every run is a fresh command-line process with its own settings and caches, so nothing is warm. Outputs and
times.json go to build/qa/tool_times. --src runs a copy of the source, so the working tree can change meanwhile.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build" / "qa" / "tool_times"


def run(tool: str, model: Path, limit: float, src: str) -> dict:
    work = OUT / "out" / tool
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="atlas_times_", ignore_cleanup_errors=True) as home:
        env = dict(os.environ, ATLAS_TOOLKIT_HOME=home, ATLAS_TOOLKIT_NO_UPDATE_CHECK="1", PYTHONPATH=src)
        cmd = [sys.executable, "-m", "atlas_toolkit", "run", tool, str(model), "--out-dir", str(work), "--force", "-q"]
        t = time.perf_counter()
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
        try:
            text = proc.communicate(timeout=limit)[0].decode("utf-8", "replace")
            status = "ok" if proc.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            status = "limit"
            if sys.platform == "win32":                          # its worker and helpers too
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            else:
                proc.kill()
            text = proc.communicate()[0].decode("utf-8", "replace")
        return {"tool": tool, "model": model.stem, "seconds": round(time.perf_counter() - t, 1), "status": status,
                "tail": text[-300:] if status != "ok" else ""}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("models", nargs="+", type=Path)
    ap.add_argument("--tool", nargs="*", default=[])
    ap.add_argument("--limit", type=float, default=600.0)
    ap.add_argument("--src", default=str(ROOT / "src"))
    a = ap.parse_args()
    from atlas_toolkit.core.registry import registry
    tools = a.tool or [t.id for t in registry().ordered() if t.inputs.kind == "mesh"]
    OUT.mkdir(parents=True, exist_ok=True)
    done = []
    for tool in tools:
        for model in a.models:
            r = run(tool, model.resolve(), a.limit, a.src)
            done.append(r)
            print(f"{r['seconds']:8.1f} s  {r['status']:7s} {tool:22s} {model.stem}", flush=True)
            (OUT / "times.json").write_text(json.dumps(done, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
