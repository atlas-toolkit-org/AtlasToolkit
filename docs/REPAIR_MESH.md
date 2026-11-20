# Repair Mesh

Atlas Toolkit 2.4.1, Repair Mesh 1.0.0. It makes a damaged model a clean, closed solid that the sculpting tools
accept, keeps the surface where it can, and says what it changed.

## Why models fail

Tools that cut, grow or pack a solid stop when their input is not one: Motion Sculpture, Crumble, Stair Sculpt,
Surface Eject, Pipe Fill, Surface Stripes (and Strata Sculpt's connectors) need a **closed solid**, and
Developable Surfaces needs a **clean surface** (openings allowed). Scans and decimated models often carry one of
these:

| Problem | What it is | Where it comes from |
|---|---|---|
| Back-to-back faces | a triangle and its twin facing the other way: a sheet with no volume | decimation folding a thin spot |
| Loose crumbs | tiny pieces of a few faces, apart from the model | decimation, scan noise |
| Holes | edges with a face on one side only | scans cropped without a cap, gaps in a scan |
| Non-manifold edges | an edge shared by three or more faces | folds, fins, two parts glued along an edge |
| Bow-tie vertices | two parts touching at one point | merged pieces |
| Wrongly wound faces | neighbours facing opposite ways, or a model inside out | exporters, mirrored parts |
| Degenerate faces | triangles with no area | exporters, welding |

## Two layers of repair

**In every tool that needs a solid, a safe tidy (automatic).** When the input fails the tool's check, the tool first
removes back-to-back sheets, duplicates, degenerate faces and crumbs. It then separates non-manifold edges and bow-tie
vertices, winds the faces outward and closes pin-holes of a few edges. **None of this moves the surface.** The Log and
the top of the Report say what was tidied. Most of the catalogue's light models only needed this.

**Repair Mesh, for everything else.** Real holes, a non-orientable surface or a model that needs rebuilding stop the
tool with an error that says what is wrong and what to do. The input gets *Needs repair* in the queue as soon as it
is added, before anything runs, and the report shows **Open in Repair Mesh**, which opens this tool on the same file.
The tools that already had one keep their own resampling toggle (*Open input › Repair by resampling* or *Repair open
mesh*), as a quick way out that softens detail.

Why both: a tidy that never changes the shape can run without asking, so models with small defects simply work.
Filling a hole or rebuilding *does* change the model, and you should choose how, see it and keep the result. Repair
the model once and every tool takes the repaired file as it is: no repeated repair on each run, and no repair that
differs between tools.

## Settings

- **Make it**: *A closed solid* (the sculpting tools) or *A clean surface*, which keeps openings for a mask or a
  relief and fixes everything else.
- **Method**: *Repair, rebuild only if needed* (default) fixes in place, then resamples only if the model is
  still not a solid. *Repair only* never resamples and reports what is left. *Always rebuild* resamples the whole
  model.
- **Clean**: remove duplicate and back-to-back faces, and drop loose pieces. You can drop crumbs below a share of
  the largest piece (0.2 % by default), keep the largest piece only, or keep all. *Weld distance* (advanced) joins
  vertices closer than a distance, for models exported in patches with cracks between them.
- **Topology**: separate non-manifold edges and vertices. Faces around a crowded edge are paired by angle, so that
  each pair bounds the same part. Also: wind faces consistently, facing out.
- **Holes**: fill all of them, those up to a number of edges, or none. *Patch*: *Flat* fills across the hole's plane
  (a cut base), *Smooth* with a relaxed membrane that follows the surface around it, and *Auto* picks flat for flat
  holes.
- **Rebuild**: the resolution (cells along the longest side) used when resampling. Detail finer than that softens.

## Report and views

The report lists what was found, every fix and a before/after table of each problem. Its last line is **Ready for**:
the tools whose input needs the result now meets. **Problems** marks open, non-manifold and wrongly wound edges in
red, on the input (and on the result, if anything is left). The output is `<stem>_repaired.ply` (or STL/OBJ), signed
`atlas-toolkit:mesh-repair`.

## Limits

A non-orientable surface (a Klein bottle, Boy's surface, a Möbius band) has no inside and outside, and only a
rebuild makes it a solid. Filling a very large or twisted opening may give a patch that crosses the surface near
it: check the Problems view, or rebuild. Repairing changes face order, so a painted selection made on the damaged
model must be painted again on the repaired one.
