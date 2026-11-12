# Library and data contracts

The library imports without Qt. All extraction coordinates use the source model's units;
rendered Drawing coordinates use millimetres. The source mesh is never edited on disk.

## Extraction

    mesh = load_mesh("shape.obj")
    options = FlowRepOptions(dmax=20, cache_dir=".cache/flowrep")
    network = extract_network(mesh, options, progress, cancel)

PolygonMesh keeps ordered polygon faces and a separate triangulation. A prepared proxy also
keeps the original reference surface. from_arrays(vertices, polygon_faces) is available for
an in-memory host. Degeneracies and duplicate geometry are cleaned, disconnected vertex fans
are split and reported, and unsupported topology raises FlowRepError.

CurveNetwork has a shared float64 vertex array and ordered int64 vertex-index curves.
Curve order supplies persistent curve identity; edge_ids refer to prepared topology, and
strand_ids and kinds preserve extraction identity and trimming semantics. Junctions are
derived from the shared connectivity, so they survive persistence without spatial matching.
Curves may share vertices. A closed curve repeats its first vertex at the end.

Diagnostics include coverage, region errors, refinement, alignment, regularization acceptance,
timings and stage-boundary memory readings. Metadata includes source, options, preparation,
content key, method version and paper attribution. An incomplete strict extraction raises
FlowRepError with the measured maximum error; native remeshing is not interruptible inside
the binding, so an interactive host should use a killable process worker.

## Drawing

    drawing = render_network(mesh, network, Camera(), paper=None,
                             options=RenderOptions(paper="A4"))
    write_svg(drawing, "shape.svg")

An Atlas-compatible Camera and paper Frame can be passed directly. Camera angles, model axes,
scale and paper layout follow the borrowed Atlas helpers. The CLI fits the camera to the
paper; the Atlas adapter preserves the aimed camera. Set fit_camera=False for that behavior.
The renderer attaches display curves to the original surface, checks visibility there, adds
geometric silhouettes, clips, removes coincident ink and orders strokes. It does not change
the stored network. Drawing.layers contains millimetre polylines; write_svg fits bounded
cubics and records fitting statistics. The 0.05 mm bound is relative to those sampled drawing
polylines, not the unsampled surface's exact continuous projection.

Extraction and rendering accept progress(fraction, message) and cancel() callbacks. Progress
fractions range from zero to one within each call. A truthy cancel result raises CancelledError.
A callback may raise a host exception directly. Cancellation checks occur between Python
stages and inside long clustering and simplification loops.

## Persistence

    save_network(network, "shape.network.npz")
    network = load_network("shape.network.npz")

The schema name is atlas-flowrep-network, version 1. JSON stores readable arrays and metadata;
NPZ stores float64 geometry, packed int64 ragged arrays and a JSON header, without pickle.
Writes are atomic. Unknown versions are rejected. Both formats retain the full network state.

OBJ is a geometry interchange format: vertices, line elements and ordered FlowRep # chain
records. It also reads the authors' networks, including reversed edge orientations. OBJ does
not retain the full diagnostics or kind/strand attributes; use JSON or NPZ for working caches.

## Command line

    flowrep-svg extract shape.obj -o shape.network.npz
    flowrep-svg render shape.network.npz --psi=-60 -o another-view.svg
    flowrep-svg draw shape.obj another.obj --out-dir drawings
    flowrep-svg benchmark models --out-dir evaluation --jobs 4 --resume

Use --help on a command for its options. Extraction and drawing support batches of files or
directories; benchmarking uses fresh model worker processes. Resume validates the source hash
and configuration. Rendering an imported author network requires --mesh for visibility.
For an author network normalized independently of its input, --register-reference opts into
surface-verified uniform scale/origin registration. Benchmark evaluation applies this guarded
registration automatically and records any accepted transform. It does not fit a rotation.
