<p align="center">
  <img src="docs/images/readme/logo.png" alt="Atlas Toolkit logo" width="112">
</p>

<h1 align="center">Atlas Toolkit</h1>

<p align="center"><em>From 3D models, photographs and terrain to ink on paper.</em></p>

<p align="center">
  <a href="https://github.com/atlas-toolkit-org/AtlasToolkit/actions/workflows/ci.yml?query=event%3Apull_request"><img src="https://github.com/atlas-toolkit-org/AtlasToolkit/actions/workflows/ci.yml/badge.svg?event=pull_request" alt="CI status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/licence-GPL--3.0--or--later-blue" alt="Licence: GPL-3.0-or-later"></a>
  <a href="https://ko-fi.com/atlas_toolkit"><img src="https://img.shields.io/badge/support-Ko--fi-ff5e5b" alt="Support on Ko-fi"></a>
</p>

<p align="center">
  <a href="#install">Install</a> &nbsp;·&nbsp;
  <a href="#tools">Tools</a> &nbsp;·&nbsp;
  <a href="docs/TOOLS.md">User guide</a> &nbsp;·&nbsp;
  <a href="CHANGELOG.md">Changelog</a>
</p>

<p align="center">
  <img src="docs/images/readme/hero.webp" alt="Four drawings made with Atlas Toolkit: a sketched bust, a twisted head in cross-hatching, a head peeling open like a plant, and a hatched photograph of a bridge" width="100%">
</p>

Atlas Toolkit is a desktop app for pen-plotter art. It gathers 48 tools for transforming and sculpting 3D models, turning models and
images into line drawings, and preparing those drawings for the plotter.

**Aim.** To take a drawing from first idea to a file the plotter can draw well, without leaving one app.

**Approach.** Every tool works the same way: a queue of files, a live preview, presets, and the same settings in the
window, on the command line and in visual Workflows that chain tools together. Randomness is always seeded, so a result
can be made again. Originals are never changed, and each output records the tool that made it.

**Focus.** Lines and physical paper. Drawings are measured in millimetres, hidden lines are really removed, and strokes
are ordered to cut pen travel. Transforming and sculpting tools exist to give those lines something worth drawing, and they export real
geometry you can also print.

<p align="center">
  <img src="docs/images/readme/app.webp" alt="The Atlas Toolkit window: a queue of models, a preview of the drawing and its settings" width="100%">
</p>

## Install

