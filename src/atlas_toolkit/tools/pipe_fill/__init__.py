"""Pipe Fill. Signed outputs: atlas-toolkit:pipe-fill."""
from atlas_toolkit.core import (Bool, Choice, Float, Inputs, Int, Output, Section,
                                Seed, Tool, Vec3, frame_params)

TOOL = Tool(
    id="pipe-fill", name="Pipe Fill", version="0.1.0", category="mesh", icon="pipe-fill",
    action="Fill", summary="Replace a model with organic curves or orthogonal pipes throughout its volume.",
    tagline="A sculpture woven from tubes, with open space between them.",
    description=("Fill a closed model's interior with independent strands or closed loops. Organic curves "
                 "relax with repulsion; orthogonal pipes retain straight runs and rounded elbows. The original "
                 "body is omitted. Solid tubes and hollow pipes share outer diameter, spacing and clearance "
                 "controls in percent of model height. Seeded packing checks the tube thickness against "
                 "the boundary and neighboring paths. Thin regions may not fit "
                 "pipes; the report measures routing coverage. For plotter art, send the model to Plotter "
                 "Vision Features without decimation or Hatch Maker. Repair is optional and approximate."),
    inputs=Inputs("mesh", exclude=(r"(?i)_pipes\.(stl|obj|ply)$",)),
    output=Output("{stem}_pipes.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:pipe-fill", engine=".engine:run", runs_in="process", preview="mesh",
    columns=("curves", "coverage", "faces"), preset_skip=("up_axis", "front", "rotate"),
    params=[
        Section("Packing",
            Choice("mode", "Mode", "organic", [("organic", "Organic"),
                                                     ("orthogonal", "Orthogonal")], segmented=True),
            Choice("topology", "Paths", "strands", [("strands", "Strands"),
                                                        ("loops", "Loops")], segmented=True),
            Float("spacing", "Spacing", 6., unit="% h", min=1., max=30., step=0.5, decimals=2,
                  help="Distance between packing sites throughout the volume. Smaller values add more paths."),
            Int("length", "Run length", 28, min=4, max=160, step=4,
                help="Target packing edges per strand, or maximum merged loop length. Larger values make longer paths."),
            Float("straightness", "Straight runs", 10., unit="%", min=0., max=100., step=5., decimals=0,
                  help="Preference for continuing in the same direction before turning."),
            Float("bend", "Elbow radius", 36., unit="% spacing", min=15., max=48., step=2., decimals=0,
                  visible_if={"mode": "orthogonal"}, help="Circular bend radius; kept within half a packing edge."),
            Float("organic", "Flow", 70., unit="%", min=0., max=100., step=5., decimals=0,
                  visible_if={"mode": "organic"}, help="Smooth and loosen the routed paths with seeded spatial variation and repulsion."),
            Vec3("pattern_rotate", "Packing rotation", (0., 0., 0.), unit="°", min=-180., max=180., step=5.),
            Seed("seed", "Seed", 1)),
        Section("Tubes",
            Choice("section", "Section", "solid", [("solid", "Solid"), ("hollow", "Hollow")],
                   segmented=True),
            Float("diameter", "Outer diameter", 2.2, unit="% h", min=0.2, max=15., step=0.2, decimals=2),
            Float("variation", "Thickness variation", 0., unit="%", min=0., max=40., step=5., decimals=0,
                  help="Smooth seeded changes along each path, bounded by this percentage of the nominal diameter."),
            Float("wall", "Wall thickness", 22., unit="% radius", min=5., max=80., step=5., decimals=0,
                  visible_if={"section": "hollow"}, help="Material thickness as a fraction of the local outer radius. Ends have annular rims."),
            Float("gap", "Minimum gap", 0.3, unit="% h", min=0., max=5., step=0.1, decimals=2,
                  help="Required surface-to-surface separation between non-neighboring tube segments."),
            Float("clearance", "Boundary clearance", 0.1, unit="% h", min=0., max=5., step=0.1, decimals=2,
                  help="Keep the outside of every tube this far inside the source boundary, including cavity walls.")),
        Section("Quality",
            Int("sides", "Tube sides", 12, min=8, max=32, step=2,
                help="Circular profile resolution. 12 sides suit clean plotter silhouettes; 20 or more suit close views."),
            Int("samples", "Curve samples per spacing", 6, min=4, max=16, step=2),
            Int("max_sites", "Packing site budget", 24000, min=500, max=100000, step=1000, advanced=True),
            Int("max_faces", "Output face budget", 2000000, min=20000, max=5000000, step=100000, advanced=True),
            Bool("repair", "Repair open mesh", False,
                 help="Explicitly infer and close an imperfect volume by resampling. May soften or close openings."),
            Int("repair_resolution", "Repair resolution", 160, min=48, max=320, step=16,
                advanced=True, visible_if={"repair": True})),
        Section("Model", *frame_params(stage_sets=False)),
    ],
    sample=".engine:make_sample",
    test_values=dict(spacing=20., diameter=5., length=16, sides=8, samples=4),
)
