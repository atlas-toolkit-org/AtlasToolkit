"""
Cubic Replicator: tile copies of a model on a 3D grid, flipped and turned at random (from "STL Cubic Replicator v2").

Outputs <name>_replicated_<x>x<y>x<z>.stl, signed atlas-toolkit:cubic-replicator so Atlas links them to this tool.
"""
from atlas_toolkit.core import Bool, Float, Inputs, Output, Section, Seed, Tool, Vec3


def _spacing(info):
    ext = getattr(info, "extents", None) or (1.0, 1.0, 1.0)
    return tuple(round(float(e) * 0.05, 4) for e in ext)


def _any_rotation(v):
    return bool(v.get("rot_x") or v.get("rot_y") or v.get("rot_z"))


TOOL = Tool(
    id="cubic-replicator",
    name="Cubic Replicator",
    version="3.1",
    category="mesh",
    icon="replicate",
    action="Replicate",
    summary="Tile copies of a model on a 3D grid, flipped and turned at random.",
    tagline="Grids of busts, crowds of heads: one model, many seeded variations.",
    description=(
        "Copies sit one bounding box (plus the spacing) apart. Each copy can be mirrored at random on the chosen "
        "axes and turned by up to ± the maximum angle around them. The seed makes a result repeatable. With "
        "“Space for turning” the step is the model's bounding sphere, so turned copies never touch."),
    inputs=Inputs("mesh", exclude=(r"_replicated_\d+x\d+x\d+\.(stl|obj|ply)$",)),
    output=Output("{stem}_replicated_{repeats}.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:cubic-replicator",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    columns=("faces",),
    params=[
        Section(
            "Grid",
            Vec3("repeats", "Copies", (3, 3, 1), min=1, max=100, integer=True,
                 help="Number of copies along X, Y and Z."),
            Vec3("spacing", "Spacing", "auto", unit="units", min=0, max=100000, step=0.5, decimals=3,
                 suggest=_spacing, help="Gap added between neighbouring copies, in model units. Auto: 5% of the "
                                        "model's size on each axis (v2's suggestion)."),
            Bool("rotation_safe", "Space for turning", False,
                 help="Step by the model's bounding sphere instead of its box, so randomly turned copies never "
                      "overlap."),
        ),
        Section(
            "Variation",
            Bool("rot_x", "Turn around X", False),
            Bool("rot_y", "Turn around Y", False),
            Bool("rot_z", "Turn around Z", False),
            Float("max_angle", "Max angle", 45.0, unit="°", min=0, max=180, step=5, decimals=1, slider=True,
                  visible_if=_any_rotation, help="Each turn is a random angle between minus and plus this."),
            Bool("flip_x", "Mirror on X at random", False),
            Bool("flip_y", "Mirror on Y at random", False),
            Bool("flip_z", "Mirror on Z at random", False),
            Seed("seed", "Seed", 1, help="Same seed, same result. Change it for another random arrangement."),
        ),
    ],
    test_values={"repeats": (2, 2, 1), "rot_z": True},
    legacy=("Python/cubic_replicator_v2.py", "STL Cubic Replicator v2.exe"),
)
