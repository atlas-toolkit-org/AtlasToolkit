# Surface Stripes

Surface Stripes (`mesh-stripes`, 0.2.0) adds physical, flat-topped bands to a model or exports closed ribbons on their
own. It computes a camera-independent
stripe pattern using Geometry Central's implementation of Knöppel et al. (2015), then fuses bands and body into
a closed solid. Texture coordinates alone are insufficient for the existing Atlas SVG renderers, which draw
geometric features. This tool therefore exports actual geometry.

## Use

```text
atlas-toolkit run mesh-stripes model.obj --spacing 4 --band-width 35 --relief 0.25 -o model_stripes.stl
atlas-toolkit run plotter-vision model_stripes.stl --preset Features -o model_stripes.svg
```

In the app or a workflow: **Mesh input → Surface Stripes → Plotter Vision → Overlap Cleaner → Save**.
Plotter Vision's Features preset draws the band rims without the surface's triangle grid; keep decimation off
to retain shallow rims. Hatch Maker's outlines/creases provide a second rendering route. Paper size, camera,
hidden-line removal and pen widths belong to the downstream renderer and work in millimetres as usual.

- **Direction field:** curvature aligned by default, or smoothest. Undefined curvature falls back to smoothest.
- **Spacing:** target distance across the surface, % of the model's height. Default 4%.
- **Width:** % of spacing, default 35%; **Raised height:** % of height, default 0.25%.
- **Export stripes only:** off by default. When enabled, the main output contains only closed ribbons, with
  no body between them. Their bottoms lie on the original skin and Raised height sets their thickness. A positive
  height is required. It writes one model using the normal output filename and format; the usual overwrite rules
  apply. The preview, CLI and workflow output all show that same ribbon-only model.
- **Rotation:** turns the visible band direction; **Orthogonal pair:** adds the perpendicular stripe family.
- **Seed:** repeatable solver initialization. Changing band width/relief reuses the field and curve cache.
- **Model:** shared Atlas up/front/rotation frame. Outputs retain the input coordinate frame and source units.
- **Repair open mesh:** off by default. Explicitly closes invalid/open input by resampling; this can soften detail.
- **Field face budget:** working mesh resolution. The detailed source remains the body and projection surface.
  Body previews use the lower Preview field faces setting. Ribbon-only previews use the export resolution and
  share its cache to preserve closed topology and a stable pattern. If long field edges remain, the log reports
  the approximation.
- **Band face budget:** aborts construction instead of silently leaving stripes out. Very broad and shallow bands
  require many surface samples; increase relief, increase spacing, narrow bands, or raise the budget if necessary.

Inputs use Atlas's normal STL/OBJ/PLY/GLB/GLTF/OFF loader and scene transforms. The solid tool requires closed,
consistently oriented, orientable surfaces, or explicit repair. Disconnected surface components are solved
independently and combined. Outputs are signed `{stem}_stripes.stl` (OBJ and PLY also available).

## Plotter presets and ribbon-only drawings

Both new presets enable **Export stripes only**, use one stripe family, retain your model orientation and raise
the quality budgets. The default Flowing bands preset still includes the body.

| Preset | Field | Spacing (% height) | Width (% spacing) | Thickness (% height) | Treatment |
|---|---|---:|---:|---:|---|
| Plotter broad | Smoothest | 4 | 85 | 0.2 | Broad flowing ribbons, narrow gaps, maximum coverage |
| Plotter dense | Curvature aligned | 2.5 | 65 | 0.15 | More, finer ribbons following the form |

Skin coverage is measured from the source patches and reported as a percentage. Width is a target; interpolation
and branching affect actual coverage. Tests on the v17 head measured about 89% for broad and 68% for dense.
The ribbons leave the interior empty; rear stripes can appear through the gaps. Inner cavity surfaces also
produce independent closed ribbons. Output ribbon volume replaces the Added volume statistic in this mode.

