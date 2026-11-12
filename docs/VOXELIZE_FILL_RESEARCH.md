# Voxelize Fill: research and proposed implementation

Research date: 2026-10-31. Status: Voxelize Fill 0.1.0 implemented; see
[usage and implemented methods](VOXELIZE_FILL.md). The following records the research
and design proposal; GPU voxelization, conservative overlap and dedicated X-ray
line selection were not implemented. The tool uses cell-centre winding occupancy.

## Goal and confirmed scope

Add **Voxelize Fill** as a separate Atlas mesh tool. Build an occupied volume from a
source mesh, then construct the voxel scene from that volume. The user confirmed
3D mesh output for the existing drawing tools, rather than a dedicated SVG renderer.
The implementation includes both separate blocks and joined occupied boundaries.

Proposed identity: `voxelize-fill`, signature `atlas-toolkit:voxelize-fill`, output
`<name>_voxel-fill.stl`, with OBJ and PLY alternatives. Keep source units.

## Existing implementation

`tools/voxelizer/voxelizer.py` calls `mesh.voxelized(pitch=edge)`, optionally
`VoxelGrid.fill()`, and replicates a cube, sphere or cylinder at each occupied cell.
Its separate components retain internal faces. Its spec exposes resolution, gap,
primitive and an optional fill switch. It checks the output face count after
constructing the voxel grid, but does not preflight that grid's volume.

Voxelize Fill should make volume classification explicit, distinguish occupancy
from geometry and artistic presentation, and check grid and output costs before
large allocations. It should have its own schema, icon, presets, tests and docs.
Atlas discovers tool folders and supplies UI, CLI and Workflow integration from
their specs. Native polygon handling already exists in `geom/surface.py`.

## Research foundations

### Solid and conservative voxelization

