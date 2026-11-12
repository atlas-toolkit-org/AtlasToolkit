"""Developable Surfaces. Signed output: atlas-toolkit:mesh-developable."""
from atlas_toolkit.core import Choice, Float, Inputs, Int, Output, Section, Tool

TOOL = Tool(
    id="mesh-developable", name="Developable Surfaces", version="0.1.0", category="mesh",
    icon="developable", action="Develop", runs_in="process", preview="mesh", requires=("developable",),
    summary="Evolve a model into flowing, approximately developable surfaces for line art.",
    tagline="Broad folds, smooth seams and nearly straight rulings from a triangle mesh.",
    description=("Adapted from Stein, Grinspun and Crane, Developability of Triangle Meshes (2018). "
                 "A field-aligned triangular remesh (with a reported, topology-checked coarsening fallback) evolves through coarse optimization and regular subdivision. "
                 "The initial triangulation strongly influences the design. Open manifold surfaces are accepted; "
                 "prepared boundaries stay fixed. Source units and component placement are preserved. "
                 "The output is an approximation, not a guarantee of exact flattenability or collision-free "
                 "fabrication. Draw with Plotter Vision's Developable rulings or Developable creases presets, "
                 "without decimation, or with Contour Lines. No Blender or MATLAB required."),
    inputs=Inputs("mesh", exclude=(r"(?i)_developable\.(stl|obj|ply)$",)),
    output=Output("{stem}_developable.stl", "mesh", formats=(".stl", ".obj", ".ply")),
    signature="atlas-toolkit:mesh-developable", engine=".engine:run", inspect=".engine:inspect", columns=("faces", "reduction"),
    params=[Section("Surface",
        Choice("preparation", "Preparation", "remesh", [("remesh", "Field-aligned"),
                                                       ("keep", "Keep input triangulation")]),
        Int("target_faces", "Initial face target", 2000, min=100, max=50000, step=100,
            help="Approximate total triangle count before refinement, shared among surface components.",
            visible_if={"preparation": "remesh"}),
        Choice("energy", "Developability energy", "covariance",
               [("covariance", "Intrinsic covariance"), ("max-covariance", "Maximal intrinsic covariance"),
                ("combinatorial", "Combinatorial")]),
        Int("subdivisions", "Refinement levels", 2, min=0, max=4,
            help="Each regular subdivision multiplies the face count by four, then optimizes again.")),
        Section("Optimization",
        Int("iterations", "Iterations per level", 1000, min=1, max=10000, step=100, advanced=True),
        Float("tolerance", "Gradient tolerance", 0.000001, min=0.00000001, max=0.01,
              step=0.000001, decimals=8, advanced=True),
        Int("max_faces", "Face safety limit", 250000, min=1000, max=2000000, step=10000, advanced=True,
            help="Reject a refinement that exceeds this total budget; components are never silently dropped."))],
    sample=".engine:make_sample",
    test_values={"preparation": "keep", "subdivisions": 0, "iterations": 8},
)
