# Atlas Toolkit: working notes

One PySide6 app for pen-plotter tools, in its own repository. `README.md` is the front page, `docs/TOOLS.md` the user guide, `docs/BUILDING.md` covers building it,
`docs/PACKAGING.md` the installers, `docs/PLATFORMS.md` what each OS has proven, `docs/ARCHITECTURE.md` how it fits together,
`docs/ADDING_A_TOOL.md` adding a tool, `docs/PRESETS.md` presets and preset packs, and `native/README.md` the C++ helpers. `AGENTS.md` holds the same rules for other coding agents (Codex): keep the two in sync.

## Rules

- **After every feature or bug fix:**
  1. run the tests;
  2. bump the changed tool's `version` when its behaviour, outputs or defaults change (a spec-only refactor
     does not; saved workflows warn on a version change), and `src/atlas_toolkit/version.py` for app-level changes;
  3. add a `CHANGELOG.md` line;
  4. rebuild with `.venv\Scripts\python scripts\build.py`.

  The build compiles the native helpers (`native/build.py`, incremental), runs the tests, builds
  `dist\Atlas Toolkit.exe` and its console companion `dist\atlas-toolkit.exe`, and self-tests the exe before
  publishing it. On macOS it publishes `dist/Atlas Toolkit.app`; on Linux `dist/atlas-toolkit/`, built in
  `packaging/linux/Dockerfile` from a clean clone. The recipe is `packaging/atlas_toolkit.spec`. Also rebuild when asked.
- **A tool is a folder in `src/atlas_toolkit/tools/`:** `__init__.py` (spec, light imports), `<folder>.py`
  (algorithm, no GUI), `engine.py` (adapter). Never special-case a tool in `core/` or `ui/`. If a tool needs
  something new, add it to the contract (`core/tool.py`, `core/params.py`) so every tool can use it.
- **The README is the catalogue.** A new tool gets a card in its group in `README.md` and a recipe in
  `scripts/dev/readme_gallery.py` (`--publish` writes the image; SVG tools have none). Keep cards to 2-3 lines and put
  detail in `docs/TOOLS.md` or the tool's guide (`test_readme` checks).
- **Presets are data, never code.** No tool spec defines presets (`test_contract` fails if one does) and the app
  ships none: a tool starts from its parameters' defaults. Users save and load `.atlaspreset` files; official
  presets come in `.atlaspack` packs built from the private `AtlasToolkit-presets` repository, which tests reach
  through `[paths] presets` in `dev.local.toml` (`tests/_presets.py`, skips without it). A renamed parameter keeps
  old files working with `former=` / `former_values=`. Official packs are signed (`core/ed25519.py`); only public
  keys go in `core/preset_keys.py`, never a private key in any repository. The official pack keeps 5 or more
  presets per tool (3 for utility tools) and exactly 3 tagged `essentials`, each clearly unlike the others (fewer beat
  near-duplicates): review new ones with `scripts/dev/preset_sheet.py`. See `docs/PRESETS.md`.
- **Licence: GPL-3.0-or-later** (`LICENSE`, `COPYRIGHT.md`; `version.LICENSE_ID` is the one source, `test_licence`
  checks). New third-party code, models or images need a GPL-3-compatible licence and a line in
  `THIRD_PARTY_NOTICES.md`. Never bundle libigl's `igl.copyleft` or `igl.triangle` (`packaging/atlas_toolkit.spec`
  `LEFT_OUT`). Preset packs are separate works with their own licence: **paid preset values never enter this
  repository** (specs, tests, docs, workflows, scripts); `scripts/dev/preset_leak_check.py` checks, locally. Only
  Basic-tier presets may appear in docs and examples.
- **Every tool has its own icon.** Never reuse another tool's `icon`: draw a new glyph in `EXTRA_ICONS`
  (`src/atlas_toolkit/ui/theme/__init__.py`; 24x24 grid, stroke 1.5, round caps) and name it in the spec.
  `test_contract` fails on duplicates. Check this for tools written by other assistants too.
