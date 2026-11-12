"""
Shape Outline: one closed outline around a drawing (from SVG_Shape_outline_v1, the concave-hull extractor).

Outputs <name>_outline.svg, signed <desc>svg-shape-outline</desc> so Atlas links them to this tool.
"""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Tool


def _layers(info):
    return [("", "Every layer")] + [(n, n) for n in (getattr(info, "groups", None) or [])]


TOOL = Tool(
    id="svg-shape-outline",
    name="Shape Outline",
    version="2.0",
    category="svg",
    icon="vector",
    action="Outline",
    summary="Draw one closed outline around a drawing: a silhouette to cut with, frame or plot.",
    tagline="Silhouettes for the Shape Cutter, frames and borders around drawings.",
    description=(
        "Envelope follows the strokes, bridging gaps narrower than the gap and filling what they enclose; concave "
        "and convex hulls wrap points sampled along every stroke. Every element counts (curves, lines, rects, "
        "circles), with transforms, in mm. The outline is written on the same sheet as the drawing, alone or as a "
        "new layer, so it lines up with it, and with the Shape Cutter's “Another SVG” shape."),
    inputs=Inputs("svg", exclude=(r"_outline\.svg$",)),
    output=Output("{stem}_outline.svg", "svg"),
    signature="<desc>svg-shape-outline</desc>",
    engine=".engine:run",
    runs_in="thread",
    preview="svg",
    live=True,
    columns=("parts",),
    params=[
        Section(
            "Outline",
            Choice("method", "Method", "envelope",
                   [("envelope", "Envelope"), ("concave", "Concave"), ("convex", "Convex")], segmented=True,
                   help="Envelope: hugs the strokes, gaps under the gap are bridged. Concave: a hull that can bend "
                        "inwards. Convex: a rubber band around everything."),
            Choice("layer", "From", "", options_from=_layers,
                   help="Outline every layer, or only the strokes of one layer."),
            Float("gap", "Bridge gaps under", 3.0, unit="mm", min=0.05, max=100.0, step=0.5, decimals=2,
                  slider=True, visible_if={"method": "envelope"},
                  help="Strokes closer than this merge into one shape; wider gaps stay open (or split the "
                       "outline into parts)."),
            Float("concavity", "Concavity", 0.6, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if={"method": "concave"},
                  help="0 gives the convex hull, 1 follows the points as tightly as possible. Independent of the "
                       "drawing's size (v1's alpha was not)."),
            Float("step", "Sample every", 1.0, unit="mm", min=0.05, max=20.0, step=0.25, decimals=2,
                  visible_if={"method": ("concave", "convex")},
                  help="Points are taken along every stroke this often; smaller follows long straight lines and "
                       "curves more closely."),
        ),
        Section(
            "Shape",
            Choice("islands", "Parts", "all", [("all", "All parts"), ("largest", "Largest only")], segmented=True,
                   help="Keep every separate part of the drawing, or only the biggest (v1 always kept only the "
                        "biggest)."),
            Bool("holes", "Keep holes", False,
                 help="Keep enclosed empty areas as holes in the outline instead of filling them."),
            Float("min_area", "Drop parts under", 4.0, unit="mm²", min=0.0, max=10000.0, step=1, decimals=1,
                  help="Parts and holes smaller than this are dropped."),
            Float("offset", "Margin", 0.0, unit="mm", min=-100.0, max=100.0, step=0.5, decimals=2,
                  help="Grow the outline outwards (positive) or pull it inwards (negative), with rounded corners."),
            Float("smooth", "Simplify", 0.05, unit="mm", min=0.0, max=5.0, step=0.05, decimals=2,
                  help="Points closer than this to a straight line are dropped from the outline."),
        ),
        Section(
            "Output",
            Choice("write", "Write", "only", [("only", "Outline only"), ("layer", "Drawing + outline")],
                   segmented=True,
                   help="Outline only: a new SVG on the same sheet, holding just the outline (use it as the Shape "
                        "Cutter's “Another SVG”). Drawing + outline: the drawing with the outline as a new layer."),
            Float("width", "Pen", 0.35, unit="mm", min=0.05, max=5.0, step=0.05, decimals=2,
                  help="Stroke width of the outline."),
            Float("flatten", "Curve flattening", 0.05, unit="mm", min=0.001, max=1.0, step=0.01, decimals=3,
                  advanced=True, help="Chord tolerance used to turn curves into lines."),
            Int("precision", "Decimals", 3, min=1, max=8, advanced=True, help="Decimals written in path data."),
        ),
    ],
    sample=".engine:make_sample",
    legacy=("Python/SVG_Shape_outline_v1.py",),
)
