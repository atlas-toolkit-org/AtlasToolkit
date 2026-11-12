"""
Runs jobs off the UI thread and reports their events through one callback:

    on_event(job_id, kind, payload)   kind: queued, started, progress, log, done, error, cancelled

Two lanes:
- thread  : inspections, live analyses, light tools. One worker thread, jobs in order. A newer job of the same
            `group` drops queued older ones (the settings changed again before they ran).
- process : heavy tools (meshes, hatching) in warm worker processes (multiprocessing, spawn): one by default, up
            to `workers` jobs at once (Setup). A long pure-Python loop never freezes the window, a crash only loses
            that job, and Cancel always works: the job is asked to stop (soft, through JobContext.check_cancel),
            and the worker is killed if it has not after a grace period. A new worker starts with the next job.

The callback is called from background threads; the UI bridges it to a Qt signal.
"""
from __future__ import annotations

import collections
import itertools
import multiprocessing as mp
import queue
import threading
import time
from typing import Callable

from . import procs
from .job import Job, run_job

GRACE_S = 1.5


class Runner:
    def __init__(self, on_event: Callable[[int, str, tuple], None], plugin_dirs: list | None = None,
                 workers: int = 1):
        self._on_event = on_event
        self._ids = itertools.count(1)
        self.thread = _ThreadLane(self._emit)
        self.process = _ProcessLane(self._emit, plugin_dirs, workers)

    def _emit(self, job_id: int, kind: str, payload: tuple = ()) -> None:
        try:
            self._on_event(job_id, kind, payload)
        except Exception:   # never let a UI handler kill a lane
            import traceback
            traceback.print_exc()

    def submit(self, job: Job, lane: str = "thread") -> int:
        job.id = next(self._ids)
        (self.process if lane == "process" else self.thread).submit(job)
        return job.id

    def cancel(self, job_id: int) -> None:
        self.thread.cancel(job_id)
        self.process.cancel(job_id)

    def cancel_group(self, group: str) -> None:
        self.thread.cancel_group(group)
        self.process.cancel_group(group)

    def busy(self) -> bool:
        return self.thread.busy() or self.process.busy()

    def shutdown(self) -> None:
        self.thread.shutdown()
        self.process.shutdown()


class _Lane:
    def __init__(self, emit):
        self.emit = emit
        self.pending: collections.deque[Job] = collections.deque()
        self.cv = threading.Condition()
        self.current: Job | None = None
        self.stop = False

    def submit(self, job: Job) -> None:
        with self.cv:
            if job.group:
                dropped = [j for j in self.pending if j.group == job.group]
                for j in dropped:
                    self.pending.remove(j)
                    self.emit(j.id, "cancelled", ("superseded",))
            self.pending.append(job)
            self.cv.notify_all()
        self.emit(job.id, "queued", ())

    def _drop_where(self, pred) -> None:
        with self.cv:
            for j in [j for j in self.pending if pred(j)]:
                self.pending.remove(j)
                self.emit(j.id, "cancelled", ())

    def cancel(self, job_id: int) -> None:
        self._drop_where(lambda j: j.id == job_id)
        if self.current is not None and self.current.id == job_id:
            self._cancel_current()

    def cancel_group(self, group: str) -> None:
        self._drop_where(lambda j: j.group == group)
        if self.current is not None and self.current.group == group:
            self._cancel_current()

    def busy(self) -> bool:
        return self.current is not None or bool(self.pending)

    def _next(self) -> Job | None:
        with self.cv:
            while not self.pending and not self.stop:
                self.cv.wait()
            if self.stop:
                return None
            self.current = self.pending.popleft()
            return self.current

    def _cancel_current(self) -> None:
        raise NotImplementedError

    def shutdown(self) -> None:
        with self.cv:
            self.stop = True
            self.pending.clear()
            self.cv.notify_all()


class _ThreadLane(_Lane):
    def __init__(self, emit):
        super().__init__(emit)
        self.cancel_flag = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="atlas-runner", daemon=True)
        self.thread.start()

    def _cancel_current(self) -> None:
        self.cancel_flag.set()

    def _loop(self) -> None:
        from .registry import registry
        while True:
            job = self._next()
            if job is None:
                return
            self.cancel_flag.clear()
            self.emit(job.id, "started", ())
            try:
                run_job(job, lambda k, p, i=job.id: self.emit(i, k, p), self.cancel_flag.is_set, registry())
            except Exception as e:
                self.emit(job.id, "error", (f"{type(e).__name__}: {e}", ""))
            finally:
                self.current = None


def _worker_main(inbox, outbox, cancel_ev, plugin_dirs) -> None:
    """Entry point of the worker process: discover tools once, then run jobs until told to stop."""
    procs.lead_process_group()
    procs.watch_parent()
    from .registry import registry
    reg = registry(plugin_dirs)
    while True:
        job = inbox.get()
        if job is None:
            return
        run_job(job, lambda k, p, i=job.id: outbox.put((i, k, p)), cancel_ev.is_set, reg)
        outbox.put((job.id, "_idle", ()))


