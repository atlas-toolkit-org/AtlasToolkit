# Changelog

## 2.3.0 (2026-11-13)

**The depth environment installs from the app**
- **One button:** Setup › Components › **Install…** sets up Photo Hatching's depth environment (PyTorch and the
  model libraries) for anyone, including people who installed Atlas from an installer. They no longer need Python of
  their own, a terminal, or the source tree. Before anything is downloaded, the installer window shows:
  - the graphics card it found and the build it chose, with the reason;
  - the download size, the disk space needed and kept, and the expected time;
  - the folder it installs into;
  - a tick, note or cross for disk space, the folder, and the download sites;
  - any other environment that would be used instead.
- **While it installs:** each stage and one progress bar with the size, speed and time left. Details shows uv's own
  output, and everything goes to a log file. Cancel removes what was begun. The app stays usable, and the
  Components card shows the progress.
- **When it ends:** it says what it proved (the PyTorch version and the device it runs on), where the environment
  is, its size and the time it took. A failure says why, in plain words: the network, a proxy's certificate, a full
  disk, a locked file, or a GPU check that failed (with the next build to try). It also offers Copy details and Try
  again. **Check** proves the environment still works, and **Remove** deletes everything the installer put there.
- **The card is found automatically** (new `core/gpu.py`): `nvidia-smi` gives the card, its driver and compute
  capability. Without it, Windows' display adapters or Linux's PCI devices still tell a card with a missing driver
  (or the nouveau driver) from no card at all. The build:
  - **CUDA 13.0** for Turing and newer with driver 580 or newer;
  - **CUDA 12.6** for Maxwell to Volta, or drivers 528 to 579;
  - **CPU** otherwise, with what would allow the GPU build;
  - **PyTorch for macOS** on a Mac.
- **The environment has to prove itself first.** It is built in `ml-runtime.partial` and must show that PyTorch sees
  the GPU, has code for its architecture and calculates correctly on it, or it is not put in place. A half-made
  environment is never where the app looks, and the old one stays until the new one works. Before the swap, an idle
  worker is restarted (`Runner.recycle_process_lane`), so the running depth server lets go of its files.
- **Pinned:** `geom/depth_runtime.json` pins uv 0.13.6 (checked by SHA-256), Python 3.13, PyTorch 2.14.1 for each
  build, and the model libraries at exact versions. uv and its Python stay in `ml-runtime-base`, beside the
  environment.
- **Command line:** `atlas-toolkit components` lists what is found and the card, and `components install / check /
  remove depth` do the same as the window. This works on a server or over SSH, with the same summary, stages and
  result.
- `scripts/setup_ml.py` now runs this installer (into `.venv-ml`, or `--user`). The pasted commands in Setup are gone
  (on Windows they only worked in Command Prompt), and so is the duplicated package list.
- `ml/depth_server.py` gains `--probe` (versions, device, a sanity check on the GPU, with nothing downloaded) and
  `--fetch MODEL`.
- **Tests:** `tests/test_depth_runtime.py` tests detection, the build table, the plan and the whole install against
  a stand-in uv, in seconds. The real download runs only for releases: `scripts/dev/depth_install_test.py` runs on all
  three OSes on the frozen app (`build.yml` with `depth: true`, which `release.yml` passes), and by hand on an NVIDIA
  machine.

**Setup › Components folds each component's details**
- A component's card shows its name, status, buttons and where it was found; **Details** unfolds which tools use it,
  why, how to install it and the notes. Cards start folded, and the ones left open are remembered.

**Photo Hatching 1.1.0: a depth model's first download shows its progress**
- When a model's weights (about 1.3 GB) download on first use, the status bar shows how much has arrived, the speed
  and the time left, or that it is waiting for the network. Before, it stayed at 5 % "depth". The log says where the
  model goes, and when it is done that it will not download again.
- A failed download says how far it got and what to check (the connection, a proxy, a full disk). Cancel stops the
  download and deletes its partial file, which Hugging Face never reuses. Partial files left by an earlier killed
  download are cleared before the next one. A download that keeps going no longer gives up after 30 minutes; one
  that receives nothing for 4 minutes stops and says so.
- Quick previews never start a download. Until the model is there they use the rough depth, with a warning, and
  Hatch downloads it.
- Models download over plain HTTPS, not Hugging Face's Xet transfer, which writes in bursts and left the bar
  standing still. The installer's "download the model now" uses the same method. A user's own `HF_HUB_DISABLE_XET`
  setting still wins.

## 2.2.0 (2026-11-12)

**Open source: Atlas Toolkit is free software under GPL-3.0-or-later**
- **The licence:** `LICENSE` holds the GPL version 3, and `COPYRIGHT.md` the notice ("João Gonçalves (sci.co.plot)
  and contributors") and what the licence does not cover: third-party parts, preset packs, your own outputs, and the
  name and logo (`TRADEMARKS.md`).
- **The metadata agrees:** `pyproject.toml` (an SPDX licence expression, license files, project URLs), the deb and rpm
  packages, the Windows installer's publisher and copyright, and the About window. `tests/test_licence.py` checks that
  they agree.
- **About:**
  - A new **Licence and support** card, with links to Ko-fi and atlas-toolkit.org.
  - The footer names the licence.
- **Leaner bundles:** libigl's GPL and AGPL modules (CGAL, TetGen) and Shewchuk's Triangle, which is not open source,
  stay out of every bundle. Atlas never used them. `scripts/audit_bundle.py` also fails a build that carries them, or
  one that lacks the app's own `LICENSE`.
- **Notices:**
  - `THIRD_PARTY_NOTICES.md` turns its licence audit into a compliance section.
  - The developable helper is confirmed GPL-2.0-or-later, from its upstream file headers.
- **Presets:** `docs/PRESETS.md` describes the tiers (Basic free; Essentials and Complete paid), the official packs'
  licence, and where to get them. Paid preset values never enter the repository.
- **Preset leak check:** `scripts/dev/preset_leak_check.py`, also run by `tests/test_private_presets.py` when the
  packs are configured, finds paid preset values in any tracked file. What it found is now out:
  - 19 example workflows held a paid preset's settings. Each keeps its choices, and its tuned numbers are back to
    the tool's defaults.
  - A Bend command line equalled a preset.
  - Five docs spelled out presets' values (Line Simplify, Line Smooth, Raster DEM, Developable Surfaces, 3D
    parameters) and now describe them in words.
  - A dev validation script reproduced a preset.
- **Ready to publish:**
  - The exe's company name is sci.co.plot.
  - The agent notes and the dev config example no longer name private repositories or folders.
  - CI comments no longer speak of a private, billed repository.
  - The guard tests no longer hold personal identifiers. The machine-path check now finds any user or home
    folder.
  - `.claude/` and `.ruff_cache/` are ignored.
- **CI for a public repository:** every pull request ends in one required check, `ci-ok`.
  - Markdown-only pull requests run the documentation tests in a couple of minutes instead of the full suites.
  - `cache.yml` keeps the native helpers' build cache warm on `main`, so pull requests stop compiling them from
    scratch.
- **New files:** `CONTRIBUTING.md` (DCO sign-off), `CODE_OF_CONDUCT.md`, `SECURITY.md`, `TRADEMARKS.md` and
  `.github/FUNDING.yml` (Ko-fi). CI reads the presets repository's name from a variable only.
- **The public repository:** github.com/atlas-toolkit-org/AtlasToolkit.
  - Pull requests need a DCO sign-off (`.github/dco.yml`) and a green `ci-ok`.
  - The README shows CI, licence and Ko-fi badges, and counts 48 tools.
  - The install guides say where to download each package.

## 2.1.1 (2026-11-10)

**Preset packs: manage, sign and install them; Setup and About narrow with the window**
- **Setup › Presets** manages it all:
  - The preset packs, with each one's tier, version and status.
  - Every preset, by tool group and tool, with search. Each is marked Mine, Official, Outdated or Hidden.
  - Filters that combine: tool group, type (Mine, Official, Trusted publisher, Not verified), source (My presets or one pack), state (Outdated, Hidden, Copied from a pack, In several tiers, Has notes) and tag. Search also reads tips and tags; a count says how many presets the filters let through, and Clear filters resets them. Each choice shows how many presets it would list, and one that would list none is greyed out, so a filter such as In several tiers (with one tier installed) no longer empties the list without a word.
  - Actions: save yours to a file, copy a pack's preset to yours, rename or delete yours, hide or show a pack's, remove a pack.
  - Deleting a pack preset hides it instead (it belongs to the pack), and both ask first.
  - Each preset menu has **Manage presets and packs…**, which opens the tab on that tool.
- Official packs are signed. A pack signed by a publisher key the app trusts, and unchanged since, shows **Official**; an edited copy shows **Modified**. The check is a pure-Python Ed25519, so nothing locks and nothing goes online, and the bundle gains no new dependency.
- The installers register `.atlaspreset` and `.atlaspack` on Windows, Linux and macOS, so opening one loads it (after asking).
- A pack is one file that holds everything needed to install and check it: its presets, licence, signature and the signer's public key. A `.zip` of a pack's folder, or a `.zip` holding packs and preset files (a store download with a readme), loads the same way, from the preset menu, Setup › Presets, a drop or the command line.
- Loading shows what the files hold and what loading them would do before anything changes:
  - install, update (version 1 → 2) or replace;
  - already installed, or older than the installed copy;
  - already included in a higher tier you have;
  - cannot install, because it needs a newer app.
  
  Unsigned or modified packs, and replacing an Official copy with a lesser one, need **Install anyway**. `atlas-toolkit presets inspect` shows the same without installing, and `presets import --force` overrides.
- Packs signed by other publishers install as **Signed, publisher not trusted**. Setup › Presets (**Trust publisher…**), the load dialog and `presets import --trust` can trust their key, shown as a fingerprint to compare with the publisher's site. Their packs then show **Trusted publisher**.
- Official packs can move to a new signing key without an app update: a key the app knows endorses the new one, and the endorsement travels inside each pack.
- Fixed: About › Credits did not narrow with the window. The Built with list stayed two parts a row on one line each, so a narrow window cut off its right side. It is now one part a row and wraps, and the header's tagline wraps too.
- Fixed: Setup › Components and Setup › Presets did not narrow with the window either: long button rows cut off their right side. Their buttons now wrap onto a second line, and a component's buttons sit under its name.
- For developers: `scripts/dev/preset_sheet.py` renders a tool's pack presets and draft candidates side by side, with how far apart they look, for authoring and reviewing pack presets.
- Tool versions are unchanged: no tool's settings, outputs or defaults changed.

## 2.1.0 (2026-11-07)

**Presets are data: save and load preset files, load preset packs**
- Presets are files now, not part of the app. Your own presets live in `presets/mine/<tool>.atlaspreset` in the settings folder. Preset packs (`.atlaspack`, one file per tier of a collection such as Essentials or Complete) add their own heading to each tool's Presets drop-down, and owning two tiers lists each preset once. See `docs/PRESETS.md`.
- The Presets drop-down gains a menu: **Save these settings to a file**, **Save my presets to a file**, **Load presets from a file** (an `.atlaspreset` joins My presets, renaming a clash; an `.atlaspack` installs the pack), **Copy this preset to My presets** (pack presets are copied before they can be changed or saved to a file) and **Delete a preset of mine**, which now asks first, as saving over one of yours does. Dropping preset files on the window loads them too.
- The app ships with no presets, so every drop-down starts empty. Every tool starts from the same values as before: the 23 tools that started from a suggested preset now have its values as their defaults.
- Presets survive tool changes. A renamed setting declares its old name (`former=`) and a renamed choice its old values (`former_values=`), for presets, saved settings and workflows alike. A preset from another version applies what still fits, and the page names what did not.
- The command line: `--preset` names a preset from your own or a pack (`source:name` when two share a name), `--preset-file` takes one from a file, and `atlas-toolkit presets list | import` shows and loads them.
- Upgrading moves the presets you saved in 2.0 (inside `tools/<id>.json`) into the new store once, and imports of 2.0 settings bundles bring theirs along. Exported settings now carry your presets but never packs.
- Fixed: saving a preset dropped choice values that read "auto" (Up axis, Front).
- Tool versions are unchanged on purpose: no tool's settings, outputs or defaults changed, so saved workflows open without a version warning.

## 2.0.5 (2026-11-05)