Installers are published on the [Releases](https://github.com/atlas-toolkit-org/AtlasToolkit/releases) page. The first release, 2.2.0, is being tested before
it goes out. Until then, `scripts/package.py` makes the same packages from source
([Building](docs/BUILDING.md), [Packaging](docs/PACKAGING.md)).

| System | Package | Guide |
|---|---|---|
| **Windows** 10 or 11, 64-bit | Setup `.exe` | [Install on Windows](docs/install/windows.md) |
| **Linux** x86-64, glibc 2.34 or later | `.deb`, `.rpm` or `.AppImage` | [Install on Linux](docs/install/linux.md) |
| **macOS** 15 or later, Apple Silicon (preview) | `.dmg` | [Install on macOS](docs/install/macos.md) |

The packages aren't code-signed yet, so Windows and macOS warn on first start; the guides show how to continue. The Mac
build passes automated tests but hasn't yet been tried by hand on a real Mac. [Platforms](docs/PLATFORMS.md) says how far
each system has been tested.

Almost everything works out of the box. Two optional extras unlock more: [Blender](https://www.blender.org/) 4.2 or
later for Melt and a few Remesh methods, and a [GPU depth runtime](docs/TOOLS.md#photo-hatching-and-the-gpu-runtime) for
Photo Hatching. The app's Setup page shows what is missing and how to add it.

<details>
<summary><strong>Run from source</strong></summary>

You need Python 3.12 or later and Git, plus CMake 3.24+, Ninja and a C++17 compiler for the native helpers.

```bash
git clone https://github.com/atlas-toolkit-org/AtlasToolkit.git
cd AtlasToolkit
python scripts/setup_env.py
```

Then, on Windows:

```bat
.venv\Scripts\python native\build.py
scripts\run.bat
```

On macOS and Linux:

```bash
.venv/bin/python native/build.py
scripts/run.sh
```

[Building from source](docs/TOOLS.md#building-from-source) covers standalone builds.
</details>

## Tools

A typical drawing moves left to right, and any stage can be skipped:

<p align="center"><strong>Image → Model → Transform/Sculpt → Draw → Refine → Plot</strong></p>

| Group | Tools | What it does |
|---|---:|---|
| [Draw from 3D](#draw-from-3d) | 6 | Turn a model into line art |
| [Draw from photographs](#draw-from-photographs) | 1 | Turn a photo into hatching |
| [From image to model](#from-image-to-model) | 2 | Raise images and terrain into 3D |
| [Transform and sculpt](#transform-and-sculpt) | 33 | Transform, modify or sculpt models, in six families |
| [Refine for the plotter](#refine-for-the-plotter) | 5 | Clean and shape finished SVGs |

Each example shows a result beside a drawing of it, made with the drawing tool that suits the effect best. The samples
are the heads of the Venus de Milo and Michelangelo's David, a photograph of a bridge and a terrain model.

### Draw from 3D

Each tool gives a model its own line language and writes an SVG sized for your paper, with hidden lines removed. Aim
the camera live and the drawing follows that view.

<table>
<tr>
<td width="50%" valign="top">
<img src="docs/images/readme/contour-lines.webp" alt="David drawn as contour lines" width="100%">

**Contour Lines**<br>
Horizontal slices seen through a camera. Set line spacing in millimetres; converging lines taper, and strokes chain for
few pen lifts. Sequences of stages share one frame.
</td>
<td width="50%" valign="top">
<img src="docs/images/readme/plotter-vision.webp" alt="Venus drawn as visible mesh edges" width="100%">

**Plotter Vision**<br>
The edges you can see through a perspective camera. A flat-angle setting moves it from every facet to main features,
with depth pens, outline and hidden-line pens, and red-blue stereo.
</td>
</tr>
<tr>
<td valign="top">
<img src="docs/images/readme/sketch-hatching.webp" alt="David as a pen sketch" width="100%">

**Sketch Hatching**<br>
An illustrator's pen sketch: lost-and-found outline, suggestive contours, creases and hatching that follows the form in
tone bands. Cast shadows, ambient occlusion and a portrait focus. Two pens.
</td>
<td valign="top">
<img src="docs/images/readme/stl-hatch-maker.webp" alt="Venus shaded with hatching" width="100%">

**Hatch Maker**<br>
Shades a model from any view in seven styles, among them engraving, contours, crosshatch and stipple. Outlines and
creases, one pen per layer, ordered to cut pen travel.
</td>
</tr>
<tr>
<td valign="top">
<img src="docs/images/readme/stipple.webp" alt="David drawn in dots" width="100%">

**Stipple**<br>
Dots only: blue-noise stippling of ray-cast light and shade. Keep highlights empty, darken edges in place of an outline,
and set a dot budget.
</td>
<td valign="top">
<img src="docs/images/readme/flowrep-sketch.webp" alt="Venus as a sparse network of curves" width="100%">

**FlowRep Sketch**<br>
A sparse network of curvature-aligned curves that describe a form, after FlowRep (Gori et al., 2017). Descriptiveness
sets how many remain, and an optional silhouette closes the figure.
</td>
</tr>
</table>

### Draw from photographs

Photo Hatching reads a photograph's depth, so its lines can follow the forms in the picture.

<table>
<tr>
<td width="50%" valign="top">
<img src="docs/images/readme/dpt-hatching.webp" alt="A photograph of a bridge beside its engraving-style hatching" width="100%">

**Photo Hatching**<br>
Engraving-style strokes guided by a depth transformer. Ink density follows tone, crossing layers gather in the shadows
and outlines mark occlusions. Also draws mosaics and depth contours. Runs on the GPU, or from your own depth map.
</td>
<td width="50%"></td>
</tr>
</table>

### From image to model

These tools raise a flat source into real relief, ready to other transformations, drawing or printing. Both make printable solids or open
surfaces for drawing, and share one way of sizing the result on the plate.

<table>
<tr>
<td width="50%" valign="top">
<img src="docs/images/readme/image-to-mesh.webp" alt="A photograph raised into relief and drawn in contours" width="100%">

**[Image to Mesh](docs/IMAGE_TO_MESH.md)**<br>
Photos, illustrations and heightmaps become smooth, faceted or terraced relief. Choose a channel, adjust tone and levels,
and keep transparent silhouettes.
</td>
<td width="50%" valign="top">
<img src="docs/images/readme/raster-dem-to-mesh.webp" alt="A terrain model and its contour drawing" width="100%">

**[Raster DEM to Mesh](docs/RASTER_DEM_TO_MESH.md)**<br>
Geographic elevation data becomes faithful, smooth, faceted or terraced terrain. Handles coordinate systems, metric
scaling, masks and adaptive detail. No GIS software needed.
</td>
</tr>
</table>

### Transform and sculpt

Thirty-three tools transform a model and return real geometry as STL, OBJ or PLY, in the model's own units. Many can also
save a sequence of stages, which the drawing tools frame alike for a series of plates.

#### Prepare and cut

Get a model ready, or choose the part of it to keep.

<table>
<tr>
<td width="50%" valign="top">
<img src="docs/images/readme/remesh-decimate.webp" alt="Venus rebuilt as flowing quads" width="100%">

**[Remesh/Decimate](docs/REMESH_DECIMATE.md)**<br>
Lightens scans or rebuilds their topology: quadric decimation, even or adaptive triangles, flowing quads, directional
triangles, dual cells and voxels, with regional detail.
</td>
<td width="50%" valign="top">
<img src="docs/images/readme/plane-cut.webp" alt="David trimmed by a plane" width="100%">

**[Plane Cut](docs/PLANE_CUT.md)**<br>
Trims or splits a mesh with a plane you draw, then refine with arrows, rings or numbers. Cap the cut or leave it open;
keep one side or both.
</td>
</tr>
<tr>
<td valign="top">
<img src="docs/images/readme/mesh-mirror.webp" alt="Venus beside a mirrored copy" width="100%">

**[Mirror](docs/MIRROR.md)**<br>
Reflects a model through a plane you position. Keep the original or only the reflection, as one file or separate files.
</td>
<td></td>
</tr>
</table>

#### Deform

Change a model's pose and mass while its surface stays whole.

<table>
<tr>
<td width="50%" valign="top">
<img src="docs/images/readme/mesh-bend.webp" alt="Venus curved into an arc" width="100%">

**[Bend](docs/BEND.md)**<br>
Curves a model into arcs, S-bends and spatial waves. Set angle, direction, range, anchors, sweep and roll, or drag the
gesture on a spine. Local fold protection keeps it clean.
</td>
<td width="50%" valign="top">
<img src="docs/images/readme/mesh-twist.webp" alt="David wrung into a twist" width="100%">

**Twist**<br>
Wrings a model with undulating torsion about its axis, an S-sway and a spiral ripple. One stage, or a sequence from
intact to full.
</td>
</tr>
<tr>
<td valign="top">
<img src="docs/images/readme/mesh-flow.webp" alt="Venus swept by turbulent currents" width="100%">

**Flow Warp**<br>
Pulls a model through turbulent currents, so the surface folds without tearing. Control eddies, a vortex and a streaming
pull, across the whole model, a band or a sphere.
</td>
<td valign="top">
<img src="docs/images/readme/mesh-melt.webp" alt="David melting, with drips and a pool at its base" width="100%">

**Melt**<br>
Softens a model like ice cream or molten metal: sag, a pool at the base, drips and rivulets, up to dissolving. Generic or
head-aware. Runs in Blender, in the background.
</td>
</tr>
<tr>
<td valign="top">
<img src="docs/images/readme/mesh-dilate.webp" alt="Venus inflated into rounded volumes" width="100%">

**Dilate**<br>
Inflates a model with pressure, or swells a feathered region into a bulge. Softness, roundness, retained relief,
direction, anchors and seeded lobes shape the volume.
</td>
<td valign="top">
<img src="docs/images/readme/mesh-compress.webp" alt="David squashed in a press" width="100%">

**Compress**<br>
Squashes a model between plates, in a tube or from every side, or closes it in a cube, sphere, cone or other solid mould.
Elastic or plastic skin, stiffness, pressure and friction.
</td>
</tr>
<tr>
<td valign="top">
<img src="docs/images/readme/mesh-motion.webp" alt="Venus swept into layered wakes" width="100%">

**[Motion Sculpture](docs/MOTION_SCULPTURE.md)**<br>
Stretches a still model into swept laminae or splintered bars. Stream, Pulse, Gust and Vortex fields draw tapered wakes
behind a retained silhouette, over a region you can paint.
</td>
<td valign="top">
<img src="docs/images/readme/mesh-developable.webp" alt="David evolved into flowing folded surfaces" width="100%">

**[Developable Surfaces](docs/DEVELOPABLE_SURFACES.md)**<br>
Evolves a mesh into nearly developable pieces with flowing seams and straight rulings, after Stein, Grinspun and Crane
(2018). From recognisable sculpture to abstract folds.
</td>
</tr>
</table>

#### Surface and relief

Give the skin something for the lines to describe.

<table>
<tr>
<td width="50%" valign="top">
<img src="docs/images/readme/mesh-texture.webp" alt="Venus covered in raised cobbles" width="100%">

**[Surface Texture](docs/SURFACE_TEXTURE.md)**<br>
Real raised or engraved relief from twelve bundled heightmaps, from scales to cobbles, or your own. Surface-following,
cylindrical or triplanar mapping, in feathered regions.
</td>
<td width="50%" valign="top">
<img src="docs/images/readme/mesh-stripes.webp" alt="David covered in flowing raised bands" width="100%">

**[Surface Stripes](docs/SURFACE_STRIPES.md)**<br>
Flowing bands fused to the body or exported as closed ribbons alone, after Knöppel et al. (2015). Set spacing, width,
thickness, rotation and crossing families.
</td>
</tr>
<tr>
<td valign="top">
<img src="docs/images/readme/mesh-growth.webp" alt="Venus covered in coral ridges" width="100%">

**Reaction-Diffusion Growth**<br>
Grows a pattern over the surface and raises or engraves it: coral, fingerprints, mazes, cells, pebbles, lichen. Scale
holds across models, and a second pattern can take over up the height.
</td>
<td valign="top">
<img src="docs/images/readme/mesh-ribbon.webp" alt="David wrapped in a bandage" width="100%">

**Ribbon Wrap**<br>
Wraps a model in silk, satin, velvet, cotton or gauze: a spiral bandage, parcel bands or a loose drape. Free tails fall
outside the form, with threads, weave and ripples as geometry.
</td>
</tr>
</table>

#### Carve and fill

Open a model up, add to it, or rebuild its volume.

<table>
<tr>
<td width="50%" valign="top">
<img src="docs/images/readme/mesh-holes.webp" alt="Venus pierced like Swiss cheese" width="100%">

**Holes**<br>
Pierces a model with tubes, bores, bubbles or wormholes, in lattices, radial spokes or at random. One Irregularity slider
runs from a regular perforation to chaos.
</td>
<td width="50%" valign="top">
<img src="docs/images/readme/mesh-rods.webp" alt="David bristling with spikes" width="100%">

**Rods**<br>
The mirror of Holes: adds rods, hollow pipes, drooping cables or spikes, through the model or growing out of it. Width,
reach, end shapes and collars.
</td>
</tr>
<tr>
<td valign="top">
<img src="docs/images/readme/mesh-porous.webp" alt="Venus as a porous labyrinth" width="100%">

**Porous Forms**<br>
A continuous labyrinth inside the silhouette: gyroid, diamond, Schwarz P or Neovius walls or struts, or seeded Voronoi
cells. Keep a solid core or an intact face.
</td>
<td valign="top">
<img src="docs/images/readme/pipe-fill.webp" alt="David replaced by organic tubes" width="100%">

**[Pipe Fill](docs/PIPE_FILL.md)**<br>
Replaces a model with tubes through its interior: organic curves or orthogonal pipes with rounded elbows, as strands or
closed loops, solid or hollow.
</td>
</tr>
<tr>
<td valign="top">
<img src="docs/images/readme/voxelize-fill.webp" alt="Venus filled with blocks and cut away" width="100%">

**[Voxelize Fill](docs/VOXELIZE_FILL.md)**<br>
Fills a model with cubes, adaptive blocks or directional bars. Cut away a side, separate layers, expose depth bands or
erode a seeded front.
</td>
<td valign="top">
<img src="docs/images/readme/voxelizer.webp" alt="David rebuilt from small cubes" width="100%">

**Voxelizer**<br>
Rebuilds a model from cubes, spheres or cylinders on a grid, as a shell or filled.
</td>
</tr>
<tr>
<td valign="top">
<img src="docs/images/readme/mesh-stairs.webp" alt="Venus hollowed out around flights of stairs, landings and arches" width="100%">

**[Stair Sculpt](docs/STAIR_SCULPT.md)**<br>
Turns a model into a staircase: linear, turning or helical. Carve a cavity on a side or where you paint and build one
stair or an impossible architecture inside it, or make the whole body the treads or a flight cut into it.
</td>
<td valign="top">
<img src="docs/images/readme/mesh-skeleton.webp" alt="David hollowed into a see-through net of metal straps" width="100%">

**[Skeleton Shell](docs/SKELETON_SHELL.md)**<br>
Empties a model and keeps only a structure on its skin: wire cells, a triangle net, flowing strands, airframe ribs or a cut
plate, in up to two layers. Keep part of the skin solid, or fuse it all to print.
</td>
</tr>
</table>

#### Fragment and separate

Take a model apart, and leave the parts in the air.

<table>
<tr>
<td width="50%" valign="top">
<img src="docs/images/readme/mesh-shatter.webp" alt="Venus blown into shards" width="100%">

**Mesh Shatter**<br>
Breaks the surface into shards along its creases and blows them apart. Can tie them to a central hub with stems or a branching tree.
</td>
<td width="50%" valign="top">
<img src="docs/images/readme/mesh-disintegrate.webp" alt="David dissolving into loose triangles" width="100%">

**Disintegrate**<br>
Subdivides a model and moves every triangle's corners until the surface comes apart. Choose strength, growth and
direction, as a final form or a progression.
</td>
</tr>
<tr>
<td valign="top">
<img src="docs/images/readme/mesh-surface-eject.webp" alt="Pieces of Venus floating out of matching cavities" width="100%">

**[Surface Eject](docs/SURFACE_EJECT.md)**<br>
Lifts real pieces of the surface out of matching cavities, cut as cubes, boxes, spheres, ellipsoids or cylinders. Paint
the region, and join fragments back with bars or organic branches.
</td>
<td valign="top">
<img src="docs/images/readme/mesh-peel.webp" alt="David's skin peeling open in petals" width="100%">

**Surface Peel**<br>
Cuts the skin into petals or scales and peels each back about its root: unroll, open, curl. Nested layers make a rose of
heads.
</td>
</tr>
<tr>
<td valign="top">
<img src="docs/images/readme/mesh-strata.webp" alt="Venus cut into floating slabs" width="100%">

**[Strata Sculpt](docs/STRATA_SCULPT.md)**<br>
Cuts a model into capped slabs and dislocates them: lifted wafers, a sideways wave, a spiral turn, seeded slips. Optional
supports fuse neighbouring slabs.
</td>
<td valign="top">
<img src="docs/images/readme/cube-grid-slicer.webp" alt="David cut into separated blocks" width="100%">

**Grid Slicer**<br>
Cuts a model into a grid of capped blocks and pushes them apart. Optionally shuffles them and shifts them at random.
</td>
</tr>
<tr>
<td valign="top">
<img src="docs/images/readme/mesh-crumble.webp" alt="Venus with one side breaking into blocks that fall and heap up below it" width="100%">

**[Crumble](docs/CRUMBLE.md)**<br>
Part of a model breaks into solid blocks that collapse under gravity with rigid-body physics, leaving a hollow. On a side,
a patch or where you paint, frozen at any moment from first cracks to settled rubble.
</td>
<td></td>
</tr>
</table>

#### Multiply and combine

Compose one model into many, or two models into one.

<table>
<tr>
<td width="50%" valign="top">
<img src="docs/images/readme/cubic-replicator.webp" alt="A grid of turning copies of Venus" width="100%">

**Cubic Replicator**<br>
Tiles copies of a model on a 3D grid, mirrored and turned at random.
</td>
<td width="50%" valign="top">
<img src="docs/images/readme/form-replicator.webp" alt="Copies of David arranged in a star" width="100%">

**[Form Replicator](docs/FORM_REPLICATOR.md)**<br>
Arranges copies into crosses, stars, clocks, rosettes, urchins, spirals, helices and knots. Aim, stretch, taper and roll
them; copies that touch fuse into solids.
</td>
</tr>
<tr>
<td valign="top">
<img src="docs/images/readme/mesh-fusion.webp" alt="A hybrid halfway between Venus and David" width="100%">

**Shape Fusion**<br>
Morphs a model into another model, itself or a built-in shape, or fuses, carves and intersects them. Holes open and pieces
join, and the change can sweep across as a soft front.
</td>
<td valign="top">
<img src="docs/images/readme/mesh-dual.webp" alt="David and Venus merging into one sculpture" width="100%">

**Dual Fusion**<br>
Two models become one fused shape or sculpture: converging, emerging, split along a seam, interleaved, bridged by strands, crumbling,
echoed, patchworked or cocooned.
</td>
</tr>
</table>

### Refine for the plotter

The last step before the pen. These tools work on any SVG, in millimetres, and keep its layers, ids, styles and
transforms.

| Tool | What it does |
|---|---|
| **Overlap Cleaner** | Cuts ink drawn twice, so every line is plotted once. Optionally joins stroke ends and removes whole duplicates. |
| **[Line Smooth](docs/LINE_SMOOTH.md)** | Removes sawtooth and jagged linework with curvature smoothing, within limits on displacement, corners and topology. |
| **[Line Simplify](docs/LINE_SIMPLIFY.md)** | Reduces vertices within a physical error limit, protecting corners, crossings and enclosed shapes. |
| **Shape Cutter** | Keeps the lines inside or outside a closed shape, taken from a layer, a named element or another SVG. |
| **Shape Outline** | Draws one closed outline around a drawing: an envelope, a concave or a convex hull, with a margin in millimetres. |

## Going further

- **[Tools and user guide](docs/TOOLS.md):** every tool in depth, presets, shortcuts, the files the app keeps.
- **[Workflows](docs/TOOLS.md#workflows):** chain tools into one visual pipeline and run it over many files.
- **[Command line](docs/TOOLS.md#command-line):** the same tools and settings, for scripts and batches.
- **[Building](docs/BUILDING.md)**, **[packaging](docs/PACKAGING.md)** and **[adding a tool](docs/ADDING_A_TOOL.md)**.
- **[Platforms](docs/PLATFORMS.md):** what has really been proven on each system.
- **[Changelog](CHANGELOG.md):** what changed, version by version.

## Support

Atlas Toolkit is free and made independently by sci.co.plot. On [Ko-fi](https://ko-fi.com/atlas_toolkit) you can
donate, buy the official preset packs, or join for early-access builds. News and downloads are on
[atlas-toolkit.org](https://atlas-toolkit.org).

## Licence

Copyright © 2026 João Gonçalves (sci.co.plot) and contributors. Atlas Toolkit is free software under the
[GNU General Public License, version 3 or later](LICENSE) (GPL-3.0-or-later); see [COPYRIGHT.md](COPYRIGHT.md).
Third-party components keep their own licences, listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Preset
packs are not part of the program: each carries its own licence ([presets](docs/PRESETS.md)). The name and logo are
covered by [TRADEMARKS.md](TRADEMARKS.md), and [CONTRIBUTING.md](CONTRIBUTING.md) explains how to contribute.
