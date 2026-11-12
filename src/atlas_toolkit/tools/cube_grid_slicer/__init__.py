"""
Grid Slicer: cut a model into an n × n × n grid of closed blocks and push them apart (from cube_grid_slicer-v2).

Outputs <name>_grid<n>.stl, signed atlas-toolkit:cube-grid-slicer so Atlas links them to this tool.
"""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool

TOOL = Tool(
    id="cube-grid-slicer",
    name="Grid Slicer",
    version="3.1.1",
    category="mesh",
    icon="slice",
    action="Slice",
    summary="Cut a model into an n × n × n grid of closed blocks and push them apart.",
    tagline="Exploded cubes of a sculpture, every cut capped.",
    description=(
        "The bounding box is divided into n cells per axis. Every cut is capped, so each block is a closed solid "
        "(the model needs to be watertight for clean caps). Blocks move away from the grid centre by their cell "
        "index times the gap. Shuffle then lets a share of the blocks trade places (anywhere, within their layer or within "
        "their column) and shifts every block at random by up to a share of a cell; the seed repeats a result."),
    inputs=Inputs("mesh", exclude=(r"_grid\d+\.(stl|obj|ply)$",)),
    output=Output("{stem}_grid{n}.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:cube-grid-slicer",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("blocks",),
    params=[
        Section(
            "Grid",
            Int("n", "Blocks per side", 3, min=1, max=50, slider=True, help="Cells along each axis: n³ blocks."),
            Choice("gap_mode", "Gap in", "units", [("units", "Model units"), ("percent", "% of a cell")],
                   segmented=True, help="An absolute gap, or a share of the smallest cell edge."),
            Float("gap", "Gap", 1.0, min=0.0, max=1e6, step=0.5, decimals=3,
                  help="Clearance between neighbouring blocks (model units, or % of the smallest cell edge)."),
        ),
        Section(
            "Shuffle",
            Float("shuffle", "Blocks that trade places", 0.0, unit="%", min=0.0, max=100.0, step=5.0, decimals=0,
                  slider=True, help="Share of the blocks that swap cells with each other; every chosen block moves."),
            Choice("within", "Trade places", "all", [("all", "Anywhere"), ("layer", "In its layer"),
                                                     ("column", "In its column")], segmented=True,
                   visible_if=lambda v: v.get("shuffle", 0) > 0,
                   help="In its layer keeps every block at its height (the stack still stands); in its column keeps "
                        "it above the same spot."),
            Float("shift", "Random shift", 0.0, unit="% cell", min=0.0, max=200.0, step=5.0, decimals=0,
                  slider=True, help="Each block moves by up to this share of a cell edge, in a random direction."),
            Choice("shift_axes", "Shift along", "xyz", [("xyz", "X Y Z"), ("xy", "X Y"), ("z", "Z")],
                   segmented=True, visible_if=lambda v: v.get("shift", 0) > 0,
                   help="X Y keeps every block at its height."),
            Seed("seed", "Seed", 0, help="Same seed, same shuffle.",
                 visible_if=lambda v: v.get("shuffle", 0) > 0 or v.get("shift", 0) > 0),
        ),
        Section(
            "Extra files",
            Bool("separate", "Also save every block", False,
                 help="Write each block to its own file in a <name>_grid<n>_blocks folder, besides the combined "
                      "model."),
        ),
    ],
    test_values={"n": 2, "shuffle": 50.0, "shift": 10.0},
    legacy=("Python/cube_grid_slicer-v2.py", "cube_grid_slicer-v2.exe"),
)
