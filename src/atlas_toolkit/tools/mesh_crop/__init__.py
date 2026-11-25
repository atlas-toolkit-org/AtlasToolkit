"""Shape Crop: keep the part of a model inside a box, sphere, cylinder or pyramid you place on it; or cut that region
away, add the shape (union), or keep both remainders (symmetric difference). Signed meshes: atlas-toolkit:mesh-crop."""
from atlas_toolkit.core import Choice, Inputs, Int, Output, Section, Tool, Vec3

SHAPES = [("box", "Box"), ("sphere", "Sphere"), ("cylinder", "Cylinder"), ("pyramid", "Pyramid")]
OPERATIONS = [("intersect", "Keep inside · intersection"), ("subtract", "Cut away · difference"),
              ("union", "Add the shape · union"), ("xor", "Both remainders · symmetric difference")]

TOOL = Tool(
    id="mesh-crop", name="Shape Crop", version="0.1.0", category="mesh", group="prepare", icon="shape-crop", action="Crop",
    summary="Keep the part of a model inside a box, sphere, cylinder or pyramid, or cut it away.",
    tagline="Place a shape on the model; keep what is inside it.",
    description=("In the Shape tab, move, rotate and scale the shape with its handles, or draw a box over the view; "
                 "the model is cropped live as you go. Keep inside keeps the region; Cut away removes it. Add the "
                 "shape joins the two (a plinth, a block), and Both remainders keeps the model without the region "
                 "and the shape without the model. Closed models get closed cuts; open scans are clipped along the "
                 "shape and stay open. Sizes are a share of the model's largest dimension or model units; each file "
                 "keeps its own placement. Model units are preserved."),
    inputs=Inputs("mesh", exclude=(r"_crop\.\w+$", r"_crop_shape(-\d+)?\.\w+$")),
    output=Output("{stem}_crop.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-crop", engine=".engine:run", viewer=".viewer:CropView",
    runs_in="process", preview="mesh", columns=("faces", "kept"),
    params=[
        Section("Crop",
            Choice("operation", "Operation", "intersect", OPERATIONS,
                   help="Keep inside: the part of the model within the shape (the crop). Cut away: the model "
                        "without it. Add the shape: model and shape as one solid. Both remainders: the model "
                        "without the shape plus the shape without the model."),
            Choice("shape", "Shape", "box", SHAPES, segmented=True,
                   help="Each size is set on its own, so a box can be any block, a sphere an ellipsoid and a "
                        "pyramid any rectangular pyramid (its apex points along the shape's own +Z)."),
            Choice("cut", "Cut faces", "closed", [("closed", "Close the cut"), ("open", "Leave open")],
                   visible_if={"operation": ("intersect", "subtract")},
                   help="Close the cut: a closed model stays closed, the shape's surface filling the cut. Leave "
                        "open: only the model's own surface is kept, clipped exactly along the shape (quicker, "
                        "and what an open scan gets either way)."),
            Choice("export_mode", "Remainders", "combined", [("combined", "One combined mesh"),
                                                              ("separate", "Two separate files")],
                   visible_if={"operation": "xor"},
                   help="Separate: the main file holds the model's remainder and a _shape sibling the shape's. "
                        "Combined: both in one file; they touch where the model met the shape."),
            Int("segments", "Curve segments", 0, min=0, max=1440, step=16, advanced=True,
                visible_if={"shape": ("sphere", "cylinder")},
                help="How finely a closed cut follows a sphere or cylinder. 0 = automatic, from the model's own "
                     "edge length (48 to 720 around)."),
        ),
        Section("Shape",
            Choice("reference", "Position relative to", "center", [("center", "Model bounds centre"),
                                                                   ("world", "World origin")], per_input=True,
                   help="Centre-relative positions repeat the same placement across a batch of models."),
            Vec3("position", "Shape centre", (0.0, 0.0, 0.0), min=-1e12, max=1e12, decimals=4, unit="units",
                 per_input=True, help="Where the shape's centre sits, in model units, from the chosen reference."),
            Vec3("rotation", "Shape rotation", (0.0, 0.0, 0.0), min=-360.0, max=360.0, decimals=4, step=1.0,
                 unit="°", per_input=True, help="XYZ Euler angles in degrees: about X, then Y, then Z."),
            Choice("size_unit", "Size in", "percent", [("percent", "% of model size"), ("units", "Model units")],
                   per_input=True, help="% of model size: a share of the model's largest dimension, so the same "
                                        "setting suits models of any scale."),
            Vec3("size", "Shape size", (60.0, 60.0, 60.0), min=1e-9, max=1e12, decimals=4, step=5.0,
                 per_input=True, help="The shape's width, depth and height along its own axes (a sphere's "
                                      "diameters, a cylinder's two diameters and height)."),
        ),
    ],
    preset_skip=("reference", "position", "rotation", "size_unit", "size"),
    needs_if=(("solid", {"operation": ("union", "xor")}),),
    official_presets=False, per_input_label="shape",
    sample=".engine:make_sample",
    test_variants=({"operation": "subtract", "shape": "sphere"}, {"operation": "union", "shape": "cylinder"},
                   {"operation": "xor", "shape": "pyramid", "export_mode": "separate"},
                   {"cut": "open", "shape": "sphere", "rotation": (20.0, 30.0, 0.0), "size": (110.0, 90.0, 100.0)}),
)
