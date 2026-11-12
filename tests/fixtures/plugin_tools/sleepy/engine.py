"""Says who it started in $ATLAS_TEST_PIDS, then never looks at Cancel again."""
import os
import subprocess
import sys
import time
from pathlib import Path

from atlas_toolkit.core import procs


def run(ctx, src, dst, p):
    child = procs.spawn([sys.executable, "-c", "import time; time.sleep(600)"], stdin=subprocess.DEVNULL)
    report = Path(os.environ["ATLAS_TEST_PIDS"])
    staged = report.with_suffix(".tmp")
    staged.write_text(f"{os.getpid()} {child.pid}", encoding="utf-8")
    os.replace(staged, report)
    time.sleep(600)
