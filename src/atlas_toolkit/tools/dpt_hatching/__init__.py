"""
Photo Hatching: a photo drawn as pen-plotter hatching, guided by depth from a DPT-family transformer on the GPU
(from the Colab notebooks DPT_Transformer, DPT_Hatching_generator, hatching_DPT_Large_2 and _3).

Outputs <name>_photohatch.svg, signed <desc>dpt-hatching</desc> so Atlas links them to this tool.
"""
from atlas_toolkit.core import Bool, Choice, FileRef, Float, Inputs, Int, Output, Section, Seed, Tool, paper_params
from atlas_toolkit.geom.depth import MODELS


def style_is(*names):
    return {"style": names}


TOOL = Tool(
    id="dpt-hatching",
    name="Photo Hatching",
    version="1.0.1",
    optional=(("depth", "Depth from a neural network (MiDaS / DPT, Depth Anything) on the GPU; without it, "
                        "a depth-map file or a rough depth from tone"),),
    category="image",
    icon="image",
    action="Hatch",
    summary="Draw a photo as plotter hatching: strokes that wrap its forms, guided by depth from a DPT transformer.",
    tagline="Engraving-style hatching from photographs, depth estimated on the GPU.",
    description=(
        "A depth model (MiDaS 3.1 BEiT-L-512 by default) estimates how far each part of the photo is. Strokes follow "
        "the forms that depth reveals, their density follows the photo's tone, darker tones get crossing layers, and "
        "the edges where one surface passes in front of another become outlines. The depth model runs in the "
        "separate GPU runtime (scripts/setup_ml.py) and is cached per photo; without it, use a depth-map file or the "
        "rough guess."),
    inputs=Inputs("image", exclude=(r"_depth\.png$",)),
    output=Output("{stem}_photohatch.svg", "svg"),
    signature="<desc>dpt-hatching</desc>",
    engine=".engine:run",
    runs_in="process",
    preview="svg",
    columns=("strokes",),
    params=[
        Section(
            "Depth",
            Choice("depth_source", "Depth from", "model", [("model", "Model"), ("file", "File"),
                                                           ("rough", "Rough")], segmented=True,
                   help="Model: estimated by a depth transformer on the GPU. File: a grey depth map (white = "
                        "near; <name>_depth.png next to the photo is used when no file is chosen). Rough: a guess "
                        "from tone, no GPU needed."),
            Choice("model", "Model", "dpt-beit-large-512", MODELS, visible_if={"depth_source": "model"},
                   help="MiDaS 3.1 BEiT-L-512: good overall layout, soft detail, MIT licence. DPT-Large: the "
                        "notebooks' model, the softest. Depth Anything V2 Large: by far the most detail (faces, thin "
                        "parts, railings), but its weights are licensed for non-commercial use only. Each downloads "
                        "once (~1.3 GB)."),
            Choice("detail", "Detail", "boost", [("native", "Native"), ("high", "High"), ("boost", "Boost")],
                   segmented=True, visible_if={"depth_source": "model"},
                   help="Native: the model's own resolution. High: about 1024 px (sharper, but can lose the "
                        "overall layout). Boost: the layout of the native pass with the fine detail of the high pass "
                        "(best for hatching)."),
            FileRef("depth_file", "Depth file", "", exts=(".png", ".tif", ".tiff", ".npy"),
                    visible_if={"depth_source": "file"}),
            Bool("save_depth", "Save the depth map", False,
                 help="Write <name>_depth.png (16-bit, white = near) next to the drawing, for reuse."),
        ),
        Section(
            "Tone",
            Float("contrast", "Local contrast", 0.35, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="Brings out detail in flat light (CLAHE). 0 keeps the photo's own tones."),
            Float("curve", "Tone curve", 1.0, min=0.3, max=3.0, step=0.05, decimals=2, slider=True,
                  help="Above 1 darkens the mid-tones (more ink), below 1 lightens them."),
            Float("white", "Paper white above", 0.85, min=0.3, max=1.0, step=0.01, decimals=2, slider=True,
                  help="Tones lighter than this get no ink at all: the highlights stay paper."),
            Float("fog", "Depth fog", 0.0, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="Lightens what is far away (aerial perspective), so the foreground stands out."),
        ),
        Section(
            "Hatching",
            Choice("style", "Style", "flow", [("flow", "Flow"), ("patches", "Patches"), ("contours", "Contours"),
                                              ("none", "None (outlines only)")],
                   help="Flow: curved strokes that follow the forms. Patches: notebook (d)'s mosaic of straight "
                        "hatching, fixed. Contours: lines of equal depth, like a topographic map. None: outlines only."),
            Choice("direction", "Direction", "form", [("form", "Around forms"), ("cross-form", "Over forms"),
                                                      ("image", "Along edges"), ("angle", "Fixed angle")],
                   visible_if=style_is("flow", "patches"),
                   help="Around forms: strokes wrap the shapes the depth reveals (engraving). Over forms: they run "
                        "across them. Along edges: they follow the photo's own edges and textures. Fixed angle: "
                        "straight hatching."),
            Float("angle", "Angle", 45.0, unit="°", min=-180, max=180, step=5, decimals=1,
                  visible_if=style_is("flow", "patches"),
                  help="The fixed angle, also used where the depth gives no direction (flat walls, sky)."),
            Int("layers", "Tone layers", 3, min=1, max=4, visible_if=style_is("flow", "patches"),
                help="Darker tones get extra crossing layers: the 2nd crosses the 1st at the crossing angle, the "
                     "3rd crosses it the other way, the 4th at 90°."),
            Float("cross_below", "Cross-hatch below", 0.5, min=0.05, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=lambda v: v.get("style") in ("flow", "patches") and int(v.get("layers", 1)) > 1,
                  help="Tones darker than this get crossing layers; lighter ones keep a single layer of strokes "
                       "(the engraver's way). The further layers start evenly below it."),
            Float("cross_angle", "Crossing angle", 55.0, unit="°", min=15, max=90, step=5, decimals=0,
                  visible_if=lambda v: v.get("style") in ("flow", "patches") and int(v.get("layers", 1)) > 1,
                  help="Angle between the first layer and the crossing ones: below 90° the crossings form "
                       "lozenges, as in copper engraving; 90° gives a square grid."),
            Float("spacing_dark", "Spacing, dark", 0.6, unit="mm", min=0.2, max=10.0, step=0.05, decimals=2,
                  visible_if=style_is("flow", "patches"), help="Distance between strokes in the darkest tones."),
            Float("spacing_light", "Spacing, light", 2.4, unit="mm", min=0.3, max=20.0, step=0.1, decimals=2,
                  visible_if=style_is("flow", "patches"), help="Distance between strokes in the lightest inked tones."),
            Float("stroke_len", "Longest stroke", 30.0, unit="mm", min=1.0, max=300.0, step=1, decimals=1,
                  visible_if=style_is("flow")),
            Float("min_len", "Shortest stroke", 1.5, unit="mm", min=0.0, max=20.0, step=0.5, decimals=1,
                  visible_if=style_is("flow"), help="Shorter strokes are dropped (they read as noise)."),
            Float("turn", "Turn limit", 35.0, unit="°", min=5, max=90, step=5, decimals=0, visible_if=style_is("flow"),
                  help="A stroke ends where the direction turns more than this in one step."),
            Float("field_smooth", "Smoothness", 2.0, unit="mm", min=0.2, max=20.0, step=0.25, decimals=2,
                  visible_if=style_is("flow", "patches", "contours"),
                  help="How much the directions (and contours) are smoothed: larger gives calmer, broader strokes."),
            Bool("stop_at_edges", "Stop at occlusion edges", True, visible_if=style_is("flow"),
                 help="Strokes end where one surface passes in front of another, instead of running across."),
            Float("hand", "Hand", 0.2, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=style_is("flow"),
                  help="A little irregularity in stroke length and spacing, so it reads drawn rather than machined."),
            Int("patches", "Patches", 2000, min=50, max=10000, visible_if=style_is("patches"),
                help="Number of superpixels (notebook d used 1000)."),
            Float("compactness", "Compactness", 10.0, min=1.0, max=100.0, step=1, decimals=1,
                  visible_if=style_is("patches"),
                  help="Higher gives rounder, more even patches; lower follows the photo and depth more closely."),
            Float("depth_weight", "Depth weight", 1.0, min=0.0, max=5.0, step=0.1, decimals=2,
                  visible_if=style_is("patches"), help="How much depth (vs colour) shapes the patches."),
            Bool("add_contours", "Add depth contours", False, visible_if=style_is("flow", "patches", "none"),
                 help="Also draw lines of equal depth, on their own pen."),
            Int("levels", "Contour lines", 40, min=2, max=400,
                visible_if=lambda v: v.get("style") == "contours" or v.get("add_contours")),
            Choice("level_mode", "Spread", "area", [("area", "Even on paper"), ("depth", "Even in depth")],
                   segmented=True, visible_if=lambda v: v.get("style") == "contours" or v.get("add_contours"),
                   help="Even on paper: lines spread over the picture (no bunching at edges). Even in depth: "
                        "equal depth steps, like a topographic map."),
            Float("bias", "Bunch towards", 0.0, min=-1.0, max=1.0, step=0.1, decimals=2,
                  visible_if=lambda v: v.get("style") == "contours" or v.get("add_contours"),
                  help="Positive puts more lines near the viewer, negative far away."),
            Float("far_cut", "Skip the farthest", 0.02, min=0.0, max=0.6, step=0.02, decimals=2,
                  visible_if=lambda v: v.get("style") == "contours" or v.get("add_contours"),
                  help="No contours in the farthest part of the depth range (sky, background)."),
            Seed("seed", "Seed", 7, visible_if=style_is("flow", "patches")),
        ),
        Section(
            "Lines",
            Bool("occlusions", "Occlusion outlines", True,
                 help="Draw the edges where one surface passes in front of another (from depth)."),
            Float("occlusion_sens", "Sensitivity", 0.5, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if={"occlusions": True}),
            Bool("photo_edges", "Photo edges", False, help="Also draw the photo's own edges (contrast, texture)."),
            Float("photo_sens", "Edge sensitivity", 0.4, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if={"photo_edges": True}),
            Float("edge_min", "Shortest outline", 3.0, unit="mm", min=0.0, max=50.0, step=0.5, decimals=1),
        ),
        Section("Paper", *paper_params("A3", "portrait", 15.0)),
        Section(
            "Pens",
            Float("hatch_width", "Hatching pen", 0.3, unit="mm", min=0.05, max=3, step=0.05, decimals=2),
            Float("contour_width", "Contour pen", 0.3, unit="mm", min=0.05, max=3, step=0.05, decimals=2),
            Float("outline_width", "Outline pen", 0.5, unit="mm", min=0.05, max=3, step=0.05, decimals=2),
            Bool("layer_per_direction", "One layer per direction", False,
                 help="Each tone layer (direction) on its own layer, e.g. to plot them in different inks."),
            Bool("optimise", "Order strokes to cut travel", True),
        ),
        Section(
            "Quality",
            Int("work_px", "Working resolution", 1600, min=400, max=4000, advanced=True,
                help="Long side of the photo while computing (Preview uses at most 900)."),
            Float("step", "Step", 0.3, unit="mm", min=0.05, max=2.0, step=0.05, decimals=2, advanced=True,
                  help="Integration step along strokes."),
            Float("simplify", "Simplify", 0.03, unit="mm", min=0.0, max=1.0, step=0.01, decimals=3, advanced=True),
            collapsed=True,
        ),
    ],
    sample=".engine:make_sample",
    test_values={"depth_source": "rough", "work_px": 400, "spacing_dark": 1.5, "spacing_light": 4.0,
                 "paper": "A5"},
    legacy=("Python/google_colab/DPT_Transformer.ipynb", "Python/google_colab/DPT_Hatching_generator.ipynb",
            "Python/google_colab/hatching_DPT_Large_2.ipynb", "Python/google_colab/hatching_DPT_Large_3.ipynb"),
)
