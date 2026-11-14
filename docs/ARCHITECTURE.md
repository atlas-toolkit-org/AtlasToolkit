# Architecture

```
src/atlas_toolkit/
  core/      no Qt. What a tool is and how it runs.
    tool.py        Tool, Inputs, Output, Result, Stat, InputInfo: the contract
    params.py      parameter types; one schema → form, CLI, presets, saved settings, metadata; renamed keys and
                   values (former=, former_values=) and lenient reading for old files; shared groups:
                   paper_params, camera_params (plotter.vision's camera), frame_params (up, front, stage sets)
    runtimes.py    external programs a tool requires or can use (Tool.requires / Tool.optional): finding them,
                   the notice, skipping in tests, and the install guide Setup › Components shows
    procs.py       every child process starts here: a clean environment, and it dies with its job on every OS
    registry.py    finds built-in tools (atlas_toolkit.tools.*) and plug-in folders; broken tools are listed
    job.py         Job, JobContext (progress, log, cancel, draft), execute(): the one place an engine is called
    runner.py      thread lane (light work, stale jobs dropped) + worker-process lane (heavy work, kill on cancel;
                   one worker or more side by side, Setup's "heavy jobs at once")
    io.py          expanding dropped files/folders, output names, never overwriting an input
    settings.py    JSON settings per tool (moves 2.0's presets out of them once)
    presets.py     presets as data: .atlaspreset files, .atlaspack packs, your own store, the library; apply/match;
                   pack verification and signing; install plans for packs, preset files and .zip files (docs/PRESETS.md)
    ed25519.py     pure-Python Ed25519 (RFC 8032) that checks preset pack signatures
    preset_keys.py the publisher keys the app trusts to sign packs (public halves only)
    prefs.py       the app's options (Setup) in app.json, checked; outputs that exist (overwrite / ask / keep both
                   with a date-time stamp); exporting and importing all settings as one file
    history.py     history.jsonl of finished runs
    cli.py         command line generated from the specs
    selftest.py    run every tool on a sample (in the worker for heavy tools), used after every build
    workflow.py    workflows: graph of steps, Check, chain names, content-addressed cache, run_workflow (a Job)
    paths.py       per-platform folders, portable mode, reveal/open files
  geom/      shared geometry, no Qt
    svg.py         SVG reading (re-exported from the Overlap Cleaner's engine), drawings for previews,
                   signing, <metadata> run records, default SVG inspect
    svgout.py      writing new mm SVGs, joining and ordering polylines
    drawing.py     Drawing / Pen: polylines per pen in mm, plot statistics (ink, travel, lifts, time)
    mesh.py        trimesh loading and cleaning, preview decimation, signed saving, default mesh inspect
    paper.py       paper sizes, fitting
    image.py       photos: EXIF-aware loading, the default image inspect, raster preview views
    depth.py       depth maps: the GPU runtime client (cached per photo/model/detail), depth files, rough depth
    polylines.py   graph trails: thin edge pixels or mesh edges into polylines drawn once
    camera.py      plotter.vision's orbit camera and the paper frame it looks through (all 3D drawing tools)
    frame3d.py     a model's canonical frame: up, front, extra turn, scale; one frame for a whole stage set
    stages.py      stage sets (…_stage_NN + stages.json): finding them, publishing them all or nothing
    raycast.py     embree driven directly: batched hits, occlusion with a ray length, visibility + bisection
    curvature.py   curvature and its derivatives, suggestive contours, ridges and valleys (the Blender pieces')
    shading.py     the ray-cast trace on the paper grid: G-buffer, AO, soft shadows, fields, feature lines
    sketch.py      drawing on the paper grid: polyline utilities, silhouette from the hit mask, coverage
    blender.py     the Blender runtime's client: finding Blender on each OS, the warm worker, cancel
  ml/        run by the GPU runtime's own Python, never imported by the app
    depth_server.py  JSON lines on stdin/stdout: loads a depth model (torch, transformers), writes depth .npy files
  blender/   run by Blender's own Python (the app reads only melt_gn's constants)
    server.py        JSON lines (tagged) on stdin/stdout: builds meshes from .npz, evaluates node groups, writes .npz
    melt_gn.py       the melt as Geometry Nodes (from the Blender pieces, generalised); wave_gn.py: their twist
  ui/        PySide6
    app.py         start-up, splash, snapshot mode
    shell.py       main window: top bar, sidebar, pages, footer, log, shortcuts, palette
    about.py       the About window: tools, command line, shortcuts, changelog, credits, diagnostics
    setup.py       the Setup window (gear): options, optional components, folders and housekeeping
    preset_install.py  loading preset files and packs, from anywhere: shows the install plan, asks, installs
    setup_presets.py  Setup › Presets: preset packs (and whether they are Official) and every preset, to manage
    sidebar.py     the tool list: foldable groups, full or icons only (the Workflows library mirrors it)
    tool_page.py   the generic tool page
    home.py  activity.py  palette.py  bridge.py (runner events → Qt signals)  context.py
    workflow/      the Workflows page: canvas (steps, ports, arrows), step library, inspector, page (check, run)
    theme/         tokens.json and icons.json (synced from the studio design system), Geist fonts, qss.py stylesheet
    widgets/       spin (the one number field: triangles, steps that fit the parameter), param_form, file_queue, preview (stage), plot_view (2D), mesh_view (3D), common
                   (a tool's own interactive view, spec `viewer`, is hosted by the stage as a "widget" view)
  tools/     one folder per tool = one Atlas code unit
```

