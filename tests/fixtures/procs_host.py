"""For test_procs: stands in for the app's window process, so the test can kill it the hard way.

    procs_host.py run <plugins> <input.svg>     start a stuck job (it reports its pids through $ATLAS_TEST_PIDS)
    procs_host.py idle                          start a worker with no job, and print its pid
"""
import sys
import time

from atlas_toolkit.core.job import Job
from atlas_toolkit.core.runner import Runner

if __name__ == "__main__":
    plugins = [sys.argv[2]] if sys.argv[1] == "run" else None
    runner = Runner(lambda *_: None, plugin_dirs=plugins)
    if sys.argv[1] == "run":
        runner.submit(Job("test-sleepy", "run", sys.argv[3], sys.argv[3] + ".out.svg", {}), "process")
    else:
        runner.process.warm_up()
        print(runner.process.workers[0].proc.pid, flush=True)
    time.sleep(600)
