# Presets

A preset is a named set of a tool's settings. Since Atlas Toolkit 2.1, presets are **data, never code**. No tool spec
defines presets, and the app ships with none. Each tool starts from its own defaults, and its **Presets** drop-down
lists two kinds of preset:

- **My presets:** the presets you save yourself, or load from a file someone gave you.
- **Pack presets:** the presets in the preset packs you have loaded. Each pack is one file per tier of a collection,
  for example *Atlas Presets 2027 · Essentials* or *· Complete*.

The code is in `src/atlas_toolkit/core/presets.py` (no Qt) and `src/atlas_toolkit/ui/widgets/presets.py` (the
drop-down). The tests are `tests/test_presets.py`, which uses made-up packs in `tests/fixtures/presets/`, and
`tests/test_private_presets.py`, which uses the real packs.

## In the app

- **Drop-down:** opens on "Custom settings". Picking a preset sets the form. A picked preset stays shown while the
  settings still match it.
- **Icons beside it:**
  - **Save:** stores the current settings as one of your presets. It asks before replacing one with the same name.
  - **Menu (☰):**
    - **Save these settings to a file…**
    - **Save my presets to a file…**
    - **Load presets from a file…** An `.atlaspreset` file joins My presets, and a name that is taken gets " (2)".
      An `.atlaspack` file installs the pack. A `.zip` holding packs or preset files (a store download) loads the
      same way. The app first shows what the file holds and what loading it would do (see Installing).
    - **Copy this preset to My presets:** for pack presets, so you can change one or save it to a file.
    - **Delete a preset of mine…** (asks first).
    - **Manage presets and packs…:** opens Setup › Presets on this tool.
  - **Reset:** back to the tool's defaults.
- **Opening files:** dropping `.atlaspreset` or `.atlaspack` files on the window loads them, after showing what they
  hold. So does a `.zip` that holds them; any other `.zip` goes to the tools as before. Opening one from the file
  manager works too: the installers register both types on Windows, Linux (deb, rpm) and macOS.

### Setup › Presets

- **Preset packs:** every pack the app reads. Each one shows its tier, version, preset count and status:

  | Status | Meaning |
  |---|---|
  | Official | Signed by Atlas Toolkit's publisher (a key in `core/preset_keys.py`, or one such a key endorsed), and unchanged since |
  | Trusted publisher | Signed by someone else whose key you chose to trust, and unchanged since |
  | Signed, publisher not trusted | The signature holds, so it is unchanged since its signer signed it, but the app does not know who the signer is |
  | Unsigned | No signature: a test build, or a pack someone made by hand |
  | Modified | A file differs from what was signed, or the signature does not match |
  | Unknown publisher key | An older pack that names its key without carrying it, and the app does not know that key |
  | Key revoked | The key that signed it is no longer trusted |

  The tooltip says where the pack comes from, who signed it and the key's fingerprint, its edition, licence and any
  problem.
  - **Load presets or a pack…** adds files (`.atlaspreset`, `.atlaspack`, or a `.zip` holding them).
  - **Trust publisher…** is for a pack signed by someone the app does not know. Compare the key's fingerprint with
    the one on the publisher's own website first. Trusted, their packs show *Trusted publisher*; **Stop trusting
    publisher…** undoes it. Official packs need neither.
  - **Remove pack…** asks first. To get the pack back, load its file again.
  - **Open packs folder** shows where installed packs are kept.
  - A pack read from a folder in `ATLAS_TOOLKIT_PRESET_DIRS` is not installed, so it cannot be removed here.