## Principles

- **Tools are data plus a pure function.** A tool never builds widgets or parses arguments. Any part of the app
  (form, CLI, presets, history, self-test, contract tests) works for every tool, including future ones and
  plug-ins.
- **Heavy work never runs on the UI thread.**
  - Light tools (the SVG ones) run in a worker thread. A newer analysis of the same file drops a queued older one.
  - Heavy tools run in a warm worker process. A pure-Python loop cannot freeze the window. A crash loses one job,
    not the app. Cancel first asks the engine to stop, then kills the worker after 1.5 s.
- **Outputs are reproducible and traceable:**
  - seeded randomness;
  - settings stored in SVG `<metadata>` and in the history;
  - every output signed for Atlas.
- **Originals are never changed.** Outputs are named by template, and the app asks before overwriting.
- **Geometry is shared.** All SVG tools read SVGs through the Overlap Cleaner's parser: transforms, units, curves
  and pens are treated the same way everywhere. Mesh tools share loading, cleaning, saving and previews.
- **One look.** Colours, type and icons come from the studio design system (`scripts/sync_design.py`), as in the Atlas
  gallery. Dark is the default, with a light theme; artwork shows on paper or blueprint.

## Atlas

- **Code units:** the gallery reads `[tool.atlas-toolkit]` in `pyproject.toml`. Each tool folder is a code unit;
  the rest is one unit, the repository root (the gallery reads this repository as an extra code root).
- **Linking outputs to units:**
  - SVG outputs carry `<desc>tool-id</desc>` first;
  - mesh outputs carry `atlas-toolkit:<tool-id> <version>` in their header;
  - output names follow each tool's template.

## The GPU runtime

PyTorch with CUDA is about 3 GB, so it never goes into the exe. The app installs it for the user instead:
`geom/depth_runtime.py` is the installer, the module named by the depth runtime's `installer` field (core/runtimes.py,
`INSTALLER_API`), so Setup › Components (`ui/runtime_install.py`) and `atlas-toolkit components` (`core/components.py`)
drive it without naming it. It detects the card (`core/gpu.py`), picks a PyTorch build from the pins in
`depth_runtime.json`, downloads uv and a standalone Python (both pinned and SHA-256 checked; uv never installs Python, so it makes no links), has uv fetch the packages into
`ml-runtime.partial`, has the new environment prove itself (`depth_server.py --probe`), and only then renames it to
the per-user `ml-runtime` folder (`core/paths.ml_runtime_dir`). An idle worker is recycled first
(`Runner.recycle_process_lane`), so the old environment's server lets go of its files. `scripts/setup_ml.py` runs the
same installer for developers, into `.venv-ml` in the checkout. `geom/depth.py` finds its Python and starts `ml/depth_server.py` lazily, inside the worker
process, and keeps it alive between jobs: one model on the GPU at a time, switched on request. Requests and answers
are JSON lines; depth comes back as an `.npy` file in the cache, so the app needs no torch at all. Without the
runtime the tool says how to install it, and depth files or the rough depth still work.

## The frozen app

`scripts/build.py` runs PyInstaller on one recipe for every OS, `packaging/atlas_toolkit.spec`, and publishes to `dist/` only if every check passes. It makes `Atlas Toolkit.exe` with a splash screen on Windows (or a folder with `--onedir`), `Atlas Toolkit.app` on macOS, and a folder `atlas-toolkit/` on Linux. Installers are made from these by `scripts/package.py` (`docs/PACKAGING.md`).

- **Launcher:** `scripts/launcher.py` calls `multiprocessing.freeze_support()` first, so the worker process starts from the same program.
- **Command line:** the program attaches to the console it was started from. On Windows, which has windowed and console programs, the companion `atlas-toolkit.exe` makes a terminal wait for it.
- **Data files:** `ml/depth_server.py` and `blender/*.py` ship as real files, since other interpreters run them.
- **Checks:** `self-test --strict` runs every tool inside the frozen app, and starts every helper, so a missing hidden import shows up at build time. `scripts/audit_bundle.py` reads the result without starting it. `scripts/check_frozen_gui.py` counts the colours in a real 3D view, because a blank pane passes everything else.

## Running on three OSes

Platform differences stay behind `sys.platform` checks in a few places, and `tests/test_portability.py` catches on Windows what would break elsewhere. `docs/dev/multi-os-port.md` has the evidence behind each of these.

