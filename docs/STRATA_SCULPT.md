# Strata Sculpt 1.1

Strata Sculpt cuts a mesh into capped slabs, then thins, separates, turns, waves and dislocates them.
Optional **Connect neighbouring strata** adds real fused supports between facing cut surfaces.
Connectors are off by default: existing settings and presets keep their floating-layer results.

## Supports

**Supports per gap** requests 1–20 supports (default 3) for each facing pair of slab components. Attachments
are sampled by original cut area, with spacing and clearance from cap boundaries and holes. **Connection seed**
rerolls placement and shape independently of the Dislocation seed. Candidates are planned on original
interfaces and transformed with the slabs; a stage depends on its amount, not on how many stages are requested.

- **Cylinder** makes straight round supports.
- **Square bar** makes straight square beams; Thickness is the side length.
- **Organic** makes gently curved, tapering stems with seeded directional and thickness variation.

Thickness minimum/maximum are percentages of the original model height. Equal values request a constant
thickness. Thickness is reduced to fit both slabs and their cap margins. Small caps may accommodate fewer
supports than requested; every eligible separated pair must still receive at least one reliable support.
Already joined slab pairs are fused without adding supports. Separate source objects stay separate, and empty
layer intervals are not bridged. Sliced components within one source are matched by cap overlap, with nearest
partners for otherwise unmatched facing caps.

## Organic branches

Tip thickness controls taper; Curvature and Irregularity shape the stem. Extra branches are requested per
support, excluding the main trunk: fixed 0–100, or an independent inclusive random range. Branch seed changes
counts, distributed origins and parent selection without moving the slabs or changing the main supports.

Branch origins use the visible parent span: Beginning 5–30%, Middle 35–65%, End 70–95%, Along length 5–95%,
or Mixed zones. At split position uses Branch split. Levels 1–4 allow smaller twigs to grow from earlier limbs.
Every branch shares its trunk and ends inside the same receiving slab, near its facing cap.

Branch spread and Maximum extent use **cap width**, defined as the square root of the smaller facing cap's
area. Extent bounds every centreline's lateral offset from the direct line between the embedded endpoints;
rod thickness is additional. Zero extent gives a straight, unforked stem. Zero curvature and irregularity
give straight paths. Dense trees use finer limbs. Curvature may be reduced to avoid obstructions, and optional
leaves may be omitted or removed to keep joints reliably closed. Requested and achieved counts, generations,
thickness fitting and measured extent appear in the report.

Start with **Airy Roots**: three slender supports per gap, gently curved tapered stems and two fine forks each.
**Pinned Strata** and **Square Supports** give simpler structural rhythms. Use Plotter Vision's feature edges
or Sketch Hatching to draw fine supports; widely spaced horizontal contours can miss narrow vertical stems.

## Solids, stages and output

Connectors require outward-oriented closed input and closed capped slabs. Both embedded ends must overlap
their intended slabs by positive volume. Supports must avoid unrelated slabs and earlier trees. Atlas uses
the bundled Manifold library to union supports and slabs, checks required connectivity, and checks cleaned
double/float32 geometry with the same bounded precision policy as Surface Eject. It never repairs by leaving
holes or silently exporting an unattached required joint. Reduce dislocation or thickness, or change Connection
seed, if a required connection cannot be fitted.

Amount zero is the exact original mesh. The original influence and motion controls still apply; supports join
affected layers to their neighbours. Connected Draft and Preview compute the same full geometry as Sculpt;
display copies may still be simplified. Maximum faces limits input, generated geometry and progression output.

Outputs remain `<name>_strata.stl` (also OBJ and PLY), signed `atlas-toolkit:mesh-strata`. Each file contains the
fused sculpture. Stage files, their frame manifest, and side-by-side progression retain the existing structure.
Files are staged and reloaded to check closed topology and component counts before publication; an error or
cancellation leaves existing primary and stage files unchanged. No separate connector mesh is exported.

Result data includes per-support source/layer/component identifiers, embedded endpoints, paths, fitted thickness,
requested/achieved branches and origin/parent metadata; coordinates and lengths are in original model units.
`connector_stages` supplies records and pair counts per amount. Pair layer/component indices are zero-based.

## CLI and Workflows

```powershell
atlas-toolkit run mesh-strata model.stl --preset "Airy Roots"
atlas-toolkit run mesh-strata model.stl --connectors --connector-count 3 --connector-seed 7
atlas-toolkit run mesh-strata model.stl --connectors --connector-style bar --connector-diameter-min 0.5 --connector-diameter-max 0.5
atlas-toolkit run mesh-strata model.stl --connectors --connector-style organic --connector-branches 4 --connector-branch-placement mixed --connector-branch-levels 2
```

The same schema drives the form, CLI, settings, presets and Workflow steps. Feed the fused mesh into Plotter
Vision, Contour Lines or Sketch Hatching; changing connector settings invalidates the Workflow cache.

## Verification

`tests/test_strata_connectors.py` covers embedded fused contacts, spacing, ring/concave caps, separate sources,
split slabs, empty intervals, all slice directions, motion/influence, thin slabs, seed independence, organic
zones/generations and 100-branch requests, zero controls, face limits, cancellation, unchanged floating output,
stage stability, full previews, signed STL/OBJ/PLY reloads, rollback and a cached drawing Workflow.

Run `.venv/Scripts/python scripts/dev/validate_strata_connectors.py` for repeatable primitive and optional
reference-sculpture drawings. It writes only to `build/qa/strata-connectors`; reference models come from
`devpaths` or `--model` and are read only.
