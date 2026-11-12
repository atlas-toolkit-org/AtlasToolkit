"""
Surface Peel / Bloom: cut a model's skin into petals or scales and bend each panel away from the body about a root
that stays attached (unroll, open, curl), thicken the panels into shells, optionally nest smaller layers inside, in
one stage or a sequence of stages.

Outputs <name>_peel.stl (and <name>_peel_stages/ with stages.json), signed atlas-toolkit:mesh-peel.
"""
from atlas_toolkit.core import (Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, Vec3, frame_params,
                                influence_params)


def staged(v):
    return int(v.get("stages", 1)) > 1


def petals(v):
    return v.get("pattern", "petals") == "petals"


def scales(v):
    return v.get("pattern", "petals") == "scales"


def layered(v):
    return int(v.get("layers", 1)) > 1


def ordered(v):
    return v.get("order", "together") != "together"


AXES = [("+z", "Crown"), ("-z", "Base"), ("-y", "Face"), ("+y", "Back"), ("+x", "Right"), ("-x", "Left")]

TOOL = Tool(
    id="mesh-peel",
    name="Surface Peel",
    version="1.0.1",
    optional=(("blender", "Solidify with Blender: Complex Solidify, even thickness and careful corners"),),
    category="mesh",
    icon="peel",
    action="Peel",
    summary="Cut a model's skin into petals or scales and peel them back about their roots: a head opening like a "
            "flower, a figure shedding curled plates.",
    tagline="Unfurling petals and sculptural scales; draw it with Plotter Vision or Sketch Hatching.",
    description=(
        "The surface is cut into panels: Petals are sectors about a bloom axis, in rows, with wavy edges and "
        "scalloped tips; Scales are patches grown along the surface from seeded points (Mesh Shatter's), cracks "
        "following the creases. Each panel keeps a root, the edge nearest the point where the petals meet (Peel) "
        "or farthest from it (Bloom), and bends away from the body along its length: Unroll straightens it out of "
        "the body's curvature, Open turns it outward at the hinge, Curl rolls it back, tighter toward the tip. The "
        "root never moves. Panels are thickened into closed shells, so their rims and undersides draw. Layers nest "
        "smaller copies inside that open less, like a rose; a Core leaves an intact copy at the heart. Amount is "
        "how far the bloom has gone: stage 0 of a sequence is the untouched model, and panels can open in turn, "
        "from the pole, the far side or at random. Curled panels can pass through their neighbours; nothing is "
        "solved for collisions."),
    inputs=Inputs("mesh", exclude=(r"(?i)_peel(?:_stage_\d+)?\.(stl|obj|ply)$",)),
    output=Output("{stem}_peel.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-peel",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("panels", "stages", "faces"),
    params=[
        Section(
            "Panels",
            Choice("pattern", "Pattern", "petals", [("petals", "Petals"), ("scales", "Scales")], segmented=True,
                   help="Petals: sectors about the bloom axis, in rows. Scales: patches grown along the surface from "
                        "seeded points, cracks following the creases."),
            Int("petals", "Petals", 7, min=2, max=48, slider=True, visible_if=petals,
                help="Petals around the axis in each row. 5-9 read as a flower on a head; 16+ as a fringe."),
            Int("rows", "Rows", 2, min=1, max=8, slider=True, visible_if=petals,
                help="Rings of petals from the pole outward. 1: long strips, like a peeled fruit."),
            Bool("stagger_rows", "Stagger rows", True, visible_if=lambda v: petals(v) and int(v.get("rows", 2)) > 1,
                 help="Each row turned by half a petal, so the petals overlap like a rose's or an artichoke's."),
            Float("scallop", "Petal tips", 0.7, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=lambda v: petals(v) and int(v.get("rows", 2)) > 1,
                  help="How far each petal's middle reaches into the next row: 0 straight rings, 1 pointed tips."),
            Float("wobble", "Wobble", 0.3, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=petals,
                  help="Wavy petal edges (smooth, seeded): 0 straight meridians."),
            Int("pieces", "Panels", 32, min=3, max=400, slider=True, visible_if=scales,
                help="Target number of scales. Separate parts and wrapped pieces are split, slivers merged."),
            Float("edge_sensitivity", "Edge sensitivity", 1.0, min=0.0, max=3.0, step=0.1, decimals=2,
                  visible_if=scales,
                  help="How strongly sharp edges resist being crossed: higher makes the cracks follow creases."),
            Seed("seed", "Seed", 7, help="Same seed, same panels, wobble and per-panel variation."),
        ),
        Section(
            "Bloom",
            Float("amount", "Amount", 1.0, min=0.0, max=1.5, step=0.05, decimals=2, slider=True,
                  help="How far the bloom has gone: 0 the untouched model, 1 every panel fully open."),
            Float("curl", "Curl", 80.0, unit="°", min=-360.0, max=720.0, step=5.0, decimals=0, slider=True,
                  help="How far each panel rolls back along its length, at its tip. Over 360° winds it into a "
                       "scroll; negative curls it in toward the body."),
            Float("open_angle", "Open", 35.0, unit="°", min=-60.0, max=150.0, step=5.0, decimals=0, slider=True,
                  help="Turn at the hinge, just past the root: how far the panel swings out from the body."),
            Float("unroll", "Unroll", 0.55, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="How much the panel straightens out of the body's own curvature before it curls: 0 keeps "
                       "its shape (a shell lifting off), 1 lays it flat (a petal opened out)."),
            Float("tighten", "Tighten at tip", 0.4, min=0.0, max=1.0, step=0.05, decimals=2,
                  help="0 spreads the curl evenly (an arc); 1 gathers it at the tip (a scroll, a fern)."),
            Float("root_share", "Root", 18.0, unit="%", min=0.0, max=80.0, step=1.0, decimals=0,
                  help="Share of each panel's length, from its root, that stays on the body."),
            Choice("root", "Root at", "bloom", [("bloom", "Bloom"), ("peel", "Peel")], segmented=True,
                   help="Bloom: panels are rooted far from the point where they meet, which opens like a flower. "
                        "Peel: rooted at that point, their far ends lift away, like a peeled fruit or roof tiles."),
            Float("lift", "Lift off", 0.0, unit="% h", min=0.0, max=40.0, step=0.5, decimals=1, advanced=True,
                  help="Also float every opened panel out along its normal, % of the height (the roots let go)."),
            Float("variation", "Variation", 0.25, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="Seeded per-panel differences in curl and opening, so the bloom looks grown."),
        ),
        Section(
            "Axis",
            Choice("bloom_axis", "Petals meet at", "+z", AXES, segmented=True,
                   help="The pole of the bloom. Crown: a head opens from the top; Face: rings of petals around the "
                        "nose, like a rose seen from above."),
            Vec3("bloom_centre", "Centre", (0.0, 0.0, -10.0), unit="%", min=-100.0, max=100.0, step=5.0, decimals=1,
                 help="Where the axis passes, from the model's centre in % of its size (X across, Y depth with the "
                      "front at −, Z up). Layers shrink toward it."),
        ),
        Section(
            "Order",
            Choice("order", "Panels open", "together", [("together", "Together"), ("pole", "Pole first"),
                                                        ("far", "Far side first"), ("random", "At random")],
                   segmented=True,
                   help="Whether every panel opens at once or one after another (stages show it best)."),
            Float("stagger", "Stagger", 0.6, min=0.0, max=0.95, step=0.05, decimals=2, slider=True,
                  visible_if=ordered,
                  help="How spread out the openings are: 0 all at once, 0.9 one by one."),
        ),
        Section(
            "Layers",
            Int("layers", "Layers", 1, min=1, max=5, slider=True,
                help="Smaller copies nested inside, each cut and bloomed on its own (petals turned by half a "
                     "petal): a rose of heads."),
            Float("layer_scale", "Layer size", 0.82, unit="×", min=0.4, max=0.97, step=0.01, decimals=2,
                  visible_if=lambda v: layered(v) or bool(v.get("core")),
                  help="Each layer's size relative to the one around it, shrunk toward the centre."),
            Float("layer_falloff", "Inner opening", 0.6, unit="×", min=0.0, max=1.0, step=0.05, decimals=2,
                  visible_if=layered,
                  help="Each layer opens this much of the one around it: inner petals stay closer."),
            Bool("core", "Intact core", False,
                 help="Leave an untouched copy, one layer smaller, at the heart of the bloom."),
        ),
        Section(
            "Shell",
            Float("thickness", "Thickness", 0.6, unit="% h", min=0.0, max=5.0, step=0.1, decimals=2,
                  help="Panel thickness, % of the height, grown inward: the rims and undersides that make overlaps "
                       "read in line. 0 leaves open surfaces."),
            Choice("solidify", "Solidify with", "atlas", [("atlas", "Built in"), ("blender", "Blender")],
                   segmented=True, advanced=True,
                   help="Built in: offset and rims, fast. Blender: its Complex Solidify (even thickness, careful "
                        "corners); needs Blender 4.2 or newer."),
        ),
        Section("Acts on", *influence_params(), collapsed=True),
        Section(
            "Stages",
            Int("stages", "Stages", 1, min=1, max=8, slider=True,
                help="1: one bloomed model. More: a sequence from intact to the full amount, panels and roots "
                     "fixed at rest."),
            Bool("save_stages", "Also save every stage", True, visible_if=staged,
                 help="Stage files go in <name>_peel_stages/ with stages.json (its frame), so the drawing tools "
                      "give every plate one scale and position."),
            Choice("layout", "Primary output", "final", [("final", "Last stage"), ("progression", "All stages")],
                   segmented=True, visible_if=staged,
                   help="All stages: side by side in one file, for a 3D print or a single view of the sequence."),
            Choice("axis", "Side by side along", "x", [("x", "X"), ("y", "Y"), ("z", "Z")], segmented=True,
                   visible_if=lambda v: staged(v) and v.get("layout") == "progression"),
            Float("gap", "Gap", 15.0, unit="% h", min=0.0, max=200.0, step=1.0, decimals=1,
                  visible_if=lambda v: staged(v) and v.get("layout") == "progression",
                  help="Clearance between stages, % of the height."),
        ),
        Section("Model", *frame_params(stage_sets=False)),
        Section("Quality",
                Float("max_edge", "Longest edge", 1.5, unit="% h", min=0.2, max=20.0, step=0.1, decimals=1,
                      advanced=True,
                      help="Edges longer than this are split (conformingly) before bending, so low-poly models curl "
                           "smoothly instead of folding as flat facets."),
                Int("seam_smooth", "Seam smoothing", 6, min=0, max=30, advanced=True,
                    help="Passes that relax the panel edges from triangle zig-zags into curves."),
                Int("draft_faces", "Preview faces", 60_000, min=2_000, max=2_000_000, advanced=True,
                    help="Preview simplifies bigger models to this many faces and skips the edge splitting; Peel "
                         "always uses the full model."),
                collapsed=True),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"stages": 2, "max_edge": 20.0, "petals": 4, "rows": 1},
    legacy=(),
)
