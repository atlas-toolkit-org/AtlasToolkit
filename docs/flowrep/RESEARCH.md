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
In a preserved polygon input, remaining non-manifold edges, branching vertex links and non-orientable
surfaces are rejected. Remeshed inputs are healed first (below).

Triangle inputs use PyNanoInstantMeshes 0.0.3, an Instant Meshes binding with Windows ABI3 wheels.
The binding is called with 4-RoSy/4-PoSy, extrinsic smoothing, boundary alignment, feature detection
and deterministic mode. Its mandatory pure-quad subdivision multiplies the nominal output count
by approximately four, so the requested intermediate count is corrected accordingly.
The final number of faces remains approximate. Anisotropic curvature alignment and sampled
surface deviation are reported; successful remeshing alone is not proof of an ideal FlowRep input.

Instant Meshes' extraction leaves local defects, and which ones appear depends on the resolution. They
include near-duplicate vertices, quads with two welded corners, folded pairs of quads with the same corners,
"fin" edges shared by four quads around a pinched singularity, and holes in closed surfaces. A 524k-triangle
sculpted head showed some of these at every resolution tried. `mesh._heal_polygons` repairs the remesh before
the strict loader runs:
1. It welds vertices within 1e-6 of the diagonal.
2. It drops polygons that repeat a corner, all copies of a folded face and zero-area faces.
3. It removes the faces around any vertex on an edge with more than two faces, or with a branching link,
   until none is left.
4. It patches every open loop that is not a real opening of the reference surface (a loop with a vertex
   within one mean edge of the reference's boundary is kept), using Atlas's hole patcher (`geom.repair.fill_loop`,
   or a centroid fan standalone), and projects the new vertices onto the reference surface.

Holes therefore no longer become trim loops. If the healed remesh still fails the checks, the target is
retried at +4 % and -4 %. The repairs are recorded as `remesh_repairs` in the preparation metadata and the
network diagnostics.

Initial signed affinities follow equations 6–7: positive near parallel, negative near orthogonal,
with negative weight -5, angular widths 5° and 15°, and opposite-quad weight 0.05.
The objective maximizes the sum of affinities within clusters. Sparse greedy additive edge
contraction initializes the labeling; Kernighan–Lin pair moves retain only improving prefixes,
and singleton splits address negative internal affinities. Ties and final labels are deterministic.

Initial paths end at ambiguous/irregular and trim vertices. The second graph uses endpoint tangent
affinities with 7.5° width, normalized opposite-edge affinities, and -25 crossing penalties.
Reliable strands yield closed cycles before open paths. Weighted tree diameters choose long
remaining paths; projectivity resolves cycle preferences. Opposite-edge revisits expose spiral turns.
Spirals that climb more than one quad per turn never lie next to themselves, and the opposite-edge
test misses them (a remeshed rounded cube had one flowline winding it 11 times). A flowline that comes
back within 2.5 average edges of an earlier point, at least eight edges further along and running the
same way, has completed a turn: that turn is closed into a ring through the shortest mesh path back.
This follows the paper's §6.2 (split spirals once they complete a full cycle).

### Descriptiveness and simplification

Selected edges block the face-adjacency graph. Its connected regions represent patches.
Boundary cycles are traced through oriented halfedges, including slits and open boundaries.
Non-planar regions with multiple boundary cycles receive 90° error.
That rule is checked even when a thin annular strip has no interior vertices.
Planar patches skip unnecessary boundary tracing internally; their cycle count is left uncomputed.

For an interior vertex, the implementation interpolates boundary normals along each crossing
reliable flowline, takes the largest directional prediction error, and then the 90th percentile
over the region. Consistent with the paper, isolated unpredicted vertices are omitted and counted.
A wholly unpredicted non-planar region is explicitly uncovered, unless all it has inside is up to three
vertices: flowlines end at remesh singularities rather than cross them, so a few faces round a
singularity could never be predicted. Treating them as uncovered blocked the removal of every flowline
next to a singularity, and a remeshed teapot stayed a dense grid (120 flowlines; 81 now).

A region with no interior vertex at all (a strip one quad wide, or one quad) is judged by its boundary:
each stretch between two junctions is predicted from the normals at its ends, as interior vertices are
along a crossing flowline. A fully dense network (every side one edge) still scores 0. Without this
rule, zero interior vertices meant zero error, which made the dense start network trivially
descriptive. The simplifier then removed meridians first (open, with endpoint costs) down to the one
needed to break the annuli, and the rings could no longer go. Bottles, glasses and handles came out as
stacks of parallel rings. With it, the paper's models come out close to the authors' networks (see
Validation below).
Geometrically planar patches have zero error, avoiding spurious penalties from normals averaged
across sharp trim boundaries.

Trim-only initialization is refined by joint insertion of flowlines crossing uncovered regions.
Joint insertion supports non-separating loops on positive-genus surfaces. The simplifier scores
flowlines and compatible composites with projectivity plus 360° minus removal-based dominance.
Proposals include endpoint shortening and are committed only after every affected patch satisfies
the threshold. A priority queue refreshes neighboring candidates after each accepted removal.

Local optimization tries the two strongest available parallel neighbors in the same strand,
checking the full context-dependent objective before accepting a replacement. Default search
uses two passes, with early exit when nothing improves.

The final decluttering step follows §7.2. It looks at every pair of selected flowlines that share
no vertex and run side by side, with the shorter one's median distance (and 60% of its vertices)
within two average edge lengths of the other. Of each pair it removes the flowline whose removal
raises the error least, when every affected region stays within the paper's 20% relaxation
(dmax × 1.2). The library leaves it off (`relaxed_declutter`); FlowRep Sketch turns it on by default
(*Declutter parallel curves*). It used to consider only flowlines one quad apart in the same strand,
which missed most bundles.

The network then gets the paper's last shortening pass (§7.2: "no open-ended curves"). A flowline
end that stops in nothing is valence 1, or valence 2 at a corner of 90° or less; an obtuse valence-2
joint reads as one continuing curve and is left alone. Such an end first tries to reach the network
along its own flowline, at most three more edges. Otherwise it is shortened back to its last junction
(cascading to curves that this frees). Each change is checked like a removal and kept only when every
affected region stays within the bound. `dangling_ends_before`/`_after` and `ends_extended`/`_shortened`
are reported.

Regularization uses tangent-plane Gauss–Seidel smoothing, angle equalization at crossings and
T junctions, and projection to the original surface. Compatible near-planar and near-linear
constraints are solved together at shared vertices. Snapping displacement and surface distance
are bounded by half the average input edge length. Coverage is rechecked using interpolated
reference-surface normals; a violating regularization is rolled back.

### Speed (2.5.1)

Profiling the sculpted head at 10k quads (cProfile; 658 s in all) showed where the time goes: 38 s
in clustering and 611 s in simplification. Removing one flowline reshapes every region along it, so
the simplifier measured 1.45 million regions; most of them it had measured before. The changes:

- **Region measures are cached** under a key of exactly what a measure reads: the region's faces,
  which edges round its vertices are blocked, and which of its flowlines count as crossing (positions
  and normals are fixed until regularization, which clears the cache). The key is two order-free sums
  of 64-bit hashes, computed for all of a proposal's regions at once. Whole assessments (the regions
  a proposal makes from the affected faces) are cached the same way. A measure no longer allocates
  arrays the size of the mesh, and partitions read only the affected faces' edges.
