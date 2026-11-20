"""Repair Mesh: make a damaged model a clean, closed solid the sculpting tools accept. Signed outputs:
atlas-toolkit:mesh-repair."""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Tool

TOOL = Tool(
    id="mesh-repair", name="Repair Mesh", version="1.0.1", category="mesh", icon="mesh-repair", action="Repair",
    summary="Fix the mesh errors that stop the sculpting tools: holes, debris, non-manifold edges, flipped faces.",
    tagline="Run it once on a damaged model; every tool then takes the result as it is.",
    description=(
        "Many scans and decimated models are not clean solids: back-to-back faces left by decimation, loose crumbs, "
        "open holes, edges shared by more than two faces, parts that touch at a single point, faces wound the wrong "
        "way. Tools that cut or grow a solid (Motion Sculpture, Crumble, Stair Sculpt, Surface Eject, Pipe Fill, "
        "Surface Stripes, Developable Surfaces…) stop on them. Repair Mesh finds them, fixes them without moving "
        "the surface where it can (removing debris, separating non-manifold edges, winding faces outward, filling "
        "holes), and only as a last resort rebuilds the model by resampling, which closes anything but softens fine "
        "detail. The report lists every problem before and after, and which tools the result is ready for. The "
        "Problems view marks open, non-manifold and wrongly wound edges in red."),
    inputs=Inputs("mesh"),
    output=Output("{stem}_repaired.ply", "mesh", formats=(".ply", ".stl", ".obj")),
    signature="atlas-toolkit:mesh-repair", engine=".engine:run", inspect=".engine:inspect", runs_in="process",
    preview="mesh", columns=("problems", "fixed"),
    params=[
        Section("Goal",
            Choice("goal", "Make it", "solid", [("solid", "A closed solid"), ("manifold", "A clean surface")],
                   segmented=True,
                   help="A closed solid is what the sculpting tools need. A clean surface keeps openings (a mask, "
                        "a relief) and fixes everything else, for Developable Surfaces and the drawing tools."),
            Choice("strategy", "Method", "auto", [
                ("auto", "Repair, rebuild only if needed"), ("repair", "Repair only, never rebuild"),
                ("rebuild", "Always rebuild")],
                help="Repair keeps every vertex where it is. Rebuilding resamples the whole model: it closes "
                     "anything but softens detail finer than the resolution.")),
        Section("Clean",
            Bool("sheets", "Remove duplicate and back-to-back faces", True,
                 help="Back-to-back pairs are sheets with no volume, often left by decimation."),
            Choice("pieces", "Loose pieces", "crumbs", [
                ("crumbs", "Remove crumbs"), ("largest", "Keep the largest only"), ("all", "Keep all")],
                help="Crumbs are pieces with fewer faces than the share below of the largest piece."),
            Float("crumb_share", "Crumb size", 0.2, unit="% of the largest", min=0.0, max=20.0, step=0.1,
                  decimals=2, visible_if={"pieces": "crumbs"}, advanced=True),
            Float("weld", "Weld distance", 0.0, unit="units", min=0.0, max=10.0, step=0.001, decimals=4,
                  advanced=True,
                  help="Join vertices closer than this (0 joins only vertices at exactly the same place). Raise it "
                       "a little for models exported in separate patches with cracks between them.")),
        Section("Topology",
            Bool("nonmanifold", "Separate non-manifold edges and vertices", True,
                 help="Where more than two faces share an edge, or parts touch at a single vertex, the faces are "
                      "paired around the edge so each part becomes a surface of its own."),
            Bool("orient", "Wind faces consistently, facing out", True)),
        Section("Holes",
            Choice("fill", "Fill holes", "all", [("all", "All"), ("small", "Up to a size"), ("none", "None")],
                   segmented=True,
                   help="A closed solid needs every hole filled; with A clean surface choose what to keep open."),
            Int("fill_max_edges", "Largest hole", 200, unit="edges", min=3, max=100000,
                visible_if={"fill": "small"}),
            Choice("fill_shape", "Patch", "auto", [("auto", "Auto"), ("flat", "Flat"), ("smooth", "Smooth")],
                   segmented=True, visible_if=lambda v: v.get("fill") != "none",
                   help="Flat fills a hole across its plane, Smooth with a relaxed membrane that follows the "
                        "surface around it (curved openings). Auto picks flat for flat holes.")),
        Section("Rebuild",
            Int("resolution", "Resolution", 192, min=64, max=384, step=16,
                visible_if=lambda v: v.get("strategy") != "repair",
                help="Grid cells along the model's longest side when rebuilding. Higher keeps more detail and "
                     "takes longer.")),
    ],
    sample=".engine:make_sample",
    test_values={"resolution": 64},
)
