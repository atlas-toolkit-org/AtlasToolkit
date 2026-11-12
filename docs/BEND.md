# Bend 0.1.0

Bend turns a model into a circular arc, a soft sweeping arc, an opposed S or a spatial wave.
It carries each cross-section along a spine and rotates it with the curve. The neutral spine
keeps its length; the inner side compresses and the outer side stretches. Fine surface detail
travels with the model. All mesh processing runs in Atlas's worker process, without Blender.

## Shaping the gesture

Start with **Sculptural arc**, then change **Angle** and **Direction**. For the default Up axis,
0° bends right (+X), 90° bends back (+Y), and −90° bends toward the face. Negative Angle reverses
the gesture. **Amount** scales Angle and Section roll together; zero preserves the input exactly.

**Circular arc** has constant curvature and Angle is the total turn at the end. **Soft arc**
eases curvature to zero at both ends, with stronger curvature in the middle. **S-bend** turns
one way and then the other; its ends remain parallel. **Wave** feathers an adjustable number
of oscillations into the range. For S-bend and Wave, Angle is the largest tangent deflection,
not the final turn. The S can become quite subtle on a short, thick model because of protection.

**Plane sweep** turns the bend direction along the spine: a large sweep makes a spatial curl.
**Section roll** independently rotates cross-sections around that curved spine. Zero roll uses
rotation-minimising transport; it does not add the artificial twist a Frenet frame can develop
at inflections. The model never acquires a seam at an S-bend's inflection.

## Range, anchors and model axes

**From / To** locate the active range as percentages along the chosen **Bend along** axis.
They must be ordered. Parts below From and above To follow as rigid ends. There is no positional
blend between an intact mesh and a bent mesh, which would flatten sections and create pinches.

**Hold section** preserves both the position and orientation of the range start, middle or end.
With Range start, everything below From stays fixed; Range end holds everything above To.
Middle balances the motion around a stable cross-section. This anchors a section, not a point
on the outside skin. **Spine offset U / V** move the neutral axis in units of the original
model's height; moving the spine changes how much each side stretches.

Up, Front and Extra turn use Atlas's shared model frame. The chosen bend axis lives in that
frame. Up/Z uses transverse U=X, V=Y; Across/X uses U=Y, V=Z; Depth/Y uses U=Z, V=X. All lengths
and saved positions return to the source units. An anchor does not move the model to the origin.
STL/OBJ/PLY outputs carry the Atlas signature and source frame axes. Stage sets carry their
complete common frame in `stages.json`.

## Spine editor and preview

The **Spine** tab shows U/axis and V/axis projections of the requested neutral spine. The dashed
line is the straight reference; section ticks show transported orientation and the large open
circle marks the anchor. Drag horizontally to change Angle; Shift-drag changes Direction.
Drag the two range handles at the right to move From and To. These edits write the same schema
parameters as the inspector, CLI and Workflow form. The guide uses the display copy's bounds;
for a strongly rotated dense scan, those bounds can differ slightly from the full input.

**Preview bend** computes the refined final geometry. It writes no output, uses the same
resolution as Run, and reports any reduction in angle. The requested spine remains a guide;
the mesh preview is the achieved result. Large meshes use a display proxy, labelled accordingly;
the full geometry is retained for export. Before and every stage can be inspected separately.

## Quality and limits

**Refine coarse faces** splits shared edges before bending, without changing the rest surface.
It preserves open boundaries and closed connectivity. **Bend resolution** sets the maximum
longitudinal edge span inside the range: range length divided by resolution. It is not a global
remesh. Try 48 for an arc and 64–96 for strong roll or waves. Turn refinement off for a deliberate
faceted result. Native polygon input is triangulated; source face loops are not preserved.

**Protect inner curve** reduces Angle if the conservative local Jacobian bound would fall below
0.15. It uses the whole transverse bounding box, so it may be cautious on very irregular forms
and disconnected parts. The report and Achieved angle tile expose the result. To retain a
stronger gesture, extend the range, reduce Plane sweep, move the neutral spine, or disable the
guard intentionally. Section roll is retained; it does not change the local determinant.

