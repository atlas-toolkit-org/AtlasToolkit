"""
Stipple: a 3D model drawn in dots only - blue-noise stippling of its ray-cast light and shade, dense in the
shadows, empty in the light (the sphere of Digital divide, projects/blender3d_project, for any model).

Outputs <name>_stipple.svg, signed <desc>stipple</desc> so Atlas links them to this tool.
"""
from atlas_toolkit.core import (CAMERA_KEYS, FRAME_KEYS, Bool, Choice, Float, Inputs, Int, Output, Section, Seed,
                                Tool, camera_params, frame_params, paper_params)


def outlined(v):
    return v.get("outline") != "none"


TOOL = Tool(
    id="stipple",
    name="Stipple",
    version="1.0",
    category="mesh2svg",
    icon="stipple",
    action="Draw",
    summary="Draw a 3D model in dots: blue-noise stippling of its light and shade, ray-cast through a camera.",
    tagline="Pointillism for the plotter: the form built by dots alone, the paper left as the light.",
    description=(
        "Aim the camera in the Aim tab. The light falls from where you set it (relative to the camera, or to the "
        "model), with soft cast shadows and, if you like, ambient occlusion; wrap softens the terminator and a "
        "bounce lifts the underside. The darker a place, the closer the dots, down to the darkest spacing; tones "
        "lighter than the highlight threshold stay empty paper. Dots are spread as blue noise (no clumps, no "
        "grid) and drawn as tiny closed circles. Edge darkening builds the silhouette with dots instead of a line; "
        "a thin outline can be added on its own pen. Every dot is a pen lift: the budget caps the plot time."),
    inputs=Inputs("mesh"),
    output=Output("{stem}_stipple.svg", "svg"),
    signature="<desc>stipple</desc>",
    engine=".engine:run",
    runs_in="process",
    preview="svg",
    columns=("dots",),
    viewer="atlas_toolkit.tools.plotter_vision.aim:AimView",
    preset_skip=CAMERA_KEYS + FRAME_KEYS,
    params=[
        Section("Camera", *camera_params(theta=72.4, psi=148.0, radius=262.0, fov=36.2)),
        Section(
            "Dots",
            Float("min_spacing", "Darkest spacing", 0.85, unit="mm", min=0.3, max=5.0, step=0.05, decimals=2,
                  slider=True, help="Distance between dots where the tone is darkest (about one dot per spacing²)."),
            Float("void", "Highlight left empty", 0.30, min=0.0, max=0.95, step=0.01, decimals=2, slider=True,
                  help="Darkness below which no dot is drawn: the lit side stays paper."),
            Float("density_gamma", "Contrast", 1.4, min=0.3, max=4.0, step=0.05, decimals=2,
                  help="Above 1 empties the half-tones and keeps the shadows dense; below 1 fills the half-tones."),
            Float("dot_radius", "Dot size", 0.15, unit="mm", min=0.02, max=1.0, step=0.01, decimals=2,
                  help="Radius of each little circle; with the pen width it sets how big a dot looks."),
            Int("budget", "Dot budget", 8000, min=50, max=100_000, step=500,
                help="Most dots drawn (each one is a pen lift, about 0.25 s)."),
            Seed("seed", "Seed", 5, help="Where the dots fall."),
        ),
        Section(
            "Light",
            Float("light_az", "Light from (azimuth)", -40.0, unit="°", min=-180.0, max=180.0, step=5.0, decimals=1,
                  help="Around the up axis; negative = from the left (seen from the camera, or from the model's "
                       "front)."),
            Float("light_el", "Light height (elevation)", 40.0, unit="°", min=-89.0, max=89.0, step=5.0, decimals=1),
            Float("ambient", "Ambient", 0.0, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="Light everywhere: lifts the shadows and empties the half-tones. Strong: 0.1-0.3 goes a long "
                       "way."),
            Choice("light_frame", "Light turns with", "camera", [("camera", "The camera"), ("model", "The model")],
                   segmented=True, advanced=True,
                   help="The camera: the light stays where it is in the picture as you orbit. The model: it stays "
                        "on the same side of the model (azimuth from its front)."),
            Bool("shadows", "Cast shadows", True, help="Parts of the model shade the parts behind them."),
            Float("softness", "Shadow softness", 3.0, unit="°", min=0.0, max=20.0, step=0.5, decimals=1,
                  advanced=True, visible_if={"shadows": True}),
            Float("wrap", "Wrap", 0.15, min=0.0, max=1.0, step=0.05, decimals=2, advanced=True,
                  help="Softens the terminator (the step where the light ends)."),
            Float("bounce", "Bounce from below", 0.18, min=0.0, max=1.0, step=0.02, decimals=2, advanced=True,
                  help="Light thrown back up onto the underside."),
            Float("ao", "Ambient occlusion", 0.0, min=0.0, max=1.0, step=0.05, decimals=2, advanced=True,
                  help="Darkens hollows and contacts (folds, eye sockets, between fingers)."),
            Float("gamma", "Tone curve", 1.0, min=0.3, max=3.0, step=0.05, decimals=2, advanced=True,
                  help="Above 1 darkens the half-tones of the light itself (more dots); Contrast reshapes the "
                       "dots' density instead."),
        ),
        Section(
            "Texture",
            Float("noise_amp", "Patchiness", 0.35, min=0.0, max=1.0, step=0.05, decimals=2, advanced=True,
                  help="Low-frequency noise that breaks the half-tones into patches, leaving some voids."),
            Float("noise_scale", "Patch size", 7.0, unit="mm", min=1.0, max=50.0, step=0.5, decimals=1,
                  advanced=True),
            Float("rim_lo", "Rim hint from", 0.55, min=0.0, max=1.0, step=0.05, decimals=2, advanced=True,
                  help="A few dots hint at the lit silhouette (1 - |n·v| from this ...)."),
            Float("rim_hi", "Rim hint to", 0.85, min=0.0, max=1.0, step=0.05, decimals=2, advanced=True),
            Float("rim_spacing", "Rim hint spacing", 3.2, unit="mm", min=0.0, max=20.0, step=0.1, decimals=1,
                  advanced=True, help="0 = no rim hint."),
            Float("rim_dark", "Edge darkening", 0.0, min=0.0, max=1.0, step=0.05, decimals=2, advanced=True,
                  help="Darker toward the silhouette, except under the highlight: the dots build the contour, the "
                       "form reads as a solid."),
            Float("rim_dark_lo", "Edge darkening from", 0.45, min=0.0, max=0.99, step=0.05, decimals=2,
                  advanced=True, visible_if=lambda v: v.get("rim_dark", 0) > 0),
            Float("rim_dark_hl_lo", "Spare the highlight from", 0.55, min=0.0, max=1.0, step=0.05, decimals=2,
                  advanced=True, visible_if=lambda v: v.get("rim_dark", 0) > 0),
            Float("rim_dark_hl_hi", "Spare the highlight to", 0.9, min=0.0, max=1.0, step=0.05, decimals=2,
                  advanced=True, visible_if=lambda v: v.get("rim_dark", 0) > 0),
            Int("dot_sides", "Dot polygon sides", 8, min=3, max=24, advanced=True),
        ),
        Section(
            "Outline",
            Choice("outline", "Outline", "none", [("none", "None"), ("pen", "Thin line")], segmented=True,
                   help="A silhouette line on its own pen; the dots keep clear of it."),
            Float("clear_mm", "Dots keep clear by", 0.5, unit="mm", min=0.0, max=5.0, step=0.1, decimals=2,
                  visible_if=outlined, advanced=True),
        ),
        Section("Model", *frame_params(stage_sets=True)),
        Section("Paper", *paper_params("A3", "portrait", 18.0)),
        Section(
            "Pens",
            Float("dot_width", "Dot pen", 0.4, unit="mm", min=0.05, max=3.0, step=0.05, decimals=2),
            Float("outline_width", "Outline pen", 0.3, unit="mm", min=0.05, max=3.0, step=0.05, decimals=2,
                  visible_if=outlined),
            Bool("optimise", "Order dots to cut travel", True),
        ),
        Section(
            "Quality",
            Float("px_per_mm", "Shading resolution", 5.0, unit="px/mm", min=1.0, max=12.0, step=0.5, decimals=1,
                  advanced=True, help="Rays per mm on paper for the light and shade."),
            Int("draft_faces", "Preview faces", 150_000, min=5_000, max=5_000_000, advanced=True,
                help="Preview simplifies bigger models and shades at a lower resolution."),
            collapsed=True),
    ],
    test_values={"paper": "A5", "budget": 300, "px_per_mm": 3.0},
    legacy=("projects/blender3d_project/digital_divide_1 (plotter_lib/stipple.py, dd_runner.sphere_dots)",),
)
