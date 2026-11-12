"""
Shape Fusion: transformations between different shapes without matching vertices. Morph a model into another one (or
into a torus, a sphere, a block, a column), fuse them with a smooth fillet, carve one out of the other or keep what
both hold, everywhere at once or swept across the model as a front, in one stage or a sequence.

Outputs <name>_fusion.stl (and <name>_fusion_stages/ with stages.json), signed atlas-toolkit:mesh-fusion.
"""
from atlas_toolkit.core import (Choice, Float, Bool, FileRef, Inputs, Int, Output, Section, Tool, Vec3, frame_params,
                                influence_params)
from atlas_toolkit.core.params import AXIS_CHOICES

MESH_EXTS = (".stl", ".obj", ".ply", ".glb", ".gltf", ".off")


def staged(v):
    return int(v.get("stages", 1)) > 1


def from_file(v):
    return v.get("target", "torus") == "file"


def smooth_mode(v):
    return v.get("mode", "morph") != "morph"


def dropping(v):
    return v.get("keep") == "drop"


TOOL = Tool(
    id="mesh-fusion",
    name="Shape Fusion",
    version="1.0",
    category="mesh",
    icon="fusion",
    action="Fuse",
    summary="Morph, fuse or carve a model with another shape: hybrid sculptures and topology-changing transitions.",
    tagline="A bust turning into a ring, two heads fused face to face, a figure bored through or hewn into a crystal: "
            "one closed mesh, ready for Contour Lines, Sketch Hatching or Plotter Vision.",
    description=(
        "Both shapes are sampled as signed-distance volumes on one grid, so they need no matching vertices and the "
        "result can change topology (a hole opens, two pieces join). The target is another model, the model itself "
        "(turned, moved and repeated around its vertical axis: Janus heads, three faces, a kiss) or a built-in shape "
        "(torus, sphere, block, column). Morph extracts the surface between the two fields: Blend 0 is the model, 1 "
        "the target. Fuse joins them with a fillet of the smooth radius, Carve cuts the target out of the model, "
        "Intersect keeps what both hold. Sweep moves the change across the model as a soft front (from the base, the "
        "top, the front, the centre ...), so a sequence of stages reads as a metamorphosis; Acts on limits it to a "
        "band or a sphere. Shapes that overlap little thin out half way: align them (Fit, Offset) or add Swell. "
        "Stage 0 is the model, and Morph's last stage the placed target, both exactly as they are."),
    inputs=Inputs("mesh", exclude=(r"(?i)_fusion(?:_stage_\d+)?\.(stl|obj|ply)$",)),
    output=Output("{stem}_fusion.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-fusion",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("stages", "faces", "pieces"),
    params=[
        Section(
            "Fusion",
            Choice("mode", "Mode", "morph", [("morph", "Morph"), ("fuse", "Fuse"), ("carve", "Carve"),
                                             ("intersect", "Intersect")], segmented=True,
                   help="Morph: the model becomes the target, through the surface between them. Fuse: both, joined "
                        "by a smooth fillet. Carve: the target cut out of the model. Intersect: only what both hold "
                        "(a head hewn by a block)."),
            Float("amount", "Blend", 0.5, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="How far the change has gone: 0 is the model, 1 the full result (Morph: the target). With "
                       "several stages, the last stage's blend."),
            Float("radius", "Smooth radius", 6.0, unit="% h", min=0.0, max=40.0, step=0.5, decimals=1,
                  visible_if=smooth_mode,
                  help="Fillet where the shapes meet, % of the model's height: 0 a sharp seam, 4-8 a sculpted join, "
                       "15+ a soft, melted one."),
            Float("swell", "Swell", 0.0, unit="% h", min=0.0, max=20.0, step=0.5, decimals=1,
                  help="Adds volume half way through the change (none at either end), so shapes that overlap "
                       "little do not thin out or split in between."),
        ),
        Section(
            "Target",
            Choice("target", "Target", "torus", [("file", "Another model"), ("self", "The model itself"),
                                                 ("torus", "Torus"), ("sphere", "Sphere"), ("box", "Block"),
                                                 ("cylinder", "Column")],
                   help="Another model (a file, or a workflow's side input); the model itself, turned, moved or "
                        "repeated (Janus heads, a kiss, three faces); or a built-in shape, as tall as the model."),
            FileRef("target_file", "Target model", "", exts=MESH_EXTS, visible_if=from_file,
                    help="The second model. A closed solid works best; an open one is closed by its winding number."),
            Float("tube", "Tube", 30.0, unit="%", min=5.0, max=90.0, step=1.0, decimals=0,
                  visible_if={"target": "torus"},
                  help="Torus: the tube's radius as a share of the ring's. The ring stands upright, its hole facing "
                       "the front."),
            Float("aspect", "Width", 50.0, unit="% h", min=5.0, max=200.0, step=5.0, decimals=0,
                  visible_if=lambda v: v.get("target") in ("box", "cylinder"),
                  help="Block: its width and depth; column: its diameter; % of its height."),
            Choice("fit", "Fit", "height", [("height", "Same height"), ("size", "Same size"),
                                            ("keep", "Keep placement")], segmented=True, visible_if=from_file,
                   help="Same height: as tall as the model, on the same base, centred across. Same size: its largest "
                        "side as the model's. Keep placement: where the file puts it, in the same units (two "
                        "models exported from one scene)."),
            Float("target_scale", "Scale", 1.0, min=0.05, max=5.0, step=0.05, decimals=2,
                  help="Scales the target after fitting (built-in shapes and the model itself: about the model's "
                       "centre)."),
            Vec3("target_rotate", "Turn", (0.0, 0.0, 0.0), unit="°", min=-180.0, max=180.0, step=5.0, decimals=1,
                 help="Turns the target about X, then Y, then Z, about its own centre (before fitting)."),
            Vec3("target_offset", "Offset", (0.0, 0.0, 0.0), unit="% h", min=-300.0, max=300.0, step=5.0,
                 decimals=1, help="Moves the target, % of the model's height: X across, Y depth (front is −), Z up."),
            Int("copies", "Copies", 1, min=1, max=8,
                help="Repeats the target evenly around the model's vertical axis: 2 copies of the model itself "
                     "is a Janus head, 3 three faces; 3 rings an armillary sphere."),
            Choice("target_up", "Target up", "auto", AXIS_CHOICES, visible_if=from_file, advanced=True,
                   help="Which way is up in the target file (auto: as for the model)."),
            Choice("target_front", "Target front", "auto", AXIS_CHOICES, visible_if=from_file, advanced=True,
                   help="The side the target faces (auto: as for the model)."),
        ),
        Section(
            "Sweep",
            Choice("sweep", "Sweep", "none", [("none", "Everywhere"), ("up", "From the base"), ("down", "From the top"),
                                              ("front", "From the front"), ("back", "From the back"),
                                              ("left", "From the left"), ("right", "From the right"),
                                              ("out", "From the centre"), ("in", "From the outside")],
                   help="Everywhere: the whole model changes together. Otherwise the change travels across it as a "
                        "soft front: half way, one end is the result and the other still the model."),
            Float("sweep_width", "Front width", 35.0, unit="%", min=2.0, max=100.0, step=1.0, decimals=0,
                  visible_if=lambda v: v.get("sweep", "none") != "none",
                  help="How soft the front is, % of the way across: 10 a sharp line, 50+ a long gradient."),
            collapsed=True,
        ),
        Section("Acts on", *influence_params(), collapsed=True,
                help="Where the change happens; elsewhere the model stays as it is, with a feathered transition."),
        Section(
            "Stages",
            Int("stages", "Stages", 1, min=1, max=8, slider=True,
                help="1: one result. More: a sequence from the model (stage 0, untouched) to the blend, every stage "
                     "in place."),
            Bool("save_stages", "Also save every stage", True, visible_if=staged,
                 help="Stage files go in <name>_fusion_stages/ with stages.json (its frame), so Contour Lines draws "
                      "them all with one scale, position and slice grid."),
            Choice("layout", "Primary output", "final", [("final", "Last stage"), ("progression", "All stages")],
                   segmented=True, visible_if=staged,
                   help="All stages: side by side in one file, for a 3D print or a single view of the sequence."),
            Choice("axis", "Side by side along", "x", [("x", "X"), ("y", "Y"), ("z", "Z")], segmented=True,
                   visible_if=lambda v: staged(v) and v.get("layout") == "progression"),
            Float("gap", "Gap", 15.0, unit="% h", min=0.0, max=200.0, step=1.0, decimals=1,
                  visible_if=lambda v: staged(v) and v.get("layout") == "progression",
                  help="Clearance between stages, % of the height."),
        ),
        Section(
            "Clean-up",
            Choice("keep", "Loose pieces", "all", [("all", "Keep all"), ("drop", "Drop small"),
                                                   ("largest", "Largest only")], segmented=True,
                   help="Half way between shapes that overlap little, thin parts can come loose. Keep all leaves "
                        "them; drop small removes pieces under a share of the volume; largest only keeps one body."),
            Float("min_piece", "Smaller than", 0.5, unit="% vol", min=0.0, max=20.0, step=0.1, decimals=2,
                  visible_if=dropping, help="Pieces below this share of the total volume are removed."),
            Int("smooth", "Smoothing", 3, min=0, max=30,
                help="Taubin passes over the extracted surface: removes the grid's faint facets without shrinking "
                     "it. 0 keeps the raw marching-cubes surface."),
            Int("max_faces", "Output faces", 600_000, min=0, max=20_000_000, step=50_000,
                help="Simplify the result to at most this many faces (quadric decimation). 0 keeps every face of "
                     "the grid."),
            collapsed=True,
        ),
        Section("Model", *frame_params(stage_sets=False)),
        Section(
            "Quality",
            Int("resolution", "Resolution", 256, min=48, max=512, step=16,
                help="Samples along the longest side of both shapes together. 256 keeps a head's features; 384 or "
                     "512 for fine detail or a target placed far away (memory and time grow with the cube)."),
            Int("draft_resolution", "Preview resolution", 128, min=32, max=512, step=16, advanced=True,
                help="Preview samples along the longest side (Run always uses Resolution)."),
            Int("draft_faces", "Preview input faces", 60_000, min=2_000, max=2_000_000, advanced=True,
                help="Preview simplifies bigger models to this many faces before sampling; Run samples them in "
                     "full."),
            collapsed=True),
    ],
    preset_skip=("up_axis", "front", "rotate", "target_file", "target_up", "target_front"),
    test_values={"resolution": 48, "draft_resolution": 40, "stages": 2, "max_faces": 0},
)
