"""Surface Stripes: raised surface bands. Signed outputs: atlas-toolkit:mesh-stripes."""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, frame_params

TOOL = Tool(
    id="mesh-stripes", name="Surface Stripes", version="0.2.0", category="mesh", icon="surface-stripes",
    action="Stripe",
    summary="Generate flowing surface bands, on the model or as solid ribbons alone.",
    tagline="Flowing bands on a model, or solid ribbons that describe its shape on their own.",
    description=("Generate a smooth direction field and solve Stripe Patterns on Surfaces (Knöppel et al., 2015), "
                 "then fuse shallow, flat-topped bands to the original body, or export closed ribbons only. "
                 "Export stripes only is off by default; when enabled it omits the body from the output. "
                 "Spacing and relief are % of the "
                 "model's height; width is % of the spacing. Rotation turns the visible bands, and a second "
                 "orthogonal family makes a crossed pattern. Closed meshes keep their detail. Open inputs need "
                 "the explicit Repair option, which closes them by resampling and can soften detail. "
                 "Geometry Central's curve extraction approximates interpolation inside triangles. Send the "
                 "result to Plotter Vision (Features, without decimation), or Hatch Maker, for a plotter SVG."),
    inputs=Inputs("mesh", exclude=(r"(?i)_stripes\.(stl|obj|ply)$",)),
    output=Output("{stem}_stripes.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-stripes", engine=".engine:run", runs_in="process", preview="mesh",
    requires=("stripes",), columns=("curves", "faces", "coverage"),
    preset_skip=("up_axis", "front", "rotate"),
    params=[
        Section("Stripes",
                Choice("field", "Direction field", "curvature", [("curvature", "Curvature aligned"),
                                                                    ("smoothest", "Smoothest")]),
                Float("spacing", "Stripe spacing", 4., unit="% h", min=0.5, max=40., step=0.5, decimals=2,
                      help="Target distance between bands over the surface; branch points accommodate the shape."),
                Float("band_width", "Band width", 35., unit="% spacing", min=5., max=85., step=5., decimals=1),
                Float("relief", "Raised height", 0.25, unit="% h", min=0., max=5., step=0.05, decimals=3,
                      help="Height above the skin; also the ribbon thickness with Export stripes only. "
                           "Zero keeps the original body when stripes-only export is off."),
                Float("rotation", "Band rotation", 0., unit="°", min=-180., max=180., step=5., decimals=1),
                Choice("families", "Stripe families", "single", [("single", "One"), ("orthogonal", "Orthogonal pair")],
                       segmented=True),
                Seed("seed", "Seed", 0, help="Repeatable initialization of the numerical field and stripe solvers.")),
        Section("Surface",
                Bool("stripes_only", "Export stripes only", False,
                     help="Write only closed ribbons, without the underlying body. Raised height sets thickness. "
                          "Rear stripes can be seen through the gaps."),
                Bool("repair", "Repair open mesh", False,
                     help="Close an open or invalid body by resampling. Fine details can soften; reported in the log."),
                Int("repair_resolution", "Repair resolution", 160, min=48, max=400, step=16, advanced=True,
                    visible_if={"repair": True}),
                Int("solve_faces", "Field face budget", 100000, min=1000, max=500000, step=5000, advanced=True,
                    help="Maximum triangles for the working field mesh. Original body detail is retained."),
                Int("draft_faces", "Preview field faces", 8000, min=1000, max=50000, step=1000, advanced=True,
                    visible_if={"stripes_only": False}),
                Int("max_faces", "Band face budget", 2000000, min=20000, max=5000000, step=100000, advanced=True,
                    help="Stop rather than silently omit bands if construction exceeds this budget.")),
        Section("Model", *frame_params(stage_sets=False)),
    ],
    sample=".engine:make_sample", test_values={"spacing": 18., "solve_faces": 2500, "draft_faces": 2500},
)
