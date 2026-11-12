"""Surface Eject: remove real surface volumes and float them along their outward normals."""
from atlas_toolkit.core import (Bool, Choice, FileRef, Float, Inputs, Int, Output, Section, Seed, Text,
                                Tool, frame_params)


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
    id="mesh-surface-eject", name="Surface Eject", version="0.3.2", category="mesh", icon="surface-eject",
    action="Eject", summary="Lift actual pieces of a model's surface out of matching cavities, along their outward normals.",
    tagline="A recognizable form, crisp missing patches and floating fragments of its own skin.",
    description=("Intersect surface-aligned solid samplers with the remaining model, keep the actual sampled "
                 "volume, and translate it along the area-weighted outward normal of its original skin. "
                 "Pieces retain their orientation. Fully detach clears the original body and earlier pieces, "
                 "then guarantees at least the requested gap. Choose the whole surface, a height band, or paint "
                 "a specific area in the Selection tab's modal editor. Samples touching unselected source faces "
                 "are rejected. Cube and sphere penetration must be smaller than their size; Box, Ellipsoid and "
                 "Cylinder allow independent depth. Exact cuts require a closed solid; resampling repair is opt-in. "
                 "Optional cylinders, square bars or curved/forked organic stems join a chosen percentage of fragments "
                 "back to their own cavities. Seeded random, largest-first or smallest-first selection; remaining "
                 "pieces stay floating. Controls bound thickness, taper, curvature, branch count, split position, spread "
                 "and maximum lateral extent. Up to 100 extra branches, fixed or randomly drawn per fragment; "
                 "origins near the cavity, middle, fragment, randomly along the stem, or mixed, with optional "
                 "secondary branching. Selected joints are fused; other fragments remain floating. Separate output retains body, fragments "
                 "and connectors as independent files in the same coordinates. No Blender is needed."),
    inputs=Inputs("mesh", exclude=(r"(?i)_surface_eject(?:_(?:parts|connectors)(?:-\d+)?)?\.(stl|obj|ply)$",)),
    output=Output("{stem}_surface_eject.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-surface-eject", engine=".engine:run", runs_in="process", preview="mesh",
    viewer=".viewer:SelectionView", sample=".engine:make_sample", columns=("pieces", "removed", "faces"),
    preset_inputs=("region", "band_from", "band_to", "selection", "selection_file", "repair"),
    params=[
        Section("Sampling",
            Choice("shape", "Sampler", "cube", [("cube", "Cube"), ("box", "Box"), ("sphere", "Sphere"),
                   ("ellipsoid", "Ellipsoid"), ("cylinder", "Cylinder")],
                   help="The sampler is intersected with the model; the exported piece is actual model material. Box, Ellipsoid and Cylinder allow arbitrary depth relative to width."),
            Int("count", "Pieces", 24, min=1, max=300,
                help="Requested successful extractions. Spacing, region, thin walls or available surface may leave fewer; the report explains rejections."),
            Float("size_min", "Size minimum", 7., unit="% h", min=.2, max=80., step=.5, decimals=2,
                  help="Cube/box side width; sphere/ellipsoid/cylinder diameter. Percent of original model height."),
            Float("size_max", "Size maximum", 13., unit="% h", min=.2, max=80., step=.5, decimals=2),
            Float("depth_min", "Depth minimum", 2., unit="% h", min=.1, max=80., step=.5, decimals=2,
                  help="Deepest sampler penetration below its surface anchor, along the inward normal. Cube and sphere require Depth maximum < Size minimum."),
            Float("depth_max", "Depth maximum", 5., unit="% h", min=.1, max=80., step=.5, decimals=2),
            Float("spacing", "Footprint spacing", 1.5, unit="% h", min=0., max=30., step=.25, decimals=2,
                  help="Conservative spacing between surface sampling footprints; deep cuts can still meet inside the body. Zero allows overlapping cutters; each extraction uses only material still in the body."),
            Seed("seed", "Seed", 42)),
        Section("Ejection",
            Bool("fully_detach", "Fully detach", True,
                 help="Clear the original model and earlier fragments, preserving orientation, then leave at least the randomly chosen gap. Concavities can require more travel."),
            Float("distance_min", "Gap minimum", 5., unit="% h", min=0., max=200., step=.5, decimals=2,
                  help="With Fully detach: minimum clearance to body and other fragments. Otherwise: translation from the original position, which may leave pieces embedded."),
            Float("distance_max", "Gap maximum", 12., unit="% h", min=0., max=200., step=.5, decimals=2)),
        Section("Connectors",
            Bool("connectors", "Connect fragments to cavities", False,
                 help="Off preserves the floating-piece result. On connects the chosen percentage to matching cavities. Combined export fuses selected joints and retains other fragments as separate solids. Percentages above zero require Fully detach."),
            Float("connector_percent", "Connected fragments", 100., unit="%", min=0., max=100., step=5., decimals=1,
                  slider=True, visible_if={"connectors": True},
                  help="Percentage of successfully extracted fragments to connect, rounded to the nearest whole count (half rounds up). 100 connects all; 0 creates no connectors. Changing this does not alter sampling or ejection."),
            Choice("connector_selection", "Choose fragments", "random", [("random", "Random"),
                   ("largest", "Largest first"), ("smallest", "Smallest first")], visible_if=partial_connectors,
                   help="Random chooses an exact rounded count using Connection seed. Largest/Smallest use actual extracted volume. Increasing the percentage keeps the previous selection and adds more pieces."),
            Seed("connector_seed", "Connection seed", 0,
                 visible_if=lambda v: partial_connectors(v) and v.get("connector_selection") == "random",
                 help="Rerolls only which fragments are connected. Sampling, ejection and connector shape randomness keep their existing Seed."),
            Choice("connector_style", "Connector shape", "cylinder", [("cylinder", "Cylinder"),
                   ("bar", "Square bar"), ("organic", "Organic")], visible_if=some_connectors),
            Float("connector_diameter_min", "Thickness minimum", .6, unit="% h", min=.05, max=8., step=.1, decimals=2,
                  visible_if=some_connectors, help="Round diameter or square bar side. Drawn independently for each piece; limited to fit the cavity and fragment safely. Equal endpoints give one thickness."),
            Float("connector_diameter_max", "Thickness maximum", 1., unit="% h", min=.05, max=8., step=.1, decimals=2,
                  visible_if=some_connectors),
            Float("connector_taper", "Tip thickness", .55, unit="× base", min=.15, max=1., step=.05, decimals=2,
                  visible_if=lambda v: some_connectors(v) and v.get("connector_style") == "organic",
                  help="Organic stem diameter at the fragment relative to its base; forks are finer still."),
            Float("connector_curve", "Curvature", .35, min=0., max=1., step=.05, decimals=2, slider=True,
                  visible_if=lambda v: some_connectors(v) and v.get("connector_style") == "organic",
                  help="Lateral bow within Maximum extent, also limited by stem length to keep bends gentle. Zero gives straight paths."),
            Float("connector_irregularity", "Irregularity", .15, min=0., max=1., step=.05, decimals=2, slider=True,
                  visible_if=lambda v: some_connectors(v) and v.get("connector_style") == "organic",
                  help="Seeded gentle variation in direction and thickness, within the same extent limit."),
            Choice("connector_branch_count", "Branch count", "fixed", [("fixed", "Fixed"), ("random", "Random range")],
                   visible_if=organic, help="One extra-branch count, or an independent inclusive integer draw per connected fragment. Main trunks are not counted."),
            Int("connector_branches", "Extra branches", 0, min=0, max=100,
                visible_if=lambda v: organic(v) and v.get("connector_branch_count", "fixed") != "random",
                help="Requested extra branches per connected fragment, up to 100. Dense branching uses finer tips; room and face budgets can limit achieved counts."),
            Int("connector_branches_min", "Branches minimum", 1, min=0, max=100,
                visible_if=lambda v: organic(v) and v.get("connector_branch_count") == "random",
                help="Smallest inclusive extra-branch count drawn independently for each connected fragment."),
            Int("connector_branches_max", "Branches maximum", 8, min=0, max=100,
                visible_if=lambda v: organic(v) and v.get("connector_branch_count") == "random",
                help="Largest inclusive count, up to 100. Equal endpoints give a fixed count; zero permits bare stems."),
            Choice("connector_branch_placement", "Branch origins", "split", [("split", "At split position"),
                   ("beginning", "Beginning"), ("middle", "Middle"), ("end", "End"),
                   ("random", "Along length"), ("mixed", "Mixed zones")], visible_if=has_branches,
                   help="New modes place roots along the visible parent stem: Beginning 5–30%, Middle 35–65%, End 70–95%, Random 5–95%. Mixed balances all three zones. At split position preserves previous presets."),
            Float("connector_fork", "Branch split", 55., unit="% length", min=20., max=80., step=5., decimals=1,
                  visible_if=lambda v: has_branches(v) and v.get("connector_branch_placement", "split") == "split",
                  help="Where forks leave the trunk, measured from the cavity. Higher values keep branches shorter."),
            Int("connector_branch_levels", "Branch levels", 1, min=1, max=4, visible_if=has_branches,
                help="Maximum generation: 1 grows every fork from the main trunk; 2–4 allow smaller twigs from earlier branches. Extra branches counts all generations together."),
            Seed("connector_branch_seed", "Branch seed", 0,
                 visible_if=lambda v: organic(v) and (v.get("connector_branch_count") == "random" or
                     (has_branches(v) and (v.get("connector_branch_placement", "split") != "split" or (v.get("connector_branch_levels") or 1) > 1))),
                 help="Rerolls branch counts, distributed origins and parent choices without resampling/ejecting fragments or changing the connection subset."),
            Float("connector_spread", "Branch spread", 25., unit="% piece width", min=0., max=80., step=5., decimals=1,
                  visible_if=has_branches,
                  help="Desired spread of the attachment tips across the fragment, capped by Maximum extent and available interior space."),
            Float("connector_branch_thickness", "Branch thickness", .68, unit="× parent", min=.15, max=1., step=.05, decimals=2,
                  visible_if=has_branches,
                  help="Extra-branch root radius relative to its parent at the junction. Dense sets and deeper levels become finer to fit; tip taper still applies."),
            Float("connector_extent", "Maximum extent", 35., unit="% piece width", min=0., max=100., step=5., decimals=1,
                  visible_if=lambda v: some_connectors(v) and v.get("connector_style") == "organic",
                  help="Hard bound on all stem/branch centreline offsets from the direct cavity-to-fragment line. Rod thickness is additional. Zero makes a straight, unforked stem.")),
        Section("Selection",
            Choice("region", "Extract from", "whole", [("whole", "Whole surface"), ("band", "Height band"),
                   ("paint", "Painted area")], help="Painted area: open the Selection tab and Paint / select area. In Workflows use Aim in tool, paint, then Back to workflow."),
            Float("band_from", "Band from", 40., unit="% h", min=0., max=100., step=1., decimals=1,
                  visible_if={"region": "band"}),
            Float("band_to", "Band to", 80., unit="% h", min=0., max=100., step=1., decimals=1,
                  visible_if={"region": "band"}, help="Cutters are clipped at these heights; cavities stay inside the band."),
            FileRef("selection_file", "Selection file", "", exts=(".json",), visible_if={"region": "paint"},
                    help="Optional exported selection JSON for CLI or Workflows. Overrides the embedded selection. Bound to the exact source mesh."),
            Text("selection", "Embedded selection", "", advanced=True, visible_if={"region": "paint"},
                 help="Written by the selection modal, persisted with settings and Workflows. Mesh fingerprint and compressed selected face ranges.")),
        Section("Output",
            Choice("export_mode", "Export", "combined", [("combined", "Body + ejected parts"),
                   ("separate", "Separate body and parts")],
                   help="Combined fuses connected pieces to the body and retains unselected floating pieces. Separate writes the cavity body, _parts and (if any connections exist) _connectors. At 0% there is no empty connector file. All retain common placement; Workflow Save retains every file.")),
        Section("Quality",
            Choice("repair", "Open input", "strict", [("strict", "Require closed solid"),
                   ("resample", "Repair by resampling")], help="Repair changes the source surface and may soften detail. To paint a repaired input, repair/export it first and then paint that closed model."),
            Int("repair_resolution", "Repair resolution", 128, min=64, max=320, step=16,
                visible_if={"repair": "resample"}, advanced=True),
            Int("segments", "Curved shape segments", 32, min=12, max=96, step=4, advanced=True,
                help="Sampler quality and round connector cross-sections (connectors use at most 48 segments)."),
            Int("max_faces", "Maximum faces", 1_000_000, min=10_000, max=2_000_000, step=50_000, advanced=True,
                help="Hard input/output face budget; failure publishes nothing. Preview computes the same final geometry as Run.")),
        Section("Model frame", *frame_params(stage_sets=False)),
    ],
    test_values={"count": 4, "segments": 16},
)
