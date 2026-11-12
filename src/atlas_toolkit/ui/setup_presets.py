"""
Setup › Presets: every preset the app offers, and the preset packs they come from (core/presets.py).

    Preset packs   each installed pack (or one on a folder in ATLAS_TOOLKIT_PRESET_DIRS): tier, version, how many
                   presets, and whether it is Official (signed by a publisher key this app trusts, unchanged since).
                   Load presets or a pack, remove an installed pack, open the packs folder.
    Presets        a tree by tool group and tool: your own presets and each pack's, with what they are (Mine,
                   Official, Unsigned, Modified, Outdated, Hidden). Save your own to a file, copy a pack preset to
                   My presets, rename or delete yours, hide or show a pack's, open your presets folder.
                   Filters narrow the tree: tool group, type (Mine, Official, Trusted, Not verified), source (My
                   presets or one pack), state (Outdated, Hidden, Copied from a pack ...) and tag; search also reads
                   tips and tags. "Showing N of M" counts what the filters let through. Each choice says how
                   many presets it would list with the other filters as they are, and a choice that would list
                   none is greyed out, so a filter never empties the tree by surprise.

Pack presets are never deleted one by one (they belong to the pack): "Delete" hides them, after asking, and
"Show hidden" brings them back. Removing a whole pack asks first and says how to get it back. Every action takes
`ask` / `path` arguments so tests can run it without a dialog. Like the rest of Setup it shows no paths.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLineEdit, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from ..core import presets as P
from ..core.paths import reveal
from .about import _card, _text
from .theme import icon
from .widgets.common import hint
from .widgets.flow import FlowLayout

ROLE = Qt.UserRole                    # ("preset", tool id, uid) | ("tool", tool id) | ("pack", key)
NOT_INSTALLED = "Tools not installed"
ANY = ""                              # a filter's "everything" value

TYPES = {                             # Type filter: value -> (label, test on a preset)
    "mine": ("Mine", lambda p: p.mine),
    "official": ("Official", lambda p: not p.mine and p.pack.status == "official"),
    "trusted": ("Trusted publisher", lambda p: not p.mine and p.pack.status == "trusted"),
    "unverified": ("Not verified", lambda p: not p.mine and p.pack.status not in ("official", "trusted")),
}
STATES = {                            # State filter: value -> (label, tooltip); see PresetsPanel._row
    "outdated": ("Outdated", "Made for another version of its tool: some values no longer fit"),
    "hidden": ("Hidden", "Pack presets you hid (listed even with Show hidden off)"),
    "copied": ("Copied from a pack", "Your presets that started as a copy of a pack preset"),
    "tiers": ("In several tiers", "Pack presets that more than one installed tier of a collection holds"),
    "tip": ("Has notes", "Presets that carry a tip or description"),
}


FILTERS = ("group", "type", "source", "state", "tag")
BASE = Qt.UserRole + 1                # a filter choice's label without its count


@dataclass
class Row:
    """One preset as the filters see it: worked out once per refresh, then matched cheaply many times."""
    preset: P.Preset
    tool: object                      # None for a tool this app does not have
    group: str                        # the tool group, or NOT_INSTALLED
    source_title: str                 # the From column
    type: str                         # a TYPES key
    source: str                       # P.MINE or the pack's collection
    states: set = field(default_factory=set)
    tags: set = field(default_factory=set)
    hay: str = ""                     # what search reads, folded
    hidden: bool = False

    def has(self, key: str, value: str) -> bool:
        if key in ("state", "tag"):
            return value in (self.states if key == "state" else self.tags)
        return getattr(self, key) == value


class PresetsPanel(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.theme = ctx.theme
        self.library: P.PresetLibrary = ctx.presets
        self._outdated: dict = {}         # (tool id, preset uid, source) -> outdated?; emptied when presets change
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        lay.addWidget(self._packs_card())
        lay.addWidget(self._presets_card(), 1)
        self.refresh()
        self.library.subscribe(self._changed)

    # ------------------------------------------------------------------ building
    def _button(self, text: str, ic: str, fn, tip: str = "") -> QPushButton:
        b = QPushButton(text)
        b.setIcon(icon(ic, self.theme["text-2"], 14))
        b.setToolTip(tip)
        b.clicked.connect(lambda _=False: fn())
        return b

    def _tree(self, columns: list[str]) -> QTreeWidget:
        t = QTreeWidget()
        t.setColumnCount(len(columns))
        t.setHeaderLabels(columns)
        t.setRootIsDecorated(True)
        t.setUniformRowHeights(True)
        t.setAlternatingRowColors(False)
        head = t.header()
        head.setStretchLastSection(False)
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, len(columns)):
            head.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        return t

    def _packs_card(self) -> QWidget:
        card, cl = _card("Preset packs")
        cl.addWidget(hint("Packs add presets to every tool they cover, under the pack's name. A pack is one file "
                          "(.atlaspack, or a .zip holding it) that carries its signature and its publisher's key. "
                          "Official packs are signed by Atlas Toolkit's publisher and unchanged since; packs from "
                          "others can be trusted once you have checked their key. Owning two tiers of a collection "
                          "lists each preset once."))
        self.packs_view = self._tree(["Pack", "Version", "Presets", "Status"])
        self.packs_view.setRootIsDecorated(False)
        self.packs_view.setMinimumHeight(84)
        self.packs_view.setMaximumHeight(150)
        self.packs_view.itemSelectionChanged.connect(self._sync_buttons)
        cl.addWidget(self.packs_view)
        row = FlowLayout()                        # wraps in a narrow window instead of widening it
        row.addWidget(self._button("Load presets or a pack…", "folder", self.import_files,
                                   "An .atlaspreset file joins My presets; an .atlaspack (or a .zip holding "
                                   "packs) installs. You see what it holds before anything changes."))
        self.btn_remove_pack = self._button("Remove pack…", "trash", self.remove_pack,
                                            "Remove the selected pack and its presets (load its file again to get "
                                            "them back).")
        row.addWidget(self.btn_remove_pack)
        self.btn_trust = self._button("Trust publisher…", "check", self.toggle_trust_selected,
                                      "Trust packs signed with this publisher's key from now on, or stop.")
        row.addWidget(self.btn_trust)
        row.addWidget(self._button("Open packs folder", "folder", self.open_packs_folder))
        cl.addLayout(row)
        return card

    def _presets_card(self) -> QWidget:
        card, cl = _card("Presets")
        top = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Find a preset, tool, pack, tag or note")
        self.search.textChanged.connect(lambda _t: self.refresh())
        top.addWidget(self.search, 1)
        self.show_hidden = QCheckBox("Show hidden")
        self.show_hidden.toggled.connect(lambda _on: self.refresh())
        top.addWidget(self.show_hidden)
        cl.addLayout(top)
        cl.addLayout(self._filter_row())
        self.view = self._tree(["Preset", "From", "Notes"])
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setMinimumHeight(300)
        self.view.itemSelectionChanged.connect(self._selection_changed)
        cl.addWidget(self.view, 1)
        self.details = _text("", "hint", select=True)
        self.details.setTextFormat(Qt.RichText)
        cl.addWidget(self.details)
        row = FlowLayout()
        self.btn_export = self._button("Save to a file…", "save", self.export_selected,
                                       "Your own presets only; copy a pack preset to My presets first.")
        self.btn_copy = self._button("Copy to My presets", "copy", self.duplicate_selected)
        self.btn_rename = self._button("Rename…", "pen", self.rename_selected)
        self.btn_delete = self._button("Delete…", "trash", self.delete_selected,
                                       "Deletes your own presets; hides a pack's (Show hidden brings them back).")
        self.btn_hide = self._button("Hide", "eyeoff", self.toggle_hidden_selected)
        for b in (self.btn_export, self.btn_copy, self.btn_rename, self.btn_delete, self.btn_hide):
            row.addWidget(b)
        row.addWidget(self._button("Open my presets folder", "folder", self.open_mine_folder))
        cl.addLayout(row)
        return card

    # ------------------------------------------------------------------ contents
    def _combo(self, tip: str, items: list) -> QComboBox:
        c = QComboBox()
        c.setToolTip(tip)
        c.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        for value, text in items:
            c.addItem(text, value)
            c.setItemData(c.count() - 1, text, BASE)
        c.currentIndexChanged.connect(lambda _i: self.refresh())
        return c

    def _filter_row(self) -> FlowLayout:
        """Group, Type, Source, State and Tag drop-downs, Clear filters and a count: wraps in a narrow window."""
        row = FlowLayout(spacing=8)
        self.f_group = self._combo("Only presets of tools in this group", [(ANY, "All tool groups")])
        self.f_type = self._combo("Your own presets, or a pack's by how its signature checks out",
                                  [(ANY, "All types")] + [(k, label) for k, (label, _f) in TYPES.items()])
        self.f_source = self._combo("Only My presets, or only one pack's", [(ANY, "All sources")])
        self.f_state = self._combo("Presets in a particular state",
                                   [(ANY, "Any state")] + [(k, label) for k, (label, _t) in STATES.items()])
        for i, (_label, tip) in enumerate(STATES.values(), 1):
            self.f_state.setItemData(i, tip, Qt.ToolTipRole)
        self.f_tag = self._combo("Presets the author tagged with this word", [(ANY, "All tags")])
        self.filters = (self.f_group, self.f_type, self.f_source, self.f_state, self.f_tag)
        self.combos = dict(zip(FILTERS, self.filters))
        for c in self.filters:
            row.addWidget(c)
        self.btn_clear = QPushButton("Clear filters")
        self.btn_clear.setProperty("variant", "ghost")
        self.btn_clear.clicked.connect(self.clear_filters)
        row.addWidget(self.btn_clear)
        self.count = _text("", "hint", wrap=False)
        self.count.setMinimumHeight(self.btn_clear.sizeHint().height())
        row.addWidget(self.count)
        return row

    def clear_filters(self) -> None:
        for w in (*self.filters, self.search):
            w.blockSignals(True)
            w.setCurrentIndex(0) if isinstance(w, QComboBox) else w.clear()
            w.blockSignals(False)
        self.refresh()

    def set_filter(self, name: str, value: str) -> None:
        """Choose a filter's value by its data ("group", "type", "source", "state", "tag"), as a click would."""
        c = self.combos[name]
        i = c.findData(value)
        if i < 0:
            raise ValueError(f"no {name} filter {value!r}")
        c.setCurrentIndex(i)

    def choice(self, name: str, value: str) -> tuple[str, bool]:
        """A filter choice's text (with its count) and whether it can be picked."""
        c = self.combos[name]
        i = c.findData(value)
        return c.itemText(i), c.model().item(i).isEnabled()

    def _refill(self, c: QComboBox, first: str, items: list) -> None:
        """New choices for a filter whose choices come from what is installed; the chosen one stays if it can."""
        keep = c.currentData()
        c.blockSignals(True)
        c.clear()
        for value, text in [(ANY, first), *items]:
            c.addItem(text, value)
            c.setItemData(c.count() - 1, text, BASE)
        c.setCurrentIndex(max(0, c.findData(keep)))
        c.blockSignals(False)

    def filtering(self) -> bool:
        return any(c.currentData() for c in self.filters) or bool(self.search.text().strip())

    def _criteria(self) -> dict:
        return {k: c.currentData() or ANY for k, c in self.combos.items()}

    def _row(self, p: P.Preset, tool, group: str, source_title: str, hidden: set) -> Row:
        if p.mine:
            kind = "mine"
        else:
            kind = next(k for k, (_l, test) in TYPES.items() if k != "mine" and test(p))
        states = set()
        if tool is not None:
            key = (tool.id, p.uid, p.source)
            if key not in self._outdated:
                self._outdated[key] = bool(P.apply(tool, p)[1])
            if self._outdated[key]:
                states.add("outdated")
        if p.uid in hidden:
            states.add("hidden")
        if p.mine and p.based_on:
            states.add("copied")
        if not p.mine and len(self.library.tiers_of(p)) > 1:
            states.add("tiers")
        if p.tip.strip():
            states.add("tip")
        where = p.pack.name if p.pack else P.MINE_TITLE
        hay = f"{p.name} {tool.name if tool else p.tool} {where} {p.tip} {' '.join(p.tags)}".casefold()
        return Row(p, tool, group, source_title, kind, P.MINE if p.mine else p.pack.collection, states,
                   set(p.tags), hay, p.uid in hidden)

    def _rows(self) -> list[Row]:
        """Every preset the app knows, hidden ones too, in the tree's order."""
        hidden = self.library.hidden()
        reg = self.ctx.registry
        rows = [self._row(p, tool, category, g.title, hidden)
                for category, tools in reg.by_category() for tool in tools
                for g in self.library.groups(tool, include_hidden=True) for p in g.presets]
        rows += [self._row(p, None, NOT_INSTALLED, p.pack.title, hidden)
                 for pack in self.library.packs() for p in pack.presets if p.tool not in reg.tools]
        return rows

    def _passes(self, row: Row, crit: dict, query: str, skip: str = "") -> bool:
        if row.hidden and not (self.show_hidden.isChecked() or crit["state"] == "hidden"):
            return False
        if any(crit[k] and k != skip and not row.has(k, crit[k]) for k in FILTERS):
            return False
        return not query or query in row.hay

    def _fill_filters(self, rows: list[Row], crit: dict, query: str) -> None:
        """Choices from what is installed, each with how many presets it would list given the other filters."""
        packs = self.library.packs()
        names = {}
        for pack in packs:                    # one entry a collection: its tiers share one heading in the tree
            names.setdefault(pack.collection, pack.name)
        groups = [(c, c) for c, _t in self.ctx.registry.by_category()]
        if any(r.group == NOT_INSTALLED for r in rows):
            groups.append((NOT_INSTALLED, NOT_INSTALLED))
        self._refill(self.f_group, "All tool groups", groups)
        self._refill(self.f_source, "All sources",
                     [(P.MINE, P.MINE_TITLE)] + sorted(names.items(), key=lambda kv: kv[1].casefold()))
        tags = sorted({t for r in rows for t in r.tags}, key=str.casefold)
        self._refill(self.f_tag, "All tags", [(t, t) for t in tags])
        self.f_tag.setVisible(bool(tags) or bool(self.f_tag.currentData()))     # no tags anywhere: no Tag filter
        for key, c in self.combos.items():
            others = [r for r in rows if self._passes(r, crit, query, skip=key)]
            for i in range(c.count()):
                value = c.itemData(i)
                n = sum(1 for r in others if not value or r.has(key, value)) if key != "state" or value != "hidden" \
                    else sum(1 for r in rows if r.hidden and self._passes(r, {**crit, "state": value}, query))
                c.setItemText(i, f"{c.itemData(i, BASE)} ({n})")
                c.model().item(i).setEnabled(n > 0 or i == c.currentIndex() or not value)

    def _changed(self, _tool_id) -> None:
        self._outdated.clear()
        self.refresh()

    def _location(self, pack: P.Pack) -> str:
        if pack.path is not None and pack.path.parent == self.library.packs_dir:
            return "Installed"
        return "Read from a folder named in ATLAS_TOOLKIT_PRESET_DIRS"

    def refresh(self) -> None:
        selected = {it.data(0, ROLE) for it in self.view.selectedItems()}
        self._fill_packs()
        self._fill_presets(selected)
        self._selection_changed()

    def _fill_packs(self) -> None:
        keep = self._selected_pack_key()
        v = self.packs_view
        v.clear()
        for pack in self.library.packs():
            it = QTreeWidgetItem([pack.title, pack.pack_version, str(len(pack.presets)), pack.status_label])
            it.setData(0, ROLE, ("pack", pack.key))
            lines = [self._location(pack)]
            if pack.signer is not None:
                lines.append(f"Signed by {pack.signer.publisher}, key {pack.signer.fingerprint}")
            if pack.edition:
                lines.append(f"Edition {pack.edition}")
            if pack.licence:
                lines.append(f"Licence: {pack.licence}")
            lines += pack.problems
            it.setToolTip(0, "\n".join(lines))
            it.setToolTip(3, "\n".join(pack.problems) or lines[0])
            v.addTopLevelItem(it)
            if pack.key == keep:
                it.setSelected(True)
        for path, error in self.library.broken:
            it = QTreeWidgetItem([path.name, "", "", "Unreadable"])
            it.setToolTip(0, error)
            it.setData(0, ROLE, ("broken", path.name))
            v.addTopLevelItem(it)
        if not v.topLevelItemCount():
            it = QTreeWidgetItem(["No preset packs yet", "", "", ""])
            it.setFlags(Qt.NoItemFlags)
            v.addTopLevelItem(it)
        self._sync_buttons()

    def _notes(self, row: Row) -> str:
        p = row.preset
        notes = ["Mine"] if p.mine else [p.pack.status_label]
        if "tiers" in row.states:
            notes.append(" · ".join(self.library.tiers_of(p)))
        if "outdated" in row.states:
            notes.append("Outdated")
        if row.hidden:
            notes.append("Hidden")
        return ", ".join(notes)

    def _fill_presets(self, selected: set) -> None:
        v = self.view
        query = self.search.text().strip().casefold()
        crit = self._criteria()
        rows = self._rows()
        self._fill_filters(rows, crit, query)
        crit = self._criteria()                       # a refilled choice may have gone
        see_hidden = self.show_hidden.isChecked() or crit["state"] == "hidden"
        universe = [r for r in rows if see_hidden or not r.hidden]          # what "of N" counts
        shown = [r for r in rows if self._passes(r, crit, query)]
        v.setUpdatesEnabled(False)
        v.clear()
        groups: dict = {}                             # group -> item; tool id -> item, in the rows' order
        for r in shown:
            if r.group not in groups:
                groups[r.group] = QTreeWidgetItem([r.group, "", ""])
                groups[r.group].setFlags(Qt.ItemIsEnabled)
                if r.group == NOT_INSTALLED:
                    groups[r.group].setToolTip(0, "Presets for tools this version of the app does not have. "
                                                  "They stay in their pack.")
                v.addTopLevelItem(groups[r.group])
            if r.tool is None:
                it = QTreeWidgetItem([f"{r.preset.name} ({r.preset.tool})", r.source_title, "Tool not installed"])
                it.setFlags(Qt.ItemIsEnabled)
                groups[r.group].addChild(it)
                continue
            tool_item = groups.get(r.tool.id)
            if tool_item is None:
                tool_item = groups[r.tool.id] = QTreeWidgetItem([r.tool.name, "", ""])
                tool_item.setData(0, ROLE, ("tool", r.tool.id))
                tool_item.setIcon(0, icon(r.tool.icon, self.theme["text-3"], 14))
                groups[r.group].addChild(tool_item)
            it = QTreeWidgetItem([r.preset.name, r.source_title, self._notes(r)])
            data = ("preset", r.tool.id, r.preset.uid)
            it.setData(0, ROLE, data)
            if r.preset.tip:
                it.setToolTip(0, r.preset.tip)
            tool_item.addChild(it)
            tool_item.setText(2, str(tool_item.childCount()))
            if data in selected:
                it.setSelected(True)
        filtering = self.filtering()
        self.count.setText(f"Showing {len(shown)} of {len(universe)}" if filtering else f"{len(universe)} presets")
        self.btn_clear.setEnabled(filtering)
        if filtering and not shown:
            blame = [self.combos[k].itemData(self.combos[k].currentIndex(), BASE) for k in FILTERS if crit[k]]
            if query:
                blame.append(f'"{self.search.text().strip()}"')
            empty = QTreeWidgetItem([f"Nothing matches {' + '.join(blame)}. Clear filters to see all "
                                     f"{len(universe)}.", "", ""])
            empty.setFlags(Qt.NoItemFlags)
            v.addTopLevelItem(empty)
            v.setFirstColumnSpanned(0, v.rootIndex(), True)
        v.expandAll() if filtering else v.expandToDepth(0)
        for it in v.selectedItems():
            parent = it.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
        v.setUpdatesEnabled(True)

    # ------------------------------------------------------------------ selection
    def _selected_pack_key(self) -> str | None:
        items = self.packs_view.selectedItems() if hasattr(self, "packs_view") else []
        data = items[0].data(0, ROLE) if items else None
        return data[1] if data and data[0] == "pack" else None

    def selected(self) -> list[tuple]:
        """(tool, preset) for each selected preset row."""
        out = []
        for it in self.view.selectedItems():
            data = it.data(0, ROLE)
            if not data or data[0] != "preset":
                continue
            tool = self.ctx.registry.tools.get(data[1])
            preset = self.library.get(tool, data[2]) if tool else None
            if preset is None and tool is not None:
                preset = next((p for g in self.library.groups(tool, include_hidden=True) for p in g.presets
                               if p.uid == data[2]), None)
            if preset is not None:
                out.append((tool, preset))
        return out

    def select(self, tool_id: str, uid: str | None = None) -> None:
        """Select a tool's row (or one of its presets) and show it, e.g. from a tool page's Manage presets…."""
        self.view.clearSelection()
        for i in range(self.view.topLevelItemCount()):
            cat = self.view.topLevelItem(i)
            for j in range(cat.childCount()):
                tool_item = cat.child(j)
                if tool_item.data(0, ROLE) != ("tool", tool_id):
                    continue
                target = tool_item
                for k in range(tool_item.childCount()):
                    if uid and tool_item.child(k).data(0, ROLE) == ("preset", tool_id, uid):
                        target = tool_item.child(k)
                cat.setExpanded(True)
                tool_item.setExpanded(True)
                target.setSelected(True)
                self.view.scrollToItem(target)
                return

    def _selection_changed(self) -> None:
        sel = self.selected()
        hidden = self.library.hidden()
        if len(sel) == 1:
            tool, p = sel[0]
            lines = [f"<b>{p.name}</b> · {tool.name}"]
            if p.mine:
                lines.append("Your preset." + (f" Copied from {p.based_on.split('/')[0]}." if p.based_on else ""))
            else:
                tiers = self.library.tiers_of(p)
                lines.append(f"From {p.pack.name}" + (f" ({' · '.join(tiers)})" if tiers else "")
                             + f", {p.pack.status_label.lower()}.")
            if p.tip:
                lines.append(p.tip)
            problems = P.apply(tool, p)[1]
            if problems:
                lines.append("Made for another version: " + "; ".join(problems) + ".")
            if p.explicit and len(p.explicit) < len(p.values):
                labels = {q.key: q.label for q in tool.param_list}
                lines.append("Sets: " + ", ".join(labels.get(k, k) for k in p.explicit))
            self.details.setText("<br>".join(lines))
        elif sel:
            self.details.setText(f"{len(sel)} presets selected.")
        else:
            self.details.setText("Select presets to save, copy, rename, delete or hide them.")
        mine = [p for _t, p in sel if p.mine]
        packed = [p for _t, p in sel if not p.mine]
        self.btn_export.setEnabled(bool(mine))
        self.btn_copy.setEnabled(bool(packed))
        self.btn_rename.setEnabled(len(sel) == 1 and bool(mine))
        self.btn_delete.setEnabled(bool(sel))
        self.btn_hide.setEnabled(bool(packed))
        self.btn_hide.setText("Show" if packed and all(p.uid in hidden for p in packed) else "Hide")
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        key = self._selected_pack_key()
        pack = self.library.installed(key) if key else None
        self.btn_remove_pack.setEnabled(pack is not None and self._location(pack) == "Installed")
        signer = pack.signer if pack is not None and pack.status in ("signed", "trusted") else None
        self.btn_trust.setEnabled(signer is not None)
        self.btn_trust.setText("Stop trusting publisher…" if pack is not None and pack.status == "trusted"
                               else "Trust publisher…")

    # ------------------------------------------------------------------ actions
    def _confirm(self, text: str, ask: bool) -> bool:
        return not ask or QMessageBox.question(self, "Presets", text) == QMessageBox.Yes

    def import_files(self, paths: list | None = None, ask: bool = True) -> int:
        """Load .atlaspreset, .atlaspack or .zip files (ui/preset_install.py shows what they hold first); returns
        how many packs and preset files were installed."""
        from .preset_install import choose_files, install_files
        if paths is None:
            paths = choose_files(self)
        plan = install_files(self, self.library, paths, self.ctx.toast, registry=self.ctx.registry, ask=ask)
        return sum(1 for i in plan.items if i.done) if plan else 0

    def toggle_trust_selected(self, ask: bool = True) -> bool:
        """Trust the selected pack's signer (its packs then show as from a trusted publisher), or stop trusting it.
        Official packs need nothing: the app knows its own publisher's keys."""
        key = self._selected_pack_key()
        pack = self.library.installed(key) if key else None
        if pack is None or pack.signer is None or pack.status not in ("signed", "trusted"):
            return False
        s = pack.signer
        if pack.status == "trusted":
            if not self._confirm(f"Stop trusting {s.publisher} (key {s.fingerprint})? Its packs stay installed.", ask):
                return False
            self.library.untrust(s.fingerprint)
            self.ctx.toast(f"{s.publisher} is no longer trusted.", "success")
            return True
        if not self._confirm(f"Trust packs signed by {s.publisher} with key {s.fingerprint}?\n\nOnly do this when "
                             "the key matches the one the publisher shows on their own website: anyone can make a "
                             "key and call themselves anything.", ask):
            return False
        self.library.trust(s)
        self.ctx.toast(f"Packs signed by {s.publisher} are trusted now.", "success")
        return True

    def remove_pack(self, key: str | None = None, ask: bool = True) -> bool:
        key = key or self._selected_pack_key()
        pack = self.library.installed(key) if key else None
        if pack is None:
            return False
        if self._location(pack) != "Installed":
            self.ctx.toast("This pack is read from a folder, not installed: take that folder out of "
                           "ATLAS_TOOLKIT_PRESET_DIRS instead.", "warning", ms=6000)
            return False
        if not self._confirm(f"Remove {pack.title}? Its {len(pack.presets)} presets leave every tool. To get them "
                             "back, load the pack file again.", ask):
            return False
        self.library.remove_pack(pack)
        self.ctx.toast(f"Removed {pack.title}.", "success")
        return True

    def export_selected(self, path: str | None = None) -> Path | None:
        sel = self.selected()
        mine = [p for _t, p in sel if p.mine]
        if len(mine) != len(sel):
            self.ctx.toast("Only your own presets can be saved to a file: copy a pack preset to My presets first.",
                           "warning", ms=6000)
        if not mine:
            return None
        if path is None:
            name = P.slug(mine[0].name if len(mine) == 1 else "my presets") + P.EXT
            path, _ = QFileDialog.getSaveFileName(self, "Save presets to a file", str(Path.home() / name),
                                                  f"Presets (*{P.EXT})")
            if not path:
                return None
        try:
            out = self.library.export(Path(path), mine)
        except OSError as e:
            self.ctx.toast(f"Could not save the file: {e}", "danger")
            return None
        self.ctx.toast(f"Saved {len(mine)} preset{'s' if len(mine) != 1 else ''} to {out.name}.", "success",
                       "Show", lambda: reveal(out))
        return out

    def duplicate_selected(self) -> list:
        copies = [self.library.duplicate(tool, p) for tool, p in self.selected() if not p.mine]
        if copies:
            self.ctx.toast(f"Copied {len(copies)} preset{'s' if len(copies) != 1 else ''} to My presets.", "success")
        return copies

    def rename_selected(self, name: str | None = None) -> P.Preset | None:
        sel = [(t, p) for t, p in self.selected() if p.mine]
        if len(sel) != 1:
            return None
        tool, preset = sel[0]
        if name is None:
            name, ok = QInputDialog.getText(self, "Rename preset", "New name:", text=preset.name)
            if not ok:
                return None
        name = (name or "").strip()
        if not name or name == preset.name:
            return None
        return self.library.rename_mine(tool, preset.uid, name)

    def delete_selected(self, ask: bool = True) -> int:
        """Delete the selected presets of yours; hide the selected pack presets (both after asking)."""
        sel = self.selected()
        mine = [(t, p) for t, p in sel if p.mine]
        packed = [(t, p) for t, p in sel if not p.mine]
        done = 0
        if mine and self._confirm(
                f"Delete {len(mine)} preset{'s' if len(mine) != 1 else ''} of yours"
                + (f" (“{mine[0][1].name}”)" if len(mine) == 1 else "") + "? This cannot be undone.", ask):
            for tool, p in mine:
                done += bool(self.library.delete_mine(tool, p.uid))
        if packed and self._confirm(
                f"{len(packed)} of these come from a preset pack, which keeps them. Hide them from the drop-downs? "
                "Show hidden brings them back.", ask):
            for _tool, p in packed:
                self.library.set_hidden(p.uid, True)
                done += 1
        return done

    def toggle_hidden_selected(self) -> int:
        packed = [p for _t, p in self.selected() if not p.mine]
        hidden = self.library.hidden()
        hide = not all(p.uid in hidden for p in packed)
        for p in packed:
            self.library.set_hidden(p.uid, hide)
        return len(packed)

    def open_packs_folder(self) -> None:
        self.library.packs_dir.mkdir(parents=True, exist_ok=True)
        reveal(self.library.packs_dir)

    def open_mine_folder(self) -> None:
        self.library.mine.folder.mkdir(parents=True, exist_ok=True)
        reveal(self.library.mine.folder)
