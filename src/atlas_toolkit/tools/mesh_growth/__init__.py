"""
Reaction-Diffusion Growth: grow a Gray-Scott pattern over a model's surface and raise or engrave it as relief
(coral ridges, fingerprint channels, dividing cells, a honeycomb skin), in one stage or a sequence of stages taken
from the same simulation.

Outputs <name>_growth.stl (and <name>_growth_stages/ with stages.json), signed atlas-toolkit:mesh-growth.
"""
from atlas_toolkit.core import (Bool, Choice, Float, Inputs, Output, Section, Seed, Tool, Int, frame_params,
                                influence_params)

PATTERN_CHOICES = [("coral", "Coral"), ("fingerprint", "Fingerprint"), ("maze", "Maze"), ("cells", "Cells"),
                   ("honeycomb", "Honeycomb"), ("pebbles", "Pebbles"), ("lichen", "Lichen")]


def staged(v):
    return int(v.get("stages", 1)) > 1


def custom(v):
    return v.get("pattern") == "custom" or v.get("second") == "custom"


def graded(v):
    return v.get("second", "none") != "none"


def aligned(v):
    return v.get("align", "none") != "none"


TOOL = Tool(
    id="mesh-growth",
    name="Reaction-Diffusion Growth",
    version="1.0",
    category="mesh",
    icon="growth",
    action="Grow",
    summary="Grow a living pattern over a model: coral ridges, fingerprint channels, dividing cells or a "
            "honeycomb skin, raised or engraved as relief.",
    tagline="A classical face overgrown with coral or worn into fingerprint channels: draw it with Sketch "
            "Hatching or Contour Lines.",
    description=(
        "Two chemicals react and diffuse over the surface itself (Gray-Scott, after Turk's reaction-diffusion on "
        "arbitrary surfaces), so the pattern follows the model's form and never crosses a gap between separate "
        "pieces. It starts from seeded spots and spreads until it covers the surface; Pattern picks the regime "
        "(branching coral, parallel fingerprint channels, a maze, cells that divide, a honeycomb of pits, "
        "pebbles, lichen plateaus) and Wavelength its scale, in % of the model's height, so a setting carries "
        "over between models and mesh densities (the surface is refined or simplified to the wavelength first). "
        "Growth time is how long it runs: the stages of a sequence are moments of the same simulation, from the "
        "untouched model through the first colonies to the full skin. The pattern becomes relief along the rest "
        "normals: raised, engraved or both, Sharpness from soft swells to terraces; deep relief changes the "
        "silhouette. Direction lays the channels around or along the model; a second pattern can take over "
        "up the height; Acts on keeps the pattern inside a band or a sphere, where it stops at a natural edge. "
        "Patterns finer than ~3% of the height are lost at A3: keep the wavelength above that for plotting."),
    inputs=Inputs("mesh", exclude=(r"(?i)_growth(?:_stage_\d+)?\.(stl|obj|ply)$",)),
    output=Output("{stem}_growth.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-growth",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("stages", "faces", "cover"),
    params=[
        Section(
            "Pattern",
            Choice("pattern", "Pattern", "coral", PATTERN_CHOICES + [("custom", "Custom (feed, kill)")],
                   help="Coral: thick branching ridges. Fingerprint: parallel channels in a labyrinth. Maze: "
                        "thinner, denser meanders. Cells: round spots that divide until they tile the surface. "
                        "Honeycomb: the negative, a web of walls around round pits (Raised keeps the walls up). "
                        "Pebbles: sparse spots. Lichen: plateaus broken by stripes and holes."),
            Float("wavelength", "Wavelength", 5.0, unit="% h", min=1.0, max=30.0, step=0.5, decimals=1,
                  slider=True,
                  help="Spacing of the ridges or cells, % of the model's height. 4-8% reads well on A3 paper; "
                       "under ~3% the pattern is finer than the pen can show (and the run gets slow: the mesh "
                       "is refined to the wavelength)."),
            Float("growth", "Growth time", 10.0, unit="k steps", min=0.2, max=40.0, step=0.5, decimals=1,
                  slider=True,
                  help="How long the chemistry runs, in thousands of steps. ~3: colonies spreading from the "
                       "spots; 8-12: the pattern covers the surface; longer: it settles and straightens. A "
                       "sequence's stages are moments along this time."),
            Float("density", "Starting spots", 30.0, unit="/100 λ²", min=1.0, max=200.0, step=5.0, decimals=0,
                  help="How many seed spots per 100 square wavelengths of surface. Few: separate colonies that "
                       "meet in seams (and take longer to cover); many: an even skin sooner."),
            Choice("origin", "Starts", "scattered", [("scattered", "Scattered"), ("crown", "At the crown"),
                                                     ("face", "At the face"), ("base", "At the base")],
                   segmented=True,
                   help="Scattered: spots all over at once. Otherwise the spots are planted by a wave from that end "
                        "of the model, which reaches the far end at half the growth time: a colony creeping over "
                        "the model (best as a sequence)."),
            Seed("seed", "Seed", 0, help="Same seed, same spots and the same pattern."),
        ),
        Section(
            "Relief",
            Float("depth", "Depth", 2.0, unit="% h", min=0.0, max=15.0, step=0.25, decimals=2, slider=True,
                  help="Height of the relief, % of the model's height. 1-2%: a carved surface; 4% and more: "
                       "bulges that change the silhouette. 0 leaves the model as it is."),
            Choice("relief", "Relief", "raised", [("raised", "Raised"), ("engraved", "Engraved"),
                                                  ("both", "Both")], segmented=True,
                   help="Raised: the pattern stands out of the surface. Engraved: it is cut in. Both: half out, "
                        "half in, the surface as the midline."),
            Float("sharpness", "Sharpness", 0.35, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="0: soft swells as grown; 1: steep walls and flat tops, like cut terraces."),
            Float("balance", "Balance", 0.0, min=-1.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="Moves the edge of the relief: + fattens the raised pattern, − thins it to fine lines."),
            Float("soften", "Soften", 0.3, min=0.0, max=1.0, step=0.05, decimals=2, advanced=True,
                  help="Smooths the pattern over a share of the wavelength before it becomes relief (fewer "
                       "facets, rounder ridges)."),
        ),
        Section(
            "Direction",
            Choice("align", "Channels run", "none", [("none", "Free"), ("rings", "Around"),
                                                    ("vertical", "Up and down")], segmented=True,
                   help="Free: the pattern meanders. Around: channels wrap around the model like rings of a "
                        "fingerprint or contour lines. Up and down: they run from base to crown."),
            Float("align_strength", "Strength", 0.6, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=aligned,
                  help="How strongly the channels keep the direction (stripe patterns follow it best: "
                       "fingerprint, maze, coral)."),
            collapsed=True,
        ),
        Section(
            "Gradient",
            Choice("second", "Up the height becomes", "none",
                   [("none", "Same pattern")] + PATTERN_CHOICES + [("custom", "Custom (feed, kill)")],
                   help="A second pattern takes over up the model: coral at the base turning into cells at the "
                        "crown."),
            Float("grad_from", "From", 30.0, unit="% h", min=0.0, max=100.0, step=5.0, decimals=1,
                  visible_if=graded, help="Below this height: the first pattern only."),
            Float("grad_to", "To", 80.0, unit="% h", min=0.0, max=100.0, step=5.0, decimals=1,
                  visible_if=graded, help="Above this height: the second pattern only."),
            collapsed=True,
        ),
        Section(
            "Chemistry",
            Float("feed", "Feed", 0.0545, min=0.005, max=0.1, step=0.001, decimals=4, visible_if=custom,
                  help="Gray-Scott feed rate F (Custom only). Coral 0.0545, cells 0.0367, maze 0.029."),
            Float("kill", "Kill", 0.062, min=0.04, max=0.075, step=0.0005, decimals=4, visible_if=custom,
                  help="Gray-Scott kill rate k (Custom only). Coral 0.062, cells 0.0649, maze 0.057. Many "
                       "pairs grow nothing: try the named patterns' values first."),
            Float("du_dv", "Diffusion ratio", 2.0, min=1.2, max=4.0, step=0.1, decimals=2, advanced=True,
                  help="How much faster the substrate spreads than the activator (2 for every named pattern). "
                       "Other values shift the regime and the wavelength."),
            collapsed=True,
        ),
        Section("Acts on", *influence_params(), collapsed=True),
        Section(
            "Stages",
            Int("stages", "Stages", 1, min=1, max=8, slider=True,
                help="1: the grown model. More: a sequence from the untouched model to the full growth, each "
                     "stage a moment of the same simulation."),
            Bool("save_stages", "Also save every stage", True, visible_if=staged,
                 help="Stage files go in <name>_growth_stages/ with stages.json (its frame), so Contour Lines "
                      "draws them all with one scale, position and slice grid."),
            Choice("layout", "Primary output", "final", [("final", "Last stage"), ("progression", "All stages")],
                   segmented=True, visible_if=staged,
                   help="All stages: side by side in one file, for a 3D print or a single view of the sequence."),
            Choice("axis", "Side by side along", "x", [("x", "X"), ("y", "Y"), ("z", "Z")], segmented=True,
                   visible_if=lambda v: staged(v) and v.get("layout") == "progression"),
            Float("gap", "Gap", 15.0, unit="% h", min=0.0, max=200.0, step=1.0, decimals=1,
                  visible_if=lambda v: staged(v) and v.get("layout") == "progression",
                  help="Clearance between stages, % of the height."),
        ),
        Section("Model", *frame_params(stage_sets=False)),
        Section("Quality",
                Bool("refine", "Fit the mesh to the wavelength", True, advanced=True,
                     help="Split long edges (and simplify much denser scans) so the simulation has about "
                          "Simulation detail vertices per wavelength. Off: it runs on the mesh as it is."),
                Float("detail", "Simulation detail", 7.0, unit="/ λ", min=4.0, max=14.0, step=0.5, decimals=1,
                      advanced=True,
                      help="Mesh spacing of the simulation, samples per wavelength. 6-8 is enough; more is "
                           "slower and changes the pattern little."),
                Float("relief_detail", "Relief detail", 10.0, unit="/ λ", min=4.0, max=30.0, step=1.0,
                      decimals=0, advanced=True,
                      help="Mesh spacing of the output, samples per wavelength: smoother walls, more faces "
                           "(capped at 4 million)."),
                Int("draft_faces", "Preview faces", 60_000, min=2_000, max=2_000_000, advanced=True,
                    help="Preview simplifies bigger models to this many faces, simulates at 5 samples per "
                         "wavelength and skips the relief refinement; Grow uses the full model."),
                collapsed=True),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"stages": 2, "growth": 0.4, "wavelength": 12.0, "relief_detail": 6.0, "detail": 5.0},
    legacy=(),
)
