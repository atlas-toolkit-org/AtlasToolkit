"""A 3D view on a machine whose OpenGL does not work: a message in its place, not an error for every frame."""
from __future__ import annotations

import traceback

from PySide6.QtCore import QTimer

from ...core.paths import data_dir


def _reason(error: Exception) -> str:
    text = getattr(error, "description", None)             # PyOpenGL's GLError prints as many lines, and this is the point
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    text = str(text or error).strip().split("\n")[0]
    return f"OpenGL does not work on this machine ({text})"


class GuardedGL:
    """Mixed in before GLViewWidget. Qt hands an exception from painting to sys.excepthook and then paints again, so one
    driver that cannot draw would raise for every frame. The first failure stops the view and tells its owner, once."""
    gl_failure = ""
    on_gl_failure = None          # called with the reason, on the next turn of the event loop: not from inside a paint

    def _guarded(self, step) -> None:
        if self.gl_failure:
            return
        try:
            step()
        except Exception as e:    # any GL or driver error, from PyOpenGL, pyqtgraph or the shaders
            self.gl_failure = _reason(e)
            try:
                with (data_dir() / "crash.log").open("a", encoding="utf-8") as log:
                    log.write(traceback.format_exc() + "\n")
            except OSError:
                pass
            if self.on_gl_failure is not None:
                QTimer.singleShot(0, lambda: self.on_gl_failure(self.gl_failure))

    def initializeGL(self):
        self._guarded(super().initializeGL)

    def paintGL(self):
        self._guarded(super().paintGL)
