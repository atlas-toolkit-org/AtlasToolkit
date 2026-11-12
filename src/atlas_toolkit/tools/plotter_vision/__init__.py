"""
Plotter Vision: a 3D model drawn as the edges you can see, through a perspective camera, hidden lines removed
(plotter.vision by Trammell Hudson, reimplemented, with a live view to aim the camera).

Outputs <name>_vision.svg, signed <desc>plotter-vision</desc> so Atlas links them to this tool.
"""
from atlas_toolkit.core import (CAMERA_KEYS, Bool, Choice, Float, Inputs, Int, Output, Section, Tool, camera_params,
                                paper_params)

SIDE_CHOICES = [("auto", "Auto"), ("one", "Front"), ("two", "Both")]


def depth_on(v):
    return int(v.get("depth_layers", 1)) > 1 and not v.get("stereo")


TOOL = Tool(
    id="plotter-vision",
    name="Plotter Vision",
    version="1.4.2",
    category="mesh2svg",
    icon="eye",
    action="Draw",
    summary="Draw a 3D model as the edges you can see, through a perspective camera, hidden lines removed.",
    tagline="plotter.vision in the toolkit: aim the camera live, then draw the visible wireframe in mm.",
    description=(
        "Drag in the Vision tab to orbit the model, wheel to come closer, Shift-drag (or right-drag) to move the "
        "look-at point; keys 1-5 give the front, side, back, other side and top views, F fits the model in the "
        "frame. While you move, every candidate edge shows; when you stop, the hidden lines are removed, as on "
        "plotter.vision. The frame is the paper's drawing area: what you see in it is what gets drawn. Edges "
        "between faces flatter than an angle are left out (0.01° is plotter.vision; a few degrees cleans "
        "low-poly curves; 30° or more leaves only creases and outlines). Optional decimation, depth pens, an "
        "outline pen, hidden lines and the red/blue stereo pair."),
    inputs=Inputs("mesh"),
    output=Output("{stem}_vision.svg", "svg"),
    signature="<desc>plotter-vision</desc>",
    engine=".engine:run",
    runs_in="process",
    preview="svg",
    columns=("strokes",),
    viewer=".viewer:VisionView",
    preset_skip=CAMERA_KEYS,
    params=[
        Section("Camera", *camera_params()),
        Section(
            "Edges",
            Choice('edge_mode', 'Draw edges', 'features', [('features', 'Features'), ('mesh', 'Mesh edges')],
                   help='Mesh edges draws native triangles, quads or cell boundaries, including coplanar edges. '
                        'Polygon triangulation diagonals are omitted; visibility still uses triangles.'),
            Float("flat_angle", "Hide edges flatter than", 0.01, unit="°", min=0.0, max=90.0, step=0.5, decimals=2,
                  help="An edge between two faces is drawn only where they meet at more than this angle. 0.01° is "
                       "plotter.vision (only coplanar faces merge); 1-5° removes the diagonals of flat or gently "
                       "curved quads; 30° or more keeps only creases. Outlines and open borders always show."),
            Choice("sides", "Faces", "auto", SIDE_CHOICES,
                   help="Front: faces turned away are skipped (plotter.vision). Both: open surfaces also hide "
                        "what is behind them and show their back. Auto: both for open meshes."),
            Bool("outline_layer", "Outline on its own pen", False,
                 help="Silhouettes and open borders on a separate layer, e.g. for a thicker pen."),
            Bool("show_hidden", "Hidden lines (dashed)", False, help="Also draw the hidden lines, dashed, on a layer."),
            Bool("crossings", "Where parts cross", True,
                 help="Draw the lines where separate parts cut into each other (overlapping spheres, a cone "
                      "through a plate). plotter.vision left them out, so overlapping solids met without a line."),
            Float("min_piece", "Shortest piece", 0.1, unit="mm", min=0.0, max=5.0, step=0.05, decimals=2,
                  help="Visible bits shorter than this are dropped (plotter.vision dropped those under a pixel)."),
            Float("min_stroke", "Shortest stroke", 0.0, unit="mm", min=0.0, max=20.0, step=0.5, decimals=1,
                  help="After joining, inner strokes shorter than this are dropped: the specks scan noise leaves "
                       "in feature mode. Outlines always stay."),
        ),
        Section(
            "Model",
            Bool("decimate", "Decimate", False,
                 help="Reduce the model to a face budget first (quadric error), instead of in Blender."),
            Int("target_faces", "Faces", 3500, min=100, max=500_000, visible_if={"decimate": True},
                help="Face budget: 2-12K gives plotter.vision's typical low-poly look."),
            Bool("fix_winding", "Fix face winding", True,
                 help="Make the faces' winding consistent, so no face is culled by mistake (plotter.vision showed "
                      "flipped faces as holes)."),
        ),
        Section(
            "Depth and stereo",
            Int("depth_layers", "Depth pens", 1, min=1, max=3,
                help="Split the lines by depth into near / (middle) / far layers, each its own pen."),
            Bool("far_dashed", "Far lines dashed", False, visible_if=depth_on),
            Bool("stereo", "Red/blue stereo", False,
                 help="plotter.vision's anaglyph: a second eye turned a little further round, red and blue layers."),
            Float("eye_sep", "Eye separation", 2.0, unit="°", min=0.1, max=6.0, step=0.1, decimals=1,
                  visible_if={"stereo": True}),
        ),
        Section("Paper", *paper_params("A3", "portrait", 15.0, custom=True)),
        Section(
            "Pens",
            Float("line_width", "Line pen", 0.3, unit="mm", min=0.05, max=3, step=0.05, decimals=2),
            Float("outline_width", "Outline pen", 0.5, unit="mm", min=0.05, max=3, step=0.05, decimals=2,
                  visible_if={"outline_layer": True}),
            Bool("optimise", "Order strokes to cut travel", True),
        ),
        Section(
            "Quality",
            Float("simplify", "Simplify", 0.02, unit="mm", min=0.0, max=1.0, step=0.01, decimals=3, advanced=True,
                  help="Points closer than this to a straight stroke are dropped after joining."),
            collapsed=True,
        ),
    ],
    test_values={"paper": "A5"},
)