For clean pen lines, use Plotter Vision Features with decimation off, a single 0.2 mm pen, 0.05 mm simplification
and a 1 mm minimum stroke. Disabling the separate outline layer lets the short-stroke filter apply to silhouette
fragments as well as interior edges. Narrow pens separate the paired top/bottom rims of a solid ribbon.

```text
atlas-toolkit run mesh-stripes model.stl --preset "Plotter broad" -o model_broad.stl
atlas-toolkit run mesh-stripes model.stl --preset "Plotter dense" -o model_dense.stl
atlas-toolkit run mesh-stripes model.stl --stripes-only --relief 0.2 -o model_ribbons.stl
atlas-toolkit run plotter-vision model_broad.stl --preset Features --no-decimate --no-outline-layer --line-width 0.2 --simplify 0.05 --min-stroke 1 -o model_broad.svg
```

Hatch Maker is another route: No hatching, outlines and creases, Everything on one pen, about 0.2 mm width.
Paper, camera and pen settings remain controls of the downstream drawing tool. Dense presets do more work;
ribbon-only Preview uses the same field budget as Run, so the pattern and cache remain stable between them.

## Integration and portability

The normal Atlas Tool contract supplies discovery, forms, CLI, presets, history, process execution, previews and
workflow ports. The tool has its own icon and an external-runtime declaration for locating a missing native
helper in source installations. The release bundles that helper and its notices; a compiler is unnecessary
when using the app. Build instructions are in `native/README.md` and `native/stripes/README.md`.

`ATLAS_TOOLKIT_STRIPES` overrides the helper path; About → Setup can also locate it. `ATLAS_STRIPES_CACHE`
overrides the curve cache directory; otherwise Atlas's normal portable/user cache is used. Cached results are
keyed by geometry, field preparation, spacing, rotation, families, seed and the executable's SHA-256. Corrupt
cache files are recomputed. Switching Export stripes only reuses the same cached field; the bundled native helper
and protocol are unchanged. Files are published atomically, and cancellation kills the native helper. Windows
job objects also tie the helper's lifetime to the Atlas worker.

Geometry Central supplies periodic corner values, singularity indices and 3D polyline extraction. The adapter
converts spacing to cycles per model unit and adjusts GC's perpendicular field convention. For robust solid
geometry, it samples the periodic phase onto a refined copy of the detailed source surface, cuts each face into
band and gap patches, raises band tops along surface normals and inserts shoulder walls. Shared integer edge
IDs keep adjoining patches stitched into one fused body. This replaces the initially proposed overlapping
ribbon Boolean construction, whose tiny contacts were fragile in STL/PLY. Manifold validates and simplifies the
solid; bounded precision cleanup removes export-scale slivers, and saved meshes are reloaded and checked for
closed topology before publication. The extracted curve network, with heuristic singularity reconnection,
remains cached for diagnostics; phase transfer approximates the bands locally near singular faces.
Ribbon-only construction omits gap patches, reverses the band patches to close their undersides and uses the
existing shoulder walls as caps. Smooth normals are interpolated from the original surface for thickness; export
cleanup preserves separate nearby ribbon shells instead of welding them into pinched contacts.

## Limitations

Geometry Central omits the paper's special interior interpolation; refinement and singularity reconnection are
approximations. Target spacing may vary near branch points and where curvature demands it. A mesh field's global
energy optimum does not imply exact constant spacing everywhere. Surface sampling and linear interpolation of
the cosine phase slightly approximate band width, particularly at branch points or a limited face budget.
Binary STL and PLY have float32 precision, so extremely distant origins relative to band dimensions may require
recentering. Numerical repeatability is scoped to a backend build and identical input ordering.

The tool has automatic fields, raised bands and standalone closed ribbons. It does not implement imported
fields, manual trivial-connection editing, grooves, unthickened open surfaces or a direct stripe SVG renderer.

The original 2015 code remains untouched. Geometry Central's commit and Eigen version are pinned; the app's
`licenses/surface-stripes.txt` retains third-party notices. See About → Credits for the paper and implementation.
