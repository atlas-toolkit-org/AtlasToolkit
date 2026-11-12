# Raster DEM to Mesh

Atlas Toolkit 1.11.1 · tool 0.3.1 · **Image → Mesh**

Build terrain from a supplied geographic numeric DEM. Non-geographic photos and grayscale heightmaps use
[Image to Mesh](IMAGE_TO_MESH.md). A CRS is required in the source or override. No R, QGIS or Blender
installation is required. The same controls work on the tool page, command line and workflow steps.

## Start with a DEM

1. Add a `.tif`, `.tiff` or `.png`. Inspection shows the dimensions, CRS, valid elevation range and elevation image.
2. Choose an elevation band. A CRS override assigns a missing or incorrect CRS. Legacy Auto/GIS interpretation
   settings both use GIS coordinates; Heightmap mode reports migration to Image to Mesh.
3. Keep the valid-data footprint, or choose a rectangular footprint. Native masks and NoData values are honoured.
   Background exclusion compares raw band values before the band's scale and offset are applied.
4. Select a surface method, physical model size, padding and height controls. Preview the draft and adjust the
   terrain turn in **Placement**. Run builds at the requested final sampling pitch.
5. Save a printable STL, OBJ or PLY, or select **Terrain surface · drawing** for an open skin without plate,
   pedestal or exterior walls. Terraced drawing surfaces retain their treads and internal risers.

The supplied extent is processed in full. Cropping, polygon boundaries, XYZ text and online DEM acquisition
are deferred. Sources are read in bounded tiles, with a quick display up to 1,024 pixels on the longest side.
Authoritative elevation statistics and native mask outlines are independent of preview resolution. There is no
fixed 25-million-cell input ceiling: source and estimated construction memory must fit available RAM. Adaptive
TIN retains native samples for error measurement, so very large native arrays can still exceed memory limits.
The export budget defaults to two million triangles, with an advanced maximum of eight million. Quality and
budget errors are actionable; final detail is never silently reduced.

## Surface methods

| Method | Appearance | Controls |
|---|---|---|
| Faithful terrain | Continuous relief with no smoothing | Regular grid pitch or Adaptive TIN error and maximum XY edge |
| Smooth terrain | Softened continuous terrain, with mask-aware Gaussian filtering and bounded cubic sampling | Grid/TIN detail; smoothing radius in final mm |
| Faceted terrain | Broad triangular planes | Approximate terrain triangle target; 3D surface-error tolerance in mm |
| Contour terraces | Horizontal treads and vertical risers | Pitch; terrace step in final mm |

Faceting uses the bundled PMP helper with protected boundaries. Normal and projection constraints may retain
more triangles than requested; the report explains this. It retries conservatively if a collapse would fold the
terrain in XY. Terrace elevations are rounded down to multiples of the step; the highest plateau can therefore
be lower than the continuous peak. A run is limited to 500 terraces.

Regular-grid pitch defaults to **0.25 mm**. Previously saved explicit pitches remain unchanged. Pitch controls
surface sampling; native mask clipping preserves islands and holes even when grid centres miss them. Finer
sampling cannot recover detail absent from the source. At A4 fit, a 20 m cell in the supplied AMinho DEM is about
0.054 mm; a 0.5 mm pitch skips roughly nine source cells along each axis.

**Adaptive TIN** uses pydelatin, then conforms long edges, clips to the valid outline and welds shared boundaries.
Defaults are **0.02 mm vertical tolerance**, **1 mm maximum horizontal edge** and **0.025 mm boundary tolerance**.
Boundary tolerance zero retains the raster outline. Simplification is retained only when valid source centres
remain covered. Reports include maximum and RMS vertical error against every valid source sample after physical
scaling and exaggeration. Nearest-valid extension is internal only; no geometry is exported outside the footprint.
For Smooth, the reference is the explicitly smoothed source field. Unsupported precision or tolerance fails export.

Fast / Balanced / Detailed previews target approximately **25k / 100k / 250k** terrain triangles. Balanced is the
default. Drafts use a bounded regular grid independently of export sampling and are visibly labelled. Large final
meshes may also use a labelled display proxy; the saved mesh retains final detail. Timed phases separate reading,
footprint, sampling, triangulation, solid construction and validation. Rounded corners affect only the plate outline.

## Physical dimensions and heights

Paper sizes are maximum XY dimensions for the **whole plate and terrain**, minus the selected margins. Custom
width and height follow the same orientation control as standard paper. Snug fit produces the smallest padded
rectangle allowed by the fit; Full available rectangle fills the complete available rectangle. Terrain is centred
and proportions are preserved. Padding is a minimum clearance to the actual rounded plate outline.

