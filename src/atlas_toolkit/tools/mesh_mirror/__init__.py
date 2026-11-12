"""Mirror meshes through a manually placed plane. Signed outputs: atlas-toolkit:mesh-mirror."""
from atlas_toolkit.core import Choice, Float, Inputs, Output, Section, Tool, Vec3


TOOL = Tool(
    id="mesh-mirror", name="Mirror", version="0.1.0", category="mesh", icon="mirror-plane", action="Mirror",
    summary="Reflect a model across a plane, with or without the original.",
    tagline="Place the plane; see the reflection move with it.",
    description=("The Plane tab shares Plane Cut's screen-line drawing, XYZ arrows, rotation rings, normal slide, "
                 "axis alignment, orthographic views and numeric controls. Expand opens a maximizable editor. "
                 "The default plane sits on the model's +X side, making an adjacent reflected copy. Other side "
                 "moves the plane to the opposite side of the model centre. Keep original + copy or export only "
                 "the mirrored model. Parts are not cut, welded or unioned. Combined or separate signed STL/OBJ/PLY "
                 "outputs retain model units, surface detail and correct reflected triangle winding."),
    inputs=Inputs("mesh"), output=Output("{stem}_mirror.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-mirror", engine=".engine:run", viewer=".viewer:MirrorView",
    runs_in="process", preview="mesh", columns=("copies",),
    params=[
        Section("Mirror",
            Choice("result_mode", "Result", "copy", [("copy", "Original + mirrored copy"),
                                                          ("mirror", "Mirrored model only")]),
            Choice("export_mode", "Copy output", "combined", [("combined", "One combined mesh"),
                                                                 ("separate", "Two separate files")],
                   visible_if={"result_mode": "copy"}, help="Separate: the main file is the mirrored model and "
                   "an _original sibling contains the original in its current position. Combined keeps both as "
                   "unwelded shells; downstream importers may weld touching vertices."),
        ),
        Section("Plane",
            Choice("reference", "Position relative to", "side", [("side", "Model +X side"),
                  ("center", "Model bounds centre"), ("world", "World origin")],
                  help="Model +X side starts at the right edge of the input bounds. The reference point stays "
                  "fixed while you rotate the plane. Centre-relative placement repeats across a batch."),
            Vec3("position", "Plane position", (0., 0., 0.), min=-1e12, max=1e12, decimals=6, unit="units",
                 help="Pivot displacement from the selected reference, in original model units."),
            Vec3("rotation", "Plane rotation", (0., 90., 0.), min=-360., max=360., decimals=6, step=1., unit="°",
                 help="XYZ Euler angles: X, then Y, then Z. Default (0, 90, 0) mirrors across a YZ plane."),
            Float("offset", "Slide along normal", 0., min=-1e12, max=1e12, decimals=6, unit="units",
                  help="Move the plane perpendicular to itself; the reflected copy moves twice this distance."),
        ),
    ],
    preset_skip=("reference", "position", "rotation", "offset"),
    sample=".engine:make_sample",
)
