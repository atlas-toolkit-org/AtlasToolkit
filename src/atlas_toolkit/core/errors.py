"""Errors a tool raises on purpose: the input or the settings cannot work, and the user can fix it.

A `ToolError` is expected: the app shows its message without a traceback, puts `hint` (what to do) in a block of
its own, in the report and the Log, and offers to open `suggest_tool` (a tool id) on the same file. Any tool may
raise one; `geom.repair.NeedsRepair` is the one the solid-mesh tools raise.
"""
from __future__ import annotations


class ToolError(ValueError):
    def __init__(self, message: str, hint: str = "", suggest_tool: str = ""):
        super().__init__(message)
        self.hint = hint
        self.suggest_tool = suggest_tool

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base}\n{self.hint}" if self.hint else base

    @property
    def message(self) -> str:
        return super().__str__()
