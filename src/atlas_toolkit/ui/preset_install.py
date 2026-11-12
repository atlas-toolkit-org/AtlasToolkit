"""
Loading preset files, one way everywhere: a preset bar's "Load presets from a file", Setup › Presets, files dropped
on the window and files the system opens with the app all come here.

The library first looks at what the files hold (core/presets.PresetLibrary.plan_install: an .atlaspack, an
.atlaspreset, or a .zip of either, such as a store download) without changing anything. One dialog then shows the
whole plan: what installs, what updates, what is already there or included in a higher tier, and anything to be
wary of (a changed pack, an unsigned one, a publisher the app does not know yet, which the person may choose to
trust). Nothing installs until they agree.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QFileDialog, QMessageBox

from ..core import presets as P

FILE_FILTER = (f"Presets and packs (*{P.EXT} *{P.PACK_EXT} *{P.ZIP_SUFFIX});;Preset files (*{P.EXT});;"
               f"Preset packs (*{P.PACK_EXT});;Zip files holding presets (*{P.ZIP_SUFFIX})")


def choose_files(parent, start: str = "") -> list[str]:
    paths, _ = QFileDialog.getOpenFileNames(parent, "Load presets or a preset pack", start, FILE_FILTER)
    return paths


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _question(plan: P.InstallPlan) -> str:
    packs = sum(1 for i in plan.installing if i.kind == "pack")
    presets = sum(len(i.presets) for i in plan.installing if i.kind == "presets")
    parts = ([f"install {_plural(packs, 'preset pack')}"] if packs else []) + (
        [f"add {_plural(presets, 'preset')} to My presets"] if presets else [])
    return (" and ".join(parts) + "?").capitalize()


def install_files(parent, library: P.PresetLibrary, paths, toast, registry=None, ask: bool = True
                  ) -> P.InstallPlan | None:
    """Show what `paths` hold, ask, and install; returns the plan carried out (None when nothing was installed)."""
    paths = [Path(p) for p in paths or []]
    if not paths:
        return None
    try:
        plan = library.plan_install(paths, registry)
    except OSError as e:
        toast(f"Could not read the file: {e}", "danger", ms=7000)
        return None
    if not plan.installing:
        lines = plan.summary() or ["Nothing in it loads."]
        bad = plan.errors or any(i.action == "blocked" for i in plan.items)
        toast(" ".join(lines), "danger" if bad else "info", ms=8000)
        return None
    trust: list = []
    if ask:
        box = QMessageBox(QMessageBox.Question, "Load presets", _question(plan), parent=parent)
        details = "\n".join(f"• {line}" for line in plan.summary())
        warnings = plan.warnings
        if warnings:
            box.setIcon(QMessageBox.Warning)
            details += "\n\nBefore you go on:\n" + "\n".join(f"• {w}" for w in warnings)
        untrusted = plan.untrusted
        check = None
        if untrusted:
            keys = "\n".join(f"• {s.publisher}: key {s.fingerprint}" for s in untrusted)
            details += ("\n\nThe app does not know who signed some of these. Their signature shows the pack is "
                        "unchanged since, but anyone can make a key under any name. Trust it only if the key "
                        f"matches the one on the publisher's own website:\n{keys}")
            names = ", ".join(s.publisher for s in untrusted)
            check = QCheckBox(f"Trust {names} from now on")
            box.setCheckBox(check)
        box.setInformativeText(details)
        go = box.addButton("Install anyway" if warnings else "Install", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel if warnings else go)
        box.exec()
        if box.clickedButton() is not go:
            return None
        if check is not None and check.isChecked():
            trust = untrusted
    try:
        library.install(plan, accept_warnings=True, trust=trust, registry=registry)
    except (OSError, P.PresetFormatError) as e:
        toast(f"Could not install: {e}", "danger", ms=7000)
        return None
    toast(_done_text(plan), "success", ms=6000)
    return plan


def _done_text(plan: P.InstallPlan) -> str:
    said = []
    for item in plan.items:
        if not item.done:
            continue
        if item.kind == "pack":
            verb = {"update": "Updated", "replace": "Replaced"}.get(item.action, "Installed")
            said.append(f"{verb} {item.pack.title} ({item.pack.status_label.lower()}, "
                        f"{_plural(len(item.pack.presets), 'preset')})")
        else:
            r = item.report
            text = f"Added {_plural(len(r.added), 'preset')} to My presets"
            if r.skipped:
                text += f" ({r.skipped} already there)"
            said.append(text)
    skipped = [i for i in plan.items if not i.done]
    if skipped:
        said.append(f"skipped {len(skipped)}: " + ", ".join(
            f"{i.pack.title if i.pack else i.source} ({P.ACTION_LABELS[i.action]})" for i in skipped))
    return "; ".join(said) + "."