All exported coordinates are **millimetres**, centred in XY with +Z up. The bottom of a printable plate is Z=0.

```
terrain Z = plate thickness + basal lift + terrain relief
geographic relief = (elevation − minimum processed elevation) × XY mm/metre scale × z-factor
target relief = normalized elevation × target relief height × z-factor
```

Geographic mode uses metric XY and elevation units consistently. Angular rasters are reprojected to a local
azimuthal equidistant projection; projected feet and rotated grids are handled explicitly. The north handle
indicates grid north, rather than correcting for meridian convergence.

Elevation metadata units default to metres when absent, with international-foot and US-survey-foot
overrides available. No vertical-datum conversion or geoid correction is performed. Image-only pixel aspect,
inversion and tonal preparation live in Image to Mesh. To move an old non-geographic workflow, replace its
DEM step with Image to Mesh and enable Auto levels to reproduce the old range stretch.

Absolute plate thickness and basal lift are independent of z-factor. Percentage plate thickness is computed from
maximum relief **after** scaling and exaggeration, before basal lift, with a separate minimum thickness. A flat
raster has zero relief and still creates its plate and optional pedestal.

## Masks and topology

Select **Automatic safe clearance** to separate diagonal contacts using the physical source-cell size.
The change is confined to touching corners; it does not fill holes or merge islands. **Manual clearance**
retains saved values, including zero for strict final rejection. Placement shows the actual source-cell
limit and suggested value. At the supplied AMinho A4 fit, the limit is about **0.0136 mm**, so 0.02 mm
is too large; 0.002 mm is a valid conservative manual setting. Mesh pitch is separate from source-cell size.

Open surfaces use local link-safe precision repair; they are no longer extracted from a simplified solid.
Every topology-changing stage and saved output remains validated. Genuine unresolved defects refuse export.

Exposed missing regions become visible areas of the continuous plate, including holes inside terrain. Fill small
internal gaps uses the requested maximum inscribed gap diameter; Fill to terrain minimum creates a rectangular
raised block with missing areas at the lowest elevation. Rectangular footprint extrapolates boundary elevations
into missing cells.

Two diagonal valid cells can touch at one corner. Zero **Point-contact clearance** refuses this non-manifold
configuration with an explanation. A positive clearance trims the touching corners by that distance in mm;
0.02 mm works for the supplied Viana example. Clearance must be smaller than one quarter of a source-cell edge
(or a draft sampling-cell edge for the approximate preview).
The trim is local; islands remain joined through the continuous base plate.

Printable output is checked for manifold edges and vertex fans, consistent winding, positive volume and one
connected closed solid. Exported files are reloaded and checked before replacing an existing destination.
Cancellation or a failed check leaves the existing output intact. Drawing surfaces are intentionally open.

## Placement and presets

Exact source outlines are shared between Placement, bounded drafts and final runs. The first quick image
can appear while preparation continues; Fit, Lock and plate resizing wait for the authoritative outline.
Known placement or contact-policy conflicts block explicit Build before a worker is submitted. Strict
zero-clearance masks can still be inspected in a labelled draft with temporary contact separation.

Built-in artistic presets preserve current input, band/unit, background, mask and contact-policy settings
unless they explicitly override a setting. Detailed/Fine faithful still explicitly select 0.002 mm clearance.
Saved user presets remain complete snapshots. CLI presets resolve against complete defaults plus flags.

In **Placement**, drag the north handle or enter **Terrain rotation** to turn the elevations clockwise relative
to the page-aligned plate. The shaded elevation image, footprint and north handle turn together; the same angle
is applied to the exported mesh. The inspector's **Terrain turn** edits this same setting. Reset north returns
to zero; Best fit searches whole-degree orientations for the largest fitting terrain.

The editor keeps a fixed page scale while turning. **Snug padded fit** recalculates the plate dimensions to
surround the turned footprint. **Full available rectangle** uses the paper area minus margins. **Custom plate**
has separate physical width and height, bounded by that available area.

Drag an edge or corner square to resize the centred plate symmetrically. Numeric Plate W/H controls do the same;
Plate aspect keeps their ratio. Starting a resize switches to Custom plate and **locks terrain scale** at its
current physical size. Later plate resizing changes only the plate. With Lock terrain scale enabled, rotation
also preserves XY scale and geographic relief; plate thickness and basal lift keep their explicit settings.
The ordinary **Locked raster width** parameter stores the unrotated full raster extent in mm, including masked
areas, so CLI and Workflows reproduce the scale exactly. Percentage plate thickness remains tied to relief.

