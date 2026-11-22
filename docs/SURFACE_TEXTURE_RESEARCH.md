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

The helper transports little-endian arrays through protocol 3 (protocol 2 plus xatlas's chart options) and returns
per-face-corner UVs, chart IDs and original vertex references. It never changes geometry topology. Atlas verifies
face correspondence and retains one geometric vertex at every shared edge, including UV seams. The helper is killed
with its worker on cancellation.

xatlas keeps every chart's stretch within about 10%, so on a scan it cuts wherever the normals are noisy: 1,124
charts on the 260,000-face catalogue David head (about half the skin within a seam feather), 74 on the 15,000-face
bust, most smaller than one texture element; raising `maxCost` changes nothing. Atlas therefore charts a proxy: the
surface simplified to 8,000 faces and smoothed (40 Taubin iterations, which keep its size). The head then gets 84
charts, the bust 26, in about a second; the UV scale stays within about 0.7-1.05 (10th-90th percentile). Each source
face takes the chart of the nearest proxy face turned the same way (so the far side of an ear never lends its chart),
and each vertex one UV per chart, projected on that chart's nearest face: UVs stay continuous inside a chart. At a
seam each side also samples the neighbouring chart, and the two cross-fade over Seam blend, meeting half and half.

## Refinement and safety

Geometry starts on the original triangle surface. Shared edge midpoints make every split conforming. A flat patch
made of long slivers (the fan that closes a cut: trimesh's `slice_plane` caps, as on the catalogue's cropped busts) is
first re-triangulated with a clipped Delaunay grid of the same outline, shape and area unchanged: refined as it was,
the head's 623-face neck cap became 532,540 faces and 96% of the box overlaps the crossing test had to examine.

Refinement first splits edges longer than element/6 (High: /10), then edges of faces whose sampled height error
(edge midpoints and centre against the linear surface) exceeds Detail tolerance, a share of the relief: the face
count then does not depend on the relief height. In 0.1 the tolerance was a share of the model's height, so relief
1% h needed 2.7 times the faces of 0.4% and 2% h six times. Error splits are red (all three edges, avoiding thin
green slivers), with a longest-edge closure, but never of an edge below element/24 (High: /40): a crisp step in the
heightmap otherwise never meets the tolerance, and a sliver would be cut into crumbs across its width. Only faces
created in the last pass are sampled again, per-vertex heights are kept, and edges are found by one integer key each.
The face budget is spent worst-first: when the closed split of every wanted face does not fit, a bisection over the
error order finds the most that does, and refinement stops there and reports the share of the surface that met the
tolerance. Regional bounds include intersected coarse triangles, even when their corners lie outside the selected
band/sphere.

Displacement follows vertex normals smoothed by one implicit diffusion step over a quarter of an element (neighbours
whose normals are over 90 degrees apart do not mix, so a thin rim keeps its two sides). Sampling is bounded by motif
width and tested at edge midpoints and triangle centres; these are numerical samples, not a proof that arbitrary
user imagery contains no unsampled spikes. Cancellation is checked throughout. Original open borders can be pinned
with a smooth taper.

Crossings are found by `geom/contact.py`: a uniform grid over the faces' bounding boxes gives each candidate pair
once (in the cell of the low corner of the two boxes' overlap), shared-corner pairs are dropped, and a plane cut tests
the rest; coplanar pairs are tested by separating axes in their plane. A crossing needs a cut longer than a
billionth of the model, so faces that only touch do not count. On jittered spheres it finds exactly libigl's pairs;
on a textured bust libigl also reported five pairs of nearly parallel faces that exact rational arithmetic shows do
not cross. After a local change, only boxes that share a grid cell with a changed face are gridded again (a
summed-volume table finds them), so a re-test costs a fraction of a full pass.

Protection limits each vertex's motion to 45% of the free distance along it (a ray past its own faces), feathered,
then repeats: find every flipped or collapsed triangle and every new crossing, and restrain the relief around them
with a smooth falloff over a quarter of an element, re-testing only what moved. Zero relief is the valid source, so
the loop always ends; restraint widens if a patch keeps failing, but nothing is scaled down globally. 0.1 reused
Dilate's guard, which tested 128 contacts per pass for 12 passes and then halved the whole relief: at 1.5% h it kept
12% of it. A source's own few crossings are left as they are, without relief around them; a surface with many is
refused.

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
.venv/Scripts/python scripts/dev/validate_surface_texture.py --benchmark
```

This textures the tool's sample ellipsoid, the 15,000-face David bust (studio workspace, when configured) and the
bundled 260,000-face catalogue David head with every wrapping at 0.4, 1 and 2% h, then draws the original and each
result with Contour Lines, Sketch Hatching and Plotter Vision from the same camera (A3 / 0.3 mm). It writes the
meshes, SVGs and PNGs to `build/qa/surface-texture`, a contact sheet per drawing tool, `summary.md` and
`acceptance.json`: success or the error, faces, time per phase, achieved relief, displacement restrained, the share of
the surface that met the tolerance, and per drawing a visibility score, the share of the textured drawing's ink farther
than a pen width from the original drawing's ink. `--quick`, `--models`, `--mappings`, `--reliefs`, `--drawings`,
`--set key=value` and `--presets` (the official presets on the bust) narrow or widen it; `--resume` keeps finished
cases. The JSON is written after every case, retaining evidence if a later case fails.

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

## Measurements from the 2.4.7 acceptance run

On this Windows / Python 3.14 workstation, before and after 0.2.0, with default settings unless stated (15k bust:
`david_15k_reshape.stl`; head: the bundled catalogue David head, 258,710 faces):

| Case | 0.1.1 | 0.2.0 |
|---|---|---|
| 15k bust, defaults, triplanar / cylindrical / follow surface | 32 s / fails at 407,600 faces / 91 s | 6–10 s / 7–14 s / 8–10 s |
| 15k bust, relief 1.5% h, triplanar | 168 s, 88% of the displacement restrained | 8 s, 4% |
| 15k bust, relief 3% h (8% elements) | 380 s, 97% restrained (0.09% h achieved) | 8 s, 9% (3% h achieved) |
| 15k bust, relief 4% h on 4% elements, cylindrical | fails | 27 s, 29% |
| head, every wrapping, relief 0.4 and 1% h | fails ("did not converge" or the face limit) | 18–42 s, under 2% restrained |
| head, follow surface charts | 73 s, 1,124 charts | about 6 s, 84 charts |

All 27 cases of the acceptance (ellipsoid, bust and head; three wrappings; relief 0.4, 1 and 2% h) finish without
reaching the 1,000,000-face budget: 160,900–531,860 faces, at most 10% of the displacement restrained (2% h on the
head). The visibility score, the share of the textured drawing's ink farther than a pen width from the original's,
rises with relief as the drawings suggest: on the bust at 0.4 / 1 / 2% h, 5–6 / 17–20 / 28–34% in Contour Lines,
42–49 / 56–58 / 65–67% in Sketch Hatching and 33–57 / 77–84 / 88–90% in Plotter Vision. Times include saving and
reloading the output; drawing times and peak memory are in the acceptance JSON.

The fixed-resolution comparison used 217,014 triangles / 108,509 vertices. NumPy height sampling plus displacement
took 0.0092 seconds. Blender 5.2.2 LTS mesh/image/modifier setup plus displacement took 1.688 seconds, or 3.623
seconds including cold process startup. RMS output position difference was about 9.55e-7 model units at requested
height 0.1607 model units. These are measurements of different setup paths with the same topology/UV/image;
they are not an end-to-end speed ratio for the full tool.

The fixed-resolution comparison above is from the 1.13.1 run. The v17 head scan (126,724 faces) is closed but crosses
itself in 280 pairs of faces (0.2% of them); 0.1 refused it with collision protection on, 0.2 textures around those
pairs with a warning, as it does the catalogue head's single pair. A surface where more than 1% of the faces cross
is still refused.
