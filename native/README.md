# Native helpers

Four small headless C++ programs that do the numerical heavy lifting for some Atlas tools. Each is a standalone
executable the Python app runs as a subprocess (no Python extension ABI), so one build serves every Python version.

| Folder | Executable | Used by | Method / library | Licence |
|---|---|---|---|---|
| `texture/` | `atlas-texture-core` | Surface Texture | xatlas UV charts (vendored, `third_party/xatlas`) | MIT |
| `stripes/` | `atlas-stripes-core` | Surface Stripes | Knöppel et al. 2015 stripe patterns, Geometry Central + Eigen | MIT / MPL-2.0 |
| `remesh/` | `atlas-remesh-core` | Remesh/Decimate, Image/DEM to Mesh | PMP 3.0.0 decimation and remeshing (patched copies) | MIT |
| `developable/` | `atlas-developable-core` | Developable Surfaces | Stein, Grinspun and Crane 2018 (adapted, `vendor/`) | **GPL-2.0-or-later** |

Each folder's README documents its command-line protocol and what Atlas changed upstream.

## Build

```
python native/build.py                       # fetch pinned deps, build all four, install into the package
python native/build.py --only remesh,texture
python native/build.py --no-fetch            # offline, once native/_deps is populated
python native/build.py --check               # toolchain and dependency report
```

Requirements: Git, CMake 3.24+, Ninja and a C++17 compiler (`--check` says what is missing).

- **Windows:** Visual Studio 2022+ or the C++ Build Tools with the x64 toolset. The driver finds them through
  vswhere and uses their bundled CMake and Ninja; nothing is installed and PATH is not changed.
- **macOS:** Xcode command-line tools, plus `brew install cmake ninja`. Executables target macOS 15 or later.
- **Linux:** `g++` or `clang++`, plus `cmake ninja-build` from the distribution, and `libstdc++-static` on Fedora and
  RHEL. Releases are built in `packaging/linux/Dockerfile`, on the glibc 2.34 floor.

The driver runs these steps:

1. **Fetch.** Shallow-fetches each dependency at the exact revision in `deps.lock.json` into `_deps/`. Eigen is
   shared by stripes and developable.
2. **Build.** Configures and builds the superbuild with the platform's preset (`CMakePresets.json`).
3. **Install.** Copies `atlas-*-core[.exe]` into `src/atlas_toolkit/bin/`, with each helper's third-party notice in
   `src/atlas_toolkit/licenses/`.
4. **Developable source archive.** Writes `developable-source.zip`, the GPL corresponding source for the
   developable helper. It contains the helper, the exact dependency headers it compiles, every licence and a hash
   manifest. Unzipped, `cmake -S atlas_developable -B build -G Ninja -DCMAKE_BUILD_TYPE=Release` rebuilds it offline.

`scripts/build.py` (the app build) runs this driver first, incrementally, so a release always bundles helpers built
from the committed sources.

From an IDE or CI, once `_deps/` is populated (`python native/build.py --check` reports it), the same build is:

```
cmake --preset windows-msvc      # or linux, macos
cmake --build --preset windows-msvc
```

The superbuild (`CMakeLists.txt`) builds each helper as its own CMake project (ExternalProject). This keeps their
third-party targets apart, and every helper can still be built alone with `cmake -S native/<helper>`.

The executables run on their own on every OS. Windows uses the static MSVC runtime, so they need no redistributable.
Linux links `libstdc++` statically (the `linux` preset), so a helper works whatever version the target system has.
`libgcc` stays dynamic on purpose: RHEL 9 back-ported a `GLIBC_2.35` symbol that static `libgcc` then requires, which
would raise the floor above glibc 2.34, and every supported system has `libgcc_s`. No helper uses OpenMP: PMP links it
when found without using it, so `remesh/CMakeLists.txt` turns that off.

## Layout

```
native/
  build.py             the driver (fetch, toolchain, build, install, notices, source archive)
  deps.lock.json       pinned upstream URLs and revisions
  CMakeLists.txt       superbuild;  CMakePresets.json  windows-msvc / linux / macos
  texture/ stripes/ remesh/ developable/
  _deps/               pinned upstream checkouts      (gitignored, fetched)
  build/<preset>/      CMake trees, stage/bin         (gitignored)
  reference/           upstream research apps, local only (gitignored, see below)
```

`remesh/generate.py` writes Atlas's patched copies of PMP's `remeshing.cpp` and `decimation.cpp` into the build
tree at configure time. The PMP checkout itself stays verbatim, and each patch anchor must match exactly, so an
upstream change fails loudly.

## Upstream reference sources

The original research code the helpers adapt is not part of this repository. Get it from:

- StripePatterns (Keenan Crane, 2015, BSD-2-Clause): https://www.cs.cmu.edu/~kmcrane/Projects/StripePatterns/.
  Its licence is kept as `stripes/NOTICE-StripePatterns.txt` because the stripes notice quotes it.
- DevelopabilityOfTriangleMeshes (Stein, Grinspun and Crane, 2018, GPL-2.0 / LGPL-2.1):
  https://github.com/odedstein/DevelopabilityOfTriangleMeshes, revision `67848c18c3b0c6389fef9b165a37fe7db5eed0f3`.
  `scripts/dev/validate_developable.py` reads two of its sample meshes from `native/reference/`.

Put local copies in `native/reference/` (gitignored) if you need them.
