"""
The preset system (src/atlas_toolkit/core/presets.py) on a made-up tool and a made-up two-tier pack
(tests/fixtures/presets): file formats, the user's own store, packs, the library, and how presets survive a
schema that changed since they were saved.
"""
import _isolate  # noqa: F401
import json
import os
import shutil
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path
from types import SimpleNamespace

from atlas_toolkit.core import presets as P
from atlas_toolkit.core.params import Bool, Choice, FileRef, Float, ParamError, resolve, resolve_lenient
from atlas_toolkit.core.registry import ToolTable
from atlas_toolkit.core.tool import Inputs, Output, Tool

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "presets"


def demo_tool(**kw) -> Tool:
    params = [Float("width", "Width", 4.0, min=0, max=20), Choice("mode", "Mode", "a", ["a", "b"]),
              Bool("flag", "Flag", False), Choice("source", "Source", "image", ["image", "file"]),
              FileRef("shape", "Shape", ""), Float("cam", "Camera", 30.0, min=0, max=90)]
    spec = dict(id="demo", name="Demo", version="1.2", category="svg", summary="A tool for tests.", inputs=Inputs(),
                output=Output("{stem}--demo.svg"), engine=".x:run", params=params, preset_skip=("cam",),
                preset_inputs=("source",))
    spec.update(kw)
    return Tool(**spec)


def fake_registry(*tools):
    table = ToolTable()
    for t in tools:
        table[t.id] = t
        table.aliases.update({a: t.id for a in t.aliases})
    return SimpleNamespace(tools=table)


class Versions(unittest.TestCase):
    def test_padded_and_ordered(self):
        self.assertEqual(P.version_tuple("1.1"), P.version_tuple("1.1.0"))
        self.assertTrue(P.older("1.9", "1.10"))
        self.assertTrue(P.older("", "0.1"))
        self.assertFalse(P.older("2.0.0", "2"))

    def test_slugs(self):
        self.assertEqual(P.slug("Möbius Wreath"), "mobius-wreath")
        self.assertEqual(P.slug("2 × 2"), "2-x-2")
        self.assertEqual(P.slug("Bird's Nest"), "bird-s-nest")
        self.assertEqual(P.slug("Pen & ink"), "pen-ink")


class SchemaChanges(unittest.TestCase):
    def test_renamed_keys_and_values_still_load(self):
        mode = Choice("mode", "Mode", "a", ["a", "b"], former=("style",), former_values={"old": "b"})
        self.assertEqual(resolve([mode], {"style": "old"})["mode"], "b")
        self.assertEqual(resolve([mode], {"mode": "a", "style": "b"})["mode"], "a")    # the new key wins

    def test_lenient_falls_back_and_reports(self):
        params = [Float("w", "W", 1.0, min=0, max=5), Choice("m", "M", "a", ["a", "b"])]
        with self.assertRaises(ParamError):
            resolve(params, {"m": "z"})
        values, problems = resolve_lenient(params, {"w": 3, "m": "z", "gone": 1})
        self.assertEqual(values, {"w": 3.0, "m": "a"})
        self.assertEqual(len(problems), 2)
        self.assertIn("gone", problems[1])

    def test_apply_tolerates_an_older_preset(self):
        tool = demo_tool()
        preset = P.Preset("Old", "demo", {"width": 5, "mode": "gone-value", "removed": 1})
        values, problems = P.apply(tool, preset)
        self.assertEqual((values["width"], values["mode"]), (5.0, "a"))
        self.assertEqual(len(problems), 2)

    def test_migrate_hook_sees_the_writing_version(self):
        seen = []

        def migrate(values, from_version):
            seen.append(from_version)
            if P.older(from_version, "1.2"):
                values["width"] = values.pop("breadth")
            return values
        tool = demo_tool(migrate=".hook:migrate")
        tool.resolve = lambda ref: migrate
        values, problems = P.apply(tool, P.Preset("Old", "demo", {"breadth": 7}, tool_version="1.0"))
        self.assertEqual((values["width"], problems, seen), (7.0, [], ["1.0"]))


