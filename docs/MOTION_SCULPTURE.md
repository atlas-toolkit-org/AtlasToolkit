# Motion Sculpture 0.3.0

Make a still mesh express motion through layered silhouettes, trailing fins and splintered bars.
The visual references are stacks of glass rods and elongated bronze or glass laminae. This is a procedural
interpretation of their motion language, operating on your own input model rather than generating a figure.

Open a closed STL/OBJ/PLY in **Motion Sculpture**, choose **Wind-carved bronze** or **Glass runner**, and Preview.
Send the output to **Sketch Hatching**, **Plotter Vision** or **Contour Lines** for a pen drawing.

## Paint a motion area

Choose **Region → Acts on → Painted area**, then use **Selection → Paint / select area…**.
The modal uses the same editor as Surface Eject: left-drag paints visible source triangles, Shift erases,
Alt+left-drag or Orbit rotates, and the wheel zooms. Undo, Clear, Select all, Load and Export are available.
Rotate the model to paint other sides; hidden faces remain unselected. Click **Use selected area**, then
**Preview motion**. Travel heading still determines the pulling direction.

**Painted geometry → Sliced layers** is the default. It samples the whole source silhouette into closed
plates or bars with real gaps. Only painted, outward-facing section edges grow wakes. Travel heading points
in the travel direction, so the wake grows opposite it. If the painted faces do not face that wake direction,
the report warns that no outward roots were found. Change heading or paint a larger area.

The unextended silhouette is a layer-resolution approximation, rather than the original surface: increase
Layers to retain smaller features. Layer gap controls actual plate thickness; Bar gap separates bars.
Zero gaps retain a microscopic minimum clearance so an STL reload does not weld neighbouring caps.
Selected-edge sweeps preserve the original section and extend it into tapered wakes; they do not need
global deformation backtracking. Leading form moderates the leading side without suppressing the entire
painted front. Paint feather softens extension inside the selection.

Choose **Connected surface** for strict unpainted-skin preservation. It blends neighbouring layer gestures,
uses corrugations instead of open slots, and pins unpainted source skin. Both modes use the same selection.
Selection precision follows the original source triangles. Changing the source mesh requires repainting; rotating the Model frame
does not invalidate a selection. Empty, malformed and wrong-mesh masks fail before publication.

The selection is embedded in settings/history/workflows. Selection file optionally overrides it, using the
same exported JSON format as Surface Eject. File content participates in workflow cache keys. In Workflows,
use Aim in tool, paint, then Back to workflow. Artistic UI presets preserve an existing painted area;
the Held portrait preset explicitly switches to its height band.

Paint weights use exact distances to painted and unpainted source triangles. Sliced layers takes native
cross-sections and intersects source triangles to identify painted roots; polygon unions on a 0.0001%-height
precision grid combine the section with tapered edge sweeps before closed extrusion. Holes and disconnected
limbs are retained. This avoids fragile 3D boolean seams and dense skin refinement. Previews use shape-preserving
reduction to retain thin fins. Wake length responds directly, subject to feather, rhythm, base hold and seed.

Connected surface uses conforming surface refinement. Local triangle orientation checks now reduce only
offending vertex neighbourhoods, leaving safe painted areas strong. Motion retained reports the average
retained displacement. If protection limits the result, expand the selection, increase Paint feather or
reduce reach/curl/taper. The guard does not certify absence of distant self-intersections.
Bounded file-precision stabilization may adjust sub-format edges. Export is staged,
reloaded and checked for a closed, oriented positive-volume solid before replacing an existing output.

## Shape controls

