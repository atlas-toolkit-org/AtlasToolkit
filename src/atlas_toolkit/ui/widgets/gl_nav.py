"""
What a 3D view needs beyond pyqtgraph's mouse handling. A Mac trackpad has no middle button, and its two-finger swipe
arrives as a wheel event, which would only zoom:

- a pinch zooms, wherever the system reports one;
- on macOS a two-finger swipe orbits, and with Shift it pans.

A real wheel still zooms, a drag still orbits, and Command-drag (Ctrl-drag elsewhere) still pans. Windows and Linux
touchpads keep the behaviour they have, where a two-finger swipe zooms.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, Qt


class GestureNavigation:
    """Mix-in, before gl.GLViewWidget in the bases."""

    def _navigated(self) -> None:
        moved = getattr(self, "moved", None)
        if moved is not None:
            moved.emit()

    def event(self, ev):
        if ev.type() == QEvent.Type.NativeGesture and ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.opts["distance"] /= max(0.2, 1.0 + ev.value())
            self.update()
            self._navigated()
            ev.accept()
            return True
        return super().event(ev)

    def wheelEvent(self, ev):
        if sys.platform == "darwin" and ev.phase() != Qt.ScrollPhase.NoScrollPhase and not ev.pixelDelta().isNull():
            delta = ev.pixelDelta()
            if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.pan(delta.x(), delta.y(), 0, relative="view-upright")
            else:
                self.orbit(-delta.x() * 0.5, delta.y() * 0.5)
            self.update()
            ev.accept()
            return
        super().wheelEvent(ev)