- **The local search's energy is incremental.** Each flowline's removal cost records the vertices it
  looked at (the flowlines its shortening examined and the regions it reshaped); a swap stamps the
  vertices of the edges it changes, and only costs that read a stamped vertex are recomputed.
- **Vertex degrees are kept with the edge counts**, not counted again for every proposal, and a
  flowline's range change is cut by index arithmetic.
- **The edge-affinity graph is built with array operations** (the same arcs and weights, in the same
  order), and clustering refinement passes count with arrays.
- **Kernighan-Lin refinement works on a window.** For each pair of neighbouring clusters, only the
  nodes where they touch, grown by two arcs into each, may move (`KL_DEPTH`); their neighbours outside
  still count in the gains. Refining over both whole clusters made a pass cost the size of a cluster
  times the number of its neighbours, which grew as faces^1.5 (86 s at 20k quads, 9 s now). Moves far
  from the interface never paid off: on the head at 20k the window's objective is the full refinement's
  (edges: 30510.27 both; flowlines: 2515.65 against 2514.73).
- **Clustering reports progress** in its own steps (merging, then each refinement pass), instead of
  holding the bar at "building signed edge-affinity graph" for the whole step.
- **Uniform meshes load with array operations.** When every face has the same number of corners (scans,
  sculpts, STL), welding, duplicate and sliver removal, the non-manifold checks and orientation are
  computed for all faces at once, with exactly the general path's result and messages. Anything else
  (mixed polygons, a polygon that loses a corner, touching sheets to split) takes the general path.
  Loading the 524k-triangle head went from 32 s to 2 s.

