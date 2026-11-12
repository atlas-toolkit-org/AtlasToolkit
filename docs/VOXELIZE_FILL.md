# Voxelize Fill 0.1.0

Voxelize Fill converts a mesh into a **filled volume of cells**, then builds a
block sculpture from those cells. Find it under Mesh tools. It is independent of
Voxelizer and has its own presets and saved settings. No Blender is required.

The default is a full volume of touching, separate cubes. OBJ is the default
export because its square faces survive into Plotter Vision. PLY retains the
same faces; STL triangulates them. All files are signed for Atlas, keep source
units and placement, and carry source orientation metadata across formats.

## Start with a drawing

1. Load a sculpture and choose **Cutaway sculpture** or **Exploded strata**.
2. Preview and orbit the Output view. Preview computes final geometry; a large
   mesh may use a labelled display proxy, without changing the saved output.
3. Export OBJ or PLY. Open it in Plotter Vision with **Remeshed network** to draw
   square cell boundaries without triangulation diagonals. Leave decimation off.
4. Try an oblique view. Use Features/Clean instead when drawing STL or when you
   want only exposed creases. Contour Lines is useful for section rhythms.

The supplied workflows chain the same tools:
[Cutaway blocks](workflows/voxel_fill_cutaway.atlasflow) and
[Exploded strata](workflows/voxel_fill_exploded.atlasflow).

## Controls

| Control | Behaviour |
|---|---|
| Voxels across | Cell count along the longest source dimension; default 32. The grid is centred on source bounds. |
| Cell edge | Alternative fixed pitch in original model units. Short dimensions round up to whole cells. |
| Input | Infer scan interior permits open inputs with diagnostics. Closed mesh only rejects non-watertight inputs. |
| Geometry | Separate blocks retain every block, including internal faces. Joined volume removes faces shared by occupied cells. |
| Block layout | Uniform cubes; Adaptive cubes merge aligned complete groups of eight; Directional bars merge consecutive occupied runs. |
| Block gap | Clearance as a percentage of the smallest cell edge, default zero. Adaptive blocks and bars use the same absolute clearance. |
| Merge levels | Maximum adaptive cube size is 2 to this power, in cells. Merging never crosses empty cells or exploded slab boundaries. |
| Bar direction | X, Y or Z in source coordinates. Every bar is a closed rectangular solid. |
| Effect | Full volume, Exploded layers, Depth bands or Erosion front. |
| Cells per layer | Thickness of each exploded slab before displacement, measured in cell rows. |
| Layer separation | Extra gap between slabs as a percentage of cell edge. Slabs translate symmetrically about the original grid centre. Zero reproduces the baseline. |
| Cells per band | Thickness of alternating distance-to-boundary bands. The first band is retained; the next is removed. |
| Erosion | Advances a removal front in the selected positive-axis direction. Zero preserves the full volume; 100% leaves no geometry and reports an empty-result error. |
| Front roughness / Seed | Correlated spatial noise shapes the erosion front. The same input, resolution and seed reproduce the result. |
| Expose the interior | Cutaway plane applied before packing, combinable with every effect and layout. |
| Plane normal | Arbitrary nonzero XYZ vector perpendicular to the cut plane, in source coordinates. |
| Plane position / Keep | Place the plane across the grid's projected bounds, then keep its lower or upper side. Cells are selected by centre; the exposed cut is stepped. |

Joined volume always uses full uniform cells without block gaps or adaptive/bar
packing. Those controls are hidden and inactive in joined mode. Exploded slabs
remain separate closed boundaries where their faces separate.

## Nine presets

- **Full volume:** every interior cell becomes a full cube.
- **Block study:** regular blocks with small clearances.
- **Cutaway sculpture:** an oblique cut exposes a dense interior grid.
- **Exploded strata:** groups of filled layers float apart; cell count is preserved.
- **Adaptive excavation:** large interior cubes contrast with fine boundary cells.
- **Directional engraving:** long vertical bars revealed by a cutaway.
- **Nested depth bands:** alternating stepped shells, exposed by a cutaway.
- **Dissolving blocks:** a seeded irregular erosion front.
- **Joined solid:** the occupied volume's exterior, with shared internal faces removed.

