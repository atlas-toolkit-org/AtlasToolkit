# Developable Surfaces 0.1.0

Convert a mesh into an approximately piecewise developable surface for projected pen-plotter drawings.
This adapts the original solver by Oded Stein, Eitan Grinspun and Keenan Crane, *Developability of Triangle
Meshes*, ACM TOG 37(4), 2018, DOI: https://doi.org/10.1145/3197517.3201303.
Research: https://www.cs.cmu.edu/~kmcrane/Projects/DiscreteDevelopable/ .
Preprocessing: https://odedstein.com/projects/developability/preprocessing.html .

## Use

Choose **Developable Surfaces** under Mesh, load a model, select **Balanced**, and Preview or Develop.
Use **Sculpture** for more identity, **Broad folds** for larger regions, **Clean rulings** to discourage
branching, or **Abstract form** for a strongly transformed cage. Draft previews use at most 500 initial
triangles, one subdivision and 100 iterations per level. Keep-input preparation retains its original face
count in a draft; use remeshing for fast previews of dense inputs.

Inputs: STL, OBJ, PLY, GLB/GLTF and OFF, through the shared Atlas mesh loader. Scene transforms are applied.
Textures, materials and split shading normals are ignored; coincident geometry vertices are welded before
validation. Outputs: `<name>_developable.stl`, or signed OBJ/PLY, in original coordinates and model units.
Original inputs are never replaced. Extreme coordinate offsets can lose detail in float32 mesh formats;
recenter a working copy or choose OBJ if export validation rejects the result.

Open manifold surfaces are accepted. Prepared boundary positions remain fixed, including subdivided edges.
Components are solved separately and restored to their source coordinate frame. Non-manifold sources or
inconsistent winding must be repaired first. No component is dropped, and holes are not filled automatically.

## Drawing and Workflows

Send the result to **Plotter Vision / Developable rulings** (the gentle folds too) or **Developable
creases** (only the sharp ones), from the preset packs. Both disable decimation, put outlines on their own pen,
and remove short inner strokes. Contour Lines gives a different language of horizontal slices. SVG output is in mm.
Choose the camera in the renderer's Aim/Vision view and set paper size and pen width there.

Import `docs/workflows/developable_rulings.atlasflow` or `developable_contours.atlasflow` in Workflows.
Each has a mesh Save branch alongside the SVG branch; remove that Save node if only the drawing is wanted.
They use the batch input location for final files. Intermediate results stay in Atlas's workflow cache.

```powershell
atlas-toolkit describe mesh-developable
atlas-toolkit run mesh-developable model.obj --preset "Balanced"
atlas-toolkit run mesh-developable model.obj --preparation keep --subdivisions 1 --iterations 200
atlas-toolkit run plotter-vision model_developable.stl --preset "Developable rulings" --paper A3
```

## Method and limits

Field-aligned triangular remeshing uses installed PyNanoInstantMeshes with triangular symmetry (rosy 6,
posy 3), deterministic execution, extrinsic alignment and a 45-degree crease threshold. Initial face counts
are approximate and shared proportionally among components. If it introduces invalid topology or slivers,
Atlas tries libigl edge-collapse coarsening, validates topology again, and explicitly reports the fallback.
It does not claim that this fallback has field-aligned edges. Use Keep input triangulation for a deliberately
constructed cage. The initial tessellation has a strong aesthetic effect.

The native helper uses the original intrinsic covariance, maximal intrinsic covariance or combinatorial
energy and accurate Kopp eigensolver. It uses an eight-pair L-BFGS history and Overton weak-Wolfe search,
with monotone Armijo steepest descent on a stalled search. History belongs to one solve and resets at each
subdivision. Invalid geometry is rejected during line search. Optimisation stops at the gradient tolerance,
iteration limit or a stalled search; the last valid solution is retained and its status is reported. Regular
1-to-4 subdivision refines between levels. It does not project back to the original detailed mesh.

Defaults: 2,000 initial faces, two subdivisions, 1,000 iterations per level, gradient RMS tolerance 1e-6,
and a 250,000-face ceiling. Sculpture starts at 5,000 faces, Broad folds at 800, Abstract form at 400.
The report separates each level's energy change; raw energy cannot be compared across energy types.
Displacement is sampled one-way distance from the output surface to the input, in source units.
Runs target a few minutes but have deterministic iteration bounds rather than a wall-clock cutoff.
Cancellation stops the native child. Cached results include geometry, settings and the executable hash;
corrupt results are recomputed. Output files are published atomically after validation.

This is approximate developability. It does not prevent self-intersections, guarantee exact straight rulings,
or generate certified fabrication geometry. Unfolding, cut layouts and evolution sequences are outside this
release. The linked research videos could not be retrieved during development; implementation and validation
use the paper, authors' notes and source code.

## Build, source and licensing

Run the Atlas Python on `native/build.py --only developable`. It fetches pinned libigl, Eigen and nlohmann JSON
into project-local `deps/`, then uses CMake and a C++ compiler (MSVC Build Tools on Windows). No OpenGL,
MATLAB, Blender, CGAL or system-wide dependency installation is needed. `--no-fetch` builds offline.
The app's build script builds the helper before packaging it. Installed users need no compiler.

The separately licensed GPL-2.0-or-later helper uses upstream revision
`67848c18c3b0c6389fef9b165a37fe7db5eed0f3`. Kopp's eigensolver is LGPL-2.1-or-later;
libigl and Eigen have MPL notices, and nlohmann JSON is MIT. Full notices and the complete corresponding
helper source archive are bundled as `licenses/developable-surfaces.txt` and `licenses/developable-source.zip`.
Release builds also publish both files beside the desktop executable. Extract the archive and run its `build.py --no-fetch` to rebuild; a manifest verifies dependency contents.
Set `ATLAS_TOOLKIT_DEVELOPABLE` or use About > Setup > Locate to select another compatible helper.

Validation script: `scripts/dev/validate_developable.py --out <folder>` generates mesh/preset diagnostics and
A3 SVG/PNG comparisons for the supplied sphere/bunny and a local head, without writing to source libraries.


## Validation for the initial release

The full Atlas suite passed 530 tests; 11 focused tests cover this solver's numerical and integration behavior.
All five presets produced checked A3 line drawings on the supplied sphere/bunny and a local sculpture head
(100 iterations per level for the comparison matrix). Default Balanced runs at 1,000 iterations per level took
319.3 s (sphere), 311.5 s (head) and 346.5 s (bunny), during concurrent build/validation activity. These reached
the iteration bound and are approximate results, not proof of convergence. Sculpture's larger face budget can
run longer. Draft previews remain much faster. Native source was separately rebuilt offline.
