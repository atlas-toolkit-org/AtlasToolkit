# Skeleton Shell

Skeleton Shell empties a model and keeps only a structure on its skin: a wire or plate sculpture that you can see through
to the far side. It reads the model as a surface, so the input need not be closed, and nothing is built inside except the
frames of a ribbed structure, which reach in from the skin.

Porous Forms fills a volume with a labyrinth. This tool is a single layer of metal around nothing, and that is what makes
a drawing of it read as depth: the near wires cross the far ones.

Output is `<name>_skeleton.stl` (or OBJ, PLY), signed `atlas-toolkit:mesh-skeleton`, in the model's own units.

## Structures

| Structure | What it makes | Best for |
|---|---|---|
| **Wire Cells** | Geodesic Voronoi walls on the skin: an even honeycomb, a biomorphic net or a cast, bony lace | The strongest figure drawing: the form stays legible |
| **Trimesh** | The same seeds joined as triangles, either straight rods or following the surface | Faceted, geodesic or hand-welded looks |
| **Flow Strands** | Long curves along the form's curvature, upright, or as spirals of two hands | Gesture and movement, with few lines |
| **Ribs & Stringers** | Stations as flat ring frames with lightening holes, or as straps, fins or rods, tied by stringers and a keel | An old airframe or an upturned hull |
| **Cut Plate** | A sheet of skin pierced with polygons or triangles, sharp or rounded | Bold, graphic line art with white bands |

## Settings that matter

All sizes are % of the model's height, so a setting means the same on any model.

- **Cell size** sets the scale. 5-9% keeps a figure legible and lets the far side show; under 3% it reads as tone.
- **Regularity** runs from scattered seeds (0) to an even, near-hexagonal packing (1). **Straightness** runs from wires
  that curve with the surface (0) to straight rods (1). **Cell stretch** draws cells out along the height, into slashes
  above about 2. **Finer on detail** makes cells smaller on the face, hands and feet.
- **Profile** is how a wire draws. See [Drawing it](#drawing-it).
- **Joint swell**, **Thickness variation** and **Taper** give a cast or organic line. **Hand wobble** and **Overrun** give
  hand-bent, tied or welded wire. Joints stay together under wobble; Overrun deliberately runs the ends past them.
- **Features first** (Flow Strands) gives the strongest features the first strands and the thickest wire. With a large Cell
  size, only rods along the main lines remain.
- **Band width** and **Rounding** (Cut Plate) set the metal left between openings and how rounded they are. Rounding keeps
  the same share of metal.
- **Frame depth**, **Lightening holes** and **Keel** apply to flat frames. Narrow sections always keep an open middle.

## Two layers and solid regions

**Second layer** adds another structure with its own size, thickness and profile, such as fine cells carried on heavy
frames. It shares the other settings with the first layer.

**Acts on** chooses where the model is hollowed; the rest keeps its skin as a thin plate, still empty behind. Across the
**Feather**, each cell lets go at its own seeded moment, so the skin breaks up along real cell edges. **Wire along the edge**
finishes the border with a wire.

## Printing

Off by default, wires are separate overlapping closed solids: fast, and fine for drawing. **Fuse into one solid** merges
them into one closed solid. Parts that don't touch stay separate: single-family strands usually stay in many pieces, so
add **Second family** or a second layer that ties them. Wires thinner than about 0.5% of the height may not survive a
small print.

## Drawing it

Draw with **Plotter Vision** with **Clean** and a flat angle near 25°. The profile decides how a wire looks on paper:

- **Strap** draws as two clean edges with white between, and keeps a face most legible. It is the default.
- **Round with 4 sides** is an open, see-through bar.
- **Above 8 sides** a rod looks round in a render, but its many edges draw as dark marks. Keep it low for plotting.

If a drawing goes dark, raise Cell size first, then lower Finer on detail. Contour Lines suits this geometry less: it
slices each wire into small fragments.

## Presets

| Preset | Look |
|---|---|
| Wire Dancer | Biomorphic strap net, finer on the face and hands |
| Honeycomb Cage | Large, even cells of straight bar |
| Bone Lace | Curved walls that thicken into their joints |
| Bent Rod Figure | Straight bars in loose triangles, running past their joints |
| Geodesic Shell | An even triangle net |
| Facet Net | Crisp flat triangles, denser where the form turns |
| Spiral Weave | Two hands of long spirals, crossing up the form |
| Windswept Strands | One hand of tapering strands, blown off course |
| Feature Rods | A few heavy rods along the main forms |
| Form Net | Lines down and around the form |
| Bird's Nest | Thin wire, hand-laid in every direction |
| Airframe | Flat formers with lightening holes, stringers and a keel |
| Ship's Ribs | Close flat ribs across the model, with planks along it |
| Hoop and Bar | Hand-bent hoops and upright bars |
| Laser-Cut Sheet | A sheet cut through with long triangular slashes |
| Bony Web | A sheet with rounded openings and thick joints |
| Struts and Lace | Heavy flat frames carrying a net of cells |
| Skin Letting Go | A thin upper shell breaking up, cell by cell, into wire |

## How it works

The skin is copied into an even working mesh, with long edges split so it stays conforming. Seeds spread over it by Lloyd
iterations on its edge graph, and multi-source Dijkstra gives each vertex its nearest seed. Cell walls are traced where
two seeds' distances meet, and the dual pairs give the triangles. Strands walk a line field exactly across the triangles;
the field comes from principal curvatures (Rusinkiewicz 2004) held as tensors, which have no sign to flip. A voxel grid
per surface direction spaces them. Ribs are level curves of height or of geodesic distance, and flat frames cut each
section's ring with Manifold. Cut plates clip the skin where it is within half a band of the walls, then thicken it inward.
Wires are swept with their outer face on the skin, so the silhouette holds.

The result is seeded. Preview uses a coarser working skin, so cells may fall a little differently from Run.