**Skeleton Shell: a new tool empties a model and keeps only a structure on its skin**
- Skeleton Shell 0.1.0 (mesh to mesh) turns a model into a wire or plate sculpture with nothing inside, so a drawing sees through the near side to the far one. It reads the model as a surface, so the input need not be closed.
- Five structures: **Wire Cells** (geodesic Voronoi walls), **Trimesh** (regular to organic triangles), **Flow Strands** (along the form's curvature, upright or in spirals), **Ribs & Stringers** (flat ring frames with lightening holes, straps, fins or rods, with stringers and a keel) and **Cut Plate** (a pierced sheet, sharp or rounded).
- A **Second layer** adds another structure, and **Acts on** keeps part of the skin as a thin shell that lets go cell by cell across the feather. Straps, fins or rods; joint swell, taper, hand wobble and overrun. **Fuse into one solid** makes it printable.
- Eighteen presets, from Wire Dancer to Airframe. No Blender or new dependencies. See `docs/SKELETON_SHELL.md`.

## 2.0.4 (2026-11-04)

**Crumble: a new tool brings part of a model down like a building**
- Crumble 0.1.0 (mesh to mesh) fractures a side, a round patch, a painted area or the whole model into closed blocks of rock, shards, slabs or bricks. The body keeps a jagged crater as deep as **Hollow depth**, with tapered cracks spreading around it.
- The blocks let go in a staggered order and fall in a new built-in rigid-body solver, colliding with each other, the body and the floor. **Collapse** picks the moment, from just cracked to settled rubble, and the same seed gives the same event.
- Output is loose closed solids where the collapse left them, or with **Fuse into one solid** one printable solid, with thin bridges to anything floating and an optional base plate.
- Ten presets, from Rock Cluster to Demolition. No Blender or new dependencies. See `docs/CRUMBLE.md`.

## 2.0.3 (2026-11-04)

**Mesh Shatter: hub connectors tie the shards to one central point**
- Mesh Shatter 4.1: **Connect shards to a hub** ties a chosen percentage of shards (random, largest, smallest, nearest or farthest) to one point; the others float free as debris. It is off by default, and shards land in the same places either way.
- Connectors are cylinders, square bars or organic limbs. Organic ones form a **Shared tree**, whose trunks fork again and again, or **Radial stems**, with optional extra branches that grip each shard.
- **Hub position** defaults to the roomiest point near the middle of a closed model, which is always inside it, unlike a centroid. **Hub core** is a gnarled knot, a clean sphere, a miniature of the model or nothing visible.
- **Shard thickness** above 0 makes every shard a solid and fuses the connected ones with the hub into one closed piece, ready to print. At 0 shards stay open surfaces and the connectors only touch them.
- New presets: **Living Roots** and **Taut Tethers**.

## 2.0.2 (2026-11-04)

**Stair Sculpt: a new tool turns a model into a staircase**
- Stair Sculpt 0.1.0 (mesh to mesh) makes linear, turning (quarter turns or switchbacks) or helical stairs in three modes. **Carve** opens a cavity on a chosen side or where you paint and builds one staircase, or an ensemble of flights, landings, piers and arches. **Treads** keeps only the model inside the steps, so its silhouette outlines each tread. **Cut** cuts the steps into the body, as a flight up it, a groove around it or a square ramp.
- Fifteen presets. Lips, parapets and four kinds of underside give crisp parallel edges for Plotter Vision. Every result is one closed solid, in the model's own units. See `docs/STAIR_SCULPT.md`.

## 2.0.1 (2026-11-04)

**Surface Texture: automatic wrapping no longer depends on the size of the model**
- Surface Texture 0.1.1: a model of a few hundred units took minutes or crashed the unwrap helper, and a very small one could end in "Some faces could not be unwrapped". xatlas's limits are absolute, so the helper (0.2.0, protocol 2) now centres every model and unwraps it at one working size and resolution, returning UVs in the model's own units. Graphic Scales on a closed 15,000-face head now takes about 50 s. UV charts cached by earlier versions are not reused.

**A new README**
- `README.md` is now the front page and a catalogue of every tool, with a gallery of examples. The old text moved to the user guide, `docs/TOOLS.md`, and `tests/test_readme.py` keeps the catalogue complete.
- `test_procs` and `scripts/install_test.py` no longer fail at random on Windows when they read a file at the moment it is renamed into place.

## 2.0.0 (2026-11-04)

**Atlas Toolkit runs on Windows, Linux and macOS, with an installer for each**

One codebase now builds, tests and installs on all three. 1.19.3 to 1.24.0 did the work, and this release completes the documentation. See `docs/PLATFORMS.md` for what has really been proven on each OS.
- **Windows and Linux are fully supported.** macOS is built and tested automatically but not by hand: nobody has tried it on a real Mac, so `docs/dev/macos-verification.md` is the checklist for doing that.
- **Instructions:** `docs/install/` for each OS, `docs/BUILDING.md` from a clean machine to a tested build, and `docs/PACKAGING.md` for how installers are made and tested.
- **About:** Command line now says how to start it on each OS, a built app is called that instead of a "one-file app", and the credits add Python and PyInstaller's bootloader.
- **Linux notices:** a build now records the package and licence of each system library it carries, with their licence texts, in `licenses/linux-system-libraries.txt`. `THIRD_PARTY_NOTICES.md` also lists the libraries that come inside the Python packages.
- **Draft releases:** pushing a version tag runs every gate and makes a private draft release with all the installers. It never publishes (`docs/PACKAGING.md`).
- `requirements.lock.txt` no longer records the folder of the machine that made it, and `test_portability` now also checks the documents and the lock for machine paths.

Nothing is signed by a trusted certificate yet, so Windows may warn and macOS asks for **Open Anyway**. That waits for the decision on how the app will be distributed.

## 1.24.0 (2026-11-04)

**A disk image for macOS, and an AppImage for Linux**
- **macOS:** a DMG to drag into Applications, ad-hoc signed. `docs/install/macos.md` explains the first start, which macOS
  blocks until you choose Open Anyway, and how to add the command-line link. It is tested automatically on GitHub's Macs but
  not by hand yet: `docs/dev/macos-verification.md` is the checklist for a real Mac.
- **Linux:** an AppImage with the same folder the deb and rpm install, for running with no administrator rights. It goes
  through the same install test on the same six distributions.
- `LICENSE` and the third-party notices are now inside every bundle, so even a dragged `.app` or a single exe has them.

## 1.23.0 (2026-11-04)

**Real installers for Windows and Linux**
- **Windows:** an installer that needs no administrator by default, with an option to install for all users. It adds a Start
  Menu entry, opens `.atlasflow` files, and can add `atlas-toolkit` to PATH. It upgrades in place, and uninstalling leaves
  your settings alone.
- **Linux:** deb and rpm packages that install to `/opt/atlas-toolkit` with a menu entry, icon, the `.atlasflow` file type and
  the `atlas-toolkit` command. They declare the system libraries the app needs, so the package manager brings them.
- **Each installer is tested by really installing it** (`scripts/install_test.py`): every tool's self-test, the 3D view on X11
  and Wayland, nothing left running after the window is killed, a clean uninstall, and on Linux that the declared
  dependencies are complete. It runs on clean Ubuntu, Debian, Fedora and Rocky systems.
- Instructions are in `docs/install/`, and how the installers are made is in `docs/PACKAGING.md`.

## 1.22.0 (2026-11-04)

**At home on each desktop**
- **Opening files from the system works on macOS:** Finder's Open With and a drop on the Dock icon arrive as events, at start
  or later. A workflow opens as a workflow, and other files go to the tool that takes them.
- **A real macOS menu bar,** with About, Setup and Quit where people look for them, and Close Window. Other systems keep the
  window's own header, with no menu bar.
- **Shortcuts are written the Mac way there** (⌘K instead of Ctrl+K) in tooltips and the About window's list.
- **Trackpads:** a pinch zooms the 3D views, and on macOS a two-finger swipe orbits and with Shift pans. Windows and Linux
  touchpads keep what they do today. Not tried on real hardware yet.
- **Blender is found in more places:** on macOS any `Blender*.app` in Applications or `~/Applications`; on Linux also
  `/usr/local/bin`, Flatpak and unpacked tarballs. Locate accepts a whole `Blender.app`.
- **Workflows made on another OS keep their file names:** a Windows path no longer comes out whole on Linux. Two Saves that
  differ only by case count as different files on Linux, where they are.
- **PyTorch on macOS:** the setup installs PyPI's build and the commands stop asking for CUDA. Apple's GPU is opt-in with
  `ATLAS_DEPTH_DEVICE=mps`, because these models are untested on it. They run on the CPU until someone tries it on a Mac.
- The Linux window now ties to its desktop entry and icon under Wayland.

## 1.21.0 (2026-11-04)

**The app builds and runs frozen on macOS and Linux, and proves it draws**
- **`packaging/atlas_toolkit.spec` is the one recipe for every OS.** `scripts/build.py` now publishes a real
  `Atlas Toolkit.app` on macOS and an `atlas-toolkit` folder on Linux. On Windows the published exe and companion are
  unchanged, and `--onedir` still makes a folder. A build is staged and replaces the old one only once it passes.
- **3D views work on current Linux desktops.** The frozen app bundled a C++ runtime that stopped Mesa from loading: it
  crashed on 3D pages on Ubuntu 24.04 and Fedora, and left them blank on Debian 12 and Ubuntu 22.04. Those libraries now
  come from the system, and the 3D views pick EGL or GLX to match what Qt is really using, under X11 and Wayland.
- **`scripts/check_frozen_gui.py` proves a build really draws:** it opens a mesh tool and counts the colours in the 3D pane,
  because a broken OpenGL leaves everything else looking right.
- **`self-test --strict`** is what a release must pass: every bundled helper starts, a missing one fails instead of being
  skipped, and tools run their `test_variants`, which is how Surface Texture's xatlas helper finally gets exercised.
- **`scripts/audit_bundle.py`** reads a build without starting it: helpers and notices are there, no file needs a glibc above
  2.34 or a macOS above 15, everything runs on Apple Silicon, and the signature verifies.
- `scripts/setup_ml.py` starts again: 1.20.0 gave `core/paths.py` an import that fails when the script loads it alone.
- `check_frozen_raster.py` and `check_frozen_dem_code.py` now work on every OS. The spec no longer needs embreex to be
  installed. A build without a splash no longer logs a splash error at startup.
- **3D views work on Windows with no graphics driver,** such as a virtual machine or some Remote Desktop sessions. Qt falls
  back to its bundled software renderer there, but the views were calling a different OpenGL library, so every frame failed
  and error dialogs stacked up with no way out. Now they draw.
- **When OpenGL really cannot work, a 3D view shows a message in its place** (Plane Cut's editor and Surface Eject 0.3.2's
  painter too). The crash dialog no longer repeats the same error or opens over itself, and a `--snapshot` run never waits
  for a click: it logs the error and exits with a failure.
- **Plotter Vision 1.4.2:** moving the camera again right after a previous move could lose the new move, because a settings
  update already on its way put the old camera back. Slow machines were most likely to see it.

## 1.20.0 (2026-11-04)

**Nothing is left running, on any OS**
- **Cancel, a crashed worker and a closed or crashed window now stop everything a job started,** on macOS and Linux as on
  Windows. Before, a native helper or the depth model could run on by itself on macOS and Linux, and a worker could wait
  for its next job forever once the window had gone. All of it goes through `core/procs.py`, and `tests/test_procs.py`
  proves each case on all three OSes with a job stuck inside native code.
- **Closing the app no longer risks hanging it:** two threads could stop the same worker at once, which raised an error
  inside the window's close. The depth runtime now also dies with its worker on Windows.
- **Programs started from a frozen app get a clean environment** (helpers, Blender, the depth runtime, the browser and
  file manager), so they no longer load the app's own libraries on Linux. A frozen app ignores the GDAL and PROJ settings
  another install left in the environment, such as QGIS or conda.
- **Finding a helper is one rule for all four:** the environment variable, then a path you picked, then the bundled one,
  then the PATH. A picked path that no longer exists now falls through to the bundled helper, so settings from another
  machine cannot disable it. Each helper is started once with `--version` before its first job, so one from another OS or
  CPU gets a clear message instead of a failed job.
- **The portable data folder sits beside a macOS app or an AppImage,** never inside it.
- **macOS and Linux details:** the memory check before a large terrain uses what the system can really free; install
  commands and "Locate" dialogs say the right names instead of `.exe`; the install commands no longer create a folder
  literally named `~`.

## 1.19.4 (2026-11-04)

**The C++ helpers build and run on macOS and Linux**
- **Remesh helper 0.1.1.** It no longer needs `vcomp140.dll` or `libgomp`: PMP linked OpenMP when it found it, without
  using it. Its peak memory report now works on macOS and Linux.
- **Linux helpers link `libstdc++` statically,** so they run on any supported distribution and never depend on what the
  app bundles. **macOS helpers target macOS 15.**
- **`native/build.py --check`** and **`doctor.py`** now compile a small program to prove the toolchain works, and say which
  helpers actually start on this OS, so a helper from another OS shows up as one.
- **`packaging/linux/Dockerfile`** is the Linux build environment, on the glibc 2.34 floor.
- CI requires the helpers: a missing one is now a failure, not a quiet skip.

## 1.19.3 (2026-11-04)

**Foundations for macOS and Linux**
- **One lock for every OS.** `uv.lock` replaces the Windows-only freeze, and a supported target without a wheel now
  fails when locking. `requirements.lock.txt` is exported from it with hashes and platform markers, so `setup_env.py`
  still works with plain pip. It now asks for Python 3.12 to 3.14, as the pinned packages do.
- **`scripts/doctor.py`** checks what this machine needs to develop and build Atlas, and prints the command for each gap.
- **`ATLAS_TOOLKIT_HOME`** keeps settings, presets, history and caches in one chosen folder on any OS. An empty `XDG_*`
  variable now counts as unset on Linux, as the XDG spec says, instead of meaning the current folder.
- **Tests run on all three OSes.** Every test module keeps its settings and caches in a temporary folder on every OS, and
  test bugs tied to Windows behaviour are fixed.
- **`tests/test_portability.py`** catches on Windows what would only break elsewhere: text files without an encoding,
  Windows-only calls outside their modules, imports with the wrong case, and machine paths.
- **CI** runs on GitHub Actions: Linux and Windows on pull requests, macOS and the oldest Python on demand.
- The helpers' notices are now the same bytes on every OS: hashes use LF, and file lists have one fixed order.
- **Placement editor:** in a small window the round rotate handle could not be grabbed, because the plate's top square
  sits almost on it and took the click. The nearest handle now wins, for DEM Terrain and Image to Mesh alike.

## 1.19.2 (2026-11-03)

**Number fields with step triangles, everywhere**
- Every number field in the app (tool settings, workflow steps, the plane and placement editors, Surface Eject's
  brush, Setup) now has up and down triangles inside its right edge: click to step, hold to repeat. A triangle
  fades at the limit. Up / Down keys and the wheel (with the field focused) do the same; **Shift** steps ten times
  as far, **Ctrl** a tenth as far. One shared field, `ui/widgets/spin.py`; a test refuses plain Qt spin boxes.
- **Steps fit the parameter.** A spec's own step stays where it suits the range. A very wide range follows the value
  in round numbers (150000 faces step to 152000, not 150001); a small size that cannot reach zero steps by part of
  itself (an edge of 0.02 no longer jumps by 0.1). Integers stay integers, decimals never go finer than the field shows.
- **Typing no longer fights you.** A click selects the value, so typing replaces it. A comma or a point both work,
  extra decimals are rounded and out-of-range values clamped when you press Enter or leave the field (they used to
  be refused keystroke by keystroke), `2e-5` works, and an empty field goes back to its value. Numbers read the same on
  every computer: point decimals, no thousands separators.
- Fields beside a slider are as wide as their longest value, so it is never clipped.

## 1.19.1 (2026-11-03)

**Workflows: New variation**
- **New variation** (dice, under Run) runs a checked workflow again with a new random value for every seed of its
  steps and every other setting as it is: a new design from established settings. The steps keep their own seeds
  (nothing changes on the canvas, the Check stays valid), every file gets the run's date and time
  (`<name>_YYYYMMDD-HHMMSS`), so no variation overwrites another, and the log and Activity record the seeds drawn.
  Steps before the first seeded one come from the cache. Shown only when a step has a seed.

## 1.19.0 (2026-11-03)

**Strata Sculpt 1.1 — connected layers and organic roots**
- Optional cylinders, square bars and organic branching supports fuse neighbouring strata into real closed
  joints. Supports are randomly spaced across facing cut surfaces, respect holes and concavities, fit thin
  slabs, and follow the slabs through dislocation and stage sequences. Existing floating results stay unchanged.
- Supports per gap (1–20, default 3), an independent Connection seed, thickness ranges, taper, curvature and
  irregularity; fixed or random extra branches (0–100), origin zones, up to four generations, spread and extent.
  Reports give fitted support/branch counts; required joints, face limits, cancellation and export reloads are
  validated before any files are published. Connected previews compute the same geometry as Sculpt.
- Three new presets: **Pinned Strata**, **Square Supports**, and **Airy Roots**. The primary and stage files
  contain the fused sculpture. CLI, saved settings and Workflows use the same controls.
- Surface Eject 0.3.1 shares its solid connector primitives and branch planning with Strata; its existing
  geometry, defaults, helper imports and connector behavior are preserved. No new runtime dependency.
- Added connector topology, seed, stage, export/rollback and Workflow tests, plus repeatable primitive and
  reference-sculpture plates (`scripts/dev/validate_strata_connectors.py`).

## 1.18.0 (2026-11-03)

**Setup window**
- A new **Setup** window (gear button in the top bar, `Ctrl+,`, or the palette) for the app's own options, saved as
  they change (`core/prefs.py`, in `app.json`). Tabs: General, Outputs, Components, Storage.
- **When an output file exists**, for tool pages and workflows: overwrite without asking (the new default), ask
  first (the old behaviour), or keep both: the new file is named `<name>_YYYYMMDD-HHMMSS.<ext>` with one stamp per
  run, for random variations and new designs. The command line gains `--suffix` for the same.
- **Theme and artwork at start:** the last one used, dark, light or the system's (followed live); **interface
  scale** (next start); **open at start** Home or the last tool / workflow; **ask before quitting** during a run;
  **preview delay**.
- **Heavy jobs at once:** the worker-process lane can run up to four jobs side by side (default one). The workflow
  cache no longer clears another worker's fresh partial entries.
- **Where outputs go** for tools not run yet, and **when a run ends:** show the first output, flash the taskbar, play
  a sound.
- **Storage:** where the app keeps its files with the cache's size (moved from About), clear the cache or the run
  history, export / import every option, tool setting and preset as one file, reset the options.
- About's Setup tab moves here as **Components**; About keeps the toolkit, its tools, the command line, shortcuts,
  changelog and credits. New `gear` and `sliders` glyphs.

## 1.17.0 (2026-11-03)

**Disintegrate (formerly Mesh Morph) and generic preset names**
- Mesh Morph is now **Disintegrate** 1.1.0 (`mesh-disintegrate`): the name says what it does, and "Morph" stays
  free for the morph modes of Shape Fusion and Compress. Outputs are `<name>_disintegrate_…` signed
  `atlas-toolkit:mesh-disintegrate`. The old id `mesh-morph` keeps working on the command line, in workflows and
  in history, its saved settings carry over, and earlier `_morph_` outputs are still skipped as inputs.
- New contract field `Tool.aliases` (former ids of a renamed tool); the registry, CLI and settings honour it.
- Presets are named for what they do, not for the studio pieces they were first tuned on (values unchanged):
  - Twist 1.1: *v17 torsion sequence* → **Torsion sequence**, *Twist plate* → **Single twist**.
  - Contour Lines 1.1.0: *Plate* → **Classic**, *Sequence* → **Stage set**, *Melt sequence* → **Fine detail**.
  - Melt 1.1: *v17 dissolution sequence* → **Dissolution sequence**.
  - Disintegrate: *Sketch progression* → **Long progression**.
- Taglines, help texts and preset tips of Twist, Melt, Contour Lines, Sketch Hatching (1.0.2) and Disintegrate no
  longer refer to the studio's own models and projects.

## 1.16.3 (2026-11-03)

**Standalone repository**
- Atlas Toolkit leaves the studio repository: its own git repository with the full history (git-filter-repo,
  tag `v1.16.2` = studio commit `608c93e`), its own README, licence placeholder and third-party notices with a
  licence audit, `.gitattributes` / `.editorconfig`, and `AGENTS.md` beside `CLAUDE.md` for other coding agents.
- Native helpers move into `native/` with one cross-platform driver, `native/build.py`: pinned upstreams in
  `deps.lock.json` fetched into a shared `_deps/` (Eigen once), toolchain discovery (vswhere/vcvars on Windows,
  the system compiler elsewhere), a CMake superbuild with Windows/Linux/macOS presets, install into the package,
  regenerated notices and the developable GPL source archive. The Remesh/Decimate and Developable Surfaces
  sources, built since 1.10 but never committed, are now tracked. PMP's patched sources are generated at
  configure time. Rebuilt helpers give byte-identical results to 1.16.2's.
- `scripts/build.py` publishes to `dist/`, builds the helpers through the driver, and says so when it packages
  existing helpers (`--skip-native`) instead of silently reusing them.
- The GPU depth runtime can live in a per-user folder (`setup_ml.py --user`;
  `%LOCALAPPDATA%\AtlasToolkit\ml-runtime` on Windows) that any installed exe finds; About › Setup gives those
  commands. The old `code/Python/atlas_toolkit/.venv-ml` lookup is gone.
- No machine paths in tests or scripts: `atlas_toolkit.devpaths` reads optional dev folders (studio workspace,
  design system) from `dev.local.toml` or environment variables; tests that need them skip without them.
  Validation scripts move to `scripts/dev/` and write to `build/qa/`; label fonts are the bundled Geist.
- FlowRep: the obsolete installer (`flowrep3d/install.py`) is retired, the library's own tests are ported to
  `tests/flowrep_tests/` with generated meshes, and its docs describe the in-repository layout.

## 1.16.2 (2026-11-02)

**Motion Sculpture 0.3.0 — stronger painted wakes and real stratification**
- Painted geometry defaults to Sliced layers: actual section polygons, selected outward-edge sweeps, tapered
  wakes and closed plate/bar extrusion with real layer/bar gaps. The silhouette follows layer resolution;
  the previous connected-surface method remains selectable for strict unpainted-skin preservation.
- Polygon unions on a bounded precision grid avoid fragile 3D boolean seams and dense skin refinement.
  Painted masks no longer receive whole-shape leading-side suppression or global motion backtracking.
- Connected mode now limits offending vertex neighbourhoods locally instead of weakening every painted point.
  Retained-motion statistics and measured extension/section reports make the achieved result inspectable.
- Layer previews use shape-preserving reduction so thin fins and gaps survive. Mesh-bound selection,
  cancellation, budgets, staged file validation and unpainted-mode algorithms remain intact.
- Regressions cover stronger reach response, local protection, true gaps, single-face selection, section holes,
  STL/OBJ/PLY closure and workflow reuse; real-model comparison and source/frozen release checks.

## 1.16.1 (2026-11-02)

**Motion Sculpture 0.2.0 — painted motion areas**
- Region → Painted area and a Selection tab reuse Surface Eject's exact-source visible-face painter, including
  erase/orbit, Undo, Clear, Select all and compatible selection JSON load/export. No Surface Eject changes.
- Mesh-bound masks persist in settings, history and Workflows; optional selection-file input supports CLI and
  cache invalidation. Artistic presets preserve the current painted selection unless explicitly overridden.
- Exact selected/unselected triangle distance fields feather motion and gaps inside the painted area, leaving
  unpainted source skin fixed. Local triangle orientation backtracking limits unsafe boundary deformation.
- Painted mode blends layer gestures on the connected source surface, guards file precision, and validates staged files by reload
  before publication. Existing unpainted modes retain their algorithms and separate-shell output.
- Dedicated mask/geometry/export/UI/workflow tests and full Atlas regression/release checks.

## 1.16.0 (2026-11-02)

**Motion Sculpture 0.1.0**
- Experimental mesh tool inspired by layered motion sculptures: real capped laminae or source-cut bars,
  monotone directional wake stretching, finite tapered tails and planar curl. Stream, Pulse, Gust and Vortex.
- Seven presets; leading-form preservation, base hold, whole/band/sphere regions, layer axes and tilt,
  independent seeded per-piece variation, conforming wake refinement and a hard face budget.
- Identical Preview/Run geometry, signed STL/OBJ/PLY with retained frame and source units, unique icon,
  batch/CLI/Workflows, two drawing workflows and a usage/method guide. No new dependencies or Blender.
- Closed oriented solids required. Separate shells, without boolean fusion; curled bars may intersect.

## 1.15.1 (2026-11-02)

**Line Smooth 0.1.0**
- New SVG post-processing tool for jagged, zigzag and sawtooth line art, including Surface Stripes drawings.
  Whittaker–Henderson curvature smoothing and spectral compensated Taubin filtering move geometry without
  simplification or vertex decimation. Physical arc-length grids and exact box-integration anti-aliasing.
- Smoothing span, strength, continuous physical displacement cap, persistent corner support/angle, loop
  area and stroke length limits. Fixed endpoints, all-pen contact anchors, swept-region obstacle checks
  and earlier-result constraints; whole-stroke backtracking avoids clipping-induced kinks.
- Five presets, unique icon, live Before/After, batch/CLI/Workflows, signed SVG and restorable metadata.
  Optional shared-reader shape selection includes retained circles/rectangles in this tool's previews.
  Layers, styles, transforms, stroke order and closed paths retained; curves stay original by default.
  Dense vector polyline output, identical preview/final geometry and bounded sample budgets.
- Geometry/integration regressions, research and usage guide, reusable workflow and visual/timing validation
  on synthetic line art and an existing Surface Stripes SVG. No new dependencies.

## 1.15.0 (2026-11-02)

**Line Simplify 0.1.0 — controlled SVG line-art reduction**
- New SVG post-processing tool: compiled GEOS Douglas–Peucker and heap-based Visvalingam–Whyatt,
  with physical distance, corner, reversal, local arc-length and signed-loop-area limits.
- Conservative swept-region constraints protect crossings, junctions and enclosed motifs across pens;
  static original-segment STRtree plus dynamic accepted-shortcut R-tree, with closed-loop rollback.
- Original curves by default; optional bounded adaptive flattening reserves part of the displacement budget.
  Round-trip coordinates, same element types, transforms, layers, styles, stroke order and endpoints.
- Four presets, unique icon, equal Preview/Run geometry, before/after statistics, metadata/signature,
  worker cancellation, CLI/Workflow integration, research guide, reusable workflow and benchmark script.
- Overlap Cleaner 1.2.1: backwards-compatible optional curve-flattener hooks in the shared parser;
  cleaner defaults unchanged.

## 1.14.4 (2026-11-01)

**Surface Eject 0.3.0 — organic branching**
- Raise extra branches to 100. Fixed counts or inclusive seeded min–max draws per connected fragment;
  separate Branch seed rerolls counts/origins/parent choices without changing extraction or connection share.
- Roots at the previous split position, near the cavity, in the middle, near the fragment, randomly along
  the visible parent span, or balanced across all three zones. Up to four generations of finer branches
  grow from earlier branches; thickness ratio, tip spread and the existing extent envelope bound every level.
- Dense interior tip sampling, adaptive fine branches, per-branch cancellation and face-budget checks.
  Validate candidate trees at their joints and trim newest optional leaves if precision prevents a safe export;
  retain the main cavity-to-fragment bridge. Report actual/requested counts, origin metadata and achieved levels.
- Four new tree presets, drawing workflow and visual acceptance cases. Preserve full connector/fragment
  geometry in previews, simplifying only the body when needed. UI/CLI/Workflow controls and cache keys,
  100-branch closed STL/OBJ/PLY regression checks, guide, About and README updated. Existing split presets
  and default settings retain their geometry; no Blender or new dependency.

## 1.14.3 (2026-11-01)

**Surface Eject 0.2.1 — connection percentage**
- Connected fragments percentage, 0–100% with 100% preserving previous behavior. Applies to successfully
  extracted pieces; exact nearest whole count, with half rounded up. Unselected fragments stay floating.
- Seeded random subset with a separate Connection seed, plus Largest first / Smallest first by extracted
  volume. Stable rankings preserve earlier selections when the percentage grows; sampling, ejection and
  per-fragment connector shapes remain unchanged.
- Selected assembly joints are validated independently from deliberately floating pieces. Reports and
  statistics show actual count/share and fragment IDs. At zero selected pieces, export omits empty connectors.
- Shared UI/CLI/Workflow parameters and cache keys, Half-Rooted preset and mixed drawing workflow; About,
  README and usage guide updated. Count/rounding/subset/topology/export/cache regressions and visual checks.

## 1.14.2 (2026-11-01)

**Surface Eject 0.2.0 — cavity connectors**
- Optional solid connectors, off by default, join each ejected fragment to its matching final cavity.
  Cylinders, square bars, or organic stems with taper, curvature and seeded irregularity; independent
  thickness ranges. Controls bound fork count, split position, tip spread and all lateral centreline offsets.
- Containment-tested fragment interiors handle centroids outside concave pieces. Final cut interfaces,
  embedded joints, local contact and positive-volume attachment checks; combined output is boolean-fused.
  Bounded joint-sliver stabilization checks closed double/float32 copies, component count and volume.
  Thin fragments limit thickness and available forks, with achieved values in the report. No loose ends.
- Three-file separate export retains the cavity body, fragments and named connectors, with common placement,
  signed staged STL/OBJ/PLY and saved-copy validation. Generic mesh publication supports distinct extra labels.
- Six new plotter presets and connected drawing workflows. Shared UI/CLI/Workflow schema; default extraction
  geometry and previous presets remain unchanged. About, README, usage guide, geometry/export/cache tests
  and repeatable real-model connector plates updated. No new libraries or Blender requirement.

## 1.14.1 (2026-11-01)

**Surface Eject 0.1.0**
- New independent mesh tool extracts actual model volumes with surface-aligned cube, box, sphere,
  ellipsoid or cylinder samplers. Matching cavities and original surface detail; area-weighted outward
  normals, preserved fragment orientation and independent seeded size, penetration and ejection ranges.
- Full detachment certifies clearance against the original body and earlier fragments. Whole surface,
  exactly clipped height bands, or a mesh-bound painted face mask. Modal orbit/paint/erase editor,
  undo, clear/select-all and portable selection JSON; hidden faces are protected by exact ray picking.
- Eight graphic presets, combined or separate cavity-body/fragments exports, signed staged STL/OBJ/PLY,
  source units and frames, saved-copy validation, volume conservation, face budgets and cancellation.
  Strict closed input by default; explicit optional resampling repair. No Blender or new dependencies.
- Shared UI/CLI/Workflow contract, selection through Aim in tool / Back to workflow, drawing workflows,
  controls/method guide and repeatable visual acceptance. Preview uses final extraction geometry.

## 1.14.0 (2026-11-01)

**Bend 0.1.0**
- New mesh tool carries sections along an arc-length spine: circular/soft arcs, S-bends and feathered waves.
  Signed angle and direction, active axis range, fixed start/middle/end section, neutral-axis offsets,
  spatial plane sweep and independent section roll; ten artistic presets and intact-to-bent stage sets.
- Interactive two-projection Spine editor writes shared schema settings. Conforming linear refinement
  preserves source detail and open/closed topology; Preview and Run use the same final geometry.
- Conservative local fold protection reports achieved angle; face/memory limits, cancellation, reloaded-copy
  validation and staged publication. Signed STL/OBJ/PLY preserve source units, placement and frame axes.
  No Blender or new dependencies. UI, CLI and Workflows share the standard Atlas integration contract.
- Drawing workflows, controls/method guide, repeatable real-model visual acceptance and geometry, export,
  workflow/cache, contour-rendering and editor regressions in both themes.

## 1.13.1 (2026-11-01)

**Surface Texture 0.1.0**
- New mesh tool applies raised, engraved or balanced relief using twelve bundled CC0 heightmaps or a local
  grayscale PNG/TIFF, including 16-bit data. Scales, rock, sand, soil, veins, leaves, bricks, bark, leather,
  cracked earth, cobbles and wood grain; provenance, preparation settings and checksums are bundled.
- Pinned MIT xatlas helper for surface-following charts; cylindrical periodic wrapping and triplanar blending.
  Welded, conforming local refinement with sampled displacement error, chart-seam feathering, border protection,
  whole-model/band/sphere controls and collision limiting including swept normal clearances.
- Fourteen graphic relief presets, A3 / 0.3 mm drawing workflows, cached charts/height preparation, face/memory
  guards, cancellation, staged validated STL/OBJ/PLY exports, final-geometry previews and material swatches.
  Same controls in UI, CLI and Workflows; native helper and textures bundled, no Blender or network required.
- Usage/research guides, repeatable visual acceptance and matched-resolution Blender benchmark scripts,
  geometry/export/cache tests and UI/Workflow regressions. Existing drawing-tool defaults are unchanged.

## 1.13.0 (2026-10-31)

**Pipe Fill 0.1.0**
- Replace a closed model with volumetric organic curves or orthogonal pipes with circular elbows.
  Independent strands and closed loops; solid tubes or hollow pipes with annular mouths; no retained body.
- Seeded routing, thickness variation, packing rotation, diameter, spacing and finite-radius clearance.
  Cached libigl boundary queries, conservative segment containment, exact segment separation and transported
  tube frames. Explicit optional resampling repair; grid/site/face budgets and measured routing coverage.
- Eight artistic presets, including airy/dense plotter sculptures, looping cords and architectural pipes.
  UI, CLI and Workflows share the standard contract. Signed staged STL/OBJ/PLY exports preserve source units;
  saved-copy validation precedes publication. Previews use final geometry, with display-only proxies as needed.
- Research/usage guides, drawing workflows, repeatable visual acceptance script and geometry/export/cache tests.
  Existing bundled dependencies suffice. Independent packing inspired by Repulsive Curves, not its original solver.

## 1.12.0 (2026-10-31)

**Voxelize Fill 0.1.0**
- New independent mesh tool classifies full volumetric occupancy with winding numbers. Cell-centre sampling
  preserves oriented cavities; open scans are explicitly inferred, with a strict closed-input option.
- Separate cubes, adaptive octree blocks and directional bars; joined occupied boundaries omit internal faces.
  Combine cutaways with exploded filled layers, nested depth bands or seeded erosion. Nine artistic presets.
- Native quad OBJ/PLY, triangulated STL, signed staged exports, original units, final-geometry previews and
  grid/face budget checks. Shared schema supplies UI, CLI and Workflows; no new dependencies or Blender required.
- Add research and usage guides, example drawing workflows and volume/export/drawing regression coverage.

## 1.11.1 (2026-10-31)

**Heightfield robustness — Raster DEM to Mesh 0.3.1 / Image to Mesh 0.1.1**
- Placement, drafts and final DEM builds share the authoritative source outline, including native and
  reprojected inputs. Fit/lock/resize wait for authoritative preparation; explicit Preview and Build reject
  known conflicts before starting workers. Locked errors show physical terrain, plate and padding dimensions.
- Replace closed-volume simplification/top-face extraction with local link-safe precision repair for open
  heightfields. Safe surfaces retain their vertices; short-edge repairs preserve local winding, boundaries
  and manifold vertex fans. Printable cleanup returns the mesh actually validated, with saved-copy checks.
  Boolean-built planar terraces retain a validated closed-solid fallback, without extracting an open skin.
- Add explicit Automatic safe clearance alongside existing manual values. Placement shows source contacts,
  physical source-cell limits and a valid suggestion. Remove the invalid fixed 0.02 mm recommendation.
- Mask-aware grid allocation and early clipped-face budget checks replace the full-rectangle rejection.
  Printable preflight includes walls/deck/bottom; error guidance names controls active in the selected method.
- Built-in artistic presets retain input/preparation/mask settings unless a preset explicitly overrides them;
  user presets remain complete snapshots. Hide inactive adaptive/grid/target-height controls in both tools.
- Heightfield logs include job mode/ID, versions, parameters, resolved units, counts, phase and elapsed time;
  failed run history retains actual duration and submitted values. Expected validation errors use concise
  guidance while unexpected geometry defects retain traces.
- Regression and frozen checks cover both tools, alpha holes/islands, contact policy, native/preview fitting,
  precision repair, sparse-mask budgets, preset preservation and UI preflight. Repeatable real-DEM export
  acceptance script added. No C++ source changes or new dependencies.

## 1.11.0 (2026-10-31)

**Image to Mesh 0.1.0 and Raster DEM to Mesh 0.3.0**
- Split geographic DEM processing from non-geographic image reliefs. DEM requires a source/override CRS;
  explicit legacy heightmap settings report migration to Image to Mesh. Geographic presets remain terrain-focused.
- New image tool reads PNG/JPEG/TIFF/BMP/WebP, including numeric 16-bit and floating-point heights. Weighted,
  average and lightness grayscale or RGB channel selection; EXIF orientation, quarter turns and mirroring.
- Brightness, contrast, gamma, invert, levels, optional Auto levels and mask-aware noise smoothing. Black/white
  height calibration survives mesh smoothing and sampling; grayscale preparation is neutral by default.
- Alpha silhouettes and holes, optional background compositing/exclusion, shared plate fitting and scale locking.
  Neutral Image top rotation replaces north controls. Original, Prepared heightmap, Placement and Relief mesh views.
- Shared heightfield geometry, adaptive triangulation, placement editor and atomic validated STL/OBJ/PLY exports.
  Fix highest terrace treads at exact step boundaries. Seven image presets include three open A3 drawing surfaces;
  new image contours/faceted-network workflows, with the same controls in UI, CLI and Workflows.
- Updated About, README, guides and release checks; image preparation, calibrated geometry, UI, CLI/workflow cache
  and frozen-build regression coverage. Existing dependencies suffice; no Blender requirement.

## 1.10.6 (2026-10-30)

**Raster DEM to Mesh 0.2.0 — responsive placement and faithful detail**
- Bounded 1,024-pixel display, tiled statistics, dependency-aware raster/outline caches and convex-hull
  fitting replace repeated coastline buffering. Cancellable preparation workers and cached drawing paths
  keep placement responsive. Named build phases include elapsed time.
- Independent Fast / Balanced / Detailed drafts target 25k / 100k / 250k terrain triangles. Drafts and
  display proxies are labelled. Native boundaries retain holes and tiny islands at coarse export pitch.
- Placement adds symmetric plate handles, numeric dimensions, aspect locking, Custom plate, terrain-scale
  locking, explicit Fit terrain, Reset plate and an expandable editor with accept/cancel. Locked rotation
  conflicts are shown without silent scaling. All settings work through CLI and Workflow Aim in tool.
- Faithful and Smooth add Adaptive TIN sampling with conforming maximum-edge refinement, mask clipping
  and independently measured maximum/RMS source-height error. New grid default 0.25 mm; saved pitches stay
  unchanged. Detailed faithful, Fine faithful and Large-region draft presets.
- Direct solid stitching, sparse vertex-fan validation and topology-preserving file-precision cleanup.
  Memory preflight replaces the 25M source-cell ceiling. Export budgets default to 2M triangles with an
  advanced maximum of 8M; quality is never silently relaxed to fit.
- Bundle pydelatin 0.4.0 and native dependency notices; update guides, workflow examples and repeatable
  high-resolution acceptance/render checks. No C++ source changes.

## 1.10.5 (2026-10-30)

**Raster DEM to Mesh 0.1.1 — terrain placement**
- Automatic draft completion keeps the Placement tab active. Dragging the north handle edits exported terrain
  rotation relative to the page-aligned plate; drafts wait for the drag to finish.
- Shaded elevations rotate with the footprint, an inline numeric rotation control shares the Terrain turn setting,
  and a fixed page scale keeps the handle stable as the snug plate refits. The guide explains fixed versus snug plates.
- Superseded previews are cancelled immediately and stale worker results cannot replace newer geometry or clear
  an active preview. Export reports include the terrain angle. Regression checks cover handle-to-worker-to-export
  placement, all four surface methods, signed STL/OBJ/PLY orientation and preview replacement races.

## 1.10.4 (2026-10-30)

**Raster DEM to Mesh 0.1.0 — Image → Mesh**
- Numeric GeoTIFF and grayscale heightmap input, band/CRS/unit controls, masks, background exclusion and missing-data handling.
- Faithful, smooth, boundary-preserving faceted and contour-terraced relief; seven presets and millimetre sampling controls.
- Complete-model paper/custom fit, padding, independent plate thickness/basal lift/z-factor, percentage thickness and rounded corners.
- Top-view rotation handle, grid-north reset, best-fit alignment and cancellable draft mesh previews; shared UI/CLI/Workflow contract.
- Double-precision solid fusion, manifold edge/vertex validation and staged export reloading before publication. Optional open terrain skins
  for drawing; explicit point-contact clearance for masked raster outlines. Signed STL/OBJ/PLY with embedded Z-up frames.
- Contour Lines 1.0.1 and Plotter Vision 1.4.1 add custom paper dimensions; two terrain-to-SVG workflow examples.
- Generic tool input-dependency discovery invalidates workflow caches when raster sidecars change. Rasterio/GDAL/PROJ resources and
  third-party notices are bundled; method guide, synthetic tests and repeatable Viana acceptance examples added.

## 1.10.3 (2026-10-30)

**Remesh/Decimate 0.1.0**
- New mesh preparation and artistic topology tool: constrained quadric decimation, uniform/adaptive triangles,
  aligned quads (Instant Meshes or optional Blender QuadriFlow), directional triangles, dual cells, planar
  dissolve and voxel reconstruction. Ten complete presets; regional detail protection/density and source-unit preservation.
- Bundled double-precision PMP helper, pinned source, boundary/feature constraints, zero-area collapse guard,
  topology checks and deterministic sampled surface error. Cancellable processing and staged atomic signed exports.
- Native OBJ/PLY polygons and explicit STL triangulation; native-edge wireframes, CLI, presets, history and Workflows.
  Generic conditional runtime requirements keep Blender optional until its methods are selected.
- Plotter Vision 1.4 adds Features / Mesh edges and the Remeshed network preset; polygon diagonals stay out of
  line art while triangles retain occlusion and silhouettes. FlowRep Sketch 0.1.2 reads native PLY/OFF polygons;
  Hatch Maker 6.0.2 and Sketch Hatching 1.0.1 suppress artificial polygon creases.
- Method guide, example drawing workflows, validation script, About research/setup credits and third-party notices.

## 1.10.2 (2026-10-30)

**Mirror 0.1.0**
- New mesh tool: reflect the whole model across a manually placed plane, retaining the original or exporting
  only the reflection. The initial plane is on the +X side, creating an adjacent copy; Other side mirrors its
  placement around the model centre. Correct reflected winding, unchanged source units and surface detail.
- Live original/reflection preview with shared screen-line drawing, XYZ/normal dragging, rotation rings,
  snapping, orthographic views, numeric position/angles and a maximizable editor with accept/cancel/preview.
- Combined unjoined shells or separate signed STL/OBJ/PLY files; staged publication, safe extra filenames,
  CLI, presets, history and workflow support. No cutting, automatic welding or solid union.
- Plane Cut 0.1.1 retains its behavior and viewer entry point while sharing plane math, controls, shaders and
  export transactions. Both tools have geometry, interaction, workflow, export and cancellation regression tests.

## 1.10.1 (2026-10-30)

**Plane Cut 0.1.0**
- Draw a screen line to define an infinite cutting plane, then refine it with XYZ translation arrows, rotation
  rings, a normal-slide handle, numeric position/angles/offset, axis alignment and orthographic camera views.
- Dedicated Plane tab and a resizable, maximizable editor with Use settings, Preview cut and Cancel. Explicit
  kept-side selection, discarded-side ghost, flip, gesture undo, fine movement and snapping.
- Cut/discard one side or Slice/keep both; open, minimal, remeshed and fixed-size fills. Signed STL/OBJ/PLY in
  original units; combined or separate slice outputs, optional separation, staged saving and safe sibling names.
- Uses the existing tool/viewer contract for UI, CLI, presets, history and Workflows. Geometry/topology, export,
  cancellation, workflow cache, projection and editor-interaction regression tests.

## 1.10.0 (2026-10-29)

**Developable Surfaces 0.1.0**
- New mesh tool adapting Stein, Grinspun and Crane (2018): field-aligned triangle preparation, coarse-to-fine developability flow, fixed open boundaries and component-wise source-unit preservation.
- Five aesthetic presets, cancellable standalone C++ helper, diagnostics, validated cache, signed STL/OBJ/PLY and atomic exports. Invalid field remeshing uses a reported, topology-checked coarsening fallback.
- Available through the common UI, CLI and Workflow contracts; unique icon, previews, About research credits and Setup information. GPL/LGPL notices and corresponding helper source are bundled.
- Plotter Vision 1.3: Developable rulings and Developable creases presets retain generated topology. Example ruling and contour workflows, method/build guide and repeatable validation script.


## 1.9.2 (2026-10-29)

**Presets drop-down, steadier**
- It always opens on "Custom settings": no preset is selected by matching values, and none is remembered from an
  earlier session (the values still are). A preset picked now stays shown while the values match it and goes back
  to "Custom settings" as soon as a setting changes. A tool that starts from a suggested preset (Overlap Cleaner's
  Standard) says so in the line under it.
- Tools without presets show the same "Custom settings" box.
- Two icons beside the heading replace the ☰ menu: save the current settings as a preset, and reset to the
  defaults. Deleting one of your presets, and a workflow step's "Use the tool page's current settings", are lines
  at the end of the list.

**Cubic Replicator 3.1**
- Presets: Neat grid (3 × 3), Turning crowd (4 × 4, turned about the up axis, some mirrored, spaced so none touch)
  and Tumbling cube (3 × 3 × 3, turned freely on every axis).

## 1.9.1 (2026-10-29)

**Presets as a drop-down**
- The right-hand panel's presets (tool pages and workflow steps) are a drop-down instead of rows of buttons: one
  line high however many presets a tool has (Dual Fusion's 19 took eight rows), names shown in full in the open
  list, each with its description as a tooltip, and the chosen preset's description under it as before. It reads
  "Custom settings" when the values match no preset.
- Your own presets are in the same drop-down under "My presets" (the ☰ menu keeps Save, Delete and Reset), and a
  preset saved in a workflow step appears on the tool's page at once.
- The section is called "Presets".

## 1.9.0 (2026-10-29)

**Dilate 0.1.0** (new, Mesh → mesh)
- Inflate a model with pressure, elastic stretching and bending resistance, or swell a feathered height band
  or spherical region. All directions or around an axis, adjustable softness, roundness, retained relief and
  seeded broad lobes. Free, Keep base and Keep open boundary anchors. One final signed STL/OBJ/PLY per run.
- Closed parts have independent volume targets. Open parts preserve their openings and use area-equivalent
  outward swelling with a warning. Component-aware detail transfer, triangle contact checks and backtracking
  limit new crossings and report when the requested inflation could not be reached.
- Six plotter-oriented presets: Sculptural Swell, Rotund Bronze (Botero-inspired), Flowing Goddess
  (Miriam Lenk-inspired), Balloon, Inflated Clownfish and Local Bulge. Pair with Contour Lines or Plotter Vision.
- Shared tool contract integration across the UI, CLI, presets, history and Workflows; About lists Dilate and
  its method. Uses the bundled Python libraries; no Blender dependency.

## 1.8.3 (2026-10-29)

**Surface Stripes 0.2.0**
- Export stripes only (off by default): replace the output with closed, solid ribbons and leave out the body.
  Raised height becomes ribbon thickness. STL/OBJ/PLY, previews, CLI and workflows share the same option.
- Plotter broad and Plotter dense presets enable ribbon-only output, with broad smooth coverage or more, finer
  form-following stripes, higher field/surface resolution and preserved model orientation.
- Report measured skin coverage and ribbon volume. Reuse the existing cached field and packaged native helper;
  the helper and its protocol are unchanged.
- Ribbon-only previews use the export field resolution and cache to preserve closed topology and a stable pattern.

## 1.8.2 (2026-10-29)

**Tools that need a missing program**
- A tool whose required program cannot be found (Blender for Melt, the stripes helper for Surface Stripes) is faded
  in every list, with an amber warning mark right of its name and the reason in the tooltip: the side bar (full and
  icons only), Home (an amber "needs Blender" badge in place of input → output, cards and list), About › Tools,
  the Workflows step list and quick add, and the search palette.
- Its page shows the amber notice below the tool's name (Locate, Get, Setup guide → About › Setup); Run and Preview
  wait, as before. A missing optional program only adds a quiet note (Ribbon Wrap without Blender: Settle in
  Blender is unavailable; the tool still runs).
- Workflows: such a step turns amber with a "needs Blender" chip as soon as it is added, before Check, and its
  inspector shows a compact notice; Check still stops the run.
- One shared state (AppContext.lacking), refreshed when a program is located anywhere and whenever the window
  comes back into focus (after installing Blender, say), so every list and page updates together.

## 1.8.1 (2026-10-29)

**Home**
- Cards or List: the list shows each tool (and workflow) on one 44 px line with its icon, input → output, a line of
  summary and its version, about a quarter of a card's height.
- Every section folds under its header (Workflows, SVG · post-process, Mesh → SVG, Mesh · model prep,
  Image → SVG), with Fold all / Unfold all. The view and the folded sections are remembered.
- Saved workflows show by the name stored in them (the file name in the tooltip).
- The subtitle no longer mentions the gallery.

**About window**
- The cache folder's size and file count, added up in the background when the window opens.
- Where each optional component was found, relative to the app's folder or a standard folder, never a drive or
  user name: `<app>\.venv-ml\…`, `%ProgramFiles%\Blender…`, or "bundled with the app" (Setup tab and the Version
  card).

## 1.8.0 (2026-10-29)

**Atlas Toolkit 1.8**, the release that closes the 1.7 series. Since 1.7.0:
- New tools: Surface Stripes and Compress (below), FlowRep Sketch, Form Replicator, Rods, Holes, Dual Fusion,
  Ribbon Wrap and the other mesh tools of 1.7.x.
- Interface: foldable tool list with an icons-only rail; the About window with Tools, Setup (optional components),
  Command line, Shortcuts, Changelog and Credits, showing nothing of the computer it runs on.
- Command line in a terminal through the console companion `atlas-toolkit`.
- Review of 1.7.22-1.7.23: both tools pass their suites and a run on a sample (signed, watertight). The helper's
  `CMakeLists.txt` is now kept in git (the repository ignored *.txt); help texts name `cpp/atlas_stripes/build.py`
  without this repository's layout. About › Credits adds Projective Dynamics (Compress) and libigl.

## 1.7.23 (2026-10-29)

**Compress 0.1.0** (new, Mesh → mesh)
- Compress any model in a single step. Free compression: two plates (any axis, tilted shear press, or onto the
  floor), a tube tightening around an axis, or pressure from every side. Into a shape: a cube, sphere, cylinder,
  cone, pyramid, prism (3-12 sides), octahedron, icosahedron or dodecahedron with an aspect ratio, as a mould that
  closes in (fill, final volume, centred or standing on the floor) or a morph that keeps the relief.
- Quasi-static projective dynamics on a near-uniform, watertight copy (libigl shortest-edge decimation): elastic or
  plastic skin, bending stiffness, internal pressure for compressibility, friction, approximate self-collision and
  seeded imperfections. The full-detail model follows by blended one-ring rotations, so its detail is kept.
- 13 presets tuned for Contour Lines and Plotter Vision; own icon (`press`).

## 1.7.22 (2026-10-29)

**Surface Stripes 0.1.0** (new, Mesh → mesh)
- Generate curvature-aligned or smoothest surface stripes with a pinned, headless Geometry Central/Eigen helper.
- Fuse flat-topped raised bands to the detailed source body; spacing, width, relief, rotation, orthogonal families
  and seeded repeatability. Signed STL/OBJ/PLY outputs feed Plotter Vision or Hatch Maker.
- Separate curve cache, source-unit preservation, component-wise processing, explicit optional open-mesh repair,
  process previews and native-child cancellation. Bundle the helper in the frozen app.

## 1.7.21 (2026-10-28)

**FlowRep Sketch 0.1.1**
- Integrate descriptive 3D curve extraction and visible SVG sketches, cached camera redraws,
  Clean/Detailed/Sparse presets, Aim view, process previews and cancellation.
- Bundle deterministic quad preparation and native remeshing in frozen builds.
- Portable source installer, implementation notes and repeatable install/build instructions: docs/flowrep/.
- Review on integration: the 3D network cache now lives in the toolkit's cache folder (portable mode and Clear
  cache include it; FLOWREP_CACHE still overrides), the report cites the method instead of the installer, the icon
  sits in the icon table with the others. About › Credits names the FlowRep paper, Instant Meshes and NetworkX;
  ChatGPT's card mentions the tool. The installer's backups (.flowrep-install/) stay out of git.

## 1.7.20 (2026-10-28)

**Form Replicator 1.0.1**
- Own icon (`rosette`, a four-petal flower); it shared Cubic Replicator's.

## 1.7.19 (2026-10-28)

**Form Replicator 1.0** (new, Mesh → mesh)
- Sculptural replication of figurative and abstract models: planar and spatial crosses and stars (five Platonic
  symmetries), clock rings and arcs, lobed rosettes, golden-angle sea urchins and sunflower disks, helices,
  toroidal wreaths, an arc-length-sampled trefoil knot and a half-twisted Möbius wreath. Twelve starting presets.
- Shared source Up / Front / Extra turn; composition plane and phase; outward, inward, tangent, surface-normal
  or upright copies; aim by model up or front; centre or base anchors, stretch, taper, roll and progressive roll.
  Seeded variation and thinning keep surviving placements stable. Curve frames avoid abrupt orientation flips.
- Touching copies are always exact boolean unions, removing internal faces; deliberate gaps remain. Shared open
  source repair is reported explicitly. Pinched sources are repaired, reductions that pinch retry at higher detail,
  and export precision is checked before publication; cavity shells are not counted as extra solids.
  A reported 0.1% Contact weld retry resolves exact point/edge contacts without remeshing; 0 requires exact sizes.
  Source detail and copy/face budgets bound work; Preview reduces source
  detail while keeping every placement. Signed STL / OBJ / PLY, original units, atomic export on cancel/failure.
- Discovered through the existing tool contract alone: generated inspector and CLI, presets and saved settings,
  workflow library, typed mesh connections and versioned cache. Feed its mesh to the existing drawing tools.
- Tests cover every layout, symmetry, orientation frames, exact overlap volumes, source safety, all exports,
  preview/dry run, repair, limits, failed/cancelled publication, CLI and a cached workflow through Plotter Vision
  and Overlap Cleaner. No tool-specific changes to the framework or UI.

## 1.7.18 (2026-10-28)

**About window: nothing of this computer**
- The window is the same on every computer: no resolved paths, user name, drives or operating system. Folders are
  named generically (%APPDATA%\AtlasToolkit …, or the portable folder) and still open with Open; Blender and the
  depth environment show only found / not installed; the install commands use a placeholder for the app's folder;
  the Command line tab says `atlas-toolkit`, the console companion, with no path. Copy diagnostics, asked for to
  report a problem, is the one place with real paths.
- A test reads every text and tooltip of every tab, and the changelog, and fails on this computer's home folder,
  user name, app folder, Python path or any drive path.

## 1.7.17 (2026-10-28)

**Command line in a terminal**
- Fixed: `"Atlas Toolkit.exe" --version` (or any command) typed in cmd or PowerShell printed nothing. The one-file
  exe runs as a child of its own unpacker, which has no console, so the app attached to none. It now finds the
  console of the nearest process above it that has one, and closes the splash screen at once.
- New: `atlas-toolkit.exe`, a small console companion (9 MB) built next to the app: `atlas-toolkit list` (in
  PowerShell `.\atlas-toolkit list`). The terminal waits, the output streams as it comes (progress included), the
  exit code comes back and redirection works. No splash screen. The build checks it (--version, list, exit codes).
- `describe` with an unknown tool id says so (exit code 2) instead of a traceback.
- About › Command line and the README use `atlas-toolkit`. In PowerShell a quoted path needs `&` before it
  (`& ".\Atlas Toolkit.exe" --version`); without it PowerShell only prints the string.

## 1.7.16 (2026-10-28)

**About window: Command line**
- A new tab for running tools from a terminal: how to start it here (the exe, or `python -m atlas_toolkit`), the
  commands (list, describe, run, self-test), the options of run, examples with real tool ids taken from the
  installed tools (Copy examples; a test parses every one), and what to know: runs start from the tool's defaults
  or a preset, progress on stderr and outputs on stdout, exit codes 0 / 1 / 2, runs recorded in Activity, and no
  command for Workflows yet.

## 1.7.15 (2026-10-28)

**About window: Setup**
- A new Setup tab for the optional components: Blender and the depth environment (PyTorch in `.venv-ml`). For
  each: found or not, its path, Locate and download buttons, which tools need it or can use it, why, how to install
  it (numbered steps and commands to copy, filled in for this computer) and what to know: model weights (about
  1.3 GB each) download on first use into the Hugging Face cache, and Depth Anything V2 is non-commercial.
- Tools declare the programs they can use but run without (`optional`, in the tool contract): Ribbon Wrap 1.0.1
  and Surface Peel 1.0.1 (Blender), Photo Hatching 1.0.1 (the depth environment). The Tools tab shows them.
- The window describes the toolkit as a standalone app: no gallery or studio-only references; the System card and
  Copy diagnostics list every runtime. Credits list the optional parts and their licences.

## 1.7.14 (2026-10-28)

**Rods 1.0** (new, Mesh → mesh)
- The mirror of Holes: rods are added to a closed model in one exact union (manifold3d), so the line where a rod
  meets the skin stays crisp on paper. Rods run Through the model (sticking out at both ends, each end's Reach drawn
  on its own) or grow Outward from it (rooted a little inside, Root depth).
- Kinds: rods of any profile (circle, an n-sided polygon, pillow or blob; End size tapers them); hollow pipes (Wall,
  open mouths; the bore Open through the model, so you see through the head, or Plugged at the skin); cables that
  wander (Irregularity), wave and swell, and droop under gravity (a fixed rate per height, so long cables hang; one
  pointing up tips over first); spikes narrowing to a point with a thorn's curve. Ends flat, round, pointed or flared
  like a bell; collars of the rod's own profile where it crosses the skin.
- Holes' placement, reused: lattice (one to three axes, square or hex, woven; outward rods from one or both faces),
  radial (spokes round an axis with a spiral per row, or a burst from a point), random through points inside the
  model, or spread over its surface (aimed out of the surface, along axes, away from the centre or anywhere). Width and
  Reach in % of the height, fixed or drawn from a range; a minimum gap measured only between the parts outside the
  model; Acts on keeps rods to a band or sphere, thinner and shorter across the feather.
- Cable steering is one clamped turn per step (wander, wave and droop together), so a bend is never tighter than the
  cable at its widest. Pipe bores keep no ring in the plane of an end cap, and a plugged bore starts above the
  buried cap, so the cut leaves no pinched edges.
- Output `<name>_rods.stl`, and the rods alone as `<name>_rods_tubes.stl` (same frame, for a second pen or style).
  Presets checked through Sketch Hatching on the v17 head: Pin Cushion, Skewered, Sea Urchin, Crown of Thorns,
  Scaffold, Organ Pipes, Exhausts, Hanging Cables, Tendrils, Hex Pencils, Starburst. About 1-4 s a run.

**Holes 1.0.2**
- Wormholes are swept in pieces that overlap by two rings, each piece's seam ring shrunk 2% inside its neighbour
  (`sweep_chunks`, shared with Rods): a shared ring left coincident faces, which the cut turned into pinched edges.

## 1.7.13 (2026-10-28)

**About window** (the ⓘ button)
- A real window instead of a message box: resizable, with minimise and maximise buttons, every tab scrolls, and
  it stays open beside the app.
- Tabs: About (what the toolkit is, where its files live with Open buttons, system and Blender), Tools (every
  tool with its icon, version, input → output and summary, grouped, with a filter), Shortcuts, Changelog (one
  release at a time; CHANGELOG.md now ships inside the exe) and Credits (sci.co.plot, Claude Code, ChatGPT, and
  the open-source parts with their licences). The logo heads the window and the Changelog and Credits tabs.
- Copy diagnostics: version, Python and Qt, system, folders, Blender and the tool versions, for a bug report.
- The settings path is the running user's own (%APPDATA%\AtlasToolkit, or the portable folder), never a fixed one.
- In the search palette: About, Changelog, All tools and Credits open the window on that tab.

## 1.7.12 (2026-10-28)

**Tool list**
- Groups fold and unfold: click a header (Mesh → SVG, Mesh · model prep …); a fold-all button above the list. A
  folded group that holds the open tool turns cyan. Headers are bolder: Geist semibold, a step larger, with a count.
- Icons only (`]` or the arrow above the list): a 58 px rail of tool icons, the groups split by hairlines, the
  names in tooltips.
- Hide it completely with the new button at the top left of the window, or `[`, on every tab.
- The Workflows step list does the same (foldable headers, icons-only rail, hide), and both remember their state.

## 1.7.11 (2026-10-28)

**Plotter Vision 1.2**
- Sketch preset: the look of the Aim tab of Contour Lines, Sketch Hatching and Stipple as a drawing of its own. The
  model decimated to 20K faces, only creases sharper than 35° and outlines, one pen: a loose, faceted sketch with
  moderate detail. A test keeps it equal to the Aim view's settings.

## 1.7.10 (2026-10-27)

**Dual Fusion 1.1**
- Disintegration: a front (left, right, up, down, front, back, centre, outside) crosses the first model, which
  crumbles behind it into explicit fragments, seeded cubes or convex shards, that drift (rise, on with the front, or
  scatter), shrink and tumble with their age; behind the front the second model stands (or nothing). Fragment
  cells, size, thinning and a fragment budget.
- Motion echo: moments of the passage from the first model's place to the second's (straight, arc or spiral, with a
  twist), each the morph between them at that point, the oldest thinner, smooth-unioned into one sculpture.
- Cellular patchwork: seeded Voronoi cells filled from either model, the second's share spreading with Fusion (at
  random or swept); exact walls only between cells of different models; grout and explode.
