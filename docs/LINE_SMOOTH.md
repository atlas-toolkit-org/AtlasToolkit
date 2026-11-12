# Line Smooth 0.1.0

Line Smooth is an independent tool in **SVG · post-process**. It filters a stroke's coordinates along
physical arc length to suppress high-frequency zigzags, teeth and irregular kinks. It does not run
Douglas–Peucker, Visvalingam–Whyatt, point selection, curve fitting or any vertex-reduction pass.
Every source vertex remains in the validation partition; output often contains **more** points.

Load an SVG, choose **Gentle** or **Surface stripes**, and compare **Before / After** at drawing scale
and zoomed in. Run writes `<name>_smoothed.svg` on the original sheet. Layers, ids, styles, transforms,
pen order, stroke order and closure stay with the original elements. Outputs are signed `svg-smooth`
and include restorable settings in Atlas metadata. Preview and Run use identical geometry.

## Controls

| Control | Meaning and practical use |
|---|---|
| Algorithm | **Whittaker curvature** is the default smoothness/fidelity solve. **Taubin compensated** has a flatter low-frequency response and can better retain broad flowing gestures. Neither promises zero geometric shrinkage. |
| Smoothing span | Physical wavelength reduced to roughly half amplitude before blending and guards. Shorter wavelengths are suppressed more; longer gestures survive. Increase to remove coarser teeth. It is not a simplification tolerance. |
| Strength | Blend with the source. 100% requests the full filtered result; lower retains more drawing texture. Zero is an exact geometry no-op. |
| Maximum displacement | Maximum matched-point movement in **millimetres on the sheet**, continuously along the sampled source and output. Begin below the pen width. Zero retains geometry. |
| Keep persistent turns above | Protect angle peaks measured across Corner support, rather than locking every raw tooth. Lower protects more corners. 0 locks every source vertex; 180 disables this protection. |
| Corner support | Distance to look along each side of a point. Set larger than the tooth scale. A tiny support can mistake noise for intended corners and stop smoothing. |
| Protect topology | Default on. Pin source segments at crossings and junctions, retain self-crossing/retraced strokes, check motion against other ink across every pen and check earlier accepted results. Dense contacts can substantially limit smoothing. |
| Maximum loop area change | Backtrack the blend to keep a closed stroke's area within this percentage. Winding remains unchanged even with topology checking off. |
| Maximum stroke length change | Limit total ink-length change per stroke. De-jagging necessarily shortens noisy ink: strict length limits can retain visible teeth. This is an independent gesture safeguard. |
| Curved paths | Keep original Béziers and arcs by default. **Also smooth curves** uses bounded sampling and exports changed curves as dense straight segments. |
| Sample spacing | Maximum physical arc-length spacing for the filter grid, also capped at span/8. Lower gives finer vector output and more work. Source vertices remain in the common source/output partition. |
| Curve sampling error | Approximation bound for curved obstacles and optional curved inputs. When smoothing curves, this error is reserved from Maximum displacement. It must be smaller than that cap. |
| Smooth filled outlines | Off by default. Filled artwork remains original as an obstacle unless explicitly enabled. |

The report lists smoothed strokes, displacement, achieved average blend, limited strokes, sample count
and ink length. A low achieved blend means a displacement, area, length or topology guard intervened.
The whole stroke's blend is reduced uniformly; pointwise clipping is avoided because it can add kinks.
After ten unsuccessful backtracking trials the original stroke is retained.

## Surface Stripes and fragmented drawings

Use **Surface Stripes → Plotter Vision / Hatch Maker → Line Smooth → Save**. For an existing SVG, use
the [SVG workflow](workflows/line_smooth.atlasflow). The **Surface stripes** preset (in the preset packs) keeps
the span, displacement and corner support small, at full strength, with all topology guards enabled.

Visible-line export may fragment a stripe into many separate paths. Two-point strokes have no interior
geometry to smooth while keeping endpoints fixed; gaps are not inferred or joined. A drawing with thousands
of fragments may therefore smooth a smaller fraction of its stroke count. Exact contacts and tiny motifs
take priority. The source 3D mesh and the Surface Stripes generator are unaffected.

## Algorithms and research decisions

Each smoothable span is parameterized by **physical arc length** in the complete affine sheet metric,
including nested transforms, reflections and nonuniform `preserveAspectRatio="none"` viewports. Exact
piecewise-linear box integration precedes uniform sampling, preventing dense sawteeth from aliasing into
spurious low-frequency waves. Protected corners/contacts split open filter spans; unanchored rings use
periodic filtering without an arbitrary fixed seam. Translating each span to a local origin improves stability.

