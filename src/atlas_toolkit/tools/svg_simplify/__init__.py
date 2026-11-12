"""Controlled line-art reduction. Outputs <desc>svg-simplify</desc> for Atlas."""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Output, Section, Tool

TOOL = Tool(
    id="svg-simplify", name="Line Simplify", version="0.1.0", category="svg", icon="line-simplify",
    action="Simplify", summary="Reduce line-art vertices with limits on displacement, gesture and topology.",
    tagline="Fewer points, with control over the character of the drawing.",
    description=("Native Douglas–Peucker or heap-based Visvalingam–Whyatt, with physical error limits. "
                 "Protect corners, reversals, length and loop area. The topology guard checks swept regions "
                 "against all pens, including small enclosed motifs. Layers, styles, stroke order, endpoints "
                 "and closed paths stay in place. Curves remain original by default; optional bounded "
                 "flattening converts curved paths to polylines. Preview and Run use the same geometry."),
    inputs=Inputs("svg", exclude=(r"_simplified\.svg$",)),
    output=Output("{stem}_simplified.svg", "svg"), signature="<desc>svg-simplify</desc>",
    engine=".engine:run", runs_in="process", live=True, preview="svg", columns=("reduction",),
    params=[
        Section("Simplification",
            Choice("method", "Algorithm", "rdp", [("rdp", "Douglas–Peucker"), ("vw", "Visvalingam–Whyatt")],
                   help="Douglas–Peucker: fast native distance-based reduction. Visvalingam–Whyatt: gradual "
                        "effective-area reduction, additionally constrained by the same distance limit."),
            Float("tolerance", "Maximum deviation", 0.05, unit="mm", min=0, max=10, step=0.01, decimals=3,
                  slider=True, help="Physical displacement budget on the sheet. Zero leaves all geometry original. "
                                    "Start below your pen width. Guards may retain more points than this allows."),
            Float("area_factor", "Area strength", 1.0, min=0.05, max=100, step=0.25, decimals=2,
                  visible_if={"method": "vw"}, help="Effective triangle area threshold = this × deviation². "
                  "Higher removes more candidates; the distance and preservation limits still apply."),
            Choice("curves", "Curved paths", "keep", [("keep", "Keep original curves"), ("flatten", "Convert to lines")],
                   help="Keep retains Béziers and arcs exactly. Convert uses adaptive bounded flattening, then "
                        "simplifies. Rectangles, circles and ellipses always stay original."),
        ),
        Section("Preserve the drawing",
            Bool("topology", "Protect topology", True,
                 help="Reject shortcuts whose swept region meets another stroke or motif, across all pens. "
                      "Existing crossings and junction spans stay original. Conservative, so dense drawings "
                      "may reduce less. Curves are checked at the flattening resolution."),
            Float("corner_angle", "Keep turns above", 35, unit="°", min=0, max=180, step=5, decimals=1,
                  help="Lock vertices where the direction changes by this angle. 0 locks every vertex; "
                       "180 disables corner locking. Smaller protects more accents and cusps."),
            Bool("reversals", "Preserve stroke reversals", True,
                 help="Reject shortcuts that erase backward motion along their chord, preserving hooks and retracing."),
            Float("length_loss", "Maximum local length loss", 2, unit="%", min=0, max=100, step=0.5,
                  decimals=2, help="Each replaced span may lose at most this much of its original length. "
                                  "Controls waviness and gesture independently of distance."),
            Float("area_loss", "Maximum loop area change", 3, unit="%", min=0, max=100, step=0.5,
                  decimals=2, help="Closed strokes retain winding and at least three distinct vertices. "
                                  "Revert a loop if its signed area changes by more than this percentage."),
        ),
        Section("Advanced",
            Float("flatten_share", "Curve error budget", 20, unit="%", min=1, max=50, step=1, decimals=0,
                  advanced=True, help="Share of deviation reserved for curve approximation. The remainder is "
                  "the simplification budget. Also controls curved obstacles when curves are kept. "
                  "Smaller improves curve fidelity at the cost of more samples."),
        ),
    ],
    sample=".engine:make_sample",
)
