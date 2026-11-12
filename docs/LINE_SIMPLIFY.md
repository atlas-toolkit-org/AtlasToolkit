# Line Simplify 0.1.0

**SVG · post-process → Line Simplify** reduces the number of vertices in a drawing and saves
`<name>_simplified.svg`. The input remains untouched. UI, command line, saved presets, batch jobs,
history and Workflows use the same tool contract and geometry. No new dependency or external executable.

Start with a small Maximum deviation, inspect Before / After at drawing scale, then increase it. The preset packs
add looks from **Fine pen** (the gentlest) to **Stronger**; **Organic** uses effective-area reduction.
These are physical distances on the sheet, including SVG units, transforms and viewport aspect ratios.
Simplification reduces vertices, not the number of pen strokes or overlapping lines.

| Control | Effect |
|---|---|
| Algorithm | Native GEOS Douglas–Peucker, or min-heap Visvalingam–Whyatt effective area. |
| Maximum deviation | Maximum distance from the original polyline span to each replacement chord. Zero preserves geometry. |
| Area strength | VW removal threshold = strength × deviation², in mm². Distance constraints still apply. |
| Curved paths | Keep original curves (default), or adaptively convert curved paths into lines before simplification. |
| Protect topology | Check the entire swept region against original segments and already accepted shortcuts, across all pens. |
| Keep turns above | Protect sharp accents. Lower values lock more corners; 0 locks every vertex; 180 disables locking. |
| Preserve stroke reversals | Keep backward movement along each candidate chord; protects retracing, hooks and gesture. |
| Maximum local length loss | Limits loss of waviness, independently for every replaced span, relative to its original arc length. |
| Maximum loop area change | Revert a closed stroke if its signed area changes too much. Preserve winding and at least three distinct vertices. |
| Curve error budget | Share of deviation reserved for curve approximation (default 20%); the rest remains for simplification. |

The guards take priority over reduction. Dense hatch intersections, overlapping strokes, tight motifs and
accented corners can legitimately retain most of their points. The report lists input/output sampled
vertex counts, removed percentage, ink length, measured span error and subdivided proposals.
Counts for retained curves refer to the sampled representation used for analysis, not SVG command counts;
vertex reduction is not a promise of proportional byte-size reduction.

## Drawing preservation

Endpoints, subpath order, separate pen strokes, groups/layers, transforms, element IDs and styles remain.
Paths stay paths; polylines and polygons retain their element types, so element-specific styling is retained.
Coordinates use round-trip floating-point serialization without decimal quantization. Exact retained endpoints
therefore do not disconnect through rounding. No joining, smoothing, snapping, rasterization or stroke deletion.
Rectangles, circles and ellipses remain original; their sampled boundaries constrain other strokes.

The topology guard is deliberately conservative. It rejects shortcuts crossing other strokes, erasing an
existing intersection, touching an interior junction, or sweeping across even an entirely enclosed motif.
Zero-length stroked segments act as point constraints, protecting stipple dots as well as line motifs.
Existing intersecting/overlapping spans remain original instead of splitting strokes at crossings. Already
accepted shortcuts constrain later shortcuts, including other subpaths of the same element. A closed-loop
area rejection rolls back that loop's proposed shortcuts from the dynamic index.

Curve topology is checked against an approximation at the Curve error budget resolution. It is **not an
exact-arithmetic guarantee for arbitrarily close analytic curves**. Keep original curves and reduce the error
budget for very fine curved details. Converted curves share the total distance budget with simplification;
adaptive Bézier subdivision bounds control hulls against finite chords, and arcs use a second-derivative
interpolation bound. A sampling budget stops excessively large jobs with a useful error instead of silently
capping accuracy. Preview computes the same geometry as Run.

Documents containing referenced instances (`use`), text, scripts, CSS geometry/transform properties, imported
stylesheets, stylesheet-driven markers/dashes, animation or nested SVG
viewports are kept unchanged, with an explanation in the report. Expand these in your vector editor first.
Ordinary fill/stroke stylesheets are retained. Elements with inherited markers, dashed strokes, path effects or `pathLength` remain original because their
appearance can depend on vertex placement or arc length. Definitions and clipping resources remain intact.
Stale plot-data caches are removed only when geometry changes. Outputs receive the Atlas signature and a
restorable settings record even if no geometric change was needed.

