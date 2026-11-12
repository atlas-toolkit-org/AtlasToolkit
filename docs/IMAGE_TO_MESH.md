# Image to Mesh

Atlas Toolkit 1.11.1 · tool 0.1.1 · **Image → Mesh**

Convert a photograph, illustration or numeric grayscale heightmap into a physical relief, or an open surface
for pen-plotter line art. Geographic DEMs belong in [Raster DEM to Mesh](RASTER_DEM_TO_MESH.md).
No Blender, R, GIS editor or AI depth model is required. Image brightness supplies height; this is an artistic
heightfield interpretation, rather than reconstruction of the photographed object's real depth.

## Start

1. Add PNG, JPEG, TIFF, BMP or WebP. **Original** shows the source with EXIF orientation applied.
2. Choose a height source and adjust preparation if needed. Neutral controls preserve grayscale input.
3. **Prepared heightmap** shows absolute prepared tones; checkerboard areas are excluded. Its status gives
   the black/white height reference. **Placement** uses these same tones and mask.
4. Choose faithful, smooth, faceted or contour-terraced relief. Fit the entire image to paper or custom mm dimensions.
5. Preview **Relief mesh**, then build a printable solid or select **Relief surface · drawing** for SVG workflows.

The source file is never modified. The whole image is processed; crop externally when needed. Animated or
multi-page files use the first image. Geographic metadata, pixel size, elevation units and scale/offset are
ignored in this tool. Decoding and preparation preserve native numeric precision, while bounded working grids
and display proxies keep previews manageable. Memory and triangle-budget failures do not silently lower export quality.

## Preparation

The shared heightfield robustness fixes in 1.11.1 also apply to image reliefs. Native alpha silhouettes and
holes are the authoritative placement domain at every preview resolution. Local link-safe precision repair
preserves surface fans; printable cleanup returns the mesh actually validated, including a saved-copy check.
Black/white calibration and tonal preparation are unchanged.

**Point contacts** offers explicit **Automatic safe clearance** or the saved manual value. Automatic uses
the physical source-pixel size and trims only touching corners. Placement displays the current limit.
Known conflicts block Build/Preview before expensive work; locked physical image scale is never silently
reduced. Strict zero clearance remains available; its drafts use temporary separation for inspection.

Built-in artistic presets preserve current preparation, orientation, aspect, alpha/background and contact
settings unless the selected preset explicitly changes them. User presets remain complete snapshots.
The reset button still deliberately resets all settings. Adaptive controls and grid pitch appear only when
used, and the calibrated White relief height remains visible for images.

Final budgets count masked terrain and printable walls/deck/bottom. Preview quality is independent of
export quality. The same fixes work through CLI and Workflow/Aim in tool; examples include
`--contact-policy automatic` and existing `--contact-trim` manual values.
The [1.11.1 validation report](HEIGHTFIELD_1_11_1_VALIDATION.md) includes shared image and DEM export checks.

Grayscale inputs bypass color conversion. Color sources offer weighted grayscale (scikit-image's
`0.2125 R + 0.7154 G + 0.0721 B`), RGB average, lightness `(minimum RGB + maximum RGB)/2`, or one RGB channel.
Weighted grayscale is the default. Palette and ordinary color formats are decoded to RGB; numeric PNG/TIFF
channels retain their source precision, including 16-bit unsigned grayscale and floating-point TIFF.

The deterministic order is:

1. EXIF orientation, clockwise quarter turns, mirroring, then alpha masking/compositing.
2. Grayscale/channel selection and input-range mapping; optional Auto levels.
3. Background-tone exclusion, then optional mask-aware noise smoothing in original image pixels.
4. Black/white levels, contrast around mid-gray, additive brightness, gamma, then invert.

Unsigned images use their bit-depth range, such as 0–65535 for 16-bit. Float input defaults to 0–1. For other
numeric ranges, enable **Auto levels** or choose **Custom source range** in advanced settings. Auto levels
stretches the valid source minimum/maximum before tonal changes. Constant images retain their native level
when there is no range to stretch. Set a custom range for out-of-range constant numeric sources.

Brightness 0, contrast 1, gamma 1, levels 0–1, noise smoothing 0 and invert off are neutral. Gamma is
`intensity ** gamma`: above 1 lowers mid-tones, below 1 raises them. Levels clip outside their range.
White must exceed black in both source-range and level settings.

