"""
Melt: a model melting like ice cream or molten metal - softening, sagging, pooling, dripping, dissolving - in one
stage or a sequence of stages, for any model. Runs the
melt in the installed Blender, in the background.

Outputs <name>_melt.stl (and <name>_melt_stages/ with stages.json), signed atlas-toolkit:mesh-melt.
"""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, frame_params


def staged(v):
    return int(v.get("stages", 1)) > 1


def is_head(v):
    return bool(v.get("head"))


TOOL = Tool(
    id="mesh-melt",
    name="Melt",
    version="1.1",
    category="mesh",
    icon="melt",
    action="Melt",
    summary="Melt a model: it softens, sags, pools at its base and drips, up to dissolving; in Blender.",
    tagline="One melted model, or a sequence of stages from intact to dissolved, to draw with Contour Lines.",
    description=(
        "Amount runs along a four-stage melt: 0 intact, 0.33 first drips, 0.67 heavy melt and "
        "a pool, 1 dissolved. Sag, Pool, Drips, Drip size, Dissolve and Softening scale those stages' values. "
        "Head or bust adds the face terms (the face slides off the skull, nose and ears droop, jowls bulge); "
        "leave it off for vases, figures, animals. With several stages the drips are seeded on the intact model, "
        "so the same drips grow in the same places from stage to stage; every stage is saved in place, ready for "
        "Contour Lines. Smoothing is counted for the mesh's own density, so a coarse model and a dense scan melt "
        "alike. Up and Front say how the model stands; Melt measures everything against its height. The melt runs "
        "in Blender (4.2 or newer), started once in the background."),
    inputs=Inputs("mesh", exclude=(r"(?i)_melt(?:_stage_\d+)?\.(stl|obj|ply)$",)),
    output=Output("{stem}_melt.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-melt",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("stages", "faces"),
    requires=("blender",),
    inspect=".mesh_melt:inspect",
    params=[
        Section(
            "Melt",
            Float("amount", "Amount", 0.667, min=0.0, max=1.0, step=0.01, decimals=3, slider=True,
                  help="How far the melt has gone: 0 intact, 0.33 first drips, 0.67 heavy melt with a pool, "
                       "1 dissolved (the four stages of the Dissolution sequence)."),
            Bool("head", "Head or bust", False,
                 help="Adds the face terms: the face slides down off the skull, the nose and ears droop, jowls "
                      "bulge, melted mass gathers under the jaw. The face looks toward the model's Front (Model "
                      "section; Auto is -Y). Off: any object (vase, figure, animal)."),
            Float("face_slide", "Face slide", 1.0, unit="×", min=0.0, max=3.0, step=0.1, decimals=2,
                  visible_if=is_head, help="How far the face slides off the skull (with the jowls and jaw mass)."),
            Float("sag", "Sag", 1.0, unit="×", min=0.0, max=3.0, step=0.1, decimals=2, slider=True,
                  help="Slump into the pool, the crown's sag and spread, material mounding at the base."),
            Float("pool", "Pool", 1.0, unit="×", min=0.0, max=3.0, step=0.1, decimals=2, slider=True,
                  help="Height of the pool of melted material: with contours, its rings."),
            Float("drips", "Drips", 1.0, unit="×", min=0.0, max=2.0, step=0.1, decimals=2, slider=True,
                  help="How many drips have formed (0: none). Hair and fine relief grow many: lower it there."),
            Float("drip_size", "Drip size", 1.0, unit="×", min=0.2, max=3.0, step=0.05, decimals=2,
                  suggest=lambda info: info.values.get("drip_size_auto", 1.0),
                  help="Thickness of drips and rivulets. Thin models (an arm, fingers) want ~0.5; thin drips "
                       "drawn at a small scale can vanish. Auto: scaled by how thin the model is (a life-size head = 1)."),
            Float("dissolve", "Dissolve", 1.0, unit="×", min=0.0, max=1.5, step=0.05, decimals=2, slider=True,
                  help="Pull toward a featureless blob (it starts at the heavy melt): the real feature eraser."),
            Float("dissolve_detail", "Dissolve grain", 1.0, unit="×", min=0.3, max=4.0, step=0.1, decimals=2,
                  help="Voxel size of the dissolve: features smaller than it go first. Low, only fine relief "
                       "erodes; high, whole features go and the model rounds toward a blob."),
            Float("soften", "Softening", 1.0, unit="×", min=0.0, max=3.0, step=0.1, decimals=2,
                  help="How far the heat blunts crisp detail (a radius: 2× is four times the smoothing)."),
            Seed("seed", "Seed", 0, help="Where the drips form and how the melt wanders."),
        ),
        Section(
            "Stages",
            Int("stages", "Stages", 1, min=1, max=8, slider=True,
                help="1: one melted model. More: a sequence from intact to the amount, each stage in place."),
            Bool("save_stages", "Also save every stage", True, visible_if=staged,
                 help="Stage files go in <name>_melt_stages/ with stages.json, so Contour Lines draws them all "
                      "with one scale, position and slice grid."),
            Choice("layout", "Primary output", "final", [("final", "Last stage"), ("progression", "All stages")],
                   segmented=True, visible_if=staged),
            Choice("axis", "Side by side along", "x", [("x", "X"), ("y", "Y"), ("z", "Z")], segmented=True,
                   visible_if=lambda v: staged(v) and v.get("layout") == "progression"),
            Float("gap", "Gap", 25.0, unit="% h", min=0.0, max=300.0, step=1.0, decimals=1,
                  visible_if=lambda v: staged(v) and v.get("layout") == "progression"),
        ),
        Section(
            "Character",
            Float("droop", "Features droop", 1.0, unit="×", min=0.0, max=3.0, step=0.1, decimals=2,
                  visible_if=is_head, advanced=True, help="The nose tip and the earlobes hang lowest."),
            Float("asymmetry", "Asymmetry", 0.5, min=0.0, max=1.0, step=0.05, decimals=2, visible_if=is_head,
                  advanced=True, help="One side of the face melts this much more than the other."),
            Float("head_from", "Head spans from", 0.0, unit="% h", min=0.0, max=95.0, step=5.0, decimals=1,
                  visible_if=is_head, advanced=True,
                  help="The head's height band, % of the model's height from its base (a bust: ~55 to 100)."),
            Float("head_to", "Head spans to", 100.0, unit="% h", min=5.0, max=100.0, step=5.0, decimals=1,
                  visible_if=is_head, advanced=True),
            Float("pool_flatness", "Pool rim", 1.0, unit="×", min=0.2, max=3.0, step=0.1, decimals=2,
                  advanced=True, help="High: a flat pool with a steep rim. Low: a sloped pool of topographic rings."),
            Float("drip_length", "Drip length", 1.0, unit="×", min=0.2, max=3.0, step=0.1, decimals=2,
                  advanced=True),
            Float("drip_spacing", "Drip spacing", 1.0, unit="×", min=0.3, max=4.0, step=0.1, decimals=2,
                  advanced=True, help="Distance between neighbouring drips: higher, fewer and sparser."),
            Bool("hang_drips", "Drips from overhangs", True, advanced=True,
                 help="Brow, nose, lips, chin, jaw: drips hang from surfaces that face down."),
            Bool("run_drips", "Rivulets", True, advanced=True,
                 help="Rivulets running down steep sides (off the face when Head or bust is on)."),
            Choice("seed_mode", "Seed the drips on", "rest", [("rest", "The intact model"),
                                                             ("final", "The melted model")], advanced=True,
                   help="The intact model: the same drips grow in the same places through a sequence."),
            Float("drip_floor", "Drips stop above the base", 0.0, unit="% h", min=0.0, max=30.0, step=0.5,
                  decimals=1, advanced=True,
                  help="A falling drip ends this far above the floor (Thin figure: 1.5%); 0 = off, drips end where "
                       "they meet material."),
            Float("irregularity", "Irregularity", 1.0, unit="×", min=0.0, max=2.5, step=0.1, decimals=2,
                  advanced=True, help="Low-frequency unevenness of the melt."),
            Float("flow", "Flow streaks", 1.0, unit="×", min=0.0, max=4.0, step=0.1, decimals=2, advanced=True,
                  help="Faint vertical streaks where material moves."),
            Float("base_flatten", "Flatten base", 2.0, unit="% h", min=0.0, max=10.0, step=0.5, decimals=1,
                  advanced=True, help="Flattens a sliver above the floor (ragged scan cuts)."),
        ),
        Section("Model", *frame_params(stage_sets=False),
                Int("subdiv", "Smoothing subdivision", 1, min=0, max=2, advanced=True,
                    help="Catmull-Clark levels before melting (each ×4 faces): smoother contours, slower.")),
        Section("Quality",
                Int("draft_faces", "Preview faces", 40_000, min=2_000, max=2_000_000, advanced=True,
                    help="Preview melts a lighter copy, without subdivision and with coarser drip tubes."),
                collapsed=True),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"stages": 2, "subdiv": 0},
    legacy=("projects/blender3d_project (skill plotter-contour-art: melt_distortion.py)",),
)
