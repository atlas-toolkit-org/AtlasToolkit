# Multi-OS port: what was proved, and how

Atlas was brought to Windows x64, Linux x86-64 (glibc 2.34 or later) and macOS 15 or later on Apple Silicon, in 2.0.0. This
is the working record of that port: the evidence behind its decisions, so nobody has to rediscover them. The support
matrix for users is in `docs/PLATFORMS.md`.

## Why these targets

The version floors come from the pinned wheels, not from a choice:

| Floor | Set by |
|---|---|
| macOS 15 | `rasterio` 1.5.2 ships `macosx_15_0` wheels only (1.5.1 still has `macosx_14_0`) |
| glibc 2.34 | `PySide6-Essentials` 6.12 on x86-64 (2.39 on arm64) |
| No Intel Mac, no Linux arm64 | `pynanoinstantmeshes` 0.0.3 has no wheel for them, and `embreex` has none for Linux arm64 |

`[tool.uv] required-environments` in `pyproject.toml` makes a missing wheel on a supported target fail at lock time.

One oddity: PySide6 6.12's wheels are tagged macOS 14, but their binding libraries say `minos 26.0`. `dyld` does not enforce that field for libraries, and CI runs them on macOS 15 through the frozen self-test, so `audit_bundle.py` notes them and fails on anything else above the floor.

## What the spikes proved (November 2026)

Throwaway CI probes on the branch `spike/multi-os`, on `ubuntu-24.04`, `macos-15` and clean containers of seven
distributions (Alma 9, Rocky 9.3, Ubuntu 22.04, 24.04 and 26.04, Debian 12, Fedora 45).

- **Helpers:** all four compile unchanged on GCC 11, GCC 13 and AppleClang.
- **Freezing:** uv's standalone Python works with PyInstaller on Alma 9 and on macos-15. No hand-built CPython is needed.
- **`.app`:** the frozen bundle passes self-test (workers, helpers, PROJ data, 45 tools with only Melt skipped for lack of
  Blender), and its ad-hoc signature verifies.
- **Numbers:** no floating-point drift on arm64 across 922 tests. Most failures found were test bugs tied to Windows
  behaviour: the order NTFS lists files, the Windows profile layout, and one timing race. One was a real app bug on every
  OS: below about 382 px of canvas height, the plate's top square took clicks meant for the round rotate handle. The Mac
  runner's smaller window showed it.
- **OpenGL on macOS:** the default 2.1 context draws the mesh on Apple's software renderer, and the Atlas shader also
  compiles in 4.1 core. **A real Mac GPU is still unproven**: GitHub's runners have none.

## Linux helpers and the glibc floor

Helpers are built in `packaging/linux/Dockerfile` (Alma 9) and link `libstdc++` statically, so they need only `libc`,
`libm` and `libgcc_s`, with no `GLIBCXX` symbols at all. `libgcc` must stay dynamic: RHEL 9 back-ported
`_dl_find_object@GLIBC_2.35`, and static `libgcc` requires it, which stops a helper starting on Rocky 9.3. As built, the
highest symbol is `GLIBC_2.34`, and the same binaries run on bare Rocky 9.3, Debian 12 and Ubuntu 22.04 containers.

Dropping OpenMP from the remesh helper changed nothing else: on a 327,680-face mesh, decimation, uniform and adaptive
remeshing gave byte-identical output at the same speed. `vcomp140.dll` is gone.

## The trap in frozen Linux builds

PyInstaller bundles the build system's `libstdc++` and `libgcc_s`, and the bootloader's `LD_LIBRARY_PATH` puts them ahead
of the system's. Mesa and LLVM need newer ones (`GLIBCXX_3.4.30`, `3.4.32`), so OpenGL cannot load:

| System | Result with both bundled |
|---|---|
| Ubuntu 24.04 and 26.04, Fedora 45 | Abort on any 3D page under X11 (`Could not initialize GLX`), a blank pane under Wayland |
| Debian 12, Ubuntu 22.04 | A silently blank 3D pane |
| Rocky 9.3 | Will not start: Alma 9.8's `libgcc_s` wants a newer glibc symbol |

