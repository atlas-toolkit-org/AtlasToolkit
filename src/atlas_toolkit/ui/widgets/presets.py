"""
Presets for a tool's parameter form: a drop-down of the presets the library offers for the tool (your own under
"My presets", then one heading per preset pack), a hint under it, and three icons beside the heading: save the
current settings as a preset of yours, more preset actions, and reset to the defaults.

The drop-down always opens on "Custom settings": nothing is selected until a preset is picked in this session, and
the choice is not remembered between sessions (the values are). A picked preset stays shown while the values still
match it and goes back to "Custom settings" once a setting is changed. The menu saves presets to a file, loads
.atlaspreset and .atlaspack files, copies a pack preset into My presets, and deletes one of yours. A tool page and a
workflow step share the library, so a preset saved in one is offered in the other. View keys (`preset_skip`, a
camera), second input files and the input settings a preset does not set stay as they are (core/presets.apply).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QMenu, QMessageBox, QSizePolicy,
                               QVBoxLayout, QWidget)

from .common import apply_icon, hint, icon_button, label
from ...core import presets as P

PLACEHOLDER = "Custom settings"


class PresetBar(QWidget):
    applied = Signal()          # a preset or the defaults were set on the form

    def __init__(self, tool, library, form, theme, toast=None, extra: list | None = None, manage=None):
        """`library` is the app's PresetLibrary (ctx.presets); `extra` adds (text, callback) actions to the menu;
        `manage(tool_id, uid)` opens Setup › Presets."""
        super().__init__()
        self.manage = manage
        self.tool, self.library, self.form, self.theme = tool, library, form, theme
        self.toast = toast or (lambda *a, **k: None)
        self.extra = list(extra or [])
        self.chosen: str | None = None          # uid picked in this session, while the values still match it
        self.last_dir = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        ph = QHBoxLayout()
        ph.setSpacing(2)
        ph.addWidget(label("presets"))
        ph.addStretch(1)
        self.btn_save = icon_button("save", "Save the current settings as a preset of mine", theme, size=16)
        self.btn_save.clicked.connect(lambda: self.save_preset())
        self.btn_more = icon_button("menu", "More: save presets to a file, load a preset file or pack, delete", theme,
                                    size=16)
        self.btn_more.clicked.connect(self.show_menu)
        self.btn_reset = icon_button("reset", "Reset every setting to the tool's defaults", theme, size=16)
        self.btn_reset.clicked.connect(self.reset)
        for b in (self.btn_save, self.btn_more, self.btn_reset):
            ph.addWidget(b)
        lay.addLayout(ph)
        self.combo = QComboBox(objectName="presetCombo")
        self.combo.setPlaceholderText(PLACEHOLDER)
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(12)
        self.combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)   # never widens the panel
        self.combo.setMaxVisibleItems(22)
        self.combo.activated.connect(self._picked)
        lay.addWidget(self.combo)
        self.hint = hint("")
        lay.addWidget(self.hint)
        self._presets: dict[str, P.Preset] = {}
        self._fill()
        self._show(None)
        library.subscribe(self._library_changed)

    # ------------------------------------------------------------------ the drop-down
    def _heading(self, text: str) -> None:
        c = self.combo
        c.addItem(text)
        model = c.model()
        if isinstance(model, QStandardItemModel):            # a heading, not a choice
            model.item(c.count() - 1).setEnabled(False)

    def _fill(self) -> None:
        """Your presets under "My presets", then each pack's under its name; each preset's description as tooltip."""
        c = self.combo
        c.blockSignals(True)
        c.clear()
        self._presets = {}
        for group in self.library.groups(self.tool):
            if c.count():
                c.insertSeparator(c.count())
            self._heading(group.title)
            for p in group.presets:
                c.addItem(f"  {p.name}", p.uid)
                self._presets[p.uid] = p
                if p.tip:
                    c.setItemData(c.count() - 1, p.tip, Qt.ToolTipRole)
        c.setToolTip("" if self._presets else "No presets yet: save your own with the save icon, or load a preset "
                                              "file from the menu beside it.")
        c.blockSignals(False)
        view = c.view()                                     # the open list shows the longest name in full
        if c.count():
            view.setMinimumWidth(min(520, view.sizeHintForColumn(0) + 36))

    def _library_changed(self, tool_id) -> None:
        if tool_id in (None, self.tool.id):
            self._fill()
            self.sync()

    def _picked(self, index: int) -> None:
        uid = self.combo.itemData(index)
        if not uid:
            self._show(self.chosen)
            return
        self.apply(uid)

    def _show(self, uid: str | None) -> None:
        idx = self.combo.findData(uid) if uid else -1
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(idx)                      # -1: the "Custom settings" placeholder
        self.combo.blockSignals(False)

    # ------------------------------------------------------------------ presets
    def preset(self, ref: str) -> P.Preset | None:
        """A preset offered here, by uid or name (yours first)."""
        if ref in self._presets:
            return self._presets[ref]
        hits = self.library.find(self.tool, ref)
        return next((p for p in hits if p.mine), hits[0] if hits else None)

    def apply(self, ref: str) -> None:
        preset = self.preset(ref)
        if preset is None:
            self.toast(f"No preset “{ref}” for {self.tool.name}.", "warning")
            self._show(self.chosen)
            return
        values, problems = P.apply(self.tool, preset, self.form.values())
        if problems:
            self.toast(f"“{preset.name}” was made for another version of {self.tool.name}: "
                       f"{'; '.join(problems)}.", "warning", ms=8000)
        self.chosen = preset.uid
        self.form.set_values(values)
        self.applied.emit()
        self.sync()

    def reset(self) -> None:
        self.chosen = None
        self.form.set_values(self.tool.defaults())
        self.applied.emit()
        self.sync()

    def matching(self) -> P.Preset | None:
        return P.match(self.tool, self.form.values(), self._presets.values())

    def sync(self) -> None:
        """Keep a preset picked in this session shown while the values still match it; otherwise the placeholder."""
        chosen = self._presets.get(self.chosen) if self.chosen else None
        if chosen is None or not P.matches(self.tool, self.form.values(), chosen):
            self.chosen, chosen = None, None                 # a setting changed: custom settings again
        self._show(self.chosen)
        if chosen is not None:
            if chosen.mine:
                self.hint.setText(f"Your preset <b>{chosen.name}</b>.")
            else:
                where = (f" From {chosen.pack.title}." if chosen.pack is not None else "")
                self.hint.setText((chosen.tip or f"Preset <b>{chosen.name}</b>.") + where)
        elif not self._presets:
            self.hint.setText("No presets yet: save your own settings with the save icon, or load a preset file "
                              "from the menu.")
        else:
            self.hint.setText("Pick a preset to start from it.")

    def current_name(self) -> str:
        found = self.matching()
        return found.name if found else ("Custom" if self._presets else "")

    def _chosen_preset(self) -> P.Preset | None:
        return self._presets.get(self.chosen) if self.chosen else None

    def _mine(self) -> list[P.Preset]:
        return [p for p in self._presets.values() if p.mine]

    # ------------------------------------------------------------------ the menu
    def show_menu(self) -> None:
        menu = self.build_menu()
        menu.exec(self.btn_more.mapToGlobal(self.btn_more.rect().bottomLeft()))

    def build_menu(self) -> QMenu:
        menu = QMenu(self)
        chosen = self._chosen_preset()
        mine = self._mine()
        menu.addAction("Save these settings to a file…", lambda: self.save_to_file())
        a = menu.addAction("Save my presets to a file…", lambda: self.save_mine_to_file())
        a.setEnabled(bool(mine))
        menu.addAction("Load presets from a file…", lambda: self.load_from_file())
        menu.addSeparator()
        a = menu.addAction("Copy this preset to My presets", lambda: self.duplicate())
        a.setEnabled(chosen is not None and not chosen.mine)
        a = menu.addAction("Delete a preset of mine…", lambda: self.ask_delete())
        a.setEnabled(bool(mine))
        if self.manage is not None:
            menu.addSeparator()
            menu.addAction("Manage presets and packs…", lambda: self.manage(self.tool.id, self.chosen))
        if self.extra:
            menu.addSeparator()
            for text, fn in self.extra:
                menu.addAction(text, fn)
        return menu

    def _confirm(self, title: str, text: str, ask: bool) -> bool:
        return not ask or QMessageBox.question(self, title, text) == QMessageBox.Yes

    def save_preset(self, name: str | None = None, ask: bool = True) -> P.Preset | None:
        """Save the current settings as a preset of yours (asks for a name; asks before replacing one)."""
        if name is None:
            name, ok = QInputDialog.getText(self, "Save preset", "Name for these settings:")
            if not ok:
                return None
        name = (name or "").strip()
        if not name:
            return None
        taken = next((p for p in self._mine() if p.name.casefold() == name.casefold()), None)
        if taken is not None and not self._confirm("Save preset", f"Replace your preset “{taken.name}”?", ask):
            return None
        try:
            preset = self.library.save_mine(self.tool, name, self.form.values())
        except OSError as e:
            self.toast(f"Could not save the preset: {e}", "danger")
            return None
        self.chosen = preset.uid
        self._fill()
        self.sync()
        self.toast(f"Saved preset “{name}”.", "success")
        return preset

    def duplicate(self) -> P.Preset | None:
        """Copy the chosen pack preset into My presets, to change it or save it to a file."""
        chosen = self._chosen_preset()
        if chosen is None or chosen.mine:
            return None
        copy = self.library.duplicate(self.tool, chosen)
        self.chosen = copy.uid
        self._fill()
        self.sync()
        self.toast(f"Copied “{chosen.name}” to My presets as “{copy.name}”.", "success")
        return copy

    def ask_delete(self) -> None:
        mine = self._mine()
        if not mine:
            return
        names = [p.name for p in mine]
        name, ok = QInputDialog.getItem(self, "Delete preset", "Delete which of your presets?", names, 0, False)
        if ok and name:
            self.delete_preset(mine[names.index(name)].uid)

    def delete_preset(self, ref: str, ask: bool = True) -> bool:
        preset = self.preset(ref)
        if preset is None or not preset.mine:
            return False
        if not self._confirm("Delete preset", f"Delete your preset “{preset.name}”? This cannot be undone.", ask):
            return False
        self.library.delete_mine(self.tool, preset.uid)
        if self.chosen == preset.uid:
            self.chosen = None
        self._fill()
        self.sync()
        return True

    # ------------------------------------------------------------------ files
    def _save_path(self, title: str, suggested: str) -> str:
        start = str(Path(self.last_dir or Path.home()) / suggested)
        path, _ = QFileDialog.getSaveFileName(self, title, start, f"Presets (*{P.EXT})")
        return path

    def _remember(self, path) -> None:
        self.last_dir = str(Path(path).parent)

    def save_to_file(self, path: str | None = None, name: str | None = None) -> Path | None:
        """Write the current settings as a one-preset file (named after the file), without adding it to My presets.
        Pack presets are not written out: copy one to My presets first."""
        chosen = self._chosen_preset()
        if chosen is not None and not chosen.mine:
            self.toast("Presets from a pack stay in the pack. Copy it to My presets first (menu), then save that.",
                       "warning", ms=6000)
            return None
        default = chosen.name if chosen is not None else f"{self.tool.name} settings"
        path = path or self._save_path("Save settings to a file", P.slug(default) + P.EXT)
        if not path:
            return None
        name = name or (chosen.name if chosen is not None else Path(path).stem)
        preset = P.Preset(name=name, tool=self.tool.id, values=P.snapshot(self.tool, self.form.values()),
                          tool_version=self.tool.version, created=P._now(), modified=P._now(),
                          uid=chosen.uid if chosen is not None else "")
        preset.explicit = tuple(preset.values)
        try:
            out = P.write_preset_file(Path(path), [preset])
        except OSError as e:
            self.toast(f"Could not save the file: {e}", "danger")
            return None
        self._remember(out)
        self.toast(f"Saved “{name}” to {out.name}.", "success")
        return out

    def save_mine_to_file(self, path: str | None = None) -> Path | None:
        """Write all your presets for this tool into one file."""
        mine = self._mine()
        if not mine:
            return None
        path = path or self._save_path("Save my presets to a file", P.slug(f"{self.tool.name} presets") + P.EXT)
        if not path:
            return None
        try:
            out = self.library.export(Path(path), mine)
        except OSError as e:
            self.toast(f"Could not save the file: {e}", "danger")
            return None
        self._remember(out)
        self.toast(f"Saved {len(mine)} preset{'s' if len(mine) != 1 else ''} to {out.name}.", "success")
        return out

    def load_from_file(self, path: str | None = None, ask: bool = True):
        """Load an .atlaspreset into My presets, or install a pack (an .atlaspack, or a .zip holding packs and
        preset files), after showing what it holds (ui/preset_install.py); returns what was added, or None."""
        from ..preset_install import choose_files, install_files
        paths = [path] if path else choose_files(self, self.last_dir)
        if not paths:
            return None
        plan = install_files(self, self.library, paths, self.toast, ask=ask)
        self._remember(Path(paths[0]))
        self._fill()
        self.sync()
        return plan.report() if plan else None

    # ------------------------------------------------------------------ state
    def set_enabled(self, on: bool) -> None:
        for w in (self.btn_save, self.btn_more, self.btn_reset, self.combo):
            w.setEnabled(on)

    def retheme(self, theme) -> None:
        self.theme = theme
        for b in (self.btn_save, self.btn_more, self.btn_reset):
            apply_icon(b, theme)
