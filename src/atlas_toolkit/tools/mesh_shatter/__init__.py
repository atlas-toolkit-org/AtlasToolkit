"""
Mesh Shatter: break a model's surface into shards and blow them apart (from "Mesh shatter v3").

Outputs <name>_shattered_<K>pcs.obj, signed atlas-toolkit:mesh-shatter so Atlas links them to this tool.
"""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool


def some_connectors(v):
    return bool(v.get("connectors")) and (v.get("connector_percent", 100.) or 0.) > 0.


def partial_connectors(v):
    return some_connectors(v) and (v.get("connector_percent", 100.) or 0.) < 100.


def organic(v):
    return some_connectors(v) and v.get("connector_style") == "organic"


def has_branches(v):
    key = "connector_branches_max" if v.get("connector_branch_count") == "random" else "connector_branches"
    return organic(v) and (v.get(key) or 0) > 0


TOOL = Tool(
    id="mesh-shatter",
    name="Mesh Shatter",
    version="4.1",
    category="mesh",
    icon="shatter",
    action="Shatter",
    summary="Break a model's surface into shards and blow them apart, cracks following its creases.",
    tagline="Exploded shells of a sculpture, seeded and repeatable.",
    description=(
        "Seeds are spread over the surface; every face joins its nearest seed along the surface, crossing sharp "
        "edges reluctantly, so cracks follow the creases. Shards fly out from the centre by the ejection "
        "distance, with jitter, and can be turned, shifted and mirrored at random. Shards are open surface "
        "patches, not solids. Hub connectors tie a chosen share of them to one central point, with straight "
        "stems or a branching tree, and Shard thickness turns them into one fused solid."),
    inputs=Inputs("mesh", exclude=(r"_shattered_\d+pcs\.(stl|obj|ply)$",)),
    output=Output("{stem}_shattered_{num_pieces}pcs.obj", "mesh", formats=(".obj", ".stl", ".ply")),
    signature="atlas-toolkit:mesh-shatter",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("shards",),
    params=[
        Section(
            "Shards",
            Int("num_pieces", "Pieces", 25, min=2, max=300, slider=True, help="Number of shards (seeds)."),
            Float("edge_sensitivity", "Edge sensitivity", 1.0, min=0.0, max=3.0, step=0.1, decimals=2, slider=True,
                  help="How strongly sharp edges resist being crossed: higher makes cracks follow the creases."),
        ),
        Section(
            "Explosion",
            Float("ejection", "Ejection", 0.25, unit="×", min=0.0, max=2.5, step=0.05, decimals=2, slider=True,
                  help="How far shards fly out from the centre, as a share of the model's bounding-box diagonal."),
            Float("jitter", "Jitter", 0.15, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="Random extra displacement, as a share of the ejection distance."),
            Float("perturb", "Perturbation", 0.20, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="Random per-shard turn (up to 120° at 1), shift (up to 10% of the diagonal) and mirror "
                       "(up to 50% of shards). 0 turns it off."),
            Seed("seed", "Seed", 42, help="Same seed, same shards."),
        ),
        Section(
            "Hub connectors",
            Bool("connectors", "Connect shards to a hub", False,
                 help="Tie shards to one central point. Off keeps the free-floating result. Shards stay exactly where "
                      "they flew either way."),
            Float("connector_percent", "Connected shards", 100., unit="%", min=0., max=100., step=5., decimals=1,
                  slider=True, visible_if={"connectors": True},
                  help="Share of shards tied to the hub, rounded to a whole count. The others float free as debris."),
            Choice("connector_selection", "Choose shards", "random",
                   [("random", "Random"), ("largest", "Largest first"), ("smallest", "Smallest first"),
                    ("nearest", "Nearest first"), ("farthest", "Farthest first")], visible_if=partial_connectors,
                   help="Which shards are tied. Raising the percentage keeps the earlier choice and adds to it."),
            Seed("connector_seed", "Connection seed", 0,
                 visible_if=lambda v: partial_connectors(v) and v.get("connector_selection") == "random",
                 help="Rerolls only which shards are tied."),
            Choice("hub_position", "Hub position", "deep",
                   [("deep", "Deep inside"), ("centroid", "Surface centroid"), ("mass", "Centre of mass")],
                   visible_if=some_connectors,
                   help="Deep inside finds the roomiest point near the middle of a closed model, which is always "
                        "inside it. A centroid can fall outside a concave model. Surface centroid is the point the "
                        "shards flew away from."),
            Float("hub_offset_x", "Hub offset X", 0., unit="% diag", min=-100., max=100., step=1., decimals=1,
                  advanced=True, visible_if=some_connectors, help="Moves the hub, as a share of the bounding-box diagonal."),
            Float("hub_offset_y", "Hub offset Y", 0., unit="% diag", min=-100., max=100., step=1., decimals=1,
                  advanced=True, visible_if=some_connectors, help="Moves the hub, as a share of the bounding-box diagonal."),
            Float("hub_offset_z", "Hub offset Z", 0., unit="% diag", min=-100., max=100., step=1., decimals=1,
                  advanced=True, visible_if=some_connectors, help="Moves the hub, as a share of the bounding-box diagonal."),
            Choice("hub_core", "Hub core", "knot",
                   [("knot", "Gnarled knot"), ("sphere", "Clean sphere"), ("miniature", "Miniature of the model"),
                    ("none", "Nothing visible")], visible_if=some_connectors,
                   help="What sits where the connectors meet. The knot swells where limbs leave it. The miniature is a "
                        "small intact copy of the model."),
            Float("hub_size", "Hub size", 5., unit="% diag", min=.5, max=25., step=.5, decimals=1, slider=True,
                  visible_if=lambda v: some_connectors(v) and v.get("hub_core") != "none",
                  help="Radius of the core; a miniature is about twice as wide. Limited so it never swallows a shard."),
            Choice("connector_style", "Connector shape", "organic",
                   [("cylinder", "Cylinder"), ("bar", "Square bar"), ("organic", "Organic")],
                   visible_if=some_connectors, help="Cylinders and bars are straight stems, one per shard."),
            Choice("connector_network", "Network", "tree", [("tree", "Shared tree"), ("radial", "Radial stems")],
                   visible_if=organic,
                   help="Shared tree: a few trunks fork again and again, so one trunk feeds many shards. "
                        "Radial stems: one bowed stem per shard, a starburst."),
            Int("connector_trunks", "Trunks", 5, min=1, max=16, slider=True,
                visible_if=lambda v: organic(v) and v.get("connector_network") == "tree",
                help="How many limbs leave the hub. Each feeds the shards lying in its direction."),
            Float("connector_diameter_min", "Thickness minimum", 1.2, unit="% diag", min=.05, max=8., step=.1, decimals=2,
                  visible_if=some_connectors,
                  help="Diameter or bar side where a limb leaves the hub, drawn at random per limb up to the maximum. "
                       "Limited to fit the shard it ends on."),
            Float("connector_diameter_max", "Thickness maximum", 1.8, unit="% diag", min=.05, max=8., step=.1, decimals=2,
                  visible_if=some_connectors),
            Float("connector_taper", "Tip thickness", .45, unit="× base", min=.1, max=1., step=.05, decimals=2,
                  visible_if=organic, help="Thickness where a limb reaches its shard, relative to where it set off."),
            Float("connector_curve", "Curvature", .45, min=0., max=1., step=.05, decimals=2, slider=True,
                  visible_if=organic, help="How far limbs bow. 0 gives straight limbs."),
            Float("connector_irregularity", "Irregularity", .35, min=0., max=1., step=.05, decimals=2, slider=True,
                  visible_if=organic, help="Seeded wandering and uneven thickness along each limb."),
            Float("connector_fork", "Branch split", 55., unit="% length", min=20., max=80., step=5., decimals=1,
                  visible_if=lambda v: organic(v) and (v.get("connector_network") == "tree" or (
                      has_branches(v) and v.get("connector_branch_placement", "split") == "split")),
                  help="How far along a limb it forks. Lower forks early into a wide crown; higher keeps long trunks "
                       "with forks near the shards."),
            Choice("connector_branch_count", "Branch count", "fixed", [("fixed", "Fixed"), ("random", "Random range")],
                   visible_if=organic, help="One count of extra branches for all shards, or a seeded draw per shard."),
            Int("connector_branches", "Extra branches", 0, min=0, max=100,
                visible_if=lambda v: organic(v) and v.get("connector_branch_count", "fixed") != "random",
                help="Finer limbs per connected shard, spreading over it like roots gripping a stone."),
            Int("connector_branches_min", "Branches minimum", 1, min=0, max=100,
                visible_if=lambda v: organic(v) and v.get("connector_branch_count") == "random",
                help="Fewest extra branches drawn for a shard."),
            Int("connector_branches_max", "Branches maximum", 6, min=0, max=100,
                visible_if=lambda v: organic(v) and v.get("connector_branch_count") == "random",
                help="Most extra branches drawn for a shard."),
            Choice("connector_branch_placement", "Branch origins", "split",
                   [("split", "At split position"), ("beginning", "Beginning"), ("middle", "Middle"), ("end", "End"),
                    ("random", "Along length"), ("mixed", "Mixed zones")], visible_if=has_branches,
                   help="Where along its stem an extra branch starts."),
            Int("connector_branch_levels", "Branch levels", 1, min=1, max=4, visible_if=has_branches,
                help="1 grows every extra branch from the stem; 2-4 allow smaller twigs from earlier branches."),
            Float("connector_spread", "Branch spread", 30., unit="% shard width", min=0., max=80., step=5., decimals=1,
                  visible_if=has_branches, help="How widely extra branches land across their shard."),
            Float("connector_branch_thickness", "Branch thickness", .68, unit="× parent", min=.15, max=1., step=.05,
                  decimals=2, visible_if=has_branches, help="An extra branch's thickness where it leaves its parent."),
            Float("connector_extent", "Maximum bow", 35., unit="% length", min=0., max=100., step=5., decimals=1,
                  visible_if=lambda v: organic(v) and (v.get("connector_network") == "radial" or has_branches(v)),
                  help="Limit on how far a radial stem or an extra branch strays from its straight line."),
            Seed("connector_branch_seed", "Branch seed", 0, visible_if=has_branches,
                 help="Rerolls extra-branch counts and origins only."),
            Float("shard_thickness", "Shard thickness", 0., unit="% diag", min=0., max=5., step=.05, decimals=2,
                  slider=True, visible_if=some_connectors,
                  help="0 keeps shards as open surfaces, with connectors touching them: not printable. Above 0 every "
                       "shard becomes a solid fragment and the connected ones fuse with the hub into one piece."),
            Int("connector_segments", "Curve segments", 12, min=6, max=48, advanced=True, visible_if=some_connectors,
                help="Sides of a round connector."),
            Int("max_faces", "Maximum faces", 3_000_000, min=10_000, max=8_000_000, step=50_000, advanced=True,
                visible_if=some_connectors, help="The run stops, publishing nothing, if the result would exceed this."),
        ),
        Section(
            "Extra files",
            Bool("separate", "Also save every shard", False,
                 help="Write each shard to its own file in a <name>_shards_<K>pcs folder."),
        ),
    ],
    test_values={"num_pieces": 6},
    legacy=("Python/mesh_shatter_gui", "Mesh shatter v3.exe"),
)
