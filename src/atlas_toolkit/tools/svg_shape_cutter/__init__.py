"""
SVG Shape Cutter: keep the linework inside (or outside) a closed shape.

Outputs <name>_cutout.svg, signed <desc>svg-shape-cutter</desc> so Atlas links them to this tool.
"""
from atlas_toolkit.core import Bool, Choice, FileRef, Float, Inputs, Int, Output, Section, Tool


def _layers(info):
    names = list(getattr(info, "groups", []) or [])
    return [(n, n) for n in names] or [("", "no layers in this file")]


TOOL = Tool(
    id="svg-shape-cutter",
    name="Shape Cutter",
    version="2.0",
    category="svg",
    icon="scissors",
    action="Cut",
    summary="Clip a drawing to a shape: keep the lines inside (or outside) a closed outline.",
    tagline="Clip hatching to a silhouette, keep every island and hole.",
    description=(
        "The shape is the element or layer named “shape” (as in the first Shape Cutter), a layer you choose, or "
        "the closed outlines of another SVG placed over this one in millimetres. Everything else is clipped, or "
        "only the element or layer named “hatch” when there is one. Every island and hole of the shape counts. "
        "Paths entirely kept stay exactly as written; clipped strokes are merged end to end."),
    inputs=Inputs("svg", exclude=(r"_cutout\.svg$",)),
    output=Output("{stem}_cutout.svg", "svg"),
    signature="<desc>svg-shape-cutter</desc>",
    engine=".engine:run",
    runs_in="thread",
    preview="svg",
    live=True,
    columns=("clipped",),
    params=[
        Section(
            "Shape",
            Choice("shape_source", "Shape from", "named",
                   [("named", "Named “shape”"), ("layer", "A layer"), ("file", "Another SVG")],
                   help="Named “shape”: the element or layer whose id or label is “shape” (else the first path). "
                        "A layer: one of this file's layers. Another SVG: its closed outlines, in mm."),
            Choice("shape_layer", "Layer", "", options_from=_layers, visible_if={"shape_source": "layer"},
                   help="The layer whose closed outlines make the shape."),
            FileRef("shape_file", "Shape file", "", exts=(".svg",), visible_if={"shape_source": "file"},
                    help="An SVG whose closed outlines are laid over this drawing in millimetres."),
            Choice("keep", "Keep", "inside", [("inside", "Inside"), ("outside", "Outside")], segmented=True,
                   help="Keep the linework inside the shape, or cut a hole and keep what is outside."),
            Bool("keep_shape", "Keep the shape's outline", False,
                 help="Leave the shape itself in the output (for example to plot the silhouette too)."),
        ),
        Section(
            "Strokes",
            Bool("merge", "Merge clipped strokes", True,
                 help="Join the clipped segments end to end, so the pen lifts only where it must."),
            Float("min_length", "Drop bits under", 0.05, unit="mm", min=0.0, max=5.0, step=0.05, decimals=2,
                  help="Clipped pieces shorter than this (slivers at the edge of the shape) are dropped."),
            Float("flatten", "Curve flattening", 0.02, unit="mm", min=0.001, max=1.0, step=0.01, decimals=3,
                  advanced=True, help="Chord tolerance used to turn curves into lines for clipping."),
            Int("precision", "Decimals", 4, min=1, max=8, advanced=True, help="Decimals written in path data."),
        ),
    ],
    sample=".engine:make_sample",
    legacy=("Python/svg_shape_cutter", "svg_shape_cutter.exe"),
)
