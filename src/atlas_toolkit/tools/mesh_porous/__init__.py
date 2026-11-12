"""
Porous Forms: turn a closed model into a continuous labyrinth inside its own silhouette: gyroid, diamond, Schwarz P
or Neovius walls (or struts), or seeded Voronoi cells, cut by the model's envelope, with solid parts kept where the
influence says so, in one stage or a sequence from solid to porous.

Outputs <name>_porous.stl (and <name>_porous_stages/ with stages.json), signed atlas-toolkit:mesh-porous.
"""
from atlas_toolkit.core import (Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, Vec3, frame_params,
                                influence_params)


def staged(v):
    return int(v.get("stages", 1)) > 1


def voronoi(v):
    return v.get("pattern") == "voronoi"


def dropping(v):
    return v.get("keep") == "drop"


TOOL = Tool(
    id="mesh-porous",
    name="Porous Forms",
    version="1.0",
    category="mesh",
    icon="porous",
    action="Hollow",
    summary="Turn a model into a continuous labyrinth of walls or struts inside its own silhouette.",
    tagline="A bust becomes a gyroid, a figure a cellular foam: connected walls and open negative space, as a "
            "closed mesh ready for Sketch Hatching, Plotter Vision or Contour Lines.",
    description=(
        "The model is sampled as a signed-distance volume and filled with a periodic pattern: the walls of a "
        "triply periodic minimal surface (gyroid, diamond, Schwarz P, Neovius) or of seeded Voronoi cells, or their "
        "struts. Wall thickness and cell size are % of the model's height, so they mean the same on any model. "
        "Porosity opens the holes: 0 is the solid model, 1 the full pattern; a sequence of stages grows from solid "
        "to porous with every stage in place. Acts on keeps a band or a sphere solid (an intact face, a porous "
        "skull), with a feathered transition where the walls thicken until they close. Porous depth keeps a solid "
        "core behind the openings, which reads better on paper. The model must be a closed solid; the result is a "
        "closed surface (marching cubes) whose detail is set by Resolution: walls need at least three samples."),
    inputs=Inputs("mesh", exclude=(r"(?i)_porous(?:_stage_\d+)?\.(stl|obj|ply)$",)),
    output=Output("{stem}_porous.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-porous",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("stages", "faces", "pieces"),
    params=[
        Section(
            "Pattern",
            Choice("pattern", "Pattern", "gyroid", [("gyroid", "Gyroid"), ("diamond", "Diamond"),
                                                    ("primitive", "Schwarz P"), ("neovius", "Neovius"),
                                                    ("voronoi", "Voronoi cells")],
                   help="Gyroid: two interwoven labyrinths, no straight lines (the default). Diamond: a "
                        "crystalline lattice of tetrahedral channels. Schwarz P: round openings on a cubic grid. "
                        "Neovius: a finer cubic cell with small round windows. Voronoi cells: an irregular foam, "
                        "seeded."),
            Choice("style", "Solid", "sheet", [("sheet", "Walls"), ("network", "Struts")], segmented=True,
                   help="Walls: thin sheets with open channels on both sides (a labyrinth). Struts: rods along the "
                        "centre lines of one side's channels, a space frame (bone-like with Voronoi cells)."),
            Float("cell", "Cell size", 12.0, unit="% h", min=3.0, max=60.0, step=1.0, decimals=1, slider=True,
                  help="One period of the pattern, % of the model's height. 10-18% keeps the openings readable on "
                       "paper; under ~6% a bust turns into texture."),
            Float("wall", "Wall thickness", 2.0, unit="% h", min=0.3, max=15.0, step=0.1, decimals=2,
                  help="Wall thickness (struts: their approximate diameter), % of the height. Needs three samples "
                       "of the Resolution grid: about 1.2% h at 256 for a model taller than wide."),
            Float("amount", "Porosity", 1.0, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="0 is the solid model, 1 the full pattern; between, the walls are thicker and the holes "
                       "smaller, opening first where they are farthest from the walls."),
            Float("depth", "Porous depth", 0.0, unit="% h", min=0.0, max=50.0, step=1.0, decimals=1,
                  help="Keep the model solid deeper than this below its surface, so the openings show a solid "
                       "core instead of more labyrinth (cleaner on paper). 0 = porous all the way through."),
            Float("windows", "Windows", 0.0, unit="%", min=0.0, max=90.0, step=5.0, decimals=0,
                  visible_if=lambda v: voronoi(v) and v.get("style", "sheet") == "sheet",
                  help="Opens every Voronoi wall, leaving a rim along its edges (a radiolarian, an open-cell "
                       "foam): 0 closed walls, 60-80 wide windows. Closed cells read as flat facets on paper."),
            Seed("seed", "Seed", 1, visible_if=voronoi, help="Where the Voronoi cells fall."),
        ),
        Section(
            "Pattern frame",
            Vec3("pattern_rotate", "Turn pattern", (0.0, 0.0, 0.0), unit="°", min=-180.0, max=180.0, step=5.0,
                 decimals=1, help="Turns the pattern about X, then Y, then Z (the model stays put): changes which "
                                  "channels run along the face."),
            Vec3("pattern_offset", "Shift pattern", (0.0, 0.0, 0.0), unit="% cell", min=-100.0, max=100.0,
                 step=5.0, decimals=1,
                 help="Slides the pattern by a share of a cell, e.g. to centre an opening on an eye or keep a "
                      "wall across a thin neck."),
            Float("grade", "Cell grading", 1.0, min=0.25, max=4.0, step=0.05, decimals=2,
                  help="Cell size at the top over the cell size at the base: above 1 the cells open up toward the "
                       "top, below 1 they grow toward the base. 1 = uniform."),
            collapsed=True,
        ),
        Section("Acts on", *influence_params(), collapsed=True,
                help="Where the pattern opens; elsewhere the model stays solid, and the walls thicken across the "
                     "feather until they close."),
        Section(
            "Stages",
            Int("stages", "Stages", 1, min=1, max=8, slider=True,
                help="1: one porous model. More: a sequence from the solid model (stage 0, untouched) to the full "
                     "porosity, every stage in place."),
            Bool("save_stages", "Also save every stage", True, visible_if=staged,
                 help="Stage files go in <name>_porous_stages/ with stages.json (its frame), so Contour Lines draws "
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
                   help="The pattern can cut thin parts (fingers, ears, a nose tip) into loose pieces. Keep all "
                        "(the default) leaves them where they are, floating; drop small removes pieces under a share "
                        "of the volume; largest only keeps the one connected body (for a print)."),
            Float("min_piece", "Smaller than", 0.5, unit="% vol", min=0.0, max=20.0, step=0.1, decimals=2,
                  visible_if=dropping, help="Pieces below this share of the total volume are removed."),
            Int("smooth", "Smoothing", 3, min=0, max=30,
                help="Taubin passes over the extracted surface: removes the grid's faint facets without "
                     "shrinking it. 0 keeps the raw marching-cubes surface, more rounds the cut edges."),
            Int("max_faces", "Output faces", 600_000, min=0, max=20_000_000, step=50_000,
                help="Simplify the result to at most this many faces (quadric decimation, the shape kept within a "
                     "fraction of a sample). 0 keeps every face of the grid (millions at high resolution; slow to "
                     "draw)."),
        ),
        Section("Model", *frame_params(stage_sets=False)),
        Section(
            "Quality",
            Int("resolution", "Resolution", 256, min=48, max=512, step=16,
                help="Samples along the model's longest side. 256 resolves walls down to ~1.2% h; 384 or 512 for "
                     "thin walls or small cells (memory and time grow with the cube)."),
            Int("draft_resolution", "Preview resolution", 128, min=32, max=512, step=16, advanced=True,
                help="Preview samples along the longest side (Run always uses Resolution). Walls thinner than "
                     "three preview samples are reported, and may break up in the preview only."),
            Int("draft_faces", "Preview input faces", 60_000, min=2_000, max=2_000_000, advanced=True,
                help="Preview simplifies bigger input models to this many faces before sampling; Run always "
                     "samples the full model."),
            collapsed=True),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"resolution": 64, "draft_resolution": 48, "cell": 30.0, "wall": 6.0, "stages": 2,
                 "max_faces": 0},
)
