"""
Sketch Hatching: a 3D model drawn as an illustrator's pen sketch - outline, suggestive contours, valleys and creases,
and hatching that follows the form in tone bands, every visibility decision by ray casting, for any model, without
Blender.

Outputs <name>_sketch.svg, signed <desc>sketch-hatching</desc> so Atlas links them to this tool.
"""
from atlas_toolkit.core import (CAMERA_KEYS, FRAME_KEYS, Bool, Choice, Float, Inputs, Int, Output, Section, Seed,
                                Tool, camera_params, frame_params, paper_params)

FIELDS = [("principal", "The form"), ("slice", "Cross-sections"), ("isophote", "Bands of light"),
          ("screen", "One angle")]


def hatched(v):
    return bool(v.get("hatching"))


def focused(v):
    return bool(v.get("focus"))


TOOL = Tool(
    id="sketch-hatching",
    name="Sketch Hatching",
    version="1.0.2",
    category="mesh2svg",
    icon="sketch",
    action="Draw",
    summary="Draw a 3D model as a pen sketch: outline, suggestive contours and hatching that follows the form.",
    tagline="An illustrator's line: lost-and-found outline, feature lines, form-following hatching in tone bands.",
    description=(
        "Aim the camera in the Aim tab. Lines: the outline comes straight from the ray-cast silhouette, unbroken, "
        "heavier where the form turns into shadow and thinning into the light; inner contours, suggestive "
        "contours (where a contour would appear if you moved a little), valleys (lids, lips, folds) and creases "
        "follow. Tone: a key light with soft cast shadows and ambient occlusion, simplified as an artist would. "
        "Hatching: evenly spaced strokes along the form's main curvature, one pass per tone band, crossing in the "
        "darks, never closer than 2.5 pen widths. Complexity scales how many lines survive and how dense the "
        "hatching is. A focus point gives one region (a face) finer lines and hatching and its own portrait light. "
        "Two pens: fine (hatching, details) and bold (outline, contours). Unlike Hatch Maker this draws through a "
        "perspective camera with cast shadows, ambient occlusion and the curvature lines of the form."),
    inputs=Inputs("mesh"),
    output=Output("{stem}_sketch.svg", "svg"),
    signature="<desc>sketch-hatching</desc>",
    engine=".engine:run",
    runs_in="process",
    preview="svg",
    columns=("strokes",),
    viewer="atlas_toolkit.tools.plotter_vision.aim:AimView",
    preset_skip=CAMERA_KEYS + FRAME_KEYS,
    params=[
        Section("Camera", *camera_params(theta=80.0, psi=155.0, radius=262.0, fov=38.0)),
        Section(
            "Drawing",
            Float("complexity", "Complexity", 0.5, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="0 sparse, 0.5 as tuned, 1 rich: how many interior lines survive and how dense and how "
                       "layered the hatching is."),
            Bool("hatching", "Hatching", True, help="Off: lines only (outline, contours, details)."),
            Float("density", "Hatch density", 1.0, unit="×", min=0.3, max=3.0, step=0.05, decimals=2, slider=True,
                  visible_if=hatched, help="More strokes (closer) above 1; never closer than 2.5 pen widths."),
            Choice("field", "Strokes follow", "principal", FIELDS, visible_if=hatched,
                   help="The form: across the main curvature (around an arm, across a brow), with cross-sections "
                        "where the form is round. Cross-sections: level slices. Bands of light: along the tone. "
                        "One angle: classic parallel hatching."),
            Float("outline_weight", "Outline weight", 0.16, unit="mm", min=0.0, max=0.6, step=0.02, decimals=2,
                  advanced=True,
                  help="The outline returns beside itself this far apart, tapered: heavier in the middle, thin at "
                       "the ends, no extra pen lift. 0 = a single line."),
            Float("wobble", "Hand wobble", 0.07, unit="mm", min=0.0, max=0.6, step=0.01, decimals=2, advanced=True,
                  help="A slow, sure waver along the lines; 0 = mechanical."),
            Seed("seed", "Seed", 7, help="Hatch starts and the hand's waver."),
        ),
        Section(
            "Light",
            Float("light_az", "Light from (azimuth)", -40.0, unit="°", min=-180.0, max=180.0, step=5.0, decimals=1,
                  help="Around the up axis; negative = from the left. A light near the camera (|azimuth| under "
                       "~30°) flattens a face: its planes never separate in tone."),
            Float("light_el", "Light height (elevation)", 40.0, unit="°", min=-89.0, max=89.0, step=5.0, decimals=1),
            Float("ambient", "Ambient", 0.42, min=0.0, max=1.0, step=0.02, decimals=2, slider=True,
                  help="Light everywhere (scaled by ambient occlusion): how deep the shadows go. Lower, darker and "
                       "more hatched (Engraving 0.32); higher, fewer strokes."),
            Choice("light_frame", "Light turns with", "camera", [("camera", "The camera"), ("model", "The model")],
                   segmented=True, advanced=True),
            Bool("shadows", "Cast shadows", True, advanced=True),
            Float("softness", "Shadow softness", 3.0, unit="°", min=0.0, max=20.0, step=0.5, decimals=1,
                  advanced=True),
        ),
        Section(
            "Tone",
            Float("key", "Key light", 0.85, min=0.0, max=1.5, step=0.05, decimals=2, advanced=True),
            Float("ao", "Ambient occlusion", 0.6, min=0.0, max=1.0, step=0.05, decimals=2, advanced=True,
                  help="Darkens hollows and contacts."),
            Float("form_shading", "Form shading", 0.7, min=0.0, max=1.0, step=0.05, decimals=2, advanced=True,
                  help="1 shades with the big forms (smoothed normals), 0 with every small plane of the surface."),
            Float("gamma", "Tone curve", 1.0, min=0.3, max=3.0, step=0.05, decimals=2, advanced=True,
                  help="Above 1 darkens the mid-tones (more hatching)."),
            Float("tone_blur", "Tone blur", 0.8, unit="mm", min=0.0, max=5.0, step=0.1, decimals=1, advanced=True,
                  help="Simplifies the tone like an artist: scan-scale noise never reaches the hatching."),
        ),
        Section(
            "Lines",
            Bool("suggestive", "Suggestive contours", True, advanced=True,
                 help="Where a contour would appear if the view moved a little: the soft folds of a form."),
            Float("suggestive_strength", "Suggestive threshold", 3.0, min=0.0, max=20.0, step=0.5, decimals=1,
                  advanced=True, visible_if={"suggestive": True},
                  help="Only stronger ones are drawn: 2-4 on scans (lower turns the relief into scribbles)."),
            Bool("valleys", "Valleys", True, advanced=True, help="Concave creases: lids, the lip line, nostrils."),
            Float("valley_strength", "Valley threshold", 110.0, min=10.0, max=1000.0, step=10.0, decimals=0,
                  advanced=True, visible_if={"valleys": True},
                  help="Curvature of drawn valleys (per unit of a 2-tall model)."),
            Bool("creases", "Creases", True, advanced=True, help="Sharp edges of the model (and open borders)."),
            Float("crease_angle", "Crease angle", 55.0, unit="°", min=5.0, max=180.0, step=5.0, decimals=0,
                  advanced=True, visible_if={"creases": True}),
            Bool("lost_found", "Lost and found outline", True, advanced=True,
                 help="The outline thins where the surface inside it is fully lit, heavier on the shadow side."),
        ),
        Section(
            "Hatching",
            Int("passes", "Tone passes", 3, min=1, max=3, advanced=True, visible_if=hatched,
                help="One pass per tone band: the 2nd crosses the 1st at 72°, the 3rd at -38°."),
            Choice("tone_scheme", "Tone bands", "classic", [("classic", "Classic"), ("engraving", "Engraving")],
                   segmented=True, advanced=True, visible_if=hatched,
                   help="Engraving starts hatching in lighter tones, a little denser."),
            Float("angle", "Calm angle", 45.0, unit="°", min=-90.0, max=90.0, step=5.0, decimals=0, advanced=True,
                  visible_if=hatched, help="A touch of one fixed direction everywhere (all of it for One angle)."),
            Float("max_len", "Longest stroke", 45.0, unit="mm", min=3.0, max=200.0, step=1.0, decimals=0,
                  advanced=True, visible_if=hatched),
            Float("min_len", "Shortest stroke", 2.5, unit="mm", min=0.5, max=20.0, step=0.5, decimals=1,
                  advanced=True, visible_if=hatched),
            Float("edge_gap", "Gap at edges", 0.35, unit="mm", min=0.0, max=3.0, step=0.05, decimals=2,
                  advanced=True, visible_if=hatched, help="Hatching stops this short of outlines and depth edges."),
        ),
        Section(
            "Focus",
            Bool("focus", "Focus point", False, advanced=True,
                 help="One region (a face) drawn with more care: finer lines and hatching, less smoothing, its own "
                      "portrait light; the rest calms down."),
            Float("focus_u", "Across", 0.5, min=0.0, max=1.0, step=0.01, decimals=3, advanced=True,
                  visible_if=focused, help="Fraction of the drawing's width, from the left."),
            Float("focus_v", "Down", 0.3, min=0.0, max=1.0, step=0.01, decimals=3, advanced=True,
                  visible_if=focused, help="Fraction of the drawing's height, from the top."),
            Float("focus_radius", "Radius", 8.5, unit="% h", min=1.0, max=50.0, step=0.5, decimals=1,
                  advanced=True, visible_if=focused, help="% of the model's height (a head on a bust: ~8)."),
            Float("focus_light_az", "Portrait light from", 25.0, unit="°", min=-180.0, max=180.0, step=5.0,
                  decimals=1, advanced=True, visible_if=focused, help="Relative to the model's front."),
            Float("focus_light_el", "Portrait light height", 55.0, unit="°", min=-89.0, max=89.0, step=5.0,
                  decimals=1, advanced=True, visible_if=focused),
        ),
        Section("Model", *frame_params(stage_sets=True)),
        Section("Paper", *paper_params("A3", "portrait", 18.0)),
        Section(
            "Pens",
            Float("fine_width", "Fine pen", 0.2, unit="mm", min=0.05, max=2.0, step=0.05, decimals=2,
                  help="Hatching, details, creases."),
            Float("bold_width", "Bold pen", 0.4, unit="mm", min=0.05, max=3.0, step=0.05, decimals=2,
                  help="Outline and contours."),
            Float("max_ink", "Most ink", 0.0, unit="m", min=0.0, max=1000.0, step=5.0, decimals=0, advanced=True,
                  help="Thins the hatching until the drawing fits (0 = no limit)."),
        ),
        Section(
            "Quality",
            Float("px_per_mm", "Ray resolution", 5.0, unit="px/mm", min=1.0, max=12.0, step=0.5, decimals=1,
                  advanced=True),
            Int("draft_faces", "Preview faces", 150_000, min=5_000, max=5_000_000, advanced=True,
                help="Preview simplifies bigger models, casts fewer rays and hatches more openly."),
            collapsed=True),
    ],
    test_values={"paper": "A5", "complexity": 0.2, "density": 0.5, "px_per_mm": 2.5},
    legacy=("projects/blender3d_project/augustus (plotter_lib/sketch_*.py, augustus_sketch)",),
)
