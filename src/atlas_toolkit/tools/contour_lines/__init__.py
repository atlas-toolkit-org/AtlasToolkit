"""
Contour Lines: a 3D model drawn as horizontal slices seen through a camera, hidden parts removed, for any model,
without Blender.

Outputs <name>_contours.svg, signed <desc>contour-lines</desc> so Atlas links them to this tool.
"""
from atlas_toolkit.core import (CAMERA_KEYS, FRAME_KEYS, Bool, Float, Inputs, Int, Output, Section, Tool,
                                camera_params, frame_params, paper_params)

TOOL = Tool(
    id="contour-lines",
    name="Contour Lines",
    version="1.1.0",
    category="mesh2svg",
    icon="contours",
    action="Draw",
    summary="Draw a 3D model as horizontal slices seen through a camera, hidden lines removed.",
    tagline="Dense, even contours that describe the form by themselves.",
    description=(
        "Aim the camera in the Aim tab (drag to orbit, wheel to come closer, F to fit). The model is cut by "
        "horizontal planes a fixed distance apart on paper; what the camera cannot see is removed by ray casting, "
        "short hidden gaps are drawn straight through, lines that converge at a grazing silhouette taper off "
        "instead of pooling ink, and strokes are joined and ordered for few pen lifts. Draw every stage of a Twist, "
        "Melt or Disintegrate sequence with the same settings: a stage set shares one frame, so "
        "the plates keep one scale, one position and the same slice heights."),
    inputs=Inputs("mesh"),
    output=Output("{stem}_contours.svg", "svg"),
    signature="<desc>contour-lines</desc>",
    engine=".engine:run",
    runs_in="process",
    preview="svg",
    columns=("strokes", "slices"),
    viewer="atlas_toolkit.tools.plotter_vision.aim:AimView",
    preset_skip=CAMERA_KEYS + FRAME_KEYS,
    params=[
        Section("Camera", *camera_params(theta=72.4, psi=148.0, radius=262.0, fov=36.2)),
        Section("Contours",
                Float("spacing_mm", "Line spacing", 2.0, unit="mm", min=0.3, max=20.0, step=0.1, decimals=2,
                      slider=True,
                      help="Distance between slices on paper, measured at the look-at point. 1.8-2.2 mm with a "
                           "0.3 mm pen reads dense but clean; under ~5× the pen width lines start to merge."),
                Bool("hidden", "Remove hidden lines", True,
                     help="Off draws every slice whole, as wire rings (a transparent model)."),
                Float("slice_offset", "Grid offset", 0.0, min=0.0, max=1.0, step=0.05, decimals=2, advanced=True,
                      help="Shifts the planes by this fraction of the spacing (0: the first plane one spacing "
                           "above the frame's floor)."),
                ),
        Section("Model", *frame_params(stage_sets=True)),
        Section("Plotting",
                Float("min_len", "Shortest stroke", 4.0, unit="mm", min=0.0, max=50.0, step=0.5, decimals=1,
                      help="Strokes shorter than this after joining are left out. Too high at small scales "
                           "empties thin features (drips become white slots)."),
                Float("min_gap", "Taper converging lines", 0.45, unit="mm", min=0.0, max=3.0, step=0.05,
                      decimals=2,
                      help="Where a line runs closer than this to lines already drawn for more than 3 mm it is "
                           "cut, so contours bunching at a grazing silhouette taper off instead of pooling ink. "
                           "0 = off."),
                Float("join_any", "Join strokes within", 0.5, unit="mm", min=0.0, max=5.0, step=0.1, decimals=2,
                      advanced=True,
                      help="Chain any two strokes whose ends are this close (contour into drip into contour). Keep "
                           "it below the line spacing."),
                Float("join_same", "Join a contour within", 1.5, unit="mm", min=0.0, max=10.0, step=0.1,
                      decimals=2, advanced=True, help="Chain pieces of the same contour whose ends are this close."),
                Float("gap_bridge", "Bridge hidden gaps under", 1.5, unit="mm", min=0.0, max=10.0, step=0.1,
                      decimals=2, advanced=True,
                      help="A contour hidden for less than this is drawn straight through: one pen lift less."),
                ),
        Section("Paper", *paper_params("A3", "landscape", 25.0, custom=True)),
        Section("Pen",
                Float("line_width", "Pen", 0.3, unit="mm", min=0.05, max=3.0, step=0.05, decimals=2),
                Bool("optimise", "Order strokes to cut travel", True),
                ),
        Section("Quality",
                Float("step_mm", "Visibility step", 0.25, unit="mm", min=0.05, max=2.0, step=0.05, decimals=2,
                      advanced=True, help="Lines are tested for hiding every this many mm on paper; the switches "
                                          "between seen and hidden are then found exactly."),
                Float("simplify", "Simplify", 0.08, unit="mm", min=0.0, max=1.0, step=0.01, decimals=3,
                      advanced=True, help="Points closer than this to a straight stroke are dropped."),
                Int("draft_faces", "Preview faces", 150_000, min=5_000, max=5_000_000, advanced=True,
                    help="Preview simplifies bigger models to this many faces, with wider spacing; Draw always "
                         "uses the full model."),
                collapsed=True),
    ],
    test_values={"paper": "A5", "spacing_mm": 4.0},
    legacy=("projects/blender3d_project (skill plotter-contour-art: head_plot_pipeline.py trace_contours)",),
)
