"""
Rods: add rods, hollow pipes, drooping cables or spikes to a closed model, through it or growing out of it, placed on
a lattice, radially, at random or over its surface. The mirror of Holes.

Outputs <name>_rods.stl (and, optionally, <name>_rods_tubes.stl: the rods alone, same frame), signed
atlas-toolkit:mesh-rods.
"""
from atlas_toolkit.core import (Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, Vec3, frame_params,
                                influence_params)


def get(v, key, default):
    """A form value for a visibility rule: the default when it is missing or None (a choice with nothing picked)."""
    x = v.get(key)
    return default if x is None else x


def num(v, key, default=0.0) -> float:
    try:
        return float(get(v, key, default))
    except (TypeError, ValueError):
        return default


def fam(*f):
    return lambda v: get(v, "family", "rod") in f


def lay(*m):
    return lambda v: get(v, "layout", "surface") in m


def outward(v):
    return get(v, "extend", "outward") == "outward"


def ranged(a, b):
    return lambda v: num(v, b) > num(v, a)


def uses_axes(v):
    return lay("lattice")(v) or (lay("random", "surface")(v) and get(v, "aim", "normal") == "axes")


def uses_frame(v):
    return lay("lattice", "radial")(v) or get(v, "aim", "normal") in ("axes", "centre")


def uses_centre(v):
    return lay("radial")(v) or (lay("random", "surface")(v) and get(v, "aim", "normal") == "centre")


def has_tip(v):
    return get(v, "family", "rod") != "spike"


