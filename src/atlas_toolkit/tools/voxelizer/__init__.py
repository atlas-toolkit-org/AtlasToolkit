"""
Voxelizer: rebuild a model from cubes, spheres or cylinders (from "Voxelizer tool v1").

Outputs <name>_vox.stl, signed atlas-toolkit:voxelizer so Atlas links them to this tool.
"""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Tool

TOOL = Tool(
    id="voxelizer",
    name="Voxelizer",
    version="2.0",
    category="mesh",
    icon="voxel",
    action="Voxelize",
    summary="Rebuild a model from cubes, spheres or cylinders on a voxel grid.",
    tagline="Blocky, beaded or columnar versions of a model, ready to slice or hatch.",
    description=(
        "The surface is voxelized on a grid; with “Fill the inside” the interior too (v1 always made a hollow "
        "shell). Each voxel becomes the chosen shape, shrunk by the gap. Sizes are in the model's own units, or "
        "set by how many voxels fit across the longest side."),
    inputs=Inputs("mesh", exclude=(r"_vox\.(stl|obj|ply)$",)),
    output=Output("{stem}_vox.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:voxelizer",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("faces",),
    params=[
        Section(
            "Grid",
            Choice("size_by", "Size by", "count", [("count", "Voxels across"), ("edge", "Edge length")],
                   segmented=True, help="Voxels across: so many voxels fit along the longest side, whatever the "
                                        "model's size. Edge length: a fixed edge in model units."),
            Int("across", "Voxels across", 50, min=2, max=1000, visible_if={"size_by": "count"}, slider=True,
                help="Voxels along the longest side of the model (v1's suggestions were 20, 50 and 100)."),
            Float("edge", "Edge", 1.0, unit="units", min=0.0001, max=10000, step=0.1, decimals=4,
                  visible_if={"size_by": "edge"}, help="Voxel edge in model units (usually mm)."),
            Float("gap", "Gap", 0.0, unit="%", min=0.0, max=90.0, step=5, decimals=1, slider=True,
                  help="Space left around each voxel, as a share of the edge: 20% makes 0.8-edge shapes."),
        ),
        Section(
            "Shape",
            Choice("shape", "Shape", "cube", [("cube", "Cube"), ("sphere", "Sphere"), ("cylinder", "Cylinder")],
                   segmented=True),
            Int("sphere_detail", "Sphere detail", 2, min=1, max=4, visible_if={"shape": "sphere"},
                help="Icosphere subdivisions: 1 = 80 faces, 2 = 320, 3 = 1280 per sphere."),
            Int("sections", "Cylinder sides", 32, min=6, max=96, visible_if={"shape": "cylinder"}),
            Bool("fill", "Fill the inside", False,
                 help="Fill the interior with voxels too. Off, only the surface is voxelized (a shell), as in v1."),
        ),
    ],
    test_values={"across": 8},
    legacy=("Python/voxelizer-tool.py", "Voxelizer tool v1.exe"),
)