**Keep out of the bundle:** `libstdc++`, `libgcc_s`, `libz`, `libzstd`, `libexpat`, `liblzma`. Removing the first two
fixed rendering on all seven distributions.

**Keep in the bundle:** `libxml2` and `libbz2`, whose sonames differ between distributions. Removing `libxml2.so.2` stops
the app starting on Ubuntu 26.04 and Fedora 45.

`packaging/atlas_toolkit.spec` applies this, and `scripts/audit_bundle.py` fails a build that breaks it.

A screenshot that exists proves nothing: every older check passed with a blank pane. `scripts/check_frozen_gui.py` counts
the colours in the 3D pane instead: over 200 on a real GPU, around 560 on llvmpipe, 940 on Apple's software renderer, and 1
when blank.

The Linux folder is about 550 MB (about 190 MB compressed). Nearly all of it is native libraries the tools need (rasterio,
scipy, numpy, embreex, ICU), so trimming Qt further would save little.

## Windows with no GPU

A Windows machine with no usable driver includes virtual machines, some Remote Desktop sessions and GitHub's runners. Qt falls back there to its bundled Mesa, `opengl32sw.dll`, but PyOpenGL always loads the system's `opengl32.dll`, where no context is ever current. So every call fails with `invalid operation`, starting with `glClearColor`.

The same happens on any Windows machine with `QT_OPENGL=software`, so this can be reproduced and tested without a GPU-less machine. `scripts/build.py` checks that case on every Windows build.

- **The fix:** `_follow_qts_software_renderer` in `ui/app.py` creates one context, which is what makes Qt choose, and if it chose Mesa, it points PyOpenGL at that library. It must run before anything imports `OpenGL.GL`. The first context costs about 240 ms at startup on a real GPU, a cost the first 3D page would otherwise pay. On Mesa the view draws at Mesa 11.2's OpenGL 3.0, and every shader compiles.
- **A paint failure used to be far worse than a blank pane:** Qt hands the exception to `sys.excepthook`, the crash dialog runs its own event loop, the next frame fails inside it, and the dialogs stack with no way out. Now `GuardedGL` stops a view at its first failure, and `test_gl_failure` checks that every canvas has the guard.

`check_frozen_gui.py` also had a gap. On that runner's 1024x768 screen, the page's antialiased text fell inside the box it measured, and a blank pane scored 547 colours. The box is now the middle of the pane.

## macOS and files on the command line

AppKit turns file names on the command line into "open file" events as well. Qt drops those only if they are in `qApp->arguments()`, and the app starts `QApplication` with just the program's name, so they arrive. Handling them meant opening a file twice, and worse, opening it inside AppKit's launch callback. For a model many tools take, that shows a menu, whose nested loop leaves `NSApp` with the wrong run count, and the app aborts on quitting: `NSApp with wrong _running count`. A double-click in Finder comes through the same callback.

`FileOpener` now skips each command-line file once, and always opens files on the next turn of the loop.

**The test suite cannot see this,** because it pumps events and never enters `[NSApplication run]`. Only a real `app.exec()` does, which is why `check_frozen_gui.py` found it. M4 passed that step because it had no file-open handling yet.

## Running the tests

- With Linux's `offscreen` Qt plugin the 3D views do not draw, so CI uses `xcb` under Xvfb with
  `LIBGL_ALWAYS_SOFTWARE=1`, and `cocoa` on macOS. `offscreen` next to a display gets an OpenGL that fails or crashes, so
  `_isolate` hides the display from Qt whenever it runs offscreen.
- Every test module starts with `import _isolate`, which keeps settings and caches out of real profiles on every OS
  (`ATLAS_TOOLKIT_HOME`). `tests/test_portability.py` checks this.
- CI is rationed because the repository is private: test locally first, and add the label `full-ci` to a pull request
  once per milestone to run macOS and the oldest Python.