class ApplyAndMatch(unittest.TestCase):
    def setUp(self):
        self.tool = demo_tool()

    def test_snapshot_leaves_out_view_state_and_files(self):
        values = dict(self.tool.defaults(), shape="shapes/outline.svg")
        snap = P.snapshot(self.tool, values)
        self.assertNotIn("cam", snap)
        self.assertNotIn("shape", snap)
        self.assertIn("source", snap)
        self.assertNotIn("source", P.snapshot(self.tool, values, inputs_too=False))
        self.assertIn("source", P.snapshot(self.tool, values, inputs_too=False, explicit=["source"]))

    def test_apply_keeps_what_presets_never_change(self):
        current = dict(self.tool.defaults(), cam=60.0, shape="x.svg", source="file")
        values, _ = P.apply(self.tool, P.Preset("Wide", "demo", {"width": 9.0}), current)
        self.assertEqual((values["width"], values["cam"], values["shape"], values["source"]), (9.0, 60.0, "x.svg", "file"))
        values, _ = P.apply(self.tool, P.Preset("Sets source", "demo", {"source": "image"}), current)
        self.assertEqual(values["source"], "image")            # a preset that sets an input setting does change it
        cli, _ = P.apply(self.tool, P.Preset("Wide", "demo", {"width": 9.0}))
        self.assertEqual(cli["cam"], 30.0)                      # without current values: the defaults

    def test_match(self):
        wide = P.Preset("Wide", "demo", {"width": 9.0, "mode": "b"})
        narrow = P.Preset("Narrow", "demo", {"width": 1.0})
        values = dict(P.apply(self.tool, wide)[0], cam=80.0)    # the camera does not count
        self.assertIs(P.match(self.tool, values, [narrow, wide]), wide)
        values["flag"] = True
        self.assertIsNone(P.match(self.tool, values, [narrow, wide]))


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=_isolate.ROOT))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_round_trip_keeps_unknown_fields(self):
        p = P.Preset("Mine", "demo", {"width": 3.0}, tip="hi", extra={"future": {"x": 1}})
        path = P.write_preset_file(self.dir / "out", [p])
        self.assertEqual(path.suffix, P.EXT)
        back = P.read_preset_file(path)[0]
        self.assertEqual((back.uid, back.name, back.values, back.tip, back.extra), (p.uid, "Mine", {"width": 3.0}, "hi",
                                                                                     {"future": {"x": 1}}))

    def test_newer_or_foreign_files_are_refused(self):
        path = self.dir / "new.atlaspreset"
        path.write_text(json.dumps({"format": P.FORMAT, "format_version": P.FORMAT_VERSION + 1, "presets": []}),
                        encoding="utf-8")
        with self.assertRaisesRegex(P.PresetFormatError, "newer"):
            P.read_preset_file(path)
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(P.PresetFormatError):
            P.read_preset_file(path)


class Mine(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=_isolate.ROOT))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.tool = demo_tool()
        self.store = P.MineStore(self.dir)

    def test_put_replaces_by_name(self):
        a = self.store.put(self.tool, "Look", dict(self.tool.defaults(), width=5.0))
        b = self.store.put(self.tool, "look", dict(self.tool.defaults(), width=6.0))
        stored = self.store.load(self.tool)
        self.assertEqual((len(stored), a.uid, stored[0].values["width"]), (1, b.uid, 6.0))

    def test_add_renames_clashes_and_skips_copies(self):
        self.store.put(self.tool, "Look", self.tool.defaults())
        same = P.Preset("Look", "demo", dict(self.store.load(self.tool)[0].values))
        other = P.Preset("Look", "demo", {"width": 9.0})
        added = self.store.add(self.tool, [same, other])
        self.assertEqual([(o, p.name) for o, p in added], [("Look", "Look (2)")])

    def test_two_windows_do_not_lose_each_others_saves(self):
        other = P.MineStore(self.dir)
        self.store.put(self.tool, "One", self.tool.defaults())
        other.put(self.tool, "Two", self.tool.defaults())
        self.assertEqual(self.store.names(self.tool), ["One", "Two"])

    def test_legacy_adoption_is_idempotent(self):
        legacy = {"Old look": {"width": 3.0, "mode": "b"}}
        self.assertEqual(self.store.adopt_legacy(self.tool, legacy), 1)
        self.assertEqual(self.store.adopt_legacy(self.tool, legacy), 0)      # a crash halfway, then again
        self.assertEqual(self.store.names(self.tool), ["Old look"])

    def test_remove_and_rename(self):
        p = self.store.put(self.tool, "A", self.tool.defaults())
        self.store.put(self.tool, "B", self.tool.defaults())
        self.assertEqual(self.store.rename(self.tool, p.uid, "B").name, "B (2)")
        self.assertTrue(self.store.remove(self.tool, p.uid))
        self.assertEqual(self.store.names(self.tool), ["B"])

    def test_a_renamed_tool_keeps_its_presets(self):
        self.store.put(self.tool, "Kept", self.tool.defaults())
        renamed = demo_tool(id="demo-two", aliases=("demo",))
        self.assertEqual(self.store.names(renamed), ["Kept"])


