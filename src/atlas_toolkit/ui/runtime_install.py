"""
The installer window for an optional component that has an installer module (Runtime.installer; Setup › Components).
Nothing here names a component: the module says what it will do and the window shows it.

    Ready     what this computer has (each check with a tick, a note or a cross), the build and why, what is
              downloaded, the disk space, the time, the folder. Nothing starts before Install.
    Running   each stage with its state, one progress bar with the size done, the speed and the time left, the
              program's own output under Details, the log file. Cancel removes what was begun.
    Result    installed (where, what was proven, size, time) or what went wrong, why, and what to do; Open folder,
              Check with a model, Copy details, Try again.

Modeless: the app stays usable while it installs. The work runs on a thread; its news arrives through signals.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from ..core import runtimes
from ..core.paths import display_path, reveal
from .about import _card, _page, _scroll, _text
from .theme import icon, mono
from .widgets.common import label

STATE_ICON = {"ok": ("check", "success"), "info": ("info", "text-3"), "warn": ("info", "warning"),
              "fail": ("close", "danger"), "waiting": ("clock", "text-4"), "running": ("activity", "highlight"),
              "done": ("check", "success"), "failed": ("close", "danger"), "skipped": ("info", "text-3")}


class _News(QObject):
    """The install thread's observer: every call becomes a queued signal, handled on the UI thread."""
    stage_changed = Signal(int, str, str)
    progressed = Signal(float, float, float, object)
    logged = Signal(str)
    planned = Signal(object, object)          # plan, error text
    finished = Signal(object)                 # the installer's Result
    checked = Signal(bool, object)            # works, readable lines

    def stage(self, index, state, note=""):
        self.stage_changed.emit(index, state, note)

    def progress(self, done, total, rate, eta):
        self.progressed.emit(done, total, rate, eta)

    def log(self, line):
        self.logged.emit(line)


def _fmt_eta(eta) -> str:
    if eta is None:
        return ""
    return f" · about {eta / 60:.0f} min left" if eta >= 90 else f" · {max(eta, 1):.0f} s left"


