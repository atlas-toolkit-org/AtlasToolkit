# FlowRep in Atlas

FlowRep Sketch is a built-in mesh2svg tool. It uses Atlas's Aim camera, paper controls, presets, workflow
contract, process worker and SVG preview. Its mathematical library is independent of Qt.

## Where it lives

| Part | Path |
|---|---|
| Library (extraction, network, rendering, SVG; its own CLI: `python -m flowrep3d`) | `src/flowrep3d/` |
| Atlas geometry helpers it uses (camera, frame, ray casting, curvature) | `src/flowrep3d/_atlas/` |
| The Atlas tool (spec, engine adapter) | `src/atlas_toolkit/tools/flowrep_sketch/` |
| The library's own tests (generated meshes only) | `tests/flowrep_tests/` |
| Research notes, API contracts, validation | `docs/flowrep/` |

The library ships in the same distribution as `atlas_toolkit` (`[tool.setuptools.packages.find] where = ["src"]`);
`scripts/build.py` collects its submodules and the native `pynanoinstantmeshes` wheel, so the frozen app needs
nothing else. The research checkout it came from (papers, the authors' models and networks, benchmark outputs)
is not part of this repository.

FlowRep was first merged into Atlas 1.7.21 by an installer (`flowrep3d/install.py`) that copied the library and
patched shared files. Now that the library is developed here, the installer and its `.flowrep-install/` manifest
are retired; change the library like any other code (tests, version in `flowrep3d/__init__.py`, changelog).

## Checks

    .venv\Scripts\python -m unittest discover -s tests
    .venv\Scripts\python -m atlas_toolkit self-test
    .venv\Scripts\python scripts\build.py

Then launch Atlas and choose Tools → Mesh → SVG → FlowRep Sketch. Test a quad OBJ and a triangle mesh, aim a view,
preview and save. Camera changes reuse extraction. Clean / Detailed / Sparse set 20 / 10 / 30 degrees; smaller
values usually retain more geometry. Native remeshing is cancelled by terminating the process worker; Python stages
check cancellation directly.

## Implementation notes

The engine adapts extraction and rendering into Atlas's Tool contract, signs outputs as flowrep-sketch and
includes Atlas run metadata. Dry previews write no SVG. The tool caches in Atlas's cache folder; `FLOWREP_CACHE`
overrides it (and is also the standalone CLI's default when set). The build uses explicit native/module collection
because geometry imports are lazy. Research equations, approximations, reference registration and quality limits are
documented in RESEARCH.md, API.md and VALIDATION.md. This is an independent research reproduction; output can differ
from the original FlowRep implementation.
