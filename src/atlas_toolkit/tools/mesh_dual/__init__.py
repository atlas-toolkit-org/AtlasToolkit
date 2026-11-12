"""
Dual Fusion: two models fused into one sculpture, cinematically: they converge and melt into one, one pushes through
the other like a membrane, they are split and rejoined along a seam, interleaved in slabs, joined by strands of liquid
metal, one crumbles to dust revealing the other, the passage between them is frozen in echoes, they share a mosaic of
cells, or one becomes a cracking cocoon round the other; in one stage or a sequence.

Outputs <name>_dual.stl (and <name>_dual_stages/ with stages.json), signed atlas-toolkit:mesh-dual.
"""
from atlas_toolkit.core import Bool, Choice, FileRef, Float, Inputs, Int, Output, Section, Seed, Tool, Vec3, frame_params
from atlas_toolkit.core.params import AXIS_CHOICES

MESH_EXTS = (".stl", ".obj", ".ply", ".glb", ".gltf", ".off")
SWEEP_CHOICES = [("none", "Even"), ("up", "From the base"), ("down", "From the top"), ("left", "From the left"),
                 ("right", "From the right"), ("front", "From the front"), ("back", "From the back"),
                 ("out", "From the centre"), ("in", "From the outside")]


def kind(*names):
    return lambda v: v.get("fusion", "convergence") in names


def staged(v):
    return int(v.get("stages", 1)) > 1


def apart(v):
    return v.get("fusion", "convergence") in ("convergence", "bridge", "echo") or (
        v.get("fusion") == "emergence" and v.get("host", "model") != "model")


def dropping(v):
    return v.get("keep") == "drop"


