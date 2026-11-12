"""
Stair Sculpt: turn a model into a staircase, built inside a cavity or made from the model itself, with linear,
turning or helical stairs.

Outputs <name>_stairs.stl, signed atlas-toolkit:mesh-stairs.
"""
from atlas_toolkit.core import (Bool, Choice, FileRef, Float, Inputs, Int, Output, Section, Seed, Text, Tool,
                                frame_params)


def carving(v):
    return v.get("mode") == "carve"


def automatic(v):
    return carving(v) and v.get("placement") != "paint"


def painting(v):
    return carving(v) and v.get("placement") == "paint"


def ensemble(v):
    return carving(v) and (v.get("flights") or 1) > 1


def whole(v):
    return v.get("mode") != "carve"


def cutting(v):
    return v.get("mode") == "cut"


def helix(v):
    return v.get("stair_type") == "helix"


def turning(v):
    return v.get("stair_type") == "turns"


TOOL = Tool(
    id="mesh-stairs",
    name="Stair Sculpt",
    version="0.1.0",
    category="mesh",
    icon="stairs",
    action="Sculpt",
    summary="Turn a model into a staircase: carve a cavity and build stairs inside, or make the whole body the stair.",
    tagline="Linear, turning or helical stairs, from one clean flight to an impossible architecture, as one solid.",
    description=(
        "Carve opens a cavity behind a window, on a chosen side or where you paint, and builds stairs inside it: one "
        "staircase, or with several Flights an ensemble of landings, piers and arches. Treads keeps only the model's "
        "material inside the steps, so its silhouette becomes the outline of each tread, as a strata stack that "
        "climbs. Cut carves the steps into the body: a straight flight up the surface with the model as its "
        "walls, a square ramp around it or a spiral groove. Types are linear, turning (quarter turns around a well, or "
        "switchbacks) and helical. Risers, treads and lips overhanging each riser give crisp parallel edges for "
        "Plotter Vision, Contour Lines and hatching. Booleans fuse everything into one closed solid, so the input must "
        "be closed; resampling repair is opt-in. Lengths are % of the model's height."),
    inputs=Inputs("mesh", exclude=(r"(?i)_stairs\.(stl|obj|ply)$",)),
    output=Output("{stem}_stairs.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-stairs",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    viewer=".viewer:SelectionView",
    sample=".engine:make_sample",
    columns=("steps", "flights", "faces"),
    preset_inputs=("placement", "selection", "selection_file", "repair"),
    params=[
        Section(
            "Staircase",
            Choice("mode", "Mode", "carve", [("carve", "Carve"), ("treads", "Treads"), ("cut", "Cut")],
                   segmented=True,
                   help="Carve: a cavity with stairs built in it. Treads: the model reduced to the steps. Cut: a flight "
                        "cut into the body, with the model as walls."),
            Choice("stair_type", "Type", "linear", [("linear", "Linear"), ("turns", "Turning"), ("helix", "Helix")],
                   segmented=True),
            Int("steps", "Steps", 220, min=6, max=600, slider=True,
                help="Steps in all, shared among the flights. Risers should stay wider than a pen line: at a 200 mm "
                     "drawing, 100-200 steps is about the limit."),
            Int("flights", "Flights", 26, min=1, max=60, slider=True,
                help="Carve: 1 is one staircase, more builds an ensemble of flights and landings. Treads: "
                     "intertwined stairs (2 on a helix is a double helix) or, on a linear stair, a zigzag up the "
                     "model. Cut: parallel flights or starts."),
            Float("turns", "Helix turns", 2.0, min=0.25, max=10.0, step=0.25, decimals=2, visible_if=helix,
                  help="Full turns around the axis, over the height."),
            Int("twists", "Turns", 5, min=1, max=40, visible_if=turning,
                help="How many times the stair changes direction."),
            Choice("turn_style", "Turn by", "quarter", [("quarter", "Quarter (around a well)"),
                   ("switchback", "Half (switchback)")], segmented=True,
                   visible_if=lambda v: turning(v) and not cutting(v),
                   help="Quarter: a square spiral around a well. Half: flights side by side, back and forth."),
            Float("heading", "Heading", 0.0, unit="°", min=-180.0, max=180.0, step=15.0, decimals=0,
                  visible_if=lambda v: whole(v) or helix(v),
                  help="The way the stair sets off, around the model. 0 runs across it (from the viewer's left to "
                       "right), 90 runs from the front to the back."),
            Choice("climb", "Climb", "forward", [("forward", "Forward"), ("reverse", "Reverse")], segmented=True,
                   help="Mirrors the direction: left or right on a helix or a turning stair, and the way up on a "
                        "linear one."),
        ),
        Section(
            "Form",
            Float("width", "Width", 70.0, unit="%", min=10.0, max=100.0, step=5.0, decimals=0, slider=True,
                  visible_if=lambda v: not (helix(v) or (cutting(v) and turning(v))),
                  help="Width of a flight, % of the room it has."),
            Float("well", "Well", 18.0, unit="%", min=0.0, max=80.0, step=1.0, decimals=0, slider=True,
                  visible_if=lambda v: helix(v) or turning(v),
                  help="The open middle, % of the radius or half width. In Cut mode, the deepest the cut can go "
                       "toward the axis."),
            Bool("core", "Solid core", False,
                 visible_if=lambda v: (helix(v) or turning(v)) and not cutting(v) and not ensemble(v),
                 help="A column fills the well, as in a newel stair. Treads stay connected through it."),
            Choice("support", "Underside", "band", [("band", "Stepped band"), ("smooth", "Smooth"),
                   ("open", "Open treads"), ("solid", "Solid to floor")],
                   visible_if=lambda v: not cutting(v),
                   help="Stepped band: each step a deep block. Smooth: treads over a sloped soffit, like the carved "
                        "helical stair. Open: thin treads on a carriage. Solid: everything below the steps stays."),
            Float("thickness", "Thickness", 4.0, unit="% h", min=0.5, max=40.0, step=0.5, decimals=1, slider=True,
                  visible_if=lambda v: not cutting(v) and v.get("support") in ("band", "smooth"),
                  help="Depth of the stair's body. On a helix, a thicker stair hides more of the turn above."),
            Float("tread", "Tread", 40.0, unit="% rise", min=5.0, max=100.0, step=5.0, decimals=0,
                  visible_if=lambda v: not cutting(v) and v.get("support") == "open",
                  help="Thickness of an open tread."),
            Float("nosing", "Lip", 0.0, unit="% going", min=0.0, max=50.0, step=1.0, decimals=0, slider=True,
                  help="Treads overhang their risers by this. Adds a second parallel line to each step in a drawing."),
            Float("parapet", "Parapet", 0.0, unit="% h", min=0.0, max=15.0, step=0.5, decimals=1, visible_if=carving,
                  help="Low stepped walls along the flights' free edges. 0: none."),
            Bool("arches", "Arches and arcades", True, visible_if=ensemble,
                 help="Arched openings under the flights and behind the landings."),
            Seed("seed", "Seed", 1, visible_if=ensemble, help="Rearranges the ensemble's flights."),
        ),
        Section(
            "Cavity",
            Choice("placement", "Opening", "side", [("side", "On a side"), ("paint", "Painted area")], segmented=True,
                   visible_if=carving,
                   help="On a side needs no painting. Painted area: open the Selection tab and paint what to open. In "
                        "Workflows use Aim in tool, paint, then Back to workflow."),
            Choice("side", "Side", "right", [("front", "Front"), ("right", "Right"), ("back", "Back"),
                   ("left", "Left"), ("top", "Top")], visible_if=automatic),
            Float("side_turn", "Turn side by", -35.0, unit="°", min=-90.0, max=90.0, step=5.0, decimals=0,
                  visible_if=lambda v: automatic(v) and v.get("side") != "top",
                  help="Turns the opening around the model, for a three-quarter view."),
            Choice("open_shape", "Shape", "follow", [("follow", "Follow the outline"), ("arch", "Arch"),
                   ("ellipse", "Ellipse"), ("rect", "Rectangle")], visible_if=automatic,
                   help="Follow the outline keeps an even rim all around the model's silhouette."),
            Float("open_w", "Opening width", 100.0, unit="%", min=5.0, max=140.0, step=5.0, decimals=0, slider=True,
                  visible_if=automatic, help="% of the model's width seen from this side."),
            Float("open_h", "Opening height", 85.0, unit="%", min=5.0, max=140.0, step=5.0, decimals=0, slider=True,
                  visible_if=automatic, help="% of the model's height seen from this side."),
            Float("open_x", "Across", 0.0, unit="%", min=-60.0, max=60.0, step=2.0, decimals=0, visible_if=automatic,
                  help="Moves the opening sideways, % of the model's width."),
            Float("open_y", "Up", 5.0, unit="%", min=-60.0, max=60.0, step=2.0, decimals=0, visible_if=automatic,
                  help="Moves the opening up or down, % of the model's height."),
            Float("depth", "Depth", 62.0, unit="%", min=5.0, max=100.0, step=1.0, decimals=0, slider=True,
                  visible_if=carving, help="How far in, % of the model's depth behind the opening."),
            Float("wall", "Wall", 3.5, unit="% h", min=0.5, max=20.0, step=0.5, decimals=1, visible_if=carving,
                  help="Thickness left around the cavity, and of the rim around the opening."),
            Float("recess", "Recess", 1.5, unit="% h", min=0.0, max=16.0, step=0.5, decimals=1, visible_if=carving,
                  help="How far the stairs stay behind the model's surface at the opening. Limited to 80% of Wall so "
                       "they still reach the walls."),
            FileRef("selection_file", "Selection file", "", exts=(".json",), visible_if=painting,
                    help="Optional exported selection JSON for the command line or Workflows. Overrides the embedded "
                         "selection. Bound to this exact mesh."),
            Text("selection", "Embedded selection", "", advanced=True, visible_if=painting,
                 help="Written by the selection editor, kept with settings and Workflows."),
        ),
        Section(
            "Whole model",
            Float("keep_bottom", "Keep base", 6.0, unit="% h", min=0.0, max=70.0, step=1.0, decimals=0, slider=True,
                  visible_if=whole, help="The model's lower part stays whole, as a plinth the stairs rise from."),
            Float("keep_top", "Keep top", 0.0, unit="% h", min=0.0, max=70.0, step=1.0, decimals=0, slider=True,
                  visible_if=whole, help="The model's upper part stays whole: a head on a stair."),
            Bool("follow_axis", "Follow the model's axis", True,
                 visible_if=lambda v: whole(v) and helix(v),
                 help="The helix's axis follows the sections' centres, so it stays inside a neck or a leaning "
                      "figure. Off: one straight vertical axis."),
            Float("cut_depth", "Cut depth", 6.0, unit="% h", min=0.5, max=40.0, step=0.5, decimals=1, visible_if=cutting,
                  help="How far the treads lie below the surface they follow."),
            Float("follow", "Follow the surface", 100.0, unit="%", min=0.0, max=100.0, step=5.0, decimals=0,
                  visible_if=lambda v: cutting(v) and v.get("stair_type") == "linear",
                  help="100: the steps climb with the model's surface. 0: one straight pitch from the bottom of the "
                       "flight to the top."),
            Float("headroom", "Headroom", 0.0, unit="% h", min=0.0, max=100.0, step=1.0, decimals=0, visible_if=cutting,
                  help="Clear height above the treads. 0: open to the sky on a linear flight, and part of the way to "
                       "the coil above on the others."),
        ),
        Section(
            "Quality",
            Int("resolution", "Cavity resolution", 128, min=48, max=320, step=16, advanced=True, visible_if=carving,
                help="Samples along the longest side when the cavity is shaped after the model. Stairs are always exact."),
            Int("draft_resolution", "Preview resolution", 72, min=32, max=256, step=8, advanced=True, visible_if=carving,
                help="The same for Preview."),
            Bool("keep_fragments", "Keep floating pieces", False,
                 help="The model's shape can leave parts of the stair unconnected. Off: keep the one connected "
                      "solid. On: keep them too (they float)."),
            Choice("repair", "Open input", "strict", [("strict", "Require closed solid"),
                   ("resample", "Repair by resampling")],
                   help="Repair changes the source surface and may soften detail. To paint a repaired input, save it "
                        "first and paint that model."),
            Int("repair_resolution", "Repair resolution", 128, min=64, max=320, step=16, advanced=True,
                visible_if={"repair": "resample"}),
            Int("max_faces", "Maximum faces", 1_000_000, min=10_000, max=2_000_000, step=50_000, advanced=True,
                help="Hard input and output face budget. Failure publishes nothing."),
            collapsed=True,
        ),
        Section("Model", *frame_params(stage_sets=False)),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"steps": 18, "flights": 3, "resolution": 48, "draft_resolution": 40},
    test_variants=({"mode": "treads", "stair_type": "helix", "steps": 18, "flights": 1},
                   {"mode": "cut", "stair_type": "linear", "steps": 12, "flights": 1, "heading": 90.0}),
)
