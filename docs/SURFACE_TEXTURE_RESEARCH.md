# Surface Texture: method and reproducible evaluation

## Why real geometry and a native helper

Atlas's existing mesh-to-SVG tools consume geometry: silhouettes, sections, creases and curvature. Material-only
normal/bump maps do not change those geometric signals. Surface Texture therefore samples a height field and
displaces actual vertices. It leaves all existing drawing-tool defaults unchanged.

Blender's Displace modifier offers texture-driven vertex displacement and vertex-group influence:
[manual](https://docs.blender.org/manual/en/latest/modeling/modifiers/deform/displace.html).
It is a viable external implementation, but still needs enough tessellation and applied geometry for Atlas export.
Cycles render-time adaptive subdivision is camera-dependent rendering machinery, rather than a direct replacement
for a reusable mesh preparation step.

[xatlas](https://github.com/jpcy/xatlas) supplies automatic charting, parameterization, physical texel density,
original-vertex cross references and progress callbacks. It is MIT licensed and can be integrated through two
C++ source files. Atlas bundles a headless helper instead of requiring Python ABI-specific xatlas wheels or a
Blender installation. Sources are pinned to `f700c7790aaa030e794b52ba7791a05c085faf0c`.

The helper transports little-endian arrays through protocol 1 and returns per-face-corner UVs, chart IDs and
original vertex references. It never changes geometry topology. Atlas verifies face correspondence and retains
one geometric vertex at every shared edge, including UV seams. The helper is killed with its worker on cancellation.

## Refinement and safety

Geometry starts on the original triangle surface without smoothing or global remeshing. Shared edge midpoints
make every split conforming. Regional bounds include intersected coarse triangles, even when their corners lie
outside the selected band/sphere. Texture-error triangles split all three edges to avoid repeatedly creating
thin transition slivers. Vertex normals interpolate from the original mesh; displacement samples at UV corners
are area-weighted onto welded vertices. A graph-distance feather suppresses chart-boundary discontinuities.

Sampling is bounded by motif width and tested at edge midpoints and triangle centres. These are numerical samples,
not a proof that arbitrary user imagery contains no unsampled spikes. Conservative face/memory limits and repeated
cancellation checks bound work. Original open borders can be pinned with a smooth taper.

Atlas reuses its R-tree/libigl nonadjacent-triangle checks and local contact backtracking. Additional normal-ray
clearance limits prevent straightforward thin-shell tunnelling that endpoint-only intersection tests miss.
The guard is deliberately conservative and reports reduced displacement rather than silently claiming the
requested height was reached. Existing intersecting inputs are rejected while protection is enabled.

## Texture sources and preparation

- [ambientCG licence](https://docs.ambientcg.com/license/): all selected materials permit CC0 redistribution.
- [TextureCan terms](https://www.texturecan.com/terms/): PBR texture maps are CC0.
- [Poly Haven licence](https://polyhaven.com/license/) and
  [texture standards](https://docs.polyhaven.com/en/technical-standards/textures) were also reviewed; selected
  ambientCG/TextureCan maps provide the initial motifs without a runtime API integration.

Only actual displacement/height maps are used, not albedo interpreted as height or generated substitute textures.
`scripts/prepare_surface_textures.py --cache <archive-folder>` reproduces the twelve packaged tiles. Sources use
identified Atlas requests; full archives remain outside the app package. The script records source precision,
hashes, URLs, filtering, percentile levels and tile-edge preparation. Prepared thumbnails are generated locally.
Source maps with narrow physical displacement ranges are expanded for readable graphic relief; custom input
heightmaps keep their original 0..1 calibration, including constant maps.

## Reproduce visual and performance acceptance

```powershell
.venv/Scripts/python scripts/dev/validate_surface_texture.py --out <QA-folder> --benchmark
```

This exports every material on a curved form, a cylindrical band and representative bust cases. Bust cases use
at least 8% element size, 0.06% sampled tolerance and a 600,000-face limit for their more complex original topology. It produces
actual A3 / 0.3 mm Contour Lines and Plotter Vision SVGs, PNGs, contact sheets and `acceptance.json`, including
triangle counts, achieved relief, cache status, preparation/repeat times, drawing times and process peak memory.
The decimated bust uses a 35-degree feature cutoff to suppress original facet edges; the smooth curved forms
use 12 degrees to show shallow texture. This does not change drawing-tool defaults.
The JSON is written after every case, retaining evidence if a later case fails. The sketch workflow is covered
separately by Workflow validation and can be run directly for botanical line drawings.

`--benchmark` compares the same tessellation, UV coordinates and prepared height field using NumPy sampling
and Blender's actual Displace modifier. `benchmark.json` reports native displacement time, Blender mesh/image/
modifier setup and execution, cold-process time, output RMS difference and the matched resolution. This isolates
displacement; neither timed path includes chart generation, refinement, seam/pole taper, masking or collision
checks. Texture filtering and normal interpolation can differ. These measurements do not establish that every
Atlas operation is faster than Blender, and no universal speed claim is made.

Acceptance focuses on recognisable motifs, useful white space, retained model identity and avoidance of dominant
triangulation artifacts. Compare against originals at intended print scale. Mesh validation alone is insufficient.
The full regression suite and release builder additionally exercise source/frozen UI, CLI, Workflows and staged
export publication. No hardware performance threshold is asserted across unrelated machines.

## Measurements from the 1.13.1 acceptance run

On this Windows / Python 3.14.3 workstation, the twelve curved-form builds produced roughly 76,000–276,000
triangles. Complete runs including refinement and collision protection took approximately 6–29 seconds;
the David bust examples produced 121,080 (scales) and 172,400 (bark) triangles. Actual drawing times, cache timings
and cumulative process peaks are in the generated acceptance JSON, not inferred from these mesh counts.

The fixed-resolution comparison used 217,014 triangles / 108,509 vertices. NumPy height sampling plus displacement
took 0.0092 seconds. Blender 5.2.2 LTS mesh/image/modifier setup plus displacement took 1.688 seconds, or 3.623
seconds including cold process startup. RMS output position difference was about 9.55e-7 model units at requested
height 0.1607 model units. These are measurements of different setup paths with the same topology/UV/image;
they are not an end-to-end speed ratio for the full tool.

The existing v17 head scan was also checked as an invalid-input case: it is closed but contains intersections.
Collision-protected texturing correctly rejected it. The clean David model supplies the positive bust cases.