Fully transparent pixels are masked by default. Partially transparent pixels retain their unassociated image
tones unless the alpha threshold excludes them. Alternatively, composite onto white or black for a rectangular
source. Background exclusion compares normalized grayscale before smoothing and tonal adjustments.
Mask holes and islands are retained; missing areas may be exposed, filled across small internal gaps, filled
to the image minimum, or extended to a rectangular footprint. Point-contact clearance defaults to 0.002 mm.

## Physical heights and placement

```
image relief = prepared intensity × white relief height × Z-factor
surface Z = image relief
solid Z = plate thickness + basal lift + image relief
```

No later stage stretches the prepared range. An image containing only gray 0.4 reaches 8 mm with a 20 mm
white reference. Smoothing may reduce peaks; it does not restore them to the white reference. Terraces round
down to the chosen step. Plate thickness by percentage uses maximum prepared relief after Z-factor.
Drawing surfaces omit base geometry and ignore plate thickness/basal lift.

The common placement editor keeps the plate aligned with the page. Drag the **Image top** rotation handle,
enter **Image rotation**, use **Reset rotation**, or rotate image pixels by quarter turns. Mirroring is in
Image orientation. Best fit, physical plate W/H, aspect locking, image scale locking, Fit image and Reset plate
work as in the terrain editor. Resizing a plate locks image scale. Locked conflicts require Fit image or a
larger page. **Expand…** opens the maximizable editor; Cancel restores every setting.

Pixel proportions are preserved by default. Disable Keep pixel aspect to specify an aspect width and height.
The paper size limits the whole padded model; those aspect dimensions are proportions, not a second page size.
Mesh coordinates are centred XY with +Z up, in millimetres. Camera orbit changes viewing only; placement is exported.

## Presets and pen drawings

Seven presets cover Faithful heightmap, Soft photo relief, Sculpted facets, Terraced relief, Plotter soft contours,
Plotter bold contours and Plotter triangle network. The three plotter presets produce open A3 landscape surfaces.
Preparation is neutral in all presets; use smoothing and tone controls to reduce noisy photographic detail.

- [Image Relief Contours](workflows/image_contours.atlasflow): smooth relief → Contour Lines → SVG.
- [Image Faceted Network](workflows/image_facets.atlasflow): faceted relief → Plotter Vision mesh edges → SVG.

Contours suit flowing tonal structure; faceted networks suit broad planes and graphic structure. The contour
workflow uses an upright top view with room for the full image. The network workflow uses a shallow view and
1 px noise smoothing for cleaner photographic edges; the presets themselves retain neutral preparation. Paper and
camera settings in the drawing step are independent of the mesh's physical fit. The drawing is a projection,
so final SVG dimensions do not automatically reproduce the mesh at 1:1 scale.

## CLI and compatibility

```bat
atlas-toolkit describe image-to-mesh
atlas-toolkit run image-to-mesh portrait.jpg --preset "Soft photo relief" --gamma 1.2 --contrast 1.1
atlas-toolkit run image-to-mesh height16.png --relief-height 25 --quarter-turns 1 --mirror-x
atlas-toolkit run image-to-mesh height_float.tif --auto-levels --preset "Plotter soft contours"
atlas-toolkit run image-to-mesh graphic.png --grayscale red --alpha-threshold 0.1 --format .obj
```

All preparation, placement, sampling and export settings are ordinary schema parameters, shared by UI, CLI,
saved presets and Workflows. Cache keys include every preparation setting and the input file signature.
Outputs are `<stem>_image_solid.stl` or `<stem>_image_surface.stl`, with OBJ and PLY alternatives, the
`atlas-toolkit:image-to-mesh` signature and an embedded Z-up frame. Exports are reloaded and validated before
atomic publication; failure or cancellation preserves an existing destination.

Since 1.11.0, Raster DEM to Mesh requires a CRS. Replace old heightmap workflow steps manually with Image to Mesh.
Use Auto levels to reproduce the old source-range stretching, and transfer physical/surface settings. Old
`source_mode=heightmap` steps report this migration requirement; they are never silently converted.

Run `python scripts/dev/validate_image_to_mesh.py` for repeatable photographic, ramp, graphic and transparent
examples, drawing SVGs, preparation previews and a validation report under `build/qa/image_mesh_validation`.