TOOL = Tool(
    id="mesh-rods",
    name="Rods",
    version="1.0",
    category="mesh",
    icon="rods",
    action="Add rods",
    summary="Add rods, hollow pipes, drooping cables or spikes to a model, through it or growing out of it.",
    tagline="A head skewered by beams, bristling with spikes, sprouting organ pipes or hung with cables: an exact "
            "union with crisp junctions, as a closed mesh for Contour Lines, Plotter Vision or Sketch Hatching.",
    description=(
        "The mirror of Holes: the same layouts, profiles and irregularity, but the solids are added. Rods run "
        "Through the model (entering one side, leaving the other, sticking out at both ends by Reach) or grow "
        "Outward from it (rooted a little inside, out of the skin by Reach). Kinds: rods (any profile: circle, an "
        "n-sided polygon, pillow or blob, tapering to End size), hollow pipes (a wall and an open bore; the bore "
        "can also run on through the model, so you see through it), cables (wandering, waving, swelling and "
        "drooping under gravity) and spikes (narrowing to a point, a thorn's curve). Ends flat, round, pointed or "
        "flared like a bell; collars where rods cross the skin. Layouts: lattice (one to three axes, square or hex, "
        "woven), radial (spokes around an axis or out from a point), random or over the surface. The union is "
        "exact (manifold3d): the line where a rod meets the skin stays crisp on paper. Long rods enlarge the "
        "drawing's frame, so the model gets smaller on the page. The model should be closed; an open one is "
        "closed first by resampling."),
    inputs=Inputs("mesh", exclude=(r"(?i)_rods(?:_tubes)?\.(stl|obj|ply)$",)),
    output=Output("{stem}_rods.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-rods",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("rods", "added", "faces"),
    params=[
        Section(
            "Rods",
            Choice("family", "Kind", "rod", [("rod", "Rods"), ("pipe", "Hollow pipes"), ("cable", "Cables"),
                                             ("spike", "Spikes")],
                   help="Rods: solid bars of any profile. Hollow pipes: a wall round an open bore. Cables: paths "
                        "that wander, wave and droop. Spikes: narrowing to a point outside the model."),
            Choice("extend", "Extend", "outward", [("through", "Through"), ("outward", "Outward")], segmented=True,
                   help="Through: each rod crosses the model and sticks out at both ends (a skewer). Outward: rooted "
                        "inside, it grows out of the skin on one side (a quill)."),
            Choice("layout", "Layout", "surface", [("lattice", "Lattice"), ("radial", "Radial"),
                                                   ("random", "Random"), ("surface", "Surface")],
                   help="Lattice: lines on a regular grid. Radial: spokes around an axis or out from a point. "
                        "Random: through random points inside the model. Surface: spread evenly over its skin."),
            Float("size", "Width", 2.0, unit="% h", min=0.2, max=40.0, step=0.25, decimals=2,
                  help="Rod width (the diameter of a circle of the same area), % of the model's height. Set 'up "
                       "to' above it for a random width per rod."),
            Float("size_max", "up to", 3.5, unit="% h", min=0.2, max=40.0, step=0.25, decimals=2,
                  help="Widest rod; equal to Width for one width."),
            Choice("size_mix", "Widths", "small", [("even", "Even"), ("small", "Mostly thin"),
                                                   ("large", "Mostly thick")], visible_if=ranged("size", "size_max"),
                   help="How widths spread over the range: evenly, many thin and a few thick, or the reverse."),
            Float("reach", "Reach", 12.0, unit="% h", min=0.5, max=300.0, step=1.0, decimals=1,
                  help="How far a rod sticks out of the model, % of the height (each end of a through rod is "
                       "drawn on its own). Set 'up to' above it for random lengths."),
            Float("reach_max", "up to", 35.0, unit="% h", min=0.5, max=300.0, step=1.0, decimals=1,
                  help="Longest reach; equal to Reach for one length."),
            Float("root", "Root depth", 3.0, unit="% h", min=0.0, max=50.0, step=0.5, decimals=1,
                  visible_if=outward,
                  help="How far inside the skin an outward rod starts, so it is anchored in the model."),
            Choice("tip", "Ends", "flat", [("flat", "Flat"), ("round", "Round"), ("pointed", "Pointed"),
                                           ("flared", "Flared")], segmented=True, visible_if=has_tip,
                   help="The free ends: cut flat, a round cap, a cone point, or flared like a bell (pipes: open "
                        "mouths)."),
            Float("flare", "Flare", 1.8, unit="×", min=1.1, max=4.0, step=0.1, decimals=2,
                  visible_if=lambda v: has_tip(v) and get(v, "tip", "flat") == "flared",
                  help="How wide a flared end opens, × the rod's width, over its last few widths."),
            Float("wall", "Wall", 0.3, unit="× radius", min=0.05, max=0.9, step=0.05, decimals=2,
                  visible_if=fam("pipe"), help="A pipe's wall thickness as a share of its radius."),
            Choice("bore", "Bore", "open", [("open", "Open through"), ("plugged", "Plugged")], segmented=True,
                   visible_if=fam("pipe"),
                   help="Open through: the bore is also cut through the model, so you can look down a pipe and "
                        "through the head. Plugged: the bore stops at the skin."),
            Float("gap", "Minimum gap", 0.5, unit="% h", min=0.0, max=20.0, step=0.1, decimals=2,
                  help="The least space between two rods outside the model, % of the height: rods that would come "
                       "closer get thinner (down to Width) or are left out, thickest first. 0 lets them cross and "
                       "merge. Only the parts outside the model count."),
            Seed("seed", "Seed", 1, help="Where random rods go and how every rod varies."),
        ),
        Section(
            "Placement",
            Choice("axes", "Axes", "y", [("x", "X"), ("y", "Y"), ("z", "Z"), ("xy", "XY"), ("xz", "XZ"),
                                         ("yz", "YZ"), ("xyz", "XYZ")], segmented=True, visible_if=uses_axes,
                   help="Directions of lattice rods, in the model's frame: X across, Y front to back, Z up."),
            Float("spacing", "Spacing", 12.0, unit="% h", min=1.0, max=100.0, step=0.5, decimals=1,
                  visible_if=lay("lattice", "radial"),
                  help="Distance between neighbouring lattice lines, or between rows of a radial layout, % of the "
                       "height."),
            Choice("grid", "Grid", "square", [("square", "Square"), ("hex", "Hex")], segmented=True,
                   visible_if=lay("lattice", "radial"),
                   help="Square, or hex: rows offset by half a step (radial rows turned half a spoke)."),
            Bool("weave", "Weave the axes", True,
                 visible_if=lambda v: lay("lattice")(v) and len(str(get(v, "axes", "y"))) > 1,
                 help="Offset each direction's lines by half a step so they pass each other instead of crossing."),
            Bool("both_sides", "From both sides", True, visible_if=lambda v: lay("lattice")(v) and outward(v),
                 help="Grow each lattice line's rods out of both faces of the model, not only the far one."),
            Choice("centre_mode", "Around", "axis", [("axis", "An axis"), ("point", "A point")], segmented=True,
                   visible_if=uses_centre,
                   help="Radial rods go around a vertical axis (rows of spokes) or out from a point (a burst)."),
            Vec3("radial_centre", "Centre", (0.0, 0.0, 0.0), unit="%", min=-100.0, max=100.0, step=5.0, decimals=1,
                 visible_if=uses_centre,
                 help="From the model's centre, % of its size along X (across), Y (depth, front is −) and Z (up)."),
            Int("spokes", "Spokes", 12, min=1, max=200, visible_if=lay("radial"),
                help="Rods per row around the axis, or in all from the point."),
            Float("spiral", "Spiral", 0.0, unit="° / row", min=-180.0, max=180.0, step=2.5, decimals=1,
                  visible_if=lambda v: lay("radial")(v) and get(v, "centre_mode", "axis") == "axis",
                  help="Turns each row against the one below: a helix of rods up the model."),
            Int("count", "Count", 60, min=1, max=4000, step=10, visible_if=lay("random", "surface"),
                help="How many rods to try; a minimum gap may leave fewer."),
            Choice("aim", "Aim", "normal", [("normal", "Out of the surface"), ("axes", "Along axes"),
                                            ("centre", "Away from the centre"), ("random", "Random")],
                   visible_if=lay("random", "surface"),
                   help="Direction of random and surface rods: along the surface normal, along the lattice axes, "
                        "away from the centre axis or point, or any direction."),
            Vec3("lattice_turn", "Turn", (0.0, 0.0, 0.0), unit="°", min=-180.0, max=180.0, step=5.0, decimals=1,
                 visible_if=uses_frame,
                 help="Turns the lattice (or the radial axis) about X, then Y, then Z; the model stays put."),
            Vec3("lattice_shift", "Shift", (0.0, 0.0, 0.0), unit="% step", min=-100.0, max=100.0, step=5.0,
                 decimals=1, visible_if=lay("lattice", "radial"),
                 help="Slides the lattice (radial: the rows) by a share of a step."),
        ),
        Section(
            "Profile",
            Choice("profile", "Profile", "circle", [("circle", "Circle"), ("polygon", "Polygon"),
                                                   ("pillow", "Pillow"), ("blob", "Blob")], segmented=True,
                   help="Cross-section of every rod: a circle, an n-sided polygon, a pillow (superellipse) or a "
                        "seeded blob."),
            Int("sides", "Sides", 6, min=3, max=12, visible_if={"profile": "polygon"},
                help="Polygon corners: 3 a triangular bar, 4 a square beam, 6 a hexagonal pencil."),
            Float("roundness", "Squareness", 4.0, min=2.5, max=12.0, step=0.5, decimals=1,
                  visible_if={"profile": "pillow"},
                  help="Superellipse exponent: 2.5 a puffy cushion, 4 a squircle, 10 a square with soft corners."),
            Int("lobes", "Lobes", 5, min=2, max=12, visible_if={"profile": "blob"},
                help="How many swellings round the outline: a fluted column, a flower."),
            Float("lumpiness", "Lumpiness", 0.3, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if={"profile": "blob"},
                  help="0 a circle, 0.3 soft flutes, 0.8 deep petals. Irregularity makes every rod's outline its "
                       "own."),
            Float("aspect", "Stretch", 1.0, min=0.2, max=5.0, step=0.1, decimals=2,
                  help="Width over height of the profile: a flat bar, a blade."),
            Float("roll", "Turn profile", 0.0, unit="°", min=-180.0, max=180.0, step=5.0, decimals=1,
                  help="Turns the profile about its rod: a square beam on its edge."),
            Float("twist", "Twist", 0.0, unit="° / 10% h", min=-180.0, max=180.0, step=5.0, decimals=1,
                  visible_if=lambda v: get(v, "profile", "circle") != "circle",
                  help="Turns the profile along the rod: twisted bars, drills, screws."),
            Float("taper", "End size", 1.0, min=0.1, max=3.0, step=0.05, decimals=2, visible_if=has_tip,
                  help="Width at the free end over the width at the skin: below 1 narrowing, above 1 widening."),
        ),
        Section(
            "Cables",
            Float("droop", "Droop", 0.5, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=fam("cable"),
                  help="Gravity: 0 cables keep their heading; at 1 a cable leaving the skin level turns down within "
                       "about a fifth of the model's height and hangs (one pointing up tips over first)."),
            Float("waviness", "Waviness", 0.0, unit="% h", min=0.0, max=40.0, step=0.5, decimals=1,
                  visible_if=fam("cable"),
                  help="Sideways swing of a regular wave along each cable, % of the height (tendrils)."),
            Float("wavelength", "Wavelength", 25.0, unit="% h", min=3.0, max=200.0, step=1.0, decimals=1,
                  visible_if=fam("cable"), help="Length of one wave (and of the swell's beads), % of the height."),
            Float("swell", "Swell", 0.0, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=fam("cable"),
                  help="How much a cable widens and narrows along its length: beads at 0 irregularity, uneven at "
                       "1."),
            collapsed=True,
        ),
        Section(
            "Collars",
            Bool("collars", "Collars", False,
                 help="A short, wider sleeve of the rod's own profile where it crosses the skin: a flange, a socket, "
                      "a grommet."),
            Float("collar_size", "Collar width", 1.8, unit="× width", min=1.1, max=5.0, step=0.1, decimals=2,
                  visible_if={"collars": True}),
            Float("collar_length", "Collar length", 1.5, unit="% h", min=0.2, max=20.0, step=0.1, decimals=2,
                  visible_if={"collars": True},
                  help="Along the rod, centred on the skin, % of the height: thin for a flange, long for a sleeve."),
            collapsed=True,
        ),
        Section(
            "Irregularity",
            Float("chaos", "Irregularity", 0.3, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="0 is perfectly regular; 1 moves lattice rods up to half a step, tilts directions up to 50°, "
                       "makes cables wander and gives every rod its own outline and stretch. The same seed keeps the "
                       "same rods as it rises."),
            Float("drop", "Leave out", 0.0, unit="%", min=0.0, max=95.0, step=5.0, decimals=0,
                  help="Randomly leave out this share of the rods: a lattice with gaps."),
            Float("jitter_pos", "Position", 1.0, min=0.0, max=2.0, step=0.1, decimals=2, advanced=True,
                  help="How much Irregularity moves rods (lattice and radial) and shifts cable waves."),
            Float("jitter_dir", "Direction", 1.0, min=0.0, max=2.0, step=0.1, decimals=2, advanced=True,
                  help="How much Irregularity tilts rods and makes cables wander."),
            Float("jitter_shape", "Shape", 1.0, min=0.0, max=2.0, step=0.1, decimals=2, advanced=True,
                  help="How much Irregularity turns, stretches and reshapes each rod's profile."),
        ),
        Section("Acts on", *influence_params(), collapsed=True,
                help="Rods only where this says: a rod crossing the skin in the feather is thinner and shorter."),
        Section(
            "Output",
            Bool("save_tubes", "Also save the rods alone", True,
                 help="<name>_rods_tubes.stl: the rods, pipes and collars without the model, in the same place, "
                      "to draw with another pen or another style."),
            Int("max_faces", "Output faces", 0, min=0, max=20_000_000, step=50_000,
                help="Simplify the result to at most this many faces; 0 keeps the exact union."),
            collapsed=True,
        ),
        Section("Model", *frame_params(stage_sets=False)),
        Section(
            "Quality",
            Int("segments", "Segments", 32, min=8, max=160, step=4,
                help="Points round a circle, blob or pillow profile."),
            Int("draft_segments", "Preview segments", 14, min=8, max=160, step=4, advanced=True,
                help="Segments in the preview (Run uses Segments)."),
            Int("draft_faces", "Preview input faces", 80_000, min=2_000, max=2_000_000, advanced=True,
                help="Preview simplifies bigger models to this many faces first; Run uses the full model."),
            Int("repair_resolution", "Closing resolution", 256, min=64, max=512, step=16, advanced=True,
                help="An open model is closed by resampling it on a grid this many samples along its longest "
                     "side first."),
            collapsed=True),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"count": 12, "segments": 12, "draft_segments": 12, "draft_faces": 20_000, "repair_resolution": 64},
)
