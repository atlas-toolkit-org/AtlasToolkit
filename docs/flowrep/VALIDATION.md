> Validation of the research checkout this library came from (version 0.1.1). The `output/` and `models/` paths
> below refer to that checkout, which is not part of this repository.

# Validation — 2026-10-28

Tested in the local Python 3.14.3 environment, using Atlas's scientific dependencies.

- 38 automated tests passed; see output/tests-final.log.
- All 24 supplied meshes extracted, rendered and compared against their author networks.
- Every generated network meets the measured 20° patch criterion; maximum 19.99588°.
- Every generated SVG satisfies the 0.05 mm fitting bound; maximum 0.04994864 mm.
- Largest sampled network-to-surface 95th-percentile distance: 0.10261% of model diagonal.
- 58 original input files were compared with the supplied archive; none changed.

## What was checked

Analytic cases cover planes, spheres, cylinders, tori, a sharp quad box, open boundaries and
disconnected components. Checks include signed affinities, KL objective improvement, endpoint-exact
normal interpolation, geodesicity, multi-boundary patch topology (including thin annuli), threshold
coverage, termination, 10°/30° presets, reproducibility on fixed geometry, remeshing and cache reuse.
Touching vertex fans are split and reported; the espresso and plane inputs needed this repair.

Rendering checks cover front/back occlusion, near-plane clipping, coincident ink, point crossings,
continuous cubic error bounds, fixed endpoints, units, margins, native path layers, finite parameters,
camera independence and cancellation. Extraction cancellation is checked both immediately and during work.

Atlas contract tests and a spawned worker verify dry Drawing previews and signed SVG run metadata.
The real source app's Drawing preview was also captured in output/atlas-flowrep-preview.png.
The existing Atlas source, profile and executable were preserved; the launcher uses its own local profile.
The wheel installation check, including native triangle preparation and a Qt-free import, is reproducible
with scripts/wheel_smoke.py; its machine-readable result is output/wheel-smoke.json.

## Reference measurements

Author and generated networks use the same renderer and per-model view. The camera overrides in
examples/views.json correct the boats' and dog head's axes. Author networks are reference data, not
generated results. Sampled curve distance measures agreement with those networks, not reconstruction
accuracy or perceptual equivalence. Curve counts include trim curves and depend on curve splitting.
The bunny author network uses a different scale and origin; a surface-verified uniform scale/translation
is recorded in its reference_registration field before rendering and comparison. No rotation or deformation
is fitted. The supplied files remain unchanged.

| Model | Curves ours / author | Junctions ours / author | Max error ° | Mean curve distance % diagonal | Extract s | Redraw s |
|---|---:|---:|---:|---:|---:|---:|
| bathtub | 15 / 140 | 61 / 72 | 18.81 | 1.265 | 217.92 | 0.99 |
| beetle | 83 / 233 | 92 / 134 | 19.65 | 0.565 | 33.94 | 1.19 |
| big buck bunny | 427 / 204 | 2064 / 1218 | 19.94 | 0.397 | 539.39 | 25.71 |
| boat-artmesh | 79 / 175 | 379 / 101 | 19.28 | 0.980 | 810.25 | 1.12 |
| boat-qm | 275 / 612 | 8686 / 326 | 20.00 | 0.908 | 748.17 | 92.95 |
| bottle | 74 / 307 | 321 / 156 | 19.46 | 0.685 | 350.99 | 0.81 |
| doghead | 82 / 193 | 379 / 102 | 19.97 | 1.001 | 290.11 | 0.94 |
| ellipsetorus | 47 / 9 | 266 / 30 | 19.41 | 1.485 | 12.58 | 0.77 |
| espresso | 188 / 318 | 363 / 184 | 19.43 | 1.019 | 49.87 | 1.67 |
| mouse | 74 / 304 | 542 / 156 | 18.88 | 0.712 | 628.78 | 1.40 |
| mug | 30 / 70 | 115 / 37 | 19.81 | 1.327 | 61.28 | 0.44 |
| phone | 20 / 118 | 115 / 62 | 19.93 | 1.405 | 51.59 | 0.37 |
| plane | 373 / 296 | 1009 / 160 | 19.99 | 0.547 | 144.66 | 3.62 |
| rowboat | 134 / 177 | 1465 / 111 | 19.81 | 0.991 | 258.63 | 3.26 |
| spherecylinder | 14 / 5 | 50 / 14 | 19.31 | 2.660 | 0.92 | 0.19 |
| spring | 65 / 9 | 63 / 30 | 5.86 | 1.497 | 1.05 | 0.30 |
| toilet | 174 / 345 | 1252 / 191 | 19.99 | 1.023 | 547.76 | 3.16 |
| toothpaste | 33 / 6 | 239 / 18 | 19.48 | 0.963 | 160.83 | 0.59 |
| torus-filled | 26 / 5 | 2 / 14 | 0.00 | 2.028 | 3.38 | 0.56 |
| torus-noisy | 8 / 32 | 16 / 16 | 15.03 | 2.561 | 11.06 | 0.12 |
| torus-simple | 65 / 8 | 64 / 16 | 0.00 | 1.776 | 2.13 | 0.56 |
| toy | 1 / 6 | 0 / 9 | 14.68 | 4.472 | 0.02 | 0.02 |
| trebol | 6 / 36 | 18 / 18 | 19.91 | 1.257 | 336.99 | 0.36 |
| wineglass | 82 / 88 | 81 / 45 | 15.29 | 1.314 | 30.76 | 0.87 |

Stored extraction times span 0.02–810.25 seconds; camera-only drawing spans
0.02–92.95 seconds. The first evaluation used four model workers; timings are
wall times under varying CPU load, not hardware-normalized comparisons. Cached networks retain their
original extraction timing; extraction_call_seconds separately records the actual cached call.
The drawing refresh changes cameras without rerunning extraction. SVG fitting is measured by its bound;
render timing excludes SVG serialization/fitting, which occurs afterward.

Working sets and cumulative process peaks are recorded at stage boundaries in the JSON. They are not
isolated allocation peaks for each stage. Parallel cases used a fresh worker per model; the espresso
and plane topology-repair reruns share one process and are labeled accordingly.

## Dense triangle preparation

A subdivided dog-head STL with 117,760 triangles produced 6,070 quads
and 88 curves. Maximum measured patch error: 19.90206°.
Native preparation took 2.03 seconds; total extraction took 127.01 seconds.
Prepared-vertex 95th-percentile surface deviation was 0.00354% of source diagonal.
The original triangles remain the rendering/projection surface. Files are in output/dense-triangle.

## Practical limits

This is an independently optimized reproduction. Networks can be substantially denser and less balanced
than the authors' outputs, particularly for complex organic forms and some remeshed inputs. Fixed-mesh
reproducibility does not imply identical native remeshing on every platform. The normal metric omits
isolated unpredicted vertices as specified in the paper; their counts are reported in the diagnostics.
It is a sampled constraint, not a guarantee of perceptual quality or worst-case surface approximation.

The boat-qm case has many junctions and pen lifts and a comparatively slow redraw. Retaining junctions
and occlusion endpoints can produce more strokes than an artist would use. Preparation quality, view,
sampling scale and model resolution still matter. See RESEARCH.md for algorithm differences.
