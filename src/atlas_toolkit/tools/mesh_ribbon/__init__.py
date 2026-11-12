"""
Ribbon Wrap: a flexible band of silk, satin, velvet, cotton or gauze wound around a model, partly or wholly, like a
bandage, a parcel's ribbon or a loose scarf, its two free ends falling outside the form so the scene reads; the
fabric's grain is in the geometry, so Contour Lines, Sketch Hatching or Plotter Vision draw it. An optional Blender
cloth pass settles it with gravity.

Outputs <name>_ribbon.stl (model and ribbon, or the ribbon alone), signed atlas-toolkit:mesh-ribbon.
"""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, frame_params


def style(*names):
    return lambda v: v.get("style", "helix") in names


def bands(layout=None):
    def ok(v):
        return v.get("style", "helix") == "bands" and (layout is None or v.get("band_layout", "meridians") in layout)
    return ok


def has_tails(v):
    return float(v.get("tail_start", 0) or 0) > 0 or float(v.get("tail_end", 0) or 0) > 0


def cloth(v):
    return bool(v.get("cloth"))


def tipped(v):
    return has_tails(v) and v.get("tip", "cut") != "cut"


AXIS_CHOICES = [("z", "Up"), ("y", "Front"), ("x", "Side")]

TOOL = Tool(
    id="mesh-ribbon",
    name="Ribbon Wrap",
    version="1.0.1",
    optional=(("blender", "Settle in Blender: a cloth simulation drapes the ribbon over the model"),),
    category="mesh",
    icon="ribbon",
    action="Wrap",
    summary="Wrap a model in a ribbon of silk, satin, velvet or cotton: a bandage, parcel bands or a loose drape, "
            "its free ends falling outside the form.",
    tagline="Cloth on stone: the ribbon's threads and grain are in the mesh, ready for Contour Lines or Sketch "
            "Hatching.",
    description=(
        "Spiral winds a bandage round an axis (Up, Front or Side, tipped by Tilt and Lean) between two heights, "
        "Overlap setting how much each turn covers the last (negative leaves skin showing between turns); more "
        "passes come back on a steeper pitch, crossing the first in X's. Bands are loops where planes meet the "
        "outer skin: meridians through the axis (2 = a parcel's cross), rings across it, or seeded planes; each "
        "runs a little past its start and its tails leave from there. Drape is a seeded walk over the skin, "
        "turning, crossing itself, drawn around the axis by Around. The ribbon rides on the outer skin; Tension "
        "lets it bridge hollows instead of dipping in; where it passes over itself it is lifted a layer and ramps "
        "over the edge below. The two tails leave at the first and last contact (Lift tilts them out), bend under "
        "Gravity, sway, twist and curl, slide over the body and lie on the floor; their tips are cut, tapered or "
        "frayed into threads. Fabric is geometry: lengthwise threads (silk), a woven grain (cotton, gauze), "
        "ripples at the edges and on the tails, and a thickness with rounded or pillowed (velvet) edges. Settle "
        "in Blender runs a cloth simulation on the result (Blender 4.2 or newer), the wrapped part held by Hold, "
        "the tails free to fall and fold."),
    inputs=Inputs("mesh", exclude=(r"(?i)_ribbon\.(stl|obj|ply)$",)),
    output=Output("{stem}_ribbon.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-ribbon",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("ribbons", "covered", "faces"),
    params=[
        Section(
            "Wrap",
            Choice("style", "Wrap", "helix", [("helix", "Spiral"), ("bands", "Bands"), ("drape", "Drape")],
                   segmented=True,
                   help="Spiral: a bandage wound round the axis. Bands: loops cut by planes (parcel ribbon, straps). "
                        "Drape: a loose seeded walk over the skin, like a scarf."),
            Float("width", "Width", 8.0, unit="% h", min=0.5, max=40.0, step=0.5, decimals=1, slider=True,
                  help="Ribbon width, % of the model's height."),
            Choice("axis", "Axis", "z", AXIS_CHOICES, segmented=True,
                   help="What the ribbon winds about (Spiral, Drape) or the bands are laid around: the model's "
                        "up, front-to-back or side-to-side axis."),
            Float("tilt", "Tilt", 0.0, unit="°", min=-80.0, max=80.0, step=5.0, decimals=1,
                  help="Tips the axis toward the front (about the side-to-side axis)."),
            Float("lean", "Lean", 0.0, unit="°", min=-80.0, max=80.0, step=5.0, decimals=1,
                  help="Tips the axis toward the side (about the front-to-back axis)."),
            Float("span_from", "From", 0.0, unit="%", min=0.0, max=100.0, step=1.0, decimals=1, slider=True,
                  visible_if=lambda v: not bands(("meridians", "random"))(v),
                  help="Where along the axis the wrap starts, % of the model's extent along it (0 = the base)."),
            Float("span_to", "To", 100.0, unit="%", min=0.0, max=100.0, step=1.0, decimals=1, slider=True,
                  visible_if=lambda v: not bands(("meridians", "random"))(v),
                  help="Where it ends: 0-100 wraps the whole model, a narrow band wraps a part of it."),
            Float("overlap", "Overlap", 30.0, unit="%", min=-300.0, max=90.0, step=5.0, decimals=1, slider=True,
                  visible_if=style("helix"),
                  help="How much each turn covers the one before, % of the width: 30 covers the model fully, 0 "
                       "lays turns edge to edge, negative leaves that much skin between turns."),
            Int("passes", "Passes", 1, min=1, max=6, visible_if=style("helix"),
                help="1 winds once from From to To; more come back and forth, crossing the first in X's."),
            Float("return_pitch", "Return pitch", 4.0, unit="×", min=1.0, max=20.0, step=0.5, decimals=1,
                  visible_if=lambda v: style("helix")(v) and int(v.get("passes", 1)) > 1, advanced=True,
                  help="Return passes wind this many times more steeply: the crossings open up."),
            Choice("handed", "Turns", "right", [("right", "Right-handed"), ("left", "Left-handed")],
                   segmented=True, visible_if=style("helix"), advanced=True),
            Float("wobble", "Hand-wound", 0.3, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=style("helix"),
                  help="Unevenness of a wrap made by hand: the pitch and the angle wander a little."),
            Choice("band_layout", "Bands", "meridians", [("meridians", "Meridians"), ("rings", "Rings"),
                                                         ("random", "Random")], segmented=True,
                   visible_if=style("bands"),
                   help="Meridians pass through the axis (2 = a parcel's cross, 3-4 a star); rings stand across "
                        "it, spread between From and To; random planes are seeded."),
            Int("count", "Count", 2, min=1, max=24, visible_if=style("bands", "drape"),
                help="How many bands, or how many drape ribbons."),
            Float("band_turn", "Turn bands", 0.0, unit="°", min=-180.0, max=180.0, step=5.0, decimals=1,
                  visible_if=bands(("meridians",)), help="Turns the meridians about the axis."),
            Float("start_angle", "Start at", 90.0, unit="°", min=-180.0, max=180.0, step=5.0, decimals=1,
                  visible_if=style("helix", "bands"), advanced=True,
                  help="Where the wrap starts round the axis (Bands: where the knot and tails are; 90 = the top "
                       "of a meridian, the back of a ring)."),
            Float("length", "Length", 6.0, unit="× h", min=0.5, max=60.0, step=0.5, decimals=1, slider=True,
                  visible_if=style("drape"), help="Length of each drape ribbon on the skin, in model heights."),
            Float("curviness", "Curviness", 0.5, min=0.0, max=2.0, step=0.05, decimals=2, slider=True,
                  visible_if=style("drape"), help="How much the drape turns as it goes."),
            Float("around", "Around", 0.5, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=style("drape"), help="How strongly the drape is drawn round the axis (1: it spirals, "
                                                  "0: it wanders)."),
            Float("tension", "Tension", 0.5, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="0 lies in every hollow of the skin; 1 is pulled taut, bridging eye sockets, the neck "
                       "under the chin, the gaps between limbs."),
            Float("gap", "Lift off the skin", 0.15, unit="% h", min=0.0, max=3.0, step=0.05, decimals=2,
                  advanced=True,
                  help="Clearance between skin and ribbon, so drawing tools see the ribbon cleanly over the skin."),
            Seed("seed", "Seed", 0, help="Hand-wound wobble, drape paths, random bands, tail sway and fraying."),
        ),
        Section(
            "Tails",
            Float("tail_start", "Start tail", 25.0, unit="% h", min=0.0, max=200.0, step=1.0, decimals=1,
                  slider=True, help="How much of the ribbon's start hangs free outside the form (0: none)."),
            Float("tail_end", "End tail", 40.0, unit="% h", min=0.0, max=200.0, step=1.0, decimals=1,
                  slider=True, help="How much of its end hangs free."),
            Float("tail_lift", "Lift", 25.0, unit="°", min=0.0, max=90.0, step=5.0, decimals=1, visible_if=has_tails,
                  help="How steeply the tails leave the skin: higher holds them clear of the form."),
            Float("gravity", "Gravity", 0.6, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=has_tails, help="0 tails stream straight out; 1 they turn down at once and hang."),
            Float("sway", "Sway", 0.4, min=0.0, max=1.0, step=0.05, decimals=2, slider=True, visible_if=has_tails,
                  help="Soft S-curves as the tails fall."),
            Float("twist", "Twist", 1.0, unit="turns", min=-6.0, max=6.0, step=0.25, decimals=2, visible_if=has_tails,
                  help="Turns of each tail about its own length (half turns show the back of the ribbon)."),
            Float("curl", "Curl", 0.3, min=0.0, max=1.0, step=0.05, decimals=2, slider=True, visible_if=has_tails,
                  help="Curls the last part of each tail into a spiral, like ribbon pulled over a blade."),
            Choice("tip", "Tips", "cut", [("cut", "Cut"), ("taper", "Taper"), ("fray", "Fray")], segmented=True,
                   visible_if=has_tails,
                   help="Cut square, narrowed to a point, or unravelled into loose threads."),
            Float("tip_length", "Tip length", 30.0, unit="% tail", min=2.0, max=100.0, step=1.0, decimals=1,
                  visible_if=tipped, help="Share of each tail that tapers or frays."),
            Bool("floor", "Rest on the floor", True, visible_if=has_tails,
                 help="Tails that reach the model's base lie on the ground instead of passing through it."),
            Float("tail_vary", "Vary lengths", 1.0, min=0.0, max=1.0, step=0.1, decimals=2, advanced=True,
                  visible_if=has_tails, help="Seeded ±15% on each tail's length (several bands look hand-tied)."),
        ),
        Section(
            "Fabric",
            Float("thickness", "Thickness", 0.35, unit="% h", min=0.05, max=4.0, step=0.05, decimals=2,
                  help="Silk 0.2-0.3, satin 0.35, cotton bandage 0.5, velvet ~1."),
            Float("softness", "Pillowed edges", 0.2, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="0: flat with rounded edges; 1: thick in the middle and thin at the edges (velvet)."),
            Int("threads", "Threads", 0, min=0, max=24,
                help="Lengthwise ridges across the width (silk, grosgrain); 0 = smooth."),
            Float("thread_depth", "Thread depth", 0.04, unit="% h", min=0.0, max=1.0, step=0.01, decimals=3,
                  visible_if=lambda v: int(v.get("threads", 0)) > 0),
            Float("weave", "Weave", 0.0, unit="% h", min=0.0, max=1.0, step=0.01, decimals=3,
                  help="Depth of a woven grain (cotton, gauze); 0 = none."),
            Float("weave_size", "Weave size", 0.8, unit="% h", min=0.1, max=10.0, step=0.1, decimals=2,
                  visible_if=lambda v: float(v.get("weave", 0)) > 0, help="Spacing of the weave."),
            Float("ripple", "Ripple", 0.2, unit="% h", min=0.0, max=5.0, step=0.05, decimals=2, slider=True,
                  help="Soft waves along the ribbon, strongest at its edges and on the free tails (silk)."),
            Float("ripple_length", "Ripple length", 6.0, unit="% h", min=0.5, max=50.0, step=0.5, decimals=1,
                  visible_if=lambda v: float(v.get("ripple", 0)) > 0),
        ),
        Section(
            "Blender cloth",
            Bool("cloth", "Settle in Blender", False,
                 help="Runs Blender's cloth simulation on the ribbon (Blender 4.2 or newer, in the background): the "
                      "tails fall, fold and drape for real. Slower."),
            Int("cloth_frames", "Frames", 40, min=5, max=400, visible_if=cloth,
                help="How long it settles (24 a second): more lets long tails come to rest."),
            Float("cloth_hold", "Hold", 0.9, min=0.0, max=1.0, step=0.05, decimals=2, slider=True, visible_if=cloth,
                  help="How firmly the wrapped part stays put (1: only the tails move; lower lets the wrap sag "
                       "and slip)."),
            Bool("self_collide", "Self collision", False, visible_if=cloth, advanced=True,
                 help="Keeps the ribbon from passing through itself while it settles (much slower)."),
        ),
        Section(
            "Output",
            Bool("ribbon_only", "Ribbon only", False, help="Leave the model out of the output mesh."),
        ),
        Section("Model", *frame_params(stage_sets=False)),
        Section(
            "Quality",
            Int("face_budget", "Face budget", 1_500_000, min=50_000, max=8_000_000, advanced=True,
                help="Most faces for the ribbon; past it the fabric is sampled more coarsely."),
            Int("draft_faces", "Preview faces", 60_000, min=2_000, max=2_000_000, advanced=True,
                help="Preview wraps a lighter copy of the model, with a coarser ribbon."),
            collapsed=True),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"width": 15.0, "tail_start": 20.0, "tail_end": 20.0, "draft_faces": 4000, "face_budget": 60000},
)
