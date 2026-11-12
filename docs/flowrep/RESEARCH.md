# FlowRep research and implementation notes

## Sources reviewed

- Gori, Sheffer, Vining, Rosales, Carr and Ju, **FlowRep: Descriptive Curve Networks for
  Free-Form Design Shapes**, ACM TOG 36(4), 59, SIGGRAPH 2017.
  [Project and supplements](https://www.cs.ubc.ca/labs/imager/tr/2017/FlowRep/);
  [DOI](https://doi.org/10.1145/3072959.3073639).
  The local paper is flowrep-paper.pdf. Its sections 5–7 and appendix define the implementation.
- [Authors' video](https://www.youtube.com/watch?v=en7ICJRGzH0), 3:41.
  Reviewed its transcript and visual examples: 0:27–1:03 describes the perceptual criteria;
  1:09–1:34 shows quad inputs, strands and flowlines; 1:34–2:00 shows assembly,
  simplification and regularization; 2:07 onward shows results and comparisons.
  The auto-generated transcript sometimes misrecognizes “anisotropic”; the paper is authoritative.
- The supplied model bundle contains 24 input meshes, output networks and, for several models,
  networks before and after regularization. Inputs are quad-dominant with occasional other polygons.
  Published output OBJ files encode ordered curves using line indices in “# chain” comments.
  The bunny's output network is centered and normalized independently of its input mesh.
  Reference evaluation recovers its uniform scale and translation and verifies surface adherence;
  it records that transform explicitly. Reference registration never rotates or distorts a network.
- Local supplementary questionnaires contain four pages of artist comparisons and eight pages
  comparing FlowRep against alternative representations. They support visual evaluation; they do
  not specify additional algorithm stages.
- [Towards3DVRSketch](https://github.com/ygryadit/Towards3DVRSketch) explicitly uses the
  authors' FlowRep implementation to generate input networks. Its SyntheticSketch code provides
  curve consolidation, selection, splitting and perturbation after extraction. It is useful research
  context; this project does not copy that implementation.
- The original project page and first author's public pages were checked for source releases.
  No public original FlowRep core release was located. This is not a claim that no private
  or separately distributed implementation exists. The PyPI package named “flowrep” is an
  unrelated workflow-representation library.

## What FlowRep represents

FlowRep produces a sparse **3D curve network**, rather than a low-polygon replacement mesh.
Curvature-aligned curves describe smooth changes in form; trimming curves describe boundaries
and sharp features. Its sparse network is intended to convey shape from informative static views.

The three criteria are cycle descriptiveness, flowline dominance, and projectivity.
Curves close to planar/geodesic paths and meaningful orthogonal crossings are preferred.
The surface reconstruction results in the paper use a separate surfacing algorithm.
This project implements extraction and drawing, not that reconstruction algorithm.

## Mapping to the implementation

### Meshes and reliable flowlines

The topology graph contains the original polygon edges. A separate triangulation evaluates normals,
curvature, projection and occlusion. OBJ negative indices, mixed face arities, disconnected components,
open boundaries, duplicate vertices/faces and winding correction are handled before extraction.
Disconnected face fans touching at a vertex are split into separate vertices and recorded in
the preparation metadata. This repair is needed by the supplied espresso and plane inputs.
Remaining non-manifold edges, branching vertex links and non-orientable surfaces are rejected.

Triangle inputs use PyNanoInstantMeshes 0.0.3, an Instant Meshes binding with Windows ABI3 wheels.
The binding is called with 4-RoSy/4-PoSy, extrinsic smoothing, boundary alignment, feature detection
and deterministic mode. Its mandatory pure-quad subdivision multiplies the nominal output count
by approximately four, so the requested intermediate count is corrected accordingly.
The final number of faces remains approximate. Anisotropic curvature alignment and sampled
surface deviation are reported; successful remeshing alone is not proof of an ideal FlowRep input.

Initial signed affinities follow equations 6–7: positive near parallel, negative near orthogonal,
with negative weight -5, angular widths 5° and 15°, and opposite-quad weight 0.05.
The objective maximizes the sum of affinities within clusters. Sparse greedy additive edge
contraction initializes the labeling; Kernighan–Lin pair moves retain only improving prefixes,
and singleton splits address negative internal affinities. Ties and final labels are deterministic.

Initial paths end at ambiguous/irregular and trim vertices. The second graph uses endpoint tangent
affinities with 7.5° width, normalized opposite-edge affinities, and -25 crossing penalties.
Reliable strands yield closed cycles before open paths. Weighted tree diameters choose long
remaining paths; projectivity resolves cycle preferences. Opposite-edge revisits expose spiral turns.

### Descriptiveness and simplification

Selected edges block the face-adjacency graph. Its connected regions represent patches.
Boundary cycles are traced through oriented halfedges, including slits and open boundaries.
Non-planar regions with multiple boundary cycles receive 90° error.
That rule is checked even when a thin annular strip has no interior vertices.
Planar patches skip unnecessary boundary tracing internally; their cycle count is left uncomputed.

For an interior vertex, the implementation interpolates boundary normals along each crossing
reliable flowline, takes the largest directional prediction error, and then the 90th percentile
over the region. Consistent with the paper, isolated unpredicted vertices are omitted and counted.
A wholly unpredicted non-planar region is explicitly uncovered.
Geometrically planar patches have zero error, avoiding spurious penalties from normals averaged
across sharp trim boundaries.

Trim-only initialization is refined by joint insertion of flowlines crossing uncovered regions.
Joint insertion supports non-separating loops on positive-genus surfaces. The simplifier scores
flowlines and compatible composites with projectivity plus 360° minus removal-based dominance.
Proposals include endpoint shortening and are committed only after every affected patch satisfies
the threshold. A priority queue refreshes neighboring candidates after each accepted removal.

Local optimization tries the two strongest available parallel neighbors in the same strand,
checking the full context-dependent objective before accepting a replacement. Default search
uses two passes, with early exit when nothing improves. The optional final decluttering step
allows the paper's 20% error relaxation; it is disabled by default and reported explicitly.

Regularization uses tangent-plane Gauss–Seidel smoothing, angle equalization at crossings and
T junctions, and projection to the original surface. Compatible near-planar and near-linear
constraints are solved together at shared vertices. Snapping displacement and surface distance
are bounded by half the average input edge length. Coverage is rechecked using interpolated
reference-surface normals; a violating regularization is rolled back.

### Interpretations of inconsistent printed formulas

These choices are tested and should be preserved when porting:

- A normal at the first boundary intersection must reproduce that boundary normal exactly.
  With distances measured from the query to each endpoint, the opposite distance supplies
  each interpolation weight. This corrects the printed distance/weight inconsistency.
- Geodesicity uses the angle between the surface normal and the curve's osculating plane,
  as stated in the prose. A great-circle arc therefore has zero geodesicity penalty.
  The angle between the two normals printed in the equation would give the opposite behavior.
- Planarity uses an actual mean of valid unsigned plane-normal angles. Straight segments
  have no unique osculating plane and receive zero local bending cost.
- Refinement runs while an uncovered region is above the threshold. The appendix's reversed
  inequality and its C1/C2 naming inconsistency are treated as typographical errors.

### Rendering

The renderer uses Atlas's in-memory frame, camera, ray-casting and paper conventions.
Extraction is camera independent. Display curves are attached to the original surface before
visibility testing to prevent planar-snapping self-occlusion artifacts. Visibility is sampled in
paper units, including intermediate points even when segment endpoints have matching visibility,
and transitions are bisected.

Silhouettes are geometric front/back triangle boundaries, not traced raster contours.
Near-plane and paper-frustum clipping happens before sampling. Projected strokes retain network
junctions as fixed endpoints. Exact coincident ink is removed before short-stroke filtering and
Atlas-style nearest-endpoint ordering.

SVGs contain native paths only. Fitted cubic segments are checked using a convex-hull bound on
the complete cubic against each original piecewise-linear interval. This supplies a continuous,
two-sided correspondence bound instead of checking only sampled fit points. Control hulls stay
inside the drawing area; unfittable sections retain line segments. Export rounding is much smaller
than the reserved fitting tolerance.

## Differences and limits

- This is independently implemented approximate optimization, not the original binary.
  Its networks can differ substantially from the authors' examples even when the measured
  descriptiveness constraint is satisfied.
- Sharp trims use conservative polygon dihedral angles and open boundaries, rather than
  reproducing Yoshizawa's full ridge/valley detector. Small coarse meshes can make smooth
  bends appear sharp; increase resolution or adjust the sharp-feature angle in that case.
- Reliable open-path selection uses weighted tree diameters; spiral handling and local search
  are bounded practical implementations. These choices are visible in the reported networks.
- The geometric normal metric depends on sampled directions and does not guarantee perceptual
  equivalence, full worst-case curvature accuracy, or the paper's surface-reconstruction error.
- Automatic quad preparation can drift from curvature directions, particularly on noisy scans,
  thin parts and isotropic areas. The original surface is kept for rendering, but preprocessing
  cannot guarantee identical curve placement across remesh resolutions.
- Global symmetry is not enforced. The paper also relies on externally prepared symmetric inputs
  for several examples. Reproduction is deterministic for fixed prepared geometry.
- Visibility sampling defaults to 0.35 mm on paper. Features thinner than the sampling scale may
  require a smaller step. The scalar reference ray caster remains available when Embree is absent.
- Rendering may emit more pen lifts than an artist's drawing because it preserves real junctions,
  clipping boundaries and occlusion transitions.
- Sparse Python optimization is slower than the authors' C++ implementation on complex meshes.
  Camera-only redraws reuse the cached extraction and avoid that cost.

The evaluation report compares both networks through the same renderer. Sampled curve distance
measures agreement with an author network; it is not a surface-reconstruction error or an
independent perceptual score.
Timing is recorded per stage. Memory snapshots report the working set and cumulative process peak
at stage boundaries; they are not allocation traces or separately isolated stage peaks.

