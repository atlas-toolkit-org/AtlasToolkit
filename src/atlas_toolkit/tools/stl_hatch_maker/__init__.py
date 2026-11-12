"""
STL Hatch Maker: draw a 3D model for the plotter, hatching that shades its form plus outlines and creases, hidden
lines removed (v6 of "STL 3D Hatch Maker", bringing together v2.1, v3, C-v4 and v5).

Outputs <name>_hatch.svg, signed <desc>stl-hatch-maker</desc> so Atlas links them to this tool.
"""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, paper_params

STYLE_CHOICES = [("streamlines", "Surface strokes"), ("screen", "Straight lines"), ("contours", "Depth contours"),
                 ("stipple", "Stipple"), ("directional", "Normal strokes"), ("regions", "Tone regions"),
                 ("isophotes", "Isophotes"), ("none", "No hatching")]
LIGHT_CHOICES = [("front", "Front (headlight)"), ("top-left", "Top left"), ("top-right", "Top right"),
                 ("top", "Top"), ("left", "Left"), ("right", "Right")]


def style_is(*names):
    return {"style": names}


TOOL = Tool(
    id="stl-hatch-maker",
    name="Hatch Maker",
    version="6.0.2",
    category="mesh2svg",
    icon="hatch",
    action="Draw",
    summary="Draw a 3D model for the plotter: hatching that shades its form, its outline and creases.",
    tagline="Engraving-style line drawings of sculptures, from any angle, hidden lines removed.",
    description=(
        "Pick the view by orbiting the model in the Input tab and pressing “Use the 3D view”. Seven hatching "
        "styles shade the form by the light's direction: strokes that follow the surface, straight or crossed "
        "lines, depth contours, stipple, short strokes along the normals, tone regions and isophotes. Outlines "
        "and creases are added on their own pens. Everything is cut where the model hides it, fitted to the "
        "paper in millimetres, joined into long strokes and ordered so the pen travels little."),
    inputs=Inputs("mesh"),
    output=Output("{stem}_hatch.svg", "svg"),
    signature="<desc>stl-hatch-maker</desc>",
    engine=".engine:run",
    runs_in="process",
    preview="svg",
    columns=("strokes",),
    panel=".panel:ViewPanel",
    panel_after="View",
    preset_skip=("azimuth", "elevation", "roll"),
    params=[
        Section(
            "View",
            Float("azimuth", "Azimuth", -90.0, unit="°", min=-180, max=180, step=5, decimals=1,
                  help="Camera direction around the vertical (Z) axis, as in the 3D viewer. -90: from the front "
                       "(the -Y side)."),
            Float("elevation", "Elevation", 10.0, unit="°", min=-90, max=90, step=5, decimals=1,
                  help="Camera height above the horizon. 90 looks straight down."),
            Float("roll", "Roll", 0.0, unit="°", min=-180, max=180, step=5, decimals=1,
                  help="Turns the drawing on the paper."),
            Choice("light", "Light", "front", LIGHT_CHOICES,
                   help="Where the light comes from, seen from the camera. It sets the tone: darker where the "
                        "surface turns away from it."),
        ),
        Section("Paper", *paper_params("A3", "portrait", 20.0)),
        Section(
            "Hatching",
            Choice("style", "Style", "streamlines", STYLE_CHOICES,
                   help="Surface strokes (v5) · straight lines (v3) · depth contours (v3) · stipple (v3) · normal "
                        "strokes (v3) · tone regions and isophotes (v2.1)."),
            Float("spacing_dark", "Spacing, dark", 0.8, unit="mm", min=0.15, max=20, step=0.1, decimals=2,
                  visible_if=style_is("streamlines"), help="Distance between strokes where the tone is darkest."),
            Float("spacing_light", "Spacing, light", 3.0, unit="mm", min=0.2, max=40, step=0.25, decimals=2,
                  visible_if=style_is("streamlines"), help="Distance between strokes where the tone is lightest."),
            Float("spacing", "Spacing", 1.2, unit="mm", min=0.15, max=20, step=0.1, decimals=2,
                  visible_if=style_is("screen", "regions", "directional"),
                  help="Distance between lines (or between strokes, for normal strokes)."),
            Float("angle", "Angle", 45.0, unit="°", min=-180, max=180, step=5, decimals=1,
                  visible_if=style_is("screen", "regions"), help="Direction of the lines on the paper."),
            Float("stroke_len", "Stroke length", 12.0, unit="mm", min=0.5, max=200, step=1, decimals=1,
                  visible_if=style_is("streamlines", "directional"),
                  help="Longest stroke (surface strokes), or the length of the darkest normal strokes."),
            Float("turn", "Turn limit", 30.0, unit="°", min=1, max=90, step=5, decimals=0,
                  visible_if=style_is("streamlines"), help="A stroke ends where the surface makes it turn more "
                                                           "than this in one step."),
            Float("follow", "Curvature ↔ tone", 0.65, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=style_is("streamlines"),
                  help="What the strokes follow: 1 = the surface's main curvature, 0 = lines of equal tone "
                       "(isophotes). C-v4 used 0.65."),
            Int("smooth", "Smoothing", 5, min=0, max=50, visible_if=style_is("streamlines"), advanced=True,
                help="Rounds of smoothing of the stroke direction field."),
            Int("levels", "Contours", 30, min=2, max=500, visible_if=style_is("contours"),
                help="Number of depth planes across the model."),
            Float("dots", "Dots", 40.0, unit="/cm²", min=1, max=1000, step=5, decimals=0,
                  visible_if=style_is("stipple"), help="Dot density where the tone is darkest."),
            Float("dot_size", "Dot size", 0.35, unit="mm", min=0.05, max=5, step=0.05, decimals=2,
                  visible_if=style_is("stipple")),
            Choice("dot_shape", "Dots as", "circle", [("circle", "Small circles"), ("dash", "Short dashes")],
                   segmented=True, visible_if=style_is("stipple")),
            Float("gamma", "Tone contrast", 0.8, min=0.2, max=4.0, step=0.1, decimals=2,
                  visible_if=style_is("streamlines", "stipple", "directional"),
                  help="Tone curve: below 1 lightens the mid-tones (fewer strokes), above 1 darkens them."),
            Float("t_light", "Light threshold", 0.7, min=0.0, max=1.0, step=0.05, decimals=2,
                  visible_if=style_is("regions", "isophotes"),
                  help="Tones below this are hatched (regions) or get isophote lines."),
            Float("t_dark", "Dark threshold", 0.3, min=0.0, max=1.0, step=0.05, decimals=2,
                  visible_if=style_is("regions", "isophotes"),
                  help="Tones below this get the second, crossing direction (regions); isophotes start here."),
            Float("tone_step", "Tone step", 0.05, min=0.01, max=0.5, step=0.01, decimals=2,
                  visible_if=style_is("isophotes"), help="Tone difference between two isophote lines."),
            Float("highlight", "Highlights from", 0.95, min=0.0, max=1.0, step=0.05, decimals=2,
                  visible_if=style_is("streamlines", "screen"), help="Tones above this stay white."),
            Bool("cross", "Cross-hatch the darks", True, visible_if=style_is("streamlines", "screen"),
                 help="Add a second direction where the tone is below the cross band."),
            Float("cross_band", "Cross below", 0.55, min=0.0, max=1.0, step=0.05, decimals=2,
                  visible_if=lambda v: v.get("style") in ("streamlines", "screen") and v.get("cross"),
                  help="Tone under which the second direction is drawn (v5 used 0.55)."),
            Seed("seed", "Seed", 7, visible_if=style_is("streamlines", "stipple", "directional"),
                 help="Same seed, same drawing."),
        ),
        Section(
            "Lines",
            Bool("silhouettes", "Outlines", True, help="Where the surface turns away from the camera, plus open "
                                                      "edges."),
            Choice("silhouette_method", "Outline", "contour", [("contour", "True outline"), ("grazing", "Grazing band")],
                   segmented=True, visible_if={"silhouettes": True},
                   help="True outline: edges between faces turned towards and away from the camera (v2.1). Grazing "
                        "band: every edge of faces seen almost edge-on (v5), bolder and sketchier."),
            Float("grazing", "Band", 0.1, min=0.01, max=0.9, step=0.01, decimals=2,
                  visible_if={"silhouette_method": "grazing", "silhouettes": True},
                  help="|n·v| below this counts as edge-on (v5's silhouette threshold)."),
            Bool("creases", "Creases", False, help="Sharp edges of the model."),
            Float("crease_angle", "Crease angle", 30.0, unit="°", min=1, max=180, step=5, decimals=0,
                  visible_if={"creases": True}, help="Edges folding more than this are drawn."),
        ),
        Section(
            "Pens",
            Float("hatch_width", "Hatching pen", 0.3, unit="mm", min=0.05, max=3, step=0.05, decimals=2),
            Float("outline_width", "Outline pen", 0.5, unit="mm", min=0.05, max=3, step=0.05, decimals=2),
            Float("crease_width", "Crease pen", 0.35, unit="mm", min=0.05, max=3, step=0.05, decimals=2),
            Bool("one_layer", "Everything on one pen", False,
                 help="One layer instead of one per feature (hatching, outlines, creases)."),
            Bool("optimise", "Order strokes to cut travel", True,
                 help="Draw each stroke from the end nearest the last one: much less pen-up travel."),
        ),
        Section(
            "Quality",
            Float("vis_step", "Visibility step", 0.3, unit="mm", min=0.05, max=3, step=0.05, decimals=2,
                  advanced=True, help="Lines are tested for hiding every this many mm on the paper."),
            Float("simplify", "Simplify", 0.02, unit="mm", min=0.0, max=1, step=0.01, decimals=3, advanced=True,
                  help="Points closer than this to a straight stroke are dropped."),
            Int("draft_faces", "Preview faces", 60000, min=2000, max=2_000_000, advanced=True,
                help="Preview decimates bigger models to this many faces; Draw always uses the full model."),
            collapsed=True,
        ),
    ],
    test_values={"draft_faces": 20000, "spacing_dark": 2.0, "spacing_light": 6.0},
    legacy=("Python/STL_3d_hatch_maker-v5.py", "STL 3D Hatch Maker v5.exe", "Python/STL_3d_hatch_maker-v3.py",
            "STL 3D Hatch Maker v3.exe", "Python/STL_3d_hatch_maker-v2.1.py"),
)