class RuntimeInstallDialog(QDialog):
    progress_text = Signal(str)               # "Installing… 45%" for the Components card; "" when it ends
    changed = Signal()                        # installed or removed: the card and the tools look again

    def __init__(self, ctx, key: str, parent=None, build: str = "auto"):
        super().__init__(parent)
        self.ctx, self.key = ctx, key
        self.rt = runtimes.get(key)
        self.inst = self.rt.installer_module()
        self.theme = ctx.theme
        self.build = build
        self.plan = None
        self.result = None
        self.internet = None                  # the plan's internet check, reused when only the build changes
        self.cancelled = threading.Event()
        self.thread: threading.Thread | None = None
        self.news = _News()
        self.news.stage_changed.connect(self._stage)
        self.news.progressed.connect(self._progress)
        self.news.logged.connect(self._log)
        self.news.planned.connect(self._planned)
        self.news.finished.connect(self._finished)
        self.news.checked.connect(self._checked)
        self.setObjectName("setupDialog")
        self.setWindowTitle(f"Install {self.rt.label}")
        self.setWindowIcon(icon("down", self.theme["highlight"], 32))
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowMinMaxButtonsHint
                            | Qt.WindowCloseButtonHint)
        self.setSizeGripEnabled(True)
        self.setMinimumSize(620, 480)
        self.resize(760, 720)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget()
        lay.addWidget(self.pages, 1)
        self.ready_page = QWidget()
        self.run_page = QWidget()
        self.result_page = QWidget()
        for p in (self.ready_page, self.run_page, self.result_page):
            QVBoxLayout(p).setContentsMargins(0, 0, 0, 0)
            self.pages.addWidget(p)
        self.footer = QHBoxLayout()
        self.footer.setContentsMargins(20, 10, 20, 14)
        lay.addLayout(self.footer)
        self.detect()

    # ------------------------------------------------------------------ helpers
    def _icon(self, state: str, px: int = 16) -> QLabel:
        name, tone = STATE_ICON.get(state, ("info", "text-3"))
        lab = QLabel()
        lab.setPixmap(icon(name, self.theme[tone], px).pixmap(px, px))
        lab.setFixedSize(px + 4, px + 4)
        return lab

    def _set_page(self, page: QWidget, body: QWidget) -> None:
        old = page.layout().takeAt(0)
        if old is not None and old.widget() is not None:
            old.widget().hide()              # gone at once, not when the event loop gets to deleting it
            old.widget().deleteLater()
        page.layout().addWidget(body)
        self.pages.setCurrentWidget(page)

    def _buttons(self, *buttons) -> None:
        while self.footer.count():
            item = self.footer.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.footer.addStretch(1)
        for b in buttons:
            self.footer.addWidget(b)

    def _button(self, text: str, slot, primary: bool = False, icon_name: str = "") -> QPushButton:
        b = QPushButton(text)
        if primary:
            b.setProperty("variant", "primary")
        if icon_name:
            b.setIcon(icon(icon_name, self.theme["on-accent" if primary else "text-2"], 14))
        b.clicked.connect(slot)
        return b

    def _head(self, lay, title: str, sub: str = "") -> None:
        lay.addWidget(_text(title, "title", wrap=False))
        if sub:
            lay.addWidget(_text(sub, "summary"))

    # ------------------------------------------------------------------ ready
    def detect(self, keep_internet: bool = False) -> None:
        """Look at this computer and make the plan, off the UI thread (it may start nvidia-smi and reach the network)."""
        w, lay = _page()
        self._head(lay, f"Install the {self.rt.label}", "Looking at this computer: graphics card, disk space, "
                                                       "internet connection …")
        lay.addStretch(1)
        self._set_page(self.ready_page, _scroll(w))
        self._buttons(self._button("Close", self.close))
        build, with_model = self.build, bool(getattr(self, "with_model", False))
        internet = self.internet if keep_internet else None

        def work():
            try:
                plan = self.inst.plan(build, with_model, settings=self.ctx.settings, online=internet is None)
                if internet is not None:
                    plan.checks.append(internet)
                self.news.planned.emit(plan, None)
            except Exception as e:      # an unexpected failure must still reach the window
                self.news.planned.emit(None, f"{type(e).__name__}: {e}")

        threading.Thread(target=work, daemon=True).start()

    def _planned(self, plan, error) -> None:
        if plan is None:
            w, lay = _page()
            self._head(lay, f"Install the {self.rt.label}", f"Could not look at this computer: {error}")
            self._set_page(self.ready_page, _scroll(w))
            self._buttons(self._button("Try again", self.detect), self._button("Close", self.close))
            return
        self.plan = plan
        self.internet = next((c for c in plan.checks if c.title == "Internet"), self.internet)
        w, lay = _page()
        self._head(lay, f"Install the {self.rt.label}", "Nothing is downloaded until you press Install.")

        card, cl = _card("This computer")
        for c in plan.checks:
            cl.addLayout(self._check_row(c.level, c.title, c.text))
        lay.addWidget(card)

        card, cl = _card("What will be installed")
        row = QHBoxLayout()
        row.addWidget(label("Build"))
        self.build_box = QComboBox()
        builds = self.inst.builds()
        for k, title, _about in builds:
            self.build_box.addItem(title + ("  (recommended)" if k == plan.recommended else ""), k)
        self.build_box.setCurrentIndex(max(0, self.build_box.findData(plan.build)))
        self.build_box.currentIndexChanged.connect(self._build_changed)
        row.addWidget(self.build_box, 1)
        cl.addLayout(row)
        about = next((a for k, _t, a in builds if k == plan.build), "")
        cl.addWidget(_text(f"{plan.choice.reason} {about}", "muted"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        rows = [(h, t) for h, t in plan.summary() if h != "Build"]      # the build is the list above
        for r, (head, text) in enumerate(rows):
            h = label(head)
            grid.addWidget(h, r, 0, Qt.AlignTop)
            grid.addWidget(_text(text, "muted", select=True), r, 1)
        grid.setColumnStretch(1, 1)
        cl.addLayout(grid)
        self.model_box = QCheckBox("Also download the default depth model now, so the first drawing does not wait for it")
        self.model_box.setChecked(plan.with_model)
        self.model_box.toggled.connect(self._model_toggled)
        cl.addWidget(self.model_box)
        lay.addWidget(card)
        lay.addStretch(1)
        self._set_page(self.ready_page, _scroll(w))

        go = self._button("Install", self.start, primary=True, icon_name="down")
        go.setEnabled(plan.ok())
        if not plan.ok():
            go.setToolTip("Fix what is marked with a cross first.")
        self._buttons(self._button("Detect again", lambda: self.detect()), self._button("Cancel", self.close), go)

    def _check_row(self, level: str, title: str, text: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self._icon(level), 0, Qt.AlignTop)
        lab = _text(f"<b>{title}</b> · {text}", "muted", select=True)
        lab.setTextFormat(Qt.RichText)
        row.addWidget(lab, 1)
        return row

    def _build_changed(self, _i: int) -> None:
        self.build = self.build_box.currentData()
        self.detect(keep_internet=True)

    def _model_toggled(self, on: bool) -> None:
        self.with_model = on
        self.detect(keep_internet=False)

    # ------------------------------------------------------------------ running
    def start(self) -> None:
        if self.ctx.bridge.busy():
            QMessageBox.information(self, "Wait for the running jobs",
                                    "Some tools are still running. The install replaces the environment at its end, so "
                                    "let them finish (or cancel them) first, then press Install again.")
            return
        plan = self.plan
        w, lay = _page()
        self._head(lay, f"Installing the {self.rt.label}", f"{plan.label} into {display_path(plan.folders.target)}. "
                                                           "You can keep using the app meanwhile.")
        card, cl = _card()
        self.stage_rows = []
        for title in (t for _k, t in plan.stages()):
            row = QHBoxLayout()
            ic = self._icon("waiting")
            text = _text(title, "muted", wrap=True)
            note = _text("", "muted", wrap=False)
            row.addWidget(ic, 0, Qt.AlignTop)
            row.addWidget(text, 1)
            row.addWidget(note)
            cl.addLayout(row)
            self.stage_rows.append((ic, text, note))
        lay.addWidget(card)
        self.bar = QProgressBar(textVisible=False)
        self.bar.setRange(0, 1000)
        lay.addWidget(self.bar)
        self.speed = _text("Starting …", "muted", wrap=False)
        self.speed.setFont(mono(11.5))
        lay.addWidget(self.speed)
        self.details_btn = QPushButton("Show details")
        self.details_btn.setProperty("variant", "ghost")
        self.details_btn.setCheckable(True)
        lay.addWidget(self.details_btn, 0, Qt.AlignLeft)
        self.details = QPlainTextEdit(readOnly=True, objectName="commands")
        self.details.setFont(mono(10.5))
        self.details.setMaximumBlockCount(3000)
        self.details.setVisible(False)
        self.details.setMinimumHeight(200)
        self.details_btn.toggled.connect(lambda on: (self.details.setVisible(on),
                                                     self.details_btn.setText("Hide details" if on else "Show details")))
        lay.addWidget(self.details, 1)
        self._set_page(self.run_page, w)
        self.stop_btn = self._button("Cancel", self.cancel)
        self.stop_btn.setToolTip("Stops now and removes what was downloaded; your current environment, if any, is untouched.")
        self._buttons(self.stop_btn)
        self.cancelled.clear()
        release = self.ctx.bridge.runner.recycle_process_lane
        settings = self.ctx.settings

        def work():
            try:
                result = self.inst.install(plan, self.news, self.cancelled, release, settings)
            except Exception as e:      # install() reports expected failures itself; this is a bug, still shown
                result = e
            self.news.finished.emit(result)

        self.thread = threading.Thread(target=work, daemon=True, name="atlas-installer")
        self.thread.start()
        self.progress_text.emit("Installing … 0%")

    def _stage(self, index: int, state: str, note: str) -> None:
        if not hasattr(self, "stage_rows") or index >= len(self.stage_rows):
            return
        ic, text, note_lab = self.stage_rows[index]
        name, tone = STATE_ICON.get(state, ("info", "text-3"))
        ic.setPixmap(icon(name, self.theme[tone], 16).pixmap(16, 16))
        text.setProperty("class", "h3" if state == "running" else "muted")
        text.style().unpolish(text)
        text.style().polish(text)
        note_lab.setText(note)

    def _progress(self, done: float, total: float, rate: float, eta) -> None:
        if not hasattr(self, "bar"):
            return
        frac = done / total if total else 0
        self.bar.setValue(int(frac * 1000))
        speed = f" · {rate:.1f} MB/s" if rate > 0.05 else ""
        self.speed.setText(f"{frac * 100:.0f}% · {done / 1024:.2f} of {total / 1024:.2f} GB{speed}{_fmt_eta(eta)}")
        self.progress_text.emit(f"Installing … {frac * 100:.0f}%")

    def _log(self, line: str) -> None:
        if hasattr(self, "details"):
            self.details.appendPlainText(line)

    def cancel(self) -> None:
        if self.thread is None or not self.thread.is_alive():
            return
        if QMessageBox.question(self, "Cancel the install?", "Stop now and remove what was downloaded? Your current "
                                "environment, if any, stays as it is.") != QMessageBox.Yes:
            return
        self.cancelled.set()
        self.stop_btn.setEnabled(False)
        self.speed.setText("Cancelling …")

    # ------------------------------------------------------------------ result
    def _finished(self, result) -> None:
        self.progress_text.emit("")
        self.result = result
        w, lay = _page()
        if isinstance(result, Exception):
            ok, lines, state = False, [f"Unexpected error: {type(result).__name__}: {result}",
                                      "This looks like a bug in Atlas: please report it."], "failed"
        else:
            ok, lines = result.ok, result.lines()
            state = "done" if ok else "skipped" if result.cancelled else "failed"
        title = ("Installed" if ok else "Cancelled" if getattr(result, "cancelled", False) else "The install did not finish")
        head = QHBoxLayout()
        head.addWidget(self._icon(state, 28), 0, Qt.AlignVCenter)
        head.addWidget(_text(title, "title", wrap=False), 1)
        lay.addLayout(head)
        card, cl = _card()
        for line in lines:
            cl.addWidget(_text(line, "muted", select=True))
        lay.addWidget(card)
        self.check_out = QPlainTextEdit(readOnly=True, objectName="commands")
        self.check_out.setFont(mono(10.5))
        self.check_out.setVisible(False)
        lay.addWidget(self.check_out, 1)
        lay.addStretch(0)
        self._set_page(self.result_page, _scroll(w) if not ok else w)
        buttons = []
        if ok:
            self.ctx.refresh_runtimes()
            self.changed.emit()
            buttons += [self._button("Open folder", lambda: reveal(result.folder), icon_name="folder"),
                        self._button("Check with a model", self.check_model, icon_name="activity")]
        elif not isinstance(result, Exception) and not result.cancelled:
            buttons.append(self._button("Copy details", self.copy_details, icon_name="copy"))
            for k in result.fallbacks:
                label_ = dict((b[0], b[1]) for b in self.inst.builds()).get(k, k)
                buttons.append(self._button(f"Install {label_}", lambda _=False, k=k: self._retry(k)))
            buttons.append(self._button("Try again", lambda: self._retry(self.plan.build)))
        if getattr(result, "log_path", None):
            buttons.insert(0, self._button("Open log", lambda: reveal(result.log_path), icon_name="doc"))
        buttons.append(self._button("Close", self.close, primary=ok))
        self._buttons(*buttons)

    def _retry(self, build: str) -> None:
        self.build = build
        self.detect(keep_internet=False)

    def copy_details(self) -> None:
        r = self.result
        text = "\n".join(r.lines()) if hasattr(r, "lines") else str(r)
        if getattr(r, "error", None) is not None:
            text += f"\n\nkind: {r.error.kind}\nstage: {r.error.stage}"
        if getattr(r, "log_path", None):
            text += f"\nlog: {r.log_path}"
        QGuiApplication.clipboard().setText(text)

    def check_model(self) -> None:
        self.check_out.setVisible(True)
        self.check_out.setPlainText("Running the default model once (it downloads on first use, about 1.3 GB) …")
        model = self.inst.default_model()
        news = _News()
        news.logged.connect(self.check_out.appendPlainText)
        self._check_news = news

        def work():
            ok, _info, lines = self.inst.check(model, None, news)
            self.news.checked.emit(ok, lines)

        threading.Thread(target=work, daemon=True).start()

    def _checked(self, ok: bool, lines) -> None:
        if hasattr(self, "check_out"):
            self.check_out.appendPlainText("")
            for line in lines:
                self.check_out.appendPlainText(line)

    # ------------------------------------------------------------------ closing
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def closeEvent(self, e) -> None:
        if self.running():
            if QMessageBox.question(self, "Cancel the install?", "Closing this window cancels the install and "
                                    "removes what was downloaded. Your current environment, if any, stays as it is.") \
                    != QMessageBox.Yes:
                e.ignore()
                return
            self.cancelled.set()
            self.thread.join(15)
            self.progress_text.emit("")
        super().closeEvent(e)
