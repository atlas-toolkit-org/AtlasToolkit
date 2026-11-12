"""Solid volume studies for pen drawings. Signed meshes: atlas-toolkit:voxelize-fill."""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, Vec3


TOOL = Tool(
    id="voxelize-fill", name="Voxelize Fill", version="0.1.0", category="mesh",
    icon="voxel-fill", action="Voxelize",
    summary="Fill a mesh with volumetric blocks, then expose, separate or erode its interior.",
    tagline="Solid cubes, adaptive blocks and long bars for architectural pen drawings.",
    description=(
        "Classifies cell centres throughout the model's volume, including its interior. Separate blocks keep "
        "every cube; Joined volume exports only the occupied volume's boundary. Cutaways reveal the interior; "
        "exploded layers separate filled slabs; depth bands and seeded erosion remove selected cells. Adaptive "
        "blocks merge complete groups of eight, while directional bars join occupied runs. OBJ/PLY retain square "
        "faces for Plotter Vision's Remeshed network preset. STL triangulates them: use Features or Clean. "
        "Open scans use an inferred winding-number interior, with a warning; strict mode requires a closed mesh. "
        "Sizes and placement stay in source units. Preview computes the same geometry as the final build."),
    inputs=Inputs("mesh", exclude=(r"_voxel-fill\.(stl|obj|ply)$",)),
    output=Output("{stem}_voxel-fill.obj", "mesh", formats=(".obj", ".ply", ".stl"),
                  format_notes={".stl": "STL triangulates square faces. Draw with Plotter Vision Features or Clean.",
                                ".obj": "Native square faces; use Remeshed network to draw the cell grid.",
                                ".ply": "Native square faces; use Remeshed network to draw the cell grid."}),
    signature="atlas-toolkit:voxelize-fill", engine=".engine:run", runs_in="process", preview="mesh",
    columns=("voxels", "blocks"), sample=".engine:make_sample", test_values={"across": 8},
    params=[
        Section("Volume",
            Choice("size_by", "Size by", "count", [("count", "Count"), ("edge", "Edge")],
                   segmented=True, help="Count uses Voxels across; Edge uses a fixed cell edge in model units."),
            Int("across", "Voxels across", 32, min=2, max=256, slider=True,
                visible_if={"size_by": "count"},
                help="Cells across the longest side. Higher values increase the full 3D grid, not just the skin."),
            Float("edge", "Cell edge", 1., min=0.0001, max=1e9, decimals=4, unit="units",
                  visible_if={"size_by": "edge"}),
            Choice("input_policy", "Input", "infer", [("infer", "Infer scan interior"), ("closed", "Closed mesh only")],
                   help="Winding numbers classify the interior. Open scans are inferred, never silently treated as a shell. "
                        "Oriented nested shells preserve cavities; missing surfaces cannot be recovered exactly."),
        ),
        Section("Blocks",
            Choice("geometry", "Geometry", "separate", [("separate", "Blocks"), ("joined", "Joined")],
                   segmented=True, help="Joined volume removes internal faces and uses full cells without gaps."),
            Choice("layout", "Block layout", "cubes", [("cubes", "Uniform cubes"), ("adaptive", "Adaptive cubes"),
                                                         ("runs", "Directional bars")],
                   visible_if={"geometry": "separate"}),
            Float("gap", "Block gap", 0., min=0., max=80., step=2., decimals=1, unit="%", slider=True,
                  visible_if={"geometry": "separate"},
                  help="An absolute clearance based on the smallest cell, also between adaptive blocks and bars. "
                       "Zero fills the occupied volume completely; touching separate blocks retain internal faces."),
            Int("adaptive_levels", "Merge levels", 3, min=1, max=6,
                visible_if=lambda v: v.get("geometry") == "separate" and v.get("layout") == "adaptive",
                help="Merge complete aligned 2x2x2 groups recursively; 3 levels allow blocks 8 cells wide."),
            Choice("bar_axis", "Bar direction", "z", [("x", "X"), ("y", "Y"), ("z", "Z")], segmented=True,
                   visible_if=lambda v: v.get("geometry") == "separate" and v.get("layout") == "runs"),
        ),
        Section("Presentation",
            Choice("effect", "Effect", "full", [("full", "Full volume"), ("explode", "Exploded layers"),
                                                   ("bands", "Depth bands"), ("erode", "Erosion front")]),
            Choice("axis", "Direction", "z", [("x", "X"), ("y", "Y"), ("z", "Z")], segmented=True,
                   visible_if={"effect": ("explode", "erode")}, help="Axes refer to the input model's coordinates."),
            Int("layer_cells", "Cells per layer", 3, min=1, max=128, visible_if={"effect": "explode"}),
            Float("separation", "Layer separation", 150., min=0., max=1000., unit="%", step=10.,
                  visible_if={"effect": "explode"}, help="Extra distance between layers. All occupied cells are retained."),
            Int("band_cells", "Cells per band", 2, min=1, max=64, visible_if={"effect": "bands"},
                help="Alternate distance-to-boundary bands are removed. Use a cutaway to expose nested shells."),
            Float("erosion", "Erosion", 45., min=0., max=100., unit="%", slider=True, visible_if={"effect": "erode"}),
            Float("roughness", "Front roughness", 60., min=0., max=100., unit="%", slider=True,
                  visible_if={"effect": "erode"}),
            Seed("seed", "Seed", 1, visible_if={"effect": "erode"}),
        ),
        Section("Cutaway",
            Bool("cutaway", "Expose the interior", False,
                 help="Remove cells on one side of a plane. Can be combined with every effect and block layout."),
            Vec3("cut_normal", "Plane normal", (1., 0., 0.), min=-1., max=1., step=0.1, decimals=3,
                 visible_if={"cutaway": True}, help="Direction perpendicular to the cutting plane; cannot be zero."),
            Float("cut_position", "Plane position", 50., min=0., max=100., unit="%", slider=True,
                  visible_if={"cutaway": True}, help="0–100% of the grid bounds projected along the plane normal."),
            Choice("cut_side", "Keep", "lower", [("lower", "Lower side"), ("upper", "Upper side")],
                   visible_if={"cutaway": True}, segmented=True),
        ),
    ],
)
