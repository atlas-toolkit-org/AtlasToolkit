"""Dilate: one inflated mesh per run, signed atlas-toolkit:mesh-dilate."""
from atlas_toolkit.core import (Choice, Float, Inputs, Int, Output, Section, Seed, Tool,
                                frame_params, influence_params)


TOOL = Tool(
    id="mesh-dilate", name="Dilate", version="0.1.0", category="mesh", icon="dilate", action="Dilate",
    summary="Inflate a model with pressure, round its volumes, or swell a feathered region into a sculptural bulge.",
    tagline="Rotund bronze, a flowing goddess, a taut balloon: broad volumes to draw with contours and hidden lines.",
    description=(
        "Pressure expands an elastic surface with stretching and bending resistance. Each closed part has its "
        "own volume target; a near-uniform simulation carries the detailed model along. Softness lets the skin "
        "relax, Roundness softens its curvature, and Keep detail retains fine relief. Around an axis keeps the "
        "length along that axis fixed while the model swells sideways. A height band or sphere confines the "
        "effect, with feathered edges. Keep base fixes the lowest 2% of the height and fades over the next 5%; "
        "Keep open boundary fixes openings. Open or unreliable-volume parts keep their openings and swell "
        "along their oriented surface instead, with a warning. Broad lobes vary pressure smoothly in space "
        "using the seed. Contact checks and backtracking limit crossings; constrained models may achieve less "
        "than the requested inflation. One final mesh per run, in the input's units and position."),
    inputs=Inputs("mesh", exclude=(r"(?i)_dilate\.(stl|obj|ply)$",)),
    output=Output("{stem}_dilate.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-dilate", engine=".engine:run", runs_in="process", preview="mesh",
    columns=("inflation", "displacement", "faces"),
    params=[
        Section("Dilation",
            Float("inflation", "Inflation", 80.0, unit="%", min=0, max=300, step=5, decimals=1, slider=True,
                  help="Requested added volume for a whole closed model: 80% means 1.8 times its volume. "
                       "A local region reduces participation. Open parts use an equivalent outward swelling "
                       "distance, not an enclosed-volume measurement. Constraints can limit the result."),
            Choice("direction", "Direction", "all", [("all", "All directions"), ("radial", "Around an axis")],
                   segmented=True),
            Choice("axis", "Axis", "up", [("up", "Up"), ("front", "Front"), ("side", "Side")], segmented=True,
                   visible_if={"direction": "radial"},
                   help="Around an axis leaves every point's coordinate along this axis unchanged."),
            Float("softness", "Softness", 0.70, min=0, max=1, step=0.05, decimals=2, slider=True,
                  help="Low: an elastic skin with stronger memory of its shape. High: material that stretches "
                       "and relaxes readily into generous volumes."),
            Float("roundness", "Roundness", 0.60, min=0, max=1, step=0.05, decimals=2, slider=True,
                  help="Relaxes bending memory and smooths the pressure field. High gives broad, taut curves "
                       "that render cleanly as pen-plotter contours."),
            Float("detail", "Keep detail", 0.85, min=0, max=1, step=0.05, decimals=2, slider=True,
                  help="Fine relief carried by the inflated surface: 0 softens it, 1 keeps it fully. "
                       "The large-scale shape is retained in either case."),
            Choice("anchor", "Anchor", "free", [("free", "Free"), ("base", "Keep base"),
                                                    ("boundary", "Keep open boundary")],
                   help="Base: fix the lowest 2% of model height, fading over the next 5%. Boundary: "
                        "fix opening edges with a smooth transition. Closed parts have no open boundary."),
        ),
        Section("Region", *influence_params()),
        Section("Broad lobes",
            Float("lobes", "Broad lobes", 0.0, min=0, max=1, step=0.05, decimals=2, slider=True,
                  help="Smooth variation of swelling pressure, rather than fine surface noise. 0 is uniform."),
            Float("lobe_size", "Lobe size", 35.0, unit="% h", min=10, max=200, step=5, decimals=1,
                  visible_if=lambda v: float(v.get("lobes") or 0) > 0,
                  help="Spatial wavelength relative to model height. Larger gives fewer, broader swells."),
            Seed("seed", "Seed", 0, visible_if=lambda v: float(v.get("lobes") or 0) > 0),
        ),
        Section("Model", *frame_params(stage_sets=False)),
        Section("Quality",
            Choice("quality", "Quality", "standard", [("standard", "Standard"), ("high", "High")],
                   segmented=True, help="About 12,000 or 36,000 simulation faces. Preview uses about 5,000. "
                                        "Solver convergence is automatic; every run exports one mesh."),
            Int("out_faces", "Output face limit", 400_000, min=2_000, max=2_000_000, step=10_000,
                advanced=True, help="Shared budget for detail refinement across all parts. Long edges are "
                                    "split so even a coarse model can curve smoothly."),
            collapsed=True),
    ],
    preset_skip=("up_axis", "front", "rotate"),
    sample=".engine:make_sample", test_values={"out_faces": 4_000}, legacy=(),
)