TOOL = Tool(
    id="mesh-dual",
    name="Dual Fusion",
    version="1.1",
    category="mesh",
    icon="dual",
    action="Fuse",
    summary="Fuse two models into one sculpture: converge and melt, emerge, split along a seam, interleave, bridge "
            "with liquid strands, disintegrate, echo, patchwork or a cracking cocoon.",
    tagline="Two heads kiss and melt into one, a face pushes through another like rubber, two figures share a "
            "carved seam: one closed mesh, ready for Contour Lines, Sketch Hatching or Plotter Vision.",
    description=(
        "The second model is normalised to the first: both stand on one base, the second Size ratio times as tall. "
        "Each model is sampled once as a signed-distance volume on its own grid and moved through its pose, so a "
        "sequence of stages can show them approaching without losing detail. Convergence: the models glide "
        "together, a smooth union bridges them before they touch, and past Melt from they melt into one hybrid. "
        "Emergence: one model pushes out of the other (or out of a wall, or up from the floor) under a stretched "
        "membrane that Breach tears open. Split: the two overlaid and divided by a seam, plane or wavy, that wipes "
        "across with Fusion; a carved groove, a raised suture, or the halves pulled apart. Strata: slabs taken from "
        "one model or the other, B's share growing across the model, a dithered metamorphosis. Liquid bridge: the "
        "two apart, joined by seeded strands that thin and sag between them. Disintegration: a front crosses the "
        "first model, which crumbles into drifting cubes or shards, revealing the second. Motion echo: moments of "
        "the passage from one to the other fused into one sculpture. Cellular patchwork: Voronoi cells from either "
        "model, optionally grouted and exploded. Chrysalis shell: the second model as a cocoon round the first that "
        "cracks or opens. Fusion 0 is the starting scene (the "
        "models themselves, exactly), 1 the end of the change. Without a second model, the first one mirrored is "
        "used."),
    inputs=Inputs("mesh", exclude=(r"(?i)_dual(?:_stage_\d+)?\.(stl|obj|ply)$",)),
    output=Output("{stem}_dual.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-dual",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("stages", "faces", "pieces"),
    params=[
        Section(
            "Fusion",
            Choice("fusion", "Fusion type", "convergence",
                   [("convergence", "Convergence"), ("emergence", "Emergence"), ("split", "Split with seam"),
                    ("strata", "Interleaved strata"), ("bridge", "Liquid bridge"), ("disintegrate", "Disintegration"),
                    ("echo", "Motion echo"), ("patchwork", "Cellular patchwork"), ("chrysalis", "Chrysalis shell")],
                   help="Convergence: approach, bridge and melt into one. Emergence: one pushes through the other "
                        "(or a wall, the floor) under a membrane. Split: halves of each along a seam. Strata: "
                        "alternating slabs of each. Liquid bridge: joined by strands across a gap. Disintegration: the first "
                        "crumbles into drifting fragments, revealing the second. Motion echo: the passage from one "
                        "to the other fused into one sculpture. Cellular patchwork: a mosaic of cells from each. "
                        "Chrysalis shell: the second as a shell round the first, cracking or opening."),
            FileRef("second", "Second model", "", exts=MESH_EXTS,
                    help="Model B (a file, or a workflow's side input). None: the first model mirrored."),
            Float("ratio", "Size ratio", 1.0, min=0.2, max=5.0, step=0.05, decimals=2, slider=True,
                  help="The second model's height over the first's, after both are normalised to one height."),
            Float("amount", "Fusion", 0.6, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="0 is the starting scene (the models as placed), 1 the end of the change. With several "
                       "stages, the last stage's amount."),
            Bool("swap", "Swap roles", False,
                 visible_if=kind("emergence", "split", "strata", "disintegrate", "patchwork", "chrysalis"),
                 help="The second model takes the first one's role: it becomes the host, or the side and slabs "
                      "the seam starts from."),
        ),
        Section(
            "Arrangement",
            Choice("arrangement", "Arrangement", "face", [("side", "Side by side"), ("face", "Face to face"),
                                                          ("back", "Back to back"), ("stacked", "Stacked"),
                                                          ("overlaid", "Overlaid")],
                   visible_if=kind("convergence", "bridge", "echo"),
                   help="How the two stand at the start: side by side facing the viewer, face to face or back to "
                        "back in profile, the second on top, or overlaid."),
            Float("gap", "Gap", 30.0, unit="% h", min=-50.0, max=300.0, step=5.0, decimals=1, visible_if=apart,
                  help="Space between them at the start, % of the first model's height (negative: overlapping)."),
            Vec3("b_turn", "Turn second", (0.0, 0.0, 0.0), unit="°", min=-180.0, max=180.0, step=5.0, decimals=1,
                 help="Turns the second model about X, then Y, then Z, before it is placed."),
            Vec3("b_offset", "Move second", (0.0, 0.0, 0.0), unit="% h", min=-300.0, max=300.0, step=5.0,
                 decimals=1, help="Moves the second model, % of the first's height: X across, Y depth (front is −), "
                                  "Z up."),
            Bool("mirror_b", "Mirror second", False,
                 help="Mirrors the second model left to right (a mirrored twin, or a better match of profiles)."),
            collapsed=True,
        ),
        Section(
            "Convergence",
            Float("radius", "Bridge radius", 12.0, unit="% h", min=0.0, max=60.0, step=1.0, decimals=1,
                  visible_if=kind("convergence"),
                  help="How far apart the two start to join, % h: necks and bridges reach across this distance "
                       "before they touch."),
            Float("melt_from", "Melt from", 0.6, min=0.05, max=1.0, step=0.05, decimals=2,
                  visible_if=kind("convergence"),
                  help="Past this Fusion, the joined pair melts into one hybrid (1: they only join)."),
            Float("balance", "Balance", 0.5, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=kind("convergence"),
                  help="The final hybrid: 0 the first model's form, 1 the second's, 0.5 halfway."),
            Bool("turn_front", "Turn to the viewer", True,
                 visible_if=lambda v: kind("convergence")(v) and v.get("arrangement") in ("face", "back"),
                 help="Face-to-face (or back-to-back) pairs turn to face the front as they melt, ending as one "
                      "head looking at you."),
            collapsed=False,
        ),
        Section(
            "Emergence",
            Choice("host", "Emerges from", "model", [("model", "The other model"), ("wall", "A wall"),
                                                     ("floor", "The floor")], segmented=True,
                   visible_if=kind("emergence"),
                   help="The other model: the second pushes out of the first's front. A wall: both push through a "
                        "wall toward the viewer. The floor: both rise out of the ground."),
            Float("depth", "Emerge by", 55.0, unit="%", min=0.0, max=150.0, step=5.0, decimals=0,
                  visible_if=kind("emergence"),
                  help="How far out they come at Fusion 1, % of their own depth (height, from the floor)."),
            Float("skin", "Membrane softness", 3.0, unit="% h", min=0.0, max=20.0, step=0.5, decimals=1,
                  visible_if=kind("emergence"),
                  help="Blur of the form under the membrane: 0 sharp, 2-4 rubber, 8+ a sheet barely shaped."),
            Float("skin_thickness", "Membrane", 0.8, unit="% h", min=0.0, max=10.0, step=0.1, decimals=1,
                  visible_if=kind("emergence"), help="How much the membrane stands off the form."),
            Float("rim", "Rim", 5.0, unit="% h", min=0.0, max=30.0, step=0.5, decimals=1,
                  visible_if=kind("emergence"),
                  help="Where the membrane leaves the host: a smooth, stretched rim of this radius."),
            Float("breach", "Breach", 0.0, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=kind("emergence"),
                  help="Tears the membrane open around the leading point (a nose, a brow): 0 intact, 1 most of "
                       "the form bare."),
            Float("wall_thickness", "Wall thickness", 15.0, unit="% h", min=1.0, max=100.0, step=1.0, decimals=1,
                  visible_if=lambda v: kind("emergence")(v) and v.get("host") != "model"),
            Float("wall_margin", "Wall margin", 25.0, unit="% h", min=0.0, max=200.0, step=5.0, decimals=1,
                  visible_if=lambda v: kind("emergence")(v) and v.get("host") != "model",
                  help="How far the wall or floor reaches beyond the models."),
            collapsed=False,
        ),
        Section(
            "Seam",
            Choice("seam", "Seam", "lr", [("lr", "Left | right"), ("fb", "Front | back"),
                                          ("horizontal", "Horizontal"), ("tilted", "Tilted")],
                   visible_if=kind("split"),
                   help="The surface dividing the two; Fusion moves it across, from all first model to all "
                        "second."),
            Float("tilt", "Tilt", 30.0, unit="°", min=-80.0, max=80.0, step=5.0, decimals=0,
                  visible_if=lambda v: kind("split")(v) and v.get("seam") == "tilted"),
            Float("wave", "Wave", 0.0, unit="% h", min=0.0, max=30.0, step=0.5, decimals=1, visible_if=kind("split"),
                  help="Amplitude of a wave along the seam (a jagged fault, a meander)."),
            Float("waves", "Waves", 3.0, min=0.5, max=20.0, step=0.5, decimals=1,
                  visible_if=lambda v: kind("split")(v) and float(v.get("wave", 0)) > 0,
                  help="Waves over the model's height."),
            Choice("style", "Seam style", "groove", [("blend", "Blend"), ("groove", "Groove"),
                                                     ("suture", "Suture"), ("open", "Split open")],
                   segmented=True, visible_if=kind("split"),
                   help="Blend: one melts into the other across the seam's width. Groove: a carved channel along "
                        "the seam. Suture: a raised ridge. Split open: the halves pulled apart, sections showing."),
            Float("seam_width", "Seam width", 3.0, unit="% h", min=0.2, max=40.0, step=0.5, decimals=1,
                  visible_if=kind("split"), help="The groove's or ridge's width, or the blend's band."),
            Float("seam_depth", "Seam depth", 2.0, unit="% h", min=0.1, max=20.0, step=0.5, decimals=1,
                  visible_if=lambda v: kind("split")(v) and v.get("style") in ("groove", "suture"),
                  help="How deep the groove is carved, or how high the ridge stands."),
            Float("open", "Opening", 20.0, unit="% h", min=0.0, max=200.0, step=5.0, decimals=1,
                  visible_if=lambda v: kind("split")(v) and v.get("style") == "open",
                  help="How far apart the two halves are pulled."),
            collapsed=False,
        ),
        Section(
            "Strata",
            Int("slabs", "Slabs", 24, min=2, max=200, slider=True, visible_if=kind("strata"),
                help="Slab periods across the model: each holds a slab of each model."),
            Choice("strata_axis", "Slabs along", "up", [("up", "Height"), ("across", "Width"), ("depth", "Depth")],
                   segmented=True, visible_if=kind("strata")),
            Float("slab_gap", "Gap between slabs", 0.0, unit="% h", min=0.0, max=10.0, step=0.1, decimals=2,
                  visible_if=kind("strata"), help="Air between the slabs, % h (0: they touch)."),
            Choice("grade_dir", "Graded", "up", SWEEP_CHOICES, visible_if=kind("strata"),
                   help="Where the second model's share grows first. Even: the same share in every slab."),
            Float("grading", "Gradient", 60.0, unit="%", min=5.0, max=100.0, step=5.0, decimals=0,
                  visible_if=lambda v: kind("strata")(v) and v.get("grade_dir") != "none",
                  help="How long the gradient is, % of the way across: 100 a slow change over the whole model, "
                       "20 a sharp front."),
            collapsed=False,
        ),
        Section(
            "Liquid bridge",
            Int("strands", "Strands", 14, min=1, max=80, slider=True, visible_if=kind("bridge"),
                help="How many filaments join the two."),
            Float("strand_width", "Strand width", 7.0, unit="% h", min=0.3, max=20.0, step=0.1, decimals=1,
                  visible_if=kind("bridge"), help="Thickness of a strand at its roots, % h."),
            Float("thinning", "Thinnest", 0.4, min=0.05, max=1.0, step=0.05, decimals=2,
                  visible_if=kind("bridge"),
                  help="Thickness at a strand's middle, as a share of its roots (1: an even tube)."),
            Float("spread", "Spread", 0.7, min=0.05, max=1.0, step=0.05, decimals=2, visible_if=kind("bridge"),
                  help="How widely the strands spread over the facing sides (low: a bundle at the middle)."),
            Float("wobble", "Waver", 3.0, unit="% h", min=0.0, max=30.0, step=0.5, decimals=1,
                  visible_if=kind("bridge"), help="Sideways wavering of each strand."),
            Float("sag", "Sag", 6.0, unit="% h", min=0.0, max=50.0, step=1.0, decimals=1,
                  visible_if=kind("bridge"), help="How far the strands droop at their middle, under gravity."),
            Seed("seed", "Seed", 1,
                 visible_if=lambda v: kind("bridge", "disintegrate", "patchwork")(v) or (
                     kind("chrysalis")(v) and v.get("shell_pattern") == "cracks"),
                 help="Where the strands run, the fragments fall, the cells or the cracks lie."),
            collapsed=False,
        ),
        Section(
            "Disintegration",
            Choice("reveal", "Reveals", "second", [("second", "The second model"), ("nothing", "Nothing")],
                   segmented=True, visible_if=kind("disintegrate"),
                   help="What the front leaves behind: the second model, standing where the first was, or nothing "
                        "(the first model simply turns to dust)."),
            Choice("front_dir", "Front", "left", SWEEP_CHOICES[1:], visible_if=kind("disintegrate"),
                   help="Where the front starts."),
            Float("front_width", "Dust band", 35.0, unit="%", min=5.0, max=100.0, step=5.0, decimals=0,
                  visible_if=kind("disintegrate"),
                  help="How far behind the front fragments are still in the air, % of the way across."),
            Float("cell", "Fragment cells", 3.0, unit="% h", min=0.5, max=20.0, step=0.25, decimals=2,
                  visible_if=kind("disintegrate"),
                  help="The grid the first model breaks along, % h: 2-4 fine dust, 8+ chunks. Finer cells mean "
                       "more fragments (see Fragment budget)."),
            Choice("fragment", "Fragments", "cube", [("cube", "Cubes"), ("shard", "Shards")], segmented=True,
                   visible_if=kind("disintegrate"), help="Cubes: voxel dust. Shards: seeded irregular splinters."),
            Float("fragment_size", "Fragment size", 0.85, min=0.2, max=1.5, step=0.05, decimals=2,
                  visible_if=kind("disintegrate"), help="A fragment's size as a share of its cell."),
            Choice("drift_dir", "Drift", "rise", [("rise", "Rise"), ("along", "With the front"),
                                                  ("scatter", "Scatter")], segmented=True,
                   visible_if=kind("disintegrate"),
                   help="Where fragments go: up like ash, on with the front like wind, or out from the centre."),
            Float("drift", "Drift distance", 40.0, unit="% h", min=0.0, max=300.0, step=5.0, decimals=0,
                  visible_if=kind("disintegrate"), help="How far the oldest fragments have travelled."),
            Float("shrink", "Shrink", 0.7, min=0.0, max=1.0, step=0.05, decimals=2, visible_if=kind("disintegrate"),
                  help="How much the oldest fragments have shrunk (1: to nothing)."),
            Float("tumble", "Tumble", 180.0, unit="°", min=0.0, max=720.0, step=15.0, decimals=0,
                  visible_if=kind("disintegrate"), help="How far the oldest fragments have turned."),
            Float("survive", "Thinning", 0.2, min=0.0, max=0.95, step=0.05, decimals=2,
                  visible_if=kind("disintegrate"),
                  help="Share of the cells that crumble to nothing instead of flying off (an airier cloud)."),
            Int("max_fragments", "Fragment budget", 20_000, min=100, max=200_000, step=1000,
                visible_if=kind("disintegrate"), help="At most this many fragments (a seeded choice beyond it)."),
            collapsed=False,
        ),
        Section(
            "Motion echo",
            Int("echoes", "Echoes", 6, min=2, max=16, slider=True, visible_if=kind("echo"),
                help="How many moments of the passage are fused into the sculpture."),
            Choice("path", "Path", "arc", [("straight", "Straight"), ("arc", "Arc"), ("spiral", "Spiral")],
                   segmented=True, visible_if=kind("echo"),
                   help="Straight from one model's place to the other's, over an arc, or spiralling round."),
            Float("lift", "Arc height", 25.0, unit="% h", min=0.0, max=150.0, step=5.0, decimals=0,
                  visible_if=lambda v: kind("echo")(v) and v.get("path") != "straight",
                  help="Height of the arc, or the spiral's radius."),
            Float("turns", "Turns", 1.0, min=0.25, max=4.0, step=0.25, decimals=2,
                  visible_if=lambda v: kind("echo")(v) and v.get("path") == "spiral"),
            Float("twist", "Twist", 0.0, unit="°", min=-360.0, max=360.0, step=15.0, decimals=0,
                  visible_if=kind("echo"), help="How far the form turns about its vertical axis along the way."),
            Float("thin", "Fade", 2.0, unit="% h", min=0.0, max=15.0, step=0.5, decimals=1,
                  visible_if=kind("echo"), help="How much thinner the oldest echoes are (a trail that fades)."),
            Float("echo_blend", "Join", 6.0, unit="% h", min=0.0, max=40.0, step=0.5, decimals=1,
                  visible_if=kind("echo"), help="Smooth radius between neighbouring echoes."),
            collapsed=False,
        ),
        Section(
            "Patchwork",
            Float("cell_size", "Cell size", 18.0, unit="% h", min=3.0, max=80.0, step=1.0, decimals=1,
                  visible_if=kind("patchwork"), help="Size of the seeded Voronoi cells, % h."),
            Choice("patch_dir", "Spreads", "up", SWEEP_CHOICES, visible_if=kind("patchwork"),
                   help="Where the second model's cells appear first. Even: at random everywhere."),
            Float("patch_width", "Gradient", 60.0, unit="%", min=5.0, max=100.0, step=5.0, decimals=0,
                  visible_if=lambda v: kind("patchwork")(v) and v.get("patch_dir") != "none"),
            Float("grout", "Grout", 0.0, unit="% h", min=0.0, max=10.0, step=0.1, decimals=2,
                  visible_if=kind("patchwork"), help="Gaps between all cells, % h (0: the cells touch)."),
            Float("explode", "Explode", 0.0, unit="%", min=0.0, max=100.0, step=5.0, decimals=0,
                  visible_if=kind("patchwork"),
                  help="Pushes every cell out from the centre by this share of its distance: an exploded mosaic."),
            collapsed=False,
        ),
        Section(
            "Chrysalis",
            Choice("shell_pattern", "Openings", "cracks", [("cracks", "Cracks"), ("gyroid", "Gyroid"),
                                                          ("windows", "Round windows")], segmented=True,
                   visible_if=kind("chrysalis"),
                   help="Cracks: the shell breaks into plates (seeded). Gyroid: a woven lattice opens. Round "
                        "windows: Schwarz P openings."),
            Float("shell", "Shell", 2.5, unit="% h", min=0.3, max=15.0, step=0.1, decimals=1,
                  visible_if=kind("chrysalis"), help="Thickness of the shell, inward from the outer model's skin."),
            Float("clearance", "Clearance", 4.0, unit="% h", min=0.5, max=40.0, step=0.5, decimals=1,
                  visible_if=kind("chrysalis"),
                  help="Space kept between the inner model and the shell, which bulges out where the outer model is "
                       "too small to hold the inner one."),
            Float("pattern_cell", "Pattern size", 16.0, unit="% h", min=3.0, max=80.0, step=1.0, decimals=1,
                  visible_if=kind("chrysalis"), help="Plate or lattice cell size, % h."),
            Float("rib", "Opening", 6.0, unit="% h", min=0.3, max=30.0, step=0.5, decimals=1,
                  visible_if=kind("chrysalis"),
                  help="Cracks: how wide they open at Fusion 1. Lattices: the ribs left at Fusion 1."),
            Choice("open_dir", "Opens", "down", SWEEP_CHOICES, visible_if=kind("chrysalis"),
                   help="Where the shell opens first."),
            Float("open_width", "Gradient", 60.0, unit="%", min=5.0, max=100.0, step=5.0, decimals=0,
                  visible_if=lambda v: kind("chrysalis")(v) and v.get("open_dir") != "none"),
            collapsed=False,
        ),
        Section(
            "Shape",
            Float("swell", "Swell", 0.0, unit="% h", min=0.0, max=20.0, step=0.5, decimals=1,
                  visible_if=kind("convergence", "split"),
                  help="Adds volume half way through the change, so forms that overlap little do not thin out."),
            collapsed=True,
        ),
        Section(
            "Stages",
            Int("stages", "Stages", 1, min=1, max=8, slider=True,
                help="1: one result. More: a sequence from the starting scene (stage 0) to the Fusion amount."),
            Bool("save_stages", "Also save every stage", True, visible_if=staged,
                 help="Stage files go in <name>_dual_stages/ with stages.json, so Contour Lines draws them all with "
                      "one scale, position and slice grid."),
            Choice("layout", "Primary output", "final", [("final", "Last stage"), ("progression", "All stages")],
                   segmented=True, visible_if=staged,
                   help="All stages: side by side in one file, a single view of the sequence."),
            Choice("axis", "Side by side along", "x", [("x", "X"), ("y", "Y"), ("z", "Z")], segmented=True,
                   visible_if=lambda v: staged(v) and v.get("layout") == "progression"),
            Float("stage_gap", "Gap", 15.0, unit="% h", min=0.0, max=200.0, step=1.0, decimals=1,
                  visible_if=lambda v: staged(v) and v.get("layout") == "progression",
                  help="Clearance between stages, % of the height."),
        ),
        Section(
            "Clean-up",
            Choice("keep", "Loose pieces", "all", [("all", "Keep all"), ("drop", "Drop small"),
                                                   ("largest", "Largest only")], segmented=True,
                   help="Keep all leaves every piece; drop small removes pieces under a share of the volume; "
                        "largest only keeps one body."),
            Float("min_piece", "Smaller than", 0.5, unit="% vol", min=0.0, max=20.0, step=0.1, decimals=2,
                  visible_if=dropping, help="Pieces below this share of the total volume are removed."),
            Int("smooth", "Smoothing", 3, min=0, max=30,
                help="Taubin passes over the extracted surface (removes the grid's faint facets)."),
            Int("max_faces", "Output faces", 600_000, min=0, max=20_000_000, step=50_000,
                help="Simplify each stage to at most this many faces. 0 keeps every face of the grid."),
            collapsed=True,
        ),
        Section(
            "Models",
            *frame_params(stage_sets=False),
            Choice("second_up", "Second: up", "auto", AXIS_CHOICES, advanced=True,
                   help="Which way is up in the second model's file (auto: as for the first)."),
            Choice("second_front", "Second: front", "auto", AXIS_CHOICES, advanced=True,
                   help="The side the second model faces."),
            collapsed=True,
        ),
        Section(
            "Quality",
            Int("resolution", "Resolution", 256, min=48, max=512, step=16,
                help="Samples along each model's largest side, and along the taller one's height in the scene. 256 "
                     "keeps a head's features; 384 for fine detail (time grows with the cube)."),
            Int("draft_resolution", "Preview resolution", 112, min=32, max=512, step=16, advanced=True,
                help="Preview samples (Run always uses Resolution)."),
            Int("draft_faces", "Preview input faces", 60_000, min=2_000, max=2_000_000, advanced=True,
                help="Preview simplifies bigger models to this many faces before sampling; Run samples them in "
                     "full."),
            collapsed=True),
    ],
    preset_skip=("second", "up_axis", "front", "rotate", "second_up", "second_front"),
    test_values={"resolution": 48, "draft_resolution": 40, "stages": 2, "max_faces": 0},
)