- **Child processes** go through `core/procs.py`. A frozen Linux app sets `LD_LIBRARY_PATH` for itself, and a child must not inherit it. A worker leads its own process group on POSIX, and a job object does that job on Windows, so Cancel and a crash both leave nothing running.
- **Folders** come from `core/paths.py`: each OS's profile folders, or portable mode. A `.app` and an AppImage count as one file, so nothing portable may live inside them.
- **OpenGL** is the trap. PyOpenGL works out its library by itself and can end up with a different one from Qt's, so `_match_opengl_to_qt` in `ui/app.py` runs before anything imports it. On Linux it picks EGL or GLX to match Qt's platform, and on Windows it follows Qt to its software renderer when there is no usable driver. Every 3D canvas mixes in `GuardedGL` (`ui/widgets/gl_guard.py`), so a driver that can't draw gets a message in place of the view and not an error for every frame.
- **Desktop conventions:** `ui/keys.py` writes shortcuts the Mac way there, `ui/widgets/gl_nav.py` adds trackpad gestures, and `FileOpener` in `ui/app.py` takes macOS's file-open events.

## External runtimes

A tool lists the programs it needs in `Tool.requires`, and those it can use but runs without in `Tool.optional`
(`core/runtimes.py` knows them: today `"blender"` and `"depth"`, the PyTorch environment). Each runtime also carries
its install guide (`why`, `steps`, `commands`, `notes`), shown in Setup › Components with the tools that use it. Nothing in
`core/` or `ui/` names a tool:
- the tool page shows a notice per missing program, with **Locate …** (saved as `blender_exe` in `app.json`, through
  the app's own settings object) and a download link, and keeps Run and Preview off until it is found;
- `self-test` and the contract tests skip the tool with the reason instead of failing;
- `run` on the command line refuses with the hint, `describe` says where the program is.

## The Blender runtime (Melt)

No venv: the installed Blender brings its own Python (3.13) and numpy. `geom/blender.py` finds Blender
(`ATLAS_TOOLKIT_BLENDER`, the app setting, PATH, then each OS's usual places, newest first) and starts, inside the
worker process and only when a job needs it:

```
blender -b --factory-startup -noaudio --python-exit-code 1 --python blender/server.py -- --protocol 1
```

- **Protocol:** JSON lines. Blender's C code prints on stdout too, so the server keeps a private copy of stdout and
  sends every other print (Python and C, via `dup2`) to stderr; its lines are also tagged (`ATLAS1 `), so
  untagged output is only kept as context for error messages. `ready` reports the Blender version and any missing
  node type; requests are `melt`, `wave` (tests), `check`, `ping`.
- **Data:** meshes go both ways as `.npz` in `cache/blender` (the toolkit loads, cleans and normalises them; the
  server builds objects with `foreach_set`, no importer). All stages of a sequence are evaluated from one object,
  so drips seeded on the rest shape grow in place.
- **Warm:** the server stays up between jobs (a cold start is ~2-8 s).
- **Cancel:** the client polls the job's cancel flag while it waits, asks the server to stop between steps and kills
  Blender if it does not answer within 0.4 s. `core/procs.py` ties Blender to the worker, so it dies with it on every OS, and
  the server exits as soon as its stdin closes.
- **Frozen app:** `blender/*.py` ship as real files (`DATA` in the spec), like `ml/depth_server.py`; they import `bpy`
  only inside functions, so collecting the package's submodules never needs Blender.

## 3D drawing pipeline

The drawing tools share one path from a mesh to the paper:
1. **Frame** (`geom/frame3d`): up, front and an extra turn put the model upright with its front at -Y; it is
   centred and scaled (largest side 100, plotter.vision's units). A stage set (`geom/stages`) shares one frame: the
   union of its stages' bounds.
2. **Camera** (`geom/camera`): plotter.vision's orbit camera through the paper's drawing area. The Aim tab is
   Plotter Vision's live view (`tools/plotter_vision/aim.py`), fed the same frame, so its numbers are the engine's.
3. **Rays** (`geom/raycast`): embree, every ray of a step in one batch: visibility (Contour Lines), a G-buffer per
   paper pixel, ambient occlusion and soft shadows (`geom/shading`, Sketch Hatching and Stipple).
4. **Paper** (`geom/sketch`, `geom/svgout`): strokes in mm, one layer per pen, ordered, signed.

`geom/shading` works on the model scaled to 2 units tall (the Blender pieces' units) so their tuned lengths and
curvature thresholds keep their meaning; smoothing pass counts scale with the mesh's edge length.

## FlowRep Sketch and `src/flowrep3d`

`src/flowrep3d` is a self-contained library (an independent implementation of FlowRep, Gori et al. 2017) with its own
command line (`python -m flowrep3d`). The tool `tools/flowrep_sketch` is a thin adapter over it. So that the library
runs on its own, `flowrep3d/_atlas/` carries copies of a few `geom/` modules (camera, paper, raycast, curvature,
frame3d); a fix in one of those `geom/` modules may need copying there too. Its notes are in `docs/flowrep/`
(`ATLAS_INTEGRATION.md`, `RESEARCH.md`, `THIRD_PARTY.md`, `API.md`).