| Control | Effect |
|---|---|
| Structure | Laminae retains broad source slabs. Splinters subdivides slabs into parallel source-cut bars. |
| Gesture | Stream is even; Pulse repeats accentuated fins; Gust has a nested oscillation; Vortex turns the heading through the stack. |
| Travel heading | Direction the subject travels. For horizontal layers, 0° is +X, 90° is +Y; wakes extend in the opposite direction. |
| Wake length | Maximum added trailing length, in percent of the original model height. Rhythm, region, hold and seeded variation reduce this maximum. |
| Leading form | Share of each piece's leading extent kept unstretched. Increase to preserve more facial or anatomical detail. |
| Tail taper | Narrows depth and thickness toward the rear. Limited to 95%, keeping tips finite. |
| Sideways curl | Bends the wake sideways within each layer's plane. Large values can bring bars into contact. |
| Irregularity / Seed | Reproducible per-piece length variation; 0% gives uniform lengths before rhythm and regional weighting. |
| Layers / Layer gap | More layers make finer lines; gap compresses entire slabs, preserving all source intervals. |
| Bars / Bar gap | More parallel bars per slab. Bar gap narrows each deformed bar about its rest centre. |
| Hold base | Prevents sweep below the selected stack percentage, with a smooth 10% transition. Layers remain sliced and gapped. |
| Region | Whole model, feathered height band or sphere (per slab); or Painted area with Sliced layers / Connected surface. |
| Amount | Blends sweep, taper and gaps. Exactly zero returns the untouched input mesh. |

Lengths use source height, not the expanded output's bounds. Frame controls follow Atlas's shared Up, Front
and Extra turn settings. Geometry is returned to original source units and placement, with frame metadata.
Layer tilt inclines the stack normal toward its first in-plane axis. Heading is measured in that tilted plane.

## Method and limits

The source is normalized to height 2 in its canonical frame. Parallel plane cuts use double-precision native
Manifold bisection, preserving closed boundaries and section holes. Splinters makes a second family of cuts in
each layer. Disconnected components are handled independently, so a leg's front does not depend on another leg.

Each piece is refined with shared-edge midpoint triangulation until longitudinal edges span no more than
1 / Wake resolution of its travel extent. Vertices at the front remain fixed. Let x point toward travel,
t be normalized distance into the unprotected trailing part and q = smoothstep(t). The transformation is:

    x' = x − length q
    y' = centre_y + (y − centre_y)(1 − taper q) + curl q²
    z' = centre_z + (z − centre_z)(1 − taper q)

x' is strictly increasing in x, and transverse scales stay positive. The continuous piece map is invertible.
Refinement approximates its curved surface with triangles; this is not a general triangle intersection solver.
Layer thickness is then compressed about its stack centre. With positive layer gaps, different layer intervals
remain separate. A seeded independent stream for each layer/bar/component fixes per-piece length variation.
Changing topology or layer/bar counts changes piece identity.

The engine requires closed, consistently oriented, positive-volume input and checks every resulting shell for
closure, winding, finite coordinates and positive volume. It does not silently repair a scan. Simplify or repair
the source first if cuts encounter invalid geometry. Maximum faces is a hard total budget; a second guard limits
Splinters to 5,000 layer/bar cells. Cancellation is checked during cuts, refinement and every piece.

The original whole/band/sphere modes produce separate sculptural shells, without a boolean union, supports or structural certification. Curl can
make bars within a layer overlap; zero gaps can leave touching surfaces. Treat the output as drawing geometry
or prepare supports/fusion separately for fabrication. No physics, optical flow or motion capture is implied.

Preview and Run use the same source, cuts and refinement, including in Draft mode. Only the displayed mesh
may be reduced to Atlas's standard viewer proxy. This avoids a lightweight preview that promises a different
final silhouette. Large source meshes and many bars can take longer; reduce resolution or layers if needed.

## Drawing recipes

- **Wind-carved bronze:** broad tapered fins; Sketch Hatching or Plotter Vision features exposes the silhouette.
- **Glass runner:** dense bars with little taper; use an oblique view so depth separates the strokes.
- **Velocity fins:** bold repeated pulses, suited to sparse contours.
- **Spiral afterimage:** inspect from above and obliquely to read the changing direction.
- **Held portrait:** head and upper torso stretch while lower layers retain their source form.

Start on A3 with a 0.3 mm pen. Fine horizontal contours can run parallel to layers and hide their top surfaces;
Sketch Hatching is often clearer. Expanded wakes affect automatic framing, so lower reach or aim manually when
the original subject becomes too small. The bundled motion_contours and motion_sketch workflows expose the
motion controls and use the same engine as the standalone tool.

```powershell
& '.\atlas-toolkit.exe' run mesh-motion model.stl --preset 'Glass runner' --out-dir output
& '.\atlas-toolkit.exe' run mesh-motion model.stl --gesture vortex --turn 240 --reach 70
& '.\atlas-toolkit.exe' describe mesh-motion
```
