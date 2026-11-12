"""Keyboard shortcuts as this system writes them: Ctrl+K here, ⌘K on a Mac. Qt already maps Ctrl to Command there, so only
the text the user reads has to change."""
from __future__ import annotations

import re
import sys

from PySide6.QtGui import QKeySequence

_SHORTCUT = re.compile(r"\b(?:(?:Ctrl|Alt|Shift)\+)+(?:[A-Za-z0-9]+|,)")
_ALONE = {"Ctrl": "⌘", "Alt": "⌥", "Shift": "⇧"}


def shown(text: str) -> str:
    """`text` with every shortcut in it written the way this system does, as in "Save the workflow (Ctrl+S)"."""
    if sys.platform != "darwin":
        return text
    text = _SHORTCUT.sub(lambda m: QKeySequence(m[0]).toString(QKeySequence.SequenceFormat.NativeText), text)
    return re.sub(r"\b(Ctrl|Alt|Shift)\b", lambda m: _ALONE[m[1]], text)     # also a key named on its own: "Ctrl ÷ 10"
