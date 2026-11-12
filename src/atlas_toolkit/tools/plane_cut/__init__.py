"""Manual plane cutting. Signed meshes: atlas-toolkit:plane-cut."""
from atlas_toolkit.core import Choice, Float, Inputs, Output, Section, Tool, Vec3

TOOL = Tool(
    id="plane-cut", name="Plane Cut", version="0.1.1", category="mesh", icon="plane-cut", action="Cut",
    summary="Draw and position a cutting plane, discard one side or keep both pieces.",
    tagline="Draw the plane, refine its position, choose what stays.",
    description=("In the Plane tab, draw a line through the current view, then drag the XYZ arrows or rotation rings. "
                 "The plane extends through the entire model, beyond the drawn line. Numeric position, rotation and "
                 "normal offset give precise control. Cut keeps the chosen side; Slice keeps both. No fill leaves "
                 "the cut open; Minimal triangulates it; Remeshed refines cap interiors to the source edge density; "
                 "Fixed uses your cap edge size. Existing surface triangles away from the cut are retained. "
                 "Preview computes the final caps without saving. Original files and model units are preserved."),
    inputs=Inputs("mesh"),
    output=Output("{stem}_plane-cut.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:plane-cut", engine=".engine:run", viewer=".viewer:PlaneView",
    runs_in="process", preview="mesh", columns=("pieces",),
    params=[
        Section("Cut",
            Choice("cut_type", "Cut type", "cut", [("cut", "Cut · discard one side"),
                                                     ("slice", "Slice · keep both")]),
            Choice("side", "Kept / primary side", "positive", [("positive", "Along normal (+)"),
                                                                  ("negative", "Opposite normal (−)")]),
            Choice("fill", "Fill type", "remeshed", [("none", "No fill"), ("minimal", "Minimal fill"),
                                                       ("remeshed", "Remeshed fill"), ("fixed", "Fixed fill")]),
            Float("cap_size", "Cap edge size", 1.0, min=0.000001, max=1e9, decimals=6, unit="units",
                  visible_if={"fill": "fixed"}, help="Target interior cap edge length. Boundary edges stay fixed; "
                  "refinement is limited to 200 000 cap faces per piece."),
            Choice("export_mode", "Slice output", "combined", [("combined", "One combined mesh"),
                                                                   ("separate", "Two separate files")],
                   visible_if={"cut_type": "slice"}, help="Separate files keep both halves in their original "
                   "positions. Combined closed halves touch at the cut; other software may weld touching vertices."),
            Float("separation", "Slice separation", 0.0, min=0.0, max=1e9, decimals=6, unit="units",
                  visible_if={"cut_type": "slice"}, help="Optional gap along the plane normal, half on each side. "
                  "Zero preserves both halves' original coordinates."),
        ),
        Section("Plane",
            Choice("reference", "Position relative to", "center", [("center", "Model bounds centre"),
                                                                        ("world", "World origin")],
                   help="Centre-relative positions repeat the same placement across a batch of models."),
            Vec3("position", "Plane position", (0.0, 0.0, 0.0), min=-1e12, max=1e12, decimals=6,
                 unit="units", help="Pivot position in model coordinates, relative to the chosen reference."),
            Vec3("rotation", "Plane rotation", (0.0, 0.0, 0.0), min=-360.0, max=360.0, decimals=6, step=1.0,
                 unit="°", help="XYZ Euler angles in degrees: rotate about X, then Y, then Z. Zero gives the XY plane."),
            Float("offset", "Slide along normal", 0.0, min=-1e12, max=1e12, decimals=6, unit="units",
                  help="Move the plane perpendicular to itself without changing its orientation."),
        ),
    ],
    preset_skip=("reference", "position", "rotation", "offset"),
    sample=".engine:make_sample", test_values={"fill": "minimal"},
)
