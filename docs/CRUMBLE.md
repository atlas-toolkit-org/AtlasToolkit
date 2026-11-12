# Crumble 0.1.0

Crumble makes a closed model come down like a building. Part of it breaks into solid blocks, the body keeps a jagged
hollow where they were, and the blocks fall under gravity, striking each other, the body and the floor. You choose the
moment to freeze. Lengths are % of the model's height, and the result stays in the model's own units and place.

## Where it breaks

**Area** is one of three:

- **Side:** **Slice** takes everything on that side, straight across the model (**Coverage** says how far), and **Patch**
  makes a round crater where a line from that side first meets the model. **Turn side by** gives a three-quarter break.
  Sides follow the model's frame: if the wrong side breaks, set **Front** under Model.
- **Paint:** open the Selection tab and paint what breaks, in the same editor as Surface Eject and Stair Sculpt. The
  selection is tied to this exact mesh. In Workflows use Aim in tool, paint, then Back to workflow.
- **All:** the whole model breaks up. With **Hollow depth** at 100, nothing stays standing.

**Hollow depth** decides how deep the break goes: a shallow scar, a deep cavity, or everything. **Edge roughness** makes
the edge of the area wander.

## The blocks

**Style** gives faceted **Rock**, long **Shards**, flat **Slabs** or staggered **Bricks**. **Block size** counts most:
half the size makes eight times the blocks, and the time grows with them. **Variety** makes blocks uneven and their
order random. **Rubble** breaks a share of blocks again, mostly near the edge. **Chipped corners** knocks their corners
off.

Blocks are real closed solids, cut with exact booleans, so each keeps its piece of the model's surface on the outside
and flat broken faces on the inside. **Cracks** spread beyond the area as tapered slots in the surface: **Crack reach**
says how far, **Crack width** how wide, and 0 turns them off.

## The collapse

**Collapse** is the moment shown, and it is the setting to try first. At 0 the model is cracked and nothing has moved,
around 50 it is mid-fall with the first blocks already landed, and at 100 the simulation runs until everything is still.
The same seed gives the same collapse, so moving this slider shows the same event at another moment.

| Setting | What it does |
|---|---|
| **Failure order** | Which blocks let go first. Undermined is the demolition look: the lower blocks go and the rest follows. |
| **Stagger** | Seconds between the first block and the last letting go. At 0 they drop together, as one mass. |
| **Clinging blocks** | The share that never falls, nearest the edge first. **Loosened** tilts them out of place. |
| **Gravity** | 1 is as in life for a model 2 m tall. Near 0, blocks hang in the air around the model. |
| **Burst** | The push as they let go. Without it, many blocks stay in their own crater. |
| **Pull outward** | A steady sideways pull, for blocks streaming away in one direction. |
| **Tumble** | How fast blocks start turning. |
| **Friction** | Low spreads the rubble wide; high keeps steep heaps. |
| **Floor** | Where rubble lands. Off, blocks fall out of the picture and those far below are left out. |

## Output

By default the file holds **loose closed solids**: every block and the standing body, exactly where the collapse left
them, some in mid-air. That suits Plotter Vision, Contour Lines, hatching and renders.

**Fuse into one solid** makes one closed solid for printing. Blocks swell a little to weld where they touch, and thin
**bridges** reach whatever still floats, up to **Bridge reach**. Anything farther is left out and reported. **Add a base
plate** puts a slab under it all, sized for the heap, and with Fuse on, blocks that rolled off the plate are left out.
Printable Ruin combines these.

**Also save every block** writes each block and the standing body to its own file.

The input must be a closed solid. **Repair by resampling** can close an open one, softening its detail. To paint a
repaired model, save it first and paint that.

## Presets

| Preset | Shows |
|---|---|
| **Mid-Collapse** | One side coming down: rubble at the foot, blocks still falling. The default. |
| **Rock Cluster** | Faceted rocks swell away from a calm surface and hang there, nearly weightless. |
| **Temple Crater** | A shell breaks open on one spot, with cracks across the surface. |
| **Brick Breakaway** | Masonry courses peel off and drift away. |
| **Landslide** | A flank has come down and settled in a heap below a deep scar. |
| **Demolition** | The whole model goes from the bottom up. |
| **First Cracks** | The start: cracks everywhere and the first few blocks falling. |
| **Shard Rain** | Long splinters fall tumbling, a few at a time. |
| **Slab Slide** | Flat plates shear off and skid over one another. |
| **Printable Ruin** | Settled and fused to a base plate: one closed solid. |

## Time and limits

On a head with a few hundred blocks, a mid-collapse takes about 5-20 s and a settled heap of 700-900 blocks 30-120 s,
because every block ends up touching others. Preview uses a coarser collision grid, so blocks may land slightly
differently from the final run. The solver is built for a convincing look: blocks collide as convex hulls, so a heap can
show small overlaps, and blocks that have come to rest stop being simulated. **Maximum blocks** stops a run that would
take too long; rubble simply stops growing at that limit.

## Drawing it

Plotter Vision draws the broken faces best: its flat-angle filter keeps the crisp block edges and drops mesh noise. With
Contour Lines, loose blocks give many short closed loops, so a larger Block size reads better.
