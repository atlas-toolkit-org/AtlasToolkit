# Heightfield robustness validation — Atlas 1.11.1

Raster DEM to Mesh 0.3.1 and Image to Mesh 0.1.1 share these corrections. No new runtime libraries or
C++ source changes are required. Existing manual settings remain valid; the new contact-policy parameter
defaults to Manual, preserving strict zero-clearance behavior for existing DEM configurations.

## Reproduced failures and corrected behavior

The reported A3 Smooth regular-grid surface was valid immediately after clipping, then acquired five
non-manifold vertex fans during closed-volume precision simplification and top-face extraction. That
conversion has been removed. Stable surfaces keep their original vertices. Unsafe sub-format edges use
local collapses with the manifold link condition, preserved XY orientation and protected component survival.
Native and simulated saved copies are validated. Tiny unrepresentable islands fail rather than disappear.

The A4 fine-grid case acquired six bad vertex fans in the old pipeline. It now reaches a validated printable
solid. Ordinary solids use local repair; already boolean-built planar terraces retain a validated closed-volume
fallback. No open terrain skin is extracted from simplified solid face normals.

Native/preview fit disagreement is covered by synthetic projected DEM and alpha-mask image regressions,
with locked scale at 0°, 15°, 35°, 65° and 90°. DEM readers use the same authoritative source outline at native
and reduced resolution. Reprojected masks exclude target cells outside the true source domain. The first
quick raster can display immediately, but fitting actions await authoritative preparation. Known selected-file
placement conflicts prevent explicit Preview/Build submission; CLI/Workflow engines still validate independently.

## Real and representative export checks

`scripts/dev/validate_heightfield_fixes.py` hashes the supplied AMinho source before and after the acceptance run.
It writes results only under `build/qa/heightfield111_validation` in the repository.

| Case | Saved triangles | Geometry | Verified formats |
|---|---:|---|---|
| AMinho A3 Smooth, 0.2 mm grid, z-factor 8, 0.01 mm clearance, 5M budget | 2,176,898 | Open drawing surface | STL, OBJ, PLY |
| AMinho A4 Faithful, 0.1 mm grid, background 0 excluded, 0.01 mm clearance, 7M budget | 3,386,308 | One watertight positive-volume solid | STL |
| AMinho Faithful Adaptive TIN, 0.02 mm vertical tolerance, automatic clearance | 248,688 | One printable solid | STL |
| 1,200 × 960 alpha-mask image, grid, gamma 1.2 | 361,854 | One printable solid | STL, OBJ, PLY |
| Same image, Adaptive TIN, 2 mm maximum XY edge | 709,657 | Open drawing surface | STL |

The adaptive DEM maximum source-height error measured **0.01800 mm**. The image fixture includes holes
and a curved alpha silhouette; its calibrated black/white heights and explicit gamma are retained. All saved
meshes pass edge/fan/winding checks and dimension comparisons; solids additionally pass volume/connectivity.
Small native/saved face-count differences can result from harmless format-precision merging, with all checks
repeated on the saved mesh. The supplied DEM is unchanged.

## Responsiveness and UI checks

`scripts/dev/validate_heightfield_ui.py` exercises the actual styled app, expanded placement editor, themes and
cached draft pipeline. Local Windows measurements over 50 updates per action:

| Tool | Rotation p95 | Plate resizing p95 |
|---|---:|---:|
| Raster DEM to Mesh | 22.8 ms | 12.2 ms |
| Image to Mesh | 13.1 ms | 10.5 ms |

Both remain within the 33.3 ms interaction target. AMinho Balanced drafts took **2.00 s cold / 0.95 s warm**
in the measured pipeline. These observations are workstation-specific. Dark/light screenshots and expanded
views show the physical source-cell clearance limit, native contact count and masked output estimate.

## Additional regression and release gates

- Cleanup returns the actual validated mesh, including a synthetic collapsed-edge closed-solid regression.
- Sparse masks are not rejected using the full raster rectangle. Exact clipping checks budgets early;
  ordinary printable preflight includes perimeter walls, deck and bottom. No selected final quality is silently reduced.
- Manual clearance errors include the physical source-cell limit and a valid suggestion; Automatic safe
  clearance is explicit. Input/preparation/mask settings survive built-in artistic preset changes unless
  that preset declares an override. User presets remain full snapshots.
- Both UI tools reject known invalid requests before submitting workers, recover after corrected settings,
  and retain physical scale. The controls used only by another sampling/height mode are hidden.
- Worker failure diagnostics retain elapsed time, mode, job ID, submitted parameters and last phase;
  unexpected geometry failures retain traces. Failed history records no longer default to zero seconds.
- Full unittest discovery covers all tools. Frozen self-tests exercise every tool, CLI routing, GUI snapshots,
  both image/terrain workflows, geographic/16-bit inputs, adaptive native code, locked custom plates and
  automatic contact repair with holes/islands in both readers. Archive bytecode and dependency notices are
  compared with source using `scripts/check_frozen_dem_code.py` before publication.