- Chrysalis shell: the second model as a shell round the first, unioned with the first inflated by a clearance so it
  always encloses it; Fusion cracks it into Voronoi plates or opens gyroid or round windows, swept from the crown.
- Stages can carry loose meshes (the fragments) and skip void filling (the cocoon's air).
- Presets: Dust to Form, Shattered Ascension, Continuity in Space, Spiral Echo, Mosaic Hybrid, Exploded Mosaic,
  Chrysalis, Cracked Cocoon. Two heads at 256: 10-46 s.

## 1.7.9 (2026-10-27)

**Ribbon Wrap 1.0** (new, Mesh → mesh)
- Wraps a model in a ribbon of silk, satin, velvet, cotton or gauze, partly or wholly, its two free ends falling
  outside the form. Output: model and ribbon in one mesh (or the ribbon alone), the fabric's grain in the geometry, so
  Sketch Hatching, Plotter Vision or Contour Lines draw it.
- Spiral (a bandage): a helix about an axis (Up, Front, Side, tipped by Tilt and Lean) between two heights, the pitch
  measured along the skin's profile so turns spread out where the form rounds over; Overlap from fully covering (30%)
  to skin showing between turns (negative); more passes come back on a steeper pitch, crossing in X's; Hand-wound
  wobble. Bands: loops where planes meet the outer skin (meridians through the axis, rings across it, seeded planes),
  each running past its start, tails leaving from the knot. Drape: a seeded walk over the skin, drawn round the axis.
- The path rides on the outer skin (rays from outside); the strip is shrink-wrapped along its own normal; Tension
  smooths it along its normal only, so it bridges hollows (eye sockets, under the chin) without sliding or narrowing;
  where it passes over itself or an earlier ribbon it is lifted a layer and ramps over the edge below.
- Tails: start and end lengths, Lift off the skin, Gravity, Sway, Twist; they slide flat over the body and rest on
  the floor; Curl winds the end into a corkscrew round the falling spine (curled gift ribbon). Tips cut, tapered to a
  point or frayed into loose threads.
- Fabric: thickness, pillowed edges (velvet), lengthwise threads (silk, grosgrain), a woven grain (cotton, gauze),
  ripples strongest at the edges and on the tails; closed, outward solids. Covered % of the skin is reported.
- Settle in Blender (optional): Blender's cloth on the ribbon (Hold pins the wrapped part, the tails fall and fold),
  through a new `cloth` operation of the Blender worker. Presets: Mummy bandage, Loose bandage, Silk scarf, Gift
  ribbon, Velvet straps.

## 1.7.8 (2026-10-27)

**Dual Fusion 1.0** (new, Mesh → mesh)
- Two models fused into one sculpture. The second model (a FileRef: a file or a workflow side port; none = the first
  one mirrored) is set upright by its own Up / Front, normalised to the first one's height and scaled by Size ratio
  (0.2-5), standing on the same base; Arrangement (side by side, face to face, back to back, stacked, overlaid), Gap,
  Turn / Move / Mirror second, Swap roles.
- Each model's signed distance is sampled once on its own padded grid (Porous Forms' libigl sampler, exact within a
  band sized by the fusion type) and read through a pose, so models move between stages with no new distance pass
  and keep their detail in a wide scene. Each stage is evaluated on its own grid, coarse first and exact only near
  the surface, then extracted, sealed pockets filled, specks dropped, smoothed and simplified.
