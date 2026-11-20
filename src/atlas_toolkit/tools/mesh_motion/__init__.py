"""Motion Sculpture. Atlas signature: atlas-toolkit:mesh-motion."""
from dataclasses import replace

from atlas_toolkit.core import Choice, Float, Inputs, Int, Output, Section, Seed, Tool, frame_params, influence_params


def region_params():
    params = influence_params()
    # 0.5.0 dropped Painted area: a file or workflow saved with it acts on the whole model.
    params[0] = replace(params[0], former_values={"paint": "all"})
    return params

TOOL = Tool(
    id="mesh-motion", name="Motion Sculpture", version="0.5.0", category="mesh",
    icon="motion-sculpture", action="Sculpt",
    summary="Stretch a silhouette into swept laminae, splintered bars or a rising vortex of motion.",
    tagline="Give a still form a wake. Carve time into the silhouette.",
    description=("Cut real capped layers from a model and stretch their trailing edges into tapered wakes. "
                 "Laminae make broad glass-like fins; Splinters cuts each layer into independently swept bars. "
                 "Heading is the direction of travel: the wake extends behind it. Pulse, gust and vortex fields "
                 "change the gesture through the stack. Preserve the leading face, anchor the feet, and use a "
                 "feathered region to isolate the motion. Preview and Run use identical geometry. "
                 "Closed, consistently oriented input is required: small defects are tidied automatically, and "
                 "Repair Mesh fixes the rest (or Open input › Repair by resampling). Source units and placement are retained. "
                 "The output is separate closed shells, not a fused printable assembly. No Blender is needed."),
    inputs=Inputs("mesh", exclude=(r"(?i)_motion\.(stl|obj|ply)$",)),
    output=Output("{stem}_motion.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-motion", engine=".engine:run", runs_in="process", preview="mesh",
    sample=".engine:make_sample", columns=("pieces", "faces"),
    preset_inputs=("influence", "repair"),
    needs="solid",
    params=[
        Section("Motion",
            Choice("structure", "Structure", "laminae", [("laminae", "Laminae"), ("splinters", "Splinters")],
                   help="Real slabs from the source; Splinters divides each slab into parallel bars before sweeping."),
            Choice("gesture", "Gesture", "gust", [("stream", "Stream"), ("pulse", "Pulse"),
                   ("gust", "Gust"), ("vortex", "Vortex")]),
            Float("amount", "Amount", 1., min=0., max=1., step=.05, decimals=2, slider=True,
                  help="Zero returns the exact source. Otherwise blends motion, gaps and taper from rest."),
            Float("reach", "Wake length", 45., unit="% h", min=0., max=200., step=2., slider=True,
                  help="Maximum added length behind each piece, as a percentage of source height."),
            Float("heading", "Travel heading", 0., unit="°", min=-180., max=180., step=5., slider=True,
                  help="In the layer plane: 0° travels toward +X for horizontal layers; the wake trails toward −X."),
            Float("preserve", "Leading form", 40., unit="%", min=0., max=90., step=5., slider=True,
                  help="Leading share of each piece kept unstretched."),
            Float("taper", "Tail taper", 70., unit="%", min=0., max=95., step=5., slider=True,
                  help="Narrows both depth and layer thickness toward the trailing tip; tips stay finite."),
            Float("curl", "Sideways curl", 8., unit="% h", min=-60., max=60., step=2., slider=True,
                  help="Curves the wake sideways inside its layer plane, preserving separation between layers."),
            Float("cycles", "Rhythm", 2., min=.25, max=8., step=.25, decimals=2,
                  visible_if={"gesture": ("pulse", "gust")}),
            Float("phase", "Phase", 0., unit="°", min=-180., max=180., step=5.,
                  visible_if={"gesture": ("pulse", "gust")}),
            Float("turn", "Vortex turn", 160., unit="°", min=-540., max=540., step=10.,
                  visible_if={"gesture": "vortex"}, help="Heading changes by this angle from bottom to top."),
            Float("variation", "Irregularity", 35., unit="%", min=0., max=100., step=5.,
                  help="Seeded per-piece wake length. Does not move the leading edge."),
            Seed("seed", "Seed", 19),
        ),
        Section("Layers",
            Int("layers", "Layers", 64, min=2, max=240,
                help="Stack resolution. More layers give finer horizontal motion lines."),
            Float("gap", "Layer gap", 18., unit="% pitch", min=0., max=85., step=2.,
                  help="Compresses each complete slab about its centre; no source layers are discarded."),
            Int("lanes", "Bars per layer", 8, min=2, max=32, visible_if={"structure": "splinters"}),
            Float("bar_gap", "Bar gap", 12., unit="% pitch", min=0., max=70., step=2.,
                  visible_if={"structure": "splinters"}),
            Choice("layer_axis", "Layer direction", "up", [("up", "Horizontal"),
                   ("front", "Front to back"), ("side", "Side to side")]),
            Float("tilt", "Layer tilt", 0., unit="°", min=-70., max=70., step=5.),
            Float("hold_base", "Hold base", 6., unit="% stack", min=0., max=90., step=2.,
                  help="Leaves lower layers unswept; a smooth transition occupies the next 10% of the stack."),
        ),
        Section("Region", *region_params()),
        Section("Model", *frame_params(stage_sets=False)),
        Section("Quality",
            Int("resolution", "Wake resolution", 12, min=4, max=48, advanced=True,
                help="Maximum longitudinal edge span is piece length divided by this number. Conforming refinement."),
            Int("max_faces", "Maximum faces", 1500000, min=10000, max=4000000, advanced=True,
                help="Hard output budget. Reduce layers, bars or wake resolution when exceeded."),
            Choice("repair", "Open input", "strict", [("strict", "Require closed solid"),
                   ("resample", "Repair by resampling")],
                   help="Small defects (decimation debris, pin-holes, flipped faces) are always tidied without "
                        "changing the surface. For larger holes use Repair Mesh, or resample here: it changes the "
                        "surface and may soften detail."),
            Int("repair_resolution", "Repair resolution", 128, min=64, max=320, step=16, advanced=True,
                visible_if={"repair": "resample"}),
        ),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"layers": 6, "resolution": 4},
)
