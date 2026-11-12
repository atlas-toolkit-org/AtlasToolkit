"""
Compress: squash a model between press plates, crush it from around an axis or from every side, press it into a
mould (cube, sphere, cylinder, cone, pyramid, prism, octahedron, icosahedron, dodecahedron) or morph it onto one.
One step per run.

Outputs <name>_compress.stl, signed atlas-toolkit:mesh-compress.
"""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, Vec3, frame_params


def free(v):
    return v.get("mode", "free") == "free"


def shaped(v):
    return v.get("mode") == "shape"


def physical(v):
    return free(v) or v.get("fit", "mould") == "mould"


def pressing(v):
    return free(v) and v.get("press", "plates") in ("plates", "floor")


SHAPE_CHOICES = [("cube", "Cube / box"), ("sphere", "Sphere / ellipsoid"), ("cylinder", "Cylinder"),
                 ("cone", "Cone"), ("pyramid", "Pyramid"), ("prism", "Prism"), ("octahedron", "Octahedron"),
                 ("icosahedron", "Icosahedron"), ("dodecahedron", "Dodecahedron")]

TOOL = Tool(
    id="mesh-compress",
    name="Compress",
    version="0.1.0",
    category="mesh",
    icon="press",
    action="Compress",
    summary="Squash a model in a press, crush it from around or every side, or press it into a cube, sphere, "
            "pyramid or another solid.",
    tagline="A head flattened in a hydraulic press, a bust crammed into a cube: draw it with Contour Lines.",
    description=(
        "Free compression closes rigid colliders on the model: two press plates (or one onto the floor, "
        "optionally tilted for a shear press), a tube tightening around an axis, or pressure from every side. "
        "Into a shape closes a mould on it, from the size that just holds the model down to a chosen volume, "
        "or (Morph) slides every point onto the shape's surface along its ray, keeping its relief. The physical "
        "modes are a quasi-static position-based dynamics solve of the surface: a skin that resists stretch and "
        "creeps plastically, bending springs that set the size of the folds, a volume that the material keeps "
        "or gives up (Compressibility), friction against the press, and approximate self-collision so the folds "
        "stack instead of passing through each other. Seeded imperfections choose where the folds form. The "
        "model is remeshed to near-uniform triangles first. One step per run; the output stays in the model's "
        "units and position."),
    inputs=Inputs("mesh", exclude=(r"(?i)_compress\.(stl|obj|ply)$",)),
    output=Output("{stem}_compress.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-compress",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("strain", "volume", "faces"),
    params=[
        Section(
            "Compression",
            Choice("mode", "Mode", "free", [("free", "Free"), ("shape", "Into a shape")],
                   segmented=True,
                   help="Free: plates, a tube or all-round pressure squash the model. Into a shape: a mould "
                        "closes on it, or it morphs onto the shape."),
            Choice("press", "Press", "plates", [("plates", "Two plates"), ("floor", "Onto the floor"),
                                                ("radial", "Around an axis"), ("iso", "From every side")],
                   visible_if=free,
                   help="Two plates close from both ends; Onto the floor keeps the bottom plate still, so the "
                        "contact at the base sticks while the top spreads; Around an axis is a tightening "
                        "tube (a column wrung thin); From every side shrinks an enclosing sphere (vacuum-packed)."),
            Choice("axis", "Axis", "up", [("up", "Up"), ("front", "Front"), ("side", "Side")], segmented=True,
                   visible_if=lambda v: (free(v) and v.get("press", "plates") != "iso") or shaped(v),
                   help="Plates: the direction they close along (Up squashes the height, Front flattens the "
                        "face, Side squeezes the width). Around an axis: the tube's axis. Shapes: the "
                        "direction of the shape's own height (a cone's apex, a prism's axis)."),
            Float("strain", "Compression", 42.0, unit="%", min=0.0, max=90.0, step=1.0, decimals=1, slider=True,
                  visible_if=free,
                  help="How far the press closes, % of the model's extent along the axis (plates) or of its "
                       "radius (tube, all round). 25-45% keeps a face legible while it barrels and folds; past "
                       "60% it becomes a slab."),
            Float("tilt", "Plate tilt", 0.0, unit="°", min=-45.0, max=45.0, step=1.0, decimals=1,
                  visible_if=pressing,
                  help="Tilts the moving plate, so one side is crushed harder: a shear press, a wedge."),
            Choice("shape", "Shape", "cube", SHAPE_CHOICES, visible_if=shaped,
                   help="The solid the model is pressed into or morphed onto. Every shape takes an aspect ratio "
                        "below (a cube becomes a slab or a brick, a sphere an ellipsoid)."),
            Int("sides", "Prism sides", 3, min=3, max=12, slider=True,
                visible_if=lambda v: shaped(v) and v.get("shape") == "prism",
                help="3: a triangular prism; 6: hexagonal; 12: nearly a cylinder."),
            Choice("fit", "Fit", "mould", [("mould", "Pressed into a mould"), ("morph", "Morph onto it")],
                   segmented=True, visible_if=shaped,
                   help="Mould: the shape is a rigid container that closes in, the model fills it and the "
                        "excess surface folds. Morph: a smooth projection onto the shape, no folds, the relief "
                        "kept."),
            Float("fill", "Fill", 0.85, min=0.0, max=1.0, step=0.05, decimals=2, slider=True, visible_if=shaped,
                  help="How far the mould closes (or the morph goes): 0 the untouched model, 1 the full shape."),
            Float("shape_size", "Shape volume", 100.0, unit="% vol", min=20.0, max=200.0, step=5.0, decimals=0,
                  visible_if=shaped,
                  help="The shape's volume at fill 1, % of the model's own. Mould: 100% fills it with clay; "
                       "under 100% squeezes harder (with compressible material) or bulges between facets; over "
                       "100% only presses the protruding parts."),
            Vec3("aspect", "Aspect W × D × H", (1.0, 1.0, 1.0), min=0.2, max=5.0, step=0.1, decimals=2,
                 visible_if=shaped,
                 help="Stretches the shape along its own width, depth and height (the axis above); its volume "
                      "stays as set. (2, 1, 0.5) turns a cube into a brick."),
            Float("turn", "Shape turn", 0.0, unit="°", min=-180.0, max=180.0, step=5.0, decimals=1,
                  visible_if=shaped, advanced=True,
                  help="Turns the shape about its axis, so a corner or an edge faces the front."),
            Choice("seat", "Mould stands", "centre", [("centre", "About the centre"), ("floor", "On the floor")],
                   segmented=True, visible_if=lambda v: shaped(v),
                   help="About the centre: the mould shrinks toward the model's middle. On the floor: its base "
                        "stays on the model's base, so a bust settles into it from above."),
            Float("relief", "Keep relief", 1.0, min=0.0, max=1.5, step=0.05, decimals=2, slider=True,
                  visible_if=lambda v: shaped(v) and v.get("fit") == "morph",
                  help="Morph: how much of the features (nose, brow, lips) stay raised on the shape's faces. 0: "
                       "the bare shape; 1: the full relief; above 1 exaggerates it."),
        ),
        Section(
            "Material",
            Float("compressibility", "Compressibility", 0.05, min=0.0, max=1.0, step=0.05, decimals=2,
                  slider=True, visible_if=physical,
                  help="0: incompressible clay, it keeps its volume and bulges out sideways. 1: a crushed can or "
                       "foam, it loses the volume the press takes and folds inward."),
            Float("plasticity", "Plasticity", 0.7, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=physical,
                  help="How freely the skin flows. Low: an elastic sheet that resists shortening and buckles "
                       "into folds (foil, rubber). High: putty that spreads smoothly."),
            Float("stiffness", "Bending stiffness", 0.5, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=physical,
                  help="Resistance to bending. Low: many fine, sharp creases. High: a few broad, rounded folds."),
            Float("friction", "Friction", 0.8, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=physical,
                  help="Grip of the press on the surface. High: the contact sticks and the middle barrels out. "
                       "Low: the model slides out sideways."),
            Float("imperfection", "Imperfection", 0.3, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=physical,
                  help="Seeded long-wave dents that decide where the folds start, as flaws in real material do. "
                       "0: only the model's own shape breaks the symmetry."),
            Seed("seed", "Seed", 0, visible_if=physical, help="Same seed, same imperfections and folds."),
        ),
        Section("Model", *frame_params(stage_sets=False)),
        Section(
            "Quality",
            Int("sim_faces", "Simulation faces", 12_000, min=2_000, max=120_000, step=1_000,
                visible_if=physical,
                help="The press acts on a near-uniform copy of the model with about this many triangles; its "
                     "motion then carries the full-detail model along. More: finer, more numerous folds, slower "
                     "(the time grows about linearly)."),
            Int("out_faces", "Output faces", 400_000, min=10_000, max=2_000_000, step=10_000, advanced=True,
                help="Models denser than this are simplified first; long edges are split (within this budget) "
                     "so large flat triangles can bend with the folds."),
            Int("steps", "Load steps", 48, min=8, max=200, advanced=True, visible_if=physical,
                help="The press closes over this many steps. More: smoother, slower; the result converges near "
                     "40."),
            Int("iterations", "Iterations per step", 16, min=2, max=60, advanced=True, visible_if=physical,
                help="Constraint passes per step: more makes the skin stiffer and the volume more exact."),
            Bool("collide", "Self-collision", True, advanced=True, visible_if=physical,
                 help="Keeps folds from passing through each other (approximately). Off is faster."),
            Int("smooth", "Smooth motion", 2, min=0, max=20, advanced=True, visible_if=physical,
                help="Taubin passes over the simulated motion (not the model, whose detail is kept): removes "
                     "solver jitter that would show as wavering contour lines."),
            Int("draft_faces", "Preview faces", 5_000, min=1_000, max=50_000, advanced=True,
                help="Preview simulates on this many faces, with half the load steps, and outputs four times as "
                     "many."),
            collapsed=True),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"sim_faces": 2_000, "out_faces": 10_000, "steps": 8, "iterations": 4},
    legacy=(),
)