- Convergence: eased approach, a smooth union whose radius grows as they near (bridges before contact), then past
  Melt from a blend toward the overlaid hybrid at a Balance; face-to-face pairs turn to the viewer as they melt.
- Emergence: the second model out of the first's front, or both through a wall or up from the floor, under a
  membrane (Gaussian-blurred, inflated field, smooth rim), with Breach tearing it open around the leading point.
- Split with seam: overlaid halves along a seam (left | right, front | back, horizontal, tilted, optionally wavy)
  that wipes across with Fusion; blend, carved groove, raised suture, or split open with capped sections.
- Interleaved strata: exact slabs of each model (height, width or depth), the second model's share graded across
  the model; optional gaps.
- Liquid bridge: seeded filaments between the facing sides, sagging and wavering, thinnest at the middle; they reach
  out as tapering tendrils up to Fusion 0.5, join, then thicken.
- Stage 0 returns the models themselves (exactly); strata and split return the second model exactly at Fusion 1.
- Presets, checked on v17 + David, Venus + Discobolus and two kiss sculptures: First Contact, Merging Twins, Face in
  the Wall, Emerging Self, Two-Face, Split Open, Fault Line, Interleaved Strata, Dithered Metamorphosis, Liquid Metal,
  Molten Strands. Two heads at 256: 15-40 s; Preview 4-10 s.

