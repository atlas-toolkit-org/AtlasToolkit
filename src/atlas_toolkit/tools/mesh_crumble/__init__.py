"""
Crumble: fracture part of a closed model into solid blocks, hollow it where they were, and let them collapse under
gravity, frozen at any moment.

Outputs <name>_crumble.stl, signed atlas-toolkit:mesh-crumble.
"""
from atlas_toolkit.core import (Bool, Choice, FileRef, Float, Inputs, Int, Output, Section, Seed, Text, Tool,
                                frame_params)


def on_side(v):
    return v.get("area") == "side"


def half(v):
    return on_side(v) and v.get("area_shape") == "half"


def patch(v):
    return on_side(v) and v.get("area_shape") == "patch"


def painting(v):
    return v.get("area") == "paint"


def floor_on(v):
    return bool(v.get("floor"))


def fusing(v):
    return bool(v.get("fuse"))


TOOL = Tool(
    id="mesh-crumble",
    name="Crumble",
    version="0.1.0",
    category="mesh",
    icon="crumble",
    action="Crumble",
    summary="Fracture part of a model into solid blocks that break away and collapse under gravity, leaving a hollow.",
    tagline="A sculpture coming down like a building, frozen at any moment, with real rigid-body physics.",
    description=(
        "Choose a side, a round patch, a painted area or the whole model. That part is cut into closed blocks of rock, "
        "shards, slabs or bricks, with exact booleans, and the body keeps a jagged crater as deep as Hollow depth. "
        "Cracks spread into the surface around it. The blocks then let go in a staggered order and fall, colliding "
        "with each other, the body and the floor, in a built-in seeded rigid-body solver. Collapse picks the moment "
        "shown: 0% is the cracked model, about 50% is mid-fall, 100% runs until the rubble has settled. Output is loose "
        "closed solids, or one fused solid with thin bridges for printing. The input must be closed; resampling "
        "repair is opt-in. Lengths are % of the model's height."),
    inputs=Inputs("mesh", exclude=(r"(?i)_crumble\.(stl|obj|ply)$",)),
    output=Output("{stem}_crumble.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-crumble",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    viewer=".viewer:SelectionView",
    sample=".engine:make_sample",
    columns=("blocks", "falling", "faces"),
    preset_inputs=("area", "selection", "selection_file", "repair"),
    params=[
        Section(
            "Area",
            Choice("area", "Area", "side", [("side", "Side"), ("paint", "Paint"), ("whole", "All")],
                   segmented=True,
                   help="Side needs no painting. Paint: open the Selection tab and paint what breaks. In Workflows use "
                        "Aim in tool, paint, then Back to workflow. All: the whole model."),
            Choice("side", "Side", "left", [("front", "Front"), ("right", "Right"), ("back", "Back"), ("left", "Left"),
                   ("top", "Top")], visible_if=on_side),
            Float("side_turn", "Turn side by", 25.0, unit="°", min=-90.0, max=90.0, step=5.0, decimals=0,
                  visible_if=lambda v: on_side(v) and v.get("side") != "top",
                  help="Turns the side around the model, for a three-quarter break."),
            Choice("area_shape", "Shape", "half", [("half", "Slice"), ("patch", "Patch")],
                   segmented=True, visible_if=on_side,
                   help="Slice takes everything on that side, straight across the model. Patch is a round crater where "
                        "a line from this side first meets the model."),
            Float("coverage", "Coverage", 48.0, unit="%", min=3.0, max=100.0, step=1.0, decimals=0, slider=True,
                  visible_if=half, help="How far across the model the break reaches from this side."),
            Float("patch_radius", "Patch radius", 22.0, unit="% h", min=3.0, max=80.0, step=1.0, decimals=0, slider=True,
                  visible_if=patch),
            Float("patch_height", "Patch height", 15.0, unit="% h", min=-50.0, max=50.0, step=1.0, decimals=0,
                  visible_if=patch, help="Moves the patch up or down from the model's middle (across, for Top)."),
            Float("edge_rough", "Edge roughness", 7.0, unit="% h", min=0.0, max=40.0, step=0.5, decimals=1, slider=True,
                  help="How far the edge of the area wanders."),
            Float("depth", "Hollow depth", 24.0, unit="% h", min=2.0, max=100.0, step=1.0, decimals=0, slider=True,
                  help="How deep below the surface the model breaks up: a shallow scar, a deep cavity, or at 100 "
                       "everything inside the area."),
            FileRef("selection_file", "Selection file", "", exts=(".json",), visible_if=painting,
                    help="Optional exported selection JSON for the command line or Workflows. Overrides the embedded "
                         "selection. Bound to this exact mesh."),
            Text("selection", "Embedded selection", "", advanced=True, visible_if=painting,
                 help="Written by the selection editor, kept with settings and Workflows."),
        ),
        Section(
            "Blocks",
            Choice("block_style", "Style", "rock", [("rock", "Rock"), ("shards", "Shards"), ("slabs", "Slabs"),
                   ("bricks", "Bricks")],
                   help="Rock: faceted, roughly round chunks. Shards: long splinters. Slabs: flat plates. Bricks: "
                        "staggered masonry courses."),
            Float("block_size", "Block size", 8.0, unit="% h", min=1.5, max=35.0, step=0.5, decimals=1, slider=True,
                  help="Typical block, % of the model's height. Half the size makes eight times the blocks."),
            Float("variety", "Variety", 0.7, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="0: even blocks, released strictly in order. 1: very uneven blocks, with a random order."),
            Float("brick_ratio", "Brick length", 2.0, unit="× size", min=1.0, max=4.0, step=0.1, decimals=1,
                  visible_if={"block_style": "bricks"}),
            Float("rubble", "Rubble", 30.0, unit="%", min=0.0, max=100.0, step=5.0, decimals=0, slider=True,
                  help="Share of blocks that break again into smaller pieces, mostly near the edge of the area."),
            Int("rubble_parts", "Rubble pieces", 6, min=1, max=20, visible_if=lambda v: (v.get("rubble") or 0) > 0,
                help="At most this many extra pieces from one block."),
            Float("chip", "Chipped corners", 0.45, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="Knocks corners off the blocks. 0 keeps clean cells; bricks stay square."),
            Float("gap", "Crack gap", 0.25, unit="% h", min=0.02, max=3.0, step=0.05, decimals=2,
                  help="The space between blocks that have not fallen yet."),
            Seed("seed", "Seed", 7, help="Same seed, same fracture and same collapse."),
        ),
        Section(
            "Collapse",
            Float("collapse", "Collapse", 45.0, unit="%", min=0.0, max=100.0, step=1.0, decimals=0, slider=True,
                  help="The moment shown. 0: cracked, nothing has moved. About 50: mid-fall, with early blocks already "
                       "landed. 100: runs until everything has come to rest."),
            Choice("order", "Failure order", "bottom", [("bottom", "Undermined (lowest first)"),
                   ("top", "From the top"), ("outside", "Outermost first"), ("edge", "Centre outward"),
                   ("random", "At random")],
                   help="Which blocks let go first. Undermined is the demolition look: the lower blocks go and the "
                        "rest drops after them."),
            Float("stagger", "Stagger", 1.6, unit="s", min=0.0, max=5.0, step=0.1, decimals=1, slider=True,
                  help="Time between the first block and the last letting go. 0: all at once."),
            Float("cling", "Clinging blocks", 22.0, unit="%", min=0.0, max=100.0, step=1.0, decimals=0, slider=True,
                  help="Share of blocks that never fall, nearest the edge of the area first: they stay on the model, "
                       "cracked and tilted."),
            Float("loosen", "Loosened", 0.6, min=0.0, max=1.5, step=0.05, decimals=2, slider=True,
                  help="How far clinging blocks have slipped and tilted out of place."),
            Float("gravity", "Gravity", 1.0, unit="×", min=0.0, max=2.0, step=0.05, decimals=2, slider=True,
                  help="1: as in life, at the model's scale of 2 m. Lower it for a slow-motion crumble; 0 only lets "
                       "Burst drive the blocks."),
            Float("burst", "Burst", 0.35, unit="m/s", min=0.0, max=6.0, step=0.05, decimals=2, slider=True,
                  help="How hard blocks are pushed out as they let go. They have to clear the crater to fall past it."),
            Float("wind", "Pull outward", 0.0, unit="× g", min=-1.0, max=2.0, step=0.05, decimals=2,
                  help="A steady sideways pull away from the area's side, as the blocks streaming off in a gale."),
            Float("spin", "Tumble", 0.45, min=0.0, max=1.5, step=0.05, decimals=2, slider=True,
                  help="How fast blocks start turning as they let go."),
            Float("friction", "Friction", 0.65, min=0.0, max=1.5, step=0.05, decimals=2, slider=True,
                  help="Low: rubble spreads out. High: it holds steep piles."),
            Bool("floor", "Floor", True, help="Rubble lands on a floor below the model. Off: blocks can fall out of the "
                                              "picture, and those far below are left out."),
            Float("floor_drop", "Floor below base", 0.0, unit="% h", min=0.0, max=100.0, step=1.0, decimals=0,
                  visible_if=floor_on, help="Lowers the floor below the model's lowest point."),
            Bool("base_plate", "Add a base plate", False, visible_if=floor_on,
                 help="A slab under everything, in the output: the rubble and the model then stand on something."),
        ),
        Section(
            "Cracks",
            Float("crack_zone", "Crack reach", 14.0, unit="% h", min=0.0, max=60.0, step=1.0, decimals=0, slider=True,
                  help="How far the cracks spread beyond the area over the intact surface."),
            Float("crack_width", "Crack width", 0.3, unit="% h", min=0.0, max=2.0, step=0.05, decimals=2,
                  help="The widest a crack gets; they taper off. 0 turns cracks off."),
        ),
        Section(
            "Output",
            Bool("fuse", "Fuse into one solid", False,
                 help="Off: closed blocks stay loose, where the collapse left them. On: one printable solid. Blocks "
                      "weld where they touch and thin bridges reach whatever still floats."),
            Float("weld", "Weld overlap", 1.5, unit="%", min=0.0, max=10.0, step=0.5, decimals=1, visible_if=fusing,
                  advanced=True, help="How much each block swells to weld with what it touches."),
            Float("bridge_reach", "Bridge reach", 40.0, unit="% h", min=0.0, max=200.0, step=1.0, decimals=0,
                  visible_if=fusing, help="Floating pieces farther away than this are left out."),
            Float("bridge_size", "Bridge thickness", 1.2, unit="% h", min=0.1, max=6.0, step=0.1, decimals=1,
                  visible_if=fusing),
            Bool("separate", "Also save every block", False,
                 help="Write each block and the standing body to its own file in a <name>_blocks_<K>pcs folder."),
        ),
        Section(
            "Quality",
            Int("resolution", "Collision resolution", 110, min=48, max=256, step=8, advanced=True,
                help="Samples along the longest side of the distance grid the blocks land on. Finer costs more."),
            Int("draft_resolution", "Preview resolution", 72, min=32, max=192, step=8, advanced=True,
                help="The same for Preview. Blocks are always exact; only where they land differs slightly."),
            Float("min_block", "Smallest block", 0.5, unit="% h", min=0.05, max=5.0, step=0.05, decimals=2, advanced=True,
                  help="Crumbs smaller than this (edge of a cube of the same volume) are left out."),
            Int("max_blocks", "Maximum blocks", 2000, min=10, max=6000, step=50, advanced=True,
                help="Rubble stops at this limit. If the blocks alone exceed it the run stops, publishing nothing. The "
                     "simulation slows with the number of blocks."),
            Choice("repair", "Open input", "strict", [("strict", "Require closed solid"),
                   ("resample", "Repair by resampling")],
                   help="Repair changes the source surface and may soften detail. To paint a repaired input, save it "
                        "first and paint that model."),
            Int("repair_resolution", "Repair resolution", 128, min=64, max=320, step=16, advanced=True,
                visible_if={"repair": "resample"}),
            Int("max_faces", "Maximum faces", 2_000_000, min=10_000, max=4_000_000, step=50_000, advanced=True,
                help="Hard input and output face budget. Failure publishes nothing."),
            collapsed=True,
        ),
        Section("Model", *frame_params(stage_sets=False)),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"block_size": 28.0, "rubble": 0.0, "collapse": 25.0, "resolution": 48, "draft_resolution": 40,
                 "stagger": 0.4},
)
