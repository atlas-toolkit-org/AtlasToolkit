# Surface Texture 0.2.0

Surface Texture adds actual relief to a model. Send its STL, OBJ or PLY to Contour Lines, Sketch Hatching or
Plotter Vision to draw that relief. The tool keeps the original model's units, position and silhouette apart
from the requested displacement. The release works offline and includes its native helper; Blender is not required.

## Start with a drawing

1. Add a model and choose **Graphic Scales**, **Carved Bark**, **Botanical Veins** or another material preset.
2. Set **Element size** to the approximate width of a scale, cell or groove, in percent of model height.
   Start around 6–10%. The catalogue calibrates the approximate number of motifs in each source image.
3. Set **Height / depth**. Raised relief occupies 0..height; Engraved occupies -height..0;
   Balanced occupies -height/2..height/2. A heightmap need not reach both endpoints. About 1% h (the default)
   reads clearly at A3; 0.4% h is a whisper, 2% h and more is bold and is restrained a little more in creases.
4. Preview the final mesh, then run a drawing workflow. The reference is A3 with a 0.3 mm pen.

Example workflows: [texture contours](workflows/texture_contours.atlasflow),
[botanical sketch](workflows/texture_sketch.atlasflow) and [band features](workflows/texture_band.atlasflow).
The last uses Plotter Vision's feature edges with decimation disabled. Smooth relief often reads better as
contour undulation or curvature lines than through a high feature-angle cutoff. Lowering that cutoff too far
can expose the mesh triangulation. Inspect the SVG at actual print scale before plotting.

## Wrapping and placement

**Follow surface** uses xatlas charts with approximately uniform physical texture density. It does not require
UVs in the input. Geometry is welded independently of the texture coordinates. Atlas asks xatlas for few, large
charts, and a model of more than 25,000 faces is charted through a simplified copy whose charts are transferred back
to every face: seconds instead of a minute on a 260,000-face scan, and charts that do not follow scan noise. At a
chart join the two charts' patterns **cross-fade** over **Seam blend**, so the relief carries on across the seam
instead of dipping to the bare skin; motifs can still change direction there. Seam blend 0 leaves a hard join
(the geometry stays welded). This is automatic charting, not a globally seamless pattern solver.

**Cylindrical** wraps about Up, Front or Side. The default seam is behind an upright model; seam rotation moves
it. The circumference fits a whole approximate number of motifs; a tile can be cropped so large elements remain
possible. The ends blend over Seam blend (at least 10% of an element), keeping the angular seam continuous.
Actual element width can differ slightly from the request on small circumferences. Texture rotation becomes a helical twist
so the seam remains periodic. Where the skin faces along the axis (the crown of a head, under the chin, the
ends of a column) angles crowd the motifs together, so there the texture turns into a projection along the axis,
blended by the surface's direction. Relief still fades within half an element of the axis itself.

**Triplanar** samples three perpendicular projections, blended by the smoothed vertex normals. It is seamless,
the quickest, and useful for irregular rock and soil; it can blur structured motifs where the projections meet.

U/V offsets are in tile repeats. Aspect ratio stretches the V direction; custom image proportions are retained.
Model Up/Front/Extra turn use the existing Atlas frame controls, independently of the viewer's orbit camera.

**Acts on** reuses Atlas's whole-model, height-band and sphere controls. Band start/end are percentages from the
base along Up. Feather extends outside the selected region, and Invert textures its complement. The default
Textured Band preset selects 30–65% of height with a 5% feather. Unaffected source vertices do not move; adjacent
triangles may be subdivided to keep the transition conforming. Preserved open borders stay fixed, fading into
the relief over a quarter of an element.

## Materials and custom heightmaps

The twelve materials are reptile scales, fractured rock, sand ripples, soil, leaf veins, fallen leaves,
brickwork, bark, leather grain, cracked earth, cobblestones and wood grain. The swatch links to the original
source. Each source has one artistic preset; Engraved Cracks and Textured Band supply two additional recipes.

Bundled artistic maps have explicitly recorded contrast levels, filtering and edge preparation. They expand
the useful relief in sources whose original physical heights occupy a narrow gray range. Available source
precision is retained in 16-bit prepared PNGs; the brick source itself is 8-bit. Their CC0 provenance, original
map names, archive/map checksums and preparation settings live in the bundled `assets/catalog.json`.