## 1.7.7 (2026-10-27)

**Holes 1.0.1**
- Opening the tool no longer fails with "object of type 'NoneType' has no len()": its visibility rules read a choice
  with nothing picked yet (Axes, while the page is built) as the default instead of crashing.

**Tests**
- Contract: every tool's visibility rules are evaluated with each choice unpicked, from the defaults and every preset,
  against every option of the other choices.

## 1.7.6 (2026-10-27)

**Holes 1.0** (new, Mesh → mesh)
- Pierces a closed model with solid cutters taken away in one exact boolean (manifold3d): through tubes, blind bores
  (flat, round or pointed end, optional taper), bubbles (sphere, faceted crystal, superellipsoid or lumpy blob; craters
  where they meet the skin, voids inside it) and wormholes (a heading that wanders, a regular wave, a swelling radius,
  bends kept wider than the tunnel, built in pieces so a path that crosses itself stays valid).
- Layouts: lattice (lines along 1-3 axes on a square or hex grid, woven so the families never meet; cubic or
  close-packed bubbles), radial (spokes around an axis with a spiral per row, or from a point; rings or shells of
  bubbles), random inside the model, or spread evenly over the surface (aimed along the normal, the lattice axes, at
  the centre or anywhere; surface bubbles sunk by a share of their radius).
