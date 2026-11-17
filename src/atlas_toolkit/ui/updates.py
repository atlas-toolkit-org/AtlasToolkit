"""
The update strip under the top bar (core/updates.py does the asking).

    ask        until the person answers, once: "Check for new versions once a day?" Yes sets Setup's option to
               "Check once a day" and checks at once; No thanks sets it to "Never check". Either can be changed
               later in Setup › General.
    available  "Atlas Toolkit X.Y.Z is out": Download (the site's download page), What's new (the release notes),
               Skip this version (not offered again; a later one is), and × (hidden until the next start).

The network request runs on a worker thread; its answer comes back through a signal, and only the UI thread writes
app.json. Nothing is shown when a check fails: Check for updates in About says so instead.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton
from shiboken6 import isValid

from ..core import updates
from ..version import APP_NAME, __version__
from .widgets.common import apply_icon, icon_button

START_DELAY_MS = 2500        # after the window is up, so a slow network never delays the start


class UpdateBar(QFrame):
    fetched = Signal(object, object)        # (latest version or None, callback or None), from the worker thread

    def __init__(self, ctx):
        super().__init__(objectName="updatebar")
        self.ctx = ctx
        self.mode = ""                      # "", "ask" or "available"
        self.version: str | None = None
        self._busy = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 8, 12, 8)
        lay.setSpacing(8)
        self.text = QLabel(objectName="updatetext")
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.RichText)
        lay.addWidget(self.text, 1)
        self.buttons = {}
        for key, label, variant in (("yes", "Check once a day", "primary"), ("no", "No thanks", "ghost"),
                                    ("download", "Download", "primary"), ("notes", "What's new", "ghost"),
                                    ("skip", "Skip this version", "ghost")):
            b = QPushButton(label)
            b.setProperty("variant", variant)
            b.setCursor(Qt.PointingHandCursor)
            lay.addWidget(b)
            self.buttons[key] = b
        self.btn_close = icon_button("close", "Hide until the next start", ctx.theme, size=14)
        lay.addWidget(self.btn_close)
        self.buttons["yes"].clicked.connect(self.accept)
        self.buttons["no"].clicked.connect(self.decline)
        self.buttons["download"].clicked.connect(lambda: self._open(updates.DOWNLOAD_URL))
        self.buttons["notes"].clicked.connect(lambda: self._open(updates.NOTES_URL.format(version=self.version)))
        self.buttons["skip"].clicked.connect(self.skip)
        self.btn_close.clicked.connect(self.dismiss)
        self.fetched.connect(self._fetched)
        self.hide()

    def retheme(self, theme) -> None:
        apply_icon(self.btn_close, theme)

    # ------------------------------------------------------------------ what to show
    def start(self, delay_ms: int = START_DELAY_MS) -> None:
        """At start: ask, or show what an earlier check found and check again when a day has passed."""
        if updates.disabled():
            return
        choice = self.ctx.pref("update_check")
        if choice == "ask":
            self._show("ask")
        elif choice == "daily":
            found = updates.offer(self.ctx.settings)
            if found:
                self._show("available", found)
            if updates.due(self.ctx.settings):
                QTimer.singleShot(delay_ms, lambda: isValid(self) and self.check_now())

    def _show(self, mode: str, version: str | None = None) -> None:
        self.mode, self.version = mode, version
        if mode == "ask":
            self.text.setText(f"<b>Hear about new versions?</b> {APP_NAME} can ask atlas-toolkit.org for the latest "
                              "version number once a day. It sends nothing about you or your files.")
        elif mode == "available":
            self.text.setText(f"<b>{APP_NAME} {version} is out.</b> You have {__version__}.")
        for key, b in self.buttons.items():
            b.setVisible(key in (("yes", "no") if mode == "ask" else ("download", "notes", "skip")))
        self.btn_close.setVisible(mode == "available")
        self.setVisible(bool(mode))

    def dismiss(self) -> None:
        self.mode = ""
        self.hide()

    # ------------------------------------------------------------------ answers
    def accept(self) -> None:
        self.ctx.set_pref("update_check", "daily")
        self.dismiss()
        self.check_now()

    def decline(self) -> None:
        self.ctx.set_pref("update_check", "never")
        self.dismiss()
        self._toast("No update checks. Setup › General can turn them on.")

    def skip(self) -> None:
        if self.version:
            self.ctx.settings.set("update_skip", self.version)
            self.ctx.settings.save()
        self.dismiss()

    def pref_changed(self) -> None:
        """Setup changed the option: never hides the strip, daily answers the question (and checks if due)."""
        choice = self.ctx.pref("update_check")
        if choice != "ask" and self.mode == "ask":
            self.dismiss()
        if choice == "never" and self.mode == "available":
            self.dismiss()
        if choice == "daily" and not updates.disabled() and updates.due(self.ctx.settings):
            self.check_now()

    # ------------------------------------------------------------------ checking
    def check_now(self, callback=None, fetch=None) -> None:
        """Ask now, off the UI thread. `callback(latest, offered)` runs on the UI thread when it ends (About's
        button): latest is None when nothing answered."""
        if self._busy:
            return
        self._busy = True

        def work():
            found = updates.latest(fetch=fetch)
            if isValid(self):
                self.fetched.emit(found, callback)

        threading.Thread(target=work, daemon=True, name="atlas-update-check").start()

    def _fetched(self, found, callback) -> None:
        self._busy = False
        updates.record(self.ctx.settings, found)
        offered = updates.offer(self.ctx.settings)
        if callback is not None:                      # asked for by hand: offer it even if it was skipped
            offered = found if found and updates.newer(found) else None
        if offered and (callback is not None or self.ctx.pref("update_check") != "never"):
            self._show("available", offered)
        if callback is not None:
            callback(found, offered)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _open(url: str) -> None:
        from ..core.paths import open_url
        open_url(url)

    def _toast(self, text: str) -> None:
        if callable(getattr(self.ctx, "toast", None)):
            self.ctx.toast(text)
