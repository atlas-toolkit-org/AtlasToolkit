# Pipe Fill: research and development

## Confirmed scope

Two distinct modes replace a model with a new volumetric pipe sculpture: organic
repulsive curves and orthogonal pipes with rounded elbows. Gaps are intentional.
Independent open strands and closed loops, solid tubes and hollow pipes, original
units, seeded variation, and plotter-oriented presets. No retained body or stage
sequence. Closed input is required unless repair is explicitly enabled.

## Sources and implementation decision

- Yu, Schumacher and Crane, *Repulsive Curves*, ACM TOG 40(2), 2021:
  https://www.cs.cmu.edu/~kmcrane/Projects/RepulsiveCurves/index.html
  The paper packs curves by increasing length while repelling curves and the
  enclosing surface. Its tangent-point energy and fractional Sobolev solver are
  substantially more involved than a local separation force.
- Authors' implementation and scene format:
  https://github.com/ythea/repulsive-curves
  https://github.com/ythea/repulsive-curves/blob/master/scenes/FORMATS.md
  Initial curves must already approximately satisfy constraints. Importing an
  arbitrary mesh alone does not initialize a feasible, space-filling network.
- Michael Fogleman's `ln`: https://github.com/fogleman/ln
  A hidden-line vector renderer, not a general mesh-volume packing solver.
  Atlas already supplies the rendering stage through Plotter Vision and Hatch
  Maker, so no separate Go renderer is needed.
- Barill et al., *Fast Winding Numbers for Soups and Clouds*, 2018:
  https://www.dgp.toronto.edu/projects/fast-winding-numbers/
  Existing libigl bindings provide a cached winding hierarchy and AABB distance
  queries. Preserve oriented cavities; do not flood-fill enclosed voids.
- Hanson and Ma, *Parallel Transport Approach to Curve Framing*, 1995:
  https://legacy.cs.indiana.edu/ftp/techreports/TR425.pdf
  Transport frames along centerlines to sweep tubes without Frenet-frame flips.

Atlas uses an independent, finite-radius packing algorithm inspired by the
repulsion/containment objectives. It does **not** claim to reproduce the original
tangent-point energy, Sobolev descent, topology preservation, or its performance.
No third-party source is copied; existing bundled numpy/scipy/libigl/trimesh
suffice, without Blender or an additional runtime.

## Prototype stages

1. Classify a bounded lattice throughout the volume, and certify neighbor edges
   with boundary-distance subdivision. Budget checks precede grid allocation.
2. Route seeded disjoint paths, or build square cycles and splice compatible
   cycle edges. Cycle mode never silently falls back to open strands.
3. Orthogonal mode rounds elbows with circular arcs. Organic mode relaxes and
   perturbs the paths, with local repulsion. Backtracking accepts only candidates
   satisfying finite-radius boundary and segment-separation checks.
4. Sweep circular solid tubes or inner/outer pipe walls with transported frames.
   Closed loops have periodic frames; open pipes have annular rims and open bores.
5. Validate finite, consistently oriented, watertight shells; validate saved copies
   before atomic publication. A hollow pipe's material boundary is watertight even
   though the bore is open. Separate pipes remain separate shells.
6. Evaluate primitives, cavities, disconnected components and sculpture inputs;
   inspect actual Atlas previews and pen-plotter SVGs. Publish measured coverage
   and limitations rather than promising literal complete volume occupancy.

Spacing controls sampling density; diameter and gap control material separation.
Regions narrower than the outer diameter cannot contain a tube. Lattice scale,
closed-cycle routing and boundary clearance can exclude additional narrow regions;
reports distinguish eligible sites from sites used by accepted paths.

## Atlas contract

Tool folder `tools/pipe_fill`: light spec, GUI-free algorithm, engine adapter.
Unique icon, schema-generated UI/CLI/Workflow controls, deterministic seed,
cancellable process worker, sample/self-test, signed STL/OBJ/PLY output, safe
staged publication, original-coordinate output and result-only preview.
