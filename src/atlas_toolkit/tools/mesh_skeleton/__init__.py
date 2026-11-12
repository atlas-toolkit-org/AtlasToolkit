"""
Skeleton Shell: empty a model and keep only a structure on its skin, a wire or plate sculpture you can see through:
wire cells, a triangle net, flowing strands, ribs and stringers, or a cut plate, alone or in two layers, with part of the
skin kept solid if you like.

Outputs <name>_skeleton.stl, signed atlas-toolkit:mesh-skeleton.
"""
from atlas_toolkit.core import (Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, frame_params,
                                influence_params)

STRUCTURES = [("cells", "Wire Cells"), ("trimesh", "Trimesh"), ("flow", "Flow Strands"),
              ("ribs", "Ribs & Stringers"), ("plate", "Cut Plate")]


def main_is(*names):
    return lambda v: v.get("structure", "cells") in names


def used(*names):
    """A setting shared by the first and the second layer."""
    return lambda v: v.get("structure", "cells") in names or v.get("second", "none") in names


def has_wire(v):
    return used("cells", "trimesh", "flow")(v) or (used("ribs")(v) and (v.get("rib_profile", "frame") != "frame"
                                                                        or float(v.get("stringer_spacing", 0)) > 0))


def has_second(v):
    return v.get("second", "none") != "none"


def is_frame(v):
    return used("ribs")(v) and v.get("rib_profile", "frame") == "frame"


def has_sheet(v):
    return used("plate")(v) or is_frame(v) or v.get("influence", "all") != "all"


def flat_profile(v):
    return v.get("profile", "round") != "round" or (has_second(v) and v.get("second_profile", "round") != "round") \
        or (used("ribs")(v) and v.get("rib_profile", "frame") in ("strap", "fin"))


