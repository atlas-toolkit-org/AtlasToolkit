#!/usr/bin/env python3
"""
run_tests.py - The test suite with every module in its own process, so a crash in one does not hide the rest. Used by CI.

    python scripts/dev/run_tests.py [--out build/qa/tests] [--pattern test_gui*.py]

Prints one line per module and writes the log of each module that is not clean, plus summary.txt, to --out. The exit
code is 0 only when every module passed. For everyday work the plain command is enough:
python -m unittest discover -s tests
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_S = 900


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "qa" / "tests")
    ap.add_argument("--pattern", default="test_*.py")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    rows, ran, skipped, bad = [], 0, 0, []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        if not fnmatch.fnmatch(path.name, a.pattern):
            continue
        started = time.monotonic()
        try:
            r = subprocess.run([sys.executable, "-X", "faulthandler", "-m", "unittest", "discover", "-s", "tests", "-p",
                                path.name, "-v"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=TIMEOUT_S, encoding="utf-8", errors="replace")
            code, text = r.returncode, r.stdout
        except subprocess.TimeoutExpired as e:
            code, text = "timeout", e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        count = re.search(r"^Ran (\d+) tests?", text, re.M)
        skip = re.search(r"skipped=(\d+)", text)
        ran += int(count.group(1)) if count else 0
        skipped += int(skip.group(1)) if skip else 0
        state = ("ok" if code == 0 else "TIMEOUT" if code == "timeout" else
                 "FAIL" if count and code == 1 else "CRASH")
        rows.append(f"{state:8} {path.name:44} {count.group(1) if count else '?':>4} tests "
                    f"{time.monotonic() - started:6.0f} s  exit {code}")
        print(rows[-1], flush=True)
        if state != "ok":
            bad.append(path.name)
            (a.out / f"{path.stem}.log").write_text(text, encoding="utf-8")
            for failed in re.findall(r"^(?:ERROR|FAIL): (.+)$", text, re.M)[:12]:
                print(f"      {failed}", flush=True)
            if state != "FAIL":
                print("".join(text.splitlines(True)[-30:]), flush=True)
    rows.append(f"\nran {ran} tests, skipped {skipped}, modules not ok: {len(bad)} {bad}")
    print(rows[-1])
    (a.out / "summary.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