Schwarz and Seidel, *Fast Parallel Surface and Solid Voxelization on GPUs*, 2010:
[author project and paper](https://michael-schwarz.com/research/publ/2010/vox/).
The paper distinguishes surface occupancy from solid occupancy, presents
triangle/box overlap tests, and describes sparse octrees with fine boundary cells
and coarse uniform regions. These are useful algorithmic references; this proposal
does not assume Atlas will reproduce the GPU implementation or its performance.

Two boundary policies are worth distinguishing:

- **Cell centre inside:** include a cell when its centre is classified inside.
  This is a clear solid rule, but sub-cell features can disappear.
- **Conservative:** also include every cell intersected by a source triangle.
  This retains thin details at the cost of expanding the silhouette by part of a
  cell. A conservative implementation needs an overlap test or an appropriately
  conservative surface method; ordinary triangle subdivision is not a proof of
  conservative coverage.

Exterior flood fill of a closed voxel shell is another option, but closes every
enclosed empty region, including intentional cavities. Shell gaps can also leak.
It must not be silently treated as equivalent to classifying the source solid.

### Winding numbers for scans

Jacobson, Kavan and Sorkine-Hornung, *Robust Inside-Outside Segmentation using
Generalized Winding Numbers*, 2013:
[author project](https://igl.ethz.ch/projects/winding-number/).
Barill et al., *Fast Winding Numbers for Soups and Clouds*, 2018:
[author project and implementation links](https://www.dgp.toronto.edu/projects/fast-winding-numbers/).

The generalized winding field measures how a surface surrounds a query point;
fast evaluation approximates distant contributions hierarchically. A threshold
can classify grid centres even for some imperfect meshes. This is an adaptation
of the winding-field idea, not the 2013 paper's full CDT/graph-cut pipeline.

Orientation matters, especially for nested cavity shells. Taking an absolute
value handles a globally reversed closed surface, but does not fix arbitrarily
mixed winding. A severely open scan has no uniquely recoverable interior.
Report inferred occupancy and input defects rather than claiming exact repair.
Atlas already declares libigl; inspect its installed bindings and existing usage
before choosing a query backend. Evaluate grid centres in bounded batches.

### Exploded volumes

Ruiz et al., *Similarity-based Exploded Views*, 2008:
[author project](https://www.cg.tuwien.ac.at/research/publications/2008/ruiz-2008-SEV/).
Their method partitions a volume into parallel slabs and separates them to
overcome occlusion. Atlas could use a simpler, explicitly user-directed version:
group occupied grid rows into slabs and translate those groups along an axis.
This would not reproduce their information-theoretic partition optimisation.

## Artistic algorithms to evaluate

The following are proposed Atlas adaptations and combinations, not claims of
newly invented research algorithms or already validated aesthetic results.

| Approach | Construction | Expected line-art effect | Drawing route |
|---|---|---|---|
| Full block volume | Place cubes at every occupied cell; control gap | Recognisable sculpture with a rigid architectural rhythm | Plotter Vision, visible features |
| Cutaway volume | Classify the full volume, then remove cells beyond a plane or inside a corner region | Exposed interior block cross-sections against the original silhouette | Plotter Vision, oblique view |
| Exploded strata | Translate groups of occupied layers while retaining every voxel | Repeated cross-sections and negative-space bands | Plotter Vision or Contour Lines |
| Adaptive blocks | Merge aligned complete 2x2x2 occupied groups recursively; retain smaller boundary blocks | Coarse masses and intricate stepped edges; most visible in cutaways or with gaps | Plotter Vision |
| Distance bands | Compute interior distance to the boundary, group by depth, separate groups or select bands | Nested stepped shells and archaeological strata | Contour Lines or Plotter Vision |
| Directional runs | Combine consecutive occupied cells into aligned rectangular bars | Cubist engraving, long structural strokes and fewer cells | Plotter Vision |
| Seeded erosion front | Remove cells according to a spatial front with reproducible correlated variation | A solid figure dissolving into blocks | Plotter Vision |
| X-ray blocks | Keep separate block geometry and draw hidden edges through the existing renderer | Dense transparent construction drawing | Plotter Vision hidden-line options |

Distance bands, erosion and selected X-ray lines are optional presentation effects;
the baseline must always offer a completely filled volume. Removing cells for an
effect must be explicit and reported separately from occupancy classification.
With opaque touching cubes, interior blocks are invisible from outside. More
interior voxels alone will not create a more detailed external drawing.

## Geometry and drawing considerations

- **Separate cells:** closed cubes, adjustable gaps, no Boolean union per cell.
  Appropriate for explosion, cutaway block grids and hidden-line experiments.
- **Joined volume, if selected:** emit faces only at occupied/empty boundaries.
  This is a boundary representation of a filled solid, even though internal cube
  faces are absent. Diagonal-only contacts can be non-manifold; validate and
  explain that case rather than promise universal printable topology.
- Coplanar neighbouring quads can retain grid lines in native polygon output.
  Plotter Vision's mesh-edge mode is relevant for those lines; its feature mode
  intentionally omits coplanar edges. Triangulated STL needs feature filtering to
  suppress face diagonals. Do not assume native polygons automatically fix
  coincident faces or overlapping strokes.
- Keep a deterministic grid anchor and coordinate transform. Check rotated-grid
  sampling and return geometry to the original coordinate system if offered.
- Do not coarsen final output silently. Report occupied cells, displayed cells or
  blocks, grid dimensions, pitch, faces and any inference about an open input.
- At zero gap, adjacent separate cubes have coincident internal faces. Joined
  geometry avoids those faces but changes which cell boundaries are drawable.
- A fully filled 100x100x100 box has one million cubes, or twelve million
  triangles as separate triangulated cubes. Preflight memory and face counts;
  combine array construction with progress/cancellation checks.
- Judge plot quality in paper millimetres: tiny projected gaps, repeated lines,
  very short strokes, pen lifts and excess hidden lines can overwhelm a drawing.

## Proposed validation and delivery

1. Analytic box: occupied cells at the centre and throughout multiple interior
   cross-sections; count and pitch under scale/translation; correct cube volume.
2. Concave closed shape, torus and disconnected solids: preserve through-holes,
   background and separate parts. Test an oriented hollow shell for cavity policy.
3. Reversed winding and open scan: verify chosen classification policy, explicit
   diagnostics, no unreported fallback to a surface shell.
4. Artistic modes: zero displacement reproduces baseline; explosion preserves
   occupied-cell count; cutaway follows its specified plane; adaptive merging
   preserves the underlying occupied volume when gaps are zero.
5. Validate invalid resolution, empty result and oversized grid before large
   allocations. Check mesh exports, signed metadata and schema/CLI/Workflow parity.
6. Compare drawings of the same sculpture at one fixed camera and paper frame.
   Inspect previews and SVGs for diagonals, duplicate strokes and legibility.
7. Run required Atlas checks, update README/changelog/version and build the exe
   according to the project's adding-a-tool instructions after implementation.

The subsequent implementation request adopted the broad collection and both
geometry options, with separate full cubes as the default. Depth bands are an
explicit cell-removal effect; X-ray drawing uses existing Plotter Vision controls.