TOOL = Tool(
    id="mesh-skeleton",
    name="Skeleton Shell",
    version="0.1.0",
    category="mesh",
    icon="skeleton-shell",
    action="Hollow",
    summary="Empty a model and keep only a structure on its skin: wire cells, a triangle net, strands, ribs or a cut "
            "plate.",
    tagline="A figure becomes a wire or plate sculpture with nothing inside: you see through the near side to the far "
            "one, as real geometry for Plotter Vision, Hatch Maker or a 3D printer.",
    description=(
        "The model is read as a surface, not a volume, so nothing has to be closed and nothing is built inside. "
        "Wire Cells draws geodesic Voronoi walls over the skin, from an even honeycomb to a biomorphic net. Trimesh "
        "joins the same seeds into triangles, straight as rods or following the surface. Flow Strands traces long "
        "curves along the form's own curvature, upright, or as spirals of two hands. Ribs & Stringers makes the "
        "stations of an old airframe or hull: rods, straps or real flat ring frames with lightening holes, tied by "
        "stringers and a keel. Cut Plate keeps a band of skin and pierces the rest, as a laser-cut sheet or, rounded, "
        "a bony web. Wires lie with their outer face on the skin, so the silhouette holds. A second layer adds another "
        "structure, usually a finer one, and Acts on keeps part of the skin solid, letting go cell by cell across the "
        "feather. All sizes are % of the model's height. Fuse merges everything into one printable solid."),
    inputs=Inputs("mesh", exclude=(r"(?i)_skeleton\.(stl|obj|ply)$",)),
    output=Output("{stem}_skeleton.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-skeleton",
    engine=".engine:run",
    runs_in="process",
    preview="mesh",
    sample=".engine:make_sample",
    columns=("wires", "faces"),
    params=[
        Section(
            "Structure",
            Choice("structure", "Structure", "cells", STRUCTURES,
                   help="Wire Cells: a net of polygons over the skin. Trimesh: the triangles between the same seeds. "
                        "Flow Strands: long curves along the form. Ribs & Stringers: the stations and longitudinals "
                        "of an airframe or hull. Cut Plate: a sheet of skin with openings cut in it."),
            Float("size", "Cell size", 6.5, unit="% h", min=1.0, max=40.0, step=0.5, decimals=1, slider=True,
                  help="The structure's scale: the width of a cell, or the gap between strands or ribs. At 5-8% a "
                       "figure stays legible and the far side shows through; under 3% it reads as tone."),
            Float("regularity", "Regularity", 0.5, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=used("cells", "trimesh", "plate"),
                  help="0 scatters the seeds at random, for wild uneven cells. 1 relaxes them into an even, almost "
                       "hexagonal packing."),
            Float("straightness", "Straightness", 0.45, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  visible_if=used("cells", "trimesh", "plate"),
                  help="0: wires curve with the surface. 1: straight rods from joint to joint, which facet the form "
                       "like bent metal rod."),
            Float("stretch", "Cell stretch", 1.0, min=1.0, max=5.0, step=0.1, decimals=1,
                  visible_if=used("cells", "trimesh", "plate"),
                  help="Draws the cells out along the model's height. Above 2 the net turns to long slashes, like a cut "
                       "plate figure."),
            Float("detail", "Finer on detail", 0.7, min=0.0, max=1.0, step=0.05, decimals=2,
                  visible_if=used("cells", "trimesh", "plate"),
                  help="Makes cells smaller where the form turns fast (face, hands, feet), down to a third of Cell size "
                       "at 1, and leaves the large planes open."),
            Float("missing", "Missing wires", 0.0, unit="%", min=0.0, max=70.0, step=5.0, decimals=0,
                  help="Leaves out this share of the wires at random, to open the net or suggest decay."),
            Seed("seed", "Seed", 1, help="Where cells and strands fall, and every irregularity."),
        ),
        Section(
            "Flow strands",
            Choice("flow_field", "Follow", "form", [("form", "The form"), ("upright", "Upright"), ("spiral", "Spiral")],
                   segmented=True,
                   help="The form: strands run along the direction the surface bends least, down limbs and ridges. "
                        "Upright: straight up the model. Spiral: at an angle around it.",
                visible_if=used("flow")),
            Float("flow_angle", "Spiral angle", 35.0, unit="°", min=5.0, max=85.0, step=1.0, decimals=0,
                  visible_if=lambda v: used("flow")(v) and v.get("flow_field") == "spiral",
                  help="How far the strands lean from upright. Low: a long drawn-out twist. High: nearly hoops."),
            Bool("crossing", "Second family", False,
                 help="Adds strands crossing the first ones: around the form, or the opposite hand of a spiral. Makes a "
                      "woven lattice, and ties the strands together.",
                visible_if=used("flow")),
            Float("strand_length", "Longest strand", 120.0, unit="% h", min=5.0, max=400.0, step=5.0, decimals=0,
                  help="Strands stop at this length, or sooner where they run out of room.",
                visible_if=used("flow")),
            Float("min_length", "Shortest strand", 8.0, unit="% h", min=0.5, max=100.0, step=0.5, decimals=1,
                  help="Strands shorter than this are left out; raise it for fewer, longer gestures.",
                visible_if=used("flow")),
            Float("wander", "Wander", 0.0, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="Turns the strands off their course with slow seeded noise, for a looser, hand-laid line.",
                visible_if=used("flow")),
            Float("feature", "Features first", 0.0, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="Gives the strongest features of the form the first strands and the thickest wire. Raise "
                       "Cell size with it to keep only rods along the main lines.",
                visible_if=used("flow")),
            Int("flow_smooth", "Field smoothing", 30, min=0, max=200, step=5, advanced=True,
                help="Passes of smoothing on the curvature field: more gives longer, calmer lines, less follows small "
                     "forms.",
                visible_if=used("flow")),
        ),
        Section(
            "Ribs and stringers",
            Choice("rib_axis", "Stations along", "up", [("up", "Height"), ("across", "Width"), ("depth", "Depth"),
                                                        ("rings", "Rings from crown")],
                   help="The direction the stations step along. Rings from crown follows the surface from the highest "
                        "point, so they are not flat; they take a rod or strap.",
                visible_if=used("ribs")),
            Choice("rib_profile", "Ribs are", "frame", [("frame", "Flat frames"), ("strap", "Straps"), ("fin", "Fins"),
                                                        ("rod", "Rods")],
                   help="Flat frames: rings cut from the model's section, as a ship's or airframe's formers. Straps lie "
                        "on the skin, fins stand inward from it, rods are round.",
                visible_if=used("ribs")),
            Float("frame_depth", "Frame depth", 5.0, unit="% h", min=0.5, max=30.0, step=0.5, decimals=1,
                  visible_if=is_frame,
                  help="How far each frame reaches in from the skin. Narrow sections keep an open middle whatever this "
                       "says."),
            Bool("lightening", "Lightening holes", True, visible_if=is_frame,
                 help="Round holes down the middle of each frame, as in the formers of an old aeroplane."),
            Bool("keel", "Keel", False, visible_if=is_frame,
                 help="Adds one long frame through the model's middle, running across all the stations."),
            Float("stringer_spacing", "Stringer spacing", 8.0, unit="% h", min=0.0, max=40.0, step=0.5, decimals=1,
                  help="The gap between the long members that tie the ribs. 0 leaves only the ribs.",
                visible_if=used("ribs")),
            Choice("stringer_style", "Stringers", "radial", [("radial", "Radial"), ("parallel", "Parallel")],
                   segmented=True, visible_if=lambda v: used("ribs")(v) and float(v.get("stringer_spacing", 0.0)) > 0.0,
                   help="Radial: meridians from the model's axis, as on a fuselage. Parallel: flat parallel cuts, which "
                        "make a lattice with the ribs."),
        ),
        Section(
            "Cut plate",
            Choice("plate_pattern", "Openings", "cells", [("cells", "Polygons"), ("triangles", "Triangles")],
                   segmented=True,
                   help="Polygons: Voronoi cells. Triangles: three-sided shards; Missing wires merges some into larger "
                        "shapes.",
                visible_if=used("plate")),
            Float("band", "Band width", 1.6, unit="% h", min=0.2, max=15.0, step=0.1, decimals=2,
                  help="The width of metal left between two openings.",
                visible_if=used("plate")),
            Int("rounding", "Rounding", 0, min=0, max=8,
                help="0 keeps the laser-cut corners. Higher rounds the openings and thickens the joints into a bony, "
                     "webbed look.",
                visible_if=used("plate")),
        ),
        Section(
            "Wire",
            Choice("profile", "Profile", "strap", [("round", "Round"), ("strap", "Strap"), ("fin", "Fin")],
                   segmented=True,
                   help="Round: a rod. Strap: a flat band on the skin, drawn as two clean edges. Fin: a band standing "
                        "inward, showing its depth where the form turns.",
                visible_if=has_wire),
            Float("wire", "Wire thickness", 1.0, unit="% h", min=0.05, max=8.0, step=0.05, decimals=2,
                  help="Diameter of a rod, or width of a strap or fin. On a sheet 25 cm tall, 0.5% is 1.25 mm.",
                visible_if=has_wire),
            Float("flatness", "Flatness", 5.0, min=1.5, max=12.0, step=0.5, decimals=1, visible_if=flat_profile,
                  help="A strap's or fin's width over its thickness."),
            Int("sides", "Wire sides", 4, min=3, max=16,
                visible_if=lambda v: has_wire(v) and (v.get("profile") == "round"
                                                      or (has_second(v) and v.get("second_profile") == "round")
                                                      or (used("ribs")(v) and v.get("rib_profile") == "rod")),
                help="Sides of a round rod. 4 draws as a clean open bar, the best on paper. Above 8 a rod looks round "
                     "in a render, but its many edges draw as dark marks."),
            Float("wire_vary", "Thickness variation", 0.0, min=0.0, max=0.9, step=0.05, decimals=2, slider=True,
                  help="Slow seeded swelling and thinning along the wires.",
                visible_if=has_wire),
            Float("swell", "Joint swell", 0.0, min=0.0, max=3.0, step=0.1, decimals=1, slider=True,
                  help="Wires grow thicker into their joints, like bone or poured metal.",
                visible_if=has_wire),
            Float("taper", "Taper", 0.0, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="Free ends thin to a point over this share of each half of the wire.",
                visible_if=has_wire),
            Float("wobble", "Hand wobble", 0.0, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="Bends the wires off true, as in hand-formed wire. Joints stay together.",
                visible_if=has_wire),
            Float("overrun", "Overrun", 0.0, min=0.0, max=1.0, step=0.05, decimals=2, slider=True,
                  help="Wires run past their joints by up to this share of Cell size, like tied or welded rod.",
                visible_if=has_wire),
        ),
        Section(
            "Second layer",
            Choice("second", "Add", "none", [("none", "None")] + STRUCTURES,
                   help="A second structure over the first: fine cells carried on heavy ribs, or strands over a net. "
                        "It shares the settings above and has its own scale and wire."),
            Float("second_size", "Size", 3.0, unit="% h", min=1.0, max=40.0, step=0.5, decimals=1, visible_if=has_second,
                  help="This layer's Cell size."),
            Float("second_wire", "Thickness", 0.25, unit="% h", min=0.05, max=8.0, step=0.05, decimals=2,
                  visible_if=has_second, help="This layer's Wire thickness."),
            Choice("second_profile", "Profile", "round", [("round", "Round"), ("strap", "Strap"), ("fin", "Fin")],
                   segmented=True, visible_if=has_second, help="This layer's wire profile."),
            collapsed=True,
        ),
        Section(
            "Acts on",
            *influence_params(),
            Bool("rim", "Wire along the edge", True, visible_if=lambda v: v.get("influence", "all") != "all",
                 help="Finishes the edge of the kept skin with a wire."),
            collapsed=True,
            help="Where the model is hollowed. The rest keeps its skin as a thin plate, still empty behind, and the "
                 "feather lets go of it cell by cell.",
        ),
        Section(
            "Output",
            Float("thickness", "Plate thickness", 0.5, unit="% h", min=0.05, max=5.0, step=0.05, decimals=2,
                  visible_if=has_sheet,
                  help="The thickness of cut plates, kept skin and flat frames. It goes inward, so the silhouette "
                       "holds."),
            Bool("fuse", "Fuse into one solid", False,
                 help="Merges all parts into one closed printable solid. Slower, and parts that don't touch stay "
                      "separate, so single-family strands need a second family or layer. Off: fast overlapping solids, "
                      "fine for drawing."),
            Int("max_faces", "Output faces", 4_000_000, min=10_000, max=30_000_000, step=100_000,
                help="The largest result allowed. Wires need faces: raise Cell size or lower Wire sides to come under "
                     "it."),
        ),
        Section("Model", *frame_params(stage_sets=False)),
        Section(
            "Quality",
            Int("working_faces", "Working faces", 400_000, min=20_000, max=4_000_000, step=20_000,
                help="The size of the skin the structure is laid out on, and the detail kept skin has. Raise for finer "
                     "cuts on a Cut Plate; it does not change wires much."),
            Int("draft_faces", "Preview faces", 100_000, min=10_000, max=1_000_000, step=10_000, advanced=True,
                help="The working skin of a Preview. Cells can fall a little differently from Run."),
            collapsed=True,
        ),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"size": 22.0, "second_size": 25.0, "working_faces": 20_000, "draft_faces": 10_000, "sides": 4,
                 "regularity": 0.3},
)