class LegacyAndBundles(unittest.TestCase):
    """Atlas Toolkit 2.0 kept your presets inside tools/<id>.json and the settings bundle; both still load."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=_isolate.ROOT))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_tool_settings_move_old_presets_once(self):
        from atlas_toolkit.core.settings import ToolSettings
        tool = demo_tool()
        old = self.dir / "tools" / "demo.json"
        old.parent.mkdir(parents=True)
        old.write_text(json.dumps({"values": {"width": 2.0}, "presets": {"Old": {"width": 7.0, "mode": "b"}}}),
                       encoding="utf-8")
        s = ToolSettings(tool, self.dir)
        self.assertEqual(list(s.user_presets()), ["Old"])
        self.assertNotIn("presets", json.loads(old.read_text(encoding="utf-8")))
        self.assertEqual(s.values()["width"], 2.0)                     # the rest of the file is untouched
        # a crash after the presets were written but before the tool file was: nothing doubles
        old.write_text(json.dumps({"presets": {"Old": {"width": 7.0, "mode": "b"}}}), encoding="utf-8")
        self.assertEqual(list(ToolSettings(tool, self.dir).user_presets()), ["Old"])

    def test_settings_bundle_carries_your_presets_and_merges_them(self):
        from atlas_toolkit.core import prefs
        from atlas_toolkit.core.registry import registry
        from atlas_toolkit.core.settings import AppSettings
        tool = registry().get("svg-overlap-cleaner")
        a, b = self.dir / "a", self.dir / "b"
        P.MineStore(a / "presets" / "mine").put(tool, "Shared", dict(tool.defaults(), tol=0.3))
        bundle = self.dir / "bundle.json"
        prefs.export_settings(AppSettings(a), bundle)
        self.assertIn(tool.id, json.loads(bundle.read_text(encoding="utf-8"))["presets"])
        P.MineStore(b / "presets" / "mine").put(tool, "Local", tool.defaults())
        prefs.import_settings(AppSettings(b), bundle)
        self.assertEqual(P.MineStore(b / "presets" / "mine").names(tool), ["Local", "Shared"])   # merged, kept
        # a 2.0 bundle had them inside each tool's entry
        old = {prefs.BUNDLE: 1, "app": {}, "tools": {tool.id: {"presets": {"Older": {"tol": 0.4}}}}}
        bundle.write_text(json.dumps(old), encoding="utf-8")
        prefs.import_settings(AppSettings(b), bundle)
        self.assertIn("Older", P.MineStore(b / "presets" / "mine").names(tool))
        self.assertNotIn("presets", json.loads((b / "tools" / f"{tool.id}.json").read_text(encoding="utf-8")))


class OpeningFiles(unittest.TestCase):
    def test_preset_files_the_system_hands_over_load_as_presets(self):
        from atlas_toolkit.ui.app import open_paths
        calls = []
        win = SimpleNamespace(import_preset_files=lambda f: calls.append(("presets", f)),
                              open_workflow=lambda *a: calls.append(("workflow", a)),
                              home=SimpleNamespace(route=lambda f: calls.append(("route", f))),
                              raise_=lambda: None, activateWindow=lambda: None)
        open_paths(win, ["a.atlaspreset", "b.ATLASPACK", "c.svg"])
        self.assertEqual(calls, [("presets", ["a.atlaspreset", "b.ATLASPACK"]), ("route", ["c.svg"])])
        calls.clear()
        open_paths(win, ["only.atlaspack"])
        self.assertEqual(calls, [("presets", ["only.atlaspack"])])
        folder = Path(tempfile.mkdtemp(dir=_isolate.ROOT))             # a .zip goes to presets only if it holds them
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        with zipfile.ZipFile(folder / "pack.zip", "w") as zf:
            zf.writestr("Demo/pack.json", "{}")
        with zipfile.ZipFile(folder / "models.zip", "w") as zf:
            zf.writestr("model.stl", "solid")
        calls.clear()
        open_paths(win, [str(folder / "pack.zip"), str(folder / "models.zip")])
        self.assertEqual(calls, [("presets", [str(folder / "pack.zip")]), ("route", [str(folder / "models.zip")])])


class Signing(unittest.TestCase):
    """Official packs are signed (core/ed25519.py) by a key the app trusts (core/preset_keys.py)."""

    def setUp(self):
        from atlas_toolkit.core import ed25519, preset_keys
        self.dir = Path(tempfile.mkdtemp(dir=_isolate.ROOT))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.secret = ed25519.new_secret()
        self.keys = preset_keys.KEYS
        self.keys["test-key"] = preset_keys.PublisherKey(ed25519.public_key(self.secret).hex(), "Atlas Toolkit tests")
        self.addCleanup(self.keys.pop, "test-key", None)

    def build(self, name="demo-complete", key=True) -> Path:
        out = self.dir / f"{name}.atlaspack"
        out.write_bytes(P.pack_bytes(FIXTURES / name, ("test-key", self.secret) if key else None))
        return out

    def rewrite(self, src: Path, change) -> Path:
        out = self.dir / f"changed-{src.name}"
        with zipfile.ZipFile(src) as a, zipfile.ZipFile(out, "w") as b:
            for info in a.infolist():
                data = change(info.filename, a.read(info))
                if data is not None:
                    b.writestr(info, data)
        return out

    def test_signed_and_unchanged_is_official(self):
        pack = P.read_pack(self.build())
        self.assertEqual((pack.status, pack.status_label, pack.signed_by, pack.problems),
                         ("official", "Official", "Atlas Toolkit tests", []))
        self.assertEqual(self.build(), self.build())                      # the same folder, the same bytes

    def test_any_change_is_modified(self):
        signed = self.build()
        edited = self.rewrite(signed, lambda n, d: d.replace(b"2.0", b"9.0") if n.endswith("demo.json") else d)
        self.assertEqual(P.read_pack(edited).status, "modified")
        manifest = self.rewrite(signed, lambda n, d: d.replace(b"Demo Presets", b"Free Presets")
                                if n == P.PACK_MANIFEST else d)
        self.assertEqual(P.read_pack(manifest).status, "modified")
        added = self.dir / "added.atlaspack"
        added.write_bytes(signed.read_bytes())
        with zipfile.ZipFile(added, "a") as zf:
            zf.writestr("presets/extra.json", "{}")
        got = P.read_pack(added)
        self.assertEqual(got.status, "modified")
        self.assertIn("added after", " ".join(got.problems))

    def test_unknown_retired_and_revoked_keys(self):
        from atlas_toolkit.core import preset_keys
        signed = self.build()
        public = self.keys["test-key"].public
        self.keys["test-key"] = preset_keys.PublisherKey(public, "Atlas Toolkit tests", "retired")
        self.assertEqual(P.read_pack(signed).status, "official")          # retired keys still vouch for old packs
        self.keys["test-key"] = preset_keys.PublisherKey(public, "Atlas Toolkit tests", "revoked")
        self.assertEqual(P.read_pack(signed).status, "revoked")
        del self.keys["test-key"]
        got = P.read_pack(signed)          # the pack carries its key: the signature holds, the signer is unknown
        self.assertEqual((got.status, got.signer.publisher, got.verified), ("signed", "Atlas Toolkit tests", False))
        old_style = self.rewrite(signed, lambda n, d: _drop_key(d) if n == P.SIGNATURE else d)
        self.assertEqual(P.read_pack(old_style).status, "unknown key")
        self.assertEqual(P.read_pack(self.build(key=False)).status, "unsigned")

    def test_the_pack_carries_everything_to_check_it(self):
        sig = json.loads(zipfile.ZipFile(self.build()).read(P.SIGNATURE))
        self.assertEqual((sig["public"], sig["publisher"]), (self.keys["test-key"].public, "Atlas Toolkit tests"))
        self.assertRegex(P.read_pack(self.build()).signer.fingerprint, r"^([0-9A-F]{4} ){3}[0-9A-F]{4}$")

    def test_a_carried_key_cannot_pass_for_a_builtin_one(self):
        from atlas_toolkit.core import ed25519
        forged = self.dir / "forged.atlaspack"
        forged.write_bytes(P.pack_bytes(FIXTURES / "demo-complete", ("test-key", ed25519.new_secret())))
        self.assertEqual(P.read_pack(forged).status, "modified")       # it names the built-in key, carries another

    def test_a_key_the_builtin_one_endorsed_is_official(self):
        """Key rotation without an app release: the old, built-in key vouches for the new one inside the pack."""
        from atlas_toolkit.core import ed25519, preset_keys
        new = ed25519.new_secret()
        endorsement = P.endorse(("test-key", self.secret), "test-key-2", ed25519.public_key(new).hex(), "Atlas")
        out = self.dir / "rotated.atlaspack"
        out.write_bytes(P.pack_bytes(FIXTURES / "demo-complete", ("test-key-2", new), endorsement=endorsement))
        got = P.read_pack(out)
        self.assertEqual((got.status, got.signed_by), ("official", "Atlas"))
        stranger = ed25519.new_secret()                                 # vouched for by a key the app does not know
        bogus = P.endorse(("other", stranger), "test-key-2", ed25519.public_key(new).hex(), "Atlas")
        out.write_bytes(P.pack_bytes(FIXTURES / "demo-complete", ("test-key-2", new), endorsement=bogus))
        self.assertEqual(P.read_pack(out).status, "signed")
        self.keys["test-key-2"] = preset_keys.PublisherKey(ed25519.public_key(new).hex(), "Atlas", "revoked")
        self.addCleanup(self.keys.pop, "test-key-2", None)
        self.assertEqual(P.read_pack(out).status, "revoked")             # the app can still revoke the new key

    def test_the_official_copy_wins(self):
        lib = P.PresetLibrary(mine_dir=self.dir / "mine", packs_dir=self.dir / "packs", extra_dirs=[],
                              hidden_path=self.dir / "hidden.json")
        lib.install_pack(self.build("demo-essentials"))                   # signed, lower tier
        lib.extra_dirs = [FIXTURES / "demo-complete"]                     # higher tier, unsigned
        lib.reload()
        narrow = next(p for p in lib.pack_presets(demo_tool()) if p.name == "Narrow")
        self.assertTrue(narrow.pack.verified)


def _drop_key(data: bytes) -> bytes:
    sig = json.loads(data)
    sig.pop("public", None)
    return json.dumps(sig).encode()


class Packs(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=_isolate.ROOT))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.tool = demo_tool()

    def library(self, extra=()):
        return P.PresetLibrary(mine_dir=self.dir / "mine", packs_dir=self.dir / "packs", extra_dirs=list(extra),
                               hidden_path=self.dir / "hidden.json")

    def build(self, name) -> Path:
        out = self.dir / f"{name}.atlaspack"
        out.write_bytes(P.pack_bytes(FIXTURES / name))
        return out

    def test_folder_and_built_pack_read_the_same(self):
        folder = P.read_pack(FIXTURES / "demo-complete")
        built = P.read_pack(self.build("demo-complete"))
        self.assertEqual([p.uid for p in folder.presets], [p.uid for p in built.presets])
        self.assertEqual((built.key, built.title, built.status), ("demo-presets:complete", "Demo Presets · Complete",
                                                                 "unsigned"))
        self.assertEqual(built.problems, [])

    def test_an_edited_pack_is_flagged(self):
        path = self.build("demo-essentials")
        tampered = self.dir / "tampered.atlaspack"
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(tampered, "w") as dst:
            for info in src.infolist():
                data = src.read(info)
                if info.filename.endswith("demo.json"):
                    data = data.replace(b"2.0", b"3.0")
                dst.writestr(info, data)
        self.assertEqual(P.read_pack(tampered).status, "modified")

    def test_unsafe_entries_are_refused(self):
        bad = self.dir / "bad.atlaspack"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("pack.json", "{}")
            zf.writestr("../escape.json", "{}")
        with self.assertRaises(P.PresetFormatError):
            P.read_pack(bad)

    def test_two_tiers_show_each_preset_once(self):
        lib = self.library([FIXTURES])
        names = [p.name for p in lib.pack_presets(self.tool)]
        self.assertEqual(names, ["Narrow", "Wide", "Möbius"])
        groups = lib.groups(self.tool)
        self.assertEqual([(g.title, len(g.presets)) for g in groups], [("Demo Presets", 3)])
        self.assertEqual(groups[0].presets[0].pack.tier, "complete")           # the higher tier's copy wins
        self.assertEqual(lib.tiers_of(groups[0].presets[0]), ["Essentials", "Complete"])
        self.assertEqual(lib.tiers_of(groups[0].presets[2]), ["Complete"])

    def test_install_replace_and_remove(self):
        lib = self.library()
        pack = lib.install_pack(self.build("demo-essentials"))
        self.assertEqual(len(lib.pack_presets(self.tool)), 2)
        lib.install_pack(self.build("demo-essentials"))                        # again: replaced, not doubled
        self.assertEqual(len(list((self.dir / "packs").iterdir())), 1)
        self.assertTrue(lib.remove_pack(lib.installed(pack.key)))
        self.assertEqual(lib.pack_presets(self.tool), [])

    def test_hidden_presets(self):
        lib = self.library([FIXTURES])
        wide = lib.find(self.tool, "Wide")[0]
        lib.set_hidden(wide.uid)
        self.assertEqual([p.name for p in lib.presets(self.tool)], ["Narrow", "Möbius"])
        lib.set_hidden(wide.uid, False)
        self.assertEqual(len(lib.presets(self.tool)), 3)

    def test_find_by_uid_source_or_name(self):
        lib = self.library([FIXTURES])
        lib.save_mine(self.tool, "Wide", self.tool.defaults())
        self.assertEqual(len(lib.find(self.tool, "wide")), 2)
        self.assertTrue(lib.find(self.tool, "mine:Wide")[0].mine)
        self.assertEqual(lib.find(self.tool, "demo-presets/demo/wide")[0].source, "demo-presets")

    def test_only_own_presets_export_and_duplicates_can(self):
        lib = self.library([FIXTURES])
        wide = lib.find(self.tool, "Wide")[0]
        with self.assertRaises(PermissionError):
            lib.export(self.dir / "x.atlaspreset", [wide])
        copy = lib.duplicate(self.tool, wide)
        self.assertEqual((copy.name, copy.based_on), ("Wide", wide.uid))
        path = lib.export(self.dir / "x.atlaspreset", [copy])
        self.assertEqual([p.name for p in P.read_preset_file(path)], ["Wide"])

    def test_import_reports(self):
        lib = self.library()
        lib.save_mine(self.tool, "Look", self.tool.defaults())
        path = P.write_preset_file(self.dir / "in", [P.Preset("Look", "demo", {"width": 2.0}),
                                                     P.Preset("Else", "missing-tool", {})])
        report = lib.import_file(path, registry=fake_registry(self.tool))
        self.assertEqual(([p.name for p in report.added], report.renamed, report.unknown_tools),
                         (["Look (2)"], [("Look", "Look (2)")], ["missing-tool"]))
        self.assertIsNotNone(lib.import_file(self.build("demo-complete")).pack)

    def test_extra_folders_from_the_environment(self):
        old = os.environ.get(P.ENV_DIRS)
        os.environ[P.ENV_DIRS] = str(FIXTURES)
        try:
            lib = P.PresetLibrary(mine_dir=self.dir / "mine", packs_dir=self.dir / "packs",
                                  hidden_path=self.dir / "hidden.json")
            self.assertEqual(len(lib.packs()), 3)
        finally:
            if old is None:
                os.environ.pop(P.ENV_DIRS, None)
            else:
                os.environ[P.ENV_DIRS] = old


class Installing(unittest.TestCase):
    """One file to install: an .atlaspack, a .zip of the pack's folder, or a .zip (a store download) holding packs,
    preset files and a readme. The library shows what it would do before it does it."""

    def setUp(self):
        from atlas_toolkit.core import ed25519, preset_keys
        self.dir = Path(tempfile.mkdtemp(dir=_isolate.ROOT))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.tool = demo_tool()
        self.reg = fake_registry(self.tool)
        self.secret = ed25519.new_secret()
        preset_keys.KEYS["test-key"] = preset_keys.PublisherKey(ed25519.public_key(self.secret).hex(), "Tests")
        self.addCleanup(preset_keys.KEYS.pop, "test-key", None)
        self.lib = P.PresetLibrary(mine_dir=self.dir / "mine", packs_dir=self.dir / "packs", extra_dirs=[],
                                   hidden_path=self.dir / "hidden.json")

    def source(self, name="demo-complete", **meta) -> Path:
        """A copy of a fixture pack's folder with pack.json fields changed."""
        self.copies = getattr(self, "copies", 0) + 1
        dst = self.dir / "src" / f"{name}-{self.copies}"
        shutil.copytree(FIXTURES / name, dst)
        data = json.loads((dst / P.PACK_MANIFEST).read_text(encoding="utf-8"))
        data.update(meta)
        (dst / P.PACK_MANIFEST).write_text(json.dumps(data), encoding="utf-8")
        return dst

    def build(self, folder, key="builtin") -> Path:
        """The folder as a pack signed by the app's (test) key, by a stranger's key, or not at all."""
        from atlas_toolkit.core import ed25519
        if key == "stranger" and not hasattr(self, "stranger"):
            self.stranger = ed25519.new_secret()
        secret = {"builtin": ("test-key", self.secret), "none": None,
                  "stranger": ("someone-1", getattr(self, "stranger", None))}[key]
        out = self.dir / f"{Path(folder).name}-{key}.atlaspack"
        out.write_bytes(P.pack_bytes(Path(folder), secret))
        return out

    def zip_of(self, name, entries: dict) -> Path:
        out = self.dir / name
        with zipfile.ZipFile(out, "w") as zf:
            for arc, data in entries.items():
                zf.writestr(arc, data)
        return out

    def plan(self, *paths):
        return self.lib.plan_install(list(paths), registry=self.reg)

    def test_a_zip_of_the_pack_folder_installs_and_stays_official(self):
        built = self.build(FIXTURES / "demo-complete")
        with zipfile.ZipFile(built) as zf:                 # what "Send to › Compressed folder" makes of the folder
            entries = {f"Demo Presets/{n}": zf.read(n) for n in zf.namelist()}
        entries["__MACOSX/._pack.json"] = b"junk"
        plan = self.plan(self.zip_of("demo.zip", entries))
        self.assertEqual([(i.action, i.pack.status) for i in plan.items], [("install", "official")])
        self.lib.install(plan)
        installed = self.lib.installed("demo-presets:complete")
        self.assertEqual((installed.status, installed.path.name), ("official", "demo-presets-complete.atlaspack"))

    def test_a_store_download_holding_tiers_presets_and_a_readme(self):
        complete = self.build(FIXTURES / "demo-complete").read_bytes()
        essentials = self.build(FIXTURES / "demo-essentials").read_bytes()
        mine = P.preset_file_data([P.Preset("Shared look", "demo", {"width": 3.0})])
        bundle = self.zip_of("Demo Presets 2027.zip", {
            "README.txt": b"Thanks for buying!", "Demo/demo-complete.atlaspack": complete,
            "Demo/demo-essentials.atlaspack": essentials, "Demo/extras.atlaspreset": json.dumps(mine).encode()})
        self.assertTrue(P.holds_presets(bundle))
        plan = self.plan(bundle)
        got = {(i.pack.tier if i.pack else i.kind): i.action for i in plan.items}
        self.assertEqual(got, {"complete": "install", "essentials": "included", "presets": "add"})
        self.lib.install(plan, registry=self.reg)
        self.assertEqual([pk.tier for pk in self.lib.packs()], ["complete"])
        self.assertEqual(self.lib.mine.names(self.tool), ["Shared look"])
        again = self.plan(bundle)                           # loading it twice changes nothing
        self.assertEqual({i.action for i in again.items if i.kind == "pack"}, {"same", "included"})

    def test_versions_update_and_older_ones_wait(self):
        v1, v2 = self.source(pack_version="1"), self.source(pack_version="2")
        self.lib.install(self.plan(self.build(v1)))
        newer = self.plan(self.build(v2))
        self.assertEqual((newer.items[0].action, newer.items[0].note), ("update", "version 1 → 2"))
        self.lib.install(newer)
        older = self.plan(self.build(v1))
        self.assertEqual(older.items[0].action, "older")
        self.lib.install(older)
        self.assertEqual(self.lib.installed("demo-presets:complete").pack_version, "2")
        self.lib.install(self.plan(self.build(v1)), downgrade=True)
        self.assertEqual(self.lib.installed("demo-presets:complete").pack_version, "1")

    def test_what_cannot_or_should_not_install(self):
        future = self.plan(self.build(self.source(toolkit_min="99.0")))
        self.assertEqual(future.items[0].action, "blocked")
        self.assertIn("99.0", future.items[0].note)
        self.lib.install(future, accept_warnings=True, downgrade=True)
        self.assertEqual(self.lib.packs(), [])
        unsigned = self.plan(self.build(FIXTURES / "demo-complete", key="none"))
        self.assertTrue(unsigned.warnings)
        self.lib.install(unsigned)                                   # warnings: nothing without someone's yes
        self.assertEqual(self.lib.packs(), [])
        self.lib.install(unsigned, accept_warnings=True)
        self.assertEqual(self.lib.installed("demo-presets:complete").status, "unsigned")
        official = self.plan(self.build(FIXTURES / "demo-complete"))
        self.lib.install(official)
        self.assertEqual(self.plan(self.build(FIXTURES / "demo-complete")).items[0].action, "same")
        worse = self.plan(self.build(FIXTURES / "demo-complete", key="none"))   # an unsigned copy over the Official
        self.assertEqual(worse.items[0].action, "replace")
        self.assertIn("the installed copy is Official", " ".join(worse.warnings))
        self.assertEqual(self.plan(self.zip_of("photos.zip", {"a.png": b"x"})).errors[0][1],
                         "it holds no presets or preset packs")
        self.assertFalse(P.holds_presets(self.zip_of("photos2.zip", {"a.png": b"x"})))

    def test_a_publisher_you_choose_to_trust(self):
        plan = self.plan(self.build(FIXTURES / "demo-complete", key="stranger"))
        (signer,) = plan.untrusted
        self.assertEqual(plan.items[0].pack.status, "signed")
        self.lib.install(plan, trust=[signer])
        pack = self.lib.installed("demo-presets:complete")
        self.assertEqual((pack.status, pack.status_label), ("trusted", "Trusted publisher"))
        self.assertTrue(self.lib.untrust(signer.fingerprint))
        self.assertEqual(self.lib.installed("demo-presets:complete").status, "signed")

    def test_the_command_line_inspects_then_imports(self):
        import contextlib
        import io
        from atlas_toolkit.core import cli
        path = self.build(FIXTURES / "demo-complete", key="stranger")
        args = SimpleNamespace(action="inspect", files=[str(path)], tool=None, force=False, trust=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), unittest.mock.patch.object(cli, "_library", lambda: self.lib):
            self.assertEqual(cli.presets_command(self.reg, args), 0)
            self.assertEqual(self.lib.packs(), [])                     # inspect changes nothing
            args.action, args.trust = "import", True
            self.assertEqual(cli.presets_command(self.reg, args), 0)
        self.assertIn("signed by Atlas Toolkit tests", out.getvalue())
        self.assertIn(self.lib.installed("demo-presets:complete").signer.fingerprint, out.getvalue())
        self.assertEqual(self.lib.installed("demo-presets:complete").status, "trusted")


if __name__ == "__main__":
    unittest.main()
