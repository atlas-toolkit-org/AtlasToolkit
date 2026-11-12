# Atlas developable helper

Headless adaptation of Stein, Grinspun & Crane (2018), GPL-2.0-or-later.
Upstream: https://github.com/odedstein/DevelopabilityOfTriangleMeshes , revision
67848c18c3b0c6389fef9b165a37fe7db5eed0f3. The original local prototype is unchanged.

Build with `python native/build.py --only developable` from the repository root; requires C++14, CMake and Ninja
(MSVC on Windows). Pinned header dependencies are in `native/deps.lock.json`. Use `--no-fetch` offline. No viewer
or MATLAB dependency. Inside the corresponding-source archive, `cmake -S . -B build -G Ninja
-DCMAKE_BUILD_TYPE=Release && cmake --build build` builds offline from its bundled `deps/`.

Protocol 1: `atlas-developable-core --input mesh.obj --settings settings.json --output out.obj --report report.json`.
Settings: energy (`covariance`, `max-covariance`, `combinatorial`), subdivisions, iterations, tolerance,
max_faces. `evaluate_only: true` returns energy and the boundary-constrained analytic gradient for validation.
Output progress lines start `PROGRESS fraction message`; failures return nonzero and a readable diagnostic.
Caller provides a single normalized, oriented, manifold triangle component. Boundaries are fixed. No collision guarantee.

Changes from upstream: headless driver; input/link checks; per-solve eight-pair L-BFGS and weak-Wolfe/Armijo
search; finite/degenerate-step guards; constrained boundary degrees of freedom; deterministic serial evaluation;
regular subdivision scheduling, diagnostics and evaluation interface. Eigen 3.4/MSVC compatibility fixes use
`.derived().resize` for output energies and initialize Kopp's matrix outputs through `.derived().setZero`.
Original energy expressions and accurate eigensolver are retained. Numerically degenerate triangles are rejected
rather than using the prototype's unsafe viewer-coupled edge-editing code. Topology changes are subdivision only.

Licenses: LICENSE.txt, GPLv2.txt, LGPLv2.1.txt, plus dependency notices. The build installs the helper, full notices
and a corresponding-source ZIP (all required headers and build inputs, dependency hashes, rebuildable offline)
into Atlas's package.
