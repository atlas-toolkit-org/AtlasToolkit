# Supported platforms

Where Atlas Toolkit runs, what sets each limit, and how much of it has really been proven.

| Platform | Needs | Installer | Status |
|---|---|---|---|
| Windows | Windows 10 or 11, x64 | Setup `.exe` | **Full support.** Automated, and used by hand with a real GPU |
| Linux | x86-64, glibc 2.34 or later, X11 or Wayland | `.deb`, `.rpm`, `.AppImage` | **Full support.** Automated on clean systems with software OpenGL; not yet tried on a real Linux desktop |
| macOS | macOS 15 or later, Apple Silicon | `.dmg` | **Built and tested automatically, not by hand.** See below |

That covers Ubuntu 22.04 and later, Debian 12 and later, Fedora, and RHEL, Rocky and AlmaLinux 9 and later.

**Not supported:** Intel Macs, Linux on arm64, and Windows on arm64.

## Where the limits come from

They are set by the libraries the app is built on, not chosen. `[tool.uv] required-environments` in `pyproject.toml` makes a library that drops a supported platform fail when the lock is refreshed.

| Limit | Set by |
|---|---|
| macOS 15 | `rasterio` 1.5.2 ships `macosx_15_0` wheels only |
| glibc 2.34 | `PySide6-Essentials` 6.12 on x86-64 |
| No Intel Mac, no Linux arm64 | `pynanoinstantmeshes` 0.0.3 has no wheel for them, and `embreex` has none for Linux arm64 |
| Python 3.12 to 3.14, from source | `requires-python` in `pyproject.toml`. Builds are frozen on 3.14 |

## What has been proven

**Every platform, on each milestone (CI):** the whole test suite, a frozen build that passes `self-test --strict` with every helper started, a bundle audit, and `scripts/install_test.py` on the real installer.

The install test checks the following:

- The 3D view really draws, counted in colours in the pane.
- Nothing keeps running after the window is killed.
- Uninstalling leaves nothing behind and keeps the user's settings.

| | Automated | By hand | Still unproven |
|---|---|---|---|
| **Windows** | `windows-2025`, with no GPU, so through Qt's software renderer | Windows 11 on an NVIDIA GPU: the app, its tools and the 3D views. The installer has been compiled but not run by hand | Other GPU makers |
| **Linux** | Clean Ubuntu 22.04, 24.04, 26.04, Debian 12, Fedora 45, Rocky 9. X11 under Xvfb everywhere, Wayland under headless Weston where the distribution has it. Mesa's software OpenGL | Not yet | A real desktop session, its menus and file associations, real GPU drivers (NVIDIA's proprietary one especially) |
| **macOS** | `macos-15`: Cocoa start, workers, helpers, minimum-version and architecture audit, signature, disk image cycle, command-line link | Nobody yet | A real GPU, Retina scaling, trackpad gestures, Gatekeeper on a downloaded file, Finder and Dock behaviour, GPU depth estimation (MPS) |

GitHub's Mac runners have no GPU. They draw with Apple's software renderer, so a real GPU has never been seen drawing, and the 3D check there is soft: it reports a blank pane without failing. `docs/dev/macos-verification.md` is the checklist that closes this gap in a few hours on any Apple Silicon Mac, including a rented one. Until someone runs it, macOS keeps the status above.

GPU depth estimation on macOS stays opt-in for the same reason (`ATLAS_DEPTH_DEVICE=mps`), and it runs on the CPU by default.

**The depth environment's installer** (Setup › Components › Install…) is proven as follows:

- **CI, every release, all three OSes, on the frozen app:** the real CPU install, a model run, Photo Hatching with model depth, a reinstall and Remove (`scripts/dev/depth_install_test.py`). The runners have no GPU, so this also proves the "no NVIDIA card" path.
- **By hand:** the CUDA 13.0 build on Windows 11 with an RTX 5070 Laptop GPU (driver 595), detected, installed and proven on the GPU.
- **Not yet proven:** the CUDA 12.6 build on a real older card, a Linux desktop with NVIDIA's driver, and a Mac by hand.

## Graphics

The 3D views need OpenGL 2.0 or later from the system or from Qt.

- **Windows with no usable driver** (a virtual machine, some Remote Desktop sessions): Qt's bundled software renderer draws them. It is slower, and enough for previews.
- **Linux:** Mesa's software renderer works if the driver cannot start.
- **If OpenGL really can't work,** a 3D view shows a message in its place. The numeric settings and every tool's output still work, because tools compute without OpenGL.

## Unsigned installers

Nothing is signed by a trusted certificate yet, since that depends on the distribution model. Windows may show a SmartScreen warning, and macOS requires **Open Anyway** on the first start. `docs/install/` explains both, and `docs/PACKAGING.md` has the recipe for signing later.
