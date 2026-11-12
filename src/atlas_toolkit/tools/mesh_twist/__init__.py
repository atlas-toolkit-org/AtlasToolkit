"""
Twist: wring a model with a lateral S-sway, an undulating torsion and a spiral ripple, in one stage or a sequence of
stages, for any model, without Blender.

Outputs <name>_twist.stl (and <name>_twist_stages/ with stages.json), signed atlas-toolkit:mesh-twist.
"""
from atlas_toolkit.core import (Bool, Choice, Float, Inputs, Int, Output, Section, Tool, frame_params)


def staged(v):
    return int(v.get("stages", 1)) > 1


TOOL = Tool(
    id="mesh-twist",
    name="Twist",
    version="1.1",
    category="mesh",
    icon="twist",
    action="Twist",
    summary="Wring a model: a lateral S-sway, an undulating torsion about its up axis and a spiral ripple.",
    tagline="One twisted model, or a sequence of stages from intact to fully wrung, to draw with Contour Lines.",
    description=(
        "Three motions, all smooth functions of height, so the model bends without tearing. Turn wrings it about "
        "its up axis (the sign sets the direction; waves over the height decide whether it wrings one way or "
        "back and forth). Sway bends it sideways in an S that grows toward the top. Ripple corrugates the "
        "surface in rings or, with spiral lobes, in a helix. Amount scales all three. With several stages, the "
        "amplitudes ramp up from intact to full (0, 0.3, 0.62, 1 for four stages) and every stage is saved in place, "
        "ready for Contour Lines, which draws a stage set with one frame. Up and Front say how the model "
        "stands; lengths are % of its height, so settings carry over between models."),
    inputs=Inputs("mesh", exclude=(r"(?i)_twist(?:_stage_\d+)?\.(stl|obj|ply)$",)),
    output=Output("{stem}_twist.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-twist",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("stages", "faces"),
    params=[
        Section(
            "Twist",
            Float("amount", "Amount", 1.0, min=0.0, max=1.5, step=0.05, decimals=2, slider=True,
                  help="Scales the turn, sway and ripple together: 1 applies them as set."),
            Float("turn", "Turn", 35.52, unit="°", min=-360.0, max=360.0, step=1.0, decimals=2,
                  help="Largest rotation about the up axis. The sign is the direction (negative = clockwise seen "
                       "from above). The Serpentine preset wrings the other way, -54°."),
            Float("sway", "Sway", 12.0, unit="% h", min=0.0, max=40.0, step=0.5, decimals=2,
                  help="Sideways S-bend, % of the model's height, growing from the base to the top. Above ~15% a "
                       "head starts to read as a different shape."),
            Float("ripple", "Ripple", 2.25, unit="% h", min=0.0, max=8.0, step=0.05, decimals=3,
                  help="Corrugation of the surface, % of the height. Detailed scans want less (Serpentine: 0.65%) or "
                       "the relief turns into noise."),
        ),
        Section(
            "Stages",
            Int("stages", "Stages", 1, min=1, max=8, slider=True,
                help="1: one twisted model. More: a sequence from intact to the full amount, each stage in place."),
            Bool("save_stages", "Also save every stage", True, visible_if=staged,
                 help="Stage files go in <name>_twist_stages/ with stages.json (its frame), so Contour Lines draws "
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
        Section(
            "Shape",
            Float("phase", "Phase", 22.92, unit="°", min=-180.0, max=180.0, step=5.0, decimals=2,
                  help="Shifts every wave along the height. Keep it fixed through a sequence so it reads as one "
                       "motion growing."),
            Float("turn_waves", "Turn waves", 0.7003, min=0.05, max=4.0, step=0.05, decimals=4,
                  help="Waves of torsion over the height: under ~0.7 the model wrings one way; above 1 it twists "
                       "back and forth."),
            Float("sway_waves", "Sway waves", 0.9549, min=0.05, max=4.0, step=0.05, decimals=4,
                  help="S-bends over the height: ~1 a single S, 1.75 a serpentine double S."),
            Float("ripple_waves", "Ripple waves", 6.3662, min=0.5, max=30.0, step=0.5, decimals=4,
                  help="Rings of ripple over the height."),
            Int("lobes", "Spiral lobes", 3, min=0, max=12,
                help="0: level rings; 1 or more: the ripple climbs around the model as a helix with this many "
                     "starts."),
            Choice("ripple_dir", "Ripple pushes", "radial", [("radial", "Out from the axis"),
                                                             ("normal", "Along the surface"),
                                                             ("smooth", "Along the smoothed surface")],
                   advanced=True,
                   help="Out from the axis depends on position only, so thin parts cannot tear (the default). "
                        "Along the surface follows the normals (the Torsion sequence preset): livelier, but it tears "
                        "fingers and fine relief at large ripple."),
            Float("fade", "Calm near the axis", 15.0, unit="% h", min=0.0, max=50.0, step=1.0, decimals=1,
                  advanced=True,
                  help="The ripple fades within this distance of the axis, where a spiral has no direction (keeps "
                       "the crown from crumpling). 0 = off."),
            Choice("pivot", "Turn about", "centre", [("centre", "The vertical axis"), ("spine", "Its spine")],
                   advanced=True, segmented=True,
                   help="Its spine: a smoothed centre line through the model's height bands, so a part that leans "
                        "out (a raised arm) wrings about itself."),
        ),
        Section("Model", *frame_params(stage_sets=False),
                Int("subdiv", "Smoothing subdivision", 1, min=0, max=2, advanced=True,
                    help="Loop subdivision before twisting (each level ×4 faces): smoother bends and contours. "
                         "Skipped when it would pass 2 million faces.")),
        Section("Quality",
                Int("draft_faces", "Preview faces", 60_000, min=2_000, max=2_000_000, advanced=True,
                    help="Preview simplifies bigger models to this many faces and skips the subdivision; Twist "
                         "always uses the full model."),
                collapsed=True),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"stages": 2, "subdiv": 0},
    legacy=("projects/blender3d_project (skill plotter-contour-art: head_plot_pipeline.py add_wave)",),
)
