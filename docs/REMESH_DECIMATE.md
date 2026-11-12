# Remesh/Decimate 0.1.0

One mesh in, one signed mesh out, available from the tool page, CLI and Workflows. Coordinates, model units and
placement are retained. Outputs default to `<name>_remesh.obj`. OBJ and PLY retain native quads/cells; STL
triangulates them and cannot retain their intended edge pattern.

## Choosing a method

| Method | Use it for | Backend |
|---|---|---|
| Decimate | Reduce scans while preserving features and open boundaries | PMP constrained quadric error |
| Uniform triangles | Even triangular networks and better simulation elements | PMP incremental remeshing |
| Adaptive triangles | Dense detail around curvature, calmer flat areas | PMP curvature-adaptive remeshing |
| Aligned quads | Flowing, approximately curvature-aligned grids | Instant Meshes; alternative Blender QuadriFlow |
| Directional triangles | Long elements and rhythmic directional line art | Uniform remeshing in a stretched metric |
| Dual cells | Organic polygon networks and cellular drawing | Feature-aware barycentric dual of uniform/adaptive triangles |
| Planar dissolve | Remove redundant nearly planar edges | Blender BMesh limited dissolve |
| Voxel reconstruction | Rebuild the volume or merge nearby shells | Blender OpenVDB |

Dual cells are barycentric dual polygons, **not exact geodesic Voronoi cells**. Irregularity moves their source
face centres inside triangles using a seeded distribution. Sharp source edges split cells into separate wedges.
Curved cells are triangulated internally for processing and visibility; their exported boundary remains native.

## Density, preservation and regional detail

- Face counts mean **native faces**, not the triangles used by rendering. Counts are approximate, especially
  for curvature adaptation, quads, features and fixed boundaries. Constraints win over the requested count.
- Edge lengths and voxel size use either model units or % of bounding-box diagonal. Surface-error tolerance
  always uses % of that diagonal. Internal normalisation protects small/large coordinates without changing units.
- Keep open boundaries fixed and Keep sharp features default on. The sharp threshold is 45°. Surface error
  defaults to 0.1% diagonal; normal deviation defaults to 60°. Larger tolerances allow stronger simplification.
- Triangle remeshing defaults to ten split/collapse/flip/relax iterations with source projection. Adaptive edge
  factors bound the curvature-driven local sizing. Directional elongation specifies the long-edge direction
  in the Model frame. Zero direction vectors are rejected.
- Regional detail is off initially. With decimation it preserves vertices in the selected band or sphere;
  with triangle remeshing it increases local density. Shared feathering, inversion and frame controls apply.
  Quad, planar and voxel methods operate on the whole mesh. This is not a painted selection or a stitched patch remesher.
- Seeds affect cellular irregularity and QuadriFlow. Instant Meshes runs deterministically with its fixed native
  initialisation. Different algorithms and Blender versions can produce different topology.

Invalid, degenerate, non-manifold or inconsistently wound surfaces are rejected by surface-preserving methods.
For triangle/grid/cell methods Atlas makes up to five attempts with progressively denser meshes, reporting
each refinement; it keeps the selected algorithm. An invalid result after refinement is rejected. Difficult
Instant Meshes results can be retried explicitly with QuadriFlow. No automatic backend substitution occurs.
No face deletion, hole filling or algorithm substitution is used to hide a failed remesh. Increase density or
choose another method when preservation cannot be met. Voxel reconstruction is an explicit volume rebuild:
openings may close, nearby shells may merge, thin parts may disappear. Process shells independently defaults on;
disable it to allow their volumes to merge. Small disconnected output pieces are not automatically removed.

Every result reports native/triangle counts, requested density, components and deterministic bidirectional
sampled maximum/RMS distance in source units. Sampling is not a certified Hausdorff bound and cannot prove the
absence of all small defects or self-intersections. Each component is validated against its own source.

## Drawing and workflows

Choose Plotter Vision → **Remeshed network** to draw native edges, including coplanar quad/cell boundaries.
The Features setting retains angle filtering. Internal diagonals do not become feature lines; triangles still
provide visibility and occluding silhouettes. Keep downstream decimation off when the generated edge network matters.
FlowRep's Preserve polygons setting uses native OBJ/PLY topology. Contour Lines and other surface algorithms
use the derived triangle view. Tools that rebuild topology return their actual resulting triangles.

