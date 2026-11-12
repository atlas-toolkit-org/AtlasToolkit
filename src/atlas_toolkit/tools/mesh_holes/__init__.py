"""
Holes: carve a closed model with tubes, blind bores, bubbles or wormholes, placed on a lattice, radially, at random
or over its surface, from a regular perforation to a cheese.

Outputs <name>_holes.stl, signed atlas-toolkit:mesh-holes.
"""
from atlas_toolkit.core import (Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, Vec3, frame_params,
                                influence_params)


def get(v, key, default):
    """A form value for a visibility rule: the default when it is missing or None (a segmented choice with nothing
    picked yet, a page still being built)."""
    x = v.get(key)
    return default if x is None else x


def num(v, key, default=0.0) -> float:
    try:
        return float(get(v, key, default))
    except (TypeError, ValueError):
        return default


def fam(*f):
    return lambda v: get(v, "family", "bubble") in f


def lay(*m):
    return lambda v: get(v, "layout", "random") in m


def lines(v):
    return get(v, "family", "bubble") != "bubble"


def ranged(a, b):
    return lambda v: num(v, b) > num(v, a)


def uses_axes(v):
    return lay("lattice")(v) or (lay("random", "surface")(v) and get(v, "aim", "normal") == "axes")


def uses_frame(v):
    return lay("lattice", "radial")(v) or get(v, "aim", "normal") in ("axes", "centre")


def uses_centre(v):
    return lay("radial")(v) or (lay("random", "surface")(v) and get(v, "aim", "normal") == "centre")


def dropping(v):
    return get(v, "keep", "all") == "drop"


