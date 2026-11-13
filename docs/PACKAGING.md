# Packaging

How the installers are made and tested. For installing the app, see `docs/install/`. Nothing here publishes anything: installers end up in `dist/installers`, in CI as artifacts kept for three days, and in a draft release when a version tag is pushed.

| OS | Format | Tool | Recipe |
|---|---|---|---|
| Windows | Setup `.exe` | Inno Setup 6.7.3 | `packaging/windows/atlas_toolkit.iss` |
| Linux | `.deb` and `.rpm` | nfpm 2.48.0 | `packaging/linux/nfpm.yaml` |
| Linux | `.AppImage` | appimagetool 1.9.1, runtime 20251108 | `packaging/linux/AppRun`, `atlas-toolkit.desktop` |
| macOS | `.dmg` | dmgbuild | `packaging/macos/dmg_settings.py` |

All of them package the output of `scripts/build.py`, after that output has passed every gate. Packaging never freezes anything of its own.

## Making them

On Windows:

```bat
winget install --id JRSoftware.InnoSetup
.venv\Scripts\python scripts\package.py
```

This makes the folder build (`dist\Atlas Toolkit\`) and then the installer. `--skip-build` uses the folder already there.

On macOS, `.venv/bin/python scripts/package.py` makes the `.app` and then the DMG. The `build` dependency group brings dmgbuild.

On Linux, always work in the build image from a clean clone. It holds the glibc floor and has nfpm and appimagetool:

```bash
docker build -t atlas-linux-build packaging/linux
docker run --rm -v "$PWD:/src" -w /src atlas-linux-build sh -c "uv sync --locked --all-extras && .venv/bin/python scripts/package.py"
```

Use a clone inside the Linux file system, never a mount of a Windows folder, because `.venv`, the helpers and the line endings all differ per OS. `--only deb`, `rpm` or `appimage` makes one format. `python scripts/doctor.py` reports what is missing.

## What every installer must do

- Install without touching anything outside its own places, and start from the system's menu.
- Open `.atlasflow` workflows, `.atlaspreset` presets and `.atlaspack` preset packs, where the format can register
  them. An AppImage cannot. A preset file or pack opened this way loads into the preset library after asking.
- Upgrade in place, so that no file of an older version stays mixed in with the new one.
- Uninstall completely, and leave the user's settings alone.
- Ship the notices and the GPL helper's source. They are inside the bundle at `licenses/`, so every format has them. That folder is `_internal/atlas_toolkit/licenses/` on Windows and Linux.

## Testing them

`scripts/install_test.py` runs the same chain on a real install:

1. Install it the way a user would, and find the menu entry, file type and icon. On macOS, check the disk image's layout, the signature, the minimum version and a command-line link.
2. Run `--version`, which must match the package's name. On Linux, ask the system's loader about every library in the bundle, and check that other users can read and run every file.
3. Run `self-test --strict`, with every helper started. In a container this has no display, so it also proves that the command line works on a server.
4. Count the colours in the 3D view (`check_frozen_gui.py`), on X11 and on Wayland where the distribution has Weston.
5. Start a job stuck forever, kill the window hard, and check that the worker and its child both end.
6. Find the notices and the GPL source, and install over itself. On Windows a planted stale file must be gone afterwards.
7. Uninstall, then check that nothing is left and that the profile folders are untouched.

It installs and removes software, so it asks to be told that this is fine: `--container`, `--throwaway`, or a CI runner.

**A dependency test must not bring the dependency along.** The usual way to run a script in a clean container is to install Python, and that alone pulls in `libexpat`, `zlib` and `liblzma`, which are three of the libraries the package has to declare. So the script runs under uv's standalone Python, mounted read-only. The virtual display and software OpenGL, which pull in `libGL` and `libxcb`, are only added after the library check.

To run it locally, with Docker and no CI minutes, from the clean clone:

```bash
uv python install 3.12 --install-dir /tmp/py
docker run --rm -v "$PWD:/w:ro" -v /tmp/py:/py:ro ubuntu:24.04 \
    /py/cpython-3.12.16-linux-x86_64-gnu/bin/python3 /w/scripts/install_test.py /w/dist/installers/atlas-toolkit_<version>_amd64.deb --container
```

Use the Python folder with the full version in its name. The shorter name beside it is an absolute link, which does not resolve inside the container.

In CI, `.github/workflows/build.yml` runs it with the label `full-ci`:

| Runner | What it tests |
|---|---|
| Ubuntu 22.04, 24.04 and 26.04, Debian 12 | The deb and the AppImage |
| Fedora 45, Rocky 9 | The rpm and the AppImage |
| `windows-2025` | The installer. The runner has no GPU, so the 3D view check also proves Qt's software renderer |
| `macos-15` | The DMG. The 3D view check is soft, because the runner has no GPU |

**macOS:** none of this has been run by hand on a real Mac yet. `docs/dev/macos-verification.md` is the checklist for doing so.

## How the dependencies were chosen

The Linux bundle leaves some libraries to the system on purpose (see "The trap in frozen Linux builds" in `docs/dev/multi-os-port.md`). The list in `nfpm.yaml` is those libraries plus what Qt's X11 and Wayland plugins link against, found by reading every ELF file's `NEEDED` entries. An AppImage can't declare anything, so its test installs that same list and no more: it stands for what any desktop already has.

Some files in the bundle lack libraries on a normal system, and that is fine: the GTK theme plugin, the framebuffer and direct-to-display platforms, and a few image codecs. Qt loads them when it can and carries on when it can't. `OPTIONAL_FILES` in `scripts/install_test.py` lists them.

The rpm depends on sonames rather than package names, because Fedora and RHEL name these packages differently from release to release.

## The depth environment's installer

The installer behind Setup › Components › Install… downloads 0.2 to 3 GB, so the everyday suite never does: it tests
the whole flow against a stand-in uv in seconds (`tests/test_depth_runtime.py`). The real download is tested only for
releases, by `scripts/dev/depth_install_test.py` against the frozen app:

1. `components list`: what the computer has, and the build it would get.
2. The pinned torch wheel of every build exists for this OS and Python.
3. The real install, timed and measured against the sizes in `depth_runtime.json`.
4. A real model run.
5. Photo Hatching with depth from the model.
6. An install over itself.
7. `remove`, leaving nothing behind.

`build.yml` runs it with the input `depth: true`, which `release.yml` always passes. To try it on a branch without a
release, run the workflow by hand (`gh workflow run build.yml --ref <branch> -f depth=true`). The runners have no GPU,
so CI proves the CPU build and the "no NVIDIA card" path on all three OSes.

**Before each release, by hand, on an NVIDIA machine:** the GPU path, which CI cannot reach:

```bat
python scripts\dev\depth_install_test.py --app "dist\Atlas Toolkit\atlas-toolkit.exe" --gpu --no-reinstall
```

Also do it on Linux (in WSL, CUDA comes through Windows' driver) when the pins change.

## A draft release

Once a version is merged to `main`, tag it:

```bash
git tag v2.0.0
```

```bash
git push origin v2.0.0
```

`.github/workflows/release.yml` then does three things:

1. **Checks the tag.** It must match `version.py`, the version must have a `CHANGELOG.md` entry, and the commit must be on `main`. A mistyped tag stops here, before the long work starts.
2. **Runs all of `build.yml`:** the freeze, every gate and the install tests on all three OSes, plus the depth environment's real install on each (below). It takes a little longer than the label `full-ci`. The test suites themselves ran on the version's pull request.
3. **Makes a draft release** with all five installers, `SHA256SUMS.txt`, the changelog entry and links to the install guides (`scripts/release_notes.py`). If any installer is missing, there is no draft.

It never publishes. A draft is visible only to people who can write to the repository, and it doesn't expire as CI artifacts do.
The tag itself is public as soon as it is pushed, like the source it points to.

To mark a version as a pre-release (a tested build that is not yet announced), tick **Set as a pre-release** on the
draft, or run `gh release edit vX.Y.Z --prerelease`. Publishing a pre-release makes it downloadable without making it
the latest release.

To redo a release, delete the draft and the tag, then push the tag again.

## Decisions waiting for the distribution model

- **Maintainer:** `nfpm.yaml` has a placeholder address (`noreply@atlastoolkit.invalid`), because a deb must name someone.
- **Licence field:** it points at `LICENSE`, which says no licence has been chosen yet. Update `nfpm.yaml` when one is.
- **Inno Setup's licence:** the licence shipped with 6.7.3 permits commercial use. Its publisher also sells commercial licences, so read the terms at <https://jrsoftware.org/isorder-terms.php> again before selling anything.
- **Package repositories, winget, Homebrew, Flatpak:** not started. Each needs somewhere to host packages and a signing key.

### Signing, when it comes

There is no signing code yet: it could not be tested without certificates and would rot. The `.app` is ad-hoc signed today, which is why macOS asks for **Open Anyway**.

- **Windows:** sign the app, the console companion and the helpers before packaging, then give Inno Setup a `SignTool=` entry so that it signs the setup and uninstaller. This also stops most SmartScreen warnings.
- **macOS:** needs a paid Apple Developer ID. Sign with `codesign --options runtime --timestamp`, starting with the helpers and libraries and working outward, then submit the DMG with `xcrun notarytool submit --wait` and attach the ticket with `xcrun stapler staple`. PyInstaller apps usually need the JIT and library-validation entitlements under the hardened runtime, so check this on a real Mac.
- **Linux:** nfpm signs with `deb.signature.key_file` and `rpm.signature.key_file`. An apt or dnf repository needs its own key on top. An AppImage can embed a GPG signature with `appimagetool --sign`.