- **Presets:** a tree by tool group and tool, with a search box. Each preset shows where it comes from (My presets or
  the pack's name) and notes:
  - **Mine**, or the pack's status
  - the tiers that hold it ("Essentials · Complete")
  - **Outdated:** it was made for another version of the tool, and the details say what no longer fits
  - **Hidden**

  Tools this version of the app does not have list their presets under "Tools not installed".
- **Filters** under the search box narrow the tree, and combine:

  | Filter | Choices |
  |---|---|
  | Tool group | each group of the sidebar |
  | Type | Mine; Official; Trusted publisher; Not verified (any other pack status: unsigned, modified, signed by an unknown publisher …) |
  | Source | My presets, or one pack (a collection's tiers count as one) |
  | State | Outdated; Hidden (listed even with Show hidden off); Copied from a pack; In several tiers; Has notes |
  | Tag | the tags presets carry (shown only when some do) |

  Search also reads each preset's tip and tags. "Showing N of M" counts what the filters let through, and
  **Clear filters** empties them and the search. Each choice shows how many presets it would list with the other
  filters as they are ("Official (357)"); a choice that would list none is greyed out, and an empty list names the
  filters that emptied it.
- **Actions on the selected presets:**
  - **Save to a file…** (yours only).
  - **Copy to My presets.**
  - **Rename…** (yours).
  - **Delete…** Yours are deleted, after asking. Pack presets cannot be deleted one by one, because they belong to
    the pack: Delete offers to hide them instead, and **Show hidden** with **Show** brings them back.
  - **Hide / Show.**
- **Sharing:** the tool page and its workflow steps share one library, so a preset saved in one shows in the other.
- **What a preset never changes:** view state (`Tool.preset_skip`, such as a camera), second input files, and the
  input settings a preset does not set itself (`Tool.preset_inputs`).

Pack presets cannot be saved to a file directly. Copy one to My presets first, and the copy can be exported. Sharing a
copy is still possible: this rule only makes reposting a bought preset take a deliberate step.

### Command line

```bash
atlas-toolkit presets list [TOOL]                 # your presets and installed packs
atlas-toolkit presets import look.atlaspreset     # into My presets
atlas-toolkit presets inspect "Atlas Presets 2027.zip"   # what it holds, who signed it, what importing would do
atlas-toolkit presets import atlas-2027-complete.atlaspack
atlas-toolkit presets import someones-pack.atlaspack --trust   # and trust its signer from now on
atlas-toolkit run stipple bust.stl --preset Soft
atlas-toolkit run stipple bust.stl --preset "mine:Soft"        # when a pack has one of the same name
atlas-toolkit run stipple bust.stl --preset-file look.atlaspreset --budget 9000
```

`presets import` installs what the plan says installs, and skips the rest with a reason. `--force` also installs
packs with warnings (unsigned, modified) and older versions than the installed one. `--preset` takes a preset's name,
`source:name` (`source` is `mine` or a pack's collection id) or its uid. Flags given on the command line win over the
preset.

## Where they live

| What | Where |
|---|---|
| Your presets | `<settings>/presets/mine/<tool-id>.atlaspreset`, one file per tool |
| Pack presets you hid | `<settings>/presets/hidden.json` |
| Publishers you trust | `<settings>/presets/trusted-publishers.json` (by key fingerprint) |
| Installed packs | `<data>/packs/<collection>-<tier>.atlaspack` (copied there when loaded) |
| More packs, read in place | folders in `ATLAS_TOOLKIT_PRESET_DIRS` (separated like `PATH`) |

`<settings>` and `<data>` are the folders in `core/paths.py`, and portable mode applies to them. The settings bundle
(Setup › Storage › Export) carries your presets but never packs, because packs are purchases.

Atlas Toolkit 2.0 kept your presets inside `tools/<id>.json`. Opening a tool's settings moves them once:
`core/settings.ToolSettings` writes them to the new file first and only then removes them from the old one. A crash in
between therefore only repeats the move, which skips presets that are already there. Importing a 2.0 settings bundle
brings its presets along in the same way.

## `.atlaspreset` format

UTF-8 JSON:

```json
{
  "format": "atlas-toolkit.preset",
  "format_version": 1,
  "toolkit": "2.1.0",
  "presets": [
    {
      "uid": "mine/stipple/3f2a9c1e0b7d",
      "name": "Soft shadows",
      "tool": "stipple",
      "tool_version": "1.0",
      "values": {"min_spacing": 0.85, "shadows": true, "...": "..."},
      "explicit": ["min_spacing", "shadows"],
      "tip": "One line shown as the item's tooltip.",
      "tags": [],
      "created": "2026-11-07T18:00:00+00:00",
      "modified": "2026-11-07T18:00:00+00:00",
      "based_on": ""
    }
  ]
}
```

- **One file, many presets:** a file can hold presets for any number of tools.
- **`values`:** a full snapshot of the tool's settings, in each parameter's JSON form. Lists stand for 3-vectors, and
  the string `"auto"` keeps meaning "work it out per file".
  - **Left out:** view state (`preset_skip`) and file paths (`FileRef`), because paths belong to one computer.
  - **Pack presets** also leave out input settings (`preset_inputs`) the author did not set on purpose.
- **`explicit`:** the keys the author set on purpose. The rest are what the defaults were when the preset was made.
- **`uid`:** identifies a preset and never changes when it is renamed.
  - **Your own presets:** `mine/<tool>/<random>`.
  - **Pack presets:** `<collection>/<tool>/<slug>`. The slug is ASCII from the name, so "Möbius Wreath" becomes
    `mobius-wreath` and "2 × 2" becomes `2-x-2`.
- **Fields this version does not know** are kept when the file is read and written again.
- **A newer `format_version`** is refused with "made by a newer Atlas Toolkit", so an old app never half-reads a
  new file.

## `.atlaspack` format

A zip file, and the one file a publisher ships: it holds everything the app needs to install it, check it and use
it, the signer's public key included. While a pack is being made, the same layout can be an ordinary folder.

```
pack.json                 the manifest
presets/<tool-id>.json    that tool's presets, in order, in the .atlaspreset format
media/…                   optional previews (PNG, WebP, JPEG); licence or readme text (.txt, .md)
signature.json            the signature, the signer's public key, and its endorsement if any (see Signing)
```

`pack.json`:

| Field | Meaning |
|---|---|
| `format`, `format_version` | `atlas-toolkit.pack`, `1` |
| `collection` | Lower-case id shared by every tier of one collection: `atlas-presets-2027` |
| `name` | Shown as the heading in the drop-down: `Atlas Presets 2027` |
| `tier`, `tier_rank` | `basic` 10, `essentials` 20, `complete` 30 (a higher rank wins when two tiers hold a preset) |
| `pack_version` | Bumped for every release of the pack; a newer one replaces the installed copy |
| `edition`, `publisher`, `licence` | Shown in the manager; the licence travels with the pack |
| `toolkit_min` | The oldest app the pack is made for (older apps load it and say so) |
| `supersedes` | Collections or tiers this pack contains |
| `files` | `{path: sha256}` of every other file, written when the pack is built; a mismatch marks the pack *modified* |

**Reading a pack:**

- It is read in memory and never unpacked.
- Absolute paths, `..`, unexpected file types and oversized packs are refused.
- When several tiers hold the same uid, the preset shows once, under the collection's heading, from the best copy:
  verified first, then the higher tier, then the newer version. Owning Essentials and Complete therefore lists each
  preset once.

`core/presets.pack_bytes(folder, key)` builds a pack file from a source folder with sorted entries, fixed timestamps,
hashes and, given a key, the signature. The same folder always gives the same bytes.

### Installing

What loads, from any of the ways in (the preset bar, Setup › Presets, a drop, the file manager, the command line):

- an `.atlaspack`;
- an `.atlaspreset`;
- a `.zip` of a pack's folder, as a file manager's "compress" makes it, with the folder at the top. It keeps its
  signature, because every file's bytes are unchanged;
- a `.zip` holding any of these, such as a store download with a readme beside the packs. One level of zip inside a zip
  is read; other files are ignored;
- a folder holding any of these.

`PresetLibrary.plan_install(paths)` reads them all in memory, changes nothing, and says per pack or presets file what
loading would do:

| Plan | When |
|---|---|
| install | The pack is new here |
| update | A newer `pack_version` of an installed pack |
| replace | The same version with different contents (a re-signed build, say) |
| add to My presets | An `.atlaspreset` |
| already installed | Identical to the installed copy: skipped |
| older than the installed one | Skipped unless forced (`--force`, `downgrade=True`) |
| already included | A higher tier of the same collection, installed or in the same load, holds every one of its presets: skipped |
| cannot install | It needs a newer app (`toolkit_min`), or holds no presets |

The plan also carries warnings: the pack is unsigned or modified, or it would replace an Official copy with a less
trusted one. It also lists signers the app does not know. The app shows the plan in one dialog with **Install** (or
**Install anyway** when there are warnings, with Cancel the default), plus a box to trust an unknown signer.
`PresetLibrary.install(plan, …)` then carries it out. The same pack found twice, say in a bundle and as a loose copy,
loads once, from the better copy. Installed packs are stored as
`<collection>-<tier>.atlaspack`, whatever the downloaded file was called, so a new version replaces the old one.

## Signing

An official pack is signed by its publisher, which is what earns *Official*. Nothing is locked: anyone can read or
copy a pack. A signature proves only that a pack is the publisher's own and unchanged.

- **What is signed:** `signature.json` is `{"key_id", "alg": "ed25519", "public", "publisher", "sig", "endorsement"?}`.
  The signature covers `atlas-toolkit.pack.v1` and a newline, followed by the exact bytes of `pack.json`. `pack.json`
  lists the SHA-256 of every other file, so one signature covers the whole pack.
- **The pack carries its key.** Any app can check that a pack is unchanged since it was signed. Carrying a key does
  not make it trusted, though: anyone can make a key and write any publisher's name next to it. Who the key belongs to
  decides the status.
- **How the app checks:** `core/presets._verification` (with `core/ed25519.py`) checks every listed hash, then the
  signature:
  - A `key_id` in `core/preset_keys.KEYS` is checked against the built-in public key. A pack that names a built-in key
    but carries a different one is *Modified*. A good signature makes it *Official*.
  - Any other key is checked against the key the pack carries. When its `endorsement` is a valid statement, signed by
    a built-in key, that this key belongs to the publisher, the pack is *Official*. When you trust the key's
    fingerprint, it is *Trusted publisher*. Otherwise it is *Signed, publisher not trusted*.
  - Any file added after the build makes the pack *Modified*. Verification never needs the network.
- **Keys:** `core/preset_keys.py` holds public keys only, each with a status:
  - `active` keys sign new packs.
  - `retired` keys sign no more, but their packs stay Official.
  - `revoked` keys are distrusted, and their packs show "Key revoked".
  
  Never remove a key that signed packs people own.
- **Moving to a new key:**
  - Have a built-in key endorse it: `tools/build_pack.py endorse --root <old key> --key <new key>`, in the private
    repository. Every pack the new key signs then carries the endorsement, so apps already out there show those packs
    Official.
  - Add the new key to `KEYS` in the next release anyway.
  - A leaked key is marked `revoked`. That also voids its endorsements, and an endorsed key can be revoked by its own
    id.
- **Private keys** never live in a repository. The private presets repository's `tools/build_pack.py keygen` writes
  one to a file outside every repository and prints the entry for `preset_keys.py`. Back it up offline: whoever has
  it can sign packs the app calls Official.
- **Building:** in the private presets repository, `tools/build_pack.py check` validates every pack against the
  current app. `tools/build_pack.py build --key <file>` writes signed packs to `dist/`, carrying the key's endorsement
  when one sits beside the key file, and verifies each one with the app's own code. See that repository's README.

## When tools change

Saved presets, settings and workflows outlive the settings that wrote them. In order:

1. **A renamed setting:** declare its old key on the parameter, `Float("width", …, former=("breadth",))`. A renamed
   choice value: `Choice(…, former_values={"old": "new"})`. `params.resolve()` applies both everywhere, with no
   version check, and a value already in the new form is left alone.
2. **A change that needs logic:** `Tool.migrate = ".module:fn"`, where `fn(values, from_version) -> values` gets a
   preset's values and the tool version that wrote them. `core/presets.older()` compares versions, padded so 1.1
   equals 1.1.0.
3. **Whatever still does not fit:** `params.resolve_lenient()` falls back to the default for that setting and reports
   it. The preset applies, the page shows a warning that names what changed, and nothing breaks.
4. **A tool that is not installed:** its presets are kept and simply not offered.

A preset stores a full snapshot, so a later change to a tool's defaults does not shift a look someone bought. A
setting added later starts from its default.

## For developers

- **Never put presets in a spec.** `tests/test_contract.py` fails if `Tool` has preset fields, if a file under
  `tools/` assigns `presets`, `preset_tips` or `default_preset`, or if a preset file is anywhere under `src/`.
- **Starting values:** a tool's starting values are its parameters' defaults, so tune those.
- **The official presets** live in a separate private repository, `AtlasToolkit-presets`, as pack source folders
  (`packs/<pack>/pack.json`, `presets/<tool>.json`).
  - **Setting it up:** set `presets = "…/AtlasToolkit-presets"` under `[paths]` in `dev.local.toml`, or
    `ATLAS_PRESETS_DIR`.
  - **Tests:** tests that need real presets use `tests/_presets.py` and skip without it. With
    `ATLAS_TEST_REQUIRE_PRESETS=1` (CI with a deploy key), a missing repository is an error instead.
  - **Dev scripts:** they use `devpaths.preset_names / preset_raw / preset_values`, and their child runs get
    `devpaths.preset_dirs_env()`.
  - **Authoring and reviewing presets:** `scripts/dev/preset_sheet.py --tool <id> [--draft <file.json>]` runs a
    tool's pack presets, plus draft candidates, on the reference models. It writes a contact sheet and a separation
    table to `build/qa/preset_sheets/<tool>/`.
    - Mesh tools are drawn by Contour Lines and Plotter Vision; drawing tools show their own SVG; SVG tools work on a
      Contour Lines drawing.
    - The separation table gives 1 − SSIM to each preset's nearest neighbour. A new preset should be no closer to its
      neighbour than the existing ones are, or it is a near-duplicate.
    - The presets repository's `tools/build_pack.py add` writes the drafts that earn a place into the pack.
    - Its `check` holds the pack to 5 or more presets per tool (3 for the utility tools) and exactly 3 tagged
      `essentials`.
- **Running the app with the packs:** set `ATLAS_TOOLKIT_PRESET_DIRS` to the repository (its `packs/` folder is found
  by itself). Source folders read this way show as *Unsigned*; a built, signed pack shows as *Official*.

### Continuous integration

CI runs the tests that need real presets only when it can read the private repository. Until then they skip, as on a
fresh clone. Every test job runs `.github/actions/private-presets`, which checks the repository out next to the app
and sets `ATLAS_PRESETS_DIR` and `ATLAS_TEST_REQUIRE_PRESETS=1`. It costs one extra checkout and no extra job.

**Off for now (decided 2026-11-11):** with the key set, every CI job would also run every pack preset, which takes
minutes per job and costs too much CI. So the secret stays unset. Preset changes are tested locally instead: run
`tests/test_private_presets.py` and the test module of each tool whose presets changed. To switch it on later:

1. The packs live in the private repository `atlas-toolkit-org/AtlasToolkit-presets`. Put that `owner/name` in the
   app repository's variable `PRESETS_REPOSITORY` (there is no default).
2. Make a key pair just for this: `ssh-keygen -t ed25519 -N "" -f presets_deploy_key`. Add the `.pub` half to the
   presets repository as a read-only **deploy key**.
3. Add the private half to the app repository as the secret `PRESETS_DEPLOY_KEY`, then delete both files.

## Paid packs: requirements

- **Building:**
  - One `.atlaspack` per tier.
  - The Complete pack holds every Essentials preset with the same uids.
  - uids are never reused for a different look.
- **Contents of every preset:** a tip; values that apply with no problems on the `toolkit_min` app; no file paths.
- **Inside the pack:**
  - The licence: personal use of the presets, and drawings and prints made with them can be sold.
  - A `pack_version` for updates.
  - An edition policy: whether a new edition replaces an old one or adds to it.
- **Signing:** official packs are signed with an Ed25519 key kept offline, outside both repositories (see
  Signing). The app verifies the signature and shows *Official*, and an edited copy shows *Modified*. Nothing is
  locked and nothing phones home.
- **Shipping:** the `.atlaspack` is the download. A store that delivers `.zip` files can wrap it with a readme, and it
  installs the same way.
- **For buyers:** removing a pack deletes the installed copy, so buyers keep their purchase file to load it again.

## Tiers, licence and where packs come from

The app is GPL-3.0-or-later. Preset packs are separate works: the app ships none, and each pack carries its own
licence (`pack.json` `licence`, with the full text in `media/`).

| Tier | `tier_rank` | Holds | Price |
|---|---|---|---|
| Basic | 10 | About one preset per tool, to get started | Free |
| Essentials | 20 | Exactly 3 per tool, each clearly unlike the others | Paid |
| Complete | 30 | 5 or more per tool (3 for utility tools), Essentials included | Paid |

- **Where to get them:** official packs are published on [Ko-fi](https://ko-fi.com/atlas_toolkit) and
  [atlas-toolkit.org](https://atlas-toolkit.org). The Basic pack is free there.
- **Official packs' licence** (`LicenseRef-Atlas-Presets-1.0`):
  - Use the presets for personal and commercial work. Drawings, prints and models made with them are yours, and you
    may sell them.
  - Don't pass the pack, or presets copied from it, on to others.
- **Paid presets never enter this repository.** Not in specs, tests, docs, workflows or scripts. Tests use the
  made-up packs in `tests/fixtures/presets/`, and dev scripts read the real packs through `[paths] presets` in
  `dev.local.toml`. Docs may name pack presets, but they never list a paid preset's values. CI does not read the
  packs (see Continuous integration), so check this locally, with the packs configured, before every release:
  `python scripts/dev/preset_leak_check.py`. `tests/test_private_presets.py` runs it too. It fails on a leak: a file
  that holds enough of one preset's own values together (in code, CLI examples, JSON or workflow steps). It also lists
  prose that names a preset next to one of its tuned numbers, for you to read. A line that only happens to match
  ends with `preset-leak: ok` (in Markdown, `<!-- preset-leak: ok -->`).
- **Basic presets** may appear in docs and examples. Example workflows (`docs/workflows/`) keep a tool's choices
  but not a paid preset's tuned numbers.
