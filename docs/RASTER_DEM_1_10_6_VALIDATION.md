# Raster DEM to Mesh 0.2.0 validation

Atlas 1.10.6, Windows CPython 3.14. Measurements are local observations, not portable timing guarantees.
The supplied source rasters were hashed before and after acceptance runs and remained unchanged.

## Responsiveness

AMinho is 3,319 × 3,033 cells. A fresh Placement reader displayed its first bounded raster in **0.38 s** and
completed exact preparation in **0.79 s**. A Balanced mesh draft took **1.73 s** cold and **0.64 s** warm,
with 59,180 complete-solid triangles. Final export settings were independent of that draft.

In the actual styled app's expanded editor, synchronous changes, repaint and event processing measured
**23.5 ms rotation / 14.2 ms plate resizing** at the 95th percentile over 50 updates each. Both are within
the 33.3 ms frame target. The simpler isolated placement benchmark measured 16.8 / 2.6 ms respectively.
Dark/light main-window and expanded-editor captures were inspected. OpenGL close-ups use the saved full
mesh, without the interactive preview's display proxy.

## Saved printable meshes

Common settings: A4 portrait, 10 mm margin, geographic scale, z-factor 1, 2 mm plate, 2 mm basal lift,
5 mm padding, zero rotation, 0.002 mm explicit point-contact clearance and a 2M triangle budget. Viana also
excludes raw background value 10; its previous elevation transformation is not reversed.

| Source | Detail | Complete solid triangles | Maximum source-height error | Build and reload |
|---|---|---:|---:|---:|
| AMinho | Standard, 0.25 mm grid | 551,926 | 0.09664 mm | 17.9 s |
| AMinho | Detailed faithful | 244,110 | 0.01800 mm | 16.7 s |
| AMinho | Fine faithful | 613,254 | 0.00900 mm | 62.5 s |
| Viana | Standard, 0.25 mm grid | 829,696 | 0.06200 mm | 31.7 s |
| Viana | Detailed faithful | 218,116 | 0.01800 mm | 20.6 s |
| Viana | Fine faithful | 722,816 | 0.00900 mm | 75.1 s |

Adaptive horizontal terrain edges measured below their requested 1 mm / 0.5 mm limits after STL reload.
All six outputs passed closed-edge and vertex-fan topology, consistent winding, positive volume, connected
solid and physical-dimension checks. The Standard error is an additional comparison measurement; regular
grid sampling does not claim an adaptive error guarantee. Adaptive RMS errors were approximately
0.0058–0.0066 mm at Detailed and 0.0030 mm at Fine.

AMinho also passed signed printable Smooth, Faceted and Terraces exports at 1 mm grid pitch: 71,726,
41,562 and 53,676 triangles respectively. Faceting retained more than the approximate target to protect
the native coastline. Terraces retained the deliberately quantized elevation levels.

## Regression and frozen checks

The tests cover flat/ramp/ridge/peak/valley relief, negative elevations, holes/islands, coarse-grid mask
clipping, adaptive maximum/RMS error, conforming edge length, direct stitching, file precision, all three
export formats, both budget settings, low-memory rejection, cancellation, physical scale locking, rotation
conflicts and recovery by plate enlargement, rounded corners, aspect locking and expanded-editor rollback.
A 5,001 × 5,001 synthetic raster exercises bounded reading beyond the old 25M-cell limit, exact statistics,
immutable cache reuse and source-change invalidation. Streamed native outlines are checked in projected,
geographic and rotated coordinate systems.

Packaged checks exercise every tool and a workflow, the GUI Home snapshot, console routing/exit codes,
geographic reprojection, 16-bit PNG input, native Adaptive TIN, locked custom plate flags, a generated
1,600 × 1,300 GeoTIFF Placement snapshot and the terrain contour workflow. Bytecode comparison verifies
all DEM modules in the executable against current source; the native extension and pydelatin/hmm/GLM
notices are checked inside the archive.

Reproduce the sample comparisons with `scripts/dev/validate_raster_dem_detail.py`; use `--mode ui`, `meshes`
or `renders` separately. On Windows, renders require a working Windows/OpenGL Qt platform, rather than
the offscreen platform used for headless unit tests. Artifacts and JSON timings are written to
`build/qa/dem106_validation` in the repository. `scripts/check_frozen_raster.py` and
`scripts/check_frozen_dem_code.py` accept `--exe` for packaged release verification.