- **Workflows** (`core/workflow.py`, `ui/workflow/`) reuse tools through the contract alone: a tool needs nothing
  extra to be a step. A `FileRef` parameter becomes a side port, and an engine that writes nothing in run mode passes
  its input on. Tests: `tests/test_workflow.py`.
- **The Overlap Cleaner's engine** (`tools/svg_overlap_cleaner/svg_overlap_cleaner.py`) stays a standalone,
  standard-library script with its own CLI. Its default behaviour must not change: `tests/test_svg_overlap_cleaner.py`
  is v1's suite, unchanged. New behaviour goes in as options that are off by default.
- **Outputs are signed for Atlas:** `<desc>tool-id</desc>` for SVGs, `atlas-toolkit:<id>` for meshes. The literal
  must be in the tool's folder (`test_contract` checks it). Names come from the `Output` template.
- **Tests and screenshots use temporary folders.** Outputs never go into a user's model, image or project folders
  unless the user runs the app on files there. Dev and validation scripts write to `build/qa/<name>`. Every test module
  starts with `import _isolate`, which keeps settings and caches out of real profiles (`test_portability` checks).
- **Mesh tools keep model units; SVG tools work in mm.** Randomness is always seeded through a `Seed` parameter.
- **The UI uses the design tokens** (`ui/theme`, synced from the studio design system by `scripts/sync_design.py`).
  No hard-coded colours in widgets. Number fields are always `IntSpin` / `DoubleSpin` (`ui/widgets/spin.py`), never a plain
  Qt spin box (`test_spin` checks).
- **The app is standalone and portable.** There are no machine paths, user details or studio-workspace
  references in the app, its UI, tests or committed scripts. Optional dev folders come from
  `atlas_toolkit.devpaths`, which reads `dev.local.toml` (gitignored; copy `dev.local.example.toml`) or the
  `ATLAS_STUDIO_WORKSPACE` / `ATLAS_DESIGN_DIR` variables. Tests that need them skip without them. Platform
  specifics go behind `sys.platform` checks: the app targets Windows x64, Linux x86-64 and macOS 15+ on Apple Silicon
  (`docs/dev/multi-os-port.md`), and `test_portability` catches on Windows what would break elsewhere.
- **Every 3D canvas mixes in `GuardedGL`** (`ui/widgets/gl_guard.py`), so a driver that cannot draw gets a message and not an
  error for every frame (`test_gl_failure` checks). PyOpenGL must not load before `_match_opengl_to_qt` in `ui/app.py`.
- **Keep `docs/PLATFORMS.md` true.** It says what has really been proven on each OS: macOS stays "built and tested
  automatically, not by hand" until someone runs `docs/dev/macos-verification.md` on a real Mac.
- **Child processes start through `core/procs.py`** (`spawn`, `finish`), and web pages and files open through
  `paths.open_url`, never `QDesktopServices`. A child then gets a clean environment and dies with its job on every OS
  (`test_portability` checks, `test_procs` proves it). A new helper uses `runtimes.find_helper` and `check_helper`.
- **CI is rationed.** The public repository's runners are free, but full runs are long and the private presets
  repository's are not. Test locally first (Windows, then Linux in WSL) and push batches that already pass. Pull requests run Linux and Windows (only the
  documentation tests when just Markdown changed); the label `full-ci` runs macOS and the oldest Python, once per
  milestone before merging. Every run ends in `ci-ok`, the check the `main` ruleset requires with DCO. Every
  milestone ends with a full run on all three OSes.
- **No full preset testing, for now (decided 2026-11-11).** It takes too long and costs too much CI. CI does not
  read the private presets: do not add the `PRESETS_DEPLOY_KEY` secret, so those tests skip there. Locally, after a
  preset change, run `tests/test_private_presets.py` (seconds) and the test module of each tool whose presets changed,
  not every preset of every tool. The full local suite runs them anyway when the packs are configured; that is
  fine when a full run is due.
- **Dependencies live in `pyproject.toml` and `uv.lock`.** After changing them run `python scripts/setup_env.py --lock`,
  which also exports `requirements.lock.txt` for machines without uv.