TOOL = Tool(
    id="mesh-holes",
    name="Holes",
    version="1.0.2",
    category="mesh",
    icon="holes",
    action="Pierce",
    summary="Pierce a model with tubes, bores, bubbles or wormholes: from a regular perforation to a cheese.",
    tagline="Through-holes on a lattice, spokes around the head, drill holes over the skin, wormholes or cheese "
            "bubbles: an exact cut, crisp rims, as a closed mesh for Contour Lines, Plotter Vision or Sketch "
            "Hatching.",
    description=(
        "Solid cutters are placed in or around the model and taken away from it with an exact boolean "
        "(manifold3d), so the rims stay sharp and the rest of the model untouched. Hole types: through tubes "
        "(straight, crossing the whole model), blind bores (drilled in from the surface to a depth, with a flat, "
        "round or pointed end), bubbles (closed voids that open as craters where they meet the skin) and "
        "wormholes (wandering, swelling tunnels). Layouts: a lattice (lines along one to three axes, square or hex, "
        "woven so the families never meet; a cubic or close-packed grid for bubbles), radial (spokes around an "
        "axis or from a point; rings or shells of bubbles), random inside the model, or spread over its surface. "
        "Profiles: circle, polygon, pillow (superellipse) or a seeded blob, flower to cloud. Sizes are % of the "
        "model's height, fixed or drawn from a range. Irregularity moves the whole thing from order to chaos with "
        "the same seed: positions, directions and shapes drift together. A minimum wall keeps holes apart "
        "(shrinking them to fit), 0 lets them merge into caverns. Voids that stay inside the model are kept: they "
        "only show in slices. The model should be closed; an open one is closed first by resampling it."),
    inputs=Inputs("mesh", exclude=(r"(?i)_holes\.(stl|obj|ply)$",)),
    output=Output("{stem}_holes.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-holes",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("holes", "removed", "faces"),
    params=[
        Section(
            "Holes",
            Choice("family", "Hole type", "bubble", [("tube", "Through tubes"), ("bore", "Blind bores"),
                                                     ("bubble", "Bubbles"), ("worm", "Wormholes")],
                   help="Through tubes cross the whole model. Blind bores are drilled in from the surface to a "
                        "depth. Bubbles are closed voids, craters where they reach the skin (cheese). Wormholes "
                        "are tunnels that wander and swell."),
            Choice("layout", "Layout", "random", [("lattice", "Lattice"), ("radial", "Radial"),
                                                  ("random", "Random"), ("surface", "Surface")],
                   help="Lattice: a regular grid. Radial: spokes around an axis or from a point (bubbles: rings or "
                        "shells). Random: scattered inside the model. Surface: spread evenly over its skin."),
            Float("size", "Size", 4.0, unit="% h", min=0.2, max=80.0, step=0.5, decimals=1,
                  help="Hole width (the diameter of a circle of the same area; bubbles: of a sphere of the same "
                       "volume), % of the model's height. Set 'up to' above it for a random size per hole."),
            Float("size_max", "up to", 14.0, unit="% h", min=0.2, max=80.0, step=0.5, decimals=1,
                  help="Largest size; equal to Size for one fixed size."),
            Choice("size_mix", "Sizes", "small", [("even", "Even"), ("small", "Mostly small"),
                                                  ("large", "Mostly large")],
                   visible_if=ranged("size", "size_max"),
                   help="How sizes spread over the range: evenly, or many small holes and a few large ones (the "
                        "natural look of cheese and pumice), or the reverse."),
            Float("depth", "Depth", 8.0, unit="% h", min=0.5, max=100.0, step=1.0, decimals=1,
                  visible_if=fam("bore"), help="How deep a bore goes below the surface, % of the height."),
            Float("depth_max", "up to", 16.0, unit="% h", min=0.5, max=100.0, step=1.0, decimals=1,
                  visible_if=fam("bore"), help="Deepest bore; equal to Depth for one depth."),
            Choice("tip", "Bore end", "round", [("flat", "Flat"), ("round", "Round"), ("pointed", "Pointed")],
                   segmented=True, visible_if=fam("bore"),
                   help="The bottom of a bore: flat (a mill), round (a ball end) or pointed (a drill)."),
            Float("length", "Length", 40.0, unit="% h", min=2.0, max=400.0, step=5.0, decimals=0,
                  visible_if=lambda v: fam("worm")(v) and lay("random", "surface")(v),
                  help="How far a wormhole runs, % of the height (on a lattice or radially they cross the model)."),
            Float("length_max", "up to", 90.0, unit="% h", min=2.0, max=400.0, step=5.0, decimals=0,
                  visible_if=lambda v: fam("worm")(v) and lay("random", "surface")(v)),
            Float("sink", "Sink", 0.25, unit="× size", min=-0.9, max=0.9, step=0.05, decimals=2,
                  visible_if=lambda v: fam("bubble")(v) and lay("surface")(v),
                  help="How far below the skin a surface bubble's centre sits, in its own radius: 0 a half-sphere "
                       "crater, 0.3-0.6 a deep pocket with a narrower mouth, negative a shallow dish."),
            Float("gap", "Minimum wall", 1.0, unit="% h", min=0.0, max=20.0, step=0.1, decimals=2,
                  help="The thinnest wall left between two holes, % of the height: holes that would come closer "
                       "shrink (down to Size) or are left out, biggest first. 0 lets holes overlap and merge into "
                       "caverns. Long holes in random directions (tubes, wormholes) nearly always pass close to "
                       "each other, so most are left out with a wall: use 0 for them."),
            Seed("seed", "Seed", 1, help="Where random holes fall and how every hole varies."),
        ),
        Section(
            "Placement",
            Choice("axes", "Axes", "y", [("x", "X"), ("y", "Y"), ("z", "Z"), ("xy", "XY"), ("xz", "XZ"),
                                         ("yz", "YZ"), ("xyz", "XYZ")], segmented=True, visible_if=uses_axes,
                   help="Directions of a lattice's holes (bubbles: their stretch), in the model's frame: X across, "
                        "Y front to back (seen face on, they read as openings), Z up."),
            Float("spacing", "Spacing", 12.0, unit="% h", min=1.0, max=100.0, step=0.5, decimals=1,
                  visible_if=lay("lattice", "radial"),
                  help="Distance between neighbouring lines or points of the lattice, rows of a radial layout and "
                       "rings of radial bubbles, % of the height."),
            Choice("grid", "Grid", "square", [("square", "Square"), ("hex", "Hex")], segmented=True,
                   visible_if=lay("lattice", "radial"),
                   help="Square, or hex: rows offset by half a step (a honeycomb of tubes, close-packed bubbles, "
                        "radial rows turned half a spoke)."),
            Bool("weave", "Weave the axes", True,
                 visible_if=lambda v: lay("lattice")(v) and lines(v) and len(get(v, "axes", "y")) > 1,
                 help="Offset each direction's lines by half a step so they pass each other (a woven block) "
                      "instead of crossing in big nodes."),
            Bool("both_sides", "From both sides", True, visible_if=lambda v: fam("bore")(v) and lay("lattice")(v),
                 help="Drill each lattice line from both ends, not only from its first side."),
            Choice("centre_mode", "Around", "axis", [("axis", "An axis"), ("point", "A point")], segmented=True,
                   visible_if=uses_centre,
                   help="Radial holes go around a vertical axis (rows of spokes, rings of bubbles) or out from a "
                        "point (a burst of spokes, shells of bubbles)."),
            Vec3("radial_centre", "Centre", (0.0, 0.0, 0.0), unit="%", min=-100.0, max=100.0, step=5.0, decimals=1,
                 visible_if=uses_centre,
                 help="From the model's centre, % of its size along X (across), Y (depth, front is −) and Z (up)."),
            Int("spokes", "Spokes", 12, min=1, max=96, visible_if=lambda v: lay("radial")(v) and lines(v),
                help="Holes per row around the axis, or in all from the point. Through tubes cross the centre, so "
                     "each makes two openings."),
            Float("spiral", "Spiral", 0.0, unit="° / row", min=-180.0, max=180.0, step=2.5, decimals=1,
                  visible_if=lambda v: lay("radial")(v) and get(v, "centre_mode", "axis") == "axis",
                  help="Turns each row against the one below: a helix of openings up the model."),
            Int("count", "Count", 120, min=1, max=5000, step=10, visible_if=lay("random", "surface"),
                help="How many holes to try; a minimum wall may leave fewer."),
            Choice("aim", "Aim", "normal", [("normal", "Into the surface"), ("axes", "Along axes"),
                                            ("centre", "To the centre"), ("random", "Random")],
                   visible_if=lay("random", "surface"),
                   help="Direction of random and surface holes (bubbles: their stretch): inward along the surface "
                        "normal, along the lattice axes, toward the centre axis or point, or any direction."),
            Vec3("lattice_turn", "Turn", (0.0, 0.0, 0.0), unit="°", min=-180.0, max=180.0, step=5.0, decimals=1,
                 visible_if=uses_frame,
                 help="Turns the lattice (or the radial axis) about X, then Y, then Z; the model stays put."),
            Vec3("lattice_shift", "Shift", (0.0, 0.0, 0.0), unit="% step", min=-100.0, max=100.0, step=5.0,
                 decimals=1, visible_if=lay("lattice", "radial"),
                 help="Slides the lattice (radial: the rows) by a share of a step, e.g. to centre a hole on an eye."),
        ),
        Section(
            "Profile",
            Choice("profile", "Profile", "circle", [("circle", "Circle"), ("polygon", "Polygon"),
                                                   ("pillow", "Pillow"), ("blob", "Blob")], segmented=True,
                   help="Cross-section of tubes, bores and wormholes. Bubbles: sphere, faceted crystal, "
                        "superellipsoid or lumpy blob."),
            Int("sides", "Sides", 6, min=3, max=12, visible_if={"profile": "polygon"},
                help="Polygon corners (3 triangle, 4 square, 6 hexagon). Bubbles: more facets."),
            Float("roundness", "Squareness", 4.0, min=2.5, max=12.0, step=0.5, decimals=1,
                  visible_if={"profile": "pillow"},
                  help="Superellipse exponent: 2.5 a puffy cushion, 4 a squircle, 10 a square with soft corners."),
            Int("lobes", "Lobes", 5, min=2, max=12, visible_if={"profile": "blob"},
                help="How many swellings round the outline: a flower or a cloud."),
            Float("lumpiness", "Lumpiness", 0.4, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if={"profile": "blob"},
                  help="0 a circle, 0.3 soft lobes, 0.8 deep petals. Irregularity makes every hole's outline its "
                       "own."),
            Float("aspect", "Stretch", 1.0, min=0.2, max=5.0, step=0.1, decimals=2,
                  help="Width over height of the profile (a slot, a rectangle); bubbles: stretched along their "
                       "axis (above 1) or flattened (below 1)."),
            Float("roll", "Turn profile", 0.0, unit="°", min=-180.0, max=180.0, step=5.0, decimals=1,
                  help="Turns the profile about its hole: a square on its corner, a slot standing up."),
            Float("twist", "Twist", 0.0, unit="° / 10% h", min=-180.0, max=180.0, step=5.0, decimals=1,
                  visible_if=lambda v: lines(v) and get(v, "profile", "circle") != "circle",
                  help="Turns the profile along the hole: rifled tubes, screw-like bores."),
            Float("taper", "End size", 1.0, min=0.2, max=3.0, step=0.05, decimals=2, visible_if=fam("tube", "bore"),
                  help="Size at the far end over the size at the start: below 1 a cone narrowing inward, above 1 a "
                       "flare."),
        ),
        Section(
            "Wormholes",
            Float("waviness", "Waviness", 3.0, unit="% h", min=0.0, max=40.0, step=0.5, decimals=1,
                  visible_if=fam("worm"), help="Sideways swing of a regular wave along each wormhole, % of the height (bends are kept "
                       "wider than the tunnel)."),
            Float("wavelength", "Wavelength", 25.0, unit="% h", min=3.0, max=200.0, step=1.0, decimals=1,
                  visible_if=fam("worm"), help="Length of one wave, % of the height."),
            Float("swell", "Swell", 0.3, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=fam("worm"), help="How much the tunnel widens and narrows along its length (beads at 0 irregularity, uneven "
                       "at 1)."),
            collapsed=True,
        ),
        Section(
            "Irregularity",
            Float("chaos", "Irregularity", 0.5, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="0 is perfectly regular; 1 moves lattice holes up to half a step, tilts directions up to "
                       "50°, makes wormholes wander and gives every hole its own outline and stretch. The same "
                       "seed keeps the same holes as it rises."),
            Float("drop", "Leave out", 0.0, unit="%", min=0.0, max=95.0, step=5.0, decimals=0,
                  help="Randomly leave out this share of the holes: a lattice with gaps."),
            Float("jitter_pos", "Position", 1.0, min=0.0, max=2.0, step=0.1, decimals=2, advanced=True,
                  help="How much Irregularity moves holes (lattice and radial) and shifts wormhole waves."),
            Float("jitter_dir", "Direction", 1.0, min=0.0, max=2.0, step=0.1, decimals=2, advanced=True,
                  help="How much Irregularity tilts holes and makes wormholes wander."),
            Float("jitter_shape", "Shape", 1.0, min=0.0, max=2.0, step=0.1, decimals=2, advanced=True,
                  help="How much Irregularity turns, stretches and reshapes each hole's profile."),
        ),
        Section("Acts on", *influence_params(), collapsed=True,
                help="Holes only where this says: cutters are clipped to the band or sphere (grown by the "
                     "feather) and shrink across the feather."),
        Section(
            "Clean-up",
            Choice("keep", "Loose pieces", "all", [("all", "Keep all"), ("drop", "Drop small"),
                                                   ("largest", "Largest only")], segmented=True,
                   help="Holes can cut thin parts (fingers, ears) loose. Keep all leaves them in place; drop small "
                        "removes pieces under a share of the volume; largest only keeps one connected body."),
            Float("min_piece", "Smaller than", 0.5, unit="% vol", min=0.0, max=20.0, step=0.1, decimals=2,
                  visible_if=dropping, help="Pieces below this share of the total volume are removed."),
            Int("max_faces", "Output faces", 0, min=0, max=20_000_000, step=50_000,
                help="Simplify the result to at most this many faces; 0 keeps the exact cut."),
            collapsed=True,
        ),
        Section("Model", *frame_params(stage_sets=False)),
        Section(
            "Quality",
            Int("segments", "Segments", 40, min=8, max=160, step=4,
                help="Points round a circle, blob or pillow profile, and the detail of bubbles."),
            Int("draft_segments", "Preview segments", 16, min=8, max=160, step=4, advanced=True,
                help="Segments in the preview (Run uses Segments)."),
            Int("draft_faces", "Preview input faces", 80_000, min=2_000, max=2_000_000, advanced=True,
                help="Preview simplifies bigger models to this many faces before cutting; Run cuts the full model."),
            Int("repair_resolution", "Closing resolution", 256, min=64, max=512, step=16, advanced=True,
                help="An open model is closed by resampling it on a grid this many samples along its longest "
                     "side before it is cut."),
            collapsed=True),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"count": 12, "segments": 12, "draft_segments": 12, "size": 10.0, "size_max": 20.0,
                 "draft_faces": 20_000, "repair_resolution": 64},
)
