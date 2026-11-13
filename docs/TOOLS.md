# Atlas Toolkit: tools and user guide

The studio's pen-plotter tools in one desktop app, in the Atlas look: clean SVGs for the plotter, prepare 3D models,
and turn models and photos into line drawings. Every tool works the same way: same queue of files, live preview, presets,
output rules and run history, and every output is signed so the Atlas gallery links it to the tool that made it.

It runs on Windows, Linux and macOS: see [Install](../README.md#install). Drop files on the window, or open them with the app.

The app ships with no presets: every tool starts from its own tuned defaults. The presets this guide names (counts
such as "fifteen presets", looks such as **Torsion sequence**, and the `--preset` examples) are those of the Atlas
preset packs, loaded separately from the app; see [Presets](PRESETS.md).

## Tools

**Motion Sculpture 0.3.0** turns a still model into swept laminae or splintered bars, inspired by layered glass
and wind-carved bronze sculptures. Stream, Pulse, Gust and Vortex fields stretch tapered wakes behind a retained
leading silhouette. Seven presets, seeded irregularity, layer tilt, base hold, feathered regions and bounded
conforming refinement. Final-geometry previews and signed STL/OBJ/PLY, original units, UI/CLI/Workflows;
no Blender or new dependencies. **Region → Painted area** reuses Surface Eject's visible-face painter in a
Selection tab. Sliced layers builds real plates/bars with strong painted wakes and gaps;
Connected surface preserves unpainted skin with local boundary protection.
See [controls and method](MOTION_SCULPTURE.md),
[motion contours](workflows/motion_contours.atlasflow) and
[motion sketch](workflows/motion_sketch.atlasflow).

**Line Smooth 0.1.0** removes sawtooth and jagged linework from SVG pen drawings with Whittaker curvature
smoothing or compensated Taubin filtering. Physical span, blend, displacement, persistent corners,
stroke length, loop area and all-pen topology checks control the result. Five presets include Surface stripes;
Before/After, UI/CLI/Workflows and signed dense-polyline SVG output. This tool moves geometry and performs
no simplification or vertex reduction. See [controls, method and research](LINE_SMOOTH.md) and
[SVG smoothing workflow](workflows/line_smooth.atlasflow).

**Line Simplify 0.1.0** in **SVG · post-process** reduces line-art vertices with native Douglas–Peucker or
heap-based Visvalingam–Whyatt. Control physical deviation, protected corners, backward motion, local length
loss and loop area. Conservative swept-region checks protect crossings, junctions and enclosed motifs across
pens. Keep curves original or optionally convert them with a shared approximation budget. Four presets,
Before/After previews and signed SVG, UI/CLI/Workflows, no new dependencies. See
[controls, limitations and research](LINE_SIMPLIFY.md) and
[SVG simplification workflow](workflows/line_simplify.atlasflow).

**Stair Sculpt 0.1.0** turns a model into a linear, turning or helical staircase. Carve a cavity on a chosen side or
where you paint, and build one staircase or an ensemble of flights, landings, piers and arches inside it. Or make the
whole model the stair: as treads whose ends keep its silhouette, or as a flight, groove or square ramp cut into the
body. Fifteen presets; one closed, signed solid in the model's units; closed inputs by default, with optional repair.
No Blender or new libraries. See [Stair Sculpt modes and drawing](STAIR_SCULPT.md).

**Skeleton Shell 0.1.0** empties a model and keeps only a structure on its skin, a wire or plate sculpture you can see
through: Wire Cells, Trimesh, Flow Strands, Ribs & Stringers or Cut Plate, in up to two layers, with part of the skin kept
as a thin shell that lets go cell by cell. It reads the model as a surface, so the input need not be closed. Optionally
fused into one printable solid. Eighteen presets; seeded; no Blender or new libraries. See
[Skeleton Shell](SKELETON_SHELL.md).

**Crumble 0.1.0** fractures part of a closed model into solid blocks of rock, shards, slabs or bricks, on a chosen side,
a round patch, a painted area or the whole model, and leaves a jagged crater with cracks around it. The blocks let go in
a staggered order and fall in a built-in rigid-body solver, colliding with each other, the body and the floor. Collapse
picks the moment shown, from just cracked to settled rubble. Output is loose closed solids or one fused solid. Ten
presets; seeded; no Blender or new libraries. See [Crumble](CRUMBLE.md).

**Surface Eject 0.3.1** extracts real pieces of the model surface with cube, box, sphere, ellipsoid or cylinder
samplers and floats them along the area-weighted outward normal, preserving orientation and leaving matching
cavities. Independent seeded size/depth/gap ranges, certified full detachment, whole surface / clipped height
band / visible-face painting in a modal selection editor. Optional cylinders, square bars or organic stems
connect fragments back to their cavities, with bounded forks, curvature, taper, spread and thickness controls.
Choose 0–100% connected fragments: an exact seeded random subset, or largest/smallest first. Independent
connection seed, stable subsets and floating unselected pieces. Up to 100 extra branches, fixed or random
per-fragment min–max counts; beginning/middle/end/random/mixed origins, up to four generations and branch seed.
Nineteen pen-plotter presets; combined
assemblies with fused selected joints or separate body/fragments/connectors in signed
STL/OBJ/PLY, original units, final-geometry previews and UI/CLI/Workflows. Connectors are off by default.
Closed inputs by default; explicit optional repair. No Blender or new libraries. See
[Surface Eject controls and plotting](SURFACE_EJECT.md),
[contour workflow](workflows/surface_eject_contours.atlasflow) and
[sketch workflow](workflows/surface_eject_sketch.atlasflow),
[connected contours](workflows/surface_eject_connected_contours.atlasflow) and
[branching sketch](workflows/surface_eject_branches_sketch.atlasflow) and
[mixed floating/connected sketch](workflows/surface_eject_mixed_sketch.atlasflow) and
[organic tree sketch](workflows/surface_eject_trees_sketch.atlasflow).

**Bend 0.1.0** curves a model into circular or soft arcs, sculptural S-bends and spatial waves.
Angle, direction, bend range, anchors, neutral spine, plane sweep and section roll shape ten artistic presets.
Drag the gesture in the Spine editor; Preview uses the same refined geometry as Run. Local fold protection
reports the achieved angle. Signed STL/OBJ/PLY and shared-frame stage sets, original units, UI/CLI/Workflows;
no Blender. Draw with Contour Lines or Sketch Hatching. See [Bend controls and drawing recipes](BEND.md),
[Bend Contours](workflows/bend_contours.atlasflow) and [Bend Sketch](workflows/bend_sketch.atlasflow).

**Surface Texture 0.1.0** wraps a model in real raised, engraved or balanced relief. Twelve bundled CC0
heightmaps cover scales, rock, sand, soil, leaf veins, fallen leaves, bricks, bark, leather, cracked earth,
cobbles and wood grain; custom grayscale PNG/TIFF includes 16-bit heights. Automatic surface-following
charts, cylindrical wrapping or triplanar mapping; element size, relief, grain suppression, crispness,
whole model / feathered bands / spheres, border protection and collision limiting. Fourteen presets target
A3 / 0.3 mm pen drawings. Signed STL/OBJ/PLY, final-geometry previews, UI/CLI/Workflows, offline; no Blender.
See [controls and drawing recipes](SURFACE_TEXTURE.md), [method and research](SURFACE_TEXTURE_RESEARCH.md),
[texture contours](workflows/texture_contours.atlasflow),
[texture sketch](workflows/texture_sketch.atlasflow) and
[textured band features](workflows/texture_band.atlasflow).

**Pipe Fill 0.1.0** replaces a model with tubes throughout its interior: organic repulsive curves or
orthogonal pipes with rounded elbows, independent strands or closed loops, solid or hollow. Eight presets
include airy/dense plotter sculptures and architectural pipe mazes. Diameter, spacing, gaps, thickness
variation and seed control the result; the original body is omitted. Closed input by default, optional
explicit repair. Signed STL/OBJ/PLY, original units, final-geometry previews, UI/CLI/Workflows; no Blender.
See [controls, method and plotting](PIPE_FILL.md), [research](PIPE_FILL_RESEARCH.md),
[organic drawing](workflows/pipe_fill_organic.atlasflow) and
[architectural drawing](workflows/pipe_fill_architectural.atlasflow).

**Voxelize Fill 0.1.0** fills a model with volumetric cubes, adaptive blocks or directional bars.
Cut away a side, separate filled layers, expose nested depth bands or erode a seeded front. Separate blocks
retain the interior; Joined volume exports its exposed boundary. Nine presets, native quad OBJ/PLY and STL,
with the same controls in UI, CLI and Workflows. Draw with Plotter Vision or Contour Lines.
See [Voxelize Fill methods, controls and drawing recipes](VOXELIZE_FILL.md) and
[Cutaway blocks](workflows/voxel_fill_cutaway.atlasflow).

**Image to Mesh 0.1.1** turns non-geographic photos, illustrations and grayscale heightmaps into faithful,
smooth, faceted or terraced relief. Choose grayscale conversion or an RGB channel, adjust brightness, contrast,
gamma, levels and smoothing, and preserve transparent silhouettes. Black is zero height and white is the
requested relief; tonal changes are never stretched away. Neutral image rotation and the shared plate editor
control physical placement. Seven presets include three open A3 surfaces for pen drawings.
See [preparation, height mapping and examples](IMAGE_TO_MESH.md),
[Image Relief Contours](workflows/image_contours.atlasflow) and
[Image Faceted Network](workflows/image_facets.atlasflow).

**Raster DEM to Mesh 0.3.1** focuses on geographic numeric DEMs with a CRS in the source or override.
Metric terrain scaling, masks, adaptive TIN, plate fitting and validated STL/OBJ/PLY exports remain available.
Non-geographic heightmaps now use Image to Mesh; old heightmap workflow steps give a migration message.
Both tools share physical layout, sampling, solid construction and the same UI/CLI/Workflow contract.
Authoritative fitting keeps locked previews consistent with final output. Local precision repair preserves
surface topology, Automatic safe clearance handles diagonal mask contacts, and artistic presets preserve
input-specific preparation and mask settings. Budget checks use occupied geometry and complete-solid costs.
No Blender, QGIS or R is needed. See [DEM methods, scaling and examples](RASTER_DEM_TO_MESH.md).

**Remesh/Decimate 0.1.0** prepares large scans or rebuilds their topology for pen drawings: constrained quadric
decimation, uniform/adaptive triangles, flowing quads, elongated directional triangles, dual cells, planar
dissolve and voxel reconstruction. Ten presets cover faithful preparation through bold facets and organic
networks. Native OBJ/PLY polygons survive wireframe preview and drawing; STL explicitly triangulates them.
Regional bands/spheres preserve detail or increase triangle density. The PMP helper is bundled; only voxel,
planar dissolve and the alternative QuadriFlow algorithm require Blender. Works in UI, CLI and Workflows.
See [methods, controls and examples](REMESH_DECIMATE.md).

Plotter Vision's **Remeshed network** preset draws every native mesh edge, including coplanar cell boundaries,
without polygon triangulation diagonals. FlowRep accepts preserved polygons from OBJ and PLY.

**Mirror 0.1.0** reflects a whole model through a manually positioned plane. Its plane starts at the model's
side, making an adjacent copy. Keep the original or save only the reflection; export combined unjoined shells
or separate files. The live Plane tab shares Plane Cut's drawing, arrows, rings, numeric controls and maximizable
editor. See [Mirror controls and output behavior](MIRROR.md).

**Plane Cut 0.1.0** trims or splits a mesh with a manually placed plane. Draw a line in the **Plane** tab, then
refine it with XYZ arrows/rings or numeric controls; **Expand** opens a maximizable editor. Choose the kept side,
flip it, cap the cut or leave it open, and save both halves together or separately. UI, CLI and Workflows use the
same settings. See [Plane Cut controls and limitations](PLANE_CUT.md).

**Developable Surfaces 0.1.0** evolves triangle meshes into approximately developable pieces with flowing
seams and nearly straight rulings. Five presets range from recognisable sculpture to broad folds and abstract
form. Open boundaries stay fixed; STL/OBJ/PLY outputs keep source units. The separately licensed headless
C++ helper is bundled, with corresponding source; no Blender or MATLAB is required. Draw with Plotter Vision's
new **Developable rulings** / **Developable creases** presets or Contour Lines. Works in the UI, CLI and Workflows.
See [method, setup and examples](DEVELOPABLE_SURFACES.md).

**Dilate 0.1.0** inflates a mesh with pressure or swells a feathered region into a sculptural bulge. Softness,
roundness, retained relief, directional expansion, anchors and seeded broad lobes shape six presets for
Contour Lines and Plotter Vision. One final mesh per run, available in the UI, CLI and Workflows, without Blender.

**Surface Stripes 0.2.0** adds raised bands to a mesh or exports only closed ribbons, using Crane's stripe-pattern
method through Geometry Central. Plotter broad and Plotter dense presets cover the form with flowing bands or
finer stripes. Send its signed STL/OBJ/PLY to Plotter Vision (Features) or Hatch Maker for pen-plotter SVGs.
See [Surface Stripes](SURFACE_STRIPES.md) for controls, build instructions and limitations.

| Tool | In → out | Replaces | What it does |
|---|---|---|---|
| **Line Smooth** 0.1.0 | SVG → SVG | new | Arc-length curvature fairing and compensated Taubin filtering for jagged strokes. Physical span/blend/displacement, persistent corner protection and all-pen swept-region checks. No vertex reduction. `<name>_smoothed.svg` |
| **[Skeleton Shell](SKELETON_SHELL.md)** 0.1.0 | Mesh → mesh | new | A hollow wire or plate sculpture on the model's skin: wire cells, a triangle net, flowing strands, airframe ribs and stringers, or a cut plate, in up to two layers, with optional kept skin and a fused printable result. Eighteen presets. Signed `<name>_skeleton.stl`. |
| **[Crumble](CRUMBLE.md)** 0.1.0 | Mesh → mesh | new | Part of a model breaks into solid blocks that collapse under gravity with real rigid-body physics, on a side, a patch, a painted area or everywhere, frozen at any moment and leaving a hollow. Ten presets. Signed `<name>_crumble.stl`. |
| **[Stair Sculpt](STAIR_SCULPT.md)** 0.1.0 | Mesh → mesh | new | Linear, turning or helical stairs, built in a carved or painted cavity (one stair or an ensemble with landings and arches), made from the model's own material as treads, or cut into its body. Fifteen presets. Signed `<name>_stairs.stl`. |
| **Surface Eject** 0.3.1 | Mesh → mesh | new | Actual surface-volume extraction with five samplers and whole/band/painted regions. Connect 0–100% of fragments; cylinders, bars or organic trees with up to 100 fixed/random extra branches, origin zones, generations, thickness and extent controls. Nineteen presets; combined assemblies or separate body/fragments/connectors in signed STL/OBJ/PLY. |
| **Bend** 0.1.0 | Mesh → mesh | new | Arc-length spine bending with transported sections, arcs/S-bends/waves, active range and anchors, spatial sweep and roll, local fold protection, conforming refinement and stage sequences. Signed `<name>_bend.stl` / OBJ / PLY. |
| **Surface Texture** 0.1.0 | Mesh → mesh | new | Twelve offline CC0 heightmaps or custom PNG/TIFF to real surface relief; surface-following, cylindrical and triplanar mapping, feathered regions, collision protection and plotter presets. Signed `<name>_textured.stl` / OBJ / PLY. |
| **Voxelize Fill** 0.1.0 | Mesh → mesh | new | Full volumetric occupancy as cubes, adaptive blocks or directional bars; joined boundaries, cutaways, exploded strata, depth bands and seeded erosion. Nine presets. Signed `<name>_voxel-fill.obj` (also PLY or triangulated STL). |
| **Raster DEM to Mesh** 0.3.1 | Image → mesh | R 3d_DEM prototype | Geographic numeric DEM to faithful, smooth, faceted or terraced terrain. CRS, elevation units and masks; adaptive detail, plate sizing and validated printable solids or open drawing surfaces. `<name>_dem_solid.stl` / `<name>_dem_surface.stl` |
| **Image to Mesh** 0.1.1 | Image → mesh | DEM heightfield foundation | Non-geographic photos and grayscale images to calibrated relief. Grayscale/channel selection, tonal preparation, alpha masks, neutral placement and seven presets; printable solids or open surfaces for pen drawings. `<name>_image_solid.stl` / `<name>_image_surface.stl` |
| **Remesh/Decimate** 0.1.0 | Mesh → mesh | new | Constrained decimation, uniform/adaptive and directional triangles, aligned quads, native dual cells, planar dissolve and voxel rebuilding. Regional detail, source projection, measured surface error, ten presets. Signed `<name>_remesh.obj` (also PLY or triangulated STL). |
| **Line Simplify** 0.1.0 | SVG → SVG | new | Native distance-based or heap effective-area reduction, physical error cap, corner/reversal/length/area controls and conservative all-pen topology guards. `<name>_simplified.svg` |
| **Overlap Cleaner** 1.2.1 | SVG → SVG | SVG Overlap Cleaner v1, SVG Join Paths v1/v2, svg_dedup | Cuts ink drawn twice, so every line is plotted once. Optional: join stroke ends, whole-duplicates mode, rect/circle/ellipse, stroke width as part of the pen. `<name>--nooverlap.svg` |
| **Shape Cutter** 2.0 | SVG → SVG | svg_shape_cutter | Keeps the lines inside (or outside) a closed shape: the element or layer named “shape”, a chosen layer or another SVG. Every island and hole counts. `<name>_cutout.svg` |
| **Shape Outline** 2.0 | SVG → SVG | SVG_Shape_outline_v1 | One closed outline around a drawing: an envelope that hugs the strokes (bridging gaps under N mm), a concave or a convex hull. All parts or the largest, optional holes, a margin in mm. Written alone on the same sheet (ready as the Shape Cutter's “Another SVG”) or as a new layer. `<name>_outline.svg` |
| **Hatch Maker** 6.0.2 | Mesh → SVG | STL 3D Hatch Maker v2.1, v3, v5 | Draws a model from any view: seven hatching styles, outlines and creases, hidden lines removed, fitted to paper in mm, one pen per layer, ordered to cut pen travel. `<name>_hatch.svg` |
| **Plotter Vision** 1.4.1 | Mesh → SVG | plotter.vision (web app, reimplemented) | A model as the edges you can see through a perspective camera, aimed live in the Vision tab: drag to orbit, wheel for distance, 1-5 views, F fit; hidden lines removed as soon as you stop. Edges flatter than an angle left out (0.01° = plotter.vision, a few degrees = clean quads, 30°+ = features; the Sketch preset is the Aim tab's look), lines where parts cross, optional decimation, depth pens, outline and hidden-line pens, red/blue stereo. `<name>_vision.svg` |
| **Contour Lines** 1.1.0 | Mesh → SVG | new | A model as horizontal slices seen through a camera, hidden lines removed by ray casting: the melting and twisting heads' line language, for any model, without Blender. Spacing in mm on paper, converging lines tapered, strokes chained for few pen lifts. Draws a Twist or Melt stage set with one frame (same scale, position and slice heights on every plate). `<name>_contours.svg` |
| **Sketch Hatching** 1.0.2 | Mesh → SVG | new | A model as an illustrator's pen sketch: lost-and-found outline, suggestive contours, valleys, creases, and hatching that follows the form in tone bands, with cast shadows and ambient occlusion; a focus point and a portrait light for a face. Two pens. `<name>_sketch.svg` |
| **Stipple** 1.0 | Mesh → SVG | Digital divide's sphere (`projects/blender3d_project/digital_divide_1`) | A model in dots only: blue-noise stippling of its ray-cast light and shade, an empty highlight, edge darkening instead of an outline, a dot budget. `<name>_stipple.svg` |
| **FlowRep Sketch** 0.1.2 | Mesh → SVG | new (FlowRep, Gori et al. 2017) | A model as a sparse network of curvature-aligned curves that describe its form, seen through a camera with hidden lines removed, plus an optional silhouette. Triangle meshes are remeshed into quads (Instant Meshes); quad-dominant OBJ/PLY keeps its topology. Descriptiveness sets how many curves remain (presets Clean, Detailed, Sparse); the 3D network is cached, so aiming again only redraws. `<name>_flowrep.svg` |
| **Photo Hatching** 1.0 | Image → SVG | the DPT notebooks (a–d) in `google_colab` | Engraving-style hatching of a photo, guided by depth from a transformer (MiDaS 3.1 BEiT-L-512, DPT-Large or Depth Anything V2 Large on the GPU): strokes wrap the forms, ink density follows the tone, crossing layers only in the shadows, occlusion outlines. Also the notebooks' patches (mosaic) and depth contours, fixed. `<name>_photohatch.svg` |
| **Cubic Replicator** 3.0 | Mesh → mesh | STL Cubic Replicator v2 | Copies of a model on a 3D grid, mirrored and turned at random (seeded). `<name>_replicated_3x3x1.stl` |
| **Form Replicator** 1.0 | Mesh → mesh | new | Compose one model into planar/spatial crosses and stars, clock rings, rosettes, sea urchins, sunflower spirals, helices, toroidal wreaths, trefoil knots and Möbius bands. Aim, stretch, taper and roll copies with seeded variation; touching copies are always fused into solids. `<name>_forms_star2d.stl` |
| **Grid Slicer** 3.1.1 | Mesh → mesh | cube_grid_slicer-v2 | Cuts a model into n × n × n capped blocks and pushes them apart; optionally shuffles them (blocks trade places, anywhere, in their layer or column) and shifts them at random. `<name>_grid3.stl` |
| **[Strata Sculpt](STRATA_SCULPT.md)** 1.1 | Mesh → mesh | new | Cuts a model into a stack of capped slabs (horizontal, front to back or side to side, optionally tilted) and dislocates them: thinner wafers lifted apart, a sideways or orbiting wave, a spiral turn, seeded slips and tilts; whole model, a height band or a sphere; one stage or a sequence. Optional randomly spaced cylinders, square bars or organic branching supports fuse neighbouring slabs; independent seeds and three connected presets. Pair with Plotter Vision. `<name>_strata.stl` |
| **Porous Forms** 1.0 | Mesh → mesh | new | Turns a closed model into a continuous labyrinth inside its own silhouette: gyroid, diamond, Schwarz P or Neovius walls or struts, or seeded Voronoi cells (foam, trabecular bone), cut by the envelope from a signed-distance volume (libigl) and extracted closed (marching cubes). Cell and wall in % of the height, a solid core behind the openings, a protected band or sphere (an intact face), cell grading; one stage or a sequence from solid to porous. Pair with Sketch Hatching, or Plotter Vision's Features. `<name>_porous.stl` |
| **Holes** 1.0.2 | Mesh → mesh | new | Pierces a closed model with through tubes, blind bores (flat, round or pointed end), bubbles (closed voids, craters where they reach the skin) or wandering, swelling wormholes, on a lattice (one to three axes, square or hex, woven), radially (spokes around an axis or from a point; rings or shells of bubbles), at random or spread over the surface. Circle, polygon, pillow or seeded blob profiles; sizes fixed or drawn from a range (mostly small for cheese); one Irregularity slider from a regular perforation to chaos with the same seed; a minimum wall that shrinks or drops holes. Exact cut (manifold3d), crisp rims. Pair with Sketch Hatching or Contour Lines. `<name>_holes.stl` |
| **Rods** 1.0 | Mesh → mesh | new | The mirror of Holes: adds rods, hollow pipes, drooping cables or spikes to a closed model in one exact union, Through it (sticking out both ends) or Outward from it (rooted inside). Holes' layouts (lattice, radial, random, surface), profiles (circle, n-sided polygon, pillow, blob) and Irregularity; width and reach in % of the height, fixed or from a range; flat, round, pointed or flared ends; collars; pipe bores open through the model or plugged. Pair with Sketch Hatching or Contour Lines. `<name>_rods.stl` (and `<name>_rods_tubes.stl`, the rods alone) |
| **Surface Peel** 1.0 | Mesh → mesh | new | Cuts the skin into petals (sectors about a bloom axis, in staggered rows with scalloped tips) or scales (seeded geodesic patches) and peels each back about a fixed root: unroll, open at the hinge, curl tighter toward the tip. Panels become closed shells (built in, or Blender's Complex Solidify); nested layers that open less and an intact core make a rose of heads; whole model, a height band or a sphere; one stage or a sequence with panels opening in turn. Pair with Plotter Vision or Sketch Hatching. `<name>_peel.stl` |
| **Shape Fusion** 1.0 | Mesh → mesh | new | Transformations between different shapes, no matching vertices needed: Morph a model into another model, itself (turned, moved, repeated around its axis) or a built-in torus, sphere, block or column; Fuse them with a smooth fillet, Carve one out of the other or Intersect them. Both are signed-distance volumes on one grid (libigl), so holes open and pieces join; the change can sweep across the model as a soft front, or stay in a band or a sphere; one stage or a sequence whose ends are the two shapes themselves. Pair with Contour Lines. `<name>_fusion.stl` |
| **Dual Fusion** 1.1 | Mesh → mesh | new | Two models fused into one sculpture: the second normalised to the first's height with a Size ratio, then Convergence (they glide together, bridge and melt into one hybrid, face-to-face pairs turning to the viewer), Emergence (one pushes through the other, a wall or the floor under a stretched membrane that can tear), Split with seam (halves of each, a carved groove, a raised suture or pulled apart), Interleaved strata (slabs of each, graded), Liquid bridge (sagging filaments that reach out and join), Disintegration (a front crumbles the first into drifting cubes or shards, revealing the second), Motion echo (the passage from one to the other frozen in fused moments), Cellular patchwork (Voronoi cells of each, grouted, exploded) or Chrysalis shell (the second as a cocoon round the first that cracks into plates or opens into a lattice). Each model is a signed-distance field sampled once and moved by pose, so sequences keep their detail. Pair with Contour Lines, or Plotter Vision for strands, fragments and cells. `<name>_dual.stl` |
| **Ribbon Wrap** 1.0 | Mesh → mesh | new | Wraps a model in a ribbon of silk, satin, velvet, cotton or gauze: a spiral bandage about an axis (full cover to gaps between turns, passes crossing in X's), parcel bands (meridians, rings, seeded planes) or a loose drape walking round the form. The ribbon rides the outer skin, bridges hollows with Tension and stacks in layers where it crosses itself; its start and end tails fall outside the form (Lift, Gravity, Sway, Twist, a corkscrew Curl), rest on the floor and end cut, tapered or frayed. Threads, weave, ripples and pillowed edges are geometry; optional Blender cloth settle. Pair with Sketch Hatching. `<name>_ribbon.stl` |
| **Reaction-Diffusion Growth** 1.0 | Mesh → mesh | new | Grows a Gray-Scott reaction-diffusion pattern over the surface itself (coral ridges, fingerprint channels, a maze, dividing cells, a honeycomb of pits, pebbles, lichen) and raises or engraves it as relief along the rest normals. Wavelength in % of the height on a mesh fitted to it, so the scale holds across models and densities; channels can wrap around the model or run up it, a second pattern can take over up the height, and the pattern stops at a natural edge outside a band or sphere. One stage or a sequence of moments of one simulation, scattered or creeping from the crown, face or base. Pair with Sketch Hatching or Contour Lines. `<name>_growth.stl` |
| **Mesh Shatter** 4.1 | Mesh → mesh | Mesh shatter v3 | Breaks the surface into shards along its creases and blows them apart (seeded), optionally tied to a central hub by stems or a branching tree. `<name>_shattered_25pcs.obj` |
| **Disintegrate** 1.1.0 | Mesh → mesh | new | Progressively subdivides a model and pushes every triangle corner on its own until the surface comes apart into loose triangles (formerly Mesh Morph; `mesh-morph` still works). Seeded, directional controls, final or combined progression, optional stage files. `<name>_disintegrate_progression_2steps.stl` |
| **Twist** 1.1 | Mesh → mesh | new | Wrings a model: an undulating torsion about its up axis, an S-sway, a (spiral) ripple; one stage or a sequence from intact to full, each stage saved in place for Contour Lines. No Blender needed. `<name>_twist.stl` |
| **Surface Stripes** 0.2.0 | Mesh → mesh | new (Knöppel et al. 2015) | Flowing surface bands, fused to the body or exported as closed ribbons alone (off by default). Plotter broad and Plotter dense presets, measured skin coverage, spacing, width, thickness, rotation, crossing families and seed. Bundled Geometry Central/Eigen helper. Draw with Plotter Vision (Features) or Hatch Maker. `<name>_stripes.stl` |
| **Compress** 0.1.0 | Mesh → mesh | new | Compresses a model in one step. Free: two press plates (tilted for a shear press, or onto the floor), a tube tightening around an axis, or pressure from every side. Into a shape: a cube, sphere, cylinder, cone, pyramid, prism, octahedron, icosahedron or dodecahedron mould (any aspect ratio) closes on it, or the model morphs onto the shape with its features in relief. Physical modes are a quasi-static projective-dynamics solve on a near-uniform copy (elastic or plastic skin, bending stiffness, internal pressure that keeps or gives up the volume, friction, approximate self-collision, seeded imperfections) whose motion carries the full-detail model. Pair with Contour Lines or Plotter Vision. `<name>_compress.stl` |
| **Developable Surfaces** 0.1.0 | Mesh → mesh | Stein, Grinspun & Crane (2018) | Coarse-to-fine developability optimisation; field-aligned remeshing with checked coarsening fallback, fixed open boundaries and five aesthetic presets. `<name>_developable.stl` (also OBJ/PLY). |
| **Dilate** 0.1.0 | Mesh → mesh | new | Pressure-driven expansion with elastic stretching and bending resistance, per-part volume targets, adjustable softness, roundness and relief, seeded broad lobes, global or feathered local influence, and base/boundary anchors. Open meshes keep their openings and use outward surface swelling with a warning. Triangle contact checks and backtracking limit new crossings. One signed `<name>_dilate.stl` (also OBJ/PLY); pair with Contour Lines or Plotter Vision. |
| **Flow Warp** 1.0 | Mesh → mesh | new | Pulls a model through turbulent currents. Curl-noise eddies, a vortex and a streaming pull carry every vertex along one smooth field (Runge-Kutta), so the surface folds without tearing. Base anchor; whole model, a height band or a sphere; one stage or a sequence on the same trajectories. Pair with Contour Lines. `<name>_flow.stl` |
| **Melt** 1.1 | Mesh → mesh | new | Melts a model like ice cream or molten metal: softening, sag, a pool at its base, drips and rivulets, up to dissolving; generic or head-aware; one stage or a sequence whose drips grow in place. Runs in the installed Blender (4.2+), in the background. `<name>_melt.stl` |
| **Voxelizer** 2.0 | Mesh → mesh | Voxelizer tool v1 | Rebuilds a model from cubes, spheres or cylinders, as a shell or filled. `<name>_vox.stl` |

Mesh tools work in the model's own units and can save STL, OBJ or PLY. SVG tools work in millimetres whatever units
the file uses, and keep layers, ids, styles and transforms.

## Form Replicator

Choose a figurative model or an abstract shape, then a preset and **Preview**. The twelve layouts include 2D and
3D crosses and stars, a clock, petal rosettes, sea urchins, sunflower spirals, helices and three spatial wreaths:
toroidal, trefoil and Möbius. Copies can point outward, inward, along a curve or along a surface normal, or stay
upright. Aim their **Model up** or **Model front**, place their centre or base on the layout, and adjust size,
stretch, taper and progressive roll. Seeded irregularity and thinning add variation reproducibly.

Touching and overlapping copies are **always boolean-unioned**; gaps can remain as separate closed components.
The tool preserves source units and uses the shared source frame, mesh previews and signed STL / OBJ / PLY
exporters. Preview reduces source detail but keeps the full arrangement. Run uses **Source detail** (12,000 faces
by default; 0 keeps the original). Open-source repair, when needed, is reported because it can soften detail.

For the plotter, connect **Mesh input → Form Replicator → Plotter Vision / Contour Lines / Sketch Hatching →
Overlap Cleaner → Save** in Workflows. Front-plane layouts look especially clear in an orthographic front view;
an oblique view reveals the holes and crossings of spatial forms. Presets, parameters and mesh formats also work
through the generated CLI:

```powershell
atlas-toolkit run form-replicator model.stl --preset "Radiant Star" --out-dir output
atlas-toolkit run form-replicator model.stl --preset "Sea Urchin" --count 72 --seed 42 --out-dir output
atlas-toolkit run form-replicator model.obj --preset "Endless Knot" --format .ply --out-dir output
```

Distances are percentages of the source height **before** Copy size / Stretch. Lower **Arm step** to join copies
along rays; adjust **Radius**, **Copy size** and **Rows** to balance overlaps and negative space on curves.
The working face limit refuses oversized arrays before building them. See [the detailed guide](FORM_REPLICATOR.md).

## Disintegrate

Formerly **Mesh Morph** (renamed in 1.17.0; the old id `mesh-morph` still works on the command line).

Import a mesh, set **Subdivision steps** and **Strength**, then **Preview**. Stage 0 is the original; every step
splits each triangle into four and independently moves its corners. The result is open surface fragments.
The Input, Stage and Progression tabs let you inspect the transformation; Preview uses the same geometry as Run.
Large previews sample intact triangles (up to 80,000 faces per view); exported meshes retain every face.

**Strength in** chooses model units or a percentage of the original bounding-box diagonal. At stage i the maximum
displacement is strength × i^growth: growth 0 is constant, 1 is linear, 2 is quadratic.
**Direction** can be random, along each triangle's normal, or radial from the original bounding-box centre.
**XYZ influence** scales displacement per axis without renormalizing; zero locks an axis. **Seed** makes runs repeatable.

**Primary output** saves all stages arranged along X/Y/Z, or just the final stage in the original coordinate frame.
The **Gap** clears the actual bounds of neighbouring stages. **Also save every stage** adds signed files for stage 0
through the final stage, without layout offsets, in a sibling stages folder (numbered when one already exists).
STL, OBJ and PLY are available. Send any saved mesh to **Hatch Maker** to make plotter SVGs.

The **Gentle** preset uses two steps and 1% strength. **Long progression** uses five steps (six stages), 1.5 model
units and linear growth. **Strong** uses three steps and 3% strength with growth 1.5. Imported geometry is never
simplified: if all stages together would exceed **2,000,000 faces**, the tool stops before subdivision and reports
the largest supported step count. For example, 100,000 input faces support one step (500,000 total), while two
would require 2,100,000. Reduce steps or explicitly prepare a lighter input in another tool.

```bat
atlas-toolkit run mesh-disintegrate model.stl --steps 3 --strength 1 --units percent --seed 42
atlas-toolkit run mesh-disintegrate model.obj --layout final --direction normal --separate --format .ply
```

## Plotter Vision

The **Vision** tab is plotter.vision inside the toolkit. The paper's drawing area is framed: the camera looks
through it, so what is in the frame is what Draw writes, cut at the frame in mm.

| Do | To |
|---|---|
| drag | orbit (θ, ψ) |
| Shift-drag or right-drag | move the look-at point |
| wheel / Ctrl+wheel | distance / field of view |
| `1` `2` `3` `4` `5` | front, side, back, other side, top |
| `F` or double-click | fit the model in the frame |
| `R` / `T` | plotter.vision's start view / red-blue stereo |

While you move, every candidate edge shows in the accent colour; when you stop, hidden lines are removed and the
drawing fills in from the middle. Letting go writes the camera into the settings, so presets keep it and Draw uses
it. It draws the same lines as plotter.vision (checked against its own JavaScript on a reference scene), but hides
them exactly. Lines inside other solids, faces crossing the window edge or the near plane, and tiny faces no longer
leak hidden lines, and no edge is drawn twice.

## 3D drawings and distortions: Contour Lines, Sketch Hatching, Stipple, Twist, Melt

The tools of the Blender pieces in `projects/blender3d_project` (the melting and twisting heads, the Augustus sketch,
Digital divide's sphere), for any model. `docs/PARAMETERS_3D.md` ranks their settings by how much they change the
drawing and gives the recipes that reproduce the pieces.

- **Aim** (Contour Lines, Sketch Hatching, Stipple): the camera is Plotter Vision's, and so is its live view, the
  **Aim** tab (drag, wheel, `1`-`5`, `F`). The model shows as its outline and creases; Preview draws the real thing.
- **Up and Front** say how a model stands (Auto: Z-up STL, Y-up OBJ, a statue stored lying down stands up, as in
  the Atlas gallery); **Extra turn** lays it down or tilts it. Lengths are % of the model's height and frequencies
  are waves over its height, so settings carry over between models.
- **Sequences:** Twist, Flow Warp, Porous Forms, Surface Peel, Shape Fusion, Reaction-Diffusion Growth and Melt make 1-8 stages from intact to the amount you set, saved in
  `<name>_twist_stages/` (or `_melt_stages/`) with a `stages.json`. Add the stage files to Contour Lines (or
  Sketch Hatching, Stipple): a stage set gets one frame, so the plates share scale, position and slice heights and
  read as one motion. Disintegrate's stage folders work too.
- **Compress** presets were tuned through Contour Lines and Plotter Vision on the v17 head and David:
  **Hydraulic Press** (default) barrels clay between plates, **Face on Glass** prints the face flat (best in Plotter
  Vision), **Crumpled Foil** crushes from every side into facets, **Cubed**, **Head Ball** and **Gem Cast** close a
  mould to 80-85% so the face survives, and the **Relief** presets morph onto a solid with the features raised.
  Flat horizontal faces of a mould leave a few slice islands in Contour Lines; turn the view or use Plotter Vision.
- **Dilate** makes one inflated model. Inflation is requested added volume (80% means 1.8×) for a whole closed
  part; local influence and anchors reduce participation. Around an axis leaves the axial coordinate fixed.
  Softness relaxes the elastic skin, Roundness broadens curves, Keep detail retains relief, and Broad lobes varies
  pressure smoothly at the seeded spatial scale. Keep base fixes the lowest 2% of the height and fades over the
  next 5%; Keep open boundary fixes openings. Quality sets simulation density; Output face limit is shared by
  all parts. Zero inflation leaves geometry unchanged. Preview and dry-run write no output.
  **Sculptural Swell** is the balanced default, **Rotund Bronze** widens upright figures with the base held
  ([Botero reference](https://www.nevadaart.org/art/exhibitions/the-baroque-world-of-fernando-botero/)),
  **Flowing Goddess** adds flowing localized volumes ([Miriam Lenk reference](https://miriamlenk.de/en/texte/)),
  **Balloon** gives taut fullness, **Inflated Clownfish** swells a central sphere (position it to spare fins and
  tail), and **Local Bulge** focuses the effect toward the front. Adjust the band or sphere to your model.
  Open or unreliable-volume parts keep their openings and swell along their oriented surface using an
  area-equivalent displacement; their enclosed volume is not reported. Contact checks are discrete safeguards,
  not continuous collision detection. Existing intersections are reported, not repaired. Anchors, contact or
  convergence can limit inflation; the report shows the achieved result. Workflows can connect Dilate directly
  to Contour Lines or Plotter Vision, then to SVG cleanup and Save.

  ```bat
  atlas-toolkit describe mesh-dilate
  atlas-toolkit run mesh-dilate model.stl --preset "Rotund Bronze" --out-dir output
  atlas-toolkit run mesh-dilate model.stl --inflation 120 --influence sphere --sphere-radius 25 --format obj
  ```

- **Flow Warp** carries the model through a turbulent current, a vortex or a streaming pull. Its presets were tuned
  through Contour Lines' default camera on the v17 head: **Vortex Portrait** puts a whirlpool on the face, **Breaking
  Wave** rolls the crown back, and **Current sequence** gives four plates along one flow.
- **Porous Forms** samples the model as a signed-distance volume (Resolution samples along its longest side; walls
  need three, so 256 resolves walls down to ~1.2% of the height) and fills it with a periodic pattern. Its presets
  were checked through Sketch Hatching's default camera on the v17 head: **Porous Marble** (a gyroid over a solid
  core), **Exposed Labyrinth**, **Open Skull** (the face kept solid), **Solid to Cellular** (four stages opening
  from the top down), **Trabecular Bone**, **Cellular Foam** and **Diamond Lattice**. Keep cells at 10-20% of the
  height: smaller ones turn a bust into texture on paper. The output is closed; thin parts the pattern cuts off stay
  as loose pieces unless Loose pieces says otherwise.
- **Surface Peel** cuts the surface into panels and bends each away from the body about its root. Draw it with
  Plotter Vision at a 20-30° flat angle (panel rims, curls and overlaps, no mesh noise) or Sketch Hatching. Its
  presets were checked through Plotter Vision on the v17 head: **Marble Blossom** (two rows of petals opening from
  the crown), **Opening Mask** (rings of petals peeling back from the nose, the face still legible; look from the
  front), **Rose of Heads** (three nested layers), **Armored Scales**, **Shedding Crown**, **Unfurling Shell**
  (strips wound into scrolls round an intact core) and **Blossom sequence**. Curled panels can pass through their
  neighbours: no collisions are solved. Shell → Solidify with Blender uses its Complex Solidify (Blender 4.2+).
- **Shape Fusion** puts a target in the model's frame (another model: same height on the same base, same size, or
  its own placement; the model itself; or a built-in shape as tall as the model), then Morphs, Fuses, Carves or
  Intersects. A second model can come from a workflow's side input. Its presets were checked through Contour Lines
  on the v17 head (David as the other model): **Halfway Hybrid**, **Directional Metamorphosis** (five plates turning
  into the other model from the top down) and **Organic Union** need a target model; **Ring Metamorphosis** (a ring
  opening through the face from the base), **Haloed Bust**, **Janus**, **Three Faces**, **The Kiss** (a copy facing
  the model, fused at the lips), **Bored Through** and **Hewn Crystal** work on any model. Shapes that overlap little
  thin out half way: the run warns, and Offset, Fit or Swell fix it. Air sealed between overlapping shapes is
  filled, so the result is one closed solid.
- **Dual Fusion** works on two models: the input and **Second model** (a file, or a workflow's side port; none
  uses the first one mirrored). Both stand on one base, the second **Size ratio** times as tall, arranged side by
  side, face to face, back to back, stacked or overlaid. Its presets were checked on the v17 head with David, Venus
  with the Discobolus and two kiss sculptures, through Contour Lines (front three-quarter) and Plotter Vision for
  the strands: **First Contact** (five plates: two profiles meet, bridge and turn to you as one head), **Merging
  Twins**, **Face in the Wall**, **Emerging Self**, **Two-Face**, **Split Open**, **Fault Line**, **Interleaved
  Strata** and **Dithered Metamorphosis** (upright slabs, which Contour Lines draws best), **Liquid Metal** and
  **Molten Strands** (four plates of tendrils reaching out and joining), **Dust to Form** and **Shattered
  Ascension** (fragments; draw them with Plotter Vision at a 25° flat angle), **Continuity in Space** and **Spiral
  Echo** (after Boccioni), **Mosaic Hybrid**, **Exploded Mosaic**, **Chrysalis** (a gyroid cocoon) and **Cracked
  Cocoon** (four plates of a shell cracking from the crown). The cocoon always clears the inner model (Clearance),
  whatever the second model's shape. Floor scenes are wide and flat: set Up to
  +Z in the drawing tool. About 15-40 s for two heads at Resolution 256; Preview 4-10 s.
- **Mesh Shatter's hub connectors** run after the explosion and never move a shard. **Connected shards** picks the
  share tied to the hub; the rest float free. **Shared tree** thins each limb by the number of shards it carries, so a
  few **Trunks** branch out across the burst, while **Radial stems** give one stem per shard. A low **Branch split**
  forks early into a wide crown. **Extra branches** spread over each shard like roots gripping a stone. **Deep inside**
  needs a closed model; an open one uses its surface centroid. **Shard thickness** at 0 keeps open shards, which draw
  as thin shells but cannot be printed; above 0 the connected shards and hub are one closed solid and free shards are
  closed pieces beside it. Connectors can pass through a shard that lies in their way: the tool does not steer around
  them, so change **Seed** or the **Hub position** if one lands badly. Review with Plotter Vision at a 25° flat angle.
- **Reaction-Diffusion Growth** simulates on a copy of the surface with about 7 vertices per wavelength (scans that
  are much denser are simplified for the simulation; the relief goes back onto the full model at 10 per wavelength).
  About 30-60 s for the v17 head at the default 5% wavelength; halving the wavelength costs four times as much, and
  Run refuses over 800 000 simulation vertices. Its presets were checked on the v17 head through Sketch Hatching
  (Classic) and Contour Lines: **Coral Ridges**, **Fingerprint Marble** (engraved channels wrapping around the head),
  **Cellular Skin**, **Brain Coral** (deep broad meanders for Contour Lines), **Pitted Mask** (cells cut into the face only),
  **Lichen Crown** (stops above the brow), **Coral to Cells** and **Creeping Coral sequence** (five plates, coral
  spreading from the crown). Keep the wavelength at 4% of the height or more for A3: finer patterns turn to texture.
- **Holes** places cutters as skeletons first (placement, spacing and Acts on need no geometry), then builds only the survivors and takes them all away in one manifold3d boolean: a second or two for a few hundred holes on the v17 head. Every cutter is nudged by 0.01% of the height so that nothing meets exactly (radial tubes through one point). Voids that stay inside the model are kept: they show in Contour Lines' slices, not in a view of the surface. Long holes in random directions nearly always pass near each other, so give them a Minimum wall of 0. An open model is closed first by resampling (Closing resolution). Presets checked through Sketch Hatching (Classic) on the v17 head: **Swiss Cheese**, **Cratered Rind**, **Perforated Mask**, **Woven Block**, **Lantern**, **Drilled Surface**, **Honeycomb Bores**, **Wormholes**, **Wavy Channels**, **Cloud Tunnels** and **Pierced Crown** (bubbles in the upper half only).
- **Ribbon Wrap** works in the distortion frame (height 2): the path comes from rays from outside (the outer skin), the strip is shrink-wrapped along its own normal and tensioned along it only (no sliding, no narrowing), layers are counted on a coarse strip (earlier ribbon points within a cell) and lifted with a ramp, and the fine strip with its threads, weave and ripples takes the coarse one's shape by interpolation. Spiral pitch is measured along the skin's profile, and the wrap stops where a turn would be narrower than the ribbon (no crumpled crown). Tails that touch the body lie flat on it; Curl coils round a spine kept a radius off the skin. **Settle in Blender** sends the coarse strips to the Blender worker's `cloth` operation (wrapped rows pinned by Hold, model and floor as colliders): ~6 s for 40 frames on the v17 head. Horizontal contours run parallel to a spiral's turns, so draw spirals with Sketch Hatching or Plotter Vision; Contour Lines suits bands and drapes. Presets: **Mummy bandage**, **Loose bandage**, **Silk scarf**, **Gift ribbon**, **Velvet straps**.
- **Rods** reuses Holes' placement, profiles, irregularity and spacing (from `tools/mesh_holes`); the spacing counts only the parts of rods outside the model, so through rods that cross inside it are kept. Long rods and cables enlarge the result's bounding box, so a drawing tool that frames the whole mesh shows the model smaller: lower Reach or set the camera by hand. Use the `_rods_tubes` file to draw the rods with another pen. Presets checked through Sketch Hatching (Classic) on the v17 head: **Pin Cushion**, **Skewered**, **Sea Urchin**, **Crown of Thorns**, **Scaffold**, **Organ Pipes**, **Exhausts**, **Hanging Cables**, **Tendrils**, **Hex Pencils** and **Starburst**.
- **Twist** runs in the toolkit. **Melt** runs its Geometry Nodes melt in the installed **Blender** (4.2 or newer;
  5.2 here), started once in the background and kept warm (`blender -b --factory-startup`, no window, no
  venv: Blender brings its own Python). It is found in `%ProgramFiles%\Blender*`, on PATH, from
  `ATLAS_TOOLKIT_BLENDER`, or where **Locate Blender…** on the Melt page points. Cancel stops Blender at once.

```bat
atlas-toolkit run mesh-twist head.stl --preset "Torsion sequence"
atlas-toolkit run mesh-melt head.stl --preset "Dissolution sequence"
atlas-toolkit run contour-lines head_melt_stages/*.stl --preset "Fine detail"
atlas-toolkit run sketch-hatching bust.stl --preset Engraving --rotate 0 90 0 --front=-y
```

(An axis value that starts with a minus needs `=`: `--front=-y`.)

## Photo Hatching and the GPU runtime

The depth models need PyTorch, which is far too large for the app itself (0.5 GB for the CPU build, about 3 GB with
CUDA). They run in a separate Python environment, the **depth environment**, that the tool starts in the background
and keeps warm.

**Installing it.** Open **Setup › Components** and press **Install…** on the Depth environment card. You need no
Python, terminal or source of your own. Before anything is downloaded the installer shows:

- **Graphics card:** what it found (the card, its memory, driver and compute capability).
- **Build:** the PyTorch build it chose, and why.
- **Download, disk space and time:** the download size, the disk space needed and kept, and an estimate of the time.
- **Folder:** where it will install.
- **Readiness:** whether this computer is ready (free space, a writable folder, the download sites reachable).

During the install it shows each stage, one progress bar with the speed and the time left, and the installer's own
output under Details. Cancel removes what was begun. At the end it says what it proved: the PyTorch version and the
device the models run on. **Check** proves it again later, and **Remove** deletes everything it installed.

| This computer has | Build installed |
|---|---|
| An NVIDIA card from the GTX 16 / RTX 20 series on (Turing and newer), driver 580 or newer | CUDA 13.0 |
| An older NVIDIA card (Maxwell, Pascal, Volta), or a newer one with driver 528 to 579 (RTX 50: 580 needed) | CUDA 12.6 |
| An NVIDIA card with no working driver | CPU, with a link to NVIDIA's drivers and **Detect again** |
| No NVIDIA card (AMD, Intel graphics) | CPU: works everywhere, 10 to 30 times slower per drawing |
| A Mac | PyTorch for macOS, on the CPU (`ATLAS_DEPTH_DEVICE=mps` tries Apple's GPU, untested) |

The card is found with `nvidia-smi`. Without it, Windows' list of display adapters or Linux's PCI devices still tell a
card with a missing driver from no card at all. After installing, PyTorch itself must see the GPU, list its
architecture and compute correctly on it, or the environment is not put in place and the next build is offered.
Every version is pinned in `src/atlas_toolkit/geom/depth_runtime.json`: uv (checked by SHA-256), Python, PyTorch and
the model libraries.

The same from a terminal, a server or over SSH:

```bat
atlas-toolkit components                         :: what is found, the card, the build it would install
atlas-toolkit components install depth           :: shows the plan, asks, installs (--yes, --build cpu, --with-model)
atlas-toolkit components check depth --model dpt-beit-large-512   :: run a model once: device, time, memory
atlas-toolkit components remove depth            :: delete it (--models also deletes the downloaded models)
```

**Where it lives.** The environment is in `ml-runtime` in the per-user app-data folder
(`%LOCALAPPDATA%\AtlasToolkit\ml-runtime` on Windows, `~/Library/Application Support/AtlasToolkit/ml-runtime` on
macOS, `~/.local/share/atlas-toolkit/ml-runtime` on Linux). The installer's own tools, uv and its Python, are in
`ml-runtime-base` beside it. Uninstalling the app leaves both, like your settings: use **Remove** first, or delete the
two folders. The app looks for an environment, in order: the `ATLAS_TOOLKIT_ML_PYTHON` variable or a located one,
`.venv-ml` next to the exe, `.venv-ml` in the source checkout, then that folder. **Locate** points the app at an
environment you made yourself.

From a checkout, developers run the same installer with `scripts/setup_ml.py` (into `.venv-ml`, or `--user` for the
per-user folder):

```bat
.venv\Scripts\python scripts\setup_ml.py                      :: the build that suits this computer, in .venv-ml
.venv\Scripts\python scripts\setup_ml.py --check photo.jpg    :: every model at every detail level, timings and GPU memory
```

Each model's weights (about 1.3 GB) download into the Hugging Face cache on first use. Depth is cached per photo,
model and detail in `depth/` in the cache folder, so changing the hatching never runs the model again.

| Model | Licence | On this RTX 5070 Laptop (8 GB), fp16 |
|---|---|---|
| MiDaS 3.1 BEiT-L-512 (default) | MIT | 0.3 s native, 1.5 s at 1024 px, 1.5 GB |
| DPT-Large (the notebooks' model) | Apache 2.0 | 0.2 s native, 0.35 s at 1024 px, 1.5 GB |
| Depth Anything V2 Large | **CC BY-NC 4.0** (non-commercial) | 0.25 s native, 0.4 s at 1024 px, 1.4 GB; by far the most detailed |

Depth detail: **native** (the model's own size), **high** (1024 px; can lose the overall layout), **boost** (layout
from native, fine detail from high; the default). Without the runtime, use a depth-map file (`<name>_depth.png` is
found automatically, 8 or 16 bit, white = near) or the rough depth guessed from tone.

The settings that matter most: **Direction** (around forms = engraving), **Cross-hatch below** and **Crossing
angle** (how soon and how the shadows get crossing layers), **Spacing dark / light** with the pen width (how black the
blacks get), **Paper white above**, **Local contrast**. Presets: Engraving, Pen & ink, Fine etching, Topographic,
Mosaic.

## Using it

- **Home** shows the tools. Drop files on it and it opens the tool that takes them (or asks which).
- **A tool page:**
  - The **input queue** lists the files, each with its status and key numbers.
  - The **preview** shows the selected file: Before / After / Cut ink on paper for SVGs, the model in 3D for meshes.
    Wheel zooms, drag pans or orbits, double-click fits. The paper icon in the top bar switches paper and blueprint.
  - The **inspector** on the right has presets, settings (hover any of them for help) and where outputs go.
    Settings are remembered. Pick a preset from the **Presets** drop-down: your own under My presets, then each
    preset pack you have loaded. It opens on "Custom settings" every time. The icons beside it save the current
    settings as a preset of yours, open a menu (save presets to a file, load a preset file or pack, copy a pack
    preset to My presets, delete one of yours) and reset to the defaults. See [Presets](PRESETS.md).
  - **SVG tools** analyse every file again as you change a setting.
  - **Mesh tools** have **Preview**, a dry run of the selected file. **Run** (or Clean, Cut, Draw…) writes every
    file. An output that exists is overwritten, or kept, as Setup says (below); originals are never changed.
- **Plotter Vision:** aim in the Vision tab (see below); presets change the lines, never the camera.
- **Hatch Maker:** orbit the model in the Input tab, press **Use the 3D view**, then **Preview**. The drawing is made
  from exactly that view.
- **Home** shows the tools as cards or as a one-line **List**; each section (Workflows and each tool group)
  folds under its header, and **Fold all** folds them together. Saved workflows show by their names.
- A tool whose required program (Blender for Melt, the stripes helper) is missing is **faded** in every list, with an amber ⚠ and the reason in its tooltip; its page shows a notice under its name (Locate, Get, Setup guide) and Run waits. In Workflows its step turns amber at once. An optional program only adds a quiet note on the tool's page. The app looks again whenever its window comes back into focus.
- **Activity** lists every run. Show an output, or open the tool again with the same file and settings.
- **Send output to** (right-click a saved file) chains tools, for example Replicator → Grid Slicer.
- Dropping an SVG made by a tool back on that tool offers to restore the settings it was made with.

**Number fields:** the triangles at the right of a field step its value; hold to repeat, **Shift** for ten times
as far, **Ctrl** for a tenth. The step fits the setting: face counts move in round thousands, small sizes in small
steps. Or click and type: a comma or a point, any number of decimals; it is rounded and kept in range on Enter.

**Tool list:** click a group's header to fold it (a folded group holding the open tool turns cyan); the
buttons above the list fold every group and switch to **icons only**, a narrow rail with the groups split by
lines. The button at the top left of the window (or `[`) hides the list. The Workflows step list works the same
way. All of it is remembered.

**Setup** (the gear, top right, or `Ctrl+,`): the app's own options, saved as they change.

- **General:** the theme and artwork the app opens in (the last one used, dark, light or the system's), the interface
  scale (next start), what opens at start (Home, or the tool or workflow it was closed on), asking before quitting
  during a run, and the preview delay.
- **Outputs:** when an output file exists, for tools and workflows alike: **overwrite** without asking (the default),
  **ask** first, or **keep both**: the new file gets the run's date and time, `head_contours_20261103-142530.svg`,
  for random variations and new seeds. Where a tool's outputs go until it has its own choice, how many heavy jobs
  run at once (each in its own worker process: faster batches, more memory), and what happens when a run ends
  (show the output, flash the taskbar, a sound).
- **Components:** the optional components (Blender, the GPU depth runtime): whether they are found, the tools that
  use them, why, how to install them, and **Locate**.
- **Presets:** your presets and the preset packs you have loaded. Load an `.atlaspreset` file, an `.atlaspack`
  pack or a `.zip` holding them (you see what it holds and what it would change first), see whether a pack is
  **Official** (signed by its publisher, unchanged) and who signed it, trust another publisher's key, save your presets to a file, copy a
  pack's preset to yours, rename or delete yours, hide a pack's (Show hidden brings them back), remove a pack. Each
  tool's preset menu has **Manage presets and packs…**, which opens it on that tool. See [Presets](PRESETS.md).
- **Storage:** where the app keeps its files (Open buttons, the cache's size); clear the cache or the history,
  **export / import** the options, every tool's settings and your own presets as one file (preset packs are not
  included), reset the options.

**About** (ⓘ, top right): the toolkit and its version, the tools with their icons, the command line, the shortcuts,
the changelog, credits, and **Copy diagnostics** for a bug report.

**Keyboard:**

| Key | Action |
|---|---|
| `/` or `Ctrl+K` | Search tools and actions |
| `Ctrl+O` | Add files |
| `Ctrl+Enter` | Run |
| `Esc` | Cancel |
| `[` | Show or hide the tool list (on Workflows, the step list) |
| `]` | Tool list: icons only or names |
| `Ctrl+L` | Log |
| `Ctrl+,` | Setup |
| `Alt+1…9` | Tools |

## Workflows

The **Workflows** tab (Ctrl+3) chains tools into one pipeline, as in the QGIS model designer. For example:
Mesh input → Mesh Shatter → Hatch Maker (Contours) → Overlap Cleaner (Whole duplicates) → Save.

- **Build.** Add steps from the list on the left: drag them, or double-click to add one next to the selected step,
  already connected. Drag from a step's output dot to another step's input to connect them. Tab adds a step at the
  cursor. One output can feed several steps. A tool's file settings (Shape Cutter's shape, Photo Hatching's depth)
  are extra input dots another step can feed.
- **Set up each step** with the same presets and settings as on the tool's page. **Aim in tool…** opens the step's
  input in the tool's page (Hatch Maker's view, Plotter Vision's camera); **Back to workflow** brings the settings
  back. Right-click a setting's name to show it on the workflow's own panel, which appears when no step is selected.
- **Check, then Run.** Check lists anything that would stop the workflow, both on the canvas and in Problems. Run is
  enabled once Check passes, and runs once per file in Inputs. Any edit asks for a new Check.
- **New variation** (under Run, for workflows with seeded steps) runs again with new random seeds and the same
  settings; its files get the date and time, so every variation is kept, and the log lists the seeds drawn.
- **Only Save writes files.** Intermediate results stay in a cache, so steps that did not change are not run again
  (the step shows `cached`). **Preview** (P or double-click) shows any step's result. A Save names its file after
  the chain (`head_shattered_25pcs_hatch--nooverlap.svg`), or by a template: `{name}`, `{stem}`, `{workflow}`,
  `{node}`.
- Workflows are `.atlasflow` files (JSON) in the settings folder's `workflows`. Open them from the list, Home or the
  command palette. Undo with Ctrl+Z, save with Ctrl+S. The workflow you are editing survives a restart.

## Command line

The same tools, generated from the same settings:

```bash
atlas-toolkit list
atlas-toolkit describe stl-hatch-maker
atlas-toolkit run svg-overlap-cleaner drawings/*.svg --preset-file clean.atlaspreset --join 0.1
atlas-toolkit presets list svg-overlap-cleaner
atlas-toolkit run voxelizer head.stl --across 80 --shape sphere --out-dir out
atlas-toolkit self-test
```

The output streams as it comes, and the exit code comes back: 0 done, 1 a file failed, 2 a mistake in the command. An output that exists is skipped unless you add `--force` (overwrite it) or `--suffix` (keep it; the new one gets a date-time suffix). About › Command line lists the commands.

- **Windows:** `atlas-toolkit.exe` is the console companion next to `Atlas Toolkit.exe`. The installer can add it to PATH. Otherwise, in PowerShell, type `.\atlas-toolkit …` in its folder. `Atlas Toolkit.exe` takes the same commands, but the terminal does not wait for it.
- **Linux:** the deb and rpm put `atlas-toolkit` on PATH, and it needs no display, so it also works on a server. An AppImage file takes the same commands.
- **macOS:** the program inside the app takes these commands. [Install on macOS](install/macos.md) shows how to link it as `atlas-toolkit`.
- **From source:** `python -m atlas_toolkit …`

## Files it keeps

The app keeps these in its settings folder:
- settings (`app.json`, `tools/<id>.json`) and your presets (`presets/mine/<id>.atlaspreset`);
- installed preset packs (`packs/*.atlaspack`, in the data folder);
- plug-ins (`plugins/`);
- workflows (`workflows/*.atlasflow`);
- the run history (`history.jsonl`), which on Linux is in the data folder.

Preview files, depth maps and workflow intermediates (`workflows/` in the cache folder, at most 5 GB) go in the cache folder.

| | Settings | Cache |
|---|---|---|
| Windows | `%APPDATA%\AtlasToolkit` | `%LOCALAPPDATA%\AtlasToolkit\cache` |
| Linux | `~/.config/atlas-toolkit`, data in `~/.local/share/atlas-toolkit` | `~/.cache/atlas-toolkit` |
| macOS | `~/Library/Application Support/AtlasToolkit` | `~/Library/Caches/AtlasToolkit` |

A folder named `AtlasToolkit-data` next to the program, the `.app` or the AppImage keeps everything there instead (portable mode).

## Building from source

[BUILDING.md](BUILDING.md) goes from a clean machine to a tested build on each OS. In short, and on Windows use `.venv\Scripts\python`:

```bash
python scripts/doctor.py                # is this machine ready? It prints the command that closes each gap
python scripts/setup_env.py             # .venv with the pinned packages, editable install
.venv/bin/python native/build.py        # the four C++ helpers into src/atlas_toolkit/bin (see native/README.md)
.venv/bin/python -m atlas_toolkit       # the app from source
.venv/bin/python scripts/dev/run_tests.py
.venv/bin/python scripts/build.py       # helpers, tests, the app into dist/, every check, publish
.venv/bin/python scripts/package.py     # the installers into dist/installers (PACKAGING.md)
```

## Developing

```bat
.venv\Scripts\python scripts\new_tool.py my_tool --kind svg|mesh|mesh2svg
.venv\Scripts\python scripts\sync_design.py <design-folder>   :: after the studio design system changes
.venv\Scripts\python scripts\dev\validate_<tool>.py           :: QA sheets and benchmarks, written to build\qa
```

Some tests and dev scripts compare against material outside this repository: the original Blender pieces,
the legacy scripts and the reference models. Point at it in `dev.local.toml` (copy `dev.local.example.toml`).
Without it those tests skip, saying why.

- `docs/ADDING_A_TOOL.md` explains how a tool is made.
- `docs/ARCHITECTURE.md` explains how the app fits together.
- `CHANGELOG.md` lists what changed.

The build needs the packages in `pyproject.toml`; embreex (fast ray casting for the Hatch Maker) is optional.
A build is staged in `build/release`, checked with every tool's self-test, a frozen start and a bundle audit, and only then
replaces what is in `dist/`, next to the Developable Surfaces helper's GPL source archive. A failed check leaves the published
app in place.

Third-party licences and the licence audit: [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

Fonts: Geist and Geist Mono (SIL OFL 1.1, `src/atlas_toolkit/ui/theme/fonts/OFL-LICENSE.txt`).