**Fit terrain** explicitly recalculates the largest fitting terrain scale for the current plate, then locks it.
**Reset plate** returns to automatic snug fitting. Padding and rounded-corner containment apply in every mode.
A locked rotation that exceeds the paper area shows a conflict and suspends drafts; choose Fit terrain or larger
paper. It never silently shrinks locked terrain. Terrain remains centred; translation is not included.
**Expand…** opens a maximizable editor. Use settings accepts its parameters; Cancel restores the prior values.
Automatic drafts update the 3D mesh without switching away from Placement, and wait until a handle drag ends.
Use **Preview** or the **Terrain mesh** tab to inspect the result in 3D. Dragging there orbits the camera.
In Workflows, **Aim in tool…** opens the same editor and returns accepted settings to the selected step.

The preset packs hold ten presets for this tool, among them **Detailed faithful** and **Fine faithful** (TIN, finer
error and pitch) and **Large-region draft** (Fast preview, a coarse export grid). Both adaptive presets apply no
smoothing and a tight point-contact clearance. The two plotter presets export open A3 surfaces.

## Viana example and command line

`dem_vc.tif` is already processed: it includes a flat value-10 background and raised terrain. Exclude raw value
10 and use 0.02 mm point-contact clearance. Atlas does not guess or reverse earlier height transformations.

```bat
atlas-toolkit run raster-dem-to-mesh dem_vc.tif --exclude-background --background 10 --contact-trim 0.02 --preset "Faithful print"
atlas-toolkit run raster-dem-to-mesh dem_vc.tif --exclude-background --background 10 --contact-trim 0.02 --method terraces --terrace-step 1 --z-factor 2
atlas-toolkit run raster-dem-to-mesh DEM20m_AMinho.tif --preset "Detailed faithful"
atlas-toolkit run raster-dem-to-mesh DEM20m_AMinho.tif --plate-fit custom --plate-width 190 --plate-height 240 --lock-scale --terrain-width 130 --yaw 25 --contact-trim 0.002
```

Run `atlas-toolkit describe raster-dem-to-mesh` for all flags. Output names are `<stem>_dem_solid.stl` or
`<stem>_dem_surface.stl`; `--format .obj` and `--format .ply` select alternatives. Files carry the Atlas signature
and Z-up frame. STL applications generally assume mm; confirm this when importing into a slicer.

## SVG workflows and validation

Grid budgeting counts masked geometry rather than rejecting the whole raster rectangle. Clipping checks
the budget before accumulating boundary pieces, and printable construction checks walls, deck and bottom.
Fast/Balanced/Detailed affect drafts only. Raise export budget or grid pitch for a dense regular grid;
adaptive errors name vertical tolerance and maximum XY edge. Inactive controls are hidden: Target relief
height is used only in Target height mode, and grid pitch is unused by faithful/smooth Adaptive TIN.

`python scripts/dev/validate_heightfield_fixes.py` reproduces the logged A3/A4 cases and validates shared image
reliefs, writing signed exports and JSON to `build/qa/heightfield111_validation`. Logs include mode,
parameters, resolved units, phase, triangle counts and elapsed time; failed runs retain actual duration.
See [1.11.1 reproduction and validation results](HEIGHTFIELD_1_11_1_VALIDATION.md).

- [DEM Terrain Contours](workflows/dem_contours.atlasflow): image → terrain surface → Contour Lines → SVG.
- [DEM Faceted Network](workflows/dem_facets.atlasflow): image → terrain surface → Plotter Vision Mesh edges → SVG.

Use the same paper/custom dimensions in the mesh and drawing steps when desired. The drawing step has its own
camera and page fit, so projected SVG dimensions are not automatically a 1:1 topographic plan.

`python scripts/dev/validate_raster_dem.py` builds the supplied Viana examples and two SVGs in
`build/qa/raster_dem_validation`. It does not modify the supplied source or preliminary STL.

`python scripts/dev/validate_raster_dem_detail.py` compares Standard, Detailed faithful and Fine faithful on both
supplied DEMs, checks source hashes, writes signed printable STLs, phase/error/dimension JSON and full-resolution
overview/close-up renders in `build/qa/dem106_validation`. Use `--mode ui`, `meshes` or `renders` separately.
The high-resolution source has six diagonal point contacts; these examples explicitly select 0.002 mm clearance.
Drafts temporarily use at least this clearance for display; final output always honours the explicit setting.
See [1.10.6 measurements and release checks](RASTER_DEM_1_10_6_VALIDATION.md) for the recorded comparison.

Rasterio, GDAL, PROJ and pydelatin/hmm data/DLLs and licence notices are bundled. Raster masks and georeferencing sidecars used
by the reader participate in workflow cache keys. Input source files and their sidecars remain unchanged.