- Profiles: circle, polygon, pillow (superellipse) or a seeded blob (flower lobes plus noise), all of the circle's
  area so Size means the same for each; stretch, turn, twist along the hole. Sizes in % of the height, fixed or drawn
  from a range (even, mostly small, mostly large); bore depth and wormhole length ranges.
- Irregularity (0-1) scales draws made once from separate seeded streams, so the same seed goes smoothly from a
  regular lattice to chaos (position, direction and shape weights in the advanced settings); Leave out drops a share.
- Minimum wall: holes are accepted largest first and kept that far apart (segment distance of their skeletons),
  shrinking down to Size before they are dropped; 0 lets them merge. Acts on clips the cutters to a band or sphere.
  Loose pieces counts bodies (a void's shell is not a piece) and keeps the voids of the bodies it keeps.
- Models that are not closed are closed by resampling first. Preview cuts a simplified model with fewer segments; a
  Cutters view shows what was taken away. Presets: Swiss Cheese, Cratered Rind, Perforated Mask, Woven Block, Lantern,
  Drilled Surface, Honeycomb Bores, Wormholes, Wavy Channels, Cloud Tunnels, Pierced Crown.

## 1.7.5 (2026-10-27)

**Reaction-Diffusion Growth 1.0** (new, Mesh → mesh)
- Grows a Gray-Scott pattern over the model's surface and turns it into relief: coral ridges, fingerprint channels,
  a maze, cells that divide, a honeycomb of pits, pebbles, lichen plateaus, or custom feed and kill. The chemicals
  diffuse over the mesh itself (nonnegative cotangent weights over lumped areas), so the pattern follows the form
  and separate pieces never exchange anything; open rims are no-flux and the relief eases out at them.
- Diffusion is semi-implicit, solved by Jacobi sweeps that are each a weighted average: bounded at any step, slivers
  included, with no factorisation (about 3 ms a step for 120k vertices). Each regime's wavelength was measured once
  on a grid; the diffusion is set from the wavelength asked for (% of the height) and the surface is refined, or a
  dense scan simplified, to ~7 samples per wavelength, so the scale holds across models and mesh densities (tested).
- Seeded spots, scattered or planted by a wave from the crown, face or base so a colony creeps over the model.
  Channels can wrap around the model or run up it (anisotropic diffusion); a second pattern can take over up the
  height; outside Acts on (band, sphere) the kill rate rises so the pattern stops at a natural edge.
- Relief raised, engraved or both, Depth in % of the height, Sharpness from soft swells to terraces, Balance for
  thicker or thinner ridges, along smoothed rest normals; the output is refined to 10 samples per wavelength.
  1-8 stages are moments of one simulation (closer together early on, normalised by the last), stage 0 the source.
- Presets, checked on the v17 head through Sketch Hatching and Contour Lines: Coral Ridges, Fingerprint Marble,
  Cellular Skin, Brain Coral, Pitted Mask, Lichen Crown, Coral to Cells, Creeping Coral sequence. Preview
  simplifies and simulates at 5 samples per wavelength; Run refuses over 800 000 simulation vertices and says what
  to raise. Works as a Workflows step like any tool.

## 1.7.4 (2026-10-27)

**Shape Fusion 1.0** (new, Mesh → mesh)
- Transformations between different shapes without matching vertices: both are sampled as signed distances on one
  padded grid over their joint bounds (Porous Forms' libigl sampler), so the result can change topology. Modes:
  Morph (the surface between the two fields), Fuse (smooth union), Carve (smooth difference), Intersect (smooth
  intersection), with a smooth radius in % h, and Swell to keep shapes that overlap little from thinning half way.
- Targets: another model (a FileRef, so a workflow's side port), placed at the same height on the same base, at the
  same size, or where its file puts it (target up / front for its own axes); the model itself, sampled from its own
  field (fast); or a built-in torus, sphere, block or column (exact distances). Turn, scale, offset and copies
  evenly about the vertical axis (Janus heads, three faces, armillary rings).
- Sweep moves the change across the model as a soft front (from the base, top, front, back, sides, centre or
  outside); Acts on limits it to a band or a sphere. 1-8 stages; stage 0 is the source and Morph's last stage the
  placed target, both returned exactly (not remeshed). Sealed pockets of air between overlapping shapes are filled
  and single-cell specks dropped, so fused results are one closed solid. Empty results and poorly overlapping
  morphs name the controls that fix them (Offset, Fit, Scale, Swell).
- Presets, checked through Contour Lines on the v17 head: Halfway Hybrid, Directional Metamorphosis, Organic Union
  (with a target model), Ring Metamorphosis, Haloed Bust, Janus, Three Faces, The Kiss, Bored Through, Hewn
  Crystal. Run at 256: 8-25 s on the 127k-face head; Preview (128) 2-15 s.

**Workflows**
- A FileRef chosen in a step's settings (not fed by a side port) now keys the step's cache by the file's size and
  modification time, so changing that file re-runs the step.

## 1.7.3 (2026-10-27)

**Surface Peel 1.0** (new, Mesh → mesh)
- Cuts a model's skin into panels and peels each one back about a root that stays attached: a head opening like a
  flower, a figure shedding curled plates. Petals are sectors about a bloom axis (crown, base, face, back, sides)
  in rows, staggered by half a petal, with wavy edges and scalloped tips; Scales are Mesh Shatter's seeded
  geodesic patches. Panels are split into connected pieces, slivers merged, and their seams relaxed from triangle
  zig-zags into curves without folding a face.
- Every panel gets its distance along the surface from its root (Bloom: the edge far from the pole, so the tips
  fall open; Peel: the edge at the pole, so the far ends lift) and a smooth spine of rotation-minimising frames.
  The spine is rebuilt with Unroll (straightening out of the body's curvature), Open (a hinge turn past the root)
  and Curl (rolling back, tightening toward the tip, over 360° into a scroll); the root share never moves.
  Per-panel variation and opening order (together, pole first, far side first, random) are seeded at rest, so the
  stages of a sequence are one motion; stage 0 is the source.
- Panels become closed shells (built in: offset inward along smoothed normals plus rims; or Blender's Complex
  Solidify through the existing worker's new `solidify` op). Layers nest smaller copies that open less, a rose of
  heads, with an optional intact core. Acts on (whole model, height band, sphere) leaves whole panels shut, and the
  shut ones stay one piece without seams. Long edges are split first so low-poly models curl smoothly; Preview
  simplifies and skips that; Run refuses over 6 M faces a stage and says what to lower.
- Presets, checked through Plotter Vision (flat angle 25°) on the v17 head: Marble Blossom, Unfurling Shell,
  Armored Scales, Rose of Heads, Opening Mask, Shedding Crown, Blossom sequence (four stages, the crown first).
  About 3-4 s for the 127k-face head (9 s for three layers). Works as a Workflows step like any tool.

## 1.7.2 (2026-10-27)

**Porous Forms 1.0** (new, Mesh → mesh)
- Turns a closed model into a continuous labyrinth inside its own silhouette. The model is sampled as a signed
  distance on a padded grid (libigl, fast winding number sign; exact near the surface, interpolated from a coarse
  grid elsewhere), filled with a periodic pattern, cut by the envelope and extracted closed (Lewiner marching cubes).
- Patterns: gyroid, diamond, Schwarz P and Neovius walls (first-order distance to the zero set, so thickness is in
  model units) or struts (rods around each channel's skeleton, a space frame), and seeded Voronoi cells (walls,
  optional windows, or edge struts). Cell and wall in % of the height; pattern turn, shift and geometric cell
  grading from base to top.
- Porosity from solid (0: the model itself) to the full pattern; Porous depth keeps a solid core behind the
  openings. Acts on (whole model, height band, sphere) keeps the rest solid, the walls thickening across the
  feather until they close. 1-8 stages share one set of fields; stage 0 is the source.
- Walls need three grid samples: Run refuses thinner ones and names the Resolution or thickness that works (Preview
  warns). Open meshes warn, volumeless ones are refused; the report gives the sample spacing, pieces and volume
  kept. Loose pieces are kept unless asked otherwise; Taubin smoothing and an output face budget (600k).
- Presets, checked through Sketch Hatching on the v17 head: Porous Marble, Exposed Labyrinth, Solid to Cellular
  (four stages opening from the top down), Open Skull (the face kept solid), Trabecular Bone, Cellular Foam,
  Diamond Lattice.
- New dependency: libigl 2.6.3 (bundled in the exe).

## 1.7.1 (2026-10-27)

**Flow Warp 1.0** (new, Mesh → mesh)
- Pulls a model through turbulent currents. Every vertex is carried along one smooth velocity field by Runge-Kutta 4
  integration, so the surface folds and stretches but stays connected. The field has three parts: curl noise
  (divergence-free eddies, with Swirl size, Turbulence over up to four octaves, and a seed), a Lamb-Oseen vortex
  (axis through the front, up or side, centre, core radius, reach, and a draw-in that winds it into spiral arms), and
  a directional pull combed into strands by Streaks. Travel and lengths are % of the model's height.
- Anchor base keeps a plinth fixed, with a smooth blend. Acts on (whole model, height band, sphere) uses the shared
  influence controls. Weights are taken at rest, so the stages of a sequence are samples of the same trajectories.
- Step size depends on the field only, so a stage shared by two sequences has the same shape and tighter steps
  converge. Long edges are split conformingly (no T-junctions) to the flow's finest feature, capped at 2 M faces;
  Preview skips the splitting and says so. The report gives edge stretch and collapsed slivers, with warnings.
- Presets: Marble Current, Vortex Portrait, Windblown Figure, Breaking Wave, Rising Smoke, Current sequence (four
  stages). Tuned on the v17 head through Contour Lines, its best pairing. Works as a Workflows step like any tool.

## 1.7.0 (2026-10-27)

**Strata Sculpt 1.0** (new, Mesh → mesh)
- Cuts a model into an ordered stack of capped slabs along one direction (horizontal, front to back, side to side,
  tilted up to 80°) and dislocates them as a stack. Amount grows everything together: the slabs thin from the full
  model to the retained thickness, lift apart from an anchor (base, centre, top), follow a sideways wave (back and
  forth, or orbiting into a helix), turn about the stack axis, and, seeded, a share of them slip, spin and tilt.
  Amount 0 is the untouched model; full-thickness slabs that stay in place rebuild its volume; ring sections keep
  their holes; separate parts and oblique cuts stay capped. One stage or 1-8 stages published as a stage set.
- Presets: Floating Marble, Spiral Dislocation, Seismic Shift, Gallery Sections, Ruined Crown. Draw it with Plotter
  Vision (a 20-30° edge angle keeps the slab rims and the outline) or Contour Lines. Usable as a workflow step.
- `<name>_strata.stl`, signed `atlas-toolkit:mesh-strata`; about 3 s for a 127k-face head.

**Shared**
- `influence_params()` (core) and `geom/influence.py`: a mesh tool can act on the whole model, a feathered height band
  or a sphere, with Invert.
- `geom/cutting.py`: capped plane cuts (`split`, bisecting `partition`), taken out of Grid Slicer. The manifold3d cap
  triangulation (`best_engine()`) keeps the holes of ring-shaped sections, which earcut loses.

**Grid Slicer 3.1.1**
- Cuts through the shared `geom/cutting.split`; same blocks as before.

## 1.6.2 (2026-10-27)

**Workflows**
- The left panel lists saved workflows by their name (the one set on the canvas), not by their file name; two with
  the same name show their file names too, and search finds either. The tooltip still gives the file.

## 1.6.1 (2026-10-27)

**Grid Slicer 3.1**
- Shuffle: a share of the blocks trade cells with each other (anywhere, within their layer so the stack still stands,
  or within their column), and every block shifts at random by up to a share of a cell along X Y Z, X Y or Z. Seeded;
  both at 0 (the default) give the same blocks as before. Presets Shuffled 4 × 4 and Scattered layers.

## 1.6.0 (2026-10-27)

The 3D tools of the earlier Blender pieces (the melting and twisting heads; the Augustus sketch; Digital divide's
stipple), for any model. `docs/PARAMETERS_3D.md` ranks their
settings by measured effect and gives the recipes that reproduce the pieces.

**Contour Lines 1.0** (new, Mesh → SVG)
- Horizontal slices seen through a camera, hidden lines removed by ray casting: the heads' line language. Spacing in
  mm on paper (at the look-at point), short hidden gaps bridged, converging lines tapered off (`min_gap`), strokes
  chained per contour and across, fragments dropped; the pieces' functions, vectorised (v17 head: ~1 s, Augustus
  319k faces: ~1.5 s, where Blender took minutes).
- A stage set (Twist, Melt, Mesh Morph) gets one frame: every plate the same scale, position and slice heights.
- Aim in the **Aim** tab (Plotter Vision's live view). Presets: Plate, Sequence, Melt sequence, Dense, Open.

**Sketch Hatching 1.0** (new, Mesh → SVG)
- The Augustus sketch: unbroken lost-and-found outline, inner and suggestive contours, valleys and accents, creases;
  hatching along the form's main curvature in tone bands (Jobard & Lefer), crossing in the darks; key light with
  soft cast shadows and ambient occlusion; a focus point with its own portrait light; hand wobble and a tapered
  double-pass outline; two pens; complexity knob, ink budget, coverage check. Presets Minimal, Classic, Engraving
  (the Augustus plates' values), Portrait focus. Complementary to Hatch Maker (perspective camera, shadows, AO,
  curvature lines).

**Stipple 1.0** (new, Mesh → SVG)
- Digital divide's sphere for any model: blue-noise variable-radius dots (random sequential adsorption) from the
  ray-cast tone (wrap light, bounce, cast shadows, optional ambient occlusion, edge darkening), empty highlights,
  noise patches, a dot budget, an optional outline pen. Presets Soft, Solid (the two spheres), Engraved shadow, Airy.

**Twist 1.0** (new, Mesh → mesh)
- The pieces' twist field with numpy, no Blender: torsion about the up axis, S-sway, (spiral) ripple; amplitudes in
  % of the height and degrees, frequencies in waves over the height. One stage or 1-8 stages from intact to the
  amount, ramped as the v17 sequence (exactly its values at four stages), saved in place with `stages.json`.
- New: ripple pushed out from the axis (cannot tear thin parts; the pieces' "along the surface" kept as an option),
  a pivot along the model's spine, Up/Front/extra turn for any model. Presets: v17 torsion sequence, Twist plate,
  Serpentine (Augustus), Gentle.

**Melt 1.0** (new, Mesh → mesh, needs Blender 4.2+)
- The pieces' Geometry Nodes melt (softening, voxel dissolve, sag, pool, drips and rivulets) evaluated in the
  installed Blender, headless and warm. One master Amount along the v17 four-stage melt (exact at 0, 1/3, 2/3, 1)
  and character multipliers (sag, pool, drips, drip size, dissolve, softening); generic by default, Head or bust
  adds the face terms, scaled to the head's own size and height band; smoothing counted for the mesh's density;
  stages seeded on the rest shape so drips grow in place. Presets: v17 dissolution sequence, Melting head, Any
  object, Thin figure, Gentle.

**Plotter Vision 1.1**
- Its camera and live view are shared: `geom/camera.py`, `camera_params()`, and the Aim tab of the new tools
  (`aim.py`). Drawings are unchanged (byte-identical paths on the reference scene).

**Mesh Morph 1.0.1**
- Publishes its stages with the shared `geom/stages.py` (same files, same all-or-nothing behaviour).

**Framework**
- `Tool.requires` and `core/runtimes.py`: external programs a tool needs. The tool page shows a notice with
  "Locate …" and keeps Run off until it is found; self-test and contract tests skip such a tool with the reason;
  the command line refuses with the hint.
- The Blender runtime (`geom/blender.py`, `blender/`): JSON lines tagged on stdout, meshes as `.npz`, cancel between
  steps or kill, a Windows job object so Blender dies with the worker, exit on stdin EOF.
- Shared 3D geometry: `geom/frame3d.py` (up/front/turn, stage-set frames), `geom/stages.py`, `geom/raycast.py`
  (embree driven directly, batched visibility with bisection), `geom/curvature.py`, `geom/shading.py`,
  `geom/sketch.py`; `camera_params()`, `frame_params()`; `svgout.order_indices`; `mesh.simplify_faces`.
- `scripts/param_sweep.py` (dev): sweeps every setting of a tool on several models (blur and metric per tool, seed
  noise floor), contact sheets and a ranking. Its findings set the forms: Ambient (Sketch Hatching, Stipple) and
  Dissolve grain (Melt) became primary; Outline weight, Hand wobble and Stipple's Edge darkening advanced. It also
  caught Melt failing on the largest seed (Blender's int sockets), fixed.

## 1.5.0 (2026-10-26)

**Workflows** (new: a visual pipeline builder, in the style of the QGIS model designer and Dinamica EGO)
- **Build:** a Workflows tab (Ctrl+3) with a canvas of steps joined by arrows. Inputs (the files of a run, or a
  fixed file), every tool, and Save nodes come from a searchable list, by drag or double-click, connected to the
  selected step when the kinds match. Tab adds a step at the cursor; letting go of an arrow on empty canvas offers
  only the steps that take it. Branches and side inputs work: a tool's file parameters (Shape Cutter's shape,
  Photo Hatching's depth) are ports another step can feed.
- **Steps are their tools:** the same presets (yours included) and the same form as the tool's page; nothing is
  special-cased, so new tools and plug-ins appear on their own. **Aim in tool…** prepares a step's input, opens it in
  the tool's own page (Hatch Maker's view, Plotter Vision's live camera) and brings the settings back.
- **Check, then Run:** Check lists problems (loops, kinds that do not fit, missing files or tools, unknown name
  placeholders, two Saves on one file, tool versions) on the canvas and in Problems; Run is enabled only once it
  passes, and any edit asks for a new Check. One job per input file, in the worker process; Cancel always works.
- **Temporary unless saved:** intermediates live in a content-addressed cache (default 5 GB, oldest dropped first),
  so a step whose tool, settings and inputs did not change is not run again; only Save nodes write to your folders,
  named by the chain of tool suffixes (`head_shattered_25pcs_hatch--nooverlap.svg`) or a template.
- **Preview up to here** (P, double-click) shows any step's result: the real output when cached, else a draft.
- **Workflow parameters:** right-click a setting's name in a step to show it on the workflow's own panel.
- **Files:** `.atlasflow` (JSON) in the settings folder's `workflows`, listed in the step library, Home and the
  command palette; undo and redo; the current workflow is kept as a draft across restarts; runs go to Activity.

**Framework**
- `core/workflow.py` (graph, Check, cache, runner) and `Job.workflow`/`run_job()`: the lanes run workflow jobs
  like tool jobs. The self-test runs a workflow in the worker process.
- `PresetBar` shared by tool pages and workflow steps; one `ToolSettings` per tool for the whole app;
  `Tool.matching_preset()`; `ParamForm` label menus and marks; tool pages can be borrowed by a workflow step.

## 1.4.1 (2026-10-26)

**Hatch Maker 6.0.1**
- Presets keep the view: azimuth, elevation and roll stay where you put them, and changing them no longer shows
  "Custom settings".

## 1.4.0 (2026-10-26)

**Plotter Vision 1.0** (new: plotter.vision by Trammell Hudson, reimplemented from its method, no code copied)
- **Live Vision tab, plotter.vision's interface:** drag to orbit at its speed, Shift/right-drag to move the look-at
  point, wheel for distance (Ctrl: field of view), keys 1-5, arrows, F fit, R start view, T stereo. Candidate edges
  while moving, with its XYZ marker; hidden lines removed in the background as soon as you stop, filling in from
  the middle. The frame is the paper's drawing area (WYSIWYG); the camera returns to the settings on release.
- **Same lines as plotter.vision:** its camera (θ, ψ, distance, look-at, 60°, the model scaled to 100), back-face
  culling and coplanar-edge removal. A reference scene, rendered by its own JavaScript, matches line for line.
- **Exact hidden lines** (closed-form intervals per edge and face, numpy): fixes edges inside other solids drawn,
  hidden lines leaking at the window edge (faces with all corners off screen were skipped) and under tiny faces
  (< 2 px² were skipped), faces crossing the near plane dropped, endpoints moved by in-place clipping, and edges
  drawn twice. 347K faces in 4 s.
- **Improvements:** one angle chooses the edges (0.01° plotter.vision, a few degrees cleans quads, 30°+ keeps
  creases and outlines); lines where separate parts cross; decimation to a face budget (fast_simplification);
  consistent winding; open meshes hide from both sides; outline, depth (near/middle/far, far dashed) and hidden
  (dashed back edges) pens; red/blue stereo layers; orthographic; shortest piece and stroke filters; mm output
  joined, simplified and ordered.

**Framework**
- A tool may bring an interactive preview widget (`viewer`): the stage hosts it as the first tab and passes it
  theme, art mode, Fit and settings changes.
- `preset_skip`: keys presets neither set nor compare (a camera stays where you put it).

## 1.3.0 (2026-10-26)

**Mesh Morph 1.0** (new, from Processing's shape_mesh_morph_MULTI_v03)
- Import any supported mesh instead of generating a primitive. Each stage subdivides triangles into four and
  independently distorts their corners, preserving detached fragments and cumulative deformation.
- Seeded random, normal or radial displacement; model-unit or original-diagonal strength; growth and XYZ influence.
- Original, intermediate and progression previews; final or combined STL/OBJ/PLY output and optional unshifted
  stage files. Layout clears actual bounds; every mesh is signed for Atlas and can be sent to Hatch Maker.
- A two-million-face preflight budget preserves dense inputs, reports the supported step count and never silently
  simplifies. Whole-triangle preview sampling keeps fragments intact. Exports are prepared in temporary files
  before publication, and existing stage folders are preserved.
- Uses the existing discovery, worker, forms, presets, history and design system without shared contract changes.
- Long segmented-control labels now show an ellipsis instead of clipping their middle; full names, actions and
  tooltips are retained. Large mesh exports are batched so cancellation remains responsive.
- Builds resolve Windows system DLLs before unrelated Poppler/Conda DLLs on PATH, preventing an incompatible
  ICU library from breaking Qt startup. The one-file app is staged, self-tested and GUI-smoke-tested before
  atomically replacing the installed executable.

## 1.2.0 (2026-10-24)

**Photo Hatching 1.0** (new, from the Colab notebooks DPT_Transformer, DPT_Hatching_generator, hatching_DPT_Large_2
and _3)
- **Depth on the GPU:** MiDaS 3.1 BEiT-L-512 (default), DPT-Large or Depth Anything V2 Large, fp16, in a separate
  runtime (`scripts/setup_ml.py`, `.venv-ml`) kept warm by the tool; the exe stays small. The photo keeps its
  aspect ratio (the notebooks squashed it to 384 × 384; DPT-Large in transformers only accepted squares, now
  patched). Detail native, high or boost. Depth cached per photo; a depth-map file or a rough guess work without a
  GPU.
- **Flow hatching** (new default): evenly spaced strokes that follow the depth's isophotes (wrapping the forms),
  traced once each; ink density linear in tone; crossing layers only below a tone, at a lozenge angle; strokes stop
  at occlusion edges; tone smoothed at the stroke scale so textures give long strokes, not dashes; seeded hand
  jitter.
- **Patches** (notebook d) fixed: superpixels now follow the photo (scikit-image's feature rescaling had turned them
  into a square grid, in the notebook too), orientation averaged as doubled angles (opposite directions no longer
  cancel), continuous spacing, one global line phase, no seams.
- **Contours** (notebook c) fixed: equal-area or equal-depth levels, working bias, no lines along occlusions.
- **Outlines:** occlusion edges from depth and optional photo edges, each traced once (the notebooks drew them
  twice).
- **Output:** fitted to paper in mm, no background rectangle, one layer per pen (optionally one per direction),
  strokes joined and ordered. Previews: Depth, Tone, Drawing, Over photo.

**Framework:** an `image` input kind (photos) and raster previews on the paper.

## 1.1.0 (2026-10-24)

**Shape Outline 2.0** (new in the toolkit, from SVG_Shape_outline_v1)
- **Methods:** envelope (new, the default) follows the strokes, bridging gaps under a set width in mm and filling
  what they enclose; concave hull with a scale-free concavity (v1's alpha depended on the drawing's size and was
  labelled backwards); convex hull.
- **Reads every element** (curves, lines, polylines, rects, circles, ellipses) with transforms, in mm; v1 read only
  paths and polygons and ignored transforms. One layer can be outlined on its own.
- **Shape:** all parts or only the largest (v1 kept only the largest), optional holes, minimum area, a margin in mm,
  simplification.
- **Output** on the same sheet as the drawing (v1 wrote no viewBox, so mm drawings came out at the wrong size), as
  the outline alone or as a new layer; it lines up with the Shape Cutter's “Another SVG” shape.
- alphashape and svg.path are no longer needed.

**Framework:** a tool parameter may no longer use a key the command line already has (`output`, `out_dir`, …).

## 1.0.1 (2026-10-24)

- The inspector (right panel) can be resized by dragging its edge; the width is remembered. Fields shrink instead
  of being cut off, and fields with several boxes (copies, spacing, files) get their own line.

## 1.0.0 (2026-10-23)

First release: seven tools in one app, in the Atlas design system.

**App**
- **Pages and windows:**
  - Atlas layout: top bar with search and command palette, tool sidebar, Home, Activity;
  - tool pages with an input queue, a 2D or 3D preview, stat tiles, report and log;
  - a dark and a light theme, and paper or blueprint art.
- **Running tools:**
  - presets (built-in and your own), remembered settings, AUTO values;
  - a run history with re-run, "Send output to" to chain tools, and settings restored from an output's metadata.
- **Command line and testing:** the CLI is generated from the tool specs, and a self-test runs every tool.
- **Extending:** plug-in folders, and `scripts/new_tool.py` for new tools.
- **Shipping:** one-file exe with a splash screen, built and self-tested by `scripts/build.py`.

**Overlap Cleaner 1.2** (from SVG Overlap Cleaner v1)
- With its defaults the engine gives output byte-identical to v1, checked on 15 project drawings.
- **Optional stages:**
  - join stroke ends (replaces SVG Join Paths v1/v2, without their flattening and recolouring);
  - a whole-duplicates mode (replaces svg_dedup);
  - rect, circle and ellipse input;
  - stroke width as part of the pen.
- Progress, logging and cancelling; previews of the cut ink.

**Shape Cutter 2.0** (from svg_shape_cutter)
- Works in mm and follows transforms.
- The shape can be a named element or layer, a chosen layer, or another SVG.
- Every island and hole counts; keep inside or outside.
- Other content is kept, untouched elements stay exact, and clipped strokes are merged.

**Hatch Maker 6.0** (from STL 3D Hatch Maker v2.1, v3, C-v4, v5)
- **Styles:** all seven work: surface strokes, straight lines, depth contours, stipple, normal strokes, tone regions
  and isophotes.
- **Fixes to v5:**
  - real Poisson seeding;
  - the view now matches the 3D viewer;
  - output in mm on a chosen paper, the right way up;
  - joined polylines, ordered to cut pen travel.
- **Drawing:** one batch of rays removes hidden lines for every line type; true outlines or the grazing band;
  creases; one pen per layer; seeded randomness.

**Mesh Shatter 4.0** (from Mesh shatter v3)
- Works with trimesh 5.
- Mirrored shards are no longer turned inside out.
- Dijkstra now runs in scipy, and unreachable parts are kept.
- Same shards as v3 for the same seed.

**Grid Slicer 3.0** (from cube_grid_slicer-v2)
- Same blocks as v2.
- A plane only cuts the parts it crosses.
- The gap is a true %, and every block can be saved on its own.

**Cubic Replicator 3.0** (from STL Cubic Replicator v2)
- Same grid as v2.
- Seeded variation, spacing safe for turning, and a guard against huge outputs.

**Voxelizer 2.0** (from Voxelizer tool v1)
- Same voxels as v1.
- An option to fill the inside, and vectorised building (fast).
- Size by voxel count or edge length.
- Fixes v1's `_vox.stl.stl` name.