The ten presets range from Clean preparation (20k faces), Fast drawing preparation (8k) and Bold facets (1.5k)
through Even triangle net, Adaptive portrait, Flowing quad grid, Organic cells, Directional weave, Planar
architecture and Soft voxel sculpture. Each is complete: switching resets other method settings.
The artistic net/grid/cell/directional presets use broader 3%/2%/4%/3% surface tolerances. Clean preparation
retains the strict 0.1% default. Complex scans may require more faces than these starting targets.

Example workflows in `docs/workflows/` cover facets/quads/cells → Plotter Vision, adaptive → Contour Lines and
quads → FlowRep. Their output is SVG from the existing drawing tools; Remesh/Decimate itself outputs only meshes.

```bat
atlas-toolkit run remesh-decimate sculpture.stl --preset "Bold facets" --out-dir out
atlas-toolkit run remesh-decimate sculpture.stl --preset "Flowing quad grid" --format ply --out-dir out
atlas-toolkit run plotter-vision out\sculpture_remesh.ply --preset "Remeshed network" --out-dir plots
atlas-toolkit run remesh-decimate sculpture.stl --method voxel --voxel-size 1 --out-dir out
```

Preview writes no output and reduces iterations/face targets in draft mode. Display meshes are bounded, and
wireframes over 160k edges are sampled for display only; saved topology is complete. Inputs never get overwritten.
Output publication is staged: cancellation or validation/save failure preserves an existing output.

## Setup and source

The release bundles `atlas-remesh-core` 0.1.0, protocol 1: no compiler is needed to run it. Source builds use:

```bat
.venv\Scripts\python native\build.py --only remesh
```

PMP is pinned to `f2fb04f4a4188a5c1ab137e83b96e62fa99c639f` (3.0.0), with its included Eigen 3.4.0. Build
requires CMake and C++17 tools. Generated copies add regional sizing/boundary locks, progress, an intermediate
face limit and a zero-area collapse guard; original dependency sources stay unchanged. The helper uses doubles.
Notices ship in `licenses/remesh-decimate.txt`. Override with About → Setup or `ATLAS_TOOLKIT_REMESH`.

Blender 4.2+ is required only for voxel reconstruction, planar dissolve and QuadriFlow. Locate it through Atlas's
standard Setup controls. Selected-method requirements are checked consistently in the UI, CLI and Workflows.

Research: [PMP remeshing](https://www.pmp-library.org/remeshing.html),
[PMP decimation](https://www.pmp-library.org/group__algorithms.html),
[Instant Meshes](https://github.com/wjakob/instant-meshes),
[QuadriFlow](https://stanford.edu/~jingweih/papers/quadriflow/).

## Validation recorded for 1.10.3

The validation script writes native meshes, SVGs and rendered PNGs for the ten presets. Unit tests cover native
polygon round trips, collinear corners, bent shared boundaries, geometric validity, deterministic seeds, regional
density/protection, unit/placement invariance, selected runtimes, workflow drawing/cache and interrupted publication.

Measured on the development Windows machine with Clean preparation, 0.1% tolerance (times are not guarantees):

| Input | Output | Elapsed | Sampled max error | Python peak working set | Native helper peak |
|---|---:|---:|---:|---:|---:|
| Sphere, 1,310,720 triangles | 20,000 triangles | 38 s | 0.0206% diagonal | 2,021 MiB | 330 MiB |
| The Shadow sculpture, 4,000,004 triangles | 48,358 triangles | 179 s | 0.0978% diagonal | 5,093 MiB | 1,110 MiB |

These are separate process peaks, not a combined simultaneous-memory measurement. The larger scan retained
additional faces to meet its constraints. Large scans can need several GB of RAM. Binary STL loading uses exact
float32 corner welding to avoid rounded float64 merge copies.

The 126,724-triangle head scan also validates clean preparation, bold facets, triangle nets, directional meshes
and cellular output. Its complex surface did not admit a valid aligned-quad result with the tested settings;
Atlas rejects it without writing a damaged output. Try an explicitly selected voxel preparation step for such
scans before quadrangulation. The simpler validation form passes both Instant Meshes and Blender QuadriFlow.
