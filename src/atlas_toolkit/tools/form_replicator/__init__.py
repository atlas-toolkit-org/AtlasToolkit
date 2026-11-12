"""Form Replicator: sculptural arrays of one model, signed atlas-toolkit:form-replicator."""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, Vec3, frame_params

LAYOUTS = [
    ("cross2d", "Cross · 2D"), ("cross3d", "Cross · 3D"),
    ("star2d", "Star · 2D"), ("star3d", "Star · 3D"),
    ("clock", "Clock / ring"), ("rosette", "Rosette"), ("urchin", "Sea urchin"),
    ("sunflower", "Sunflower spiral"), ("helix", "Helix"), ("torus", "Toroidal wreath"),
    ("trefoil", "Trefoil knot"), ("mobius", "Möbius wreath"),
]
RAYS = ("cross2d", "cross3d", "star2d", "star3d")


def layouts(*names):
    return {"layout": names}


TOOL = Tool(
    id="form-replicator", name="Form Replicator", version="1.0.1", category="mesh", icon="rosette",
    action="Compose", summary="Compose a model into crosses, stars, urchins, spirals and sculptural wreaths.",
    tagline="One shape, a new sculpture: rhythmic silhouettes and open spaces for line art.",
    description=(
        "Replicate any closed model along rays, rings, a sphere, a golden-angle spiral or a spatial curve. "
        "Touching and overlapping copies are always boolean-unioned: internal surfaces disappear, while "
        "deliberate gaps remain. Source Up and Front set the model's frame; Layout plane sets the composition's "
        "plane (or the axis perpendicular to it). Aim aligns either the model's up or its front along the chosen "
        "orientation. Distances are percentages of the source height, before Copy size and Stretch. "
        "Run preserves model units. Preview uses fewer source faces but exactly the same copy positions. "
        "Use the output with Plotter Vision, Contour Lines or Sketch Hatching; front-plane compositions read "
        "especially well through an orthographic front view. The source file is never changed."),
    inputs=Inputs("mesh", exclude=(r"_forms_[a-z0-9]+\.(stl|obj|ply)$",)),
    output=Output("{stem}_forms_{layout}.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:form-replicator", engine=".engine:run", runs_in="process", preview="mesh",
    columns=("copies", "solids", "faces"),
    params=[
        Section("Form",
            Choice("layout", "Layout", "star2d", LAYOUTS),
            Choice("plane", "Layout plane", "xz", [("xz", "XZ · front"), ("xy", "XY · top"),
                                                        ("yz", "YZ · side")],
                   help="Planar layouts lie in this plane. Spatial layouts wind around its perpendicular axis."),
            Int("arms", "Star points", 5, min=3, max=32, visible_if=layouts("star2d")),
            Choice("symmetry", "Spatial symmetry", "icosahedron",
                   [("tetrahedron", "Tetrahedral · 4"), ("octahedron", "Octahedral · 6"),
                    ("cube", "Cubic · 8"), ("icosahedron", "Icosahedral · 12"),
                    ("dodecahedron", "Dodecahedral · 20")], visible_if=layouts("star3d")),
            Int("per_arm", "Copies per arm", 3, min=1, max=64, visible_if=layouts(*RAYS)),
            Float("spacing", "Arm step", 55.0, unit="% h", min=1, max=1000, step=5,
                  visible_if=layouts(*RAYS), help="Centre-to-centre step along each ray; small steps fuse neighbours."),
            Bool("centre_copy", "Copy at centre", True, visible_if=layouts(*RAYS)),
            Int("count", "Copies around / along", 48, min=1, max=512,
                visible_if=layouts(*(k for k, _ in LAYOUTS if k not in RAYS)),
                help="Copies total, except toroidal and Möbius wreaths: copies in each row."),
            Float("radius", "Radius", 170.0, unit="% h", min=1, max=5000, step=10,
                  visible_if=layouts(*(k for k, _ in LAYOUTS if k not in RAYS)),
                  help="Ring or curve radius; sunflower's outer radius. Toroids and Möbius bands need a larger radius than their half-width."),
            Float("inner_radius", "Inner reach", 0.0, unit="% h", min=0, max=5000, step=10,
                  visible_if=layouts(*RAYS), help="Empty reach before the first half-step along each arm."),
            Float("phase", "Phase", 0.0, unit="°", min=-180, max=180, step=5, advanced=True,
                  help="Rotate the complete layout around its perpendicular axis."),
            Float("arc", "Ring arc", 360.0, unit="°", min=5, max=360, step=5,
                  visible_if=layouts("clock"), help="360 closes the ring without duplicating the first copy."),
            Int("petals", "Rosette lobes", 6, min=2, max=32, visible_if=layouts("rosette")),
            Float("lobes", "Lobe depth", 25.0, unit="% radius", min=0, max=80, step=5,
                  visible_if=layouts("rosette")),
            Float("coverage", "Sphere coverage", 180.0, unit="°", min=10, max=180, step=5,
                  visible_if=layouts("urchin"), help="180: whole sphere; 90: a hemisphere. Golden-angle placement avoids crowded poles."),
            Float("turns", "Turns", 2.5, min=0.1, max=20, step=0.25, visible_if=layouts("helix")),
            Float("pitch", "Rise per turn", 80.0, unit="% h", min=0, max=2000, step=10,
                  visible_if=layouts("helix")),
            Float("minor_radius", "Tube radius / half-width", 60.0, unit="% h", min=1, max=2000, step=5,
                  visible_if=layouts("torus", "mobius"), help="Toroid's tube radius or Möbius strip's half-width; must be smaller than Radius."),
            Int("rows", "Rows", 4, min=1, max=32, visible_if=layouts("torus", "mobius")),
        ),
        Section("Copies",
            Float("copy_size", "Copy size", 75.0, unit="%", min=1, max=1000, step=5,
                  help="Uniform scale relative to the original model; layout distances stay fixed."),
            Vec3("stretch", "Stretch XYZ", (100.0, 100.0, 100.0), unit="%", min=5, max=1000, step=5,
                 advanced=True, help="In the source's upright frame, before aiming. Elongate Z for spines or petals."),
            Choice("anchor", "Place by", "centre", [("centre", "Centre"), ("base", "Base")],
                   help="The source's box centre or bottom centre sits on each layout point."),
            Choice("orientation", "Orientation", "radial", [("radial", "Outward"), ("inward", "Inward"),
                   ("tangent", "Along curve"), ("normal", "Surface normal"), ("upright", "Keep upright")],
                   help="Outward follows rays or points away from the axis; on a sphere it points away from the centre. "
                        "Surface normal follows the toroid or Möbius strip's skin."),
            Choice("aim_axis", "Aim", "up", [("up", "Model up"), ("front", "Model front")],
                   visible_if=lambda v: v.get("orientation") != "upright"),
            Float("end_scale", "End size", 100.0, unit="% of start", min=5, max=300, step=5,
                  help="Taper along each arm, from inside to outside in a sunflower, or along the sequence."),
            Float("roll", "Roll", 0.0, unit="°", min=-180, max=180, step=5,
                  help="Turn each copy around the axis selected in Aim."),
            Float("spin", "Progressive roll", 0.0, unit="°", min=-1440, max=1440, step=15,
                  help="Extra roll accumulated from the first to the last copy, or down each arm."),
        ),
        Section("Variation",
            Float("irregularity", "Irregularity", 0.0, min=0, max=1, step=0.05, decimals=2, slider=True,
                  help="Scales the same seeded displacements, turns and size changes smoothly from order to disorder."),
            Float("position_jitter", "Position variation", 15.0, unit="% h", min=0, max=200, step=5, advanced=True),
            Float("angle_jitter", "Angle variation", 15.0, unit="°", min=0, max=180, step=5, advanced=True),
            Float("size_jitter", "Size variation", 15.0, unit="%", min=0, max=90, step=5, advanced=True),
            Float("omit", "Leave out", 0.0, unit="%", min=0, max=95, step=5, advanced=True,
                  help="Seeded thinning; surviving copies keep their original positions and variations."),
            Seed("seed", "Seed", 1),
        ),
        Section("Source model", *frame_params(stage_sets=False)),
        Section("Detail and limits",
            Float("contact_weld", "Contact weld", 0.1, unit="% copy size", min=0, max=0.5, step=0.01,
                  decimals=3, advanced=True,
                  help="Only if the union has pinched contacts: retry with copies enlarged by this tiny amount. "
                       "Reported when used. 0 requires the exact planned sizes."),
            Int("source_faces", "Source detail", 12000, min=0, max=2000000, step=1000, advanced=True,
                help="Target source faces before replication; 0 keeps all detail. If a reduction pinches the solid, a higher detail is reported."),
            Bool("repair_open", "Close open source", True, advanced=True,
                 help="If exact solid conversion fails, reuse Atlas's signed-distance repair. The report states when detail softens."),
            Int("repair_resolution", "Closing resolution", 128, min=32, max=384, step=32, advanced=True),
            Int("draft_faces", "Preview source detail", 2000, min=64, max=20000, step=500, advanced=True,
                help="Preview also limits total source faces to 200,000 across copies; placements are unchanged."),
            Int("work_faces", "Working face limit", 4000000, min=10000, max=20000000, step=100000, advanced=True,
                help="Refuse an oversized replication before building copies. Lower Source detail or the copy count."),
        ),
    ],
    sample=".engine:make_sample",
    test_values=dict(layout="clock", count=6, radius=65, copy_size=100, source_faces=0),
)
