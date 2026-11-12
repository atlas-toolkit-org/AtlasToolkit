# Building Atlas Toolkit

From a clean machine to a tested build, on each OS. For using the app, see `docs/install/`. For how installers are made, see `docs/PACKAGING.md`. `CLAUDE.md` has the working rules for changing the code.

## What you need

`python scripts/doctor.py` checks all of this on any OS, changes nothing, and prints the command that closes each gap. It needs only Python.

| | Windows | Linux | macOS |
|---|---|---|---|
| Python 3.12 to 3.14 | Yes | Yes | Yes |
| `uv` | Recommended | Recommended | Recommended |
| Git | Yes | Yes | Yes |
| C++17 compiler | Visual Studio Build Tools, x64 C++ tools | `g++` | Xcode command-line tools |
| CMake 3.24+, Ninja | Come with Build Tools | `cmake ninja-build` | `brew install cmake ninja` |
| Installers (only for `package.py`) | Inno Setup | nfpm, appimagetool: in the build image | dmgbuild: in the lock |
| Docker (optional) | | For release builds and clean-system tests | |

On Windows, keep the checkout's path short (for example `C:\src\AtlasToolkit`) or enable long paths. PySide6 installs files nested deep enough to pass Windows' 260-character limit from a long folder.

## From source

The commands are the same on every OS. On Windows use `.venv\Scripts\python`, and on Linux and macOS use `.venv/bin/python`.

```bash
python scripts/setup_env.py
```

This creates `.venv` with the exact pinned versions, using `uv` if it's there and `pip` otherwise.

```bash
.venv/bin/python native/build.py
```

This builds the four C++ helpers into `src/atlas_toolkit/bin`. It fetches their pinned sources the first time, then builds incrementally. See `native/README.md`.

```bash
.venv/bin/python -m atlas_toolkit
```

This runs the app. `scripts/run.bat` and `scripts/run.sh` do the same.

## Testing

```bash
.venv/bin/python scripts/dev/run_tests.py
```

This runs each test module in its own process, as CI does. `python -m unittest discover -s tests` also works, in one process.

- **Settings stay out of your profile:** every test module starts with `import _isolate`, and `test_portability` checks that it does.
- **Linux:** Qt's `offscreen` platform has no OpenGL, so tests that draw 3D need a real one. Run the suite under Xvfb:

  ```bash
  QT_QPA_PLATFORM=xcb LIBGL_ALWAYS_SOFTWARE=1 xvfb-run -a -s "-screen 0 1600x1000x24" .venv/bin/python scripts/dev/run_tests.py
  ```
- **Helpers:** tests that need them skip when they aren't built. `ATLAS_TEST_REQUIRE_HELPERS=1` makes that a failure, as in CI.
- **Lint:** `.venv/bin/ruff check .` Never run `ruff format`.

## Building the app

```bash
.venv/bin/python scripts/build.py
```

This builds the helpers, runs the tests, freezes the app with `packaging/atlas_toolkit.spec`, runs every check on the result, and publishes it to `dist/` only if they pass. A failed check leaves the previous build in place.

| OS | Result in `dist/` |
|---|---|
| Windows | `Atlas Toolkit.exe` and its console companion `atlas-toolkit.exe`. `--onedir` makes a folder instead |
| Linux | A folder, `atlas-toolkit/` |
| macOS | `Atlas Toolkit.app` |

The checks are `self-test --strict`, a frozen start, the console, `scripts/audit_bundle.py`, the frozen raster and DEM checks, and on Windows the 3D view through Qt's software renderer.

**A Linux build for other people comes from `packaging/linux/Dockerfile`.** A build on your own desktop only runs on systems as new as yours. The image sits on the glibc 2.34 floor:

```bash
docker build -t atlas-linux-build packaging/linux
docker run --rm -v "$PWD:/src" -w /src atlas-linux-build sh -c "uv sync --locked --all-extras && .venv/bin/python scripts/build.py"
```

## Linux from a Windows machine

WSL2 with Ubuntu 24.04 and Docker Engine inside it does everything except a real desktop.

- **Use a clone inside WSL, never a mount of `D:\`.** `.venv`, the helpers and the line endings all differ per OS. A clone can fetch your local branches from the Windows checkout (`git remote add origin /mnt/d/...`), so no push is needed.
- **Docker Hub pulls may fail with "network is unreachable".** WSL can lack an IPv6 route, and dockerd doesn't fall back to IPv4. Turn IPv6 off with a file in `/etc/sysctl.d/`.
- **From PowerShell, run WSL commands from a script file.** Inline `wsl -- bash -c` loses `$` and quotes.

## CI

The repository is public, so GitHub's standard runners are free. A full run still takes about half an hour, so test
locally first, Windows and then Linux, and push batches that already pass.

| Runs | When | What |
|---|---|---|
| `ci.yml` | Each pull request that changes code | Lint, then the Linux and Windows suites |
| `ci.yml` | Each pull request that changes only Markdown or `LICENSE` | Lint, then the documentation tests (`docs`, a couple of minutes) |
| `ci.yml` | Label `full-ci` | macOS suite, the oldest Python |
| `cache.yml` | A push to `main` that changes `native/` or `uv.lock`, or by hand | Builds the native helpers to warm the cache every pull request restores |
| `build.yml` | Label `full-ci`, by hand, or from `release.yml` | Freeze on all three OSes, every gate, the installers, and the install test on clean systems |
| `release.yml` | A pushed version tag | All of `build.yml`, then a draft release with every installer |

Every run of `ci.yml` ends in **`ci-ok`**. It passes when every other job passed or was skipped on purpose. The `main`
ruleset requires it, together with the DCO check. Its name never changes, so the ruleset need not change when jobs do.

A commit whose message contains `[skip ci]` runs no workflows. Use it only for changes that cannot affect a test, such
as CI configuration that CI itself is about to exercise; the required `ci-ok` check then needs an admin to merge past
it.

Add the label `full-ci` once per milestone before merging. It only fires when added, so after a fix, remove it and add it again.

**One trap:** the labelled run shows the Linux and Windows jobs as skipped, and `gh pr checks` keeps only the latest result under each name, which can hide a real failure from the earlier run. Before merging, look at each run's jobs.

## Release checklist

This is what a build must pass before it is released. To make a draft release from a merged version, see "A draft release" in `docs/PACKAGING.md`.

1. `python scripts/doctor.py` says ready.
2. Tests pass on Windows, then on Linux.
3. The version is bumped and `CHANGELOG.md` has its entry.
4. A pull request with `full-ci` passes every job on all three OSes, in every run.
5. `docs/PLATFORMS.md` still tells the truth about what has been proven.
6. If it's going to real users on a Mac, someone has run `docs/dev/macos-verification.md`.
7. The decisions in `docs/PACKAGING.md` have been made: licence, maintainer, signing.
8. The `.lock` files, `native/deps.lock.json` and `THIRD_PARTY_NOTICES.md` match what ships.