## Command line and workflow

```bat
atlas-toolkit run svg-simplify drawing.svg --preset "Faithful"
atlas-toolkit run svg-simplify drawing.svg --method vw --tolerance 0.1 --length-loss 2
atlas-toolkit run svg-simplify drawing.svg --curves flatten --tolerance 0.05
atlas-toolkit run svg-simplify drawing.svg --dry-run
```

[Line Simplify workflow](workflows/line_simplify.atlasflow): SVG input → Line Simplify → Save.
The tool can also follow any mesh/image drawing tool and precede Overlap Cleaner. A small preview sample
is included in the contract self-test. `scripts/dev/validate_line_simplify.py` creates benchmark results, signed
sample outputs and a before/after comparison under an explicitly selected output directory.

On the development machine, a 100,000-vertex / 100-stroke synthetic flowing-line drawing at Faithful
settings reduced to 3,300 vertices with DP (96.7% removed, measured span error 0.04873 mm); runs took
about 1.4–2.6 seconds including topology checks. This is a reproducible workload, not a speed guarantee.
An existing 5,198-sample Atlas drawing reduced by only 1.06% under those same conservative constraints.

## Research and implementation decisions

Research checked on 2026-11-02, against primary project documentation:

1. [CGAL 6.2.1, 2D Polyline Simplification](https://doc.cgal.org/latest/Polyline_simplification_2/index.html)
   separates removal cost, stopping predicates and topological constraints. It explicitly protects nesting as
   well as intersections. This motivates the swept-region test, not just checking whether a new chord crosses
   another line. Atlas does not bundle or claim to implement CGAL's constrained-triangulation algorithm.
2. [Shapely simplify](https://shapely.readthedocs.io/en/stable/reference/shapely.simplify.html) exposes GEOS's
   compiled Douglas–Peucker implementation. Used for fast candidate generation; Atlas adds corner splitting
   and checks every candidate against the original span. An independent preserve-topology call per path would
   not protect motifs across paths/pens, so it is insufficient here.
3. [Mapshaper's current simplification guide](https://mapshaper.org/docs/guides/simplification.html) compares
   distance and effective-area methods. Weighted VW preferentially removes acute points to smooth output;
   that can erase intentional sketch accents. Atlas uses unweighted, monotone effective areas, protected
   corners and an additional hard distance cap. No copied Mapshaper implementation.
4. [Mike Bostock's algorithm demonstration](https://bost.ocks.org/mike/simplify/) describes heap-based
   effective-area removal. Atlas implements a min-heap with versioned entries and linked neighbours:
   O(n log n) candidate selection, O(n) storage. A gradual reduction option complements distance-first DP.
5. [psimpl Douglas–Peucker](https://psimpl.sourceforge.net/douglas-peucker.html) documents output-sensitive
   complexity and radial prefilters. Atlas avoids a radial prefilter so errors do not accumulate outside the
   requested displacement budget. A forced target point count is also omitted: it can sacrifice motif fidelity.
6. [Shapely STRtree](https://shapely.readthedocs.io/en/stable/strtree.html) supplies an immutable native index
   of original segments. Existing Rtree/libspatialindex supplies a dynamic accepted-chord index. Vectorized
   GEOS intersections filter only spatial candidates. No all-pairs intersection pass.
7. [SVG 2 coordinate systems](https://www.w3.org/TR/SVG2/coords.html) defines viewport aspect handling and
   root transforms outside the viewBox mapping. The physical metric respects this ordering and uses the
   largest singular value to budget flattening under non-uniform group transforms.

Both candidate methods are established algorithms backed by current high-performance implementations and
data structures. Neural re-vectorization, free Bézier refitting and smoothing were not selected: they can move
extrema or change stroke character, and do not supply the local preservation contract required here. Topology
validation and original-span distance checks add input-dependent work; worst-case overall complexity is not
claimed to remain O(n log n). Candidate and validation passes check cancellation; jobs run in an isolated worker process.
