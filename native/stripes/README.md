# Atlas Surface Stripes native backend

Headless wrapper around the Geometry Central implementation of *Stripe Patterns on Surfaces*
(Knöppel, Crane, Pinkall and Schröder, 2015), with David Jourdan's polyline extraction.
Crane's 2015 reference source is not part of this repository (see `native/README.md`); its licence is kept
here as `NOTICE-StripePatterns.txt`.

## Build

From the repository root, with Atlas's Python:

```
python native/build.py --only stripes
```

Requires Git, CMake 3.24+, Ninja and a C++17 compiler. On Windows the script discovers Visual Studio C++ Build
Tools through vswhere and uses its bundled CMake/Ninja. It does not install system tooling or change PATH.
First build fetches the exact Geometry Central and Eigen revisions recorded in `native/deps.lock.json`; the happly submodule
is pinned by Geometry Central. Other header dependencies are vendored by Geometry Central.
Use `--no-fetch` for an offline build once dependencies are present; `--jobs N` sets compilation parallelism.

The helper uses Eigen's sparse solvers with `SUITESPARSE=OFF`, static libraries and the static MSVC runtime.
It has no viewer, GPU, OpenGL, GLUT, SuiteSparse or Python-extension dependency. The executable is installed into
Atlas's `bin/` package directory, along with third-party notices in `licenses/`. Atlas's application build
(`scripts/build.py`) runs `native/build.py` and bundles the executable.

## Native interface, schema 2

```text
atlas-stripes-core --input triangles.obj --output curves.json --spacing 0.08
    --field curvature --rotation 0 --families single --seed 0
atlas-stripes-core --version
```

Input is one connected orientable triangle manifold. The low-level helper also supports boundary meshes; the
Atlas solid tool requires a closed body or explicit repair. Spacing is in input model units. Fields are
`curvature` or `smoothest`; families are `single` or `orthogonal`. Rotation is the angle of the **visible stripe
tangent**, in degrees. The wrapper converts the field to the 2-RoSy power representation and compensates for
Geometry Central's perpendicular isoline convention. Frequency passed to GC is `1 / spacing` cycles per unit.

Output JSON has `schema`, `backend`, `revision`, `curvature_fallback`, `mesh_vertices`, `mesh_faces`, and `families`.
Each family has `id`, `singular_faces`, `vertices` (XYZ triples), `edges` (zero-based vertex-index pairs), and
`corner_phase` (one triple in face/corner order per field triangle). The field geometry and phase support
topology-preserving band construction on Atlas's detailed source body. Curve vertices remain
in the input coordinate frame. Output exists only when exit status is zero; callers discard a partial file
on failure. `PROGRESS fraction message` records go to stderr. Other library messages are diagnostic text.
Exit status 2 means invalid arguments/input; 3 means computation/output failure.

The seed initializes both Eigen's random eigenvector iteration and Geometry Central's generator. Repeatability
is expected for the same backend build and input ordering; numerical results can differ across compiler builds.
Undefined curvature fields fall back to a smoothest field and report that choice.

## Method limits and attribution

GC does not implement the original paper's special nonlinear interpolation inside triangles. Atlas refines
the working mesh, reports unresolved long edges, and uses the library's heuristic singularity reconnection.
The result is a numerical stripe solution rather than a guarantee of identical reference-viewer pixels.

- Paper and reference: https://www.cs.cmu.edu/~kmcrane/Projects/StripePatterns/
- Library API: https://geometry-central.net/surface/algorithms/stripes/
- Geometry Central source: https://github.com/nmwsharp/geometry-central

Geometry Central is MIT licensed. Eigen and its third-party notices, happly, nanoflann, nanort and Crane's BSD
reference notice are retained in Atlas's bundled `licenses/surface-stripes.txt`.