- **Native helpers** (`native/<helper>`, see `native/README.md`):
  - Pin every upstream in `native/deps.lock.json`. `native/build.py` installs the executables into
    `src/atlas_toolkit/bin/` (gitignored) and regenerates the tracked notices in `src/atlas_toolkit/licenses/`.
  - A helper change bumps the helper's version, its protocol check in the tool's `backend.py`, and the cache key.
  - The developable helper is GPL: keep its corresponding-source archive building and shipping.
- **The depth environment installs from the app** (`geom/depth_runtime.py`, pins in `depth_runtime.json`). The suite
  tests it against a stand-in uv (`tests/test_depth_runtime.py`, seconds); the real download runs only for releases
  (`build.yml` with `depth: true`) and by hand on an NVIDIA machine (`docs/PACKAGING.md`). After changing a pin, bump
  its `revision` and run `scripts/dev/depth_install_test.py`.

## Commands

```bat
.venv\Scripts\python -m unittest discover -s tests
.venv\Scripts\python -m atlas_toolkit self-test   :: --strict: what a release must pass
.venv\Scripts\python scripts\check_frozen_gui.py --exe <app>   :: does a build really draw? (counts colours in the 3D pane)
.venv\Scripts\python scripts\audit_bundle.py <folder or .app>  :: glibc and macOS floors, helpers, notices, signature
.venv\Scripts\python -m atlas_toolkit --tool <id> <files> --snapshot shot.png [--theme light] [--draft] [--view After]
.venv\Scripts\python scripts\build.py            :: or --onedir, --skip-tests, --skip-native, --no-splash
.venv\Scripts\python native\build.py             :: C++ helpers only: --only stripes,remesh  --check  --no-fetch
.venv\Scripts\python scripts\package.py          :: the installers into dist\installers, only when asked (docs/PACKAGING.md)
git tag vX.Y.Z && git push origin vX.Y.Z         :: only when asked: a draft release (private until published) with every installer
python scripts\install_test.py <package> --container   :: installs it for real, so containers and CI only: proves it, removes it
.venv\Scripts\python scripts\new_tool.py <folder> --kind svg|mesh|mesh2svg
python scripts\doctor.py                         :: is this machine ready to develop and build? (any OS)
python scripts\setup_env.py [--lock]             :: the dev venv (.venv), with uv or pip; --lock refreshes both locks
.venv\Scripts\python scripts\setup_ml.py [--user] [--check]   :: the depth environment (the app's own installer): .venv-ml here, or the per-user folder
.venv\Scripts\python -m atlas_toolkit components [install|check|remove] depth   :: the same as Setup › Components › Install…
python scripts\dev\depth_install_test.py --app <console exe> [--gpu]   :: the real depth download: releases (CI) and by hand only, never in the suite
.venv\Scripts\python scripts\dev\run_tests.py    :: each test module in its own process, as CI runs them
.venv\Scripts\ruff check .                       :: lint only; never `ruff format`
.venv\Scripts\python scripts\dev\param_sweep.py --tool <id> [--models v17,augustus,julia] [--quick]   :: 3D tools' settings
```

`scripts/dev/` holds the QA and validation scripts (`validate_*.py`, `param_sweep.py`). They read reference models
from the studio workspace through devpaths and write to `build/qa/`.

**3D tools** (Contour Lines, Sketch Hatching, Stipple, Twist, Melt) port the studio's Blender pieces
(`projects/blender3d_project` in the workspace). Keep their tuned values (`docs/PARAMETERS_3D.md`); the tests
compare against the pieces' own functions when the workspace is configured. Melt needs Blender (`Tool.requires`):
its tests run the real Blender when it is installed and a fake server otherwise. On the command line an axis value
that starts with a minus needs `=` (`--front=-y`).

The legacy scripts these tools replaced live in the workspace's `code/Python/_old/` (see each spec's `legacy`).
`tests/test_mesh_parity.py` lifts their functions with `ast` to check that the ports still match.

## History

The public repository starts at 2.2.0, its first public release. The history before it, and earlier dev logs,
agent plans and QA material, are kept privately by the maintainer (locally in `_devarchive/`, which git ignores).
