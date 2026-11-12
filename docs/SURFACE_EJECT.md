# Surface Eject 0.3.1

Surface Eject lifts actual pieces of a model out of matching cavities. It keeps a recognizable body between
the cuts and preserves the orientation and sculpted skin of every fragment. Mesh Shatter partitions the
whole skin into adjacent shards; Disintegrate subdivides it into loose triangles; Holes discards the cut material.
Surface Eject retains that material and positions it outside the body.

## Controls and method

1. Choose points uniformly by original surface area, using the saved Seed. Each candidate draws independent
   uniform Size, Depth and Gap values from their min–max intervals. Equal endpoints give fixed values.
2. Align a sampler to the source face normal and place its deepest point at Depth below the surface anchor.
3. Split the **remaining** model with the sampler. Manifold produces the intersection and difference together,
   so removed material is neither copied from an earlier sample nor lost. A fragment must contain original skin
   and be connected. Samples reaching opposite-facing skin are rejected to retain a surface-extraction effect.
4. Sum the oriented area vectors of the original skin triangles in the intersection. Their normalized sum is the
   actual sampled area's outward normal. Translate the piece strictly along that normal; never rotate it.
5. Retain the matching difference as the cavity body. The body plus all restored fragments conserves volume.

The implementation uses Atlas's bundled manifold3d in double precision. See the primary
[Manifold Split documentation](https://manifoldcad.org/docs/html/classmanifold_1_1_manifold.html).
There is no Blender requirement or additional dependency.

Sampling lengths, gaps and connector thickness are **percent of original model height** in the chosen Up/Front frame.
Organic spread and extent use the individual sampling width; tip thickness is a ratio. Export restores source
units, placement and frame metadata. The expanded output bounds never redefine the meaning of a parameter.

| Sampler | Size | Depth |
|---|---|---|
| Cube (default) | True cube side length | Shift cube along its normal; deepest point is Depth below the anchor |
| Box | Square footprint side | Independent inward depth; outer reach is half Size |
| Sphere | Diameter | Shift sphere along its normal; deepest point is Depth below the anchor |
| Ellipsoid | Tangential diameter | Inward/outward normal radius; centered on the anchor |
| Cylinder | Diameter | Independent inward depth; outer reach is half Size |

Cube and Sphere require **Depth maximum < Size minimum**, ensuring every independent draw still intersects
the anchor surface. Box, Ellipsoid and Cylinder allow deeper samples without that restriction. Curved shape
segments controls curved samplers and connector cross-sections (connectors use at most 48); source detail is never simplified.

**Footprint spacing** uses conservative footprint circumradii to space sites; deep cuts can still meet inside
the body. Zero allows overlapping cutters;
each later sample still contains only material remaining in the body. **Pieces** is a requested count,
with at most 35 attempts per requested piece (at least 200). Small painted areas, narrow bands, deep samples,
thin walls or large spacing can leave fewer pieces. The report gives achieved count and rejection reasons.

## Full detachment and drawing visibility

**Fully detach** is on by default. The tool first lifts a fragment out of its original surface, then checks
the requested clearance against the entire original model and all previously ejected pieces. If a concavity
or earlier fragment blocks this local lift, it moves farther along the same normal to clear a conservative
support plane. The randomly chosen Gap is a **minimum 3D clearance**, not a maximum total travel distance.
Numerical tolerance adds a tiny positive margin, including for zero gap. The report lists actual travel.

With Fully detach off, Gap values are literal translations from the source position; partial embedding and
overlapping fragments are possible. This option can be useful for restrained shallow relief studies.
Connection percentages above zero require Fully detach: the tool first places fragments with clearance,
then bridges the gap for the selected subset.

A fragment displaced toward the camera can still hide its own cavity in a 2D projection. Use an oblique view,
inspect **Cavity body** and **Ejected parts** separately, and adjust the drawing camera before plotting.
No camera-independent geometric translation can guarantee cavity visibility from every direction.

## Connect fragments to their cavities

**Connect fragments to cavities** is optional and off by default. Existing floating-fragment presets and
extraction geometry remain unchanged. **Connected fragments** chooses what percentage receive a real solid
connection to their own cavities; the others remain floating. Choose straight **Cylinder**, geometric **Square bar** (a square-section parallelepiped),
or **Organic** stems and branches. The connectors add material after the source-volume partition is checked.

| Control | Effect |
|---|---|
| Connected fragments | 0–100% of successfully extracted pieces, default 100%. The count is rounded to nearest integer, with half rounded up; 25% of 10 pieces connects 3. Small positive percentages can round to zero. |
| Choose fragments | Random (default), Largest first, or Smallest first. The size options use actual extracted volume, with stable tie ordering. |
| Connection seed | Rerolls only the random connected subset; the existing Seed still controls extraction and stem shape randomness. Visible for partial random selection. |
| Thickness minimum / maximum | Independently seeded round diameter or square side, in % original height. Equal values give fixed thickness. |
| Tip thickness | Organic tip diameter relative to its base; smaller ratios taper the stem. Forks are finer than the trunk. |
| Curvature | Gentle bow within the extent envelope; zero makes straight paths. |
| Irregularity | Smooth seeded directional and thickness variation, with fixed end positions. |
| Branch count | Fixed or Random range. Random draws one inclusive integer count independently for each connected fragment. |
| Extra branches | Fixed requested count, 0–100, excluding the main stem and including all generations. Zero gives one stalk. |
| Branches minimum / maximum | Inclusive random endpoints, each 0–100. Equal endpoints give a fixed count. A range starting at zero mixes bare stems and trees. |
| Branch origins | At split position (previous behavior), Beginning, Middle, End, Along length (random), or Mixed zones. New modes measure roots along the visible parent span outside the body and fragment. |
| Branch split | Previous 20–80% split-position control, available in At split position mode. Existing presets keep their original roots. |
| Branch levels | Maximum generation, 1–4. One grows every fork from the main stem; higher settings allow smaller twigs to grow from earlier branches. All extra generations share the same count limit. |
| Branch seed | Rerolls random counts, distributed origins and parent choices. Does not move fragments, change connection share, or reshape the main stem. |
| Branch thickness | Root radius relative to its parent at the junction; dense sets and deeper generations become finer to fit. Tip thickness still controls taper. |
| Branch spread | Desired tip spread across the same fragment, in % sampling width; limited by available interior room and Maximum extent. |
| Maximum extent | Hard limit on **every centreline's lateral offset** from the direct cavity-to-fragment line, in % sampling width. Tube radius is additional. Zero gives a straight, unforked stem. |

Random selection uses a seeded ranking and an exact rounded count, rather than independent per-piece
probability trials. This makes the density predictable, especially for small piece counts. Increasing the
percentage with the same connection seed retains the previously chosen subset and adds more pieces.
Changing percentage, selection mode or Connection seed never moves or resamples fragments. The report and
statistics show the actual connected count and percentage, which can differ slightly from the requested
percentage after rounding. It uses achieved extraction count, even when the requested Pieces cannot all fit.

Attachments are established after **all** extraction cuts finish. The tool retains each cut's new interface,
finds a point on it that still belongs to the final cavity body, and embeds the root inside that body. It finds
a roomy interior point in the fragment using containment and surface-distance checks. A mass centroid is only
a candidate: it is never trusted if it lies outside a concave piece. The rod reaches a safe interior point,
so its visible end meets the fragment surface while the embedded overlap provides a robust boolean joint.

The new origin zones are **Beginning 5–30%**, **Middle 35–65%**, **End 70–95%** and **Random 5–95%** of the
visible parent span. Mixed zones balances beginning, middle and end, with seeded random locations inside
each zone. With more than one generation, the same rule applies along each selected parent branch.
Origins use arc-length interpolation; the older split mode retains its original sampled split point.

Organic branches share a rooted tree and all terminate inside **that same fragment**. They do not form a
network between unrelated fragments. All generations' paths and endpoints obey the same Maximum extent.
Thin pieces can limit thickness or accommodate fewer forks than requested. The report lists fitted thickness,
achieved fork counts and measured lateral extent. Curvature is reduced if necessary to avoid obstructions.
Dense counts use more interior attachment samples and finer limbs. If a new optional leaf prevents reliable
closed export at an intersection, it is removed with any newer leaves until the tree passes; parent links
and the main cavity-to-fragment connection remain intact. Reports give each fragment's requested and achieved
counts, origin zone/fraction, parent index and achieved level. These are fitted counts, rather than a promise
that every small fragment can accommodate 100 distinct tips. Face-budget failures publish nothing.

For an airy tree, start with Random range 4–12, Mixed zones, 2–3 levels, 25–40% extent and moderate taper.
Beginning creates long root fans; End creates short crowns; Middle or Random adds more distributed branching.
The fixed 100 setting is supported on suitably roomy fragments; use fewer Pieces or lower Curved shape
segments for dense work. Cancellation is checked during branch generation, not only between fragments.

Each selected connector must overlap its own fragment and the final cavity body by positive volume, remain one closed
solid, and avoid other fragments. The selected assembly is checked for valid fused joints independently from
the deliberately floating unselected pieces. If a selected joint cannot be established, the run stops before publishing rather
than exporting an unattached fragment. Increasing Footprint spacing, reducing ejection distance or changing
Seed can resolve an obstructed connector. Existing disconnected components of the input body are retained.
Boolean junctions are checked through cleaned double-precision and float32 copies. If tiny joint slivers
would open during reload, bounded native simplification and validated closed precision copies stabilize them
without accepting holes or resampling:
at most 0.00002% of original height, with component count retained and volume change below one part per million.
The report records any stabilization tolerance. Separate cavity-body and fragment geometry stays unchanged.

## Select where extraction happens

- **Whole surface** samples the entire source. It means all areas are eligible, rather than partitioning away
  the entire body.
- **Height band** clips samplers at Band from/to, measured from the original lowest point. Entire extracted
  volumes and cavities stay inside this band.
- **Painted area** opens the Selection tab's modal editor. Paint with left-drag, Shift to erase, Alt+left-drag
  or Orbit mode to rotate, and wheel to zoom. Exact nearest-hit ray tests protect hidden faces. Undo restores
  up to 20 strokes; Clear and Select all are available. Painting follows source triangles; refine a coarse
  mesh first when a finer selection boundary is needed. A sample touching any unselected original surface
  face is rejected, including along coplanar painted/unpainted boundaries.

Use **Use selected area** to commit or Cancel to discard edits. Choosing Painted area opens the modal when
a model is selected. The saved mask contains compressed face ranges and a mesh fingerprint. It is tied to
the exact input geometry; using a different or edited mesh fails explicitly. Reopen the editor to repaint.
Selection JSON can be loaded/exported in the modal; `--selection-file` supplies it to CLI or a Workflow side input.
The embedded selection travels in settings/history/Workflow files without a sidecar; an explicit file overrides it.

## Input and output

Require a closed solid is the default. Closed inward-wound inputs are oriented outward. **Repair by resampling**
is explicit and may soften detail; it then extracts from the repaired surface. If repair is needed with a painted
mask, repair/export the input first and paint that closed model, since repair changes its faces.

**Body + ejected parts** writes one combined signed mesh. **Separate body and parts** writes the cavity body to
`name_surface_eject.stl` and fragments to `name_surface_eject_parts.stl`. When connectors are enabled, it adds
`name_surface_eject_connectors.stl` when at least one piece is connected; OBJ and PLY are also supported.
At 0% (or a positive percentage rounding to zero), it writes only body and fragment files, with no empty
connector file. Combined output fuses selected pieces to the body and retains all other pieces as separate solids.
All separate files keep the same coordinates, so load them together without re-centering. The three separate
meshes intentionally overlap at embedded joints; boolean-union them to reconstruct the fused assembly.
Existing parts and connector siblings are
preserved by numbered names. All files are staged and reloaded for validation before the primary is replaced.
For separate SVGs drawn with different pens, use the same drawing camera and set **Frame from → Another model**
to the original input in every drawing job; this keeps their scale and registration aligned.
Preview computes the same final geometry as Run. Dense combined previews simplify only the display body,
retaining complete fragments and connectors; the Connectors tab retains its full geometry. Saved geometry
always uses the validated boolean assembly.

Maximum faces applies to the input and combined output. Constraint failures and cancellation publish nothing.
Excessive extraction that would remove the whole body is rejected. No input artwork is overwritten.

## Pen-plotter recipes

Start with A3, a 0.3 mm pen and **Quiet Excavation**: fewer broad fragments and ample white space. **Floating
Tesserae** gives a balanced square rhythm; **Cubist Halo** distributes more small fragments farther out.
**Round Relics** and **Circular Levitation** emphasize rounded openings. **Shallow Scales** keeps thin patches
near the source; **Architectural Plugs** reveals deeper cavity walls. **Crown Fragments** confines extraction
to an upper band, retaining the lower silhouette.

Six connected presets add new rhythms: **Pinned Relics** uses slender cylinders; **Square Armature** bridges
broad pieces with crisp beams; **Living Stems** carries them on curved tapering stalks. **Rooted Canopy** gives
each larger piece two restrained forks. **Fine Veins** uses finer, shorter forks under shallow oval patches;
**Crown Grafts** confines compact branching stems to an upper band. Sketch Hatching makes thin rods and cavity
walls especially legible; oblique Contour Lines emphasize their sculptural sections. Start with 1–2 forks and
20–40% extent to retain white space and recognizable fragments.
**Half-Rooted** connects an exact random half of the fragments on organic supports. Try 25% for sparse
supports among floating relics, or Largest first to connect the visually heavier volumes while smaller chips hover.
**Root Fans** starts long forks near cavities; **Twig Crowns** varies short, finer crowns per fragment.
**Wandering Branches** distributes roots randomly with up to three generations; **Wild Canopy** combines
beginning, middle and end zones with varied, denser trees. These presets keep the number of fragments modest
to retain readable white space in pen-plotter drawings.

Use Contour Lines around 1.3–1.6 mm spacing for open sculptural sections, or Sketch Hatching / Classic for
readable cavity walls and retained surface relief. Start with an oblique view. Dense small fragments increase
crossings and pen travel; lower Pieces or increase Footprint spacing for small paper. Presets deliberately avoid
rotation, jitter and tearing, keeping the surface-extraction idea legible.

## CLI and Workflows

```powershell
atlas-toolkit run mesh-surface-eject model.stl --preset "Quiet Excavation"
atlas-toolkit run mesh-surface-eject model.stl --shape ellipsoid --depth-min 2 --depth-max 5 --count 20
atlas-toolkit run mesh-surface-eject model.stl --region band --band-from 55 --band-to 90
atlas-toolkit run mesh-surface-eject model.stl --region paint --selection-file selection.json --export-mode separate
atlas-toolkit run mesh-surface-eject model.stl --preset "Rooted Canopy"
atlas-toolkit run mesh-surface-eject model.stl --connectors --connector-style organic --connector-branches 2 --connector-extent 25 --connector-spread 20
atlas-toolkit run mesh-surface-eject model.stl --connectors --connector-style bar --connector-diameter-min 0.8 --connector-diameter-max 0.8 --export-mode separate
atlas-toolkit run mesh-surface-eject model.stl --connectors --connector-percent 50 --connector-seed 7
atlas-toolkit run mesh-surface-eject model.stl --connectors --connector-percent 30 --connector-selection largest
atlas-toolkit run mesh-surface-eject model.stl --connectors --connector-style organic --connector-branches 100 --connector-branch-placement mixed --count 1
atlas-toolkit run mesh-surface-eject model.stl --connectors --connector-style organic --connector-branch-count random --connector-branches-min 4 --connector-branches-max 20 --connector-branch-placement random --connector-branch-levels 3 --connector-branch-seed 7
```

The same schema powers the UI, CLI and Workflows. Add Surface Eject between a mesh input and a drawing tool.
For painting in a Workflow, select the step, click **Aim in tool…**, paint its prepared input, then **Back to
workflow** to transfer settings. Changing upstream geometry invalidates the mask explicitly; repaint it.
In Separate mode, the primary cavity body passes to the next tool; a Save connected directly to this step
retains body, fragments and connectors. Use Combined mode to draw the fused assembly together.

Bundled examples: [Surface Eject Contours](workflows/surface_eject_contours.atlasflow) and
[Surface Eject Sketch](workflows/surface_eject_sketch.atlasflow),
[Connected Contours](workflows/surface_eject_connected_contours.atlasflow) and
[Branching Sketch](workflows/surface_eject_branches_sketch.atlasflow) and
[Mixed Sketch](workflows/surface_eject_mixed_sketch.atlasflow) and
[Tree Sketch](workflows/surface_eject_trees_sketch.atlasflow).

## Verification

`tests/test_mesh_surface_eject.py` checks material conservation, actual source skin, orientation, outward
normals, all sampler families, certified body/fragment gaps, a concave obstruction, bands, coplanar painted
boundaries, mask binding, repeatable seeds, open-input policy, three export formats, safe publication,
cancellation, visible painting, CLI/drawing and Workflow caching. The common tool contract adds discovery,
schema/CLI round trips, icon uniqueness and signed sample output.

`tests/test_surface_eject_connectors.py` checks all three connector styles, real embedded contacts and fused
topology, a concave fragment with its centroid outside, unchanged extraction when enabled, fork/extent bounds,
zero controls, seeded repeatability, safe thickness fitting, cancellation, three-file exports, staged rollback,
existing-sibling protection, controls, connected drawings and Workflow cache invalidation.
`tests/test_surface_eject_connection_share.py` adds exact count/rounding, 0/100% endpoints, independent random
seed, nested subsets, size ordering, mixed solid topology, unchanged connector shapes, all-format exports,
achieved-count behavior, schema/CLI visibility and Workflow cache invalidation for percentage and selection seed.
`tests/test_surface_eject_trees.py` covers inclusive seeded count ranges, every origin zone, secondary/tertiary
parents, unchanged main stems, complete dense previews, cancellation inside generation, face limits,
Workflow keys and a real 100-branch, three-generation assembly reloaded closed from OBJ/PLY/STL.

Run `scripts/dev/validate_surface_eject.py --out <folder>` to render repeatable real-sculpture preset plates,
primitive controls and a painted-area example through Atlas's own line-art tools. Add `--editor` to capture
the selection modal in both themes on a working OpenGL Qt platform.
Use `--connectors-only` for the connector presets and connected primitive/painted examples.

Solid connector primitives and deterministic branch planning are shared with [Strata Sculpt](STRATA_SCULPT.md).
This refactor preserves Surface Eject's existing connector geometry and controls.