For **Custom heightmap**, choose a grayscale PNG/TIFF. Eight- and sixteen-bit integers are calibrated to their
full range; float TIFF must contain finite values in 0..1. Custom maps are never automatically stretched.
Constant images remain constant. Transparent areas blend relief to zero. RGB files are accepted only when
all three channels are identical. Custom files represent one element across the image width; use Element size
to set the full repeat width. Use a tileable image for clean repetition. Limits: 16 million pixels / 256 MiB.

**Suppress fine grain** filters in relation to motif size, **Relief contrast** adjusts around mid-gray, and
**Crisp profile** changes the height transfer from linear to a smooth cubic transition with broader plateaus.
Invert Heightmap swaps black/white before the selected relief direction is applied.

## Quality, protection and output

Standard starts with six samples across an approximate element; High starts with ten. Midpoint and triangle-centre
height-error samples then refine wherever the surface misses the heightmap by more than **Detail tolerance**, a
share of the relief height (10% by default). Because it is a share, taller relief needs no more faces than shallow
relief. Refinement never goes below 1/24 of an element (High: 1/40), finer than a 0.3 mm pen resolves at A3, so a
crisp heightmap edge cannot make it chase detail forever. This is sampled error control, not a certified continuous
maximum. Both modes construct final geometry even for Preview. A display proxy can lighten very large previews; it
never replaces the saved mesh.

**Maximum output faces** (1,000,000 by default, up to 3,000,000) is a budget, not a requirement. Refinement spends it
on the places with the largest error first; when the next pass would not fit, it refines the worst faces that do and
stops, with a warning that says how much of the surface met the tolerance. It never fails for want of faces. A
source already at the budget is textured on its own vertices. Presets do not set the budget: it belongs to your
computer. Larger outputs take longer in the drawing tools, so raise Element size or Detail tolerance before the
budget when a drawing is slow. Automatic charting accepts at most two million source faces/vertices. Cancellation
stops the worker and its native helper.

Relief is displaced along normals smoothed over about a quarter of an element, so it does not follow scan noise.
Triangles are always kept valid: where relief would flip or collapse one (deep relief in a tight crease), it is
restrained there, fading into the surroundings over a quarter of an element.

**Collision protection** also keeps the relief from pushing one piece of skin through another. It limits the motion
along each normal to under half the free distance in front of it, then finds every pair of triangles that would cross
and restrains the relief locally around them, repeating until nothing crosses; the rest of the model keeps its full
height. It reports the achieved maximum relief and the share of the total displacement restrained (typically a few
percent, in creases and near contacts; a warning appears above 5%). A source that already crosses itself in a few
places (a scan's stray fold) is textured around them, with a warning; a badly broken one is refused, and Repair Mesh
fixes it. It is a geometric guard on the final triangles, not a continuous collision proof for arbitrary nonlinear
motion. With protection disabled, triangles are still kept valid but crossings are not checked.
Open inputs remain open; valid closed inputs remain closed. Zero relief bypasses texture preparation and exports
unchanged input geometry. New relief exports are triangulated in all formats.

Files are staged, signed `atlas-toolkit:mesh-texture` and reloaded before replacing the destination. Float32
STL/PLY can lose detail at very large coordinates; saved-copy topology checks reject damaged exports.
Use a model near the origin or OBJ where float precision is a concern.

## CLI and Workflows

```powershell
atlas-toolkit describe mesh-texture
atlas-toolkit run mesh-texture model.stl --preset "Graphic Scales" --format .ply
atlas-toolkit run mesh-texture model.stl --preset "Textured Band" --element-size 8 --relief 0.5
atlas-toolkit run mesh-texture model.stl --source custom --heightmap height.png --mapping triplanar
```

Every control comes from the shared tool schema. Workflow steps receive the mesh through their main port;
the optional Heightmap side port accepts an image when Source is set to Custom heightmap. Workflow caches include the selected map, catalogue and
helper dependencies. Charts and prepared heights have separate content-addressed caches, so changing relief or
region reuses preparation. Built-in presets preserve custom-source and region settings unless explicitly overridden;
saved user presets retain full snapshots.

Sources and method comparison: [research notes](SURFACE_TEXTURE_RESEARCH.md).
