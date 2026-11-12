# Atlas: six new tools for dramatic mesh transformations

## 1. Summary and assessment

Build six complementary tools: **Strata Sculpt, Flow Warp, Porous Forms, Surface Peel / Bloom, Reaction-Diffusion Growth, and Shape Fusion / Morph**.

Prioritize recognizable models undergoing strong transformations: a classical bust unfolding into petals, a figure becoming a porous labyrinth, or a head pulled into turbulent currents. Each tool should produce actual geometry suitable for further sculptural work and Atlas’s mesh-to-SVG pipeline.

**No code was changed.** This assessment covers the current working-tree implementation, including its existing uncommitted additions. Small, file-free feasibility checks confirmed native capped slicing and implicit lattice extraction; they do not establish production performance.

Atlas’s existing [model-preparation tools](../../README.md) provide a useful foundation:

| Existing tool | Actual transformation | Opportunity left open |
|---|---|---|
| Cubic Replicator | Places transformed copies on a rectangular grid. | Changes composition more than the individual model’s shape. |
| Grid Slicer | Produces capped Cartesian blocks, then separates or shuffles them. | Thin directional strata with coordinated bending, rotation, and displacement. |
| Mesh Shatter | Segments the surface along mesh adjacency and ejects the patches. | Controlled unfolding and curling of surface sections. The existing shards have no newly constructed solid interiors. |
| Mesh Morph | Subdivides triangles and independently displaces their corners. | Continuous deformation and transitions between different models. This is fragmentation rather than correspondence-based shape morphing. |
| Twist | Combines torsion, sway, and ripples. | Turbulent spatial fields, localized vortices, and directional pulling. |
| Melt | Produces sagging, pools, drips, and dissolution through Blender. | Biological pattern growth and deliberate fusion between shapes. |
| Voxelizer | Reconstructs geometry from repeated primitives. | Continuous porous structures with connected walls and controlled negative space. |

The strongest additions introduce **new transformation mechanisms**, rather than more variations on random displacement or fragmentation.

## 2. Shared implementation foundation

Follow Atlas’s existing [tool contract](../ADDING_A_TOOL.md): a lightweight specification, geometry algorithm, and engine adapter for each tool. Preserve automatic CLI generation, presets, history, signatures, workflows, and cancellable worker execution.

### Common behavior

- Register six new mesh tools with IDs `mesh-strata`, `mesh-flow`, `mesh-porous`, `mesh-peel`, `mesh-growth`, and `mesh-fusion`.
- Accept Atlas’s existing mesh formats; export signed STL, OBJ, or PLY geometry.
- Reuse canonical framing and restore the source’s coordinates and units on export.
- Provide effect amount, seeded randomness where applicable, and single-result or 1–8-stage output through the existing stage publisher.
- Evaluate randomness, segmentation, and spatial controls against the rest model so sequences remain coherent.
- Support whole-model influence, a feathered height band, or an influence sphere. Use numeric controls and presets; painted masks and draggable cages are deferred.
- Offer Draft and Final preview quality. Draft reductions must be identified; Final preview must evaluate the same geometry as export.
- Keep the original model at stage zero. Avoid automatic simplification during final processing; reject excessive requests with actionable limits.

### Geometry infrastructure

Create reusable helpers for influence fields, mesh refinement, and implicit-volume processing.