**Whittaker–Henderson** minimizes `sum ||q-p||² + lambda sum ||D2 q||²`. The open-span solve eliminates
fixed endpoint values and applies a three-band Cholesky solve to both coordinates, in O(n) time/storage.
For very large penalties an equivalent discrete-sine spectral solve avoids numerical conditioning problems;
periodic rings use FFT. This choice follows the banded formulation discussed in the
[2022 comparison of smoothing filters](https://pmc.ncbi.nlm.nih.gov/articles/PMC9026279/) and the
[2026 scalable Whittaker paper](https://arxiv.org/html/2604.00048v1). The latter studies satellite signals;
using the deterministic smoother on arc-length curve coordinates is Atlas's adaptation, not a claim that
its neural training or remote-sensing results apply to drawings. The established method remains suitable
with current compiled band solvers; no trained model or new dependency is needed.

**Taubin compensated** uses a positive and a negative Laplacian pass, adapted from
[Taubin's signal-processing fairing method](https://www.cs.jhu.edu/~misha/ReadingSeminar/Papers/Taubin95.pdf).
Atlas uses lambda=0.5 and mu=-0.5, giving `H(k)=(1-0.25 k²)^N` for the normalized path Laplacian. Equal
opposite passes cancel the leading low-frequency contraction term without amplifying broad modes. The
pass count is calibrated to the requested physical wavelength, then the polynomial is evaluated directly
with FFT (rings) or DST-I (fixed-end spans), O(n log n), avoiding potentially thousands of explicit passes.
This is a deliberate variant, rather than Taubin's commonly used unequal coefficients. Independent
regressions compare spectral results to literal contraction/expansion iterations.

Both filters use compiled SciPy operations already bundled with Atlas:
[banded solver](https://docs.scipy.org/doc/scipy/reference/generated/scipy.linalg.solveh_banded.html),
[DST](https://docs.scipy.org/doc/scipy/reference/generated/scipy.fft.dst.html), and
[FFT](https://docs.scipy.org/doc/scipy/reference/generated/scipy.fft.rfft.html).

Alternatives considered: plain Gaussian/Laplacian averaging contracts broad curves; interpolating splines
pass through noisy vertices; unconstrained automatic-knot smoothing changes representation and makes
local limits harder to enforce. Classic Savitzky–Golay has weak stopband suppression and boundary artifacts
described in the 2022 paper. Modified-sinc and weighted Savitzky–Golay filters are credible alternatives,
but broader extrapolation kernels complicate exact contact/corner constraints. These are engineering
tradeoffs, not a claim that the selected two methods universally outperform every newer smoother.

## Geometry guarantees and limits

The output is a **vector SVG made of dense line segments**, not raster tracing or a compact cubic refit.
All original polyline breakpoints and the filter grid are merged into a shared parameter partition.
Source and output are linear on each shared interval, so the norm of their displacement is bounded by
its endpoint maximum throughout that interval. For changed curves, the bounded source-flattening error
is added to this budget. Round-trip precision and original local coordinates at exact anchors prevent
decimal output rounding from undoing endpoint locks.

The conservative guard checks convex swept regions against original other-stroke geometry and earlier
accepted strokes, rather than testing only final intersections. It therefore protects small motifs that
could be swept over without intersecting the final stroke. Original self-crossing or retraced strokes stay
intact. Candidate simple strokes remain simple; closed candidates retain winding and area limits.
Basic shapes remain original. Dots, repeated-point strokes, markers, dashes, path effects, clipping,
filters and masks are retained. Documents containing instances, CSS, text, animations or nested viewports
are retained with an explicit expansion message; flatten/expand them in an SVG editor first.

Topology checks operate on **sampled** curved obstacles; they are not exact algebraic Bézier intersection
certificates and do not account for physical pen thickness. Lower Curve sampling error for near contacts.
The guard is intentionally conservative and can limit useful smoothing in crowded ink. The two-million
sample budget fails explicitly rather than silently reducing detail. Cancellation is checked while parsing,
indexing, filtering spans, checking swept regions and processing files; individual compiled solves cannot
be interrupted internally, and the tool runs in Atlas's cancellable worker process.

## Validation and use

```powershell
.\atlas-toolkit.exe run svg-smooth drawing.svg --preset "Surface stripes" --out-dir output
.\atlas-toolkit.exe run svg-smooth drawing.svg --method taubin --span 2 --deviation 0.1 --strength 80
.venv\Scripts\python -m unittest discover -s tests -p test_svg_smooth.py
.venv\Scripts\python scripts\dev\validate_svg_smooth.py --source path\to\stripes.svg
```

The validation script writes before/after PNGs, signed SVGs and timing reports under
`build/qa/svg_smooth_validation`. On this workstation, a 100,000-sample filter pass took about
0.02 s for Whittaker and 0.05 s for spectral Taubin. An existing 3,224-stroke Surface Stripes SVG took
about 2 s including parsing, all-pen guards, preview geometry and SVG export: 813 strokes smoothed,
maximum movement 0.2 mm, total ink length 5866.6 → 5831.0 mm. These are observations, not timing guarantees.
