"""
JobBridge: the runner reports from background threads; the bridge re-emits on the UI thread and routes each event
to the callback given at submit time. It also counts active jobs for the top-bar badge and the footer.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Signal

from ..core.job import Job
from ..core.runner import Runner

TERMINAL = ("done", "error", "cancelled")


class JobBridge(QObject):
    event = Signal(int, str, object)
    activity = Signal(int, int)          # running, queued
    progress = Signal(int, float, str)   # job id, fraction, message (for the footer)
    log = Signal(str, str, str)          # level, message, source

    def __init__(self, plugin_dirs: list | None = None, workers: int = 1):
        super().__init__()
        self.handlers: dict[int, Callable] = {}
        self.meta: dict[int, Job] = {}
        self.running: set[int] = set()
        self.queued: set[int] = set()
        self.event.connect(self._dispatch)
        self.runner = Runner(lambda jid, kind, payload: self.event.emit(jid, kind, payload), plugin_dirs, workers)

    def submit(self, job: Job, lane: str, handler: Callable[[str, tuple], None]) -> int:
        jid = self.runner.submit(job, lane)
        self.handlers[jid] = handler
        self.meta[jid] = job
        return jid

    def cancel(self, jid: int) -> None:
        self.runner.cancel(jid)

    def cancel_group(self, group: str) -> None:
        self.runner.cancel_group(group)

    def _dispatch(self, jid: int, kind: str, payload) -> None:
        if kind == "queued":
            self.queued.add(jid)
        elif kind == "started":
            self.queued.discard(jid)
            self.running.add(jid)
        elif kind == "progress":
            self.progress.emit(jid, payload[0], payload[1])
        elif kind == "log":
            job = self.meta.get(jid)
            src = f"{job.tool_id} · {job.src.rsplit(chr(92), 1)[-1].rsplit('/', 1)[-1]}" if job else ""
            self.log.emit(payload[0], payload[1], src)
        if kind in TERMINAL:
            self.queued.discard(jid)
            self.running.discard(jid)
        h = self.handlers.get(jid)
        if h is not None:
            try:
                h(kind, payload)
            finally:
                if kind in TERMINAL:
                    self.handlers.pop(jid, None)
                    self.meta.pop(jid, None)
        elif kind in TERMINAL:
            self.meta.pop(jid, None)
        if kind in ("queued", "started") or kind in TERMINAL:
            self.activity.emit(len(self.running), len(self.queued))

    def busy(self) -> bool:
        return bool(self.running or self.queued)

    def shutdown(self) -> None:
        self.runner.shutdown()
