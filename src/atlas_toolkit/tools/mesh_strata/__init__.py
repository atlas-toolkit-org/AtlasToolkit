"""
Strata Sculpt: cut a model into an ordered stack of capped slabs and dislocate them as a stack (suspended wafers, a
spiral staircase of sections, a seismic wave), in one stage or a sequence of stages.

Outputs <name>_strata.stl (and <name>_strata_stages/ with stages.json), signed atlas-toolkit:mesh-strata.
"""
from atlas_toolkit.core import (Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, frame_params,
                                influence_params)


def staged(v):
    return int(v.get("stages", 1)) > 1


def some_connectors(v):
    return bool(v.get("connectors"))


def organic(v):
    return some_connectors(v) and v.get("connector_style") == "organic"


def has_branches(v):
    key = "connector_branches_max" if v.get("connector_branch_count") == "random" else "connector_branches"
    return organic(v) and (v.get(key) or 0) > 0


def dislocated(v):
    return v.get("jitter_shift", 0) > 0 or v.get("jitter_spin", 0) > 0 or v.get("jitter_tilt", 0) > 0


TOOL = Tool(
    id="mesh-strata",
    name="Strata Sculpt",
    version="1.1",
    category="mesh",
    icon="strata",
    action="Sculpt",
    summary="Cut a model into a stack of capped slabs and dislocate them: floating wafers, a spiral of sections, a "
            "seismic wave.",
    tagline="Suspended layers and architectural dislocation, every slab a closed solid, ready for Plotter Vision.",
    description=(
        "The model is cut by parallel planes into an ordered stack of layers (up, front to back or side to side, "
        "optionally tilted). Every cut is capped, so each slab of a watertight model is a closed solid and rings keep "
        "their holes. Amount grows everything together: the slabs thin from the full model to the retained "
        "thickness, lift apart, follow a sideways wave (in one direction, or orbiting into a helix), turn about the "
        "stack axis from the anchor outward and, seeded, a share of them slip, spin and tilt out of line. Amount 0 "
        "is the untouched model. Acts on lets only a height band or a sphere come apart while the rest stays whole. "
        "Lengths are % of the model's height; with several stages every stage is saved in place for Contour Lines "
        "or Plotter Vision. Optional cylinders, square bars or organic branching supports fuse neighbouring strata "
        "into a connected sculpture. Seeded attachments follow the cut surfaces through the sequence."),
    inputs=Inputs("mesh", exclude=(r"(?i)_strata(?:_stage_\d+)?\.(stl|obj|ply)$",)),
    output=Output("{stem}_strata.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-strata",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("layers", "faces"),
    params=[
        Section(
            "Strata",
            Float("amount", "Amount", 1.0, min=0.0, max=1.5, step=0.05, decimals=2, slider=True,
                  help="Grows thinning, lift, wave, turn and dislocation together: 0 is the untouched model."),
            Int("layers", "Layers", 24, min=2, max=200, slider=True,
                help="Slabs in the stack. 12-40 read well on paper; past ~60 the wafers turn into hatching."),
            Float("retained", "Retained thickness", 75.0, unit="%", min=5.0, max=100.0, step=5.0, decimals=0,
                  slider=True,
                  help="Share of its layer each slab keeps at full amount; the rest is cut away. 100% keeps the "
                       "whole model, sliced."),
            Choice("direction", "Slice", "up", [("up", "Level"), ("front", "Front"), ("side", "Side")],
                   segmented=True,
                   help="The stack's direction: level wafers stacked up the height, or vertical sections from "
                        "the front backward (Front) or from side to side (Side)."),
            Float("tilt", "Tilt", 0.0, unit="°", min=0.0, max=80.0, step=5.0, decimals=1,
                  help="Tilts the cutting planes: oblique strata, like bedding in rock."),
            Float("tilt_toward", "Tilt toward", 0.0, unit="°", min=-180.0, max=180.0, step=15.0, decimals=0,
                  advanced=True, visible_if=lambda v: v.get("tilt", 0) > 0,
                  help="Which way the planes tilt, around the stack (0: across the model, 90: in depth)."),
        ),
        Section(
            "Stack",
            Float("separation", "Separation", 35.0, unit="%", min=0.0, max=400.0, step=5.0, decimals=0,
                  slider=True, help="Extra space between neighbouring slabs, % of a layer: the stack lifts apart."),
            Choice("anchor", "Anchored at", "base", [("base", "Base"), ("centre", "Centre"), ("top", "Top")],
                   segmented=True,
                   help="The layer that stays put: the base keeps standing on its plinth, the centre spreads both "
                        "ways, the top hangs."),
            Float("turn", "Turn", 0.0, unit="°", min=-720.0, max=720.0, step=5.0, decimals=1,
                  help="Rotation of the far end of the stack about its axis, growing layer by layer from the "
                       "anchor: a spiral staircase of sections."),
        ),
        Section(
            "Wave",
            Float("wave", "Wave", 0.0, unit="% h", min=0.0, max=60.0, step=1.0, decimals=1, slider=True,
                  help="Sideways shift of the layers, % of the height, following a sine through the stack."),
            Choice("wave_shape", "Shape", "planar", [("planar", "Back and forth"), ("orbit", "Orbit")],
                   segmented=True, visible_if=lambda v: v.get("wave", 0) > 0,
                   help="Back and forth in one direction (a seismic shift), or around the axis (a helix)."),
            Float("waves", "Waves", 1.5, min=0.1, max=12.0, step=0.25, decimals=2,
                  visible_if=lambda v: v.get("wave", 0) > 0, help="Periods of the wave over the stack."),
            Float("phase", "Phase", 0.0, unit="°", min=-180.0, max=180.0, step=15.0, decimals=0,
                  visible_if=lambda v: v.get("wave", 0) > 0),
            Float("wave_heading", "Heading", 0.0, unit="°", min=-180.0, max=180.0, step=15.0, decimals=0,
                  visible_if=lambda v: v.get("wave", 0) > 0 and v.get("wave_shape") == "planar",
                  help="Direction of the shift around the stack (0: across the model, 90: in depth)."),
        ),
        Section(
            "Dislocation",
            Float("jitter_shift", "Slip", 0.0, unit="% h", min=0.0, max=40.0, step=0.5, decimals=1,
                  help="Each chosen slab slides sideways by up to this, in its own direction."),
            Float("jitter_spin", "Spin", 0.0, unit="°", min=0.0, max=180.0, step=1.0, decimals=1,
                  help="Each chosen slab turns about its own centre by up to this."),
            Float("jitter_tilt", "Tilt", 0.0, unit="°", min=0.0, max=45.0, step=1.0, decimals=1,
                  help="Each chosen slab tips out of level by up to this: floating, unsettled wafers."),
            Float("share", "Slabs dislocated", 100.0, unit="%", min=0.0, max=100.0, step=5.0, decimals=0,
                  visible_if=dislocated, help="A share of the slabs slips; the others stay in line."),
            Seed("seed", "Seed", 0, visible_if=dislocated, help="Same seed, same dislocation."),
        ),
        Section("Connectors",
            Bool("connectors", "Connect neighbouring strata", False,
                 help="Fuse randomly distributed supports between facing cut surfaces. Requires closed solid input. Off preserves floating slabs."),
            Int("connector_count", "Supports per gap", 3, min=1, max=20, visible_if=some_connectors,
                help="Requested supports per facing pair of slab components. Small caps may fit fewer; every eligible pair needs at least one. Existing joined regions need none."),
            Seed("connector_seed", "Connection seed", 0, visible_if=some_connectors,
                 help="Rerolls support placement and shape without changing slab dislocation. Attachments follow their slabs through every stage."),
            Choice("connector_style", "Connector shape", "cylinder", [("cylinder", "Cylinder"),
                   ("bar", "Square bar"), ("organic", "Organic")], visible_if=some_connectors),
            Float("connector_diameter_min", "Thickness minimum", .6, unit="% h", min=.05, max=8., step=.1, decimals=2,
                  visible_if=some_connectors, help="Round diameter or square bar side. Drawn independently for each support; limited to fit both slabs safely. Equal endpoints give one thickness."),
            Float("connector_diameter_max", "Thickness maximum", 1., unit="% h", min=.05, max=8., step=.1, decimals=2,
                  visible_if=some_connectors),
            Float("connector_taper", "Tip thickness", .55, unit="× base", min=.15, max=1., step=.05, decimals=2,
                  visible_if=lambda v: some_connectors(v) and v.get("connector_style") == "organic",
                  help="Organic stem diameter at the receiving slab relative to its base; forks are finer still."),
            Float("connector_curve", "Curvature", .35, min=0., max=1., step=.05, decimals=2, slider=True,
                  visible_if=lambda v: some_connectors(v) and v.get("connector_style") == "organic",
                  help="Lateral bow within Maximum extent, also limited by stem length to keep bends gentle. Zero gives straight paths."),
            Float("connector_irregularity", "Irregularity", .15, min=0., max=1., step=.05, decimals=2, slider=True,
                  visible_if=lambda v: some_connectors(v) and v.get("connector_style") == "organic",
                  help="Seeded gentle variation in direction and thickness, within the same extent limit."),
            Choice("connector_branch_count", "Branch count", "fixed", [("fixed", "Fixed"), ("random", "Random range")],
                   visible_if=organic, help="One extra-branch count, or an independent inclusive integer draw per support. Main trunks are not counted."),
            Int("connector_branches", "Extra branches", 0, min=0, max=100,
                visible_if=lambda v: organic(v) and v.get("connector_branch_count", "fixed") != "random",
                help="Requested extra branches per support, up to 100. Dense branching uses finer tips; room and face budgets can limit achieved counts."),
            Int("connector_branches_min", "Branches minimum", 1, min=0, max=100,
                visible_if=lambda v: organic(v) and v.get("connector_branch_count") == "random",
                help="Smallest inclusive extra-branch count drawn independently for each support."),
            Int("connector_branches_max", "Branches maximum", 8, min=0, max=100,
                visible_if=lambda v: organic(v) and v.get("connector_branch_count") == "random",
                help="Largest inclusive count, up to 100. Equal endpoints give a fixed count; zero permits bare stems."),
            Choice("connector_branch_placement", "Branch origins", "split", [("split", "At split position"),
                   ("beginning", "Beginning"), ("middle", "Middle"), ("end", "End"),
                   ("random", "Along length"), ("mixed", "Mixed zones")], visible_if=has_branches,
                   help="Place roots along the visible parent stem: Beginning 5–30%, Middle 35–65%, End 70–95%, Random 5–95%. Mixed balances all three zones. At split position uses Branch split."),
            Float("connector_fork", "Branch split", 55., unit="% length", min=20., max=80., step=5., decimals=1,
                  visible_if=lambda v: has_branches(v) and v.get("connector_branch_placement", "split") == "split",
                  help="Where forks leave the trunk, measured from the lower slab. Higher values keep branches shorter."),
            Int("connector_branch_levels", "Branch levels", 1, min=1, max=4, visible_if=has_branches,
                help="Maximum generation: 1 grows every fork from the main trunk; 2–4 allow smaller twigs from earlier branches. Extra branches counts all generations together."),
            Seed("connector_branch_seed", "Branch seed", 0,
                 visible_if=lambda v: organic(v) and (v.get("connector_branch_count") == "random" or
                     (has_branches(v) and (v.get("connector_branch_placement", "split") != "split" or (v.get("connector_branch_levels") or 1) > 1))),
                 help="Rerolls branch counts, distributed origins and parent choices without moving slabs or changing main supports."),
            Float("connector_spread", "Branch spread", 25., unit="% cap width", min=0., max=80., step=5., decimals=1,
                  visible_if=has_branches,
                  help="Desired spread of the attachment tips across the slab, capped by Maximum extent and available interior space."),
            Float("connector_branch_thickness", "Branch thickness", .68, unit="× parent", min=.15, max=1., step=.05, decimals=2,
                  visible_if=has_branches,
                  help="Extra-branch root radius relative to its parent at the junction. Dense sets and deeper levels become finer to fit; tip taper still applies."),
            Float("connector_extent", "Maximum extent", 35., unit="% cap width", min=0., max=100., step=5., decimals=1,
                  visible_if=lambda v: some_connectors(v) and v.get("connector_style") == "organic",
                  help="Hard bound on all stem/branch centreline offsets from the direct line between slabs. Rod thickness is additional. Zero makes a straight, unforked stem.")),
        Section("Acts on", *influence_params(), collapsed=True),
        Section(
            "Stages",
            Int("stages", "Stages", 1, min=1, max=8, slider=True,
                help="1: one sculpted model. More: a sequence from intact to the full amount, each stage in place."),
            Bool("save_stages", "Also save every stage", True, visible_if=staged,
                 help="Stage files go in <name>_strata_stages/ with stages.json (its frame), so Contour Lines "
                      "draws them all with one scale and position."),
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
                Int("connector_segments", "Curved shape segments", 24, min=12, max=96, step=4,
                    visible_if=some_connectors, advanced=True,
                    help="Round support cross-sections, capped at 48 segments."),
                Int("max_faces", "Maximum faces", 1_000_000, min=10_000, max=2_000_000, step=50_000,
                    visible_if=some_connectors, advanced=True,
                    help="Hard input and output face budget when connectors are enabled. Failure publishes nothing."),
                Int("draft_faces", "Preview faces", 60_000, min=2_000, max=2_000_000, advanced=True,
                    help="Without connectors, Preview simplifies bigger models to this many faces. Connected previews and Sculpt use full geometry."),
                collapsed=True),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"layers": 6, "stages": 2, "jitter_tilt": 5.0, "connectors": True, "connector_style": "organic", "connector_branches": 1},
)
