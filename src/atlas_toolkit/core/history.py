"""
Run history: one JSON line per finished job in <data>/history.jsonl, read by the Activity page (re-run with the
same settings, reveal outputs). The file is trimmed to the newest MAX_LINES entries now and then.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .paths import data_dir

MAX_LINES = 5000


@dataclass
class RunRecord:
    tool: str
    tool_version: str
    status: str                      # done | error | cancelled
    src: str
    outputs: list = field(default_factory=list)
    values: dict = field(default_factory=dict)
    seconds: float = 0.0
    message: str = ""
    toolkit_version: str = ""
    time: float = field(default_factory=time.time)
    stats: dict = field(default_factory=dict)
    workflow: dict = field(default_factory=dict)   # a workflow run (tool "workflow"): {"name", "path"}


class History:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else data_dir() / "history.jsonl"

    def append(self, rec: RunRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(rec), ensure_ascii=False, default=str) + "\n")

    def read(self, limit: int = 500) -> list[RunRecord]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for ln in reversed(lines[-limit:]):
            try:
                out.append(RunRecord(**json.loads(ln)))
            except (ValueError, TypeError):
                continue
        return out

    def trim(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if len(lines) > MAX_LINES:
            self.path.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
