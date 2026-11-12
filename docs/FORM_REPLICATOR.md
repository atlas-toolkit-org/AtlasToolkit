# Form Replicator

Form Replicator takes one mesh and produces a new mesh for Atlas's existing drawing tools. Its spec lives in
`tools/form_replicator/__init__.py`, its placement and union functions in `form_replicator.py`, and its adapter in
`engine.py`. The registry discovers it without framework or UI changes. The same schema supplies the inspector,
CLI, presets, saved settings and workflow step; the tool version participates in workflow cache keys.

## Layouts and line-art ideas

| Layout | Structure | Useful source and drawing approach |
|---|---|---|
| Cross · 2D | Four chains from a centre | Heads or hands; front elevation shows the whole cross |
| Cross · 3D | Six chains along three perpendicular axes | Busts or blocks; an oblique view reveals the depth |
| Star · 2D | 3–32 evenly spaced rays | Leaves, heads or a stretched solid; taper makes lighter tips |
| Star · 3D | Rays to tetrahedral, octahedral, cubic, icosahedral or dodecahedral vertices | A small source stretched into crystalline limbs |
| Clock / ring | Full ring or open arc; full rings have no duplicate endpoint | Twelve heads turned like numerals, or upright figures in a procession |
| Rosette | Equal arc-length positions on a lobed polar curve | Tangential leaves or busts form a scalloped garland |
| Sea urchin | Golden-angle points on a sphere or cap | Base-anchored elongated copies make a spiny silhouette |
| Sunflower spiral | Equal-area golden-angle disk | Upright heads or seeds; smaller outer copies create a density gradient |
| Helix | A constant-pitch spatial spiral | A rising procession; progressive roll changes the profile |
| Toroidal wreath | Rows around a torus's major and minor circles | Normal-facing copies make an ornamented ring with an open centre |
| Trefoil knot | Equal arc-length positions on a closed trefoil | Tangent copies trace an over-and-under loop; smooth frames distribute residual roll |
| Möbius wreath | Rows across a band with one half-twist | Tangential figures or flattened copies expose the twist in an oblique view |

The source controls how much neighbouring copies overlap. A thin leaf and a wide bust at the same settings will
produce different connections. These are arrangements of the source geometry, rather than predefined solids.

## Frame and placement

1. Set **Source model → Up / Front / Extra turn** using Atlas's usual model frame. Up is canonical +Z; the front
   faces −Y. A model lying on its side can be corrected here.
2. Set **Layout plane**: XZ is a front-facing composition, XY is horizontal, YZ is side-facing. For spatial layouts
   this also chooses the axis perpendicular to the plane. **Phase** rotates the composition around that axis.
3. **Orientation** chooses the direction each copy follows. Outward follows an arm, a ring's radius or a sphere's
   radius; on a torus it points out of the tube. Along curve follows the tangent. Surface normal follows the
   spherical, toroidal or Möbius skin. Inward reverses Outward. Keep upright preserves the canonical source pose.
4. **Aim** aligns either Model up (+Z) or Model front (−Y) to that direction. On a planar curve the remaining axis
   faces the plane's normal. **Roll** turns around the selected source axis; **Progressive roll** accumulates down
   an arm or along a sequence. The optional central copy of a ray layout stays upright before roll/variation.
5. **Place by** chooses the source box centre or bottom centre as its anchor. Base anchors are useful for outward
   spines. **Stretch XYZ** acts in the upright source frame before aiming.

All lengths use percentages of the original upright source height, before Copy size and Stretch. Arm centres sit
at Inner reach + (index + ½) × Arm step. Thus an Inner reach of zero does not put every arm's first copy at the
same point. The optional central copy bridges the middle. Source units and the source frame are restored on export.

**End size** varies linearly along each arm or sequence, inside to outside for the sunflower and pole to pole for
the urchin. Clock/rosette/knot/wreath sequences close spatially; a nonzero taper or progressive roll can intentionally
make their first/last copies different. Return End size to 100 and Progressive roll to 0 for an unbroken repeating seam.

## Solids, detail and safe output

Touching and overlapping copies are exact manifold3d boolean unions. There is no concatenated/interpenetrating
output mode. Copies separated by space stay as separate closed components; the report counts them. Exact union
does not add connector geometry or smooth away source features. A Source detail of 0 preserves the source's face
detail, subject to the working budget. The default 12,000-face simplification makes dense arrays practical.

An open source cannot participate in a solid union. **Close open source** reuses Atlas's existing signed-distance
repair, reports when it is used, and may soften fine detail. Turn it off to require an exact source solid. If a
simplification pinches thin features, the adapter increases its detail target until a closed solid survives, or
uses the unmodified solid, within the working face budget. The report states when more detail was needed.

Preview uses at most Preview source detail and approximately 200,000 source faces across the composition (with
a minimum of 64 per copy), but keeps every placement. It never saves output. Run uses the chosen Source detail.
At most 2,048 copies are proposed; the working face limit checks the source × copies before replication and each
union batch's result. Reduce count/rows or source detail when the limit is exceeded. Native unions run in Atlas's
cancellable worker process, with checkpoints between small batches.

STL, OBJ and PLY carry `atlas-toolkit:form-replicator 1.0`. Output is written beside the destination into a scratch
folder, checked for cancellation and atomically published through Atlas's shared Publisher. A failed or cancelled
export preserves an existing output. The source cannot be used as the destination, including through a hard link.
Pinched contacts and slivers that collapse at mesh export precision are detected before publication. **Contact
weld** retries an invalid union with copies enlarged by 0.1% around their anchors; the report states when this was
needed. This small overlap resolves edge/point contacts without remeshing their detail. Set it to 0 to require
exact planned sizes. If the result still cannot be exported as a closed solid, the error suggests adjusting the
step, radius or copy size, or reducing source detail; an invalid mesh is never published.

## Workflows and drawing

Add Form Replicator from the Mesh model-prep group. Connect a mesh input or any mesh-producing step to it, then
connect its output to Plotter Vision, Contour Lines or Sketch Hatching. Add Overlap Cleaner and Save for an SVG.
The workflow inspector exposes the same settings; previews use draft geometry; normal runs cache the signed mesh
and invalidate it when input contents, settings or tool version change. No side input is required.

Start with a front orthographic camera for XZ layouts. Reduce paper line density as copy counts rise: repeated
silhouettes, a few strong creases and visible gaps usually read more clearly than every triangle edge. Use an
oblique camera for the spatial cross, urchin, helix and wreaths to reveal their depth. These camera and pen choices
remain in the drawing tool, where Atlas already provides Aim, hidden-line removal and paper controls.
