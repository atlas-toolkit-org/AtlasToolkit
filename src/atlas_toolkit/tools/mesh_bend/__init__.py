"""Bend a model around an arc-length spine. Atlas signature: atlas-toolkit:mesh-bend."""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Tool, frame_params


def staged(v):
    return int(v.get("stages", 1)) > 1


TOOL = Tool(
    id="mesh-bend", name="Bend", version="0.1.0", category="mesh", icon="bend-spine", action="Bend",
    summary="Curve a model into an arc, a sculptural S or a spatial wave, with anchored ends and transported sections.",
    tagline="A gesture through the whole form. Shape the silhouette, then draw its flowing contours.",
    description=("Bend carries cross-sections along a spine of unchanged length, rotating them with the curve. "
                 "Set the angle, direction and active range; straight parts outside the range move as rigid ends. "
                 "Hold the lower, middle or upper section in place. Sweep turns the bend plane through space, "
                 "while Roll turns the sections around the spine. Arc, Soft arc, S-bend and Wave create different "
                 "line rhythms. The Spine tab edits the gesture; Preview shows the actual refined geometry. "
                 "A local fold guard can reduce the angle on thick models; distant self-contact is not solved. "
                 "Source units and placement are retained. No Blender is needed."),
    inputs=Inputs("mesh", exclude=(r"(?i)_bend(?:_stage_\d+)?\.(stl|obj|ply)$",)),
    output=Output("{stem}_bend.stl", "mesh", formats=(".stl", ".obj", ".ply"),
                  format_notes={".obj": "Bend exports refined triangles; use Remesh/Decimate afterward for a graphic network.",
                                ".ply": "Bend exports refined triangles; source polygon loops are triangulated."}),
    signature="atlas-toolkit:mesh-bend", engine=".engine:run", runs_in="process", preview="mesh",
    viewer=".viewer:SpineView", sample=".engine:make_sample", columns=("angle", "faces"),
    params=[
        Section("Bend",
            Choice("profile", "Profile", "soft", [("arc", "Circular arc"), ("soft", "Soft arc"),
                   ("s", "S-bend"), ("wave", "Wave")],
                   help="Arc/Soft arc: Angle is the end's turn. S-bend/Wave: Angle is the peak tangent deflection; both ends finish parallel."),
            Float("angle", "Angle", 70., unit="°", min=-300., max=300., step=5., decimals=1, slider=True,
                  help="Signed bend angle. Zero with Roll zero is exactly the source. Negative bends in the opposite direction."),
            Float("direction", "Direction", 0., unit="°", min=-180., max=180., step=5., decimals=1, slider=True,
                  help="Around the bend axis: for Up, 0° bends toward +X, 90° toward +Y (away from the face), −90° toward the face."),
            Float("amount", "Amount", 1., min=0., max=1., step=.05, decimals=2, slider=True,
                  help="Scales Angle and Roll together. A sequence runs from zero to this amount."),
            Float("waves", "Waves", 1.5, min=.5, max=4., step=.25, decimals=2, slider=True,
                  visible_if={"profile": "wave"}, help="Oscillations inside a gently feathered range. Fewer waves give a stronger readable silhouette.")),
        Section("Range and anchor",
            Choice("bend_axis", "Bend along", "z", [("z", "Up (Z)"), ("x", "Across (X)"), ("y", "Depth (Y)")],
                   help="Axes refer to the model frame after Up, Front and Extra turn. Across uses Y/Z as its transverse plane; Depth uses Z/X."),
            Float("bend_from", "From", 0., unit="% axis", min=0., max=99.9, step=1., decimals=1, slider=True,
                  help="Start of the active bend, measured from the low end of the selected axis. Must be below To."),
            Float("bend_to", "To", 100., unit="% axis", min=.1, max=100., step=1., decimals=1, slider=True,
                  help="End of the active bend. Material beyond it follows as a rigid end, without a tear or a positional blend."),
            Choice("anchor", "Hold section", "base", [("base", "Range start"), ("centre", "Range middle"), ("tip", "Range end")],
                   help="The chosen cross-section keeps its original position AND orientation. With Range start, everything below From stays fixed."),
            Float("neutral_u", "Spine offset U", 0., unit="% h", min=-100., max=100., step=1., decimals=1, advanced=True,
                  help="Move the neutral spine sideways; 0 is the bounding-box centre. For Up, U is X; lengths use original model height."),
            Float("neutral_v", "Spine offset V", 0., unit="% h", min=-100., max=100., step=1., decimals=1, advanced=True,
                  help="Second transverse offset. For Up, V is Y. Offsets change which side is compressed or stretched.")),
        Section("Spatial gesture",
            Float("sweep", "Plane sweep", 0., unit="°", min=-720., max=720., step=10., decimals=1, slider=True,
                  help="Turn the bending direction along the range, taking a planar arc into a spatial curl. Zero stays in one plane."),
            Float("roll", "Section roll", 0., unit="°", min=-540., max=540., step=10., decimals=1, slider=True,
                  help="Rotate cross-sections around the curved spine. Independent of Plane sweep; zero uses rotation-minimising transport.")),
        Section("Quality",
            Bool("fold_guard", "Protect inner curve", True,
                 help="Reduce Angle to keep a conservative local Jacobian of at least 0.15 across the model's transverse bounds. Reports achieved angle. Does not detect distant self-contact."),
            Bool("refine", "Refine coarse faces", True,
                 help="Conforming linear edge splits before bending, preserving the original surface rather than smoothing away relief. No decimation in Preview."),
            Int("segments", "Bend resolution", 48, min=8, max=192, step=8, visible_if={"refine": True},
                help="Maximum longitudinal edge span is active-range length / this value. More samples smooth coarse meshes. Wave and roll may need 96."),
            Int("max_faces", "Maximum faces", 1_000_000, min=10_000, max=2_000_000, step=50_000, advanced=True,
                help="Hard limit for input and refined output, per stage. A budget failure writes nothing; reduce Bend resolution or simplify the input.")),
        Section("Stages",
            Int("stages", "Stages", 1, min=1, max=8, slider=True,
                help="One model, or an evenly spaced intact-to-bent sequence. Each stage is evaluated from the same rest shape."),
            Bool("save_stages", "Also save every stage", True, visible_if=staged,
                 help="Signed stages with stages.json share a frame in Contour Lines and the other drawing tools."),
            Choice("layout", "Primary output", "final", [("final", "Last stage"), ("progression", "All stages")], visible_if=staged),
            Choice("axis", "Side by side along", "x", [("x", "X"), ("y", "Y"), ("z", "Z")],
                   visible_if=lambda v: staged(v) and v.get("layout") == "progression"),
            Float("gap", "Gap", 15., unit="% h", min=0., max=200., step=1., decimals=1,
                  visible_if=lambda v: staged(v) and v.get("layout") == "progression")),
        Section("Model", *frame_params(stage_sets=False)),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    preset_inputs=("bend_axis", "neutral_u", "neutral_v"),
    test_values={"segments": 12, "stages": 2},
)
