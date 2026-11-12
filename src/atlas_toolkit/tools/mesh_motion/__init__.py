"""Motion Sculpture. Atlas signature: atlas-toolkit:mesh-motion."""
from atlas_toolkit.core import (Choice, FileRef, Float, Inputs, Int, Output, Section, Seed, Text, Tool,
                               frame_params, influence_params)


def region_params():
    params = influence_params()
    params[0] = Choice("influence", "Acts on", "all", [("all", "Whole model"), ("band", "Height band"),
                       ("sphere", "Sphere"), ("paint", "Painted area")],
                       help="Painted area: open Selection and Paint / select area. In Workflows use Aim in tool, then Back to workflow.")
    return params + [
        Choice("paint_style", "Painted geometry", "layers", [("layers", "Sliced layers"), ("connected", "Connected surface")],
               visible_if={"influence": "paint"}, help="Sliced layers samples the whole silhouette into solid plates/bars; only painted outward section edges grow wakes. Connected surface keeps unpainted skin fixed."),
        Float("paint_feather", "Paint feather", 8., unit="% h", min=.5, max=50., step=1., decimals=1,
              visible_if={"influence": "paint"}, help="Softens wake strength at painted boundaries. Smaller values give more of the selected area full intensity."),
        FileRef("selection_file", "Selection file", "", exts=(".json",), visible_if={"influence": "paint"},
                help="Optional Surface Eject-compatible selection JSON. Overrides the embedded mask; tied to the exact source mesh."),
        Text("selection", "Embedded selection", "", advanced=True, visible_if={"influence": "paint"},
             help="Face mask written by the paint editor, saved with settings, history and Workflows."),
    ]

TOOL = Tool(
    id="mesh-motion", name="Motion Sculpture", version="0.3.0", category="mesh",
    icon="motion-sculpture", action="Sculpt",
    summary="Stretch a silhouette into swept laminae, splintered bars or a rising vortex of motion.",
    tagline="Give a still form a wake. Carve time into the silhouette.",
    description=("Cut real capped layers from a model and stretch their trailing edges into tapered wakes. "
                 "Laminae make broad glass-like fins; Splinters cuts each layer into independently swept bars. "
                 "Heading is the direction of travel: the wake extends behind it. Pulse, gust and vortex fields "
                 "change the gesture through the stack. Preserve the leading face, anchor the feet, and use a "
                 "feathered region to isolate the motion. Preview and Run use identical geometry. "
                 "Painted area uses the Selection tab's visible-face editor. Sliced layers makes real slabs or bars; "
                 "Connected surface keeps the skin continuous. Local protection retains strong motion in safe areas. "
                 "Closed, consistently oriented input is required; source units and placement are retained. "
                 "Unpainted modes output separate closed shells, not a fused printable assembly. No Blender is needed."),
    inputs=Inputs("mesh", exclude=(r"(?i)_motion\.(stl|obj|ply)$",)),
    output=Output("{stem}_motion.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-motion", engine=".engine:run", runs_in="process", preview="mesh",
    sample=".engine:make_sample", columns=("pieces", "faces"), viewer=".viewer:SelectionView",
    preset_inputs=("influence", "selection", "selection_file", "paint_feather", "paint_style"),
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
                  help="Leading share retained in whole-model modes. Painted wakes retain the original section and use this control to moderate extension near the leading side."),
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
        ),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    test_values={"layers": 6, "resolution": 4},
)
