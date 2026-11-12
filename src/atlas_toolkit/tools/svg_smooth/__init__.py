"""Geometry fairing for pen drawings. Atlas output: <desc>svg-smooth</desc>."""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Output, Section, Tool

TOOL = Tool(
    id="svg-smooth", name="Line Smooth", version="0.1.0", category="svg", icon="line-smooth",
    action="Smooth", summary="Remove jagged and sawtooth linework with controlled curve smoothing.",
    tagline="Flowing strokes, with control over the drawing's character.",
    description=("Arc-length Whittaker–Henderson curvature smoothing or spectral Taubin filtering moves linework "
                 "instead of removing vertices. Tune the scale of jaggedness, blend and physical displacement. "
                 "Persistent corners, endpoints, contacts, loop area and broad gesture have independent limits. "
                 "All-pen swept-region checks protect surrounding motifs. Output is a vector SVG of dense line "
                 "segments, retaining layers, styles, transforms and stroke order. Preview and Run agree."),
    inputs=Inputs("svg", exclude=(r"_smoothed\.svg$",)),
    output=Output("{stem}_smoothed.svg", "svg"), signature="<desc>svg-smooth</desc>",
    engine=".engine:run", runs_in="process", live=True, preview="svg", columns=("smoothed", "displacement"),
    params=[
        Section("Smoothing",
            Choice("method", "Algorithm", "whittaker", [("whittaker", "Whittaker curvature"), ("taubin", "Taubin compensated")],
                   help="Whittaker: curvature-penalized least squares, fast banded solve. Taubin: paired "
                   "contraction/expansion evaluated spectrally, with less broad-form shrinkage. Neither simplifies."),
            Float("span", "Smoothing span", 1., unit="mm", min=.02, max=50, step=.1, decimals=3, slider=True,
                  help="Wavelength reduced to about half amplitude before blending/guards. Shorter jaggedness "
                  "is suppressed more strongly; broad gestures survive. Increase for coarser zigzags."),
            Float("strength", "Strength", 75., unit="%", min=0, max=100, step=5, decimals=1, slider=True,
                  help="Blend from the exact original to the filtered curve. Zero is an exact geometry no-op."),
            Float("deviation", "Maximum displacement", .08, unit="mm", min=0, max=10, step=.01, decimals=3,
                  help="Continuous matched-point displacement cap on the sheet. A whole-stroke blend enforces "
                  "the cap without pointwise clipping kinks. Start below the pen width. Zero keeps geometry."),
            Choice("curves", "Curved paths", "keep", [("keep", "Keep original curves"), ("smooth", "Also smooth curves")],
                   help="Default targets polylines and straight-segment paths. Optional smoothing samples "
                   "Béziers/arcs within Curve sampling error; changed curves export as dense vector polylines."),
        ),
        Section("Preserve the drawing",
            Bool("topology", "Protect topology", True,
                 help="Anchor segments at crossings/junctions, retain self-crossing strokes and reject swept "
                 "regions that touch other ink, including earlier smoothed strokes and enclosed motifs. "
                 "Backtrack strength rather than reconnect paths. Curved obstacles use sampled geometry."),
            Float("corner_angle", "Keep persistent turns above", 65., unit="°", min=0, max=180, step=5, decimals=1,
                  help="Lock significant direction-change peaks measured over Corner support, ignoring fine "
                  "sawteeth. 0 locks all source vertices; 180 disables corner locking."),
            Float("corner_support", "Corner support", 1., unit="mm", min=.02, max=50, step=.1, decimals=3,
                  help="Distance on each side used to distinguish intended corners from noise. Set larger "
                  "than a tooth. Small values may protect the jaggedness you want to remove."),
            Float("area_change", "Maximum loop area change", 3., unit="%", min=0, max=100, step=.5, decimals=2,
                  help="Backtrack if a closed stroke changes area more than this; winding is always retained."),
            Float("length_change", "Maximum stroke length change", 25., unit="%", min=0, max=100, step=1, decimals=1,
                  help="Limit change in total stroke length. Removing zigzags necessarily shortens ink; very "
                  "small limits can leave them visible. Use displacement/span to preserve broad gesture."),
        ),
        Section("Advanced",
            Float("sample_step", "Sample spacing", .08, unit="mm", min=.005, max=1., step=.01, decimals=3,
                  advanced=True, help="Maximum arc-length filter grid spacing (also capped at span/8). Original "
                  "vertices remain in the matched validation partition. Smaller gives finer vector output."),
            Float("curve_error", "Curve sampling error", .005, unit="mm", min=.0001, max=.1, step=.001, decimals=4,
                  advanced=True, help="Bounded approximation of curved obstacles and optionally curved inputs. "
                  "Reserved from Maximum displacement when smoothing curves. Lower for closely spaced details."),
            Bool("filled", "Smooth filled outlines", False, advanced=True,
                 help="Allow filled paths/polygons to change. Default leaves filled artwork and basic shapes "
                 "original as obstacles. Markers, dashes, clipping and path effects always stay original."),
        ),
    ],
    sample=".engine:make_sample",
)