class _ProcessLane(_Lane):
    """One or more warm worker processes (`workers`, Setup › Outputs) taking jobs from one queue, in order."""

    def __init__(self, emit, plugin_dirs, workers: int = 1):
        super().__init__(emit)
        self.plugin_dirs = plugin_dirs
        self.ctx = mp.get_context("spawn")
        self.workers: list[_Worker] = []
        self.set_workers(workers)

    def set_workers(self, n: int) -> None:
        """How many jobs run at once from now on. Extra workers finish their job first, then stop."""
        n = max(1, int(n))
        with self.cv:
            live = [w for w in self.workers if not w.retire]
            for w in live[n:]:
                w.retire = True
            for _ in range(n - len(live)):
                w = _Worker(self)
                self.workers.append(w)
                w.thread.start()
            self.cv.notify_all()

    def warm_up(self) -> None:
        """Start the first worker early (its imports take a second or two), so the first run is quick."""
        with self.cv:
            w = next((w for w in self.workers if not w.retire), None)
        if w is not None:
            w.warm_up()

    def _take(self, w: "_Worker") -> Job | None:
        with self.cv:
            while not self.pending and not self.stop and not w.retire:
                self.cv.wait()
            if self.stop or w.retire:
                return None
            w.current = self.pending.popleft()
            return w.current

    def _running(self) -> list:
        return [w for w in list(self.workers) if w.current is not None]

    def cancel(self, job_id: int) -> None:
        self._drop_where(lambda j: j.id == job_id)
        for w in self._running():
            if w.current is not None and w.current.id == job_id:
                w.cancel_current()

    def cancel_group(self, group: str) -> None:
        self._drop_where(lambda j: j.group == group)
        for w in self._running():
            if w.current is not None and w.current.group == group:
                w.cancel_current()

    def busy(self) -> bool:
        return bool(self.pending) or bool(self._running())

    def shutdown(self) -> None:
        super().shutdown()
        for w in list(self.workers):
            w.shutdown()


class _Worker:
    """One worker process and the thread that feeds it jobs and forwards its events."""

    def __init__(self, lane: _ProcessLane):
        self.lane = lane
        self.proc = None
        self.inbox = self.outbox = self.cancel_ev = None
        self.current: Job | None = None
        self.retire = False
        self.cancel_requested = 0.0
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, name="atlas-worker-pump", daemon=True)

    def _spawn(self) -> None:
        ctx = self.lane.ctx
        self.inbox, self.outbox = ctx.Queue(), ctx.Queue()
        self.cancel_ev = ctx.Event()
        self.proc = ctx.Process(target=_worker_main, name="atlas-worker", daemon=True,
                                args=(self.inbox, self.outbox, self.cancel_ev, self.lane.plugin_dirs))
        self.proc.start()

    def warm_up(self) -> None:
        with self.lock:
            if self.proc is None or not self.proc.is_alive():
                self._spawn()

    def cancel_current(self) -> None:
        if self.cancel_ev is not None:
            self.cancel_ev.set()
            self.cancel_requested = time.monotonic()

    def _kill(self) -> None:
        # Taken first: the feeding thread and shutdown() both come here, and either may get in first.
        proc, self.proc = self.proc, None
        if proc is not None:
            procs.kill_tree(proc)
            proc.join(2)

    def _loop(self) -> None:
        while True:
            job = self.lane._take(self)
            if job is None:
                self._kill()
                with self.lane.cv:
                    if self in self.lane.workers:
                        self.lane.workers.remove(self)
                return
            self.warm_up()
            self.cancel_ev.clear()
            self.cancel_requested = 0.0
            self.lane.emit(job.id, "started", ())
            self.inbox.put(job)
            self._pump(job)
            self.current = None

    def _pump(self, job: Job) -> None:
        """Forward the worker's events for `job` until it ends, dies or is cancelled."""
        emit = self.lane.emit
        finished = False
        while True:
            try:
                jid, kind, payload = self.outbox.get(timeout=0.1)
            except queue.Empty:
                if self.cancel_requested and time.monotonic() - self.cancel_requested > GRACE_S:
                    self._kill()
                    if not finished:
                        emit(job.id, "cancelled", ("killed",))
                    return
                proc = self.proc
                if proc is None or not proc.is_alive():
                    code = proc.exitcode if proc is not None else None
                    if proc is not None:
                        procs.kill_tree(proc)        # a helper it started must not run on without it
                    self.proc = None
                    if not finished:
                        emit(job.id, "error", (f"The worker process stopped unexpectedly (exit code {code}). "
                                               "The input may be too large for memory.", ""))
                    return
                continue
            if kind == "_idle":
                return
            if kind in ("done", "error", "cancelled"):
                finished = True
            emit(jid, kind, payload)

    def shutdown(self) -> None:
        proc = self.proc                  # the feeding thread may clear it at any moment
        try:
            if proc is not None and proc.is_alive():
                self.inbox.put(None)
                proc.join(1)
        finally:
            self._kill()