Depth bands use Euclidean distance on the sampled occupied grid, with an exterior
padding layer. They are not exact offsets of the original mesh. Erosion and bands
explicitly remove cells after full occupancy classification. Logs distinguish
source occupied cells from retained cells, removed cells and generated blocks.

## What full volume means

Cell centres throughout the source bounds are classified using libigl's fast
generalized winding numbers: a cell is occupied when the absolute winding value
exceeds 0.5. The method does not merely thicken or fill a sampled surface shell.
Closed models with correctly oriented inner shells retain cavities and through-
holes. Reversing the entire mesh does not change its occupancy. Inconsistent
adjacent winding is repaired on a working copy; the source file is unchanged.

This is **cell-centre solid voxelization**, not conservative triangle/box overlap
voxelization. Features smaller than a cell can disappear and boundaries move by
part of a cell. Increase resolution for thin details. Open scans have an inferred
interior, not a uniquely recoverable solid. Missing surfaces, overlapping shells
with opposing orientations and mixed disconnected shell orientations can change
the inference. Review warnings and use Closed mesh only for strict input checks.

A joined solid is represented by its boundary mesh; internal cube faces are
unnecessary to describe its full volume. Diagonal-only cell contacts may create
non-manifold edges or vertex pinches. Edge non-manifold contacts are reported;
joined output is not certified printable. Separate cells at zero gap retain
coincident internal faces; use a nonzero gap to expose blocks or Joined volume
for a clean external boundary. Native square faces suppress diagonals, not
coincident lines from touching separate blocks.

## Drawing recipes

| Look | Voxelize Fill | Drawing tool |
|---|---|---|
| Architectural sculpture | Block study, or Cutaway sculpture | Plotter Vision, Remeshed network, oblique view |
| Floating cross-sections | Exploded strata; 2–4 cells/layer, 150–250% separation | Plotter Vision, Clean or Remeshed network |
| Excavated monolith | Adaptive excavation, 2–4 merge levels | Plotter Vision, Remeshed network |
| Cubist engraving | Directional engraving; change bar direction | Plotter Vision, Clean |
| Archaeological shells | Nested depth bands, 1–3 cells/band | Plotter Vision or Contour Lines |
| Disintegrating portrait | Dissolving blocks; erosion 30–60% | Plotter Vision, Features or Clean |
| Transparent construction | Separate blocks, coarse grid, small gap | Plotter Vision with hidden lines enabled |

Interior cells are hidden by opaque outer cells. Filling the volume alone does
not increase external drawing detail. Cutaways, gaps and explosions expose it.
Transparent drawings can become very dense; begin with a coarse grid and check
stroke count, pen width and projected spacing before committing to a plot.

## CLI

```powershell
atlas-toolkit describe voxelize-fill
atlas-toolkit run voxelize-fill model.stl --preset "Cutaway sculpture" -o blocks.obj
atlas-toolkit run voxelize-fill model.stl --geometry joined --across 48 -o solid.stl
atlas-toolkit run voxelize-fill model.stl --effect explode --layer-cells 3 --separation 200 -o strata.ply
```

UI, CLI and Workflows use the same schema and engine. Exports are staged and
signed before replacing the destination. Preview writes no output file. Long
phases report progress, and the tool runs in a cancellable worker process.

## Cost and validation

The full grid is limited to 8 million cells and generated geometry to 4 million
triangles. Limits raise actionable errors; final resolution is never silently
reduced. A completely filled 100-cube grid contains one million cubes and would
require twelve million triangles as separate blocks. Adaptive blocks, bars and
joined boundaries can greatly reduce output cost, while the occupancy grid
itself remains dense. Winding queries and block construction use bounded batches.

Regression tests cover analytic interior counts, oriented cavities, through-holes,
disconnected parts, inversion, local winding repair, open-input policy, scale and
translation, packing volume preservation, explosion, bands, erosion, bounds and
face guards, native exports, non-writing previews and Plotter Vision integration.
Run the repeatable real-model comparison with:

```powershell
.venv\Scripts\python scripts\dev\validate_voxel_fill.py --out build\qa\voxel_fill_validation
```

See [research, primary sources and algorithm distinctions](VOXELIZE_FILL_RESEARCH.md).