This is a geometric deformation, not an elastic or collision simulation. The guard prevents
local curvature collapse within the sampled bound; it does not detect distant self-contact,
solve intersecting limbs, repair existing intersections or guarantee a printable solid.
Very large angles and rolls can still make separated regions intersect. Closed input stays
topologically closed, and open input stays open; no cap or repair is introduced.

Maximum faces is a hard per-stage limit, also applied to input. There is a 6-million-face stage
set limit and conservative refinement memory accounting. Budget failures and cancellation
publish nothing. Saved copies are reloaded and checked before staged publication. Existing
stage folders are never replaced. All randomness is absent: identical settings are deterministic.

## Drawing recipes

- **Sculptural arc / Reclining marble:** Contour Lines, A3 portrait, 0.3 mm pen, 1.2–1.6 mm
  contour spacing. Sideways bends read clearly through a front three-quarter view.
- **Deep bow / Crown hook:** use a profile or oblique camera; a straight front view can hide
  a depth bend. Lower From if a thick crown makes protection restrict the hook too much.
- **Serpentine / Flowing wave:** long statues, thin reliefs and narrow stems give the widest
  range before protection limits the angle. Use 1.2 mm contours to reveal the changing rhythm.
- **Spatial arabesque / Rolled arc:** Sketch Hatching makes the changing section orientation
  legible in light and shadow. Try Plotter Vision on a deliberately coarse source for graphic facets.
- **Bending sequence:** four in-place stages plus a final model. Draw the stage files with
  Contour Lines' shared stage frame so scale and slice heights remain comparable. All stages
  in the primary output makes a side-by-side composition; separate stage files stay in place.

The bundled [Bend Contours](workflows/bend_contours.atlasflow) and
[Bend Sketch](workflows/bend_sketch.atlasflow) workflows connect mesh input → Bend → drawing → Save.

```bat
atlas-toolkit describe mesh-bend
atlas-toolkit run mesh-bend model.stl --preset "Sculptural arc" --out-dir output
atlas-toolkit run mesh-bend model.stl --profile s --angle 30 --direction 25 --segments 48 --format .obj
atlas-toolkit run mesh-bend model.stl --preset "Bending sequence" --format .ply
```

## Method and verification

For range coordinate t in [0,1], the tangent angle is Angle times t (arc),
`t − sin(2πt)/(2π)` (soft arc), `sin(2πt)` (S-bend), or a peak-normalised
`sin(2π Waves t) sin²(πt)` (wave). The bend azimuth is Direction + Sweep × t.
`T = (sin θ cos φ, sin θ sin φ, cos θ)` has unit length. Trapezoidal integration
builds the arc-length spine C. Minimal rotations between 2049 tangent samples transport
the section basis. Section roll adds an eased axial rotation. Interpolated bases are
re-orthonormalised. Endpoint tangents extend the curve outside the active range.

The mapping is `F(u,v,s) = C(s) + u E0(s) + v E1(s)`. An inverse rigid transform
fixes the requested anchor section. Its local Jacobian determinant is
`1 − u dot(T',E0) − v dot(T',E1)`. Maximising compression over all corners of the
transverse bounds gives a conservative spatial bound sampled along the spine. The guard
reduces bend amplitude deterministically until this bound is at least 0.15; each sequence
stage is checked independently too. This is an independent implementation derived from
these equations using existing NumPy/SciPy dependencies.

`tests/test_mesh_bend.py` checks the analytic circular solution (including rigid ends), exact
zero, tiny-angle continuity, anchor sections, transported orthonormality, arc length, axis,
direction and unit covariance, fold limiting against numerical mapping Jacobians, open/closed refinement topology, signed formats,
frame metadata, preview agreement, stage manifests, dry runs, budget rollback, cancellation,
actual contour SVG generation, workflow caching and the interactive editor in both themes.
`scripts/dev/validate_bend.py` produces repeatable preset/model runs and real Atlas drawing sheets.