For Porous Forms and Shape Fusion, add **libigl 2.6.3** for accelerated signed-distance queries, retaining NumPy/SciPy for field operations and scikit-image’s Lewiner marching cubes for extraction. libigl supplies NumPy-native distance and mesh operators, and the published release includes Windows wheels compatible with the current interpreter. Packaging still requires a frozen-app verification. [libigl bindings](https://libigl.github.io/python-bindings/), [release files](https://pypi.org/project/libigl/), [marching cubes documentation](https://scikit-image.org/docs/stable/api/skimage.measure.html#skimage.measure.marching_cubes)

Use a negative-inside field convention, padded bounds, chunked queries, and one shared grid for interacting shapes. Start with **128 samples along the longest dimension for Draft and 256 for Final**, with an advanced resolution setting and allocation preflight. Report voxel size because features smaller than the sampling interval cannot be preserved reliably.

Require valid closed solids for volume-based operations. Surface deformation tools can accept suitable open meshes. Describe input problems explicitly rather than silently closing major openings.

## 3. Individual tool plans

### A. Strata Sculpt — suspended layers and architectural dislocation

**Visual result:** A statue becomes a stack of floating stone wafers, a spiral staircase of cross-sections, or a figure displaced by a rhythmic sideways wave.

**Why implement it:** It should deliver the quickest strong result and reads particularly well as line art. Unlike Grid Slicer, its composition follows an ordered stack of layers.

**Implementation**

1. Orient the source into a slicing frame using an arbitrary plane normal.
2. Divide its projected extent into ordered slabs using capped plane cuts. Extract reusable cutting helpers from Grid Slicer without changing its behavior.
3. Give each slab a stable index. Apply separation, a sinusoidal lateral shift, and progressive rotation around the shared stack axis.
4. Animate retained slab thickness from the complete partition toward the chosen thinner sections; preserve the untouched source at amount zero.
5. Keep the cuts planar in the first version. Curved cutting surfaces would introduce a separate meshing problem.

**Initial controls and presets:** 24 layers, retained thickness 75%, separation, lateral-wave amplitude/frequency, total rotation, slice direction, and phase. Presets: **Floating Marble**, **Spiral Dislocation**, and **Seismic Shift**.

**Acceptance:** Zero amount reproduces the source; unseparated full-thickness slabs reconstruct its volume within numerical tolerance; toroidal sections retain their holes; oblique cuts and disconnected components remain capped.

**Plotter pairing:** Plotter Vision for the exposed layer edges; oblique Contour Lines for the displaced silhouette. The underlying cutting technique resembles Blender’s plane-based [Bisect tool](https://docs.blender.org/UATEST/manual/en/dev/modeling/meshes/editing/mesh/bisect.html).

**Relative effort:** Medium, with substantial reuse.

### B. Flow Warp — models pulled through turbulent currents

**Visual result:** A face swept into a vortex, a torso stretched into flowing folds, or a sculpture appearing caught in a localized current.

**Why implement it:** Twist supplies an organized axial deformation. Flow Warp adds spatial variation and localized movement while keeping the surface connected.

**Implementation**

1. Build a deterministic vector field combining curl noise, an analytic vortex, and directional pull.
2. Refine long edges before deformation according to the smallest active field wavelength. Use a fixed rest mesh throughout a sequence.
3. Advect vertices through the field with fourth-order Runge–Kutta integration. Bound the integration step relative to field wavelength.
4. Apply feathered influence and optional base anchoring. Sample stages along the same trajectory.
5. Report extreme triangle stretching and degeneracy. Do not claim collision-free or volume-preserving output: numerical integration, masks, and directional pulling invalidate such a blanket guarantee.

**Initial controls and presets:** Travel distance defaulting to 15% of model height; swirl size, turbulence, vortex center/axis, pull direction, base anchoring, and seed. Presets: **Marble Current**, **Vortex Portrait**, and **Windblown Figure**. <!-- preset-leak: ok -->

**Acceptance:** Zero travel is identity; the seed is repeatable; anchored vertices remain fixed; tighter integration steps converge; changing stage count does not change a shared stage’s shape.

**Plotter pairing:** Contour Lines should reveal the flow particularly clearly.

**Inspiration:** Houdini’s [Volume Velocity](https://www.sidefx.com/docs/houdini/nodes/sop/volumevelocity.html) and Bridson et al.’s [curl-noise method](https://www.cs.ubc.ca/~rbridson/docs/bridson-siggraph2007-curlnoise.pdf).

**Relative effort:** Medium.

### C. Porous Forms — continuous labyrinths inside familiar silhouettes

**Visual result:** A bust becomes a gyroid labyrinth, an architectural figure develops cellular openings, or solid marble gradually gives way to an intricate internal structure.

**Why implement it:** This adds continuous walls and connected negative space, which the existing primitive-based Voxelizer does not produce.

**Implementation**

1. Convert the closed source to a signed-distance grid.
2. Generate gyroid and diamond periodic fields in an independently orientable coordinate frame.
3. Convert the periodic pattern into a sampled wall-distance field so wall thickness is expressed in model units.
4. Intersect that structure with the source volume. Spatial influence preserves solid regions, allowing an intact face to transition into a porous rear skull.
5. Extract and validate the resulting mesh. Report connected components and sampling-limited thickness; retain all components unless removal is explicitly requested.

**Initial controls and presets:** Gyroid default; cell size 12% of model height; wall thickness 2%; pattern orientation/phase; solid-to-porous transition; protected height band or sphere. Presets: **Porous Marble**, **Exposed Labyrinth**, and **Solid to Cellular**.

Require at least three final-grid samples across the requested wall thickness; otherwise request higher resolution or thicker walls.

**Acceptance:** Geometry stays inside the source envelope within sampling tolerance; padded extraction produces closed surfaces; wall thickness and connectivity behave consistently under refinement; thin appendages are tested explicitly.

**Plotter pairing:** Plotter Vision for internal openings and Sketch Hatching for depth. Keep defaults relatively coarse to avoid an unreadable mass of lines.

**Inspiration:** nTop’s [periodic implicit fields](https://learn.ntop.com/courses/220-intro-to-field-driven-design/lessons/different-types-of-fields-in-ntop/) and [field-controlled lattice variation](https://resources.ntop.com/resources/videos/how-to-design-control-lattice-structures-gyroids-pt-2/).

**Relative effort:** High, including the shared volume foundation.

### D. Surface Peel / Bloom — unfurling petals and sculptural scales

**Visual result:** A head opens like a flower; a figure sheds curled plates; a surface unfolds into overlapping metallic petals.

**Why implement it:** It turns segmentation into directed, legible movement. Shatter translates rigid patches; Peel bends them around stable roots.

**Implementation**

1. Reuse Shatter’s seeded geodesic segmentation, splitting disconnected or excessively wrapping patches into manageable surface sections.
2. Establish a local tangent frame for each patch. Derive its root from the selected world direction and its boundary.
3. Apply an analytic cylindrical bend with a smooth root transition, plus optional outward opening and progressive activation by height.
4. Keep patch membership and root frames fixed throughout the sequence.
5. Extend Atlas’s existing Blender worker with a `solidify` operation to give the deformed panels thickness using Complex Solidify. Transfer geometry through the existing array-based bridge.

**Initial controls and presets:** Target 32 patches; curl angle 75°; opening distance; thickness 0.5% of model height; root direction; activation order; seed. Presets: **Marble Blossom**, **Unfurling Shell**, and **Armored Scales**.

The result is an artistic panel assembly. Large curls can intersect neighboring panels; physical collision solving is outside this version.

**Acceptance:** Repeatable patches and roots; root vertices remain anchored; panel boundaries close after solidification; sequences never reseed; Blender cancellation and missing-runtime handling follow Melt’s behavior.

**Plotter pairing:** Plotter Vision and Sketch Hatching emphasize overlaps and exposed undersides.

**Inspiration:** Combining local [bend deformation](https://docs.blender.org/manual/en/latest/modeling/modifiers/deform/simple_deform.html) with [surface solidification](https://docs.staging.blender.org/manual/en/latest/modeling/modifiers/generate/solidify.html).

**Relative effort:** High.

### E. Reaction-Diffusion Growth — living ridges and cellular relief

**Visual result:** A classical face develops coral-like ridges, fingerprint channels, cellular bulges, or branching surface patterns.

**Why implement it:** The pattern responds to surface connectivity, giving a more organized biological character than ordinary noise displacement.

**Implementation**

1. Refine the surface sufficiently to resolve the requested pattern wavelength.
2. Construct a mass-weighted mesh Laplacian using libigl/SciPy, with nonnegative diffusion weights for stable evolution.
3. Run a Gray–Scott two-species simulation directly over mesh adjacency. Use seeded spatial starting spots and no-flux boundaries.
4. Use semi-implicit diffusion and bounded reaction steps; cache the reusable sparse factorizations.
5. Convert concentration into raised or engraved geometric relief along smoothed rest normals. Capture stages from one evolving simulation.

The first version creates **surface relief**, including silhouette-changing bulges. It does not promise free-growing branches or a full biological growth simulation.

**Initial controls and presets:** Pattern wavelength 5% of model height; relief depth 2%; growth duration; seed; advanced feed, kill, and diffusion settings. Presets: **Coral Ridges**, **Fingerprint Marble**, and **Cellular Skin**. Tune numerical preset values against Atlas fixtures before release.

**Acceptance:** Concentrations remain finite and bounded; disconnected components do not exchange chemicals; pattern scale remains comparable across mesh densities; zero relief preserves the source; saved stages match the corresponding simulation states.

**Plotter pairing:** Sketch Hatching reveals the relief; Contour Lines emphasizes larger bulges. Avoid defaults whose patterns are too fine to survive the paper scale.

**Inspiration:** Greg Turk’s [reaction-diffusion on arbitrary surfaces](https://faculty.cc.gatech.edu/~turk/reaction_diffusion/reaction_diffusion.html) and Tissue’s [mesh-based implementation](https://github.com/alessandro-zomparelli/tissue/blob/master/weight_reaction_diffusion.py).

**Relative effort:** High, with the greatest preset-tuning burden.

### F. Shape Fusion / Morph — hybrid sculptures and topology-changing transitions

**Visual result:** A bust merges with a torus; a figure transitions into an architectural object; two heads fuse into a continuous surreal sculpture.

**Why implement it:** This fills the largest semantic gap in the current collection: transformation between different shapes without requiring matching vertex counts.

**Implementation**

1. Add a target-model `FileRef`, automatically exposing the second mesh input to workflows.
2. Default to upright alignment, matching source height and base position. Provide target rotation, scale, and translation, plus an explicit preserve-placement option.
3. Sample both solids on one padded grid covering their combined bounds.
4. Offer two modes: **Morph**, extracting the zero surface of the interpolated distance fields; and **Fuse**, using a smooth union with adjustable blend radius.
5. Support a feathered spatial transition so only part of a model changes. Return exact source and aligned-target geometry at the global Morph endpoints rather than remeshing those endpoints.

**Initial controls and presets:** Morph default; blend 50%; target transform; resolution; transition direction/band; smooth-union radius in Fuse mode. Presets: **Halfway Hybrid**, **Directional Metamorphosis**, and **Organic Union**.

This produces volumetric shape transitions without anatomical correspondence. Misaligned inputs can shrink, split, or disappear at intermediate blends; detect empty results and identify alignment as the corrective control.

**Acceptance:** Different topologies work without vertex correspondence; endpoints are correct; target changes invalidate workflow caches; translated inputs use the full joint domain; sequences retain a common frame.

**Plotter pairing:** Contour Lines for transformation series; Sketch Hatching for fused volumes.

**Inspiration:** Houdini’s [VDB Morph SDF](https://www.sidefx.com/docs/houdini/nodes/sop/vdbmorphsdf). Atlas’s initial implementation would use sampled field interpolation rather than reproduce Houdini’s advection solver.

**Relative effort:** High, reduced by sharing the Porous Forms infrastructure.

## 4. Delivery sequence and validation

Implement in this order:

1. **Strata Sculpt and Flow Warp:** establish the shared influence controls and deliver two strong transformations using mostly existing infrastructure.
2. **Porous Forms:** introduce and validate signed-distance sampling, extraction, resolution controls, and libigl packaging.
3. **Shape Fusion / Morph:** reuse that foundation and verify two-input workflows.
4. **Surface Peel / Bloom:** add local patch deformation and the Blender solidification operation.
5. **Reaction-Diffusion Growth:** complete the simulation solver and visual preset calibration.

For every tool, validate primitives, a torus, disconnected geometry, an open surface, thin features, and a dense sculptural scan. Use existing bust and figure models for visual review without writing into the model library.

Acceptance includes:

- Contract, CLI, settings, preset, signature, workflow, cancellation, and atomic-publication tests.
- Tool-specific geometry tests listed above.
- Fixed-camera comparison plates through Contour Lines and either Plotter Vision or Sketch Hatching.
- Recorded processing time, peak memory, face count, and resulting SVG complexity on representative inputs.
- Frozen executable self-tests and GUI checks, followed by the repository’s required version bumps, changelog updates, and rebuild.

## 5. Defaults and boundaries

The collection targets **sculpture and plotter art**. Strong geometry and readable negative space take priority over automatic printability.

First versions use presets and numeric spatial controls. They export baked meshes and Atlas stage sets; material authoring, editable Blender scenes, painted masks, physical collision solving, and animation timelines remain outside this implementation.

The most promising initial combinations are **Flow Warp → Contour Lines**, **Strata Sculpt → Plotter Vision**, **Porous Forms → Sketch Hatching**, and **Surface Peel → Plotter Vision**. Review each tool independently before tuning combinations so that its visual contribution remains clear.
