# Pipe Fill 0.1.0

Replace a closed 3D model with a sculpture made of tubes throughout its interior.
The model defines the envelope but is omitted from the output. Gaps are intentional;
the tool fills the volume with paths, not with a continuous solid.

## Start

Choose **Pipe Fill** under Mesh, add a model, select **Plotter organic** and Preview.
Choose **Architectural pipes** for the second reference's straight-run pipe maze.
Preview computes the same paths and geometry as Run. Large results use a display
proxy; that proxy does not replace the exported geometry.

Inputs: STL, OBJ, PLY, GLB/GLTF and OFF through Atlas's shared loader. Export signed
`<name>_pipes.stl`, with OBJ and PLY alternatives. Source coordinates and units are
preserved. The original file is never replaced. No Blender, downloads or new
runtime setup are required.

## Controls

| Control | Effect |
|---|---|
| Mode | Organic curves relax with smooth variation and local repulsion. Orthogonal pipes retain axis-aligned runs and circular elbows. |
| Paths | Independent strands have ends. Closed loops have no ends; every accepted path is a genuine cycle. |
| Spacing | Packing-site separation in % of model height. Smaller values increase density and retain more thin features. |
| Run length | Target edges per strand, or maximum size of merged loops. Longer paths weave through more of the volume. |
| Straight runs | Prefer continuing before turning. Low values suit organic tangles; high values suit architectural runs. |
| Elbow radius | Bend radius as % of spacing in orthogonal mode. Tight bends need thinner pipes. |
| Flow | Organic smoothing and seeded displacement. Clearance checks can limit movement locally. |
| Packing rotation | Rotate the packing axes within the model, independently of model orientation. |
| Outer diameter | Nominal diameter in % of model height. It must fit within spacing and the enclosing volume. |
| Thickness variation | Smooth seeded variation along a strand, up to the selected ± percentage. |
| Section | Solid tubes or hollow pipes. |
| Wall thickness | Hollow material thickness as % of the local outer radius. Open ends have annular rims and open bores. |
| Minimum gap | Surface-to-surface separation between non-neighboring pipe segments. |
| Boundary clearance | Minimum distance from the pipe exterior to the enclosing surface and cavity walls. |
| Tube sides / Curve samples | Circular and longitudinal mesh resolution. Higher values increase output size. |
| Seed | Repeatable packing, routing and thickness variation. |

Lengths use the model's oriented height, before Packing rotation. Model Up/Front
and Extra turn follow the other Atlas mesh tools. Presets preserve those settings.

## Presets for drawing

| Preset | Visual intent |
|---|---|
| Plotter organic | Curving, interwoven strands with readable tube silhouettes. |
| Plotter airy | Thinner and longer strands; more open space between layers. |
| Plotter dense | Finer packing with more silhouette detail and denser linework. |
| Organic loops | Closed interwoven cords without end caps. |
| Architectural pipes | Long straight runs with rounded elbows. |
| Orthogonal loops | Closed pipe circuits within the model's envelope. |
| Open bores | Hollow pipes with readable annular mouths in oblique views. |
| Swelling cords | Smooth thickness changes for a softer sculptural rhythm. |

Send the output to **Plotter Vision → Features**, turn **Decimate off**, and start
with **Shortest stroke** around 0.1–0.3 mm. An angle around 35° draws silhouettes
and rims without drawing tube tessellation. Turn **Where parts cross** off:
paths are deliberately separate. An oblique view reveals depth. **Hatch Maker**
adds form-following tone; use its shared camera and pen settings. Contour Lines
can produce a different stacked cross-section drawing.

The two example workflows combine Pipe Fill and Plotter Vision with suitable
settings. Open the `.atlasflow`, add a source mesh, aim the camera, and Run.
Only Save publishes the final SVG; the mesh remains a cached intermediate.
Add a second Save branch after Pipe Fill to publish that mesh as well.

## Volume and geometry checks

Input must be watertight with consistent winding. Enable **Repair open mesh** to
explicitly infer a volume by signed-distance resampling. Repair is approximate:
openings can close and fine details can soften. It is not enabled by any preset.
Oriented cavities and disconnected solids are respected during packing.

Boundary queries certify the full outer-radius segment capsule by recursive
distance bounds, rather than checking centerline points alone. Path candidates
are backtracked or rejected if they violate containment, segment separation or
local bend limits. Closed-loop frames compensate for accumulated twist.
Hollow loop material has separate outer and inner surfaces; open hollow pipes
join those surfaces at annular rims. The material boundary is watertight even
when the bore is open. Pipes remain separate shells, not a fused printable network.

The report gives **routing coverage** (used / eligible interior packing sites)
and **material fill** (tube material / envelope volume). Neither means every
interior location contains tube material. Closed loops leave more unrouted
boundary sites than strands. Regions narrower than the selected diameter, and
some regions narrower than the packing spacing, may be absent. Reduce both
spacing and diameter for ears, fingers or other thin details.

Grid, site, sample and face budgets prevent oversized allocations. If a budget
is exceeded, the tool stops with setting guidance instead of dropping the rest
of the model. Saved meshes are staged and reloaded for closed-shell validation
before publication; failed export preserves any existing output.

## Method and development

See [research and implementation decisions](PIPE_FILL_RESEARCH.md). This is an
independent finite-radius packing method inspired by the shape-filling objective
of Yu, Schumacher and Crane's *Repulsive Curves* (2021). It uses seeded volume
routing, local repulsion and containment checks; it does not reproduce their
tangent-point energy or fractional Sobolev optimizer. `fogleman/ln` informs the
line-art reference; Atlas's own drawing engines provide hidden-line rendering.

```powershell
.\atlas-toolkit.exe run pipe-fill head.stl --preset "Plotter organic"
.\atlas-toolkit.exe run pipe-fill head.stl --preset "Open bores" --out-dir out
.\atlas-toolkit.exe run plotter-vision head_pipes.stl --preset Features --min-stroke 0.15 --no-decimate
```

Development validation:

```powershell
.venv\Scripts\python -m unittest discover -s tests -p test_pipe_fill.py
.venv\Scripts\python scripts\dev\validate_pipe_fill.py --out <validation-folder> --head <closed-sculpture.stl>
.venv\Scripts\python scripts\build.py
```
