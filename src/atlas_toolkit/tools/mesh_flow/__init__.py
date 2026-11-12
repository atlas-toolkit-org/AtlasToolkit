"""
Flow Warp: pull a model through turbulent currents (curl noise, a vortex, a streaming pull), every vertex carried
along a smooth velocity field, in one stage or a sequence of stages sampled along the same trajectories.

Outputs <name>_flow.stl (and <name>_flow_stages/ with stages.json), signed atlas-toolkit:mesh-flow.
"""
from atlas_toolkit.core import (Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, Vec3, frame_params,
                                influence_params)


def staged(v):
    return int(v.get("stages", 1)) > 1


def has_current(v):
    return float(v.get("current", 1.0)) > 0


def has_vortex(v):
    return float(v.get("spin", 0.0)) != 0


def has_pull(v):
    return float(v.get("pull", 0.0)) > 0


TOOL = Tool(
    id="mesh-flow",
    name="Flow Warp",
    version="1.0",
    category="mesh",
    icon="flow",
    action="Warp",
    summary="Pull a model through turbulent currents: eddies, a vortex and a streaming wind sweep the surface "
            "without tearing it.",
    tagline="A face swept into a vortex, a torso drawn out in flowing folds: draw it with Contour Lines.",
    description=(
        "Every vertex is carried along a smooth velocity field by Runge-Kutta integration, so the surface bends, "
        "folds and stretches but stays in one piece. The field mixes three motions. Current is curl noise: "
        "divergence-free eddies (Swirl size sets the largest, Turbulence adds smaller ones), seeded. Vortex "
        "spins the model about an axis through a centre, rigid in its core and falling off outside; Draw in "
        "winds it into spiral arms. Pull drifts it one way, combed into strands by Streaks. Travel and the "
        "lengths are % of the model's height, so settings carry over between models. Anchor base keeps the "
        "plinth in place and Acts on limits the flow to a band or a sphere. Amount is how far along the flow: "
        "0 is the untouched model, and the stages of a sequence are points on the same trajectories. Long edges "
        "are split first so small eddies have something to bend. Strong flows can fold the surface through "
        "itself: the report gives the stretch."),
    inputs=Inputs("mesh", exclude=(r"(?i)_flow(?:_stage_\d+)?\.(stl|obj|ply)$",)),
    output=Output("{stem}_flow.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-flow",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("stages", "faces", "stretch"),
    params=[
        Section(
            "Flow",
            Float("amount", "Amount", 1.0, min=0.0, max=1.5, step=0.05, decimals=2, slider=True,
                  help="How far along the flow: 0 is the untouched model, 1 the full travel, spin and pull."),
            Float("travel", "Travel", 12.0, unit="% h", min=0.0, max=80.0, step=1.0, decimals=1, slider=True,
                  help="How far the current carries the surface, % of the model's height (its RMS speed × the "
                       "amount). 8-15% keeps a face recognisable while it is swept; past ~30% it dissolves."),
            Float("swirl", "Swirl size", 60.0, unit="% h", min=5.0, max=200.0, step=5.0, decimals=1,
                  help="Wavelength of the largest eddies, % of the height. Small: many tight curls; large: a few "
                       "broad folds. Under ~15% the curls get finer than a pen's contour spacing."),
            Float("turbulence", "Turbulence", 0.45, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="Smaller eddies on top of the largest (up to three octaves, each half the size): 0 smooth "
                       "folds, 1 broken, marbled currents."),
            Seed("seed", "Seed", 0, help="Same seed, same current and streaks."),
        ),
        Section(
            "Mix",
            Float("current", "Current", 1.0, min=0.0, max=2.0, step=0.05, decimals=2, slider=True,
                  help="Strength of the turbulent current (× Travel). 0 turns it off: vortex and pull only."),
            Float("spin", "Vortex spin", 0.0, unit="°", min=-1080.0, max=1080.0, step=15.0, decimals=0,
                  help="How far a point on the vortex core's edge turns at amount 1 (the sign is the direction, + "
                       "counter-clockwise seen from the axis' end). 0: no vortex."),
            Float("pull", "Pull", 0.0, unit="% h", min=0.0, max=100.0, step=1.0, decimals=1,
                  help="A steady drift in one direction, % of the height at amount 1: a windblown figure. 0: none."),
        ),
        Section(
            "Vortex",
            Choice("vortex_axis", "Axis", "front", [("front", "Through the front"), ("up", "Up"),
                                                    ("side", "Side to side")], segmented=True,
                   visible_if=has_vortex,
                   help="Through the front swirls a face like a whirlpool seen head-on; Up wrings the model "
                        "like a tornado."),
            Vec3("vortex_centre", "Centre", (0.0, 0.0, 25.0), unit="%", min=-100.0, max=100.0, step=5.0,
                 decimals=1,
                 visible_if=has_vortex,
                 help="From the model's centre, % of its size along X (across), Y (depth, front is −) and Z (up). "
                      "A head's face is near (0, -40, 20)."),
            Float("vortex_radius", "Core radius", 25.0, unit="% h", min=2.0, max=150.0, step=1.0, decimals=1,
                  visible_if=has_vortex,
                  help="Inside the core the model turns as one; outside, the turn falls off with distance, so the "
                       "surface winds into a spiral."),
            Float("vortex_depth", "Reach along axis", 2.0, unit="× core", min=0.2, max=20.0, step=0.1, decimals=1,
                  advanced=True,
                  visible_if=has_vortex,
                  help="The vortex fades along its axis over this many core radii (large: a column that runs "
                       "through the whole model)."),
            Float("inflow", "Draw in", 0.0, min=0.0, max=1.0, step=0.05, decimals=2,
                  visible_if=has_vortex,
                  help="Share of the swirl that also pulls toward the axis: spiral arms wound into the eye."),
        ),
        Section(
            "Pull",
            Choice("pull_dir", "Direction", "+x", [("+x", "+X"), ("-x", "−X"), ("-y", "Forward"),
                                                  ("+y", "Back"), ("+z", "Up"), ("-z", "Down")], segmented=True,
                   visible_if=has_pull,
                   help="Which way the drift goes, in the model's frame (front is −Y: Forward comes toward the "
                        "viewer of a head)."),
            Float("streaks", "Streaks", 0.5, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=has_pull,
                  help="Neighbouring strands drift by different lengths (a noise across the direction of travel, "
                       "as wide as half the swirl): combed hair, wind-torn cloth."),
        ),
        Section(
            "Anchor",
            Float("anchor", "Anchor base", 8.0, unit="% h", min=0.0, max=100.0, step=1.0, decimals=1,
                  help="This much of the model above its base never moves: a plinth or a neck stays put while "
                       "the rest is swept away."),
            Float("anchor_blend", "Anchor blend", 25.0, unit="% h", min=0.0, max=100.0, step=1.0, decimals=1,
                  help="Over this height above the anchor the flow fades in, so the surface bends out of the "
                       "fixed part instead of shearing off it."),
        ),
        Section("Acts on", *influence_params(), collapsed=True),
        Section(
            "Stages",
            Int("stages", "Stages", 1, min=1, max=8, slider=True,
                help="1: one warped model. More: a sequence from intact to the full amount, each stage in place "
                     "and on the same trajectories."),
            Bool("save_stages", "Also save every stage", True, visible_if=staged,
                 help="Stage files go in <name>_flow_stages/ with stages.json (its frame), so Contour Lines draws "
                      "them all with one scale, position and slice grid."),
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
                Bool("refine", "Split long edges", True, advanced=True,
                     help="Split edges longer than the flow's finest visible feature (its smaller eddies, the spacing "
                          "of a vortex's arms, the streaks) / Edges per eddy before warping, so the flow "
                          "has vertices to bend (low-poly models would otherwise fold as flat facets)."),
                Int("edges_per_wave", "Edges per eddy", 10, min=3, max=40, advanced=True,
                    visible_if={"refine": True},
                    help="Finer refinement: smoother curls, more faces (capped at 2 million)."),
                Float("step", "Integration step", 0.2, unit="× eddy", min=0.02, max=1.0, step=0.05, decimals=2,
                      advanced=True,
                      help="No point moves more than this share of the flow scale per Runge-Kutta step. Smaller "
                           "is more exact and slower; the result converges below ~0.25."),
                Int("draft_faces", "Preview faces", 60_000, min=2_000, max=2_000_000, advanced=True,
                    help="Preview simplifies bigger models to this many faces and skips the edge splitting; Warp "
                         "always uses the full model."),
                collapsed=True),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"stages": 2, "refine": False},
    legacy=(),
)
