"""Atlas adapter for FlowRep Sketch. Outputs carry <desc>flowrep-sketch</desc>."""
from atlas_toolkit.core import (CAMERA_KEYS, FRAME_KEYS, Bool, Choice, Float, Inputs, Int,
                                Output, Section, Tool, camera_params, frame_params, paper_params)

TOOL = Tool(
    id="flowrep-sketch",
    name="FlowRep Sketch",
    version="0.1.2",
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
        "The drawing includes visible curves and an optional silhouette, with one black pen."),
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
        Section("Camera", *camera_params()),
        Section("FlowRep",
                Float("dmax","Descriptiveness",20.,unit="°",min=1,max=80,step=1,decimals=1,
                      help="Maximum 90th-percentile normal-prediction error per patch. Lower retains more curves."),
                Choice("prepare","Mesh preparation","auto",
                       [("auto","Auto"),("preserve","Preserve polygons"),("remesh","Remesh")]),
                Int("target_faces","Remesh resolution",6000,min=100,max=200000,step=500,
                    help="Approximate number of quads for triangle inputs."),
                Float("crease_angle","Sharp feature angle",45.,unit="°",min=1,max=180,step=1,decimals=1,advanced=True),
                Int("regularize","Smoothing passes",5,min=0,max=20,advanced=True)),
        Section("Drawing",
                Bool("silhouette","Silhouette",True),
                Bool("show_hidden","Hidden curves",False,help="A dashed grey layer for construction drawings."),
                Float("line_width","Pen width",0.3,unit="mm",min=0.05,max=2.,step=0.05,decimals=2),
                Float("tolerance","Curve tolerance",0.05,unit="mm",min=0.005,max=0.5,step=0.005,decimals=3,advanced=True),
                Float("min_length","Shortest stroke",0.5,unit="mm",min=0,max=10,step=0.1,decimals=2,advanced=True)),
        Section("Paper", *paper_params()),
        Section("Model", *frame_params(stage_sets=False)),
    ],
)

