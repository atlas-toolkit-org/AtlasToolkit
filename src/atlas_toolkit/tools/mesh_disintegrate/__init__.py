"""Disintegrate: progressive surface fragmentation into loose triangles, signed atlas-toolkit:mesh-disintegrate.

Renamed from Mesh Morph in 1.17.0: the former id `mesh-morph` stays an alias, and earlier outputs carry the signature
atlas-toolkit:mesh-morph."""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, Vec3

TOOL = Tool(
    id="mesh-disintegrate",
    name="Disintegrate",
    version="1.1.0",
    category="mesh",
    icon="cube",
    action="Disintegrate",
    summary="Break a model's surface into loose triangles that drift apart, stage by stage.",
    tagline="A surface coming apart into its own triangles, one stage at a time.",
    description=(
        "Stage 0 is the imported model. Each step splits every triangle into four, then moves each triangle "
        "corner on its own, so neighbouring triangles separate. Strength grows with the stage number; the "
        "result is open surface fragments, not closed solids. Preview computes the same geometry as Run. Large "
        "previews sample whole triangles without welding them. All stages together must fit a two-million-face "
        "budget; dense inputs are never simplified. Save the progression, the final stage, or also every stage "
        "separately, then draw a mesh with Hatch Maker or Contour Lines."),
    inputs=Inputs("mesh", exclude=(
        r"(?i)_(?:disintegrate|morph)_(?:final|progression)_\d+steps(?:_stage_\d+)?\.(stl|obj|ply)$",)),
    output=Output("{stem}_disintegrate_{layout}_{steps}steps.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-disintegrate",
    aliases=("mesh-morph",),
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("stages", "faces"),
    params=[
        Section(
            "Subdivision",
            Int("steps", "Subdivision steps", 2, min=0, max=8, slider=True,
                help="Steps after the original (stage 0). Each step multiplies faces by four, so five steps make "
                     "six stages and want a light model. Total budget: 2,000,000 faces."),
        ),
        Section(
            "Distortion",
            Choice("units", "Strength in", "percent", [("percent", "% of original diagonal"),
                                                        ("units", "Model units")],
                   help="Relative strength uses the original model's bounding-box diagonal, at every stage."),
            Float("strength", "Strength", 1.0, min=0, max=1e6, step=0.25, decimals=3,
                  help="Maximum displacement at stage 1, in the units selected above. Zero only subdivides."),
            Float("growth", "Growth exponent", 1.0, min=0, max=3, step=0.1, decimals=2, slider=True,
                  help="Stage i uses strength × i^exponent. 0: constant; 1: linear; 2: quadratic."),
            Choice("direction", "Direction", "random", [("random", "Random"), ("normal", "Surface normal"),
                                                        ("radial", "Radial")],
                   help="Each corner moves independently in a random direction, along its triangle's normal, "
                        "or toward/away from the original bounding-box centre."),
            Vec3("influence", "XYZ influence", (1.0, 1.0, 1.0), min=0, max=1, step=0.1, decimals=2,
                 help="Scale displacement along each axis. Zero locks that axis; directions are not renormalized."),
            Seed("seed", "Seed", 42, help="Same input, settings and seed give the same fragments."),
        ),
        Section(
            "Arrangement",
            Choice("layout", "Primary output", "progression", [("progression", "All stages"),
                                                                ("final", "Final stage")], segmented=True,
                   help="All stages includes stage 0. Final stage stays in the original coordinate frame."),
            Choice("axis", "Progression axis", "x", [("x", "X"), ("y", "Y"), ("z", "Z")], segmented=True,
                   help="Axis for the progression preview and combined output; individual stages stay in place."),
            Choice("gap_units", "Gap in", "percent", [("percent", "% of original diagonal"),
                                                       ("units", "Model units")]),
            Float("gap", "Gap", 25.0, min=0, max=1e6, step=1, decimals=3,
                  help="Clearance between the actual bounds of successive stages, in the selected gap units."),
        ),
        Section(
            "Extra files",
            Bool("separate", "Also save every stage", False,
                 help="Save stages 0 through the final stage without layout offsets, in a sibling stages folder. "
                      "An existing stages folder is preserved and a numbered folder is used instead."),
        ),
    ],
    sample=".engine:make_sample",
    test_values={"steps": 2},
    legacy=("Processing/generators/shape_mesh_morph_MULTI_v03/shape_mesh_morph_MULTI_v03.pde",),
)
