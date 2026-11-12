"""
SVG Overlap Cleaner: cuts ink drawn twice so every line is plotted once, splitting paths where needed.

Outputs <name>--nooverlap.svg, signed <desc>svg-overlap-cleaner</desc> so Atlas links them to this tool.
The engine (svg_overlap_cleaner.py) is the standalone, standard-library cleaner; it still runs on its own:
    python svg_overlap_cleaner.py drawing.svg
"""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Tool

TOOL = Tool(
    id="svg-overlap-cleaner",
    name="Overlap Cleaner",
    version="1.2.1",
    category="svg",
    icon="layers",
    action="Clean",
    summary="Cut ink drawn twice so every line is plotted once; drop duplicate and collinear vertices.",
    tagline="Cut ink drawn twice, split paths, keep the drawing.",
    description=(
        "Paths are read in plotting order. A stretch of a path that runs over ink already drawn, nearly parallel "
        "and within the tolerance for at least the minimum overlap, is cut and the path is split there. Crossings "
        "and touching ends are never cut. Overlaps are only looked for within one pen (top-level layer and stroke "
        "colour) unless 'Compare across layers and colours' is on. Element order, ids, styles, groups and "
        "transforms are kept; curves are flattened only in paths that are cut."),
    inputs=Inputs("svg", exclude=(r"--nooverlap\.svg$",)),
    output=Output("{stem}--nooverlap.svg", "svg"),
    signature="<desc>svg-overlap-cleaner</desc>",
    engine=".engine:run",
    runs_in="thread",
    preview="svg",
    live=True,
    columns=("ink_cut",),
    params=[
        Section(
            "Mode",
            Choice("mode", "Mode", "retraced", [("retraced", "Cut retraced ink"), ("duplicates", "Whole duplicates")],
                   segmented=True,
                   help="Cut retraced ink: cut every stretch drawn twice, splitting paths. Whole duplicates: only "
                        "remove paths drawn entirely before (exact, reversed or within tolerance); split nothing."),
        ),
        Section(
            "Overlap",
            Float("tol", "Tolerance", 0.05, unit="mm", min=0.001, max=1.0, step=0.01, decimals=3,
                  help="Strokes closer than this are treated as the same ink."),
            Float("angle", "Max angle", 5.0, unit="°", min=0.5, max=30.0, step=0.5, decimals=1,
                  help="Only strokes this close to parallel can overlap, so crossings are never cut."),
            Float("min_overlap", "Min overlap", 2.0, unit="mm", min=0.0, max=50.0, step=0.5, decimals=2,
                  help="Retraced stretches shorter than this are left alone: two pen lifts cost more than a short "
                       "double pass."),
            Float("min_piece", "Min piece", 0.3, unit="mm", min=0.0, max=5.0, step=0.05, decimals=2,
                  help="Leftover bits shorter than this, between two cuts, are dropped. Free line ends are kept "
                       "unless shorter than 2 × tolerance."),
            Float("simplify", "Simplify", 0.01, unit="mm", min=0.0, max=1.0, step=0.005, decimals=3,
                  visible_if={"mode": "retraced"},
                  help="Collinear vertices closer than this to a straight line are removed. 0 removes only exact "
                       "duplicates."),
            Bool("across_pens", "Compare across layers and colours", False,
                 help="By default only paths of the same pen (top-level group and stroke colour) are compared."),
            Bool("pen_by_width", "Stroke width is part of the pen", False,
                 help="Lines of one colour but different widths count as different pens, so a bold line drawn twice "
                      "on purpose is kept."),
            Bool("shapes", "Read rectangles, circles and ellipses", False,
                 help="Also compare rect, circle and ellipse elements. They stay shapes unless they are cut."),
        ),
        Section(
            "Join",
            Float("join", "Join ends within", 0.0, unit="mm", min=0.0, max=2.0, step=0.05, decimals=2,
                  help="Join stroke ends closer than this into one stroke, so the pen does not lift there. Only "
                       "within one pen and group. 0 = off. Gaps wider than this are never bridged."),
            Bool("join_reverse", "Reverse strokes to join them", True, visible_if=lambda v: v.get("join", 0) > 0,
                 help="A stroke may be drawn the other way round when that lets it continue another."),
            Choice("join_scope", "Join", "pen", [("pen", "Across paths"), ("element", "Within each path")],
                   segmented=True, visible_if=lambda v: v.get("join", 0) > 0,
                   help="Across paths: strokes of different paths of one pen and group are merged into the first "
                        "path. Within each path: only its own subpaths."),
        ),
        Section(
            "Output detail",
            Float("flatten", "Curve flattening", 0.01, unit="mm", min=0.001, max=1.0, step=0.005, decimals=3,
                  help="Chord tolerance used when a curve has to be cut.", advanced=True),
            Int("precision", "Decimals", 4, min=1, max=8, help="Decimals written in path data.", advanced=True),
            collapsed=True,
        ),
    ],
    legacy=("Python/svg_overlap_cleaner", "SVG Overlap Cleaner v1.exe", "Python/svg_join_paths_v2.py",
            "SVG Join Paths v2.exe", "Python/svg_join_paths_v1.py", "process_svg_joins_gui.exe",
            "Python/svg_dedup"),
)