Everything but the window is exact: with full refinement the networks are identical to before,
bit for bit (checked on the head at 3k and on every benchmark model whose drawing changed). The window
changes a few networks slightly (4 of 14 benchmark models at 6000 quads; the other 10 drawings are
byte-identical); see Validation.

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

Network curves follow the quad proxy's edges, so they kink at every proxy vertex, while the paper's
drawings show smooth regularized curves. Before projection, each curve between junctions is resampled
at half an average edge and Taubin-smoothed (λ = 0.5, μ = -0.53, 30 × *smoothing* passes; trims get
half). This takes out kinks about two edges long and leaves bends of ten edges or more practically
untouched. Junctions stay fixed, and every sample is laid back on the reference surface.

Silhouettes are interpolated contours by default (Hertzmann and Zorin 2000). They are the zero set of
n·(eye − p) over the reference's vertex normals, smoothed by 2 + 6 × *smoothing* neighbour averages,
with one segment per triangle the zero set crosses. The segments are chained through shared edges
into long curves. Averaged normals mean nothing along a real crease (a cut base, a box's edges): there
the interpolation would cut across flat faces or lose the outline. Real creases are clusters of edges
sharper than 60° that are at least 8% of the model's size; smaller clusters are sculpt noise. Triangles
touching them are left out of the interpolation, and the contour there is the exact one: the edge
between a front- and a back-facing face. Open boundaries are added. Contour pieces shorter than 2 mm on
paper (loops around noise) are left out. A ray to a contour point grazes the surface, so visibility is
tested half an average edge off the surface along the normal. The contour is drawn where it lies: an
earlier build drew the lifted points, which moved the outline outward visibly on coarse models
(15k-triangle Venus). The older front/back triangle boundaries remain as *Silhouette style: Mesh
edges*. On a 524k-triangle sculpted head they gave 652 pieces in one view; the contours give 32.

Near-plane and paper-frustum clipping happens before sampling. Hidden stretches shorter than 0.4 mm
between visible runs of one curve are drawn (grazing rays flicker). In each layer, strokes whose ends
meet within 0.05 mm and continue each other (turning by 40° or less) are joined, best-aligned pairs
first: a flowline through a junction becomes one stroke, a corner stays two. Exact coincident ink is
then removed before short-stroke filtering. Loose ends are then tidied on paper. An end that touches
nothing but stops within 0.8 mm short of another stroke, heading towards it (within 60°), is drawn on to
meet it: a curve passing behind a contour ends on it rather than a hair before. An open stroke under
3 mm that touches nothing at either end is a floating fleck and is left out (`snap`, `floating`).
The contour lift is capped at 1% of the model's size. The same lift was tried for network curves; it
was dropped, because curves on the far side near the outline then peeked past it (more fragments on
smooth models). Strokes are then put in Atlas-style nearest-endpoint order.

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
  reproducing Yoshizawa's full ridge/valley detector. On a remeshed input, a crease must also be
  sharp on the reference surface: the area-weighted normals a fifth of an edge to each side must
  differ by at least 75% of the sharp-feature angle. Otherwise the coarse proxy turns tight but
  smooth folds (nostrils, lids, ears) into trims. Clusters of creases shorter than *Shortest sharp
  feature* (4 edges) that close no loop and touch no boundary are dropped. On the sculpted head,
  587 proxy creases became 61, and trim curves fell from 228 to 13. Short crease spurs that leave a
  junction and stop in nothing are pruned by the same length (that took 85 creases to 61).
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
- Rendering still lifts the pen at real occlusion transitions, clipping boundaries and corners. Strokes
  that continue each other across junctions are joined.
- The paper uses dmax = 20° for design shapes and 30° for organic ones (dog head, phone). On sculpted
  organic models, 20° keeps many parallel flowlines, because without sharp features nothing else
  bounds the regions; 25-30° is closer to the paper's look there.
- Sparse Python optimization is slower than the authors' C++ implementation on complex meshes, though
  since 2.5.1 its time grows about linearly with the remesh resolution (Speed, above). Camera-only
  redraws reuse the cached extraction and avoid that cost.

The evaluation report compares both networks through the same renderer. Sampled curve distance
measures agreement with an author network; it is not a surface-reconstruction error or an
independent perceptual score.
Timing is recorded per stage. Memory snapshots report the working set and cumulative process peak
at stage boundaries; they are not allocation traces or separately isolated stage peaks.

