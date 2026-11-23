"""Atlas adapter for FlowRep Sketch. Outputs carry <desc>flowrep-sketch</desc>."""
from atlas_toolkit.core import (CAMERA_KEYS, CAMERA_NOTE, FRAME_KEYS, Bool, Choice, Float, Inputs, Int,
                                Output, Section, Tool, camera_params, frame_params, paper_params,
                                pen_output_param)

TOOL = Tool(
    id="flowrep-sketch",
    name="FlowRep Sketch",
    version="0.3.0",
    category="mesh2svg",
    icon="flowrep-sketch",
    action="Draw",
    summary="Draw a model with sparse curves that describe its form.",
    tagline="FlowRep's descriptive 3D curve network, projected as clean plotter paths.",
    description=(
        "Extract a camera-independent network of curvature-aligned curves, then aim and draw it. "
        "Descriptiveness sets the allowed normal-prediction error: smaller values retain more detail. "
        "Triangle inputs are prepared with Instant Meshes; quad-dominant OBJ files keep their original topology. "
        "The first extraction is cached so camera changes only repeat visibility and SVG layout. "
        "The drawing has a pen for the sharp features, one for the flowlines, an optional silhouette and optional "
        "hidden curves (dashed, grey), all the same width; Save pens as keeps them apart or puts them on one pen."),
    inputs=Inputs("mesh"),
    output=Output("{stem}_flowrep.svg", "svg"),
    signature="<desc>flowrep-sketch</desc>",
    engine=".engine:run",
    runs_in="process",
    preview="svg",
    live=False,
    columns=("curves","strokes"),
    viewer="atlas_toolkit.tools.plotter_vision.aim:AimView",
    preset_skip=CAMERA_KEYS+FRAME_KEYS,
    params=[
        Section("Camera", *camera_params(), note=CAMERA_NOTE),
        Section("FlowRep",
                Float("dmax","Descriptiveness",20.,unit="°",min=1,max=80,step=1,decimals=1,
                      help="Maximum 90th-percentile normal-prediction error per patch. Lower retains more curves. "
                           "The paper uses 20° for designed shapes and 30° for organic ones (heads, figures)."),
                Choice("prepare","Mesh preparation","auto",
                       [("auto","Auto"),("preserve","Preserve polygons"),("remesh","Remesh")]),
                Int("target_faces","Remesh resolution",6000,min=100,max=200000,step=500,
                    help="Approximate number of quads for triangle inputs."),
                Float("crease_angle","Sharp feature angle",45.,unit="°",min=1,max=180,step=1,decimals=1,advanced=True),
                Bool("declutter","Declutter parallel curves",True,
                     help="Remove one of two flowlines running side by side when the shape is still described "
                          "within 20 % of Descriptiveness (the paper's last step)."),
                Int("regularize","Smoothing passes",5,min=0,max=20,advanced=True),
                Int("min_trim_edges","Shortest sharp feature",4,min=0,max=50,advanced=True,
                    help="Sharp-feature curves shorter than this many edges that touch nothing are left out. 0 keeps them all.")),
        Section("Drawing",
                Bool("silhouette","Silhouette",True),
                Choice("silhouette_style","Silhouette style","smooth",[("smooth","Smooth contours"),("mesh","Mesh edges")],
                       advanced=True,help="Smooth contours follow the surface between its vertices; mesh edges is the style before 0.2.0."),
                Float("smoothing","Curve smoothing",0.5,min=0,max=1,step=0.05,decimals=2,
                      help="Smooths the network's curves between junctions and the silhouette. 0 draws the raw quad edges."),
                Bool("show_hidden","Hidden curves",False,
                     help="Dashed grey curves on a pen of their own, for construction drawings. The dashes are drawn "
                          "strokes, so the plotter draws them dashed too."),
                Float("line_width","Pen width",0.3,unit="mm",min=0.05,max=2.,step=0.05,decimals=2),
                pen_output_param(),
                Float("tolerance","Curve tolerance",0.05,unit="mm",min=0.005,max=0.5,step=0.005,decimals=3,advanced=True),
                Float("min_length","Shortest stroke",0.5,unit="mm",min=0,max=10,step=0.1,decimals=2,advanced=True)),
        Section("Paper", *paper_params()),
        Section("Model", *frame_params(stage_sets=False)),
    ],
)

